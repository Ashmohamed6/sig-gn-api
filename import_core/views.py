from __future__ import annotations

import json
import uuid
from typing import Any

from django.db import connection
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import RefProject
from data_api.mixins import CurrentProjectRequiredMixin

from .csv_parser import CsvParserError, parse_csv_upload, sample_rows
from .core_etl import CorePublishError, publish_dataset_to_core
from .etl import (
    build_validation_report,
    categorize_column,
    execute_import_into_stage,
    find_potential_existing_identifiers,
    get_stage_columns,
    list_datasets_for_project,
    resolve_dataset_definition,
)
from .models import ImportLog
from .permissions import (
    CanUseImportPipelinePermission,
    ImportScope,
    actor_project_ids,
    ensure_actor_has_project_access,
    is_global_admin_user,
    normalize_role,
    resolve_region_scope,
)
from .serializers import ImportLogQuerySerializer, ImportRequestSerializer


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_load(text: str | None) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"message": raw}
    return parsed if isinstance(parsed, dict) else {"message": raw}


def create_import_log_entry(
    import_uuid: uuid.UUID,
    project_id: str,
    dataset_code: str,
    rows_total: int,
    metadata: dict[str, Any],
) -> None:
    sql = """
        INSERT INTO audit.import_log (
            import_uuid,
            project_id,
            dataset,
            rows_total,
            rows_ok,
            rows_error,
            started_at,
            status,
            message
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    params = [
        str(import_uuid),
        project_id,
        dataset_code,
        rows_total,
        0,
        rows_total,
        timezone.now(),
        "running",
        _json_dump(metadata),
    ]
    with connection.cursor() as cursor:
        cursor.execute(sql, params)


def finalize_import_log_entry(
    import_uuid: uuid.UUID,
    *,
    rows_ok: int,
    rows_error: int,
    status_text: str,
    metadata: dict[str, Any],
) -> None:
    sql = """
        UPDATE audit.import_log
        SET ended_at = %s,
            status = %s,
            rows_ok = %s,
            rows_error = %s,
            message = %s
        WHERE import_uuid = %s
    """
    params = [
        timezone.now(),
        status_text,
        rows_ok,
        rows_error,
        _json_dump(metadata),
        str(import_uuid),
    ]
    with connection.cursor() as cursor:
        cursor.execute(sql, params)


def create_etl_run_entry(run_id: uuid.UUID, trigger: str, project_id: str | None, message: str) -> None:
    sql = """
        INSERT INTO audit.etl_run (
            run_id,
            trigger,
            project_id,
            started_at,
            status,
            errors_cnt,
            messages
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            [str(run_id), trigger, project_id, timezone.now(), "running", 0, message],
        )


def finalize_etl_run_entry(
    run_id: uuid.UUID,
    *,
    status_text: str,
    errors_cnt: int,
    message: str,
) -> None:
    sql = """
        UPDATE audit.etl_run
        SET ended_at = %s,
            status = %s,
            errors_cnt = %s,
            messages = %s
        WHERE run_id = %s
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, [timezone.now(), status_text, errors_cnt, message, str(run_id)])


def _quote_ident(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


class ImportBaseView(CurrentProjectRequiredMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, CanUseImportPipelinePermission]

    def get_throttles(self):
        if not getattr(self, "throttle_scope", None):
            method = getattr(self.request, "method", "GET").upper()
            self.throttle_scope = "import_read" if method in {"GET", "HEAD", "OPTIONS"} else "import_write"
        return super().get_throttles()

    def resolve_scope(self, request, validated_data: dict[str, Any]) -> tuple[ImportScope, Response | None]:
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return ImportScope(project_id="", project_code="", region_id=None), error_response

        actor = request.user
        ensure_actor_has_project_access(actor, project)

        project_code = str(getattr(project, "code_fonc", "") or "").upper()
        project_code_body = validated_data.get("project_code")
        if project_code_body and project_code_body != project_code:
            return (
                ImportScope(project_id="", project_code="", region_id=None),
                Response(
                    {"detail": "project_code ne correspond pas au header X-Project-Code."},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            )

        region_id = resolve_region_scope(actor, validated_data.get("region_id"))
        return (
            ImportScope(
                project_id=str(project.project_id),
                project_code=project_code,
                region_id=region_id,
            ),
            None,
        )

    @staticmethod
    def _read_uploaded_file(request):
        uploaded = request.FILES.get("file")
        if uploaded is None:
            raise CsvParserError("Le champ fichier 'file' est obligatoire.")
        return uploaded


class ImportDatasetsView(ImportBaseView):
    def get(self, request):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        ensure_actor_has_project_access(request.user, project)
        project_code = str(getattr(project, "code_fonc", "") or "").upper()
        datasets = list_datasets_for_project(project_code)

        return Response(
            {
                "project_code": project_code,
                "count": len(datasets),
                "datasets": [
                    {
                        "dataset_code": d.code,
                        "label": d.label,
                        "stage_table": f"stage.{d.stage_table}",
                    }
                    for d in datasets
                ],
            }
        )


class ImportColumnsView(ImportBaseView):
    def get(self, request, dataset_code: str):
        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        ensure_actor_has_project_access(request.user, project)
        project_code = str(getattr(project, "code_fonc", "") or "").upper()

        dataset = resolve_dataset_definition(dataset_code, project_code=project_code)
        if dataset is None:
            return Response(
                {"detail": "dataset_code invalide pour ce projet."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            stage_columns = get_stage_columns(dataset.stage_table)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "dataset_code": dataset.code,
                "stage_table": f"stage.{dataset.stage_table}",
                "columns": [
                    {
                        "name": col.name,
                        "data_type": col.data_type,
                        "udt_name": col.udt_name,
                        "is_nullable": col.is_nullable,
                        "category": categorize_column(col.name),
                    }
                    for col in stage_columns
                ],
            }
        )


class ImportValidateView(ImportBaseView):
    def post(self, request):
        serializer = ImportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        scope, error_response = self.resolve_scope(request, serializer.validated_data)
        if error_response is not None:
            return error_response

        try:
            uploaded = self._read_uploaded_file(request)
            parse_result = parse_csv_upload(uploaded)
        except CsvParserError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        dataset = resolve_dataset_definition(
            serializer.validated_data["dataset_code"],
            project_code=scope.project_code,
        )
        if dataset is None:
            return Response(
                {"detail": "dataset_code invalide pour ce projet."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            stage_columns = get_stage_columns(dataset.stage_table)
            existing_identifiers = find_potential_existing_identifiers(
                dataset=dataset,
                stage_columns=stage_columns,
                records=parse_result.records,
                project_code=scope.project_code,
            )
            report = build_validation_report(
                parse_result=parse_result,
                dataset=dataset,
                stage_columns=stage_columns,
                project_code=scope.project_code,
                region_id=scope.region_id,
                existing_identifiers=existing_identifiers,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                **report,
                "preview": sample_rows(parse_result.records, limit=10),
            }
        )


class ImportExecuteView(ImportBaseView):
    def post(self, request):
        serializer = ImportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        scope, error_response = self.resolve_scope(request, serializer.validated_data)
        if error_response is not None:
            return error_response

        try:
            uploaded = self._read_uploaded_file(request)
            parse_result = parse_csv_upload(uploaded)
        except CsvParserError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        dataset = resolve_dataset_definition(
            serializer.validated_data["dataset_code"],
            project_code=scope.project_code,
        )
        if dataset is None:
            return Response(
                {"detail": "dataset_code invalide pour ce projet."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            stage_columns = get_stage_columns(dataset.stage_table)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        validation = build_validation_report(
            parse_result=parse_result,
            dataset=dataset,
            stage_columns=stage_columns,
            project_code=scope.project_code,
            region_id=scope.region_id,
        )

        import_uuid = uuid.uuid4()
        actor = request.user
        base_metadata = {
            "project_code": scope.project_code,
            "region_id": scope.region_id,
            "dataset_code": dataset.code,
            "dataset_label": dataset.label,
            "actor_username": getattr(actor, "username", None),
            "actor_role": normalize_role(getattr(actor, "role", "")),
        }
        create_import_log_entry(
            import_uuid=import_uuid,
            project_id=scope.project_id,
            dataset_code=dataset.code,
            rows_total=parse_result.row_count,
            metadata=base_metadata,
        )

        if not validation["valid"]:
            finalize_import_log_entry(
                import_uuid,
                rows_ok=0,
                rows_error=parse_result.row_count,
                status_text="failed_validation",
                metadata={**base_metadata, "validation": validation},
            )
            return Response(
                {
                    "detail": "Validation bloquante. Corrigez le fichier puis relancez.",
                    "import_uuid": str(import_uuid),
                    "validation": validation,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = execute_import_into_stage(
                dataset=dataset,
                parse_result=parse_result,
                project_code=scope.project_code,
                region_id=scope.region_id,
                import_batch=import_uuid,
                import_source="admin_csv",
                on_duplicate=serializer.validated_data.get("on_duplicate", "update"),
            )
        except Exception as exc:
            finalize_import_log_entry(
                import_uuid,
                rows_ok=0,
                rows_error=parse_result.row_count,
                status_text="failed_runtime",
                metadata={**base_metadata, "error": str(exc)},
            )
            return Response(
                {
                    "detail": "Echec de l'import pendant l'execution.",
                    "import_uuid": str(import_uuid),
                    "error": str(exc),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        rows_ok = int(result["rows_ok"])
        rows_error = int(result["rows_error"])
        if rows_ok > 0 and rows_error == 0:
            log_status = "success"
        elif rows_ok > 0 and rows_error > 0:
            log_status = "partial_success"
        else:
            log_status = "failed"

        finalize_import_log_entry(
            import_uuid,
            rows_ok=rows_ok,
            rows_error=rows_error,
            status_text=log_status,
            metadata={**base_metadata, "validation": validation, "result": result},
        )

        http_status = status.HTTP_200_OK if rows_ok > 0 else status.HTTP_400_BAD_REQUEST
        return Response(
            {
                "detail": "Import termine.",
                "import_uuid": str(import_uuid),
                "status": log_status,
                "validation": validation,
                "result": result,
            },
            status=http_status,
        )


class ImportPublishView(ImportBaseView):
    def post(self, request):
        serializer = ImportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        scope, error_response = self.resolve_scope(request, serializer.validated_data)
        if error_response is not None:
            return error_response

        dataset = resolve_dataset_definition(
            serializer.validated_data["dataset_code"],
            project_code=scope.project_code,
        )
        if dataset is None:
            return Response(
                {"detail": "dataset_code invalide pour ce projet."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        run_id = uuid.uuid4()
        create_etl_run_entry(
            run_id=run_id,
            trigger="publish_stage_to_core",
            project_id=scope.project_id,
            message=_json_dump(
                {
                    "dataset_code": dataset.code,
                    "dataset_label": dataset.label,
                    "project_code": scope.project_code,
                    "region_id": scope.region_id,
                    "actor_username": getattr(request.user, "username", None),
                    "actor_role": normalize_role(getattr(request.user, "role", "")),
                }
            ),
        )

        try:
            publish_result = publish_dataset_to_core(
                dataset=dataset,
                project_code=scope.project_code,
                region_id=scope.region_id,
            )
        except CorePublishError as exc:
            finalize_etl_run_entry(
                run_id=run_id,
                status_text="failed",
                errors_cnt=1,
                message=_json_dump({"error": str(exc)}),
            )
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            finalize_etl_run_entry(
                run_id=run_id,
                status_text="failed_runtime",
                errors_cnt=1,
                message=_json_dump({"error": str(exc)}),
            )
            return Response(
                {"detail": "Echec ETL stage->core.", "error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        finalize_etl_run_entry(
            run_id=run_id,
            status_text="success",
            errors_cnt=0,
            message=_json_dump(publish_result),
        )

        return Response(
            {
                "detail": "ETL stage->core termine.",
                "run_id": str(run_id),
                "dataset_code": dataset.code,
                "project_code": scope.project_code,
                "region_id": scope.region_id,
                **publish_result,
            },
            status=status.HTTP_200_OK,
        )


class ImportLogView(ImportBaseView):
    def get(self, request):
        query_serializer = ImportLogQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        filters = query_serializer.validated_data

        actor = request.user
        is_global_admin = is_global_admin_user(actor)

        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        ensure_actor_has_project_access(actor, project)
        current_project_code = str(getattr(project, "code_fonc", "") or "").upper()
        requested_project_code = str(filters.get("project_code") or current_project_code).strip().upper()

        project_lookup = {
            str(p.project_id): {"project_code": str(p.code_fonc or "").upper(), "project_label": p.libelle_public}
            for p in RefProject.objects.filter(actif=True)
        }
        project_id_by_code = {v["project_code"]: pid for pid, v in project_lookup.items() if v["project_code"]}

        queryset = ImportLog.objects.all().order_by("-started_at")

        if requested_project_code:
            project_id = project_id_by_code.get(requested_project_code)
            if not project_id:
                return Response(
                    {"detail": f"Aucun projet actif pour '{requested_project_code}'."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            queryset = queryset.filter(project_id=project_id)

        if not is_global_admin:
            scope_ids = actor_project_ids(actor)
            if not scope_ids:
                return Response({"count": 0, "results": []})
            queryset = queryset.filter(project_id__in=list(scope_ids))

        if filters.get("dataset_code"):
            dataset_code = str(filters["dataset_code"]).strip().lower()
            queryset = queryset.filter(dataset__iexact=dataset_code)

        if filters.get("status"):
            log_status = str(filters["status"]).strip().lower()
            queryset = queryset.filter(status__iexact=log_status)

        if filters.get("date_from"):
            queryset = queryset.filter(started_at__gte=filters["date_from"])

        if filters.get("date_to"):
            queryset = queryset.filter(started_at__lte=filters["date_to"])

        region_filter = str(filters.get("region_id") or "").strip() or None
        role = normalize_role(getattr(actor, "role", ""))
        actor_region = getattr(actor, "region_id", None)
        force_region = actor_region if role == "manager" and not is_global_admin else None

        logs = list(queryset[:1000])

        serialized_logs: list[dict[str, Any]] = []
        for log in logs:
            metadata = _json_load(log.message)
            region_id = metadata.get("region_id")

            if force_region and region_id and region_id != force_region:
                continue
            if force_region and not region_id:
                continue
            if region_filter and region_id != region_filter:
                continue

            project_meta = project_lookup.get(str(log.project_id), {})
            serialized_logs.append(
                {
                    "import_uuid": str(log.import_uuid),
                    "project_id": str(log.project_id) if log.project_id else None,
                    "project_code": project_meta.get("project_code"),
                    "project_label": project_meta.get("project_label"),
                    "dataset_code": log.dataset,
                    "rows_total": log.rows_total,
                    "rows_ok": log.rows_ok,
                    "rows_error": log.rows_error,
                    "status": log.status,
                    "started_at": log.started_at,
                    "ended_at": log.ended_at,
                    "region_id": region_id,
                    "actor_username": metadata.get("actor_username"),
                    "message": metadata.get("message"),
                }
            )

        paginator = PageNumberPagination()
        paginator.page_size_query_param = "page_size"
        paginator.page_size = 20
        page = paginator.paginate_queryset(serialized_logs, request, view=self)
        return paginator.get_paginated_response(page)


class ImportRefreshViewsView(ImportBaseView):
    @staticmethod
    def _list_target_matviews() -> list[tuple[str, str]]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT schemaname, matviewname
                FROM pg_matviews
                WHERE schemaname IN ('core', 'marts')
                ORDER BY schemaname, matviewname
                """
            )
            return cursor.fetchall()

    @staticmethod
    def _refresh_single_matview(schema_name: str, matview_name: str) -> None:
        quoted = f"{_quote_ident(schema_name)}.{_quote_ident(matview_name)}"

        try:
            with connection.cursor() as cursor:
                cursor.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {quoted}")
            return
        except Exception:
            pass

        with connection.cursor() as cursor:
            cursor.execute(f"REFRESH MATERIALIZED VIEW {quoted}")

    def post(self, request):
        actor = request.user
        if not is_global_admin_user(actor):
            raise PermissionDenied("Seul l'admin global peut rafraichir les vues materialisees.")

        project, error_response = self.get_current_project(request)
        if error_response is not None:
            return error_response

        run_id = uuid.uuid4()
        create_etl_run_entry(
            run_id=run_id,
            trigger="manual_refresh",
            project_id=str(project.project_id),
            message="Demarrage refresh materialized views core/marts.",
        )

        rows = self._list_target_matviews()

        if not rows:
            finalize_etl_run_entry(
                run_id=run_id,
                status_text="success",
                errors_cnt=0,
                message=_json_dump({"refreshed": [], "failed": [], "note": "no_matviews_found"}),
            )
            return Response(
                {
                    "detail": "Aucune vue materialisee core/marts a rafraichir.",
                    "run_id": str(run_id),
                    "status": "success",
                    "refreshed_count": 0,
                    "failed_count": 0,
                    "refreshed": [],
                    "failed": [],
                },
                status=status.HTTP_200_OK,
            )

        refreshed: list[str] = []
        failed: list[dict[str, str]] = []
        for schema_name, matview_name in rows:
            full_name = f"{schema_name}.{matview_name}"
            try:
                self._refresh_single_matview(schema_name, matview_name)
                refreshed.append(full_name)
            except Exception as exc:
                failed.append({"view": full_name, "error": str(exc)})

        run_status = "success" if not failed else ("partial_success" if refreshed else "failed")
        finalize_etl_run_entry(
            run_id=run_id,
            status_text=run_status,
            errors_cnt=len(failed),
            message=_json_dump({"refreshed": refreshed, "failed": failed}),
        )

        response_status = (
            status.HTTP_200_OK
            if run_status in {"success", "partial_success"}
            else status.HTTP_500_INTERNAL_SERVER_ERROR
        )

        return Response(
            {
                "detail": "Refresh termine.",
                "run_id": str(run_id),
                "status": run_status,
                "refreshed_count": len(refreshed),
                "failed_count": len(failed),
                "refreshed": refreshed,
                "failed": failed,
            },
            status=response_status,
        )
