# data_api/views_geojson_ref.py

"""
Vues GeoJSON pour les données référentielles (schéma ref).
Expose les limites administratives, environnement, infrastructures.

Version PRO :
- Pas de X-Project-Code requis (couches communes)
- Filtrage région AUTOMATIQUE via request.current_region_id (profil user)
- Admin/staff peut surcharger via ?region=GN005
"""

from django.http import JsonResponse
from django.db import connection
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
import json


class BaseRefGeoJSONView(APIView):
    """
    Classe de base pour les vues GeoJSON référentielles.
    PAS de vérification de projet - données communes à tous les projets.
    """
    permission_classes = [IsAuthenticated]

    sql_query = ""
    layer_name = "features"

    def get_region(self, request) -> str:
        """Région effective : profil user pour les non-admin, override possible pour admin/staff."""
        user = getattr(request, "user", None)

        # admin/staff peut passer ?region=...
        if user and user.is_authenticated and (user.is_staff or user.is_superuser):
            return request.query_params.get("region", "") or (getattr(request, "current_region_id", "") or "")

        # non-admin : région imposée depuis middleware (profil user)
        return (getattr(request, "current_region_id", "") or "")

    def get_sql_query(self, request):
        return self.sql_query

    def get(self, request, *args, **kwargs):
        try:
            sql = self.get_sql_query(request)

            if not sql:
                return JsonResponse({
                    "type": "FeatureCollection",
                    "name": self.layer_name,
                    "features": []
                })

            with connection.cursor() as cursor:
                cursor.execute(sql)
                rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description]

            features = []
            for row in rows:
                row_dict = dict(zip(columns, row))

                geom_json = row_dict.pop('geom_json', None)
                if not geom_json:
                    continue

                try:
                    geometry = json.loads(geom_json) if isinstance(geom_json, str) else geom_json
                except (json.JSONDecodeError, TypeError):
                    continue

                properties = {k: v for k, v in row_dict.items()
                              if k not in ('geom', 'geometry', 'geom_json')}

                features.append({
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": properties
                })

            return JsonResponse({
                "type": "FeatureCollection",
                "name": self.layer_name,
                "features": features
            })

        except Exception as e:
            print(f"Erreur GeoJSON {self.layer_name}: {e}")
            return JsonResponse({
                "type": "FeatureCollection",
                "name": self.layer_name,
                "features": [],
                "error": str(e)
            }, status=200)


# ============================================================
# LIMITES ADMINISTRATIVES
# ============================================================

class AdminRegionGeoJSONView(BaseRefGeoJSONView):
    """Régions administratives"""
    layer_name = "regions"

    def get_sql_query(self, request):
        region = self.get_region(request)

        where_clause = "WHERE geom IS NOT NULL"
        # Si on veut filtrer par région, table = admin_region => clé id_region (GN00X)
        if region:
            where_clause += f" AND id_region = '{region}'"

        return f"""
            SELECT
                id_region,
                nom AS nom_region,
                ref_name,
                admin0_nom AS pays,
                admin0_pcod AS code_pays,
                shape_area AS superficie,
                shape_leng AS perimetre,
                ST_AsGeoJSON(geom) as geom_json
            FROM ref.admin_region
            {where_clause}
        """


class AdminPrefectureGeoJSONView(BaseRefGeoJSONView):
    """Préfectures"""
    layer_name = "prefectures"

    def get_sql_query(self, request):
        region = self.get_region(request)

        where_clause = "WHERE p.geom IS NOT NULL"
        if region:
            where_clause += f" AND p.id_region = '{region}'"

        return f"""
            SELECT
                p.id_prefecture,
                p.nom AS nom_prefecture,
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
            {where_clause}
        """


class AdminCommuneGeoJSONView(BaseRefGeoJSONView):
    """Communes / Sous-préfectures"""
    layer_name = "communes"

    def get_sql_query(self, request):
        region = self.get_region(request)
        # Admin peut filtrer une prefecture ; non-admin => on ignore param si pas admin
        user = getattr(request, "user", None)
        prefecture = request.query_params.get('prefecture', '') if (user and user.is_authenticated and (user.is_staff or user.is_superuser)) else ''

        where_clause = "WHERE c.geom IS NOT NULL"
        if region:
            where_clause += f" AND c.id_region = '{region}'"
        if prefecture:
            where_clause += f" AND c.id_prefecture = '{prefecture}'"

        return f"""
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
            {where_clause}
        """


# ============================================================
# ENVIRONNEMENT
# ============================================================

class AireProtegeeGeoJSONView(BaseRefGeoJSONView):
    """Aires protégées"""
    layer_name = "aires_protegees"

    def get_sql_query(self, request):
        region = self.get_region(request)

        where_clause = "WHERE ap.geom IS NOT NULL"
        if region:
            where_clause += f" AND c.id_region = '{region}'"

        return f"""
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
            {where_clause}
        """


class ZoneHumideGeoJSONView(BaseRefGeoJSONView):
    """Zones humides"""
    layer_name = "zones_humides"

    def get_sql_query(self, request):
        region = self.get_region(request)

        where_clause = "WHERE zh.geom IS NOT NULL"
        if region:
            where_clause += f" AND c.id_region = '{region}'"

        return f"""
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
            {where_clause}
        """


class ZoneSableuseGeoJSONView(BaseRefGeoJSONView):
    """Zones sableuses"""
    layer_name = "zones_sableuses"

    def get_sql_query(self, request):
        region = self.get_region(request)

        where_clause = "WHERE zs.geom IS NOT NULL"
        if region:
            where_clause += f" AND c.id_region = '{region}'"

        return f"""
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
            {where_clause}
        """


class HydrographieGeoJSONView(BaseRefGeoJSONView):
    """Cours d'eau (hydrographie) - Lignes"""
    layer_name = "hydrographie"

    def get_sql_query(self, request):
        region = self.get_region(request)

        # hydrographie n'est pas joinée aux communes dans ton schéma
        # => pas de filtre région ici (à moins d'ajouter une jointure spatiale plus lourde)
        return """
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


class OccupationSolGeoJSONView(BaseRefGeoJSONView):
    """Occupation du sol"""
    layer_name = "occupation_sol"

    def get_sql_query(self, request):
        region = self.get_region(request)

        where_clause = "WHERE os.geom IS NOT NULL"
        if region:
            where_clause += f" AND c.id_region = '{region}'"

        return f"""
            SELECT
                os.occsol_id,
                os.code_2020 AS code,
                os.classe,
                c.nom AS nom_commune,
                os.id_commune,
                ST_AsGeoJSON(os.geom) as geom_json
            FROM ref.occupation_sol os
            LEFT JOIN ref.admin_commune c ON os.id_commune = c.id_commune
            {where_clause}
            LIMIT 5000
        """


# ============================================================
# INFRASTRUCTURES
# ============================================================

class ReseauRoutierGeoJSONView(BaseRefGeoJSONView):
    """Réseau routier"""
    layer_name = "reseau_routier"

    def get_sql_query(self, request):
        return """
            SELECT
                id,
                name AS nom,
                highway AS type_route,
                surface,
                oneway,
                bridge AS pont,
                tunnel,
                ST_AsGeoJSON(geom) as geom_json
            FROM ref.reseau_routier
            WHERE geom IS NOT NULL
            LIMIT 10000
        """


class EquipementGeoJSONView(BaseRefGeoJSONView):
    """Équipements (écoles, centres de santé, marchés, etc.)"""
    layer_name = "equipements"

    def get_sql_query(self, request):
        user = getattr(request, "user", None)
        type_equip = request.query_params.get('type', '') if (user and user.is_authenticated and (user.is_staff or user.is_superuser)) else ''
        region = self.get_region(request)

        where_clause = "WHERE e.geom IS NOT NULL"
        if type_equip:
            where_clause += f" AND e.fclass = '{type_equip}'"
        if region:
            where_clause += f" AND c.id_region = '{region}'"

        return f"""
            SELECT
                e.equip_id,
                e.nom,
                e.nom_fr,
                e.fclass AS type_equipement,
                e.code,
                c.nom AS nom_commune,
                e.id_commune,
                p.nom AS nom_prefecture,
                r.nom AS nom_region,
                ST_AsGeoJSON(e.geom) as geom_json
            FROM ref.equipement e
            LEFT JOIN ref.admin_commune c ON e.id_commune = c.id_commune
            LEFT JOIN ref.admin_prefecture p ON c.id_prefecture = p.id_prefecture
            LEFT JOIN ref.admin_region r ON c.id_region = r.id_region
            {where_clause}
        """


class LocaliteGeoJSONView(BaseRefGeoJSONView):
    """Localités / Villages"""
    layer_name = "localites"

    def get_sql_query(self, request):
        region = self.get_region(request)

        where_clause = "WHERE l.geom IS NOT NULL"
        if region:
            where_clause += f" AND c.id_region = '{region}'"

        return f"""
            SELECT
                l.localite_id,
                l.nom,
                l.nom_fr,
                l.fclass AS type_localite,
                l.population,
                c.nom AS nom_commune,
                l.id_commune,
                p.nom AS nom_prefecture,
                r.nom AS nom_region,
                ST_AsGeoJSON(l.geom) as geom_json
            FROM ref.localite l
            LEFT JOIN ref.admin_commune c ON l.id_commune = c.id_commune
            LEFT JOIN ref.admin_prefecture p ON c.id_prefecture = p.id_prefecture
            LEFT JOIN ref.admin_region r ON c.id_region = r.id_region
            {where_clause}
            LIMIT 5000
        """


class AgglomerationGeoJSONView(BaseRefGeoJSONView):
    """Agglomérations / Villes"""
    layer_name = "agglomerations"

    def get_sql_query(self, request):
        region = self.get_region(request)

        where_clause = "WHERE a.geom IS NOT NULL"
        if region:
            where_clause += f" AND c.id_region = '{region}'"

        return f"""
            SELECT
                a.agglom_id,
                a.nom,
                a.nom_fr,
                a.fclass AS type_agglo,
                a.code,
                c.nom AS nom_commune,
                a.id_commune,
                p.nom AS nom_prefecture,
                r.nom AS nom_region,
                ST_AsGeoJSON(a.geom) as geom_json
            FROM ref.agglomeration a
            LEFT JOIN ref.admin_commune c ON a.id_commune = c.id_commune
            LEFT JOIN ref.admin_prefecture p ON c.id_prefecture = p.id_prefecture
            LEFT JOIN ref.admin_region r ON c.id_region = r.id_region
            {where_clause}
        """


class HabitationDisperseeGeoJSONView(BaseRefGeoJSONView):
    """Habitations dispersées"""
    layer_name = "habitations_dispersees"

    def get_sql_query(self, request):
        region = self.get_region(request)

        where_clause = "WHERE h.geom IS NOT NULL"
        if region:
            where_clause += f" AND c.id_region = '{region}'"

        return f"""
            SELECT
                h.hab_id,
                h.nom,
                h.fclass,
                h.type_bat,
                c.nom AS nom_commune,
                h.id_commune,
                p.nom AS nom_prefecture,
                r.nom AS nom_region,
                ST_AsGeoJSON(h.geom) as geom_json
            FROM ref.habitation_dispersee h
            LEFT JOIN ref.admin_commune c ON h.id_commune = c.id_commune
            LEFT JOIN ref.admin_prefecture p ON c.id_prefecture = p.id_prefecture
            LEFT JOIN ref.admin_region r ON c.id_region = r.id_region
            {where_clause}
            LIMIT 3000
        """
