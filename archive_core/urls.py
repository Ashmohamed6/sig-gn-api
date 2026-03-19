from django.urls import path

from .views import (
    ArchiveCaptureView,
    ArchiveComparativeView,
    ArchiveFormEntitiesView,
    ArchiveFormListView,
    ArchiveFormPreviewView,
    ArchiveMetricDefinitionListView,
    ArchiveSeriesView,
    ArchiveSnapshotBulkUpsertView,
    CompareEntityView,
    CompareGlobalView,
    DirectEntitiesView,
)

urlpatterns = [
    path("metrics/", ArchiveMetricDefinitionListView.as_view(), name="archive-metrics"),
    path("forms/", ArchiveFormListView.as_view(), name="archive-forms"),
    path("forms/<str:dataset_code>/entities/", ArchiveFormEntitiesView.as_view(), name="archive-form-entities"),
    path("forms/<str:dataset_code>/preview/", ArchiveFormPreviewView.as_view(), name="archive-form-preview"),
    path("forms/<str:dataset_code>/entities-direct/", DirectEntitiesView.as_view(), name="archive-entities-direct"),
    path("forms/<str:dataset_code>/compare-entity/", CompareEntityView.as_view(), name="archive-compare-entity"),
    path("forms/<str:dataset_code>/compare-global/", CompareGlobalView.as_view(), name="archive-compare-global"),
    path("capture/", ArchiveCaptureView.as_view(), name="archive-capture"),
    path("snapshots/upsert/", ArchiveSnapshotBulkUpsertView.as_view(), name="archive-snapshots-upsert"),
    path("comparative/", ArchiveComparativeView.as_view(), name="archive-comparative"),
    path("series/", ArchiveSeriesView.as_view(), name="archive-series"),
]