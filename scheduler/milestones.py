"""
Milestone Intelligence — ScheduleIQ

Pure, DB-free service (same pattern as the other engines). Detects
milestones from the imported activity type (isMilestone, already set by
every parser — XER via TT_Mile, Excel/CSV via zero duration or a Milestone
column, MSP via the Milestone flag) and reports variance/movement/risk.

baselineFinish and varianceDays are only populated when the caller supplies
a distinct baseline activity list (a ScheduleUpload classified
APPROVED_BASELINE/REVISED_BASELINE) — never guessed from the current
version's own bStart/bFinish, which represent the CURRENT schedule, not a
fixed baseline. Likewise, previousUpdateFinish/movement only appear when a
comparison_result (schedule_comparison.compare_schedules output) is supplied.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from .schedule_comparison import _id_to_code_name


def _act_id(a: dict) -> str:
    return str(a.get('code') or a.get('id') or '')


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


def _tf(a: dict) -> Optional[float]:
    v = a.get('totalFloat')
    try:
        return float(v) if v is not None and v != '' else None
    except (TypeError, ValueError):
        return None


def detect_milestones(activities: List[dict]) -> List[dict]:
    return [a for a in activities if a.get('isMilestone')]


def _driving_predecessor(
    activity: dict, act_by_id: Dict[str, dict], id_map: Optional[Dict[str, Tuple[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    """Same 'lowest float wins' heuristic as the frontend's path tracing —
    a relationship trace, not a recalculated CPM (see App.tsx's tracePath).

    `id_map` resolves a relationship's raw P6 task_id back to the Activity
    Code that keys `act_by_id` (schedule_comparison._id_to_code_name) — on
    real XER data a predecessor's actId is the source file's internal
    task_id, not the human-readable code, so without this resolution every
    lookup here silently misses and no driving predecessor is ever found."""
    id_map = id_map or {}
    best = None
    best_float = float('inf')
    for p in (activity.get('predecessors') or []):
        raw_id = p.get('actId')
        resolved_id = id_map.get(raw_id, (raw_id, ''))[0] or raw_id
        act = act_by_id.get(resolved_id)
        if not act:
            continue
        f = _tf(act)
        fv = f if f is not None else float('inf')
        if fv < best_float:
            best_float = fv
            best = {
                'activityId': _act_id(act), 'activityName': act.get('name', ''),
                'totalFloat': f, 'relationshipType': p.get('relType', 'FS'),
                'lagDays': p.get('lagDays', 0),
            }
    return best


def _milestone_risk_level(variance_days: Optional[float], total_float: Optional[float],
                           movement_days: Optional[float]) -> str:
    if variance_days is not None and variance_days > 14:
        return 'Critical'
    if total_float is not None and total_float < 0:
        return 'Critical'
    if variance_days is not None and variance_days > 3:
        return 'At Risk'
    if movement_days is not None and movement_days > 5:
        return 'At Risk'
    if total_float is not None and 0 <= total_float <= 10:
        return 'Watch'
    if movement_days is not None and movement_days > 0:
        return 'Watch'
    return 'Healthy'


def build_milestone_report(
    activities: List[dict],
    baseline_activities: Optional[List[dict]] = None,
    comparison_result: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    milestones = detect_milestones(activities)
    act_by_id = {_act_id(a): a for a in activities}
    baseline_by_id = {_act_id(a): a for a in (baseline_activities or [])}
    id_map = _id_to_code_name(activities)

    movement_by_id: Dict[str, dict] = {}
    if comparison_result:
        for m in comparison_result.get('milestoneMovement', []):
            movement_by_id[m['activityId']] = m

    report = []
    for m in milestones:
        aid = _act_id(m)
        current_finish = _parse_date(m.get('bFinish') or m.get('finish'))

        bl = baseline_by_id.get(aid)
        baseline_finish = _parse_date(bl.get('bFinish')) if bl else None
        variance_days = (current_finish - baseline_finish).days if (current_finish and baseline_finish) else None

        pct = m.get('pctComplete') or 0
        status = 'Complete' if pct >= 100 else ('In Progress' if m.get('start') else 'Not Started')

        movement = movement_by_id.get(aid)
        movement_days = movement['deltaDays'] if movement else None

        tf = _tf(m)
        report.append({
            'activityId': aid,
            'activityName': m.get('name', ''),
            'wbs': m.get('wbs', ''),
            'currentFinish': current_finish.isoformat() if current_finish else None,
            'baselineFinish': baseline_finish.isoformat() if baseline_finish else None,
            'varianceDays': variance_days,
            'totalFloat': tf,
            'status': status,
            'isCritical': bool(m.get('isCritical')),
            'riskLevel': _milestone_risk_level(variance_days, tf, movement_days),
            'drivingPredecessor': _driving_predecessor(m, act_by_id, id_map),
            'previousUpdateFinish': movement['previousFinish'] if movement else None,
            'movementSinceLastUpdateDays': movement_days,
        })

    report.sort(key=lambda r: (r['currentFinish'] is None, r['currentFinish'] or ''))
    return report


def upcoming_within_horizon(report: List[dict], data_date: date, horizon_days: float) -> List[dict]:
    """Milestones (not yet complete) forecast to finish within `horizon_days`
    of the data date. horizon_days is caller-supplied — never a hardcoded
    industry default."""
    out = []
    for m in report:
        if m['status'] == 'Complete':
            continue
        cf = _parse_date(m['currentFinish'])
        if not cf:
            continue
        delta = (cf - data_date).days
        if 0 <= delta <= horizon_days:
            out.append(m)
    return out
