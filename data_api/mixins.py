# data_api/mixins.py

"""
Mixins pour les vues DRF de l'application data_api.
Gestion du projet courant via le header X-Project-Code.
"""

import re

from rest_framework import status
from rest_framework.response import Response

try:
    from accounts.models import RefProject
except ImportError:
    RefProject = None


class CurrentProjectRequiredMixin:
    """
    Mixin qui recupere le projet courant a partir du header X-Project-Code.

    Comportement:
    1. Cherche le header X-Project-Code dans la requete
    2. Valide que le projet existe et est actif
    3. Verifie l'acces projet/utilisateur de maniere stricte
    """

    PROJECT_HEADER_NAME = "X-Project-Code"
    PROJECT_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{2,50}$")

    def _is_admin_user(self, user) -> bool:
        if not user:
            return False

        role = str(getattr(user, "role", "") or "").strip().lower()
        return bool(
            getattr(user, "is_superuser", False)
            or getattr(user, "is_staff", False)
            or role == "admin"
        )

    def _resolve_dynamic_throttle_scope(self):
        request = getattr(self, "request", None)
        path = (getattr(request, "path", "") or "").lower()

        if not path.startswith("/api/data/"):
            return None

        # Endpoints d'agrégations les plus coûteux.
        if "/stats/" in path:
            return "stats"

        # Endpoints cartographiques (GeoJSON).
        if "/carto/" in path:
            return "geojson"

        return None

    def get_throttles(self):
        # N'applique un scope dynamique que si la vue n'en définit pas déjà un.
        if not getattr(self, "throttle_scope", None):
            dynamic_scope = self._resolve_dynamic_throttle_scope()
            if dynamic_scope:
                self.throttle_scope = dynamic_scope

        parent_get_throttles = getattr(super(), "get_throttles", None)
        if callable(parent_get_throttles):
            return parent_get_throttles()
        return []

    def get_current_project(self, request):
        """
        Recupere le projet courant strictement depuis le header X-Project-Code.

        Returns:
            tuple: (RefProject, None) si succes, (None, Response) si erreur
        """
        if RefProject is None:
            return None, Response(
                {"detail": "Configuration erreur: modele RefProject non trouve."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        code = (
            request.headers.get(self.PROJECT_HEADER_NAME)
            or request.headers.get(self.PROJECT_HEADER_NAME.lower())
            or request.headers.get("X-Project-Code")
            or request.META.get("HTTP_X_PROJECT_CODE")
        )

        user = request.user if getattr(request.user, "is_authenticated", False) else None

        if not code:
            return None, Response(
                {
                    "detail": "Header X-Project-Code obligatoire.",
                    "hint": "Ajoutez le header X-Project-Code avec la valeur du code_fonc du projet (ex: FIERE ou AGRIECO).",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        code = code.strip().upper()
        if not self.PROJECT_CODE_PATTERN.match(code):
            return None, Response(
                {"detail": "Format invalide pour X-Project-Code."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            project = RefProject.objects.get(code_fonc__iexact=code, actif=True)
        except RefProject.DoesNotExist:
            try:
                project = RefProject.objects.get(code_kobo__iexact=code, actif=True)
            except RefProject.DoesNotExist:
                return None, Response(
                    {
                        "detail": f"Aucun projet actif associe au code '{code}'.",
                        "hint": "Verifiez que le code_fonc est correct (FIERE ou AGRIECO).",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if user is not None and not self._is_admin_user(user):
            user_projects = self._get_user_projects(user)
            if user_projects is None:
                return None, Response(
                    {"detail": "Acces projet impossible: aucune relation de projets utilisateur detectee."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            if not user_projects.filter(pk=project.pk).exists():
                return None, Response(
                    {"detail": "Acces refuse: ce projet n'est pas associe a votre compte."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        self.current_project = project
        return project, None

    def _get_user_projects(self, user):
        """
        Recupere les projets de l'utilisateur.
        Gere les differentes facons dont la relation peut etre definie.
        """
        if hasattr(user, "projects"):
            return user.projects.all()
        if hasattr(user, "ref_projects"):
            return user.ref_projects.all()
        if hasattr(user, "project_set"):
            return user.project_set.all()
        return None


class OptionalProjectMixin:
    """
    Variante du mixin ou le projet est optionnel.
    Utile pour les endpoints qui peuvent fonctionner sans projet specifique.
    """

    PROJECT_HEADER_NAME = "X-Project-Code"
    PROJECT_CODE_PATTERN = CurrentProjectRequiredMixin.PROJECT_CODE_PATTERN

    def get_current_project_optional(self, request):
        """
        Recupere le projet courant si disponible, sinon retourne None.
        Ne genere jamais d'erreur.

        Returns:
            RefProject | None
        """
        if RefProject is None:
            return None

        code = request.headers.get(self.PROJECT_HEADER_NAME) or request.META.get("HTTP_X_PROJECT_CODE")

        if not code:
            return None

        code = code.strip().upper()
        if not self.PROJECT_CODE_PATTERN.match(code):
            return None

        try:
            project = RefProject.objects.get(code_fonc__iexact=code, actif=True)
        except RefProject.DoesNotExist:
            return None

        user = request.user if getattr(request.user, "is_authenticated", False) else None
        if user and not self._is_admin_user(user):
            if not hasattr(user, "projects") or not user.projects.filter(pk=project.pk).exists():
                return None

        return project
