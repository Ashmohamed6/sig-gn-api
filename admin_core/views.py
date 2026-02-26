# admin_core/views.py
"""
Vues pour l'administration de la plateforme SIG Guinee.
"""
import uuid
import secrets
import string

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import RefProject
from .serializers import (
    AdminUserListSerializer,
    AdminUserDetailSerializer,
    AdminUserCreateSerializer,
    AdminUserUpdateSerializer,
)

UserModel = get_user_model()

ROLE_ADMIN = "admin"
ROLE_MANAGER = "manager"
ROLE_PROJECT_MANAGER = "project_manager"
MANAGEABLE_ROLES_BY_TEAM_LEAD = {"reader", "editor"}
MANAGEABLE_ROLES_BY_PROJECT_MANAGER = {"reader", "editor", "manager"}


def normalize_role(value) -> str:
    return str(value or "").strip().lower()


def is_platform_admin_user(user) -> bool:
    return bool(
        user
        and user.is_authenticated
        and (getattr(user, "is_superuser", False) or normalize_role(getattr(user, "role", "")) == ROLE_ADMIN)
    )


def is_team_lead_user(user) -> bool:
    return bool(
        user
        and user.is_authenticated
        and not getattr(user, "is_superuser", False)
        and normalize_role(getattr(user, "role", "")) == ROLE_MANAGER
    )


def is_project_manager_user(user) -> bool:
    return bool(
        user
        and user.is_authenticated
        and not getattr(user, "is_superuser", False)
        and normalize_role(getattr(user, "role", "")) == ROLE_PROJECT_MANAGER
    )


def can_manage_users(user) -> bool:
    return is_platform_admin_user(user) or is_project_manager_user(user) or is_team_lead_user(user)


def get_scope_project_ids(user) -> set[str]:
    if not user or not hasattr(user, "projects"):
        return set()
    return {str(pid) for pid in user.projects.values_list("project_id", flat=True)}


def is_manageable_role(target_user, allowed_roles: set[str]) -> bool:
    return normalize_role(getattr(target_user, "role", "")) in allowed_roles and not bool(
        getattr(target_user, "is_superuser", False)
    )


def target_in_team_lead_scope(actor, target_user) -> bool:
    if not is_team_lead_user(actor):
        return True

    actor_region = getattr(actor, "region_id", None)
    if not actor_region or getattr(target_user, "region_id", None) != actor_region:
        return False

    if not is_manageable_role(target_user, MANAGEABLE_ROLES_BY_TEAM_LEAD):
        return False

    actor_scope = get_scope_project_ids(actor)
    if not actor_scope:
        return False

    if not hasattr(target_user, "projects"):
        return False
    target_project_ids = {str(pid) for pid in target_user.projects.values_list("project_id", flat=True)}
    if not target_project_ids:
        return False

    return target_project_ids.issubset(actor_scope)


def target_in_sub_admin_scope(actor, target_user) -> bool:
    """
    Alias retro-compatible: ancien "sous-admin" = chef d'equipe (manager).
    """
    return target_in_team_lead_scope(actor, target_user)


def target_in_project_manager_scope(actor, target_user) -> bool:
    if not is_project_manager_user(actor):
        return True

    if not is_manageable_role(target_user, MANAGEABLE_ROLES_BY_PROJECT_MANAGER):
        return False

    actor_scope = get_scope_project_ids(actor)
    if not actor_scope:
        return False

    if not hasattr(target_user, "projects"):
        return False
    target_project_ids = {str(pid) for pid in target_user.projects.values_list("project_id", flat=True)}
    if not target_project_ids:
        return False

    return target_project_ids.issubset(actor_scope)


def target_in_actor_scope(actor, target_user) -> bool:
    if is_platform_admin_user(actor):
        return True
    if is_project_manager_user(actor):
        return target_in_project_manager_scope(actor, target_user)
    if is_team_lead_user(actor):
        return target_in_team_lead_scope(actor, target_user)
    return False


def scope_users_for_actor(queryset, actor):
    if is_platform_admin_user(actor):
        return queryset

    scope_project_ids = get_scope_project_ids(actor)
    if not scope_project_ids:
        return queryset.none()

    outside_projects = RefProject.objects.exclude(project_id__in=scope_project_ids)

    if is_project_manager_user(actor):
        return (
            queryset.filter(role__in=list(MANAGEABLE_ROLES_BY_PROJECT_MANAGER), is_superuser=False)
            .filter(projects__project_id__in=list(scope_project_ids))
            .exclude(projects__in=outside_projects)
            .distinct()
        )

    if not is_team_lead_user(actor):
        return queryset.none()

    region_id = getattr(actor, "region_id", None)
    if not region_id:
        return queryset.none()

    return (
        queryset.filter(region_id=region_id)
        .filter(role__in=list(MANAGEABLE_ROLES_BY_TEAM_LEAD), is_superuser=False)
        .filter(projects__project_id__in=list(scope_project_ids))
        .exclude(projects__in=outside_projects)
        .distinct()
    )


class IsUserAdminPermission(permissions.BasePermission):
    """
    Acces admin users:
    - admin global (role=admin ou superuser)
    - chef projet (role=project_manager) limite par perimetre projet
    - chef d'equipe (role=manager) limite par perimetre projet+region
    """

    def has_permission(self, request, view):
        return bool(request.user and can_manage_users(request.user))


class AdminThrottleMixin:
    """Throttle scope dynamique lecture/ecriture pour endpoints admin."""

    def get_throttles(self):
        if not getattr(self, "throttle_scope", None):
            method = getattr(self.request, "method", "GET")
            self.throttle_scope = "admin_read" if method in ("GET", "HEAD", "OPTIONS") else "admin_write"
        return super().get_throttles()


class AdminScopeMixin:
    def is_platform_admin(self, user=None) -> bool:
        return is_platform_admin_user(user or self.request.user)

    def is_team_lead(self, user=None) -> bool:
        return is_team_lead_user(user or self.request.user)

    def is_project_manager(self, user=None) -> bool:
        return is_project_manager_user(user or self.request.user)

    def scoped_users_queryset(self):
        base = UserModel.objects.all().select_related().prefetch_related("projects")
        return scope_users_for_actor(base, self.request.user)

    def forbid_out_of_scope(self, target_user, actor=None):
        actor = actor or getattr(getattr(self, "request", None), "user", None)
        if self.is_platform_admin(actor):
            return None

        if not (self.is_project_manager(actor) or self.is_team_lead(actor)):
            return Response({"detail": "Acces refuse."}, status=status.HTTP_403_FORBIDDEN)

        if not target_in_actor_scope(actor, target_user):
            return Response(
                {"detail": "Acces refuse: utilisateur hors perimetre de votre role."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    def serializer_context_with_scope(self):
        parent_get_serializer_context = getattr(super(), "get_serializer_context", None)
        if callable(parent_get_serializer_context):
            context = parent_get_serializer_context()
        else:
            context = {}
        context["admin_scope_actor"] = self.request.user
        return context


class AdminUserListCreateView(AdminScopeMixin, AdminThrottleMixin, generics.ListCreateAPIView):
    """
    GET  /api/admin/users/ - Liste les utilisateurs gerables
    POST /api/admin/users/ - Cree un utilisateur dans le perimetre autorise
    """

    permission_classes = [IsUserAdminPermission]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return AdminUserCreateSerializer
        return AdminUserListSerializer

    def get_serializer_context(self):
        return self.serializer_context_with_scope()

    def get_queryset(self):
        queryset = self.scoped_users_queryset()

        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(username__icontains=search)
                | Q(email__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
            )

        role = self.request.query_params.get("role")
        if role:
            queryset = queryset.filter(role=role)

        is_active = self.request.query_params.get("is_active")
        if is_active in ["true", "false"]:
            queryset = queryset.filter(is_active=(is_active == "true"))

        region = self.request.query_params.get("region", "").strip()
        if region:
            queryset = queryset.filter(region_id=region)

        project = self.request.query_params.get("project", "").strip()
        if project:
            project_filter = Q(projects__code_fonc__iexact=project)
            try:
                project_uuid = uuid.UUID(project)
            except (ValueError, TypeError, AttributeError):
                project_uuid = None
            if project_uuid is not None:
                project_filter = project_filter | Q(projects__project_id=project_uuid)

            queryset = queryset.filter(project_filter).distinct()

        ordering = self.request.query_params.get("ordering", "-date_joined")
        if ordering:
            queryset = queryset.order_by(ordering)

        return queryset


class AdminUserDetailView(AdminScopeMixin, AdminThrottleMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/admin/users/:id/ - Recupere un utilisateur
    PUT    /api/admin/users/:id/ - Met a jour (complet)
    PATCH  /api/admin/users/:id/ - Met a jour (partiel)
    DELETE /api/admin/users/:id/ - Supprime (si cible dans le perimetre autorise)
    """

    permission_classes = [IsUserAdminPermission]
    lookup_field = "id"

    def get_serializer_class(self):
        if self.request.method in ["PUT", "PATCH"]:
            return AdminUserUpdateSerializer
        return AdminUserDetailSerializer

    def get_serializer_context(self):
        return self.serializer_context_with_scope()

    def get_queryset(self):
        return self.scoped_users_queryset()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()

        if instance.id == request.user.id:
            return Response(
                {"detail": "Vous ne pouvez pas supprimer votre propre compte."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if instance.is_superuser or normalize_role(getattr(instance, "role", "")) == ROLE_ADMIN:
            admin_count = UserModel.objects.filter(Q(is_superuser=True) | Q(role=ROLE_ADMIN)).count()
            if admin_count <= 1:
                return Response(
                    {"detail": "Impossible de supprimer le dernier administrateur du systeme."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        self.perform_destroy(instance)
        return Response({"detail": "Utilisateur supprime avec succes."}, status=status.HTTP_200_OK)


class AdminUserActivateView(AdminScopeMixin, AdminThrottleMixin, APIView):
    """
    POST /api/admin/users/:id/activate/
    Active un utilisateur
    """

    permission_classes = [IsUserAdminPermission]

    def post(self, request, id):
        try:
            user = UserModel.objects.get(id=id)
        except UserModel.DoesNotExist:
            return Response({"detail": "Utilisateur non trouve."}, status=status.HTTP_404_NOT_FOUND)

        denied = self.forbid_out_of_scope(user, request.user)
        if denied is not None:
            return denied

        if user.is_active:
            return Response({"detail": "L'utilisateur est deja actif."}, status=status.HTTP_400_BAD_REQUEST)

        user.is_active = True
        user.save(update_fields=["is_active"])

        serializer = AdminUserDetailSerializer(user, context=self.serializer_context_with_scope())
        return Response(serializer.data)


class AdminUserDeactivateView(AdminScopeMixin, AdminThrottleMixin, APIView):
    """
    POST /api/admin/users/:id/deactivate/
    Desactive un utilisateur
    """

    permission_classes = [IsUserAdminPermission]

    def post(self, request, id):
        try:
            user = UserModel.objects.get(id=id)
        except UserModel.DoesNotExist:
            return Response({"detail": "Utilisateur non trouve."}, status=status.HTTP_404_NOT_FOUND)

        denied = self.forbid_out_of_scope(user, request.user)
        if denied is not None:
            return denied

        if user.id == request.user.id:
            return Response(
                {"detail": "Vous ne pouvez pas desactiver votre propre compte."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not user.is_active:
            return Response({"detail": "L'utilisateur est deja inactif."}, status=status.HTTP_400_BAD_REQUEST)

        user.is_active = False
        user.save(update_fields=["is_active"])

        serializer = AdminUserDetailSerializer(user, context=self.serializer_context_with_scope())
        return Response(serializer.data)


class AdminUserResetPasswordView(AdminScopeMixin, AdminThrottleMixin, APIView):
    """
    POST /api/admin/users/:id/reset-password/
    Reinitialise le mot de passe d'un utilisateur et retourne un mot de passe temporaire.
    """

    permission_classes = [IsUserAdminPermission]

    @staticmethod
    def _generate_temporary_password(length: int = 16) -> str:
        alphabet = string.ascii_letters + string.digits + "@#%_-!"
        return "".join(secrets.choice(alphabet) for _ in range(length))

    def post(self, request, id):
        try:
            user = UserModel.objects.get(id=id)
        except UserModel.DoesNotExist:
            return Response({"detail": "Utilisateur non trouve."}, status=status.HTTP_404_NOT_FOUND)

        denied = self.forbid_out_of_scope(user, request.user)
        if denied is not None:
            return denied

        is_admin_account = bool(user.is_superuser or normalize_role(getattr(user, "role", "")) == ROLE_ADMIN)
        if is_admin_account:
            active_admin_count = UserModel.objects.filter(
                Q(is_active=True) & (Q(is_superuser=True) | Q(role=ROLE_ADMIN))
            ).count()
            if active_admin_count <= 1:
                return Response(
                    {"detail": "Impossible de reinitialiser le dernier administrateur actif."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        temporary_password = self._generate_temporary_password()
        user.set_password(temporary_password)
        if not user.is_active:
            user.is_active = True
            user.save(update_fields=["password", "is_active"])
        else:
            user.save(update_fields=["password"])

        return Response(
            {
                "detail": "Mot de passe reinitialise avec succes.",
                "temporary_password": temporary_password,
            },
            status=status.HTTP_200_OK,
        )


class AdminUserStatsView(AdminScopeMixin, AdminThrottleMixin, APIView):
    """
    GET /api/admin/users/stats/
    Statistiques utilisateurs selon le perimetre de l'acteur.
    """

    permission_classes = [IsUserAdminPermission]

    def get(self, request):
        queryset = self.scoped_users_queryset()

        total_users = queryset.count()
        active_users = queryset.filter(is_active=True).count()
        inactive_users = total_users - active_users

        stats_by_role = {}
        roles = queryset.values_list("role", flat=True).distinct()
        for role in roles:
            if role:
                stats_by_role[role] = queryset.filter(role=role).count()

        if self.is_platform_admin(request.user):
            superuser_count = queryset.filter(is_superuser=True).count()
            stats_by_role[ROLE_ADMIN] = stats_by_role.get(ROLE_ADMIN, 0) + superuser_count

        return Response(
            {
                "total": total_users,
                "active": active_users,
                "inactive": inactive_users,
                "by_role": stats_by_role,
            }
        )


class AdminDashboardView(AdminScopeMixin, AdminThrottleMixin, APIView):
    """
    GET /api/admin/dashboard/
    Donnees pour le tableau de bord admin selon perimetre.
    """

    permission_classes = [IsUserAdminPermission]

    def get(self, request):
        scoped_users = self.scoped_users_queryset()
        now = timezone.now()

        user_stats = {
            "total": scoped_users.count(),
            "active": scoped_users.filter(is_active=True).count(),
            "new_this_month": scoped_users.filter(date_joined__year=now.year, date_joined__month=now.month).count(),
        }

        return Response({"users": user_stats})
