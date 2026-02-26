"""
Administration CRUD des referentiels (schema ref).

Endpoints cibles:
- GET    /api/data/referentiels/layers/
- GET    /api/data/referentiels/<layer_id>/schema/
- POST   /api/data/referentiels/<layer_id>/records/
- PATCH  /api/data/referentiels/<layer_id>/records/<record_id>/
- DELETE /api/data/referentiels/<layer_id>/records/<record_id>/
- POST   /api/data/referentiels/<layer_id>/upload-csv/
"""

from __future__ import annotations

import csv
import io
import json
import sys
from dataclasses import dataclass
from typing import Any

from django.db import connection, transaction, DataError, DatabaseError, IntegrityError
from psycopg2 import sql
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


ROLE_MANAGER = "manager"
ROLE_PROJECT_MANAGER = "project_manager"
ROLE_ADMIN = "admin"
ALLOWED_REF_EDIT_ROLES = {ROLE_MANAGER, ROLE_PROJECT_MANAGER, ROLE_ADMIN}


@dataclass(frozen=True)
class RefLayerConfig:
    id: str
    label: str
    schema: str
    table: str
    id_column: str
    geom_column: str
    scope_mode: str  # "direct_region" | "via_commune" | "unrestricted"


REF_LAYER_REGISTRY: dict[str, RefLayerConfig] = {
    "admin-region": RefLayerConfig(
        id="admin-region",
        label="Limites - Regions",
        schema="ref",
        table="admin_region",
        id_column="id_region",
        geom_column="geom",
        scope_mode="direct_region",
    ),
    "admin-prefecture": RefLayerConfig(
        id="admin-prefecture",
        label="Limites - Prefectures",
        schema="ref",
        table="admin_prefecture",
        id_column="id_prefecture",
        geom_column="geom",
        scope_mode="direct_region",
    ),
    "admin-commune": RefLayerConfig(
        id="admin-commune",
        label="Limites - Communes",
        schema="ref",
        table="admin_commune",
        id_column="id_commune",
        geom_column="geom",
        scope_mode="direct_region",
    ),
    "equipements": RefLayerConfig(
        id="equipements",
        label="Infrastructures - Equipements",
        schema="ref",
        table="equipement",
        id_column="equip_id",
        geom_column="geom",
        scope_mode="via_commune",
    ),
    "localites": RefLayerConfig(
        id="localites",
        label="Infrastructures - Localites",
        schema="ref",
        table="localite",
        id_column="localite_id",
        geom_column="geom",
        scope_mode="via_commune",
    ),
    "agglomerations": RefLayerConfig(
        id="agglomerations",
        label="Infrastructures - Agglomerations",
        schema="ref",
        table="agglomeration",
        id_column="agglom_id",
        geom_column="geom",
        scope_mode="via_commune",
    ),
    "aire-protegee": RefLayerConfig(
        id="aire-protegee",
        label="Environnement - Aires protegees",
        schema="ref",
        table="aire_protegee",
        id_column="ap_id",
        geom_column="geom",
        scope_mode="via_commune",
    ),
    "zone-humide": RefLayerConfig(
        id="zone-humide",
        label="Environnement - Zones humides",
        schema="ref",
        table="zone_humide",
        id_column="zh_id",
        geom_column="geom",
        scope_mode="via_commune",
    ),
    "zone-sableuse": RefLayerConfig(
        id="zone-sableuse",
        label="Environnement - Zones sableuses",
        schema="ref",
        table="zone_sableuse",
        id_column="zs_id",
        geom_column="geom",
        scope_mode="via_commune",
    ),
    "occupation-sol": RefLayerConfig(
        id="occupation-sol",
        label="Environnement - Occupation du sol",
        schema="ref",
        table="occupation_sol",
        id_column="occsol_id",
        geom_column="geom",
        scope_mode="via_commune",
    ),
    "hydrographie": RefLayerConfig(
        id="hydrographie",
        label="Environnement - Hydrographie",
        schema="ref",
        table="hydrographie",
        id_column="id",
        geom_column="geom",
        scope_mode="unrestricted",
    ),
    "reseau-routier": RefLayerConfig(
        id="reseau-routier",
        label="Infrastructures - Reseau routier",
        schema="ref",
        table="reseau_routier",
        id_column="id",
        geom_column="geom",
        scope_mode="unrestricted",
    ),
    "habitations-dispersees": RefLayerConfig(
        id="habitations-dispersees",
        label="Infrastructures - Habitations dispersees",
        schema="ref",
        table="habitation_dispersee",
        id_column="hab_id",
        geom_column="geom",
        scope_mode="via_commune",
    ),
}


class ReferentielAdminBaseView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "admin_write"

    def _normalize_role(self, value: Any) -> str:
        role = str(value or "").strip().lower()
        if role in {"projectmanager", "chef_projet", "chef projet"}:
            return ROLE_PROJECT_MANAGER
        return role

    def _role(self, request) -> str:
        return self._normalize_role(getattr(request.user, "role", ""))

    def _is_global_admin(self, request) -> bool:
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (
                bool(getattr(user, "is_staff", False))
                or bool(getattr(user, "is_superuser", False))
                or self._role(request) == ROLE_ADMIN
            )
        )

    def _is_project_admin(self, request) -> bool:
        return self._is_global_admin(request) or self._role(request) == ROLE_PROJECT_MANAGER

    def _is_manager(self, request) -> bool:
        return self._role(request) == ROLE_MANAGER and not self._is_project_admin(request)

    def _assert_can_manage_referentiels(self, request) -> None:
        role = self._role(request)
        if not (
            self._is_global_admin(request)
            or self._is_project_admin(request)
            or role in ALLOWED_REF_EDIT_ROLES
        ):
            raise PermissionDenied("Modification referentiels reservee aux admins N1/N2/global.")

    def _assert_can_run_maintenance(self, request) -> None:
        # Operations structurelles (truncate, recalcul global) reservees N2/global.
        if not self._is_project_admin(request):
            raise PermissionDenied("Operation reservee aux admins N2/global.")

    def _resolve_layer(self, layer_id: str) -> RefLayerConfig:
        config = REF_LAYER_REGISTRY.get((layer_id or "").strip())
        if not config:
            raise NotFound("Couche referentielle inconnue.")
        return config

    def _actor_region(self, request) -> str:
        region = str(
            getattr(request, "current_region_id", "")
            or getattr(request.user, "region_id", "")
            or ""
        ).strip()
        return region

    def _fetch_table_columns(self, layer: RefLayerConfig) -> list[dict[str, Any]]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    column_name,
                    data_type,
                    udt_name,
                    is_nullable,
                    column_default
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = %s
                ORDER BY ordinal_position
                """,
                [layer.schema, layer.table],
            )
            rows = cursor.fetchall()

        result: list[dict[str, Any]] = []
        for column_name, data_type, udt_name, is_nullable, column_default in rows:
            is_geom = str(column_name) == layer.geom_column
            is_id = str(column_name) == layer.id_column
            required = (str(is_nullable).upper() == "NO") and (column_default is None) and (not is_geom)
            result.append(
                {
                    "name": str(column_name),
                    "data_type": str(data_type),
                    "udt_name": str(udt_name),
                    "is_nullable": str(is_nullable).upper() == "YES",
                    "required": required,
                    "is_geometry": is_geom,
                    "is_id": is_id,
                    "editable": not is_geom,
                }
            )
        return result

    def _column_map(self, layer: RefLayerConfig) -> dict[str, dict[str, Any]]:
        return {c["name"]: c for c in self._fetch_table_columns(layer)}

    def _record_exists(self, layer: RefLayerConfig, record_id: str) -> bool:
        query = sql.SQL("SELECT 1 FROM {}.{} WHERE {} = %s LIMIT 1").format(
            sql.Identifier(layer.schema),
            sql.Identifier(layer.table),
            sql.Identifier(layer.id_column),
        )
        with connection.cursor() as cursor:
            cursor.execute(query, [record_id])
            return bool(cursor.fetchone())

    def _commune_belongs_to_region(self, commune_id: str, region_id: str) -> bool:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM ref.admin_commune
                WHERE id_commune = %s
                  AND id_region = %s
                LIMIT 1
                """,
                [commune_id, region_id],
            )
            return bool(cursor.fetchone())

    def _record_is_in_manager_scope(self, layer: RefLayerConfig, record_id: str, region_id: str) -> bool:
        if layer.scope_mode == "direct_region":
            query = sql.SQL("SELECT 1 FROM {}.{} WHERE {} = %s AND id_region = %s LIMIT 1").format(
                sql.Identifier(layer.schema),
                sql.Identifier(layer.table),
                sql.Identifier(layer.id_column),
            )
            params = [record_id, region_id]
        elif layer.scope_mode == "via_commune":
            query = sql.SQL(
                """
                SELECT 1
                FROM {}.{} t
                JOIN ref.admin_commune c ON c.id_commune = t.id_commune
                WHERE t.{} = %s
                  AND c.id_region = %s
                LIMIT 1
                """
            ).format(
                sql.Identifier(layer.schema),
                sql.Identifier(layer.table),
                sql.Identifier(layer.id_column),
            )
            params = [record_id, region_id]
        else:
            query = sql.SQL(
                """
                SELECT 1
                FROM {}.{} t
                JOIN ref.admin_region r ON r.id_region = %s
                WHERE t.{} = %s
                  AND t.{} IS NOT NULL
                  AND ST_Intersects(t.{}, r.geom)
                LIMIT 1
                """
            ).format(
                sql.Identifier(layer.schema),
                sql.Identifier(layer.table),
                sql.Identifier(layer.id_column),
                sql.Identifier(layer.geom_column),
                sql.Identifier(layer.geom_column),
            )
            params = [region_id, record_id]

        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return bool(cursor.fetchone())

    def _assert_manager_layer_scope_supported(self, request, layer: RefLayerConfig) -> None:
        return

    def _assert_manager_payload_scope(self, request, layer: RefLayerConfig, values: dict[str, Any]) -> None:
        if not self._is_manager(request):
            return

        region_id = self._actor_region(request)
        if not region_id:
            raise PermissionDenied("Aucune region assignee a votre compte.")

        self._assert_manager_layer_scope_supported(request, layer)

        if layer.scope_mode == "direct_region":
            payload_region = str(values.get("id_region") or "").strip()
            if not payload_region:
                raise ValidationError("Le champ id_region est requis pour votre role.")
            if payload_region != region_id:
                raise PermissionDenied("Vous ne pouvez modifier que votre region.")
            return

        if layer.scope_mode == "via_commune":
            commune_id = str(values.get("id_commune") or "").strip()
            if not commune_id:
                raise ValidationError("Le champ id_commune est requis pour votre role.")
            if not self._commune_belongs_to_region(commune_id, region_id):
                raise PermissionDenied("La commune fournie est hors de votre region.")

    def _assert_manager_record_scope(self, request, layer: RefLayerConfig, record_id: str) -> None:
        if not self._is_manager(request):
            return

        region_id = self._actor_region(request)
        if not region_id:
            raise PermissionDenied("Aucune region assignee a votre compte.")

        self._assert_manager_layer_scope_supported(request, layer)

        if not self._record_is_in_manager_scope(layer, record_id, region_id):
            raise PermissionDenied("Entite hors perimetre regional.")

    def _normalize_values(
        self,
        layer: RefLayerConfig,
        raw_values: Any,
        *,
        for_update: bool,
    ) -> dict[str, Any]:
        if raw_values is None:
            parsed: dict[str, Any] = {}
        elif isinstance(raw_values, dict):
            parsed = raw_values
        elif isinstance(raw_values, str):
            text = raw_values.strip()
            if not text:
                parsed = {}
            else:
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValidationError(f"JSON values invalide: {exc}") from exc
                if not isinstance(payload, dict):
                    raise ValidationError("Le champ values doit etre un objet JSON.")
                parsed = payload
        else:
            raise ValidationError("Le champ values doit etre un objet JSON.")

        columns = self._column_map(layer)
        unknown = [k for k in parsed.keys() if k not in columns]
        if unknown:
            raise ValidationError(f"Colonnes inconnues: {', '.join(sorted(map(str, unknown)))}")

        cleaned: dict[str, Any] = {}
        for key, value in parsed.items():
            if key == layer.geom_column:
                raise ValidationError(
                    "La geometrie doit etre transmise via geometry_geojson ou geometry_wkt."
                )
            if for_update and key == layer.id_column:
                raise ValidationError("La cle primaire ne peut pas etre modifiee.")

            if isinstance(value, str):
                stripped = value.strip()
                cleaned[key] = None if stripped == "" else stripped
            else:
                cleaned[key] = value
        return cleaned

    def _normalize_geometry(self, data: Any) -> tuple[str | None, Any]:
        geometry_geojson = data.get("geometry_geojson")
        geometry_wkt = data.get("geometry_wkt")
        clear_geometry = data.get("clear_geometry")

        clear_geometry_flag = str(clear_geometry or "").strip().lower() in {"1", "true", "yes", "oui"}
        has_geojson = geometry_geojson not in (None, "")
        has_wkt = geometry_wkt not in (None, "")

        if clear_geometry_flag and (has_geojson or has_wkt):
            raise ValidationError("clear_geometry ne peut pas etre combine avec geometry_geojson/geometry_wkt.")
        if has_geojson and has_wkt:
            raise ValidationError("Utilisez soit geometry_geojson soit geometry_wkt, pas les deux.")
        if clear_geometry_flag:
            return "clear", None
        if has_geojson:
            if isinstance(geometry_geojson, str):
                text = geometry_geojson.strip()
                if not text:
                    raise ValidationError("geometry_geojson est vide.")
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValidationError(f"geometry_geojson invalide: {exc}") from exc
                return "geojson", json.dumps(payload)
            return "geojson", json.dumps(geometry_geojson)
        if has_wkt:
            text = str(geometry_wkt).strip()
            if not text:
                raise ValidationError("geometry_wkt est vide.")
            return "wkt", text
        return None, None

    def _required_columns_missing(self, layer: RefLayerConfig, values: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        for col in self._fetch_table_columns(layer):
            if not col["required"]:
                continue
            name = col["name"]
            if name == layer.geom_column:
                continue
            if name not in values:
                missing.append(name)
        return missing

    def _insert_record(
        self,
        request,
        layer: RefLayerConfig,
        values: dict[str, Any],
        geometry_mode: str | None,
        geometry_payload: Any,
    ) -> str:
        self._assert_manager_payload_scope(request, layer, values)

        missing = self._required_columns_missing(layer, values)
        if missing:
            raise ValidationError(f"Colonnes requises manquantes: {', '.join(missing)}")

        columns: list[str] = []
        value_sql: list[sql.SQL] = []
        params: list[Any] = []

        for key, value in values.items():
            columns.append(key)
            value_sql.append(sql.SQL("%s"))
            params.append(value)

        if geometry_mode == "geojson":
            columns.append(layer.geom_column)
            value_sql.append(sql.SQL("ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)"))
            params.append(geometry_payload)
        elif geometry_mode == "wkt":
            columns.append(layer.geom_column)
            value_sql.append(sql.SQL("ST_SetSRID(ST_GeomFromText(%s), 4326)"))
            params.append(geometry_payload)
        elif geometry_mode == "clear":
            columns.append(layer.geom_column)
            value_sql.append(sql.SQL("NULL"))

        if not columns:
            raise ValidationError("Aucune colonne fournie pour la creation.")

        query = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({}) RETURNING {}").format(
            sql.Identifier(layer.schema),
            sql.Identifier(layer.table),
            sql.SQL(", ").join([sql.Identifier(c) for c in columns]),
            sql.SQL(", ").join(value_sql),
            sql.Identifier(layer.id_column),
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                created_id = cursor.fetchone()[0]
        except (DataError, IntegrityError, DatabaseError) as exc:
            raise ValidationError(f"Echec creation referentiel: {exc}") from exc

        created_text = str(created_id)
        if self._is_manager(request):
            self._assert_manager_record_scope(request, layer, created_text)
        return created_text

    def _update_record(
        self,
        request,
        layer: RefLayerConfig,
        record_id: str,
        values: dict[str, Any],
        geometry_mode: str | None,
        geometry_payload: Any,
    ) -> None:
        if not self._record_exists(layer, record_id):
            raise NotFound("Entite non trouvee.")

        self._assert_manager_record_scope(request, layer, record_id)

        if self._is_manager(request):
            if layer.scope_mode == "direct_region" and "id_region" in values:
                actor_region = self._actor_region(request)
                if str(values.get("id_region") or "").strip() != actor_region:
                    raise PermissionDenied("Vous ne pouvez modifier que votre region.")
            if layer.scope_mode == "via_commune" and "id_commune" in values:
                actor_region = self._actor_region(request)
                commune_id = str(values.get("id_commune") or "").strip()
                if not commune_id or not self._commune_belongs_to_region(commune_id, actor_region):
                    raise PermissionDenied("La commune fournie est hors de votre region.")

        assignments: list[sql.SQL] = []
        params: list[Any] = []

        for key, value in values.items():
            assignments.append(sql.SQL("{} = %s").format(sql.Identifier(key)))
            params.append(value)

        if geometry_mode == "geojson":
            assignments.append(
                sql.SQL("{} = ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)").format(
                    sql.Identifier(layer.geom_column)
                )
            )
            params.append(geometry_payload)
        elif geometry_mode == "wkt":
            assignments.append(
                sql.SQL("{} = ST_SetSRID(ST_GeomFromText(%s), 4326)").format(
                    sql.Identifier(layer.geom_column)
                )
            )
            params.append(geometry_payload)
        elif geometry_mode == "clear":
            assignments.append(sql.SQL("{} = NULL").format(sql.Identifier(layer.geom_column)))

        if not assignments:
            raise ValidationError("Aucune colonne a mettre a jour.")

        query = sql.SQL("UPDATE {}.{} SET {} WHERE {} = %s").format(
            sql.Identifier(layer.schema),
            sql.Identifier(layer.table),
            sql.SQL(", ").join(assignments),
            sql.Identifier(layer.id_column),
        )
        params.append(record_id)

        try:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                if cursor.rowcount <= 0:
                    raise NotFound("Entite non trouvee.")
        except (DataError, IntegrityError, DatabaseError) as exc:
            raise ValidationError(f"Echec mise a jour referentiel: {exc}") from exc

        self._assert_manager_record_scope(request, layer, record_id)

    def _delete_record(self, request, layer: RefLayerConfig, record_id: str) -> None:
        if not self._record_exists(layer, record_id):
            raise NotFound("Entite non trouvee.")

        self._assert_manager_record_scope(request, layer, record_id)

        query = sql.SQL("DELETE FROM {}.{} WHERE {} = %s").format(
            sql.Identifier(layer.schema),
            sql.Identifier(layer.table),
            sql.Identifier(layer.id_column),
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(query, [record_id])
                if cursor.rowcount <= 0:
                    raise NotFound("Entite non trouvee.")
        except (DataError, IntegrityError, DatabaseError) as exc:
            raise ValidationError(f"Echec suppression referentiel: {exc}") from exc

    def _csv_rows(self, file_obj, delimiter: str) -> list[dict[str, str]]:
        try:
            content = file_obj.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValidationError("Fichier CSV invalide (encodage UTF-8 attendu).")

        # Some geometries (WKT) can exceed Python csv's default 128KB field limit.
        try:
            csv.field_size_limit(2_147_483_647)
        except OverflowError:
            size = sys.maxsize
            while size > 0:
                try:
                    csv.field_size_limit(size)
                    break
                except OverflowError:
                    size //= 10

        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
        if not reader.fieldnames:
            raise ValidationError("CSV invalide: en-tetes absents.")
        return list(reader)

    def _exc_message(self, exc: Exception) -> str:
        if isinstance(exc, ValidationError):
            detail = exc.detail
            if isinstance(detail, list):
                return "; ".join(str(item) for item in detail)
            if isinstance(detail, dict):
                parts: list[str] = []
                for key, value in detail.items():
                    if isinstance(value, list):
                        parts.append(f"{key}: {', '.join(str(v) for v in value)}")
                    else:
                        parts.append(f"{key}: {value}")
                return "; ".join(parts) if parts else "Validation error"
            return str(detail)
        return str(exc)

    def _ensure_hydrographie_multilinestring(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                ALTER TABLE ref.hydrographie
                ALTER COLUMN geom TYPE geometry(MultiLineString, 4326)
                USING (
                    CASE
                        WHEN geom IS NULL THEN NULL
                        WHEN ST_GeometryType(geom) IN ('ST_LineString', 'ST_MultiLineString')
                            THEN ST_Multi(ST_Force2D(geom))
                        WHEN ST_GeometryType(geom) = 'ST_GeometryCollection'
                            THEN ST_Multi(ST_CollectionExtract(ST_Force2D(geom), 2))
                        ELSE NULL
                    END
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS sidx_hydrographie_geom
                ON ref.hydrographie
                USING GIST (geom)
                """
            )

    def _truncate_layer_table(self, layer: RefLayerConfig) -> None:
        if layer.id == "hydrographie":
            self._ensure_hydrographie_multilinestring()

        query = sql.SQL("TRUNCATE TABLE {}.{} RESTART IDENTITY").format(
            sql.Identifier(layer.schema),
            sql.Identifier(layer.table),
        )
        with connection.cursor() as cursor:
            cursor.execute(query)

    def _recompute_referentiel_id_commune(self) -> None:
        # Phase 1: intersection stricte
        intersection_updates = [
            ("ref.localite", "geom", False),
            ("ref.equipement", "geom", False),
            ("ref.agglomeration", "geom", True),
            ("ref.aire_protegee", "geom", True),
            ("ref.habitation_dispersee", "geom", True),
            ("ref.zone_humide", "geom", True),
            ("ref.zone_sableuse", "geom", True),
            ("ref.occupation_sol", "geom", True),
        ]

        with connection.cursor() as cursor:
            for table_name, geom_col, use_pos in intersection_updates:
                geom_expr = f"ST_PointOnSurface(t.{geom_col})" if use_pos else f"t.{geom_col}"
                cursor.execute(
                    f"""
                    UPDATE {table_name} t
                    SET id_commune = c.id_commune
                    FROM ref.admin_commune c
                    WHERE t.id_commune IS NULL
                      AND t.{geom_col} IS NOT NULL
                      AND ST_Intersects(c.geom, {geom_expr})
                    """
                )

            # Phase 2: nearest-neighbour fallback
            nearest_updates = [
                ("ref.localite", "geom", False),
                ("ref.equipement", "geom", False),
                ("ref.agglomeration", "geom", True),
                ("ref.aire_protegee", "geom", True),
                ("ref.habitation_dispersee", "geom", True),
                ("ref.zone_humide", "geom", True),
                ("ref.zone_sableuse", "geom", True),
                ("ref.occupation_sol", "geom", True),
            ]
            for table_name, geom_col, use_pos in nearest_updates:
                geom_expr = f"ST_PointOnSurface(t.{geom_col})" if use_pos else f"t.{geom_col}"
                cursor.execute(
                    f"""
                    UPDATE {table_name} t
                    SET id_commune = (
                        SELECT c.id_commune
                        FROM ref.admin_commune c
                        ORDER BY c.geom <-> {geom_expr}
                        LIMIT 1
                    )
                    WHERE t.id_commune IS NULL
                      AND t.{geom_col} IS NOT NULL
                    """
                )

    def _referentiel_commune_stats(self) -> list[dict[str, Any]]:
        query = """
            SELECT table_name, row_count, with_commune, without_commune
            FROM (
                SELECT 'agglomeration'::text AS table_name, count(*)::bigint AS row_count, count(id_commune)::bigint AS with_commune, count(*)::bigint - count(id_commune)::bigint AS without_commune FROM ref.agglomeration
                UNION ALL
                SELECT 'aire_protegee', count(*), count(id_commune), count(*) - count(id_commune) FROM ref.aire_protegee
                UNION ALL
                SELECT 'equipement', count(*), count(id_commune), count(*) - count(id_commune) FROM ref.equipement
                UNION ALL
                SELECT 'habitation_dispersee', count(*), count(id_commune), count(*) - count(id_commune) FROM ref.habitation_dispersee
                UNION ALL
                SELECT 'localite', count(*), count(id_commune), count(*) - count(id_commune) FROM ref.localite
                UNION ALL
                SELECT 'occupation_sol', count(*), count(id_commune), count(*) - count(id_commune) FROM ref.occupation_sol
                UNION ALL
                SELECT 'zone_humide', count(*), count(id_commune), count(*) - count(id_commune) FROM ref.zone_humide
                UNION ALL
                SELECT 'zone_sableuse', count(*), count(id_commune), count(*) - count(id_commune) FROM ref.zone_sableuse
            ) t
            ORDER BY table_name
        """
        with connection.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()

        return [
            {
                "table_name": str(row[0]),
                "row_count": int(row[1]),
                "with_commune": int(row[2]),
                "without_commune": int(row[3]),
            }
            for row in rows
        ]


class ReferentielLayerListView(ReferentielAdminBaseView):
    throttle_scope = "geojson"

    def get(self, request):
        self._assert_can_manage_referentiels(request)
        rows = [
            {
                "id": layer.id,
                "label": layer.label,
                "schema": layer.schema,
                "table": layer.table,
                "id_column": layer.id_column,
                "geom_column": layer.geom_column,
                "scope_mode": layer.scope_mode,
                "supports_upload_csv": True,
            }
            for layer in REF_LAYER_REGISTRY.values()
        ]
        return Response({"count": len(rows), "results": rows})


class ReferentielLayerSchemaView(ReferentielAdminBaseView):
    throttle_scope = "geojson"

    def get(self, request, layer_id: str):
        self._assert_can_manage_referentiels(request)
        layer = self._resolve_layer(layer_id)
        columns = self._fetch_table_columns(layer)
        return Response(
            {
                "layer_id": layer.id,
                "label": layer.label,
                "schema": layer.schema,
                "table": layer.table,
                "id_column": layer.id_column,
                "geom_column": layer.geom_column,
                "scope_mode": layer.scope_mode,
                "columns": columns,
            }
        )


class ReferentielRecordCreateView(ReferentielAdminBaseView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def post(self, request, layer_id: str):
        self._assert_can_manage_referentiels(request)
        layer = self._resolve_layer(layer_id)

        values = self._normalize_values(layer, request.data.get("values"), for_update=False)
        geometry_mode, geometry_payload = self._normalize_geometry(request.data)

        with transaction.atomic():
            created_id = self._insert_record(
                request=request,
                layer=layer,
                values=values,
                geometry_mode=geometry_mode,
                geometry_payload=geometry_payload,
            )

        return Response(
            {
                "detail": "Entite referentielle creee.",
                "layer_id": layer.id,
                "record_id": created_id,
            },
            status=status.HTTP_201_CREATED,
        )


class ReferentielRecordDetailView(ReferentielAdminBaseView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def patch(self, request, layer_id: str, record_id: str):
        self._assert_can_manage_referentiels(request)
        layer = self._resolve_layer(layer_id)

        values = self._normalize_values(layer, request.data.get("values"), for_update=True)
        geometry_mode, geometry_payload = self._normalize_geometry(request.data)

        with transaction.atomic():
            self._update_record(
                request=request,
                layer=layer,
                record_id=str(record_id),
                values=values,
                geometry_mode=geometry_mode,
                geometry_payload=geometry_payload,
            )

        return Response(
            {
                "detail": "Entite referentielle mise a jour.",
                "layer_id": layer.id,
                "record_id": str(record_id),
            }
        )

    def delete(self, request, layer_id: str, record_id: str):
        self._assert_can_manage_referentiels(request)
        layer = self._resolve_layer(layer_id)

        with transaction.atomic():
            self._delete_record(request=request, layer=layer, record_id=str(record_id))

        return Response(
            {
                "detail": "Entite referentielle supprimee.",
                "layer_id": layer.id,
                "record_id": str(record_id),
            }
        )


class ReferentielCsvUploadView(ReferentielAdminBaseView):
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, layer_id: str):
        self._assert_can_manage_referentiels(request)
        layer = self._resolve_layer(layer_id)

        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError("Fichier CSV requis (champ 'file').")

        mode = str(request.data.get("mode") or "upsert").strip().lower()
        if mode not in {"upsert", "insert"}:
            raise ValidationError("Mode invalide. Valeurs attendues: upsert, insert.")

        delimiter = str(request.data.get("delimiter") or ",")
        if len(delimiter) != 1:
            raise ValidationError("Delimiter CSV invalide.")

        rows = self._csv_rows(upload, delimiter)
        if not rows:
            return Response(
                {
                    "detail": "CSV vide.",
                    "layer_id": layer.id,
                    "created": 0,
                    "updated": 0,
                    "failed": 0,
                    "errors": [],
                }
            )

        created = 0
        updated = 0
        failed = 0
        errors: list[dict[str, Any]] = []

        geom_alias_geojson = {"geometry_geojson", "geom_geojson"}
        geom_alias_wkt = {"geometry_wkt", "geom_wkt"}

        for line_number, row in enumerate(rows, start=2):
            savepoint_id = transaction.savepoint()
            try:
                values_payload: dict[str, Any] = {}
                geometry_geojson = None
                geometry_wkt = None

                for key, value in (row or {}).items():
                    column = str(key or "").strip()
                    if not column:
                        continue
                    cell = None if value is None else str(value).strip()
                    if column in geom_alias_geojson:
                        geometry_geojson = cell
                        continue
                    if column in geom_alias_wkt:
                        geometry_wkt = cell
                        continue
                    if column == layer.geom_column and cell:
                        geometry_wkt = cell
                        continue
                    values_payload[column] = None if cell == "" else cell

                values = self._normalize_values(layer, values_payload, for_update=False)
                geometry_mode, geometry_payload = self._normalize_geometry(
                    {
                        "geometry_geojson": geometry_geojson,
                        "geometry_wkt": geometry_wkt,
                    }
                )

                record_id_value = str(values.get(layer.id_column) or "").strip()
                if mode == "upsert" and record_id_value and self._record_exists(layer, record_id_value):
                    update_values = {k: v for k, v in values.items() if k != layer.id_column}
                    self._update_record(
                        request=request,
                        layer=layer,
                        record_id=record_id_value,
                        values=update_values,
                        geometry_mode=geometry_mode,
                        geometry_payload=geometry_payload,
                    )
                    updated += 1
                else:
                    self._insert_record(
                        request=request,
                        layer=layer,
                        values=values,
                        geometry_mode=geometry_mode,
                        geometry_payload=geometry_payload,
                    )
                    created += 1

                transaction.savepoint_commit(savepoint_id)
            except Exception as exc:
                transaction.savepoint_rollback(savepoint_id)
                failed += 1
                errors.append({"line": line_number, "error": self._exc_message(exc)})

        status_text = "success" if failed == 0 else ("partial_success" if created + updated > 0 else "failed")
        http_status = status.HTTP_200_OK if status_text != "failed" else status.HTTP_400_BAD_REQUEST

        return Response(
            {
                "detail": "Import CSV referentiel termine.",
                "status": status_text,
                "layer_id": layer.id,
                "mode": mode,
                "created": created,
                "updated": updated,
                "failed": failed,
                "errors": errors[:100],
            },
            status=http_status,
        )


class ReferentielLayerTruncateView(ReferentielAdminBaseView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def post(self, request, layer_id: str):
        self._assert_can_manage_referentiels(request)
        self._assert_can_run_maintenance(request)
        layer = self._resolve_layer(layer_id)

        with transaction.atomic():
            self._truncate_layer_table(layer)

        return Response(
            {
                "detail": "Couche referentielle videe.",
                "layer_id": layer.id,
                "table": f"{layer.schema}.{layer.table}",
            },
            status=status.HTTP_200_OK,
        )


class ReferentielRecomputeCommuneView(ReferentielAdminBaseView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def post(self, request):
        self._assert_can_manage_referentiels(request)
        self._assert_can_run_maintenance(request)

        with transaction.atomic():
            self._recompute_referentiel_id_commune()
            stats = self._referentiel_commune_stats()

        return Response(
            {
                "detail": "Recalcul id_commune termine.",
                "stats": stats,
            },
            status=status.HTTP_200_OK,
        )
