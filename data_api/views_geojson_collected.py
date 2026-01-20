# data_api/views_geojson_collected.py

"""
Vues GeoJSON pour les données collectées (schéma core/marts).
Expose les entités géographiques des projets AGRIECO et FIERE.

Version PRO :
- Les données projet exigent un projet actif (header X-Project-Code valide)
- Les utilisateurs non-admin sont automatiquement filtrés sur leur région (accounts_user.region_id)
- Admin/staff peut surcharger via ?region=GN005 (sinon toutes)
"""

from django.http import JsonResponse
from django.db import connection
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
import json


class BaseGeoJSONView(APIView):
    permission_classes = [IsAuthenticated]

    layer_name = "features"

    def get_current_project_code(self, request) -> str:
        # Middleware : request.current_project (RefProject) OU header direct
        proj = getattr(request, "current_project", None)
        if proj and getattr(proj, "code_fonc", None):
            return proj.code_fonc
        return request.headers.get("X-Project-Code") or request.query_params.get("project", "") or ""

    def get_current_region(self, request) -> str:
        # Middleware : request.current_region_id
        return getattr(request, "current_region_id", "") or ""

    def require_project(self, request) -> str:
        code = self.get_current_project_code(request)
        if not code:
            return ""
        return code

    def execute_geojson(self, sql: str, params: list, name: str):
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            cols = [c[0] for c in cursor.description]

        feats = []
        for row in rows:
            d = dict(zip(cols, row))
            geom_json = d.pop("geom_json", None)
            if not geom_json:
                continue
            try:
                geom = json.loads(geom_json) if isinstance(geom_json, str) else geom_json
            except Exception:
                continue
            props = {k: v for k, v in d.items() if k not in ("geom", "geometry", "geom_json")}
            feats.append({"type": "Feature", "geometry": geom, "properties": props})

        return JsonResponse({"type": "FeatureCollection", "name": name, "features": feats})


# ============================================================
# AGRIECO (marts.*)
# ============================================================

class CepParcellesGeoJSONView(BaseGeoJSONView):
    layer_name = "cep_parcelles"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)
        if not project_code:
            return JsonResponse({
                "detail": "Aucun projet actif (header X-Project-Code manquant ou invalide).",
                "hint": "Ajoutez le header X-Project-Code avec la valeur du code_fonc du projet (ex: FIERE ou AGRIECO)."
            }, status=400)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region and not (request.user.is_staff or request.user.is_superuser):
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                id_parcelle,
                id_cep,
                filiere_label,
                surface_decl AS surface_decl_ha,
                rendement AS rendement_kg_ha,
                campagne_yyyy as campagne,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_cep_parcelle
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class TeteSourceGeoJSONView(BaseGeoJSONView):
    layer_name = "tetes_sources"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)
        if not project_code:
            return JsonResponse({"detail": "Header X-Project-Code requis."}, status=400)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region and not (request.user.is_staff or request.user.is_superuser):
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                id_source,
                nom_source as nom,
                est_protegee,
                type_protection,
                etat_fonctionnel,
                debit_estime_ls,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_tete_source
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class OuvragesGeoJSONView(BaseGeoJSONView):
    layer_name = "ouvrages"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)
        if not project_code:
            return JsonResponse({"detail": "Header X-Project-Code requis."}, status=400)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region and not (request.user.is_staff or request.user.is_superuser):
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                id_ouvrage,
                type_ouvrage,
                etat_ouvrage as etat,
                longueur_m,
                largeur_m,
                annee_realisation,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_ouvrage
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class CouloirsGeoJSONView(BaseGeoJSONView):
    layer_name = "couloirs"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)
        if not project_code:
            return JsonResponse({"detail": "Header X-Project-Code requis."}, status=400)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region and not (request.user.is_staff or request.user.is_superuser):
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                id_couloir,
                nom_couloir as nom,
                type_couloir,
                statut_couloir,
                longueur_km,
                largeur_moyenne_m,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_couloir
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class ZonesDegradeesGeoJSONView(BaseGeoJSONView):
    layer_name = "zones_degradees"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)
        if not project_code:
            return JsonResponse({"detail": "Header X-Project-Code requis."}, status=400)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region and not (request.user.is_staff or request.user.is_superuser):
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                id_zone,
                type_degradation,
                surface_degradee_ha,
                surface_restauree_ha,
                surface_regeneree_ha,
                annee_intervention,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_zone_degradee
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class OrganisationsGeoJSONView(BaseGeoJSONView):
    layer_name = "organisations"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)
        if not project_code:
            return JsonResponse({"detail": "Header X-Project-Code requis."}, status=400)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region and not (request.user.is_staff or request.user.is_superuser):
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                id_org,
                nom_org,
                type_org,
                statut,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_organisation
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class MenagesGeoJSONView(BaseGeoJSONView):
    layer_name = "menages"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)
        if not project_code:
            return JsonResponse({"detail": "Header X-Project-Code requis."}, status=400)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region and not (request.user.is_staff or request.user.is_superuser):
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                id_menage,
                nb_personnes,
                activite_principale,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_menage
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class SitesGeoJSONView(BaseGeoJSONView):
    layer_name = "sites"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)
        if not project_code:
            return JsonResponse({"detail": "Header X-Project-Code requis."}, status=400)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region and not (request.user.is_staff or request.user.is_superuser):
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                id_site,
                nom_site,
                type_site,
                statut_site,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_site
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class PratiquesParcellesGeoJSONView(BaseGeoJSONView):
    layer_name = "pratiques_parcelles"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)
        if not project_code:
            return JsonResponse({"detail": "Header X-Project-Code requis."}, status=400)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region and not (request.user.is_staff or request.user.is_superuser):
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                id_pratique,
                type_pratique,
                description,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_pratique_parcelle
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


# ============================================================
# FIERE (marts.*)
# ============================================================

class EntreprisesEmploiGeoJSONView(BaseGeoJSONView):
    layer_name = "entreprises_emploi"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)
        if not project_code:
            return JsonResponse({"detail": "Header X-Project-Code requis."}, status=400)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region and not (request.user.is_staff or request.user.is_superuser):
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                id_entreprise,
                nom_entreprise,
                secteur,
                nb_emplois,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_entreprise_emploi
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class EntreprisesInsertionGeoJSONView(BaseGeoJSONView):
    layer_name = "entreprises_insertion"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)
        if not project_code:
            return JsonResponse({"detail": "Header X-Project-Code requis."}, status=400)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region and not (request.user.is_staff or request.user.is_superuser):
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                id_entreprise,
                nom_entreprise,
                filiere,
                nb_insertion,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_entreprise_insertion
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class ClustersGeoJSONView(BaseGeoJSONView):
    layer_name = "clusters"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)
        if not project_code:
            return JsonResponse({"detail": "Header X-Project-Code requis."}, status=400)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region and not (request.user.is_staff or request.user.is_superuser):
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                id_cluster,
                nom_cluster,
                filiere,
                statut,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_cluster
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


# ============================================================
# Utilitaire
# ============================================================

class AllLayersGeoJSONView(BaseGeoJSONView):
    """Retourne un résumé très léger pour debug (pas les géométries)."""
    layer_name = "all_layers"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)
        if not project_code:
            return JsonResponse({"detail": "Header X-Project-Code requis."}, status=400)

        region = self.get_current_region(request)

        # Exemple : renvoyer les compteurs par couche (utile pour diagnostiquer)
        # On s'appuie sur les vues marts.* si elles existent.
        q = []
        params = []
        for name, view in [
            ("cep_parcelles", "marts.vw_cep_parcelle"),
            ("tete_source", "marts.vw_tete_source"),
            ("ouvrage", "marts.vw_ouvrage"),
            ("couloir", "marts.vw_couloir"),
            ("zone_degradee", "marts.vw_zone_degradee"),
        ]:
            if region and not (request.user.is_staff or request.user.is_superuser):
                q.append(f"SELECT '{name}' as layer, count(*)::int as count FROM {view} WHERE project_code=%s AND id_region=%s")
                params.extend([project_code, region])
            else:
                q.append(f"SELECT '{name}' as layer, count(*)::int as count FROM {view} WHERE project_code=%s")
                params.append(project_code)

        sql = " UNION ALL ".join(q)

        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()

        return JsonResponse({"project": project_code, "region": region or None, "counts": [{"layer": r[0], "count": r[1]} for r in rows]})



# ============================================================
# Alias de compatibilité pour éviter les ImportError dans urls
# ============================================================

# si la vue "stations meteo" existe sous un autre nom, on la mappe ici
if "MeteoStationsGeoJSONView" not in globals():
    # variantes possibles
    if "MeteoStationGeoJSONView" in globals():
        MeteoStationsGeoJSONView = MeteoStationGeoJSONView
    elif "MeteoStationGeoJsonView" in globals():
        MeteoStationsGeoJSONView = MeteoStationGeoJsonView
    elif "MeteoStationsGeoJsonView" in globals():
        MeteoStationsGeoJSONView = MeteoStationsGeoJsonView
