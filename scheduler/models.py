import uuid
from django.db import models
from django.utils import timezone


SCHEDULE_CLASSIFICATION_CHOICES = [
    ('CURRENT_UPDATE',    'Current Update'),
    ('APPROVED_BASELINE', 'Approved Baseline'),
    ('PREVIOUS_UPDATE',   'Previous Update'),
    ('RECOVERY_SCHEDULE', 'Recovery Schedule'),
    ('REVISED_BASELINE',  'Revised Baseline'),
    ('WHAT_IF',           'What-If Schedule'),
]

APPROVAL_STATUS_CHOICES = [
    ('PENDING',     'Pending Review'),
    ('APPROVED',    'Approved'),
    ('REJECTED',    'Rejected'),
    ('SUPERSEDED',  'Superseded'),
]

OVERALL_STATUS_CHOICES = [
    ('ON_TRACK',     'On Track'),
    ('AT_RISK',      'At Risk'),
    ('OFF_TRACK',    'Off Track / Delayed'),
    ('UNDETERMINED', 'Undetermined'),
]

MILESTONE_CATEGORY_CHOICES = [
    ('CONTRACTUAL_COMPLETION', 'Contractual Completion'),
    ('CONTRACTUAL_INTERIM',    'Contractual Interim Milestone'),
    ('OWNER',                  'Owner Milestone'),
    ('GC',                     'General Contractor Milestone'),
    ('SUBCONTRACTOR',          'Subcontractor Milestone'),
    ('COMMISSIONING',          'Commissioning Milestone'),
    ('TURNOVER',               'Turnover Milestone'),
    ('BENEFICIAL_USE',         'Beneficial Use Milestone'),
    ('INTERNAL_TARGET',        'Internal Target'),
    ('INFORMATIONAL',          'Informational Milestone'),
]


class ThresholdProfile(models.Model):
    """Administrator-editable thresholds. Never buried in source code."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.CharField(max_length=200, blank=True)

    # Float thresholds (working days)
    critical_float_days = models.FloatField(default=0.0)
    near_critical_float_days = models.FloatField(default=5.0)
    float_warning_days = models.FloatField(default=14.0)
    max_negative_float_pct = models.FloatField(default=5.0)
    max_high_float_pct = models.FloatField(default=15.0)

    # Milestone thresholds (calendar days)
    milestone_yellow_days = models.FloatField(default=5.0)
    milestone_red_days = models.FloatField(default=14.0)

    # Project-finish thresholds (calendar days)
    project_finish_yellow_days = models.FloatField(default=14.0)
    project_finish_red_days = models.FloatField(default=30.0)

    # Quality thresholds (percentages)
    max_open_ends_pct = models.FloatField(default=5.0)
    max_constraint_pct = models.FloatField(default=15.0)
    max_hard_constraint_pct = models.FloatField(default=5.0)
    max_lag_pct = models.FloatField(default=20.0)
    max_excessive_lag_pct = models.FloatField(default=5.0)
    max_long_duration_pct = models.FloatField(default=5.0)
    max_invalid_progress_pct = models.FloatField(default=5.0)
    max_missed_task_pct = models.FloatField(default=10.0)

    # Forecast change thresholds (calendar days)
    max_forecast_date_change_days = models.FloatField(default=14.0)
    max_float_consumption_days = models.FloatField(default=5.0)
    max_critical_path_slippage_days = models.FloatField(default=5.0)

    # Score minimums (0–100)
    min_schedule_quality_score = models.FloatField(default=60.0)
    min_completeness_score = models.FloatField(default=70.0)
    min_confidence_score = models.FloatField(default=50.0)

    # Risk score weights (should sum to 100)
    weight_milestone = models.FloatField(default=25.0)
    weight_project_finish = models.FloatField(default=15.0)
    weight_critical_path = models.FloatField(default=20.0)
    weight_progress = models.FloatField(default=15.0)
    weight_float_health = models.FloatField(default=10.0)
    weight_quality = models.FloatField(default=10.0)
    weight_trend = models.FloatField(default=5.0)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @classmethod
    def get_or_create_default(cls):
        obj = cls.objects.filter(is_default=True).first()
        if obj:
            return obj
        return cls.objects.create(
            name='Default Profile',
            description='Standard schedule assessment thresholds based on CPM best practice.',
            is_default=True,
        )


class ScheduleUpload(models.Model):
    """Persists every uploaded schedule file with its metadata."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    original_filename = models.CharField(max_length=500)
    sanitized_filename = models.CharField(max_length=500)
    file_type = models.CharField(max_length=20)        # XER XLSX CSV XML PDF
    xer_version = models.CharField(max_length=50, blank=True)

    # P6 project metadata extracted from file
    project_id_in_file = models.CharField(max_length=200, blank=True)
    project_name_in_file = models.CharField(max_length=500, blank=True)
    data_date = models.DateField(null=True, blank=True)
    planned_start = models.DateField(null=True, blank=True)
    forecast_finish = models.DateField(null=True, blank=True)
    must_finish_by = models.DateField(null=True, blank=True)
    baseline_finish = models.DateField(null=True, blank=True)

    # Upload context
    upload_timestamp = models.DateTimeField(default=timezone.now)
    uploaded_by = models.CharField(max_length=200, blank=True)
    user_notes = models.TextField(blank=True)
    file_checksum = models.CharField(max_length=64, blank=True)   # SHA-256 hex
    file_size_bytes = models.BigIntegerField(default=0)

    # Classification (user must designate — default is CURRENT_UPDATE, never auto-baseline)
    schedule_classification = models.CharField(
        max_length=30, choices=SCHEDULE_CLASSIFICATION_CHOICES, default='CURRENT_UPDATE'
    )
    approval_status = models.CharField(
        max_length=20, choices=APPROVAL_STATUS_CHOICES, default='PENDING'
    )
    schedule_revision_number = models.CharField(max_length=50, blank=True)

    # Parse counts
    activity_count = models.IntegerField(default=0)
    relationship_count = models.IntegerField(default=0)
    wbs_node_count = models.IntegerField(default=0)
    calendar_count = models.IntegerField(default=0)

    # Engine traceability
    analysis_engine_version = models.CharField(max_length=20, default='1.0.0')
    threshold_profile = models.ForeignKey(
        ThresholdProfile, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='uploads'
    )

    # Parse diagnostics
    parsing_warnings = models.JSONField(default=list)
    missing_required_data = models.JSONField(default=list)

    # Full activity payload — preserves what /api/upload already returns so the
    # frontend workflow is unchanged; also used by the status engine.
    activities_json = models.JSONField(default=list)

    class Meta:
        ordering = ['-upload_timestamp']
        indexes = [
            models.Index(fields=['schedule_classification']),
            models.Index(fields=['upload_timestamp']),
            models.Index(fields=['project_id_in_file']),
        ]

    def __str__(self):
        return f'{self.original_filename} ({self.schedule_classification})'


class MilestoneDefinition(models.Model):
    """
    User-designated milestones with contractual dates.
    Not every P6 milestone is contractual — the user must designate.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule_upload = models.ForeignKey(
        ScheduleUpload, on_delete=models.CASCADE,
        related_name='milestones', null=True, blank=True
    )

    activity_id = models.CharField(max_length=200)
    activity_name = models.CharField(max_length=500, blank=True)
    description = models.TextField(blank=True)
    milestone_category = models.CharField(
        max_length=30, choices=MILESTONE_CATEGORY_CHOICES, default='INFORMATIONAL'
    )

    # Dates
    baseline_date = models.DateField(null=True, blank=True)
    current_forecast_date = models.DateField(null=True, blank=True)
    previous_forecast_date = models.DateField(null=True, blank=True)
    contract_required_date = models.DateField(null=True, blank=True)
    must_finish_by_date = models.DateField(null=True, blank=True)
    actual_finish_date = models.DateField(null=True, blank=True)

    # Governance
    allowable_variance_days = models.FloatField(default=0.0)
    responsible_organization = models.CharField(max_length=200, blank=True)
    is_critical_milestone = models.BooleanField(default=False)
    approval_status = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    approved_by = models.CharField(max_length=200, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['contract_required_date', 'activity_id']

    @property
    def is_contractual(self):
        return self.milestone_category in (
            'CONTRACTUAL_COMPLETION', 'CONTRACTUAL_INTERIM'
        )

    def __str__(self):
        return f'{self.activity_id} — {self.milestone_category}'


class ScheduleAnalysis(models.Model):
    """Stored result of every status-classification run. Never overwritten."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    current_upload = models.ForeignKey(
        ScheduleUpload, on_delete=models.CASCADE, related_name='analyses'
    )
    baseline_upload = models.ForeignKey(
        ScheduleUpload, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='used_as_baseline_in'
    )
    previous_upload = models.ForeignKey(
        ScheduleUpload, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='used_as_previous_in'
    )
    threshold_profile = models.ForeignKey(
        ThresholdProfile, on_delete=models.SET_NULL, null=True, blank=True
    )

    analyzed_at = models.DateTimeField(default=timezone.now)
    data_date_used = models.DateField(null=True, blank=True)

    # Result
    overall_status = models.CharField(
        max_length=20, choices=OVERALL_STATUS_CHOICES, default='UNDETERMINED'
    )
    risk_score = models.FloatField(default=0.0)
    confidence_score = models.FloatField(default=0.0)
    schedule_quality_score = models.FloatField(default=0.0)

    # Explainability
    primary_reason = models.TextField(blank=True)
    reason_codes = models.JSONField(default=list)
    triggered_thresholds = models.JSONField(default=list)
    data_limitations = models.JSONField(default=list)
    recommended_actions = models.JSONField(default=list)

    # Key variances (calendar days)
    contract_date_variance_days = models.FloatField(null=True, blank=True)
    baseline_finish_variance_days = models.FloatField(null=True, blank=True)
    previous_update_finish_variance_days = models.FloatField(null=True, blank=True)
    critical_path_float_days = models.FloatField(null=True, blank=True)
    near_critical_path_count = models.IntegerField(default=0)

    # Full serialised result for API/frontend
    full_result = models.JSONField(default=dict)

    class Meta:
        ordering = ['-analyzed_at']
        indexes = [
            models.Index(fields=['overall_status']),
            models.Index(fields=['analyzed_at']),
        ]

    def __str__(self):
        return f'{self.current_upload.original_filename} → {self.overall_status}'


class AuditLog(models.Model):
    """Immutable audit trail for every governance action."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(default=timezone.now)
    user = models.CharField(max_length=200, blank=True)
    action = models.CharField(max_length=100)
    object_type = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100)
    previous_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    reason = models.TextField(blank=True)
    approval_reference = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['object_type', 'object_id']),
            models.Index(fields=['timestamp']),
        ]

    def __str__(self):
        return f'{self.timestamp} — {self.action} on {self.object_type}/{self.object_id}'
