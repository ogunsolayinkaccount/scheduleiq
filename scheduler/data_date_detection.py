"""
Data Date Detection — ScheduleIQ Import Workflow

Detects a schedule's Data Date / Status Date directly from the uploaded
source file at import time, so the user only has to touch the field when
it's missing or wrong — never as the default path.

Golden rule shared with the rest of this app: never fabricate. A date is
returned only when a recognizable, labeled source value was actually
found; otherwise `detectedDataDate` is None and the Import Preview asks
the user to enter it. This module never falls back to today's date, the
upload timestamp, or an inferred value from activity progress.

Confidence levels:
  'authoritative' — P6 XER's own PROJECT.last_recalc_date field. This IS
                     P6's internal Data Date; nothing more reliable exists,
                     so XER never needs user confirmation to be "right" —
                     only to be reviewed.
  'high'          — an explicit "Data Date"/"Status Date"-labeled column
                     header, or a label cell immediately adjacent (same
                     row, next column) to a parseable date.
  'medium'        — a label cell followed by a parseable date directly
                     below it, rather than beside it — a slightly less
                     certain layout to interpret correctly.
  None            — nothing recognizable found. Always a legitimate
                     result, never treated as an error.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from .utils import parse_date

# Matched case-insensitively. 'dd' is intentionally exact-cell-only (never a
# substring match) to avoid false-positives against unrelated short codes.
DATA_DATE_LABELS = (
    'data date', 'status date', 'schedule data date', 'current data date', 'dd',
)


def _empty_result() -> Dict[str, Any]:
    return {'detectedDataDate': None, 'source': None, 'confidence': None}


def _normalize_label(value: Any) -> str:
    return re.sub(r'[:\s]+$', '', str(value).strip().lower())


def _try_parse_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    d = parse_date(value)
    return d.isoformat() if d else None


# ── P6 XER ───────────────────────────────────────────────────────────────

def detect_data_date_xer(project_meta: Dict[str, Any]) -> Dict[str, Any]:
    """project_meta comes from parsers.parse_project_meta_from_xer(), whose
    'data_date' field is already sourced from the XER PROJECT table's
    last_recalc_date column — P6's authoritative Data Date. This wrapper
    only tags provenance; it does not re-derive the value."""
    dd = (project_meta or {}).get('data_date')
    if not dd:
        return _empty_result()
    iso = dd.isoformat() if hasattr(dd, 'isoformat') else str(dd)
    return {'detectedDataDate': iso, 'source': 'P6_XER_PROJECT', 'confidence': 'authoritative'}


# ── Excel / CSV ──────────────────────────────────────────────────────────

def detect_data_date_tabular(df, source_label: str) -> Dict[str, Any]:
    """
    Deterministic label/value search over an Excel/CSV DataFrame:
      1. A column HEADER matching a known label -> first parseable value in
         that column (handles a repeated "Data Date" column).
      2. A CELL matching a known label exactly -> the next cell to its
         right in the same row (high confidence), else the cell directly
         below it (medium confidence) — handles a small metadata block
         above/beside the activity table, a common P6/Excel export layout.
    Bounded to the first 50 rows so a large activity table doesn't turn
    this into an O(rows x cols) scan of a big file.
    """
    if df is None or df.empty:
        return _empty_result()

    for col in df.columns:
        if _normalize_label(col) in DATA_DATE_LABELS:
            for v in df[col]:
                iso = _try_parse_iso(v)
                if iso:
                    return {'detectedDataDate': iso, 'source': source_label, 'confidence': 'high'}

    columns = list(df.columns)
    n_rows = min(len(df), 50)
    for i in range(n_rows):
        row = df.iloc[i]
        for j, col in enumerate(columns):
            cell_val = row[col]
            if cell_val is None or _normalize_label(cell_val) not in DATA_DATE_LABELS:
                continue
            if j + 1 < len(columns):
                iso = _try_parse_iso(row[columns[j + 1]])
                if iso:
                    return {'detectedDataDate': iso, 'source': source_label, 'confidence': 'high'}
            if i + 1 < len(df):
                iso = _try_parse_iso(df.iloc[i + 1][col])
                if iso:
                    return {'detectedDataDate': iso, 'source': source_label, 'confidence': 'medium'}

    return _empty_result()


# ── PDF ──────────────────────────────────────────────────────────────────

_PDF_DATE = r'([A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}|\d{4}-\d{2}-\d{2})'
_PDF_LABEL_PATTERN = re.compile(
    r'(?:data\s*date|status\s*date|current\s*data\s*date|schedule\s*data\s*date)\s*[:\-]?\s*' + _PDF_DATE,
    re.IGNORECASE,
)


def detect_data_date_pdf(text: str) -> Dict[str, Any]:
    """Searches extracted PDF text for an explicit 'Data Date'/'Status
    Date'-labeled value. Never guesses from printed activity dates —
    if no labeled match is found, returns unavailable."""
    if not text:
        return _empty_result()
    match = _PDF_LABEL_PATTERN.search(text)
    if not match:
        return _empty_result()
    iso = _try_parse_iso(match.group(1))
    if not iso:
        return _empty_result()
    return {'detectedDataDate': iso, 'source': 'PDF_METADATA', 'confidence': 'high'}


# ── Effective Data Date resolution (CPM & Data Date Governance audit) ──────
#
# The ONE authoritative answer to "what Data Date should any time-dependent
# analysis of this persisted schedule version use". Every CPM-status engine
# call site in views.py/report_service.py (Look Ahead, Should-Have-Started/
# Finished, S-Curve cutoff, histogram split, variance, milestone proximity,
# risk, EVM PV, driver ranking) must resolve through this rather than
# independently falling back to upload_timestamp or today() — that fallback
# was the single most common violation found in the CPM & Data Date
# Governance audit (upload_timestamp is when the file was imported, not a
# scheduling fact about the plan). When `available` is False, callers must
# surface a "Data Date required" condition for the affected calculation(s)
# rather than silently substituting anything.

def resolve_effective_data_date(schedule_version) -> Dict[str, Any]:
    """
    Returns:
      available       bool           False when no trustworthy effective
                                      Data Date exists for this version.
      dataDate        date | None    The effective date to use — never
                                      upload_timestamp, never today.
      sourceDataDate  date | None    The original, pre-override detected value.
      overridden      bool
      source          str | None     Detection source label (e.g. 'XER_LAST_RECALC_DATE').
      confidence      str | None
      reason          str | None     Explanation when unavailable.
    """
    if schedule_version is None:
        return {
            'available': False, 'dataDate': None, 'sourceDataDate': None,
            'overridden': False, 'source': None, 'confidence': None,
            'reason': 'No schedule version selected.',
        }
    dd = schedule_version.data_date
    base = {
        'sourceDataDate': schedule_version.source_data_date,
        'overridden': schedule_version.data_date_overridden,
        'source': schedule_version.data_date_source,
        'confidence': schedule_version.data_date_confidence,
    }
    if dd is None:
        return {
            'available': False, 'dataDate': None,
            'reason': (
                'No effective Data Date could be detected or confirmed for this schedule '
                'version — time-dependent analysis (Look Ahead, status classification, '
                'S-Curve, variance, EVM) cannot run until one is set via the Data Date editor.'
            ),
            **base,
        }
    return {'available': True, 'dataDate': dd, 'reason': None, **base}
