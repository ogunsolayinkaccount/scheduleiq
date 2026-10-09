"""
Weekly Field Report Excel import — row extraction from an uploaded
workbook's DataFrame.

Scope is deliberately narrow: this reads weekly REPORTING rows (one row =
one week), not an arbitrary workbook structure. It does not attempt the
general schedule-activity parsing column_mapping.py performs — that
module's vocabulary and this one's are kept separate on purpose (see
weekly_field_report_mapping.py).

Pure, DB-free, pandas-only — no Django import here, so this is unit-
testable in isolation. All per-row business validation (Monday-start,
non-negative headcount, duplicate weeks, project identity) stays in
views.py's `_validate_weekly_report_row`, which this module's output feeds
into unmodified — a spreadsheet row is validated by the EXACT same
function a manually-typed row is.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from .weekly_field_report_mapping import WEEKLY_REPORT_FIELD_SYNONYMS, detect_weekly_report_columns

MAX_IMPORT_ROWS = 500  # ~10 years of weekly reports — generous headroom, not a schedule-file-sized ceiling.

# camelCase keys matching _validate_weekly_report_row's expected body shape,
# so an extracted row can be passed to it completely unmodified.
_DATE_FIELDS = {'week_start_date', 'last_client_update_date'}
_HEADCOUNT_FIELDS = {'actual_headcount', 'next_week_forecast_headcount', 'pm_projected_headcount', 'monthly_target_headcount'}
_CANONICAL_TO_BODY_KEY = {
    'week_start_date': 'weekStartDate',
    'actual_headcount': 'actualHeadcount',
    'next_week_forecast_headcount': 'nextWeekForecastHeadcount',
    'pm_projected_headcount': 'pmProjectedHeadcount',
    'monthly_target_headcount': 'monthlyTargetHeadcount',
    'last_client_update_date': 'lastClientUpdateDate',
}


def _cell(row, col):
    if col is None:
        return None
    val = row.get(col)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, str) and not val.strip():
        return None
    return val


def _cell_as_date_string(row, col):
    """Returns an ISO date string (never a datetime/Timestamp) so it feeds
    straight into parse_date() exactly like a hand-typed 'YYYY-MM-DD' —
    one date-parsing path for every row, Excel or manual."""
    val = _cell(row, col)
    if val is None:
        return None
    try:
        parsed = pd.to_datetime(val, errors='coerce')
    except Exception:
        return str(val)  # let the shared validator's own parse_date reject it with its own message
    if pd.isna(parsed):
        return str(val)
    return parsed.date().isoformat()


def _cell_as_raw_string(row, col):
    val = _cell(row, col)
    return None if val is None else str(val).strip()


def extract_weekly_report_rows(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Reads an uploaded workbook's DataFrame into a list of raw (unvalidated)
    weekly-report row dicts, each already shaped as a `_validate_weekly_
    report_row` body (camelCase keys) plus `rowNumber` (1-indexed, +2 to
    match the spreadsheet's own row numbering past the header) and
    `projectIdRaw` (the file's own stated project identifier for that row,
    or None if the column wasn't present/blank for that row).

    Never silently drops a row — a row with no recognizable fields at all
    still appears in the output (as all-None), so the caller's per-row
    validation surfaces it as invalid rather than it vanishing unexplained.
    """
    columns = [str(c) for c in df.columns]
    mapped = detect_weekly_report_columns(columns)
    mapped_source_cols = set(mapped.values())
    unmapped_columns = [c for c in columns if c not in mapped_source_cols]

    rows: List[Dict[str, Any]] = []
    for idx, row in df.iterrows():
        body: Dict[str, Any] = {'rowNumber': int(idx) + 2}
        project_col = mapped.get('project_id')
        body['projectIdRaw'] = _cell_as_raw_string(row, project_col)

        for canonical, body_key in _CANONICAL_TO_BODY_KEY.items():
            col = mapped.get(canonical)
            if canonical in _DATE_FIELDS:
                body[body_key] = _cell_as_date_string(row, col)
            else:
                raw = _cell(row, col)
                body[body_key] = str(raw) if raw is not None else None

        rows.append(body)

    return {
        'mappedFields': mapped,
        'unmappedColumns': unmapped_columns,
        'rows': rows,
        'rowCount': len(rows),
    }
