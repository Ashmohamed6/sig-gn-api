from django.contrib.auth.models import AbstractUser
from django.db import models


class RefRegion(models.Model):
    id_region = models.CharField(
        primary_key=True,
        max_length=50,
        db_column="id_region",
    )
    nom = models.CharField(max_length=255, db_column="nom", blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'ref"."admin_region'
        verbose_name = "région (référentiel)"
        verbose_name_plural = "régions (référentiel)"

    def __str__(self) -> str:
        return self.nom or self.id_region


class RefProject(models.Model):
    project_id = models.UUIDField(
        primary_key=True,
        db_column="project_id",
        editable=False,
    )
    code_kobo = models.CharField(max_length=50, db_column="code_kobo", blank=True, null=True)
    code_fonc = models.CharField(max_length=50, db_column="code_fonc", blank=True, null=True)
    libelle_public = models.CharField(max_length=255, db_column="libelle_public", blank=True, null=True)
    libelle_officiel = models.CharField(max_length=255, db_column="libelle_officiel", blank=True, null=True)
    date_debut = models.DateField(db_column="date_debut", blank=True, null=True)
    date_fin = models.DateField(db_column="date_fin", blank=True, null=True)
    actif = models.BooleanField(db_column="actif", default=True)

    class Meta:
        managed = False
        db_table = 'ref"."projet'
        verbose_name = "projet (référentiel)"
        verbose_name_plural = "projets (référentiel)"

    def __str__(self) -> str:
        return self.libelle_public or self.code_fonc or str(self.project_id)


class UserRole(models.TextChoices):
    READER = "reader", "Lecteur"
    EDITOR = "editor", "Éditeur / Analyste"
    MANAGER = "manager", "Chef de projet"
    ADMIN = "admin", "Admin"


class User(AbstractUser):
    role = models.CharField(
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.READER,
    )

    # Région d’affectation (Kindia / Mamou)
    region = models.ForeignKey(
        RefRegion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="region_id",
        related_name="users",
        help_text="Région à laquelle l'utilisateur est rattaché.",
    )

    # Projet actif par défaut
    default_project = models.ForeignKey(
        RefProject,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_users",
        help_text="Projet actif par défaut après connexion.",
    )

    # Projets accessibles
    projects = models.ManyToManyField(
        RefProject,
        related_name="users",
        blank=True,
        help_text="Projets du référentiel ref.projet auxquels l'utilisateur a accès.",
    )


    # ✅ AJOUTEZ CES 2 LIGNES
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Date de création")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Dernière modification")


    class Meta:
        verbose_name = "utilisateur"
        verbose_name_plural = "utilisateurs"

    def __str__(self) -> str:
        return self.get_full_name() or self.username
