from decimal import Decimal

from django.test import SimpleTestCase

from .collectors_generic import TableConfig, _obs_period
from .serializers import (
    ArchiveSnapshotUpsertItemSerializer,
    CompareEntityQuerySerializer,
    CompareGlobalQuerySerializer,
    DirectEntitiesQuerySerializer,
)
from .views import _is_excluded_column, _build_year_filter
from .services import (
    classify_trend,
    compute_delta,
    compute_delta_pct,
    parse_period_token,
    previous_period,
)
from .models import TrendPolarity, ValueBehavior, PeriodGranularity


class PeriodParsingTests(SimpleTestCase):
    def test_parse_year_token(self):
        period = parse_period_token("2026")
        self.assertEqual(period.granularity, PeriodGranularity.YEAR)
        self.assertEqual(period.year, 2026)
        self.assertEqual(period.month, 0)

    def test_parse_month_token(self):
        period = parse_period_token("2026-03")
        self.assertEqual(period.granularity, PeriodGranularity.MONTH)
        self.assertEqual(period.year, 2026)
        self.assertEqual(period.month, 3)

    def test_invalid_period_token_raises(self):
        with self.assertRaises(ValueError):
            parse_period_token("2026/03")


class PreviousPeriodTests(SimpleTestCase):
    def test_previous_period_cumulative_yearly(self):
        period = parse_period_token("2026-05")
        prev = previous_period(period, ValueBehavior.CUMULATIVE_YEARLY)
        self.assertIsNotNone(prev)
        self.assertEqual(prev.year, 2026)
        self.assertEqual(prev.month, 4)

    def test_previous_period_cumulative_continuous_january(self):
        period = parse_period_token("2026-01")
        prev = previous_period(period, ValueBehavior.CUMULATIVE_CONTINUOUS)
        self.assertIsNotNone(prev)
        self.assertEqual(prev.year, 2025)
        self.assertEqual(prev.month, 12)


class DeltaAndTrendTests(SimpleTestCase):
    def test_delta_and_pct(self):
        delta = compute_delta(Decimal("10"), Decimal("14"))
        self.assertEqual(delta, Decimal("4"))

        delta_pct = compute_delta_pct(Decimal("10"), delta)
        self.assertEqual(delta_pct, Decimal("40"))

    def test_trend_higher_is_better(self):
        trend = classify_trend(Decimal("2"), TrendPolarity.HIGHER_IS_BETTER)
        self.assertEqual(trend, "progression")

    def test_trend_lower_is_better(self):
        trend = classify_trend(Decimal("2"), TrendPolarity.LOWER_IS_BETTER)
        self.assertEqual(trend, "regression")


class SnapshotSerializerValidationTests(SimpleTestCase):
    def test_month_granularity_requires_month(self):
        serializer = ArchiveSnapshotUpsertItemSerializer(
            data={
                "metric_code": "agrieco_surface_cultivee_ha",
                "granularity": "month",
                "period_year": 2026,
                "value": "12.3",
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("period_month", serializer.errors)

    def test_year_granularity_forces_month_zero(self):
        serializer = ArchiveSnapshotUpsertItemSerializer(
            data={
                "metric_code": "agrieco_surface_cultivee_ha",
                "granularity": "year",
                "period_year": 2026,
                "period_month": 8,
                "value": "12.3",
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["period_month"], 0)

    def test_granularity_auto_infers_month(self):
        serializer = ArchiveSnapshotUpsertItemSerializer(
            data={
                "metric_code": "agrieco_surface_cultivee_ha",
                "period_year": 2026,
                "period_month": 7,
                "value": "12.3",
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["granularity"], "month")


class GenericCollectorDateResolutionTests(SimpleTestCase):
    def setUp(self):
        self.cfg = TableConfig(
            dataset_codes=("dummy",),
            project_scope="AGRIECO",
            table_name="dummy",
            metric_prefix="dummy",
            label_prefix="Dummy",
            source_dataset="dummy",
            source_reference="core.dummy",
            date_fields=("valid_from",),
            year_fields=("campagne_yyyy",),
            payload_date_fields=("today", "date_obs"),
            payload_year_fields=("campagne",),
        )

    def test_payload_today_has_priority(self):
        row = {
            "raw_payload": {"today": "2025-02-15", "date_obs": "2025-01-01"},
            "valid_from": "2024-12-31",
            "campagne_yyyy": 2023,
        }
        self.assertEqual(_obs_period(row, self.cfg), (2025, 2, True))

    def test_payload_year_fallback(self):
        row = {
            "raw_payload": '{"campagne": "2024"}',
            "valid_from": None,
            "campagne_yyyy": None,
        }
        self.assertEqual(_obs_period(row, self.cfg), (2024, 0, False))

    def test_table_year_fallback(self):
        row = {
            "raw_payload": {},
            "valid_from": None,
            "campagne_yyyy": 2021,
        }
        self.assertEqual(_obs_period(row, self.cfg), (2021, 0, False))

    def test_valid_from_fallback_when_no_payload_or_year(self):
        row = {
            "raw_payload": {},
            "valid_from": "2026-03-05",
            "campagne_yyyy": None,
        }
        self.assertEqual(_obs_period(row, self.cfg), (2026, 3, True))


class DirectEntitiesSerializerTests(SimpleTestCase):
    def test_valid_query_params(self):
        serializer = DirectEntitiesQuerySerializer(data={"year": 2025, "q": "CEP"})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["year"], 2025)
        self.assertEqual(serializer.validated_data["q"], "CEP")

    def test_empty_params_are_valid(self):
        serializer = DirectEntitiesQuerySerializer(data={})
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_invalid_year_rejected(self):
        serializer = DirectEntitiesQuerySerializer(data={"year": 1900})
        self.assertFalse(serializer.is_valid())
        self.assertIn("year", serializer.errors)


class CompareEntitySerializerTests(SimpleTestCase):
    def test_valid_query(self):
        serializer = CompareEntityQuerySerializer(data={
            "entity_id": "A1",
            "year_a": 2025,
            "year_b": 2026,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_missing_entity_id_rejected(self):
        serializer = CompareEntityQuerySerializer(data={
            "year_a": 2025,
            "year_b": 2026,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn("entity_id", serializer.errors)

    def test_missing_year_rejected(self):
        serializer = CompareEntityQuerySerializer(data={
            "entity_id": "A1",
            "year_b": 2026,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn("year_a", serializer.errors)


class CompareGlobalSerializerTests(SimpleTestCase):
    def test_valid_global_query(self):
        serializer = CompareGlobalQuerySerializer(data={
            "year_a": 2025,
            "year_b": 2026,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_with_region(self):
        serializer = CompareGlobalQuerySerializer(data={
            "year_a": 2025,
            "year_b": 2026,
            "region_id": "GN007",
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["region_id"], "GN007")

    def test_missing_year_b_rejected(self):
        serializer = CompareGlobalQuerySerializer(data={"year_a": 2025})
        self.assertFalse(serializer.is_valid())
        self.assertIn("year_b", serializer.errors)


class ExcludedColumnsTests(SimpleTestCase):
    def test_geom_excluded(self):
        self.assertTrue(_is_excluded_column("geom"))
        self.assertTrue(_is_excluded_column("geom_point"))

    def test_raw_payload_excluded(self):
        self.assertTrue(_is_excluded_column("raw_payload"))

    def test_uuid_suffix_excluded(self):
        self.assertTrue(_is_excluded_column("formation_uuid"))

    def test_regular_column_not_excluded(self):
        self.assertFalse(_is_excluded_column("surface_ha"))
        self.assertFalse(_is_excluded_column("nom_chef_menage"))

    def test_project_code_excluded(self):
        self.assertTrue(_is_excluded_column("project_code"))


class BuildYearFilterTests(SimpleTestCase):
    def test_with_year_fields(self):
        cfg = TableConfig(
            dataset_codes=("test",),
            project_scope="AGRIECO",
            table_name="test",
            metric_prefix="test",
            label_prefix="Test",
            source_dataset="test",
            source_reference="core.test",
            year_fields=("campagne_yyyy",),
        )
        year_expr, year_alias = _build_year_filter(cfg)
        self.assertEqual(year_alias, "obs_year")
        self.assertIn("campagne_yyyy", year_expr)

    def test_without_year_fields(self):
        cfg = TableConfig(
            dataset_codes=("test",),
            project_scope="AGRIECO",
            table_name="test",
            metric_prefix="test",
            label_prefix="Test",
            source_dataset="test",
            source_reference="core.test",
        )
        year_expr, year_alias = _build_year_filter(cfg)
        self.assertEqual(year_alias, "obs_year")
        self.assertIn("EXTRACT", year_expr)
        self.assertNotIn("campagne", year_expr)

    def test_with_alias(self):
        cfg = TableConfig(
            dataset_codes=("test",),
            project_scope="AGRIECO",
            table_name="test",
            metric_prefix="test",
            label_prefix="Test",
            source_dataset="test",
            source_reference="core.test",
            year_fields=("campagne_yyyy",),
        )
        year_expr, _ = _build_year_filter(cfg, alias="src")
        self.assertIn("src.", year_expr)

