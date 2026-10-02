"""
Contractual Milestone Tracker — Field Dashboard.

Governing principle (per the Field Dashboard directive): the contractual
finish date is NEVER assumed from the P6 baseline — it only exists when a
scheduler/PM has explicitly designated it (models.MilestoneDefinition,
contract_required_date), ideally with a source-document reference. This
module never reads or infers a contractual date from schedule data; the
caller supplies it from the persisted register.

Status is classified independently of schedule-wide negative-float
populations — see risk_register.py/float_intelligence.py for that
(unrelated) concept. Only the DESIGNATED milestone's own matched P6
activity and its own driving-path float are evidence here.

Pure functions, no DB access — same pattern as update_intelligence.py /
baseline_progress.py / milestones.py.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from .calendar_engine import CalendarDefinition, working_days_between


def _calendar_for(row: dict, calendars: Optional[Dict[str, CalendarDefinition]]) -> Optional[CalendarDefinition]:
    """Same per-activity calendar lookup already used by update_intelligence.py/
    baseline_progress.py (each keeps its own small copy rather than cross-
    importing) — the matched activity's own calendarId, never a project-wide
    default stand-in for a specific calendar that may not apply to it."""
    if not calendars:
        return None
    cal_id = row.get('calendarId')
    return calendars.get(cal_id) if cal_id else None

ENGINE_VERSION = '1.0.0'

GREEN = 'GREEN'
YELLOW = 'YELLOW'
RED = 'RED'
UNVERIFIED = 'UNVERIFIED'

# Default near-term driving-path warning threshold (days of float) used when
# the caller doesn't supply one explicitly. Documented calendar basis: this
# counts in CALENDAR days against the matched activity's Current Total
# Float (the same field activity_analysis.py/float_intelligence.py already
# compute — never recalculated here), consistent with how float itself is
# always expressed throughout ScheduleIQ. Callers (the Field Dashboard API)
# accept a `warningThresholdDays` query param to override this per request.
DEFAULT_WARNING_THRESHOLD_DAYS = 10.0


def _act_id(a: dict) -> str:
    return str(a.get('activityId') or a.get('code') or a.get('id') or '')


def match_p6_activity(activity_id: str, current_rows: List[dict]) -> Optional[dict]:
    """Finds the activity_analysis row (build_activity_analysis output —
    the one authoritative per-activity record) matching a registered
    contractual milestone's activity_id. None if not found in the CURRENT
    version — a stale/retired mapping must surface as UNVERIFIED, never
    silently matched to the wrong row."""
    for row in current_rows:
        if _act_id(row) == activity_id:
            return row
    return None


def classify_contractual_status(
    contract_required_date: Optional[date],
    matched_row: Optional[dict],
    warning_threshold_days: float = DEFAULT_WARNING_THRESHOLD_DAYS,
    has_documented_issue: bool = False,
    calendar: Optional[CalendarDefinition] = None,
) -> Dict[str, Any]:
    """
    Classifies ONE contractual milestone. `matched_row` is the
    activity_analysis row for the milestone's mapped P6 activity (or None
    if unmapped/not found in the current version).

    GREEN:  P6 forecasts on/before the contractual date, and the driving
            path has no near-term float warning and no documented issue.
    YELLOW: P6 still forecasts on/before the contractual date, but the
            activity's current total float is at or below
            warning_threshold_days, OR a documented schedule exposure was
            passed in.
    RED:    P6 forecasts completion AFTER the contractual date.
    UNVERIFIED: the contract date, the P6 mapping, or usable forecast/float
            evidence is missing.
    """
    evidence: Dict[str, Any] = {
        'contractRequiredDate': contract_required_date.isoformat() if contract_required_date else None,
        'activityId': matched_row.get('activityId') if matched_row else None,
        'activityName': matched_row.get('activityName') if matched_row else None,
        'p6ForecastFinish': None,
        'currentTotalFloat': matched_row.get('currentTotalFloat') if matched_row else None,
        'driving': matched_row.get('driving') if matched_row else None,
        'criticalActionable': matched_row.get('criticalActionable') if matched_row else None,
        'varianceCalendarDays': None,
        'varianceWorkingDays': None,
        'warningThresholdDays': warning_threshold_days,
        'hasDocumentedIssue': has_documented_issue,
    }

    if contract_required_date is None:
        return {
            'status': UNVERIFIED,
            'explanation': 'No contractual finish date has been designated for this milestone.',
            **evidence,
        }
    if matched_row is None:
        return {
            'status': UNVERIFIED,
            'explanation': 'The registered Activity ID does not match any activity in the current schedule version.',
            **evidence,
        }

    # Prefer an actual finish date once the activity has genuinely
    # finished (forecastFinish is already None at that point under the
    # canonical rule); otherwise use the forecast finish date.
    forecast_finish_raw = matched_row.get('actualFinish') or matched_row.get('forecastFinish') or matched_row.get('currentFinish')
    forecast_finish = _parse(forecast_finish_raw)
    evidence['p6ForecastFinish'] = forecast_finish.isoformat() if forecast_finish else None

    if forecast_finish is None:
        return {
            'status': UNVERIFIED,
            'explanation': 'The matched activity has no usable current or forecast finish date in this version.',
            **evidence,
        }

    variance_days = (forecast_finish - contract_required_date).days
    evidence['varianceCalendarDays'] = variance_days
    evidence['varianceWorkingDays'] = working_days_between(contract_required_date, forecast_finish, calendar)

    if variance_days > 0:
        return {
            'status': RED,
            'explanation': (
                f'P6 forecasts completion on {forecast_finish.isoformat()}, '
                f'{variance_days} calendar day{"s" if variance_days != 1 else ""} after the '
                f'contractual date of {contract_required_date.isoformat()}.'
            ),
            **evidence,
        }

    tf = matched_row.get('currentTotalFloat')
    near_term_float_warning = tf is not None and tf <= warning_threshold_days
    if near_term_float_warning or has_documented_issue:
        reasons = []
        if near_term_float_warning:
            reasons.append(f'the driving path has only {tf}d of float (warning threshold {warning_threshold_days}d)')
        if has_documented_issue:
            reasons.append('a documented schedule exposure has been flagged for this milestone')
        return {
            'status': YELLOW,
            'explanation': f'P6 still meets the contractual date, but {" and ".join(reasons)}.',
            **evidence,
        }

    return {
        'status': GREEN,
        'explanation': f'P6 forecasts completion on {forecast_finish.isoformat()}, on or before the contractual date.',
        **evidence,
    }


def _parse(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(v)[:10]).date()
    except (ValueError, TypeError):
        return None


def build_contractual_milestone_tracker(
    register_entries: List[dict],
    current_rows: List[dict],
    warning_threshold_days: float = DEFAULT_WARNING_THRESHOLD_DAYS,
    calendars: Optional[Dict[str, CalendarDefinition]] = None,
) -> Dict[str, Any]:
    """
    `register_entries`: serialized MilestoneDefinition rows (id, activityId,
    description, category, contractRequiredDate, sourceDocumentReference,
    allowableVarianceDays, approvalStatus, hasDocumentedIssue). Only entries
    with an is_contractual category are tracked here — see views.py.
    """
    rows = []
    for entry in register_entries:
        matched = match_p6_activity(entry.get('activityId') or '', current_rows)
        contract_date = _parse(entry.get('contractRequiredDate'))
        cal = _calendar_for(matched, calendars) if matched else None
        result = classify_contractual_status(
            contract_date, matched, warning_threshold_days,
            has_documented_issue=bool(entry.get('hasDocumentedIssue')), calendar=cal,
        )
        rows.append({
            'milestoneId': entry.get('id'),
            'milestoneName': entry.get('description') or entry.get('activityId'),
            'category': entry.get('category'),
            'sourceDocumentReference': entry.get('sourceDocumentReference') or None,
            'approvalStatus': entry.get('approvalStatus') or None,
            **result,
        })
    counts = {GREEN: 0, YELLOW: 0, RED: 0, UNVERIFIED: 0}
    for r in rows:
        counts[r['status']] += 1
    return {
        'engineVersion': ENGINE_VERSION,
        'configured': len(register_entries) > 0,
        'warningThresholdDays': warning_threshold_days,
        'milestones': rows,
        'counts': counts,
    }
