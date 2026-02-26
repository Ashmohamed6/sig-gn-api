import json
import uuid

from django.conf import settings
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from data_api.mixins import CurrentProjectRequiredMixin
from import_core.core_etl import CorePublishError, publish_dataset_to_core
from import_core.csv_parser import CsvParseResult, normalize_header_name
from import_core.etl import build_validation_report, execute_import_into_stage, get_stage_columns, resolve_dataset_definition

from .kobo import KoboClient, KoboSyncError, filter_records_since, list_project_forms, resolve_project_form
from .models import WorkflowAction, WorkflowActionLog, WorkflowStatus, WorkflowSubmission
from .serializers import (
    WorkflowActionLogSerializer,
    WorkflowKoboSyncSerializer,
    WorkflowRejectSerializer,
    WorkflowSubmissionCreateSerializer,
    WorkflowSubmissionSerializer,
    WorkflowSubmissionUpdateSerializer,
)

ROLE_READER = "reader"
ROLE_EDITOR = "editor"
ROLE_MANAGER = "manager"
ROLE_PROJECT_MANAGER = "project_manager"
ROLE_ADMIN = "admin"

WRITE_ROLES = {ROLE_EDITOR, ROLE_MANAGER, ROLE_PROJECT_MANAGER, ROLE_ADMIN}
VALIDATE_REJECT_ROLES = {ROLE_MANAGER, ROLE_PROJECT_MANAGER, ROLE_ADMIN}
PUBLISH_ROLES = {ROLE_PROJECT_MANAGER, ROLE_ADMIN}


def normalize_role(value) -> str:
    return str(value or "").strip().lower()


def is_global_admin(user) -> bool:
    role = normalize_role(getattr(user, "role", ""))
    return bool(user and user.is_authenticated and (user.is_superuser or user.is_staff or role == ROLE_ADMIN))


def actor_project_ids(user) -> set[str]:
    if not user or not hasattr(user, "projects"):
        return set()
    return {str(pid) for pid in user.projects.values_list("project_id", flat=True)}


def _normalize_record_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _extract_payload_records(payload) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        return []

    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        return []

    normalized_records: list[dict[str, str]] = []
    for row in raw_records:
        if not isinstance(row, dict):
            continue

        normalized_row: dict[str, str] = {}
        for raw_key, raw_value in row.items():
            key = normalize_header_name(str(raw_key or ""))
            if not key:
                continue
            normalized_row[key] = _normalize_record_value(raw_value)

        if any(str(v).strip() != "" for v in normalized_row.values()):
            normalized_records.append(normalized_row)

    return normalized_records


def _build_parse_result(records: list[dict[str, str]]) -> CsvParseResult:
    headers_set: dict[str, None] = {}
    for row in records:
        for key in row.keys():
            headers_set.setdefault(key, None)

    headers = list(headers_set.keys())
    return CsvParseResult(
        delimiter=";",
        headers_raw=headers,
        headers_normalized=headers,
        records=records,
        row_count=len(records),
    )


class IsWorkflowRolePermission(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        role = normalize_role(getattr(user, "role", ""))
        return bool(user.is_superuser or user.is_staff or role in WRITE_ROLES)


class WorkflowBaseView(CurrentProjectRequiredMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsWorkflowRolePermission]

    def get_throttles(self):
        if not getattr(self, "throttle_scope", None):
            method = getattr(self.request, "method", "GET")
            self.throttle_scope = "workflow_read" if method in ("GET", "HEAD", "OPTIONS") else "workflow_write"
        return super().get_throttles()

    def resolve_project(self, request):
        return self.get_current_project(request)

    @staticmethod
    def actor_can_write(actor) -> bool:
        role = normalize_role(getattr(actor, "role", ""))
        return bool(is_global_admin(actor) or role in WRITE_ROLES)

    @staticmethod
    def actor_can_validate_or_reject(actor) -> bool:
        role = normalize_role(getattr(actor, "role", ""))
        return bool(is_global_admin(actor) or role in VALIDATE_REJECT_ROLES)

    @staticmethod
    def actor_can_publish(actor) -> bool:
        role = normalize_role(getattr(actor, "role", ""))
        return bool(is_global_admin(actor) or role in PUBLISH_ROLES)

    @staticmethod
    def _base_queryset_for_project(project):
        return WorkflowSubmission.objects.filter(project_id=project.project_id).select_related(
            "created_by", "submitted_by", "reviewed_by", "published_by"
        )

    def scoped_queryset_for_actor(self, queryset, actor):
        role = normalize_role(getattr(actor, "role", ""))

        if is_global_admin(actor):
            return queryset

        scope_ids = actor_project_ids(actor)
        if not scope_ids:
            return queryset.none()

        queryset = queryset.filter(project_id__in=list(scope_ids))

        if role == ROLE_PROJECT_MANAGER:
            return queryset

        if role == ROLE_MANAGER:
            actor_region = getattr(actor, "region_id", None)
            if not actor_region:
                return queryset.none()
            return queryset.filter(region_id=actor_region)

        if role == ROLE_EDITOR:
            return queryset.filter(created_by=actor)

        return queryset.none()

    def actor_can_access_submission(self, actor, submission) -> bool:
        if is_global_admin(actor):
            return True

        scope_ids = actor_project_ids(actor)
        if str(submission.project_id) not in scope_ids:
            return False

        role = normalize_role(getattr(actor, "role", ""))
        if role == ROLE_PROJECT_MANAGER:
            return True
        if role == ROLE_MANAGER:
            return bool(getattr(actor, "region_id", None) and submission.region_id == actor.region_id)
        if role == ROLE_EDITOR:
            return submission.created_by_id == actor.id
        return False

    def enforce_region_scope_on_create_or_update(self, actor, region_id: str) -> bool:
        if is_global_admin(actor):
            return True

        role = normalize_role(getattr(actor, "role", ""))
        if role == ROLE_PROJECT_MANAGER:
            return True

        actor_region_id = getattr(actor, "region_id", None)
        if role in {ROLE_EDITOR, ROLE_MANAGER}:
            return bool(actor_region_id and actor_region_id == region_id)

        return False

    @staticmethod
    def log_action(submission, actor, action: str, from_status: str | None, to_status: str, comment: str = "", metadata=None):
        WorkflowActionLog.objects.create(
            submission=submission,
            action=action,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            comment=comment or None,
            metadata=metadata or {},
        )


class WorkflowKoboFormsView(WorkflowBaseView):
    def get(self, request):
        project, error_response = self.resolve_project(request)
        if error_response is not None:
            return error_response

        actor = request.user
        if not self.actor_can_write(actor):
            return Response({"detail": "Acces refuse."}, status=status.HTTP_403_FORBIDDEN)

        forms = list_project_forms(
            getattr(settings, "KOBO_FORM_REGISTRY", {}),
            str(getattr(project, "code_fonc", "") or ""),
        )

        return Response(
            {
                "enabled": bool(getattr(settings, "KOBO_SYNC_ENABLED", False)),
                "project_code": str(getattr(project, "code_fonc", "") or "").upper(),
                "forms": [
                    {
                        "dataset_code": form.dataset_code,
                        "asset_uid": form.asset_uid,
                        "label": form.label or form.dataset_code,
                    }
                    for form in forms
                ],
                "max_sync_records": int(getattr(settings, "KOBO_SYNC_MAX_RECORDS", 500)),
                "max_payload_records": int(getattr(settings, "KOBO_SYNC_MAX_PAYLOAD_RECORDS", 200)),
            }
        )


class WorkflowKoboSyncView(WorkflowBaseView):
    def post(self, request):
        project, error_response = self.resolve_project(request)
        if error_response is not None:
            return error_response

        actor = request.user
        if not self.actor_can_write(actor):
            return Response({"detail": "Acces refuse."}, status=status.HTTP_403_FORBIDDEN)

        if not bool(getattr(settings, "KOBO_SYNC_ENABLED", False)):
            return Response(
                {"detail": "Synchronisation Kobo desactivee sur cet environnement."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        serializer = WorkflowKoboSyncSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        region_id = serializer.validated_data["region_id"]
        if not self.enforce_region_scope_on_create_or_update(actor, region_id):
            return Response({"detail": "Acces refuse: region hors perimetre."}, status=status.HTTP_403_FORBIDDEN)

        project_code = str(getattr(project, "code_fonc", "") or "").upper()
        dataset_code = serializer.validated_data["dataset_code"].strip()

        form = resolve_project_form(
            getattr(settings, "KOBO_FORM_REGISTRY", {}),
            project_code=project_code,
            dataset_code=dataset_code,
        )
        if form is None:
            return Response(
                {
                    "detail": "Aucun mapping Kobo pour ce dataset/projet.",
                    "hint": "Configurez KOBO_FORM_REGISTRY avec ce dataset_code.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        requested_limit = int(serializer.validated_data.get("limit") or 500)
        max_limit = int(getattr(settings, "KOBO_SYNC_MAX_RECORDS", 500))
        effective_limit = min(requested_limit, max_limit)

        try:
            client = KoboClient(
                base_url=str(getattr(settings, "KOBO_BASE_URL", "")),
                token=str(getattr(settings, "KOBO_API_TOKEN", "")),
                timeout_seconds=int(getattr(settings, "KOBO_HTTP_TIMEOUT", 20)),
            )
            fetched_records = client.fetch_asset_submissions(form.asset_uid, limit=effective_limit)
        except KoboSyncError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        since = serializer.validated_data.get("since")
        if since is not None:
            records, filter_stats = filter_records_since(fetched_records, since)
        else:
            records = fetched_records
            filter_stats = {"skipped_before_since": 0, "missing_submission_date": 0}

        max_payload = int(getattr(settings, "KOBO_SYNC_MAX_PAYLOAD_RECORDS", 200))
        payload_records = records[:max_payload]
        payload_truncated = len(records) > len(payload_records)

        resolved_region = serializer.context.get("resolved_region")
        title = str(serializer.validated_data.get("title") or "").strip()
        if not title:
            title = f"Kobo sync {dataset_code} ({region_id})"

        sync_summary = {
            "connector": "kobo",
            "project_code": project_code,
            "dataset_code": dataset_code,
            "asset_uid": form.asset_uid,
            "asset_label": form.label,
            "fetched_count": len(fetched_records),
            "filtered_count": len(records),
            "stored_count": len(payload_records),
            "payload_truncated": payload_truncated,
            "since": since.isoformat() if since else None,
            "requested_limit": requested_limit,
            "effective_limit": effective_limit,
            **filter_stats,
            "synced_at": timezone.now().isoformat(),
        }

        submission = WorkflowSubmission.objects.create(
            project_id=project.project_id,
            project_code=project_code,
            region_id=region_id,
            region_name=getattr(resolved_region, "nom", None),
            dataset_code=dataset_code,
            title=title,
            source_type="kobo",
            payload={
                "meta": sync_summary,
                "records": payload_records,
            },
            status=WorkflowStatus.DRAFT,
            created_by=actor,
        )

        self.log_action(
            submission=submission,
            actor=actor,
            action=WorkflowAction.CREATE,
            from_status=None,
            to_status=WorkflowStatus.DRAFT,
            metadata={
                "project_code": submission.project_code,
                "dataset_code": submission.dataset_code,
                "kobo_sync": sync_summary,
            },
        )

        return Response(
            {
                "detail": "Synchronisation Kobo terminee, brouillon cree.",
                "submission": WorkflowSubmissionSerializer(submission).data,
                "sync_summary": sync_summary,
            },
            status=status.HTTP_201_CREATED,
        )


class WorkflowSubmissionListCreateView(WorkflowBaseView):
    def get(self, request):
        project, error_response = self.resolve_project(request)
        if error_response is not None:
            return error_response

        queryset = self._base_queryset_for_project(project)
        queryset = self.scoped_queryset_for_actor(queryset, request.user)

        status_filter = str(request.query_params.get("status", "")).strip().lower()
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        dataset_code = str(request.query_params.get("dataset_code", "")).strip()
        if dataset_code:
            queryset = queryset.filter(dataset_code__iexact=dataset_code)

        search = str(request.query_params.get("search", "")).strip()
        if search:
            queryset = queryset.filter(title__icontains=search)

        mine = str(request.query_params.get("mine", "")).strip().lower()
        if mine in {"1", "true", "yes"}:
            queryset = queryset.filter(created_by=request.user)

        paginator = PageNumberPagination()
        paginator.page_size_query_param = "page_size"
        page = paginator.paginate_queryset(queryset, request, view=self)

        serializer = WorkflowSubmissionSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        project, error_response = self.resolve_project(request)
        if error_response is not None:
            return error_response

        serializer = WorkflowSubmissionCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        actor = request.user
        role = normalize_role(getattr(actor, "role", ""))
        if role not in WRITE_ROLES and not is_global_admin(actor):
            return Response({"detail": "Acces refuse."}, status=status.HTTP_403_FORBIDDEN)

        resolved_region = serializer.context.get("resolved_region")
        region_id = serializer.validated_data["region_id"]
        if not self.enforce_region_scope_on_create_or_update(actor, region_id):
            return Response({"detail": "Acces refuse: region hors perimetre."}, status=status.HTTP_403_FORBIDDEN)

        submission = WorkflowSubmission.objects.create(
            project_id=project.project_id,
            project_code=(project.code_fonc or "").upper(),
            region_id=region_id,
            region_name=getattr(resolved_region, "nom", None),
            dataset_code=serializer.validated_data["dataset_code"],
            title=serializer.validated_data["title"],
            source_type=serializer.validated_data.get("source_type"),
            payload=serializer.validated_data.get("payload") or {},
            status=WorkflowStatus.DRAFT,
            created_by=actor,
        )

        self.log_action(
            submission=submission,
            actor=actor,
            action=WorkflowAction.CREATE,
            from_status=None,
            to_status=WorkflowStatus.DRAFT,
            metadata={"project_code": submission.project_code, "dataset_code": submission.dataset_code},
        )

        return Response(WorkflowSubmissionSerializer(submission).data, status=status.HTTP_201_CREATED)


class WorkflowSubmissionDetailView(WorkflowBaseView):
    def _resolve_submission(self, request, submission_id):
        project, error_response = self.resolve_project(request)
        if error_response is not None:
            return None, None, error_response

        try:
            submission = WorkflowSubmission.objects.select_related(
                "created_by", "submitted_by", "reviewed_by", "published_by"
            ).get(submission_id=submission_id, project_id=project.project_id)
        except WorkflowSubmission.DoesNotExist:
            return None, None, Response({"detail": "Soumission introuvable."}, status=status.HTTP_404_NOT_FOUND)

        if not self.actor_can_access_submission(request.user, submission):
            return None, None, Response({"detail": "Acces refuse."}, status=status.HTTP_403_FORBIDDEN)

        return project, submission, None

    def get(self, request, submission_id):
        _, submission, error_response = self._resolve_submission(request, submission_id)
        if error_response is not None:
            return error_response

        return Response(WorkflowSubmissionSerializer(submission).data)

    def patch(self, request, submission_id):
        _, submission, error_response = self._resolve_submission(request, submission_id)
        if error_response is not None:
            return error_response

        actor = request.user
        role = normalize_role(getattr(actor, "role", ""))

        if role not in WRITE_ROLES and not is_global_admin(actor):
            return Response({"detail": "Acces refuse."}, status=status.HTTP_403_FORBIDDEN)

        if submission.created_by_id != actor.id:
            return Response(
                {"detail": "Seul le createur peut modifier cette soumission en brouillon/rejetee."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if submission.status not in {WorkflowStatus.DRAFT, WorkflowStatus.REJECTED}:
            return Response(
                {"detail": "Modification autorisee uniquement pour les statuts brouillon/rejete."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = WorkflowSubmissionUpdateSerializer(data=request.data, context={"request": request}, partial=True)
        serializer.is_valid(raise_exception=True)

        update_fields = []
        previous_status = submission.status

        if "title" in serializer.validated_data:
            submission.title = serializer.validated_data["title"]
            update_fields.append("title")

        if "payload" in serializer.validated_data:
            submission.payload = serializer.validated_data["payload"]
            update_fields.append("payload")

        if "region_id" in serializer.validated_data:
            region_id = serializer.validated_data["region_id"]
            if not self.enforce_region_scope_on_create_or_update(actor, region_id):
                return Response({"detail": "Acces refuse: region hors perimetre."}, status=status.HTTP_403_FORBIDDEN)

            resolved_region = serializer.context.get("resolved_region")
            submission.region_id = region_id
            submission.region_name = getattr(resolved_region, "nom", None)
            update_fields.extend(["region_id", "region_name"])

        if not update_fields:
            return Response({"detail": "Aucune modification fournie."}, status=status.HTTP_400_BAD_REQUEST)

        update_fields.append("updated_at")
        submission.save(update_fields=update_fields)

        self.log_action(
            submission=submission,
            actor=actor,
            action=WorkflowAction.UPDATE,
            from_status=previous_status,
            to_status=submission.status,
            metadata={"updated_fields": update_fields},
        )

        return Response(WorkflowSubmissionSerializer(submission).data)


class WorkflowSubmissionSubmitView(WorkflowSubmissionDetailView):
    def post(self, request, submission_id):
        _, submission, error_response = self._resolve_submission(request, submission_id)
        if error_response is not None:
            return error_response

        actor = request.user
        if submission.created_by_id != actor.id:
            return Response(
                {"detail": "Seul le createur peut soumettre ce brouillon."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if submission.status not in {WorkflowStatus.DRAFT, WorkflowStatus.REJECTED}:
            return Response(
                {"detail": "La soumission doit etre en brouillon ou rejetee."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        previous_status = submission.status
        submission.status = WorkflowStatus.SUBMITTED
        submission.submitted_by = actor
        submission.submitted_at = timezone.now()
        submission.rejection_reason = None
        submission.rejected_at = None

        if previous_status == WorkflowStatus.REJECTED:
            submission.version += 1
            update_fields = [
                "status",
                "submitted_by",
                "submitted_at",
                "rejection_reason",
                "rejected_at",
                "version",
                "updated_at",
            ]
        else:
            update_fields = [
                "status",
                "submitted_by",
                "submitted_at",
                "rejection_reason",
                "rejected_at",
                "updated_at",
            ]

        submission.save(update_fields=update_fields)

        self.log_action(
            submission=submission,
            actor=actor,
            action=WorkflowAction.SUBMIT,
            from_status=previous_status,
            to_status=submission.status,
        )

        return Response(
            {
                "detail": "Soumission envoyee pour validation.",
                "previous_status": previous_status,
                "current_status": submission.status,
                "submission": WorkflowSubmissionSerializer(submission).data,
            }
        )


class WorkflowSubmissionValidateView(WorkflowSubmissionDetailView):
    def post(self, request, submission_id):
        _, submission, error_response = self._resolve_submission(request, submission_id)
        if error_response is not None:
            return error_response

        actor = request.user
        role = normalize_role(getattr(actor, "role", ""))
        if role not in VALIDATE_REJECT_ROLES and not is_global_admin(actor):
            return Response({"detail": "Acces refuse."}, status=status.HTTP_403_FORBIDDEN)

        if submission.status != WorkflowStatus.SUBMITTED:
            return Response(
                {"detail": "Validation possible uniquement sur une soumission en statut soumis."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if submission.created_by_id == actor.id:
            return Response(
                {"detail": "Auto-validation interdite: un autre role de controle doit valider."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        previous_status = submission.status
        submission.status = WorkflowStatus.VALIDATED
        submission.reviewed_by = actor
        submission.validated_at = timezone.now()
        submission.rejection_reason = None
        submission.rejected_at = None
        submission.save(
            update_fields=[
                "status",
                "reviewed_by",
                "validated_at",
                "rejection_reason",
                "rejected_at",
                "updated_at",
            ]
        )

        self.log_action(
            submission=submission,
            actor=actor,
            action=WorkflowAction.VALIDATE,
            from_status=previous_status,
            to_status=submission.status,
        )

        return Response(
            {
                "detail": "Soumission validee.",
                "previous_status": previous_status,
                "current_status": submission.status,
                "submission": WorkflowSubmissionSerializer(submission).data,
            }
        )


class WorkflowSubmissionRejectView(WorkflowSubmissionDetailView):
    def post(self, request, submission_id):
        _, submission, error_response = self._resolve_submission(request, submission_id)
        if error_response is not None:
            return error_response

        actor = request.user
        role = normalize_role(getattr(actor, "role", ""))
        if role not in VALIDATE_REJECT_ROLES and not is_global_admin(actor):
            return Response({"detail": "Acces refuse."}, status=status.HTTP_403_FORBIDDEN)

        if submission.status != WorkflowStatus.SUBMITTED:
            return Response(
                {"detail": "Rejet possible uniquement sur une soumission en statut soumis."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if submission.created_by_id == actor.id:
            return Response(
                {"detail": "Auto-rejet interdit pour eviter les circuits incoherents."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = WorkflowRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        previous_status = submission.status
        reason = serializer.validated_data["reason"].strip()
        investigator_contact = str(serializer.validated_data.get("investigator_contact") or "").strip()
        notify_note = str(serializer.validated_data.get("notify_note") or "").strip()
        issues = serializer.validated_data.get("issues") or []

        payload_obj = submission.payload if isinstance(submission.payload, dict) else {"raw_payload": submission.payload}
        review_obj = payload_obj.get("review") if isinstance(payload_obj.get("review"), dict) else {}
        rejection_event = {
            "reason": reason,
            "investigator_contact": investigator_contact or None,
            "notify_note": notify_note or None,
            "issues": issues,
            "issues_count": len(issues),
            "rejected_by": getattr(actor, "username", None),
            "rejected_at": timezone.now().isoformat(),
        }
        history = review_obj.get("history")
        if not isinstance(history, list):
            history = []
        history.append(rejection_event)
        if len(history) > 20:
            history = history[-20:]
        review_obj["last_reject"] = rejection_event
        review_obj["history"] = history
        payload_obj["review"] = review_obj

        submission.status = WorkflowStatus.REJECTED
        submission.reviewed_by = actor
        submission.rejected_at = timezone.now()
        submission.rejection_reason = reason
        submission.payload = payload_obj
        submission.save(update_fields=["status", "reviewed_by", "rejected_at", "rejection_reason", "payload", "updated_at"])

        self.log_action(
            submission=submission,
            actor=actor,
            action=WorkflowAction.REJECT,
            from_status=previous_status,
            to_status=submission.status,
            comment=reason,
            metadata={
                "issues_count": len(issues),
                "investigator_contact": investigator_contact or None,
                "notify_note": notify_note or None,
                "issues": issues,
            },
        )

        return Response(
            {
                "detail": "Soumission rejetee.",
                "previous_status": previous_status,
                "current_status": submission.status,
                "review_summary": {
                    "issues_count": len(issues),
                    "investigator_contact": investigator_contact or None,
                    "notify_note": notify_note or None,
                },
                "submission": WorkflowSubmissionSerializer(submission).data,
            }
        )


class WorkflowSubmissionPublishView(WorkflowSubmissionDetailView):
    def post(self, request, submission_id):
        _, submission, error_response = self._resolve_submission(request, submission_id)
        if error_response is not None:
            return error_response

        actor = request.user
        role = normalize_role(getattr(actor, "role", ""))
        if role not in PUBLISH_ROLES and not is_global_admin(actor):
            return Response({"detail": "Acces refuse."}, status=status.HTTP_403_FORBIDDEN)

        if submission.status != WorkflowStatus.VALIDATED:
            return Response(
                {"detail": "Publication possible uniquement apres validation."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        dataset = resolve_dataset_definition(submission.dataset_code, project_code=submission.project_code)
        if dataset is None:
            return Response(
                {"detail": "dataset_code invalide pour le projet de la soumission."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        records = _extract_payload_records(submission.payload)
        if not records:
            return Response(
                {"detail": "Aucun record exploitable dans le payload (cle 'records' manquante ou vide)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        parse_result = _build_parse_result(records)
        try:
            stage_columns = get_stage_columns(dataset.stage_table)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        validation = build_validation_report(
            parse_result=parse_result,
            dataset=dataset,
            stage_columns=stage_columns,
            project_code=submission.project_code,
            region_id=submission.region_id,
        )
        if not bool(validation.get("valid")):
            return Response(
                {
                    "detail": "Publication bloquee: validation des records invalide.",
                    "validation": validation,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        import_batch = uuid.uuid4()
        try:
            stage_result = execute_import_into_stage(
                dataset=dataset,
                parse_result=parse_result,
                project_code=submission.project_code,
                region_id=submission.region_id,
                import_batch=import_batch,
                import_source=f"workflow_{submission.source_type}",
                on_duplicate="update",
            )
        except Exception as exc:
            return Response(
                {
                    "detail": "Echec de l'import workflow vers stage.",
                    "error": str(exc),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        rows_ok = int(stage_result.get("rows_ok", 0))
        rows_error = int(stage_result.get("rows_error", 0))
        if rows_ok <= 0 or rows_error > 0:
            return Response(
                {
                    "detail": "Publication bloquee: import stage partiel ou nul.",
                    "stage_result": stage_result,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            publish_result = publish_dataset_to_core(
                dataset=dataset,
                project_code=submission.project_code,
                region_id=submission.region_id,
                import_batch=import_batch,
            )
        except CorePublishError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response(
                {
                    "detail": "Echec ETL stage->core pendant la publication workflow.",
                    "error": str(exc),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        payload_obj = submission.payload if isinstance(submission.payload, dict) else {"raw_payload": submission.payload}
        workflow_publish = payload_obj.get("workflow_publish") if isinstance(payload_obj.get("workflow_publish"), dict) else {}
        publish_event = {
            "dataset_code": dataset.code,
            "import_batch": str(import_batch),
            "records_count": parse_result.row_count,
            "stage_result": stage_result,
            "publish_result": publish_result,
            "published_by": getattr(actor, "username", None),
            "published_at": timezone.now().isoformat(),
        }
        history = workflow_publish.get("history")
        if not isinstance(history, list):
            history = []
        history.append(publish_event)
        if len(history) > 20:
            history = history[-20:]
        workflow_publish["last_publish"] = publish_event
        workflow_publish["history"] = history
        payload_obj["workflow_publish"] = workflow_publish

        previous_status = submission.status
        submission.status = WorkflowStatus.PUBLISHED
        submission.published_by = actor
        submission.published_at = timezone.now()
        submission.payload = payload_obj
        submission.save(update_fields=["status", "published_by", "published_at", "payload", "updated_at"])

        self.log_action(
            submission=submission,
            actor=actor,
            action=WorkflowAction.PUBLISH,
            from_status=previous_status,
            to_status=submission.status,
            metadata={
                "dataset_code": dataset.code,
                "import_batch": str(import_batch),
                "stage_result": stage_result,
                "publish_result": publish_result,
            },
        )

        return Response(
            {
                "detail": "Soumission publiee et chargee vers stage/core.",
                "previous_status": previous_status,
                "current_status": submission.status,
                "dataset_code": dataset.code,
                "import_batch": str(import_batch),
                "stage_result": stage_result,
                "publish_result": publish_result,
                "submission": WorkflowSubmissionSerializer(submission).data,
            }
        )


class WorkflowSubmissionHistoryView(WorkflowSubmissionDetailView):
    def get(self, request, submission_id):
        _, submission, error_response = self._resolve_submission(request, submission_id)
        if error_response is not None:
            return error_response

        logs = submission.actions.select_related("actor").all()

        paginator = PageNumberPagination()
        paginator.page_size_query_param = "page_size"
        page = paginator.paginate_queryset(logs, request, view=self)

        serializer = WorkflowActionLogSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)



