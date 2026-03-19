from django.core.management.base import BaseCommand

from archive_core.models import (
    ArchiveMetricDefinition,
    PeriodGranularity,
    ProjectScope,
    TrendPolarity,
    ValueBehavior,
)


DEFAULT_METRICS = [
    {
        "metric_code": "agrieco_surface_cultivee_ha",
        "label": "Surface cultivee (ha)",
        "description": "Surface cultivee totale par periode. Peut etre comparee entre annees pour la meme parcelle via entity_key.",
        "project_scope": ProjectScope.AGRIECO,
        "source_dataset": "cep-parcelles",
        "unit": "ha",
        "value_behavior": ValueBehavior.POINT,
        "trend_polarity": TrendPolarity.HIGHER_IS_BETTER,
        "default_granularity": PeriodGranularity.YEAR,
    },
    {
        "metric_code": "agrieco_surface_restauree_ha",
        "label": "Surface restauree (ha)",
        "description": "Cumul mensuel de la surface restauree sur l'annee.",
        "project_scope": ProjectScope.AGRIECO,
        "source_dataset": "zone-degradee",
        "unit": "ha",
        "value_behavior": ValueBehavior.CUMULATIVE_YEARLY,
        "trend_polarity": TrendPolarity.HIGHER_IS_BETTER,
        "default_granularity": PeriodGranularity.MONTH,
    },
    {
        "metric_code": "fiere_sortants_inseres",
        "label": "Sortants inseres",
        "description": "Nombre de sortants inseres professionnellement.",
        "project_scope": ProjectScope.FIERE,
        "source_dataset": "fiere-suivi-sortants",
        "unit": "personnes",
        "value_behavior": ValueBehavior.POINT,
        "trend_polarity": TrendPolarity.HIGHER_IS_BETTER,
        "default_granularity": PeriodGranularity.MONTH,
    },
    {
        "metric_code": "fiere_taux_insertion_pct",
        "label": "Taux d'insertion (%)",
        "description": "Taux d'insertion observe sur la periode.",
        "project_scope": ProjectScope.FIERE,
        "source_dataset": "fiere-suivi-sortants",
        "unit": "%",
        "value_behavior": ValueBehavior.POINT,
        "trend_polarity": TrendPolarity.HIGHER_IS_BETTER,
        "default_granularity": PeriodGranularity.MONTH,
    },
]


class Command(BaseCommand):
    help = "Initialise les metriques de reference pour archive_core."

    def add_arguments(self, parser):
        parser.add_argument(
            "--disable-missing",
            action="store_true",
            help="Desactive les metriques existantes qui ne sont pas dans la liste par defaut.",
        )

    def handle(self, *args, **options):
        created = 0
        updated = 0

        expected_codes = {item["metric_code"] for item in DEFAULT_METRICS}

        for payload in DEFAULT_METRICS:
            _, was_created = ArchiveMetricDefinition.objects.update_or_create(
                metric_code=payload["metric_code"],
                defaults={
                    "label": payload["label"],
                    "description": payload["description"],
                    "project_scope": payload["project_scope"],
                    "source_dataset": payload["source_dataset"],
                    "unit": payload["unit"],
                    "value_behavior": payload["value_behavior"],
                    "trend_polarity": payload["trend_polarity"],
                    "default_granularity": payload["default_granularity"],
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        disabled = 0
        if options.get("disable_missing"):
            disabled = (
                ArchiveMetricDefinition.objects.exclude(metric_code__in=expected_codes)
                .filter(is_active=True)
                .update(is_active=False)
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Metriques archive seedees. created={created}, updated={updated}, disabled={disabled}."
            )
        )
