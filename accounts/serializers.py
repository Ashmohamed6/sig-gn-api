from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import User, RefProject


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
