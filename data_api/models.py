from django.db import models


class EntrepriseEcon(models.Model):
    """
    Mapping de la vue marts.vw_entreprise_econ côté Django.
    On ne déclare que les champs utiles pour les listes + agrégations.
    """
    ent_uuid = models.UUIDField(
        primary_key=True,
        db_column="ent_uuid",
        editable=False,
    )
    project_code = models.CharField(
        max_length=50,
        db_column="project_code",
        blank=True,
        null=True,
    )
    id_ent = models.CharField(
        max_length=100,
        db_column="id_ent",
        blank=True,
        null=True,
    )
    raison_sociale = models.CharField(
        max_length=255,
        db_column="raison_sociale",
        blank=True,
        null=True,
    )
    secteur_principal_label = models.CharField(
        max_length=255,
        db_column="secteur_principal_label",
        blank=True,
        null=True,
    )
    taille_entreprise_label = models.CharField(
        max_length=255,
        db_column="taille_entreprise_label",
        blank=True,
        null=True,
    )
    id_region = models.IntegerField(
        db_column="id_region",
        blank=True,
        null=True,
    )
    region_nom = models.CharField(
        max_length=255,
        db_column="region_nom",
        blank=True,
        null=True,
    )
    id_commune = models.IntegerField(
        db_column="id_commune",
        blank=True,
        null=True,
    )
    commune_nom = models.CharField(
        max_length=255,
        db_column="commune_nom",
        blank=True,
        null=True,
    )
    est_mpme_formalisee = models.BooleanField(
        db_column="est_mpme_formalisee",
        blank=True,
        null=True,
    )
    est_mpme_appuyee_fiere = models.BooleanField(
        db_column="est_mpme_appuyee_fiere",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(
        db_column="created_at",
        blank=True,
        null=True,
    )
    updated_at = models.DateTimeField(
        db_column="updated_at",
        blank=True,
        null=True,
    )

    class Meta:
        managed = False  # très important : la vue existe déjà en base
        db_table = 'marts"."vw_entreprise_econ'
        verbose_name = "Entreprise (vue métiers)"
        verbose_name_plural = "Entreprises (vue métiers)"

    def __str__(self) -> str:
        return self.raison_sociale or self.id_ent or str(self.ent_uuid)
