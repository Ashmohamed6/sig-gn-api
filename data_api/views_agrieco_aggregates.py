# data_api/views_agrieco_aggregates.py

"""
Vues d'agrégation pour le dashboard AGRIECO.
Endpoints: /api/data/*/stats/
"""

from django.db import connection
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .mixins import CurrentProjectRequiredMixin


def get_user_region_filter(user):
    """
    Retourne la clause SQL et les params pour filtrer par régions de l'utilisateur.
    Les admins techniques (superuser/staff) voient tout.
    """
    role = str(getattr(user, "role", "") or "").strip().lower()
    if user.is_superuser or getattr(user, "is_staff", False) or role in {"admin", "project_manager"}:
        return "", []

    if hasattr(user, "regions"):
        region_ids = list(user.regions.values_list("id_region", flat=True))
        if region_ids:
            return "AND id_region = ANY(%s)", [region_ids]

    region_id = getattr(user, "region_id", None)
    if region_id:
        return "AND id_region = %s", [region_id]

    # Fail closed: un user non-admin sans région n'obtient aucune donnée.
    return "AND 1 = 0", []


class AgrMenageAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Agrégats pour les ménages agricoles AGRIECO.
    
    GET /api/data/agr-menages/stats/
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        project, error = self.get_current_project(request)
        if error:
            return error
        
        project_code = project.code_fonc
        region_clause, region_params = get_user_region_filter(request.user)
        
        base_where = f"project_code = %s {region_clause}"
        base_params = [project_code] + region_params
        
        with connection.cursor() as cursor:
            # Global
            cursor.execute(f"""
                SELECT
                    COUNT(*) AS total_menages,
                    COUNT(*) FILTER (WHERE pratique_agroeco = TRUE) AS nb_menages_prat_agroeco,
                    COALESCE(SUM(nb_seances_sensib), 0) AS total_seances_sensib
                FROM marts.vw_agr_menage
                WHERE {base_where}
            """, base_params)
            
            row = cursor.fetchone()
            global_stats = {
                "total_menages": row[0] or 0,
                "nb_menages_prat_agroeco": row[1] or 0,
                "total_seances_sensib": row[2] or 0,
            }
            
            # Par région
            cursor.execute(f"""
                SELECT
                    COALESCE(region_nom, 'Non renseigné') AS region_nom,
                    COUNT(*) AS total_menages
                FROM marts.vw_agr_menage
                WHERE {base_where}
                GROUP BY region_nom
                ORDER BY total_menages DESC
            """, base_params)
            
            by_region = [
                {"region_nom": r[0], "total_menages": r[1] or 0}
                for r in cursor.fetchall()
            ]
        
        return Response({
            "global": global_stats,
            "by_region": by_region,
        })


class AgrOrganisationAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Agrégats pour les organisations AGRIECO.
    
    GET /api/data/agr-organisations/stats/
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        project, error = self.get_current_project(request)
        if error:
            return error
        
        project_code = project.code_fonc
        region_clause, region_params = get_user_region_filter(request.user)
        
        base_where = f"project_code = %s {region_clause}"
        base_params = [project_code] + region_params
        
        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT
                    COUNT(*) AS total_organisations,
                    COALESCE(SUM(nb_emplois_verts), 0) AS total_emplois_verts
                FROM marts.vw_agr_organisation
                WHERE {base_where}
            """, base_params)
            
            row = cursor.fetchone()
            global_stats = {
                "total_organisations": row[0] or 0,
                "total_emplois_verts": row[1] or 0,
            }
            
            # Par région
            cursor.execute(f"""
                SELECT
                    COALESCE(region_nom, 'Non renseigné') AS region_nom,
                    COUNT(*) AS total_organisations
                FROM marts.vw_agr_organisation
                WHERE {base_where}
                GROUP BY region_nom
                ORDER BY total_organisations DESC
            """, base_params)
            
            by_region = [
                {"region_nom": r[0], "total_organisations": r[1] or 0}
                for r in cursor.fetchall()
            ]
        
        return Response({
            "global": global_stats,
            "by_region": by_region,
        })


class AgrComiteAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Agrégats pour les comités AGRIECO.
    
    GET /api/data/agr-comites/stats/
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        project, error = self.get_current_project(request)
        if error:
            return error
        
        project_code = project.code_fonc
        region_clause, region_params = get_user_region_filter(request.user)
        
        base_where = f"project_code = %s {region_clause}"
        base_params = [project_code] + region_params
        
        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT
                    COUNT(*) AS total_comites,
                    COALESCE(SUM(nb_conflits_regles), 0) AS total_conflits_regles
                FROM marts.vw_agr_comite
                WHERE {base_where}
            """, base_params)
            
            row = cursor.fetchone()
        
        return Response({
            "global": {
                "total_comites": row[0] or 0,
                "total_conflits_regles": row[1] or 0,
            }
        })


class CepParcelleAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Agrégats pour les parcelles CEP AGRIECO.
    
    GET /api/data/cep-parcelles/stats/
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        project, error = self.get_current_project(request)
        if error:
            return error
        
        project_code = project.code_fonc
        region_clause, region_params = get_user_region_filter(request.user)
        
        base_where = f"project_code = %s {region_clause}"
        base_params = [project_code] + region_params
        
        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT
                    COUNT(*) AS total_parcelles,
                    COALESCE(SUM(surface_decl_ha), 0) AS total_surface_decl_ha,
                    ROUND(AVG(rendement_kg_ha), 0) AS rendement_moyen
                FROM marts.vw_cep_parcelle
                WHERE {base_where}
            """, base_params)
            
            row = cursor.fetchone()
            global_stats = {
                "total_parcelles": row[0] or 0,
                "total_surface_decl_ha": float(row[1]) if row[1] else 0.0,
                "rendement_moyen": float(row[2]) if row[2] else 0.0,
            }
            
            # Par filière
            cursor.execute(f"""
                SELECT
                    COALESCE(filiere_label, 'Non renseigné') AS filiere_label,
                    ROUND(AVG(rendement_kg_ha), 0) AS rendement_moyen_kg_ha,
                    COALESCE(SUM(surface_decl_ha), 0) AS surface_decl_ha
                FROM marts.vw_cep_parcelle
                WHERE {base_where}
                GROUP BY filiere_label
                ORDER BY surface_decl_ha DESC
            """, base_params)
            
            by_filiere = [
                {
                    "filiere_label": r[0],
                    "rendement_moyen_kg_ha": float(r[1]) if r[1] else 0.0,
                    "surface_decl_ha": float(r[2]) if r[2] else 0.0,
                }
                for r in cursor.fetchall()
            ]
        
        return Response({
            "global": global_stats,
            "by_filiere": by_filiere,
        })


class ZoneDegradeeAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Agrégats pour les zones dégradées AGRIECO.
    
    GET /api/data/zone-degradee/stats/
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        project, error = self.get_current_project(request)
        if error:
            return error
        
        project_code = project.code_fonc
        region_clause, region_params = get_user_region_filter(request.user)
        
        base_where = f"project_code = %s {region_clause}"
        base_params = [project_code] + region_params
        
        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT
                    COALESCE(SUM(surface_restauree_ha), 0) AS surface_restauree_ha,
                    COALESCE(SUM(surface_regeneree_ha), 0) AS surface_regeneree_ha
                FROM marts.vw_zone_degradee
                WHERE {base_where}
            """, base_params)
            
            row = cursor.fetchone()
            global_stats = {
                "surface_restauree_ha": float(row[0]) if row[0] else 0.0,
                "surface_regeneree_ha": float(row[1]) if row[1] else 0.0,
            }
            
            # Par région
            cursor.execute(f"""
                SELECT
                    COALESCE(region_nom, 'Non renseigné') AS region_nom,
                    COALESCE(SUM(surface_restauree_ha), 0) AS surface_restauree_ha,
                    COALESCE(SUM(surface_regeneree_ha), 0) AS surface_regeneree_ha
                FROM marts.vw_zone_degradee
                WHERE {base_where}
                GROUP BY region_nom
                ORDER BY surface_restauree_ha DESC
            """, base_params)
            
            by_region = [
                {
                    "region_nom": r[0],
                    "surface_restauree_ha": float(r[1]) if r[1] else 0.0,
                    "surface_regeneree_ha": float(r[2]) if r[2] else 0.0,
                }
                for r in cursor.fetchall()
            ]
        
        return Response({
            "global": global_stats,
            "by_region": by_region,
        })


class TeteSourceAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Agrégats pour les têtes de sources AGRIECO.
    
    GET /api/data/tete-source/stats/
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        project, error = self.get_current_project(request)
        if error:
            return error
        
        project_code = project.code_fonc
        region_clause, region_params = get_user_region_filter(request.user)
        
        base_where = f"project_code = %s {region_clause}"
        base_params = [project_code] + region_params
        
        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT COUNT(*) FILTER (WHERE est_protegee = TRUE) AS nb_sources_protegees
                FROM marts.vw_tete_source
                WHERE {base_where}
            """, base_params)
            
            row = cursor.fetchone()
        
        return Response({
            "global": {
                "nb_sources_protegees": row[0] or 0,
            }
        })


class MeteoStationAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Agrégats pour les stations météo AGRIECO.
    
    GET /api/data/meteo/stations/stats/
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        project, error = self.get_current_project(request)
        if error:
            return error
        
        project_code = project.code_fonc
        region_clause, region_params = get_user_region_filter(request.user)
        
        base_where = f"project_code = %s {region_clause}"
        base_params = [project_code] + region_params
        
        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT
                    COUNT(*) AS nb_stations,
                    COUNT(*) FILTER (WHERE est_active = TRUE) AS nb_stations_actives
                FROM marts.vw_meteo_station
                WHERE {base_where}
            """, base_params)
            
            row = cursor.fetchone()
        
        return Response({
            "global": {
                "nb_stations": row[0] or 0,
                "nb_stations_actives": row[1] or 0,
            }
        })


class OuvrageAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Agrégats pour les ouvrages AGRIECO.
    
    GET /api/data/ouvrages/stats/
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        project, error = self.get_current_project(request)
        if error:
            return error
        
        project_code = project.code_fonc
        region_clause, region_params = get_user_region_filter(request.user)
        
        base_where = f"project_code = %s {region_clause}"
        base_params = [project_code] + region_params
        
        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT COUNT(*) AS nb_ouvrages
                FROM marts.vw_ouvrage
                WHERE {base_where}
            """, base_params)
            
            row = cursor.fetchone()
        
        return Response({
            "global": {
                "nb_ouvrages": row[0] or 0,
            }
        })


class CouloirAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Agrégats pour les couloirs de transhumance AGRIECO.
    
    GET /api/data/couloirs/stats/
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        project, error = self.get_current_project(request)
        if error:
            return error
        
        project_code = project.code_fonc
        region_clause, region_params = get_user_region_filter(request.user)
        
        base_where = f"project_code = %s {region_clause}"
        base_params = [project_code] + region_params
        
        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT COUNT(*) AS nb_couloirs
                FROM marts.vw_couloir
                WHERE {base_where}
            """, base_params)
            
            row = cursor.fetchone()
        
        return Response({
            "global": {
                "nb_couloirs": row[0] or 0,
            }
        })


class IntrantDistributionAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Agrégats pour la distribution d'intrants AGRIECO.
    
    GET /api/data/intrants-distribution/stats/
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        project, error = self.get_current_project(request)
        if error:
            return error
        
        project_code = project.code_fonc
        region_clause, region_params = get_user_region_filter(request.user)
        
        base_where = f"project_code = %s {region_clause}"
        base_params = [project_code] + region_params
        
        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT COALESCE(SUM(quantite), 0) AS total_quantite
                FROM marts.vw_intrant_distribution
                WHERE {base_where}
            """, base_params)
            
            row = cursor.fetchone()
        
        return Response({
            "global": {
                "total_quantite": float(row[0]) if row[0] else 0.0,
            }
        })
