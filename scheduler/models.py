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


class Project(models.Model):
    """
    A real-world project that accumulates multiple imported schedule versions
    over time (baseline, monthly updates, recovery schedules, what-ifs).

    Deliberately thin — the heavy schedule data stays on ScheduleUpload rows
    (activities_json etc.) so this table doesn't duplicate large payloads.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=500)
    project_number = models.CharField(max_length=100, blank=True)
    client = models.CharField(max_length=200, blank=True)
    location = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


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
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE,
        null=True, blank=True, related_name='schedule_versions'
    )
    original_filename = models.CharField(max_length=500)
    sanitized_filename = models.CharField(max_length=500)
    file_type = models.CharField(max_length=20)        # XER XLSX CSV XML PDF
    xer_version = models.CharField(max_length=50, blank=True)

    # P6 project metadata extracted from file
    project_id_in_file = models.CharField(max_length=200, blank=True)
    project_name_in_file = models.CharField(max_length=500, blank=True)

    # data_date is the EFFECTIVE Data Date — what every analysis engine
    # actually uses. source_data_date is what was detected directly from
    # the file (data_date_detection.py) and never changes after import,
    # even if the user overrides data_date — see PATCH
    # /api/projects/<id>/versions/<id>/. data_date_overridden distinguishes
    # "user reviewed and accepted the detected date" from "user changed it".
    data_date = models.DateField(null=True, blank=True)
    source_data_date = models.DateField(null=True, blank=True)
    data_date_overridden = models.BooleanField(default=False)
    data_date_source = models.CharField(max_length=30, blank=True)      # P6_XER_PROJECT / EXCEL_METADATA / CSV_METADATA / PDF_METADATA / USER_ENTERED
    data_date_confidence = models.CharField(max_length=20, blank=True)  # authoritative / high / medium
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
    milestone_count = models.IntegerField(default=0)
    completed_count = models.IntegerField(default=0)
    in_progress_count = models.IntegerField(default=0)
    not_started_count = models.IntegerField(default=0)
    critical_count = models.IntegerField(default=0)
    negative_float_count = models.IntegerField(default=0)

    # Float statistics (working days) — null when no activity carries a usable
    # totalFloat value, rather than guessing at zero.
    min_total_float = models.FloatField(null=True, blank=True)
    max_total_float = models.FloatField(null=True, blank=True)
    avg_total_float = models.FloatField(null=True, blank=True)

    # Version identity — how this upload should read in a version picker
    # ("2026-08-05 Update"). Auto-derived from data_date when not supplied.
    version_label = models.CharField(max_length=200, blank=True)

    # Provenance
    import_method = models.CharField(max_length=40, blank=True)   # xer_parser / excel_column_mapping / csv_column_mapping / msp_xml_parser / pdf_table_extraction
    parser_version = models.CharField(max_length=20, blank=True)

    # Engine traceability
    analysis_engine_version = models.CharField(max_length=20, default='1.0.0')
    threshold_profile = models.ForeignKey(
        ThresholdProfile, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='uploads'
    )

    # Parse diagnostics
    parsing_warnings = models.JSONField(default=list)
    missing_required_data = models.JSONField(default=list)
    mapped_columns = models.JSONField(default=dict)      # {scheduleField: sourceColumn}
    unmapped_columns = models.JSONField(default=list)     # source columns not recognised
    duplicate_activity_ids = models.JSONField(default=list)
    invalid_date_count = models.IntegerField(default=0)

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


class Calendar(models.Model):
    """
    A P6/MSP calendar attached to a schedule version. Shared reference data
    (many activities point at the same calendar_id) so it's normalized rather
    than repeated inside every activity's JSON.

    Detailed per-day/holiday parsing of XER's clndr_data blob is attempted at
    import time (see calendar_engine.decode_calendar_data) and sets
    has_detailed_definition=True with standard_workweek/exceptions populated
    only when that decode reaches high/partial confidence — never guessed.
    A calendar this decoder can't confidently read keeps
    has_detailed_definition=False rather than fabricating working-day
    precision the parser doesn't actually have. raw_definition keeps the
    source string for future use.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule_upload = models.ForeignKey(
        ScheduleUpload, on_delete=models.CASCADE, related_name='calendars'
    )
    calendar_id = models.CharField(max_length=50)     # source file's clndr_id — scoped to this upload, not global
    name = models.CharField(max_length=200, blank=True)
    calendar_type = models.CharField(max_length=50, blank=True)   # CA_Base / CA_Rsrc / CA_Project
    is_default = models.BooleanField(default=False)

    hours_per_day = models.FloatField(null=True, blank=True)
    hours_per_week = models.FloatField(null=True, blank=True)
    hours_per_month = models.FloatField(null=True, blank=True)
    hours_per_year = models.FloatField(null=True, blank=True)

    standard_workweek = models.JSONField(default=dict, blank=True)   # {} until real clndr_data parsing exists
    exceptions = models.JSONField(default=list, blank=True)          # [] until real clndr_data parsing exists
    has_detailed_definition = models.BooleanField(default=False)
    raw_definition = models.TextField(blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name or self.calendar_id


class ActivityCodeType(models.Model):
    """A P6 activity-code category (e.g. Area, Discipline, Contractor, Phase)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule_upload = models.ForeignKey(
        ScheduleUpload, on_delete=models.CASCADE, related_name='activity_code_types'
    )
    code_type_id = models.CharField(max_length=50)
    name = models.CharField(max_length=200)
    is_global = models.BooleanField(default=False)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ActivityCode(models.Model):
    """A single value within an ActivityCodeType (e.g. Discipline = 'Electrical')."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code_type = models.ForeignKey(
        ActivityCodeType, on_delete=models.CASCADE, related_name='codes'
    )
    code_id = models.CharField(max_length=50)
    code_value = models.CharField(max_length=200)
    description = models.CharField(max_length=500, blank=True)
    parent_code_id = models.CharField(max_length=50, blank=True)   # raw source-file ref, not a self-FK — keeps import order-independent

    class Meta:
        ordering = ['code_value']

    def __str__(self):
        return self.code_value


class UDFType(models.Model):
    """A user-defined field definition (P6 UDFTYPE) available on this schedule version."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule_upload = models.ForeignKey(
        ScheduleUpload, on_delete=models.CASCADE, related_name='udf_types'
    )
    udf_type_id = models.CharField(max_length=50)
    field_name = models.CharField(max_length=200)
    subject_area = models.CharField(max_length=50, blank=True)   # TASK / PROJECT / ...
    data_type = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ['field_name']

    def __str__(self):
        return self.field_name


class CostAccount(models.Model):
    """
    A cost/control-account grouping key for rolling up budget, actuals, and
    hours — by WBS, discipline, area, system, or an explicit cost code.

    This model deliberately stores no budget/actual/hours numbers itself.
    Those are computed on demand (see cost_engine.py) by summing the
    XER-sourced budgetedCost/actualCost/budgetedHours/actualHours fields
    already present on every activity in ScheduleUpload.activities_json,
    filtered to activities matching this account's scope. Storing a
    duplicate copy here would risk drifting out of sync with the source
    schedule and violate the "never fabricate / always traceable to source"
    rule this project holds to elsewhere (see Calendar, quality_engine.py).

    Concepts P6 XER cannot supply (approved budget changes, commitments,
    an analyst-approved EAC) are NOT modeled here — see ManualCostEntry.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule_upload = models.ForeignKey(
        ScheduleUpload, on_delete=models.CASCADE, related_name='cost_accounts'
    )
    account_code = models.CharField(max_length=50)
    name = models.CharField(max_length=200, blank=True)

    # Scope — an activity belongs to this account if it matches ALL non-blank
    # fields below. Blank means "not filtered on this dimension".
    wbs_id = models.CharField(max_length=50, blank=True)
    discipline = models.CharField(max_length=200, blank=True)
    area = models.CharField(max_length=200, blank=True)
    system = models.CharField(max_length=200, blank=True)
    cost_code = models.CharField(max_length=100, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['account_code']

    def __str__(self):
        return self.name or self.account_code


MANUAL_COST_ENTRY_TYPE_CHOICES = [
    ('APPROVED_BUDGET_CHANGE', 'Approved Budget Change'),
    ('ORIGINAL_BUDGET_OVERRIDE', 'Original Budget Override'),
    ('COMMITMENT', 'Commitment (PO / Subcontract)'),
    ('APPROVED_EAC', 'Approved Estimate at Completion'),
]


class ManualCostEntry(models.Model):
    """
    User-entered cost facts that have no XER source and must never be
    inferred: approved budget changes, commitments (purchase orders /
    subcontracts — P6 has no procurement table), original-budget overrides
    for non-cost-loaded schedules, and an analyst-designated "approved" EAC
    (distinct from any of the calculated EAC methodologies in cost_engine.py,
    which are always returned as labeled scenarios, never as THE forecast).

    Always source_type='manual' — this table exists specifically so manual
    figures are never confused with imported ones downstream.

    Anchored on Project (not ScheduleUpload): a commitment or approved
    budget change is a durable project-controls fact, not something that
    should disappear or need re-entry when a new schedule version is
    imported. `schedule_upload` is optional — set it only when an entry is
    specifically "as of this update" (e.g. an EAC approved against a
    particular schedule snapshot).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='manual_cost_entries'
    )
    schedule_upload = models.ForeignKey(
        ScheduleUpload, on_delete=models.SET_NULL, related_name='manual_cost_entries',
        null=True, blank=True,
    )
    cost_account = models.ForeignKey(
        CostAccount, on_delete=models.CASCADE, related_name='manual_entries',
        null=True, blank=True,   # null = applies at the whole-project level
    )
    entry_type = models.CharField(max_length=30, choices=MANUAL_COST_ENTRY_TYPE_CHOICES)
    cost = models.FloatField(null=True, blank=True)
    hours = models.FloatField(null=True, blank=True)
    description = models.CharField(max_length=500, blank=True)

    # Optional direct scope — lets an entry specify WBS/discipline/area/
    # system/cost code without first creating a CostAccount row.
    wbs_id = models.CharField(max_length=50, blank=True)
    discipline = models.CharField(max_length=200, blank=True)
    area = models.CharField(max_length=200, blank=True)
    system = models.CharField(max_length=200, blank=True)
    cost_code = models.CharField(max_length=100, blank=True)

    effective_date = models.DateField(null=True, blank=True)
    reference_number = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)

    entered_by = models.CharField(max_length=200, blank=True)
    entered_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-entered_at']

    def __str__(self):
        return f'{self.get_entry_type_display()}: {self.cost if self.cost is not None else self.hours}'


DOCUMENT_TYPE_CHOICES = [
    ('SCHEDULE_NARRATIVE',  'Schedule Narrative'),
    ('LOOKAHEAD',           'Lookahead'),
    ('OWNER_REPORT',        'Owner Report'),
    ('CONTRACTOR_REPORT',   'Contractor Report'),
    ('SCHEDULE_TABLE',      'Schedule Table'),
    ('GENERAL_ATTACHMENT',  'General Attachment'),
]

EXTRACTION_STATUS_CHOICES = [
    ('SUCCESS', 'Success'),
    ('PARTIAL', 'Partial'),
    ('FAILED',  'Failed'),
]


class ScheduleDocument(models.Model):
    """
    A PDF (or other document) associated with a Project/ScheduleUpload whose
    value is its text, not structured activity data — narrative reports,
    lookaheads, owner/contractor reports. Text is stored for later AI
    analysis; it is never auto-merged into schedule activities.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='documents', null=True, blank=True
    )
    schedule_upload = models.ForeignKey(
        ScheduleUpload, on_delete=models.SET_NULL, related_name='documents', null=True, blank=True
    )
    filename = models.CharField(max_length=500)
    file_type = models.CharField(max_length=20, blank=True)
    document_type = models.CharField(
        max_length=30, choices=DOCUMENT_TYPE_CHOICES, default='GENERAL_ATTACHMENT'
    )
    extracted_text = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(default=timezone.now)
    page_count = models.IntegerField(null=True, blank=True)
    extraction_status = models.CharField(
        max_length=10, choices=EXTRACTION_STATUS_CHOICES, default='SUCCESS'
    )
    extraction_warnings = models.JSONField(default=list)

    class Meta:
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['project']),
            models.Index(fields=['document_type']),
        ]

    def __str__(self):
        return self.filename


RECOVERY_SCENARIO_STATUS_CHOICES = [
    ('DRAFT',        'Draft'),
    ('UNDER_REVIEW', 'Under Review'),
    ('ACCEPTED',     'Accepted'),
    ('REJECTED',     'Rejected'),
    ('ARCHIVED',     'Archived'),
]

SCHEDULE_RISK_STATUS_CHOICES = [
    ('OPEN',                    'Open'),
    ('UNDER_REVIEW',            'Under Review'),
    ('MITIGATION_PLANNED',      'Mitigation Planned'),
    ('MITIGATION_IN_PROGRESS',  'Mitigation In Progress'),
    ('MONITORING',              'Monitoring'),
    ('CLOSED',                  'Closed'),
]

MITIGATION_ACTION_STATUS_CHOICES = [
    ('OPEN',        'Open'),
    ('IN_PROGRESS', 'In Progress'),
    ('BLOCKED',     'Blocked'),
    ('COMPLETE',    'Complete'),
    ('CANCELLED',   'Cancelled'),
]


class ScheduleRisk(models.Model):
    """
    Persisted WORKFLOW state for a schedule-derived risk — status, owner,
    mitigation notes, target date. The risk's EVIDENCE (severity, signals,
    float, movement, etc.) is never stored here: it's always recomputed
    fresh from update_intelligence/baseline_progress/driving_chain by
    risk_register.py, so it can never go stale relative to the live
    schedule. This row exists purely so a scheduler's workflow decisions
    (status, ownership, mitigation plan) survive across Risk Register
    recomputations and schedule re-imports.

    `risk_key` is the correlating identifier across schedule versions —
    currently the Activity Code itself (or a milestone's Activity Code),
    which is already how every other ScheduleIQ engine matches an activity
    across updates. A risk naturally "continues" for as long as the same
    Activity Code keeps appearing in the risk register; if it stops
    appearing (the underlying condition cleared), the row is left as-is —
    closure is a deliberate user action or explicit rule, never automatic
    on a single healthy update (see item 8 of the phase directive).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='schedule_risks')
    risk_key = models.CharField(max_length=200)  # currently == Activity Code

    status = models.CharField(max_length=30, choices=SCHEDULE_RISK_STATUS_CHOICES, default='OPEN')
    owner = models.CharField(max_length=200, blank=True)
    mitigation_notes = models.TextField(blank=True)
    target_date = models.DateField(null=True, blank=True)

    # Traceability — when this risk was first surfaced by the Risk Register,
    # never rewritten on later recomputation.
    first_identified_data_date = models.DateField(null=True, blank=True)
    first_identified_version = models.ForeignKey(
        ScheduleUpload, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        unique_together = [('project', 'risk_key')]
        indexes = [
            models.Index(fields=['project', 'risk_key']),
            models.Index(fields=['project', 'status']),
        ]

    def __str__(self):
        return f'{self.risk_key} ({self.project.name}) — {self.status}'


class RecoveryScenario(models.Model):
    """
    A hypothetical 'what if we did X' schedule scenario. Never modifies the
    ScheduleUpload it's based on — recovery_engine.py works entirely on an
    in-memory copy; `assumptions` and `result` here are the only things
    persisted, and neither touches schedule_upload.activities_json.

    `schedule_upload` (the FK, immutable once set) is what guarantees
    traceability across later imports (item 23 of the Risk & Recovery
    phase directive): if Update 10 is imported after a scenario was built
    against Update 09, the scenario's FK still points at Update 09's own
    ScheduleUpload row, so it is never silently rebased.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='recovery_scenarios')
    schedule_upload = models.ForeignKey(
        ScheduleUpload, on_delete=models.CASCADE, related_name='recovery_scenarios'
    )
    risk = models.ForeignKey(
        ScheduleRisk, on_delete=models.SET_NULL, null=True, blank=True, related_name='recovery_scenarios',
    )
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=RECOVERY_SCENARIO_STATUS_CHOICES, default='DRAFT')
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    # The action list as submitted by the user (reduce_duration, remove_lag, ...)
    assumptions = models.JSONField(default=list)
    # Cached run_scenario() output — recomputed whenever assumptions change.
    result = models.JSONField(default=dict)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.name} ({self.project.name})'


class MitigationAction(models.Model):
    """
    A scheduler-managed mitigation task, optionally linked to a
    ScheduleRisk and/or a RecoveryScenario — the Risk -> Scenario -> Action
    audit trail (item 16). Never auto-created from schedule data; ScheduleIQ
    may identify a discipline/area as a risk driver, but an owner/action is
    always a deliberate user entry.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='mitigation_actions')
    risk = models.ForeignKey(
        ScheduleRisk, on_delete=models.SET_NULL, null=True, blank=True, related_name='mitigation_actions',
    )
    scenario = models.ForeignKey(
        RecoveryScenario, on_delete=models.SET_NULL, null=True, blank=True, related_name='mitigation_actions',
    )

    description = models.TextField()
    owner = models.CharField(max_length=200, blank=True)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=MITIGATION_ACTION_STATUS_CHOICES, default='OPEN')
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['project', 'status']),
        ]

    def __str__(self):
        return f'{self.description[:50]} ({self.status})'


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


REPORT_TYPE_CHOICES = [
    ('WEEKLY_PROJECT_CONTROLS', 'Weekly Project Controls Report'),
    ('MONTHLY_EXECUTIVE', 'Monthly Executive Report'),
]

REPORT_STATUS_CHOICES = [
    ('GENERATED', 'Generated'),
    ('ARCHIVED', 'Archived'),
]


class ProjectControlsReport(models.Model):
    """
    A generated, PERSISTED report snapshot — the payload from
    report_service.build_report_payload() frozen at generation time.

    This exists specifically so a report generated today stays reproducible
    later even if cost_engine.py/trend_engine.py/driver_engine.py change in
    a future release: PDF/Excel export and report-comparison always read
    `payload_json` from this row, never recompute from the live engines.
    `engine_version`/`ev_method`/`pv_method` are duplicated onto their own
    columns (in addition to living inside payload_json) so they're
    queryable/filterable without parsing JSON.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='controls_reports'
    )
    schedule_upload = models.ForeignKey(
        ScheduleUpload, on_delete=models.SET_NULL, related_name='controls_reports',
        null=True, blank=True,
    )
    data_date = models.DateField(null=True, blank=True)
    report_type = models.CharField(max_length=30, choices=REPORT_TYPE_CHOICES)
    generated_at = models.DateTimeField(default=timezone.now)

    payload_json = models.JSONField(default=dict)

    engine_version = models.CharField(max_length=20, blank=True)
    ev_method = models.CharField(max_length=40, blank=True)
    pv_method = models.CharField(max_length=40, blank=True)

    status = models.CharField(max_length=20, choices=REPORT_STATUS_CHOICES, default='GENERATED')
    title = models.CharField(max_length=300, blank=True)
    reporting_period_start = models.DateField(null=True, blank=True)
    reporting_period_end = models.DateField(null=True, blank=True)

    generated_by = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-generated_at']
        indexes = [
            models.Index(fields=['project', 'report_type']),
            models.Index(fields=['generated_at']),
        ]

    def __str__(self):
        return f'{self.get_report_type_display()} — {self.project.name} ({self.data_date or "no date"})'
