from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from .csv_parser import CsvParserError, parse_csv_upload
from .etl import (
    _build_linestring_geom_sql,
    _build_polygon_geom_sql,
    StageColumn,
    build_validation_report,
    coerce_value,
    prepare_records_for_stage,
    resolve_dataset_definition,
)
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


class KoboCheckboxMappingTests(SimpleTestCase):
    @staticmethod
    def _text_column(name: str) -> StageColumn:
        return StageColumn(
            name=name,
            data_type="text",
            udt_name="text",
            is_nullable=True,
            has_default=False,
        )

    def test_prepare_records_derives_checkbox_multiselect_values(self):
        stage_columns = [
            self._text_column("id_comite"),
            self._text_column("themes_comite"),
            self._text_column("types_conflits"),
            self._text_column("type_techniciens"),
            self._text_column("photo_ouvr"),
        ]
        raw_records = [
            {
                "id_comite": "C-001",
                "themes_comite_feux": "1",
                "themes_comite_agroeco": "1",
                "themes_comite_intrants": "0",
                "types_conflits_rnr": "1",
                "type_techniciens_agricole": "1",
                "type_techniciens_environ": "0",
                "photo_ouvr_url": "https://example.test/photo.jpg",
            }
        ]

        prepared, alias_map, checkbox_map = prepare_records_for_stage(raw_records, stage_columns)

        self.assertEqual(alias_map.get("theme_comite"), "themes_comite")
        self.assertEqual(checkbox_map.get("themes_comite_feux"), ("themes_comite", "themes_comite"))
        self.assertEqual(checkbox_map.get("types_conflits_rnr"), ("types_conflits", "types_conflits"))

        row = prepared[0]
        self.assertEqual(set(str(row["themes_comite"]).split()), {"FEUX", "AGROECO"})
        self.assertEqual(row["types_conflits"], "RNR")
        self.assertEqual(row["type_techniciens"], "AGRICOLE")
        self.assertEqual(row["photo_ouvr"], "https://example.test/photo.jpg")

    def test_prepare_records_auto_generates_missing_id_comite(self):
        stage_columns = [
            self._text_column("id_comite"),
            self._text_column("uuid"),
            self._text_column("nom_comite"),
        ]
        raw_records = [
            {
                "id_comite": "",
                "uuid": "uuid:8b68a67c-8d3a-4b11-a86b-e39a493f8bc3",
                "nom_comite": "Comite A",
            },
            {
                "id_comite": "",
                "uuid": "uuid:8b68a67c-8d3a-4b11-a86b-e39a493f8bc3",
                "nom_comite": "Comite A",
            },
            {
                "id_comite": "",
                "uuid": "uuid:fc0c5a80-5e8d-4fb0-a541-7f260c710934",
                "nom_comite": "Comite B",
            },
        ]

        prepared_a, _, _ = prepare_records_for_stage(
            raw_records,
            stage_columns,
            identifier_fields=("id_comite",),
        )
        prepared_b, _, _ = prepare_records_for_stage(
            raw_records,
            stage_columns,
            identifier_fields=("id_comite",),
        )

        first_id = str(prepared_a[0]["id_comite"])
        self.assertTrue(first_id.startswith("AUTO-COMITE-"))
        self.assertEqual(first_id, prepared_a[1]["id_comite"])  # duplicate Kobo seed -> same auto-ID
        self.assertNotEqual(first_id, prepared_a[2]["id_comite"])
        self.assertEqual([row["id_comite"] for row in prepared_a], [row["id_comite"] for row in prepared_b])

    def test_prepare_records_auto_generates_missing_id_couloir(self):
        stage_columns = [
            self._text_column("id_couloir"),
            self._text_column("uuid"),
            self._text_column("nom_couloir"),
        ]
        raw_records = [
            {"id_couloir": "", "uuid": "uuid:0fce5f66-840f-4dca-9fe9-fba6319bbaf4", "nom_couloir": "A"},
            {"id_couloir": "", "uuid": "uuid:0fce5f66-840f-4dca-9fe9-fba6319bbaf4", "nom_couloir": "A"},
        ]

        prepared, _, _ = prepare_records_for_stage(
            raw_records,
            stage_columns,
            identifier_fields=("id_couloir",),
        )
        self.assertTrue(str(prepared[0]["id_couloir"]).startswith("AUTO-ID-COULOIR-"))
        self.assertEqual(prepared[0]["id_couloir"], prepared[1]["id_couloir"])

    def test_prepare_records_auto_generates_missing_code_station(self):
        stage_columns = [
            self._text_column("code_station"),
            self._text_column("uuid"),
        ]
        raw_records = [
            {"code_station": "", "uuid": "uuid:58772cff-8ea6-4eb0-8cb4-28558c7f10f3"},
            {"code_station": "", "uuid": "uuid:58772cff-8ea6-4eb0-8cb4-28558c7f10f3"},
        ]

        prepared, _, _ = prepare_records_for_stage(
            raw_records,
            stage_columns,
            identifier_fields=("code_station",),
        )
        self.assertTrue(str(prepared[0]["code_station"]).startswith("AUTO-CODE-STATION-"))
        self.assertEqual(prepared[0]["code_station"], prepared[1]["code_station"])

    def test_validation_report_marks_checkbox_and_kobo_meta_columns(self):
        dataset = SimpleNamespace(
            code="agr-comites",
            label="Comites",
            stage_table="agr_comite_raw",
            identifier_fields=("id_comite",),
        )
        stage_columns = [
            self._text_column("id_comite"),
            self._text_column("themes_comite"),
        ]
        parse_result = SimpleNamespace(
            row_count=1,
            headers_normalized=["id_comite", "themes_comite_feux", "start", "champ_inconnu"],
            records=[
                {
                    "id_comite": "C-001",
                    "themes_comite_feux": "1",
                    "start": "2026-03-05 07:19:37",
                    "champ_inconnu": "x",
                }
            ],
        )

        report = build_validation_report(
            parse_result=parse_result,
            dataset=dataset,
            stage_columns=stage_columns,
            project_code="AGRIECO",
            region_id="GN005",
            existing_identifiers=set(),
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["stats"]["columns_recognized"], 2)
        self.assertIn("champ_inconnu", report["columns"]["unknown"])
        self.assertNotIn("themes_comite_feux", report["columns"]["unknown"])

        mapping_by_header = {row["csv_header"]: row for row in report["column_mapping"]}
        self.assertEqual(mapping_by_header["themes_comite_feux"]["status"], "derived_checkbox")
        self.assertEqual(mapping_by_header["themes_comite_feux"]["stage_column"], "themes_comite")
        self.assertEqual(mapping_by_header["start"]["status"], "ignored_kobo_meta")

    def test_validation_report_counts_auto_generated_identifiers(self):
        dataset = SimpleNamespace(
            code="agr-comites",
            label="Comites",
            stage_table="agr_comite_raw",
            identifier_fields=("id_comite",),
        )
        stage_columns = [
            self._text_column("id_comite"),
            self._text_column("uuid"),
        ]
        parse_result = SimpleNamespace(
            row_count=2,
            headers_normalized=["id_comite", "uuid"],
            records=[
                {"id_comite": "", "uuid": "uuid:74bc6d2f-0fd2-49a5-a3cd-aaf3ddf49c85"},
                {"id_comite": "C-002", "uuid": "uuid:7ea558ef-c556-4285-a1cc-017110928fc9"},
            ],
        )

        report = build_validation_report(
            parse_result=parse_result,
            dataset=dataset,
            stage_columns=stage_columns,
            project_code="AGRIECO",
            region_id=None,
            existing_identifiers=set(),
        )

        self.assertEqual(report["stats"]["auto_generated_identifier_count"], 1)
        self.assertEqual(report["stats"]["missing_identifier_count"], 0)


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
    @patch("import_core.views.capture_archive_snapshots_for_publication")
    @patch("import_core.views.publish_dataset_to_core")
    @patch("import_core.views.resolve_dataset_definition")
    @patch("import_core.views.ImportBaseView.get_current_project")
    def test_publish_runs_stage_to_core_etl(
        self,
        mock_get_current_project,
        mock_resolve_dataset,
        mock_publish_to_core,
        mock_capture_archive,
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
        mock_capture_archive.return_value = {
            "executed": True,
            "status": "success",
            "project_code": "AGRIECO",
            "dataset_code": "agr-menages",
            "captured_metrics": [],
            "created": 0,
            "updated": 0,
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
        self.assertEqual(response.data["archive_capture"]["status"], "success")
        mock_capture_archive.assert_called_once()
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


class GeometryParsingTests(SimpleTestCase):
    @staticmethod
    def _text_column(name: str) -> StageColumn:
        return StageColumn(
            name=name,
            data_type="text",
            udt_name="text",
            is_nullable=True,
            has_default=False,
        )

    @staticmethod
    def _geom_column(name: str = "geom") -> StageColumn:
        return StageColumn(
            name=name,
            data_type="USER-DEFINED",
            udt_name="geometry",
            is_nullable=True,
            has_default=False,
        )

    def test_build_linestring_geom_sql_accepts_kobo_trace(self):
        sql = _build_linestring_geom_sql(
            "",
            {"trace_couloir": "10.1 -12.1 0 0;10.2 -12.2 0 0"},
        )
        self.assertIsNotNone(sql)
        expression, params = sql
        self.assertEqual(expression, "ST_GeomFromText(%s, 4326)")
        self.assertTrue(str(params[0]).startswith("LINESTRING("))

    def test_build_linestring_geom_sql_rejects_single_point(self):
        sql = _build_linestring_geom_sql(
            "",
            {"gps_point": "10.3873911 -12.0809014 754.7000122070312 4.718"},
        )
        self.assertIsNone(sql)

    def test_build_polygon_geom_sql_accepts_kobo_shape(self):
        sql = _build_polygon_geom_sql(
            "",
            {"zone_geom": "10.1 -12.1 0 0;10.2 -12.2 0 0;10.3 -12.1 0 0"},
        )
        self.assertIsNotNone(sql)
        expression, params = sql
        self.assertEqual(expression, "ST_GeomFromText(%s, 4326)")
        self.assertTrue(str(params[0]).startswith("POLYGON(("))

    def test_build_polygon_geom_sql_rejects_single_point(self):
        sql = _build_polygon_geom_sql(
            "",
            {"gps_point": "10.3873911 -12.0809014 754.7000122070312 4.718"},
        )
        self.assertIsNone(sql)

    def test_validation_report_marks_couloir_point_as_invalid_geom(self):
        dataset = SimpleNamespace(
            code="agr-couloirs",
            label="Couloirs",
            stage_table="couloir_raw",
            identifier_fields=("id_couloir",),
        )
        stage_columns = [
            self._geom_column("geom"),
            self._text_column("id_couloir"),
        ]
        parse_result = SimpleNamespace(
            row_count=1,
            headers_normalized=["id_couloir", "gps_point"],
            records=[
                {
                    "id_couloir": "C-001",
                    "gps_point": "10.3873911 -12.0809014 754.7000122070312 4.718",
                }
            ],
        )

        report = build_validation_report(
            parse_result=parse_result,
            dataset=dataset,
            stage_columns=stage_columns,
            project_code="AGRIECO",
            region_id=None,
            existing_identifiers=set(),
        )
        self.assertEqual(report["stats"]["invalid_geom_rows"], 1)

