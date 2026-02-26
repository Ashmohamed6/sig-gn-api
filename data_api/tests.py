from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth.models import AnonymousUser
from django.http import QueryDict
from django.test import SimpleTestCase
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIRequestFactory

from .mixins import CurrentProjectRequiredMixin
from .views import (
    build_access_scope_for_project,
    DataEntityDeleteView,
    DataEntityBulkDeleteView,
    DataEntityPurgeView,
)
from .views_geojson_collected import BaseGeoJSONView as CollectedGeoBaseView
from .views_geojson_ref import BaseRefGeoJSONView
from .views_referentiels_admin import ReferentielAdminBaseView, REF_LAYER_REGISTRY


class CurrentProjectThrottleScopeTests(SimpleTestCase):
    def test_dynamic_scope_for_stats_and_carto(self):
        mixin = CurrentProjectRequiredMixin()

        mixin.request = SimpleNamespace(path="/api/data/agr-menages/stats/")
        self.assertEqual(mixin._resolve_dynamic_throttle_scope(), "stats")

        mixin.request = SimpleNamespace(path="/api/data/carto/admin-region/")
        self.assertEqual(mixin._resolve_dynamic_throttle_scope(), "geojson")

        mixin.request = SimpleNamespace(path="/api/data/entreprises/")
        self.assertIsNone(mixin._resolve_dynamic_throttle_scope())


class CurrentProjectHeaderValidationTests(SimpleTestCase):
    def test_missing_project_header_is_rejected(self):
        request = APIRequestFactory().get("/api/data/agr-menages/stats/")
        request.user = AnonymousUser()

        _, error_response = CurrentProjectRequiredMixin().get_current_project(request)

        self.assertIsNotNone(error_response)
        self.assertEqual(error_response.status_code, 400)
        self.assertIn("X-Project-Code", error_response.data["detail"])

    def test_invalid_project_header_format_is_rejected(self):
        request = APIRequestFactory().get(
            "/api/data/agr-menages/stats/",
            HTTP_X_PROJECT_CODE="AGRIECO !!!",
        )
        request.user = AnonymousUser()

        _, error_response = CurrentProjectRequiredMixin().get_current_project(request)

        self.assertIsNotNone(error_response)
        self.assertEqual(error_response.status_code, 400)
        self.assertIn("Format invalide", error_response.data["detail"])

    def test_non_admin_user_without_project_access_is_rejected(self):
        class DummyMixin(CurrentProjectRequiredMixin):
            def _get_user_projects(self, user):
                denied_projects = Mock()
                denied_projects.filter.return_value.exists.return_value = False
                return denied_projects

        class FakeRefProject:
            class DoesNotExist(Exception):
                pass

        fake_project = SimpleNamespace(pk=123, code_fonc="AGRIECO", actif=True)

        def fake_get(**kwargs):
            if kwargs.get("code_fonc__iexact") == "AGRIECO" and kwargs.get("actif") is True:
                return fake_project
            raise FakeRefProject.DoesNotExist()

        FakeRefProject.objects = Mock()
        FakeRefProject.objects.get.side_effect = fake_get

        request = APIRequestFactory().get("/api/data/agr-menages/stats/", HTTP_X_PROJECT_CODE="AGRIECO")
        request.user = SimpleNamespace(is_authenticated=True, is_staff=False, is_superuser=False)

        with patch("data_api.mixins.RefProject", FakeRefProject):
            _, error_response = DummyMixin().get_current_project(request)

        self.assertIsNotNone(error_response)
        self.assertEqual(error_response.status_code, 403)
        self.assertIn("Acces refuse", error_response.data["detail"])

    def test_role_admin_bypasses_project_membership_check(self):
        class DummyMixin(CurrentProjectRequiredMixin):
            def _get_user_projects(self, user):
                return None

        class FakeRefProject:
            class DoesNotExist(Exception):
                pass

        fake_project = SimpleNamespace(pk=123, code_fonc="AGRIECO", actif=True)

        def fake_get(**kwargs):
            if kwargs.get("code_fonc__iexact") == "AGRIECO" and kwargs.get("actif") is True:
                return fake_project
            raise FakeRefProject.DoesNotExist()

        FakeRefProject.objects = Mock()
        FakeRefProject.objects.get.side_effect = fake_get

        request = APIRequestFactory().get("/api/data/agr-menages/stats/", HTTP_X_PROJECT_CODE="AGRIECO")
        request.user = SimpleNamespace(
            is_authenticated=True,
            is_staff=False,
            is_superuser=False,
            role="admin",
        )

        with patch("data_api.mixins.RefProject", FakeRefProject):
            project, error_response = DummyMixin().get_current_project(request)

        self.assertIsNone(error_response)
        self.assertEqual(project.code_fonc, "AGRIECO")

    def test_role_project_manager_still_requires_project_membership(self):
        class DummyMixin(CurrentProjectRequiredMixin):
            def _get_user_projects(self, user):
                denied_projects = Mock()
                denied_projects.filter.return_value.exists.return_value = False
                return denied_projects

        class FakeRefProject:
            class DoesNotExist(Exception):
                pass

        fake_project = SimpleNamespace(pk=123, code_fonc="AGRIECO", actif=True)

        def fake_get(**kwargs):
            if kwargs.get("code_fonc__iexact") == "AGRIECO" and kwargs.get("actif") is True:
                return fake_project
            raise FakeRefProject.DoesNotExist()

        FakeRefProject.objects = Mock()
        FakeRefProject.objects.get.side_effect = fake_get

        request = APIRequestFactory().get("/api/data/agr-menages/stats/", HTTP_X_PROJECT_CODE="AGRIECO")
        request.user = SimpleNamespace(
            is_authenticated=True,
            is_staff=False,
            is_superuser=False,
            role="project_manager",
        )

        with patch("data_api.mixins.RefProject", FakeRefProject):
            _, error_response = DummyMixin().get_current_project(request)

        self.assertIsNotNone(error_response)
        self.assertEqual(error_response.status_code, 403)
        self.assertIn("Acces refuse", error_response.data["detail"])


class ProjectRegionScopeTests(SimpleTestCase):
    def test_non_admin_scope_enforces_region_filter(self):
        request = SimpleNamespace(
            user=SimpleNamespace(
                is_authenticated=True,
                is_staff=False,
                is_superuser=False,
                role="reader",
                region_id="GN005",
            ),
            query_params={},
        )

        where_clauses, params = build_access_scope_for_project(request, "FIERE")

        self.assertEqual(where_clauses, ["project_code = %s", "id_region = ANY(%s)"])
        self.assertEqual(params, ["FIERE", ["GN005"]])

    def test_non_admin_rejected_when_requesting_other_region(self):
        request = SimpleNamespace(
            user=SimpleNamespace(
                is_authenticated=True,
                is_staff=False,
                is_superuser=False,
                role="reader",
                region_id="GN005",
            ),
            query_params={"region_id": "GN006"},
        )

        with self.assertRaises(PermissionDenied):
            build_access_scope_for_project(request, "FIERE")

    def test_role_admin_has_global_scope(self):
        request = SimpleNamespace(
            user=SimpleNamespace(
                is_authenticated=True,
                is_staff=False,
                is_superuser=False,
                role="admin",
                region_id="GN005",
            ),
            query_params={},
        )

        where_clauses, params = build_access_scope_for_project(request, "AGRIECO")

        self.assertEqual(where_clauses, ["project_code = %s"])
        self.assertEqual(params, ["AGRIECO"])

    def test_role_project_manager_has_project_wide_region_scope(self):
        request = SimpleNamespace(
            user=SimpleNamespace(
                is_authenticated=True,
                is_staff=False,
                is_superuser=False,
                role="project_manager",
                region_id="GN005",
            ),
            query_params={},
        )

        where_clauses, params = build_access_scope_for_project(request, "FIERE")

        self.assertEqual(where_clauses, ["project_code = %s"])
        self.assertEqual(params, ["FIERE"])


class DataEntityMutationPermissionsTests(SimpleTestCase):
    def test_delete_allowed_for_manager(self):
        view = DataEntityDeleteView()
        user = SimpleNamespace(
            is_authenticated=True,
            is_staff=False,
            is_superuser=False,
            role="manager",
        )
        self.assertTrue(view._can_delete(user))

    def test_delete_refused_for_reader(self):
        view = DataEntityDeleteView()
        user = SimpleNamespace(
            is_authenticated=True,
            is_staff=False,
            is_superuser=False,
            role="reader",
        )
        self.assertFalse(view._can_delete(user))

    def test_purge_allowed_for_project_manager(self):
        view = DataEntityPurgeView()
        user = SimpleNamespace(
            is_authenticated=True,
            is_staff=False,
            is_superuser=False,
            role="project_manager",
        )
        self.assertTrue(view._can_purge(user))

    def test_purge_refused_for_manager(self):
        view = DataEntityPurgeView()
        user = SimpleNamespace(
            is_authenticated=True,
            is_staff=False,
            is_superuser=False,
            role="manager",
        )
        self.assertFalse(view._can_purge(user))

    def test_bulk_ids_deduplicated_and_trimmed(self):
        view = DataEntityBulkDeleteView()
        ids, error = view._normalize_ids([" a ", "a", "", "b", None, " b "])
        self.assertIsNone(error)
        self.assertEqual(ids, ["a", "b"])

    def test_bulk_ids_required(self):
        view = DataEntityBulkDeleteView()
        ids, error = view._normalize_ids([])
        self.assertIsNone(ids)
        self.assertIsNotNone(error)
        self.assertEqual(error.status_code, 400)


class CartographyRegionScopeTests(SimpleTestCase):
    def test_collected_geo_admin_region_is_optional(self):
        view = CollectedGeoBaseView()
        admin_user = SimpleNamespace(
            is_authenticated=True,
            is_staff=False,
            is_superuser=False,
            role="admin",
            region_id="GN005",
        )

        no_filter_request = SimpleNamespace(
            user=admin_user,
            current_region_id=None,
            query_params={},
        )
        self.assertEqual(view.get_current_region(no_filter_request), "")

        filtered_request = SimpleNamespace(
            user=admin_user,
            current_region_id=None,
            query_params={"region": "GN004"},
        )
        self.assertEqual(view.get_current_region(filtered_request), "GN004")

    def test_collected_geo_project_manager_region_is_optional(self):
        view = CollectedGeoBaseView()
        project_manager_user = SimpleNamespace(
            is_authenticated=True,
            is_staff=False,
            is_superuser=False,
            role="project_manager",
            region_id="GN005",
        )

        no_filter_request = SimpleNamespace(
            user=project_manager_user,
            current_region_id=None,
            query_params={},
        )
        self.assertEqual(view.get_current_region(no_filter_request), "")

        filtered_request = SimpleNamespace(
            user=project_manager_user,
            current_region_id=None,
            query_params={"region": "GN007"},
        )
        self.assertEqual(view.get_current_region(filtered_request), "GN007")

    def test_collected_geo_non_admin_region_is_forced(self):
        view = CollectedGeoBaseView()
        user = SimpleNamespace(
            is_authenticated=True,
            is_staff=False,
            is_superuser=False,
            role="reader",
            region_id="GN005",
        )

        request = SimpleNamespace(
            user=user,
            current_region_id="GN005",
            query_params={"region": "GN004"},
        )
        self.assertEqual(view.get_current_region(request), "GN005")

    def test_ref_geo_non_admin_without_region_is_rejected(self):
        view = BaseRefGeoJSONView()
        user = SimpleNamespace(
            is_authenticated=True,
            is_staff=False,
            is_superuser=False,
            role="reader",
            region_id=None,
        )

        request = SimpleNamespace(
            user=user,
            current_region_id=None,
            query_params={},
        )

        with self.assertRaises(PermissionDenied):
            view.get_region(request)


class RefGeoPaginationTests(SimpleTestCase):
    def test_defaults_enable_pagination(self):
        view = BaseRefGeoJSONView()
        request = SimpleNamespace(query_params=QueryDict(""))

        enabled, page, page_size = view._get_pagination_params(request)

        self.assertTrue(enabled)
        self.assertEqual(page, 1)
        self.assertEqual(page_size, view.DEFAULT_PAGE_SIZE)

    def test_page_size_is_clamped_to_max(self):
        view = BaseRefGeoJSONView()
        request = SimpleNamespace(query_params=QueryDict("page=2&page_size=999999&paginate=1"))

        enabled, page, page_size = view._get_pagination_params(request)

        self.assertTrue(enabled)
        self.assertEqual(page, 2)
        self.assertEqual(page_size, view.MAX_PAGE_SIZE)

    def test_paginate_off_disables_pagination(self):
        view = BaseRefGeoJSONView()
        request = SimpleNamespace(query_params=QueryDict("paginate=0&page=5&page_size=1000"))

        enabled, page, page_size = view._get_pagination_params(request)

        self.assertFalse(enabled)
        self.assertEqual(page, 1)
        self.assertEqual(page_size, view.DEFAULT_PAGE_SIZE)

    def test_build_page_url_preserves_filters(self):
        view = BaseRefGeoJSONView()
        request = SimpleNamespace(
            query_params=QueryDict("region=GN005&type=school"),
            path="/api/data/carto/equipements/",
            build_absolute_uri=lambda path: f"http://testserver{path}",
        )

        url = view._build_page_url(request, page=3, page_size=1500)

        self.assertIn("http://testserver/api/data/carto/equipements/?", url)
        self.assertIn("region=GN005", url)
        self.assertIn("type=school", url)
        self.assertIn("paginate=1", url)
        self.assertIn("page=3", url)
        self.assertIn("page_size=1500", url)


class ReferentielAdminPermissionsTests(SimpleTestCase):
    def _make_request(self, role: str, *, region_id: str | None = "GN005"):
        return SimpleNamespace(
            user=SimpleNamespace(
                is_authenticated=True,
                is_staff=False,
                is_superuser=False,
                role=role,
                region_id=region_id,
            ),
            current_region_id=region_id,
        )

    def test_reader_cannot_manage_referentiels(self):
        view = ReferentielAdminBaseView()
        request = self._make_request("reader")
        with self.assertRaises(PermissionDenied):
            view._assert_can_manage_referentiels(request)

    def test_manager_can_manage_referentiels(self):
        view = ReferentielAdminBaseView()
        request = self._make_request("manager")
        # Doit passer sans exception.
        view._assert_can_manage_referentiels(request)

    def test_manager_scope_check_accepts_unrestricted_layer(self):
        view = ReferentielAdminBaseView()
        request = self._make_request("manager")
        layer = REF_LAYER_REGISTRY["hydrographie"]
        view._assert_manager_layer_scope_supported(request, layer)

    def test_normalize_values_rejects_unknown_columns(self):
        view = ReferentielAdminBaseView()
        layer = REF_LAYER_REGISTRY["admin-region"]

        with patch.object(view, "_column_map", return_value={"id_region": {"name": "id_region"}}):
            with self.assertRaises(ValidationError):
                view._normalize_values(layer, {"unknown_col": "x"}, for_update=False)
