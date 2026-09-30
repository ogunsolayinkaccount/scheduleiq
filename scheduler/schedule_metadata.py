"""
ScheduleUpload metadata computation — Phase 1.5.

Pure function, no DB dependency (same pattern as status_engine.py /
quality_engine.py) — derives ScheduleUpload summary fields from an already-
parsed activity list. Never guesses: counts/stats that can't be derived from
the actual data are left None/0 rather than fabricated.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

PARSER_VERSION = '1.0.0'


def _parse_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and v:
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _float_or_none(v) -> Optional[float]:
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compute_upload_metadata(activities: List[dict]) -> Dict[str, Any]:
    """
    Returns a dict of ScheduleUpload field values derived from the activity
    list. Every count is exact (derived from the data actually parsed);
    every date/float statistic is None when no activity supplies it.
    """
    if not activities:
        return {
            'activity_count': 0, 'relationship_count': 0, 'milestone_count': 0,
            'completed_count': 0, 'in_progress_count': 0, 'not_started_count': 0,
            'critical_count': 0, 'negative_float_count': 0,
            'min_total_float': None, 'max_total_float': None, 'avg_total_float': None,
            'data_date': None, 'planned_start': None, 'forecast_finish': None,
            'baseline_finish': None,
        }

    milestone_count = 0
    completed = in_progress = not_started = 0
    critical = negative_float = 0
    relationship_count = 0
    floats: List[float] = []
    starts: List[date] = []
    finishes: List[date] = []
    baseline_finishes: List[date] = []

    for a in activities:
        is_ms = bool(a.get('isMilestone'))
        if is_ms:
            milestone_count += 1

        pct = a.get('pctComplete') or 0.0
        if pct >= 100:
            completed += 1
        elif a.get('start'):
            in_progress += 1
        else:
            not_started += 1

        if a.get('isCritical') and not is_ms:
            critical += 1

        tf = _float_or_none(a.get('totalFloat'))
        if tf is not None:
            floats.append(tf)
            if tf < 0:
                negative_float += 1

        relationship_count += len(a.get('predecessors') or [])

        bs = _parse_date(a.get('bStart') or a.get('start'))
        if bs:
            starts.append(bs)
        bf = _parse_date(a.get('bFinish') or a.get('finish'))
        if bf:
            finishes.append(bf)
        bfl = _parse_date(a.get('bFinish'))
        if bfl and not is_ms:
            baseline_finishes.append(bfl)

    # Project data date: the latest activity data date isn't derivable from a
    # plain activity list (that lives on the source file's PROJECT record for
    # XER, or isn't present at all for Excel/CSV) — callers should pass it
    # through separately when known. Here we only derive what the activity
    # list itself can prove: planned start/forecast finish spans.
    planned_start = min(starts) if starts else None
    forecast_finish = max(finishes) if finishes else None
    baseline_finish = max(baseline_finishes) if baseline_finishes else None

    return {
        'activity_count': len(activities),
        'relationship_count': relationship_count,
        'milestone_count': milestone_count,
        'completed_count': completed,
        'in_progress_count': in_progress,
        'not_started_count': not_started,
        'critical_count': critical,
        'negative_float_count': negative_float,
        'min_total_float': round(min(floats), 2) if floats else None,
        'max_total_float': round(max(floats), 2) if floats else None,
        'avg_total_float': round(sum(floats) / len(floats), 2) if floats else None,
        'planned_start': planned_start,
        'forecast_finish': forecast_finish,
        'baseline_finish': baseline_finish,
    }


def derive_version_label(data_date: Optional[date], upload_timestamp) -> str:
    """
    Default version label shown in a version picker, e.g. "2026-08-05 Update".
    Prefers the schedule's own data date (what a scheduler would call "the
    August 5th update") over the upload timestamp.
    """
    d = data_date or (upload_timestamp.date() if hasattr(upload_timestamp, 'date') else None)
    if not d:
        return 'Update'
    return f'{d.isoformat()} Update'
