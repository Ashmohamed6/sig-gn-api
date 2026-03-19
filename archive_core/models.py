from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class ProjectScope(models.TextChoices):
    AGRIECO = "AGRIECO", "AGRIECO"
    FIERE = "FIERE", "FIERE"
    BOTH = "BOTH", "Tous projets"


class ValueBehavior(models.TextChoices):
    POINT = "point", "Valeur ponctuelle"
    CUMULATIVE_YEARLY = "cumulative_yearly", "Cumul annuel"
    CUMULATIVE_CONTINUOUS = "cumulative_continuous", "Cumul continu"


class TrendPolarity(models.TextChoices):
    HIGHER_IS_BETTER = "higher_is_better", "Hausse favorable"
    LOWER_IS_BETTER = "lower_is_better", "Baisse favorable"
    NEUTRAL = "neutral", "Neutre"


class PeriodGranularity(models.TextChoices):
    YEAR = "year", "Annee"
    MONTH = "month", "Mois"


class ArchiveMetricDefinition(models.Model):
    metric_code = models.SlugField(max_length=80, unique=True)
    label = models.CharField(max_length=160)
    description = models.TextField(blank=True)

    project_scope = models.CharField(
        max_length=16,
        choices=ProjectScope.choices,
        default=ProjectScope.BOTH,
    )
    source_dataset = models.CharField(max_length=80, blank=True, default="")
    unit = models.CharField(max_length=32, blank=True, default="")

    value_behavior = models.CharField(
        max_length=32,
        choices=ValueBehavior.choices,
        default=ValueBehavior.POINT,
    )
    trend_polarity = models.CharField(
        max_length=20,
        choices=TrendPolarity.choices,
        default=TrendPolarity.HIGHER_IS_BETTER,
    )
    default_granularity = models.CharField(
        max_length=8,
        choices=PeriodGranularity.choices,
        default=PeriodGranularity.MONTH,
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["metric_code"]
        verbose_name = "Definition metrique archive"
        verbose_name_plural = "Definitions metriques archive"

    def __str__(self):
        return f"{self.metric_code} - {self.label}"


class ArchiveSnapshot(models.Model):
    metric = models.ForeignKey(
        ArchiveMetricDefinition,
        on_delete=models.CASCADE,
        related_name="snapshots",
    )

    project_code = models.CharField(max_length=50)
    region_id = models.CharField(max_length=50, blank=True, default="")
    entity_key = models.CharField(max_length=120, blank=True, default="")

    granularity = models.CharField(
        max_length=8,
        choices=PeriodGranularity.choices,
        default=PeriodGranularity.MONTH,
    )
    period_year = models.PositiveSmallIntegerField()
    period_month = models.PositiveSmallIntegerField(default=0)

    value = models.DecimalField(max_digits=20, decimal_places=4)

    source_dataset = models.CharField(max_length=80, blank=True, default="")
    source_reference = models.CharField(max_length=180, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    captured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="archive_snapshots",
    )
    captured_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [
            "metric__metric_code",
            "project_code",
            "region_id",
            "entity_key",
            "granularity",
            "period_year",
            "period_month",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "metric",
                    "project_code",
                    "region_id",
                    "entity_key",
                    "granularity",
                    "period_year",
                    "period_month",
                ],
                name="archive_snapshot_unique_period",
            )
        ]
        indexes = [
            models.Index(
                fields=["project_code", "granularity", "period_year", "period_month"],
                name="archive_period_idx",
            ),
            models.Index(fields=["metric", "project_code"], name="archive_metric_project_idx"),
            models.Index(fields=["region_id", "entity_key"], name="archive_region_entity_idx"),
        ]
        verbose_name = "Snapshot archive"
        verbose_name_plural = "Snapshots archive"

    def clean(self):
        if self.period_year < 2000 or self.period_year > 2100:
            raise ValidationError({"period_year": "L'annee doit etre comprise entre 2000 et 2100."})

        if self.granularity == PeriodGranularity.MONTH:
            if self.period_month < 1 or self.period_month > 12:
                raise ValidationError({"period_month": "Le mois doit etre compris entre 1 et 12."})
        else:
            if self.period_month != 0:
                raise ValidationError(
                    {"period_month": "Pour une granularite annuelle, period_month doit valoir 0."}
                )

    def save(self, *args, **kwargs):
        self.project_code = str(self.project_code or "").strip().upper()
        self.region_id = str(self.region_id or "").strip().upper()
        self.entity_key = str(self.entity_key or "").strip()
        self.source_dataset = str(self.source_dataset or "").strip()
        self.source_reference = str(self.source_reference or "").strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        month_label = f"-{self.period_month:02d}" if self.granularity == PeriodGranularity.MONTH else ""
        period = f"{self.period_year}{month_label}"
        return f"{self.metric.metric_code} | {self.project_code} | {period}"
