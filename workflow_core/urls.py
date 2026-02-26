from django.urls import path

from .views import (
    WorkflowKoboFormsView,
    WorkflowKoboSyncView,
    WorkflowSubmissionDetailView,
    WorkflowSubmissionHistoryView,
    WorkflowSubmissionListCreateView,
    WorkflowSubmissionPublishView,
    WorkflowSubmissionRejectView,
    WorkflowSubmissionSubmitView,
    WorkflowSubmissionValidateView,
)

app_name = "workflow_core"

urlpatterns = [
    path("kobo/forms/", WorkflowKoboFormsView.as_view(), name="workflow_kobo_forms"),
    path("kobo/sync/", WorkflowKoboSyncView.as_view(), name="workflow_kobo_sync"),
    path("submissions/", WorkflowSubmissionListCreateView.as_view(), name="workflow_submissions_list_create"),
    path("submissions/<uuid:submission_id>/", WorkflowSubmissionDetailView.as_view(), name="workflow_submissions_detail"),
    path("submissions/<uuid:submission_id>/submit/", WorkflowSubmissionSubmitView.as_view(), name="workflow_submissions_submit"),
    path("submissions/<uuid:submission_id>/validate/", WorkflowSubmissionValidateView.as_view(), name="workflow_submissions_validate"),
    path("submissions/<uuid:submission_id>/reject/", WorkflowSubmissionRejectView.as_view(), name="workflow_submissions_reject"),
    path("submissions/<uuid:submission_id>/publish/", WorkflowSubmissionPublishView.as_view(), name="workflow_submissions_publish"),
    path("submissions/<uuid:submission_id>/history/", WorkflowSubmissionHistoryView.as_view(), name="workflow_submissions_history"),
]
