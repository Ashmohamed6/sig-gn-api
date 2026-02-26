from django.urls import path

from .views import (
    ImportColumnsView,
    ImportDatasetsView,
    ImportExecuteView,
    ImportLogView,
    ImportPublishView,
    ImportRefreshViewsView,
    ImportValidateView,
)

app_name = "import_core"

urlpatterns = [
    path("columns/<str:dataset_code>/", ImportColumnsView.as_view(), name="import_columns"),
    path("datasets/", ImportDatasetsView.as_view(), name="import_datasets"),
    path("validate/", ImportValidateView.as_view(), name="import_validate"),
    path("execute/", ImportExecuteView.as_view(), name="import_execute"),
    path("publish/", ImportPublishView.as_view(), name="import_publish"),
    path("log/", ImportLogView.as_view(), name="import_log"),
    path("refresh-views/", ImportRefreshViewsView.as_view(), name="import_refresh_views"),
]
