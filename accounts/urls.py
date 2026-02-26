from django.urls import path

from .views import (
    MeView,
    CurrentProjectView,
    LoginView,
    SignupView,
    EmailOrUsernameTokenView,
    ScopedTokenRefreshView,
)

urlpatterns = [
    # --- Auth applicative "confort" (login + user) ---
    # Permet un login direct avec retour user + tokens
    path("login/", LoginView.as_view(), name="accounts_login"),
    path("signup/", SignupView.as_view(), name="accounts_signup"),

    # --- Auth JWT "classique" ---
    # Utilisé par le front (Next) via /api/auth/login
    path("token/", EmailOrUsernameTokenView.as_view(), name="accounts_token_create"),
    path("token/refresh/", ScopedTokenRefreshView.as_view(), name="accounts_token_refresh"),

    # --- User courant & projet actif ---
    path("me/", MeView.as_view(), name="accounts_me"),
    path("current-project/", CurrentProjectView.as_view(), name="accounts_current_project"),
]
