# admin_core/serializers.py
"""
Serializers pour l'administration des utilisateurs.
"""
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from accounts.models import RefProject, RefRegion

UserModel = get_user_model()

ROLE_ADMIN = "admin"
ROLE_MANAGER = "manager"
ROLE_PROJECT_MANAGER = "project_manager"
TEAM_LEAD_ALLOWED_ROLES = {"reader", "editor"}
PROJECT_MANAGER_ALLOWED_ROLES = {"reader", "editor", "manager"}


class AdminUserListSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    project_count = serializers.SerializerMethodField()
    role_display = serializers.CharField(source="get_role_display", read_only=True)
    projects = serializers.SerializerMethodField()
    region_data = serializers.SerializerMethodField()

    class Meta:
        model = UserModel
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "role",
            "role_display",
            "is_active",
            "is_superuser",
            "date_joined",
            "last_login",
            "project_count",
            "projects",
            "region",
            "region_data",
        ]

    def get_full_name(self, obj):
        full = f"{obj.first_name} {obj.last_name}".strip()
        return full if full else obj.username

    def get_project_count(self, obj):
        return obj.projects.count() if hasattr(obj, "projects") else 0

    def get_projects(self, obj):
        if not hasattr(obj, "projects"):
            return []
        return [
            {
                "project_id": str(p.project_id),
                "code_fonc": p.code_fonc,
                "libelle_public": p.libelle_public,
            }
            for p in obj.projects.all()
        ]

    def get_region_data(self, obj):
        if obj.region:
            return {"id_region": obj.region.id_region, "nom": obj.region.nom}
        return None


class AdminUserDetailSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    projects = serializers.SerializerMethodField()
    role_display = serializers.CharField(source="get_role_display", read_only=True)
    region_data = serializers.SerializerMethodField()

    class Meta:
        model = UserModel
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "role",
            "role_display",
            "is_active",
            "is_superuser",
            "is_staff",
            "date_joined",
            "last_login",
            "created_at",
            "updated_at",
            "region",
            "region_data",
            "default_project",
            "projects",
        ]

    def get_full_name(self, obj):
        full = f"{obj.first_name} {obj.last_name}".strip()
        return full if full else obj.username

    def get_region_data(self, obj):
        if obj.region:
            return {"id_region": obj.region.id_region, "nom": obj.region.nom}
        return None

    def get_projects(self, obj):
        if not hasattr(obj, "projects"):
            return []
        return [
            {
                "project_id": str(p.project_id),
                "code_fonc": p.code_fonc,
                "libelle_public": p.libelle_public,
            }
            for p in obj.projects.all()
        ]


class AdminScopeValidationMixin:
    @staticmethod
    def _normalize_role(value: str | None) -> str:
        return str(value or "").strip().lower()

    def _request_actor(self):
        request = self.context.get("request")
        if request is not None:
            return getattr(request, "user", None)
        return self.context.get("admin_scope_actor")

    def _is_platform_admin_actor(self) -> bool:
        actor = self._request_actor()
        role = self._normalize_role(getattr(actor, "role", ""))
        return bool(actor and actor.is_authenticated and (getattr(actor, "is_superuser", False) or role == ROLE_ADMIN))

    def _is_team_lead_actor(self) -> bool:
        actor = self._request_actor()
        role = self._normalize_role(getattr(actor, "role", ""))
        return bool(actor and actor.is_authenticated and not getattr(actor, "is_superuser", False) and role == ROLE_MANAGER)

    def _is_project_manager_actor(self) -> bool:
        actor = self._request_actor()
        role = self._normalize_role(getattr(actor, "role", ""))
        return bool(
            actor
            and actor.is_authenticated
            and not getattr(actor, "is_superuser", False)
            and role == ROLE_PROJECT_MANAGER
        )

    def _actor_scope_project_ids(self) -> set[str]:
        actor = self._request_actor()
        if not actor or not hasattr(actor, "projects"):
            return set()
        return {str(pid) for pid in actor.projects.values_list("project_id", flat=True)}

    def _enforce_actor_target_role(self, role_value: str | None):
        normalized = self._normalize_role(role_value)
        if self._is_team_lead_actor():
            allowed_roles = TEAM_LEAD_ALLOWED_ROLES
            err = "Chef d'\u00e9quipe: seuls les roles reader/editor sont autorises."
        elif self._is_project_manager_actor():
            allowed_roles = PROJECT_MANAGER_ALLOWED_ROLES
            err = "Chef de projet: seuls les roles manager/editor/reader sont autorises."
        else:
            return

        if normalized not in allowed_roles:
            raise serializers.ValidationError(
                {"role": err}
            )


class AdminUserCreateSerializer(AdminScopeValidationMixin, serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password],
        style={"input_type": "password"},
        help_text="Mot de passe (min 8 caracteres)",
    )
    password_confirm = serializers.CharField(
        write_only=True,
        required=True,
        style={"input_type": "password"},
        help_text="Confirmation du mot de passe",
    )
    project_ids = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        write_only=True,
        help_text="Liste des UUIDs/codes de projets a assigner",
    )
    region_id = serializers.CharField(
        required=False,
        allow_null=True,
        write_only=True,
        help_text="ID de la region",
    )

    class Meta:
        model = UserModel
        fields = [
            "username",
            "email",
            "password",
            "password_confirm",
            "first_name",
            "last_name",
            "role",
            "is_active",
            "is_superuser",
            "region_id",
            "project_ids",
        ]

    def _resolve_region(self, raw: str | None):
        if raw is None:
            return None
        value = str(raw).strip()
        if value == "":
            return None

        region = RefRegion.objects.filter(id_region=value).first()
        if region:
            return region

        region = RefRegion.objects.filter(nom__iexact=value).first()
        if region:
            return region

        region = RefRegion.objects.filter(nom__iexact=value.replace("_", " ")).first()
        if region:
            return region

        raise serializers.ValidationError("Region invalide")

    @staticmethod
    def _split_project_identifiers(values: list[str]) -> tuple[list[uuid.UUID], list[str]]:
        uuid_values: list[uuid.UUID] = []
        code_values: list[str] = []

        for raw in values:
            value = str(raw).strip()
            if not value:
                continue
            try:
                uuid_values.append(uuid.UUID(value))
            except (ValueError, TypeError, AttributeError):
                code_values.append(value)

        return uuid_values, code_values

    def _resolve_projects(self, raw_ids: list[str] | None):
        if raw_ids is None:
            return None

        ids = [str(x).strip() for x in raw_ids if str(x).strip()]
        if not ids:
            return []

        uuid_ids, code_ids = self._split_project_identifiers(ids)
        projects_by_uuid = list(RefProject.objects.filter(project_id__in=uuid_ids)) if uuid_ids else []
        projects_by_code = list(RefProject.objects.filter(code_fonc__in=code_ids)) if code_ids else []
        projects = list({str(p.project_id): p for p in projects_by_uuid + projects_by_code}.values())
        if not projects:
            raise serializers.ValidationError("Projet(s) invalide(s)")
        return projects

    def validate_email(self, value):
        if UserModel.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Cet email est deja utilise.")
        return value.lower()

    def validate_username(self, value):
        if UserModel.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Ce nom d'utilisateur est deja utilise.")
        return value

    def validate(self, attrs):
        if attrs.get("password") != attrs.get("password_confirm"):
            raise serializers.ValidationError(
                {"password_confirm": "Les mots de passe ne correspondent pas."}
            )

        actor_is_team_lead = self._is_team_lead_actor()
        actor_is_project_manager = self._is_project_manager_actor()

        if actor_is_team_lead or actor_is_project_manager:
            actor = self._request_actor()
            actor_scope = self._actor_scope_project_ids()
            if not actor_scope:
                raise serializers.ValidationError(
                    {"detail": "Votre compte n'a aucun projet actif assigne."}
                )

            self._enforce_actor_target_role(attrs.get("role"))

            if attrs.get("is_superuser"):
                raise serializers.ValidationError(
                    {"is_superuser": "Elevation en superuser interdite pour ce role."}
                )

            requested_projects = attrs.get("project_ids")
            if requested_projects is None or not [x for x in requested_projects if str(x).strip()]:
                raise serializers.ValidationError(
                    {"project_ids": "Au moins un projet de votre perimetre est requis."}
                )

            resolved_projects = self._resolve_projects(requested_projects) or []
            resolved_ids = {str(p.project_id) for p in resolved_projects}
            if not resolved_ids or not resolved_ids.issubset(actor_scope):
                raise serializers.ValidationError(
                    {"project_ids": "Projet(s) hors perimetre autorise."}
                )

            target_role = self._normalize_role(attrs.get("role"))
            region_input = attrs.get("region_id")
            resolved_region = None

            if actor_is_team_lead:
                actor_region_id = getattr(actor, "region_id", None)
                if not actor_region_id:
                    raise serializers.ValidationError(
                        {"detail": "Chef d'\u00e9quipe sans region assignee: creation refusee."}
                    )

                if region_input in (None, ""):
                    region_input = actor_region_id
                    attrs["region_id"] = actor_region_id

                resolved_region = self._resolve_region(region_input)
                if not resolved_region or resolved_region.id_region != actor_region_id:
                    raise serializers.ValidationError(
                        {"region_id": "Chef d'\u00e9quipe: region hors perimetre."}
                    )
            else:
                # Chef de projet (N2): perimetre projet multi-region.
                # Pour les roles operationnels/encadrement, la region est obligatoire.
                if target_role in {ROLE_MANAGER, "editor"} and region_input in (None, ""):
                    raise serializers.ValidationError(
                        {"region_id": "Chef de projet: region obligatoire pour ce role."}
                    )
                if region_input not in (None, ""):
                    resolved_region = self._resolve_region(region_input)

            attrs["_resolved_region"] = resolved_region
            attrs["_resolved_projects"] = resolved_projects

        return attrs

    def create(self, validated_data):
        validated_data.pop("password_confirm", None)
        project_ids = validated_data.pop("project_ids", [])
        region_id = validated_data.pop("region_id", None)
        resolved_region = validated_data.pop("_resolved_region", None)
        resolved_projects = validated_data.pop("_resolved_projects", None)
        password = validated_data.pop("password")

        user = UserModel.objects.create(**validated_data)
        user.set_password(password)
        user.save()

        if region_id is not None:
            resolved_region = resolved_region or self._resolve_region(region_id)
            user.region = resolved_region

        user_projects = []
        if hasattr(user, "projects"):
            user_projects = (
                resolved_projects
                if resolved_projects is not None
                else (self._resolve_projects(project_ids) or [])
            )
            user.projects.set(user_projects)

        if hasattr(user, "default_project"):
            user.default_project = user_projects[0] if user_projects else None

        user.save()
        return user


class AdminUserUpdateSerializer(AdminScopeValidationMixin, serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        validators=[validate_password],
        style={"input_type": "password"},
        help_text="Nouveau mot de passe (laisser vide pour ne pas changer)",
    )
    password_confirm = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        style={"input_type": "password"},
        help_text="Confirmation du nouveau mot de passe",
    )
    project_ids = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_null=True,
        write_only=True,
        help_text="Liste des UUIDs/codes de projets (null pour ne pas changer)",
    )
    region_id = serializers.CharField(
        required=False,
        allow_null=True,
        write_only=True,
        help_text="ID de la region (null pour retirer)",
    )

    class Meta:
        model = UserModel
        fields = [
            "username",
            "email",
            "password",
            "password_confirm",
            "first_name",
            "last_name",
            "role",
            "is_active",
            "is_superuser",
            "region_id",
            "project_ids",
        ]

    def _resolve_region_id(self, value):
        if value is None:
            return None
        value = str(value).strip()
        if not value:
            return None

        region = RefRegion.objects.filter(id_region=value).first()
        if region:
            return region.id_region

        region = RefRegion.objects.filter(nom__iexact=value).first()
        if region:
            return region.id_region

        raise serializers.ValidationError({"region_id": "Region invalide"})

    def _resolve_project_ids(self, raw_ids):
        if raw_ids is None:
            return None
        if not isinstance(raw_ids, (list, tuple)):
            raise serializers.ValidationError({"project_ids": "Format invalide (liste attendue)"})

        ids = [str(x).strip() for x in raw_ids if str(x).strip()]
        if not ids:
            return []

        uuid_ids, code_ids = AdminUserCreateSerializer._split_project_identifiers(ids)
        by_uuid = list(RefProject.objects.filter(project_id__in=uuid_ids)) if uuid_ids else []
        by_code = list(RefProject.objects.filter(code_fonc__in=code_ids)) if code_ids else []
        projects = list({str(p.project_id): p for p in by_uuid + by_code}.values())
        if not projects:
            raise serializers.ValidationError({"project_ids": "Aucun projet valide"})
        return projects

    def validate_email(self, value):
        user = self.instance
        if UserModel.objects.filter(email__iexact=value).exclude(id=user.id).exists():
            raise serializers.ValidationError("Cet email est deja utilise.")
        return value.lower()

    def validate_username(self, value):
        user = self.instance
        if UserModel.objects.filter(username__iexact=value).exclude(id=user.id).exists():
            raise serializers.ValidationError("Ce nom d'utilisateur est deja utilise.")
        return value

    def validate(self, attrs):
        password = attrs.get("password")
        password_confirm = attrs.get("password_confirm")
        if password or password_confirm:
            if password != password_confirm:
                raise serializers.ValidationError(
                    {"password_confirm": "Les mots de passe ne correspondent pas."}
                )

        actor_is_team_lead = self._is_team_lead_actor()
        actor_is_project_manager = self._is_project_manager_actor()

        if actor_is_team_lead or actor_is_project_manager:
            actor = self._request_actor()
            actor_scope = self._actor_scope_project_ids()
            if not actor_scope:
                raise serializers.ValidationError(
                    {"detail": "Votre compte n'a aucun projet actif assigne."}
                )

            instance_role = self._normalize_role(getattr(self.instance, "role", ""))
            allowed_roles = TEAM_LEAD_ALLOWED_ROLES if actor_is_team_lead else PROJECT_MANAGER_ALLOWED_ROLES
            if instance_role not in allowed_roles or getattr(self.instance, "is_superuser", False):
                raise serializers.ValidationError(
                    {"detail": "Utilisateur cible hors perimetre de gestion pour ce role."}
                )

            if actor_is_team_lead:
                actor_region_id = getattr(actor, "region_id", None)
                if not actor_region_id:
                    raise serializers.ValidationError(
                        {"detail": "Chef d'\u00e9quipe sans region assignee: mise a jour refusee."}
                    )
                if getattr(self.instance, "region_id", None) != actor_region_id:
                    raise serializers.ValidationError(
                        {"detail": "Chef d'\u00e9quipe: utilisateur cible hors region."}
                    )

            instance_project_ids = (
                {str(pid) for pid in self.instance.projects.values_list("project_id", flat=True)}
                if hasattr(self.instance, "projects")
                else set()
            )
            if not instance_project_ids or not instance_project_ids.issubset(actor_scope):
                raise serializers.ValidationError(
                    {"detail": "Utilisateur cible hors perimetre projet."}
                )

            if "role" in attrs:
                self._enforce_actor_target_role(attrs.get("role"))

            if attrs.get("is_superuser"):
                raise serializers.ValidationError(
                    {"is_superuser": "Elevation en superuser interdite pour ce role."}
                )

            effective_role = self._normalize_role(attrs.get("role", instance_role))

            if "region_id" in attrs:
                requested_region = attrs.get("region_id")
                if actor_is_team_lead:
                    if requested_region in (None, ""):
                        raise serializers.ValidationError(
                            {"region_id": "Chef d'\u00e9quipe: region vide interdite."}
                        )
                    resolved_region_id = self._resolve_region_id(requested_region)
                    if resolved_region_id != actor_region_id:
                        raise serializers.ValidationError(
                            {"region_id": "Chef d'\u00e9quipe: region hors perimetre."}
                        )
                    attrs["_resolved_region_id"] = resolved_region_id
                else:
                    if effective_role in {ROLE_MANAGER, "editor"} and requested_region in (None, ""):
                        raise serializers.ValidationError(
                            {"region_id": "Chef de projet: region obligatoire pour ce role."}
                        )
                    if requested_region not in (None, ""):
                        attrs["_resolved_region_id"] = self._resolve_region_id(requested_region)
            elif actor_is_project_manager and effective_role in {ROLE_MANAGER, "editor"}:
                if not getattr(self.instance, "region_id", None):
                    raise serializers.ValidationError(
                        {"region_id": "Chef de projet: region obligatoire pour ce role."}
                    )

            if "project_ids" in attrs:
                requested_project_ids = attrs.get("project_ids")
                if requested_project_ids is None:
                    pass
                else:
                    resolved_projects = self._resolve_project_ids(requested_project_ids)
                    resolved_ids = {str(p.project_id) for p in resolved_projects}
                    if not resolved_ids:
                        raise serializers.ValidationError(
                            {"project_ids": "Au moins un projet de votre perimetre est requis."}
                        )
                    if not resolved_ids.issubset(actor_scope):
                        raise serializers.ValidationError(
                            {"project_ids": "Projet(s) hors perimetre autorise."}
                        )
                    attrs["_resolved_projects"] = resolved_projects

        return attrs

    def update(self, instance, validated_data):
        validated_data.pop("password_confirm", None)
        project_ids = validated_data.pop("project_ids", None)
        region_id = validated_data.pop("region_id", None)
        password = validated_data.pop("password", None)
        resolved_region_id = validated_data.pop("_resolved_region_id", None)
        resolved_projects = validated_data.pop("_resolved_projects", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if password:
            instance.set_password(password)

        instance.save()

        if region_id is not None:
            if region_id in (None, ""):
                instance.region = None
            else:
                region_code = resolved_region_id or self._resolve_region_id(region_id)
                if region_code:
                    instance.region = RefRegion.objects.get(id_region=region_code)

        if project_ids is not None and hasattr(instance, "projects"):
            if isinstance(project_ids, list):
                final_projects = (
                    resolved_projects
                    if resolved_projects is not None
                    else self._resolve_project_ids(project_ids)
                )
                instance.projects.set(final_projects)

                if hasattr(instance, "default_project"):
                    instance.default_project = final_projects[0] if final_projects else None

        instance.save()
        return instance
