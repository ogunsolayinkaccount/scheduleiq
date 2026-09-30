"""
Update-to-Update Intelligence & Critical/Driving Path Movement — ScheduleIQ

Pure, DB-free engine (same pattern as baseline_progress.py / schedule_risk.py)
answering "What changed since the last Data Date?" between two REAL schedule
versions of the same project — PREVIOUS and CURRENT — each evaluated using
its OWN effective Data Date, never today.

Reuses rather than duplicates:
  * schedule_comparison.py's compare_relationships() for logic changes
    (predecessor/successor add/remove/type/lag) — Phase F below only adds
    task_id -> Activity ID/Name resolution on top of it (already fixed in
    schedule_comparison.py itself) and groups the result by activity.
  * The established `_current_start()`/`_current_finish()` fallback chain
    convention (finish -> earlyFinish -> remainFinish -> lateFinish ->
    bStart/bFinish as a last resort) already used by narrative_engine.py,
    schedule_comparison.py and baseline_progress.py — kept as a small local
    copy per this codebase's sibling-engine convention rather than
    importing a private helper.
  * baseline_progress.py's build_lookahead() for Phase Q (Look-Ahead
    change) — called once per version, never re-implemented.

Golden rule shared with every other engine in this app: never fabricate a
number. A reliability percentage with a zero denominator is None
("Unavailable"), not 0% or 100%. A critical-path statement about forecast
delay is only produced when every input it sums is actually present.

Terminology: where ScheduleIQ is tracing relationships from imported P6
data (critical/driving-path membership, logic changes) rather than
recalculating CPM itself, results are labeled "Critical/Driving Path
Trace" — never described as a CPM recalculation. Total Float, Early/Late/
Remaining dates, and the imported `onLongestPath` (P6 driving_path_flag)
flag are used as-is; nothing here recomputes them.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from .calendar_engine import CalendarDefinition, working_days_between
from .schedule_comparison import compare_relationships

ENGINE_VERSION = '1.0.0'

GROUP_FIELDS = ('wbs', 'area', 'discipline', 'contractor', 'system')
NEAR_TERM_MILESTONE_DAYS = 14
FLOAT_DETERIORATION_THRESHOLD = -3.0   # matches schedule_comparison._severity_for's 'medium' cutoff
NEAR_CRITICAL_FLOAT_DAYS = 10.0


# ── Shared local helpers (small copies — see module docstring) ─────────────

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


def _current_finish(a: dict) -> Optional[date]:
    for k in ('finish', 'earlyFinish', 'remainFinish', 'lateFinish', 'bFinish'):
        d = _parse_date(a.get(k))
        if d:
            return d
    return None


def _current_start(a: dict) -> Optional[date]:
    for k in ('start', 'earlyStart', 'remainStart', 'lateStart', 'bStart'):
        d = _parse_date(a.get(k))
        if d:
            return d
    return None


def _num(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# Canonical rule, centralized (this file's own copy already matched it -
# behavior unchanged, now a single source of truth instead of a duplicate).
from .activity_analysis import is_activity_complete as _is_complete


def _activity_status(a: dict) -> str:
    if _is_complete(a):
        return 'COMPLETE'
    if a.get('start'):
        return 'IN_PROGRESS'
    return 'NOT_STARTED'


def _on_driving_path(a: dict) -> Optional[bool]:
    """Prefers P6's own imported driving_path_flag (onLongestPath) when the
    source actually carries it; falls back to the established isCritical
    (totalFloat<=0) convention otherwise. Returns None only when neither
    signal is determinable (no totalFloat at all)."""
    if 'onLongestPath' in a:
        return bool(a.get('onLongestPath'))
    if a.get('isCritical') is not None:
        return bool(a.get('isCritical'))
    tf = _num(a.get('totalFloat'))
    return (tf <= 0) if tf is not None else None


def _calendar_for(a: dict, calendars: Optional[Dict[str, CalendarDefinition]]) -> Optional[CalendarDefinition]:
    if not calendars:
        return None
    cal_id = a.get('calendarId')
    return calendars.get(cal_id) if cal_id else None


def _variance(base: Optional[date], curr: Optional[date], cal: Optional[CalendarDefinition]) -> Tuple[Optional[int], Optional[int]]:
    if base is None or curr is None:
        return None, None
    calendar_days = (curr - base).days
    working_days = working_days_between(base, curr, cal) if cal else None
    return calendar_days, working_days


def _group_activities(rows: List[dict], group_by: str) -> Dict[str, List[dict]]:
    if group_by not in GROUP_FIELDS:
        return {}
    groups: Dict[str, List[dict]] = {}
    for r in rows:
        key = (r.get(group_by) or '').strip() or 'Unassigned'
        groups.setdefault(key, []).append(r)
    return groups


# ── Phase B: Activity movement classification ──────────────────────────────

def build_activity_movement(
    previous_activities: List[dict], current_activities: List[dict],
    calendars: Optional[Dict[str, CalendarDefinition]] = None, noise_threshold_days: int = 0,
) -> List[Dict[str, Any]]:
    """One row per activity (matched, new, or removed) carrying every field
    Phase B asks for, plus a `flags` list — an activity legitimately carries
    more than one flag (e.g. STARTED_THIS_PERIOD + SLIPPED), so this is
    never forced into a single mutually-exclusive bucket."""
    prev_by_id = {_act_id(a): a for a in previous_activities if _act_id(a)}
    curr_by_id = {_act_id(a): a for a in current_activities if _act_id(a)}
    all_ids = set(prev_by_id) | set(curr_by_id)

    rows: List[Dict[str, Any]] = []
    for aid in all_ids:
        p, c = prev_by_id.get(aid), curr_by_id.get(aid)
        primary = c or p
        flags: List[str] = []

        if p is None:
            flags.append('NEW')
            match_status = 'NEW'
        elif c is None:
            flags.append('REMOVED')
            match_status = 'REMOVED'
        else:
            match_status = 'MATCHED'

        prev_start = _current_start(p) if p else None
        curr_start = _current_start(c) if c else None
        prev_finish = _current_finish(p) if p else None
        curr_finish = _current_finish(c) if c else None
        prev_remain_dur = _num(p.get('remainDur')) if p else None
        curr_remain_dur = _num(c.get('remainDur')) if c else None
        prev_pct = _num(p.get('pctComplete')) if p else None
        curr_pct = _num(c.get('pctComplete')) if c else None
        prev_tf = _num(p.get('totalFloat')) if p else None
        curr_tf = _num(c.get('totalFloat')) if c else None
        prev_actual_start = _parse_date(p.get('start')) if p else None
        curr_actual_start = _parse_date(c.get('start')) if c else None
        prev_actual_finish = _parse_date(p.get('finish')) if p else None
        curr_actual_finish = _parse_date(c.get('finish')) if c else None

        cal = _calendar_for(c or p or {}, calendars)
        start_move_days, start_move_wd = _variance(prev_start, curr_start, cal)
        finish_move_days, finish_move_wd = _variance(prev_finish, curr_finish, cal)

        if match_status == 'MATCHED':
            if finish_move_days is not None:
                if finish_move_days > noise_threshold_days:
                    flags.append('SLIPPED')
                elif finish_move_days < -noise_threshold_days:
                    flags.append('IMPROVED')
                else:
                    flags.append('UNCHANGED')
            if not prev_actual_start and curr_actual_start:
                flags.append('STARTED_THIS_PERIOD')
            if not prev_actual_finish and curr_actual_finish:
                flags.append('COMPLETED_THIS_PERIOD')

        rows.append({
            'activityId': aid,
            'activityName': primary.get('name', '') if primary else '',
            'wbs': primary.get('wbs', '') if primary else '',
            'area': primary.get('area', '') if primary else '',
            'discipline': primary.get('discipline', '') if primary else '',
            'contractor': primary.get('contractor', '') if primary else '',
            'system': primary.get('system', '') if primary else '',
            'isMilestone': bool(primary.get('isMilestone')) if primary else False,
            'matchStatus': match_status,
            'flags': flags,

            'previousStart': prev_start.isoformat() if prev_start else None,
            'currentStart': curr_start.isoformat() if curr_start else None,
            'startMovementDays': start_move_days,
            'startMovementWorkingDays': start_move_wd,
            'previousFinish': prev_finish.isoformat() if prev_finish else None,
            'currentFinish': curr_finish.isoformat() if curr_finish else None,
            'finishMovementDays': finish_move_days,
            'finishMovementWorkingDays': finish_move_wd,
            'workingDayCalendarAvailable': cal is not None,

            'previousRemainingDuration': prev_remain_dur,
            'currentRemainingDuration': curr_remain_dur,
            'durationMovement': (round(curr_remain_dur - prev_remain_dur, 2) if (prev_remain_dur is not None and curr_remain_dur is not None) else None),

            'previousPctComplete': prev_pct,
            'currentPctComplete': curr_pct,

            'previousTotalFloat': prev_tf,
            'currentTotalFloat': curr_tf,
            'floatMovement': (round(curr_tf - prev_tf, 2) if (prev_tf is not None and curr_tf is not None) else None),

            'previousStatus': _activity_status(p) if p else None,
            'currentStatus': _activity_status(c) if c else None,
        })

    rows.sort(key=lambda r: (r['currentFinish'] or r['previousFinish'] or '9999-99-99', r['activityId']))
    return rows


# ── Phase C: Forecast commitment ────────────────────────────────────────────

def compute_forecast_commitment(
    movement_rows: List[Dict[str, Any]], previous_activities: List[dict], current_activities: List[dict],
    previous_data_date: Optional[date], current_data_date: Optional[date],
) -> Dict[str, Any]:
    """Uses the PREVIOUS schedule's own forecast (as of previous_data_date)
    to determine what it said would happen during [previous_data_date,
    current_data_date], then checks CURRENT's actual status — never
    today. Unavailable (empty) when either Data Date is missing, per the
    Data Date governance rule: no substitute boundary is invented."""
    if not previous_data_date or not current_data_date:
        return {
            'available': False,
            'reason': 'Both the previous and current effective Data Dates are required to evaluate forecast commitment.',
            'startCommitments': [], 'finishCommitments': [],
        }

    prev_by_id = {_act_id(a): a for a in previous_activities if _act_id(a)}
    curr_by_id = {_act_id(a): a for a in current_activities if _act_id(a)}

    start_commitments: List[Dict[str, Any]] = []
    finish_commitments: List[Dict[str, Any]] = []

    for row in movement_rows:
        if row['matchStatus'] != 'MATCHED':
            continue
        aid = row['activityId']
        p, c = prev_by_id[aid], curr_by_id[aid]
        if _is_complete(p):
            continue   # already done as of the previous update — nothing was "committed" this period

        prev_forecast_start = _current_start(p)
        if prev_forecast_start and previous_data_date <= prev_forecast_start <= current_data_date:
            met = bool(c.get('start'))
            start_commitments.append({
                'activityId': aid, 'activityName': row['activityName'], 'wbs': row['wbs'],
                'previousForecastStart': prev_forecast_start.isoformat(),
                'actualStart': c.get('start'),
                'status': 'FORECAST_START_MET' if met else 'FORECAST_START_MISSED',
            })

        prev_forecast_finish = _current_finish(p)
        if prev_forecast_finish and previous_data_date <= prev_forecast_finish <= current_data_date:
            met = bool(c.get('finish')) or (_num(c.get('pctComplete')) or 0) >= 100
            finish_commitments.append({
                'activityId': aid, 'activityName': row['activityName'], 'wbs': row['wbs'],
                'previousForecastFinish': prev_forecast_finish.isoformat(),
                'actualFinish': c.get('finish'),
                'status': 'FORECAST_FINISH_MET' if met else 'FORECAST_FINISH_MISSED',
            })

    return {'available': True, 'startCommitments': start_commitments, 'finishCommitments': finish_commitments}


# ── Phase D: Update performance / plan reliability ──────────────────────────

def compute_reliability(commitment: Dict[str, Any]) -> Dict[str, Any]:
    if not commitment.get('available'):
        return {
            'available': False, 'reason': commitment.get('reason'),
            'plannedStarts': None, 'actualStarts': None, 'startReliabilityPct': None,
            'plannedFinishes': None, 'actualFinishes': None, 'finishReliabilityPct': None,
            'missedStarts': [], 'missedFinishes': [],
        }

    starts = commitment['startCommitments']
    finishes = commitment['finishCommitments']
    planned_starts = len(starts)
    actual_starts = sum(1 for s in starts if s['status'] == 'FORECAST_START_MET')
    planned_finishes = len(finishes)
    actual_finishes = sum(1 for f in finishes if f['status'] == 'FORECAST_FINISH_MET')

    return {
        'available': True,
        'plannedStarts': planned_starts, 'actualStarts': actual_starts,
        'startReliabilityPct': (round(actual_starts / planned_starts * 100, 1) if planned_starts else None),
        'plannedFinishes': planned_finishes, 'actualFinishes': actual_finishes,
        'finishReliabilityPct': (round(actual_finishes / planned_finishes * 100, 1) if planned_finishes else None),
        'missedStarts': [s for s in starts if s['status'] == 'FORECAST_START_MISSED'],
        'missedFinishes': [f for f in finishes if f['status'] == 'FORECAST_FINISH_MISSED'],
    }


# ── Phase E: Float movement ──────────────────────────────────────────────────

def compute_float_movement(movement_rows: List[Dict[str, Any]], previous_activities: List[dict], current_activities: List[dict]) -> Dict[str, Any]:
    prev_by_id = {_act_id(a): a for a in previous_activities if _act_id(a)}
    curr_by_id = {_act_id(a): a for a in current_activities if _act_id(a)}

    improved, deteriorated, unchanged = [], [], []
    newly_negative, recovered = [], []
    newly_critical, left_critical = [], []

    for row in movement_rows:
        if row['matchStatus'] != 'MATCHED':
            continue
        p_tf, c_tf = row['previousTotalFloat'], row['currentTotalFloat']
        entry = {'activityId': row['activityId'], 'activityName': row['activityName'], 'wbs': row['wbs'],
                  'previousTotalFloat': p_tf, 'currentTotalFloat': c_tf, 'floatMovement': row['floatMovement']}
        if p_tf is not None and c_tf is not None:
            if row['floatMovement'] > 0:
                improved.append(entry)
            elif row['floatMovement'] < 0:
                deteriorated.append(entry)
            else:
                unchanged.append(entry)
            if c_tf < 0 and p_tf >= 0:
                newly_negative.append(entry)
            elif c_tf >= 0 and p_tf < 0:
                recovered.append(entry)

        p, c = prev_by_id[row['activityId']], curr_by_id[row['activityId']]
        p_crit, c_crit = _on_driving_path(p), _on_driving_path(c)
        if p_crit is not None and c_crit is not None:
            if c_crit and not p_crit:
                newly_critical.append({'activityId': row['activityId'], 'activityName': row['activityName'], 'wbs': row['wbs']})
            elif p_crit and not c_crit:
                left_critical.append({'activityId': row['activityId'], 'activityName': row['activityName'], 'wbs': row['wbs']})

    deteriorated.sort(key=lambda e: e['floatMovement'])
    improved.sort(key=lambda e: -e['floatMovement'])

    return {
        'improved': improved, 'deteriorated': deteriorated, 'unchanged': unchanged,
        'improvedCount': len(improved), 'deterioratedCount': len(deteriorated), 'unchangedCount': len(unchanged),
        'newlyNegativeFloat': newly_negative, 'newlyNegativeFloatCount': len(newly_negative),
        'recoveredFromNegativeFloat': recovered, 'recoveredFromNegativeFloatCount': len(recovered),
        'newlyCritical': newly_critical, 'newlyCriticalCount': len(newly_critical),
        'leftCriticalPath': left_critical, 'leftCriticalPathCount': len(left_critical),
    }


# ── Phase F: Logic (relationship) changes ───────────────────────────────────

def compute_logic_changes(previous_activities: List[dict], current_activities: List[dict]) -> Dict[str, Any]:
    """Thin wrapper around schedule_comparison.compare_relationships() — no
    relationship-matching logic is duplicated here. Adds Activity Name
    resolution (compare_relationships already resolves task_id -> Activity
    ID) and a per-activity grouping for display."""
    result = compare_relationships(previous_activities, current_activities)
    name_by_id: Dict[str, str] = {}
    for acts in (previous_activities, current_activities):
        for a in acts:
            aid = _act_id(a)
            if aid and aid not in name_by_id:
                name_by_id[aid] = a.get('name') or ''

    def _label(entry: Dict[str, Any]) -> str:
        pred_name = name_by_id.get(entry['predecessorId'], '')
        succ_name = name_by_id.get(entry['successorId'], '')
        return f"{entry['predecessorId']} ({pred_name}) -> {entry['successorId']} ({succ_name})"

    by_activity: Dict[str, List[Dict[str, Any]]] = {}
    for kind, entries in (('added', result['added']), ('removed', result['removed']), ('changed', result['changed'])):
        for e in entries:
            e = dict(e, changeType=kind, label=_label(e))
            by_activity.setdefault(e['successorId'], []).append(e)
            by_activity.setdefault(e['predecessorId'], []).append(e)

    return {**result, 'byActivity': by_activity}


# ── Phase G: Constraint changes ─────────────────────────────────────────────

def compute_constraint_changes(previous_activities: List[dict], current_activities: List[dict]) -> List[Dict[str, Any]]:
    prev_by_id = {_act_id(a): a for a in previous_activities if _act_id(a)}
    changes = []
    for c in current_activities:
        aid = _act_id(c)
        p = prev_by_id.get(aid)
        if not p:
            continue
        for type_key, date_key, label in (('constraintType', 'constraintDate', 'Primary'), ('constraint2Type', 'constraint2Date', 'Secondary')):
            pt, ct = p.get(type_key) or None, c.get(type_key) or None
            pd_, cd_ = p.get(date_key), c.get(date_key)
            if pt == ct and pd_ == cd_:
                continue
            if not pt and ct:
                kind, text = 'ADDED', f'{label} constraint added: {ct}' + (f' ({cd_})' if cd_ else '')
            elif pt and not ct:
                kind, text = 'REMOVED', f'{label} constraint removed: was {pt}' + (f' ({pd_})' if pd_ else '')
            elif pt != ct:
                kind, text = 'TYPE_CHANGED', f'{label} constraint type changed: {pt} -> {ct}'
            else:
                kind, text = 'DATE_CHANGED', f'{label} constraint ({ct}) date moved: {pd_} -> {cd_}'
            changes.append({
                'activityId': aid, 'activityName': c.get('name', ''), 'wbs': c.get('wbs', ''),
                'constraintSlot': label, 'changeType': kind,
                'previousType': pt, 'currentType': ct, 'previousDate': pd_, 'currentDate': cd_,
                'description': text,
            })
    return changes


# ── Phase H: Duration changes ────────────────────────────────────────────────

def compute_duration_changes(previous_activities: List[dict], current_activities: List[dict], material_threshold: float = 1.0) -> Dict[str, Any]:
    prev_by_id = {_act_id(a): a for a in previous_activities if _act_id(a)}
    original_changes, remaining_changes = [], []
    for c in current_activities:
        aid = _act_id(c)
        p = prev_by_id.get(aid)
        if not p:
            continue
        p_dur, c_dur = _num(p.get('dur')), _num(c.get('dur'))
        if p_dur is not None and c_dur is not None and abs(c_dur - p_dur) >= material_threshold:
            original_changes.append({
                'activityId': aid, 'activityName': c.get('name', ''), 'wbs': c.get('wbs', ''),
                'previousDuration': p_dur, 'currentDuration': c_dur, 'delta': round(c_dur - p_dur, 2),
            })
        p_rd, c_rd = _num(p.get('remainDur')), _num(c.get('remainDur'))
        if p_rd is not None and c_rd is not None and abs(c_rd - p_rd) >= material_threshold:
            remaining_changes.append({
                'activityId': aid, 'activityName': c.get('name', ''), 'wbs': c.get('wbs', ''),
                'previousDuration': p_rd, 'currentDuration': c_rd, 'delta': round(c_rd - p_rd, 2),
            })
    original_changes.sort(key=lambda e: -abs(e['delta']))
    remaining_changes.sort(key=lambda e: -abs(e['delta']))
    return {'originalDurationChanges': original_changes, 'remainingDurationChanges': remaining_changes}


# ── Phase I: Milestone movement ─────────────────────────────────────────────

def compute_milestone_movement(
    previous_activities: List[dict], current_activities: List[dict], current_data_date: Optional[date],
    baseline_activities: Optional[List[dict]] = None, calendars: Optional[Dict[str, CalendarDefinition]] = None,
) -> Dict[str, Any]:
    prev_by_id = {_act_id(a): a for a in previous_activities if a.get('isMilestone')}
    curr_by_id = {_act_id(a): a for a in current_activities if a.get('isMilestone')}
    baseline_by_id = {_act_id(a): a for a in (baseline_activities or []) if a.get('isMilestone')}
    all_ids = set(prev_by_id) | set(curr_by_id)

    rows = []
    for aid in all_ids:
        p, c = prev_by_id.get(aid), curr_by_id.get(aid)
        primary = c or p
        bl = baseline_by_id.get(aid)
        baseline_date = _parse_date(bl.get('bFinish')) if bl else None

        if p is None:
            curr_finish = _current_finish(c)
            rows.append({'activityId': aid, 'activityName': primary.get('name', ''), 'wbs': primary.get('wbs', ''),
                          'classification': 'NEWLY_ADDED', 'previousDate': None,
                          'currentDate': curr_finish.isoformat() if curr_finish else None,
                          'movementDays': None, 'previousTotalFloat': None, 'currentTotalFloat': _num(c.get('totalFloat')),
                          'baselineDate': baseline_date.isoformat() if baseline_date else None,
                          'baselineVarianceDays': (curr_finish - baseline_date).days if (baseline_date and curr_finish) else None})
            continue
        if c is None:
            prev_finish = _current_finish(p)
            rows.append({'activityId': aid, 'activityName': primary.get('name', ''), 'wbs': primary.get('wbs', ''),
                          'classification': 'REMOVED', 'previousDate': prev_finish.isoformat() if prev_finish else None,
                          'currentDate': None, 'movementDays': None,
                          'previousTotalFloat': _num(p.get('totalFloat')), 'currentTotalFloat': None,
                          'baselineDate': baseline_date.isoformat() if baseline_date else None,
                          'baselineVarianceDays': None})
            continue

        prev_date, curr_date = _current_finish(p), _current_finish(c)
        cal = _calendar_for(c, calendars)
        movement_days, movement_wd = _variance(prev_date, curr_date, cal)
        p_tf, c_tf = _num(p.get('totalFloat')), _num(c.get('totalFloat'))

        if not _is_complete(p) and _is_complete(c):
            classification = 'COMPLETED_THIS_PERIOD'
        elif p_tf is not None and c_tf is not None and c_tf < 0 and p_tf >= 0:
            classification = 'NEWLY_NEGATIVE_FLOAT'
        elif movement_days is not None and movement_days > 0:
            classification = 'SLIPPED'
        elif movement_days is not None and movement_days < 0:
            classification = 'IMPROVED'
        else:
            classification = 'UNCHANGED'

        rows.append({
            'activityId': aid, 'activityName': primary.get('name', ''), 'wbs': primary.get('wbs', ''),
            'classification': classification,
            'previousDate': prev_date.isoformat() if prev_date else None,
            'currentDate': curr_date.isoformat() if curr_date else None,
            'movementDays': movement_days, 'movementWorkingDays': movement_wd,
            'workingDayCalendarAvailable': cal is not None,
            'previousTotalFloat': p_tf, 'currentTotalFloat': c_tf,
            'baselineDate': baseline_date.isoformat() if baseline_date else None,
            # Distinct from movementDays (this-update movement) — never mix
            # the two, per the Baseline-vs-Update distinction rule.
            'baselineVarianceDays': (curr_date - baseline_date).days if (baseline_date and curr_date) else None,
        })

    top_slipped = sorted([r for r in rows if r.get('movementDays') and r['movementDays'] > 0], key=lambda r: -r['movementDays'])[:10]
    completed_this_period = [r for r in rows if r['classification'] == 'COMPLETED_THIS_PERIOD']
    at_risk = [r for r in rows if r['classification'] == 'NEWLY_NEGATIVE_FLOAT' or (r.get('currentTotalFloat') is not None and r['currentTotalFloat'] < 0)]
    upcoming = []
    if current_data_date:
        for r in rows:
            cd = _parse_date(r.get('currentDate'))
            if cd and r['classification'] not in ('COMPLETED_THIS_PERIOD', 'REMOVED'):
                days_until = (cd - current_data_date).days
                if 0 <= days_until <= NEAR_TERM_MILESTONE_DAYS:
                    upcoming.append({**r, 'daysUntil': days_until})
    upcoming.sort(key=lambda r: r['daysUntil'])

    return {
        'rows': rows, 'topSlippedMilestones': top_slipped, 'upcomingMilestones': upcoming,
        'milestonesCompletedThisPeriod': completed_this_period, 'milestonesAtRisk': at_risk,
    }


# ── Phase J/K: Critical/Driving path movement ───────────────────────────────

def compute_critical_path_movement(movement_rows: List[Dict[str, Any]], previous_activities: List[dict], current_activities: List[dict]) -> Dict[str, Any]:
    prev_by_id = {_act_id(a): a for a in previous_activities if _act_id(a)}
    curr_by_id = {_act_id(a): a for a in current_activities if _act_id(a)}

    stayed, became, left, slipped, float_deterioration = [], [], [], [], []
    prev_critical_finishes, curr_critical_finishes = [], []
    divergence_candidates = []

    for row in movement_rows:
        if row['matchStatus'] != 'MATCHED':
            continue
        p, c = prev_by_id[row['activityId']], curr_by_id[row['activityId']]
        p_crit, c_crit = _on_driving_path(p), _on_driving_path(c)
        if p_crit is None or c_crit is None:
            continue
        entry = {'activityId': row['activityId'], 'activityName': row['activityName'], 'wbs': row['wbs'],
                  'currentStart': row['currentStart'], 'finishMovementDays': row['finishMovementDays']}

        if p_crit and c_crit:
            stayed.append(entry)
        elif c_crit and not p_crit:
            became.append(entry)
            divergence_candidates.append(row)
        elif p_crit and not c_crit:
            left.append(entry)
            divergence_candidates.append(row)

        if c_crit and row['finishMovementDays'] is not None and row['finishMovementDays'] > 0:
            slipped.append(entry)
        if p_crit and row['previousFinish']:
            prev_critical_finishes.append(row['previousFinish'])
        if c_crit and row['currentFinish']:
            curr_critical_finishes.append(row['currentFinish'])

        c_tf = row['currentTotalFloat']
        if c_tf is not None and 0 <= c_tf <= NEAR_CRITICAL_FLOAT_DAYS and row['floatMovement'] is not None and row['floatMovement'] <= FLOAT_DETERIORATION_THRESHOLD:
            float_deterioration.append({**entry, 'floatMovement': row['floatMovement'], 'currentTotalFloat': c_tf})

    # "The current critical path gained N days of forecast delay" must mean
    # the driving path's own endpoint moved — the LATEST finish among
    # activities on it in each version — never a sum of every individual
    # critical activity's movement (which double-counts correlated
    # slippage along one chain and conflates unrelated parallel paths).
    forecast_delay_days = None
    if prev_critical_finishes and curr_critical_finishes:
        forecast_delay_days = (_parse_date(max(curr_critical_finishes)) - _parse_date(max(prev_critical_finishes))).days

    divergence_candidates = [r for r in divergence_candidates if r.get('currentStart')]
    divergence_candidates.sort(key=lambda r: (r['currentStart'], r['activityId']))
    divergence_point = None
    if divergence_candidates:
        d = divergence_candidates[0]
        divergence_point = {
            'activityId': d['activityId'], 'activityName': d['activityName'], 'currentStart': d['currentStart'],
            'note': 'Earliest-starting activity whose critical/driving-path membership changed between the two versions — an indicator of where the paths first diverge, not a proven root cause.',
        }

    return {
        'methodologyNote': 'Critical/Driving Path Trace — based on each version\'s own imported P6 critical/driving-path status (driving_path_flag when available, otherwise Total Float <= 0), not a ScheduleIQ CPM recalculation.',
        'stayedCritical': stayed, 'stayedCriticalCount': len(stayed),
        'becameCritical': became, 'becameCriticalCount': len(became),
        'leftCriticalPath': left, 'leftCriticalPathCount': len(left),
        'criticalActivitySlipped': slipped, 'criticalActivitySlippedCount': len(slipped),
        'criticalFloatDeterioration': float_deterioration, 'criticalFloatDeteriorationCount': len(float_deterioration),
        'criticalPathForecastDelayDays': forecast_delay_days,
        'pathDivergence': divergence_point,
    }


def build_critical_path_shift_summary(cp_result: Dict[str, Any]) -> List[str]:
    """Phase K — deterministic sentences, only emitted when the underlying
    count/figure is actually present."""
    sentences = []
    if cp_result['stayedCriticalCount']:
        sentences.append(f"{cp_result['stayedCriticalCount']} activities remained critical.")
    if cp_result['becameCriticalCount']:
        sentences.append(f"{cp_result['becameCriticalCount']} activities became newly critical.")
    if cp_result['leftCriticalPathCount']:
        sentences.append(f"{cp_result['leftCriticalPathCount']} activities left the critical path.")
    if cp_result['criticalPathForecastDelayDays'] is not None and cp_result['criticalPathForecastDelayDays'] > 0:
        sentences.append(f"The current critical path gained {cp_result['criticalPathForecastDelayDays']} days of forecast delay.")
    if cp_result['pathDivergence']:
        dp = cp_result['pathDivergence']
        sentences.append(f"Path divergence begins at Activity {dp['activityId']} — {dp['activityName']}.")
    return sentences


# ── Phase L: Update driver analysis ──────────────────────────────────────────

def rank_deterioration_drivers(movement_rows: List[Dict[str, Any]], float_result: Dict[str, Any], commitment: Dict[str, Any], group_by: str = 'discipline') -> Dict[str, Any]:
    if group_by not in GROUP_FIELDS:
        return {'error': f'Unknown group_by: {group_by}. Valid: {GROUP_FIELDS}', 'drivers': []}

    slipped_rows = [r for r in movement_rows if 'SLIPPED' in r['flags']]
    groups = _group_activities(slipped_rows, group_by)
    all_rows_by_group = _group_activities(movement_rows, group_by)

    missed_start_by_group: Dict[str, int] = {}
    for s in (commitment.get('missedStarts') or []):
        key = s.get(group_by) or 'Unassigned'
        missed_start_by_group[key] = missed_start_by_group.get(key, 0) + 1
    missed_finish_by_group: Dict[str, int] = {}
    for f in (commitment.get('missedFinishes') or []):
        key = f.get(group_by) or 'Unassigned'
        missed_finish_by_group[key] = missed_finish_by_group.get(key, 0) + 1

    newly_neg_by_group: Dict[str, int] = {}
    for e in float_result.get('newlyNegativeFloat', []):
        key = e.get(group_by) or 'Unassigned'
        newly_neg_by_group[key] = newly_neg_by_group.get(key, 0) + 1
    newly_crit_by_group: Dict[str, int] = {}
    for e in float_result.get('newlyCritical', []):
        key = e.get(group_by) or 'Unassigned'
        newly_crit_by_group[key] = newly_crit_by_group.get(key, 0) + 1

    drivers = []
    all_groups = set(groups) | set(newly_neg_by_group) | set(newly_crit_by_group) | set(missed_start_by_group) | set(missed_finish_by_group)
    for key in all_groups:
        slipped_in_group = groups.get(key, [])
        cumulative_days = sum(r['finishMovementDays'] for r in slipped_in_group if r['finishMovementDays'] is not None)
        drivers.append({
            'group': key,
            'slippedCount': len(slipped_in_group),
            'cumulativeFinishMovementDays': cumulative_days,
            'averageFinishMovementDays': (round(cumulative_days / len(slipped_in_group), 1) if slipped_in_group else None),
            'newlyNegativeFloatCount': newly_neg_by_group.get(key, 0),
            'newlyCriticalCount': newly_crit_by_group.get(key, 0),
            'missedForecastStartsCount': missed_start_by_group.get(key, 0),
            'missedForecastFinishesCount': missed_finish_by_group.get(key, 0),
            'totalActivityCount': len(all_rows_by_group.get(key, [])),
        })
    drivers.sort(key=lambda d: (-d['slippedCount'], -d['cumulativeFinishMovementDays']))

    return {
        'groupBy': group_by, 'drivers': drivers,
        'methodologyNote': (
            'cumulativeFinishMovementDays is an aggregate movement indicator — the sum of individual '
            'activity finish-date movements within the group — not a claim about overall project completion delay.'
        ),
    }


# ── Phase O: Deterministic update narrative ─────────────────────────────────

def build_update_narrative(
    movement_rows: List[Dict[str, Any]], reliability: Dict[str, Any], cp_result: Dict[str, Any],
    milestone_result: Dict[str, Any], driver_result: Dict[str, Any],
) -> Dict[str, str]:
    slipped = sum(1 for r in movement_rows if 'SLIPPED' in r['flags'])
    improved = sum(1 for r in movement_rows if 'IMPROVED' in r['flags'])
    started = sum(1 for r in movement_rows if 'STARTED_THIS_PERIOD' in r['flags'])
    completed = sum(1 for r in movement_rows if 'COMPLETED_THIS_PERIOD' in r['flags'])
    new_count = sum(1 for r in movement_rows if r['matchStatus'] == 'NEW')
    removed_count = sum(1 for r in movement_rows if r['matchStatus'] == 'REMOVED')

    overview = (
        f"{slipped} activit{'y' if slipped == 1 else 'ies'} slipped, {improved} improved, "
        f"{started} started and {completed} completed this update."
    )
    if new_count or removed_count:
        overview += f" {new_count} activities were added and {removed_count} were removed from the schedule."

    if reliability.get('available') and reliability.get('plannedStarts'):
        reliability_txt = (
            f"The previous schedule forecast {reliability['plannedStarts']} start"
            f"{'s' if reliability['plannedStarts'] != 1 else ''} during the period. "
            f"{reliability['actualStarts']} occurred, producing {reliability['startReliabilityPct']}% start reliability."
        )
        if reliability['plannedStarts'] - reliability['actualStarts'] > 0:
            reliability_txt += f" {reliability['plannedStarts'] - reliability['actualStarts']} forecast starts were missed."
    else:
        reliability_txt = 'No activities were forecast to start during this update period, so start reliability is unavailable.'

    cp_parts = []
    if cp_result['becameCriticalCount'] or cp_result['leftCriticalPathCount']:
        cp_parts.append(f"{cp_result['becameCriticalCount']} activities became newly critical while {cp_result['leftCriticalPathCount']} left the critical path.")
    if cp_result['criticalActivitySlippedCount']:
        cp_parts.append(f"{cp_result['criticalActivitySlippedCount']} current critical activities experienced additional finish deterioration.")
    critical_txt = ' '.join(cp_parts) if cp_parts else 'No material change in critical-path membership was detected this update.'

    ms_parts = []
    slipped_ms = [r for r in milestone_result['rows'] if r['classification'] == 'SLIPPED']
    if slipped_ms:
        worst = max(slipped_ms, key=lambda r: r['movementDays'])
        ms_parts.append(f"{len(slipped_ms)} milestone{'s' if len(slipped_ms) != 1 else ''} slipped this update.")
        ms_parts.append(f"The largest movement was {worst['activityName']}, which moved {worst['movementDays']} calendar days.")
    milestone_txt = ' '.join(ms_parts) if ms_parts else 'No milestones slipped this update.'

    driver_txt = 'Insufficient slipped-activity data to identify a primary driver.'
    if driver_result.get('drivers'):
        top = driver_result['drivers'][0]
        if top['slippedCount'] > 0:
            driver_txt = (
                f"{driver_result['groupBy'].title()} {top['group']} contains the largest concentration of "
                f"finish deterioration with {top['slippedCount']} slipped activities."
            )

    return {
        'overview': overview, 'executionReliability': reliability_txt,
        'criticalPath': critical_txt, 'milestones': milestone_txt, 'primaryDriver': driver_txt,
    }


# ── Top-level orchestrator ───────────────────────────────────────────────────

def compute_lookahead_change(
    baseline_activities: List[dict], previous_activities: List[dict], current_activities: List[dict],
    previous_data_date: Optional[date], current_data_date: Optional[date],
    calendars: Optional[Dict[str, CalendarDefinition]] = None, weeks: int = 4,
) -> Dict[str, Any]:
    """Phase Q — reuses baseline_progress.build_lookahead() twice (once per
    version, against the SAME baseline and window size), never
    re-implementing look-ahead windowing. Diffs the two windowed row sets
    by Activity ID to answer 'what changed in the plan since last update'."""
    from . import baseline_progress

    if not previous_data_date or not current_data_date:
        return {'available': False, 'reason': 'Both effective Data Dates are required to compare Look-Ahead windows.'}

    prev_la = baseline_progress.build_lookahead(baseline_activities, previous_activities, previous_data_date, calendars, weeks=weeks)
    curr_la = baseline_progress.build_lookahead(baseline_activities, current_activities, current_data_date, calendars, weeks=weeks)

    prev_ids = {r['activityId'] for r in prev_la['rows']}
    curr_by_id = {r['activityId']: r for r in curr_la['rows']}
    curr_ids = set(curr_by_id)

    carryover = [curr_by_id[aid] for aid in (prev_ids & curr_ids)]
    newly_entering = [curr_by_id[aid] for aid in (curr_ids - prev_ids)]

    prev_by_id = {r['activityId']: r for r in prev_la['rows']}
    pushed_out = [
        prev_by_id[aid] for aid in (prev_ids - curr_ids)
        if prev_by_id[aid]['status'] not in ('COMPLETE',)  # finishing and rolling off the window isn't "pushed out"
    ]

    newly_critical_near_term = [r for r in carryover + newly_entering if r.get('isCritical')]

    return {
        'available': True,
        'previousWindow': prev_la['window'], 'currentWindow': curr_la['window'],
        'carryover': carryover, 'carryoverCount': len(carryover),
        'newlyEntering': newly_entering, 'newlyEnteringCount': len(newly_entering),
        'pushedOut': pushed_out, 'pushedOutCount': len(pushed_out),
        'newlyCriticalNearTerm': newly_critical_near_term, 'newlyCriticalNearTermCount': len(newly_critical_near_term),
    }


def build_update_intelligence(
    previous_activities: List[dict], current_activities: List[dict],
    previous_data_date: Optional[date], current_data_date: Optional[date],
    baseline_activities: Optional[List[dict]] = None, calendars: Optional[Dict[str, CalendarDefinition]] = None,
    group_by: str = 'discipline', lookahead_weeks: int = 4,
) -> Dict[str, Any]:
    movement_rows = build_activity_movement(previous_activities, current_activities, calendars)
    commitment = compute_forecast_commitment(movement_rows, previous_activities, current_activities, previous_data_date, current_data_date)
    reliability = compute_reliability(commitment)
    float_result = compute_float_movement(movement_rows, previous_activities, current_activities)
    logic_result = compute_logic_changes(previous_activities, current_activities)
    constraint_result = compute_constraint_changes(previous_activities, current_activities)
    duration_result = compute_duration_changes(previous_activities, current_activities)
    milestone_result = compute_milestone_movement(previous_activities, current_activities, current_data_date, baseline_activities, calendars)
    cp_result = compute_critical_path_movement(movement_rows, previous_activities, current_activities)
    cp_summary = build_critical_path_shift_summary(cp_result)
    driver_result = rank_deterioration_drivers(movement_rows, float_result, commitment, group_by)
    narrative = build_update_narrative(movement_rows, reliability, cp_result, milestone_result, driver_result)
    lookahead_change = compute_lookahead_change(
        baseline_activities or [], previous_activities, current_activities,
        previous_data_date, current_data_date, calendars, lookahead_weeks,
    )

    period_days = (current_data_date - previous_data_date).days if (previous_data_date and current_data_date) else None

    # Baseline context (Phase P) — attach per-row when a baseline is supplied,
    # kept clearly distinct from this-update movement.
    if baseline_activities:
        baseline_by_id = {_act_id(a): a for a in baseline_activities if _act_id(a)}
        for row in movement_rows:
            bl = baseline_by_id.get(row['activityId'])
            bl_finish = _parse_date(bl.get('bFinish')) if bl else None
            curr_finish = _parse_date(row['currentFinish'])
            row['baselineFinish'] = bl_finish.isoformat() if bl_finish else None
            row['baselineVarianceDays'] = (curr_finish - bl_finish).days if (bl_finish and curr_finish) else None

    return {
        'engineVersion': ENGINE_VERSION,
        'previousDataDate': previous_data_date.isoformat() if previous_data_date else None,
        'currentDataDate': current_data_date.isoformat() if current_data_date else None,
        'periodDays': period_days,
        'movementRows': movement_rows,
        'movementCounts': {
            'slipped': sum(1 for r in movement_rows if 'SLIPPED' in r['flags']),
            'improved': sum(1 for r in movement_rows if 'IMPROVED' in r['flags']),
            'unchanged': sum(1 for r in movement_rows if 'UNCHANGED' in r['flags']),
            'new': sum(1 for r in movement_rows if r['matchStatus'] == 'NEW'),
            'removed': sum(1 for r in movement_rows if r['matchStatus'] == 'REMOVED'),
            'startedThisPeriod': sum(1 for r in movement_rows if 'STARTED_THIS_PERIOD' in r['flags']),
            'completedThisPeriod': sum(1 for r in movement_rows if 'COMPLETED_THIS_PERIOD' in r['flags']),
        },
        'forecastCommitment': commitment,
        'reliability': reliability,
        'floatMovement': float_result,
        'logicChanges': logic_result,
        'constraintChanges': constraint_result,
        'durationChanges': duration_result,
        'milestoneMovement': milestone_result,
        'criticalPathMovement': cp_result,
        'criticalPathShiftSummary': cp_summary,
        'driverAnalysis': driver_result,
        'narrative': narrative,
        'hasBaseline': bool(baseline_activities),
        'lookaheadChange': lookahead_change,
    }
