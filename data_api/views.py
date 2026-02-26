from django.db import connection

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.generics import GenericAPIView
from rest_framework.exceptions import PermissionDenied

from .mixins import CurrentProjectRequiredMixin
from .pagination import StandardResultsSetPagination
from .serializers import PingSerializer
from accounts.serializers import RefProjectSerializer
from accounts.models import UserRole

DATA_EDIT_ALLOWED_ROLES = {
    UserRole.MANAGER,
    UserRole.PROJECT_MANAGER,
    UserRole.ADMIN,
}

DATA_PURGE_ALLOWED_ROLES = {
    UserRole.PROJECT_MANAGER,
    UserRole.ADMIN,
}

DATA_ENTITY_EDIT_CONFIG = {
    "cep": {"schema": "core", "table": "cep_parcelle", "id_fields": {"id_cep", "cep_uuid"}, "scope": "self"},
    "intrants": {"schema": "core", "table": "intrant_distribution", "id_fields": {"intrant_uuid"}, "scope": "self"},
    "ouvrages": {"schema": "core", "table": "ouvrage", "id_fields": {"ouvrage_uuid", "code_ouvrage"}, "scope": "self"},
    "zones_degradees": {"schema": "core", "table": "zone_degradee", "id_fields": {"zone_uuid", "id_zone"}, "scope": "self"},
    "tetes_sources": {"schema": "core", "table": "tete_source", "id_fields": {"ts_uuid", "id_ts"}, "scope": "self"},
    "couloirs": {"schema": "core", "table": "couloir", "id_fields": {"id_couloir"}, "scope": "self"},
    "organisations": {"schema": "core", "table": "agr_organisation", "id_fields": {"id_org", "org_uuid"}, "scope": "self"},
    "menages": {"schema": "core", "table": "agr_menage", "id_fields": {"id_menage", "menage_uuid"}, "scope": "self"},
    "comites": {"schema": "core", "table": "agr_comite", "id_fields": {"id_comite", "comite_uuid"}, "scope": "self"},
    "stations_meteo": {"schema": "core", "table": "meteo_station", "id_fields": {"station_uuid", "code_station"}, "scope": "self"},
    "marches": {"schema": "core", "table": "marche", "id_fields": {"marche_uuid", "id_marche"}, "scope": "self"},
    "formations": {"schema": "core", "table": "formation_eco", "id_fields": {"formation_uuid", "id_formation"}, "scope": "self"},
    "entreprises": {"schema": "core", "table": "entreprise_econ", "id_fields": {"ent_uuid", "id_ent"}, "scope": "self"},
    "sortants": {"schema": "core", "table": "fiere_suivi_sortant", "id_fields": {"suivi_uuid", "id_sortant"}, "scope": "self"},
    "emplois": {"schema": "core", "table": "ent_emploi_dom", "id_fields": {"emploi_dom_uuid"}, "scope": "emploi_parent"},
    "insertions": {"schema": "core", "table": "ent_insertion_dom", "id_fields": {"insertion_dom_uuid"}, "scope": "insertion_parent"},
}

DATA_ENTITY_PROTECTED_COLUMNS = {
    "created_at",
    "updated_at",
    "valid_from",
    "valid_to",
    "record_source",
    "raw_uuid",
    "project_code",
    "id_region",
    "id_prefecture",
    "id_commune",
    "geom",
    "geom_point",
    "geom_zone",
}


def is_global_admin(user) -> bool:
    role = str(getattr(user, "role", "") or "").strip().lower()
    return bool(
        user
        and user.is_authenticated
        and (user.is_superuser or user.is_staff or role == UserRole.ADMIN)
    )


def is_project_admin(user) -> bool:
    role = str(getattr(user, "role", "") or "").strip().lower()
    return bool(
        user
        and user.is_authenticated
        and (user.is_superuser or user.is_staff or role in {UserRole.ADMIN, UserRole.PROJECT_MANAGER})
    )


def get_user_region_ids(user):
    if is_project_admin(user):
        return []

    if hasattr(user, "regions"):
        ids = [rid for rid in user.regions.values_list("id_region", flat=True) if rid]
        if ids:
            return ids

    region_id = getattr(user, "region_id", None)
    if region_id:
        return [region_id]

    # Fail closed: aucun scope rÃ©gional => aucune donnÃ©e
    return ["__NO_REGION__"]


def build_access_scope_for_project(request, project_code: str):
    """
    Portee d'acces serveur (projet + region):
    - admin plateforme: acces global
    - non-admin: region imposee (fail-closed)
    """
    where_clauses = ["project_code = %s"]
    params: list = [project_code]

    user = request.user
    if is_project_admin(user):
        return where_clauses, params

    region_ids = get_user_region_ids(user)
    if region_ids:
        where_clauses.append("id_region = ANY(%s)")
        params.append(region_ids)

    requested_region = str(request.query_params.get("region_id", "") or "").strip()
    if requested_region and requested_region not in region_ids:
        raise PermissionDenied("Acces refuse: region hors perimetre de votre compte.")

    return where_clauses, params


def sql_true(column: str) -> str:
    """
    Expression SQL robuste pour interprÃ©ter un boolÃ©en, que la colonne soit
    de type BOOLEAN ou TEXT ('oui'/'true'/etc.).
    """
    return (
        f"LOWER(COALESCE({column}::text, '')) IN "
        "('true','t','1','yes','y','oui','vrai')"
    )


def sql_bool(column: str, expected: bool) -> str:
    expr = sql_true(column)
    return expr if expected else f"NOT ({expr})"


def filter_by_access(queryset, user):
    """
    Restreint un queryset en fonction :
      - des projets associÃ©s Ã  l'utilisateur (RefProject via code_kobo),
      - des rÃ©gions associÃ©es (Region via id_region).

    Les admins (superuser ou role=admin) voient tout.
    """
    if not user.is_authenticated:
        return queryset.none()

    if is_global_admin(user):
        return queryset

    # Filtre par projets (ref.projet.code_kobo)
    project_codes = list(
        user.projects.values_list("code_kobo", flat=True)
    )
    if project_codes:
        queryset = queryset.filter(project_code__in=project_codes)
    else:
        return queryset.none()

    # Filtre par rÃ©gions (ref.admin_region.id_region)
    region_ids = get_user_region_ids(user)
    if region_ids:
        queryset = queryset.filter(id_region__in=region_ids)

    return queryset



class PingView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # petit check DB
        db_ok = False
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                db_ok = cursor.fetchone()[0] == 1
        except Exception:
            db_ok = False

        return Response(
            {
                "status": "ok",
                "db": "ok" if db_ok else "error",
                "user": request.user.username if request.user.is_authenticated else None,
            }
        )



class DataEntityUpdateView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Edition controlee des donnees metier (core.*) depuis le module Donnees.
    - autorise uniquement admin N1/N2/global (manager, project_manager, admin)
    - scope projet obligatoire + scope region pour profils non project-admin
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "admin_write"

    @staticmethod
    def _quote_ident(identifier: str) -> str:
        return '"' + str(identifier).replace('"', '""') + '"'

    def _can_edit(self, user) -> bool:
        role = str(getattr(user, "role", "") or "").strip().lower()
        return bool(
            user
            and user.is_authenticated
            and (user.is_superuser or user.is_staff or role in DATA_EDIT_ALLOWED_ROLES)
        )

    def _get_table_columns(self, schema: str, table: str) -> set[str]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = %s
                """,
                [schema, table],
            )
            rows = cursor.fetchall()
        return {str(r[0]) for r in rows if r and r[0]}

    def _build_update_sql(self, cfg: dict, id_field: str, filtered_changes: dict, project_code: str, user):
        schema = cfg["schema"]
        table = cfg["table"]
        scope = cfg.get("scope", "self")
        columns = self._get_table_columns(schema, table)

        if id_field not in columns:
            return None, None, f"Champ identifiant '{id_field}' absent de {schema}.{table}."

        set_clauses = []
        params: list = []
        for key, value in filtered_changes.items():
            set_clauses.append(f"{self._quote_ident(key)} = %s")
            params.append(value)

        if "updated_at" in columns:
            set_clauses.append('"updated_at" = NOW()')

        if not set_clauses:
            return None, None, "Aucune colonne modifiable detectee dans la requete."

        where_clauses = [f"t.{self._quote_ident(id_field)} = %s"]
        from_clause = ""

        if scope == "self":
            if "project_code" in columns:
                where_clauses.append('t."project_code" = %s')
                params.append(project_code)
            else:
                return None, None, f"Table cible {schema}.{table} sans colonne project_code."

            if not is_project_admin(user) and "id_region" in columns:
                region_ids = get_user_region_ids(user)
                where_clauses.append('t."id_region" = ANY(%s)')
                params.append(region_ids)

        elif scope == "emploi_parent":
            from_clause = ' FROM "core"."ent_emploi" AS p'
            where_clauses.append('p."emploi_uuid" = t."emploi_uuid"')
            where_clauses.append('p."project_code" = %s')
            params.append(project_code)

            if not is_project_admin(user):
                region_ids = get_user_region_ids(user)
                where_clauses.append('p."id_region" = ANY(%s)')
                params.append(region_ids)

        elif scope == "insertion_parent":
            from_clause = ' FROM "core"."ent_insertion" AS p'
            where_clauses.append('p."insertion_uuid" = t."insertion_uuid"')
            where_clauses.append('p."project_code" = %s')
            params.append(project_code)

            if not is_project_admin(user):
                region_ids = get_user_region_ids(user)
                where_clauses.append('p."id_region" = ANY(%s)')
                params.append(region_ids)

        else:
            return None, None, f"Scope d'edition non supporte: {scope}."

        target_table = f"{self._quote_ident(schema)}.{self._quote_ident(table)}"
        sql = (
            f"UPDATE {target_table} AS t "
            f"SET {', '.join(set_clauses)}"
            f"{from_clause} "
            f"WHERE {' AND '.join(where_clauses)}"
        )
        return sql, params, None

    def patch(self, request, table_id: str):
        if not self._can_edit(request.user):
            return Response(
                {"detail": "Modification reservee aux admins N1/N2/global."},
                status=status.HTTP_403_FORBIDDEN,
            )

        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        cfg = DATA_ENTITY_EDIT_CONFIG.get(str(table_id or "").strip().lower())
        if not cfg:
            return Response(
                {"detail": f"Table '{table_id}' non supportee pour edition."},
                status=status.HTTP_404_NOT_FOUND,
            )

        payload = request.data if isinstance(request.data, dict) else {}
        entity_id = str(payload.get("id", "") or "").strip()
        id_field = str(payload.get("id_field", "") or "").strip()
        changes = payload.get("changes")

        if not entity_id:
            return Response({"detail": "Champ 'id' obligatoire."}, status=status.HTTP_400_BAD_REQUEST)
        if not id_field:
            return Response({"detail": "Champ 'id_field' obligatoire."}, status=status.HTTP_400_BAD_REQUEST)
        if id_field not in cfg["id_fields"]:
            return Response(
                {"detail": f"Identifiant '{id_field}' non autorise pour '{table_id}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not isinstance(changes, dict) or not changes:
            return Response(
                {"detail": "Champ 'changes' obligatoire (objet non vide)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        schema = cfg["schema"]
        table = cfg["table"]
        table_columns = self._get_table_columns(schema, table)

        filtered_changes = {}
        ignored_fields = []
        for key, value in changes.items():
            field = str(key or "").strip()
            if not field:
                continue
            if field in DATA_ENTITY_PROTECTED_COLUMNS:
                ignored_fields.append(field)
                continue
            if field.endswith("_label") or field.endswith("_labels"):
                ignored_fields.append(field)
                continue
            if field not in table_columns:
                ignored_fields.append(field)
                continue
            filtered_changes[field] = value

        if not filtered_changes:
            return Response(
                {
                    "detail": "Aucune colonne editable n'a ete soumise.",
                    "ignored_fields": sorted(set(ignored_fields)),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        sql, params, sql_error = self._build_update_sql(
            cfg=cfg,
            id_field=id_field,
            filtered_changes=filtered_changes,
            project_code=project.code_fonc,
            user=request.user,
        )
        if sql_error:
            return Response({"detail": sql_error}, status=status.HTTP_400_BAD_REQUEST)

        with connection.cursor() as cursor:
            ordered_params = params[:len(filtered_changes)] + [entity_id] + params[len(filtered_changes):]
            cursor.execute(sql, ordered_params)
            affected = int(cursor.rowcount or 0)

        if affected <= 0:
            return Response(
                {"detail": "Enregistrement introuvable ou hors perimetre de vos droits."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "detail": "Modification enregistree.",
                "table": table_id,
                "id_field": id_field,
                "id": entity_id,
                "updated_fields": sorted(filtered_changes.keys()),
                "ignored_fields": sorted(set(ignored_fields)),
                "affected_rows": affected,
                "source_schema": f"{schema}.{table}",
            }
        )


class DataEntityDeleteView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Suppression controlee d'une ligne metier (core.*) depuis le module Donnees.
    - autorise N1/N2/global (manager, project_manager, admin)
    - scope projet obligatoire + scope region pour profils non project-admin
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "admin_write"

    @staticmethod
    def _quote_ident(identifier: str) -> str:
        return '"' + str(identifier).replace('"', '""') + '"'

    def _can_delete(self, user) -> bool:
        role = str(getattr(user, "role", "") or "").strip().lower()
        return bool(
            user
            and user.is_authenticated
            and (user.is_superuser or user.is_staff or role in DATA_EDIT_ALLOWED_ROLES)
        )

    def _get_table_columns(self, schema: str, table: str) -> set[str]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = %s
                """,
                [schema, table],
            )
            rows = cursor.fetchall()
        return {str(r[0]) for r in rows if r and r[0]}

    def _resolve_id_field(self, cfg: dict, requested_id_field: str, table_id: str):
        id_field = str(requested_id_field or "").strip()
        if not id_field and len(cfg["id_fields"]) == 1:
            id_field = next(iter(cfg["id_fields"]))

        if not id_field:
            return None, Response(
                {"detail": "Champ 'id_field' obligatoire."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if id_field not in cfg["id_fields"]:
            return None, Response(
                {"detail": f"Identifiant '{id_field}' non autorise pour '{table_id}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return id_field, None

    def _build_delete_sql(self, cfg: dict, id_field: str, project_code: str, user):
        schema = cfg["schema"]
        table = cfg["table"]
        scope = cfg.get("scope", "self")
        columns = self._get_table_columns(schema, table)

        if id_field not in columns:
            return None, None, f"Champ identifiant '{id_field}' absent de {schema}.{table}."

        where_clauses = [f"t.{self._quote_ident(id_field)}::text = %s"]
        params: list = []
        from_clause = ""

        if scope == "self":
            if "project_code" not in columns:
                return None, None, f"Table cible {schema}.{table} sans colonne project_code."
            where_clauses.append('t."project_code" = %s')

            if not is_project_admin(user) and "id_region" in columns:
                region_ids = get_user_region_ids(user)
                where_clauses.append('t."id_region" = ANY(%s)')
                params.append(region_ids)

        elif scope == "emploi_parent":
            from_clause = ' USING "core"."ent_emploi" AS p'
            where_clauses.append('p."emploi_uuid" = t."emploi_uuid"')
            where_clauses.append('p."project_code" = %s')

            if not is_project_admin(user):
                region_ids = get_user_region_ids(user)
                where_clauses.append('p."id_region" = ANY(%s)')
                params.append(region_ids)

        elif scope == "insertion_parent":
            from_clause = ' USING "core"."ent_insertion" AS p'
            where_clauses.append('p."insertion_uuid" = t."insertion_uuid"')
            where_clauses.append('p."project_code" = %s')

            if not is_project_admin(user):
                region_ids = get_user_region_ids(user)
                where_clauses.append('p."id_region" = ANY(%s)')
                params.append(region_ids)

        else:
            return None, None, f"Scope de suppression non supporte: {scope}."

        target_table = f"{self._quote_ident(schema)}.{self._quote_ident(table)}"
        sql = f"DELETE FROM {target_table} AS t{from_clause} WHERE {' AND '.join(where_clauses)}"
        ordered_params = [None, project_code, *params]
        return sql, ordered_params, None

    def delete(self, request, table_id: str, record_id: str):
        if not self._can_delete(request.user):
            return Response(
                {"detail": "Suppression reservee aux admins N1/N2/global."},
                status=status.HTTP_403_FORBIDDEN,
            )

        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        cfg = DATA_ENTITY_EDIT_CONFIG.get(str(table_id or "").strip().lower())
        if not cfg:
            return Response(
                {"detail": f"Table '{table_id}' non supportee pour suppression."},
                status=status.HTTP_404_NOT_FOUND,
            )

        id_field, id_error = self._resolve_id_field(cfg, request.query_params.get("id_field", ""), table_id)
        if id_error is not None:
            return id_error

        entity_id = str(record_id or "").strip()
        if not entity_id:
            return Response({"detail": "Identifiant vide."}, status=status.HTTP_400_BAD_REQUEST)

        sql, template_params, sql_error = self._build_delete_sql(
            cfg=cfg,
            id_field=id_field,
            project_code=project.code_fonc,
            user=request.user,
        )
        if sql_error:
            return Response({"detail": sql_error}, status=status.HTTP_400_BAD_REQUEST)

        params = template_params[:]
        params[0] = entity_id

        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            affected = int(cursor.rowcount or 0)

        if affected <= 0:
            return Response(
                {"detail": "Enregistrement introuvable ou hors perimetre de vos droits."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "detail": "Suppression enregistree.",
                "table": table_id,
                "id_field": id_field,
                "id": entity_id,
                "deleted_count": affected,
            }
        )


class DataEntityBulkDeleteView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Suppression en lot de lignes metier (core.*) depuis le module Donnees.
    - autorise N1/N2/global (manager, project_manager, admin)
    - scope projet obligatoire + scope region pour profils non project-admin
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "admin_write"
    max_bulk_delete_ids = 20000

    @staticmethod
    def _quote_ident(identifier: str) -> str:
        return '"' + str(identifier).replace('"', '""') + '"'

    def _can_delete(self, user) -> bool:
        role = str(getattr(user, "role", "") or "").strip().lower()
        return bool(
            user
            and user.is_authenticated
            and (user.is_superuser or user.is_staff or role in DATA_EDIT_ALLOWED_ROLES)
        )

    def _get_table_columns(self, schema: str, table: str) -> set[str]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = %s
                """,
                [schema, table],
            )
            rows = cursor.fetchall()
        return {str(r[0]) for r in rows if r and r[0]}

    def _resolve_id_field(self, cfg: dict, requested_id_field: str, table_id: str):
        id_field = str(requested_id_field or "").strip()
        if not id_field and len(cfg["id_fields"]) == 1:
            id_field = next(iter(cfg["id_fields"]))

        if not id_field:
            return None, Response(
                {"detail": "Champ 'id_field' obligatoire."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if id_field not in cfg["id_fields"]:
            return None, Response(
                {"detail": f"Identifiant '{id_field}' non autorise pour '{table_id}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return id_field, None

    def _normalize_ids(self, raw_ids):
        if not isinstance(raw_ids, list):
            return None, Response(
                {"detail": "Champ 'ids' obligatoire (liste non vide)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        seen: set[str] = set()
        cleaned: list[str] = []
        for raw in raw_ids:
            text = str(raw or "").strip()
            if not text:
                continue
            if text in seen:
                continue
            seen.add(text)
            cleaned.append(text)

        if not cleaned:
            return None, Response(
                {"detail": "Champ 'ids' obligatoire (liste non vide)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(cleaned) > self.max_bulk_delete_ids:
            return None, Response(
                {"detail": f"Trop d'identifiants. Maximum autorise: {self.max_bulk_delete_ids}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return cleaned, None

    def _build_bulk_delete_sql(self, cfg: dict, id_field: str, project_code: str, user):
        schema = cfg["schema"]
        table = cfg["table"]
        scope = cfg.get("scope", "self")
        columns = self._get_table_columns(schema, table)

        if id_field not in columns:
            return None, None, f"Champ identifiant '{id_field}' absent de {schema}.{table}."

        where_clauses = [f"t.{self._quote_ident(id_field)}::text = ANY(%s)"]
        params: list = []
        from_clause = ""

        if scope == "self":
            if "project_code" not in columns:
                return None, None, f"Table cible {schema}.{table} sans colonne project_code."
            where_clauses.append('t."project_code" = %s')

            if not is_project_admin(user) and "id_region" in columns:
                region_ids = get_user_region_ids(user)
                where_clauses.append('t."id_region" = ANY(%s)')
                params.append(region_ids)

        elif scope == "emploi_parent":
            from_clause = ' USING "core"."ent_emploi" AS p'
            where_clauses.append('p."emploi_uuid" = t."emploi_uuid"')
            where_clauses.append('p."project_code" = %s')

            if not is_project_admin(user):
                region_ids = get_user_region_ids(user)
                where_clauses.append('p."id_region" = ANY(%s)')
                params.append(region_ids)

        elif scope == "insertion_parent":
            from_clause = ' USING "core"."ent_insertion" AS p'
            where_clauses.append('p."insertion_uuid" = t."insertion_uuid"')
            where_clauses.append('p."project_code" = %s')

            if not is_project_admin(user):
                region_ids = get_user_region_ids(user)
                where_clauses.append('p."id_region" = ANY(%s)')
                params.append(region_ids)

        else:
            return None, None, f"Scope de suppression non supporte: {scope}."

        target_table = f"{self._quote_ident(schema)}.{self._quote_ident(table)}"
        sql = f"DELETE FROM {target_table} AS t{from_clause} WHERE {' AND '.join(where_clauses)}"
        ordered_params = [None, project_code, *params]
        return sql, ordered_params, None

    def post(self, request, table_id: str):
        if not self._can_delete(request.user):
            return Response(
                {"detail": "Suppression reservee aux admins N1/N2/global."},
                status=status.HTTP_403_FORBIDDEN,
            )

        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        cfg = DATA_ENTITY_EDIT_CONFIG.get(str(table_id or "").strip().lower())
        if not cfg:
            return Response(
                {"detail": f"Table '{table_id}' non supportee pour suppression."},
                status=status.HTTP_404_NOT_FOUND,
            )

        payload = request.data if isinstance(request.data, dict) else {}
        id_field, id_error = self._resolve_id_field(cfg, payload.get("id_field", ""), table_id)
        if id_error is not None:
            return id_error

        ids, ids_error = self._normalize_ids(payload.get("ids"))
        if ids_error is not None:
            return ids_error

        sql, template_params, sql_error = self._build_bulk_delete_sql(
            cfg=cfg,
            id_field=id_field,
            project_code=project.code_fonc,
            user=request.user,
        )
        if sql_error:
            return Response({"detail": sql_error}, status=status.HTTP_400_BAD_REQUEST)

        params = template_params[:]
        params[0] = ids

        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            affected = int(cursor.rowcount or 0)

        return Response(
            {
                "detail": "Suppression en lot terminee.",
                "table": table_id,
                "id_field": id_field,
                "requested_count": len(ids),
                "deleted_count": affected,
            }
        )


class DataEntityPurgeView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Purge d'une table metier (core.*) pour le projet actif.
    - reserve N2/global (project_manager, admin)
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "admin_write"

    @staticmethod
    def _quote_ident(identifier: str) -> str:
        return '"' + str(identifier).replace('"', '""') + '"'

    def _can_purge(self, user) -> bool:
        role = str(getattr(user, "role", "") or "").strip().lower()
        return bool(
            user
            and user.is_authenticated
            and (user.is_superuser or user.is_staff or role in DATA_PURGE_ALLOWED_ROLES)
        )

    def _get_table_columns(self, schema: str, table: str) -> set[str]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = %s
                """,
                [schema, table],
            )
            rows = cursor.fetchall()
        return {str(r[0]) for r in rows if r and r[0]}

    def _build_purge_sql(self, cfg: dict, project_code: str):
        schema = cfg["schema"]
        table = cfg["table"]
        scope = cfg.get("scope", "self")
        columns = self._get_table_columns(schema, table)

        where_clauses = []
        params: list = [project_code]
        from_clause = ""

        if scope == "self":
            if "project_code" not in columns:
                return None, None, f"Table cible {schema}.{table} sans colonne project_code."
            where_clauses.append('t."project_code" = %s')

        elif scope == "emploi_parent":
            from_clause = ' USING "core"."ent_emploi" AS p'
            where_clauses.append('p."emploi_uuid" = t."emploi_uuid"')
            where_clauses.append('p."project_code" = %s')

        elif scope == "insertion_parent":
            from_clause = ' USING "core"."ent_insertion" AS p'
            where_clauses.append('p."insertion_uuid" = t."insertion_uuid"')
            where_clauses.append('p."project_code" = %s')

        else:
            return None, None, f"Scope de purge non supporte: {scope}."

        target_table = f"{self._quote_ident(schema)}.{self._quote_ident(table)}"
        sql = f"DELETE FROM {target_table} AS t{from_clause} WHERE {' AND '.join(where_clauses)}"
        return sql, params, None

    def post(self, request, table_id: str):
        if not self._can_purge(request.user):
            return Response(
                {"detail": "Purge reservee aux admins N2/global."},
                status=status.HTTP_403_FORBIDDEN,
            )

        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        cfg = DATA_ENTITY_EDIT_CONFIG.get(str(table_id or "").strip().lower())
        if not cfg:
            return Response(
                {"detail": f"Table '{table_id}' non supportee pour purge."},
                status=status.HTTP_404_NOT_FOUND,
            )

        sql, params, sql_error = self._build_purge_sql(cfg=cfg, project_code=project.code_fonc)
        if sql_error:
            return Response({"detail": sql_error}, status=status.HTTP_400_BAD_REQUEST)

        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            affected = int(cursor.rowcount or 0)

        return Response(
            {
                "detail": "Purge terminee pour le projet actif.",
                "table": table_id,
                "project_code": project.code_fonc,
                "purged_count": affected,
            }
        )

# -----------------------------------------------------------------------------
# Liste des Entreprises
# -----------------------------------------------------------------------------

class EntrepriseListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Liste les entreprises Ã©conomiques depuis la vue marts.vw_entreprise_econ,
    filtrÃ©es par projet actif via project_code (FIERE / AGRIECO)
    ET par rÃ©gion(s) autorisÃ©e(s) pour l'utilisateur connectÃ©.

    Filtres possibles en query string :

    - ?search=...                     (raison_sociale, nom_commercial,
                                       activite_detaillee, obs_entreprise, localite)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?secteur_principal=...         (code ref.secteur_eco)
    - ?taille_entreprise=...         (code ref.taille_entreprise)
    - ?marche_principal=...          (code ref.marche_principal)
    - ?enregistre_formel=oui|non
    - ?est_mpme_formalisee=true|false
    - ?est_mpme_appuyee=true|false   (utilise le champ boolÃ©en est_mpme_appuyee_fiere)
    - ?id_ent=...
    - ?has_geom=true|false           (prÃ©sence de gÃ©omÃ©trie)
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        # --- Projet courant (FIERE / AGRIECO) ---
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        # Ici on suppose que project_code dans les vues marts = code_fonc du projet
        project_code = project.code_fonc

        # -------- Filtres venant de la requÃªte --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        secteur_principal = request.query_params.get("secteur_principal")
        taille_entreprise = request.query_params.get("taille_entreprise")
        marche_principal = request.query_params.get("marche_principal")
        enregistre_formel = request.query_params.get("enregistre_formel")

        est_mpme_formalisee = request.query_params.get("est_mpme_formalisee")
        est_mpme_appuyee = request.query_params.get("est_mpme_appuyee")

        id_ent = request.query_params.get("id_ent")
        has_geom = request.query_params.get("has_geom")

        search = request.query_params.get("search")

        # -------- Construction du WHERE SQL --------
        where_clauses, params = build_access_scope_for_project(request, project_code)

        # --- Filtres territoriaux explicites (on intersecte avec les droits de l'utilisateur) ---
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # --- CaractÃ©ristiques de lâ€™entreprise ---
        if secteur_principal:
            where_clauses.append("secteur_principal = %s")
            params.append(secteur_principal)

        if taille_entreprise:
            where_clauses.append("taille_entreprise = %s")
            params.append(taille_entreprise)

        if marche_principal:
            where_clauses.append("marche_principal = %s")
            params.append(marche_principal)

        if enregistre_formel:
            # on suppose la valeur brute 'oui' / 'non' dans la colonne enregistre_formel
            where_clauses.append("enregistre_formel = %s")
            params.append(enregistre_formel)

        # --- BoolÃ©ens dÃ©rivÃ©s ---
        if est_mpme_formalisee in ("true", "false"):
            where_clauses.append(sql_bool("est_mpme_formalisee", est_mpme_formalisee == "true"))

        if est_mpme_appuyee in ("true", "false"):
            where_clauses.append(sql_bool("est_mpme_appuyee_fiere", est_mpme_appuyee == "true"))

        if id_ent:
            where_clauses.append("id_ent = %s")
            params.append(id_ent)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # --- Recherche texte ---
        if search:
            where_clauses.append(
                "("
                "raison_sociale ILIKE %s OR "
                "nom_commercial ILIKE %s OR "
                "activite_detaillee ILIKE %s OR "
                "obs_entreprise ILIKE %s OR "
                "localite ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 5)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        # -------- RequÃªtes SQL --------
        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_entreprise_econ
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_entreprise_econ
                WHERE {where_sql}
                ORDER BY raison_sociale NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class EntrepriseAggregationView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Vue d'agrÃ©gation pour les entreprises Ã©conomiques (marts.vw_entreprise_econ).

    Renvoie des indicateurs globaux (pour le tableau de bord), filtrÃ©s par :
    - projet actif (FIERE / AGRIECO),
    - rÃ©gion(s) de l'utilisateur (Mamou / Kindia),
    - les mÃªmes filtres que la liste (region_id, secteur, etc.).

    Exemple de rÃ©ponse :
    {
      "total_entreprises": 123,
      "total_mpme_formalisees": 45,
      "total_mpme_appuyees_fiere": 17
    }
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # --- Projet courant ---
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc

        # -------- Filtres de la requÃªte --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        secteur_principal = request.query_params.get("secteur_principal")
        taille_entreprise = request.query_params.get("taille_entreprise")
        marche_principal = request.query_params.get("marche_principal")
        enregistre_formel = request.query_params.get("enregistre_formel")

        est_mpme_formalisee = request.query_params.get("est_mpme_formalisee")
        est_mpme_appuyee = request.query_params.get("est_mpme_appuyee")

        id_ent = request.query_params.get("id_ent")
        has_geom = request.query_params.get("has_geom")

        search = request.query_params.get("search")

        # -------- WHERE SQL de base --------
        where_clauses, params = build_access_scope_for_project(request, project_code)

        # --- Filtres territoriaux ---
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # --- CaractÃ©ristiques entreprise ---
        if secteur_principal:
            where_clauses.append("secteur_principal = %s")
            params.append(secteur_principal)

        if taille_entreprise:
            where_clauses.append("taille_entreprise = %s")
            params.append(taille_entreprise)

        if marche_principal:
            where_clauses.append("marche_principal = %s")
            params.append(marche_principal)

        if enregistre_formel:
            where_clauses.append("enregistre_formel = %s")
            params.append(enregistre_formel)

        # --- BoolÃ©ens dÃ©rivÃ©s ---
        if est_mpme_formalisee in ("true", "false"):
            where_clauses.append(sql_bool("est_mpme_formalisee", est_mpme_formalisee == "true"))

        if est_mpme_appuyee in ("true", "false"):
            where_clauses.append(sql_bool("est_mpme_appuyee_fiere", est_mpme_appuyee == "true"))

        if id_ent:
            where_clauses.append("id_ent = %s")
            params.append(id_ent)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # --- Recherche texte ---
        if search:
            where_clauses.append(
                "("
                "raison_sociale ILIKE %s OR "
                "nom_commercial ILIKE %s OR "
                "activite_detaillee ILIKE %s OR "
                "obs_entreprise ILIKE %s OR "
                "localite ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 5)

        where_sql = " AND ".join(where_clauses)

        # -------- RequÃªte d'agrÃ©gation --------
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS total_entreprises,
                    COUNT(*) FILTER (WHERE {sql_true("est_mpme_formalisee")})
                        AS total_mpme_formalisees,
                    COUNT(*) FILTER (WHERE {sql_true("est_mpme_appuyee_fiere")})
                        AS total_mpme_appuyees_fiere
                FROM marts.vw_entreprise_econ
                WHERE {where_sql}
                """,
                params,
            )
            row = cursor.fetchone()

            cursor.execute(
                f"""
                SELECT
                    taille_entreprise,
                    COUNT(*) AS nb_entreprises
                FROM marts.vw_entreprise_econ
                WHERE {where_sql}
                GROUP BY taille_entreprise
                ORDER BY nb_entreprises DESC
                """,
                params,
            )
            taille_rows = cursor.fetchall()

        global_stats = {
            "nb_entreprises": row[0] or 0,
            "total_entreprises": row[0] or 0,
            "total_mpme_formalisees": row[1] or 0,
            "total_mpme_appuyees_fiere": row[2] or 0,
        }

        data = {
            "global": global_stats,
            "by_taille": [
                {
                    "taille_entreprise": r[0],
                    "taille_label": r[0] or "Non renseigne",
                    "nb_entreprises": r[1] or 0,
                }
                for r in taille_rows
            ],
        }

        return Response(data)



# -----------------------------------------------------------------------------
# Liste des acteurs participants
# -----------------------------------------------------------------------------

class ActeurParticipationListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Liste les acteurs / partenaires depuis la vue marts.vw_acteur_participation,
    filtrÃ©s par projet actif via project_code (FIERE / AGRIECO).

    Filtres possibles en query string :
    - ?search=...                     (nom_acteur, intitule_dispositif,
                                       objet_participation, resultats_obtenus,
                                       contraintes_particip, obs_participation, localite)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?type_acteur=...               (code ref.type_acteur)
    - ?type_participation=...        (code ref.type_participation)
    - ?statut_convention=...         (code ref.statut_convention_cfpa)
    - ?convention_cfpa_active=true|false
    - ?id_ent=...
    - ?has_geom=true|false           (prÃ©sence de gÃ©omÃ©trie)
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        # 1) Projet courant (FIERE / AGRIECO)
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        type_acteur = request.query_params.get("type_acteur")
        type_participation = request.query_params.get("type_participation")
        statut_convention = request.query_params.get("statut_convention")
        convention_cfpa_active = request.query_params.get("convention_cfpa_active")

        id_ent = request.query_params.get("id_ent")
        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques de la participation
        if type_acteur:
            where_clauses.append("type_acteur = %s")
            params.append(type_acteur)

        if type_participation:
            where_clauses.append("type_participation = %s")
            params.append(type_participation)

        if statut_convention:
            where_clauses.append("statut_convention = %s")
            params.append(statut_convention)

        if convention_cfpa_active in ("true", "false"):
            where_clauses.append(sql_bool("convention_cfpa_active", convention_cfpa_active == "true"))

        if id_ent:
            where_clauses.append("id_ent = %s")
            params.append(id_ent)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte large
        if search:
            where_clauses.append(
                "("
                "nom_acteur ILIKE %s OR "
                "intitule_dispositif ILIKE %s OR "
                "objet_participation ILIKE %s OR "
                "resultats_obtenus ILIKE %s OR "
                "contraintes_particip ILIKE %s OR "
                "obs_participation ILIKE %s OR "
                "localite ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 7)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        # -------- RequÃªtes SQL --------
        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_acteur_participation
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_acteur_participation
                WHERE {where_sql}
                ORDER BY nom_acteur NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        # -------- Liens next / previous --------
        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class ActeurParticipationAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    AgrÃ©gations pour les acteurs de participation (marts.vw_acteur_participation),
    filtrÃ©es par projet actif (FIERE / AGRIECO) et par territoire.

    MÃªme filtres que la liste :
    - ?search=...
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?type_acteur=...
    - ?type_participation=...
    - ?statut_convention=...
    - ?convention_cfpa_active=true|false
    - ?id_ent=...
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 1) Projet courant
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        type_acteur = request.query_params.get("type_acteur")
        type_participation = request.query_params.get("type_participation")
        statut_convention = request.query_params.get("statut_convention")
        convention_cfpa_active = request.query_params.get("convention_cfpa_active")

        id_ent = request.query_params.get("id_ent")
        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques de la participation
        if type_acteur:
            where_clauses.append("type_acteur = %s")
            params.append(type_acteur)

        if type_participation:
            where_clauses.append("type_participation = %s")
            params.append(type_participation)

        if statut_convention:
            where_clauses.append("statut_convention = %s")
            params.append(statut_convention)

        if convention_cfpa_active in ("true", "false"):
            where_clauses.append(sql_bool("convention_cfpa_active", convention_cfpa_active == "true"))

        if id_ent:
            where_clauses.append("id_ent = %s")
            params.append(id_ent)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte (cohÃ©rente avec la liste)
        if search:
            where_clauses.append(
                "("
                "nom_acteur ILIKE %s OR "
                "intitule_dispositif ILIKE %s OR "
                "objet_participation ILIKE %s OR "
                "resultats_obtenus ILIKE %s OR "
                "contraintes_particip ILIKE %s OR "
                "obs_participation ILIKE %s OR "
                "localite ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 7)

        where_sql = " AND ".join(where_clauses)

        data = {}

        with connection.cursor() as cursor:
            # ---- 1) Statistiques globales ----
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS total_participations,
                    COUNT(DISTINCT nom_acteur) AS distinct_acteurs,
                    COUNT(*) FILTER (
                        WHERE LOWER(est_entreprise_fiere::text) = 'oui'
                    ) AS nb_entreprises_fiere,
                    COUNT(*) FILTER (
                        WHERE type_participation = 'CONV_CFPA_ENT'
                    ) AS nb_conventions_cfpa,
                    COUNT(*) FILTER (
                        WHERE {sql_true("convention_cfpa_active")}
                    ) AS nb_conventions_cfpa_actives,
                    COALESCE(SUM(nb_part_12m), 0) AS total_nb_part_12m,
                    AVG(nb_part_12m::numeric) AS avg_nb_part_12m,
                    COUNT(*) FILTER (WHERE geom IS NOT NULL) AS nb_avec_geom,
                    COUNT(*) FILTER (WHERE geom IS NULL) AS nb_sans_geom
                FROM marts.vw_acteur_participation
                WHERE {where_sql}
                """,
                params,
            )
            row = cursor.fetchone()
            data["global"] = {
                "total_participations": row[0],
                "distinct_acteurs": row[1],
                "total_acteurs": row[1],  # alias dashboard
                "nb_acteurs": row[1],     # alias dashboard
                "nb_entreprises_fiere": row[2],
                "nb_conventions_cfpa": row[3],
                "nb_conventions_cfpa_actives": row[4],
                "nb_partenariats_actifs": row[4],  # alias dashboard
                "total_nb_part_12m": row[5],
                "nb_stages_courts": 0,  # pas de source fiable dans ce mart
                "avg_nb_part_12m": float(row[6]) if row[6] is not None else None,
                "nb_avec_geom": row[7],
                "nb_sans_geom": row[8],
            }

            # ---- 2) Par type d'acteur ----
            cursor.execute(
                f"""
                SELECT
                    type_acteur,
                    COALESCE(type_acteur_label, type_acteur) AS label,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE {sql_true("convention_cfpa_active")})
                        AS conventions_actives
                FROM marts.vw_acteur_participation
                WHERE {where_sql}
                GROUP BY type_acteur, COALESCE(type_acteur_label, type_acteur)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_type_acteur"] = [
                {
                    "type_acteur": r[0],
                    "label": r[1],
                    "total": r[2],
                    "conventions_actives": r[3],
                }
                for r in rows
            ]

            # ---- 3) Par type de participation ----
            cursor.execute(
                f"""
                SELECT
                    type_participation,
                    COALESCE(type_participation_label, type_participation) AS label,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE {sql_true("convention_cfpa_active")})
                        AS conventions_actives
                FROM marts.vw_acteur_participation
                WHERE {where_sql}
                GROUP BY type_participation,
                         COALESCE(type_participation_label, type_participation)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_type_participation"] = [
                {
                    "type_participation": r[0],
                    "label": r[1],
                    "total": r[2],
                    "conventions_actives": r[3],
                }
                for r in rows
            ]

            # ---- 4) Par statut de convention ----
            cursor.execute(
                f"""
                SELECT
                    statut_convention,
                    COALESCE(statut_convention_label, statut_convention) AS label,
                    COUNT(*) AS total
                FROM marts.vw_acteur_participation
                WHERE {where_sql}
                GROUP BY statut_convention,
                         COALESCE(statut_convention_label, statut_convention)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_statut_convention"] = [
                {
                    "statut_convention": r[0],
                    "label": r[1],
                    "total": r[2],
                }
                for r in rows
            ]

            # ---- 5) Par frÃ©quence de participation ----
            cursor.execute(
                f"""
                SELECT
                    frequence_particip,
                    COALESCE(frequence_particip_label, frequence_particip) AS label,
                    COUNT(*) AS total
                FROM marts.vw_acteur_participation
                WHERE {where_sql}
                GROUP BY frequence_particip,
                         COALESCE(frequence_particip_label, frequence_particip)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_frequence_participation"] = [
                {
                    "frequence_particip": r[0],
                    "label": r[1],
                    "total": r[2],
                }
                for r in rows
            ]

            # ---- 6) Par niveau d'implication ----
            cursor.execute(
                f"""
                SELECT
                    niveau_implication,
                    COALESCE(niveau_implication_label, niveau_implication) AS label,
                    COUNT(*) AS total
                FROM marts.vw_acteur_participation
                WHERE {where_sql}
                GROUP BY niveau_implication,
                         COALESCE(niveau_implication_label, niveau_implication)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_niveau_implication"] = [
                {
                    "niveau_implication": r[0],
                    "label": r[1],
                    "total": r[2],
                }
                for r in rows
            ]

            # ---- 7) Par satisfaction globale ----
            cursor.execute(
                f"""
                SELECT
                    satisfaction_globale,
                    COALESCE(satisfaction_globale_label, satisfaction_globale)
                        AS label,
                    COUNT(*) AS total
                FROM marts.vw_acteur_participation
                WHERE {where_sql}
                GROUP BY satisfaction_globale,
                         COALESCE(satisfaction_globale_label, satisfaction_globale)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_satisfaction"] = [
                {
                    "satisfaction_globale": r[0],
                    "label": r[1],
                    "total": r[2],
                }
                for r in rows
            ]

            # ---- 8) Par rÃ©gion ----
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE {sql_true("convention_cfpa_active")})
                        AS conventions_actives
                FROM marts.vw_acteur_participation
                WHERE {where_sql}
                GROUP BY id_region, region_nom
                ORDER BY region_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_region"] = [
                {
                    "id_region": r[0],
                    "region_nom": r[1],
                    "total": r[2],
                    "conventions_actives": r[3],
                }
                for r in rows
            ]

            # ---- 9) Par prÃ©fecture ----
            cursor.execute(
                f"""
                SELECT
                    id_prefecture,
                    prefecture_nom,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE {sql_true("convention_cfpa_active")})
                        AS conventions_actives
                FROM marts.vw_acteur_participation
                WHERE {where_sql}
                GROUP BY id_prefecture, prefecture_nom
                ORDER BY prefecture_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_prefecture"] = [
                {
                    "id_prefecture": r[0],
                    "prefecture_nom": r[1],
                    "total": r[2],
                    "conventions_actives": r[3],
                }
                for r in rows
            ]

            # ---- 10) Par commune ----
            cursor.execute(
                f"""
                SELECT
                    id_commune,
                    commune_nom,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE {sql_true("convention_cfpa_active")})
                        AS conventions_actives
                FROM marts.vw_acteur_participation
                WHERE {where_sql}
                GROUP BY id_commune, commune_nom
                ORDER BY commune_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_commune"] = [
                {
                    "id_commune": r[0],
                    "commune_nom": r[1],
                    "total": r[2],
                    "conventions_actives": r[3],
                }
                for r in rows
            ]

        return Response(data)




# -----------------------------------------------------------------------------
# Liste des comitÃ©
# -----------------------------------------------------------------------------

class AgrComiteListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Liste les comitÃ©s agricoles depuis la vue marts.vw_agr_comite,
    filtrÃ©s par projet actif via project_code (FIERE / AGRIECO).

    Filtres possibles en query string :
    - ?search=...                (nom_comite, zone_couverture, principaux_resultats,
                                  contraintes_fonctionnement, conflits_details,
                                  obs_techniciens, obs_comite, localite)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?type_comite=...          (code ref.type_comite)
    - ?statut_comite=...        (code ref.statut_comite)
    - ?suit_conflits=true|false
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        # 1) Projet courant (FIERE / AGRIECO)
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        type_comite = request.query_params.get("type_comite")
        statut_comite = request.query_params.get("statut_comite")
        suit_conflits = request.query_params.get("suit_conflits")
        has_geom = request.query_params.get("has_geom")

        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques du comitÃ©
        if type_comite:
            where_clauses.append("type_comite = %s")
            params.append(type_comite)

        if statut_comite:
            where_clauses.append("statut_comite = %s")
            params.append(statut_comite)

        if suit_conflits in ("true", "false"):
            where_clauses.append(sql_bool("suit_conflits", suit_conflits == "true"))

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte large
        if search:
            where_clauses.append(
                "("
                "nom_comite ILIKE %s OR "
                "zone_couverture ILIKE %s OR "
                "principaux_resultats ILIKE %s OR "
                "contraintes_fonctionnement ILIKE %s OR "
                "conflits_details ILIKE %s OR "
                "obs_techniciens ILIKE %s OR "
                "obs_comite ILIKE %s OR "
                "localite ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 8)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_agr_comite
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_agr_comite
                WHERE {where_sql}
                ORDER BY nom_comite NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class AgrComiteAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    AgrÃ©gations pour les comitÃ©s agricoles (marts.vw_agr_comite),
    filtrÃ©es par projet actif (FIERE / AGRIECO) et par territoire.

    Filtres (identiques Ã  la liste) :
    - ?search=...
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?type_comite=...
    - ?statut_comite=...
    - ?suit_conflits=true|false
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 1) Projet courant
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        type_comite = request.query_params.get("type_comite")
        statut_comite = request.query_params.get("statut_comite")
        suit_conflits = request.query_params.get("suit_conflits")
        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques du comitÃ©
        if type_comite:
            where_clauses.append("type_comite = %s")
            params.append(type_comite)

        if statut_comite:
            where_clauses.append("statut_comite = %s")
            params.append(statut_comite)

        if suit_conflits in ("true", "false"):
            where_clauses.append(sql_bool("suit_conflits", suit_conflits == "true"))

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte cohÃ©rente avec la liste
        if search:
            where_clauses.append(
                "("
                "nom_comite ILIKE %s OR "
                "zone_couverture ILIKE %s OR "
                "principaux_resultats ILIKE %s OR "
                "contraintes_fonctionnement ILIKE %s OR "
                "conflits_details ILIKE %s OR "
                "obs_techniciens ILIKE %s OR "
                "obs_comite ILIKE %s OR "
                "localite ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 8)

        where_sql = " AND ".join(where_clauses)

        data = {}

        with connection.cursor() as cursor:
            # ---- 1) Statistiques globales ----
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS total_comites,
                    COUNT(*) FILTER (
                        WHERE statut_comite = 'ACTIF'
                    ) AS nb_comites_actifs,
                    COALESCE(SUM(nb_membres_total), 0) AS total_membres,
                    COALESCE(SUM(nb_membres_femmes), 0) AS total_membres_femmes,
                    COALESCE(SUM(nb_membres_jeunes), 0) AS total_membres_jeunes,
                    AVG(nb_membres_total::numeric) AS avg_membres_par_comite,
                    COALESCE(SUM(nb_reunions_12m), 0) AS total_reunions_12m,
                    COALESCE(SUM(nb_sensib_12m), 0) AS total_sensibilisations_12m,
                    COUNT(*) FILTER (WHERE {sql_true("suit_conflits")}) AS nb_comites_suivi_conflits,
                    COALESCE(SUM(nb_conflits_12m), 0) AS total_conflits_12m,
                    COALESCE(SUM(nb_conflits_regles), 0) AS total_conflits_regles,
                    CASE
                        WHEN SUM(nb_conflits_12m) > 0 THEN
                            ROUND(
                                100.0 * SUM(nb_conflits_regles)::numeric
                                / SUM(nb_conflits_12m)::numeric,
                                2
                            )
                        ELSE NULL
                    END AS taux_conflits_regles_pct,
                    COALESCE(SUM(nb_techniciens_total), 0) AS total_techniciens,
                    COUNT(*) FILTER (WHERE geom IS NOT NULL) AS nb_avec_geom,
                    COUNT(*) FILTER (WHERE geom IS NULL) AS nb_sans_geom,
                    COUNT(*) FILTER (WHERE type_comite = 'COMITE_FEUX') AS nb_comites_feux
                FROM marts.vw_agr_comite
                WHERE {where_sql}
                """,
                params,
            )
            row = cursor.fetchone()
            data["global"] = {
                "total_comites": row[0],
                "nb_comites_actifs": row[1],
                "total_membres": row[2],
                "total_membres_femmes": row[3],
                "total_membres_jeunes": row[4],
                "avg_membres_par_comite": float(row[5]) if row[5] is not None else None,
                "total_reunions_12m": row[6],
                "total_sensibilisations_12m": row[7],
                "nb_comites_suivi_conflits": row[8],
                "total_conflits_12m": row[9],
                "total_conflits_regles": row[10],
                "taux_conflits_regles_pct": float(row[11]) if row[11] is not None else None,
                "total_techniciens": row[12],
                "nb_techniciens_formes": row[12],  # alias front
                "nb_avec_geom": row[13],
                "nb_sans_geom": row[14],
                "nb_comites_feux": row[15],
            }

            # ---- 2) Par type de comitÃ© ----
            cursor.execute(
                f"""
                SELECT
                    type_comite,
                    COALESCE(type_comite_label, type_comite) AS label,
                    COUNT(*) AS total,
                    COALESCE(SUM(nb_membres_total), 0) AS total_membres,
                    COALESCE(SUM(nb_membres_femmes), 0) AS total_membres_femmes,
                    COALESCE(SUM(nb_membres_jeunes), 0) AS total_membres_jeunes
                FROM marts.vw_agr_comite
                WHERE {where_sql}
                GROUP BY type_comite, COALESCE(type_comite_label, type_comite)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_type_comite"] = [
                {
                    "type_comite": r[0],
                    "type_comite_label": r[1],  # alias front
                    "label": r[1],
                    "total": r[2],
                    "nb_comites": r[2],         # alias front (charts)
                    "total_comites": r[2],      # alias front (charts)
                    "total_membres": r[3],
                    "total_membres_femmes": r[4],
                    "total_membres_jeunes": r[5],
                }
                for r in rows
            ]

            # ---- 3) Par statut de comitÃ© ----
            cursor.execute(
                f"""
                SELECT
                    statut_comite,
                    COALESCE(statut_comite_label, statut_comite) AS label,
                    COUNT(*) AS total
                FROM marts.vw_agr_comite
                WHERE {where_sql}
                GROUP BY statut_comite, COALESCE(statut_comite_label, statut_comite)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_statut_comite"] = [
                {
                    "statut_comite": r[0],
                    "label": r[1],
                    "total": r[2],
                }
                for r in rows
            ]

            # ---- 4) Par suivi des conflits (oui/non) ----
            cursor.execute(
                f"""
                SELECT
                    suit_conflits,
                    COUNT(*) AS total,
                    COALESCE(SUM(nb_conflits_12m), 0) AS total_conflits_12m,
                    COALESCE(SUM(nb_conflits_regles), 0) AS total_conflits_regles
                FROM marts.vw_agr_comite
                WHERE {where_sql}
                GROUP BY suit_conflits
                ORDER BY suit_conflits
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_suivi_conflits"] = [
                {
                    "suit_conflits": r[0],
                    "total": r[1],
                    "total_conflits_12m": r[2],
                    "total_conflits_regles": r[3],
                }
                for r in rows
            ]

            # ---- 5) Par rÃ©gion ----
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    COUNT(*) AS total,
                    COALESCE(SUM(nb_membres_total), 0) AS total_membres,
                    COALESCE(SUM(nb_reunions_12m), 0) AS total_reunions_12m,
                    COALESCE(SUM(nb_conflits_12m), 0) AS total_conflits_12m,
                    COALESCE(SUM(nb_conflits_regles), 0) AS total_conflits_regles
                FROM marts.vw_agr_comite
                WHERE {where_sql}
                GROUP BY id_region, region_nom
                ORDER BY region_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_region"] = [
                {
                    "id_region": r[0],
                    "region_nom": r[1],
                    "total": r[2],
                    "total_membres": r[3],
                    "total_reunions_12m": r[4],
                    "total_conflits_12m": r[5],
                    "total_conflits_regles": r[6],
                }
                for r in rows
            ]

            # ---- 6) Par prÃ©fecture ----
            cursor.execute(
                f"""
                SELECT
                    id_prefecture,
                    prefecture_nom,
                    COUNT(*) AS total,
                    COALESCE(SUM(nb_membres_total), 0) AS total_membres,
                    COALESCE(SUM(nb_reunions_12m), 0) AS total_reunions_12m
                FROM marts.vw_agr_comite
                WHERE {where_sql}
                GROUP BY id_prefecture, prefecture_nom
                ORDER BY prefecture_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_prefecture"] = [
                {
                    "id_prefecture": r[0],
                    "prefecture_nom": r[1],
                    "total": r[2],
                    "total_membres": r[3],
                    "total_reunions_12m": r[4],
                }
                for r in rows
            ]

            # ---- 7) Par commune ----
            cursor.execute(
                f"""
                SELECT
                    id_commune,
                    commune_nom,
                    COUNT(*) AS total,
                    COALESCE(SUM(nb_membres_total), 0) AS total_membres,
                    COALESCE(SUM(nb_reunions_12m), 0) AS total_reunions_12m
                FROM marts.vw_agr_comite
                WHERE {where_sql}
                GROUP BY id_commune, commune_nom
                ORDER BY commune_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_commune"] = [
                {
                    "id_commune": r[0],
                    "commune_nom": r[1],
                    "total": r[2],
                    "total_membres": r[3],
                    "total_reunions_12m": r[4],
                }
                for r in rows
            ]

            # ---- 8) Par type de conflit (multi-valeurs) ----
            cursor.execute(
                f"""
                SELECT
                    x.code AS type_conflit_code,
                    COALESCE(cf.label_fr, x.code) AS label,
                    COUNT(DISTINCT cmt.comite_uuid) AS nb_comites,
                    COALESCE(SUM(cmt.nb_conflits_12m), 0) AS total_conflits_12m,
                    COALESCE(SUM(cmt.nb_conflits_regles), 0) AS total_conflits_regles
                FROM marts.vw_agr_comite cmt
                JOIN LATERAL unnest(cmt.types_conflits_codes) AS x(code) ON TRUE
                LEFT JOIN ref.type_conflit cf ON cf.code = x.code
                WHERE {where_sql}
                GROUP BY x.code, COALESCE(cf.label_fr, x.code)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_type_conflit"] = [
                {
                    "type_conflit_code": r[0],
                    "label": r[1],
                    "nb_comites": r[2],
                    "total_conflits_12m": r[3],
                    "total_conflits_regles": r[4],
                }
                for r in rows
            ]

            # ---- 9) Par thÃ¨me du comitÃ© (multi-valeurs) ----
            cursor.execute(
                f"""
                SELECT
                    x.code AS theme_code,
                    COALESCE(th.label_fr, x.code) AS label,
                    COUNT(DISTINCT cmt.comite_uuid) AS nb_comites
                FROM marts.vw_agr_comite cmt
                JOIN LATERAL unnest(cmt.themes_comite_codes) AS x(code) ON TRUE
                LEFT JOIN ref.theme_comite th ON th.code = x.code
                WHERE {where_sql}
                GROUP BY x.code, COALESCE(th.label_fr, x.code)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_theme_comite"] = [
                {
                    "theme_code": r[0],
                    "label": r[1],
                    "nb_comites": r[2],
                }
                for r in rows
            ]

            # ---- 10) Par type de techniciens (multi-valeurs) ----
            cursor.execute(
                f"""
                SELECT
                    x.code AS type_tech_code,
                    COALESCE(tt.label_fr, x.code) AS label,
                    COUNT(DISTINCT cmt.comite_uuid) AS nb_comites,
                    COALESCE(SUM(cmt.nb_techniciens_total), 0) AS total_techniciens
                FROM marts.vw_agr_comite cmt
                JOIN LATERAL unnest(cmt.type_techniciens_codes) AS x(code) ON TRUE
                LEFT JOIN ref.type_tech tt ON tt.code = x.code
                WHERE {where_sql}
                GROUP BY x.code, COALESCE(tt.label_fr, x.code)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_type_technicien"] = [
                {
                    "type_tech_code": r[0],
                    "label": r[1],
                    "nb_comites": r[2],
                    "total_techniciens": r[3],
                }
                for r in rows
            ]

        return Response(data)




# -----------------------------------------------------------------------------
# Liste des mÃ©nages
# -----------------------------------------------------------------------------


class AgrMenageListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Liste les mÃ©nages agricoles depuis la vue marts.vw_agr_menage,
    filtrÃ©s par projet actif via project_code (FIERE / AGRIECO).

    Filtres possibles en query string :
    - ?search=...                    (nom_chef_menage, obs_sensib, obs_foyer,
                                      obs_nutrition, obs_menage, localite)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?type_menage=...              (code ref.type_menage)
    - ?menage_prat_agroeco=true|false
    - ?applique_bonnes_prat_nutrition=true|false
    - ?utilise_intrants_chimiques=true|false
    - ?utilise_foyer_ameliore=true|false
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        # Projet courant (FIERE / AGRIECO)
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        type_menage = request.query_params.get("type_menage")

        menage_prat_agroeco = request.query_params.get("menage_prat_agroeco")
        applique_bonnes_prat_nutrition = request.query_params.get(
            "applique_bonnes_prat_nutrition"
        )
        utilise_intrants_chimiques = request.query_params.get(
            "utilise_intrants_chimiques"
        )
        utilise_foyer_ameliore = request.query_params.get("utilise_foyer_ameliore")
        has_geom = request.query_params.get("has_geom")

        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques du mÃ©nage
        if type_menage:
            where_clauses.append("type_menage = %s")
            params.append(type_menage)

        if menage_prat_agroeco in ("true", "false"):
            where_clauses.append(sql_bool("menage_prat_agroeco", menage_prat_agroeco == "true"))

        if applique_bonnes_prat_nutrition in ("true", "false"):
            where_clauses.append(
                sql_bool("applique_bonnes_prat_nutrition", applique_bonnes_prat_nutrition == "true")
            )

        if utilise_intrants_chimiques in ("true", "false"):
            where_clauses.append(sql_bool("utilise_intrants_chimiques", utilise_intrants_chimiques == "true"))

        if utilise_foyer_ameliore in ("true", "false"):
            where_clauses.append(sql_bool("utilise_foyer_ameliore", utilise_foyer_ameliore == "true"))

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "nom_chef_menage ILIKE %s OR "
                "obs_sensib ILIKE %s OR "
                "obs_foyer ILIKE %s OR "
                "obs_nutrition ILIKE %s OR "
                "obs_menage ILIKE %s OR "
                "localite ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 6)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_agr_menage
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_agr_menage
                WHERE {where_sql}
                ORDER BY nom_chef_menage NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class AgrMenageAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    AgrÃ©gations pour les mÃ©nages agricoles (marts.vw_agr_menage),
    filtrÃ©es par projet actif (FIERE / AGRIECO) et par territoire.

    Filtres (alignÃ©s avec la liste) :
    - ?search=...
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?type_menage=...
    - ?menage_prat_agroeco=true|false
    - ?applique_bonnes_prat_nutrition=true|false
    - ?utilise_intrants_chimiques=true|false
    - ?utilise_foyer_ameliore=true|false
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Projet courant
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        type_menage = request.query_params.get("type_menage")

        menage_prat_agroeco = request.query_params.get("menage_prat_agroeco")
        applique_bonnes_prat_nutrition = request.query_params.get(
            "applique_bonnes_prat_nutrition"
        )
        utilise_intrants_chimiques = request.query_params.get(
            "utilise_intrants_chimiques"
        )
        utilise_foyer_ameliore = request.query_params.get("utilise_foyer_ameliore")
        has_geom = request.query_params.get("has_geom")

        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques
        if type_menage:
            where_clauses.append("type_menage = %s")
            params.append(type_menage)

        if menage_prat_agroeco in ("true", "false"):
            where_clauses.append(sql_bool("menage_prat_agroeco", menage_prat_agroeco == "true"))

        if applique_bonnes_prat_nutrition in ("true", "false"):
            where_clauses.append(
                sql_bool("applique_bonnes_prat_nutrition", applique_bonnes_prat_nutrition == "true")
            )

        if utilise_intrants_chimiques in ("true", "false"):
            where_clauses.append(sql_bool("utilise_intrants_chimiques", utilise_intrants_chimiques == "true"))

        if utilise_foyer_ameliore in ("true", "false"):
            where_clauses.append(sql_bool("utilise_foyer_ameliore", utilise_foyer_ameliore == "true"))

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "nom_chef_menage ILIKE %s OR "
                "obs_sensib ILIKE %s OR "
                "obs_foyer ILIKE %s OR "
                "obs_nutrition ILIKE %s OR "
                "obs_menage ILIKE %s OR "
                "localite ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 6)

        where_sql = " AND ".join(where_clauses)

        data = {}

        with connection.cursor() as cursor:
            # ---- 1) Stat global ----
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS total_menages,
                    COALESCE(SUM(nb_personnes), 0) AS total_personnes,
                    COALESCE(SUM(nb_enfants_u5), 0) AS total_enfants_u5,
                    COUNT(*) FILTER (WHERE {sql_true("menage_prat_agroeco")}) AS nb_menages_prat_agroeco,
                    COUNT(*) FILTER (WHERE {sql_true("utilise_intrants_chimiques")}) AS nb_menages_intrants_chimiques,
                    COUNT(*) FILTER (WHERE {sql_true("applique_bonnes_pratiques_intrants")}) AS nb_menages_bonnes_pratiques_intrants,
                    COUNT(*) FILTER (WHERE {sql_true("utilise_foyer_ameliore")}) AS nb_menages_foyer_ameliore,
                    COUNT(*) FILTER (WHERE {sql_true("applique_bonnes_prat_nutrition")}) AS nb_menages_bonnes_pratiques_nutrition,
                    COALESCE(SUM(nb_seances_total), 0) AS total_seances_sensib,
                    COUNT(*) FILTER (WHERE geom IS NOT NULL) AS nb_avec_geom,
                    COUNT(*) FILTER (WHERE geom IS NULL) AS nb_sans_geom
                FROM marts.vw_agr_menage
                WHERE {where_sql}
                """,
                params,
            )
            row = cursor.fetchone()
            data["global"] = {
                "total_menages": row[0],
                "total_personnes": row[1],
                "total_enfants_u5": row[2],
                "nb_menages_prat_agroeco": row[3],
                "nb_menages_intrants_chimiques": row[4],
                "nb_menages_bonnes_pratiques_intrants": row[5],
                "nb_menages_foyer_ameliore": row[6],
                "nb_menages_foyers_ameliores": row[6],  # alias pluriel pour le front
                "nb_menages_bonnes_pratiques_nutrition": row[7],
                "total_seances_sensib": row[8],
                "nb_avec_geom": row[9],
                "nb_sans_geom": row[10],
            }

            # ---- 2) Par type de mÃ©nage ----
            cursor.execute(
                f"""
                SELECT
                    type_menage,
                    COALESCE(type_menage_label, type_menage) AS label,
                    COUNT(*) AS total_menages,
                    COALESCE(SUM(nb_personnes), 0) AS total_personnes,
                    COALESCE(SUM(nb_enfants_u5), 0) AS total_enfants_u5
                FROM marts.vw_agr_menage
                WHERE {where_sql}
                GROUP BY type_menage, COALESCE(type_menage_label, type_menage)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_type_menage"] = [
                {
                    "type_menage": r[0],
                    "label": r[1],
                    "total_menages": r[2],
                    "total_personnes": r[3],
                    "total_enfants_u5": r[4],
                }
                for r in rows
            ]

            # ---- 3) Par type de foyer principal ----
            cursor.execute(
                f"""
                SELECT
                    type_foyer_principal,
                    COALESCE(type_foyer_principal_label, type_foyer_principal) AS label,
                    COUNT(*) AS total_menages,
                    COUNT(*) FILTER (WHERE {sql_true("utilise_foyer_ameliore")}) AS nb_menages_foyer_ameliore
                FROM marts.vw_agr_menage
                WHERE {where_sql}
                GROUP BY type_foyer_principal, COALESCE(type_foyer_principal_label, type_foyer_principal)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_type_foyer"] = [
                {
                    "type_foyer_principal": r[0],
                    "label": r[1],
                    "total_menages": r[2],
                    "nb_menages_foyer_ameliore": r[3],
                }
                for r in rows
            ]

            # ---- 4) Par frÃ©quence des pratiques nutrition ----
            cursor.execute(
                f"""
                SELECT
                    frequence_pratiques_nutrition,
                    COALESCE(frequence_pratiques_nutrition_label, frequence_pratiques_nutrition) AS label,
                    COUNT(*) AS total_menages,
                    COUNT(*) FILTER (WHERE {sql_true("applique_bonnes_prat_nutrition")}) AS nb_menages_bonnes_pratiques_nutrition
                FROM marts.vw_agr_menage
                WHERE {where_sql}
                GROUP BY frequence_pratiques_nutrition, COALESCE(frequence_pratiques_nutrition_label, frequence_pratiques_nutrition)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_freq_pratiques_nutrition"] = [
                {
                    "frequence_pratiques_nutrition": r[0],
                    "label": r[1],
                    "total_menages": r[2],
                    "nb_menages_bonnes_pratiques_nutrition": r[3],
                }
                for r in rows
            ]

            # ---- 5) Par rÃ©gion ----
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    COUNT(*) AS total_menages,
                    COALESCE(SUM(nb_personnes), 0) AS total_personnes,
                    COALESCE(SUM(nb_enfants_u5), 0) AS total_enfants_u5,
                    COALESCE(SUM(nb_seances_total), 0) AS total_seances_sensib
                FROM marts.vw_agr_menage
                WHERE {where_sql}
                GROUP BY id_region, region_nom
                ORDER BY region_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_region"] = [
                {
                    "id_region": r[0],
                    "region_nom": r[1],
                    "total_menages": r[2],
                    "total_personnes": r[3],
                    "total_enfants_u5": r[4],
                    "total_seances_sensib": r[5],
                }
                for r in rows
            ]

            # ---- 6) Par prÃ©fecture ----
            cursor.execute(
                f"""
                SELECT
                    id_prefecture,
                    prefecture_nom,
                    COUNT(*) AS total_menages,
                    COALESCE(SUM(nb_personnes), 0) AS total_personnes
                FROM marts.vw_agr_menage
                WHERE {where_sql}
                GROUP BY id_prefecture, prefecture_nom
                ORDER BY prefecture_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_prefecture"] = [
                {
                    "id_prefecture": r[0],
                    "prefecture_nom": r[1],
                    "total_menages": r[2],
                    "total_personnes": r[3],
                }
                for r in rows
            ]

            # ---- 7) Par commune ----
            cursor.execute(
                f"""
                SELECT
                    id_commune,
                    commune_nom,
                    COUNT(*) AS total_menages,
                    COALESCE(SUM(nb_personnes), 0) AS total_personnes
                FROM marts.vw_agr_menage
                WHERE {where_sql}
                GROUP BY id_commune, commune_nom
                ORDER BY commune_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_commune"] = [
                {
                    "id_commune": r[0],
                    "commune_nom": r[1],
                    "total_menages": r[2],
                    "total_personnes": r[3],
                }
                for r in rows
            ]

            # ---- 8) Par thÃ¨mes de sensibilisation (multi-valeurs) ----
            cursor.execute(
                f"""
                SELECT
                    x.code AS theme_code,
                    COALESCE(ts.label_fr, x.code) AS label,
                    COUNT(DISTINCT m.menage_uuid) AS nb_menages,
                    COALESCE(SUM(m.nb_seances_total), 0) AS total_seances_sensib
                FROM marts.vw_agr_menage m
                JOIN LATERAL unnest(m.themes_sensibilisation_codes) AS x(code) ON TRUE
                LEFT JOIN ref.theme_sensib ts ON ts.code = x.code
                WHERE {where_sql}
                GROUP BY x.code, COALESCE(ts.label_fr, x.code)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_theme_sensibilisation"] = [
                {
                    "theme_code": r[0],
                    "label": r[1],
                    "nb_menages": r[2],
                    "total_seances_sensib": r[3],
                }
                for r in rows
            ]

            # ---- 9) Par source d'information (multi-valeurs) ----
            cursor.execute(
                f"""
                SELECT
                    x.code AS source_code,
                    COALESCE(si.label_fr, x.code) AS label,
                    COUNT(DISTINCT m.menage_uuid) AS nb_menages
                FROM marts.vw_agr_menage m
                JOIN LATERAL unnest(m.source_information_codes) AS x(code) ON TRUE
                LEFT JOIN ref.source_info si ON si.code = x.code
                WHERE {where_sql}
                GROUP BY x.code, COALESCE(si.label_fr, x.code)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_source_information"] = [
                {
                    "source_code": r[0],
                    "label": r[1],
                    "nb_menages": r[2],
                }
                for r in rows
            ]

            # ---- 10) Par pratiques agroÃ©cologiques (multi-valeurs) ----
            cursor.execute(
                f"""
                SELECT
                    x.code AS pratique_code,
                    COALESCE(p.label_fr, x.code) AS label,
                    COUNT(DISTINCT m.menage_uuid) AS nb_menages
                FROM marts.vw_agr_menage m
                JOIN LATERAL unnest(m.pratiques_agro_codes) AS x(code) ON TRUE
                LEFT JOIN ref.pratique_agro p ON p.code = x.code
                WHERE {where_sql}
                GROUP BY x.code, COALESCE(p.label_fr, x.code)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_pratique_agro"] = [
                {
                    "pratique_code": r[0],
                    "label": r[1],
                    "nb_menages": r[2],
                }
                for r in rows
            ]

            # ---- 11) Par pratiques de nutrition (multi-valeurs) ----
            cursor.execute(
                f"""
                SELECT
                    x.code AS pratique_code,
                    COALESCE(n.label_fr, x.code) AS label,
                    COUNT(DISTINCT m.menage_uuid) AS nb_menages
                FROM marts.vw_agr_menage m
                JOIN LATERAL unnest(m.pratiques_nutrition_codes) AS x(code) ON TRUE
                LEFT JOIN ref.pratique_nutrition n ON n.code = x.code
                WHERE {where_sql}
                GROUP BY x.code, COALESCE(n.label_fr, x.code)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_pratique_nutrition"] = [
                {
                    "pratique_code": r[0],
                    "label": r[1],
                    "nb_menages": r[2],
                }
                for r in rows
            ]

        return Response(data)



# -----------------------------------------------------------------------------
# Liste des organisations
# -----------------------------------------------------------------------------

class AgrOrganisationListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Liste les organisations agricoles depuis la vue marts.vw_agr_organisation,
    filtrÃ©es par projet actif via project_code (FIERE / AGRIECO).

    Filtres possibles en query string :
    - ?search=...                 (id_org, obs_org, activites_principales_labels,
                                   filieres_principales_labels, pratiques_agro_labels,
                                   localite, commune_nom, region_nom)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?type_org=...              (code ref.type_organisation)
    - ?statut_juridique=...      (code ref.statut_juridique)
    - ?pratiques_adoptees=...    (valeur brute du champ, ex. 'oui' / 'non')
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc  # FIERE / AGRIECO

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        type_org = request.query_params.get("type_org")
        statut_juridique = request.query_params.get("statut_juridique")
        pratiques_adoptees = request.query_params.get("pratiques_adoptees")
        has_geom = request.query_params.get("has_geom")

        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques de l'organisation
        if type_org:
            where_clauses.append("type_org = %s")
            params.append(type_org)

        if statut_juridique:
            where_clauses.append("statut_juridique = %s")
            params.append(statut_juridique)

        if pratiques_adoptees:
            # on prend la valeur telle quelle (ex: 'oui' / 'non')
            where_clauses.append("pratiques_adoptees = %s")
            params.append(pratiques_adoptees)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "id_org::text ILIKE %s OR "
                "obs_org ILIKE %s OR "
                "activites_principales_labels ILIKE %s OR "
                "filieres_principales_labels ILIKE %s OR "
                "pratiques_agro_labels ILIKE %s OR "
                "localite ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 8)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_agr_organisation
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_agr_organisation
                WHERE {where_sql}
                ORDER BY id_org NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class AgrOrganisationAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    AgrÃ©gations pour les organisations agricoles (marts.vw_agr_organisation),
    filtrÃ©es par projet actif (FIERE / AGRIECO) et par territoire.

    Filtres (alignÃ©s avec la liste) :
    - ?search=...
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?type_org=...
    - ?statut_juridique=...
    - ?pratiques_adoptees=...
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Projet courant
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        type_org = request.query_params.get("type_org")
        statut_juridique = request.query_params.get("statut_juridique")
        pratiques_adoptees = request.query_params.get("pratiques_adoptees")
        has_geom = request.query_params.get("has_geom")

        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques
        if type_org:
            where_clauses.append("type_org = %s")
            params.append(type_org)

        if statut_juridique:
            where_clauses.append("statut_juridique = %s")
            params.append(statut_juridique)

        if pratiques_adoptees:
            where_clauses.append("pratiques_adoptees = %s")
            params.append(pratiques_adoptees)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "id_org::text ILIKE %s OR "
                "obs_org ILIKE %s OR "
                "activites_principales_labels ILIKE %s OR "
                "filieres_principales_labels ILIKE %s OR "
                "pratiques_agro_labels ILIKE %s OR "
                "localite ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 8)

        where_sql = " AND ".join(where_clauses)

        data = {}

        with connection.cursor() as cursor:
            # ---- 1) Stat global ----
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS total_organisations,
                    COALESCE(SUM(nb_membres_total), 0) AS total_membres,
                    COALESCE(SUM(nb_membres_femmes), 0) AS total_membres_femmes,
                    COALESCE(SUM(nb_membres_jeunes), 0) AS total_membres_jeunes,
                    COUNT(*) FILTER (WHERE pratiques_adoptees = 'oui') AS nb_org_pratiques_adoptees_oui,
                    COALESCE(SUM(nb_planteurs_accompagnes), 0) AS total_planteurs_accompagnes,
                    COALESCE(SUM(nb_producteurs_semenciers), 0) AS total_producteurs_semenciers,
                    COALESCE(SUM(nb_banques_semences), 0) AS total_banques_semences,
                    COALESCE(SUM(nb_bovins), 0) AS total_bovins,
                    COALESCE(SUM(nb_ovins), 0) AS total_ovins,
                    COALESCE(SUM(nb_caprins), 0) AS total_caprins,
                    COALESCE(SUM(nb_ruches_ken), 0) AS total_ruches_ken,
                    COALESCE(SUM(nb_ruches_lang), 0) AS total_ruches_lang,
                    COALESCE(SUM(nb_ruches_autres), 0) AS total_ruches_autres,
                    COALESCE(SUM(nb_emplois_verts), 0) AS total_emplois_verts,
                    COUNT(*) FILTER (WHERE geom IS NOT NULL) AS nb_avec_geom,
                    COUNT(*) FILTER (WHERE geom IS NULL) AS nb_sans_geom,
                    COUNT(*) FILTER (WHERE type_org = 'OP') AS nb_op,
                    COUNT(*) FILTER (
                        WHERE nb_bovins > 0 OR nb_ovins > 0 OR nb_caprins > 0
                    ) AS nb_groupements_eleveurs,
                    COALESCE(
                        SUM(nb_ruches_ken) + SUM(nb_ruches_lang) + SUM(nb_ruches_autres), 0
                    ) AS nb_ruches
                FROM marts.vw_agr_organisation
                WHERE {where_sql}
                """,
                params,
            )
            row = cursor.fetchone()
            data["global"] = {
                "total_organisations": row[0],
                "total_membres": row[1],
                "total_membres_femmes": row[2],
                "total_membres_jeunes": row[3],
                "nb_org_pratiques_adoptees_oui": row[4],
                "total_planteurs_accompagnes": row[5],
                "total_producteurs_semenciers": row[6],
                "nb_producteurs_semenciers": row[6],   # alias front
                "total_banques_semences": row[7],
                "nb_banques_semences": row[7],          # alias front
                "total_bovins": row[8],
                "total_ovins": row[9],
                "total_caprins": row[10],
                "total_ruches_ken": row[11],
                "total_ruches_lang": row[12],
                "total_ruches_autres": row[13],
                "total_emplois_verts": row[14],
                "nb_avec_geom": row[15],
                "nb_sans_geom": row[16],
                "nb_op": row[17],
                "nb_groupements_eleveurs": row[18],
                "nb_ruches": row[19],
                "nb_officines_vet": 0,                  # pas de source disponible
            }

            # ---- 2) Par type d'organisation ----
            cursor.execute(
                f"""
                SELECT
                    type_org,
                    COALESCE(type_org_label, type_org) AS label,
                    COUNT(*) AS total_organisations,
                    COALESCE(SUM(nb_membres_total), 0) AS total_membres,
                    COALESCE(SUM(nb_membres_femmes), 0) AS total_membres_femmes,
                    COALESCE(SUM(nb_membres_jeunes), 0) AS total_membres_jeunes,
                    COALESCE(SUM(nb_emplois_verts), 0) AS total_emplois_verts
                FROM marts.vw_agr_organisation
                WHERE {where_sql}
                GROUP BY type_org, COALESCE(type_org_label, type_org)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_type_org"] = [
                {
                    "type_org": r[0],
                    "type_org_label": r[1],     # alias front
                    "label": r[1],
                    "total_organisations": r[2],
                    "nb_org": r[2],             # alias front (charts)
                    "total_membres": r[3],
                    "total_membres_femmes": r[4],
                    "total_membres_jeunes": r[5],
                    "total_emplois_verts": r[6],
                }
                for r in rows
            ]

            # ---- 3) Par statut juridique ----
            cursor.execute(
                f"""
                SELECT
                    statut_juridique,
                    COALESCE(statut_juridique_label, statut_juridique) AS label,
                    COUNT(*) AS total_organisations,
                    COALESCE(SUM(nb_membres_total), 0) AS total_membres
                FROM marts.vw_agr_organisation
                WHERE {where_sql}
                GROUP BY statut_juridique, COALESCE(statut_juridique_label, statut_juridique)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_statut_juridique"] = [
                {
                    "statut_juridique": r[0],
                    "label": r[1],
                    "total_organisations": r[2],
                    "total_membres": r[3],
                }
                for r in rows
            ]

            # ---- 4) Par rÃ©gion ----
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    COUNT(*) AS total_organisations,
                    COALESCE(SUM(nb_membres_total), 0) AS total_membres,
                    COALESCE(SUM(nb_emplois_verts), 0) AS total_emplois_verts
                FROM marts.vw_agr_organisation
                WHERE {where_sql}
                GROUP BY id_region, region_nom
                ORDER BY region_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_region"] = [
                {
                    "id_region": r[0],
                    "region_nom": r[1],
                    "total_organisations": r[2],
                    "total_membres": r[3],
                    "total_emplois_verts": r[4],
                }
                for r in rows
            ]

            # ---- 5) Par prÃ©fecture ----
            cursor.execute(
                f"""
                SELECT
                    id_prefecture,
                    prefecture_nom,
                    COUNT(*) AS total_organisations,
                    COALESCE(SUM(nb_membres_total), 0) AS total_membres
                FROM marts.vw_agr_organisation
                WHERE {where_sql}
                GROUP BY id_prefecture, prefecture_nom
                ORDER BY prefecture_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_prefecture"] = [
                {
                    "id_prefecture": r[0],
                    "prefecture_nom": r[1],
                    "total_organisations": r[2],
                    "total_membres": r[3],
                }
                for r in rows
            ]

            # ---- 6) Par commune ----
            cursor.execute(
                f"""
                SELECT
                    id_commune,
                    commune_nom,
                    COUNT(*) AS total_organisations,
                    COALESCE(SUM(nb_membres_total), 0) AS total_membres
                FROM marts.vw_agr_organisation
                WHERE {where_sql}
                GROUP BY id_commune, commune_nom
                ORDER BY commune_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_commune"] = [
                {
                    "id_commune": r[0],
                    "commune_nom": r[1],
                    "total_organisations": r[2],
                    "total_membres": r[3],
                }
                for r in rows
            ]

            # ---- 7) Par filiÃ¨re principale (multi-valeurs) ----
            cursor.execute(
                f"""
                SELECT
                    x.code AS filiere_code,
                    COALESCE(f.label_fr, x.code) AS label,
                    COUNT(DISTINCT org_uuid) AS nb_organisations,
                    COALESCE(SUM(nb_planteurs_accompagnes), 0) AS total_planteurs_accompagnes
                FROM marts.vw_agr_organisation
                JOIN LATERAL unnest(filieres_principales_codes) AS x(code) ON TRUE
                LEFT JOIN ref.filiere f ON f.code = x.code
                WHERE {where_sql}
                GROUP BY x.code, COALESCE(f.label_fr, x.code)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_filiere"] = [
                {
                    "filiere_code": r[0],
                    "label": r[1],
                    "nb_organisations": r[2],
                    "total_planteurs_accompagnes": r[3],
                }
                for r in rows
            ]

            # ---- 8) Par activitÃ©s principales (multi-valeurs) ----
            cursor.execute(
                f"""
                SELECT
                    x.code AS activite_code,
                    COALESCE(a.label_fr, x.code) AS label,
                    COUNT(DISTINCT org_uuid) AS nb_organisations
                FROM marts.vw_agr_organisation
                JOIN LATERAL unnest(activites_principales_codes) AS x(code) ON TRUE
                LEFT JOIN ref.activite_organisation a ON a.code = x.code
                WHERE {where_sql}
                GROUP BY x.code, COALESCE(a.label_fr, x.code)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_activite_principale"] = [
                {
                    "activite_code": r[0],
                    "label": r[1],
                    "nb_organisations": r[2],
                }
                for r in rows
            ]

            # ---- 9) Par pratiques agroÃ©cologiques adoptÃ©es (multi-valeurs) ----
            cursor.execute(
                f"""
                SELECT
                    x.code AS pratique_code,
                    COALESCE(p.label_fr, x.code) AS label,
                    COUNT(DISTINCT org_uuid) AS nb_organisations
                FROM marts.vw_agr_organisation
                JOIN LATERAL unnest(pratiques_agro_adoptees_codes) AS x(code) ON TRUE
                LEFT JOIN ref.pratique_agro p ON p.code = x.code
                WHERE {where_sql}
                GROUP BY x.code, COALESCE(p.label_fr, x.code)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_pratique_agro"] = [
                {
                    "pratique_code": r[0],
                    "label": r[1],
                    "nb_organisations": r[2],
                }
                for r in rows
            ]

        return Response(data)



# -----------------------------------------------------------------------------
# Liste des parcelles
# -----------------------------------------------------------------------------

class CepParcelleListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Liste les parcelles CEP depuis la vue marts.vw_cep_parcelle,
    filtrÃ©es par projet actif via project_code (FIERE / AGRIECO).

    Filtres possibles en query string :
    - ?search=...                 (id_cep, filiere_label, commune_nom, region_nom)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?filiere=...                (code ref.filiere)
    - ?campagne=2023              (annÃ©e campagne_yyyy)
    - ?has_geom=true|false        (prÃ©sence de gÃ©omÃ©trie)
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc  # FIERE / AGRIECO

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        filiere = request.query_params.get("filiere")
        campagne = request.query_params.get("campagne")  # ex: 2023
        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques
        if filiere:
            where_clauses.append("filiere = %s")
            params.append(filiere)

        if campagne:
            # campagne_yyyy est un entier (annÃ©e) cÃ´tÃ© DB, mais %s cast OK
            where_clauses.append("campagne_yyyy = %s")
            params.append(campagne)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "id_cep::text ILIKE %s OR "
                "filiere_label ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 4)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_cep_parcelle
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_cep_parcelle
                WHERE {where_sql}
                ORDER BY id_cep NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class CepParcelleAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    AgrÃ©gations pour les parcelles CEP (marts.vw_cep_parcelle),
    filtrÃ©es par projet actif (FIERE / AGRIECO) et territoire.

    Filtres :
    - ?search=...      (id_cep, filiere_label, commune_nom, region_nom)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?filiere=...
    - ?campagne=2023
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Projet courant
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        filiere = request.query_params.get("filiere")
        campagne = request.query_params.get("campagne")
        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques
        if filiere:
            where_clauses.append("filiere = %s")
            params.append(filiere)

        if campagne:
            where_clauses.append("campagne_yyyy = %s")
            params.append(campagne)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "id_cep::text ILIKE %s OR "
                "filiere_label ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 4)

        where_sql = " AND ".join(where_clauses)

        data: dict = {}

        with connection.cursor() as cursor:
            # ---- 1) Global ----
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS total_parcelles,
                    COALESCE(SUM(surface_decl), 0) AS total_surface_decl_ha,
                    COALESCE(SUM(menages_beneficiaires), 0) AS total_menages_beneficiaires,
                    ROUND(AVG(rendement)::numeric, 2) AS rendement_moyen,
                    MIN(rendement) AS rendement_min,
                    MAX(rendement) AS rendement_max,
                    COUNT(*) FILTER (WHERE geom IS NOT NULL) AS nb_avec_geom,
                    COUNT(*) FILTER (WHERE geom IS NULL) AS nb_sans_geom
                FROM marts.vw_cep_parcelle
                WHERE {where_sql}
                """,
                params,
            )
            row = cursor.fetchone()
            data["global"] = {
                "total_parcelles": row[0],
                "total_surface_decl_ha": float(row[1]) if row[1] is not None else 0.0,
                "total_menages_beneficiaires": row[2],
                "rendement_moyen": float(row[3]) if row[3] is not None else None,
                "rendement_min": row[4],
                "rendement_max": row[5],
                "nb_avec_geom": row[6],
                "nb_sans_geom": row[7],
            }

            # ---- 2) Par filiÃ¨re ----
            cursor.execute(
                f"""
                SELECT
                    filiere,
                    COALESCE(filiere_label, filiere) AS label,
                    COUNT(*) AS nb_parcelles,
                    COALESCE(SUM(surface_decl), 0) AS total_surface_decl_ha,
                    COALESCE(SUM(menages_beneficiaires), 0) AS total_menages_beneficiaires,
                    ROUND(AVG(rendement)::numeric, 2) AS rendement_moyen
                FROM marts.vw_cep_parcelle
                WHERE {where_sql}
                GROUP BY filiere, COALESCE(filiere_label, filiere)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_filiere"] = [
                {
                    "filiere": r[0],
                    "label": r[1],
                    "filiere_label": r[1],  # alias front
                    "nb_parcelles": r[2],
                    "total_surface_decl_ha": float(r[3]) if r[3] is not None else 0.0,
                    "total_menages_beneficiaires": r[4],
                    "rendement_moyen": float(r[5]) if r[5] is not None else None,
                }
                for r in rows
            ]

            # ---- 3) Par campagne ----
            cursor.execute(
                f"""
                SELECT
                    campagne_yyyy,
                    COUNT(*) AS nb_parcelles,
                    COALESCE(SUM(surface_decl), 0) AS total_surface_decl_ha,
                    COALESCE(SUM(menages_beneficiaires), 0) AS total_menages_beneficiaires,
                    ROUND(AVG(rendement)::numeric, 2) AS rendement_moyen
                FROM marts.vw_cep_parcelle
                WHERE {where_sql}
                GROUP BY campagne_yyyy
                ORDER BY campagne_yyyy
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_campagne"] = [
                {
                    "campagne_yyyy": r[0],
                    "campagne": r[0],       # alias front
                    "nb_parcelles": r[1],
                    "total_surface_decl_ha": float(r[2]) if r[2] is not None else 0.0,
                    "total_menages_beneficiaires": r[3],
                    "rendement_moyen": float(r[4]) if r[4] is not None else None,
                }
                for r in rows
            ]

            # ---- 4) Par rÃ©gion ----
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    COUNT(*) AS nb_parcelles,
                    COALESCE(SUM(surface_decl), 0) AS total_surface_decl_ha,
                    COALESCE(SUM(menages_beneficiaires), 0) AS total_menages_beneficiaires
                FROM marts.vw_cep_parcelle
                WHERE {where_sql}
                GROUP BY id_region, region_nom
                ORDER BY region_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_region"] = [
                {
                    "id_region": r[0],
                    "region_nom": r[1],
                    "nb_parcelles": r[2],
                    "total_surface_decl_ha": float(r[3]) if r[3] is not None else 0.0,
                    "total_menages_beneficiaires": r[4],
                }
                for r in rows
            ]

            # ---- 5) Par prÃ©fecture ----
            cursor.execute(
                f"""
                SELECT
                    id_prefecture,
                    prefecture_nom,
                    COUNT(*) AS nb_parcelles,
                    COALESCE(SUM(surface_decl), 0) AS total_surface_decl_ha
                FROM marts.vw_cep_parcelle
                WHERE {where_sql}
                GROUP BY id_prefecture, prefecture_nom
                ORDER BY prefecture_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_prefecture"] = [
                {
                    "id_prefecture": r[0],
                    "prefecture_nom": r[1],
                    "nb_parcelles": r[2],
                    "total_surface_decl_ha": float(r[3]) if r[3] is not None else 0.0,
                }
                for r in rows
            ]

            # ---- 6) Par commune ----
            cursor.execute(
                f"""
                SELECT
                    id_commune,
                    commune_nom,
                    COUNT(*) AS nb_parcelles,
                    COALESCE(SUM(surface_decl), 0) AS total_surface_decl_ha
                FROM marts.vw_cep_parcelle
                WHERE {where_sql}
                GROUP BY id_commune, commune_nom
                ORDER BY commune_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_commune"] = [
                {
                    "id_commune": r[0],
                    "commune_nom": r[1],
                    "nb_parcelles": r[2],
                    "total_surface_decl_ha": float(r[3]) if r[3] is not None else 0.0,
                }
                for r in rows
            ]

            # ---- 7) RÃ©gion x filiÃ¨re (croisement pour cartes / graphiques) ----
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    filiere,
                    COALESCE(filiere_label, filiere) AS filiere_label,
                    COUNT(*) AS nb_parcelles,
                    COALESCE(SUM(surface_decl), 0) AS total_surface_decl_ha
                FROM marts.vw_cep_parcelle
                WHERE {where_sql}
                GROUP BY
                    id_region,
                    region_nom,
                    filiere,
                    COALESCE(filiere_label, filiere)
                ORDER BY region_nom, filiere_label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_region_and_filiere"] = [
                {
                    "id_region": r[0],
                    "region_nom": r[1],
                    "filiere": r[2],
                    "filiere_label": r[3],
                    "nb_parcelles": r[4],
                    "total_surface_decl_ha": float(r[5]) if r[5] is not None else 0.0,
                }
                for r in rows
            ]

            # ---- 8) Par pratiques agroÃ©cologiques (multi-valeurs) ----
            cursor.execute(
                f"""
                SELECT
                    u.code AS pratique_code,
                    COALESCE(p.libelle, u.code) AS label,
                    COUNT(DISTINCT cep_uuid) AS nb_parcelles,
                    COALESCE(SUM(surface_decl), 0) AS total_surface_decl_ha
                FROM marts.vw_cep_parcelle
                JOIN LATERAL unnest(pratiques_agroeco_codes) AS u(code) ON TRUE
                LEFT JOIN ref.pratique_agro p ON p.code = u.code
                WHERE {where_sql}
                GROUP BY u.code, COALESCE(p.libelle, u.code)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_pratique_agroeco"] = [
                {
                    "pratique_code": r[0],
                    "label": r[1],
                    "nb_parcelles": r[2],
                    "total_surface_decl_ha": float(r[3]) if r[3] is not None else 0.0,
                }
                for r in rows
            ]

        return Response(data)



# -----------------------------------------------------------------------------
# Liste des couloirs
# -----------------------------------------------------------------------------

class CouloirListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Liste les couloirs de transhumance depuis la vue marts.vw_couloir,
    filtrÃ©s par projet actif via project_code (FIERE / AGRIECO).

    Filtres possibles en query string :
    - ?search=...                  (nom_couloir, localites_traversees,
                                    conflits_details, obs_couloir,
                                    commune_nom, region_nom,
                                    type_couloir_label, appreciation_label)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?type_couloir=...           (code ref.type_couloir)
    - ?saison_usage=...           (code ref.saison_usage)
    - ?statut_couloir=...         (code ref.statut_couloir)
    - ?appreciation_globale=...   (code ref.app_global)
    - ?has_geom=true|false        (prÃ©sence de gÃ©omÃ©trie)
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc  # FIERE / AGRIECO

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        type_couloir = request.query_params.get("type_couloir")
        saison_usage = request.query_params.get("saison_usage")
        statut_couloir = request.query_params.get("statut_couloir")
        appreciation_globale = request.query_params.get("appreciation_globale")

        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques
        if type_couloir:
            where_clauses.append("type_couloir = %s")
            params.append(type_couloir)

        if saison_usage:
            where_clauses.append("saison_usage = %s")
            params.append(saison_usage)

        if statut_couloir:
            where_clauses.append("statut_couloir = %s")
            params.append(statut_couloir)

        if appreciation_globale:
            where_clauses.append("appreciation_globale = %s")
            params.append(appreciation_globale)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte Ã©largie
        if search:
            where_clauses.append(
                "("
                "nom_couloir ILIKE %s OR "
                "localites_traversees ILIKE %s OR "
                "conflits_details ILIKE %s OR "
                "obs_couloir ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s OR "
                "type_couloir_label ILIKE %s OR "
                "appreciation_label ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 8)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_couloir
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_couloir
                WHERE {where_sql}
                ORDER BY nom_couloir NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class CouloirAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    AgrÃ©gations sur les couloirs de transhumance (marts.vw_couloir),
    filtrÃ©es par projet actif (FIERE / AGRIECO) et territoire.

    Filtres :
    - ?search=...
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?type_couloir=...
    - ?saison_usage=...
    - ?statut_couloir=...
    - ?appreciation_globale=...
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Projet courant
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        type_couloir = request.query_params.get("type_couloir")
        saison_usage = request.query_params.get("saison_usage")
        statut_couloir = request.query_params.get("statut_couloir")
        appreciation_globale = request.query_params.get("appreciation_globale")

        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques
        if type_couloir:
            where_clauses.append("type_couloir = %s")
            params.append(type_couloir)

        if saison_usage:
            where_clauses.append("saison_usage = %s")
            params.append(saison_usage)

        if statut_couloir:
            where_clauses.append("statut_couloir = %s")
            params.append(statut_couloir)

        if appreciation_globale:
            where_clauses.append("appreciation_globale = %s")
            params.append(appreciation_globale)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "nom_couloir ILIKE %s OR "
                "localites_traversees ILIKE %s OR "
                "conflits_details ILIKE %s OR "
                "obs_couloir ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s OR "
                "type_couloir_label ILIKE %s OR "
                "appreciation_label ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 8)

        where_sql = " AND ".join(where_clauses)

        data: dict = {}

        with connection.cursor() as cursor:
            # ---- 1) Global ----
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS total_couloirs,
                    COALESCE(SUM(longueur_km), 0) AS longueur_totale_km,
                    ROUND(AVG(longueur_km)::numeric, 2) AS longueur_moy_km,
                    ROUND(AVG(largeur_m)::numeric, 2) AS largeur_moy_m,
                    COUNT(*) FILTER (WHERE geom IS NOT NULL) AS nb_avec_geom,
                    COUNT(*) FILTER (WHERE geom IS NULL) AS nb_sans_geom,
                    COUNT(*) FILTER (
                        WHERE types_conflits_codes IS NOT NULL
                          AND array_length(types_conflits_codes, 1) > 0
                    ) AS nb_couloirs_avec_conflits
                FROM marts.vw_couloir
                WHERE {where_sql}
                """,
                params,
            )
            row = cursor.fetchone()
            data["global"] = {
                "total_couloirs": row[0],
                "longueur_totale_km": float(row[1]) if row[1] is not None else 0.0,
                "longueur_moy_km": float(row[2]) if row[2] is not None else None,
                "largeur_moy_m": float(row[3]) if row[3] is not None else None,
                "nb_avec_geom": row[4],
                "nb_sans_geom": row[5],
                "nb_couloirs_avec_conflits": row[6],
            }

            # ---- 2) Par type de couloir ----
            cursor.execute(
                f"""
                SELECT
                    type_couloir,
                    COALESCE(type_couloir_label, type_couloir) AS label,
                    COUNT(*) AS nb_couloirs,
                    COALESCE(SUM(longueur_km), 0) AS longueur_totale_km,
                    ROUND(AVG(longueur_km)::numeric, 2) AS longueur_moy_km,
                    ROUND(AVG(largeur_m)::numeric, 2) AS largeur_moy_m
                FROM marts.vw_couloir
                WHERE {where_sql}
                GROUP BY type_couloir, COALESCE(type_couloir_label, type_couloir)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_type_couloir"] = [
                {
                    "type_couloir": r[0],
                    "label": r[1],
                    "nb_couloirs": r[2],
                    "longueur_totale_km": float(r[3]) if r[3] is not None else 0.0,
                    "longueur_moy_km": float(r[4]) if r[4] is not None else None,
                    "largeur_moy_m": float(r[5]) if r[5] is not None else None,
                }
                for r in rows
            ]

            # ---- 3) Par statut de couloir ----
            cursor.execute(
                f"""
                SELECT
                    statut_couloir,
                    statut_couloir_label,
                    COUNT(*) AS nb_couloirs,
                    COALESCE(SUM(longueur_km), 0) AS longueur_totale_km
                FROM marts.vw_couloir
                WHERE {where_sql}
                GROUP BY statut_couloir, statut_couloir_label
                ORDER BY statut_couloir_label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_statut_couloir"] = [
                {
                    "statut_couloir": r[0],
                    "statut_couloir_label": r[1],
                    "nb_couloirs": r[2],
                    "longueur_totale_km": float(r[3]) if r[3] is not None else 0.0,
                }
                for r in rows
            ]

            # ---- 4) Par saison dâ€™usage ----
            cursor.execute(
                f"""
                SELECT
                    saison_usage,
                    saison_usage_label,
                    COUNT(*) AS nb_couloirs,
                    COALESCE(SUM(longueur_km), 0) AS longueur_totale_km
                FROM marts.vw_couloir
                WHERE {where_sql}
                GROUP BY saison_usage, saison_usage_label
                ORDER BY saison_usage_label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_saison_usage"] = [
                {
                    "saison_usage": r[0],
                    "saison_usage_label": r[1],
                    "nb_couloirs": r[2],
                    "longueur_totale_km": float(r[3]) if r[3] is not None else 0.0,
                }
                for r in rows
            ]

            # ---- 5) Par apprÃ©ciation globale ----
            cursor.execute(
                f"""
                SELECT
                    appreciation_globale,
                    appreciation_label,
                    COUNT(*) AS nb_couloirs,
                    COALESCE(SUM(longueur_km), 0) AS longueur_totale_km
                FROM marts.vw_couloir
                WHERE {where_sql}
                GROUP BY appreciation_globale, appreciation_label
                ORDER BY appreciation_label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_appreciation"] = [
                {
                    "appreciation_globale": r[0],
                    "appreciation_label": r[1],
                    "nb_couloirs": r[2],
                    "longueur_totale_km": float(r[3]) if r[3] is not None else 0.0,
                }
                for r in rows
            ]

            # ---- 6) Par rÃ©gion ----
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    COUNT(*) AS nb_couloirs,
                    COALESCE(SUM(longueur_km), 0) AS longueur_totale_km
                FROM marts.vw_couloir
                WHERE {where_sql}
                GROUP BY id_region, region_nom
                ORDER BY region_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_region"] = [
                {
                    "id_region": r[0],
                    "region_nom": r[1],
                    "nb_couloirs": r[2],
                    "longueur_totale_km": float(r[3]) if r[3] is not None else 0.0,
                }
                for r in rows
            ]

            # ---- 7) Par prÃ©fecture ----
            cursor.execute(
                f"""
                SELECT
                    id_prefecture,
                    prefecture_nom,
                    COUNT(*) AS nb_couloirs,
                    COALESCE(SUM(longueur_km), 0) AS longueur_totale_km
                FROM marts.vw_couloir
                WHERE {where_sql}
                GROUP BY id_prefecture, prefecture_nom
                ORDER BY prefecture_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_prefecture"] = [
                {
                    "id_prefecture": r[0],
                    "prefecture_nom": r[1],
                    "nb_couloirs": r[2],
                    "longueur_totale_km": float(r[3]) if r[3] is not None else 0.0,
                }
                for r in rows
            ]

            # ---- 8) Par commune ----
            cursor.execute(
                f"""
                SELECT
                    id_commune,
                    commune_nom,
                    COUNT(*) AS nb_couloirs,
                    COALESCE(SUM(longueur_km), 0) AS longueur_totale_km
                FROM marts.vw_couloir
                WHERE {where_sql}
                GROUP BY id_commune, commune_nom
                ORDER BY commune_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_commune"] = [
                {
                    "id_commune": r[0],
                    "commune_nom": r[1],
                    "nb_couloirs": r[2],
                    "longueur_totale_km": float(r[3]) if r[3] is not None else 0.0,
                }
                for r in rows
            ]

            # ---- 9) RÃ©gion x type de couloir ----
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    type_couloir,
                    COALESCE(type_couloir_label, type_couloir) AS type_couloir_label,
                    COUNT(*) AS nb_couloirs,
                    COALESCE(SUM(longueur_km), 0) AS longueur_totale_km
                FROM marts.vw_couloir
                WHERE {where_sql}
                GROUP BY
                    id_region,
                    region_nom,
                    type_couloir,
                    COALESCE(type_couloir_label, type_couloir)
                ORDER BY region_nom, type_couloir_label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_region_and_type"] = [
                {
                    "id_region": r[0],
                    "region_nom": r[1],
                    "type_couloir": r[2],
                    "type_couloir_label": r[3],
                    "nb_couloirs": r[4],
                    "longueur_totale_km": float(r[5]) if r[5] is not None else 0.0,
                }
                for r in rows
            ]

            # ---- 10) Par espÃ¨ce de troupeau (multi-valeurs) ----
            cursor.execute(
                f"""
                SELECT
                    u.code AS espece_code,
                    COALESCE(e.libelle_fr, u.code) AS label,
                    COUNT(DISTINCT id_couloir) AS nb_couloirs,
                    COALESCE(SUM(longueur_km), 0) AS longueur_totale_km
                FROM marts.vw_couloir
                JOIN LATERAL unnest(especes_codes) AS u(code) ON TRUE
                LEFT JOIN ref.espece_troupeau e ON e.code = u.code
                WHERE {where_sql}
                GROUP BY u.code, COALESCE(e.libelle_fr, u.code)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_espece"] = [
                {
                    "espece_code": r[0],
                    "label": r[1],
                    "nb_couloirs": r[2],
                    "longueur_totale_km": float(r[3]) if r[3] is not None else 0.0,
                }
                for r in rows
            ]

            # ---- 11) Par type dâ€™infrastructure pastorale (multi-valeurs) ----
            cursor.execute(
                f"""
                SELECT
                    u.code AS infra_code,
                    COALESCE(i.libelle_fr, u.code) AS label,
                    COUNT(DISTINCT id_couloir) AS nb_couloirs
                FROM marts.vw_couloir
                JOIN LATERAL unnest(infra_codes) AS u(code) ON TRUE
                LEFT JOIN ref.infra_pastorale i ON i.code = u.code
                WHERE {where_sql}
                GROUP BY u.code, COALESCE(i.libelle_fr, u.code)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_infrastructure"] = [
                {
                    "infra_code": r[0],
                    "label": r[1],
                    "nb_couloirs": r[2],
                }
                for r in rows
            ]

            # ---- 12) Par type de conflit (multi-valeurs) ----
            cursor.execute(
                f"""
                SELECT
                    u.code AS type_conflit_code,
                    COALESCE(t.libelle_fr, u.code) AS label,
                    COUNT(DISTINCT id_couloir) AS nb_couloirs
                FROM marts.vw_couloir
                JOIN LATERAL unnest(types_conflits_codes) AS u(code) ON TRUE
                LEFT JOIN ref.types_conflit t ON t.code = u.code
                WHERE {where_sql}
                GROUP BY u.code, COALESCE(t.libelle_fr, u.code)
                ORDER BY label
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_type_conflit"] = [
                {
                    "type_conflit_code": r[0],
                    "label": r[1],
                    "nb_couloirs": r[2],
                }
                for r in rows
            ]

        return Response(data)


# -----------------------------------------------------------------------------
# Liste des emploi dom
# -----------------------------------------------------------------------------

class EntEmploiDomListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Liste les emplois par domaine (marts.vw_ent_emploi_dom),
    filtrÃ©s par projet actif via project_code (FIERE / AGRIECO).

    Filtres possibles en query string :
    - ?search=...                 (raison_sociale, domaine_label, domaine_autre,
                                   localite, commune_nom, region_nom)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?annee_ref=2023
    - ?periode_ref=ANNUEL       (code ref.periode_ref)
    - ?domaine_code=...         (code ref.domaine_emploi)
    - ?emploi_vert_dom=...      (valeur brute du champ, ex. 'oui'/'non' si c'est du texte)
    - ?id_ent=...               (filtrer sur une entreprise spÃ©cifique)
    - ?has_geom=true|false      (prÃ©sence de gÃ©omÃ©trie)
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc  # FIERE / AGRIECO

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        annee_ref = request.query_params.get("annee_ref")
        periode_ref = request.query_params.get("periode_ref")
        domaine_code = request.query_params.get("domaine_code")
        emploi_vert_dom = request.query_params.get("emploi_vert_dom")
        id_ent = request.query_params.get("id_ent")

        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # Temps / domaine
        if annee_ref:
            where_clauses.append("annee_ref = %s")
            params.append(annee_ref)

        if periode_ref:
            where_clauses.append("periode_ref = %s")
            params.append(periode_ref)

        if domaine_code:
            where_clauses.append("domaine_code = %s")
            params.append(domaine_code)

        if emploi_vert_dom:
            # on ne suppose pas le type, on filtre tel quel
            where_clauses.append("emploi_vert_dom = %s")
            params.append(emploi_vert_dom)

        if id_ent:
            where_clauses.append("id_ent = %s")
            params.append(id_ent)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "raison_sociale ILIKE %s OR "
                "domaine_label ILIKE %s OR "
                "domaine_autre ILIKE %s OR "
                "localite ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 6)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_ent_emploi_dom
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_ent_emploi_dom
                WHERE {where_sql}
                ORDER BY raison_sociale NULLS LAST, domaine_label NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class EntEmploiDomAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    AgrÃ©gations sur les emplois par domaine (marts.vw_ent_emploi_dom),
    filtrÃ©es par projet actif (FIERE / AGRIECO) et par territoire.

    MÃªme filtres que EntEmploiDomListView :
    - ?search=...
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?annee_ref=2023
    - ?periode_ref=ANNUEL
    - ?domaine_code=...
    - ?emploi_vert_dom=...
    - ?id_ent=...
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Projet courant
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc

        # -------- Filtres (identiques Ã  la ListView) --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        annee_ref = request.query_params.get("annee_ref")
        periode_ref = request.query_params.get("periode_ref")
        domaine_code = request.query_params.get("domaine_code")
        emploi_vert_dom = request.query_params.get("emploi_vert_dom")
        id_ent = request.query_params.get("id_ent")

        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # Temps / domaine
        if annee_ref:
            where_clauses.append("annee_ref = %s")
            params.append(annee_ref)

        if periode_ref:
            where_clauses.append("periode_ref = %s")
            params.append(periode_ref)

        if domaine_code:
            where_clauses.append("domaine_code = %s")
            params.append(domaine_code)

        if emploi_vert_dom:
            where_clauses.append("emploi_vert_dom = %s")
            params.append(emploi_vert_dom)

        if id_ent:
            where_clauses.append("id_ent = %s")
            params.append(id_ent)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte (alignÃ©e sur la ListView)
        if search:
            where_clauses.append(
                "("
                "raison_sociale ILIKE %s OR "
                "domaine_label ILIKE %s OR "
                "domaine_autre ILIKE %s OR "
                "localite ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 6)

        where_sql = " AND ".join(where_clauses)

        data: dict = {}

        with connection.cursor() as cursor:
            # ---- 1) Global au niveau entreprise ----
            # On Ã©vite de compter plusieurs fois la mÃªme entreprise
            cursor.execute(
                f"""
                SELECT
                    COUNT(DISTINCT id_ent) AS nb_entreprises,
                    COALESCE(SUM(emplois_total), 0) AS emplois_total,
                    COALESCE(SUM(emplois_femmes), 0) AS emplois_femmes,
                    COALESCE(SUM(emplois_jeunes), 0) AS emplois_jeunes
                FROM (
                    SELECT DISTINCT
                        emploi_uuid,
                        id_ent,
                        emplois_total,
                        emplois_femmes,
                        emplois_jeunes
                    FROM marts.vw_ent_emploi_dom
                    WHERE {where_sql}
                ) t
                """,
                params,
            )
            row = cursor.fetchone()
            nb_entreprises = row[0] or 0
            emplois_total = row[1] or 0
            emplois_femmes = row[2] or 0
            emplois_jeunes = row[3] or 0

            part_femmes_pct = (
                round(100.0 * emplois_femmes / emplois_total, 2)
                if emplois_total > 0
                else None
            )
            part_jeunes_pct = (
                round(100.0 * emplois_jeunes / emplois_total, 2)
                if emplois_total > 0
                else None
            )

            data["global_entreprises"] = {
                "nb_entreprises": nb_entreprises,
                "emplois_total": emplois_total,
                "emplois_femmes": emplois_femmes,
                "emplois_jeunes": emplois_jeunes,
                "part_femmes_pct": part_femmes_pct,
                "part_jeunes_pct": part_jeunes_pct,
            }

            # ClÃ© "global" pour compatibilitÃ© dashboard frontend
            data["global"] = {
                "nb_emplois_totaux": emplois_total,
                "nb_emplois_femmes": emplois_femmes,
                "nb_emplois_crees": emplois_total,
                "nb_emplois_maintenus": 0,
                "total_emplois": emplois_total,
                "emplois_femmes": emplois_femmes,
                "emplois_jeunes": emplois_jeunes,
                "part_femmes_pct": part_femmes_pct,
                "part_jeunes_pct": part_jeunes_pct,
            }

            # ---- 2) Global au niveau domaines ----
            cursor.execute(
                f"""
                SELECT
                    COALESCE(SUM(nb_empl_dom), 0) AS nb_emplois_dom,
                    COALESCE(SUM(nb_empl_fem_dom), 0) AS nb_emplois_fem_dom,
                    COALESCE(SUM(nb_empl_jeunes_dom), 0) AS nb_emplois_jeunes_dom,
                    COALESCE(SUM(nb_empl_pvh_dom), 0) AS nb_emplois_pvh_dom,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN (
                                    emploi_vert_dom::text IN ('true','t','1')
                                    OR emploi_vert_dom::text ILIKE 'oui'
                                )
                                THEN nb_empl_dom
                                ELSE 0
                            END
                        ),
                        0
                    ) AS nb_emplois_verts_dom
                FROM marts.vw_ent_emploi_dom
                WHERE {where_sql}
                """,
                params,
            )
            row = cursor.fetchone()
            nb_emplois_dom = row[0] or 0
            nb_emplois_fem_dom = row[1] or 0
            nb_emplois_jeunes_dom = row[2] or 0
            nb_emplois_pvh_dom = row[3] or 0
            nb_emplois_verts_dom = row[4] or 0

            part_fem_dom_pct = (
                round(100.0 * nb_emplois_fem_dom / nb_emplois_dom, 2)
                if nb_emplois_dom > 0
                else None
            )
            part_jeunes_dom_pct = (
                round(100.0 * nb_emplois_jeunes_dom / nb_emplois_dom, 2)
                if nb_emplois_dom > 0
                else None
            )
            part_pvh_dom_pct = (
                round(100.0 * nb_emplois_pvh_dom / nb_emplois_dom, 2)
                if nb_emplois_dom > 0
                else None
            )
            part_verts_dom_pct = (
                round(100.0 * nb_emplois_verts_dom / nb_emplois_dom, 2)
                if nb_emplois_dom > 0
                else None
            )

            data["global_domaines"] = {
                "nb_emplois_dom": nb_emplois_dom,
                "nb_emplois_fem_dom": nb_emplois_fem_dom,
                "nb_emplois_jeunes_dom": nb_emplois_jeunes_dom,
                "nb_emplois_pvh_dom": nb_emplois_pvh_dom,
                "nb_emplois_verts_dom": nb_emplois_verts_dom,
                "part_fem_dom_pct": part_fem_dom_pct,
                "part_jeunes_dom_pct": part_jeunes_dom_pct,
                "part_pvh_dom_pct": part_pvh_dom_pct,
                "part_verts_dom_pct": part_verts_dom_pct,
            }

            # ---- 3) Par domaine d'emploi ----
            cursor.execute(
                f"""
                SELECT
                    domaine_code,
                    domaine_label,
                    COUNT(*) AS nb_enregistrements,
                    COUNT(DISTINCT id_ent) AS nb_entreprises,
                    COALESCE(SUM(nb_empl_dom), 0) AS nb_emplois_dom,
                    COALESCE(SUM(nb_empl_fem_dom), 0) AS nb_emplois_fem_dom,
                    COALESCE(SUM(nb_empl_jeunes_dom), 0) AS nb_emplois_jeunes_dom,
                    COALESCE(SUM(nb_empl_pvh_dom), 0) AS nb_emplois_pvh_dom,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN (
                                    emploi_vert_dom::text IN ('true','t','1')
                                    OR emploi_vert_dom::text ILIKE 'oui'
                                )
                                THEN nb_empl_dom
                                ELSE 0
                            END
                        ),
                        0
                    ) AS nb_emplois_verts_dom
                FROM marts.vw_ent_emploi_dom
                WHERE {where_sql}
                GROUP BY domaine_code, domaine_label
                ORDER BY domaine_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_domaine = []
            for r in rows:
                dom_code = r[0]
                dom_label = r[1]
                nb_enreg = r[2]
                nb_ent_dom = r[3]
                nb_dom = r[4] or 0
                nb_f_dom = r[5] or 0
                nb_j_dom = r[6] or 0
                nb_pvh_dom = r[7] or 0
                nb_vert_dom = r[8] or 0

                part_f = round(100.0 * nb_f_dom / nb_dom, 2) if nb_dom > 0 else None
                part_j = round(100.0 * nb_j_dom / nb_dom, 2) if nb_dom > 0 else None
                part_pvh = (
                    round(100.0 * nb_pvh_dom / nb_dom, 2) if nb_dom > 0 else None
                )
                part_vert = (
                    round(100.0 * nb_vert_dom / nb_dom, 2) if nb_dom > 0 else None
                )

                by_domaine.append(
                    {
                        "domaine_code": dom_code,
                        "domaine_label": dom_label,
                        "nb_enregistrements": nb_enreg,
                        "nb_entreprises": nb_ent_dom,
                        "nb_emplois_dom": nb_dom,
                        "nb_emplois_fem_dom": nb_f_dom,
                        "nb_emplois_jeunes_dom": nb_j_dom,
                        "nb_emplois_pvh_dom": nb_pvh_dom,
                        "nb_emplois_verts_dom": nb_vert_dom,
                        "part_femmes_pct": part_f,
                        "part_jeunes_pct": part_j,
                        "part_pvh_pct": part_pvh,
                        "part_verts_pct": part_vert,
                    }
                )
            data["by_domaine"] = by_domaine

            # ---- 4) Par rÃ©gion ----
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    COUNT(DISTINCT id_ent) AS nb_entreprises,
                    COALESCE(SUM(nb_empl_dom), 0) AS nb_emplois_dom,
                    COALESCE(SUM(nb_empl_fem_dom), 0) AS nb_emplois_fem_dom,
                    COALESCE(SUM(nb_empl_jeunes_dom), 0) AS nb_emplois_jeunes_dom,
                    COALESCE(SUM(nb_empl_pvh_dom), 0) AS nb_emplois_pvh_dom,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN (
                                    emploi_vert_dom::text IN ('true','t','1')
                                    OR emploi_vert_dom::text ILIKE 'oui'
                                )
                                THEN nb_empl_dom
                                ELSE 0
                            END
                        ),
                        0
                    ) AS nb_emplois_verts_dom
                FROM marts.vw_ent_emploi_dom
                WHERE {where_sql}
                GROUP BY id_region, region_nom
                ORDER BY region_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            by_region = []
            for r in rows:
                reg_id = r[0]
                reg_nom = r[1]
                nb_ent_reg = r[2]
                nb_dom = r[3] or 0
                nb_f_dom = r[4] or 0
                nb_j_dom = r[5] or 0
                nb_pvh_dom = r[6] or 0
                nb_vert_dom = r[7] or 0

                part_f = round(100.0 * nb_f_dom / nb_dom, 2) if nb_dom > 0 else None
                part_j = round(100.0 * nb_j_dom / nb_dom, 2) if nb_dom > 0 else None
                part_pvh = (
                    round(100.0 * nb_pvh_dom / nb_dom, 2) if nb_dom > 0 else None
                )
                part_vert = (
                    round(100.0 * nb_vert_dom / nb_dom, 2) if nb_dom > 0 else None
                )

                by_region.append(
                    {
                        "id_region": reg_id,
                        "region_nom": reg_nom,
                        "nb_entreprises": nb_ent_reg,
                        "nb_emplois_dom": nb_dom,
                        "nb_emplois_fem_dom": nb_f_dom,
                        "nb_emplois_jeunes_dom": nb_j_dom,
                        "nb_emplois_pvh_dom": nb_pvh_dom,
                        "nb_emplois_verts_dom": nb_vert_dom,
                        "part_femmes_pct": part_f,
                        "part_jeunes_pct": part_j,
                        "part_pvh_pct": part_pvh,
                        "part_verts_pct": part_vert,
                    }
                )
            data["by_region"] = by_region

            # ---- 5) RÃ©gion x domaine ----
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    domaine_code,
                    domaine_label,
                    COUNT(DISTINCT id_ent) AS nb_entreprises,
                    COALESCE(SUM(nb_empl_dom), 0) AS nb_emplois_dom,
                    COALESCE(SUM(nb_empl_fem_dom), 0) AS nb_emplois_fem_dom,
                    COALESCE(SUM(nb_empl_jeunes_dom), 0) AS nb_emplois_jeunes_dom,
                    COALESCE(SUM(nb_empl_pvh_dom), 0) AS nb_emplois_pvh_dom,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN (
                                    emploi_vert_dom::text IN ('true','t','1')
                                    OR emploi_vert_dom::text ILIKE 'oui'
                                )
                                THEN nb_empl_dom
                                ELSE 0
                            END
                        ),
                        0
                    ) AS nb_emplois_verts_dom
                FROM marts.vw_ent_emploi_dom
                WHERE {where_sql}
                GROUP BY
                    id_region,
                    region_nom,
                    domaine_code,
                    domaine_label
                ORDER BY region_nom, domaine_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_region_and_domaine = []
            for r in rows:
                reg_id = r[0]
                reg_nom = r[1]
                dom_code = r[2]
                dom_label = r[3]
                nb_ent_reg_dom = r[4]
                nb_dom = r[5] or 0
                nb_f_dom = r[6] or 0
                nb_j_dom = r[7] or 0
                nb_pvh_dom = r[8] or 0
                nb_vert_dom = r[9] or 0

                part_f = round(100.0 * nb_f_dom / nb_dom, 2) if nb_dom > 0 else None
                part_j = round(100.0 * nb_j_dom / nb_dom, 2) if nb_dom > 0 else None
                part_pvh = (
                    round(100.0 * nb_pvh_dom / nb_dom, 2) if nb_dom > 0 else None
                )
                part_vert = (
                    round(100.0 * nb_vert_dom / nb_dom, 2) if nb_dom > 0 else None
                )

                by_region_and_domaine.append(
                    {
                        "id_region": reg_id,
                        "region_nom": reg_nom,
                        "domaine_code": dom_code,
                        "domaine_label": dom_label,
                        "nb_entreprises": nb_ent_reg_dom,
                        "nb_emplois_dom": nb_dom,
                        "nb_emplois_fem_dom": nb_f_dom,
                        "nb_emplois_jeunes_dom": nb_j_dom,
                        "nb_emplois_pvh_dom": nb_pvh_dom,
                        "nb_emplois_verts_dom": nb_vert_dom,
                        "part_femmes_pct": part_f,
                        "part_jeunes_pct": part_j,
                        "part_pvh_pct": part_pvh,
                        "part_verts_pct": part_vert,
                    }
                )
            data["by_region_and_domaine"] = by_region_and_domaine

            # ---- 6) Par prÃ©fecture ----
            cursor.execute(
                f"""
                SELECT
                    id_prefecture,
                    prefecture_nom,
                    COUNT(DISTINCT id_ent) AS nb_entreprises,
                    COALESCE(SUM(nb_empl_dom), 0) AS nb_emplois_dom
                FROM marts.vw_ent_emploi_dom
                WHERE {where_sql}
                GROUP BY id_prefecture, prefecture_nom
                ORDER BY prefecture_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_prefecture"] = [
                {
                    "id_prefecture": r[0],
                    "prefecture_nom": r[1],
                    "nb_entreprises": r[2],
                    "nb_emplois_dom": r[3] or 0,
                }
                for r in rows
            ]

            # ---- 7) Par commune ----
            cursor.execute(
                f"""
                SELECT
                    id_commune,
                    commune_nom,
                    COUNT(DISTINCT id_ent) AS nb_entreprises,
                    COALESCE(SUM(nb_empl_dom), 0) AS nb_emplois_dom
                FROM marts.vw_ent_emploi_dom
                WHERE {where_sql}
                GROUP BY id_commune, commune_nom
                ORDER BY commune_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_commune"] = [
                {
                    "id_commune": r[0],
                    "commune_nom": r[1],
                    "nb_entreprises": r[2],
                    "nb_emplois_dom": r[3] or 0,
                }
                for r in rows
            ]

            # ---- 8) Par annÃ©e / pÃ©riode de rÃ©fÃ©rence ----
            cursor.execute(
                f"""
                SELECT
                    annee_ref,
                    periode_ref,
                    periode_ref_label,
                    COUNT(DISTINCT id_ent) AS nb_entreprises,
                    COALESCE(SUM(nb_empl_dom), 0) AS nb_emplois_dom
                FROM marts.vw_ent_emploi_dom
                WHERE {where_sql}
                GROUP BY annee_ref, periode_ref, periode_ref_label
                ORDER BY annee_ref, periode_ref
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_annee_periode"] = [
                {
                    "annee_ref": r[0],
                    "periode_ref": r[1],
                    "periode_ref_label": r[2],
                    "nb_entreprises": r[3],
                    "nb_emplois_dom": r[4] or 0,
                }
                for r in rows
            ]

        return Response(data)



# -----------------------------------------------------------------------------
# Liste des insertions dom
# -----------------------------------------------------------------------------

class EntInsertionDomListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Liste les insertions par domaine (marts.vw_ent_insertion_dom),
    filtrÃ©es par projet actif via project_code (FIERE / AGRIECO).

    Filtres possibles en query string :
    - ?search=...                 (raison_sociale, domaine_label, domaine_autre,
                                   type_insertion_label, type_insertion_autres,
                                   localite, commune_nom, region_nom)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?annee_ref=2023
    - ?periode_ref=ANNUEL        (code ref.periode_ref)
    - ?domaine_code=...          (code ref.domaine_emploi)
    - ?type_insertion_code=...   (code ref.type_insertion)
    - ?insertion_verte_dom=...   (valeur brute, ex. 'oui'/'non' ou bool)
    - ?id_ent=...                (entreprise spÃ©cifique)
    - ?has_geom=true|false       (prÃ©sence de gÃ©omÃ©trie)
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc  # FIERE / AGRIECO

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        annee_ref = request.query_params.get("annee_ref")
        periode_ref = request.query_params.get("periode_ref")
        domaine_code = request.query_params.get("domaine_code")
        type_insertion_code = request.query_params.get("type_insertion_code")
        insertion_verte_dom = request.query_params.get("insertion_verte_dom")
        id_ent = request.query_params.get("id_ent")

        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # Temps / domaine / type
        if annee_ref:
            where_clauses.append("annee_ref = %s")
            params.append(annee_ref)

        if periode_ref:
            where_clauses.append("periode_ref = %s")
            params.append(periode_ref)

        if domaine_code:
            where_clauses.append("domaine_code = %s")
            params.append(domaine_code)

        if type_insertion_code:
            where_clauses.append("type_insertion_code = %s")
            params.append(type_insertion_code)

        if insertion_verte_dom:
            # on prend la valeur telle quelle (bool/texte selon ton modÃ¨le)
            where_clauses.append("insertion_verte_dom = %s")
            params.append(insertion_verte_dom)

        if id_ent:
            where_clauses.append("id_ent = %s")
            params.append(id_ent)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "raison_sociale ILIKE %s OR "
                "domaine_label ILIKE %s OR "
                "domaine_autre ILIKE %s OR "
                "type_insertion_label ILIKE %s OR "
                "type_insertion_autres ILIKE %s OR "
                "localite ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 8)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_ent_insertion_dom
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_ent_insertion_dom
                WHERE {where_sql}
                ORDER BY raison_sociale NULLS LAST,
                         domaine_label NULLS LAST,
                         type_insertion_label NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class EntInsertionDomAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    AgrÃ©gations sur les insertions par domaine (marts.vw_ent_insertion_dom),
    filtrÃ©es par projet actif (FIERE / AGRIECO) et par territoire.

    Filtres disponibles (alignÃ©s sur EntInsertionDomListView) :
    - ?search=...
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?annee_ref=2023
    - ?periode_ref=ANNUEL
    - ?domaine_code=...
    - ?type_insertion_code=...
    - ?insertion_verte_dom=...
    - ?id_ent=...
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Projet courant
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        annee_ref = request.query_params.get("annee_ref")
        periode_ref = request.query_params.get("periode_ref")
        domaine_code = request.query_params.get("domaine_code")
        type_insertion_code = request.query_params.get("type_insertion_code")
        insertion_verte_dom = request.query_params.get("insertion_verte_dom")
        id_ent = request.query_params.get("id_ent")

        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # Temps / domaine / type
        if annee_ref:
            where_clauses.append("annee_ref = %s")
            params.append(annee_ref)

        if periode_ref:
            where_clauses.append("periode_ref = %s")
            params.append(periode_ref)

        if domaine_code:
            where_clauses.append("domaine_code = %s")
            params.append(domaine_code)

        if type_insertion_code:
            where_clauses.append("type_insertion_code = %s")
            params.append(type_insertion_code)

        if insertion_verte_dom:
            where_clauses.append("insertion_verte_dom = %s")
            params.append(insertion_verte_dom)

        if id_ent:
            where_clauses.append("id_ent = %s")
            params.append(id_ent)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte (alignÃ©e sur la ListView)
        if search:
            where_clauses.append(
                "("
                "raison_sociale ILIKE %s OR "
                "domaine_label ILIKE %s OR "
                "domaine_autre ILIKE %s OR "
                "type_insertion_label ILIKE %s OR "
                "type_insertion_autres ILIKE %s OR "
                "localite ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 8)

        where_sql = " AND ".join(where_clauses)

        data: dict = {}

        with connection.cursor() as cursor:
            # ---- 1) Global au niveau insertion (entreprise/insertion) ----
            cursor.execute(
                f"""
                SELECT
                    COUNT(DISTINCT id_ent) AS nb_entreprises,
                    COUNT(DISTINCT insertion_uuid) AS nb_insertions,
                    COALESCE(SUM(insert_total), 0) AS insert_total,
                    COALESCE(SUM(insert_femmes), 0) AS insert_femmes,
                    COALESCE(SUM(insert_jeunes), 0) AS insert_jeunes
                FROM (
                    SELECT DISTINCT
                        insertion_uuid,
                        id_ent,
                        insert_total,
                        insert_femmes,
                        insert_jeunes
                    FROM marts.vw_ent_insertion_dom
                    WHERE {where_sql}
                ) t
                """,
                params,
            )
            row = cursor.fetchone()
            nb_entreprises = row[0] or 0
            nb_insertions = row[1] or 0
            insert_total = row[2] or 0
            insert_femmes = row[3] or 0
            insert_jeunes = row[4] or 0

            part_femmes_pct = (
                round(100.0 * insert_femmes / insert_total, 2)
                if insert_total > 0
                else None
            )
            part_jeunes_pct = (
                round(100.0 * insert_jeunes / insert_total, 2)
                if insert_total > 0
                else None
            )

            data["global_insertions"] = {
                "nb_entreprises": nb_entreprises,
                "nb_insertions": nb_insertions,
                "insert_total": insert_total,
                "insert_femmes": insert_femmes,
                "insert_jeunes": insert_jeunes,
                "part_femmes_pct": part_femmes_pct,
                "part_jeunes_pct": part_jeunes_pct,
            }

            cursor.execute(
                f"""
                SELECT
                    COUNT(DISTINCT insertion_uuid) FILTER (
                        WHERE duree_insertion_mois IS NOT NULL
                    ) AS nb_insertions_datees,
                    COUNT(DISTINCT insertion_uuid) FILTER (
                        WHERE duree_insertion_mois <= 3
                    ) AS nb_insertions_3m,
                    COUNT(DISTINCT insertion_uuid) FILTER (
                        WHERE duree_insertion_mois <= 6
                    ) AS nb_insertions_6m,
                    COUNT(DISTINCT insertion_uuid) FILTER (
                        WHERE duree_insertion_mois <= 12
                    ) AS nb_insertions_12m
                FROM marts.vw_ent_insertion_dom
                WHERE {where_sql}
                """,
                params,
            )
            delay_row = cursor.fetchone()
            nb_insertions_datees = delay_row[0] or 0
            nb_insertions_3m = delay_row[1] or 0
            nb_insertions_6m = delay_row[2] or 0
            nb_insertions_12m = delay_row[3] or 0
            insertion_denominator = nb_insertions_datees or nb_insertions

            # Cle "global" pour compatibilite dashboard frontend
            data["global"] = {
                "nb_insertions": nb_insertions,
                "total_insertions": insert_total,
                "insertion_3m": nb_insertions_3m,
                "insertion_6m": nb_insertions_6m,
                "insertion_12m": nb_insertions_12m,
                "taux_insertion_3m": (
                    round(100.0 * nb_insertions_3m / insertion_denominator, 2)
                    if insertion_denominator > 0
                    else None
                ),
                "taux_insertion_6m": (
                    round(100.0 * nb_insertions_6m / insertion_denominator, 2)
                    if insertion_denominator > 0
                    else None
                ),
                "taux_insertion_12m": (
                    round(100.0 * nb_insertions_12m / insertion_denominator, 2)
                    if insertion_denominator > 0
                    else None
                ),
                "taux_3m": (
                    round(100.0 * nb_insertions_3m / insertion_denominator, 2)
                    if insertion_denominator > 0
                    else None
                ),
                "taux_6m": (
                    round(100.0 * nb_insertions_6m / insertion_denominator, 2)
                    if insertion_denominator > 0
                    else None
                ),
                "taux_12m": (
                    round(100.0 * nb_insertions_12m / insertion_denominator, 2)
                    if insertion_denominator > 0
                    else None
                ),
                "insert_femmes": insert_femmes,
                "insert_jeunes": insert_jeunes,
                "part_femmes_pct": part_femmes_pct,
                "part_jeunes_pct": part_jeunes_pct,
            }
            # ---- 2) Global au niveau domaines d'insertion ----
            cursor.execute(
                f"""
                SELECT
                    COALESCE(SUM(nb_ins_dom), 0) AS nb_ins_dom,
                    COALESCE(SUM(nb_ins_fem_dom), 0) AS nb_ins_fem_dom,
                    COALESCE(SUM(nb_ins_jeunes_dom), 0) AS nb_ins_jeunes_dom,
                    COALESCE(SUM(nb_ins_pvh_dom), 0) AS nb_ins_pvh_dom,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN (
                                    insertion_verte_dom::text IN ('true','t','1')
                                    OR insertion_verte_dom::text ILIKE 'oui'
                                )
                                THEN nb_ins_dom
                                ELSE 0
                            END
                        ),
                        0
                    ) AS nb_ins_verts_dom
                FROM marts.vw_ent_insertion_dom
                WHERE {where_sql}
                """,
                params,
            )
            row = cursor.fetchone()
            nb_ins_dom = row[0] or 0
            nb_ins_fem_dom = row[1] or 0
            nb_ins_jeunes_dom = row[2] or 0
            nb_ins_pvh_dom = row[3] or 0
            nb_ins_verts_dom = row[4] or 0

            part_fem_dom_pct = (
                round(100.0 * nb_ins_fem_dom / nb_ins_dom, 2)
                if nb_ins_dom > 0
                else None
            )
            part_jeunes_dom_pct = (
                round(100.0 * nb_ins_jeunes_dom / nb_ins_dom, 2)
                if nb_ins_dom > 0
                else None
            )
            part_pvh_dom_pct = (
                round(100.0 * nb_ins_pvh_dom / nb_ins_dom, 2)
                if nb_ins_dom > 0
                else None
            )
            part_verts_dom_pct = (
                round(100.0 * nb_ins_verts_dom / nb_ins_dom, 2)
                if nb_ins_dom > 0
                else None
            )

            data["global_domaines"] = {
                "nb_ins_dom": nb_ins_dom,
                "nb_ins_fem_dom": nb_ins_fem_dom,
                "nb_ins_jeunes_dom": nb_ins_jeunes_dom,
                "nb_ins_pvh_dom": nb_ins_pvh_dom,
                "nb_ins_verts_dom": nb_ins_verts_dom,
                "part_fem_dom_pct": part_fem_dom_pct,
                "part_jeunes_dom_pct": part_jeunes_dom_pct,
                "part_pvh_dom_pct": part_pvh_dom_pct,
                "part_verts_dom_pct": part_verts_dom_pct,
            }

            # ---- 3) Statistiques de durÃ©e d'insertion ----
            cursor.execute(
                f"""
                SELECT
                    AVG(duree_insertion_mois)::numeric,
                    MIN(duree_insertion_mois),
                    MAX(duree_insertion_mois)
                FROM marts.vw_ent_insertion_dom
                WHERE {where_sql}
                  AND duree_insertion_mois IS NOT NULL
                """,
                params,
            )
            row = cursor.fetchone()
            avg_duree = float(row[0]) if row[0] is not None else None
            min_duree = row[1]
            max_duree = row[2]

            data["duree_insertion"] = {
                "avg_duree_mois": avg_duree,
                "min_duree_mois": min_duree,
                "max_duree_mois": max_duree,
            }

            # ---- 4) Par domaine d'emploi ----
            cursor.execute(
                f"""
                SELECT
                    domaine_code,
                    domaine_label,
                    COUNT(*) AS nb_enregistrements,
                    COUNT(DISTINCT id_ent) AS nb_entreprises,
                    COUNT(DISTINCT insertion_uuid) AS nb_insertions,
                    COALESCE(SUM(nb_ins_dom), 0) AS nb_ins_dom,
                    COALESCE(SUM(nb_ins_fem_dom), 0) AS nb_ins_fem_dom,
                    COALESCE(SUM(nb_ins_jeunes_dom), 0) AS nb_ins_jeunes_dom,
                    COALESCE(SUM(nb_ins_pvh_dom), 0) AS nb_ins_pvh_dom,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN (
                                    insertion_verte_dom::text IN ('true','t','1')
                                    OR insertion_verte_dom::text ILIKE 'oui'
                                )
                                THEN nb_ins_dom
                                ELSE 0
                            END
                        ),
                        0
                    ) AS nb_ins_verts_dom
                FROM marts.vw_ent_insertion_dom
                WHERE {where_sql}
                GROUP BY domaine_code, domaine_label
                ORDER BY domaine_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_domaine = []
            for r in rows:
                dom_code = r[0]
                dom_label = r[1]
                nb_enreg = r[2]
                nb_ent_dom = r[3]
                nb_ins = r[4]
                nb_dom = r[5] or 0
                nb_f_dom = r[6] or 0
                nb_j_dom = r[7] or 0
                nb_pvh_dom = r[8] or 0
                nb_vert_dom = r[9] or 0

                part_f = round(100.0 * nb_f_dom / nb_dom, 2) if nb_dom > 0 else None
                part_j = round(100.0 * nb_j_dom / nb_dom, 2) if nb_dom > 0 else None
                part_pvh = (
                    round(100.0 * nb_pvh_dom / nb_dom, 2) if nb_dom > 0 else None
                )
                part_vert = (
                    round(100.0 * nb_vert_dom / nb_dom, 2) if nb_dom > 0 else None
                )

                by_domaine.append(
                    {
                        "domaine_code": dom_code,
                        "domaine_label": dom_label,
                        "nb_enregistrements": nb_enreg,
                        "nb_entreprises": nb_ent_dom,
                        "nb_insertions": nb_ins,
                        "nb_ins_dom": nb_dom,
                        "nb_ins_fem_dom": nb_f_dom,
                        "nb_ins_jeunes_dom": nb_j_dom,
                        "nb_ins_pvh_dom": nb_pvh_dom,
                        "nb_ins_verts_dom": nb_vert_dom,
                        "part_femmes_pct": part_f,
                        "part_jeunes_pct": part_j,
                        "part_pvh_pct": part_pvh,
                        "part_verts_pct": part_vert,
                    }
                )
            data["by_domaine"] = by_domaine

            # ---- 5) Par type d'insertion ----
            cursor.execute(
                f"""
                SELECT
                    type_insertion_code,
                    type_insertion_label,
                    COUNT(*) AS nb_enregistrements,
                    COUNT(DISTINCT id_ent) AS nb_entreprises,
                    COUNT(DISTINCT insertion_uuid) AS nb_insertions,
                    COALESCE(SUM(nb_ins_dom), 0) AS nb_ins_dom,
                    COALESCE(SUM(nb_ins_fem_dom), 0) AS nb_ins_fem_dom,
                    COALESCE(SUM(nb_ins_jeunes_dom), 0) AS nb_ins_jeunes_dom,
                    COALESCE(SUM(nb_ins_pvh_dom), 0) AS nb_ins_pvh_dom,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN (
                                    insertion_verte_dom::text IN ('true','t','1')
                                    OR insertion_verte_dom::text ILIKE 'oui'
                                )
                                THEN nb_ins_dom
                                ELSE 0
                            END
                        ),
                        0
                    ) AS nb_ins_verts_dom,
                    AVG(duree_insertion_mois)::numeric AS avg_duree_mois
                FROM marts.vw_ent_insertion_dom
                WHERE {where_sql}
                GROUP BY type_insertion_code, type_insertion_label
                ORDER BY type_insertion_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_type_insertion = []
            for r in rows:
                t_code = r[0]
                t_label = r[1]
                nb_enreg = r[2]
                nb_ent = r[3]
                nb_ins = r[4]
                nb_dom = r[5] or 0
                nb_f_dom = r[6] or 0
                nb_j_dom = r[7] or 0
                nb_pvh_dom = r[8] or 0
                nb_vert_dom = r[9] or 0
                avg_duree_type = float(r[10]) if r[10] is not None else None

                part_f = round(100.0 * nb_f_dom / nb_dom, 2) if nb_dom > 0 else None
                part_j = round(100.0 * nb_j_dom / nb_dom, 2) if nb_dom > 0 else None
                part_pvh = (
                    round(100.0 * nb_pvh_dom / nb_dom, 2) if nb_dom > 0 else None
                )
                part_vert = (
                    round(100.0 * nb_vert_dom / nb_dom, 2) if nb_dom > 0 else None
                )

                by_type_insertion.append(
                    {
                        "type_insertion_code": t_code,
                        "type_insertion_label": t_label,
                        "nb_enregistrements": nb_enreg,
                        "nb_entreprises": nb_ent,
                        "nb_insertions": nb_ins,
                        "nb_ins_dom": nb_dom,
                        "nb_ins_fem_dom": nb_f_dom,
                        "nb_ins_jeunes_dom": nb_j_dom,
                        "nb_ins_pvh_dom": nb_pvh_dom,
                        "nb_ins_verts_dom": nb_vert_dom,
                        "avg_duree_mois": avg_duree_type,
                        "part_femmes_pct": part_f,
                        "part_jeunes_pct": part_j,
                        "part_pvh_pct": part_pvh,
                        "part_verts_pct": part_vert,
                    }
                )
            data["by_type_insertion"] = by_type_insertion

            # ---- 6) Par rÃ©gion ----
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    COUNT(DISTINCT id_ent) AS nb_entreprises,
                    COUNT(DISTINCT insertion_uuid) AS nb_insertions,
                    COALESCE(SUM(nb_ins_dom), 0) AS nb_ins_dom,
                    COALESCE(SUM(nb_ins_fem_dom), 0) AS nb_ins_fem_dom,
                    COALESCE(SUM(nb_ins_jeunes_dom), 0) AS nb_ins_jeunes_dom,
                    COALESCE(SUM(nb_ins_pvh_dom), 0) AS nb_ins_pvh_dom,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN (
                                    insertion_verte_dom::text IN ('true','t','1')
                                    OR insertion_verte_dom::text ILIKE 'oui'
                                )
                                THEN nb_ins_dom
                                ELSE 0
                            END
                        ),
                        0
                    ) AS nb_ins_verts_dom
                FROM marts.vw_ent_insertion_dom
                WHERE {where_sql}
                GROUP BY id_region, region_nom
                ORDER BY region_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            by_region = []
            for r in rows:
                reg_id = r[0]
                reg_nom = r[1]
                nb_ent_reg = r[2]
                nb_ins_reg = r[3]
                nb_dom = r[4] or 0
                nb_f_dom = r[5] or 0
                nb_j_dom = r[6] or 0
                nb_pvh_dom = r[7] or 0
                nb_vert_dom = r[8] or 0

                part_f = round(100.0 * nb_f_dom / nb_dom, 2) if nb_dom > 0 else None
                part_j = round(100.0 * nb_j_dom / nb_dom, 2) if nb_dom > 0 else None
                part_pvh = (
                    round(100.0 * nb_pvh_dom / nb_dom, 2) if nb_dom > 0 else None
                )
                part_vert = (
                    round(100.0 * nb_vert_dom / nb_dom, 2) if nb_dom > 0 else None
                )

                by_region.append(
                    {
                        "id_region": reg_id,
                        "region_nom": reg_nom,
                        "nb_entreprises": nb_ent_reg,
                        "nb_insertions": nb_ins_reg,
                        "nb_ins_dom": nb_dom,
                        "nb_ins_fem_dom": nb_f_dom,
                        "nb_ins_jeunes_dom": nb_j_dom,
                        "nb_ins_pvh_dom": nb_pvh_dom,
                        "nb_ins_verts_dom": nb_vert_dom,
                        "part_femmes_pct": part_f,
                        "part_jeunes_pct": part_j,
                        "part_pvh_pct": part_pvh,
                        "part_verts_pct": part_vert,
                    }
                )
            data["by_region"] = by_region

            # ---- 7) RÃ©gion x domaine ----
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    domaine_code,
                    domaine_label,
                    COUNT(DISTINCT id_ent) AS nb_entreprises,
                    COUNT(DISTINCT insertion_uuid) AS nb_insertions,
                    COALESCE(SUM(nb_ins_dom), 0) AS nb_ins_dom,
                    COALESCE(SUM(nb_ins_fem_dom), 0) AS nb_ins_fem_dom,
                    COALESCE(SUM(nb_ins_jeunes_dom), 0) AS nb_ins_jeunes_dom,
                    COALESCE(SUM(nb_ins_pvh_dom), 0) AS nb_ins_pvh_dom,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN (
                                    insertion_verte_dom::text IN ('true','t','1')
                                    OR insertion_verte_dom::text ILIKE 'oui'
                                )
                                THEN nb_ins_dom
                                ELSE 0
                            END
                        ),
                        0
                    ) AS nb_ins_verts_dom
                FROM marts.vw_ent_insertion_dom
                WHERE {where_sql}
                GROUP BY
                    id_region,
                    region_nom,
                    domaine_code,
                    domaine_label
                ORDER BY region_nom, domaine_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_region_and_domaine = []
            for r in rows:
                reg_id = r[0]
                reg_nom = r[1]
                dom_code = r[2]
                dom_label = r[3]
                nb_ent_reg_dom = r[4]
                nb_ins_reg_dom = r[5]
                nb_dom = r[6] or 0
                nb_f_dom = r[7] or 0
                nb_j_dom = r[8] or 0
                nb_pvh_dom = r[9] or 0
                nb_vert_dom = r[10] or 0

                part_f = round(100.0 * nb_f_dom / nb_dom, 2) if nb_dom > 0 else None
                part_j = round(100.0 * nb_j_dom / nb_dom, 2) if nb_dom > 0 else None
                part_pvh = (
                    round(100.0 * nb_pvh_dom / nb_dom, 2) if nb_dom > 0 else None
                )
                part_vert = (
                    round(100.0 * nb_vert_dom / nb_dom, 2) if nb_dom > 0 else None
                )

                by_region_and_domaine.append(
                    {
                        "id_region": reg_id,
                        "region_nom": reg_nom,
                        "domaine_code": dom_code,
                        "domaine_label": dom_label,
                        "nb_entreprises": nb_ent_reg_dom,
                        "nb_insertions": nb_ins_reg_dom,
                        "nb_ins_dom": nb_dom,
                        "nb_ins_fem_dom": nb_f_dom,
                        "nb_ins_jeunes_dom": nb_j_dom,
                        "nb_ins_pvh_dom": nb_pvh_dom,
                        "nb_ins_verts_dom": nb_vert_dom,
                        "part_femmes_pct": part_f,
                        "part_jeunes_pct": part_j,
                        "part_pvh_pct": part_pvh,
                        "part_verts_pct": part_vert,
                    }
                )
            data["by_region_and_domaine"] = by_region_and_domaine

            # ---- 8) Par prÃ©fecture ----
            cursor.execute(
                f"""
                SELECT
                    id_prefecture,
                    prefecture_nom,
                    COUNT(DISTINCT id_ent) AS nb_entreprises,
                    COUNT(DISTINCT insertion_uuid) AS nb_insertions,
                    COALESCE(SUM(nb_ins_dom), 0) AS nb_ins_dom
                FROM marts.vw_ent_insertion_dom
                WHERE {where_sql}
                GROUP BY id_prefecture, prefecture_nom
                ORDER BY prefecture_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_prefecture"] = [
                {
                    "id_prefecture": r[0],
                    "prefecture_nom": r[1],
                    "nb_entreprises": r[2],
                    "nb_insertions": r[3],
                    "nb_ins_dom": r[4] or 0,
                }
                for r in rows
            ]

            # ---- 9) Par commune ----
            cursor.execute(
                f"""
                SELECT
                    id_commune,
                    commune_nom,
                    COUNT(DISTINCT id_ent) AS nb_entreprises,
                    COUNT(DISTINCT insertion_uuid) AS nb_insertions,
                    COALESCE(SUM(nb_ins_dom), 0) AS nb_ins_dom
                FROM marts.vw_ent_insertion_dom
                WHERE {where_sql}
                GROUP BY id_commune, commune_nom
                ORDER BY commune_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_commune"] = [
                {
                    "id_commune": r[0],
                    "commune_nom": r[1],
                    "nb_entreprises": r[2],
                    "nb_insertions": r[3],
                    "nb_ins_dom": r[4] or 0,
                }
                for r in rows
            ]

            # ---- 10) Par annÃ©e / pÃ©riode de rÃ©fÃ©rence ----
            cursor.execute(
                f"""
                SELECT
                    annee_ref,
                    periode_ref,
                    periode_ref_label,
                    COUNT(DISTINCT id_ent) AS nb_entreprises,
                    COUNT(DISTINCT insertion_uuid) AS nb_insertions,
                    COALESCE(SUM(nb_ins_dom), 0) AS nb_ins_dom
                FROM marts.vw_ent_insertion_dom
                WHERE {where_sql}
                GROUP BY annee_ref, periode_ref, periode_ref_label
                ORDER BY annee_ref, periode_ref
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_annee_periode"] = [
                {
                    "annee_ref": r[0],
                    "periode_ref": r[1],
                    "periode_ref_label": r[2],
                    "nb_entreprises": r[3],
                    "nb_insertions": r[4],
                    "nb_ins_dom": r[5] or 0,
                }
                for r in rows
            ]

        return Response(data)



# -----------------------------------------------------------------------------
# Liste des suivi sortant
# -----------------------------------------------------------------------------

class FiereSuiviSortantListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Liste le suivi des sortants FIERE (marts.vw_fiere_suivi_sortant),
    filtrÃ© par projet actif via project_code (FIERE / AGRIECO).

    Filtres possibles en query string :
    - ?search=...                 (nom_sortant, intitule_formation,
                                   centre_formation, employeur_ou_activite,
                                   obs_suivi, obs_generales, localite,
                                   commune_nom, region_nom)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?filiere_principale=...     (code ref.filiere)
    - ?domaine_formation=...      (code ref.domaine_formation)
    - ?domaine_emploi=...         (code ref.domaine_emploi)
    - ?sexe=...                   (code ref.sexe_sortant)
    - ?niveau_etude=...           (code ref.niveau_etude)
    - ?periode_suivi=...          (code ref.periode_suivi)
    - ?insere=...                 (valeur brute, ex. 'oui'/'non' ou bool)
    - ?type_insertion=...         (code ref.type_insertion)
    - ?satisfaction_insertion=... (code ref.satisfaction_globale)
    - ?id_sortant=...
    - ?id_formation=...
    - ?has_geom=true|false        (prÃ©sence de gÃ©omÃ©trie)
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc  # FIERE en pratique, mais on garde gÃ©nÃ©rique

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        filiere_principale = request.query_params.get("filiere_principale")
        domaine_formation = request.query_params.get("domaine_formation")
        domaine_emploi = request.query_params.get("domaine_emploi")
        sexe = request.query_params.get("sexe")
        niveau_etude = request.query_params.get("niveau_etude")
        periode_suivi = request.query_params.get("periode_suivi")
        insere = request.query_params.get("insere")
        type_insertion = request.query_params.get("type_insertion")
        satisfaction_insertion = request.query_params.get("satisfaction_insertion")

        id_sortant = request.query_params.get("id_sortant")
        id_formation = request.query_params.get("id_formation")

        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques formation / sortant / insertion
        if filiere_principale:
            where_clauses.append("filiere_principale = %s")
            params.append(filiere_principale)

        if domaine_formation:
            where_clauses.append("domaine_formation = %s")
            params.append(domaine_formation)

        if domaine_emploi:
            where_clauses.append("domaine_emploi = %s")
            params.append(domaine_emploi)

        if sexe:
            where_clauses.append("sexe = %s")
            params.append(sexe)

        if niveau_etude:
            where_clauses.append("niveau_etude = %s")
            params.append(niveau_etude)

        if periode_suivi:
            where_clauses.append("periode_suivi = %s")
            params.append(periode_suivi)

        if insere:
            # on ne suppose pas le type, on filtre tel quel
            where_clauses.append("insere = %s")
            params.append(insere)

        if type_insertion:
            where_clauses.append("type_insertion = %s")
            params.append(type_insertion)

        if satisfaction_insertion:
            where_clauses.append("satisfaction_insertion = %s")
            params.append(satisfaction_insertion)

        if id_sortant:
            where_clauses.append("id_sortant = %s")
            params.append(id_sortant)

        if id_formation:
            where_clauses.append("id_formation = %s")
            params.append(id_formation)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "nom_sortant ILIKE %s OR "
                "intitule_formation ILIKE %s OR "
                "centre_formation ILIKE %s OR "
                "employeur_ou_activite ILIKE %s OR "
                "obs_suivi ILIKE %s OR "
                "obs_generales ILIKE %s OR "
                "localite ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 9)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        # -------- SQL --------
        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_fiere_suivi_sortant
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_fiere_suivi_sortant
                WHERE {where_sql}
                ORDER BY date_suivi DESC NULLS LAST, nom_sortant NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class FiereSuiviSortantAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    AgrÃ©gations sur le suivi des sortants FIERE (marts.vw_fiere_suivi_sortant),
    filtrÃ©es par projet actif (FIERE / AGRIECO) et par territoire.

    Filtres disponibles (alignÃ©s sur FiereSuiviSortantListView) :
    - ?search=...
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?filiere_principale=...
    - ?domaine_formation=...
    - ?domaine_emploi=...
    - ?sexe=...
    - ?niveau_etude=...
    - ?periode_suivi=...
    - ?insere=...
    - ?type_insertion=...
    - ?satisfaction_insertion=...
    - ?id_sortant=...
    - ?id_formation=...
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        filiere_principale = request.query_params.get("filiere_principale")
        domaine_formation = request.query_params.get("domaine_formation")
        domaine_emploi = request.query_params.get("domaine_emploi")
        sexe = request.query_params.get("sexe")
        niveau_etude = request.query_params.get("niveau_etude")
        periode_suivi = request.query_params.get("periode_suivi")
        insere = request.query_params.get("insere")
        type_insertion = request.query_params.get("type_insertion")
        satisfaction_insertion = request.query_params.get("satisfaction_insertion")

        id_sortant = request.query_params.get("id_sortant")
        id_formation = request.query_params.get("id_formation")

        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques formation / sortant / insertion
        if filiere_principale:
            where_clauses.append("filiere_principale = %s")
            params.append(filiere_principale)

        if domaine_formation:
            where_clauses.append("domaine_formation = %s")
            params.append(domaine_formation)

        if domaine_emploi:
            where_clauses.append("domaine_emploi = %s")
            params.append(domaine_emploi)

        if sexe:
            where_clauses.append("sexe = %s")
            params.append(sexe)

        if niveau_etude:
            where_clauses.append("niveau_etude = %s")
            params.append(niveau_etude)

        if periode_suivi:
            where_clauses.append("periode_suivi = %s")
            params.append(periode_suivi)

        if insere:
            where_clauses.append("insere = %s")
            params.append(insere)

        if type_insertion:
            where_clauses.append("type_insertion = %s")
            params.append(type_insertion)

        if satisfaction_insertion:
            where_clauses.append("satisfaction_insertion = %s")
            params.append(satisfaction_insertion)

        if id_sortant:
            where_clauses.append("id_sortant = %s")
            params.append(id_sortant)

        if id_formation:
            where_clauses.append("id_formation = %s")
            params.append(id_formation)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte (alignÃ©e sur la ListView)
        if search:
            where_clauses.append(
                "("
                "nom_sortant ILIKE %s OR "
                "intitule_formation ILIKE %s OR "
                "centre_formation ILIKE %s OR "
                "employeur_ou_activite ILIKE %s OR "
                "obs_suivi ILIKE %s OR "
                "obs_generales ILIKE %s OR "
                "localite ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 9)

        where_sql = " AND ".join(where_clauses)

        # Conditions "insÃ©rÃ©" / "PVH" rÃ©utilisables
        INSERE_COND = """
        (
            insere::text IN ('true','t','1')
            OR insere::text ILIKE 'oui'
        )
        """
        PVH_COND = """
        (
            pvh::text IN ('true','t','1')
            OR pvh::text ILIKE 'oui'
        )
        """

        data: dict = {}

        with connection.cursor() as cursor:
            # ---- 1) Global ----
            cursor.execute(
                f"""
                SELECT
                    COUNT(DISTINCT id_sortant) AS nb_sortants,
                    COUNT(*) AS nb_enregistrements_suivi,
                    COUNT(DISTINCT CASE WHEN {INSERE_COND} THEN id_sortant END) AS nb_sortants_inseres,
                    COUNT(DISTINCT CASE
                        WHEN NOT {INSERE_COND} OR insere IS NULL
                        THEN id_sortant
                        ELSE NULL
                    END) AS nb_sortants_non_inseres,
                    COUNT(DISTINCT CASE WHEN {PVH_COND} THEN id_sortant END) AS nb_sortants_pvh,
                    COUNT(DISTINCT CASE
                        WHEN sexe ILIKE 'F%%' OR sexe ILIKE 'femme%%' OR sexe_label ILIKE 'femme%%'
                        THEN id_sortant
                    END) AS nb_sortants_femmes,
                    AVG(age)::numeric AS age_moyen,
                    MIN(age) AS age_min,
                    MAX(age) AS age_max,
                    AVG(revenu_mensuel)::numeric AS revenu_moyen,
                    MIN(revenu_mensuel) AS revenu_min,
                    MAX(revenu_mensuel) AS revenu_max
                FROM marts.vw_fiere_suivi_sortant
                WHERE {where_sql}
                """,
                params,
            )
            row = cursor.fetchone()
            nb_sortants = row[0] or 0
            nb_enreg = row[1] or 0
            nb_sortants_inseres = row[2] or 0
            nb_sortants_non_inseres = row[3] or 0
            nb_sortants_pvh = row[4] or 0
            nb_sortants_femmes = row[5] or 0

            age_moyen = float(row[6]) if row[6] is not None else None
            age_min = row[7]
            age_max = row[8]
            revenu_moyen = float(row[9]) if row[9] is not None else None
            revenu_min = row[10]
            revenu_max = row[11]

            taux_insertion = (
                round(100.0 * nb_sortants_inseres / nb_sortants, 2)
                if nb_sortants > 0
                else None
            )

            data["global"] = {
                "nb_sortants": nb_sortants,
                "nb_enregistrements_suivi": nb_enreg,
                "nb_sortants_inseres": nb_sortants_inseres,
                "nb_sortants_non_inseres": nb_sortants_non_inseres,
                "nb_sortants_pvh": nb_sortants_pvh,
                "nb_sortants_femmes": nb_sortants_femmes,
                "nb_femmes": nb_sortants_femmes,
                "taux_insertion_pct": taux_insertion,
                "taux_achevement_pct": None,
                "age_moyen": age_moyen,
                "age_min": age_min,
                "age_max": age_max,
                "revenu_moyen": revenu_moyen,
                "revenu_min": revenu_min,
                "revenu_max": revenu_max,
            }

            # ---- 2) Par sexe ----
            cursor.execute(
                f"""
                SELECT
                    sexe,
                    sexe_label,
                    COUNT(DISTINCT id_sortant) AS nb_sortants,
                    COUNT(DISTINCT CASE WHEN {INSERE_COND} THEN id_sortant END) AS nb_sortants_inseres,
                    AVG(age)::numeric AS age_moyen,
                    AVG(revenu_mensuel)::numeric AS revenu_moyen
                FROM marts.vw_fiere_suivi_sortant
                WHERE {where_sql}
                GROUP BY sexe, sexe_label
                ORDER BY sexe_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_sexe = []
            for r in rows:
                sexe_code = r[0]
                sexe_label = r[1]
                nb_s = r[2] or 0
                nb_s_ins = r[3] or 0
                age_moy_s = float(r[4]) if r[4] is not None else None
                rev_moy_s = float(r[5]) if r[5] is not None else None
                taux_s = round(100.0 * nb_s_ins / nb_s, 2) if nb_s > 0 else None

                by_sexe.append(
                    {
                        "sexe": sexe_code,
                        "sexe_label": sexe_label,
                        "nb_sortants": nb_s,
                        "nb_sortants_inseres": nb_s_ins,
                        "taux_insertion_pct": taux_s,
                        "age_moyen": age_moy_s,
                        "revenu_moyen": rev_moy_s,
                    }
                )
            data["by_sexe"] = by_sexe

            # ---- 3) Par rÃ©gion ----
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    COUNT(DISTINCT id_sortant) AS nb_sortants,
                    COUNT(*) AS nb_enregistrements_suivi,
                    COUNT(DISTINCT CASE WHEN {INSERE_COND} THEN id_sortant END) AS nb_sortants_inseres,
                    COUNT(DISTINCT CASE WHEN {PVH_COND} THEN id_sortant END) AS nb_sortants_pvh,
                    AVG(revenu_mensuel)::numeric AS revenu_moyen
                FROM marts.vw_fiere_suivi_sortant
                WHERE {where_sql}
                GROUP BY id_region, region_nom
                ORDER BY region_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            by_region = []
            for r in rows:
                reg_id = r[0]
                reg_nom = r[1]
                nb_s_reg = r[2] or 0
                nb_enreg_reg = r[3] or 0
                nb_ins_reg = r[4] or 0
                nb_pvh_reg = r[5] or 0
                rev_moy_reg = float(r[6]) if r[6] is not None else None
                taux_reg = (
                    round(100.0 * nb_ins_reg / nb_s_reg, 2)
                    if nb_s_reg > 0
                    else None
                )

                by_region.append(
                    {
                        "id_region": reg_id,
                        "region_nom": reg_nom,
                        "nb_sortants": nb_s_reg,
                        "nb_enregistrements_suivi": nb_enreg_reg,
                        "nb_sortants_inseres": nb_ins_reg,
                        "nb_sortants_pvh": nb_pvh_reg,
                        "taux_insertion_pct": taux_reg,
                        "revenu_moyen": rev_moy_reg,
                    }
                )
            data["by_region"] = by_region

            # ---- 4) RÃ©gion x sexe ----
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    sexe,
                    sexe_label,
                    COUNT(DISTINCT id_sortant) AS nb_sortants,
                    COUNT(DISTINCT CASE WHEN {INSERE_COND} THEN id_sortant END) AS nb_sortants_inseres
                FROM marts.vw_fiere_suivi_sortant
                WHERE {where_sql}
                GROUP BY id_region, region_nom, sexe, sexe_label
                ORDER BY region_nom, sexe_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_region_and_sexe = []
            for r in rows:
                reg_id = r[0]
                reg_nom = r[1]
                sexe_code = r[2]
                sexe_label = r[3]
                nb_s = r[4] or 0
                nb_s_ins = r[5] or 0
                taux_rs = round(100.0 * nb_s_ins / nb_s, 2) if nb_s > 0 else None

                by_region_and_sexe.append(
                    {
                        "id_region": reg_id,
                        "region_nom": reg_nom,
                        "sexe": sexe_code,
                        "sexe_label": sexe_label,
                        "nb_sortants": nb_s,
                        "nb_sortants_inseres": nb_s_ins,
                        "taux_insertion_pct": taux_rs,
                    }
                )
            data["by_region_and_sexe"] = by_region_and_sexe

            # ---- 5) Par filiÃ¨re de formation ----
            cursor.execute(
                f"""
                SELECT
                    filiere_principale,
                    filiere_principale_label,
                    COUNT(DISTINCT id_sortant) AS nb_sortants,
                    COUNT(DISTINCT CASE WHEN {INSERE_COND} THEN id_sortant END) AS nb_sortants_inseres
                FROM marts.vw_fiere_suivi_sortant
                WHERE {where_sql}
                GROUP BY filiere_principale, filiere_principale_label
                ORDER BY filiere_principale_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_filiere = []
            for r in rows:
                fil_code = r[0]
                fil_label = r[1]
                nb_s = r[2] or 0
                nb_s_ins = r[3] or 0
                taux_f = round(100.0 * nb_s_ins / nb_s, 2) if nb_s > 0 else None

                by_filiere.append(
                    {
                        "filiere_principale": fil_code,
                        "filiere_principale_label": fil_label,
                        "nb_sortants": nb_s,
                        "nb_sortants_inseres": nb_s_ins,
                        "taux_insertion_pct": taux_f,
                    }
                )
            data["by_filiere_formation"] = by_filiere
            data["by_filiere"] = by_filiere  # alias dashboard

            # ---- 6) Par domaine de formation ----
            cursor.execute(
                f"""
                SELECT
                    domaine_formation,
                    domaine_formation_label,
                    COUNT(DISTINCT id_sortant) AS nb_sortants,
                    COUNT(DISTINCT CASE WHEN {INSERE_COND} THEN id_sortant END) AS nb_sortants_inseres
                FROM marts.vw_fiere_suivi_sortant
                WHERE {where_sql}
                GROUP BY domaine_formation, domaine_formation_label
                ORDER BY domaine_formation_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_dom_form = []
            for r in rows:
                dom_code = r[0]
                dom_label = r[1]
                nb_s = r[2] or 0
                nb_s_ins = r[3] or 0
                taux_d = round(100.0 * nb_s_ins / nb_s, 2) if nb_s > 0 else None

                by_dom_form.append(
                    {
                        "domaine_formation": dom_code,
                        "domaine_formation_label": dom_label,
                        "nb_sortants": nb_s,
                        "nb_sortants_inseres": nb_s_ins,
                        "taux_insertion_pct": taux_d,
                    }
                )
            data["by_domaine_formation"] = by_dom_form

            # ---- 7) Par domaine d'emploi ----
            cursor.execute(
                f"""
                SELECT
                    domaine_emploi,
                    domaine_emploi_label,
                    COUNT(DISTINCT CASE WHEN {INSERE_COND} THEN id_sortant END) AS nb_sortants_inseres,
                    AVG(revenu_mensuel)::numeric AS revenu_moyen
                FROM marts.vw_fiere_suivi_sortant
                WHERE {where_sql}
                GROUP BY domaine_emploi, domaine_emploi_label
                ORDER BY domaine_emploi_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_dom_emploi = []
            for r in rows:
                dom_code = r[0]
                dom_label = r[1]
                nb_ins = r[2] or 0
                rev_moy = float(r[3]) if r[3] is not None else None

                by_dom_emploi.append(
                    {
                        "domaine_emploi": dom_code,
                        "domaine_emploi_label": dom_label,
                        "nb_sortants_inseres": nb_ins,
                        "revenu_moyen": rev_moy,
                    }
                )
            data["by_domaine_emploi"] = by_dom_emploi

            # ---- 8) Par type d'insertion ----
            cursor.execute(
                f"""
                SELECT
                    type_insertion,
                    type_insertion_label,
                    COUNT(DISTINCT CASE WHEN {INSERE_COND} THEN id_sortant END) AS nb_sortants_inseres,
                    AVG(revenu_mensuel)::numeric AS revenu_moyen
                FROM marts.vw_fiere_suivi_sortant
                WHERE {where_sql}
                GROUP BY type_insertion, type_insertion_label
                ORDER BY type_insertion_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_type_insertion = []
            for r in rows:
                t_code = r[0]
                t_label = r[1]
                nb_ins = r[2] or 0
                rev_moy = float(r[3]) if r[3] is not None else None

                by_type_insertion.append(
                    {
                        "type_insertion": t_code,
                        "type_insertion_label": t_label,
                        "nb_sortants_inseres": nb_ins,
                        "revenu_moyen": rev_moy,
                    }
                )
            data["by_type_insertion"] = by_type_insertion

            # ---- 9) Par satisfaction ----
            cursor.execute(
                f"""
                SELECT
                    satisfaction_insertion,
                    satisfaction_insertion_label,
                    COUNT(*) AS nb_enregistrements_suivi,
                    COUNT(DISTINCT id_sortant) AS nb_sortants,
                    COUNT(DISTINCT CASE WHEN {INSERE_COND} THEN id_sortant END) AS nb_sortants_inseres
                FROM marts.vw_fiere_suivi_sortant
                WHERE {where_sql}
                GROUP BY satisfaction_insertion, satisfaction_insertion_label
                ORDER BY satisfaction_insertion_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_satisfaction = []
            for r in rows:
                s_code = r[0]
                s_label = r[1]
                nb_enr_s = r[2] or 0
                nb_s = r[3] or 0
                nb_ins_s = r[4] or 0
                taux_s = round(100.0 * nb_ins_s / nb_s, 2) if nb_s > 0 else None
                part_enreg = (
                    round(100.0 * nb_enr_s / nb_enreg, 2)
                    if nb_enreg > 0
                    else None
                )

                by_satisfaction.append(
                    {
                        "satisfaction_insertion": s_code,
                        "satisfaction_insertion_label": s_label,
                        "nb_enregistrements_suivi": nb_enr_s,
                        "nb_sortants": nb_s,
                        "nb_sortants_inseres": nb_ins_s,
                        "taux_insertion_pct": taux_s,
                        "part_enregistrements_pct": part_enreg,
                    }
                )
            data["by_satisfaction"] = by_satisfaction

            # ---- 10) Par pÃ©riode de suivi ----
            cursor.execute(
                f"""
                SELECT
                    periode_suivi,
                    periode_suivi_label,
                    COUNT(*) AS nb_enregistrements_suivi,
                    COUNT(DISTINCT id_sortant) AS nb_sortants,
                    COUNT(DISTINCT CASE WHEN {INSERE_COND} THEN id_sortant END) AS nb_sortants_inseres
                FROM marts.vw_fiere_suivi_sortant
                WHERE {where_sql}
                GROUP BY periode_suivi, periode_suivi_label
                ORDER BY periode_suivi_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_periode = []
            for r in rows:
                p_code = r[0]
                p_label = r[1]
                nb_enr_p = r[2] or 0
                nb_s = r[3] or 0
                nb_ins_p = r[4] or 0
                taux_p = round(100.0 * nb_ins_p / nb_s, 2) if nb_s > 0 else None

                by_periode.append(
                    {
                        "periode_suivi": p_code,
                        "periode_suivi_label": p_label,
                        "nb_enregistrements_suivi": nb_enr_p,
                        "nb_sortants": nb_s,
                        "nb_sortants_inseres": nb_ins_p,
                        "taux_insertion_pct": taux_p,
                    }
                )
            data["by_periode_suivi"] = by_periode

        return Response(data)


# -----------------------------------------------------------------------------
# Liste des Formation eco
# -----------------------------------------------------------------------------

class FormationEcoCatListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Liste les formations Ã©conomiques avec dÃ©tail par catÃ©gorie de participants
    (marts.vw_formation_eco_cat), filtrÃ©es par projet actif (FIERE / AGRIECO).

    Filtres possibles en query string :
    - ?search=...                   (intitule_formation, organisme_formateur,
                                     org_beneficiaire, raison_sociale, localite,
                                     commune_nom, region_nom)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?filiere_principale=...       (code ref.secteur_eco)
    - ?domaine_formation=...        (code ref.domaine_formation)
    - ?type_formation=...           (code ref.type_formation)
    - ?modalite_formation=...       (code ref.modalite_formation)
    - ?categorie_code=...           (code ref.categorie_participant)
    - ?formation_liee_ent=...       (valeur brute, ex. true/false ou 'oui'/'non')
    - ?id_ent=...                   (formation liÃ©e Ã  une entreprise)
    - ?date_debut_from=YYYY-MM-DD
    - ?date_debut_to=YYYY-MM-DD
    - ?has_geom=true|false          (prÃ©sence de gÃ©omÃ©trie)
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc  # FIERE / AGRIECO

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        filiere_principale = request.query_params.get("filiere_principale")
        domaine_formation = request.query_params.get("domaine_formation")
        type_formation = request.query_params.get("type_formation")
        modalite_formation = request.query_params.get("modalite_formation")
        categorie_code = request.query_params.get("categorie_code")

        formation_liee_ent = request.query_params.get("formation_liee_ent")
        id_ent = request.query_params.get("id_ent")

        date_debut_from = request.query_params.get("date_debut_from")
        date_debut_to = request.query_params.get("date_debut_to")

        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques formation
        if filiere_principale:
            where_clauses.append("filiere_principale = %s")
            params.append(filiere_principale)

        if domaine_formation:
            where_clauses.append("domaine_formation = %s")
            params.append(domaine_formation)

        if type_formation:
            where_clauses.append("type_formation = %s")
            params.append(type_formation)

        if modalite_formation:
            where_clauses.append("modalite_formation = %s")
            params.append(modalite_formation)

        if categorie_code:
            where_clauses.append("categorie_code = %s")
            params.append(categorie_code)

        if formation_liee_ent:
            # on filtre tel quel (bool ou texte selon ton modÃ¨le)
            where_clauses.append("formation_liee_ent = %s")
            params.append(formation_liee_ent)

        if id_ent:
            where_clauses.append("id_ent = %s")
            params.append(id_ent)

        # Dates (on considÃ¨re date_debut comme rÃ©fÃ©rence)
        if date_debut_from:
            where_clauses.append("date_debut >= %s")
            params.append(date_debut_from)

        if date_debut_to:
            where_clauses.append("date_debut <= %s")
            params.append(date_debut_to)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "intitule_formation ILIKE %s OR "
                "organisme_formateur ILIKE %s OR "
                "org_beneficiaire ILIKE %s OR "
                "raison_sociale ILIKE %s OR "
                "localite ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 7)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        # -------- SQL --------
        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_formation_eco_cat
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_formation_eco_cat
                WHERE {where_sql}
                ORDER BY date_debut DESC NULLS LAST,
                         intitule_formation NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class FormationEcoCatAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    AgrÃ©gations sur les formations Ã©conomiques (marts.vw_formation_eco_cat),
    filtrÃ©es par projet actif (FIERE / AGRIECO) + territoire et caractÃ©ristiques.

    MÃªme jeux de filtres que FormationEcoCatListView :
    - ?search=...
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?filiere_principale=...
    - ?domaine_formation=...
    - ?type_formation=...
    - ?modalite_formation=...
    - ?categorie_code=...
    - ?formation_liee_ent=...
    - ?id_ent=...
    - ?date_debut_from=YYYY-MM-DD
    - ?date_debut_to=YYYY-MM-DD
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc

        # -------- Filtres (mÃªme que ListView) --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        filiere_principale = request.query_params.get("filiere_principale")
        domaine_formation = request.query_params.get("domaine_formation")
        type_formation = request.query_params.get("type_formation")
        modalite_formation = request.query_params.get("modalite_formation")
        categorie_code = request.query_params.get("categorie_code")

        formation_liee_ent = request.query_params.get("formation_liee_ent")
        id_ent = request.query_params.get("id_ent")

        date_debut_from = request.query_params.get("date_debut_from")
        date_debut_to = request.query_params.get("date_debut_to")

        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques formation
        if filiere_principale:
            where_clauses.append("filiere_principale = %s")
            params.append(filiere_principale)

        if domaine_formation:
            where_clauses.append("domaine_formation = %s")
            params.append(domaine_formation)

        if type_formation:
            where_clauses.append("type_formation = %s")
            params.append(type_formation)

        if modalite_formation:
            where_clauses.append("modalite_formation = %s")
            params.append(modalite_formation)

        if categorie_code:
            where_clauses.append("categorie_code = %s")
            params.append(categorie_code)

        if formation_liee_ent:
            where_clauses.append("formation_liee_ent = %s")
            params.append(formation_liee_ent)

        if id_ent:
            where_clauses.append("id_ent = %s")
            params.append(id_ent)

        # Dates
        if date_debut_from:
            where_clauses.append("date_debut >= %s")
            params.append(date_debut_from)

        if date_debut_to:
            where_clauses.append("date_debut <= %s")
            params.append(date_debut_to)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "intitule_formation ILIKE %s OR "
                "organisme_formateur ILIKE %s OR "
                "org_beneficiaire ILIKE %s OR "
                "raison_sociale ILIKE %s OR "
                "localite ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 7)

        where_sql = " AND ".join(where_clauses)

        data: dict = {}

        with connection.cursor() as cursor:
            # ==== 1) GLOBAL : formation-level (CTE base) + catÃ©gorie-level ====
            cursor.execute(
                f"""
                WITH base AS (
                    SELECT DISTINCT
                        formation_uuid,
                        id_formation,
                        project_code,
                        id_region,
                        region_nom,
                        id_prefecture,
                        prefecture_nom,
                        id_commune,
                        commune_nom,
                        filiere_principale,
                        filiere_principale_label,
                        domaine_formation,
                        domaine_formation_label,
                        type_formation,
                        type_formation_label,
                        modalite_formation,
                        modalite_formation_label,
                        date_debut,
                        date_fin,
                        duree_jours,
                        participants_total,
                        participants_femmes,
                        participants_jeunes,
                        participants_pvh,
                        participants_inscrits,
                        participants_acheve
                    FROM marts.vw_formation_eco_cat
                    WHERE {where_sql}
                )
                SELECT
                    COUNT(DISTINCT id_formation) AS nb_formations,
                    SUM(participants_total)      AS participants_total,
                    SUM(participants_femmes)     AS participants_femmes,
                    SUM(participants_jeunes)     AS participants_jeunes,
                    SUM(participants_pvh)        AS participants_pvh,
                    SUM(participants_inscrits)   AS participants_inscrits,
                    SUM(participants_acheve)     AS participants_acheve,
                    AVG(duree_jours)::numeric    AS duree_moyenne_jours
                FROM base
                """,
                params,
            )
            row = cursor.fetchone()
            nb_formations = row[0] or 0
            participants_total = row[1] or 0
            participants_femmes = row[2] or 0
            participants_jeunes = row[3] or 0
            participants_pvh = row[4] or 0
            participants_inscrits = row[5] or 0
            participants_acheve = row[6] or 0
            duree_moyenne_jours = float(row[7]) if row[7] is not None else None

            # CatÃ©gorie-level pour avoir un total robuste cÃ´tÃ© catÃ©gories
            cursor.execute(
                f"""
                SELECT
                    COALESCE(SUM(nb_part_cat), 0)        AS nb_part_cat_total,
                    COALESCE(SUM(nb_part_fem_cat), 0)    AS nb_part_fem_cat_total,
                    COALESCE(SUM(nb_part_jeunes_cat), 0) AS nb_part_jeunes_cat_total
                FROM marts.vw_formation_eco_cat
                WHERE {where_sql}
                """,
                params,
            )
            cat_row = cursor.fetchone()
            nb_part_cat_total = cat_row[0] or 0
            nb_part_fem_cat_total = cat_row[1] or 0
            nb_part_jeunes_cat_total = cat_row[2] or 0

            # taux / parts (on privilÃ©gie le total catÃ©gorie pour les ratios)
            part_femmes_pct = (
                round(100.0 * nb_part_fem_cat_total / nb_part_cat_total, 2)
                if nb_part_cat_total > 0
                else None
            )
            part_jeunes_pct = (
                round(100.0 * nb_part_jeunes_cat_total / nb_part_cat_total, 2)
                if nb_part_cat_total > 0
                else None
            )
            taux_achevement_global = (
                round(100.0 * participants_acheve / participants_inscrits, 2)
                if participants_inscrits and participants_inscrits > 0
                else None
            )
            part_pvh_pct = (
                round(100.0 * participants_pvh / participants_total, 2)
                if participants_total > 0 and participants_pvh is not None
                else None
            )

            data["global"] = {
                "nb_formations": nb_formations,
                "participants_total": participants_total,
                "nb_participants": participants_total,       # alias front
                "total_participants": participants_total,     # alias front
                "participants_femmes": participants_femmes,
                "participants_jeunes": participants_jeunes,
                "participants_pvh": participants_pvh,
                "participants_inscrits": participants_inscrits,
                "participants_acheve": participants_acheve,
                "duree_moyenne_jours": duree_moyenne_jours,
                "nb_part_cat_total": nb_part_cat_total,
                "nb_part_fem_cat_total": nb_part_fem_cat_total,
                "nb_part_jeunes_cat_total": nb_part_jeunes_cat_total,
                "part_femmes_pct": part_femmes_pct,
                "part_jeunes_pct": part_jeunes_pct,
                "part_pvh_pct": part_pvh_pct,
                "taux_achevement_global_pct": taux_achevement_global,
            }

            # ==== 2) Par rÃ©gion (formation-level) ====
            cursor.execute(
                f"""
                WITH base AS (
                    SELECT DISTINCT
                        formation_uuid,
                        id_formation,
                        id_region,
                        region_nom,
                        participants_total,
                        participants_femmes,
                        participants_jeunes,
                        participants_pvh
                    FROM marts.vw_formation_eco_cat
                    WHERE {where_sql}
                )
                SELECT
                    id_region,
                    region_nom,
                    COUNT(DISTINCT id_formation) AS nb_formations,
                    SUM(participants_total)      AS participants_total,
                    SUM(participants_femmes)     AS participants_femmes,
                    SUM(participants_jeunes)     AS participants_jeunes,
                    SUM(participants_pvh)        AS participants_pvh
                FROM base
                GROUP BY id_region, region_nom
                ORDER BY region_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            by_region = []
            for r in rows:
                rid = r[0]
                rnom = r[1]
                nf = r[2] or 0
                pt = r[3] or 0
                pf = r[4] or 0
                pj = r[5] or 0
                pvh = r[6] or 0

                part_f = round(100.0 * pf / pt, 2) if pt > 0 else None
                part_j = round(100.0 * pj / pt, 2) if pt > 0 else None
                part_pvh_r = round(100.0 * pvh / pt, 2) if pt > 0 else None

                by_region.append(
                    {
                        "id_region": rid,
                        "region_nom": rnom,
                        "nb_formations": nf,
                        "participants_total": pt,
                        "participants_femmes": pf,
                        "participants_jeunes": pj,
                        "participants_pvh": pvh,
                        "part_femmes_pct": part_f,
                        "part_jeunes_pct": part_j,
                        "part_pvh_pct": part_pvh_r,
                    }
                )
            data["by_region"] = by_region

            # ==== 3) Par filiÃ¨re principale (secteur Ã©co) ====
            cursor.execute(
                f"""
                WITH base AS (
                    SELECT DISTINCT
                        formation_uuid,
                        id_formation,
                        filiere_principale,
                        filiere_principale_label,
                        participants_total,
                        participants_femmes,
                        participants_jeunes
                    FROM marts.vw_formation_eco_cat
                    WHERE {where_sql}
                )
                SELECT
                    filiere_principale,
                    filiere_principale_label,
                    COUNT(DISTINCT id_formation) AS nb_formations,
                    SUM(participants_total)      AS participants_total,
                    SUM(participants_femmes)     AS participants_femmes,
                    SUM(participants_jeunes)     AS participants_jeunes
                FROM base
                GROUP BY filiere_principale, filiere_principale_label
                ORDER BY filiere_principale_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_filiere = []
            for r in rows:
                code = r[0]
                label = r[1]
                nf = r[2] or 0
                pt = r[3] or 0
                pf = r[4] or 0
                pj = r[5] or 0

                part_f = round(100.0 * pf / pt, 2) if pt > 0 else None
                part_j = round(100.0 * pj / pt, 2) if pt > 0 else None

                by_filiere.append(
                    {
                        "filiere_principale": code,
                        "filiere_principale_label": label,
                        "nb_formations": nf,
                        "participants_total": pt,
                        "participants_femmes": pf,
                        "participants_jeunes": pj,
                        "part_femmes_pct": part_f,
                        "part_jeunes_pct": part_j,
                    }
                )
            data["by_filiere_principale"] = by_filiere

            # ==== 4) Par domaine de formation ====
            cursor.execute(
                f"""
                WITH base AS (
                    SELECT DISTINCT
                        formation_uuid,
                        id_formation,
                        domaine_formation,
                        domaine_formation_label,
                        participants_total,
                        participants_femmes,
                        participants_jeunes
                    FROM marts.vw_formation_eco_cat
                    WHERE {where_sql}
                )
                SELECT
                    domaine_formation,
                    domaine_formation_label,
                    COUNT(DISTINCT id_formation) AS nb_formations,
                    SUM(participants_total)      AS participants_total,
                    SUM(participants_femmes)     AS participants_femmes,
                    SUM(participants_jeunes)     AS participants_jeunes
                FROM base
                GROUP BY domaine_formation, domaine_formation_label
                ORDER BY domaine_formation_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_domaine = []
            for r in rows:
                code = r[0]
                label = r[1]
                nf = r[2] or 0
                pt = r[3] or 0
                pf = r[4] or 0
                pj = r[5] or 0

                part_f = round(100.0 * pf / pt, 2) if pt > 0 else None
                part_j = round(100.0 * pj / pt, 2) if pt > 0 else None

                by_domaine.append(
                    {
                        "domaine_formation": code,
                        "domaine_formation_label": label,
                        "nb_formations": nf,
                        "participants_total": pt,
                        "participants_femmes": pf,
                        "participants_jeunes": pj,
                        "part_femmes_pct": part_f,
                        "part_jeunes_pct": part_j,
                    }
                )
            data["by_domaine_formation"] = by_domaine

            # ==== 5) Par type de formation ====
            cursor.execute(
                f"""
                WITH base AS (
                    SELECT DISTINCT
                        formation_uuid,
                        id_formation,
                        type_formation,
                        type_formation_label,
                        participants_total
                    FROM marts.vw_formation_eco_cat
                    WHERE {where_sql}
                )
                SELECT
                    type_formation,
                    type_formation_label,
                    COUNT(DISTINCT id_formation) AS nb_formations,
                    SUM(participants_total)      AS participants_total
                FROM base
                GROUP BY type_formation, type_formation_label
                ORDER BY type_formation_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_type = []
            for r in rows:
                code = r[0]
                label = r[1]
                nf = r[2] or 0
                pt = r[3] or 0

                part = (
                    round(100.0 * pt / participants_total, 2)
                    if participants_total > 0
                    else None
                )

                by_type.append(
                    {
                        "type_formation": code,
                        "type_formation_label": label,
                        "nb_formations": nf,
                        "participants_total": pt,
                        "part_participants_pct": part,
                    }
                )
            data["by_type_formation"] = by_type

            # ==== 6) Par modalitÃ© de formation ====
            cursor.execute(
                f"""
                WITH base AS (
                    SELECT DISTINCT
                        formation_uuid,
                        id_formation,
                        modalite_formation,
                        modalite_formation_label,
                        participants_total
                    FROM marts.vw_formation_eco_cat
                    WHERE {where_sql}
                )
                SELECT
                    modalite_formation,
                    modalite_formation_label,
                    COUNT(DISTINCT id_formation) AS nb_formations,
                    SUM(participants_total)      AS participants_total
                FROM base
                GROUP BY modalite_formation, modalite_formation_label
                ORDER BY modalite_formation_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_modalite = []
            for r in rows:
                code = r[0]
                label = r[1]
                nf = r[2] or 0
                pt = r[3] or 0

                part = (
                    round(100.0 * pt / participants_total, 2)
                    if participants_total > 0
                    else None
                )

                by_modalite.append(
                    {
                        "modalite_formation": code,
                        "modalite_formation_label": label,
                        "nb_formations": nf,
                        "participants_total": pt,
                        "part_participants_pct": part,
                    }
                )
            data["by_modalite_formation"] = by_modalite

            # ==== 7) Par catÃ©gorie de participant (cat-level) ====
            cursor.execute(
                f"""
                SELECT
                    categorie_code,
                    categorie_label,
                    SUM(nb_part_cat)        AS nb_part_cat,
                    SUM(nb_part_fem_cat)    AS nb_part_fem_cat,
                    SUM(nb_part_jeunes_cat) AS nb_part_jeunes_cat
                FROM marts.vw_formation_eco_cat
                WHERE {where_sql}
                GROUP BY categorie_code, categorie_label
                ORDER BY categorie_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_categorie = []
            for r in rows:
                code = r[0]
                label = r[1]
                n_cat = r[2] or 0
                n_f = r[3] or 0
                n_j = r[4] or 0

                part_cat = (
                    round(100.0 * n_cat / nb_part_cat_total, 2)
                    if nb_part_cat_total > 0
                    else None
                )
                part_f = (
                    round(100.0 * n_f / n_cat, 2)
                    if n_cat > 0
                    else None
                )
                part_j = (
                    round(100.0 * n_j / n_cat, 2)
                    if n_cat > 0
                    else None
                )

                by_categorie.append(
                    {
                        "categorie_code": code,
                        "categorie_label": label,
                        "nb_part_cat": n_cat,
                        "nb_part_fem_cat": n_f,
                        "nb_part_jeunes_cat": n_j,
                        "part_participants_categorie_pct": part_cat,
                        "part_femmes_dans_categorie_pct": part_f,
                        "part_jeunes_dans_categorie_pct": part_j,
                    }
                )
            data["by_categorie_participant"] = by_categorie
            data["by_categorie"] = by_categorie  # alias dashboard

            # ==== 8) Par annÃ©e de dÃ©but ====
            cursor.execute(
                f"""
                WITH base AS (
                    SELECT DISTINCT
                        formation_uuid,
                        id_formation,
                        date_debut,
                        participants_total
                    FROM marts.vw_formation_eco_cat
                    WHERE {where_sql}
                )
                SELECT
                    EXTRACT(YEAR FROM date_debut)::int AS annee,
                    COUNT(DISTINCT id_formation)      AS nb_formations,
                    SUM(participants_total)           AS participants_total
                FROM base
                GROUP BY annee
                ORDER BY annee
                """,
                params,
            )
            rows = cursor.fetchall()
            by_annee = []
            for r in rows:
                annee = r[0]
                nf = r[1] or 0
                pt = r[2] or 0
                part = (
                    round(100.0 * pt / participants_total, 2)
                    if participants_total > 0
                    else None
                )

                by_annee.append(
                    {
                        "annee": annee,
                        "nb_formations": nf,
                        "participants_total": pt,
                        "part_participants_pct": part,
                    }
                )
            data["by_annee_debut"] = by_annee

        return Response(data)


# -----------------------------------------------------------------------------
# Liste des intrant distribution
# -----------------------------------------------------------------------------

class IntrantDistributionListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Liste les distributions d'intrants (marts.vw_intrant_distribution),
    filtrÃ©es par projet actif via project_code (FIERE / AGRIECO).

    Filtres possibles en query string :
    - ?search=...                 (localite, filiere_label, intrant_autres,
                                   obs_intrant, source_intrant,
                                   commune_nom, region_nom)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?filiere=...               (code ref.filiere)
    - ?type_intrant=...          (valeur brute du champ type_intrant)
    - ?campagne_yyyy=2023
    - ?source_intrant=...
    - ?intrant_conforme=...      (valeur brute du champ, ex. 'oui'/'non' ou bool)
    - ?has_geom=true|false       (prÃ©sence de gÃ©omÃ©trie)
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc  # FIERE / AGRIECO

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        filiere = request.query_params.get("filiere")
        type_intrant = request.query_params.get("type_intrant")
        campagne_yyyy = request.query_params.get("campagne_yyyy")
        source_intrant = request.query_params.get("source_intrant")
        intrant_conforme = request.query_params.get("intrant_conforme")

        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques de la distribution
        if filiere:
            where_clauses.append("filiere = %s")
            params.append(filiere)

        if type_intrant:
            where_clauses.append("type_intrant = %s")
            params.append(type_intrant)

        if campagne_yyyy:
            where_clauses.append("campagne_yyyy = %s")
            params.append(campagne_yyyy)

        if source_intrant:
            where_clauses.append("source_intrant = %s")
            params.append(source_intrant)

        if intrant_conforme:
            # on filtre tel quel (bool ou texte selon ton modÃ¨le)
            where_clauses.append("intrant_conforme = %s")
            params.append(intrant_conforme)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "localite ILIKE %s OR "
                "filiere_label ILIKE %s OR "
                "intrant_autres ILIKE %s OR "
                "obs_intrant ILIKE %s OR "
                "source_intrant ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 7)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        # -------- SQL --------
        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_intrant_distribution
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_intrant_distribution
                WHERE {where_sql}
                ORDER BY campagne_yyyy DESC NULLS LAST,
                         filiere_label NULLS LAST,
                         localite NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class IntrantDistributionAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    AgrÃ©gations sur les distributions d'intrants (marts.vw_intrant_distribution),
    filtrÃ©es par projet actif via project_code (FIERE / AGRIECO) + territoire et caractÃ©ristiques.

    Filtres possibles en query string (mÃªmes que IntrantDistributionListView) :
    - ?search=...
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?filiere=...
    - ?type_intrant=...
    - ?campagne_yyyy=2023
    - ?source_intrant=...
    - ?intrant_conforme=...
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc  # FIERE / AGRIECO

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        filiere = request.query_params.get("filiere")
        type_intrant = request.query_params.get("type_intrant")
        campagne_yyyy = request.query_params.get("campagne_yyyy")
        source_intrant = request.query_params.get("source_intrant")
        intrant_conforme = request.query_params.get("intrant_conforme")

        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques de la distribution
        if filiere:
            where_clauses.append("filiere = %s")
            params.append(filiere)

        if type_intrant:
            where_clauses.append("type_intrant = %s")
            params.append(type_intrant)

        if campagne_yyyy:
            where_clauses.append("campagne_yyyy = %s")
            params.append(campagne_yyyy)

        if source_intrant:
            where_clauses.append("source_intrant = %s")
            params.append(source_intrant)

        if intrant_conforme:
            where_clauses.append("intrant_conforme = %s")
            params.append(intrant_conforme)

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "localite ILIKE %s OR "
                "filiere_label ILIKE %s OR "
                "intrant_autres ILIKE %s OR "
                "obs_intrant ILIKE %s OR "
                "source_intrant ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 7)

        where_sql = " AND ".join(where_clauses)

        data: dict = {}

        with connection.cursor() as cursor:
            # ==== 1) GLOBAL ====
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS nb_distributions,
                    COALESCE(SUM(menages_beneficiaires), 0) AS menages_beneficiaires_total
                FROM marts.vw_intrant_distribution
                WHERE {where_sql}
                """,
                params,
            )
            row = cursor.fetchone()
            nb_distributions = row[0] or 0
            menages_total = row[1] or 0

            # QuantitÃ©s globales par unitÃ©
            cursor.execute(
                f"""
                SELECT
                    unite_intrant,
                    COALESCE(SUM(quantite), 0) AS quantite_totale
                FROM marts.vw_intrant_distribution
                WHERE {where_sql}
                GROUP BY unite_intrant
                ORDER BY unite_intrant
                """,
                params,
            )
            rows = cursor.fetchall()
            quantites_par_unite = []
            quantite_totale_brute = 0.0
            for r in rows:
                unite = r[0]
                qte = float(r[1] or 0)
                quantite_totale_brute += qte
                quantites_par_unite.append(
                    {
                        "unite_intrant": unite,
                        "quantite_totale": qte,
                    }
                )

            data["global"] = {
                "nb_distributions": nb_distributions,
                "menages_beneficiaires_total": menages_total,
                # Attention : somme brute toutes unitÃ©s confondues (indicatif)
                "quantite_totale_brute": quantite_totale_brute,
                "quantites_par_unite": quantites_par_unite,
            }

            # ==== 2) Par conformitÃ© ====
            cursor.execute(
                f"""
                SELECT
                    intrant_conforme,
                    COUNT(*) AS nb_distributions,
                    COALESCE(SUM(menages_beneficiaires), 0) AS menages_beneficiaires_total
                FROM marts.vw_intrant_distribution
                WHERE {where_sql}
                GROUP BY intrant_conforme
                ORDER BY intrant_conforme
                """,
                params,
            )
            rows = cursor.fetchall()
            by_conformite = []
            for r in rows:
                conf = r[0]
                nb = r[1] or 0
                menages = r[2] or 0
                part_distributions = (
                    round(100.0 * nb / nb_distributions, 2)
                    if nb_distributions > 0
                    else None
                )
                part_menages = (
                    round(100.0 * menages / menages_total, 2)
                    if menages_total > 0
                    else None
                )
                by_conformite.append(
                    {
                        "intrant_conforme": conf,
                        "nb_distributions": nb,
                        "menages_beneficiaires_total": menages,
                        "part_distributions_pct": part_distributions,
                        "part_menages_beneficiaires_pct": part_menages,
                    }
                )
            data["by_conformite"] = by_conformite

            # ==== 3) Par rÃ©gion ====
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    COUNT(*) AS nb_distributions,
                    COALESCE(SUM(menages_beneficiaires), 0) AS menages_beneficiaires_total,
                    COALESCE(SUM(quantite), 0) AS quantite_totale_brute
                FROM marts.vw_intrant_distribution
                WHERE {where_sql}
                GROUP BY id_region, region_nom
                ORDER BY region_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            by_region = []
            for r in rows:
                rid = r[0]
                rnom = r[1]
                nb = r[2] or 0
                menages = r[3] or 0
                qte = float(r[4] or 0)

                part_distributions = (
                    round(100.0 * nb / nb_distributions, 2)
                    if nb_distributions > 0
                    else None
                )
                part_menages = (
                    round(100.0 * menages / menages_total, 2)
                    if menages_total > 0
                    else None
                )

                by_region.append(
                    {
                        "id_region": rid,
                        "region_nom": rnom,
                        "nb_distributions": nb,
                        "menages_beneficiaires_total": menages,
                        "quantite_totale_brute": qte,
                        "part_distributions_pct": part_distributions,
                        "part_menages_beneficiaires_pct": part_menages,
                    }
                )
            data["by_region"] = by_region

            # ==== 4) Par filiÃ¨re ====
            cursor.execute(
                f"""
                SELECT
                    filiere,
                    filiere_label,
                    COUNT(*) AS nb_distributions,
                    COALESCE(SUM(menages_beneficiaires), 0) AS menages_beneficiaires_total,
                    COALESCE(SUM(quantite), 0) AS quantite_totale_brute
                FROM marts.vw_intrant_distribution
                WHERE {where_sql}
                GROUP BY filiere, filiere_label
                ORDER BY filiere_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_filiere = []
            for r in rows:
                code = r[0]
                label = r[1]
                nb = r[2] or 0
                menages = r[3] or 0
                qte = float(r[4] or 0)

                part_distributions = (
                    round(100.0 * nb / nb_distributions, 2)
                    if nb_distributions > 0
                    else None
                )
                part_menages = (
                    round(100.0 * menages / menages_total, 2)
                    if menages_total > 0
                    else None
                )

                by_filiere.append(
                    {
                        "filiere": code,
                        "filiere_label": label,
                        "nb_distributions": nb,
                        "menages_beneficiaires_total": menages,
                        "quantite_totale_brute": qte,
                        "part_distributions_pct": part_distributions,
                        "part_menages_beneficiaires_pct": part_menages,
                    }
                )
            data["by_filiere"] = by_filiere

            # ==== 5) Par type d'intrant ====
            cursor.execute(
                f"""
                SELECT
                    type_intrant,
                    COUNT(*) AS nb_distributions,
                    COALESCE(SUM(menages_beneficiaires), 0) AS menages_beneficiaires_total,
                    COALESCE(SUM(quantite), 0) AS quantite_totale_brute
                FROM marts.vw_intrant_distribution
                WHERE {where_sql}
                GROUP BY type_intrant
                ORDER BY type_intrant
                """,
                params,
            )
            rows = cursor.fetchall()
            by_type_intrant = []
            for r in rows:
                type_i = r[0]
                nb = r[1] or 0
                menages = r[2] or 0
                qte = float(r[3] or 0)

                part_distributions = (
                    round(100.0 * nb / nb_distributions, 2)
                    if nb_distributions > 0
                    else None
                )
                part_menages = (
                    round(100.0 * menages / menages_total, 2)
                    if menages_total > 0
                    else None
                )

                by_type_intrant.append(
                    {
                        "type_intrant": type_i,
                        "type_intrant_label": type_i,           # alias front (pas de label dispo)
                        "nb_distributions": nb,
                        "menages_beneficiaires_total": menages,
                        "quantite_totale_brute": qte,
                        "quantite_totale": qte,                 # alias front
                        "part_distributions_pct": part_distributions,
                        "part_menages_beneficiaires_pct": part_menages,
                    }
                )
            data["by_type_intrant"] = by_type_intrant

            # ==== 6) Par campagne ====
            cursor.execute(
                f"""
                SELECT
                    campagne_yyyy,
                    COUNT(*) AS nb_distributions,
                    COALESCE(SUM(menages_beneficiaires), 0) AS menages_beneficiaires_total,
                    COALESCE(SUM(quantite), 0) AS quantite_totale_brute
                FROM marts.vw_intrant_distribution
                WHERE {where_sql}
                GROUP BY campagne_yyyy
                ORDER BY campagne_yyyy
                """,
                params,
            )
            rows = cursor.fetchall()
            by_campagne = []
            for r in rows:
                annee = r[0]
                nb = r[1] or 0
                menages = r[2] or 0
                qte = float(r[3] or 0)

                part_distributions = (
                    round(100.0 * nb / nb_distributions, 2)
                    if nb_distributions > 0
                    else None
                )
                part_menages = (
                    round(100.0 * menages / menages_total, 2)
                    if menages_total > 0
                    else None
                )

                by_campagne.append(
                    {
                        "campagne_yyyy": annee,
                        "nb_distributions": nb,
                        "menages_beneficiaires_total": menages,
                        "quantite_totale_brute": qte,
                        "part_distributions_pct": part_distributions,
                        "part_menages_beneficiaires_pct": part_menages,
                    }
                )
            data["by_campagne"] = by_campagne

            # ==== 7) Par source d'intrant ====
            cursor.execute(
                f"""
                SELECT
                    source_intrant,
                    COUNT(*) AS nb_distributions,
                    COALESCE(SUM(menages_beneficiaires), 0) AS menages_beneficiaires_total,
                    COALESCE(SUM(quantite), 0) AS quantite_totale_brute
                FROM marts.vw_intrant_distribution
                WHERE {where_sql}
                GROUP BY source_intrant
                ORDER BY source_intrant
                """,
                params,
            )
            rows = cursor.fetchall()
            by_source = []
            for r in rows:
                src = r[0]
                nb = r[1] or 0
                menages = r[2] or 0
                qte = float(r[3] or 0)

                part_distributions = (
                    round(100.0 * nb / nb_distributions, 2)
                    if nb_distributions > 0
                    else None
                )
                part_menages = (
                    round(100.0 * menages / menages_total, 2)
                    if menages_total > 0
                    else None
                )

                by_source.append(
                    {
                        "source_intrant": src,
                        "nb_distributions": nb,
                        "menages_beneficiaires_total": menages,
                        "quantite_totale_brute": qte,
                        "part_distributions_pct": part_distributions,
                        "part_menages_beneficiaires_pct": part_menages,
                    }
                )
            data["by_source_intrant"] = by_source

        return Response(data)



# -----------------------------------------------------------------------------
# Liste des marchÃ©s
# -----------------------------------------------------------------------------

class MarcheListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Liste des marchÃ©s / comptoirs (marts.vw_marche),
    filtrÃ©s par projet actif via project_code (FIERE / AGRIECO).

    Filtres possibles en query string :
    - ?search=...                (localite, filiere_label, gestionnaire,
                                  obs_comptoir, commune_nom, region_nom)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?filiere=...              (code ref.filiere)
    - ?type_comptoir=...        (valeur brute du champ type_comptoir)
    - ?frequence_marche=...     (valeur brute du champ frequence_marche)
    - ?gestionnaire=...         (valeur brute du champ gestionnaire)
    - ?is_active=true|false
    - ?has_geom=true|false      (prÃ©sence de gÃ©omÃ©trie)
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc  # FIERE / AGRIECO

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        filiere = request.query_params.get("filiere")
        type_comptoir = request.query_params.get("type_comptoir")
        frequence_marche = request.query_params.get("frequence_marche")
        gestionnaire = request.query_params.get("gestionnaire")
        is_active = request.query_params.get("is_active")

        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques du marchÃ©
        if filiere:
            where_clauses.append("filiere = %s")
            params.append(filiere)

        if type_comptoir:
            where_clauses.append("type_comptoir = %s")
            params.append(type_comptoir)

        if frequence_marche:
            where_clauses.append("frequence_marche = %s")
            params.append(frequence_marche)

        if gestionnaire:
            where_clauses.append("gestionnaire = %s")
            params.append(gestionnaire)

        if is_active in ("true", "false"):
            where_clauses.append(sql_bool("is_active", is_active == "true"))

        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "localite ILIKE %s OR "
                "filiere_label ILIKE %s OR "
                "gestionnaire ILIKE %s OR "
                "obs_comptoir ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 6)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        # -------- SQL --------
        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_marche
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_marche
                WHERE {where_sql}
                ORDER BY filiere_label NULLS LAST,
                         localite NULLS LAST,
                         frequence_marche NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class MarcheAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    AgrÃ©gations sur les marchÃ©s / comptoirs (marts.vw_marche),
    filtrÃ©s par projet actif via project_code (FIERE / AGRIECO).

    Filtres possibles en query string (identiques Ã  MarcheListView) :
    - ?search=...
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?filiere=...
    - ?type_comptoir=...
    - ?frequence_marche=...
    - ?gestionnaire=...
    - ?is_active=true|false
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc  # FIERE / AGRIECO

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        filiere = request.query_params.get("filiere")
        type_comptoir = request.query_params.get("type_comptoir")
        frequence_marche = request.query_params.get("frequence_marche")
        gestionnaire = request.query_params.get("gestionnaire")
        is_active = request.query_params.get("is_active")

        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques du marchÃ©
        if filiere:
            where_clauses.append("filiere = %s")
            params.append(filiere)

        if type_comptoir:
            where_clauses.append("type_comptoir = %s")
            params.append(type_comptoir)

        if frequence_marche:
            where_clauses.append("frequence_marche = %s")
            params.append(frequence_marche)

        if gestionnaire:
            where_clauses.append("gestionnaire = %s")
            params.append(gestionnaire)

        if is_active in ("true", "false"):
            where_clauses.append(sql_bool("is_active", is_active == "true"))


        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "localite ILIKE %s OR "
                "filiere_label ILIKE %s OR "
                "gestionnaire ILIKE %s OR "
                "obs_comptoir ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 6)

        where_sql = " AND ".join(where_clauses)
        data: dict = {}

        with connection.cursor() as cursor:
            # ==== 1) GLOBAL ====
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS nb_marches_total,
                    COUNT(*) FILTER (WHERE {sql_true("is_active")}) AS nb_marches_actifs,
                    COUNT(*) FILTER (WHERE geom IS NOT NULL) AS nb_marches_geoloc
                FROM marts.vw_marche
                WHERE {where_sql}
                """,
                params,
            )
            row = cursor.fetchone()
            nb_total = row[0] or 0
            nb_actifs = row[1] or 0
            nb_geoloc = row[2] or 0

            data["global"] = {
                "nb_marches_total": nb_total,
                "nb_marches_actifs": nb_actifs,
                "nb_marches_geolocalises": nb_geoloc,
                "part_marches_actifs_pct": (
                    round(100.0 * nb_actifs / nb_total, 2) if nb_total > 0 else None
                ),
                "part_marches_geolocalises_pct": (
                    round(100.0 * nb_geoloc / nb_total, 2) if nb_total > 0 else None
                ),
            }

            # ==== 2) Par rÃ©gion ====
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    COUNT(*) AS nb_marches,
                    COUNT(*) FILTER (WHERE {sql_true("is_active")}) AS nb_marches_actifs,
                    COUNT(*) FILTER (WHERE geom IS NOT NULL) AS nb_marches_geoloc
                FROM marts.vw_marche
                WHERE {where_sql}
                GROUP BY id_region, region_nom
                ORDER BY region_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            by_region = []
            for r in rows:
                rid = r[0]
                rnom = r[1]
                nb = r[2] or 0
                nb_a = r[3] or 0
                nb_g = r[4] or 0

                by_region.append(
                    {
                        "id_region": rid,
                        "region_nom": rnom,
                        "nb_marches": nb,
                        "nb_marches_actifs": nb_a,
                        "nb_marches_geolocalises": nb_g,
                        "part_marches_pct": (
                            round(100.0 * nb / nb_total, 2)
                            if nb_total > 0
                            else None
                        ),
                    }
                )
            data["by_region"] = by_region

            # ==== 3) Par commune ====
            cursor.execute(
                f"""
                SELECT
                    id_commune,
                    commune_nom,
                    id_region,
                    region_nom,
                    COUNT(*) AS nb_marches,
                    COUNT(*) FILTER (WHERE {sql_true("is_active")}) AS nb_marches_actifs,
                    COUNT(*) FILTER (WHERE geom IS NOT NULL) AS nb_marches_geoloc
                FROM marts.vw_marche
                WHERE {where_sql}
                GROUP BY id_commune, commune_nom, id_region, region_nom
                ORDER BY region_nom, commune_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            by_commune = []
            for r in rows:
                cid = r[0]
                cnom = r[1]
                rid = r[2]
                rnom = r[3]
                nb = r[4] or 0
                nb_a = r[5] or 0
                nb_g = r[6] or 0

                by_commune.append(
                    {
                        "id_commune": cid,
                        "commune_nom": cnom,
                        "id_region": rid,
                        "region_nom": rnom,
                        "nb_marches": nb,
                        "nb_marches_actifs": nb_a,
                        "nb_marches_geolocalises": nb_g,
                        "part_marches_pct": (
                            round(100.0 * nb / nb_total, 2)
                            if nb_total > 0
                            else None
                        ),
                    }
                )
            data["by_commune"] = by_commune

            # ==== 4) Par filiÃ¨re ====
            cursor.execute(
                f"""
                SELECT
                    filiere,
                    filiere_label,
                    COUNT(*) AS nb_marches,
                    COUNT(*) FILTER (WHERE {sql_true("is_active")}) AS nb_marches_actifs,
                    COUNT(*) FILTER (WHERE geom IS NOT NULL) AS nb_marches_geoloc
                FROM marts.vw_marche
                WHERE {where_sql}
                GROUP BY filiere, filiere_label
                ORDER BY filiere_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_filiere = []
            for r in rows:
                code = r[0]
                label = r[1]
                nb = r[2] or 0
                nb_a = r[3] or 0
                nb_g = r[4] or 0

                by_filiere.append(
                    {
                        "filiere": code,
                        "filiere_label": label,
                        "nb_marches": nb,
                        "nb_marches_actifs": nb_a,
                        "nb_marches_geolocalises": nb_g,
                        "part_marches_pct": (
                            round(100.0 * nb / nb_total, 2)
                            if nb_total > 0
                            else None
                        ),
                    }
                )
            data["by_filiere"] = by_filiere

            # ==== 5) Par type de comptoir ====
            cursor.execute(
                f"""
                SELECT
                    type_comptoir,
                    COUNT(*) AS nb_marches,
                    COUNT(*) FILTER (WHERE {sql_true("is_active")}) AS nb_marches_actifs,
                    COUNT(*) FILTER (WHERE geom IS NOT NULL) AS nb_marches_geoloc
                FROM marts.vw_marche
                WHERE {where_sql}
                GROUP BY type_comptoir
                ORDER BY type_comptoir
                """,
                params,
            )
            rows = cursor.fetchall()
            by_type_comptoir = []
            for r in rows:
                tc = r[0]
                nb = r[1] or 0
                nb_a = r[2] or 0
                nb_g = r[3] or 0

                by_type_comptoir.append(
                    {
                        "type_comptoir": tc,
                        "nb_marches": nb,
                        "nb_marches_actifs": nb_a,
                        "nb_marches_geolocalises": nb_g,
                        "part_marches_pct": (
                            round(100.0 * nb / nb_total, 2)
                            if nb_total > 0
                            else None
                        ),
                    }
                )
            data["by_type_comptoir"] = by_type_comptoir

            # ==== 6) Par frÃ©quence de marchÃ© ====
            cursor.execute(
                f"""
                SELECT
                    frequence_marche,
                    COUNT(*) AS nb_marches,
                    COUNT(*) FILTER (WHERE {sql_true("is_active")}) AS nb_marches_actifs,
                    COUNT(*) FILTER (WHERE geom IS NOT NULL) AS nb_marches_geoloc
                FROM marts.vw_marche
                WHERE {where_sql}
                GROUP BY frequence_marche
                ORDER BY frequence_marche
                """,
                params,
            )
            rows = cursor.fetchall()
            by_frequence = []
            for r in rows:
                freq = r[0]
                nb = r[1] or 0
                nb_a = r[2] or 0
                nb_g = r[3] or 0

                by_frequence.append(
                    {
                        "frequence_marche": freq,
                        "nb_marches": nb,
                        "nb_marches_actifs": nb_a,
                        "nb_marches_geolocalises": nb_g,
                        "part_marches_pct": (
                            round(100.0 * nb / nb_total, 2)
                            if nb_total > 0
                            else None
                        ),
                    }
                )
            data["by_frequence_marche"] = by_frequence

            # ==== 7) Par gestionnaire ====
            cursor.execute(
                f"""
                SELECT
                    gestionnaire,
                    COUNT(*) AS nb_marches,
                    COUNT(*) FILTER (WHERE {sql_true("is_active")}) AS nb_marches_actifs,
                    COUNT(*) FILTER (WHERE geom IS NOT NULL) AS nb_marches_geoloc
                FROM marts.vw_marche
                WHERE {where_sql}
                GROUP BY gestionnaire
                ORDER BY gestionnaire
                """,
                params,
            )
            rows = cursor.fetchall()
            by_gestionnaire = []
            for r in rows:
                gest = r[0]
                nb = r[1] or 0
                nb_a = r[2] or 0
                nb_g = r[3] or 0

                by_gestionnaire.append(
                    {
                        "gestionnaire": gest,
                        "nb_marches": nb,
                        "nb_marches_actifs": nb_a,
                        "nb_marches_geolocalises": nb_g,
                        "part_marches_pct": (
                            round(100.0 * nb / nb_total, 2)
                            if nb_total > 0
                            else None
                        ),
                    }
                )
            data["by_gestionnaire"] = by_gestionnaire

        return Response(data)


# -----------------------------------------------------------------------------
# Liste des mÃ©tÃ©o mesure
# -----------------------------------------------------------------------------

class MeteoMesureListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Mesures mÃ©tÃ©o (marts.vw_meteo_mesure), filtrÃ©es par projet actif.

    Filtres possibles en query string :
    - ?search=...              (nom_station, localite, obs_pluie,
                                commune_nom, region_nom)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?code_station=...
    - ?type_station=...        (code ref.type_station)
    - ?statut_station=...      (code ref.statut_station)
    - ?date_obs_from=YYYY-MM-DD
    - ?date_obs_to=YYYY-MM-DD
    - ?is_active=true|false
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        code_station = request.query_params.get("code_station")
        type_station = request.query_params.get("type_station")
        statut_station = request.query_params.get("statut_station")

        date_obs_from = request.query_params.get("date_obs_from")
        date_obs_to = request.query_params.get("date_obs_to")

        is_active = request.query_params.get("is_active")
        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # Station / mesure
        if code_station:
            where_clauses.append("code_station = %s")
            params.append(code_station)

        if type_station:
            where_clauses.append("type_station = %s")
            params.append(type_station)

        if statut_station:
            where_clauses.append("statut_station = %s")
            params.append(statut_station)

        if date_obs_from:
            where_clauses.append("date_obs >= %s")
            params.append(date_obs_from)

        if date_obs_to:
            where_clauses.append("date_obs <= %s")
            params.append(date_obs_to)

        if is_active in ("true", "false"):
            where_clauses.append(sql_bool("is_active", is_active == "true"))


        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "nom_station ILIKE %s OR "
                "localite ILIKE %s OR "
                "obs_pluie ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 5)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        # -------- SQL --------
        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_meteo_mesure
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_meteo_mesure
                WHERE {where_sql}
                ORDER BY date_obs DESC NULLS LAST,
                         nom_station NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class MeteoMesureAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    AgrÃ©gations sur les mesures mÃ©tÃ©o (marts.vw_meteo_mesure),
    filtrÃ©es par projet actif via project_code (FIERE / AGRIECO).

    Filtres possibles en query string (identiques Ã  MeteoMesureListView) :
    - ?search=...
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?code_station=...
    - ?type_station=...
    - ?statut_station=...
    - ?date_obs_from=YYYY-MM-DD
    - ?date_obs_to=YYYY-MM-DD
    - ?is_active=true|false
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        code_station = request.query_params.get("code_station")
        type_station = request.query_params.get("type_station")
        statut_station = request.query_params.get("statut_station")

        date_obs_from = request.query_params.get("date_obs_from")
        date_obs_to = request.query_params.get("date_obs_to")

        is_active = request.query_params.get("is_active")
        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # Station
        if code_station:
            where_clauses.append("code_station = %s")
            params.append(code_station)

        if type_station:
            where_clauses.append("type_station = %s")
            params.append(type_station)

        if statut_station:
            where_clauses.append("statut_station = %s")
            params.append(statut_station)

        # Dates
        if date_obs_from:
            where_clauses.append("date_obs >= %s")
            params.append(date_obs_from)

        if date_obs_to:
            where_clauses.append("date_obs <= %s")
            params.append(date_obs_to)

        # Statut / gÃ©omÃ©trie
        if is_active in ("true", "false"):
            where_clauses.append(sql_bool("is_active", is_active == "true"))


        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "nom_station ILIKE %s OR "
                "code_station ILIKE %s OR "
                "type_station_label ILIKE %s OR "
                "statut_station_label ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 6)

        where_sql = " AND ".join(where_clauses)

        data: dict = {}

        with connection.cursor() as cursor:
            # ==== 1) GLOBAL ====
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS nb_mesures,
                    COUNT(*) FILTER (WHERE pluie_mm IS NOT NULL) AS nb_mesures_pluie,
                    COALESCE(SUM(pluie_mm), 0) AS somme_pluie_mm,
                    ROUND(AVG(pluie_mm)::numeric, 2) AS moyenne_pluie_mm,
                    ROUND(AVG(t_min)::numeric, 2) AS tmin_moy,
                    ROUND(AVG(t_max)::numeric, 2) AS tmax_moy
                FROM marts.vw_meteo_mesure
                WHERE {where_sql}
                """,
                params,
            )
            row = cursor.fetchone()
            data["global"] = {
                "nb_mesures": row[0] or 0,
                "nb_mesures_pluie": row[1] or 0,
                "somme_pluie_mm": float(row[2]) if row[2] is not None else 0.0,
                "moyenne_pluie_mm": float(row[3]) if row[3] is not None else None,
                "tmin_moy": float(row[4]) if row[4] is not None else None,
                "tmax_moy": float(row[5]) if row[5] is not None else None,
            }

            # ==== 2) Par rÃ©gion ====
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    COUNT(*) AS nb_mesures,
                    COUNT(pluie_mm) AS nb_mesures_pluie,
                    SUM(pluie_mm) AS somme_pluie_mm,
                    AVG(pluie_mm) AS moyenne_pluie_mm
                FROM marts.vw_meteo_mesure
                WHERE {where_sql}
                GROUP BY id_region, region_nom
                ORDER BY region_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_region"] = [
                {
                    "id_region": r[0],
                    "region_nom": r[1],
                    "nb_mesures": r[2] or 0,
                    "nb_mesures_pluie": r[3] or 0,
                    "somme_pluie_mm": float(r[4]) if r[4] is not None else None,
                    "moyenne_pluie_mm": float(r[5]) if r[5] is not None else None,
                }
                for r in rows
            ]

            # ==== 3) Par commune ====
            cursor.execute(
                f"""
                SELECT
                    id_commune,
                    commune_nom,
                    COUNT(*) AS nb_mesures,
                    COUNT(pluie_mm) AS nb_mesures_pluie,
                    SUM(pluie_mm) AS somme_pluie_mm,
                    AVG(pluie_mm) AS moyenne_pluie_mm
                FROM marts.vw_meteo_mesure
                WHERE {where_sql}
                GROUP BY id_commune, commune_nom
                ORDER BY commune_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_commune"] = [
                {
                    "id_commune": r[0],
                    "commune_nom": r[1],
                    "nb_mesures": r[2] or 0,
                    "nb_mesures_pluie": r[3] or 0,
                    "somme_pluie_mm": float(r[4]) if r[4] is not None else None,
                    "moyenne_pluie_mm": float(r[5]) if r[5] is not None else None,
                }
                for r in rows
            ]

            # ==== 4) Par station ====
            cursor.execute(
                f"""
                SELECT
                    code_station,
                    nom_station,
                    COUNT(*) AS nb_mesures,
                    COUNT(pluie_mm) AS nb_mesures_pluie,
                    SUM(pluie_mm) AS somme_pluie_mm,
                    AVG(pluie_mm) AS moyenne_pluie_mm
                FROM marts.vw_meteo_mesure
                WHERE {where_sql}
                GROUP BY code_station, nom_station
                ORDER BY nom_station
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_station"] = [
                {
                    "code_station": r[0],
                    "nom_station": r[1],
                    "nb_mesures": r[2] or 0,
                    "nb_mesures_pluie": r[3] or 0,
                    "somme_pluie_mm": float(r[4]) if r[4] is not None else None,
                    "moyenne_pluie_mm": float(r[5]) if r[5] is not None else None,
                }
                for r in rows
            ]

            # ==== 5) Par type de station ====
            cursor.execute(
                f"""
                SELECT
                    type_station,
                    type_station_label,
                    COUNT(*) AS nb_mesures,
                    COUNT(pluie_mm) AS nb_mesures_pluie,
                    SUM(pluie_mm) AS somme_pluie_mm,
                    AVG(pluie_mm) AS moyenne_pluie_mm
                FROM marts.vw_meteo_mesure
                WHERE {where_sql}
                GROUP BY type_station, type_station_label
                ORDER BY type_station_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_type_station = []
            for r in rows:
                tcode = r[0]
                tlabel = r[1]
                nb = r[2] or 0
                nb_p = r[3] or 0
                s_pluie = r[4]
                m_pluie = r[5]
                by_type_station.append(
                    {
                        "type_station": tcode,
                        "type_station_label": tlabel,
                        "nb_mesures": nb,
                        "nb_mesures_pluie": nb_p,
                        "somme_pluie_mm": float(s_pluie) if s_pluie is not None else None,
                        "moyenne_pluie_mm": float(m_pluie) if m_pluie is not None else None,
                    }
                )
            data["by_type_station"] = by_type_station

            # ==== 6) Par statut de station ====
            cursor.execute(
                f"""
                SELECT
                    statut_station,
                    statut_station_label,
                    COUNT(*) AS nb_mesures,
                    COUNT(pluie_mm) AS nb_mesures_pluie,
                    SUM(pluie_mm) AS somme_pluie_mm,
                    AVG(pluie_mm) AS moyenne_pluie_mm
                FROM marts.vw_meteo_mesure
                WHERE {where_sql}
                GROUP BY statut_station, statut_station_label
                ORDER BY statut_station_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_statut_station = []
            for r in rows:
                scode = r[0]
                slabel = r[1]
                nb = r[2] or 0
                nb_p = r[3] or 0
                s_pluie = r[4]
                m_pluie = r[5]
                by_statut_station.append(
                    {
                        "statut_station": scode,
                        "statut_station_label": slabel,
                        "nb_mesures": nb,
                        "nb_mesures_pluie": nb_p,
                        "somme_pluie_mm": float(s_pluie) if s_pluie is not None else None,
                        "moyenne_pluie_mm": float(m_pluie) if m_pluie is not None else None,
                    }
                )
            data["by_statut_station"] = by_statut_station

            # âœ… ==== 7) Par mois (pour le line chart pluie mensuelle) ====
            cursor.execute(
                f"""
                SELECT
                    to_char(date_trunc('month', date_obs), 'YYYY-MM') AS mois,
                    COALESCE(SUM(pluie_mm), 0) AS somme_pluie_mm,
                    ROUND(AVG(t_min)::numeric, 2) AS tmin_moy,
                    ROUND(AVG(t_max)::numeric, 2) AS tmax_moy,
                    COUNT(*) AS nb_mesures
                FROM marts.vw_meteo_mesure
                WHERE {where_sql}
                GROUP BY date_trunc('month', date_obs)
                ORDER BY date_trunc('month', date_obs)
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_month"] = [
                {
                    "mois": r[0],
                    "somme_pluie_mm": float(r[1]) if r[1] is not None else 0.0,
                    "tmin_moy": float(r[2]) if r[2] is not None else None,
                    "tmax_moy": float(r[3]) if r[3] is not None else None,
                    "nb_mesures": r[4] or 0,
                }
                for r in rows
            ]

        return Response(data)


# -----------------------------------------------------------------------------
# Liste des mÃ©tÃ©o station
# -----------------------------------------------------------------------------

class MeteoStationListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Liste des stations mÃ©tÃ©o (marts.vw_meteo_station),
    filtrÃ©es par projet actif (project_code).

    Filtres possibles en query string :
    - ?search=...                (nom_station, localite, obs_station,
                                  commune_nom, region_nom, proprietaire_label)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?code_station=...
    - ?type_station=...          (code ref.type_station)
    - ?proprietaire=...          (code ref.proprietaire_station)
    - ?statut_station=...        (code ref.statut_station)
    - ?frequence_mesure=...      (code ref.freq_mesure)
    - ?type_releve=...           (code ref.type_releve)
    - ?etat_equipements=...      (code ref.etat_equip)
    - ?date_mise_service_from=YYYY-MM-DD
    - ?date_mise_service_to=YYYY-MM-DD
    - ?is_active=true|false
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        code_station = request.query_params.get("code_station")
        type_station = request.query_params.get("type_station")
        proprietaire = request.query_params.get("proprietaire")
        statut_station = request.query_params.get("statut_station")
        frequence_mesure = request.query_params.get("frequence_mesure")
        type_releve = request.query_params.get("type_releve")
        etat_equipements = request.query_params.get("etat_equipements")

        date_mise_service_from = request.query_params.get("date_mise_service_from")
        date_mise_service_to = request.query_params.get("date_mise_service_to")

        is_active = request.query_params.get("is_active")
        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques station
        if code_station:
            where_clauses.append("code_station = %s")
            params.append(code_station)

        if type_station:
            where_clauses.append("type_station = %s")
            params.append(type_station)

        if proprietaire:
            where_clauses.append("proprietaire = %s")
            params.append(proprietaire)

        if statut_station:
            where_clauses.append("statut_station = %s")
            params.append(statut_station)

        if frequence_mesure:
            where_clauses.append("frequence_mesure = %s")
            params.append(frequence_mesure)

        if type_releve:
            where_clauses.append("type_releve = %s")
            params.append(type_releve)

        if etat_equipements:
            where_clauses.append("etat_equipements = %s")
            params.append(etat_equipements)

        # Dates
        if date_mise_service_from:
            where_clauses.append("date_mise_service >= %s")
            params.append(date_mise_service_from)

        if date_mise_service_to:
            where_clauses.append("date_mise_service <= %s")
            params.append(date_mise_service_to)

        if is_active in ("true", "false"):
            where_clauses.append(sql_bool("is_active", is_active == "true"))


        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "nom_station ILIKE %s OR "
                "localite ILIKE %s OR "
                "obs_station ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s OR "
                "proprietaire_label ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 6)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        # -------- SQL --------
        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_meteo_station
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_meteo_station
                WHERE {where_sql}
                ORDER BY nom_station NULLS LAST,
                         date_mise_service DESC NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class MeteoStationAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    AgrÃ©gations sur les stations mÃ©tÃ©o (marts.vw_meteo_station),
    filtrÃ©es par projet actif via project_code (FIERE / AGRIECO).

    Filtres possibles en query string (identiques Ã  MeteoStationListView) :
    - ?search=...
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?code_station=...
    - ?type_station=...
    - ?proprietaire=...
    - ?statut_station=...
    - ?frequence_mesure=...
    - ?type_releve=...
    - ?etat_equipements=...
    - ?date_mise_service_from=YYYY-MM-DD
    - ?date_mise_service_to=YYYY-MM-DD
    - ?is_active=true|false
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc
        data: dict = {}

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        code_station = request.query_params.get("code_station")
        type_station = request.query_params.get("type_station")
        proprietaire = request.query_params.get("proprietaire")
        statut_station = request.query_params.get("statut_station")
        frequence_mesure = request.query_params.get("frequence_mesure")
        type_releve = request.query_params.get("type_releve")
        etat_equipements = request.query_params.get("etat_equipements")

        date_mise_service_from = request.query_params.get("date_mise_service_from")
        date_mise_service_to = request.query_params.get("date_mise_service_to")

        is_active = request.query_params.get("is_active")
        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # CaractÃ©ristiques
        if code_station:
            where_clauses.append("code_station = %s")
            params.append(code_station)

        if type_station:
            where_clauses.append("type_station = %s")
            params.append(type_station)

        if proprietaire:
            where_clauses.append("proprietaire = %s")
            params.append(proprietaire)

        if statut_station:
            where_clauses.append("statut_station = %s")
            params.append(statut_station)

        if frequence_mesure:
            where_clauses.append("frequence_mesure = %s")
            params.append(frequence_mesure)

        if type_releve:
            where_clauses.append("type_releve = %s")
            params.append(type_releve)

        if etat_equipements:
            where_clauses.append("etat_equipements = %s")
            params.append(etat_equipements)

        # Dates
        if date_mise_service_from:
            where_clauses.append("date_mise_service >= %s")
            params.append(date_mise_service_from)

        if date_mise_service_to:
            where_clauses.append("date_mise_service <= %s")
            params.append(date_mise_service_to)

        if is_active in ("true", "false"):
            where_clauses.append(sql_bool("is_active", is_active == "true"))


        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "nom_station ILIKE %s OR "
                "localite ILIKE %s OR "
                "obs_station ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s OR "
                "proprietaire_label ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 6)

        where_sql = " AND ".join(where_clauses)

        with connection.cursor() as cursor:
            # ==== 1) GLOBAL ====
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS nb_stations,
                    COUNT(*) FILTER (WHERE is_active IS TRUE) AS nb_actives,
                    COUNT(*) FILTER (WHERE is_active IS FALSE) AS nb_inactives,
                    COUNT(geom) AS nb_with_geom,
                    MIN(date_mise_service) AS premiere_mise_service,
                    MAX(date_mise_service) AS derniere_mise_service
                FROM marts.vw_meteo_station
                WHERE {where_sql}
                """,
                params,
            )
            row = cursor.fetchone()
            nb_stations = row[0] or 0
            nb_actives = row[1] or 0
            nb_inactives = row[2] or 0
            nb_with_geom = row[3] or 0
            premiere_mise_service = row[4]
            derniere_mise_service = row[5]

            data["global"] = {
                "nb_stations": nb_stations,
                "nb_actives": nb_actives,
                "nb_inactives": nb_inactives,
                "nb_with_geom": nb_with_geom,
                "nb_without_geom": nb_stations - nb_with_geom,
                "premiere_mise_service": premiere_mise_service,
                "derniere_mise_service": derniere_mise_service,
            }

            # ==== 2) Par rÃ©gion ====
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    COUNT(*) AS nb_stations,
                    COUNT(*) FILTER (WHERE is_active IS TRUE) AS nb_actives,
                    COUNT(geom) AS nb_with_geom
                FROM marts.vw_meteo_station
                WHERE {where_sql}
                GROUP BY id_region, region_nom
                ORDER BY region_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            by_region = []
            for r in rows:
                rid = r[0]
                rnom = r[1]
                nb = r[2] or 0
                act = r[3] or 0
                with_geom = r[4] or 0

                by_region.append(
                    {
                        "id_region": rid,
                        "region_nom": rnom,
                        "nb_stations": nb,
                        "nb_actives": act,
                        "nb_inactives": nb - act,
                        "nb_with_geom": with_geom,
                        "nb_without_geom": nb - with_geom,
                    }
                )
            data["by_region"] = by_region

            # ==== 3) Par type de station ====
            cursor.execute(
                f"""
                SELECT
                    type_station,
                    type_station_label,
                    COUNT(*) AS nb_stations,
                    COUNT(*) FILTER (WHERE is_active IS TRUE) AS nb_actives
                FROM marts.vw_meteo_station
                WHERE {where_sql}
                GROUP BY type_station, type_station_label
                ORDER BY type_station_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_type_station = []
            for r in rows:
                tcode = r[0]
                tlabel = r[1]
                nb = r[2] or 0
                act = r[3] or 0

                by_type_station.append(
                    {
                        "type_station": tcode,
                        "type_station_label": tlabel,
                        "nb_stations": nb,
                        "nb_actives": act,
                        "nb_inactives": nb - act,
                    }
                )
            data["by_type_station"] = by_type_station

            # ==== 4) Par propriÃ©taire ====
            cursor.execute(
                f"""
                SELECT
                    proprietaire,
                    proprietaire_label,
                    COUNT(*) AS nb_stations,
                    COUNT(*) FILTER (WHERE is_active IS TRUE) AS nb_actives
                FROM marts.vw_meteo_station
                WHERE {where_sql}
                GROUP BY proprietaire, proprietaire_label
                ORDER BY proprietaire_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_proprietaire = []
            for r in rows:
                pcode = r[0]
                plabel = r[1]
                nb = r[2] or 0
                act = r[3] or 0

                by_proprietaire.append(
                    {
                        "proprietaire": pcode,
                        "proprietaire_label": plabel,
                        "nb_stations": nb,
                        "nb_actives": act,
                        "nb_inactives": nb - act,
                    }
                )
            data["by_proprietaire"] = by_proprietaire

            # ==== 5) Par statut de station ====
            cursor.execute(
                f"""
                SELECT
                    statut_station,
                    statut_station_label,
                    COUNT(*) AS nb_stations
                FROM marts.vw_meteo_station
                WHERE {where_sql}
                GROUP BY statut_station, statut_station_label
                ORDER BY statut_station_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_statut_station = []
            for r in rows:
                scode = r[0]
                slabel = r[1]
                nb = r[2] or 0

                by_statut_station.append(
                    {
                        "statut_station": scode,
                        "statut_station_label": slabel,
                        "nb_stations": nb,
                    }
                )
            data["by_statut_station"] = by_statut_station

            # ==== 6) Par frÃ©quence de mesure ====
            cursor.execute(
                f"""
                SELECT
                    frequence_mesure,
                    frequence_mesure_label,
                    COUNT(*) AS nb_stations
                FROM marts.vw_meteo_station
                WHERE {where_sql}
                GROUP BY frequence_mesure, frequence_mesure_label
                ORDER BY frequence_mesure_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_freq_mesure = []
            for r in rows:
                fcode = r[0]
                flabel = r[1]
                nb = r[2] or 0

                by_freq_mesure.append(
                    {
                        "frequence_mesure": fcode,
                        "frequence_mesure_label": flabel,
                        "nb_stations": nb,
                    }
                )
            data["by_freq_mesure"] = by_freq_mesure

            # ==== 7) Par type de relevÃ© ====
            cursor.execute(
                f"""
                SELECT
                    type_releve,
                    type_releve_label,
                    COUNT(*) AS nb_stations
                FROM marts.vw_meteo_station
                WHERE {where_sql}
                GROUP BY type_releve, type_releve_label
                ORDER BY type_releve_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_type_releve = []
            for r in rows:
                rcode = r[0]
                rlabel = r[1]
                nb = r[2] or 0

                by_type_releve.append(
                    {
                        "type_releve": rcode,
                        "type_releve_label": rlabel,
                        "nb_stations": nb,
                    }
                )
            data["by_type_releve"] = by_type_releve

            # ==== 8) Par Ã©tat des Ã©quipements ====
            cursor.execute(
                f"""
                SELECT
                    etat_equipements,
                    etat_equipements_label,
                    COUNT(*) AS nb_stations
                FROM marts.vw_meteo_station
                WHERE {where_sql}
                GROUP BY etat_equipements, etat_equipements_label
                ORDER BY etat_equipements_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_etat_equipements = []
            for r in rows:
                ecode = r[0]
                elabel = r[1]
                nb = r[2] or 0

                by_etat_equipements.append(
                    {
                        "etat_equipements": ecode,
                        "etat_equipements_label": elabel,
                        "nb_stations": nb,
                    }
                )
            data["by_etat_equipements"] = by_etat_equipements

        return Response(data)


# -----------------------------------------------------------------------------
# Liste des ouvrages
# -----------------------------------------------------------------------------

class OuvrageListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Liste des ouvrages (marts.vw_ouvrage), filtrÃ©s par projet actif.

    Filtres possibles en query string :
    - ?search=...                    (code_ouvrage, localite, type_ouvrages_label,
                                      autre_ouv_preciser, obs_ouvr,
                                      commune_nom, region_nom)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?type_ouvrage_code=...        (code ref.type_ouvrage ; testÃ© sur
                                     type_ouvrages_codes)
    - ?etat_anti=...                (code ref.etat_ouvrage)
    - ?etat_couv=...                (code ref.etat_ouvrage)
    - ?longueur_min=...             (m)
    - ?longueur_max=...             (m)
    - ?surface_min=...              (ha)
    - ?surface_max=...              (ha)
    - ?is_active=true|false
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc  # FIERE / AGRIECO

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        type_ouvrage_code = request.query_params.get("type_ouvrage_code")
        etat_anti = request.query_params.get("etat_anti")
        etat_couv = request.query_params.get("etat_couv")

        longueur_min = request.query_params.get("longueur_min")
        longueur_max = request.query_params.get("longueur_max")
        surface_min = request.query_params.get("surface_min")
        surface_max = request.query_params.get("surface_max")

        is_active = request.query_params.get("is_active")
        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # Type dâ€™ouvrage (code dans tableau type_ouvrages_codes)
        if type_ouvrage_code:
            where_clauses.append("%s = ANY(type_ouvrages_codes)")
            params.append(type_ouvrage_code)

        # Ã‰tat des ouvrages
        if etat_anti:
            where_clauses.append("etat_anti = %s")
            params.append(etat_anti)

        if etat_couv:
            where_clauses.append("etat_couv = %s")
            params.append(etat_couv)

        # Longueur / surface
        if longueur_min:
            where_clauses.append("longueur_anti_m >= %s")
            params.append(longueur_min)

        if longueur_max:
            where_clauses.append("longueur_anti_m <= %s")
            params.append(longueur_max)

        if surface_min:
            where_clauses.append("surface_couv_ha >= %s")
            params.append(surface_min)

        if surface_max:
            where_clauses.append("surface_couv_ha <= %s")
            params.append(surface_max)

        # Statut / gÃ©omÃ©trie
        if is_active in ("true", "false"):
            where_clauses.append(sql_bool("is_active", is_active == "true"))


        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "code_ouvrage ILIKE %s OR "
                "localite ILIKE %s OR "
                "type_ouvrages_label ILIKE %s OR "
                "autre_ouv_preciser ILIKE %s OR "
                "obs_ouvr ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 7)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        # -------- SQL --------
        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_ouvrage
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_ouvrage
                WHERE {where_sql}
                ORDER BY id_region NULLS LAST,
                         id_prefecture NULLS LAST,
                         id_commune NULLS LAST,
                         code_ouvrage NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class OuvrageAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    AgrÃ©gations sur les ouvrages (marts.vw_ouvrage),
    filtrÃ©s par projet actif via project_code (FIERE / AGRIECO).

    Filtres possibles en query string (identiques Ã  OuvrageListView) :
    - ?search=...
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?type_ouvrage_code=...
    - ?etat_anti=...
    - ?etat_couv=...
    - ?longueur_min=...
    - ?longueur_max=...
    - ?surface_min=...
    - ?surface_max=...
    - ?is_active=true|false
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc
        data: dict = {}

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        type_ouvrage_code = request.query_params.get("type_ouvrage_code")
        etat_anti = request.query_params.get("etat_anti")
        etat_couv = request.query_params.get("etat_couv")

        longueur_min = request.query_params.get("longueur_min")
        longueur_max = request.query_params.get("longueur_max")
        surface_min = request.query_params.get("surface_min")
        surface_max = request.query_params.get("surface_max")

        is_active = request.query_params.get("is_active")
        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # Type dâ€™ouvrage
        if type_ouvrage_code:
            where_clauses.append("%s = ANY(type_ouvrages_codes)")
            params.append(type_ouvrage_code)

        # Ã‰tats
        if etat_anti:
            where_clauses.append("etat_anti = %s")
            params.append(etat_anti)

        if etat_couv:
            where_clauses.append("etat_couv = %s")
            params.append(etat_couv)

        # Longueur / surface
        if longueur_min:
            where_clauses.append("longueur_anti_m >= %s")
            params.append(longueur_min)

        if longueur_max:
            where_clauses.append("longueur_anti_m <= %s")
            params.append(longueur_max)

        if surface_min:
            where_clauses.append("surface_couv_ha >= %s")
            params.append(surface_min)

        if surface_max:
            where_clauses.append("surface_couv_ha <= %s")
            params.append(surface_max)

        # Statut / gÃ©omÃ©trie
        if is_active in ("true", "false"):
            where_clauses.append(sql_bool("is_active", is_active == "true"))


        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "code_ouvrage ILIKE %s OR "
                "localite ILIKE %s OR "
                "type_ouvrages_label ILIKE %s OR "
                "autre_ouv_preciser ILIKE %s OR "
                "obs_ouvr ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 7)

        where_sql = " AND ".join(where_clauses)

        with connection.cursor() as cursor:
            # ==== 1) GLOBAL ====
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS nb_ouvrages,
                    COUNT(*) FILTER (WHERE is_active IS TRUE) AS nb_actifs,
                    COUNT(*) FILTER (WHERE is_active IS FALSE) AS nb_inactifs,
                    COUNT(geom) AS nb_with_geom,
                    SUM(longueur_anti_m) AS longueur_totale_m,
                    AVG(longueur_anti_m) AS longueur_moy_m,
                    MIN(longueur_anti_m) AS longueur_min_m,
                    MAX(longueur_anti_m) AS longueur_max_m,
                    SUM(surface_couv_ha) AS surface_totale_ha,
                    AVG(surface_couv_ha) AS surface_moy_ha,
                    MIN(surface_couv_ha) AS surface_min_ha,
                    MAX(surface_couv_ha) AS surface_max_ha
                FROM marts.vw_ouvrage
                WHERE {where_sql}
                """,
                params,
            )
            row = cursor.fetchone()
            nb_ouvrages = row[0] or 0
            nb_actifs = row[1] or 0
            nb_inactifs = row[2] or 0
            nb_with_geom = row[3] or 0

            data["global"] = {
                "nb_ouvrages": nb_ouvrages,
                "nb_actifs": nb_actifs,
                "nb_inactifs": nb_inactifs,
                "nb_with_geom": nb_with_geom,
                "nb_without_geom": nb_ouvrages - nb_with_geom,
                "longueur_totale_m": row[4] or 0,
                "longueur_moy_m": row[5],
                "longueur_min_m": row[6],
                "longueur_max_m": row[7],
                "surface_totale_ha": row[8] or 0,
                "surface_moy_ha": row[9],
                "surface_min_ha": row[10],
                "surface_max_ha": row[11],
            }

            # ==== 2) Par rÃ©gion ====
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    COUNT(*) AS nb_ouvrages,
                    COUNT(*) FILTER (WHERE is_active IS TRUE) AS nb_actifs,
                    COUNT(geom) AS nb_with_geom,
                    SUM(longueur_anti_m) AS longueur_totale_m,
                    SUM(surface_couv_ha) AS surface_totale_ha
                FROM marts.vw_ouvrage
                WHERE {where_sql}
                GROUP BY id_region, region_nom
                ORDER BY region_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            by_region = []
            for r in rows:
                rid = r[0]
                rnom = r[1]
                nb = r[2] or 0
                act = r[3] or 0
                with_geom = r[4] or 0

                by_region.append(
                    {
                        "id_region": rid,
                        "region_nom": rnom,
                        "nb_ouvrages": nb,
                        "nb_actifs": act,
                        "nb_inactifs": nb - act,
                        "nb_with_geom": with_geom,
                        "nb_without_geom": nb - with_geom,
                        "longueur_totale_m": r[5] or 0,
                        "surface_totale_ha": r[6] or 0,
                    }
                )
            data["by_region"] = by_region

            # ==== 3) Par type dâ€™ouvrage (array unnest) ====
            cursor.execute(
                f"""
                SELECT
                    u.code AS type_ouvrage_code,
                    MAX(t.label_fr) AS type_ouvrage_label,
                    COUNT(DISTINCT o.ouvrage_uuid) AS nb_ouvrages,
                    SUM(o.longueur_anti_m) AS longueur_totale_m,
                    SUM(o.surface_couv_ha) AS surface_totale_ha
                FROM (
                    SELECT *
                    FROM marts.vw_ouvrage
                    WHERE {where_sql}
                ) o
                LEFT JOIN LATERAL unnest(o.type_ouvrages_codes) AS u(code) ON TRUE
                LEFT JOIN ref.type_ouvrage t ON t.code = u.code
                GROUP BY u.code
                ORDER BY type_ouvrage_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_type_ouvrage = []
            for r in rows:
                tcode = r[0]
                tlabel = r[1]
                if tcode is None:
                    continue  # si aucun type, on skippe
                by_type_ouvrage.append(
                    {
                        "type_ouvrage_code": tcode,
                        "type_ouvrage_label": tlabel,
                        "nb_ouvrages": r[2] or 0,
                        "longueur_totale_m": r[3] or 0,
                        "surface_totale_ha": r[4] or 0,
                    }
                )
            data["by_type_ouvrage"] = by_type_ouvrage

            # ==== 4) Par Ã©tat antiÃ©rosion ====
            cursor.execute(
                f"""
                SELECT
                    etat_anti,
                    etat_anti_label,
                    COUNT(*) AS nb_ouvrages,
                    SUM(longueur_anti_m) AS longueur_totale_m
                FROM marts.vw_ouvrage
                WHERE {where_sql}
                GROUP BY etat_anti, etat_anti_label
                ORDER BY etat_anti_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_etat_anti = []
            for r in rows:
                by_etat_anti.append(
                    {
                        "etat_anti": r[0],
                        "etat_anti_label": r[1],
                        "nb_ouvrages": r[2] or 0,
                        "longueur_totale_m": r[3] or 0,
                    }
                )
            data["by_etat_anti"] = by_etat_anti

            # ==== 5) Par Ã©tat des couvertures ====
            cursor.execute(
                f"""
                SELECT
                    etat_couv,
                    etat_couv_label,
                    COUNT(*) AS nb_ouvrages,
                    SUM(surface_couv_ha) AS surface_totale_ha
                FROM marts.vw_ouvrage
                WHERE {where_sql}
                GROUP BY etat_couv, etat_couv_label
                ORDER BY etat_couv_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_etat_couv = []
            for r in rows:
                by_etat_couv.append(
                    {
                        "etat_couv": r[0],
                        "etat_couv_label": r[1],
                        "nb_ouvrages": r[2] or 0,
                        "surface_totale_ha": r[3] or 0,
                    }
                )
            data["by_etat_couv"] = by_etat_couv

        return Response(data)



# -----------------------------------------------------------------------------
# Liste des pratiques agro
# -----------------------------------------------------------------------------

class PratiquesAgroParcelleListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Pratiques agroÃ©cologiques par parcelle (marts.vw_pratiques_agro_parcelle),
    filtrÃ©es par projet actif (project_code).

    Filtres possibles en query string :
    - ?search=...                  (localite, culture_label, pratiques_agroeco_label,
                                    pratiques_autres, obs_pratiques, commune_nom, region_nom)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?campagne_yyyy=...
    - ?culture_code=...           (code ref.culture)
    - ?pratique_code=...          (code ref.pratique_agro ; teste sur pratiques_agroeco_codes)
    - ?effet_rendement=...        (code ref.effet_niveau)
    - ?effet_sols=...             (code ref.effet_niveau)
    - ?surface_min=...
    - ?surface_max=...
    - ?rendement_calc_min=...
    - ?rendement_calc_max=...
    - ?rendement_obs_min=...
    - ?rendement_obs_max=...
    - ?is_active=true|false
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc  # FIERE / AGRIECO

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        campagne_yyyy = request.query_params.get("campagne_yyyy")
        culture_code = request.query_params.get("culture_code")
        pratique_code = request.query_params.get("pratique_code")
        effet_rendement = request.query_params.get("effet_rendement")
        effet_sols = request.query_params.get("effet_sols")

        surface_min = request.query_params.get("surface_min")
        surface_max = request.query_params.get("surface_max")
        rendement_calc_min = request.query_params.get("rendement_calc_min")
        rendement_calc_max = request.query_params.get("rendement_calc_max")
        rendement_obs_min = request.query_params.get("rendement_obs_min")
        rendement_obs_max = request.query_params.get("rendement_obs_max")

        is_active = request.query_params.get("is_active")
        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # Dimensions agronomiques
        if campagne_yyyy:
            where_clauses.append("campagne_yyyy = %s")
            params.append(campagne_yyyy)

        if culture_code:
            where_clauses.append("culture_code = %s")
            params.append(culture_code)

        if pratique_code:
            # teste si le code est dans le tableau pratiques_agroeco_codes
            where_clauses.append("%s = ANY(pratiques_agroeco_codes)")
            params.append(pratique_code)

        if effet_rendement:
            where_clauses.append("effet_rendement = %s")
            params.append(effet_rendement)

        if effet_sols:
            where_clauses.append("effet_sols = %s")
            params.append(effet_sols)

        # Surface / rendements
        if surface_min:
            where_clauses.append("surface_ha >= %s")
            params.append(surface_min)

        if surface_max:
            where_clauses.append("surface_ha <= %s")
            params.append(surface_max)

        if rendement_calc_min:
            where_clauses.append("rendement_calc_kg_ha >= %s")
            params.append(rendement_calc_min)

        if rendement_calc_max:
            where_clauses.append("rendement_calc_kg_ha <= %s")
            params.append(rendement_calc_max)

        if rendement_obs_min:
            where_clauses.append("rendement_observe_kg_ha >= %s")
            params.append(rendement_obs_min)

        if rendement_obs_max:
            where_clauses.append("rendement_observe_kg_ha <= %s")
            params.append(rendement_obs_max)

        # Statut / gÃ©omÃ©trie
        if is_active in ("true", "false"):
            where_clauses.append(sql_bool("is_active", is_active == "true"))


        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "localite ILIKE %s OR "
                "culture_label ILIKE %s OR "
                "pratiques_agroeco_label ILIKE %s OR "
                "pratiques_autres ILIKE %s OR "
                "obs_pratiques ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 7)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        # -------- SQL --------
        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_pratiques_agro_parcelle
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_pratiques_agro_parcelle
                WHERE {where_sql}
                ORDER BY campagne_yyyy DESC NULLS LAST,
                         culture_label NULLS LAST,
                         localite NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class PratiquesAgroParcelleAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    AgrÃ©gations sur les pratiques agroÃ©cologiques par parcelle
    (marts.vw_pratiques_agro_parcelle), filtrÃ©es par projet actif (FIERE / AGRIECO).

    Filtres possibles en query string (identiques Ã  PratiquesAgroParcelleListView) :
    - ?search=...
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?campagne_yyyy=...
    - ?culture_code=...
    - ?pratique_code=...
    - ?effet_rendement=...
    - ?effet_sols=...
    - ?surface_min=...
    - ?surface_max=...
    - ?rendement_calc_min=...
    - ?rendement_calc_max=...
    - ?rendement_obs_min=...
    - ?rendement_obs_max=...
    - ?is_active=true|false
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc
        data: dict = {}

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        campagne_yyyy = request.query_params.get("campagne_yyyy")
        culture_code = request.query_params.get("culture_code")
        pratique_code = request.query_params.get("pratique_code")
        effet_rendement = request.query_params.get("effet_rendement")
        effet_sols = request.query_params.get("effet_sols")

        surface_min = request.query_params.get("surface_min")
        surface_max = request.query_params.get("surface_max")
        rendement_calc_min = request.query_params.get("rendement_calc_min")
        rendement_calc_max = request.query_params.get("rendement_calc_max")
        rendement_obs_min = request.query_params.get("rendement_obs_min")
        rendement_obs_max = request.query_params.get("rendement_obs_max")

        is_active = request.query_params.get("is_active")
        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # Dimensions agronomiques
        if campagne_yyyy:
            where_clauses.append("campagne_yyyy = %s")
            params.append(campagne_yyyy)

        if culture_code:
            where_clauses.append("culture_code = %s")
            params.append(culture_code)

        if pratique_code:
            where_clauses.append("%s = ANY(pratiques_agroeco_codes)")
            params.append(pratique_code)

        if effet_rendement:
            where_clauses.append("effet_rendement = %s")
            params.append(effet_rendement)

        if effet_sols:
            where_clauses.append("effet_sols = %s")
            params.append(effet_sols)

        # Surface / rendements
        if surface_min:
            where_clauses.append("surface_ha >= %s")
            params.append(surface_min)

        if surface_max:
            where_clauses.append("surface_ha <= %s")
            params.append(surface_max)

        if rendement_calc_min:
            where_clauses.append("rendement_calc_kg_ha >= %s")
            params.append(rendement_calc_min)

        if rendement_calc_max:
            where_clauses.append("rendement_calc_kg_ha <= %s")
            params.append(rendement_calc_max)

        if rendement_obs_min:
            where_clauses.append("rendement_observe_kg_ha >= %s")
            params.append(rendement_obs_min)

        if rendement_obs_max:
            where_clauses.append("rendement_observe_kg_ha <= %s")
            params.append(rendement_obs_max)

        # Statut / gÃ©omÃ©trie
        if is_active in ("true", "false"):
            where_clauses.append(sql_bool("is_active", is_active == "true"))


        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "localite ILIKE %s OR "
                "culture_label ILIKE %s OR "
                "pratiques_agroeco_label ILIKE %s OR "
                "pratiques_autres ILIKE %s OR "
                "obs_pratiques ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 7)

        where_sql = " AND ".join(where_clauses)

        with connection.cursor() as cursor:
            # ==== 1) GLOBAL ====
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS nb_parcelles,
                    COUNT(*) FILTER (WHERE is_active IS TRUE) AS nb_actives,
                    COUNT(geom) AS nb_with_geom,
                    SUM(surface_ha) AS surface_totale_ha,
                    AVG(surface_ha) AS surface_moy_ha,
                    MIN(surface_ha) AS surface_min_ha,
                    MAX(surface_ha) AS surface_max_ha,
                    SUM(production_totale_kg) AS production_totale_kg,
                    AVG(rendement_calc_kg_ha) AS rendement_calc_moy_kg_ha,
                    MIN(rendement_calc_kg_ha) AS rendement_calc_min_kg_ha,
                    MAX(rendement_calc_kg_ha) AS rendement_calc_max_kg_ha,
                    AVG(rendement_observe_kg_ha) AS rendement_obs_moy_kg_ha,
                    MIN(rendement_observe_kg_ha) AS rendement_obs_min_kg_ha,
                    MAX(rendement_observe_kg_ha) AS rendement_obs_max_kg_ha
                FROM marts.vw_pratiques_agro_parcelle
                WHERE {where_sql}
                """,
                params,
            )
            row = cursor.fetchone()

            nb_parcelles = row[0] or 0
            nb_actives = row[1] or 0
            nb_with_geom = row[2] or 0

            data["global"] = {
                "nb_parcelles": nb_parcelles,
                "nb_pratiques": nb_parcelles,      # alias dashboard
                "total_pratiques": nb_parcelles,   # alias dashboard
                "nb_actives": nb_actives,
                "nb_inactives": nb_parcelles - nb_actives,
                "nb_with_geom": nb_with_geom,
                "nb_without_geom": nb_parcelles - nb_with_geom,
                "surface_totale_ha": row[3] or 0,
                "surface_moy_ha": row[4],
                "surface_min_ha": row[5],
                "surface_max_ha": row[6],
                "production_totale_kg": row[7] or 0,
                "rendement_calc_moy_kg_ha": row[8],
                "rendement_calc_min_kg_ha": row[9],
                "rendement_calc_max_kg_ha": row[10],
                "rendement_obs_moy_kg_ha": row[11],
                "rendement_obs_min_kg_ha": row[12],
                "rendement_obs_max_kg_ha": row[13],
            }

            # ==== 2) Par rÃ©gion ====
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    COUNT(*) AS nb_parcelles,
                    SUM(surface_ha) AS surface_totale_ha,
                    SUM(production_totale_kg) AS production_totale_kg,
                    AVG(rendement_calc_kg_ha) AS rendement_calc_moy_kg_ha,
                    AVG(rendement_observe_kg_ha) AS rendement_obs_moy_kg_ha
                FROM marts.vw_pratiques_agro_parcelle
                WHERE {where_sql}
                GROUP BY id_region, region_nom
                ORDER BY region_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            by_region = []
            for r in rows:
                by_region.append(
                    {
                        "id_region": r[0],
                        "region_nom": r[1],
                        "nb_parcelles": r[2] or 0,
                        "surface_totale_ha": r[3] or 0,
                        "production_totale_kg": r[4] or 0,
                        "rendement_calc_moy_kg_ha": r[5],
                        "rendement_obs_moy_kg_ha": r[6],
                    }
                )
            data["by_region"] = by_region

            # ==== 3) Par culture ====
            cursor.execute(
                f"""
                SELECT
                    culture_code,
                    culture_label,
                    COUNT(*) AS nb_parcelles,
                    SUM(surface_ha) AS surface_totale_ha,
                    SUM(production_totale_kg) AS production_totale_kg,
                    AVG(rendement_calc_kg_ha) AS rendement_calc_moy_kg_ha,
                    AVG(rendement_observe_kg_ha) AS rendement_obs_moy_kg_ha
                FROM marts.vw_pratiques_agro_parcelle
                WHERE {where_sql}
                GROUP BY culture_code, culture_label
                ORDER BY culture_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_culture = []
            for r in rows:
                by_culture.append(
                    {
                        "culture_code": r[0],
                        "culture_label": r[1],
                        "nb_parcelles": r[2] or 0,
                        "surface_totale_ha": r[3] or 0,
                        "production_totale_kg": r[4] or 0,
                        "rendement_calc_moy_kg_ha": r[5],
                        "rendement_obs_moy_kg_ha": r[6],
                    }
                )
            data["by_culture"] = by_culture

            # ==== 4) Par campagne ====
            cursor.execute(
                f"""
                SELECT
                    campagne_yyyy,
                    COUNT(*) AS nb_parcelles,
                    SUM(surface_ha) AS surface_totale_ha,
                    SUM(production_totale_kg) AS production_totale_kg,
                    AVG(rendement_calc_kg_ha) AS rendement_calc_moy_kg_ha,
                    AVG(rendement_observe_kg_ha) AS rendement_obs_moy_kg_ha
                FROM marts.vw_pratiques_agro_parcelle
                WHERE {where_sql}
                GROUP BY campagne_yyyy
                ORDER BY campagne_yyyy
                """,
                params,
            )
            rows = cursor.fetchall()
            by_campagne = []
            for r in rows:
                by_campagne.append(
                    {
                        "campagne_yyyy": r[0],
                        "nb_parcelles": r[1] or 0,
                        "surface_totale_ha": r[2] or 0,
                        "production_totale_kg": r[3] or 0,
                        "rendement_calc_moy_kg_ha": r[4],
                        "rendement_obs_moy_kg_ha": r[5],
                    }
                )
            data["by_campagne"] = by_campagne

            # ==== 5) Par pratique agroÃ©cologique (unnest) ====
            cursor.execute(
                f"""
                SELECT
                    u.code AS pratique_code,
                    MAX(pr.libelle) AS pratique_label,
                    COUNT(DISTINCT p.pratique_uuid) AS nb_parcelles,
                    SUM(p.surface_ha) AS surface_totale_ha,
                    SUM(p.production_totale_kg) AS production_totale_kg,
                    AVG(p.rendement_calc_kg_ha) AS rendement_calc_moy_kg_ha,
                    AVG(p.rendement_observe_kg_ha) AS rendement_obs_moy_kg_ha
                FROM (
                    SELECT *
                    FROM marts.vw_pratiques_agro_parcelle
                    WHERE {where_sql}
                ) p
                LEFT JOIN LATERAL unnest(p.pratiques_agroeco_codes) AS u(code) ON TRUE
                LEFT JOIN ref.pratique_agro pr ON pr.code = u.code
                GROUP BY u.code
                ORDER BY pratique_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_pratique = []
            for r in rows:
                code = r[0]
                label = r[1]
                if code is None:
                    continue
                by_pratique.append(
                    {
                        "pratique_code": code,
                        "pratique_label": label,
                        "nb_parcelles": r[2] or 0,
                        "surface_totale_ha": r[3] or 0,
                        "production_totale_kg": r[4] or 0,
                        "rendement_calc_moy_kg_ha": r[5],
                        "rendement_obs_moy_kg_ha": r[6],
                    }
                )
            data["by_pratique"] = by_pratique

            # ==== 6) Par effet sur le rendement ====
            cursor.execute(
                f"""
                SELECT
                    effet_rendement,
                    effet_rendement_label,
                    COUNT(*) AS nb_parcelles
                FROM marts.vw_pratiques_agro_parcelle
                WHERE {where_sql}
                GROUP BY effet_rendement, effet_rendement_label
                ORDER BY effet_rendement_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_effet_rendement = []
            for r in rows:
                by_effet_rendement.append(
                    {
                        "effet_rendement": r[0],
                        "effet_rendement_label": r[1],
                        "nb_parcelles": r[2] or 0,
                    }
                )
            data["by_effet_rendement"] = by_effet_rendement

            # ==== 7) Par effet sur les sols ====
            cursor.execute(
                f"""
                SELECT
                    effet_sols,
                    effet_sols_label,
                    COUNT(*) AS nb_parcelles
                FROM marts.vw_pratiques_agro_parcelle
                WHERE {where_sql}
                GROUP BY effet_sols, effet_sols_label
                ORDER BY effet_sols_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_effet_sols = []
            for r in rows:
                by_effet_sols.append(
                    {
                        "effet_sols": r[0],
                        "effet_sols_label": r[1],
                        "nb_parcelles": r[2] or 0,
                    }
                )
            data["by_effet_sols"] = by_effet_sols

        return Response(data)



# -----------------------------------------------------------------------------
# Liste des tÃªtes sources
# -----------------------------------------------------------------------------

class TeteSourceListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    TÃªtes de source (marts.vw_tete_source), filtrÃ©es par projet actif.

    Filtres possibles en query string :
    - ?search=...                   (id_ts, localite, type_source_label,
                                     usage_principal_label, type_protection_labels,
                                     obs_ts, commune_nom, region_nom)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?type_source=...              (code ref.type_source)
    - ?usage_principal=...          (code ref.usage_source)
    - ?etat_fonctionnel=...         (code ref.etat_fonctionnel)
    - ?type_protection_code=...     (code ref.type_protection ; testÃ© dans type_protection_codes)
    - ?protection_exist=true|false
    - ?entretien_regulier=true|false
    - ?annee_protection_from=...
    - ?annee_protection_to=...
    - ?is_active=true|false
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc  # FIERE / AGRIECO

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        type_source = request.query_params.get("type_source")
        usage_principal = request.query_params.get("usage_principal")
        etat_fonctionnel = request.query_params.get("etat_fonctionnel")
        type_protection_code = request.query_params.get("type_protection_code")

        protection_exist = request.query_params.get("protection_exist")
        entretien_regulier = request.query_params.get("entretien_regulier")

        annee_protection_from = request.query_params.get("annee_protection_from")
        annee_protection_to = request.query_params.get("annee_protection_to")

        is_active = request.query_params.get("is_active")
        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # Typologie tÃªte de source
        if type_source:
            where_clauses.append("type_source = %s")
            params.append(type_source)

        if usage_principal:
            where_clauses.append("usage_principal = %s")
            params.append(usage_principal)

        if etat_fonctionnel:
            where_clauses.append("etat_fonctionnel = %s")
            params.append(etat_fonctionnel)

        if type_protection_code:
            # teste si le code est dans le tableau type_protection_codes
            where_clauses.append("%s = ANY(type_protection_codes)")
            params.append(type_protection_code)

        # BoolÃ©ens
        if protection_exist in ("true", "false"):
            where_clauses.append(sql_bool("protection_exist", protection_exist == "true"))

        if entretien_regulier in ("true", "false"):
            where_clauses.append(sql_bool("entretien_regulier", entretien_regulier == "true"))

        # AnnÃ©e de protection
        if annee_protection_from:
            where_clauses.append("annee_protection >= %s")
            params.append(annee_protection_from)

        if annee_protection_to:
            where_clauses.append("annee_protection <= %s")
            params.append(annee_protection_to)

        # Statut / gÃ©omÃ©trie
        if is_active in ("true", "false"):
            where_clauses.append(sql_bool("is_active", is_active == "true"))


        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "id_ts ILIKE %s OR "
                "localite ILIKE %s OR "
                "type_source_label ILIKE %s OR "
                "usage_principal_label ILIKE %s OR "
                "type_protection_labels ILIKE %s OR "
                "obs_ts ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 8)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        # -------- SQL --------
        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_tete_source
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_tete_source
                WHERE {where_sql}
                ORDER BY id_region NULLS LAST,
                         id_prefecture NULLS LAST,
                         id_commune NULLS LAST,
                         id_ts NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class TeteSourceAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    AgrÃ©gations sur les tÃªtes de sources (marts.vw_tete_source),
    filtrÃ©es par projet actif via project_code (FIERE / AGRIECO).

    + Ajout d'une estimation d'accÃ¨s Ã  l'eau potable :
      - pop_desservie (Kobo) / population de rÃ©fÃ©rence (ref.localite) agrÃ©gÃ© par commune.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc

        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        protection_exist = request.query_params.get("protection_exist")  # true/false
        type_protection = request.query_params.get("type_protection")
        etat_fonctionnel = request.query_params.get("etat_fonctionnel")

        is_active = request.query_params.get("is_active")
        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # Filtres spÃ©cifiques
        if protection_exist in ("true", "false"):
            where_clauses.append(sql_bool("protection_exist", protection_exist == "true"))

        if type_protection:
            where_clauses.append("type_protection = %s")
            params.append(type_protection)

        if etat_fonctionnel:
            where_clauses.append("etat_fonctionnel = %s")
            params.append(etat_fonctionnel)

        # Statut / gÃ©omÃ©trie
        if is_active in ("true", "false"):
            where_clauses.append(sql_bool("is_active", is_active == "true"))


        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("geom IS NOT NULL")
            else:
                where_clauses.append("geom IS NULL")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "localite ILIKE %s OR "
                "type_source_label ILIKE %s OR "
                "type_protection_labels ILIKE %s OR "
                "commune_nom ILIKE %s OR "
                "region_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 5)

        where_sql = " AND ".join(where_clauses)
        data: dict = {}

        with connection.cursor() as cursor:
            # ==== 1) GLOBAL ====
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS nb_tetes_source,
                    COUNT(*) FILTER (WHERE {sql_true("protection_exist")}) AS nb_protegees,
                    COUNT(*) FILTER (WHERE etat_fonctionnel = 'FONCTIONNEL') AS nb_fonctionnelles,
                    COUNT(*) FILTER (WHERE geom IS NOT NULL) AS nb_avec_geom,
                    COALESCE(SUM(pop_desservie), 0) AS pop_desservie_total
                FROM marts.vw_tete_source
                WHERE {where_sql}
                """,
                params,
            )
            row = cursor.fetchone()
            data["global"] = {
                "nb_tetes_source": row[0] or 0,
                "nb_protegees": row[1] or 0,
                "nb_fonctionnelles": row[2] or 0,
                "nb_avec_geom": row[3] or 0,
                "pop_desservie_total": float(row[4]) if row[4] is not None else 0.0,
            }

            # ==== 2) Par rÃ©gion ====
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    COUNT(*) AS nb_ts,
                    COUNT(*) FILTER (WHERE {sql_true("protection_exist")}) AS nb_protegees,
                    COALESCE(SUM(pop_desservie), 0) AS pop_desservie
                FROM marts.vw_tete_source
                WHERE {where_sql}
                GROUP BY id_region, region_nom
                ORDER BY region_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_region"] = [
                {
                    "id_region": r[0],
                    "region_nom": r[1],
                    "nb_tetes_source": r[2] or 0,
                    "nb_protegees": r[3] or 0,
                    "pop_desservie": float(r[4]) if r[4] is not None else 0.0,
                }
                for r in rows
            ]

            # ==== 3) Par commune ====
            cursor.execute(
                f"""
                SELECT
                    id_commune,
                    commune_nom,
                    COUNT(*) AS nb_ts,
                    COUNT(*) FILTER (WHERE {sql_true("protection_exist")}) AS nb_protegees,
                    COALESCE(SUM(pop_desservie), 0) AS pop_desservie
                FROM marts.vw_tete_source
                WHERE {where_sql}
                GROUP BY id_commune, commune_nom
                ORDER BY commune_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_commune"] = [
                {
                    "id_commune": r[0],
                    "commune_nom": r[1],
                    "nb_tetes_source": r[2] or 0,
                    "nb_protegees": r[3] or 0,
                    "pop_desservie": float(r[4]) if r[4] is not None else 0.0,
                }
                for r in rows
            ]

            # ==== 4) Par type de source ====
            cursor.execute(
                f"""
                SELECT
                    type_source,
                    type_source_label,
                    COUNT(*) AS nb_ts,
                    COUNT(*) FILTER (WHERE {sql_true("protection_exist")}) AS nb_protegees
                FROM marts.vw_tete_source
                WHERE {where_sql}
                GROUP BY type_source, type_source_label
                ORDER BY type_source_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_type_source = []
            for r in rows:
                code = r[0]
                label = r[1]
                if code is None:
                    continue
                by_type_source.append(
                    {
                        "type_source_code": code,
                        "type_source_label": label,
                        "nb_tetes_source": r[2] or 0,
                        "nb_protegees": r[3] or 0,
                    }
                )
            data["by_type_source"] = by_type_source

            # ==== 5) Par Ã©tat fonctionnel ====
            cursor.execute(
                f"""
                SELECT
                    etat_fonctionnel,
                    etat_fonctionnel_label,
                    COUNT(*) AS nb_ts
                FROM marts.vw_tete_source
                WHERE {where_sql}
                GROUP BY etat_fonctionnel, etat_fonctionnel_label
                ORDER BY etat_fonctionnel_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_etat = []
            for r in rows:
                code = r[0]
                label = r[1]
                if code is None:
                    continue
                by_etat.append(
                    {
                        "etat_fonctionnel_code": code,
                        "etat_fonctionnel_label": label,
                        "nb_tetes_source": r[2] or 0,
                    }
                )
            data["by_etat"] = by_etat

            # ==== 6) Par type de protection ====
            cursor.execute(
                f"""
                SELECT
                    u.code AS type_protection_code,
                    MAX(tp.label_fr) AS type_protection_label,
                    COUNT(DISTINCT ts.id_ts) AS nb_ts,
                    COUNT(DISTINCT ts.id_ts) FILTER (WHERE {sql_true("ts.protection_exist")}) AS nb_protegees
                FROM (
                    SELECT *
                    FROM marts.vw_tete_source
                    WHERE {where_sql}
                ) ts
                LEFT JOIN LATERAL unnest(ts.type_protection_codes) AS u(code) ON TRUE
                LEFT JOIN ref.type_protection tp ON tp.code = u.code
                GROUP BY u.code
                ORDER BY type_protection_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_type_protection = []
            for r in rows:
                code = r[0]
                label = r[1]
                if code is None:
                    continue
                by_type_protection.append(
                    {
                        "type_protection_code": code,
                        "type_protection_label": label,
                        "nb_tetes_source": r[2] or 0,
                        "nb_protegees": r[3] or 0,
                    }
                )
            data["by_type_protection"] = by_type_protection

            # ==== 7) Par annÃ©e de protection ====
            cursor.execute(
                f"""
                SELECT
                    annee_protection,
                    COUNT(*) AS nb_ts,
                    COUNT(*) FILTER (WHERE {sql_true("protection_exist")}) AS nb_protegees
                FROM marts.vw_tete_source
                WHERE {where_sql}
                GROUP BY annee_protection
                ORDER BY annee_protection
                """,
                params,
            )
            rows = cursor.fetchall()
            data["by_annee_protection"] = [
                {
                    "annee_protection": r[0],
                    "nb_tetes_source": r[1] or 0,
                    "nb_protegees": r[2] or 0,
                }
                for r in rows
            ]

            # âœ… ==== 8) AccÃ¨s eau potable (estimation) ====
            # A) pop_desservie par commune (sources protÃ©gÃ©es actives)
            cursor.execute(
                f"""
                SELECT
                    id_commune,
                    COALESCE(SUM(pop_desservie), 0) AS pop_desservie
                FROM marts.vw_tete_source
                WHERE {where_sql}
                  AND {sql_true("protection_exist")}
                  AND {sql_true("is_active")}
                  AND id_commune IS NOT NULL
                GROUP BY id_commune
                """,
                params,
            )
            rows = cursor.fetchall()
            desservie_map = {r[0]: float(r[1] or 0) for r in rows}

            # B) population de rÃ©fÃ©rence par commune (ref.localite)
            pop_where = ["1=1"]
            pop_params: list = []

            if region_id:
                pop_where.append("ac.id_region = %s")
                pop_params.append(region_id)

            if prefecture_id:
                pop_where.append("ac.id_prefecture = %s")
                pop_params.append(prefecture_id)

            if commune_id:
                pop_where.append("ac.id_commune = %s")
                pop_params.append(commune_id)

            pop_where_sql = " AND ".join(pop_where)

            cursor.execute(
                f"""
                SELECT
                    ac.id_commune,
                    ac.nom AS commune_nom,
                    COALESCE(SUM(l.population), 0) AS population
                FROM ref.admin_commune ac
                LEFT JOIN ref.localite l ON l.id_commune = ac.id_commune
                WHERE {pop_where_sql}
                GROUP BY ac.id_commune, ac.nom
                ORDER BY ac.nom
                """,
                pop_params,
            )
            pop_rows = cursor.fetchall()

            by_commune = []
            pop_total = 0.0
            pop_served_total = 0.0

            for (cid, cname, pop) in pop_rows:
                pop_val = float(pop or 0)
                dess_val = float(desservie_map.get(cid, 0.0))
                served = min(dess_val, pop_val) if pop_val > 0 else dess_val

                taux = round(100.0 * served / pop_val, 2) if pop_val > 0 else None

                by_commune.append(
                    {
                        "id_commune": cid,
                        "commune_nom": cname,
                        "population_reference": pop_val,
                        "pop_desservie": dess_val,
                        "taux_acces_pct": taux,
                    }
                )

                pop_total += pop_val
                pop_served_total += served

            taux_global = round(100.0 * pop_served_total / pop_total, 2) if pop_total > 0 else None

            data["access_eau_potable"] = {
                "population_reference_total": pop_total,
                "pop_desservie_total_cap": pop_served_total,
                "taux_acces_global_pct": taux_global,
                "by_commune": by_commune,
            }

            # Bonus aussi dans global (pratique cÃ´tÃ© front)
            data["global"]["population_reference_total"] = pop_total
            data["global"]["taux_acces_eau_potable_pct"] = taux_global

        return Response(data)



# -----------------------------------------------------------------------------
# Liste des zone dÃ©gradÃ©e
# -----------------------------------------------------------------------------

class ZoneDegradeeListView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Zones dÃ©gradÃ©es et restaurÃ©es (marts.vw_zone_degradee),
    filtrÃ©es par projet actif (project_code).

    Filtres possibles en query string :
    - ?search=...                    (id_zone, localite, type_degradation_label,
                                      type_intervention_labels, especes_labels,
                                      cause_detail, obs_degrad, obs_restaur,
                                      region_nom, commune_nom)
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...

    - ?type_degradation=...         (code ref.type_degradation)
    - ?severite=...                 (code ref.severite_degradation)
    - ?etat_restaur=...             (code ref.etat_restaur)
    - ?type_intervention_code=...   (code ref.type_intervention ; testÃ© dans type_intervention_codes)
    - ?espece_code=...              (code ref.espece_reboisement ; testÃ© dans especes_codes)

    - ?zone_degrad_pres=true|false
    - ?restauration_real=true|false
    - ?suivi_plantation=true|false

    - ?surface_degrad_min=...
    - ?surface_degrad_max=...
    - ?surface_restaur_min=...
    - ?surface_restaur_max=...
    - ?nb_plants_min=...
    - ?nb_plants_max=...
    - ?densite_plants_min=...
    - ?densite_plants_max=...
    - ?taux_survie_min=...
    - ?taux_survie_max=...
    - ?surf_regen_min=...
    - ?surf_regen_max=...
    - ?nb_terrasses_min=...
    - ?nb_terrasses_max=...
    - ?longueur_terr_min=...
    - ?longueur_terr_max=...

    - ?annee_plantation_from=...
    - ?annee_plantation_to=...

    - ?is_active=true|false
    - ?has_geom=true|false          (geom_point / geom_zone)
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc  # FIERE / AGRIECO

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        type_degradation = request.query_params.get("type_degradation")
        severite = request.query_params.get("severite")
        etat_restaur = request.query_params.get("etat_restaur")
        type_intervention_code = request.query_params.get("type_intervention_code")
        espece_code = request.query_params.get("espece_code")

        zone_degrad_pres = request.query_params.get("zone_degrad_pres")
        restauration_real = request.query_params.get("restauration_real")
        suivi_plantation = request.query_params.get("suivi_plantation")

        surface_degrad_min = request.query_params.get("surface_degrad_min")
        surface_degrad_max = request.query_params.get("surface_degrad_max")
        surface_restaur_min = request.query_params.get("surface_restaur_min")
        surface_restaur_max = request.query_params.get("surface_restaur_max")
        nb_plants_min = request.query_params.get("nb_plants_min")
        nb_plants_max = request.query_params.get("nb_plants_max")
        densite_plants_min = request.query_params.get("densite_plants_min")
        densite_plants_max = request.query_params.get("densite_plants_max")
        taux_survie_min = request.query_params.get("taux_survie_min")
        taux_survie_max = request.query_params.get("taux_survie_max")
        surf_regen_min = request.query_params.get("surf_regen_min")
        surf_regen_max = request.query_params.get("surf_regen_max")
        nb_terrasses_min = request.query_params.get("nb_terrasses_min")
        nb_terrasses_max = request.query_params.get("nb_terrasses_max")
        longueur_terr_min = request.query_params.get("longueur_terr_min")
        longueur_terr_max = request.query_params.get("longueur_terr_max")

        annee_plantation_from = request.query_params.get("annee_plantation_from")
        annee_plantation_to = request.query_params.get("annee_plantation_to")

        is_active = request.query_params.get("is_active")
        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # Typologie de dÃ©gradation / restauration
        if type_degradation:
            where_clauses.append("type_degradation = %s")
            params.append(type_degradation)

        if severite:
            where_clauses.append("severite = %s")
            params.append(severite)

        if etat_restaur:
            where_clauses.append("etat_restaur = %s")
            params.append(etat_restaur)

        if type_intervention_code:
            # code dans le tableau type_intervention_codes
            where_clauses.append("%s = ANY(type_intervention_codes)")
            params.append(type_intervention_code)

        if espece_code:
            # code dans le tableau especes_codes
            where_clauses.append("%s = ANY(especes_codes)")
            params.append(espece_code)

        # BoolÃ©ens
        if zone_degrad_pres in ("true", "false"):
            where_clauses.append(sql_bool("zone_degrad_pres", zone_degrad_pres == "true"))

        if restauration_real in ("true", "false"):
            where_clauses.append(sql_bool("restauration_real", restauration_real == "true"))

        if suivi_plantation in ("true", "false"):
            where_clauses.append(sql_bool("suivi_plantation", suivi_plantation == "true"))

        # NumÃ©riques : surfaces, plantations, terrasses, etc.
        if surface_degrad_min:
            where_clauses.append("surface_degrad_ha >= %s")
            params.append(surface_degrad_min)

        if surface_degrad_max:
            where_clauses.append("surface_degrad_ha <= %s")
            params.append(surface_degrad_max)

        if surface_restaur_min:
            where_clauses.append("surface_restaur_ha >= %s")
            params.append(surface_restaur_min)

        if surface_restaur_max:
            where_clauses.append("surface_restaur_ha <= %s")
            params.append(surface_restaur_max)

        if nb_plants_min:
            where_clauses.append("nb_plants >= %s")
            params.append(nb_plants_min)

        if nb_plants_max:
            where_clauses.append("nb_plants <= %s")
            params.append(nb_plants_max)

        if densite_plants_min:
            where_clauses.append("densite_plants_ha >= %s")
            params.append(densite_plants_min)

        if densite_plants_max:
            where_clauses.append("densite_plants_ha <= %s")
            params.append(densite_plants_max)

        if taux_survie_min:
            where_clauses.append("taux_survie_pct >= %s")
            params.append(taux_survie_min)

        if taux_survie_max:
            where_clauses.append("taux_survie_pct <= %s")
            params.append(taux_survie_max)

        if surf_regen_min:
            where_clauses.append("surf_regen_ha >= %s")
            params.append(surf_regen_min)

        if surf_regen_max:
            where_clauses.append("surf_regen_ha <= %s")
            params.append(surf_regen_max)

        if nb_terrasses_min:
            where_clauses.append("nb_terrasses >= %s")
            params.append(nb_terrasses_min)

        if nb_terrasses_max:
            where_clauses.append("nb_terrasses <= %s")
            params.append(nb_terrasses_max)

        if longueur_terr_min:
            where_clauses.append("longueur_terr_m >= %s")
            params.append(longueur_terr_min)

        if longueur_terr_max:
            where_clauses.append("longueur_terr_m <= %s")
            params.append(longueur_terr_max)

        # AnnÃ©e de plantation
        if annee_plantation_from:
            where_clauses.append("annee_plantation >= %s")
            params.append(annee_plantation_from)

        if annee_plantation_to:
            where_clauses.append("annee_plantation <= %s")
            params.append(annee_plantation_to)

        # Statut / gÃ©omÃ©trie
        if is_active in ("true", "false"):
            where_clauses.append(sql_bool("is_active", is_active == "true"))


        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("(geom_point IS NOT NULL OR geom_zone IS NOT NULL)")
            else:
                where_clauses.append("(geom_point IS NULL AND geom_zone IS NULL)")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "id_zone ILIKE %s OR "
                "localite ILIKE %s OR "
                "type_degradation_label ILIKE %s OR "
                "type_intervention_labels ILIKE %s OR "
                "especes_labels ILIKE %s OR "
                "cause_detail ILIKE %s OR "
                "obs_degrad ILIKE %s OR "
                "obs_restaur ILIKE %s OR "
                "region_nom ILIKE %s OR "
                "commune_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 10)

        where_sql = " AND ".join(where_clauses)

        # -------- Pagination --------
        paginator = self.pagination_class()
        page = request.query_params.get(paginator.page_query_param, 1)
        page_size = request.query_params.get(
            paginator.page_size_query_param,
            paginator.page_size,
        )

        try:
            page = int(page)
        except ValueError:
            page = 1

        try:
            page_size = int(page_size)
        except ValueError:
            page_size = paginator.page_size

        if page_size > paginator.max_page_size:
            page_size = paginator.max_page_size

        offset = (page - 1) * page_size
        limit = page_size

        # -------- SQL --------
        with connection.cursor() as cursor:
            # 1) Total
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM marts.vw_zone_degradee
                WHERE {where_sql}
                """,
                params,
            )
            total = cursor.fetchone()[0]

            # 2) Lignes paginÃ©es
            cursor.execute(
                f"""
                SELECT *
                FROM marts.vw_zone_degradee
                WHERE {where_sql}
                ORDER BY id_region NULLS LAST,
                         id_prefecture NULLS LAST,
                         id_commune NULLS LAST,
                         id_zone NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        results = [dict(zip(columns, row)) for row in rows]

        base_url = request.build_absolute_uri(request.path)
        next_page = None
        previous_page = None

        if offset + limit < total:
            next_page = f"{base_url}?page={page + 1}&page_size={page_size}"
        if page > 1:
            previous_page = f"{base_url}?page={page - 1}&page_size={page_size}"

        return Response(
            {
                "count": total,
                "next": next_page,
                "previous": previous_page,
                "results": results,
            }
        )


class ZoneDegradeeAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    AgrÃ©gations sur les zones dÃ©gradÃ©es/restaurÃ©es (marts.vw_zone_degradee),
    filtrÃ©es par projet actif (FIERE / AGRIECO).

    Filtres possibles en query string (mÃªmes que ZoneDegradeeListView) :
    - ?search=...
    - ?region_id=...
    - ?prefecture_id=...
    - ?commune_id=...
    - ?type_degradation=...
    - ?severite=...
    - ?etat_restaur=...
    - ?type_intervention_code=...
    - ?espece_code=...
    - ?zone_degrad_pres=true|false
    - ?restauration_real=true|false
    - ?suivi_plantation=true|false
    - ?surface_degrad_min=...
    - ?surface_degrad_max=...
    - ?surface_restaur_min=...
    - ?surface_restaur_max=...
    - ?nb_plants_min=...
    - ?nb_plants_max=...
    - ?densite_plants_min=...
    - ?densite_plants_max=...
    - ?taux_survie_min=...
    - ?taux_survie_max=...
    - ?surf_regen_min=...
    - ?surf_regen_max=...
    - ?nb_terrasses_min=...
    - ?nb_terrasses_max=...
    - ?longueur_terr_min=...
    - ?longueur_terr_max=...
    - ?annee_plantation_from=...
    - ?annee_plantation_to=...
    - ?is_active=true|false
    - ?has_geom=true|false
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        project_code = project.code_fonc
        data: dict = {}

        # -------- Filtres --------
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        type_degradation = request.query_params.get("type_degradation")
        severite = request.query_params.get("severite")
        etat_restaur = request.query_params.get("etat_restaur")
        type_intervention_code = request.query_params.get("type_intervention_code")
        espece_code = request.query_params.get("espece_code")

        zone_degrad_pres = request.query_params.get("zone_degrad_pres")
        restauration_real = request.query_params.get("restauration_real")
        suivi_plantation = request.query_params.get("suivi_plantation")

        surface_degrad_min = request.query_params.get("surface_degrad_min")
        surface_degrad_max = request.query_params.get("surface_degrad_max")
        surface_restaur_min = request.query_params.get("surface_restaur_min")
        surface_restaur_max = request.query_params.get("surface_restaur_max")
        nb_plants_min = request.query_params.get("nb_plants_min")
        nb_plants_max = request.query_params.get("nb_plants_max")
        densite_plants_min = request.query_params.get("densite_plants_min")
        densite_plants_max = request.query_params.get("densite_plants_max")
        taux_survie_min = request.query_params.get("taux_survie_min")
        taux_survie_max = request.query_params.get("taux_survie_max")
        surf_regen_min = request.query_params.get("surf_regen_min")
        surf_regen_max = request.query_params.get("surf_regen_max")
        nb_terrasses_min = request.query_params.get("nb_terrasses_min")
        nb_terrasses_max = request.query_params.get("nb_terrasses_max")
        longueur_terr_min = request.query_params.get("longueur_terr_min")
        longueur_terr_max = request.query_params.get("longueur_terr_max")

        annee_plantation_from = request.query_params.get("annee_plantation_from")
        annee_plantation_to = request.query_params.get("annee_plantation_to")

        is_active = request.query_params.get("is_active")
        has_geom = request.query_params.get("has_geom")
        search = request.query_params.get("search")

        where_clauses, params = build_access_scope_for_project(request, project_code)

        # Territoire
        if region_id:
            where_clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            where_clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            where_clauses.append("id_commune = %s")
            params.append(commune_id)

        # Typologie de dÃ©gradation / restauration
        if type_degradation:
            where_clauses.append("type_degradation = %s")
            params.append(type_degradation)

        if severite:
            where_clauses.append("severite = %s")
            params.append(severite)

        if etat_restaur:
            where_clauses.append("etat_restaur = %s")
            params.append(etat_restaur)

        if type_intervention_code:
            where_clauses.append("%s = ANY(type_intervention_codes)")
            params.append(type_intervention_code)

        if espece_code:
            where_clauses.append("%s = ANY(especes_codes)")
            params.append(espece_code)

        # BoolÃ©ens
        if zone_degrad_pres in ("true", "false"):
            where_clauses.append(sql_bool("zone_degrad_pres", zone_degrad_pres == "true"))

        if restauration_real in ("true", "false"):
            where_clauses.append(sql_bool("restauration_real", restauration_real == "true"))

        if suivi_plantation in ("true", "false"):
            where_clauses.append(sql_bool("suivi_plantation", suivi_plantation == "true"))

        # NumÃ©riques : surfaces, plantations, terrasses, etc.
        if surface_degrad_min:
            where_clauses.append("surface_degrad_ha >= %s")
            params.append(surface_degrad_min)

        if surface_degrad_max:
            where_clauses.append("surface_degrad_ha <= %s")
            params.append(surface_degrad_max)

        if surface_restaur_min:
            where_clauses.append("surface_restaur_ha >= %s")
            params.append(surface_restaur_min)

        if surface_restaur_max:
            where_clauses.append("surface_restaur_ha <= %s")
            params.append(surface_restaur_max)

        if nb_plants_min:
            where_clauses.append("nb_plants >= %s")
            params.append(nb_plants_min)

        if nb_plants_max:
            where_clauses.append("nb_plants <= %s")
            params.append(nb_plants_max)

        if densite_plants_min:
            where_clauses.append("densite_plants_ha >= %s")
            params.append(densite_plants_min)

        if densite_plants_max:
            where_clauses.append("densite_plants_ha <= %s")
            params.append(densite_plants_max)

        if taux_survie_min:
            where_clauses.append("taux_survie_pct >= %s")
            params.append(taux_survie_min)

        if taux_survie_max:
            where_clauses.append("taux_survie_pct <= %s")
            params.append(taux_survie_max)

        if surf_regen_min:
            where_clauses.append("surf_regen_ha >= %s")
            params.append(surf_regen_min)

        if surf_regen_max:
            where_clauses.append("surf_regen_ha <= %s")
            params.append(surf_regen_max)

        if nb_terrasses_min:
            where_clauses.append("nb_terrasses >= %s")
            params.append(nb_terrasses_min)

        if nb_terrasses_max:
            where_clauses.append("nb_terrasses <= %s")
            params.append(nb_terrasses_max)

        if longueur_terr_min:
            where_clauses.append("longueur_terr_m >= %s")
            params.append(longueur_terr_min)

        if longueur_terr_max:
            where_clauses.append("longueur_terr_m <= %s")
            params.append(longueur_terr_max)

        # AnnÃ©e de plantation
        if annee_plantation_from:
            where_clauses.append("annee_plantation >= %s")
            params.append(annee_plantation_from)

        if annee_plantation_to:
            where_clauses.append("annee_plantation <= %s")
            params.append(annee_plantation_to)

        # Statut / gÃ©omÃ©trie
        if is_active in ("true", "false"):
            where_clauses.append(sql_bool("is_active", is_active == "true"))


        if has_geom in ("true", "false"):
            if has_geom == "true":
                where_clauses.append("(geom_point IS NOT NULL OR geom_zone IS NOT NULL)")
            else:
                where_clauses.append("(geom_point IS NULL AND geom_zone IS NULL)")

        # Recherche texte
        if search:
            where_clauses.append(
                "("
                "id_zone ILIKE %s OR "
                "localite ILIKE %s OR "
                "type_degradation_label ILIKE %s OR "
                "type_intervention_labels ILIKE %s OR "
                "especes_labels ILIKE %s OR "
                "cause_detail ILIKE %s OR "
                "obs_degrad ILIKE %s OR "
                "obs_restaur ILIKE %s OR "
                "region_nom ILIKE %s OR "
                "commune_nom ILIKE %s"
                ")"
            )
            pattern = f"%{search}%"
            params.extend([pattern] * 10)

        where_sql = " AND ".join(where_clauses)

        with connection.cursor() as cursor:
            # ==== 1) GLOBAL ====
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS nb_zones,
                    COUNT(*) FILTER (WHERE {sql_true("zone_degrad_pres")}) AS nb_zones_degradees,
                    COUNT(*) FILTER (WHERE {sql_true("restauration_real")}) AS nb_zones_restaurees,
                    COUNT(*) FILTER (WHERE {sql_true("suivi_plantation")}) AS nb_zones_suivies,
                    SUM(surface_degrad_ha) AS surf_degrad_ha,
                    SUM(surface_restaur_ha) AS surf_restaur_ha,
                    SUM(surf_regen_ha) AS surf_regen_ha,
                    SUM(nb_plants) AS nb_plants_total,
                    AVG(densite_plants_ha) AS densite_moy_plants_ha,
                    AVG(taux_survie_pct) AS taux_survie_moy_pct,
                    SUM(nb_terrasses) AS nb_terrasses_total,
                    SUM(longueur_terr_m) AS longueur_terr_total_m,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN type_intervention_codes IS NOT NULL
                                     AND 'PLANTATION' = ANY(type_intervention_codes)
                                THEN COALESCE(surface_restaur_ha, 0)
                                ELSE 0
                            END
                        ),
                        0
                    ) AS surface_plantations_ha,
                    COUNT(*) FILTER (
                        WHERE (geom_point IS NOT NULL OR geom_zone IS NOT NULL)
                    ) AS nb_with_geom
                FROM marts.vw_zone_degradee
                WHERE {where_sql}
                """,
                params,
            )
            row = cursor.fetchone()
            nb_zones = row[0] or 0
            nb_with_geom = row[13] or 0

            data["global"] = {
                "nb_zones": nb_zones,
                "nb_zones_degradees": row[1] or 0,
                "nb_zones_restaurees": row[2] or 0,
                "nb_zones_suivies": row[3] or 0,
                "surface_degradee_ha": float(row[4]) if row[4] is not None else 0.0,
                "surface_restauree_ha": float(row[5]) if row[5] is not None else 0.0,
                "surface_regeneree_ha": float(row[6]) if row[6] is not None else 0.0,
                "nb_plants_total": int(row[7]) if row[7] is not None else 0,
                "nb_plants": int(row[7]) if row[7] is not None else 0,  # alias front
                "densite_moy_plants_ha": float(row[8]) if row[8] is not None else None,
                "taux_survie_moy_pct": float(row[9]) if row[9] is not None else None,
                "nb_terrasses_total": int(row[10]) if row[10] is not None else 0,
                "longueur_terr_total_m": float(row[11]) if row[11] is not None else 0.0,
                "surface_plantations_ha": float(row[12]) if row[12] is not None else 0.0,
                "nb_with_geom": nb_with_geom,
                "nb_without_geom": nb_zones - nb_with_geom,
            }

            # ==== 2) Par rÃ©gion ====
            cursor.execute(
                f"""
                SELECT
                    id_region,
                    region_nom,
                    COUNT(*) AS nb_zones,
                    COUNT(*) FILTER (WHERE {sql_true("zone_degrad_pres")}) AS nb_zones_degradees,
                    COUNT(*) FILTER (WHERE {sql_true("restauration_real")}) AS nb_zones_restaurees,
                    SUM(surface_degrad_ha) AS surf_degrad_ha,
                    SUM(surface_restaur_ha) AS surf_restaur_ha,
                    SUM(surf_regen_ha) AS surf_regen_ha
                FROM marts.vw_zone_degradee
                WHERE {where_sql}
                GROUP BY id_region, region_nom
                ORDER BY region_nom
                """,
                params,
            )
            rows = cursor.fetchall()
            by_region = []
            for r in rows:
                by_region.append(
                    {
                        "id_region": r[0],
                        "region_nom": r[1],
                        "nb_zones": r[2] or 0,
                        "nb_zones_degradees": r[3] or 0,
                        "nb_zones_restaurees": r[4] or 0,
                        "surface_degradee_ha": float(r[5]) if r[5] is not None else 0.0,
                        "surface_restauree_ha": float(r[6]) if r[6] is not None else 0.0,
                        "surface_regeneree_ha": float(r[7]) if r[7] is not None else 0.0,
                    }
                )
            data["by_region"] = by_region

            # ==== 3) Par type de dÃ©gradation ====
            cursor.execute(
                f"""
                SELECT
                    type_degradation,
                    type_degradation_label,
                    COUNT(*) AS nb_zones,
                    SUM(surface_degrad_ha) AS surf_degrad_ha,
                    SUM(surface_restaur_ha) AS surf_restaur_ha
                FROM marts.vw_zone_degradee
                WHERE {where_sql}
                GROUP BY type_degradation, type_degradation_label
                ORDER BY type_degradation_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_type_degradation = []
            for r in rows:
                by_type_degradation.append(
                    {
                        "type_degradation": r[0],
                        "type_degradation_label": r[1],
                        "nb_zones": r[2] or 0,
                        "surface_degradee_ha": float(r[3]) if r[3] is not None else 0.0,
                        "surface_restauree_ha": float(r[4]) if r[4] is not None else 0.0,
                    }
                )
            data["by_type_degradation"] = by_type_degradation

            # ==== 4) Par Ã©tat de restauration ====
            cursor.execute(
                f"""
                SELECT
                    etat_restaur,
                    etat_restaur_label,
                    COUNT(*) AS nb_zones,
                    SUM(surface_restaur_ha) AS surf_restaur_ha
                FROM marts.vw_zone_degradee
                WHERE {where_sql}
                GROUP BY etat_restaur, etat_restaur_label
                ORDER BY etat_restaur_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_etat_restaur = []
            for r in rows:
                by_etat_restaur.append(
                    {
                        "etat_restaur": r[0],
                        "etat_restaur_label": r[1],
                        "nb_zones": r[2] or 0,
                        "surface_restauree_ha": float(r[3]) if r[3] is not None else 0.0,
                    }
                )
            data["by_etat_restaur"] = by_etat_restaur

            # ==== 5) Par sÃ©vÃ©ritÃ© ====
            cursor.execute(
                f"""
                SELECT
                    severite,
                    severite_label,
                    COUNT(*) AS nb_zones,
                    SUM(surface_degrad_ha) AS surf_degrad_ha
                FROM marts.vw_zone_degradee
                WHERE {where_sql}
                GROUP BY severite, severite_label
                ORDER BY severite_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_severite = []
            for r in rows:
                by_severite.append(
                    {
                        "severite": r[0],
                        "severite_label": r[1],
                        "nb_zones": r[2] or 0,
                        "surface_degradee_ha": float(r[3]) if r[3] is not None else 0.0,
                    }
                )
            data["by_severite"] = by_severite

            # ==== 6) Par type dâ€™intervention (unnest) ====
            cursor.execute(
                f"""
                SELECT
                    u.code AS type_intervention_code,
                    MAX(ti.label_fr) AS type_intervention_label,
                    COUNT(DISTINCT zd.id_zone) AS nb_zones,
                    SUM(zd.surface_restaur_ha) AS surf_restaur_ha
                FROM (
                    SELECT *
                    FROM marts.vw_zone_degradee
                    WHERE {where_sql}
                ) zd
                LEFT JOIN LATERAL unnest(zd.type_intervention_codes) AS u(code) ON TRUE
                LEFT JOIN ref.type_intervention ti ON ti.code = u.code
                GROUP BY u.code
                ORDER BY type_intervention_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_type_intervention = []
            for r in rows:
                code = r[0]
                if code is None:
                    continue
                by_type_intervention.append(
                    {
                        "type_intervention_code": code,
                        "type_intervention_label": r[1],
                        "nb_zones": r[2] or 0,
                        "surface_restauree_ha": float(r[3]) if r[3] is not None else 0.0,
                    }
                )
            data["by_type_intervention"] = by_type_intervention

            # ==== 7) Par espÃ¨ce (unnest) ====
            cursor.execute(
                f"""
                SELECT
                    u.code AS espece_code,
                    MAX(e.label_fr) AS espece_label,
                    COUNT(DISTINCT zd.id_zone) AS nb_zones,
                    SUM(zd.nb_plants) AS nb_plants
                FROM (
                    SELECT *
                    FROM marts.vw_zone_degradee
                    WHERE {where_sql}
                ) zd
                LEFT JOIN LATERAL unnest(zd.especes_codes) AS u(code) ON TRUE
                LEFT JOIN ref.espece_reboisement e ON e.code = u.code
                GROUP BY u.code
                ORDER BY espece_label
                """,
                params,
            )
            rows = cursor.fetchall()
            by_espece = []
            for r in rows:
                code = r[0]
                if code is None:
                    continue
                by_espece.append(
                    {
                        "espece_code": code,
                        "espece_label": r[1],
                        "nb_zones": r[2] or 0,
                        "nb_plants": int(r[3]) if r[3] is not None else 0,
                    }
                )
            data["by_espece"] = by_espece

            # ==== 8) Par annÃ©e de plantation ====
            cursor.execute(
                f"""
                SELECT
                    annee_plantation,
                    COUNT(*) AS nb_zones,
                    SUM(surface_restaur_ha) AS surf_restaur_ha,
                    SUM(nb_plants) AS nb_plants
                FROM marts.vw_zone_degradee
                WHERE {where_sql}
                GROUP BY annee_plantation
                ORDER BY annee_plantation
                """,
                params,
            )
            rows = cursor.fetchall()
            by_annee_plantation = []
            for r in rows:
                by_annee_plantation.append(
                    {
                        "annee_plantation": r[0],
                        "nb_zones": r[1] or 0,
                        "surface_restauree_ha": float(r[2]) if r[2] is not None else 0.0,
                        "nb_plants": int(r[3]) if r[3] is not None else 0,
                    }
                )
            data["by_annee_plantation"] = by_annee_plantation

        return Response(data)


# -----------------------------------------------------------------------------
# Liste des BaseGeoJsonView
# -----------------------------------------------------------------------------

class BaseGeoJSONView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Vue gÃ©nÃ©rique pour exposer une table du schÃ©ma `ref` en GeoJSON.

    - retourne un FeatureCollection
    - les propriÃ©tÃ©s = toutes les colonnes sauf la gÃ©omÃ©trie
    """

    permission_classes = [IsAuthenticated]

    table_name = None      # ex: "ref.admin_region"
    id_column = "id"       # ex: "id_region"
    geom_column = "geom"   # nom de la colonne geometry
    default_ordering = None

    def build_where_clause(self, request):
        """
        Ã€ surcharger dans les classes filles.
        Retourne (where_sql, params).
        """
        return "TRUE", []

    def get(self, request):
        # On force un projet actif (FIERE / AGRIECO) pour rester dans la logique du dispositif
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        where_sql, params = self.build_where_clause(request)
        order_by = self.default_ordering or self.id_column

        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT jsonb_build_object(
                    'type', 'FeatureCollection',
                    'features', COALESCE(jsonb_agg(feature), '[]'::jsonb)
                )
                FROM (
                    SELECT jsonb_build_object(
                        'type', 'Feature',
                        'id', {self.id_column},
                        'geometry', ST_AsGeoJSON({self.geom_column})::jsonb,
                        'properties', to_jsonb(t) - {self.geom_column!r}
                    ) AS feature
                    FROM {self.table_name} AS t
                    WHERE {where_sql}
                    ORDER BY {order_by}
                ) AS features;
                """,
                params,
            )
            data = cursor.fetchone()[0] or {
                "type": "FeatureCollection",
                "features": [],
            }

        return Response(data)


# -----------------------------------------------------------------------------
# Limite Ref base administrative
# -----------------------------------------------------------------------------

class AdminRegionGeoJSONView(BaseGeoJSONView):
    """
    ref.admin_region
    Filtres :
    - ?region_id=
    """
    table_name = "ref.admin_region"
    id_column = "id_region"
    geom_column = "geom"
    default_ordering = "nom"

    def build_where_clause(self, request):
        region_id = request.query_params.get("region_id")

        if region_id:
            return "id_region = %s", [region_id]

        return "TRUE", []


class AdminPrefectureGeoJSONView(BaseGeoJSONView):
    """
    ref.admin_prefecture
    Filtres :
    - ?region_id=
    - ?prefecture_id=
    """
    table_name = "ref.admin_prefecture"
    id_column = "id_prefecture"
    geom_column = "geom"
    default_ordering = "nom"

    def build_where_clause(self, request):
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")

        clauses = []
        params = []

        if region_id:
            clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if not clauses:
            return "TRUE", []

        return " AND ".join(clauses), params


class AdminCommuneGeoJSONView(BaseGeoJSONView):
    """
    ref.admin_commune
    Filtres :
    - ?region_id=
    - ?prefecture_id=
    - ?commune_id=
    """
    table_name = "ref.admin_commune"
    id_column = "id_commune"
    geom_column = "geom"
    default_ordering = "nom"

    def build_where_clause(self, request):
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        clauses = []
        params = []

        if region_id:
            clauses.append("id_region = %s")
            params.append(region_id)

        if prefecture_id:
            clauses.append("id_prefecture = %s")
            params.append(prefecture_id)

        if commune_id:
            clauses.append("id_commune = %s")
            params.append(commune_id)

        if not clauses:
            return "TRUE", []

        return " AND ".join(clauses), params



# -----------------------------------------------------------------------------
# CommuneBasesGeo
# -----------------------------------------------------------------------------

class CommuneBasedGeoJSONView(BaseGeoJSONView):
    """
    Pour les tables avec une colonne id_commune (FK -> ref.admin_commune.id_commune).

    Filtres :
    - ?commune_id=
    - ?prefecture_id=
    - ?region_id=
    """

    def build_where_clause(self, request):
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        clauses = []
        params = []

        if commune_id:
            clauses.append("id_commune = %s")
            params.append(commune_id)

        if prefecture_id:
            clauses.append(
                "id_commune IN ("
                "  SELECT id_commune FROM ref.admin_commune "
                "  WHERE id_prefecture = %s"
                ")"
            )
            params.append(prefecture_id)

        if region_id:
            clauses.append(
                "id_commune IN ("
                "  SELECT id_commune FROM ref.admin_commune "
                "  WHERE id_region = %s"
                ")"
            )
            params.append(region_id)

        if not clauses:
            return "TRUE", []

        return " AND ".join(clauses), params


# -----------------------------------------------------------------------------
# Couches 
# -----------------------------------------------------------------------------

class AgglomerationGeoJSONView(CommuneBasedGeoJSONView):
    """
    ref.agglomeration
    """
    table_name = "ref.agglomeration"
    id_column = "agglom_id"
    geom_column = "geom"
    default_ordering = "nom"


class AireProtegeeGeoJSONView(CommuneBasedGeoJSONView):
    """
    ref.aire_protegee
    """
    table_name = "ref.aire_protegee"
    id_column = "ap_id"
    geom_column = "geom"
    default_ordering = "nom"


class EquipementGeoJSONView(CommuneBasedGeoJSONView):
    """
    ref.equipement
    """
    table_name = "ref.equipement"
    id_column = "equip_id"
    geom_column = "geom"
    default_ordering = "nom"


class HabitationDisperseeGeoJSONView(CommuneBasedGeoJSONView):
    """
    ref.habitation_dispersee
    """
    table_name = "ref.habitation_dispersee"
    id_column = "hab_id"
    geom_column = "geom"
    default_ordering = "nom"


class LocaliteGeoJSONView(CommuneBasedGeoJSONView):
    """
    ref.localite
    """
    table_name = "ref.localite"
    id_column = "localite_id"
    geom_column = "geom"
    default_ordering = "nom"


class OccupationSolGeoJSONView(CommuneBasedGeoJSONView):
    """
    ref.occupation_sol
    """
    table_name = "ref.occupation_sol"
    id_column = "occsol_id"
    geom_column = "geom"
    default_ordering = "code_2020"


class ZoneHumideGeoJSONView(CommuneBasedGeoJSONView):
    """
    ref.zone_humide
    """
    table_name = "ref.zone_humide"
    id_column = "zh_id"
    geom_column = "geom"
    default_ordering = "nom"


class ZoneSableuseGeoJSONView(CommuneBasedGeoJSONView):
    """
    ref.zone_sableuse
    """
    table_name = "ref.zone_sableuse"
    id_column = "zs_id"
    geom_column = "geom"
    default_ordering = "nom"



# -----------------------------------------------------------------------------
# Route & hydro
# -----------------------------------------------------------------------------

class LinearGeoJSONView(BaseGeoJSONView):
    """
    Base pour les tables linÃ©aires sans id_commune (hydro, routes).
    Filtres spatiaux :
    - ?commune_id=
    - ?prefecture_id=
    - ?region_id=
    via ST_Intersects(t.geom, geom admin)
    """

    def build_where_clause(self, request):
        region_id = request.query_params.get("region_id")
        prefecture_id = request.query_params.get("prefecture_id")
        commune_id = request.query_params.get("commune_id")

        clauses = []
        params = []

        if commune_id:
            clauses.append(
                "EXISTS ("
                "  SELECT 1 FROM ref.admin_commune c "
                "  WHERE c.id_commune = %s "
                "    AND ST_Intersects(t.geom, c.geom)"
                ")"
            )
            params.append(commune_id)

        if prefecture_id:
            clauses.append(
                "EXISTS ("
                "  SELECT 1 FROM ref.admin_prefecture p "
                "  WHERE p.id_prefecture = %s "
                "    AND ST_Intersects(t.geom, p.geom)"
                ")"
            )
            params.append(prefecture_id)

        if region_id:
            clauses.append(
                "EXISTS ("
                "  SELECT 1 FROM ref.admin_region r "
                "  WHERE r.id_region = %s "
                "    AND ST_Intersects(t.geom, r.geom)"
                ")"
            )
            params.append(region_id)

        if not clauses:
            return "TRUE", []

        return " AND ".join(clauses), params


class HydrographieGeoJSONView(LinearGeoJSONView):
    """
    ref.hydrographie
    """
    table_name = "ref.hydrographie"
    id_column = "hydro_id"
    geom_column = "geom"
    default_ordering = "nom"  # ou 'hydro_id' si tu prÃ©fÃ¨res


class ReseauRoutierGeoJSONView(LinearGeoJSONView):
    """
    ref.reseau_routier
    """
    table_name = "ref.reseau_routier"
    id_column = "route_id"
    geom_column = "geom"
    default_ordering = "highway"



# -----------------------------------------------------------------------------
# Liste des parcelles
# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# Liste des parcelles
# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# Liste des parcelles
# -----------------------------------------------------------------------------





