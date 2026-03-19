from rest_framework import serializers

from .models import (
    ArchiveMetricDefinition,
    ArchiveSnapshot,
    PeriodGranularity,
)


class ArchiveMetricDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ArchiveMetricDefinition
        fields = [
            "metric_code",
            "label",
            "description",
            "project_scope",
            "source_dataset",
            "unit",
            "value_behavior",
            "trend_polarity",
            "default_granularity",
            "is_active",
        ]


class ArchiveSnapshotUpsertItemSerializer(serializers.Serializer):
    metric_code = serializers.SlugField(max_length=80)
    period_year = serializers.IntegerField(min_value=2000, max_value=2100)
    period_month = serializers.IntegerField(required=False, min_value=0, max_value=12)
    granularity = serializers.ChoiceField(
        choices=PeriodGranularity.choices,
        required=False,
    )

    value = serializers.DecimalField(max_digits=20, decimal_places=4)

    region_id = serializers.CharField(required=False, allow_blank=True, max_length=50)
    entity_key = serializers.CharField(required=False, allow_blank=True, max_length=120)

    source_dataset = serializers.CharField(required=False, allow_blank=True, max_length=80)
    source_reference = serializers.CharField(required=False, allow_blank=True, max_length=180)
    metadata = serializers.DictField(required=False)

    def validate(self, attrs):
        period_month = attrs.get("period_month")
        granularity = attrs.get("granularity")

        if granularity is None:
            granularity = PeriodGranularity.MONTH if period_month not in (None, 0) else PeriodGranularity.YEAR
            attrs["granularity"] = granularity

        if granularity == PeriodGranularity.MONTH:
            if period_month is None:
                raise serializers.ValidationError({"period_month": "Le mois est obligatoire pour une granularite mensuelle."})
            if period_month < 1 or period_month > 12:
                raise serializers.ValidationError({"period_month": "Le mois doit etre compris entre 1 et 12."})
        else:
            attrs["period_month"] = 0

        attrs["metric_code"] = str(attrs["metric_code"]).strip().lower()
        attrs["region_id"] = str(attrs.get("region_id") or "").strip().upper()
        attrs["entity_key"] = str(attrs.get("entity_key") or "").strip()
        attrs["source_dataset"] = str(attrs.get("source_dataset") or "").strip()
        attrs["source_reference"] = str(attrs.get("source_reference") or "").strip()
        attrs["metadata"] = attrs.get("metadata") or {}

        return attrs


class ArchiveSnapshotBulkUpsertSerializer(serializers.Serializer):
    snapshots = ArchiveSnapshotUpsertItemSerializer(many=True, min_length=1)


class ArchiveComparativeQuerySerializer(serializers.Serializer):
    period_a = serializers.CharField(max_length=7)
    period_b = serializers.CharField(max_length=7)

    dataset_code = serializers.CharField(required=False, allow_blank=True, max_length=80)
    metric_codes = serializers.CharField(required=False, allow_blank=True)
    region_id = serializers.CharField(required=False, allow_blank=True, max_length=50)
    entity_key = serializers.CharField(required=False, allow_blank=True, max_length=120)
    collection_scope = serializers.ChoiceField(
        choices=("entity", "cumulated", "both"),
        required=False,
        default="both",
    )

    include_missing = serializers.BooleanField(required=False, default=False)
    normalize_cumulative = serializers.BooleanField(required=False, default=True)


class ArchiveCaptureRequestSerializer(serializers.Serializer):
    dataset_code = serializers.CharField(required=False, allow_blank=True, max_length=80)


class ArchiveSeriesQuerySerializer(serializers.Serializer):
    metric_code = serializers.SlugField(max_length=80)
    year = serializers.IntegerField(min_value=2000, max_value=2100)
    compare_year = serializers.IntegerField(required=False, min_value=2000, max_value=2100)

    region_id = serializers.CharField(required=False, allow_blank=True, max_length=50)
    entity_key = serializers.CharField(required=False, allow_blank=True, max_length=120)
    collection_scope = serializers.ChoiceField(
        choices=("entity", "cumulated", "both"),
        required=False,
        default="both",
    )
    normalize_cumulative = serializers.BooleanField(required=False, default=True)


class DirectEntitiesQuerySerializer(serializers.Serializer):
    year = serializers.IntegerField(required=False, min_value=2000, max_value=2100)
    region_id = serializers.CharField(required=False, allow_blank=True, max_length=50)
    q = serializers.CharField(required=False, allow_blank=True, max_length=200)


class CompareEntityQuerySerializer(serializers.Serializer):
    entity_id = serializers.CharField(max_length=200)
    year_a = serializers.IntegerField(min_value=2000, max_value=2100)
    year_b = serializers.IntegerField(min_value=2000, max_value=2100)


class CompareGlobalQuerySerializer(serializers.Serializer):
    year_a = serializers.IntegerField(min_value=2000, max_value=2100)
    year_b = serializers.IntegerField(min_value=2000, max_value=2100)
    region_id = serializers.CharField(required=False, allow_blank=True, max_length=50)


class ArchiveSnapshotSerializer(serializers.ModelSerializer):
    metric_code = serializers.CharField(source="metric.metric_code", read_only=True)

    class Meta:
        model = ArchiveSnapshot
        fields = [
            "id",
            "metric_code",
            "project_code",
            "region_id",
            "entity_key",
            "granularity",
            "period_year",
            "period_month",
            "value",
            "source_dataset",
            "source_reference",
            "metadata",
            "captured_by_id",
            "captured_at",
            "updated_at",
        ]
