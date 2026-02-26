from django.db import models


class ImportLog(models.Model):
    import_uuid = models.UUIDField(primary_key=True, editable=False)
    project_id = models.UUIDField(blank=True, null=True)
    dataset = models.TextField()
    rows_total = models.IntegerField(blank=True, null=True)
    rows_ok = models.IntegerField(blank=True, null=True)
    rows_error = models.IntegerField(blank=True, null=True)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(blank=True, null=True)
    status = models.TextField(blank=True, null=True)
    message = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'audit"."import_log'
        ordering = ["-started_at"]


class EtlRun(models.Model):
    run_id = models.UUIDField(primary_key=True, editable=False)
    trigger = models.TextField()
    project_id = models.UUIDField(blank=True, null=True)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(blank=True, null=True)
    status = models.TextField(blank=True, null=True)
    errors_cnt = models.IntegerField(blank=True, null=True)
    messages = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'audit"."etl_run'
        ordering = ["-started_at"]

