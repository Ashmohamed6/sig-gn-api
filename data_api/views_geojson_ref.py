# data_api/views_geojson_ref.py

"""
Vues GeoJSON pour les donnees referentielles (schema ref).
Expose les limites administratives, environnement, infrastructures.
"""

import json
import logging
import re

from django.db import connection
from django.http import JsonResponse
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView


logger = logging.getLogger(__name__)


class BaseRefGeoJSONView(APIView):
    """
    Classe de base pour les vues GeoJSON referentielles.
    PAS de verification de projet - donnees communes a tous les projets.
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "geojson"
    SAFE_PARAM_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
    DEFAULT_PAGE_SIZE = 5000
    MAX_PAGE_SIZE = 20000

    sql_query = ""
    layer_name = "features"

    def _is_platform_admin(self, request) -> bool:
        user = getattr(request, "user", None)
        role = str(getattr(user, "role", "") or "").strip().lower()
        return bool(
            user
            and user.is_authenticated
            and (user.is_staff or user.is_superuser or role in {"admin", "project_manager"})
        )

    def _safe_param(self, value: str | None, name: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            return ""
        if not self.SAFE_PARAM_PATTERN.match(cleaned):
            raise ValidationError(f"Parametre invalide: {name}")
        return cleaned

    def get_region(self, request) -> str:
        """Region effective: profil user pour les non-admin, override possible pour admin/staff."""
        user = getattr(request, "user", None)
        profile_region = self._safe_param(
            (getattr(request, "current_region_id", "") or getattr(user, "region_id", "") or ""),
            "region",
        )

        if self._is_platform_admin(request):
            return self._safe_param(request.query_params.get("region", ""), "region")

        if not profile_region:
            raise PermissionDenied("Aucune region assignee a votre compte.")
        return profile_region

    def get_sql_query(self, request):
        return self.sql_query, []

    def _parse_positive_int(self, raw_value, default: int, *, max_value: int | None = None) -> int:
        try:
            value = int(str(raw_value).strip())
        except (TypeError, ValueError):
            return default

        if value <= 0:
            return default
        if max_value is not None and value > max_value:
            return max_value
        return value

    def _is_pagination_enabled(self, request) -> bool:
        raw_value = str(request.query_params.get("paginate", "1") or "1").strip().lower()
        return raw_value not in {"0", "false", "no", "off"}

    def _get_pagination_params(self, request) -> tuple[bool, int, int]:
        if not self._is_pagination_enabled(request):
            return False, 1, self.DEFAULT_PAGE_SIZE

        page = self._parse_positive_int(request.query_params.get("page"), 1)
        page_size = self._parse_positive_int(
            request.query_params.get("page_size"),
            self.DEFAULT_PAGE_SIZE,
            max_value=self.MAX_PAGE_SIZE,
        )
        return True, page, page_size

    def _build_page_url(self, request, page: int, page_size: int) -> str:
        query = request.query_params.copy()
        query["paginate"] = "1"
        query["page"] = str(page)
        query["page_size"] = str(page_size)
        base_url = request.build_absolute_uri(request.path)
        encoded_query = query.urlencode()
        return f"{base_url}?{encoded_query}" if encoded_query else base_url

    def _rows_to_features(self, rows, columns):
        features = []
        for row in rows:
            row_dict = dict(zip(columns, row))

            geom_json = row_dict.pop("geom_json", None)
            if not geom_json:
                continue

            try:
                geometry = json.loads(geom_json) if isinstance(geom_json, str) else geom_json
            except (json.JSONDecodeError, TypeError):
                continue

            properties = {k: v for k, v in row_dict.items() if k not in ("geom", "geometry", "geom_json")}
            features.append({"type": "Feature", "geometry": geometry, "properties": properties})
        return features

    def get(self, request, *args, **kwargs):
        try:
            sql, params = self.get_sql_query(request)
            paginate, page, page_size = self._get_pagination_params(request)

            if not sql:
                payload = {"type": "FeatureCollection", "name": self.layer_name, "features": []}
                if paginate:
                    payload["pagination"] = {
                        "page": page,
                        "page_size": page_size,
                        "returned": 0,
                        "has_next": False,
                        "has_previous": page > 1,
                        "next": None,
                        "previous": self._build_page_url(request, page - 1, page_size) if page > 1 else None,
                    }
                return JsonResponse(payload)

            if paginate:
                offset = (page - 1) * page_size
                limit_plus_one = page_size + 1

                paged_sql = f"""
                    SELECT *
                    FROM ({sql}) AS ref_layer
                    ORDER BY 1 NULLS LAST
                    LIMIT %s OFFSET %s
                """

                with connection.cursor() as cursor:
                    cursor.execute(paged_sql, [*params, limit_plus_one, offset])
                    rows = cursor.fetchall()
                    columns = [col[0] for col in cursor.description]

                has_next = len(rows) > page_size
                page_rows = rows[:page_size]
                features = self._rows_to_features(page_rows, columns)

                return JsonResponse(
                    {
                        "type": "FeatureCollection",
                        "name": self.layer_name,
                        "features": features,
                        "pagination": {
                            "page": page,
                            "page_size": page_size,
                            "returned": len(features),
                            "has_next": has_next,
                            "has_previous": page > 1,
                            "next": self._build_page_url(request, page + 1, page_size) if has_next else None,
                            "previous": self._build_page_url(request, page - 1, page_size) if page > 1 else None,
                        },
                    }
                )

            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description]

            features = self._rows_to_features(rows, columns)

            return JsonResponse({"type": "FeatureCollection", "name": self.layer_name, "features": features})

        except (PermissionDenied, ValidationError):
            raise
        except Exception:
            logger.exception("Erreur GeoJSON ref sur la couche %s", self.layer_name)
            return JsonResponse(
                {
                    "type": "FeatureCollection",
                    "name": self.layer_name,
                    "features": [],
                },
                status=500,
            )


# ============================================================
# LIMITES ADMINISTRATIVES
# ============================================================

class AdminRegionGeoJSONView(BaseRefGeoJSONView):
    """Regions administratives"""

    layer_name = "regions"

    def get_sql_query(self, request):
        region = self.get_region(request)

        where_clauses = ["geom IS NOT NULL"]
        params = []
        if region:
            where_clauses.append("id_region = %s")
            params.append(region)

        where_sql = "WHERE " + " AND ".join(where_clauses)

        sql = f"""
            SELECT
                id_region,
                nom AS nom_region,
                id_region AS code_region,
                ref_name,
                admin0_nom AS pays,
                admin0_pcod AS code_pays,
                shape_area AS superficie,
                shape_area AS superficie_km2,
                shape_leng AS perimetre,
                ST_AsGeoJSON(geom) as geom_json
            FROM ref.admin_region
            {where_sql}
        """
        return sql, params


class AdminPrefectureGeoJSONView(BaseRefGeoJSONView):
    """Prefectures"""

    layer_name = "prefectures"

    def get_sql_query(self, request):
        region = self.get_region(request)

        where_clauses = ["p.geom IS NOT NULL"]
        params = []
        if region:
            where_clauses.append("p.id_region = %s")
            params.append(region)

        where_sql = "WHERE " + " AND ".join(where_clauses)

        sql = f"""
            SELECT
                p.id_prefecture,
                p.nom AS nom_prefecture,
                p.id_prefecture AS code_prefecture,
                p.ref_name,
                p.admin0_nom AS pays,
                p.admin0_pcod AS code_pays,
                r.nom AS nom_region,
                p.id_region,
                p.shape_area AS superficie,
                p.shape_leng AS perimetre,
                ST_AsGeoJSON(p.geom) as geom_json
            FROM ref.admin_prefecture p
            LEFT JOIN ref.admin_region r ON p.id_region = r.id_region
            {where_sql}
        """
        return sql, params


class AdminCommuneGeoJSONView(BaseRefGeoJSONView):
    """Communes / Sous-prefectures"""

    layer_name = "communes"

    def get_sql_query(self, request):
        region = self.get_region(request)
        user = getattr(request, "user", None)
        prefecture = (
            self._safe_param(request.query_params.get("prefecture", ""), "prefecture")
            if self._is_platform_admin(request)
            else ""
        )

        where_clauses = ["c.geom IS NOT NULL"]
        params = []
        if region:
            where_clauses.append("c.id_region = %s")
            params.append(region)
        if prefecture:
            where_clauses.append("c.id_prefecture = %s")
            params.append(prefecture)

        where_sql = "WHERE " + " AND ".join(where_clauses)

        sql = f"""
            SELECT
                c.id_commune,
                c.nom AS nom_commune,
                c.ref_name,
                c.admin0_nom AS pays,
                c.admin0_pcod AS code_pays,
                p.nom AS nom_prefecture,
                c.id_prefecture,
                r.nom AS nom_region,
                c.id_region,
                c.shape_area AS superficie,
                c.shape_leng AS perimetre,
                ST_AsGeoJSON(c.geom) as geom_json
            FROM ref.admin_commune c
            LEFT JOIN ref.admin_prefecture p ON c.id_prefecture = p.id_prefecture
            LEFT JOIN ref.admin_region r ON c.id_region = r.id_region
            {where_sql}
        """
        return sql, params


# ============================================================
# ENVIRONNEMENT
# ============================================================

class AireProtegeeGeoJSONView(BaseRefGeoJSONView):
    """Aires protegees"""

    layer_name = "aires_protegees"

    def get_sql_query(self, request):
        region = self.get_region(request)

        where_clauses = ["ap.geom IS NOT NULL"]
        params = []
        if region:
            where_clauses.append("c.id_region = %s")
            params.append(region)

        where_sql = "WHERE " + " AND ".join(where_clauses)

        sql = f"""
            SELECT
                ap.ap_id,
                ap.nom,
                ap.nom_fr,
                ap.fclass AS type_aire,
                ap.code,
                c.nom AS nom_commune,
                ap.id_commune,
                ST_AsGeoJSON(ap.geom) as geom_json
            FROM ref.aire_protegee ap
            LEFT JOIN ref.admin_commune c ON ap.id_commune = c.id_commune
            {where_sql}
        """
        return sql, params


class ZoneHumideGeoJSONView(BaseRefGeoJSONView):
    """Zones humides"""

    layer_name = "zones_humides"

    def get_sql_query(self, request):
        region = self.get_region(request)

        where_clauses = ["zh.geom IS NOT NULL"]
        params = []
        if region:
            where_clauses.append("c.id_region = %s")
            params.append(region)

        where_sql = "WHERE " + " AND ".join(where_clauses)

        sql = f"""
            SELECT
                zh.zh_id,
                zh.nom,
                zh.nom_fr,
                zh.fclass AS type_zone,
                zh.code,
                c.nom AS nom_commune,
                zh.id_commune,
                ST_AsGeoJSON(zh.geom) as geom_json
            FROM ref.zone_humide zh
            LEFT JOIN ref.admin_commune c ON zh.id_commune = c.id_commune
            {where_sql}
        """
        return sql, params


class ZoneSableuseGeoJSONView(BaseRefGeoJSONView):
    """Zones sableuses"""

    layer_name = "zones_sableuses"

    def get_sql_query(self, request):
        region = self.get_region(request)

        where_clauses = ["zs.geom IS NOT NULL"]
        params = []
        if region:
            where_clauses.append("c.id_region = %s")
            params.append(region)

        where_sql = "WHERE " + " AND ".join(where_clauses)

        sql = f"""
            SELECT
                zs.zs_id,
                zs.nom,
                zs.nom_fr,
                zs.fclass AS type_zone,
                zs.code,
                c.nom AS nom_commune,
                zs.id_commune,
                ST_AsGeoJSON(zs.geom) as geom_json
            FROM ref.zone_sableuse zs
            LEFT JOIN ref.admin_commune c ON zs.id_commune = c.id_commune
            {where_sql}
        """
        return sql, params


class HydrographieGeoJSONView(BaseRefGeoJSONView):
    """Cours d'eau (hydrographie) - lignes"""

    layer_name = "hydrographie"

    def get_sql_query(self, request):
        sql = """
            SELECT
                id,
                name AS nom,
                french AS nom_fr,
                fclass AS type_hydro,
                code,
                ST_AsGeoJSON(geom) as geom_json
            FROM ref.hydrographie
            WHERE geom IS NOT NULL
        """
        return sql, []


class OccupationSolGeoJSONView(BaseRefGeoJSONView):
    """Occupation du sol"""

    layer_name = "occupation_sol"

    def get_sql_query(self, request):
        region = self.get_region(request)

        where_clauses = ["os.geom IS NOT NULL"]
        params = []
        if region:
            where_clauses.append("c.id_region = %s")
            params.append(region)

        where_sql = "WHERE " + " AND ".join(where_clauses)

        sql = f"""
            SELECT
                os.occsol_id,
                os.code_2020 AS code,
                os.classe,
                os.classe AS type_occupation,
                c.nom AS nom_commune,
                os.id_commune,
                ST_AsGeoJSON(os.geom) as geom_json
            FROM ref.occupation_sol os
            LEFT JOIN ref.admin_commune c ON os.id_commune = c.id_commune
            {where_sql}
        """
        return sql, params


# ============================================================
# INFRASTRUCTURES
# ============================================================

class ReseauRoutierGeoJSONView(BaseRefGeoJSONView):
    """Reseau routier"""

    layer_name = "reseau_routier"

    def get_sql_query(self, request):
        sql = """
            SELECT
                id,
                name AS nom,
                highway AS type_route,
                surface,
                surface AS etat_route,
                ST_Length(ST_Transform(geom, 3857)) / 1000.0 AS longueur_km,
                oneway,
                bridge AS pont,
                tunnel,
                ST_AsGeoJSON(geom) as geom_json
            FROM ref.reseau_routier
            WHERE geom IS NOT NULL
        """
        return sql, []


class EquipementGeoJSONView(BaseRefGeoJSONView):
    """Equipements (ecoles, centres de sante, marches, etc.)"""

    layer_name = "equipements"

    def get_sql_query(self, request):
        type_equip = (
            self._safe_param(request.query_params.get("type", ""), "type")
            if self._is_platform_admin(request)
            else ""
        )
        region = self.get_region(request)

        where_clauses = ["e.geom IS NOT NULL"]
        params = []
        if type_equip:
            where_clauses.append("e.fclass = %s")
            params.append(type_equip)
        if region:
            where_clauses.append("c.id_region = %s")
            params.append(region)

        where_sql = "WHERE " + " AND ".join(where_clauses)

        sql = f"""
            SELECT
                e.equip_id,
                e.nom,
                e.nom_fr,
                e.fclass AS type_equipement,
                e.code,
                c.nom AS nom_commune,
                c.nom AS commune_nom,
                e.id_commune,
                p.nom AS nom_prefecture,
                r.nom AS nom_region,
                ST_AsGeoJSON(e.geom) as geom_json
            FROM ref.equipement e
            LEFT JOIN ref.admin_commune c ON e.id_commune = c.id_commune
            LEFT JOIN ref.admin_prefecture p ON c.id_prefecture = p.id_prefecture
            LEFT JOIN ref.admin_region r ON c.id_region = r.id_region
            {where_sql}
        """
        return sql, params


class LocaliteGeoJSONView(BaseRefGeoJSONView):
    """Localites / villages"""

    layer_name = "localites"

    def get_sql_query(self, request):
        region = self.get_region(request)

        where_clauses = ["l.geom IS NOT NULL"]
        params = []
        if region:
            where_clauses.append("c.id_region = %s")
            params.append(region)

        where_sql = "WHERE " + " AND ".join(where_clauses)

        sql = f"""
            SELECT
                l.localite_id,
                l.nom,
                l.nom AS nom_localite,
                l.nom_fr,
                l.fclass AS type_localite,
                l.population,
                c.nom AS nom_commune,
                c.nom AS commune_nom,
                l.id_commune,
                p.nom AS nom_prefecture,
                r.nom AS nom_region,
                ST_AsGeoJSON(l.geom) as geom_json
            FROM ref.localite l
            LEFT JOIN ref.admin_commune c ON l.id_commune = c.id_commune
            LEFT JOIN ref.admin_prefecture p ON c.id_prefecture = p.id_prefecture
            LEFT JOIN ref.admin_region r ON c.id_region = r.id_region
            {where_sql}
        """
        return sql, params


class AgglomerationGeoJSONView(BaseRefGeoJSONView):
    """Agglomerations / villes"""

    layer_name = "agglomerations"

    def get_sql_query(self, request):
        region = self.get_region(request)

        where_clauses = ["a.geom IS NOT NULL"]
        params = []
        if region:
            where_clauses.append("c.id_region = %s")
            params.append(region)

        where_sql = "WHERE " + " AND ".join(where_clauses)

        sql = f"""
            SELECT
                a.agglom_id,
                a.nom,
                a.nom AS nom_agglomeration,
                a.nom_fr,
                a.fclass AS type_agglo,
                a.code,
                c.nom AS nom_commune,
                c.nom AS commune_nom,
                a.id_commune,
                p.nom AS nom_prefecture,
                r.nom AS nom_region,
                ST_AsGeoJSON(a.geom) as geom_json
            FROM ref.agglomeration a
            LEFT JOIN ref.admin_commune c ON a.id_commune = c.id_commune
            LEFT JOIN ref.admin_prefecture p ON c.id_prefecture = p.id_prefecture
            LEFT JOIN ref.admin_region r ON c.id_region = r.id_region
            {where_sql}
        """
        return sql, params


class HabitationDisperseeGeoJSONView(BaseRefGeoJSONView):
    """Habitations dispersees"""

    layer_name = "habitations_dispersees"

    def get_sql_query(self, request):
        region = self.get_region(request)

        where_clauses = ["h.geom IS NOT NULL"]
        params = []
        if region:
            where_clauses.append("c.id_region = %s")
            params.append(region)

        where_sql = "WHERE " + " AND ".join(where_clauses)

        sql = f"""
            SELECT
                h.hab_id,
                h.nom,
                h.fclass,
                h.type_bat,
                1 AS nb_habitations,
                c.nom AS nom_commune,
                c.nom AS commune_nom,
                h.id_commune,
                p.nom AS nom_prefecture,
                r.nom AS nom_region,
                ST_AsGeoJSON(h.geom) as geom_json
            FROM ref.habitation_dispersee h
            LEFT JOIN ref.admin_commune c ON h.id_commune = c.id_commune
            LEFT JOIN ref.admin_prefecture p ON c.id_prefecture = p.id_prefecture
            LEFT JOIN ref.admin_region r ON c.id_region = r.id_region
            {where_sql}
        """
        return sql, params
