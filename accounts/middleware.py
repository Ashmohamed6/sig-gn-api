from django.utils.deprecation import MiddlewareMixin
from django.conf import settings

from .models import RefProject


class CurrentProjectMiddleware(MiddlewareMixin):
    """
    Contexte PRO WebSIG (projet + région)

    - Récupère le projet actif depuis le header HTTP 'X-Project-Code'
      et l'attache à request.current_project.
    - Injecte automatiquement la région depuis request.user.region_id
      et l'attache à request.current_region_id.

    Règles :
    - Les couches de référence (schéma ref) sont accessibles SANS X-Project-Code.
    - Les couches de données projet exigent X-Project-Code (sauf admin/staff si tu veux).
    - Si l'utilisateur n'est pas authentifié : ne fait rien.
    """

    # Endpoints "référence" qui ne doivent PAS exiger X-Project-Code
    REF_PATH_HINTS = (
        "admin-region",
        "admin-prefecture",
        "admin-commune",
        "aire-protegee",
        "zone-humide",
        "zone-sableuse",
        "hydrographie",
        "occupation-sol",
        "reseau-routier",
        "equipement",
        "localite",
        "agglomeration",
        "habitation-dispersee",
    )

    def process_request(self, request):
        # On ne touche pas à l'admin Django, etc.
        if not request.path.startswith("/api/"):
            return

        user = getattr(request, "user", None)

        # Contexte par défaut
        request.current_project = None
        request.current_region_id = None

        if not user or not user.is_authenticated:
            return

        # -------------------------
        # Région (déduite du profil)
        # -------------------------
        # users non-admin : région imposée
        # admin/staff : possibilité de surcharger via ?region=GN005 (sinon None => toutes régions)
        if user.is_staff or user.is_superuser:
            request.current_region_id = request.GET.get("region") or getattr(user, "region_id", None)
        else:
            request.current_region_id = getattr(user, "region_id", None)

        # -------------------------
        # Projet (header X-Project-Code)
        # -------------------------
        meta_header = getattr(settings, "CURRENT_PROJECT_HEADER", "HTTP_X_PROJECT_CODE")
        code = request.META.get(meta_header)

        # Si on est sur une URL de référence : on ne force PAS le projet
        is_ref_endpoint = any(h in request.path for h in self.REF_PATH_HINTS)

        project = None
        if code:
            try:
                project = RefProject.objects.get(code_fonc=code, actif=True)
            except RefProject.DoesNotExist:
                project = None
        else:
            # Fallback PRO : si l'user a un default_project_id, on peut l'utiliser
            # (ne bloque pas l'accès aux couches ref)
            default_pid = getattr(user, "default_project_id", None)
            if default_pid:
                try:
                    project = RefProject.objects.get(project_id=default_pid, actif=True)
                except RefProject.DoesNotExist:
                    project = None

        # Sécurité : on ne garde le projet que s'il appartient à l'utilisateur (hors admin/staff)
        if project and not (user.is_staff or user.is_superuser):
            if not user.projects.filter(project_id=project.project_id).exists():
                project = None

        request.current_project = project

        # Si ce n'est pas un endpoint ref, et qu'on n'a toujours pas de projet => on laisse la vue gérer (400)
        # (Tu peux aussi choisir de renvoyer un JsonResponse ici, mais c'est plus propre côté vues.)
        return
