# accounts/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User, RefProject, RefRegion


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    model = User

    # colonnes visibles dans la liste des utilisateurs
    list_display = ("username", "email", "role", "region", "default_project", "is_staff", "is_superuser")
    list_filter = ("role", "region", "projects", "is_staff", "is_superuser", "is_active")

    # formulaire d’édition d’un utilisateur existant
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Informations personnelles", {"fields": ("first_name", "last_name", "email")}),
        (
            "Rôles, région & projets",
            {
                "fields": (
                    "role",
                    "region",
                    "default_project",
                    "projects",
                )
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Dates importantes", {"fields": ("last_login", "date_joined")}),
    )

    # formulaire de création d’un nouvel utilisateur dans l’admin
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "password1",
                    "password2",
                    "first_name",
                    "last_name",
                    "email",
                    "role",
                    "region",
                    "default_project",
                    "projects",
                    "is_staff",
                    "is_superuser",
                    "is_active",
                    "groups",
                ),
            },
        ),
    )

    search_fields = ("username", "email", "first_name", "last_name")
    ordering = ("username",)
