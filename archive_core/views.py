from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from django.db import connection, transaction
from django.db.models import Count, Q
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from data_api.mixins import CurrentProjectRequiredMixin
from import_core.etl import list_datasets_for_project, resolve_dataset_definition

from .collectors import capture_archive_snapshots_for_publication
from .collectors_generic import (
    TABLE_CONFIGS,
    COLUMN_LABELS,
    HIDDEN_COLUMNS,
    get_column_label,
    _table_cols,
    _obs_period,
    _payload_dict,
    _parse_year,
)
from .models import (
    ArchiveMetricDefinition,
    ArchiveSnapshot,
    PeriodGranularity,
    ProjectScope,
    ValueBehavior,
)
from .serializers import (
    ArchiveCaptureRequestSerializer,
    ArchiveComparativeQuerySerializer,
    ArchiveMetricDefinitionSerializer,
    ArchiveSeriesQuerySerializer,
    ArchiveSnapshotBulkUpsertSerializer,
    CompareEntityQuerySerializer,
    CompareGlobalQuerySerializer,
    DirectEntitiesQuerySerializer,
)
from .services import (
    classify_trend,
    compute_delta,
    compute_delta_pct,
    parse_period_token,
    previous_period,
    to_decimal,
    to_float_or_none,
)


def _is_platform_admin(user) -> bool:
    role = str(getattr(user, "role", "") or "").strip().lower()
    return bool(
        getattr(user, "is_superuser", False)
        or getattr(user, "is_staff", False)
        or role == "admin"
    )


def _is_project_wide_user(user) -> bool:
    role = str(getattr(user, "role", "") or "").strip().lower()
    return _is_platform_admin(user) or role == "project_manager"


def _can_write_archive(user) -> bool:
    role = str(getattr(user, "role", "") or "").strip().lower()
    return _is_platform_admin(user) or role in {"project_manager", "manager"}


class ArchiveBaseView(CurrentProjectRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def resolve_project_code(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return None, error_response

        code = str(getattr(project, "code_fonc", "") or getattr(project, "code_kobo", "") or "").strip().upper()
        if not code:
            return None, Response(
                {"detail": "Impossible de determiner le code du projet courant."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return code, None

    def metric_allowed_for_project(self, metric: ArchiveMetricDefinition, project_code: str) -> bool:
        if metric.project_scope == ProjectScope.BOTH:
            return True
        return metric.project_scope == project_code

    def resolve_region_scope(self, request, requested_region: str | None) -> str:
        region = str(requested_region or "").strip().upper()
        if _is_project_wide_user(request.user):
            return region

        user_region = str(getattr(request.user, "region_id", "") or "").strip().upper()
        if not user_region:
            raise PermissionDenied("Aucune region assignee a votre compte.")

        if region and region != user_region:
            raise PermissionDenied("Acces refuse: cette region n'est pas dans votre perimetre.")

        return user_region

    @staticmethod
    def period_to_token(period) -> str:
        if period.granularity == PeriodGranularity.MONTH:
            return f"{period.year:04d}-{period.month:02d}"
        return f"{period.year:04d}"


def _quote_ident(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _json_ready(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _dataset_configs(project_code: str, dataset_code: str):
    normalized = str(dataset_code or "").strip().lower()
    return [
        cfg
        for cfg in TABLE_CONFIGS
        if cfg.project_scope == project_code and normalized in {item.lower() for item in cfg.dataset_codes}
    ]


def _core_table_columns(table_name: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='core' AND table_name=%s
            ORDER BY ordinal_position
            """,
            [table_name],
        )
        return [str(row[0]) for row in cursor.fetchall()]


import re as _re

_SQL_COL_RE = _re.compile(
    r"""
    (?:                          # optional table alias prefix
        \w+\.                    # e.g. e. or d.
    )?
    (?:                          # column or expression
        \w+                      # column name
        | \([^)]+\)              # parenthesized expression
    )
    \s+ AS \s+ (\w+)            # capture the alias
    """,
    _re.VERBOSE | _re.IGNORECASE,
)


def _custom_sql_columns(custom_sql: str) -> list[str]:
    """Extract column aliases exposed by a custom SQL SELECT."""
    # Get everything between SELECT and FROM
    match = _re.search(r"SELECT\s+(.*?)\s+FROM\s+", custom_sql, _re.IGNORECASE | _re.DOTALL)
    if not match:
        return []
    select_clause = match.group(1)
    aliases = _SQL_COL_RE.findall(select_clause)
    # Also add bare column names (no alias) like d.nb_empl_dom
    bare_cols: list[str] = []
    for part in select_clause.split(","):
        part = part.strip()
        if " AS " not in part.upper():
            # e.g. "d.nb_empl_dom" -> "nb_empl_dom"
            col = part.rsplit(".", 1)[-1].strip()
            if col and col.isidentifier():
                bare_cols.append(col)
    return list(dict.fromkeys(aliases + bare_cols))


def _pick_preview_columns(cfg, table_columns: list[str], max_columns: int = 20) -> list[str]:
    """Select user-relevant columns, excluding technical/internal fields."""
    preferred: list[str] = []

    # Entity identifiers first
    for name in cfg.entity_fields:
        if name in table_columns and name not in preferred:
            preferred.append(name)

    # Region
    if cfg.region_field in table_columns and cfg.region_field not in preferred:
        preferred.append(cfg.region_field)

    # Year/date fields that matter to the user
    for name in cfg.year_fields:
        if name in table_columns and name not in preferred:
            preferred.append(name)

    for name in cfg.date_fields:
        if name in table_columns and name not in preferred and name != "valid_from":
            preferred.append(name)

    # All remaining user-relevant columns
    for name in table_columns:
        low = name.lower()
        if name in preferred:
            continue
        if low in HIDDEN_COLUMNS:
            continue
        if low.startswith("geom"):
            continue
        if low.endswith("_uuid") or low == "uuid":
            continue
        preferred.append(name)
        if len(preferred) >= max_columns:
            break

    return preferred[:max_columns]


class ArchiveMetricDefinitionListView(ArchiveBaseView):
    throttle_scope = "stats"

    def get(self, request):
        project_code, error_response = self.resolve_project_code(request)
        if error_response is not None:
            return error_response

        metrics = ArchiveMetricDefinition.objects.filter(
            is_active=True,
        ).filter(Q(project_scope=ProjectScope.BOTH) | Q(project_scope=project_code))

        serializer = ArchiveMetricDefinitionSerializer(metrics, many=True)
        return Response(
            {
                "project_code": project_code,
                "count": len(serializer.data),
                "results": serializer.data,
            }
        )



class ArchiveFormListView(ArchiveBaseView):
    throttle_scope = "stats"

    def get(self, request):
        project_code, error_response = self.resolve_project_code(request)
        if error_response is not None:
            return error_response

        datasets = list_datasets_for_project(project_code)
        metrics = list(
            ArchiveMetricDefinition.objects.filter(is_active=True)
            .filter(Q(project_scope=ProjectScope.BOTH) | Q(project_scope=project_code))
            .order_by("metric_code")
        )

        metrics_by_dataset: dict[str, list[ArchiveMetricDefinition]] = defaultdict(list)
        for metric in metrics:
            dataset_key = str(metric.source_dataset or "").strip().lower()
            if dataset_key:
                metrics_by_dataset[dataset_key].append(metric)

        results = []
        for dataset in datasets:
            dataset_code = str(dataset.code)
            dataset_metrics = metrics_by_dataset.get(dataset_code.lower(), [])
            metric_ids = [m.id for m in dataset_metrics]

            years: list[int] = []
            entity_count = 0
            snapshot_count = 0
            if metric_ids:
                snapshot_qs = ArchiveSnapshot.objects.filter(
                    project_code=project_code,
                    metric_id__in=metric_ids,
                )
                years = sorted(snapshot_qs.values_list("period_year", flat=True).distinct())
                entity_count = snapshot_qs.exclude(entity_key="").values("entity_key").distinct().count()
                snapshot_count = snapshot_qs.count()

            configs = _dataset_configs(project_code, dataset_code)
            core_sources = sorted({cfg.source_reference for cfg in configs})

            results.append(
                {
                    "dataset_code": dataset_code,
                    "label": str(dataset.label),
                    "stage_table": f"stage.{dataset.stage_table}",
                    "metrics_count": len(dataset_metrics),
                    "snapshot_count": snapshot_count,
                    "entities_count": entity_count,
                    "years_available": years,
                    "has_data": snapshot_count > 0,
                    "core_sources": core_sources,
                }
            )

        return Response(
            {
                "project_code": project_code,
                "count": len(results),
                "results": results,
            }
        )


class ArchiveFormEntitiesView(ArchiveBaseView):
    throttle_scope = "stats"

    def get(self, request, dataset_code: str):
        project_code, error_response = self.resolve_project_code(request)
        if error_response is not None:
            return error_response

        dataset = resolve_dataset_definition(dataset_code, project_code=project_code)
        if dataset is None:
            return Response(
                {"detail": "dataset_code invalide pour le projet courant."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        year_raw = str(request.query_params.get("year") or "").strip()
        year_value: int | None = None
        if year_raw:
            try:
                year_value = int(year_raw)
            except ValueError:
                return Response(
                    {"detail": "Le parametre year doit etre un entier YYYY."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if year_value < 2000 or year_value > 2100:
                return Response(
                    {"detail": "Le parametre year doit etre compris entre 2000 et 2100."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        region_raw = str(request.query_params.get("region_id") or "").strip()
        search_term = str(request.query_params.get("q") or "").strip()

        try:
            region_scope = self.resolve_region_scope(request, region_raw)
        except PermissionDenied as exc:
            return Response({"detail": str(exc.detail)}, status=status.HTTP_403_FORBIDDEN)

        metric_ids = list(
            ArchiveMetricDefinition.objects.filter(
                is_active=True,
                source_dataset=dataset.code,
            )
            .filter(Q(project_scope=ProjectScope.BOTH) | Q(project_scope=project_code))
            .values_list("id", flat=True)
        )

        if not metric_ids:
            return Response(
                {
                    "project_code": project_code,
                    "dataset_code": dataset.code,
                    "label": dataset.label,
                    "count": 0,
                    "results": [],
                }
            )

        snapshots = ArchiveSnapshot.objects.filter(
            project_code=project_code,
            metric_id__in=metric_ids,
        ).exclude(entity_key="")

        if year_value is not None:
            snapshots = snapshots.filter(period_year=year_value)

        if region_scope:
            snapshots = snapshots.filter(region_id=region_scope)

        if search_term:
            snapshots = snapshots.filter(entity_key__icontains=search_term)

        year_pairs = snapshots.values_list("entity_key", "period_year").distinct().order_by("entity_key", "period_year")
        years_by_entity: dict[str, list[int]] = defaultdict(list)
        for entity_key, period_year in year_pairs:
            years_by_entity[str(entity_key)].append(int(period_year))

        grouped = snapshots.values("entity_key").annotate(rows_count=Count("id")).order_by("entity_key")

        results = []
        for row in grouped:
            entity_key = str(row["entity_key"])
            years = years_by_entity.get(entity_key, [])
            results.append(
                {
                    "entity_key": entity_key,
                    "rows_count": int(row["rows_count"]),
                    "years": years,
                    "latest_year": max(years) if years else None,
                }
            )

        return Response(
            {
                "project_code": project_code,
                "dataset_code": dataset.code,
                "label": dataset.label,
                "count": len(results),
                "results": results,
            }
        )


class ArchiveFormPreviewView(ArchiveBaseView):
    throttle_scope = "stats"

    def get(self, request, dataset_code: str):
        project_code, error_response = self.resolve_project_code(request)
        if error_response is not None:
            return error_response

        dataset = resolve_dataset_definition(dataset_code, project_code=project_code)
        if dataset is None:
            return Response(
                {"detail": "dataset_code invalide pour le projet courant."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        limit_raw = str(request.query_params.get("limit") or "15").strip()
        try:
            limit = int(limit_raw)
        except ValueError:
            return Response(
                {"detail": "Le parametre limit doit etre un entier."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        limit = max(1, min(limit, 50))

        source_ref = str(request.query_params.get("source_reference") or "").strip().lower()
        region_raw = str(request.query_params.get("region_id") or "").strip()

        try:
            region_scope = self.resolve_region_scope(request, region_raw)
        except PermissionDenied as exc:
            return Response({"detail": str(exc.detail)}, status=status.HTTP_403_FORBIDDEN)

        configs = _dataset_configs(project_code, dataset.code)
        if source_ref:
            configs = [
                cfg
                for cfg in configs
                if cfg.source_reference.lower() == source_ref or f"core.{cfg.table_name}".lower() == source_ref
            ]

        sources = []
        for cfg in configs:
            source_payload = {
                "source_reference": cfg.source_reference,
                "table_name": f"core.{cfg.table_name}",
                "columns": [],
                "rows": [],
                "returned_rows": 0,
            }

            try:
                if cfg.custom_sql:
                    where_sql = ""
                    params: list = [project_code]
                    if region_scope and cfg.custom_region_filter_sql:
                        where_sql = f" AND {cfg.custom_region_filter_sql}"
                        params.append(region_scope)

                    base_sql = str(cfg.custom_sql).format(region_filter=where_sql).strip().rstrip(";")
                    sql = f"SELECT * FROM ({base_sql}) AS src"
                    if region_scope and not cfg.custom_region_filter_sql:
                        sql += " WHERE COALESCE(src.region_id, '') = %s"
                        params.append(region_scope)
                    sql += " LIMIT %s"
                    params.append(limit)

                    with connection.cursor() as cursor:
                        cursor.execute(sql, params)
                        columns = [str(col[0]) for col in cursor.description]
                        rows = cursor.fetchall()

                    # Filter hidden columns from custom SQL results
                    visible_indices = [
                        i for i, c in enumerate(columns)
                        if c.lower() not in HIDDEN_COLUMNS
                        and not c.lower().endswith("_uuid")
                        and not c.lower().startswith("geom")
                    ]
                    visible_columns = [columns[i] for i in visible_indices]
                    source_payload["columns"] = visible_columns
                    source_payload["column_labels"] = {col: get_column_label(col) for col in visible_columns}
                    source_payload["rows"] = [
                        {visible_columns[j]: _json_ready(row[visible_indices[j]]) for j in range(len(visible_columns))}
                        for row in rows
                    ]
                    source_payload["returned_rows"] = len(rows)
                    sources.append(source_payload)
                    continue

                table_columns = _core_table_columns(cfg.table_name)
                if not table_columns:
                    source_payload["error"] = "Aucune colonne detectee pour cette table."
                    sources.append(source_payload)
                    continue

                selected_columns = _pick_preview_columns(cfg, table_columns)
                if not selected_columns:
                    source_payload["error"] = "Aucune colonne exploitable pour l'aper?u."
                    sources.append(source_payload)
                    continue

                # Add virtual date_collecte column if raw_payload->>'today' available
                has_collecte_virtual = "raw_payload" in table_columns
                select_parts = [_quote_ident(col) for col in selected_columns]
                if has_collecte_virtual:
                    select_parts.append("(raw_payload->>'today')::date AS date_collecte")

                select_sql = ", ".join(select_parts)
                sql = f"SELECT {select_sql} FROM core.{_quote_ident(cfg.table_name)}"
                params: list = []
                where_clauses: list[str] = []

                if "project_code" in table_columns:
                    where_clauses.append("project_code = %s")
                    params.append(project_code)

                if region_scope and cfg.region_field in table_columns:
                    where_clauses.append(f"COALESCE({_quote_ident(cfg.region_field)}, '') = %s")
                    params.append(region_scope)

                if where_clauses:
                    sql += " WHERE " + " AND ".join(where_clauses)

                # ORDER BY : dates de collecte terrain en priorite
                _system_cols = {"valid_from", "valid_to", "imported_at", "updated_at"}
                order_fields: list[str] = []
                # 1) Dates metier du config
                for df in (cfg.date_fields or ()):
                    if df.lower() not in _system_cols and df in table_columns:
                        order_fields.append(df)
                # 2) created_at en dernier recours
                if "created_at" in table_columns:
                    order_fields.append("created_at")
                if order_fields:
                    order_sql = ", ".join(f"{_quote_ident(name)} DESC NULLS LAST" for name in order_fields)
                    sql += f" ORDER BY {order_sql}"

                sql += " LIMIT %s"
                params.append(limit)

                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
                    rows = cursor.fetchall()

                # Build final column list including virtual date_collecte
                final_columns = list(selected_columns)
                if has_collecte_virtual:
                    final_columns.append("date_collecte")

                source_payload["columns"] = final_columns
                source_payload["column_labels"] = {col: get_column_label(col) for col in final_columns}
                source_payload["rows"] = [
                    {final_columns[idx]: _json_ready(value) for idx, value in enumerate(row)}
                    for row in rows
                ]
                source_payload["returned_rows"] = len(rows)
                sources.append(source_payload)
            except Exception as exc:
                source_payload["error"] = str(exc)
                sources.append(source_payload)

        return Response(
            {
                "project_code": project_code,
                "dataset_code": dataset.code,
                "label": dataset.label,
                "source_count": len(sources),
                "sources": sources,
            }
        )


class ArchiveSnapshotBulkUpsertView(ArchiveBaseView):
    throttle_scope = "import_write"

    def post(self, request):
        if not _can_write_archive(request.user):
            return Response(
                {"detail": "Acces refuse: droits insuffisants pour ecrire dans l'archive."},
                status=status.HTTP_403_FORBIDDEN,
            )

        project_code, error_response = self.resolve_project_code(request)
        if error_response is not None:
            return error_response

        serializer = ArchiveSnapshotBulkUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        items = serializer.validated_data["snapshots"]

        metric_codes = sorted({item["metric_code"] for item in items})
        metrics = {
            metric.metric_code: metric
            for metric in ArchiveMetricDefinition.objects.filter(metric_code__in=metric_codes, is_active=True)
        }

        missing_metric_codes = [code for code in metric_codes if code not in metrics]
        if missing_metric_codes:
            return Response(
                {
                    "detail": "Certaines metriques ne sont pas definies ou inactives.",
                    "missing_metric_codes": missing_metric_codes,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        errors = []
        prepared = []
        for index, item in enumerate(items):
            metric = metrics[item["metric_code"]]
            if not self.metric_allowed_for_project(metric, project_code):
                errors.append(
                    {
                        "index": index,
                        "metric_code": metric.metric_code,
                        "detail": "La metrique n'est pas autorisee pour le projet courant.",
                    }
                )
                continue

            try:
                region_id = self.resolve_region_scope(request, item.get("region_id"))
            except PermissionDenied as exc:
                errors.append(
                    {
                        "index": index,
                        "metric_code": metric.metric_code,
                        "detail": str(exc.detail),
                    }
                )
                continue

            prepared.append(
                {
                    "metric": metric,
                    "project_code": project_code,
                    "region_id": region_id,
                    "entity_key": item.get("entity_key", ""),
                    "granularity": item["granularity"],
                    "period_year": item["period_year"],
                    "period_month": item.get("period_month", 0),
                    "value": item["value"],
                    "source_dataset": item.get("source_dataset") or metric.source_dataset,
                    "source_reference": item.get("source_reference", ""),
                    "metadata": item.get("metadata", {}),
                }
            )

        if errors:
            return Response(
                {
                    "detail": "Validation des snapshots echouee.",
                    "errors": errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        created_count = 0
        updated_count = 0

        with transaction.atomic():
            for payload in prepared:
                _, created = ArchiveSnapshot.objects.update_or_create(
                    metric=payload["metric"],
                    project_code=payload["project_code"],
                    region_id=payload["region_id"],
                    entity_key=payload["entity_key"],
                    granularity=payload["granularity"],
                    period_year=payload["period_year"],
                    period_month=payload["period_month"],
                    defaults={
                        "value": payload["value"],
                        "source_dataset": payload["source_dataset"],
                        "source_reference": payload["source_reference"],
                        "metadata": payload["metadata"],
                        "captured_by": request.user,
                    },
                )
                if created:
                    created_count += 1
                else:
                    updated_count += 1

        return Response(
            {
                "detail": "Snapshots archives enregistres.",
                "project_code": project_code,
                "total": len(prepared),
                "created": created_count,
                "updated": updated_count,
            },
            status=status.HTTP_200_OK,
        )


class ArchiveCaptureView(ArchiveBaseView):
    throttle_scope = "import_write"

    def post(self, request):
        if not _can_write_archive(request.user):
            return Response(
                {"detail": "Acces refuse: droits insuffisants pour declencher une capture archive."},
                status=status.HTTP_403_FORBIDDEN,
            )

        project_code, error_response = self.resolve_project_code(request)
        if error_response is not None:
            return error_response

        serializer = ArchiveCaptureRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)

        dataset_code = str(serializer.validated_data.get("dataset_code") or "").strip().lower() or None

        try:
            capture = capture_archive_snapshots_for_publication(
                project_code=project_code,
                region_id=self.resolve_region_scope(request, None),
                dataset_code=dataset_code,
                captured_by=request.user,
            )
        except PermissionDenied as exc:
            return Response({"detail": str(exc.detail)}, status=status.HTTP_403_FORBIDDEN)
        except Exception as exc:
            return Response(
                {
                    "detail": "Echec capture archive.",
                    "error": str(exc),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "detail": "Capture archive terminee.",
                **capture,
            },
            status=status.HTTP_200_OK,
        )


class ArchiveComparativeView(ArchiveBaseView):
    throttle_scope = "stats"

    def _effective_value(
        self,
        metric: ArchiveMetricDefinition,
        snapshot: ArchiveSnapshot | None,
        period,
        lookup: dict,
        region_id: str,
        entity_key: str,
        normalize_cumulative: bool,
    ):
        if snapshot is None:
            return None

        raw_value = to_decimal(snapshot.value)
        if (
            not normalize_cumulative
            or period.granularity != PeriodGranularity.MONTH
            or metric.value_behavior == ValueBehavior.POINT
        ):
            return raw_value

        prev = previous_period(period, metric.value_behavior)
        if prev is None or prev.year < 2000:
            return raw_value

        prev_snapshot = lookup.get((metric.id, region_id, entity_key, prev.year, prev.month))
        if prev_snapshot is None:
            return raw_value

        return raw_value - to_decimal(prev_snapshot.value)

    def get(self, request):
        project_code, error_response = self.resolve_project_code(request)
        if error_response is not None:
            return error_response

        serializer = ArchiveComparativeQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        query = serializer.validated_data

        try:
            period_a = parse_period_token(query["period_a"])
            period_b = parse_period_token(query["period_b"])
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if period_a.granularity != period_b.granularity:
            return Response(
                {"detail": "Les deux periodes doivent avoir la meme granularite (mensuelle ou annuelle)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        region_id_raw = query.get("region_id") or ""
        entity_key = str(query.get("entity_key") or "").strip().upper()
        include_missing = bool(query.get("include_missing", False))
        normalize_cumulative = bool(query.get("normalize_cumulative", True))
        collection_scope = str(query.get("collection_scope") or "both").strip().lower()

        dataset_code_raw = str(query.get("dataset_code") or "").strip().lower()
        dataset_code = ""
        if dataset_code_raw:
            dataset = resolve_dataset_definition(dataset_code_raw, project_code=project_code)
            if dataset is None:
                return Response(
                    {"detail": "dataset_code invalide pour le projet courant."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            dataset_code = str(dataset.code).strip().lower()

        try:
            region_scope = self.resolve_region_scope(request, region_id_raw)
        except PermissionDenied as exc:
            return Response({"detail": str(exc.detail)}, status=status.HTTP_403_FORBIDDEN)

        metric_codes_raw = str(query.get("metric_codes") or "").strip()
        metric_codes = [part.strip().lower() for part in metric_codes_raw.split(",") if part.strip()]

        metric_queryset = ArchiveMetricDefinition.objects.filter(is_active=True).filter(
            Q(project_scope=ProjectScope.BOTH) | Q(project_scope=project_code)
        )
        if dataset_code:
            metric_queryset = metric_queryset.filter(source_dataset=dataset_code)
        if metric_codes:
            metric_queryset = metric_queryset.filter(metric_code__in=metric_codes)

        metrics = list(metric_queryset)
        metrics_by_id = {metric.id: metric for metric in metrics}

        if metric_codes:
            missing = sorted(set(metric_codes) - {metric.metric_code for metric in metrics})
            if missing:
                return Response(
                    {
                        "detail": "Certaines metriques sont absentes ou non autorisees pour le projet courant.",
                        "missing_metric_codes": missing,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if not metrics:
            return Response(
                {
                    "project_code": project_code,
                    "dataset_code": dataset_code or None,
                    "period_a": self.period_to_token(period_a),
                    "period_b": self.period_to_token(period_b),
                    "count": 0,
                    "results": [],
                }
            )

        periods_to_fetch = {period_a.as_key(), period_b.as_key()}
        if normalize_cumulative and period_a.granularity == PeriodGranularity.MONTH:
            for metric in metrics:
                prev_a = previous_period(period_a, metric.value_behavior)
                prev_b = previous_period(period_b, metric.value_behavior)
                if prev_a is not None and prev_a.year >= 2000:
                    periods_to_fetch.add(prev_a.as_key())
                if prev_b is not None and prev_b.year >= 2000:
                    periods_to_fetch.add(prev_b.as_key())

        snapshots = ArchiveSnapshot.objects.filter(
            project_code=project_code,
            metric_id__in=list(metrics_by_id.keys()),
            granularity=period_a.granularity,
        )

        if region_scope:
            snapshots = snapshots.filter(region_id=region_scope)

        if entity_key:
            snapshots = snapshots.filter(entity_key=entity_key)
        else:
            if collection_scope == "entity":
                snapshots = snapshots.exclude(entity_key="")
            elif collection_scope == "cumulated":
                snapshots = snapshots.filter(entity_key="")

        period_query = Q()
        for _, year, month in periods_to_fetch:
            period_query |= Q(period_year=year, period_month=month)

        snapshots = list(snapshots.filter(period_query))

        snapshot_lookup = {
            (s.metric_id, s.region_id, s.entity_key, s.period_year, s.period_month): s for s in snapshots
        }

        row_keys = set()
        for snapshot in snapshots:
            if (snapshot.period_year, snapshot.period_month) in {
                (period_a.year, period_a.month),
                (period_b.year, period_b.month),
            }:
                row_keys.add((snapshot.metric_id, snapshot.region_id, snapshot.entity_key))

        if include_missing:
            default_region = region_scope
            default_entity = entity_key
            for metric in metrics:
                row_keys.add((metric.id, default_region, default_entity))

        results = []
        for metric_id, row_region_id, row_entity_key in sorted(
            row_keys,
            key=lambda key: (
                metrics_by_id[key[0]].metric_code,
                key[1],
                key[2],
            ),
        ):
            metric = metrics_by_id[metric_id]

            snapshot_a = snapshot_lookup.get((metric_id, row_region_id, row_entity_key, period_a.year, period_a.month))
            snapshot_b = snapshot_lookup.get((metric_id, row_region_id, row_entity_key, period_b.year, period_b.month))

            if not include_missing and (snapshot_a is None or snapshot_b is None):
                continue

            raw_a = to_decimal(snapshot_a.value) if snapshot_a is not None else None
            raw_b = to_decimal(snapshot_b.value) if snapshot_b is not None else None

            effective_a = self._effective_value(
                metric,
                snapshot_a,
                period_a,
                snapshot_lookup,
                row_region_id,
                row_entity_key,
                normalize_cumulative,
            )
            effective_b = self._effective_value(
                metric,
                snapshot_b,
                period_b,
                snapshot_lookup,
                row_region_id,
                row_entity_key,
                normalize_cumulative,
            )

            delta = compute_delta(effective_a, effective_b)
            delta_pct = compute_delta_pct(effective_a, delta)
            trend = classify_trend(delta, metric.trend_polarity)

            results.append(
                {
                    "metric_code": metric.metric_code,
                    "metric_label": metric.label,
                    "unit": metric.unit,
                    "value_behavior": metric.value_behavior,
                    "trend_polarity": metric.trend_polarity,
                    "region_id": row_region_id or None,
                    "entity_key": row_entity_key or None,
                    "period_a": {
                        "token": self.period_to_token(period_a),
                        "raw_value": to_float_or_none(raw_a),
                        "effective_value": to_float_or_none(effective_a),
                    },
                    "period_b": {
                        "token": self.period_to_token(period_b),
                        "raw_value": to_float_or_none(raw_b),
                        "effective_value": to_float_or_none(effective_b),
                    },
                    "delta": to_float_or_none(delta),
                    "delta_pct": to_float_or_none(delta_pct),
                    "trend": trend,
                }
            )

        return Response(
            {
                "project_code": project_code,
                "dataset_code": dataset_code or None,
                "period_a": self.period_to_token(period_a),
                "period_b": self.period_to_token(period_b),
                "normalize_cumulative": normalize_cumulative,
                "collection_scope": collection_scope,
                "count": len(results),
                "results": results,
            }
        )


class ArchiveSeriesView(ArchiveBaseView):
    throttle_scope = "stats"

    def get(self, request):
        project_code, error_response = self.resolve_project_code(request)
        if error_response is not None:
            return error_response

        serializer = ArchiveSeriesQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        query = serializer.validated_data

        metric_code = str(query["metric_code"]).strip().lower()
        year = int(query["year"])
        compare_year = int(query.get("compare_year") or (year - 1))
        normalize_cumulative = bool(query.get("normalize_cumulative", True))
        collection_scope = str(query.get("collection_scope") or "both").strip().lower()

        region_id_raw = query.get("region_id") or ""
        entity_key = str(query.get("entity_key") or "").strip().upper()

        try:
            region_scope = self.resolve_region_scope(request, region_id_raw)
        except PermissionDenied as exc:
            return Response({"detail": str(exc.detail)}, status=status.HTTP_403_FORBIDDEN)

        metric = ArchiveMetricDefinition.objects.filter(
            metric_code=metric_code,
            is_active=True,
        ).filter(Q(project_scope=ProjectScope.BOTH) | Q(project_scope=project_code)).first()

        if metric is None:
            return Response(
                {"detail": "Metrique introuvable ou non autorisee pour ce projet."},
                status=status.HTTP_404_NOT_FOUND,
            )

        years_to_fetch = {year, compare_year}
        if normalize_cumulative and metric.value_behavior == ValueBehavior.CUMULATIVE_CONTINUOUS:
            years_to_fetch.add(year - 1)
            years_to_fetch.add(compare_year - 1)

        snapshots = ArchiveSnapshot.objects.filter(
            metric=metric,
            project_code=project_code,
            granularity=PeriodGranularity.MONTH,
            period_year__in=sorted(years_to_fetch),
        )

        if region_scope:
            snapshots = snapshots.filter(region_id=region_scope)

        if entity_key:
            snapshots = snapshots.filter(entity_key=entity_key)
        else:
            if collection_scope == "entity":
                snapshots = snapshots.exclude(entity_key="")
            elif collection_scope == "cumulated":
                snapshots = snapshots.filter(entity_key="")

        month_values = {}
        for snapshot in snapshots:
            key = (snapshot.period_year, snapshot.period_month)
            month_values[key] = month_values.get(key, Decimal("0")) + to_decimal(snapshot.value)

        def compute_effective(raw_value: Decimal | None, target_year: int, month: int):
            if raw_value is None:
                return None

            if not normalize_cumulative or metric.value_behavior == ValueBehavior.POINT:
                return raw_value

            if metric.value_behavior == ValueBehavior.CUMULATIVE_YEARLY:
                prev = month_values.get((target_year, month - 1)) if month > 1 else None
            else:
                if month > 1:
                    prev = month_values.get((target_year, month - 1))
                else:
                    prev = month_values.get((target_year - 1, 12))

            if prev is None:
                return raw_value
            return raw_value - prev

        results = []
        for month in range(1, 13):
            raw_current = month_values.get((year, month))
            raw_compare = month_values.get((compare_year, month))

            effective_current = compute_effective(raw_current, year, month)
            effective_compare = compute_effective(raw_compare, compare_year, month)

            delta = compute_delta(effective_compare, effective_current)
            delta_pct = compute_delta_pct(effective_compare, delta)

            results.append(
                {
                    "month": month,
                    "current_year": {
                        "year": year,
                        "raw_value": to_float_or_none(raw_current),
                        "effective_value": to_float_or_none(effective_current),
                    },
                    "compare_year": {
                        "year": compare_year,
                        "raw_value": to_float_or_none(raw_compare),
                        "effective_value": to_float_or_none(effective_compare),
                    },
                    "delta": to_float_or_none(delta),
                    "delta_pct": to_float_or_none(delta_pct),
                    "trend": classify_trend(delta, metric.trend_polarity),
                }
            )

        return Response(
            {
                "project_code": project_code,
                "metric_code": metric.metric_code,
                "metric_label": metric.label,
                "unit": metric.unit,
                "value_behavior": metric.value_behavior,
                "trend_polarity": metric.trend_polarity,
                "year": year,
                "compare_year": compare_year,
                "normalize_cumulative": normalize_cumulative,
                "collection_scope": collection_scope,
                "results": results,
            }
        )


# ---------------------------------------------------------------------------
# Excluded columns for direct comparison
# ---------------------------------------------------------------------------
_COMPARE_EXCLUDED = frozenset({
    "geom", "raw_payload", "record_source", "import_batch", "import_source",
    "valid_to", "valid_from", "project_code",
    "is_active", "created_at", "updated_at", "imported_at",
})


def _is_excluded_column(name: str) -> bool:
    low = name.lower()
    if low in _COMPARE_EXCLUDED:
        return True
    if low.endswith("_uuid") or low == "uuid":
        return True
    if low.startswith("geom"):
        return True
    return False


def _resolve_year_expr(cfg, table_columns: list[str] | None = None, alias: str = "") -> str:
    """Build a SQL expression that extracts the observation year from a row.

    Only references columns that actually exist in the table to avoid
    ``UndefinedColumn`` errors.
    """
    return _build_year_filter(cfg, alias=alias, table_columns=table_columns)[0]


def _build_year_filter(
    cfg,
    alias: str = "",
    table_columns: list[str] | None = None,
) -> tuple[str, str]:
    """Return (year_expr, year_alias) for SQL.

    Priorite pour determiner l'annee de collecte terrain :

    **CAS 1 — Table avec ``raw_payload``** (formulaires Kobo) :
      1. ``raw_payload->>'today'`` — date de collecte Kobo sur le terrain
      2. Dates metier (``date_suivi``, ``date_obs``...) hors dates systeme
      3. ``year_fields[0]`` — seulement en fallback (car souvent un attribut
         metier comme ``annee_creation`` et non une date de collecte)
      4. ``created_at`` — dernier recours

    **CAS 2 — Table sans ``raw_payload``** (tables derivees) :
      1. ``year_fields[0]`` (ex: ``campagne_yyyy``) — seule source temporelle
      2. Dates metier de ``cfg.date_fields``
      3. ``created_at`` — dernier recours

    ``valid_from`` et ``imported_at`` sont des dates d'import/systeme et ne
    doivent PAS etre utilisees pour determiner la periode de collecte.

    ``table_columns`` filtre les colonnes reellement presentes dans la table
    pour eviter les erreurs ``UndefinedColumn``.
    """
    cols_set = set(table_columns) if table_columns else None
    prefix = f"{alias}." if alias else ""

    def _col_exists(col_name: str) -> bool:
        return cols_set is None or col_name in cols_set

    has_raw_payload = _col_exists("raw_payload")

    _system_dates = {"valid_from", "valid_to", "imported_at", "created_at", "updated_at"}

    # ── Construction de la chaine COALESCE selon le cas ──
    coalesce_parts: list[str] = []

    if has_raw_payload:
        # CAS 1 : table Kobo — raw_payload->>'today' est la source primaire
        coalesce_parts.append(f"({prefix}raw_payload->>'today')::timestamp")

        # Dates metier en second
        for date_col in (cfg.date_fields or ()):
            if date_col.lower() not in _system_dates and _col_exists(date_col):
                coalesce_parts.append(f"{prefix}{_quote_ident(date_col)}")

        # year_fields en fallback (cast en date du 1er janvier)
        if cfg.year_fields:
            year_col = cfg.year_fields[0]
            if _col_exists(year_col):
                coalesce_parts.append(
                    f"make_date({prefix}{_quote_ident(year_col)}::int, 1, 1)::timestamp"
                )

        # Dernier recours : created_at
        if _col_exists("created_at"):
            coalesce_parts.append(f"{prefix}created_at")

        if coalesce_parts:
            coalesce_inner = ", ".join(coalesce_parts)
            year_expr = f"EXTRACT(YEAR FROM COALESCE({coalesce_inner}))::int"
        else:
            year_expr = "NULL::int"
    else:
        # CAS 2 : table sans raw_payload — year_fields est la source primaire
        fallback_parts: list[str] = []

        for date_col in (cfg.date_fields or ()):
            if date_col.lower() not in _system_dates and _col_exists(date_col):
                fallback_parts.append(f"{prefix}{_quote_ident(date_col)}")

        if _col_exists("created_at"):
            fallback_parts.append(f"{prefix}created_at")

        if cfg.year_fields:
            year_col = cfg.year_fields[0]
            if not fallback_parts:
                year_expr = f"{prefix}{_quote_ident(year_col)}::int"
            else:
                coalesce_inner = ", ".join(fallback_parts)
                year_expr = (
                    f"COALESCE({prefix}{_quote_ident(year_col)}::int, "
                    f"EXTRACT(YEAR FROM COALESCE({coalesce_inner}))::int)"
                )
        else:
            if not fallback_parts:
                year_expr = "NULL::int"
            else:
                coalesce_inner = ", ".join(fallback_parts)
                year_expr = f"EXTRACT(YEAR FROM COALESCE({coalesce_inner}))::int"

    return year_expr, "obs_year"


class DirectEntitiesView(ArchiveBaseView):
    """List unique entities from core.* tables with available years."""
    throttle_scope = "stats"

    def get(self, request, dataset_code: str):
        project_code, error_response = self.resolve_project_code(request)
        if error_response is not None:
            return error_response

        serializer = DirectEntitiesQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        query = serializer.validated_data

        year_filter = query.get("year")
        region_raw = str(query.get("region_id") or "").strip()
        search_term = str(query.get("q") or "").strip()

        try:
            region_scope = self.resolve_region_scope(request, region_raw)
        except PermissionDenied as exc:
            return Response({"detail": str(exc.detail)}, status=status.HTTP_403_FORBIDDEN)

        configs = _dataset_configs(project_code, dataset_code)
        if not configs:
            return Response(
                {"detail": "dataset_code invalide pour le projet courant."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        all_entities: dict[str, dict] = {}

        for cfg in configs:
            entity_field = cfg.entity_fields[0] if cfg.entity_fields else None
            if not entity_field:
                continue

            entity_name_field = cfg.entity_fields[1] if len(cfg.entity_fields) > 1 else None

            try:
                if cfg.custom_sql:
                    cs_cols = _custom_sql_columns(cfg.custom_sql)
                    year_expr, year_alias = _build_year_filter(cfg, alias="src", table_columns=cs_cols)
                    where_sql = ""
                    params: list = [project_code]
                    if region_scope and cfg.custom_region_filter_sql:
                        where_sql = f" AND {cfg.custom_region_filter_sql}"
                        params.append(region_scope)

                    base_sql = str(cfg.custom_sql).format(region_filter=where_sql).strip().rstrip(";")
                    entity_col = "src.entity_key"
                    name_col = "NULL"

                    sql = (
                        f"SELECT {entity_col} AS entity_id, {name_col} AS entity_name, "
                        f"{year_expr} AS {year_alias}, COUNT(*) AS cnt "
                        f"FROM ({base_sql}) AS src "
                        f"WHERE {year_expr} IS NOT NULL "
                    )
                    if year_filter:
                        sql += f" AND {year_expr} = %s "
                        params.append(year_filter)
                    if search_term:
                        sql += f" AND {entity_col}::text ILIKE %s "
                        params.append(f"%{search_term}%")
                    cs_group_cols = [entity_col, year_alias]
                    if name_col != "NULL":
                        cs_group_cols.insert(1, name_col)
                    sql += f" GROUP BY {', '.join(cs_group_cols)} ORDER BY entity_id, {year_alias}"
                else:
                    table_columns = _core_table_columns(cfg.table_name)
                    if not table_columns:
                        continue

                    year_expr, year_alias = _build_year_filter(cfg, table_columns=table_columns)

                    entity_col = _quote_ident(entity_field)
                    name_col = _quote_ident(entity_name_field) if entity_name_field and entity_name_field in table_columns else "NULL"

                    where_parts = ["project_code = %s"]
                    params: list = [project_code]

                    if region_scope and cfg.region_field in table_columns:
                        where_parts.append(f"COALESCE({_quote_ident(cfg.region_field)}, '') = %s")
                        params.append(region_scope)

                    sql = (
                        f"SELECT {entity_col} AS entity_id, {name_col} AS entity_name, "
                        f"{year_expr} AS {year_alias}, COUNT(*) AS cnt "
                        f"FROM core.{_quote_ident(cfg.table_name)} "
                        f"WHERE {' AND '.join(where_parts)} AND {year_expr} IS NOT NULL "
                    )

                    if year_filter:
                        sql += f" AND {year_expr} = %s "
                        params.append(year_filter)
                    if search_term:
                        sql += f" AND {entity_col}::text ILIKE %s "
                        params.append(f"%{search_term}%")

                    reg_group_cols = [entity_col, year_alias]
                    if name_col != "NULL":
                        reg_group_cols.insert(1, name_col)
                    sql += f" GROUP BY {', '.join(reg_group_cols)} ORDER BY entity_id, {year_alias}"

                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
                    rows = cursor.fetchall()

                for entity_id, entity_name, obs_year, cnt in rows:
                    eid = str(entity_id or "").strip()
                    if not eid:
                        continue
                    obs_yr = int(obs_year) if obs_year else None
                    if obs_yr is None:
                        continue

                    if eid not in all_entities:
                        all_entities[eid] = {
                            "entity_id": eid,
                            "entity_name": str(entity_name or eid).strip(),
                            "years": set(),
                            "records_count": 0,
                        }
                    all_entities[eid]["years"].add(obs_yr)
                    all_entities[eid]["records_count"] += int(cnt)
            except Exception as exc:
                import logging
                logging.getLogger("archive_core").warning(
                    "DirectEntitiesView error for table %s: %s", cfg.table_name, exc
                )
                continue

        results = []
        for eid, info in sorted(all_entities.items()):
            years = sorted(info["years"])
            results.append({
                "entity_id": info["entity_id"],
                "entity_name": info["entity_name"],
                "years": years,
                "records_count": info["records_count"],
            })

        return Response({
            "project_code": project_code,
            "dataset_code": dataset_code,
            "count": len(results),
            "results": results,
        })


class CompareEntityView(ArchiveBaseView):
    """Compare all raw fields of a single entity between two years."""
    throttle_scope = "stats"

    def get(self, request, dataset_code: str):
        project_code, error_response = self.resolve_project_code(request)
        if error_response is not None:
            return error_response

        serializer = CompareEntityQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        query = serializer.validated_data

        entity_id = str(query["entity_id"]).strip()
        year_a = int(query["year_a"])
        year_b = int(query["year_b"])

        configs = _dataset_configs(project_code, dataset_code)
        if not configs:
            return Response(
                {"detail": "dataset_code invalide pour le projet courant."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cfg = configs[0]
        entity_field = cfg.entity_fields[0] if cfg.entity_fields else None
        if not entity_field:
            return Response(
                {"detail": "Ce dataset ne possede pas de champ entite."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        def _fetch_entity_row(target_year: int) -> dict | None:
            try:
                if cfg.custom_sql:
                    cs_cols = _custom_sql_columns(cfg.custom_sql)
                    year_expr, _ = _build_year_filter(cfg, alias="src", table_columns=cs_cols)
                    params: list = [project_code]

                    base_sql = str(cfg.custom_sql).format(region_filter="").strip().rstrip(";")

                    # ORDER BY: prefer parent_valid_from or valid_from from custom_sql columns
                    order_col = "src.parent_valid_from" if "parent_valid_from" in cs_cols else "src.valid_from" if "valid_from" in cs_cols else None
                    order_clause = f"ORDER BY {order_col} DESC NULLS LAST " if order_col else ""

                    sql = (
                        f"SELECT src.*, {year_expr} AS obs_year "
                        f"FROM ({base_sql}) AS src "
                        f"WHERE src.entity_key = %s AND {year_expr} = %s "
                        f"{order_clause}LIMIT 1"
                    )
                    params.extend([entity_id, target_year])
                else:
                    table_columns = _core_table_columns(cfg.table_name)
                    if not table_columns:
                        return None

                    year_expr, _ = _build_year_filter(cfg, table_columns=table_columns)
                    safe_cols = [
                        _quote_ident(c) for c in table_columns if not _is_excluded_column(c)
                    ]
                    if not safe_cols:
                        return None

                    # Build ORDER BY only from existing columns
                    order_parts = []
                    for oc in ("valid_from", "created_at"):
                        if oc in table_columns:
                            order_parts.append(oc)
                    order_clause = f"ORDER BY COALESCE({', '.join(order_parts)}) DESC NULLS LAST" if order_parts else ""

                    sql = (
                        f"SELECT {', '.join(safe_cols)}, {year_expr} AS obs_year "
                        f"FROM core.{_quote_ident(cfg.table_name)} "
                        f"WHERE project_code = %s AND {_quote_ident(entity_field)} = %s "
                        f"AND {year_expr} = %s "
                        f"{order_clause} LIMIT 1"
                    )
                    params: list = [project_code, entity_id, target_year]

                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
                    columns = [str(col[0]) for col in cursor.description]
                    row = cursor.fetchone()
                    if row is None:
                        return None
                    return {columns[idx]: _json_ready(value) for idx, value in enumerate(row)}
            except Exception as exc:
                import logging
                logging.getLogger("archive_core").warning(
                    "_fetch_entity_row error for %s year=%s: %s", entity_id, target_year, exc
                )
                return None

        row_a = _fetch_entity_row(year_a)
        row_b = _fetch_entity_row(year_b)

        if row_a is None and row_b is None:
            return Response({
                "entity_id": entity_id,
                "year_a": year_a,
                "year_b": year_b,
                "fields": [],
                "records_a": [],
                "records_b": [],
                "summary": {"total_fields": 0, "progressions": 0, "regressions": 0, "stables": 0, "changed_text": 0},
            })

        # Determine the union of fields — exclude technical, skip, and year columns
        skip_set = {s.lower() for s in cfg.skip_fields}
        year_set = {y.lower() for y in cfg.year_fields}
        all_fields: list[str] = []
        seen = set()
        for row in (row_a, row_b):
            if row is None:
                continue
            for key in row:
                low = key.lower()
                if low in seen or _is_excluded_column(key) or low == "obs_year":
                    continue
                if low in skip_set or low in year_set:
                    continue
                seen.add(low)
                all_fields.append(key)

        fields = []
        progressions = 0
        regressions = 0
        stables = 0
        changed_text = 0

        for field in all_fields:
            val_a = row_a.get(field) if row_a else None
            val_b = row_b.get(field) if row_b else None

            field_label = get_column_label(field)
            is_numeric = isinstance(val_a, (int, float)) or isinstance(val_b, (int, float))

            entry: dict = {
                "field": field,
                "label": field_label,
                "value_a": val_a,
                "value_b": val_b,
            }

            if is_numeric:
                num_a = float(val_a) if val_a is not None else None
                num_b = float(val_b) if val_b is not None else None
                if num_a is not None and num_b is not None:
                    delta = num_b - num_a
                    delta_pct = (delta / num_a * 100) if num_a != 0 else None
                    if abs(delta) < 0.000001:
                        trend = "stable"
                        stables += 1
                    elif delta > 0:
                        trend = "progression"
                        progressions += 1
                    else:
                        trend = "regression"
                        regressions += 1
                    entry["delta"] = round(delta, 4)
                    entry["delta_pct"] = round(delta_pct, 2) if delta_pct is not None else None
                    entry["trend"] = trend
                else:
                    entry["delta"] = None
                    entry["delta_pct"] = None
                    entry["trend"] = "insufficient_data"
            else:
                entry["delta"] = None
                entry["delta_pct"] = None
                changed = str(val_a or "") != str(val_b or "")
                entry["changed"] = changed
                if changed:
                    changed_text += 1
                else:
                    stables += 1
                entry["trend"] = "changed" if changed else "stable"

            fields.append(entry)

        return Response({
            "entity_id": entity_id,
            "year_a": year_a,
            "year_b": year_b,
            "fields": fields,
            "records_a": [row_a] if row_a else [],
            "records_b": [row_b] if row_b else [],
            "summary": {
                "total_fields": len(fields),
                "progressions": progressions,
                "regressions": regressions,
                "stables": stables,
                "changed_text": changed_text,
            },
        })


class CompareGlobalView(ArchiveBaseView):
    """Compare aggregate numeric fields across all entities between two years."""
    throttle_scope = "stats"

    def get(self, request, dataset_code: str):
        project_code, error_response = self.resolve_project_code(request)
        if error_response is not None:
            return error_response

        serializer = CompareGlobalQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        query = serializer.validated_data

        year_a = int(query["year_a"])
        year_b = int(query["year_b"])
        region_raw = str(query.get("region_id") or "").strip()

        try:
            region_scope = self.resolve_region_scope(request, region_raw)
        except PermissionDenied as exc:
            return Response({"detail": str(exc.detail)}, status=status.HTTP_403_FORBIDDEN)

        configs = _dataset_configs(project_code, dataset_code)
        if not configs:
            return Response(
                {"detail": "dataset_code invalide pour le projet courant."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cfg = configs[0]

        def _fetch_global_agg(target_year: int) -> dict:
            try:
                if cfg.custom_sql:
                    cs_cols = _custom_sql_columns(cfg.custom_sql)
                    year_expr, _ = _build_year_filter(cfg, alias="src", table_columns=cs_cols)
                    where_sql = ""
                    params: list = [project_code]
                    if region_scope and cfg.custom_region_filter_sql:
                        where_sql = f" AND {cfg.custom_region_filter_sql}"
                        params.append(region_scope)

                    base_sql = str(cfg.custom_sql).format(region_filter=where_sql).strip().rstrip(";")

                    # For custom_sql configs, use include_fields
                    numeric_cols = list(cfg.include_fields)
                    if not numeric_cols:
                        return {}

                    agg_parts = []
                    for col in numeric_cols:
                        agg_parts.append(f"SUM(src.{_quote_ident(col)}) AS sum_{col}")
                        agg_parts.append(f"AVG(src.{_quote_ident(col)}) AS avg_{col}")

                    sql = (
                        f"SELECT COUNT(*) AS entity_count, {', '.join(agg_parts)} "
                        f"FROM ({base_sql}) AS src "
                        f"WHERE {year_expr} = %s "
                    )
                    params.append(target_year)

                    with connection.cursor() as cursor:
                        cursor.execute(sql, params)
                        columns = [str(col[0]) for col in cursor.description]
                        row = cursor.fetchone()
                        if row is None:
                            return {}
                        return {columns[idx]: _json_ready(value) for idx, value in enumerate(row)}
                else:
                    table_columns = _core_table_columns(cfg.table_name)
                    if not table_columns:
                        return {}

                    # Find numeric columns
                    col_types = {}
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            SELECT column_name, data_type
                            FROM information_schema.columns
                            WHERE table_schema='core' AND table_name=%s
                            ORDER BY ordinal_position
                            """,
                            [cfg.table_name],
                        )
                        for name, dtype in cursor.fetchall():
                            col_types[str(name)] = str(dtype).lower()

                    numeric_types = {"integer", "bigint", "smallint", "numeric", "double precision", "real"}
                    skip_set = {s.lower() for s in cfg.skip_fields}
                    numeric_cols = [
                        name for name, dtype in col_types.items()
                        if dtype in numeric_types
                        and not _is_excluded_column(name)
                        and name.lower() not in skip_set
                        and not name.lower().startswith("id_")
                        and not name.lower().endswith("_id")
                        and name.lower() not in {y.lower() for y in cfg.year_fields}
                    ]

                    if not numeric_cols:
                        return {}

                    year_expr, _ = _build_year_filter(cfg, table_columns=table_columns)

                    agg_parts = []
                    for col in numeric_cols:
                        agg_parts.append(f"SUM({_quote_ident(col)}) AS sum_{col}")
                        agg_parts.append(f"AVG({_quote_ident(col)}) AS avg_{col}")

                    sql = (
                        f"SELECT COUNT(*) AS entity_count, {', '.join(agg_parts)} "
                        f"FROM core.{_quote_ident(cfg.table_name)} "
                        f"WHERE project_code = %s AND {year_expr} = %s "
                    )
                    params: list = [project_code, target_year]

                    if region_scope and cfg.region_field in table_columns:
                        sql += f" AND COALESCE({_quote_ident(cfg.region_field)}, '') = %s "
                        params.append(region_scope)

                    with connection.cursor() as cursor:
                        cursor.execute(sql, params)
                        columns = [str(col[0]) for col in cursor.description]
                        row = cursor.fetchone()
                        if row is None:
                            return {}
                        return {columns[idx]: _json_ready(value) for idx, value in enumerate(row)}
            except Exception as exc:
                import logging
                logging.getLogger("archive_core").warning(
                    "_fetch_global_agg error for %s year=%s: %s", cfg.table_name, target_year, exc
                )
                return {}

        agg_a = _fetch_global_agg(year_a)
        agg_b = _fetch_global_agg(year_b)

        entities_a = agg_a.pop("entity_count", 0) or 0
        entities_b = agg_b.pop("entity_count", 0) or 0

        # Extract column names from sum_ prefixed keys
        col_names = sorted({
            k[4:] for k in list(agg_a.keys()) + list(agg_b.keys())
            if k.startswith("sum_")
        })

        fields = []
        for col in col_names:
            sum_a = agg_a.get(f"sum_{col}")
            sum_b = agg_b.get(f"sum_{col}")
            avg_a = agg_a.get(f"avg_{col}")
            avg_b = agg_b.get(f"avg_{col}")

            # Determine if avg or sum based on column name heuristic
            col_low = col.lower()
            is_avg = any(k in col_low for k in ("pct", "taux", "ratio", "moy", "satisfaction", "niveau", "rendement"))
            agg_type = "MOY" if is_avg else "SUM"
            val_a = float(avg_a) if is_avg and avg_a is not None else (float(sum_a) if sum_a is not None else None)
            val_b = float(avg_b) if is_avg and avg_b is not None else (float(sum_b) if sum_b is not None else None)

            entry: dict = {
                "field": col,
                "label": get_column_label(col),
                "type": agg_type,
                "value_a": round(val_a, 4) if val_a is not None else None,
                "value_b": round(val_b, 4) if val_b is not None else None,
                "entities_a": int(entities_a),
                "entities_b": int(entities_b),
            }

            if val_a is not None and val_b is not None:
                delta = val_b - val_a
                delta_pct = (delta / val_a * 100) if val_a != 0 else None
                if abs(delta) < 0.000001:
                    trend = "stable"
                elif delta > 0:
                    trend = "progression"
                else:
                    trend = "regression"
                entry["delta"] = round(delta, 4)
                entry["delta_pct"] = round(delta_pct, 2) if delta_pct is not None else None
                entry["trend"] = trend
            else:
                entry["delta"] = None
                entry["delta_pct"] = None
                entry["trend"] = "insufficient_data"

            fields.append(entry)

        return Response({
            "project_code": project_code,
            "dataset_code": dataset_code,
            "year_a": year_a,
            "year_b": year_b,
            "fields": fields,
        })
