from django.contrib import admin

from .models import WorkflowActionLog, WorkflowSubmission


@admin.register(WorkflowSubmission)
class WorkflowSubmissionAdmin(admin.ModelAdmin):
    list_display = (
        "submission_id",
        "project_code",
        "region_id",
        "dataset_code",
        "status",
        "version",
        "created_by",
        "updated_at",
    )
    list_filter = ("project_code", "region_id", "dataset_code", "status", "source_type")
    search_fields = ("submission_id", "project_code", "region_id", "dataset_code", "title")
    readonly_fields = (
        "submission_id",
        "created_at",
        "updated_at",
        "submitted_at",
        "validated_at",
        "rejected_at",
        "published_at",
    )


@admin.register(WorkflowActionLog)
class WorkflowActionLogAdmin(admin.ModelAdmin):
    list_display = ("action_id", "submission", "action", "from_status", "to_status", "actor", "created_at")
    list_filter = ("action", "from_status", "to_status")
    search_fields = ("submission__submission_id", "submission__project_code", "submission__dataset_code")
    readonly_fields = ("action_id", "created_at")
