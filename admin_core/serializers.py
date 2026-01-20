# admin_core/serializers.py
"""
Serializers pour l'administration - Version corrigée avec projets
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

UserModel = get_user_model()


class AdminUserListSerializer(serializers.ModelSerializer):
    """
    Serializer pour la liste des utilisateurs
    """
    full_name = serializers.SerializerMethodField()
    project_count = serializers.SerializerMethodField()
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    projects = serializers.SerializerMethodField()  # ✅ AJOUTÉ !
    region_data = serializers.SerializerMethodField()  # ✅ AJOUTÉ !
    
    class Meta:
        model = UserModel
        fields = [
            'id',
            'username',
            'email',
            'first_name',
            'last_name',
            'full_name',
            'role',
            'role_display',
            'is_active',
            'is_superuser',
            'date_joined',
            'last_login',
            'project_count',
            'projects',  # ✅ AJOUTÉ !
            'region',
            'region_data',  # ✅ AJOUTÉ !
        ]
    
    def get_full_name(self, obj):
        """Nom complet de l'utilisateur"""
        full = f"{obj.first_name} {obj.last_name}".strip()
        return full if full else obj.username
    
    def get_project_count(self, obj):
        """Nombre de projets assignés"""
        return obj.projects.count() if hasattr(obj, 'projects') else 0
    
    def get_projects(self, obj):
        """Liste des projets de l'utilisateur"""
        if not hasattr(obj, 'projects'):
            return []
        
        return [
            {
                'project_id': str(p.project_id),
                'code_fonc': p.code_fonc,
                'libelle_public': p.libelle_public,
            }
            for p in obj.projects.all()
        ]
    
    def get_region_data(self, obj):
        """Données de la région"""
        if obj.region:
            return {
                'id_region': obj.region.id_region,
                'nom': obj.region.nom,
            }
        return None


class AdminUserDetailSerializer(serializers.ModelSerializer):
    """
    Serializer détaillé pour un utilisateur
    """
    full_name = serializers.SerializerMethodField()
    projects = serializers.SerializerMethodField()
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    region_data = serializers.SerializerMethodField()
    
    class Meta:
        model = UserModel
        fields = [
            'id',
            'username',
            'email',
            'first_name',
            'last_name',
            'full_name',
            'role',
            'role_display',
            'is_active',
            'is_superuser',
            'is_staff',
            'date_joined',
            'last_login',
            'created_at',
            'updated_at',
            'region',
            'region_data',
            'default_project',
            'projects',
        ]
    
    def get_full_name(self, obj):
        """Nom complet"""
        full = f"{obj.first_name} {obj.last_name}".strip()
        return full if full else obj.username
    
    def get_region_data(self, obj):
        """Données de la région"""
        if obj.region:
            return {
                'id_region': obj.region.id_region,
                'nom': obj.region.nom,
            }
        return None
    
    def get_projects(self, obj):
        """Liste des projets de l'utilisateur"""
        if not hasattr(obj, 'projects'):
            return []
        
        return [
            {
                'project_id': str(p.project_id),
                'code_fonc': p.code_fonc,
                'libelle_public': p.libelle_public,
            }
            for p in obj.projects.all()
        ]


class AdminUserCreateSerializer(serializers.ModelSerializer):
    """
    Serializer pour créer un utilisateur
    """
    password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password],
        style={'input_type': 'password'},
        help_text="Mot de passe (min 8 caractères)"
    )
    password_confirm = serializers.CharField(
        write_only=True,
        required=True,
        style={'input_type': 'password'},
        help_text="Confirmation du mot de passe"
    )
    project_ids = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        write_only=True,
        help_text="Liste des UUIDs de projets à assigner"
    )
    region_id = serializers.CharField(
        required=False,
        allow_null=True,
        write_only=True,
        help_text="ID de la région"
    )

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------
    def _resolve_region(self, raw: str | None):
        """Résout une région depuis un code (GN00X) ou un libellé (ex: KINDIA/Kindia)."""
        if raw is None:
            return None
        value = str(raw).strip()
        if value == "":
            return None

        # 1) Code exact (id_region)
        region = RefRegion.objects.filter(id_region=value).first()
        if region:
            return region

        # 2) Libellé (nom)
        region = RefRegion.objects.filter(nom__iexact=value).first()
        if region:
            return region

        # 3) Certaines UI envoient des codes en MAJ (KINDIA) alors que la table stocke "Kindia"
        region = RefRegion.objects.filter(nom__iexact=value.replace("_", " ")).first()
        if region:
            return region

        raise serializers.ValidationError("Région invalide")

    def _resolve_projects(self, raw_ids: list[str] | None):
        """Résout des projets depuis UUIDs (project_id) ET/OU codes (code_fonc = AGRIECO/FIERE)."""
        if raw_ids is None:
            return None
        ids = [str(x).strip() for x in raw_ids if str(x).strip()]
        if not ids:
            return []

        # 1) UUIDs (project_id)
        qs_uuid = RefProject.objects.filter(project_id__in=ids)
        # 2) Codes (code_fonc)
        qs_code = RefProject.objects.filter(code_fonc__in=ids)

        projects = list((qs_uuid | qs_code).distinct())
        # Si on a demandé des projets mais qu'aucun ne correspond, on préfère une erreur explicite.
        if not projects:
            raise serializers.ValidationError("Projet(s) invalide(s)")
        return projects
    
    class Meta:
        model = UserModel
        fields = [
            'username',
            'email',
            'password',
            'password_confirm',
            'first_name',
            'last_name',
            'role',
            'is_active',
            'is_superuser',
            'region_id',
            'project_ids',
        ]
    
    def validate_email(self, value):
        """Vérifier que l'email n'est pas déjà utilisé"""
        if UserModel.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Cet email est déjà utilisé.")
        return value.lower()
    
    def validate_username(self, value):
        """Vérifier que le username n'est pas déjà utilisé"""
        if UserModel.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Ce nom d'utilisateur est déjà utilisé.")
        return value
    
    def validate(self, attrs):
        """Validation croisée"""
        # Vérifier que les mots de passe correspondent
        if attrs.get('password') != attrs.get('password_confirm'):
            raise serializers.ValidationError({
                'password_confirm': "Les mots de passe ne correspondent pas."
            })
        
        return attrs
    
    def create(self, validated_data):
        """Créer l'utilisateur avec mot de passe hashé"""
        # Retirer les champs non-model
        validated_data.pop('password_confirm', None)
        project_ids = validated_data.pop('project_ids', [])
        region_id = validated_data.pop('region_id', None)
        
        # Extraire le mot de passe
        password = validated_data.pop('password')
        
        # Créer l'utilisateur
        user = UserModel.objects.create(**validated_data)
        user.set_password(password)
        user.save()
        
        # Assigner la région (accepte GN00X ou libellé, ex: KINDIA)
        if region_id is not None:
            resolved_region_id = self._resolve_region_id(region_id)
            if resolved_region_id:
                from accounts.models import RefRegion
                user.region = RefRegion.objects.get(id_region=resolved_region_id)

        # Assigner les projets (accepte UUID(s) OU code_fonc, ex: AGRIECO/FIERE)
        resolved_projects = []
        if hasattr(user, 'projects'):
            resolved_projects = self._resolve_project_ids(project_ids)
            user.projects.set(resolved_projects)

        # Définir le projet par défaut si le champ existe
        if hasattr(user, 'default_project'):
            user.default_project = resolved_projects[0] if resolved_projects else None

        user.save()
        
        return user


class AdminUserUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer pour mettre à jour un utilisateur
    """
    password = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        validators=[validate_password],
        style={'input_type': 'password'},
        help_text="Nouveau mot de passe (laisser vide pour ne pas changer)"
    )
    password_confirm = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        style={'input_type': 'password'},
        help_text="Confirmation du nouveau mot de passe"
    )
    project_ids = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_null=True,
        write_only=True,
        help_text="Liste des UUIDs de projets (null pour ne pas changer)"
    )
    region_id = serializers.CharField(
        required=False,
        allow_null=True,
        write_only=True,
        help_text="ID de la région (null pour retirer)"
    )

    # ------------------------------------------------------------
    # Helpers (mêmes règles que la création)
    # ------------------------------------------------------------
    def _resolve_region_id(self, value):
        """Résout une région depuis un code (GN00X) ou un libellé (ex: KINDIA/Kindia)."""
        if value is None:
            return None
        value = str(value).strip()
        if not value:
            return None

        # 1) code officiel
        region = RefRegion.objects.filter(id_region=value).first()
        if region:
            return region.id_region

        # 2) libellé
        region = RefRegion.objects.filter(nom__iexact=value).first()
        if region:
            return region.id_region

        raise serializers.ValidationError({"region_id": "Région invalide"})

    def _resolve_project_ids(self, raw_ids):
        """Résout des projets depuis des UUIDs OU des codes (ex: AGRIECO/FIERE)."""
        if raw_ids is None:
            return None
        if not isinstance(raw_ids, (list, tuple)):
            raise serializers.ValidationError({"project_ids": "Format invalide (liste attendue)"})

        ids = [str(x).strip() for x in raw_ids if str(x).strip()]
        if not ids:
            return []

        by_uuid = RefProject.objects.filter(project_id__in=ids)
        by_code = RefProject.objects.filter(code_fonc__in=ids)
        projects = list({p.project_id: p for p in list(by_uuid) + list(by_code)}.values())

        if not projects:
            raise serializers.ValidationError({"project_ids": "Aucun projet valide"})
        return projects
    
    class Meta:
        model = UserModel
        fields = [
            'username',
            'email',
            'password',
            'password_confirm',
            'first_name',
            'last_name',
            'role',
            'is_active',
            'is_superuser',
            'region_id',
            'project_ids',
        ]
    
    def validate_email(self, value):
        """Vérifier l'unicité de l'email (sauf pour l'utilisateur actuel)"""
        user = self.instance
        if UserModel.objects.filter(email__iexact=value).exclude(id=user.id).exists():
            raise serializers.ValidationError("Cet email est déjà utilisé.")
        return value.lower()
    
    def validate_username(self, value):
        """Vérifier l'unicité du username (sauf pour l'utilisateur actuel)"""
        user = self.instance
        if UserModel.objects.filter(username__iexact=value).exclude(id=user.id).exists():
            raise serializers.ValidationError("Ce nom d'utilisateur est déjà utilisé.")
        return value
    
    def validate(self, attrs):
        """Validation croisée"""
        password = attrs.get('password')
        password_confirm = attrs.get('password_confirm')
        
        # Si un mot de passe est fourni, vérifier la confirmation
        if password or password_confirm:
            if password != password_confirm:
                raise serializers.ValidationError({
                    'password_confirm': "Les mots de passe ne correspondent pas."
                })
        
        return attrs
    
    def update(self, instance, validated_data):
        """Mettre à jour l'utilisateur"""
        # Retirer les champs non-model
        validated_data.pop('password_confirm', None)
        project_ids = validated_data.pop('project_ids', None)
        region_id = validated_data.pop('region_id', None)
        password = validated_data.pop('password', None)
        
        # Mettre à jour les champs standards
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        # Mettre à jour le mot de passe si fourni
        if password:
            instance.set_password(password)
        
        instance.save()
        
        # Mettre à jour la région
        # - champ absent => pas de changement
        # - null / "" => suppression
        if region_id is not None:
            if region_id in (None, ""):
                instance.region = None
            else:
                resolved_region_id = self._resolve_region_id(region_id)
                if resolved_region_id:
                    from accounts.models import RefRegion
                    instance.region = RefRegion.objects.get(id_region=resolved_region_id)

        # Mettre à jour les projets
        # - champ absent => pas de changement
        # - null => pas de changement (compat)
        # - liste (même vide) => remplacement
        if project_ids is not None and hasattr(instance, "projects"):
            if isinstance(project_ids, list):
                resolved_projects = self._resolve_project_ids(project_ids)
                instance.projects.set(resolved_projects)

                # Synchroniser le projet par défaut
                if hasattr(instance, "default_project"):
                    instance.default_project = resolved_projects[0] if resolved_projects else None
        
        instance.save()
        return instance