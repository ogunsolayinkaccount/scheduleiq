"""
Master Activity Analysis — ScheduleIQ Master Schedule Analysis, Float,
Progress & Milestone Intelligence phase.

This module builds the ONE authoritative analytical row per activity that
Activity Analysis, Float Analysis, and (where applicable) Progress/
Milestones all read from — so these workspaces never silently calculate
the same schedule concept two different ways. It NEVER recomputes what an
existing engine already computed: movement flags come from
update_intelligence.py, milestone/driving-predecessor fields come from
milestones.py, downstream-milestone-exposure comes from driving_chain.py,
Activity ID matching uses the same `code or id` convention as every other
engine in this codebase, and raw P6 task_id relationship endpoints are
resolved via schedule_comparison._id_to_code_name — the same established
methodology used throughout Risk & Recovery.

Golden rules enforced throughout:
  - Imported P6 Total Float (`totalFloat`) is never replaced by a
    recalculation; Baseline/Previous/Current Total Float are three
    separately-identifiable values, each read from its own version's own
    activity dict.
  - Missing source data is `None` (renders "Unavailable"), never 0.
  - Working-day variances are computed only when a confident calendar is
    available for that activity; otherwise the calendar-day value is
    returned with `calendarConfident: False` — never silently presented as
    a working-day figure.
  - Original Duration provenance: `dur` silently falls back to Remaining
    Duration in the parser when P6's own target_drtn_hr_cnt is blank/zero
    (existing, load-bearing parser behavior — not changed here). This
    module surfaces `originalDurationSource` (IMPORTED / FALLBACK_REMAINING
    / UNAVAILABLE / UNKNOWN) from the parser's own `origDurSource` field so
    a fallback value is never presented as a confidently-imported Original
    Duration.
  - Actual Duration is always `None` (Unavailable) — real XER exports do
    not carry an `act_drtn_hr_cnt` column (confirmed against real P6 data);
    deriving it from Actual Start/Finish would be inventing a value P6
    itself does not persist, so this module does not do that.
  - "Remaining Early Start/Finish" is not a real task-level P6 field (only
    exists per-resource-assignment). This module exposes the genuinely
    task-level field (`remainStart`/`remainFinish`, from P6's
    restart_date/reend_date) as `currentRemainingStart`/
    `currentRemainingFinish` — accurate terminology, not a relabeled
    substitute.
  - Completed-activity float display (P6 behavior): once an activity is
    definitively Complete (`finished` below — an actual finish date is
    present, or pctComplete >= 100), its Total Float/Free Float are no
    longer a live CPM signal, so `currentTotalFloat`/`freeFloat` (the
    fields every downstream page/aggregation reads for "current" float)
    become `None` ("—") for that row rather than presenting a possibly-
    stale/zero stored value as actionable. The imported P6 figure itself
    is NEVER mutated or discarded — it remains available unchanged via
    `importedCurrentTotalFloat`/`importedFreeFloat` for source
    traceability. `criticalActionable` (critical AND NOT finished) is the
    analogous "current actionable" signal for criticality — the raw
    imported `critical`/`isCritical` flag is preserved as-is alongside it.
    Baseline/Previous Total Float are untouched by this rule (each is a
    historical snapshot valid for that version regardless of the
    activity's current-version completion status) — only the CURRENT
    figure is display-blanked, and only for the version being evaluated.
  - Canonical date fallback hierarchy (documented once, used everywhere —
    see _canonical_current_start/_canonical_current_finish/
    _canonical_forecast_start/_canonical_forecast_finish below):
      Current  = actual, else early, else remaining, else baseline/target.
      Forecast = same chain WITHOUT the actual short-circuit — None once an
                 activity has genuinely started/finished, since forecasting
                 an already-actualized date does not apply.
    This is the exact convention already used by driving_chain.py/
    recovery_engine.py for "currentFinish" — reused verbatim, not
    reinvented, so Current Finish means the same thing on every page.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from . import calendar_engine
from .calendar_engine import CalendarDefinition
from .milestones import _driving_predecessor
from .schedule_comparison import _id_to_code_name

ENGINE_VERSION = '1.0.0'

NEAR_CRITICAL_FLOAT_THRESHOLD = 10.0


def _act_id(a: dict) -> str:
    return str(a.get('code') or a.get('id') or '')


def _parse_date(v) -> Optional[date]:
    if v is None or v == '':
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _num(v) -> Optional[float]:
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── Canonical date fallback hierarchy — the ONE place this is defined ──────

def _canonical_current_start(a: dict) -> Optional[str]:
    return a.get('start') or a.get('earlyStart') or a.get('remainStart') or a.get('bStart')


def _canonical_current_finish(a: dict) -> Optional[str]:
    return a.get('finish') or a.get('earlyFinish') or a.get('remainFinish') or a.get('bFinish')


def _canonical_forecast_start(a: dict) -> Optional[str]:
    if a.get('start'):
        return None  # already actualized — not a forecast
    return a.get('earlyStart') or a.get('remainStart') or a.get('bStart')


def _canonical_forecast_finish(a: dict) -> Optional[str]:
    if a.get('finish'):
        return None
    return a.get('earlyFinish') or a.get('remainFinish') or a.get('bFinish')


def _variance(d1: Optional[str], d2: Optional[str], calendar: Optional[CalendarDefinition]) -> Dict[str, Any]:
    """d2 - d1 in calendar days always (when both present); working days only
    when a confidently-decoded calendar is available for this activity."""
    p1, p2 = _parse_date(d1), _parse_date(d2)
    if not p1 or not p2:
        return {'calendarDays': None, 'workingDays': None, 'calendarConfident': calendar is not None}
    calendar_days = (p2 - p1).days
    working_days = None
    if calendar is not None:
        try:
            working_days = calendar_engine.working_days_between(p1, p2, calendar)
        except Exception:
            working_days = None
    return {'calendarDays': calendar_days, 'workingDays': working_days, 'calendarConfident': calendar is not None}


def is_activity_complete(a: dict) -> bool:
    """The one place 'Complete' is defined for float-display purposes —
    reused by float_intelligence.compute_float_trend so a historical
    version's own point is blanked/preserved by the exact same rule as
    the current master row, never a second definition of 'Complete'."""
    pct = _num(a.get('pctComplete'))
    return bool(a.get('finish')) or (pct is not None and pct >= 100)


def _rel_counts(rels: List[dict]) -> Dict[str, Any]:
    fs = ss = ff = sf = 0
    pos_lag = neg_lag = False
    max_lag = None
    for r in rels:
        rt = (r.get('relType') or 'FS').upper()
        if rt == 'FS':
            fs += 1
        elif rt == 'SS':
            ss += 1
        elif rt == 'FF':
            ff += 1
        elif rt == 'SF':
            sf += 1
        lag = _num(r.get('lagDays')) or 0.0
        if lag > 0:
            pos_lag = True
        elif lag < 0:
            neg_lag = True
        max_lag = lag if max_lag is None or lag > max_lag else max_lag
    return {'fs': fs, 'ss': ss, 'ff': ff, 'sf': sf, 'positiveLag': pos_lag, 'negativeLag': neg_lag, 'maxLag': max_lag}


def build_relationship_maps(
    activities: List[dict], id_map: Optional[Dict[str, Tuple[str, str]]] = None,
) -> Tuple[Dict[str, dict], Dict[str, List[dict]], Dict[str, List[dict]]]:
    """O(n + r) — by_id, pred_of, succ_of, all relationship endpoints
    resolved from raw P6 task_id to Activity Code once."""
    id_map = id_map if id_map is not None else _id_to_code_name(activities)
    by_id = {_act_id(a): a for a in activities}
    pred_of: Dict[str, List[dict]] = {}
    succ_of: Dict[str, List[dict]] = {}
    for a in activities:
        aid = _act_id(a)
        pred_of[aid] = [
            {'actId': id_map.get(str(p.get('actId') or ''), (p.get('actId'), ''))[0] or p.get('actId'),
             'relType': p.get('relType') or 'FS', 'lagDays': p.get('lagDays') or 0}
            for p in (a.get('predecessors') or [])
        ]
        succ_of[aid] = [
            {'actId': id_map.get(str(s.get('actId') or ''), (s.get('actId'), ''))[0] or s.get('actId'),
             'relType': s.get('relType') or 'FS', 'lagDays': s.get('lagDays') or 0}
            for s in (a.get('successors') or [])
        ]
    return by_id, pred_of, succ_of


def build_activity_analysis(
    current_activities: List[dict],
    previous_activities: Optional[List[dict]] = None,
    baseline_activities: Optional[List[dict]] = None,
    current_data_date: Optional[date] = None,
    previous_data_date: Optional[date] = None,
    calendars: Optional[Dict[str, CalendarDefinition]] = None,
    calendar_meta: Optional[Dict[str, Dict[str, Any]]] = None,
    update_intelligence_result: Optional[Dict[str, Any]] = None,
    milestone_report: Optional[List[dict]] = None,
    at_risk_milestone_ids: Optional[Set[str]] = None,
    risk_by_key: Optional[Dict[str, dict]] = None,
) -> Dict[str, Any]:
    """Returns {engineVersion, rowCount, rows: [...], calendarConfidence}.

    Every optional input is a value an existing engine already computed
    (update_intelligence.build_update_intelligence, milestones.
    build_milestone_report, risk_register.build_risk_register) — this
    function only assembles them plus fields that genuinely don't exist
    anywhere else yet (duration provenance, logic/relationship counts,
    variance, float-change classification). Nothing here recomputes a
    movement flag, a milestone risk level, or a risk severity that one of
    those engines already produced.

    Never truncates: iterates `current_activities` once, O(n + r) overall
    (relationship maps are built once up front), and returns one row per
    activity with no row cap.
    """
    calendars = calendars or {}
    calendar_meta = calendar_meta or {}
    at_risk_milestone_ids = at_risk_milestone_ids or set()

    id_map = _id_to_code_name(current_activities)
    by_id, pred_of, succ_of = build_relationship_maps(current_activities, id_map)
    baseline_by_id = {_act_id(a): a for a in (baseline_activities or [])}
    previous_by_id = {_act_id(a): a for a in (previous_activities or [])}

    driving_ids: Set[str] = set()
    if update_intelligence_result:
        cpm = update_intelligence_result.get('criticalPathMovement') or {}
        driving_ids = {e['activityId'] for e in (cpm.get('stayedCritical') or [])} | \
            {e['activityId'] for e in (cpm.get('becameCritical') or [])}

    movement_by_id: Dict[str, dict] = {}
    if update_intelligence_result:
        for row in (update_intelligence_result.get('movementRows') or []):
            movement_by_id[row['activityId']] = row

    logic_by_activity: Dict[str, list] = {}
    constraint_by_activity: Dict[str, list] = {}
    duration_by_activity: Dict[str, dict] = {}
    if update_intelligence_result:
        logic_by_activity = (update_intelligence_result.get('logicChanges') or {}).get('byActivity') or {}
        for c in (update_intelligence_result.get('constraintChanges') or []):
            constraint_by_activity.setdefault(c['activityId'], []).append(c)
        dur_changes = update_intelligence_result.get('durationChanges') or {}
        for d in (dur_changes.get('originalDurationChanges') or []):
            duration_by_activity.setdefault(d['activityId'], {})['original'] = d
        for d in (dur_changes.get('remainingDurationChanges') or []):
            duration_by_activity.setdefault(d['activityId'], {})['remaining'] = d

    milestone_by_id: Dict[str, dict] = {}
    if milestone_report:
        milestone_by_id = {m['activityId']: m for m in milestone_report}

    risk_by_key = risk_by_key or {}

    rows: List[Dict[str, Any]] = []
    activities_with_calendar = 0

    for a in current_activities:
        aid = _act_id(a)
        prev = previous_by_id.get(aid)
        base = baseline_by_id.get(aid)
        cal = calendars.get(a.get('calendarId'))
        if cal is not None:
            activities_with_calendar += 1
        cmeta = calendar_meta.get(a.get('calendarId')) or {}

        pct_complete = _num(a.get('pctComplete'))
        started = bool(a.get('start'))
        finished = is_activity_complete(a)
        in_progress = started and not finished
        b_start_d, b_finish_d = _parse_date(a.get('bStart')), _parse_date(a.get('bFinish'))
        should_have_started = (not started) and b_start_d is not None and current_data_date is not None and b_start_d < current_data_date
        should_have_finished = (not finished) and b_finish_d is not None and current_data_date is not None and b_finish_d < current_data_date

        # ── Duration (with Original Duration provenance) ──
        orig_dur = _num(a.get('dur'))
        orig_dur_source = a.get('origDurSource', 'UNKNOWN')
        remain_dur = _num(a.get('remainDur'))
        base_orig_dur = _num(base.get('dur')) if base else None
        prev_remain_dur = _num(prev.get('remainDur')) if prev else None
        remain_dur_change = (remain_dur - prev_remain_dur) if (remain_dur is not None and prev_remain_dur is not None) else None
        duration_variance = (orig_dur - base_orig_dur) if (orig_dur is not None and base_orig_dur is not None) else None
        # At 0% complete no actual work has occurred yet, so "at completion
        # duration" trivially equals remaining duration — not an inference.
        # Otherwise Actual Duration is Unavailable (see module docstring),
        # so At Completion Duration is Unavailable too rather than guessed.
        at_completion_dur = remain_dur if (pct_complete is not None and pct_complete <= 0) else None

        # ── Dates (canonical fallback hierarchy) ──
        current_start = _canonical_current_start(a)
        current_finish = _canonical_current_finish(a)
        forecast_start = _canonical_forecast_start(a)
        forecast_finish = _canonical_forecast_finish(a)
        prev_current_finish = _canonical_current_finish(prev) if prev else None
        prev_current_start = _canonical_current_start(prev) if prev else None

        start_var = _variance(a.get('bStart'), current_start, cal)
        finish_var = _variance(a.get('bFinish'), current_finish, cal)
        start_movement = _variance(prev_current_start, current_start, cal) if prev else {'calendarDays': None, 'workingDays': None, 'calendarConfident': cal is not None}
        finish_movement = _variance(prev_current_finish, current_finish, cal) if prev else {'calendarDays': None, 'workingDays': None, 'calendarConfident': cal is not None}

        # ── Float — imported P6 Total Float is source truth throughout ──
        baseline_tf = _num(base.get('totalFloat')) if base else None
        previous_tf = _num(prev.get('totalFloat')) if prev else None
        imported_current_tf = _num(a.get('totalFloat'))
        # Completed-activity float display (P6 behavior): the CURRENT/
        # actionable Total Float is blanked once an activity is Complete —
        # the imported value is preserved untouched in
        # importedCurrentTotalFloat, never mutated, never discarded. See
        # module docstring.
        current_tf = None if finished else imported_current_tf
        float_change_vs_baseline = (current_tf - baseline_tf) if (current_tf is not None and baseline_tf is not None) else None
        float_change_vs_previous = (current_tf - previous_tf) if (current_tf is not None and previous_tf is not None) else None
        negative_float = current_tf is not None and current_tf < 0
        newly_negative = previous_tf is not None and current_tf is not None and previous_tf >= 0 and current_tf < 0
        recovered_negative = previous_tf is not None and current_tf is not None and previous_tf < 0 and current_tf >= 0
        critical = bool(a.get('isCritical'))
        prev_critical = bool(prev.get('isCritical')) if prev else None
        newly_critical = prev is not None and (not prev_critical) and critical
        no_longer_critical = prev is not None and prev_critical and (not critical)
        near_critical = current_tf is not None and 0 < current_tf <= NEAR_CRITICAL_FLOAT_THRESHOLD
        # Current actionable critical population — a completed activity
        # never inflates this merely because its stored/imported TF or
        # isCritical flag is still zero/true. `critical` itself (above)
        # stays the untouched imported P6 signal for traceability.
        critical_actionable = critical and not finished
        imported_free_float = _num(a.get('freeFloat'))
        free_float = None if finished else imported_free_float

        # ── Logic ──
        preds, succs = pred_of.get(aid, []), succ_of.get(aid, [])
        pred_counts = _rel_counts(preds)
        succ_counts = _rel_counts(succs)
        rel_count = len(preds) + len(succs)
        is_milestone = bool(a.get('isMilestone'))
        open_start = len(preds) == 0 and not is_milestone
        open_finish = len(succs) == 0 and not is_milestone
        logic_changes = logic_by_activity.get(aid) or []
        driving_pred = _driving_predecessor(a, by_id, id_map) if not is_milestone else None

        # ── Milestone / path ──
        ms = milestone_by_id.get(aid)

        # ── Update Intelligence flags — reused, never recomputed ──
        movement = movement_by_id.get(aid)
        flags = set(movement.get('flags') or []) if movement else set()
        duration_change_entry = duration_by_activity.get(aid) or {}
        constraint_changes = constraint_by_activity.get(aid) or []

        # ── Risk & Recovery — reused, never recomputed ──
        risk = risk_by_key.get(aid)

        rows.append({
            # Identity
            'activityId': aid, 'activityName': a.get('name') or '',
            'project': a.get('projectName') or a.get('projectId') or None,
            'wbs': a.get('wbs') or '', 'wbsPath': a.get('wbsPath') or '',
            'area': a.get('area') or '', 'discipline': a.get('discipline') or '',
            'contractor': a.get('contractor') or '', 'system': a.get('system') or '',
            'activityType': a.get('type') or '', 'activityStatus': a.get('status') or '',
            # Status
            'pctComplete': pct_complete, 'pctCompleteType': a.get('pctCompleteType'),
            'physPctComplete': _num(a.get('physPctComplete')),
            'started': started, 'finished': finished, 'inProgress': in_progress,
            'shouldHaveStarted': should_have_started, 'shouldHaveFinished': should_have_finished,
            'overdue': should_have_started or should_have_finished,
            # Duration
            'originalDuration': orig_dur, 'originalDurationSource': orig_dur_source,
            'remainingDuration': remain_dur, 'actualDuration': None,
            'atCompletionDuration': at_completion_dur,
            'baselineOriginalDuration': base_orig_dur, 'baselineDuration': base_orig_dur,
            'previousRemainingDuration': prev_remain_dur, 'currentRemainingDuration': remain_dur,
            'remainingDurationChange': remain_dur_change,
            'remainingDurationGrowth': max(0.0, remain_dur_change) if remain_dur_change is not None else None,
            'remainingDurationReduction': max(0.0, -remain_dur_change) if remain_dur_change is not None else None,
            'durationVariance': duration_variance,
            # Dates
            'baselineStart': a.get('bStart') if not base else base.get('bStart'),
            'baselineFinish': a.get('bFinish') if not base else base.get('bFinish'),
            'currentStart': current_start, 'currentFinish': current_finish,
            'forecastStart': forecast_start, 'forecastFinish': forecast_finish,
            'earlyStart': a.get('earlyStart'), 'earlyFinish': a.get('earlyFinish'),
            'lateStart': a.get('lateStart'), 'lateFinish': a.get('lateFinish'),
            'currentRemainingStart': a.get('remainStart'), 'currentRemainingFinish': a.get('remainFinish'),
            'expectedFinish': a.get('expectedFinish'),
            'actualStart': a.get('start'), 'actualFinish': a.get('finish'),
            # Variance
            'startVarianceDays': start_var['calendarDays'], 'startVarianceWorkingDays': start_var['workingDays'],
            'finishVarianceDays': finish_var['calendarDays'], 'finishVarianceWorkingDays': finish_var['workingDays'],
            'calendarConfident': cal is not None,
            'startMovementDays': start_movement['calendarDays'], 'startMovementWorkingDays': start_movement['workingDays'],
            'finishMovementDays': finish_movement['calendarDays'], 'finishMovementWorkingDays': finish_movement['workingDays'],
            'updateMovementDays': movement.get('finishMovementDays') if movement else None,
            # Float
            'baselineTotalFloat': baseline_tf, 'previousTotalFloat': previous_tf, 'currentTotalFloat': current_tf,
            'importedCurrentTotalFloat': imported_current_tf,
            'floatChangeVsBaseline': float_change_vs_baseline, 'floatChangeVsPrevious': float_change_vs_previous,
            'freeFloat': free_float, 'importedFreeFloat': imported_free_float,
            'negativeFloat': negative_float, 'newlyNegativeFloat': newly_negative, 'recoveredFromNegativeFloat': recovered_negative,
            'critical': critical, 'criticalActionable': critical_actionable,
            'newlyCritical': newly_critical, 'noLongerCritical': no_longer_critical,
            'nearCritical': near_critical, 'onLongestPath': bool(a.get('onLongestPath')), 'driving': bool(a.get('onLongestPath')),
            # Logic
            'predecessorCount': len(preds), 'successorCount': len(succs), 'relationshipCount': rel_count,
            'fsCount': pred_counts['fs'] + succ_counts['fs'], 'ssCount': pred_counts['ss'] + succ_counts['ss'],
            'ffCount': pred_counts['ff'] + succ_counts['ff'], 'sfCount': pred_counts['sf'] + succ_counts['sf'],
            'positiveLag': pred_counts['positiveLag'] or succ_counts['positiveLag'],
            'negativeLag': pred_counts['negativeLag'] or succ_counts['negativeLag'],
            'maximumLag': max((v for v in (pred_counts['maxLag'], succ_counts['maxLag']) if v is not None), default=None),
            'openStart': open_start, 'openFinish': open_finish,
            'logicChanged': bool(logic_changes), 'logicChangeCount': len(logic_changes),
            'relationshipAdded': any(c.get('changeType') == 'added' for c in logic_changes) if logic_changes else False,
            'relationshipRemoved': any(c.get('changeType') == 'removed' for c in logic_changes) if logic_changes else False,
            'drivingPredecessor': driving_pred,
            # Constraints
            'constraintType': a.get('constraintType'), 'constraintDate': a.get('constraintDate'),
            'constraint2Type': a.get('constraint2Type'), 'constraint2Date': a.get('constraint2Date'),
            'constraintChanged': bool(constraint_changes),
            # Calendar
            'calendarId': a.get('calendarId'), 'calendarName': a.get('calendarName') or cmeta.get('name'),
            'calendarAvailable': cal is not None,
            'hoursPerDay': cmeta.get('hoursPerDay'), 'workingDaysPerWeek': cmeta.get('workingDaysPerWeek'),
            'workweekDescription': cmeta.get('workweekDescription'),
            # Milestone / path
            'isMilestone': is_milestone,
            'milestoneRiskLevel': ms.get('riskLevel') if ms else None,
            'milestoneVarianceDays': ms.get('varianceDays') if ms else None,
            # Update Intelligence (reused flags — never recomputed)
            'slipped': 'SLIPPED' in flags, 'improved': 'IMPROVED' in flags,
            'startedThisUpdate': 'STARTED_THIS_PERIOD' in flags, 'completedThisUpdate': 'COMPLETED_THIS_PERIOD' in flags,
            'addedSinceBaseline': movement.get('matchStatus') == 'NEW' if movement else False,
            'removedFromCurrent': movement.get('matchStatus') == 'REMOVED' if movement else False,
            'failedForecastStart': 'MISSED_FORECAST_START' in flags, 'failedForecastFinish': 'MISSED_FORECAST_FINISH' in flags,
            'floatDeteriorated': 'FLOAT_DETERIORATED' in flags or newly_negative,
            'floatImproved': 'FLOAT_IMPROVED' in flags or recovered_negative,
            'durationChanged': bool(duration_change_entry),
            'criticalityChanged': newly_critical or no_longer_critical,
            # Risk & Recovery (reused — never recomputed)
            'scheduleRisk': risk is not None,
            'riskSeverity': risk.get('severity') if risk else None,
            'riskUrgency': risk.get('urgency') if risk else None,
            'riskStatus': (risk.get('workflow') or {}).get('status') if risk else None,
            # Resources / Cost
            'budgetedLaborUnits': _num(a.get('budgetedHours')) if a.get('isResourceLoaded') else None,
            'actualLaborUnits': _num(a.get('actualHours')) if a.get('isResourceLoaded') else None,
            'remainingLaborUnits': _num(a.get('remainingHours')) if a.get('isResourceLoaded') else None,
            'atCompletionLaborUnits': (
                (_num(a.get('actualHours')) or 0.0) + (_num(a.get('remainingHours')) or 0.0)
            ) if a.get('isResourceLoaded') else None,
            'budgetedCost': _num(a.get('budgetedCost')) if a.get('isCostLoaded') else None,
            'actualCost': _num(a.get('actualCost')) if a.get('isCostLoaded') else None,
            'remainingCost': _num(a.get('remainingCost')) if a.get('isCostLoaded') else None,
            'atCompletionCost': (
                (_num(a.get('actualCost')) or 0.0) + (_num(a.get('remainingCost')) or 0.0)
            ) if a.get('isCostLoaded') else None,
            'resourceLoaded': bool(a.get('isResourceLoaded')), 'costLoaded': bool(a.get('isCostLoaded')),
        })

    return {
        'engineVersion': ENGINE_VERSION,
        'rowCount': len(rows),
        'rows': rows,
        'calendarConfidence': {
            'activitiesWithCalendar': activities_with_calendar,
            'totalActivities': len(current_activities),
            'available': activities_with_calendar > 0,
        },
    }
