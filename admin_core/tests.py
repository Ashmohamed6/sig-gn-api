from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from .serializers import AdminUserCreateSerializer
from .views import (
    AdminDashboardView,
    AdminUserResetPasswordView,
    IsUserAdminPermission,
    target_in_project_manager_scope,
    target_in_sub_admin_scope,
)


def make_projects(values):
    return SimpleNamespace(values_list=lambda *args, **kwargs: values)


class AdminSerializerHelperTests(SimpleTestCase):
    def test_split_project_identifiers_separates_uuid_and_code(self):
        valid_uuid = "cb1bf865-9b63-4022-a099-6ea48aaf283d"
        uuid_ids, code_ids = AdminUserCreateSerializer._split_project_identifiers([valid_uuid, "FIERE"])

        self.assertEqual([str(x) for x in uuid_ids], [valid_uuid])
        self.assertEqual(code_ids, ["FIERE"])

    def test_split_project_identifiers_keeps_invalid_uuid_as_code(self):
        uuid_ids, code_ids = AdminUserCreateSerializer._split_project_identifiers(["AGRIECO", "NOT-A-UUID"])

        self.assertEqual(uuid_ids, [])
        self.assertEqual(code_ids, ["AGRIECO", "NOT-A-UUID"])


class AdminPermissionUnitTests(SimpleTestCase):
    def test_permission_accepts_superuser_admin_project_manager_and_manager(self):
        permission = IsUserAdminPermission()

        request = SimpleNamespace(user=SimpleNamespace(is_authenticated=True, is_superuser=True, role="reader"))
        self.assertTrue(permission.has_permission(request, view=None))

        request = SimpleNamespace(user=SimpleNamespace(is_authenticated=True, is_superuser=False, role="admin"))
        self.assertTrue(permission.has_permission(request, view=None))

        request = SimpleNamespace(user=SimpleNamespace(is_authenticated=True, is_superuser=False, role="project_manager"))
        self.assertTrue(permission.has_permission(request, view=None))

        request = SimpleNamespace(user=SimpleNamespace(is_authenticated=True, is_superuser=False, role="manager"))
        self.assertTrue(permission.has_permission(request, view=None))

    def test_permission_rejects_non_admin_user(self):
        permission = IsUserAdminPermission()
        request = SimpleNamespace(user=SimpleNamespace(is_authenticated=True, is_superuser=False, role="reader"))
        self.assertFalse(permission.has_permission(request, view=None))


class AdminScopeHelperTests(SimpleTestCase):
    def test_target_in_sub_admin_scope_accepts_reader_editor_subset(self):
        actor = SimpleNamespace(
            is_authenticated=True,
            is_superuser=False,
            role="manager",
            region_id="GN005",
            projects=make_projects(["p1", "p2"]),
        )
        target = SimpleNamespace(
            is_superuser=False,
            role="editor",
            region_id="GN005",
            projects=make_projects(["p1"]),
        )
        self.assertTrue(target_in_sub_admin_scope(actor, target))

    def test_target_in_sub_admin_scope_rejects_outside_scope(self):
        actor = SimpleNamespace(
            is_authenticated=True,
            is_superuser=False,
            role="manager",
            region_id="GN005",
            projects=make_projects(["p1"]),
        )
        target = SimpleNamespace(
            is_superuser=False,
            role="editor",
            region_id="GN005",
            projects=make_projects(["p1", "p2"]),
        )
        self.assertFalse(target_in_sub_admin_scope(actor, target))

    def test_target_in_sub_admin_scope_rejects_admin_target(self):
        actor = SimpleNamespace(
            is_authenticated=True,
            is_superuser=False,
            role="manager",
            region_id="GN005",
            projects=make_projects(["p1"]),
        )
        target = SimpleNamespace(
            is_superuser=False,
            role="admin",
            region_id="GN005",
            projects=make_projects(["p1"]),
        )
        self.assertFalse(target_in_sub_admin_scope(actor, target))

    def test_target_in_project_manager_scope_accepts_manager_in_project_scope(self):
        actor = SimpleNamespace(
            is_authenticated=True,
            is_superuser=False,
            role="project_manager",
            projects=make_projects(["p1", "p2"]),
        )
        target = SimpleNamespace(
            is_superuser=False,
            role="manager",
            region_id="GN007",
            projects=make_projects(["p2"]),
        )
        self.assertTrue(target_in_project_manager_scope(actor, target))

    def test_target_in_project_manager_scope_rejects_outside_project_scope(self):
        actor = SimpleNamespace(
            is_authenticated=True,
            is_superuser=False,
            role="project_manager",
            projects=make_projects(["p1"]),
        )
        target = SimpleNamespace(
            is_superuser=False,
            role="manager",
            region_id="GN005",
            projects=make_projects(["p1", "p2"]),
        )
        self.assertFalse(target_in_project_manager_scope(actor, target))


class AdminResetPasswordUnitTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def test_reset_password_blocks_last_active_admin(self):
        view = AdminUserResetPasswordView()
        request = self.factory.post("/api/admin/users/1/reset-password/", {}, format="json")
        request.user = SimpleNamespace(is_authenticated=True, is_superuser=True, role="admin")

        target_user = Mock()
        target_user.is_superuser = True
        target_user.role = "admin"

        fake_user_model = Mock()
        fake_user_model.DoesNotExist = type("DoesNotExist", (Exception,), {})
        fake_user_model.objects.get.return_value = target_user
        fake_user_model.objects.filter.return_value.count.return_value = 1

        with patch("admin_core.views.UserModel", fake_user_model):
            drf_request = view.initialize_request(request)
            drf_request.user = request.user
            response = view.post(drf_request, id=1)

        self.assertEqual(response.status_code, 400)
        self.assertIn("dernier administrateur actif", response.data["detail"])

    def test_reset_password_manager_cannot_reset_admin_target(self):
        view = AdminUserResetPasswordView()
        request = self.factory.post("/api/admin/users/1/reset-password/", {}, format="json")
        request.user = SimpleNamespace(
            is_authenticated=True,
            is_superuser=False,
            role="manager",
            region_id="GN005",
            projects=make_projects(["p1"]),
        )

        target_user = Mock()
        target_user.is_superuser = False
        target_user.role = "admin"
        target_user.region_id = "GN005"
        target_user.projects = make_projects(["p1"])

        fake_user_model = Mock()
        fake_user_model.DoesNotExist = type("DoesNotExist", (Exception,), {})
        fake_user_model.objects.get.return_value = target_user

        with patch("admin_core.views.UserModel", fake_user_model):
            drf_request = view.initialize_request(request)
            drf_request.user = request.user
            response = view.post(drf_request, id=1)

        self.assertEqual(response.status_code, 403)

    def test_reset_password_returns_temp_password_and_reactivates_user(self):
        view = AdminUserResetPasswordView()
        request = self.factory.post("/api/admin/users/2/reset-password/", {}, format="json")
        request.user = SimpleNamespace(is_authenticated=True, is_superuser=True, role="admin")

        target_user = Mock()
        target_user.is_superuser = False
        target_user.role = "reader"
        target_user.is_active = False

        fake_user_model = Mock()
        fake_user_model.DoesNotExist = type("DoesNotExist", (Exception,), {})
        fake_user_model.objects.get.return_value = target_user

        with patch("admin_core.views.UserModel", fake_user_model):
            drf_request = view.initialize_request(request)
            drf_request.user = request.user
            response = view.post(drf_request, id=2)

        self.assertEqual(response.status_code, 200)
        self.assertIn("temporary_password", response.data)
        target_user.set_password.assert_called_once_with(response.data["temporary_password"])
        target_user.save.assert_called_once_with(update_fields=["password", "is_active"])


class AdminThrottleScopeTests(SimpleTestCase):
    def test_dashboard_uses_read_scope_for_get_and_write_scope_for_post(self):
        factory = APIRequestFactory()

        get_request = factory.get("/api/admin/dashboard/")
        get_view = AdminDashboardView()
        get_view.request = get_view.initialize_request(get_request)
        get_view.get_throttles()
        self.assertEqual(get_view.throttle_scope, "admin_read")

        post_request = factory.post("/api/admin/dashboard/", {}, format="json")
        post_view = AdminDashboardView()
        post_view.request = post_view.initialize_request(post_request)
        post_view.get_throttles()
        self.assertEqual(post_view.throttle_scope, "admin_write")
