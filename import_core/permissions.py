from dataclasses import dataclass

from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied, ValidationError

from accounts.models import RefRegion

ROLE_MANAGER = "manager"
ROLE_PROJECT_MANAGER = "project_manager"
ROLE_ADMIN = "admin"
IMPORT_ALLOWED_ROLES = {ROLE_MANAGER, ROLE_PROJECT_MANAGER, ROLE_ADMIN}


def normalize_role(value) -> str:
    return str(value or "").strip().lower()


def is_global_admin_user(user) -> bool:
    role = normalize_role(getattr(user, "role", ""))
    return bool(user and user.is_authenticated and (user.is_superuser or user.is_staff or role == ROLE_ADMIN))


def can_use_import_pipeline(user) -> bool:
    role = normalize_role(getattr(user, "role", ""))
    return bool(user and user.is_authenticated and (is_global_admin_user(user) or role in IMPORT_ALLOWED_ROLES))


def actor_project_ids(user) -> set[str]:
    if not user or not hasattr(user, "projects"):
        return set()
    return {str(pid) for pid in user.projects.values_list("project_id", flat=True)}


class CanUseImportPipelinePermission(permissions.BasePermission):
    def has_permission(self, request, view):
        return can_use_import_pipeline(request.user)


def ensure_actor_has_project_access(actor, project) -> None:
    if is_global_admin_user(actor):
        return

    scope_ids = actor_project_ids(actor)
    if not scope_ids:
        raise PermissionDenied("Aucun projet assigne a ce compte.")

    if str(project.project_id) not in scope_ids:
        raise PermissionDenied("Acces refuse: projet hors perimetre.")


def resolve_region_scope(actor, requested_region_id: str | None) -> str | None:
    normalized_region = str(requested_region_id or "").strip() or None
    role = normalize_role(getattr(actor, "role", ""))

    if role == ROLE_MANAGER and not is_global_admin_user(actor):
        actor_region = getattr(actor, "region_id", None)
        if not actor_region:
            raise PermissionDenied("Compte manager invalide: region obligatoire.")

        if normalized_region and normalized_region != actor_region:
            raise PermissionDenied("Acces refuse: region hors perimetre.")

        return actor_region

    if normalized_region:
        region_exists = RefRegion.objects.filter(id_region=normalized_region).exists()
        if not region_exists:
            raise ValidationError({"region_id": "Region invalide."})

    return normalized_region


@dataclass(frozen=True)
class ImportScope:
    project_id: str
    project_code: str
    region_id: str | None

