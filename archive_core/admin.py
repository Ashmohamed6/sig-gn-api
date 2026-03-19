from django.contrib import admin

from .models import ArchiveMetricDefinition, ArchiveSnapshot


@admin.register(ArchiveMetricDefinition)
class ArchiveMetricDefinitionAdmin(admin.ModelAdmin):
    list_display = (
        "metric_code",
        "label",
        "project_scope",
        "source_dataset",
        "value_behavior",
        "trend_polarity",
        "is_active",
    )
    list_filter = ("project_scope", "value_behavior", "trend_polarity", "is_active")
    search_fields = ("metric_code", "label", "description", "source_dataset")


@admin.register(ArchiveSnapshot)
class ArchiveSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "metric",
        "project_code",
        "region_id",
        "entity_key",
        "granularity",
        "period_year",
        "period_month",
        "value",
        "captured_at",
    )
    list_filter = ("project_code", "granularity", "period_year", "metric__metric_code")
    search_fields = ("metric__metric_code", "region_id", "entity_key", "source_reference")
