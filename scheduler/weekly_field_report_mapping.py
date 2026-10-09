"""
Weekly Field Report Excel import — column-mapping vocabulary.

Deliberately a SEPARATE, parallel vocabulary from column_mapping.py's
FIELD_SYNONYMS, which means schedule-ACTIVITY fields (activity_id, WBS,
dates, float, % complete) — none of that applies to a weekly manpower
report. This module reuses column_mapping.detect_columns()'s matching
ALGORITHM (via its `field_synonyms` parameter) rather than duplicating it,
with its own small, purpose-built synonym table.

Pure functions only — no Django/DB dependency, no pandas dependency, so
this (and the row-shaping logic in weekly_field_report_import.py) stays
unit-testable in isolation like every other engine in this package.
"""
from __future__ import annotations

from typing import Dict, List

from .column_mapping import detect_columns

# Canonical field -> recognised header synonyms (already-normalized by
# column_mapping.normalize_key: lowercase, non-alphanumeric -> underscore).
WEEKLY_REPORT_FIELD_SYNONYMS: Dict[str, List[str]] = {
    'project_id':                      ['project_id', 'project_number', 'project_code', 'proj_id'],
    'week_start_date':                 ['week_start_date', 'week_start', 'week_of', 'reporting_week', 'week'],
    'actual_headcount':                ['actual_headcount', 'actual_manpower', 'field_headcount', 'actual_field_headcount', 'actual'],
    'next_week_forecast_headcount':    ['next_week_forecast_headcount', 'next_week_forecast', 'forecast_headcount', 'next_week'],
    'pm_projected_headcount':          ['pm_projected_headcount', 'pm_projection', 'pm_forecast'],
    'monthly_target_headcount':        ['monthly_target_headcount', 'monthly_target', 'target_headcount'],
    'last_client_update_date':         ['last_client_update_date', 'last_client_update', 'client_update_date'],
}

# A weekly field report is meaningless without a week to attach it to —
# matches WeeklyFieldReport.week_start_date, the model's own only
# non-nullable manual field besides the project FK.
REQUIRED_FIELDS = ['week_start_date']


def detect_weekly_report_columns(columns: List[str]) -> Dict[str, str]:
    """canonical field -> source column name, using the shared fuzzy-match
    algorithm in column_mapping.detect_columns, scoped to this vocabulary."""
    return detect_columns(columns, field_synonyms=WEEKLY_REPORT_FIELD_SYNONYMS)
