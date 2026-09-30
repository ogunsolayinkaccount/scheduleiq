"""
Schedule Risk Engine — ScheduleIQ Phase 9

Deterministic, transparent risk scoring (same DB-free pattern as
status_engine.py / quality_engine.py / schedule_comparison.py). Every score
is the sum of named, capped "drivers" — never a black box — so a UI can
always render "Score 86 — driven by 18 negative-float activities, a 14-day
average finish slip, and 3 newly-critical activities" directly from the
returned structure.

Two scoring granularities:
  - score_activity()  — one activity, used for "top risk activities" lists.
  - score_group()     — an aggregate (WBS/Area/Discipline/Contractor/System/
                         whole version), driven by concentration of at-risk
                         activities within the group, not by averaging
                         individual activity scores (averaging would hide a
                         group with a small number of severely negative-float
                         activities behind a sea of healthy ones).

Comparison-based drivers (finish slip, newly critical, newly negative float,
logic changes) only appear when a `comparison_result` (from
schedule_comparison.compare_schedules) is supplied — never fabricated when
only one schedule version is available, matching the rest of ScheduleIQ's
"don't guess" convention.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

# Mirrors quality_engine.py's hard-constraint set — kept as a small local
# copy rather than importing a private helper, so this module stays
# independently testable like its sibling engines.
HARD_CONSTRAINT_TYPES = {
    'CS_MANDFIN', 'CS_MANDSTART', 'MSO', 'MFO', 'MANDATORY_START', 'MANDATORY_FINISH',
}

RISK_LEVELS = ('Healthy', 'Watch', 'At Risk', 'Critical')


def risk_level(score: float) -> str:
    if score >= 75:
        return 'Critical'
    if score >= 50:
        return 'At Risk'
    if score >= 25:
        return 'Watch'
    return 'Healthy'


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


# Canonical rule (activity_analysis.is_activity_complete): actual finish date
# present OR pctComplete >= 100. This file previously used a NARROWER rule
# (pctComplete >= 100 only) that could disagree with Float Intelligence/Risk
# Register about the same activity - centralized to the approved rule.
from .activity_analysis import is_activity_complete as _is_complete


def _overdue(a: dict, dd: date) -> bool:
    if a.get('isMilestone') or _is_complete(a):
        return False
    f = _parse_date(a.get('bFinish') or a.get('finish'))
    return bool(f and dd and f < dd)


def _incomplete_before_dd(a: dict, dd: date) -> bool:
    if a.get('isMilestone') or _is_complete(a):
        return False
    f = _parse_date(a.get('bFinish') or a.get('finish'))
    return bool(f and dd and f < dd)


def _lacks_progress(a: dict, dd: date) -> bool:
    """Schedule says work should already be underway, but 0% is logged."""
    if a.get('isMilestone') or (a.get('pctComplete') or 0) > 0:
        return False
    s = _parse_date(a.get('bStart'))
    return bool(s and dd and s <= dd)


def _driver(type_: str, impact: float, **extra) -> Dict[str, Any]:
    d = {'type': type_, 'impact': round(max(0.0, impact), 1)}
    d.update(extra)
    return d


# ── Activity-level scoring ──────────────────────────────────────────────────

def score_activity(a: dict, data_date: date, near_critical_days: float = 10.0) -> Dict[str, Any]:
    """One activity's risk — used for 'top risk activities' lists."""
    factors: List[Dict[str, Any]] = []
    tf = _tf(a)

    if not a.get('isMilestone') and not _is_complete(a):
        if tf is not None and tf < 0:
            factors.append(_driver('negative_float', min(35, 20 + abs(tf)), floatDays=tf))
        elif a.get('isCritical'):
            factors.append(_driver('critical', 15))
        elif tf is not None and 0 <= tf <= near_critical_days:
            proximity = (near_critical_days - tf) / near_critical_days if near_critical_days else 0
            factors.append(_driver('near_critical', min(12, 4 + proximity * 8), floatDays=tf))

        if _overdue(a, data_date):
            factors.append(_driver('overdue', 20))
        elif _incomplete_before_dd(a, data_date):
            factors.append(_driver('incomplete_before_data_date', 10))

        if a.get('constraintType') in HARD_CONSTRAINT_TYPES:
            factors.append(_driver('hard_constraint', 8, constraintType=a.get('constraintType')))

        if _lacks_progress(a, data_date):
            factors.append(_driver('lack_of_progress', 10))

        pred_count = len(a.get('predecessors') or [])
        if pred_count > 4:
            factors.append(_driver('dependency_complexity', min(5, (pred_count - 4)), predecessorCount=pred_count))

    score = min(100.0, sum(f['impact'] for f in factors))
    return {
        'activityId': _act_id(a),
        'activityName': a.get('name', ''),
        'wbs': a.get('wbs', ''),
        'score': round(score, 1),
        'level': risk_level(score),
        'factors': factors,
    }


# ── Group-level scoring ─────────────────────────────────────────────────────

def _scoped_comparison_stats(activity_ids: set, comparison_result: dict) -> Dict[str, Any]:
    """Filter a full compare_schedules() result down to one group's activities."""
    changes = [c for c in comparison_result['activities']['changes'] if c['activityId'] in activity_ids]
    finish_changes = [c for c in changes if c['fieldKey'] == 'bFinish' and (c.get('deltaDays') or 0) > 0]
    rel_changes = [c for c in comparison_result['relationships']['changed'] if c['successorId'] in activity_ids]
    return {'finishChanges': finish_changes, 'relationshipChanges': rel_changes}


def score_group(
    activities: List[dict],
    data_date: date,
    near_critical_days: float = 10.0,
    comparison_result: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Aggregate risk for a set of activities (a WBS bucket, an Area, a whole
    schedule version, ...). Driven by the CONCENTRATION of at-risk activities
    within the set, not by averaging individual activity scores.
    """
    non_ms_incomplete = [a for a in activities if not a.get('isMilestone') and not _is_complete(a)]
    total = len(non_ms_incomplete) or 1
    drivers: List[Dict[str, Any]] = []

    def pct_driver(subset, dtype, max_impact, base=0.0, mult=1.0):
        if not subset:
            return
        pct = len(subset) / total
        impact = min(max_impact, base + pct * max_impact * mult)
        drivers.append(_driver(dtype, impact, count=len(subset), pct=round(pct * 100, 1)))

    neg = [a for a in non_ms_incomplete if (_tf(a) or 0) < 0 and _tf(a) is not None]
    pct_driver(neg, 'negative_float', 30, base=8, mult=1.4)

    crit = [a for a in non_ms_incomplete if a.get('isCritical')]
    pct_driver(crit, 'critical_concentration', 15, mult=1.2)

    near = [a for a in non_ms_incomplete if a.get('isCritical') is False and _tf(a) is not None and 0 <= _tf(a) <= near_critical_days]
    pct_driver(near, 'near_critical_concentration', 10, mult=1.0)

    overdue = [a for a in non_ms_incomplete if _overdue(a, data_date)]
    pct_driver(overdue, 'overdue', 15, mult=1.3)

    before_dd = [a for a in non_ms_incomplete if _incomplete_before_dd(a, data_date)]
    pct_driver(before_dd, 'incomplete_before_data_date', 10, mult=1.0)

    hard = [a for a in non_ms_incomplete if a.get('constraintType') in HARD_CONSTRAINT_TYPES]
    pct_driver(hard, 'hard_constraints', 10, mult=1.2)

    no_progress = [a for a in non_ms_incomplete if _lacks_progress(a, data_date)]
    pct_driver(no_progress, 'lack_of_progress', 10, mult=1.0)

    high_dep = [a for a in non_ms_incomplete if len(a.get('predecessors') or []) > 4]
    pct_driver(high_dep, 'dependency_complexity', 5, mult=0.8)

    if comparison_result is not None:
        activity_ids = {_act_id(a) for a in activities}
        scoped = _scoped_comparison_stats(activity_ids, comparison_result)

        finish_changes = scoped['finishChanges']
        if finish_changes:
            avg_days = sum(c['deltaDays'] for c in finish_changes) / len(finish_changes)
            impact = min(20, 5 + avg_days * 0.8)
            drivers.append(_driver('finish_slip', impact, count=len(finish_changes), averageDays=round(avg_days, 1)))

        # newlyCriticalCount/newlyNegativeFloatCount on compare_activities()
        # are version-wide totals — scope to this group by checking which of
        # its own activities actually flipped from >=0 float to negative.
        newly_negative = [
            c for c in comparison_result['activities']['changes']
            if c['activityId'] in activity_ids and c['fieldKey'] == 'totalFloat'
            and _to_num(c.get('previous')) is not None and _to_num(c.get('current')) is not None
            and _to_num(c['previous']) >= 0 > _to_num(c['current'])
        ]
        if newly_negative:
            impact = min(15, len(newly_negative) * 3)
            drivers.append(_driver('newly_negative_float', impact, count=len(newly_negative)))

        rel_added = [r for r in comparison_result['relationships']['added'] if r['successorId'] in activity_ids]
        rel_removed = [r for r in comparison_result['relationships']['removed'] if r['successorId'] in activity_ids]
        logic_total = len(rel_added) + len(rel_removed) + len(scoped['relationshipChanges'])
        if logic_total:
            impact = min(10, logic_total * 1.5)
            drivers.append(_driver('logic_changes', impact, count=logic_total))

    score = min(100.0, sum(d['impact'] for d in drivers))
    affected_ids = {_act_id(a) for a in (neg + crit + near + overdue + before_dd)}
    return {
        'score': round(score, 1),
        'level': risk_level(score),
        'drivers': sorted(drivers, key=lambda d: -d['impact']),
        'affectedActivityCount': len(affected_ids),
        'totalActivityCount': len(activities),
        'incompleteActivityCount': len(non_ms_incomplete),
    }


def _to_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── Grouping ─────────────────────────────────────────────────────────────────

_GROUP_FIELD_MAP = {
    'wbs': 'wbs', 'area': 'area', 'discipline': 'discipline',
    'contractor': 'contractor', 'system': 'system',
}


def compute_risk(
    activities: List[dict],
    data_date: date,
    group_by: Optional[str] = None,
    near_critical_days: float = 10.0,
    comparison_result: Optional[dict] = None,
    top_n: int = 50,
) -> Dict[str, Any]:
    """
    Main entry point. `group_by` in (None, 'activity', 'wbs', 'area',
    'discipline', 'contractor', 'system'). Always includes an 'overall'
    (whole-version) score regardless of group_by.
    """
    overall = score_group(activities, data_date, near_critical_days, comparison_result)

    result: Dict[str, Any] = {
        'overall': overall,
        'groupBy': group_by or 'project',
        'nearCriticalThresholdDays': near_critical_days,
        'hasComparison': comparison_result is not None,
    }

    if not group_by or group_by == 'project':
        result['groups'] = []
        return result

    if group_by == 'activity':
        scored = [
            score_activity(a, data_date, near_critical_days)
            for a in activities if not a.get('isMilestone') and not _is_complete(a)
        ]
        scored.sort(key=lambda s: -s['score'])
        result['groups'] = scored[:top_n]
        result['totalScoredActivities'] = len(scored)
        return result

    field = _GROUP_FIELD_MAP.get(group_by)
    if not field:
        result['groups'] = []
        result['error'] = f'Unknown group_by value: {group_by}'
        return result

    buckets: Dict[str, List[dict]] = {}
    ungrouped = 0
    for a in activities:
        key = a.get(field)
        if not key:
            ungrouped += 1
            continue
        buckets.setdefault(key, []).append(a)

    groups = []
    for key, acts in buckets.items():
        g = score_group(acts, data_date, near_critical_days, comparison_result)
        g['key'] = key
        groups.append(g)
    groups.sort(key=lambda g: -g['score'])

    result['groups'] = groups
    result['ungroupedActivityCount'] = ungrouped
    return result
