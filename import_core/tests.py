from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from .csv_parser import CsvParserError, parse_csv_upload
from .etl import StageColumn, coerce_value, resolve_dataset_definition
from .permissions import can_use_import_pipeline
from .views import ImportExecuteView, ImportValidateView
from .views import ImportPublishView, ImportRefreshViewsView


class _FakeProjects:
    def __init__(self, values):
        self._values = values

    def values_list(self, *args, **kwargs):
        return self._values


def make_user(*, role: str, project_ids: list[str], region_id: str | None = None):
    return SimpleNamespace(
        id=123,
        pk=123,
        username="qa_user",
        is_authenticated=True,
        is_superuser=False,
        is_staff=False,
        role=role,
        region_id=region_id,
        projects=_FakeProjects(project_ids),
    )


class ImportPermissionTests(SimpleTestCase):
    def test_can_use_import_pipeline_for_expected_roles(self):
        self.assertTrue(can_use_import_pipeline(make_user(role="manager", project_ids=["p1"], region_id="GN005")))
        self.assertTrue(can_use_import_pipeline(make_user(role="project_manager", project_ids=["p1"])))
        self.assertTrue(can_use_import_pipeline(make_user(role="admin", project_ids=["p1"])))
        self.assertFalse(can_use_import_pipeline(make_user(role="editor", project_ids=["p1"], region_id="GN005")))


class CsvParserTests(SimpleTestCase):
    def test_parse_csv_upload_semicolon_utf8(self):
        payload = "id_menage;nom_chef_menage\nM001;Alpha\n".encode("utf-8")
        file_obj = SimpleUploadedFile("menages.csv", payload, content_type="text/csv")

        result = parse_csv_upload(file_obj)

        self.assertEqual(result.row_count, 1)
        self.assertEqual(result.headers_normalized, ["id_menage", "nom_chef_menage"])

    def test_parse_csv_upload_rejects_wrong_separator(self):
        payload = "id_menage,nom_chef_menage\nM001,Alpha\n".encode("utf-8")
        file_obj = SimpleUploadedFile("menages.csv", payload, content_type="text/csv")

        with self.assertRaises(CsvParserError):
            parse_csv_upload(file_obj)


class DatasetResolutionTests(SimpleTestCase):
    def test_dataset_alias_resolves(self):
        resolved = resolve_dataset_definition("menages-sensibilisation", project_code="AGRIECO")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.code, "agr-menages")

    def test_dataset_scope_rejects_wrong_project(self):
        resolved = resolve_dataset_definition("agr-menages", project_code="FIERE")
        self.assertIsNone(resolved)


class CoerceValueTests(SimpleTestCase):
    def test_zero_date_is_treated_as_null(self):
        col = StageColumn(
            name="date_debut",
            data_type="date",
            udt_name="date",
            is_nullable=True,
            has_default=False,
        )
        self.assertIsNone(coerce_value("0", col))

    def test_zero_timestamp_is_treated_as_null(self):
        col = StageColumn(
            name="submitted_at",
            data_type="timestamp with time zone",
            udt_name="timestamptz",
            is_nullable=True,
            has_default=False,
        )
        self.assertIsNone(coerce_value("0", col))

    def test_zero_int_is_not_treated_as_null(self):
        col = StageColumn(
            name="count",
            data_type="integer",
            udt_name="int4",
            is_nullable=True,
            has_default=False,
        )
        self.assertEqual(coerce_value("0", col), 0)


class ImportViewsTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.project = SimpleNamespace(project_id="p1", code_fonc="AGRIECO", libelle_public="AGRIECO")
        self.manager = make_user(role="manager", project_ids=["p1"], region_id="GN005")

    @patch("import_core.views.ImportBaseView.get_current_project")
    def test_validate_requires_file(self, mock_get_current_project):
        mock_get_current_project.return_value = (self.project, None)
        request = self.factory.post(
            "/api/import/validate/",
            data={"dataset_code": "agr-menages"},
            format="multipart",
            HTTP_X_PROJECT_CODE="AGRIECO",
        )
        force_authenticate(request, user=self.manager)

        response = ImportValidateView.as_view()(request)

        self.assertEqual(response.status_code, 400)
        self.assertIn("file", str(response.data).lower())

    @patch("import_core.views.build_validation_report")
    @patch("import_core.views.find_potential_existing_identifiers")
    @patch("import_core.views.get_stage_columns")
    @patch("import_core.views.resolve_dataset_definition")
    @patch("import_core.views.parse_csv_upload")
    @patch("import_core.views.ImportBaseView.get_current_project")
    def test_validate_returns_validation_report(
        self,
        mock_get_current_project,
        mock_parse_csv_upload,
        mock_resolve_dataset,
        mock_get_stage_columns,
        mock_find_existing,
        mock_build_validation_report,
    ):
        mock_get_current_project.return_value = (self.project, None)
        mock_parse_csv_upload.return_value = SimpleNamespace(
            row_count=1,
            records=[{"id_menage": "M001"}],
            headers_normalized=["id_menage"],
        )
        mock_resolve_dataset.return_value = SimpleNamespace(
            code="agr-menages",
            label="Menages sensibilisation",
            stage_table="agr_menage_raw",
        )
        mock_get_stage_columns.return_value = [SimpleNamespace(name="id_menage")]
        mock_find_existing.return_value = set()
        mock_build_validation_report.return_value = {
            "valid": True,
            "stats": {"rows_total": 1},
            "columns": {"expected": ["id_menage"], "recognized": ["id_menage"], "unknown": []},
            "warnings": [],
            "errors": [],
        }

        payload = "id_menage;nom_chef_menage\nM001;Alpha\n".encode("utf-8")
        file_obj = SimpleUploadedFile("menages.csv", payload, content_type="text/csv")
        request = self.factory.post(
            "/api/import/validate/",
            data={"dataset_code": "agr-menages", "file": file_obj},
            format="multipart",
            HTTP_X_PROJECT_CODE="AGRIECO",
        )
        force_authenticate(request, user=self.manager)

        response = ImportValidateView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["valid"])
        self.assertIn("preview", response.data)

    @patch("import_core.views.finalize_import_log_entry")
    @patch("import_core.views.create_import_log_entry")
    @patch("import_core.views.execute_import_into_stage")
    @patch("import_core.views.build_validation_report")
    @patch("import_core.views.get_stage_columns")
    @patch("import_core.views.resolve_dataset_definition")
    @patch("import_core.views.parse_csv_upload")
    @patch("import_core.views.ImportBaseView.get_current_project")
    def test_execute_runs_etl_and_updates_log(
        self,
        mock_get_current_project,
        mock_parse_csv_upload,
        mock_resolve_dataset,
        mock_get_stage_columns,
        mock_build_validation_report,
        mock_execute_import,
        mock_create_log,
        mock_finalize_log,
    ):
        mock_get_current_project.return_value = (self.project, None)
        mock_parse_csv_upload.return_value = SimpleNamespace(
            row_count=2,
            records=[{"id_menage": "M001"}, {"id_menage": "M002"}],
            headers_normalized=["id_menage"],
        )
        mock_resolve_dataset.return_value = SimpleNamespace(
            code="agr-menages",
            label="Menages sensibilisation",
            stage_table="agr_menage_raw",
        )
        mock_get_stage_columns.return_value = [SimpleNamespace(name="id_menage")]
        mock_build_validation_report.return_value = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "stats": {"rows_total": 2},
            "columns": {"expected": ["id_menage"], "recognized": ["id_menage"], "unknown": []},
        }
        mock_execute_import.return_value = {
            "rows_total": 2,
            "rows_ok": 2,
            "rows_error": 0,
            "errors": [],
            "stage_table": "stage.agr_menage_raw",
        }

        payload = "id_menage;nom_chef_menage\nM001;Alpha\nM002;Beta\n".encode("utf-8")
        file_obj = SimpleUploadedFile("menages.csv", payload, content_type="text/csv")
        request = self.factory.post(
            "/api/import/execute/",
            data={"dataset_code": "agr-menages", "file": file_obj},
            format="multipart",
            HTTP_X_PROJECT_CODE="AGRIECO",
        )
        force_authenticate(request, user=self.manager)

        response = ImportExecuteView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "success")
        mock_create_log.assert_called_once()
        mock_finalize_log.assert_called_once()

    @patch("import_core.views.finalize_etl_run_entry")
    @patch("import_core.views.create_etl_run_entry")
    @patch("import_core.views.publish_dataset_to_core")
    @patch("import_core.views.resolve_dataset_definition")
    @patch("import_core.views.ImportBaseView.get_current_project")
    def test_publish_runs_stage_to_core_etl(
        self,
        mock_get_current_project,
        mock_resolve_dataset,
        mock_publish_to_core,
        mock_create_run,
        mock_finalize_run,
    ):
        mock_get_current_project.return_value = (self.project, None)
        mock_resolve_dataset.return_value = SimpleNamespace(
            code="agr-menages",
            label="Menages sensibilisation",
            stage_table="agr_menage_raw",
        )
        mock_publish_to_core.return_value = {
            "stage_count": 2,
            "core_tables": [{"table": "core.agr_menage", "affected_rows": 2}],
        }

        request = self.factory.post(
            "/api/import/publish/",
            data={"dataset_code": "agr-menages"},
            format="json",
            HTTP_X_PROJECT_CODE="AGRIECO",
        )
        force_authenticate(request, user=self.manager)

        response = ImportPublishView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["dataset_code"], "agr-menages")
        self.assertEqual(response.data["stage_count"], 2)
        mock_create_run.assert_called_once()
        mock_finalize_run.assert_called_once()


class ImportRefreshViewsTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.project = SimpleNamespace(project_id="p1", code_fonc="AGRIECO", libelle_public="AGRIECO")
        self.admin = make_user(role="admin", project_ids=["p1"])
        self.manager = make_user(role="manager", project_ids=["p1"], region_id="GN005")

    def test_refresh_views_returns_200_when_no_matviews(self):
        request = self.factory.post(
            "/api/import/refresh-views/",
            data={},
            format="json",
            HTTP_X_PROJECT_CODE="AGRIECO",
        )
        force_authenticate(request, user=self.admin)

        with (
            patch("import_core.views.ImportBaseView.get_current_project") as mock_get_current_project,
            patch("import_core.views.ImportRefreshViewsView._list_target_matviews") as mock_list_matviews,
            patch("import_core.views.create_etl_run_entry") as mock_create_run,
            patch("import_core.views.finalize_etl_run_entry") as mock_finalize_run,
        ):
            mock_get_current_project.return_value = (self.project, None)
            mock_list_matviews.return_value = []

            response = ImportRefreshViewsView.as_view()(request)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["status"], "success")
            self.assertEqual(response.data["refreshed_count"], 0)
            self.assertEqual(response.data["failed_count"], 0)
            mock_create_run.assert_called_once()
            mock_finalize_run.assert_called_once()

    def test_refresh_views_is_forbidden_for_manager(self):
        request = self.factory.post(
            "/api/import/refresh-views/",
            data={},
            format="json",
            HTTP_X_PROJECT_CODE="AGRIECO",
        )
        force_authenticate(request, user=self.manager)

        response = ImportRefreshViewsView.as_view()(request)

        self.assertEqual(response.status_code, 403)
