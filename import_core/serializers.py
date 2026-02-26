from rest_framework import serializers


class ImportRequestSerializer(serializers.Serializer):
    dataset_code = serializers.CharField(max_length=120)
    region_id = serializers.CharField(max_length=50, required=False, allow_blank=True)
    project_code = serializers.CharField(max_length=50, required=False, allow_blank=True)
    on_duplicate = serializers.ChoiceField(
        choices=[("update", "update"), ("skip", "skip"), ("update_only", "update_only")],
        default="update",
        required=False,
    )

    def validate_dataset_code(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise serializers.ValidationError("dataset_code est obligatoire.")
        return text

    def validate_region_id(self, value: str) -> str | None:
        text = str(value or "").strip()
        return text or None

    def validate_project_code(self, value: str) -> str | None:
        text = str(value or "").strip().upper()
        return text or None


class ImportLogQuerySerializer(serializers.Serializer):
    dataset_code = serializers.CharField(max_length=120, required=False, allow_blank=True)
    status = serializers.CharField(max_length=50, required=False, allow_blank=True)
    date_from = serializers.DateTimeField(required=False)
    date_to = serializers.DateTimeField(required=False)
    region_id = serializers.CharField(max_length=50, required=False, allow_blank=True)
    project_code = serializers.CharField(max_length=50, required=False, allow_blank=True)
    all_projects = serializers.BooleanField(required=False, default=False)

