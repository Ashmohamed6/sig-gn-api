# data_api/views_geojson_collected.py

"""
Vues GeoJSON pour les donnees collectees (schema core/marts).
Expose les entites geographiques des projets AGRIECO et FIERE.
"""

import json

from django.db import connection
from django.http import JsonResponse
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from .mixins import CurrentProjectRequiredMixin


class BaseGeoJSONView(CurrentProjectRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    layer_name = "features"

    def _is_platform_admin(self, request) -> bool:
        user = getattr(request, "user", None)
        role = str(getattr(user, "role", "") or "").strip().lower()
        return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser or role in {"admin", "project_manager"}))

    def get_current_region(self, request) -> str:
        user = getattr(request, "user", None)
        region = (
            getattr(request, "current_region_id", "")
            or getattr(user, "region_id", "")
            or ""
        )
        if self._is_platform_admin(request):
            # Admin global: region optionnelle seulement.
            return request.query_params.get("region", "") or ""

        if not region:
            raise PermissionDenied("Aucune region assignee a votre compte.")
        return region

    def _extract_error_detail(self, error_response) -> str:
        data = getattr(error_response, "data", None)
        if isinstance(data, dict):
            return str(data.get("detail") or data.get("hint") or "Projet invalide")
        return str(data or "Projet invalide")

    def require_project(self, request) -> str:
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            detail = self._extract_error_detail(error_response)
            if getattr(error_response, "status_code", 400) == 403:
                raise PermissionDenied(detail)
            raise ValidationError(detail)

        return project.code_fonc

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

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            WITH base AS (
                SELECT
                    cep_uuid,
                    id_cep AS code_parcelle,
                    filiere_label AS culture_principale,
                    surface_decl AS superficie_ha,
                    rendement AS rendement_kg_ha,
                    campagne_yyyy AS campagne,
                    commune_nom,
                    prefecture_nom,
                    region_nom,
                    geom
                FROM marts.vw_cep_parcelle
                {where}
            ),
            enriched AS (
                SELECT
                    *,
                    ST_Area(geom::geography) AS geom_area_m2,
                    CASE
                        WHEN superficie_ha IS NOT NULL AND superficie_ha > 0
                            THEN GREATEST(superficie_ha * 10000.0, 25.0)
                        ELSE NULL
                    END AS area_decl_m2,
                    ST_Transform(ST_PointOnSurface(geom), 3857) AS centroid_3857
                FROM base
                WHERE geom IS NOT NULL
            ),
            normalized AS (
                SELECT
                    cep_uuid,
                    code_parcelle,
                    culture_principale,
                    superficie_ha,
                    rendement_kg_ha,
                    campagne,
                    commune_nom,
                    prefecture_nom,
                    region_nom,
                    CASE
                        WHEN area_decl_m2 IS NULL THEN geom
                        WHEN geom_area_m2 BETWEEN area_decl_m2 * 0.35 AND area_decl_m2 * 2.5 THEN geom
                        ELSE ST_Transform(
                            ST_Rotate(
                                ST_MakeEnvelope(
                                    ST_X(centroid_3857) - (sqrt(area_decl_m2 * 1.8) / 2.0),
                                    ST_Y(centroid_3857) - (sqrt(area_decl_m2 / 1.8) / 2.0),
                                    ST_X(centroid_3857) + (sqrt(area_decl_m2 * 1.8) / 2.0),
                                    ST_Y(centroid_3857) + (sqrt(area_decl_m2 / 1.8) / 2.0),
                                    3857
                                ),
                                radians(
                                    (
                                        (
                                            abs(hashtext(COALESCE(cep_uuid::text, code_parcelle, '0'))::bigint)
                                            %% 60
                                        ) - 30
                                    )::double precision
                                ),
                                centroid_3857
                            ),
                            4326
                        )::geometry(Polygon, 4326)
                    END AS geom_display,
                    CASE
                        WHEN area_decl_m2 IS NULL THEN 'Levee terrain'
                        WHEN geom_area_m2 BETWEEN area_decl_m2 * 0.35 AND area_decl_m2 * 2.5 THEN 'Levee terrain'
                        ELSE 'Emprise estimee (surface declaree)'
                    END AS geom_source_label
                FROM enriched
            )
            SELECT
                cep_uuid,
                code_parcelle,
                culture_principale,
                superficie_ha,
                rendement_kg_ha,
                campagne,
                commune_nom,
                prefecture_nom,
                region_nom,
                geom_source_label,
                ST_AsGeoJSON(geom_display) AS geom_json
            FROM normalized
            WHERE geom_display IS NOT NULL
        """
        return self.execute_geojson(sql, params, self.layer_name)


class TeteSourceGeoJSONView(BaseGeoJSONView):
    layer_name = "tetes_sources"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                ts_uuid,
                id_ts as nom_source,
                type_source_label as type_source,
                usage_principal_label as usage_principal,
                pop_desservie,
                protection_exist,
                type_protection_labels,
                etat_fonctionnel_label as etat_fonctionnel,
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

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                ouvrage_uuid,
                code_ouvrage,
                type_ouvrages_label as type_ouvrage,
                etat_anti_label,
                etat_couv_label,
                COALESCE(etat_anti_label, etat_couv_label) as etat_ouvrage,
                longueur_anti_m,
                surface_couv_ha,
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

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                id_couloir,
                nom_couloir,
                type_couloir,
                statut_couloir,
                longueur_km,
                largeur_m,
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

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom_zone IS NOT NULL AND project_code = %s"
        if region:
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                id_zone,
                type_degradation_label as type_degradation,
                surface_degrad_ha as surface_degradee_ha,
                surface_restaur_ha as surface_restauree_ha,
                surf_regen_ha as surface_regeneree_ha,
                annee_plantation,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom_zone, 4326)) as geom_json
            FROM marts.vw_zone_degradee
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class OrganisationsGeoJSONView(BaseGeoJSONView):
    layer_name = "organisations"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                id_org,
                id_org as nom_org,
                type_org_label as type_org,
                statut_juridique_label as statut,
                nb_membres_total as nb_membres,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_agr_organisation
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class MenagesGeoJSONView(BaseGeoJSONView):
    layer_name = "menages"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                id_menage,
                nom_chef_menage as chef_menage,
                nb_personnes,
                type_menage_label as type_menage,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_agr_menage
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class MeteoStationsGeoJSONView(BaseGeoJSONView):
    layer_name = "stations_meteo"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                station_uuid,
                code_station,
                nom_station,
                type_station_label as type_station,
                statut_station_label as statut_station,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_meteo_station
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class MarchesGeoJSONView(BaseGeoJSONView):
    layer_name = "marches"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                marche_uuid,
                localite as nom_marche,
                type_comptoir as type_marche,
                frequence_marche,
                filiere_label as filiere,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_marche
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class IntrantsGeoJSONView(BaseGeoJSONView):
    layer_name = "intrants"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                intrant_uuid,
                type_intrant,
                filiere_label,
                quantite,
                unite_intrant,
                campagne_yyyy,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_intrant_distribution
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class ComitesGeoJSONView(BaseGeoJSONView):
    layer_name = "comites"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                comite_uuid,
                id_comite,
                nom_comite,
                type_comite_label,
                statut_comite_label,
                nb_membres_total,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_agr_comite
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)

class EntreprisesGeoJSONView(BaseGeoJSONView):
    layer_name = "entreprises"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                ent_uuid,
                id_ent,
                raison_sociale as nom_entreprise,
                taille_entreprise_label as taille_entreprise,
                secteur_principal_label as secteur_activite,
                effectif_total as nb_employes,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_entreprise_econ
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class FormationsGeoJSONView(BaseGeoJSONView):
    layer_name = "formations"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                formation_uuid,
                id_formation,
                organisme_formateur as nom_centre,
                type_formation_label as type_formation,
                participants_total as nb_apprenants,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_formation_eco_cat
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class SortantsGeoJSONView(BaseGeoJSONView):
    layer_name = "sortants"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                suivi_uuid,
                id_sortant,
                nom_sortant,
                filiere_principale_label as filiere,
                insere as statut_insertion,
                type_insertion_label as type_insertion,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_fiere_suivi_sortant
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class SitesGeoJSONView(BaseGeoJSONView):
    layer_name = "sites"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
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

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
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

class EmploisDomGeoJSONView(BaseGeoJSONView):
    layer_name = "emplois"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                emploi_dom_uuid,
                id_ent,
                raison_sociale,
                domaine_label,
                nb_empl_dom,
                nb_empl_fem_dom,
                nb_empl_jeunes_dom,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_ent_emploi_dom
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)


class InsertionsDomGeoJSONView(BaseGeoJSONView):
    layer_name = "insertions"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
            where += " AND id_region = %s"
            params.append(region)

        sql = f"""
            SELECT
                insertion_dom_uuid,
                id_ent,
                raison_sociale,
                domaine_label,
                type_insertion_label,
                nb_ins_dom,
                nb_ins_fem_dom,
                nb_ins_jeunes_dom,
                commune_nom,
                prefecture_nom,
                region_nom,
                ST_AsGeoJSON(ST_Transform(geom, 4326)) as geom_json
            FROM marts.vw_ent_insertion_dom
            {where}
        """
        return self.execute_geojson(sql, params, self.layer_name)

class EntreprisesEmploiGeoJSONView(BaseGeoJSONView):
    layer_name = "entreprises_emploi"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
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

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
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

        region = self.get_current_region(request)
        params = [project_code]
        where = "WHERE geom IS NOT NULL AND project_code = %s"
        if region:
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
    """Retourne un resume leger pour debug (pas les geometries)."""

    layer_name = "all_layers"

    def get(self, request, *args, **kwargs):
        project_code = self.require_project(request)
        region = self.get_current_region(request)

        restrict_region = bool(region)
        params = []

        if restrict_region:
            sql = """
                SELECT 'cep_parcelles' as layer, count(*)::int as count
                FROM marts.vw_cep_parcelle
                WHERE project_code = %s AND id_region = %s
                UNION ALL
                SELECT 'tete_source' as layer, count(*)::int as count
                FROM marts.vw_tete_source
                WHERE project_code = %s AND id_region = %s
                UNION ALL
                SELECT 'ouvrage' as layer, count(*)::int as count
                FROM marts.vw_ouvrage
                WHERE project_code = %s AND id_region = %s
                UNION ALL
                SELECT 'couloir' as layer, count(*)::int as count
                FROM marts.vw_couloir
                WHERE project_code = %s AND id_region = %s
                UNION ALL
                SELECT 'zone_degradee' as layer, count(*)::int as count
                FROM marts.vw_zone_degradee
                WHERE project_code = %s AND id_region = %s
            """
            params.extend(
                [
                    project_code, region,
                    project_code, region,
                    project_code, region,
                    project_code, region,
                    project_code, region,
                ]
            )
        else:
            sql = """
                SELECT 'cep_parcelles' as layer, count(*)::int as count
                FROM marts.vw_cep_parcelle
                WHERE project_code = %s
                UNION ALL
                SELECT 'tete_source' as layer, count(*)::int as count
                FROM marts.vw_tete_source
                WHERE project_code = %s
                UNION ALL
                SELECT 'ouvrage' as layer, count(*)::int as count
                FROM marts.vw_ouvrage
                WHERE project_code = %s
                UNION ALL
                SELECT 'couloir' as layer, count(*)::int as count
                FROM marts.vw_couloir
                WHERE project_code = %s
                UNION ALL
                SELECT 'zone_degradee' as layer, count(*)::int as count
                FROM marts.vw_zone_degradee
                WHERE project_code = %s
            """
            params.extend([project_code, project_code, project_code, project_code, project_code])

        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()

        return JsonResponse(
            {
                "project": project_code,
                "region": region or None,
                "counts": [{"layer": r[0], "count": r[1]} for r in rows],
            }
        )


# ============================================================
# Alias de compatibilite pour eviter les ImportError dans urls
# ============================================================

if "MeteoStationsGeoJSONView" not in globals():
    if "MeteoStationGeoJSONView" in globals():
        MeteoStationsGeoJSONView = MeteoStationGeoJSONView
    elif "MeteoStationGeoJsonView" in globals():
        MeteoStationsGeoJSONView = MeteoStationGeoJsonView
    elif "MeteoStationsGeoJsonView" in globals():
        MeteoStationsGeoJSONView = MeteoStationsGeoJsonView


