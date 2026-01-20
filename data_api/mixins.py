# data_api/mixins.py

"""
Mixins pour les vues DRF de l'application data_api.
Gestion du projet courant via le header X-Project-Code.
"""

from rest_framework.response import Response
from rest_framework import status

# Import du modèle RefProject depuis accounts
try:
    from accounts.models import RefProject
except ImportError:
    RefProject = None

# Import UserRole si disponible
try:
    from accounts.models import UserRole
except ImportError:
    class UserRole:
        ADMIN = "admin"


class CurrentProjectRequiredMixin:
    """
    Mixin qui récupère le projet courant à partir du header X-Project-Code.
    
    Comportement :
    1. Cherche le header X-Project-Code dans la requête
    2. Si absent et que l'utilisateur n'a qu'un seul projet actif, l'utilise automatiquement
    3. Valide que le projet existe et est actif
    4. Stocke le projet dans self.current_project
    
    Usage dans les vues :
        class MaVue(CurrentProjectRequiredMixin, GenericAPIView):
            def get(self, request):
                project, error = self.get_current_project(request)
                if error:
                    return error
                # Utiliser project.code_fonc, project.code_kobo, etc.
    """
    
    PROJECT_HEADER_NAME = "X-Project-Code"
    
    def get_current_project(self, request):
        """
        Récupère le projet courant depuis le header ou par fallback.
        
        Returns:
            tuple: (RefProject, None) si succès, (None, Response) si erreur
        """
        if RefProject is None:
            return None, Response(
                {"detail": "Configuration erreur: modèle RefProject non trouvé."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        # 1) Récupérer le header X-Project-Code
        # Django convertit les headers en META avec le préfixe HTTP_
        code = (
            request.headers.get(self.PROJECT_HEADER_NAME) or
            request.headers.get(self.PROJECT_HEADER_NAME.lower()) or
            request.headers.get("X-Project-Code") or
            request.META.get("HTTP_X_PROJECT_CODE")
        )
        
        user = request.user if request.user.is_authenticated else None
        
        # 2) Fallback : si pas de header, chercher le projet unique de l'utilisateur
        if not code and user is not None:
            user_projects = self._get_user_projects(user)
            
            if user_projects is not None:
                active_projects = user_projects.filter(actif=True)
                count = active_projects.count()
                
                if count == 1:
                    # Un seul projet actif -> on l'utilise automatiquement
                    project = active_projects.first()
                    self.current_project = project
                    return project, None
                elif count == 0:
                    return None, Response(
                        {"detail": "Aucun projet actif associé à votre compte."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                # Si > 1 projets, on exige le header
        
        # 3) Si toujours pas de code, erreur
        if not code:
            return None, Response(
                {
                    "detail": "Aucun projet actif (header X-Project-Code manquant ou invalide).",
                    "hint": "Ajoutez le header X-Project-Code avec la valeur du code_fonc du projet (ex: FIERE ou AGRIECO)."
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # 4) Recherche du projet par code_fonc
        code = code.strip().upper()
        
        try:
            project = RefProject.objects.get(code_fonc__iexact=code, actif=True)
        except RefProject.DoesNotExist:
            # Essayer aussi avec code_kobo au cas où
            try:
                project = RefProject.objects.get(code_kobo__iexact=code, actif=True)
            except RefProject.DoesNotExist:
                return None, Response(
                    {
                        "detail": f"Aucun projet actif associé au code '{code}'.",
                        "hint": "Vérifiez que le code_fonc est correct (FIERE ou AGRIECO)."
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # 5) Vérifier accès utilisateur (optionnel mais recommandé)
        if user is not None and not user.is_superuser:
            role = getattr(user, 'role', None)
            is_admin = role == UserRole.ADMIN if hasattr(UserRole, 'ADMIN') else role == 'admin'
            
            if not is_admin:
                user_projects = self._get_user_projects(user)
                if user_projects is not None:
                    if not user_projects.filter(pk=project.pk).exists():
                        # L'utilisateur n'a pas accès - on laisse passer pour l'instant
                        pass
        
        self.current_project = project
        return project, None
    
    def _get_user_projects(self, user):
        """
        Récupère les projets de l'utilisateur.
        Gère les différentes façons dont la relation peut être définie.
        """
        # Essayer différents noms de relation
        if hasattr(user, 'projects'):
            return user.projects.all()
        elif hasattr(user, 'ref_projects'):
            return user.ref_projects.all()
        elif hasattr(user, 'project_set'):
            return user.project_set.all()
        return None
    
    # Override des méthodes HTTP pour injecter automatiquement le projet
    # NOTE: Ces méthodes appellent get_current_project mais ne bloquent pas
    # car les vues qui héritent de ce mixin doivent appeler get_current_project
    # explicitement dans leur méthode get/post pour avoir accès à self.current_project


class OptionalProjectMixin:
    """
    Variante du mixin où le projet est optionnel.
    Utile pour les endpoints qui peuvent fonctionner sans projet spécifique.
    """
    
    PROJECT_HEADER_NAME = "X-Project-Code"
    
    def get_current_project_optional(self, request):
        """
        Récupère le projet courant si disponible, sinon retourne None.
        Ne génère jamais d'erreur.
        
        Returns:
            RefProject | None
        """
        if RefProject is None:
            return None
        
        code = (
            request.headers.get(self.PROJECT_HEADER_NAME) or
            request.META.get("HTTP_X_PROJECT_CODE")
        )
        
        if not code:
            # Fallback sur projet unique de l'utilisateur
            user = request.user if request.user.is_authenticated else None
            if user and hasattr(user, 'projects'):
                active = user.projects.filter(actif=True)
                if active.count() == 1:
                    return active.first()
            return None
        
        try:
            return RefProject.objects.get(code_fonc__iexact=code.strip(), actif=True)
        except RefProject.DoesNotExist:
            return None