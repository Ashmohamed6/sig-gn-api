from datetime import datetime, timezone

from django.test import SimpleTestCase

from .kobo import (
    filter_records_since,
    list_project_forms,
    normalize_dataset_code,
    parse_iso_datetime,
    resolve_project_form,
)


class KoboHelpersTests(SimpleTestCase):
    def test_parse_iso_datetime_supports_zulu(self):
        parsed = parse_iso_datetime("2026-02-13T12:34:56Z")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_normalize_dataset_code(self):
        self.assertEqual(normalize_dataset_code("  AGR-MENAGES "), "agr-menages")

    def test_list_project_forms_uses_project_bucket(self):
        registry = {
            "AGRIECO": {
                "agr-menages": {"asset_uid": "a1", "label": "Menages"},
                "marches": "a2",
            },
            "FIERE": [{"dataset_code": "sortants", "asset_uid": "f1", "label": "Sortants"}],
        }

        forms = list_project_forms(registry, "AGRIECO")
        self.assertEqual([f.dataset_code for f in forms], ["agr-menages", "marches"])
        self.assertEqual(forms[0].asset_uid, "a1")

    def test_resolve_project_form(self):
        registry = {"AGRIECO": {"agr-menages": "abc123"}}
        form = resolve_project_form(registry, "agrieco", "AGR-MENAGES")
        self.assertIsNotNone(form)
        assert form is not None
        self.assertEqual(form.asset_uid, "abc123")

    def test_filter_records_since(self):
        since = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
        records = [
            {"_submission_time": "2026-01-15T12:00:00Z", "_id": 1},
            {"_submission_time": "2026-02-05T09:30:00Z", "_id": 2},
            {"_id": 3},  # date absente: conserve
        ]

        kept, stats = filter_records_since(records, since)

        self.assertEqual(len(kept), 2)
        self.assertEqual(stats["skipped_before_since"], 1)
        self.assertEqual(stats["missing_submission_date"], 1)
