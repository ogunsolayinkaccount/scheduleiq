"""
Recovery Scenario Engine — ScheduleIQ

Scenario planning, not schedule editing: every function here works on a
deep copy of the activity list and NEVER touches the stored ScheduleUpload.

Two classes of assumption:
  - CALCULABLE — reduce_duration, remove_lag/reduce_lag, change_relationship_type,
    add_relationship, remove_relationship. These are applied to the relationship
    network, then a real forward/backward-pass CPM (the same algorithm as
    frontend/cpm.ts, ported here since the Recovery Planner is a backend
    service) recomputes early/late dates, float, and the scenario's critical
    path. This IS a genuine CPM recalculation — unlike the Critical Path
    tab's relationship trace, which explicitly is not one. It is always
    labeled "ScheduleIQ Scenario Calculation", never presented as imported
    P6 CPM.
  - ADVISORY — increase_crew, accelerate_engineering, accelerate_procurement,
    accelerate_prefab, split_activity, other. These cannot be objectively
    turned into a day count from schedule logic alone, so they're recorded
    with a note and excluded from the calculated recovery-days figure —
    never silently assigned a fabricated number of days.

Calendar awareness (Schedule Risk/Recovery phase): when a `calendars` map
({calendarId: CalendarDefinition}) is supplied, each activity's own
duration-forward/backward shift (ES->EF, LF->LS) uses that activity's real
working-day calendar (calendar_engine.add_working_days) instead of raw
calendar-day arithmetic. Relationship LAG values are still applied as
calendar days regardless — P6's own lag-calendar semantics are genuinely
ambiguous across implementations, and this engine does not claim a fidelity
it can't support. Every scenario result discloses `calendarConfidence` so
this is never a silent guess.

Data Date discipline: activities with an actual start (`start`) keep it —
their early start is never pulled earlier than the real actual date, and a
completed activity (pctComplete >= 100) contributes zero remaining duration,
so history is never rewritten. Only remaining/forecast work at or after the
Data Date is genuinely reshaped by a scenario.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import calendar_engine
from .calendar_engine import CalendarDefinition
from .schedule_comparison import _id_to_code_name

CALCULABLE_TYPES = {
    'reduce_duration', 'remove_lag', 'reduce_lag',
    'change_relationship_type', 'add_relationship', 'remove_relationship',
}
ADVISORY_TYPES = {
    'increase_crew', 'accelerate_engineering', 'accelerate_procurement',
    'accelerate_prefab', 'split_activity', 'other',
}


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


def _add_days(d: Optional[date], n: float) -> Optional[date]:
    return d + timedelta(days=round(n)) if d else None


def _shift_by_duration(d: Optional[date], dur: float, calendar: Optional[CalendarDefinition], forward: bool) -> Tuple[Optional[date], bool]:
    """Shifts `d` by `dur` days in the given direction, using the activity's
    real working-day calendar when available. Returns (new_date, used_calendar)
    so callers can track calendar confidence per activity — never silently
    guesses a Mon-Fri workweek when the calendar is unavailable/low-confidence;
    it falls back to calendar-day arithmetic (the pre-existing behavior) and
    reports that fallback happened."""
    if d is None:
        return None, False
    n = round(dur) if forward else -round(dur)
    if calendar is not None:
        shifted = calendar_engine.add_working_days(d, n, calendar)
        if shifted is not None:
            return shifted, True
    return _add_days(d, n), False


def _duration_of(a: dict) -> float:
    if (a.get('pctComplete') or 0) >= 100:
        return 0.0
    return float(a.get('remainDur') if a.get('remainDur') is not None else (a.get('dur') or 0.0))


def _resolve_actid(raw_id: Optional[str], id_map: Dict[str, Tuple[str, str]]) -> str:
    """Resolves a predecessor/successor `actId` (which XER stores as the
    raw internal task_id, not the human-readable code) through id_map —
    the exact same convention schedule_comparison._relationship_keys uses.
    Falls back to the raw id unchanged when it isn't in the map (e.g. a
    non-XER source where id already equals code)."""
    if not raw_id:
        return ''
    return id_map.get(raw_id, (raw_id, ''))[0] or raw_id


def apply_scenario_actions(
    activities: List[dict], actions: List[dict],
) -> Tuple[List[dict], List[dict], List[dict], List[str]]:
    """
    Returns (modified_activities, applied_calculable_actions, advisory_actions, warnings).
    `activities` is deep-copied — the caller's list (and the DB row it came
    from) is never mutated. Activity/relationship ids in `actions` are
    Activity Codes (as shown to the scheduler); resolved against the same
    raw-task_id -> code map used everywhere else so this works correctly on
    real XER imports, not just fixtures where id happens to equal code.
    """
    acts = copy.deepcopy(activities)
    id_map = _id_to_code_name(acts)
    by_id = {_act_id(a): a for a in acts}
    applied: List[dict] = []
    advisory: List[dict] = []
    warnings: List[str] = []
    open_end_warnings: List[str] = []

    def _pred_actid(p):
        return _resolve_actid(str(p.get('actId') or ''), id_map)

    for action in actions:
        t = action.get('type')

        if t == 'reduce_duration':
            aid = action.get('activityId')
            a = by_id.get(aid)
            if not a:
                warnings.append(f'Activity {aid} not found — action skipped.')
                continue
            try:
                new_dur = float(action['newDuration'])
            except (KeyError, TypeError, ValueError):
                warnings.append(f'{aid}: invalid newDuration — action skipped.')
                continue
            orig = a.get('dur') or 0.0
            if new_dur >= orig:
                warnings.append(f'{aid}: new duration {new_dur}d is not less than current {orig}d — no effect.')
                continue
            a['dur'] = new_dur
            pct = a.get('pctComplete') or 0.0
            a['remainDur'] = max(0.0, new_dur * (1 - pct / 100.0))
            applied.append(action)

        elif t in ('remove_lag', 'reduce_lag'):
            pred_id, succ_id = action.get('predecessorId'), action.get('successorId')
            succ = by_id.get(succ_id)
            pred = by_id.get(pred_id)
            if not succ or not pred:
                warnings.append(f'Relationship {pred_id} -> {succ_id}: activity not found — action skipped.')
                continue
            new_lag = 0.0 if t == 'remove_lag' else float(action.get('newLagDays', 0) or 0)
            found = False
            for p in (succ.get('predecessors') or []):
                if _pred_actid(p) == pred_id:
                    p['lagDays'] = new_lag
                    found = True
            for s in (pred.get('successors') or []):
                if _pred_actid(s) == succ_id:
                    s['lagDays'] = new_lag
            if not found:
                warnings.append(f'Relationship {pred_id} -> {succ_id} not found — action skipped.')
                continue
            applied.append(action)

        elif t == 'change_relationship_type':
            pred_id, succ_id, new_type = action.get('predecessorId'), action.get('successorId'), action.get('newRelType')
            succ = by_id.get(succ_id)
            pred = by_id.get(pred_id)
            if not succ or not pred or new_type not in ('FS', 'SS', 'FF', 'SF'):
                warnings.append(f'Relationship {pred_id} -> {succ_id}: invalid change — action skipped.')
                continue
            for p in (succ.get('predecessors') or []):
                if _pred_actid(p) == pred_id:
                    p['relType'] = new_type
            for s in (pred.get('successors') or []):
                if _pred_actid(s) == succ_id:
                    s['relType'] = new_type
            applied.append(action)

        elif t == 'remove_relationship':
            pred_id, succ_id = action.get('predecessorId'), action.get('successorId')
            succ = by_id.get(succ_id)
            pred = by_id.get(pred_id)
            if not succ or not pred:
                warnings.append(f'Relationship {pred_id} -> {succ_id}: activity not found — action skipped.')
                continue
            succ['predecessors'] = [p for p in (succ.get('predecessors') or []) if _pred_actid(p) != pred_id]
            pred['successors'] = [s for s in (pred.get('successors') or []) if _pred_actid(s) != succ_id]
            applied.append(action)
            # Open-end check (item 31): does removing this relationship leave
            # the predecessor with no successors, or the successor with no
            # predecessors, other than a legitimate start/finish milestone?
            if not pred.get('successors') and not pred.get('isMilestone'):
                open_end_warnings.append(
                    f"Scenario creates an activity with no successor: {pred_id} ({pred.get('name') or ''})."
                )
            if not succ.get('predecessors') and not succ.get('isMilestone'):
                open_end_warnings.append(
                    f"Scenario creates an activity with no predecessor: {succ_id} ({succ.get('name') or ''})."
                )

        elif t == 'add_relationship':
            pred_id, succ_id = action.get('predecessorId'), action.get('successorId')
            succ = by_id.get(succ_id)
            pred = by_id.get(pred_id)
            if not succ or not pred:
                warnings.append(f'Relationship {pred_id} -> {succ_id}: activity not found — action skipped.')
                continue
            if _would_create_cycle(by_id, pred_id, succ_id, id_map):
                warnings.append(f'Adding {pred_id} -> {succ_id} would create a circular relationship — action skipped (rejected, not applied).')
                continue
            rel_type = action.get('relType', 'FS')
            lag = float(action.get('lagDays', 0) or 0)
            succ.setdefault('predecessors', []).append({'actId': pred_id, 'relType': rel_type, 'lagDays': lag})
            pred.setdefault('successors', []).append({'actId': succ_id, 'relType': rel_type, 'lagDays': lag})
            applied.append(action)

        elif t in ADVISORY_TYPES:
            advisory.append(action)

        else:
            warnings.append(f"Unknown action type '{t}' — treated as advisory (not applied to schedule logic).")
            advisory.append(action)

    return acts, applied, advisory, warnings + open_end_warnings


def _would_create_cycle(by_id: Dict[str, dict], pred_id: str, succ_id: str, id_map: Optional[Dict[str, Tuple[str, str]]] = None) -> bool:
    """Iterative (never recursive) — safe on large relationship graphs; a
    `seen` set bounds the walk to at most one visit per node regardless of
    network size."""
    if pred_id == succ_id:
        return True
    id_map = id_map or {}
    seen = {succ_id}
    stack = [succ_id]
    while stack:
        cur = stack.pop()
        if cur == pred_id:
            return True
        a = by_id.get(cur)
        if not a:
            continue
        for s in (a.get('successors') or []):
            sid = _resolve_actid(str(s.get('actId') or ''), id_map)
            if sid not in seen:
                seen.add(sid)
                stack.append(sid)
    return False


def _forward_backward_pass(
    group: List[dict], data_date: date,
    calendars: Optional[Dict[str, CalendarDefinition]] = None,
    id_map: Optional[Dict[str, Tuple[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    """Ported from frontend/cpm.ts's computeProjectCPM — same algorithm, one
    project group. Returns None (rather than a guessed answer) when the
    network contains circular logic, exactly like the frontend does."""
    id_map = id_map or {}
    calendars = calendars or {}
    by_id = {_act_id(a): a for a in group}

    def _cal_for(a):
        return calendars.get(a.get('calendarId'))

    succ_of: Dict[str, list] = {}
    pred_of: Dict[str, list] = {}
    for a in group:
        aid = _act_id(a)
        pred_of[aid] = [
            {**p, 'actId': _resolve_actid(str(p.get('actId') or ''), id_map)}
            for p in (a.get('predecessors') or [])
            if _resolve_actid(str(p.get('actId') or ''), id_map) in by_id
        ]
        succ_of[aid] = [
            {**s, 'actId': _resolve_actid(str(s.get('actId') or ''), id_map)}
            for s in (a.get('successors') or [])
            if _resolve_actid(str(s.get('actId') or ''), id_map) in by_id
        ]

    indeg = {aid: len(pred_of[aid]) for aid in by_id}
    queue = [aid for aid, d in indeg.items() if d == 0]
    order = []
    indeg_copy = dict(indeg)
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for s in succ_of.get(nid, []):
            sid = s['actId']
            indeg_copy[sid] -= 1
            if indeg_copy[sid] == 0:
                queue.append(sid)

    if len(order) != len(group):
        return None  # circular logic — CPM can't be computed reliably

    ES: Dict[str, date] = {}
    EF: Dict[str, date] = {}
    calendar_used: Dict[str, bool] = {}
    for aid in order:
        a = by_id[aid]
        dur = _duration_of(a)
        cal = _cal_for(a)
        started = _parse_date(a.get('start'))
        es = started if started else data_date
        for p in pred_of[aid]:
            pid = p['actId']
            p_es, p_ef = ES.get(pid), EF.get(pid)
            if p_es is None or p_ef is None:
                continue
            lag = p.get('lagDays') or 0
            rel = p.get('relType', 'FS')
            # Lag is applied as calendar days regardless of calendar
            # availability — see module docstring: P6 lag-calendar
            # semantics are not modeled with claimed fidelity here.
            if rel == 'SS':
                constraint = _add_days(p_es, lag)
            elif rel == 'FF':
                constraint = _add_days(p_ef, lag - dur)
            elif rel == 'SF':
                constraint = _add_days(p_es, lag - dur)
            else:
                constraint = _add_days(p_ef, lag)
            if constraint and constraint > es:
                es = constraint
        if not started and es < data_date:
            es = data_date
        ef, used_cal = _shift_by_duration(es, dur, cal, forward=True)
        ES[aid] = es
        EF[aid] = ef
        calendar_used[aid] = used_cal if cal is not None else False

    project_finish = max(EF.values()) if EF else data_date

    LS: Dict[str, date] = {}
    LF: Dict[str, date] = {}
    for aid in reversed(order):
        a = by_id[aid]
        dur = _duration_of(a)
        cal = _cal_for(a)
        lf = project_finish if not succ_of[aid] else None
        for s in succ_of[aid]:
            sid = s['actId']
            s_ls, s_lf = LS.get(sid), LF.get(sid)
            if s_ls is None or s_lf is None:
                continue
            lag = s.get('lagDays') or 0
            rel = s.get('relType', 'FS')
            if rel == 'SS':
                constraint = _add_days(s_ls, -lag)
            elif rel == 'FF':
                constraint = _add_days(s_lf, -lag)
            elif rel == 'SF':
                constraint = _add_days(s_lf, -lag)
            else:
                constraint = _add_days(s_ls, -lag)
            if constraint and (lf is None or constraint < lf):
                lf = constraint
        if lf is None:
            lf = project_finish
        LF[aid] = lf
        ls, _ = _shift_by_duration(lf, dur, cal, forward=False)
        LS[aid] = ls

    results = {}
    for aid in order:
        es, ef, ls, lf = ES[aid], EF[aid], LS[aid], LF[aid]
        total_float = (ls - es).days if (ls and es) else None
        results[aid] = {
            'earlyStart': es.isoformat() if es else None, 'earlyFinish': ef.isoformat() if ef else None,
            'lateStart': ls.isoformat() if ls else None, 'lateFinish': lf.isoformat() if lf else None,
            'totalFloat': total_float, 'isCritical': bool(total_float is not None and total_float <= 0),
            'calendarAvailable': calendar_used.get(aid, False),
        }

    incomplete_finishes = [EF[aid] for aid in order if (by_id[aid].get('pctComplete') or 0) < 100]
    forecast_finish = max(incomplete_finishes) if incomplete_finishes else project_finish

    return {'perActivity': results, 'forecastFinish': forecast_finish}


def compute_scenario_cpm(
    activities: List[dict], data_date: date,
    calendars: Optional[Dict[str, CalendarDefinition]] = None,
) -> Dict[str, Any]:
    """Runs the forward/backward pass per project group, returns the overall
    forecast finish plus each activity's recalculated float/critical status."""
    id_map = _id_to_code_name(activities)
    by_project: Dict[str, List[dict]] = {}
    for a in activities:
        by_project.setdefault(a.get('projectId') or '__none__', []).append(a)

    per_activity: Dict[str, dict] = {}
    forecast_finish: Optional[date] = None
    unresolved_groups = 0

    for group in by_project.values():
        pass_result = _forward_backward_pass(group, data_date, calendars, id_map)
        if pass_result is None:
            unresolved_groups += 1
            continue
        per_activity.update(pass_result['perActivity'])
        gf = pass_result['forecastFinish']
        if gf and (forecast_finish is None or gf > forecast_finish):
            forecast_finish = gf

    activities_with_calendar = sum(1 for r in per_activity.values() if r.get('calendarAvailable'))

    return {
        'perActivity': per_activity,
        'forecastFinish': forecast_finish.isoformat() if forecast_finish else None,
        'unresolvedGroups': unresolved_groups,
        'calendarConfidence': {
            'activitiesWithCalendar': activities_with_calendar,
            'totalActivities': len(per_activity),
            'available': activities_with_calendar > 0,
        },
    }


def _milestone_impact(
    baseline_by_id: Dict[str, dict],
    current_activities: List[dict],
    current_cpm: Dict[str, dict],
    scenario_cpm: Dict[str, dict],
) -> List[Dict[str, Any]]:
    """Per-milestone before/after: current forecast vs scenario forecast vs
    Approved Baseline (when supplied). Baseline Variance = vs Approved
    Baseline; This-Scenario Movement = scenario vs current — kept as
    separate fields, never combined, matching the same convention
    update_intelligence.py already uses for update-vs-baseline."""
    out = []
    for a in current_activities:
        if not a.get('isMilestone'):
            continue
        aid = _act_id(a)
        cur = current_cpm.get(aid)
        scn = scenario_cpm.get(aid)
        if not cur or not scn:
            continue
        cur_finish = _parse_date(cur.get('earlyFinish'))
        scn_finish = _parse_date(scn.get('earlyFinish'))
        movement_days = (cur_finish - scn_finish).days if (cur_finish and scn_finish) else None

        baseline_finish = None
        bl = baseline_by_id.get(aid)
        if bl:
            baseline_finish = _parse_date(bl.get('bFinish'))

        current_variance = (cur_finish - baseline_finish).days if (cur_finish and baseline_finish) else None
        scenario_variance = (scn_finish - baseline_finish).days if (scn_finish and baseline_finish) else None
        recovered = None
        recovery_pct = None
        if current_variance is not None and scenario_variance is not None:
            recovered = current_variance - scenario_variance
            if current_variance > 0:
                recovery_pct = round(100.0 * recovered / current_variance, 1)
            # current_variance <= 0 (already at/ahead of baseline): a
            # percentage-late-recovered is not mathematically meaningful —
            # left as None rather than a misleading number.

        out.append({
            'activityId': aid, 'activityName': a.get('name') or '',
            'currentForecastFinish': cur_finish.isoformat() if cur_finish else None,
            'scenarioForecastFinish': scn_finish.isoformat() if scn_finish else None,
            'movementDays': movement_days,
            'baselineFinish': baseline_finish.isoformat() if baseline_finish else None,
            'currentBaselineVarianceDays': current_variance,
            'scenarioBaselineVarianceDays': scenario_variance,
            'varianceRecoveredDays': recovered,
            'recoveryPct': recovery_pct,
        })
    return out


def _side_effects(current_cpm: Dict[str, dict], scenario_cpm: Dict[str, dict], activities_by_id: Dict[str, dict]) -> Dict[str, Any]:
    """Compares scenario per-activity results against the SAME activities'
    current (unmodified) CPM — the only way to know whether a scenario
    change helped, hurt, or had no effect on each individual activity, and
    whether it changed critical/driving membership. Never assumes every
    improvement is uniformly positive — negative side effects are reported
    with equal visibility (item 28)."""
    improved, worsened, unchanged = [], [], []
    newly_critical, left_critical = [], []
    newly_negative_float, recovered_from_negative_float = [], []

    for aid, cur in current_cpm.items():
        scn = scenario_cpm.get(aid)
        if not scn:
            continue
        a = activities_by_id.get(aid, {})
        cur_ef, scn_ef = _parse_date(cur.get('earlyFinish')), _parse_date(scn.get('earlyFinish'))
        movement = (cur_ef - scn_ef).days if (cur_ef and scn_ef) else None
        entry = {
            'activityId': aid, 'activityName': a.get('name') or '',
            'currentFinish': cur_ef.isoformat() if cur_ef else None,
            'scenarioFinish': scn_ef.isoformat() if scn_ef else None,
            'movementDays': movement,
            'currentTotalFloat': cur.get('totalFloat'), 'scenarioTotalFloat': scn.get('totalFloat'),
        }
        if movement is not None:
            if movement > 0:
                improved.append(entry)
            elif movement < 0:
                worsened.append(entry)
            else:
                unchanged.append(entry)

        cur_crit, scn_crit = bool(cur.get('isCritical')), bool(scn.get('isCritical'))
        if scn_crit and not cur_crit:
            newly_critical.append(entry)
        elif cur_crit and not scn_crit:
            left_critical.append(entry)

        cur_tf, scn_tf = cur.get('totalFloat'), scn.get('totalFloat')
        if cur_tf is not None and scn_tf is not None:
            if scn_tf < 0 and cur_tf >= 0:
                newly_negative_float.append(entry)
            elif scn_tf >= 0 and cur_tf < 0:
                recovered_from_negative_float.append(entry)

    return {
        'improved': improved, 'improvedCount': len(improved),
        'worsened': worsened, 'worsenedCount': len(worsened),
        'unchanged': unchanged, 'unchangedCount': len(unchanged),
        'newlyCritical': newly_critical, 'newlyCriticalCount': len(newly_critical),
        'leftCriticalPath': left_critical, 'leftCriticalPathCount': len(left_critical),
        'newlyNegativeFloat': newly_negative_float, 'newlyNegativeFloatCount': len(newly_negative_float),
        'recoveredFromNegativeFloat': recovered_from_negative_float, 'recoveredFromNegativeFloatCount': len(recovered_from_negative_float),
    }


def run_scenario(
    current_activities: List[dict],
    actions: List[dict],
    data_date: date,
    current_forecast_finish: Optional[date] = None,
    calendars: Optional[Dict[str, CalendarDefinition]] = None,
    approved_baseline_activities: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """
    Full scenario calculation: apply actions, recompute CPM, compare against
    the current (unmodified) forecast finish. `current_forecast_finish`
    should come from the already-computed baseline (e.g. the version's own
    forecast_finish) — if not supplied, it's computed here from the
    unmodified activities for a fair before/after comparison.

    When `approved_baseline_activities` is supplied (the project's real
    Approved Baseline version — NOT `current_activities`, which is the
    schedule being modified), the result also includes per-milestone
    Baseline Variance Recovered figures (items 25-27).

    `calendars` ({calendarId: CalendarDefinition}) enables working-day-aware
    date shifting per activity; when omitted or a given activity's calendar
    is unavailable, the pre-existing calendar-day arithmetic is used and
    disclosed via `calendarConfidence`/`calendarAvailable`.
    """
    current_cpm_result = compute_scenario_cpm(current_activities, data_date, calendars)
    if current_forecast_finish is None:
        current_forecast_finish = _parse_date(current_cpm_result['forecastFinish'])

    modified, applied, advisory, warnings = apply_scenario_actions(current_activities, actions)
    scenario_cpm_result = compute_scenario_cpm(modified, data_date, calendars)
    scenario_finish = _parse_date(scenario_cpm_result['forecastFinish'])

    recovery_days = None
    if current_forecast_finish and scenario_finish:
        recovery_days = (current_forecast_finish - scenario_finish).days

    affected_activity_ids = sorted({
        aid for action in applied
        for aid in (action.get('activityId'), action.get('predecessorId'), action.get('successorId'))
        if aid
    })
    critical_activity_ids = sorted([
        aid for aid, r in scenario_cpm_result['perActivity'].items() if r['isCritical']
    ])

    baseline_by_id = {_act_id(a): a for a in (approved_baseline_activities or [])}
    milestone_impact = _milestone_impact(
        baseline_by_id, modified, current_cpm_result['perActivity'], scenario_cpm_result['perActivity'],
    ) if modified else []

    modified_by_id = {_act_id(a): a for a in modified}
    side_effects = _side_effects(current_cpm_result['perActivity'], scenario_cpm_result['perActivity'], modified_by_id)

    # Project-level baseline recovery — the latest baseline finish among
    # non-milestone activities stands in for "project finish" when no
    # explicit Project Finish milestone is identifiable, mirroring how
    # other engines (e.g. compute_critical_path_movement) treat the
    # driving path's own endpoint.
    project_baseline_finish = None
    if baseline_by_id:
        bl_finishes = [d for d in (_parse_date(a.get('bFinish')) for a in approved_baseline_activities) if d]
        project_baseline_finish = max(bl_finishes) if bl_finishes else None

    project_baseline_recovery = None
    if project_baseline_finish and current_forecast_finish and scenario_finish:
        current_variance = (current_forecast_finish - project_baseline_finish).days
        scenario_variance = (scenario_finish - project_baseline_finish).days
        recovered = current_variance - scenario_variance
        recovery_pct = round(100.0 * recovered / current_variance, 1) if current_variance > 0 else None
        project_baseline_recovery = {
            'baselineFinish': project_baseline_finish.isoformat(),
            'currentBaselineVarianceDays': current_variance,
            'scenarioBaselineVarianceDays': scenario_variance,
            'varianceRecoveredDays': recovered,
            'recoveryPct': recovery_pct,
        }

    return {
        'currentForecastFinish': current_forecast_finish.isoformat() if current_forecast_finish else None,
        'scenarioForecastFinish': scenario_finish.isoformat() if scenario_finish else None,
        'recoveryDays': recovery_days,
        'appliedActions': applied,
        'advisoryActions': advisory,
        'warnings': warnings,
        'affectedActivityIds': affected_activity_ids,
        'scenarioCriticalPathActivityIds': critical_activity_ids[:200],
        'unresolvedGroups': scenario_cpm_result['unresolvedGroups'],
        'calendarConfidence': scenario_cpm_result['calendarConfidence'],
        'milestoneImpact': milestone_impact,
        'sideEffects': side_effects,
        'projectBaselineRecovery': project_baseline_recovery,
        'disclaimer': (
            'Scenario results are ScheduleIQ Scenario Calculation planning estimates — '
            'calculated from the schedule\'s relationship network, not the imported P6 '
            'CPM — and must be validated by the project team before modifying the '
            'contractual schedule.'
        ),
    }
