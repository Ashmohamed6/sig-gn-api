# data_api/views_fiere_aggregates.py

"""
Vues d'agrégation pour le dashboard FIERE.
Endpoints: /api/data/*/stats/
"""

from django.db import connection
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .mixins import CurrentProjectRequiredMixin

# Import UserRole depuis accounts
try:
    from accounts.models import UserRole
except ImportError:
    class UserRole:
        ADMIN = "admin"


def get_user_region_filter(user):
    """
    Retourne la clause SQL et les params pour filtrer par régions de l'utilisateur.
    Les admins (superuser ou role=admin) voient tout.
    """
    if user.is_superuser:
        return "", []
    
    role = getattr(user, "role", None)
    is_admin = role == UserRole.ADMIN if hasattr(UserRole, 'ADMIN') else str(role).lower() == 'admin'
    
    if is_admin:
        return "", []
    
    if hasattr(user, 'regions'):
        region_ids = list(user.regions.values_list("id_region", flat=True))
        if region_ids:
            return "AND id_region = ANY(%s)", [region_ids]
    
    return "", []


class FiereSuiviSortantAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Agrégats pour le suivi des sortants FIERE.
    
    GET /api/data/fiere-suivi-sortants/stats/
    Headers: Authorization, X-Project-Code
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        # Récupérer le projet courant
        project, error = self.get_current_project(request)
        if error:
            return error
        
        project_code = project.code_fonc
        region_clause, region_params = get_user_region_filter(request.user)
        
        base_where = f"project_code = %s {region_clause}"
        base_params = [project_code] + region_params
        
        with connection.cursor() as cursor:
            # 1) Agrégats globaux
            cursor.execute(f"""
                SELECT
                    COUNT(*) AS nb_sortants,
                    COUNT(*) FILTER (WHERE est_pvh = TRUE) AS nb_sortants_pvh,
                    COUNT(*) FILTER (WHERE sexe = 'F') AS nb_sortants_femmes,
                    COUNT(*) FILTER (WHERE est_insere = TRUE) AS nb_sortants_inseres,
                    ROUND(
                        100.0 * COUNT(*) FILTER (WHERE est_insere = TRUE) / NULLIF(COUNT(*), 0),
                        1
                    ) AS taux_insertion_pct
                FROM marts.vw_fiere_suivi_sortant
                WHERE {base_where}
            """, base_params)
            
            row = cursor.fetchone()
            global_stats = {
                "nb_sortants": row[0] or 0,
                "nb_sortants_pvh": row[1] or 0,
                "nb_sortants_femmes": row[2] or 0,
                "nb_sortants_inseres": row[3] or 0,
                "taux_insertion_pct": float(row[4]) if row[4] else 0.0,
            }
            
            # 2) Par période de suivi
            cursor.execute(f"""
                SELECT
                    COALESCE(periode_suivi_label, 'Non renseigné') AS periode_suivi_label,
                    COUNT(*) AS nb_sortants,
                    ROUND(
                        100.0 * COUNT(*) FILTER (WHERE est_insere = TRUE) / NULLIF(COUNT(*), 0),
                        1
                    ) AS taux_insertion_pct
                FROM marts.vw_fiere_suivi_sortant
                WHERE {base_where}
                GROUP BY periode_suivi_label
                ORDER BY periode_suivi_label
            """, base_params)
            
            by_periode = [
                {
                    "periode_suivi_label": r[0],
                    "nb_sortants": r[1] or 0,
                    "taux_insertion_pct": float(r[2]) if r[2] else 0.0,
                }
                for r in cursor.fetchall()
            ]
            
            # 3) Par type d'insertion
            cursor.execute(f"""
                SELECT
                    COALESCE(type_insertion_label, 'Non renseigné') AS type_insertion_label,
                    COUNT(*) AS nb_sortants_inseres
                FROM marts.vw_fiere_suivi_sortant
                WHERE {base_where} AND est_insere = TRUE
                GROUP BY type_insertion_label
                ORDER BY nb_sortants_inseres DESC
            """, base_params)
            
            by_type = [
                {
                    "type_insertion_label": r[0],
                    "nb_sortants_inseres": r[1] or 0,
                }
                for r in cursor.fetchall()
            ]
        
        return Response({
            "global": global_stats,
            "by_periode_suivi": by_periode,
            "by_type_insertion": by_type,
        })


class EntEmploiDomAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Agrégats pour les emplois par domaine.
    
    GET /api/data/ent-emplois-dom/stats/
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
                    COALESCE(SUM(nb_emplois), 0) AS nb_emplois_totaux,
                    COALESCE(SUM(nb_emplois_femmes), 0) AS nb_emplois_femmes
                FROM marts.vw_ent_emploi_dom
                WHERE {base_where}
            """, base_params)
            
            row = cursor.fetchone()
            global_stats = {
                "nb_emplois_totaux": row[0] or 0,
                "nb_emplois_femmes": row[1] or 0,
            }
            
            # Par domaine
            cursor.execute(f"""
                SELECT
                    COALESCE(domaine_label, 'Non renseigné') AS domaine_label,
                    COALESCE(SUM(nb_emplois), 0) AS nb_emplois
                FROM marts.vw_ent_emploi_dom
                WHERE {base_where}
                GROUP BY domaine_label
                ORDER BY nb_emplois DESC
            """, base_params)
            
            by_domaine = [
                {"domaine_label": r[0], "nb_emplois": r[1] or 0}
                for r in cursor.fetchall()
            ]
        
        return Response({
            "global": global_stats,
            "by_domaine": by_domaine,
        })


class EntInsertionDomAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Agrégats pour les insertions par domaine.
    
    GET /api/data/ent-insertions-dom/stats/
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
                    COUNT(*) AS nb_insertions,
                    COUNT(*) FILTER (WHERE delai_insertion_mois <= 3) AS insertion_3m,
                    COUNT(*) FILTER (WHERE delai_insertion_mois <= 6) AS insertion_6m,
                    COUNT(*) FILTER (WHERE delai_insertion_mois <= 12) AS insertion_12m
                FROM marts.vw_ent_insertion_dom
                WHERE {base_where}
            """, base_params)
            
            row = cursor.fetchone()
            global_stats = {
                "nb_insertions": row[0] or 0,
                "insertion_3m": row[1] or 0,
                "insertion_6m": row[2] or 0,
                "insertion_12m": row[3] or 0,
            }
            
            # Par type
            cursor.execute(f"""
                SELECT
                    COALESCE(type_insertion_label, 'Non renseigné') AS type_label,
                    COUNT(*) AS nb_insertions
                FROM marts.vw_ent_insertion_dom
                WHERE {base_where}
                GROUP BY type_insertion_label
                ORDER BY nb_insertions DESC
            """, base_params)
            
            by_type = [
                {"type_label": r[0], "nb_insertions": r[1] or 0}
                for r in cursor.fetchall()
            ]
        
        return Response({
            "global": global_stats,
            "by_type": by_type,
        })


class EntrepriseAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Agrégats pour les entreprises.
    
    GET /api/data/entreprises/stats/
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
                    COUNT(*) AS nb_entreprises,
                    COUNT(*) FILTER (WHERE est_mpme_formalisee = TRUE) AS total_mpme_formalisees,
                    COUNT(*) FILTER (WHERE est_mpme_appuyee_fiere = TRUE) AS total_mpme_appuyees_fiere
                FROM marts.vw_entreprise_econ
                WHERE {base_where}
            """, base_params)
            
            row = cursor.fetchone()
        
        return Response({
            "global": {
                "nb_entreprises": row[0] or 0,
                "total_mpme_formalisees": row[1] or 0,
                "total_mpme_appuyees_fiere": row[2] or 0,
            }
        })


class FormationEcoCatAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Agrégats pour les formations économiques.
    
    GET /api/data/formations-eco/stats/
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
                    COUNT(DISTINCT id_formation) AS nb_formations,
                    COALESCE(SUM(nb_participants), 0) AS nb_participants
                FROM marts.vw_formation_eco_cat
                WHERE {base_where}
            """, base_params)
            
            row = cursor.fetchone()
        
        return Response({
            "global": {
                "nb_formations": row[0] or 0,
                "nb_participants": row[1] or 0,
            }
        })


class ActeurParticipationAggregatesView(CurrentProjectRequiredMixin, GenericAPIView):
    """
    Agrégats pour les acteurs / partenaires.
    
    GET /api/data/acteurs-participation/stats/
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
                SELECT COUNT(*) AS total_acteurs
                FROM marts.vw_acteur_participation
                WHERE {base_where}
            """, base_params)
            
            row = cursor.fetchone()
        
        return Response({
            "global": {
                "total_acteurs": row[0] or 0,
            }
        })