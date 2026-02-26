from rest_framework import serializers

from accounts.models import RefRegion

from .models import WorkflowActionLog, WorkflowSourceType, WorkflowStatus, WorkflowSubmission


class WorkflowSubmissionSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    source_type_display = serializers.CharField(source="get_source_type_display", read_only=True)

    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    submitted_by_username = serializers.CharField(source="submitted_by.username", read_only=True)
    reviewed_by_username = serializers.CharField(source="reviewed_by.username", read_only=True)
    published_by_username = serializers.CharField(source="published_by.username", read_only=True)

    class Meta:
        model = WorkflowSubmission
        fields = [
            "submission_id",
            "project_id",
            "project_code",
            "region_id",
            "region_name",
            "dataset_code",
            "title",
            "source_type",
            "source_type_display",
            "payload",
            "status",
            "status_display",
            "version",
            "rejection_reason",
            "created_by",
            "created_by_username",
            "submitted_by",
            "submitted_by_username",
            "reviewed_by",
            "reviewed_by_username",
            "published_by",
            "published_by_username",
            "created_at",
            "updated_at",
            "submitted_at",
            "validated_at",
            "rejected_at",
            "published_at",
        ]
        read_only_fields = [
            "submission_id",
            "project_id",
            "project_code",
            "region_name",
            "status",
            "status_display",
            "version",
            "rejection_reason",
            "created_by",
            "created_by_username",
            "submitted_by",
            "submitted_by_username",
            "reviewed_by",
            "reviewed_by_username",
            "published_by",
            "published_by_username",
            "created_at",
            "updated_at",
            "submitted_at",
            "validated_at",
            "rejected_at",
            "published_at",
        ]


class WorkflowSubmissionCreateSerializer(serializers.Serializer):
    dataset_code = serializers.CharField(max_length=120)
    title = serializers.CharField(max_length=255)
    source_type = serializers.ChoiceField(
        choices=WorkflowSourceType.choices,
        default=WorkflowSourceType.MANUAL,
    )
    region_id = serializers.CharField(max_length=50)
    payload = serializers.JSONField(required=False, default=dict)

    def validate_region_id(self, value):
        value = str(value or "").strip()
        if not value:
            raise serializers.ValidationError("region_id est obligatoire.")

        region = RefRegion.objects.filter(id_region=value).first()
        if not region:
            raise serializers.ValidationError("Region invalide.")

        self.context["resolved_region"] = region
        return region.id_region


class WorkflowSubmissionUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    payload = serializers.JSONField(required=False)
    region_id = serializers.CharField(max_length=50, required=False)

    def validate_region_id(self, value):
        value = str(value or "").strip()
        if not value:
            raise serializers.ValidationError("region_id vide interdit.")

        region = RefRegion.objects.filter(id_region=value).first()
        if not region:
            raise serializers.ValidationError("Region invalide.")

        self.context["resolved_region"] = region
        return region.id_region


class WorkflowRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)
    investigator_contact = serializers.CharField(max_length=255, required=False, allow_blank=True)
    notify_note = serializers.CharField(max_length=500, required=False, allow_blank=True)
    issues = serializers.ListField(
        required=False,
        allow_empty=True,
        child=serializers.DictField(),
    )

    def validate_issues(self, value):
        issues = value or []
        if len(issues) > 200:
            raise serializers.ValidationError("Trop d'anomalies envoyees (max 200).")

        normalized = []
        for idx, raw in enumerate(issues, start=1):
            if not isinstance(raw, dict):
                raise serializers.ValidationError(f"Issue #{idx} invalide (objet attendu).")

            message = str(raw.get("message") or "").strip()
            if not message:
                raise serializers.ValidationError(f"Issue #{idx}: 'message' est obligatoire.")

            severity = str(raw.get("severity") or "error").strip().lower()
            if severity not in {"error", "warning"}:
                raise serializers.ValidationError(f"Issue #{idx}: severity doit etre 'error' ou 'warning'.")

            normalized.append(
                {
                    "record_ref": str(raw.get("record_ref") or "").strip(),
                    "field": str(raw.get("field") or "").strip(),
                    "message": message[:500],
                    "severity": severity,
                }
            )

        return normalized


class WorkflowKoboSyncSerializer(serializers.Serializer):
    dataset_code = serializers.CharField(max_length=120)
    region_id = serializers.CharField(max_length=50)
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    since = serializers.DateTimeField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=5000, default=500)

    def validate_dataset_code(self, value):
        text = str(value or "").strip()
        if not text:
            raise serializers.ValidationError("dataset_code est obligatoire.")
        return text

    def validate_region_id(self, value):
        value = str(value or "").strip()
        if not value:
            raise serializers.ValidationError("region_id est obligatoire.")

        region = RefRegion.objects.filter(id_region=value).first()
        if not region:
            raise serializers.ValidationError("Region invalide.")

        self.context["resolved_region"] = region
        return region.id_region


class WorkflowActionLogSerializer(serializers.ModelSerializer):
    action_display = serializers.CharField(source="get_action_display", read_only=True)
    actor_username = serializers.CharField(source="actor.username", read_only=True)

    class Meta:
        model = WorkflowActionLog
        fields = [
            "action_id",
            "submission",
            "action",
            "action_display",
            "from_status",
            "to_status",
            "actor",
            "actor_username",
            "comment",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields


class WorkflowTransitionResultSerializer(serializers.Serializer):
    submission = WorkflowSubmissionSerializer()
    detail = serializers.CharField()
    previous_status = serializers.ChoiceField(choices=WorkflowStatus.choices)
    current_status = serializers.ChoiceField(choices=WorkflowStatus.choices)
