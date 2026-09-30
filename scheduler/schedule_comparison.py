"""
Schedule Version Comparison Engine — ScheduleIQ Phase 8

Pure, DB-free comparison of two activity lists (same pattern as
status_engine.py / quality_engine.py) so it's unit-testable in isolation and
reusable regardless of how the two versions were loaded.

Terminology note (matches the rest of ScheduleIQ, e.g. utils.py's variance
calculations): "Start"/"Finish" compare bStart/bFinish — the schedule's
current planned dates — while "Actual Start"/"Actual Finish" compare
start/finish, which only carry a value once an activity has actually begun
or finished. This mirrors how VarianceView/HistogramView already use these
two field pairs, so a comparison reads consistently with the rest of the app.

All date deltas are calendar-day deltas by default. When a caller supplies
a `calendars` map (activity calendarId -> calendar_engine.CalendarDefinition,
built from confidently-decoded P6 calendar data — see calendar_engine.py),
date-field changes additionally carry a `deltaWorkingDays` figure computed
on that specific activity's calendar, alongside the existing calendar-day
`deltaDays`. This is purely additive: deltaDays/deltaUnit are unchanged
either way, so existing comparison results and callers are unaffected.
When no calendar is available for an activity, `deltaWorkingDays` is
explicitly `None` — never a guessed Mon-Fri figure — so the frontend can
render "Working-day variance: unavailable."
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from .calendar_engine import CalendarDefinition, working_days_between

ENGINE_VERSION = '1.0.0'

# (json_key, label, type, category) — type in {'text','date','number'}
FIELD_SPECS: List[Tuple[str, str, str, str]] = [
    ('name',           'Activity Name',      'text',   'General'),
    ('wbs',            'WBS',                 'text',   'General'),
    ('bStart',         'Start',               'date',   'Schedule'),
    ('bFinish',         'Finish',              'date',   'Schedule'),
    ('dur',             'Original Duration',   'number', 'Duration'),
    ('remainDur',       'Remaining Duration',  'number', 'Duration'),
    ('totalFloat',      'Total Float',          'number', 'Float'),
    ('freeFloat',       'Free Float',           'number', 'Float'),
    ('pctComplete',     'Percent Complete',     'number', 'Progress'),
    ('start',           'Actual Start',         'date',   'Progress'),
    ('finish',          'Actual Finish',        'date',   'Progress'),
    ('constraintType',  'Constraint Type',      'text',   'Constraints'),
    ('constraintDate',  'Constraint Date',      'date',   'Constraints'),
    ('_calendar',        'Calendar',             'text',   'Logic'),
    ('contractor',       'Contractor',           'text',   'Metadata'),
    ('discipline',       'Discipline',           'text',   'Metadata'),
    ('area',              'Area',                 'text',   'Metadata'),
    ('system',            'System',               'text',   'Metadata'),
]

_NUMERIC_TOLERANCE = 1e-6


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


def _calendar_of(a: dict) -> str:
    return a.get('calendarName') or a.get('calendar') or ''


def _field_value(a: dict, key: str):
    if key == '_calendar':
        return _calendar_of(a)
    return a.get(key)


def _values_equal(v1, v2, ftype: str) -> bool:
    if ftype == 'date':
        return _parse_date(v1) == _parse_date(v2)
    if ftype == 'number':
        n1 = v1 if isinstance(v1, (int, float)) else (float(v1) if _is_numlike(v1) else None)
        n2 = v2 if isinstance(v2, (int, float)) else (float(v2) if _is_numlike(v2) else None)
        if n1 is None and n2 is None:
            return True
        if n1 is None or n2 is None:
            return False
        return abs(n1 - n2) < _NUMERIC_TOLERANCE
    # text
    s1 = (str(v1).strip() if v1 not in (None, '') else '')
    s2 = (str(v2).strip() if v2 not in (None, '') else '')
    return s1 == s2


def _is_numlike(v) -> bool:
    if v is None or v == '':
        return False
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _severity_for(field_key: str, prev, curr, ftype: str) -> str:
    if field_key == 'totalFloat':
        p = float(prev) if _is_numlike(prev) else None
        c = float(curr) if _is_numlike(curr) else None
        if p is not None and c is not None:
            if c < 0 and p >= 0:
                return 'high'
            if c is not None and c < 0:
                return 'high'
            delta = c - p
            if delta <= -10:
                return 'high'
            if delta <= -3:
                return 'medium'
        return 'low'
    if field_key == 'bFinish':
        p, c = _parse_date(prev), _parse_date(curr)
        if p and c:
            delta = (c - p).days
            if delta > 14:
                return 'high'
            if delta > 3:
                return 'medium'
            if delta > 0:
                return 'low'
        return 'info'
    if field_key == 'pctComplete':
        p = float(prev) if _is_numlike(prev) else None
        c = float(curr) if _is_numlike(curr) else None
        if p is not None and c is not None and c < p:
            return 'medium'   # progress regression is unusual
        return 'info'
    if field_key == 'constraintType':
        return 'medium' if curr and not prev else 'info'
    return 'info'


def _id_to_code_name(activities: List[dict]) -> Dict[str, Tuple[str, str]]:
    """{taskId -> (activityId/code, activityName)} for resolving a
    predecessor/successor `actId` — which the P6 XER parser records as the
    source file's raw internal task_id (TASKPRED.pred_task_id), NOT the
    human-readable activity code — back to something a scheduler actually
    recognizes. Keyed by each activity's own `id` (its raw task_id); when an
    import format has no distinct task_id from its code (id == code, as for
    non-XER sources), this is a harmless identity mapping."""
    out: Dict[str, Tuple[str, str]] = {}
    for a in activities:
        raw_id = str(a.get('id') or '')
        if raw_id:
            out[raw_id] = (_act_id(a), a.get('name') or '')
    return out


def _relationship_keys(
    activities: List[dict], id_map: Optional[Dict[str, Tuple[str, str]]] = None,
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """{(predId, succId): {relType, lagDays}} built from each activity's
    predecessors list — predId is resolved through `id_map` (task_id ->
    code) when available, so it's always a real Activity ID, consistent
    with succId (already `_act_id(a)`, i.e. code). Never guessed: a
    predecessor actId that isn't in id_map is left as-is (e.g. it points
    outside the supplied activity list) rather than fabricated."""
    id_map = id_map or {}
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for a in activities:
        succ_id = _act_id(a)
        for pred in (a.get('predecessors') or []):
            raw_pred_id = str(pred.get('actId') or '')
            if not raw_pred_id:
                continue
            pred_id = id_map.get(raw_pred_id, (raw_pred_id, ''))[0]
            out[(pred_id, succ_id)] = {
                'relType': pred.get('relType') or 'FS',
                'lagDays': pred.get('lagDays') if pred.get('lagDays') is not None else 0,
            }
    return out


def compare_relationships(previous_activities: List[dict], current_activities: List[dict]) -> Dict[str, Any]:
    # Resolve against BOTH lists — a predecessor referenced from the current
    # list should resolve via current's own id map, and same for previous;
    # relationships are keyed by resolved code either way so matching a
    # relationship across versions no longer silently depends on task_ids
    # staying numerically identical between two imports of the same project.
    prev_id_map = _id_to_code_name(previous_activities)
    curr_id_map = _id_to_code_name(current_activities)
    prev_rels = _relationship_keys(previous_activities, prev_id_map)
    curr_rels = _relationship_keys(current_activities, curr_id_map)

    added = [k for k in curr_rels if k not in prev_rels]
    removed = [k for k in prev_rels if k not in curr_rels]
    common = [k for k in curr_rels if k in prev_rels]

    changes = []
    for k in common:
        p, c = prev_rels[k], curr_rels[k]
        if p['relType'] != c['relType']:
            changes.append({
                'predecessorId': k[0], 'successorId': k[1], 'field': 'relationshipType',
                'previous': p['relType'], 'current': c['relType'],
            })
        try:
            lag_changed = abs(float(p['lagDays'] or 0) - float(c['lagDays'] or 0)) >= _NUMERIC_TOLERANCE
        except (TypeError, ValueError):
            lag_changed = p['lagDays'] != c['lagDays']
        if lag_changed:
            changes.append({
                'predecessorId': k[0], 'successorId': k[1], 'field': 'lag',
                'previous': p['lagDays'], 'current': c['lagDays'],
                'delta': (c['lagDays'] or 0) - (p['lagDays'] or 0),
            })

    return {
        'added': [{'predecessorId': k[0], 'successorId': k[1], **curr_rels[k]} for k in added],
        'removed': [{'predecessorId': k[0], 'successorId': k[1], **prev_rels[k]} for k in removed],
        'changed': changes,
        'addedCount': len(added),
        'removedCount': len(removed),
        'changedCount': len(changes),
        'unchangedCount': len(common) - len(changes),
    }


def _calendar_for(
    p: dict, c: dict, calendars: Optional[Dict[str, CalendarDefinition]]
) -> Optional[CalendarDefinition]:
    if not calendars:
        return None
    cal_id = c.get('calendarId') or p.get('calendarId')
    return calendars.get(cal_id) if cal_id else None


def compare_activities(
    previous_activities: List[dict],
    current_activities: List[dict],
    calendars: Optional[Dict[str, CalendarDefinition]] = None,
) -> Dict[str, Any]:
    prev_by_id = {_act_id(a): a for a in previous_activities if _act_id(a)}
    curr_by_id = {_act_id(a): a for a in current_activities if _act_id(a)}

    added_ids = [i for i in curr_by_id if i not in prev_by_id]
    removed_ids = [i for i in prev_by_id if i not in curr_by_id]
    common_ids = [i for i in curr_by_id if i in prev_by_id]

    activity_changes: List[Dict[str, Any]] = []
    moved_later = moved_earlier = 0
    float_deteriorated = float_improved = 0
    newly_critical = no_longer_critical = 0
    newly_negative = recovered_negative = 0

    for aid in common_ids:
        p, c = prev_by_id[aid], curr_by_id[aid]
        for field_key, label, ftype, category in FIELD_SPECS:
            pv, cv = _field_value(p, field_key), _field_value(c, field_key)
            if _values_equal(pv, cv, ftype):
                continue
            entry = {
                'activityId': aid,
                'activityName': c.get('name') or p.get('name') or '',
                'field': label,
                'fieldKey': field_key,
                'category': category,
                'previous': pv.isoformat() if isinstance(pv, date) else pv,
                'current': cv.isoformat() if isinstance(cv, date) else cv,
                'severity': _severity_for(field_key, pv, cv, ftype),
            }
            if ftype == 'number' and _is_numlike(pv) and _is_numlike(cv):
                entry['delta'] = round(float(cv) - float(pv), 2)
            elif ftype == 'date':
                pd_, cd_ = _parse_date(pv), _parse_date(cv)
                if pd_ and cd_:
                    entry['deltaDays'] = (cd_ - pd_).days
                    entry['deltaUnit'] = 'calendar_days'
                    cal = _calendar_for(p, c, calendars)
                    entry['deltaWorkingDays'] = working_days_between(pd_, cd_, cal) if cal else None
            activity_changes.append(entry)

        # Roll-up counters (non-milestone only for movement stats)
        if not c.get('isMilestone'):
            pf, cf = _parse_date(p.get('bFinish')), _parse_date(c.get('bFinish'))
            if pf and cf and pf != cf:
                if cf > pf:
                    moved_later += 1
                else:
                    moved_earlier += 1

            p_tf = float(p['totalFloat']) if _is_numlike(p.get('totalFloat')) else None
            c_tf = float(c['totalFloat']) if _is_numlike(c.get('totalFloat')) else None
            if p_tf is not None and c_tf is not None and abs(c_tf - p_tf) >= _NUMERIC_TOLERANCE:
                if c_tf < p_tf:
                    float_deteriorated += 1
                else:
                    float_improved += 1
            if p_tf is not None and c_tf is not None:
                if c_tf < 0 and p_tf >= 0:
                    newly_negative += 1
                elif c_tf >= 0 and p_tf < 0:
                    recovered_negative += 1

            p_crit, c_crit = bool(p.get('isCritical')), bool(c.get('isCritical'))
            if c_crit and not p_crit:
                newly_critical += 1
            elif p_crit and not c_crit:
                no_longer_critical += 1

    added = [
        {
            'activityId': i, 'activityName': curr_by_id[i].get('name', ''),
            'wbs': curr_by_id[i].get('wbs', ''), 'bFinish': curr_by_id[i].get('bFinish'),
            'isMilestone': bool(curr_by_id[i].get('isMilestone')),
        }
        for i in added_ids
    ]
    removed = [
        {
            'activityId': i, 'activityName': prev_by_id[i].get('name', ''),
            'wbs': prev_by_id[i].get('wbs', ''), 'bFinish': prev_by_id[i].get('bFinish'),
            'isMilestone': bool(prev_by_id[i].get('isMilestone')),
        }
        for i in removed_ids
    ]

    return {
        'added': added,
        'removed': removed,
        'changes': activity_changes,
        'countBefore': len(prev_by_id),
        'countAfter': len(curr_by_id),
        'addedCount': len(added_ids),
        'removedCount': len(removed_ids),
        'commonCount': len(common_ids),
        'changedActivityCount': len({c['activityId'] for c in activity_changes}),
        'movedLaterCount': moved_later,
        'movedEarlierCount': moved_earlier,
        'floatDeterioratedCount': float_deteriorated,
        'floatImprovedCount': float_improved,
        'newlyCriticalCount': newly_critical,
        'noLongerCriticalCount': no_longer_critical,
        'newlyNegativeFloatCount': newly_negative,
        'recoveredFromNegativeFloatCount': recovered_negative,
    }


def _milestone_movement(
    previous_activities: List[dict],
    current_activities: List[dict],
    calendars: Optional[Dict[str, CalendarDefinition]] = None,
) -> List[Dict[str, Any]]:
    prev_by_id = {_act_id(a): a for a in previous_activities if a.get('isMilestone')}
    moves = []
    for c in current_activities:
        if not c.get('isMilestone'):
            continue
        aid = _act_id(c)
        p = prev_by_id.get(aid)
        if not p:
            continue
        pf, cf = _parse_date(p.get('bFinish')), _parse_date(c.get('bFinish'))
        if pf and cf and pf != cf:
            cal = _calendar_for(p, c, calendars)
            moves.append({
                'activityId': aid, 'activityName': c.get('name', ''),
                'previousFinish': pf.isoformat(), 'currentFinish': cf.isoformat(),
                'deltaDays': (cf - pf).days,
                'deltaWorkingDays': working_days_between(pf, cf, cal) if cal else None,
            })
    return moves


def build_insights(activity_result: Dict[str, Any], milestone_moves: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    insights: List[Dict[str, Any]] = []

    for m in sorted(milestone_moves, key=lambda x: -abs(x['deltaDays']))[:10]:
        insights.append({
            'type': 'milestone_slip' if m['deltaDays'] > 0 else 'milestone_gain',
            'severity': 'high' if abs(m['deltaDays']) > 14 else 'medium' if abs(m['deltaDays']) > 3 else 'low',
            'activityId': m['activityId'],
            'activityName': m['activityName'],
            'previousFinish': m['previousFinish'],
            'currentFinish': m['currentFinish'],
            'deltaDays': m['deltaDays'],
            'deltaUnit': 'calendar_days',
        })

    for c in activity_result['changes']:
        if c['fieldKey'] == 'totalFloat' and c['severity'] == 'high':
            curr_tf = c['current']
            insights.append({
                'type': 'newly_negative_float' if (_is_numlike(curr_tf) and float(curr_tf) < 0) else 'float_deterioration',
                'severity': 'high',
                'activityId': c['activityId'],
                'activityName': c['activityName'],
                'previousFloat': c['previous'],
                'currentFloat': c['current'],
                'delta': c.get('delta'),
            })
        if c['fieldKey'] == 'dur' and c.get('delta') is not None and abs(c['delta']) >= 5:
            insights.append({
                'type': 'large_duration_increase' if c['delta'] > 0 else 'large_duration_decrease',
                'severity': 'medium',
                'activityId': c['activityId'],
                'activityName': c['activityName'],
                'previousDuration': c['previous'],
                'currentDuration': c['current'],
                'delta': c['delta'],
            })

    return insights


def _group_movement_by(activities_by_id: Dict[str, dict], moved_ids: List[str], group_field: str) -> Optional[Tuple[str, int]]:
    counts: Dict[str, int] = {}
    for aid in moved_ids:
        a = activities_by_id.get(aid)
        if not a:
            continue
        key = a.get(group_field)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    top = max(counts.items(), key=lambda kv: kv[1])
    return top


def build_narrative(
    activity_result: Dict[str, Any],
    relationship_result: Dict[str, Any],
    milestone_moves: List[Dict[str, Any]],
    current_activities: List[dict],
) -> str:
    """
    Deterministic, evidence-based "What Changed This Update?" paragraph.
    Every number here traces directly to activity_result/relationship_result —
    nothing is invented, and no project-specific example values are hardcoded.
    """
    sentences: List[str] = []
    curr_by_id = {_act_id(a): a for a in current_activities}

    moved_later = activity_result['movedLaterCount']
    if moved_later > 0:
        s = f"{moved_later} activit{'y' if moved_later == 1 else 'ies'} moved later in the current update."
        # Which grouping field actually has data — try area, then discipline, then wbs.
        moved_later_ids = [
            c['activityId'] for c in activity_result['changes']
            if c['fieldKey'] == 'bFinish' and _is_numlike(c.get('deltaDays')) and c['deltaDays'] > 0
        ]
        for group_field, label in (('area', 'Area'), ('discipline', 'Discipline'), ('wbs', 'WBS')):
            top = _group_movement_by(curr_by_id, moved_later_ids, group_field)
            if top:
                name, count = top
                s += f" {label} {name} contains {count} of those movements, the largest concentration of schedule slippage."
                break
        sentences.append(s)

    if activity_result['movedEarlierCount'] > 0:
        sentences.append(f"{activity_result['movedEarlierCount']} activities moved earlier.")

    if activity_result['newlyCriticalCount'] > 0:
        sentences.append(f"{activity_result['newlyCriticalCount']} activities newly entered critical status.")
    if activity_result['noLongerCriticalCount'] > 0:
        sentences.append(f"{activity_result['noLongerCriticalCount']} activities are no longer on the critical path.")

    if activity_result['newlyNegativeFloatCount'] > 0:
        sentences.append(f"{activity_result['newlyNegativeFloatCount']} activities newly went negative on total float.")
    if activity_result['recoveredFromNegativeFloatCount'] > 0:
        sentences.append(f"{activity_result['recoveredFromNegativeFloatCount']} activities recovered from negative float.")

    if milestone_moves:
        worst = max(milestone_moves, key=lambda m: abs(m['deltaDays']))
        direction = 'later' if worst['deltaDays'] > 0 else 'earlier'
        sentences.append(
            f"The {worst['activityName'] or worst['activityId']} milestone moved "
            f"{abs(worst['deltaDays'])} calendar days {direction}."
        )

    if activity_result['addedCount'] > 0:
        sentences.append(f"{activity_result['addedCount']} activities were added to the schedule.")
    if activity_result['removedCount'] > 0:
        sentences.append(f"{activity_result['removedCount']} activities were removed.")

    if relationship_result['addedCount'] or relationship_result['removedCount'] or relationship_result['changedCount']:
        parts = []
        if relationship_result['addedCount']:
            parts.append(f"{relationship_result['addedCount']} added")
        if relationship_result['removedCount']:
            parts.append(f"{relationship_result['removedCount']} removed")
        if relationship_result['changedCount']:
            parts.append(f"{relationship_result['changedCount']} changed")
        sentences.append(f"Logic changes: {', '.join(parts)} relationship(s).")

    if not sentences:
        return 'No material differences were detected between these two schedule versions.'

    return ' '.join(sentences)


def compare_schedules(
    previous_activities: List[dict],
    current_activities: List[dict],
    calendars: Optional[Dict[str, CalendarDefinition]] = None,
) -> Dict[str, Any]:
    """Main entry point — full comparison result, DB-free and pure.

    `calendars` (optional) maps activity calendarId -> CalendarDefinition
    for confidently-decoded P6 calendars. When supplied, date-field changes
    and milestone moves additionally carry a working-day-aware
    `deltaWorkingDays` alongside the always-present calendar-day `deltaDays`.
    Omitting it (the default) reproduces prior calendar-day-only behavior
    exactly — this parameter is purely additive.
    """
    activity_result = compare_activities(previous_activities, current_activities, calendars)
    relationship_result = compare_relationships(previous_activities, current_activities)
    milestone_moves = _milestone_movement(previous_activities, current_activities, calendars)
    insights = build_insights(activity_result, milestone_moves)
    narrative = build_narrative(activity_result, relationship_result, milestone_moves, current_activities)

    return {
        'engineVersion': ENGINE_VERSION,
        'activities': activity_result,
        'relationships': relationship_result,
        'milestoneMovement': milestone_moves,
        'insights': insights,
        'summaryNarrative': narrative,
    }
