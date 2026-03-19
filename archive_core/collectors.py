from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.db import connection, transaction

from .collectors_generic import capture_generic_archive_snapshots
from .models import ArchiveMetricDefinition, ArchiveSnapshot, PeriodGranularity, ProjectScope


@dataclass(frozen=True)
class SnapshotPayload:
    metric: ArchiveMetricDefinition
    project_code: str
    region_id: str
    entity_key: str
    granularity: str
    period_year: int
    period_month: int
    value: Decimal
    source_dataset: str
    source_reference: str
    metadata: dict[str, Any]


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _fetch_rows(sql: str, params: list[Any]) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _upsert_payloads(payloads: list[SnapshotPayload], captured_by) -> tuple[int, int]:
    created = 0
    updated = 0

    with transaction.atomic():
        for payload in payloads:
            _, was_created = ArchiveSnapshot.objects.update_or_create(
                metric=payload.metric,
                project_code=payload.project_code,
                region_id=payload.region_id,
                entity_key=payload.entity_key,
                granularity=payload.granularity,
                period_year=payload.period_year,
                period_month=payload.period_month,
                defaults={
                    "value": payload.value,
                    "source_dataset": payload.source_dataset,
                    "source_reference": payload.source_reference,
                    "metadata": payload.metadata,
                    "captured_by": captured_by,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

    return created, updated


def _capture_agrieco_surface_cultivee(metric: ArchiveMetricDefinition, project_code: str, region_id: str | None) -> list[SnapshotPayload]:
    params: list[Any] = [project_code]
    region_sql = ""
    if region_id:
        region_sql = " AND ac.id_region = %s"
        params.append(region_id)

    rows = _fetch_rows(
        f"""
        SELECT
            p.campagne_yyyy AS period_year,
            COALESCE(ac.id_region, '') AS region_id,
            COALESCE(p.id_cep, '') AS entity_key,
            COALESCE(p.surface_decl, 0)::numeric AS value
        FROM core.cep_parcelle p
        LEFT JOIN ref.admin_commune ac ON ac.id_commune = p.id_commune
        WHERE p.project_code = %s
          AND p.campagne_yyyy IS NOT NULL
          AND p.surface_decl IS NOT NULL
          {region_sql}
        """,
        params,
    )

    payloads: list[SnapshotPayload] = []
    for row in rows:
        year = int(row["period_year"])
        if year < 2000 or year > 2100:
            continue

        payloads.append(
            SnapshotPayload(
                metric=metric,
                project_code=project_code,
                region_id=str(row.get("region_id") or "").strip().upper(),
                entity_key=str(row.get("entity_key") or "").strip(),
                granularity=PeriodGranularity.YEAR,
                period_year=year,
                period_month=0,
                value=_to_decimal(row.get("value") or 0),
                source_dataset="cep-parcelles",
                source_reference="core.cep_parcelle",
                metadata={"capture_mode": "auto_publish"},
            )
        )

    return payloads


def _capture_agrieco_surface_restauree(metric: ArchiveMetricDefinition, project_code: str, region_id: str | None) -> list[SnapshotPayload]:
    params: list[Any] = [project_code]
    region_sql = ""
    if region_id:
        region_sql = " AND id_region = %s"
        params.append(region_id)

    rows = _fetch_rows(
        f"""
        SELECT
            EXTRACT(YEAR FROM imported_at)::int AS period_year,
            EXTRACT(MONTH FROM imported_at)::int AS period_month,
            COALESCE(id_region, '') AS region_id,
            COALESCE(SUM(COALESCE(surface_restaur_ha, 0)), 0)::numeric AS value
        FROM core.zone_degradee
        WHERE project_code = %s
          AND imported_at IS NOT NULL
          {region_sql}
        GROUP BY 1, 2, 3
        ORDER BY 3, 1, 2
        """,
        params,
    )

    increments: dict[tuple[str, int, int], Decimal] = {}
    for row in rows:
        y = int(row["period_year"])
        m = int(row["period_month"])
        if y < 2000 or y > 2100 or m < 1 or m > 12:
            continue

        region = str(row.get("region_id") or "").strip().upper()
        increments[(region, y, m)] = _to_decimal(row.get("value") or 0)

    cumulative_by_region_year: dict[tuple[str, int], Decimal] = defaultdict(lambda: Decimal("0"))
    payloads: list[SnapshotPayload] = []

    for region, year, month in sorted(increments.keys(), key=lambda k: (k[0], k[1], k[2])):
        running_key = (region, year)
        cumulative_by_region_year[running_key] += increments[(region, year, month)]

        payloads.append(
            SnapshotPayload(
                metric=metric,
                project_code=project_code,
                region_id=region,
                entity_key="",
                granularity=PeriodGranularity.MONTH,
                period_year=year,
                period_month=month,
                value=cumulative_by_region_year[running_key],
                source_dataset="agr-zones-degradees",
                source_reference="core.zone_degradee",
                metadata={"capture_mode": "auto_publish", "source_value_type": "monthly_increment"},
            )
        )

    return payloads


def _capture_fiere_sortants_inseres(metric: ArchiveMetricDefinition, project_code: str, region_id: str | None) -> list[SnapshotPayload]:
    params: list[Any] = [project_code]
    region_sql = ""
    if region_id:
        region_sql = " AND id_region = %s"
        params.append(region_id)

    rows = _fetch_rows(
        f"""
        WITH base AS (
            SELECT
                COALESCE(date_suivi, imported_at::date) AS obs_date,
                COALESCE(id_region, '') AS region_id,
                CASE
                    WHEN LOWER(BTRIM(COALESCE(insere::text, ''))) IN ('1', 'true', 't', 'yes', 'oui', 'vrai') THEN 1
                    ELSE 0
                END AS inserted_flag
            FROM core.fiere_suivi_sortant
            WHERE project_code = %s
              AND COALESCE(date_suivi, imported_at::date) IS NOT NULL
              {region_sql}
        )
        SELECT
            EXTRACT(YEAR FROM obs_date)::int AS period_year,
            EXTRACT(MONTH FROM obs_date)::int AS period_month,
            region_id,
            SUM(inserted_flag)::numeric AS value
        FROM base
        GROUP BY 1, 2, 3
        ORDER BY 3, 1, 2
        """,
        params,
    )

    payloads: list[SnapshotPayload] = []
    for row in rows:
        y = int(row["period_year"])
        m = int(row["period_month"])
        if y < 2000 or y > 2100 or m < 1 or m > 12:
            continue

        payloads.append(
            SnapshotPayload(
                metric=metric,
                project_code=project_code,
                region_id=str(row.get("region_id") or "").strip().upper(),
                entity_key="",
                granularity=PeriodGranularity.MONTH,
                period_year=y,
                period_month=m,
                value=_to_decimal(row.get("value") or 0),
                source_dataset="fiere-suivi-sortants",
                source_reference="core.fiere_suivi_sortant",
                metadata={"capture_mode": "auto_publish"},
            )
        )

    return payloads


def _capture_fiere_taux_insertion(metric: ArchiveMetricDefinition, project_code: str, region_id: str | None) -> list[SnapshotPayload]:
    params: list[Any] = [project_code]
    region_sql = ""
    if region_id:
        region_sql = " AND id_region = %s"
        params.append(region_id)

    rows = _fetch_rows(
        f"""
        WITH base AS (
            SELECT
                COALESCE(date_suivi, imported_at::date) AS obs_date,
                COALESCE(id_region, '') AS region_id,
                CASE
                    WHEN LOWER(BTRIM(COALESCE(insere::text, ''))) IN ('1', 'true', 't', 'yes', 'oui', 'vrai') THEN 1
                    ELSE 0
                END AS inserted_flag
            FROM core.fiere_suivi_sortant
            WHERE project_code = %s
              AND COALESCE(date_suivi, imported_at::date) IS NOT NULL
              {region_sql}
        ), monthly AS (
            SELECT
                EXTRACT(YEAR FROM obs_date)::int AS period_year,
                EXTRACT(MONTH FROM obs_date)::int AS period_month,
                region_id,
                SUM(inserted_flag)::numeric AS inserted,
                COUNT(*)::numeric AS total
            FROM base
            GROUP BY 1, 2, 3
        )
        SELECT
            period_year,
            period_month,
            region_id,
            CASE WHEN total > 0 THEN (inserted * 100.0 / total) ELSE 0 END::numeric AS value
        FROM monthly
        ORDER BY region_id, period_year, period_month
        """,
        params,
    )

    payloads: list[SnapshotPayload] = []
    for row in rows:
        y = int(row["period_year"])
        m = int(row["period_month"])
        if y < 2000 or y > 2100 or m < 1 or m > 12:
            continue

        payloads.append(
            SnapshotPayload(
                metric=metric,
                project_code=project_code,
                region_id=str(row.get("region_id") or "").strip().upper(),
                entity_key="",
                granularity=PeriodGranularity.MONTH,
                period_year=y,
                period_month=m,
                value=_to_decimal(row.get("value") or 0),
                source_dataset="fiere-suivi-sortants",
                source_reference="core.fiere_suivi_sortant",
                metadata={"capture_mode": "auto_publish", "formula": "inserted/total*100"},
            )
        )

    return payloads


def capture_archive_snapshots_for_publication(
    *,
    project_code: str,
    region_id: str | None,
    dataset_code: str | None,
    captured_by,
) -> dict[str, Any]:
    project = str(project_code or "").strip().upper()
    if project not in {ProjectScope.AGRIECO, ProjectScope.FIERE}:
        return {
            "executed": False,
            "status": "skipped",
            "reason": f"Projet non supporte pour capture archive: {project}",
            "captured_metrics": [],
            "created": 0,
            "updated": 0,
        }

    normalized_dataset = str(dataset_code or "").strip().lower()

    metrics = list(
        ArchiveMetricDefinition.objects.filter(is_active=True).filter(
            project_scope=project,
        )
    )
    metrics_by_code = {metric.metric_code: metric for metric in metrics}

    dataset_metric_map: dict[str, set[str]] = {
        "cep-parcelles": {"agrieco_surface_cultivee_ha"},
        "agr-zones-degradees": {"agrieco_surface_restauree_ha"},
        "fiere-suivi-sortants": {"fiere_sortants_inseres", "fiere_taux_insertion_pct"},
    }

    target_metric_codes: set[str]
    if normalized_dataset and normalized_dataset in dataset_metric_map:
        target_metric_codes = dataset_metric_map[normalized_dataset]
    elif normalized_dataset:
        target_metric_codes = set()
    else:
        target_metric_codes = set(metrics_by_code.keys())

    created_total = 0
    updated_total = 0
    captured_metrics: list[dict[str, Any]] = []

    for metric_code in sorted(target_metric_codes):
        metric = metrics_by_code.get(metric_code)
        if metric is None:
            continue

        try:
            if metric_code == "agrieco_surface_cultivee_ha":
                payloads = _capture_agrieco_surface_cultivee(metric, project, region_id)
            elif metric_code == "agrieco_surface_restauree_ha":
                payloads = _capture_agrieco_surface_restauree(metric, project, region_id)
            elif metric_code == "fiere_sortants_inseres":
                payloads = _capture_fiere_sortants_inseres(metric, project, region_id)
            elif metric_code == "fiere_taux_insertion_pct":
                payloads = _capture_fiere_taux_insertion(metric, project, region_id)
            else:
                payloads = []
        except Exception as exc:
            captured_metrics.append(
                {
                    "metric_code": metric_code,
                    "rows_prepared": 0,
                    "created": 0,
                    "updated": 0,
                    "error": str(exc),
                }
            )
            continue

        if not payloads:
            captured_metrics.append(
                {
                    "metric_code": metric_code,
                    "rows_prepared": 0,
                    "created": 0,
                    "updated": 0,
                }
            )
            continue

        created, updated = _upsert_payloads(payloads, captured_by=captured_by)
        created_total += created
        updated_total += updated
        captured_metrics.append(
            {
                "metric_code": metric_code,
                "rows_prepared": len(payloads),
                "created": created,
                "updated": updated,
            }
        )

    generic_capture = capture_generic_archive_snapshots(
        project_code=project,
        region_id=region_id,
        dataset_code=normalized_dataset or None,
        captured_by=captured_by,
    )
    generic_metrics = list(generic_capture.get("captured_metrics") or [])
    captured_metrics.extend(generic_metrics)
    created_total += int(generic_capture.get("created") or 0)
    updated_total += int(generic_capture.get("updated") or 0)

    return {
        "executed": True,
        "status": "success",
        "project_code": project,
        "dataset_code": normalized_dataset or None,
        "captured_metrics": captured_metrics,
        "created": created_total,
        "updated": updated_total,
    }
