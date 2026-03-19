from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import User, RefProject

UserModel = get_user_model()


class RefProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = RefProject
        fields = [
            "project_id",
            "code_fonc",
            "libelle_public",
            "actif",
        ]


class CurrentUserSerializer(serializers.ModelSerializer):
    """
    Sérialisation de l'utilisateur courant pour le front :
    - infos de base
    - rôle
    - liste des projets
    """

    projects = RefProjectSerializer(many=True, read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "role",
            "projects",
        ]


class CurrentUserUpdateSerializer(serializers.ModelSerializer):
    """
    Mise a jour des informations de profil de l'utilisateur courant.
    """

    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
        ]
        extra_kwargs = {
            "username": {"required": False},
            "first_name": {"required": False, "allow_blank": True},
            "last_name": {"required": False, "allow_blank": True},
            "email": {"required": False},
        }

    def validate_username(self, value):
        cleaned = str(value or "").strip()
        if not cleaned:
            raise serializers.ValidationError("Le nom d'utilisateur ne peut pas etre vide.")

        user = self.instance
        queryset = UserModel.objects.filter(username__iexact=cleaned)
        if user is not None:
            queryset = queryset.exclude(id=user.id)
        if queryset.exists():
            raise serializers.ValidationError("Ce nom d'utilisateur est deja utilise.")
        return cleaned

    def validate_email(self, value):
        cleaned = str(value or "").strip().lower()
        if not cleaned:
            raise serializers.ValidationError("L'adresse e-mail ne peut pas etre vide.")

        user = self.instance
        queryset = UserModel.objects.filter(email__iexact=cleaned)
        if user is not None:
            queryset = queryset.exclude(id=user.id)
        if queryset.exists():
            raise serializers.ValidationError("Cet email est deja utilise.")
        return cleaned


class ChangeOwnPasswordSerializer(serializers.Serializer):
    """
    Changement du mot de passe pour l'utilisateur courant.
    """

    current_password = serializers.CharField(write_only=True, style={"input_type": "password"})
    new_password = serializers.CharField(write_only=True, style={"input_type": "password"})
    new_password_confirm = serializers.CharField(write_only=True, style={"input_type": "password"})

    default_error_messages = {
        "not_authenticated": "Utilisateur non authentifie.",
    }

    def validate(self, attrs):
        user = self.context.get("user")
        if not user or not getattr(user, "is_authenticated", False):
            raise serializers.ValidationError({"detail": self.error_messages["not_authenticated"]})

        current_password = attrs.get("current_password", "")
        new_password = attrs.get("new_password", "")
        new_password_confirm = attrs.get("new_password_confirm", "")

        if not user.check_password(current_password):
            raise serializers.ValidationError(
                {"current_password": "Le mot de passe actuel est incorrect."}
            )

        if new_password != new_password_confirm:
            raise serializers.ValidationError(
                {"new_password_confirm": "Les mots de passe ne correspondent pas."}
            )

        if current_password and new_password and current_password == new_password:
            raise serializers.ValidationError(
                {"new_password": "Le nouveau mot de passe doit etre different de l'ancien."}
            )

        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": list(exc.messages)})

        return attrs


class SignupSerializer(serializers.ModelSerializer):
    """
    Utilisé pour l'endpoint /api/accounts/signup/

    Par défaut, on réserve cet endpoint aux admins (cf. views.py).
    """

    password = serializers.CharField(write_only=True, min_length=8)

    # L'admin peut rattacher directement des projets à l'utilisateur
    projects = serializers.PrimaryKeyRelatedField(
        queryset=RefProject.objects.all(),
        many=True,
        required=False,
    )

    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "password",
            "role",
            "projects",
        ]
        extra_kwargs = {
            "role": {"required": False},
        }

    def create(self, validated_data):
        projects = validated_data.pop("projects", [])
        password = validated_data.pop("password")

        user = User(**validated_data)
        user.set_password(password)
        user.save()

        if projects:
            user.projects.set(projects)

        return user


class LoginSerializer(serializers.Serializer):
    """
    Payload attendu pour /api/accounts/login/ :

    {
      "username": "....",  // peut être un username OU un email
      "password": "...."
    }
    """

    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


class EmailOrUsernameTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Serializer utilisé par /api/accounts/token/

    Permet de se connecter avec :
    - soit le nom d'utilisateur
    - soit l'adresse e-mail (champ "username" contient alors l'email)
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # Infos supplémentaires dans le payload JWT
        token["username"] = user.username
        token["email"] = user.email
        token["role"] = getattr(user, "role", "")
        return token

    def validate(self, attrs):
        username = attrs.get("username")
        UserModel = get_user_model()

        # Si ce qui est passé dans "username" ressemble à un email,
        # on va chercher le vrai username correspondant.
        if username and "@" in username:
            try:
                u = UserModel.objects.get(email__iexact=username, is_active=True)
                attrs["username"] = u.username
            except UserModel.DoesNotExist:
                # on laisse le username tel quel -> simplejwt renverra une 401 standard
                pass

        return super().validate(attrs)
