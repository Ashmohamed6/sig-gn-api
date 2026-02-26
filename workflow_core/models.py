import uuid

from django.conf import settings
from django.db import models


class WorkflowStatus(models.TextChoices):
    DRAFT = "draft", "Brouillon"
    SUBMITTED = "submitted", "Soumis"
    VALIDATED = "validated", "Valide"
    REJECTED = "rejected", "Rejete"
    PUBLISHED = "published", "Publie"


class WorkflowSourceType(models.TextChoices):
    KOBO = "kobo", "Kobo"
    CSV = "csv", "CSV"
    MANUAL = "manual", "Manuel"
    OTHER = "other", "Autre"


class WorkflowAction(models.TextChoices):
    CREATE = "create", "Creation"
    UPDATE = "update", "Mise a jour"
    SUBMIT = "submit", "Soumission"
    VALIDATE = "validate", "Validation"
    REJECT = "reject", "Rejet"
    PUBLISH = "publish", "Publication"


class WorkflowSubmission(models.Model):
    submission_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    project_id = models.UUIDField(db_index=True)
    project_code = models.CharField(max_length=50, db_index=True)
    region_id = models.CharField(max_length=50, db_index=True)
    region_name = models.CharField(max_length=255, blank=True, null=True)

    dataset_code = models.CharField(max_length=120, db_index=True)
    title = models.CharField(max_length=255)
    source_type = models.CharField(
        max_length=20,
        choices=WorkflowSourceType.choices,
        default=WorkflowSourceType.MANUAL,
    )
    payload = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=20,
        choices=WorkflowStatus.choices,
        default=WorkflowStatus.DRAFT,
        db_index=True,
    )
    version = models.PositiveIntegerField(default=1)

    rejection_reason = models.TextField(blank=True, null=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="workflow_created_submissions",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="workflow_submitted_submissions",
        blank=True,
        null=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="workflow_reviewed_submissions",
        blank=True,
        null=True,
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="workflow_published_submissions",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(blank=True, null=True)
    validated_at = models.DateTimeField(blank=True, null=True)
    rejected_at = models.DateTimeField(blank=True, null=True)
    published_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "workflow_submission"
        indexes = [
            models.Index(fields=["project_id", "region_id", "status"], name="wf_sub_scope_idx"),
            models.Index(fields=["project_code", "dataset_code"], name="wf_sub_project_ds_idx"),
        ]
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.project_code}:{self.dataset_code}:{self.submission_id}"


class WorkflowActionLog(models.Model):
    action_id = models.BigAutoField(primary_key=True)
    submission = models.ForeignKey(
        WorkflowSubmission,
        on_delete=models.CASCADE,
        related_name="actions",
    )
    action = models.CharField(max_length=20, choices=WorkflowAction.choices)

    from_status = models.CharField(max_length=20, blank=True, null=True)
    to_status = models.CharField(max_length=20)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="workflow_actions",
        blank=True,
        null=True,
    )
    comment = models.TextField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "workflow_action_log"
        indexes = [
            models.Index(fields=["submission", "created_at"], name="wf_log_submission_idx"),
        ]
        ordering = ["-created_at", "-action_id"]

    def __str__(self) -> str:
        return f"{self.action}:{self.submission_id}"
