import os
import json
from pathlib import Path
from datetime import timedelta

import environ
from django.core.exceptions import ImproperlyConfigured
from corsheaders.defaults import default_headers

# -----------------------------------------------------------------------------
# Base
# -----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# -----------------------------------------------------------------------------
# Environ (.env) - ne casse pas si le fichier n'existe pas (docker)
# -----------------------------------------------------------------------------
env = environ.Env(DEBUG=(bool, False))

env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)

def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")

def env_list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


def env_json(name: str, default):
    raw = os.getenv(name, "")
    if not raw.strip():
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ImproperlyConfigured(f"{name} doit contenir un JSON valide.") from exc

# -----------------------------------------------------------------------------
# Sécurité / Debug / Hosts
# -----------------------------------------------------------------------------
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", env("SECRET_KEY", default="change-me-in-env"))
DEBUG = env_bool("DJANGO_DEBUG", False)

# Pour docker dev: backend, localhost, 127.0.0.1, 0.0.0.0
ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    "localhost,127.0.0.1,0.0.0.0,backend"
)

# -----------------------------------------------------------------------------
# Applications
# -----------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "rest_framework",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
    "rest_framework_simplejwt",

    "accounts",
    "dashboard",
    "data_api",
    "admin_core",
    "workflow_core",
    "import_core",
]

# -----------------------------------------------------------------------------
# Middlewares
# -----------------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",  # CORS avant CommonMiddleware
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.CurrentProjectMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

# -----------------------------------------------------------------------------
# Templates
# -----------------------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# -----------------------------------------------------------------------------
# Base de données (Docker: HOST=db par défaut)
# -----------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "sig_territoires_gn"),
        "USER": os.getenv("DB_USER", "postgres"),
        "PASSWORD": os.getenv("DB_PASSWORD", "postgres"),
        "HOST": os.getenv("DB_HOST", "db"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }
}

# -----------------------------------------------------------------------------
# User model custom
# -----------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

# -----------------------------------------------------------------------------
# Password validators
# -----------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# -----------------------------------------------------------------------------
# i18n
# -----------------------------------------------------------------------------
LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Africa/Conakry"
USE_I18N = True
USE_TZ = True

# -----------------------------------------------------------------------------
# Static files
# -----------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

_static_dir = BASE_DIR / "static"
STATICFILES_DIRS = [_static_dir] if _static_dir.exists() else []

# -----------------------------------------------------------------------------
# DRF
# -----------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": int(os.getenv("DJANGO_PAGE_SIZE", "50")),
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": os.getenv("DRF_THROTTLE_ANON", "60/min"),
        "user": os.getenv("DRF_THROTTLE_USER", "300/min"),
        "auth": os.getenv("DRF_THROTTLE_AUTH", "10/min"),
        "auth_identifier": os.getenv("DRF_THROTTLE_AUTH_IDENTIFIER", "15/min"),
        "token_refresh": os.getenv("DRF_THROTTLE_TOKEN_REFRESH", "20/min"),
        "stats": os.getenv("DRF_THROTTLE_STATS", "90/min"),
        "geojson": os.getenv("DRF_THROTTLE_GEOJSON", "120/min"),
        "admin_read": os.getenv("DRF_THROTTLE_ADMIN_READ", "120/min"),
        "admin_write": os.getenv("DRF_THROTTLE_ADMIN_WRITE", "40/min"),
        "workflow_read": os.getenv("DRF_THROTTLE_WORKFLOW_READ", "180/min"),
        "workflow_write": os.getenv("DRF_THROTTLE_WORKFLOW_WRITE", "60/min"),
        "import_read": os.getenv("DRF_THROTTLE_IMPORT_READ", "120/min"),
        "import_write": os.getenv("DRF_THROTTLE_IMPORT_WRITE", "40/min"),
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "SIG GN API",
    "DESCRIPTION": "API backend pour le dispositif WebSIG FIERE / AGRIECO en Guinée",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SECURITY": [{"BearerAuth": []}],
    "COMPONENT_SECURITY_SCHEMES": {
        "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    },
}

# -----------------------------------------------------------------------------
# JWT
# -----------------------------------------------------------------------------
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(os.getenv("JWT_ACCESS_MINUTES", "60"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(os.getenv("JWT_REFRESH_DAYS", "1"))),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# -----------------------------------------------------------------------------
# CORS / CSRF (docker-friendly)
# -----------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env_list(
    "DJANGO_CORS_ALLOWED_ORIGINS",
    "http://localhost:3000"
)

CORS_ALLOW_ALL_ORIGINS = env_bool("DJANGO_CORS_ALLOW_ALL_ORIGINS", False)

CORS_ALLOW_HEADERS = list(default_headers) + [
    "x-project-code",
]
CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = env_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "http://localhost:3000,http://localhost:8000"
)

# -----------------------------------------------------------------------------
# Security hardening
# -----------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = os.getenv("DJANGO_SESSION_COOKIE_SAMESITE", "Lax")
CSRF_COOKIE_SAMESITE = os.getenv("DJANGO_CSRF_COOKIE_SAMESITE", "Lax")
SESSION_COOKIE_SECURE = env_bool("DJANGO_SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("DJANGO_CSRF_COOKIE_SECURE", not DEBUG)

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
REFERRER_POLICY = os.getenv("DJANGO_REFERRER_POLICY", "strict-origin-when-cross-origin")

# -----------------------------------------------------------------------------
# Kobo direct sync (workflow)
# -----------------------------------------------------------------------------
KOBO_SYNC_ENABLED = env_bool("KOBO_SYNC_ENABLED", False)
KOBO_BASE_URL = os.getenv("KOBO_BASE_URL", "https://kf.kobotoolbox.org").rstrip("/")
KOBO_API_TOKEN = os.getenv("KOBO_API_TOKEN", "").strip()
KOBO_HTTP_TIMEOUT = int(os.getenv("KOBO_HTTP_TIMEOUT", "20"))
KOBO_SYNC_MAX_RECORDS = int(os.getenv("KOBO_SYNC_MAX_RECORDS", "500"))
KOBO_SYNC_MAX_PAYLOAD_RECORDS = int(os.getenv("KOBO_SYNC_MAX_PAYLOAD_RECORDS", "200"))
KOBO_FORM_REGISTRY = env_json("KOBO_FORM_REGISTRY", {})

# Keep redirect opt-in via env because some deployments are still HTTP-only.
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", False)
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_SECURE_HSTS_SECONDS", "31536000" if not DEBUG else "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", not DEBUG)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", False)


def validate_production_security() -> None:
    """
    Fail-fast en production pour eviter les deploiements dangereux.
    """
    if DEBUG:
        return

    # Secrets obligatoires (pas de placeholders).
    weak_secret_values = {"change-me-in-env", "CHANGE_ME_DJANGO_SECRET_KEY"}
    if not SECRET_KEY or SECRET_KEY in weak_secret_values or SECRET_KEY.startswith("CHANGE_ME"):
        raise ImproperlyConfigured("DJANGO_SECRET_KEY invalide pour la production.")

    db_password = os.getenv("DB_PASSWORD", "")
    if not db_password or db_password.startswith("CHANGE_ME"):
        raise ImproperlyConfigured("DB_PASSWORD invalide pour la production.")

    if KOBO_SYNC_ENABLED and (not KOBO_API_TOKEN or KOBO_API_TOKEN.startswith("CHANGE_ME")):
        raise ImproperlyConfigured("KOBO_API_TOKEN invalide pour la production quand KOBO_SYNC_ENABLED=1.")

    # Cookies transportes uniquement en HTTPS.
    if not SESSION_COOKIE_SECURE or not CSRF_COOKIE_SECURE:
        raise ImproperlyConfigured("SESSION/CSRF cookies doivent etre secure en production.")

    # HTTPS obligatoire.
    if not SECURE_SSL_REDIRECT:
        raise ImproperlyConfigured("DJANGO_SECURE_SSL_REDIRECT doit etre active en production.")

    # CORS/CSRF stricts.
    if CORS_ALLOW_ALL_ORIGINS:
        raise ImproperlyConfigured("DJANGO_CORS_ALLOW_ALL_ORIGINS=1 est interdit en production.")

    invalid_csrf_origins = [o for o in CSRF_TRUSTED_ORIGINS if not o.lower().startswith("https://")]
    if invalid_csrf_origins:
        raise ImproperlyConfigured(
            f"CSRF_TRUSTED_ORIGINS doit utiliser HTTPS en production: {invalid_csrf_origins}"
        )

    invalid_cors_origins = [o for o in CORS_ALLOWED_ORIGINS if not o.lower().startswith("https://")]
    if invalid_cors_origins:
        raise ImproperlyConfigured(
            f"CORS_ALLOWED_ORIGINS doit utiliser HTTPS en production: {invalid_cors_origins}"
        )


validate_production_security()

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CURRENT_PROJECT_HEADER = "HTTP_X_PROJECT_CODE"
