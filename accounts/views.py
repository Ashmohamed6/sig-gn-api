from django.contrib.auth import authenticate, get_user_model

from rest_framework import generics, permissions, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from .serializers import (
    CurrentUserSerializer,
    RefProjectSerializer,
    SignupSerializer,
    LoginSerializer,
    EmailOrUsernameTokenObtainPairSerializer,
)


UserModel = get_user_model()


class SignupView(generics.CreateAPIView):
    serializer_class = SignupSerializer
    permission_classes = [permissions.IsAdminUser]


class LoginView(APIView):
    """
    Endpoint "confort" /api/accounts/login/

    Retourne directement :
    {
      "access": "...",
      "refresh": "...",
      "user": { ... CurrentUserSerializer ... }
    }

    Le champ "username" peut contenir soit le username, soit l'email.
    """
    permission_classes = [permissions.AllowAny]
    serializer_class = LoginSerializer  # pour drf-spectacular

    def post(self, request, *args, **kwargs):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        username_or_email = serializer.validated_data["username"]
        password = serializer.validated_data["password"]

        # Si l'utilisateur a saisi un email, on le traduit en username
        if "@" in username_or_email:
            try:
                u = UserModel.objects.get(email__iexact=username_or_email)
                username = u.username
            except UserModel.DoesNotExist:
                username = username_or_email
        else:
            username = username_or_email

        user = authenticate(request, username=username, password=password)

        if user is None:
            return Response(
                {"detail": "Identifiants invalides."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {"detail": "Compte désactivé."},
                status=status.HTTP_403_FORBIDDEN,
            )

        refresh = RefreshToken.for_user(user)
        user_data = CurrentUserSerializer(user, context={"request": request}).data

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": user_data,
            },
            status=status.HTTP_200_OK,
        )


class EmailOrUsernameTokenView(TokenObtainPairView):
    """
    Endpoint JWT standard /api/accounts/token/

    Utilisé par le frontend pour poser les cookies.
    Accepte un username OU un email dans le champ "username".
    """
    serializer_class = EmailOrUsernameTokenObtainPairSerializer


class MeView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CurrentUserSerializer  # pour drf-spectacular

    def get(self, request):
        serializer = CurrentUserSerializer(request.user)
        return Response(serializer.data)


class CurrentProjectView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = RefProjectSerializer  # pour drf-spectacular

    def get(self, request):
        project = getattr(request, "current_project", None)
        if project is None:
            return Response({"detail": "Aucun projet actif"}, status=400)

        serializer = RefProjectSerializer(project)
        return Response(serializer.data)
