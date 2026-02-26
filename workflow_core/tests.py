from types import SimpleNamespace

from django.test import SimpleTestCase

from .views import (
    WorkflowBaseView,
    _build_parse_result,
    _extract_payload_records,
    actor_project_ids,
    is_global_admin,
    normalize_role,
)


def make_projects(values):
    return SimpleNamespace(values_list=lambda *args, **kwargs: values)


class WorkflowHelpersTests(SimpleTestCase):
    def test_normalize_role(self):
        self.assertEqual(normalize_role(" Manager "), "manager")

    def test_is_global_admin_for_role_and_superuser(self):
        self.assertTrue(is_global_admin(SimpleNamespace(is_authenticated=True, is_superuser=True, is_staff=False, role="")))
        self.assertTrue(is_global_admin(SimpleNamespace(is_authenticated=True, is_superuser=False, is_staff=False, role="admin")))
        self.assertFalse(is_global_admin(SimpleNamespace(is_authenticated=True, is_superuser=False, is_staff=False, role="editor")))

    def test_actor_project_ids(self):
        actor = SimpleNamespace(projects=make_projects(["p1", "p2"]))
        self.assertEqual(actor_project_ids(actor), {"p1", "p2"})


class WorkflowScopeTests(SimpleTestCase):
    def setUp(self):
        self.view = WorkflowBaseView()

    def test_editor_can_only_access_own_submission(self):
        actor = SimpleNamespace(
            id=5,
            is_authenticated=True,
            is_superuser=False,
            is_staff=False,
            role="editor",
            projects=make_projects(["p1"]),
            region_id="GN005",
        )
        own_submission = SimpleNamespace(project_id="p1", region_id="GN005", created_by_id=5)
        other_submission = SimpleNamespace(project_id="p1", region_id="GN005", created_by_id=8)

        self.assertTrue(self.view.actor_can_access_submission(actor, own_submission))
        self.assertFalse(self.view.actor_can_access_submission(actor, other_submission))

    def test_manager_requires_same_region(self):
        actor = SimpleNamespace(
            id=10,
            is_authenticated=True,
            is_superuser=False,
            is_staff=False,
            role="manager",
            projects=make_projects(["p1"]),
            region_id="GN007",
        )
        in_scope = SimpleNamespace(project_id="p1", region_id="GN007", created_by_id=99)
        out_scope = SimpleNamespace(project_id="p1", region_id="GN005", created_by_id=99)

        self.assertTrue(self.view.actor_can_access_submission(actor, in_scope))
        self.assertFalse(self.view.actor_can_access_submission(actor, out_scope))

    def test_project_manager_is_project_scoped_without_region_constraint(self):
        actor = SimpleNamespace(
            id=11,
            is_authenticated=True,
            is_superuser=False,
            is_staff=False,
            role="project_manager",
            projects=make_projects(["p2"]),
            region_id=None,
        )
        in_scope = SimpleNamespace(project_id="p2", region_id="GN005", created_by_id=2)
        out_scope = SimpleNamespace(project_id="p1", region_id="GN005", created_by_id=2)

        self.assertTrue(self.view.actor_can_access_submission(actor, in_scope))
        self.assertFalse(self.view.actor_can_access_submission(actor, out_scope))

    def test_enforce_region_scope(self):
        editor = SimpleNamespace(
            is_authenticated=True,
            is_superuser=False,
            is_staff=False,
            role="editor",
            region_id="GN005",
        )
        manager = SimpleNamespace(
            is_authenticated=True,
            is_superuser=False,
            is_staff=False,
            role="manager",
            region_id="GN007",
        )
        project_manager = SimpleNamespace(
            is_authenticated=True,
            is_superuser=False,
            is_staff=False,
            role="project_manager",
            region_id=None,
        )

        self.assertTrue(self.view.enforce_region_scope_on_create_or_update(editor, "GN005"))
        self.assertFalse(self.view.enforce_region_scope_on_create_or_update(editor, "GN007"))
        self.assertTrue(self.view.enforce_region_scope_on_create_or_update(manager, "GN007"))
        self.assertFalse(self.view.enforce_region_scope_on_create_or_update(manager, "GN005"))
        self.assertTrue(self.view.enforce_region_scope_on_create_or_update(project_manager, "GN001"))


class WorkflowPayloadRecordHelpersTests(SimpleTestCase):
    def test_extract_payload_records_normalizes_keys_and_values(self):
        payload = {
            "records": [
                {
                    "ID Sortant": 7,
                    "Nom/Prenom": " Awa ",
                    "meta": {"a": 1},
                    "": "ignored",
                },
                {"   ": ""},
            ]
        }

        rows = _extract_payload_records(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id_sortant"], "7")
        self.assertEqual(rows[0]["nom_prenom"], "Awa")
        self.assertEqual(rows[0]["meta"], '{"a": 1}')

    def test_build_parse_result_aggregates_headers(self):
        rows = [
            {"id_sortant": "S-1", "nom": "Awa"},
            {"id_sortant": "S-2", "commune": "GN001"},
        ]

        result = _build_parse_result(rows)
        self.assertEqual(result.row_count, 2)
        self.assertEqual(result.delimiter, ";")
        self.assertIn("id_sortant", result.headers_normalized)
        self.assertIn("nom", result.headers_normalized)
        self.assertIn("commune", result.headers_normalized)
