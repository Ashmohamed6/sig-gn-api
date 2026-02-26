from django.test import SimpleTestCase

from .serializers import WorkflowRejectSerializer


class WorkflowRejectSerializerTests(SimpleTestCase):
    def test_accepts_contact_and_issues(self):
        serializer = WorkflowRejectSerializer(
            data={
                "reason": "Coordonnees incoherentes",
                "investigator_contact": "enqueteur_1",
                "notify_note": "Merci de corriger avant 18h",
                "issues": [
                    {
                        "record_ref": "r-101",
                        "field": "latitude",
                        "message": "Latitude absente",
                        "severity": "error",
                    },
                    {
                        "record_ref": "r-102",
                        "field": "parcelle_geom",
                        "message": "Polygone auto-intersecte",
                        "severity": "warning",
                    },
                ],
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(len(serializer.validated_data["issues"]), 2)

    def test_rejects_invalid_issue_shape(self):
        serializer = WorkflowRejectSerializer(
            data={
                "reason": "Invalid",
                "issues": [{"record_ref": "x", "severity": "critical"}],
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("issues", serializer.errors)
