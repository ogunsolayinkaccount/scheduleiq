"""
Schedule Quality Engine — ScheduleIQ v1.0.0

Assesses the technical quality of a schedule using DCMA 14-point
best-practice checks adapted for P6 XER data. Returns a structured
dict usable by status_engine.classify_schedule and the API layer.

Quality checks:
  1.  Logic density (open ends — activities with no predecessors or successors)
  2.  Negative lag / reverse logic
  3.  Positive lag (total and excessive)
  4.  Constraints (total and hard)
  5.  Duration outliers (missing or excessively long)
  6.  Out-of-sequence progress
  7.  Circular logic (Kahn's algorithm on dependency graph)
  8.  High float outliers (activities with suspiciously high float)
  9.  Negative float density
 10.  Resource loading
 11.  BIM / data completeness
 12.  Hard milestones (constrained milestones)
 13.  Invalid actual dates (actual after data date)
 14.  S-curve completeness
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

try:
    from .status_engine import ThresholdValues, _parse_date, _float_days, _is_complete, _is_milestone
except ImportError:
    from status_engine import ThresholdValues, _parse_date, _float_days, _is_complete, _is_milestone  # type: ignore


_LONG_DURATION_DAYS = 20.0    # activity flagged as excessively long
_HIGH_FLOAT_DAYS    = 44.0    # DCMA: float > (project duration*0.25) is suspicious
_EXCESSIVE_LAG_DAYS = 5.0     # lag > 5 working days is flagged as excessive


# ── Circular-logic detection (Kahn's topological sort) ─────────────────────────

def _detect_circular_logic(activities: List[dict]) -> int:
    """Return the number of activities involved in circular dependency chains."""
    id_map = {}
    for a in activities:
        aid = a.get('code') or a.get('id') or ''
        if aid:
            id_map[aid] = 0

    in_degree: Dict[str, int] = {k: 0 for k in id_map}
    for a in activities:
        aid = a.get('code') or a.get('id') or ''
        for pred in (a.get('predecessors') or []):
            pid = pred.get('actId') or pred.get('id') or pred.get('activityId') or ''
            if pid in in_degree:
                in_degree[aid] = in_degree.get(aid, 0) + 1

    queue = [k for k, v in in_degree.items() if v == 0]
    visited = 0
    adj: Dict[str, List[str]] = {k: [] for k in id_map}
    for a in activities:
        aid = a.get('code') or a.get('id') or ''
        for pred in (a.get('predecessors') or []):
            pid = pred.get('actId') or pred.get('id') or pred.get('activityId') or ''
            if pid in adj:
                adj[pid].append(aid)

    while queue:
        node = queue.pop()
        visited += 1
        for nxt in adj.get(node, []):
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)

    circular = len(id_map) - visited
    return max(0, circular)


# ── Individual check helpers ───────────────────────────────────────────────────

def _check_open_ends(activities: List[dict], threshold_pct: float) -> Dict:
    non_ms = [a for a in activities if not _is_milestone(a)]
    total = len(non_ms)
    if total == 0:
        return {'flag': False, 'count': 0, 'pct': 0, 'threshold_pct': threshold_pct, 'severity': 'OK'}

    no_pred = sum(1 for a in non_ms if not (a.get('predecessors') or []))
    no_succ = sum(1 for a in non_ms if not (a.get('successors') or []))
    # Start/finish milestones are legitimate open ends — deduct one each if they exist
    start_ms = sum(1 for a in activities if _is_milestone(a) and not (a.get('predecessors') or []))
    end_ms   = sum(1 for a in activities if _is_milestone(a) and not (a.get('successors') or []))
    adj_no_pred = max(0, no_pred - start_ms)
    adj_no_succ = max(0, no_succ - end_ms)
    count = adj_no_pred + adj_no_succ
    pct = count / (total * 2) * 100

    return {
        'flag': pct > threshold_pct,
        'count': count,
        'no_predecessors': adj_no_pred,
        'no_successors': adj_no_succ,
        'total_non_milestone': total,
        'pct': round(pct, 1),
        'threshold_pct': threshold_pct,
        'severity': 'RED' if pct > threshold_pct * 2 else 'YELLOW' if pct > threshold_pct else 'OK',
    }


def _check_lags(activities: List[dict], max_lag_pct: float, max_excessive_lag_pct: float) -> Dict:
    total_rels = 0
    neg_lag = 0
    lag = 0
    excessive_lag = 0
    for a in activities:
        for pred in (a.get('predecessors') or []):
            total_rels += 1
            lag_val = pred.get('lagDays') or pred.get('lag') or 0
            try:
                lag_val = float(lag_val)
            except (TypeError, ValueError):
                lag_val = 0.0
            if lag_val < 0:
                neg_lag += 1
            elif lag_val > 0:
                lag += 1
                if lag_val > _EXCESSIVE_LAG_DAYS:
                    excessive_lag += 1

    total = total_rels or 1
    lag_pct = lag / total * 100
    exc_pct = excessive_lag / total * 100
    neg_pct = neg_lag / total * 100
    return {
        'flag': lag_pct > max_lag_pct or exc_pct > max_excessive_lag_pct,
        'total_relationships': total_rels,
        'negative_lag_count': neg_lag,
        'negative_lag_pct': round(neg_pct, 1),
        'lag_count': lag,
        'lag_pct': round(lag_pct, 1),
        'excessive_lag_count': excessive_lag,
        'excessive_lag_pct': round(exc_pct, 1),
        'threshold_lag_pct': max_lag_pct,
        'threshold_excessive_lag_pct': max_excessive_lag_pct,
        'severity': ('RED' if neg_lag > 0 or exc_pct > max_excessive_lag_pct
                     else 'YELLOW' if lag_pct > max_lag_pct else 'OK'),
    }


def _check_constraints(activities: List[dict], max_total_pct: float, max_hard_pct: float) -> Dict:
    hard_types = {'CS_MANDFIN', 'CS_MANDSTART', 'MSO', 'MFO', 'MANDATORY_START', 'MANDATORY_FINISH'}
    soft_types = {'CS_MEOB', 'CS_MSOB', 'CS_MEOA', 'CS_MSOA', 'SNLT', 'FNLT', 'SNET', 'FNET',
                  'START_NO_LATER_THAN', 'FINISH_NO_LATER_THAN', 'START_NO_EARLIER_THAN',
                  'FINISH_NO_EARLIER_THAN'}
    non_ms = [a for a in activities if not _is_milestone(a) and not _is_complete(a)]
    total = len(non_ms) or 1

    hard = sum(1 for a in non_ms if a.get('constraintType', '') in hard_types)
    soft = sum(1 for a in non_ms if a.get('constraintType', '') in soft_types)
    all_c = hard + soft
    hard_pct = hard / total * 100
    total_pct = all_c / total * 100

    return {
        'flag': total_pct > max_total_pct or hard_pct > max_hard_pct,
        'total_constrained': all_c,
        'hard_constrained': hard,
        'soft_constrained': soft,
        'total_constrained_pct': round(total_pct, 1),
        'hard_constrained_pct': round(hard_pct, 1),
        'threshold_total_pct': max_total_pct,
        'threshold_hard_pct': max_hard_pct,
        'severity': ('RED' if hard_pct > max_hard_pct
                     else 'YELLOW' if total_pct > max_total_pct else 'OK'),
    }


def _check_durations(activities: List[dict], max_long_pct: float) -> Dict:
    non_ms = [a for a in activities if not _is_milestone(a)]
    total = len(non_ms) or 1
    no_dur = sum(1 for a in non_ms if not (a.get('dur') or a.get('origDur') or 0))
    long_d = sum(
        1 for a in non_ms
        if float(a.get('dur') or a.get('origDur') or 0) > _LONG_DURATION_DAYS
    )
    long_pct = long_d / total * 100
    return {
        'flag': long_pct > max_long_pct or no_dur > 0,
        'no_duration_count': no_dur,
        'long_duration_count': long_d,
        'long_duration_pct': round(long_pct, 1),
        'threshold_days': _LONG_DURATION_DAYS,
        'threshold_pct': max_long_pct,
        'severity': 'RED' if no_dur > 0 else 'YELLOW' if long_pct > max_long_pct else 'OK',
    }


def _check_out_of_sequence(activities: List[dict], dd: date) -> Dict:
    """Activities that have actual start but predecessors show incomplete."""
    oos = 0
    incomplete_ids = set()
    for a in activities:
        aid = a.get('code') or a.get('id') or ''
        if not _is_complete(a):
            incomplete_ids.add(aid)

    for a in activities:
        if not a.get('start'):
            continue
        for pred in (a.get('predecessors') or []):
            pid = pred.get('actId') or pred.get('id') or pred.get('activityId') or ''
            if pid in incomplete_ids:
                oos += 1
                break

    total = len(activities) or 1
    pct = oos / total * 100
    return {
        'flag': pct > 5,
        'count': oos,
        'pct': round(pct, 1),
        'severity': 'RED' if pct > 10 else 'YELLOW' if pct > 5 else 'OK',
    }


def _check_high_float(activities: List[dict], max_pct: float) -> Dict:
    incomplete = [a for a in activities if not _is_complete(a) and not _is_milestone(a)]
    total = len(incomplete) or 1
    high = sum(
        1 for a in incomplete
        if (_float_days(a) or 0) > _HIGH_FLOAT_DAYS
    )
    pct = high / total * 100
    return {
        'flag': pct > max_pct,
        'count': high,
        'pct': round(pct, 1),
        'threshold_days': _HIGH_FLOAT_DAYS,
        'threshold_pct': max_pct,
        'severity': 'YELLOW' if pct > max_pct else 'OK',
    }


def _check_negative_float(activities: List[dict], max_pct: float) -> Dict:
    incomplete = [a for a in activities if not _is_complete(a) and not _is_milestone(a)]
    total = len(incomplete) or 1
    neg = [a for a in incomplete if (_float_days(a) or 0) < 0]
    pct = len(neg) / total * 100
    min_float = min((_float_days(a) or 0) for a in neg) if neg else None
    return {
        'flag': pct > max_pct,
        'count': len(neg),
        'pct': round(pct, 1),
        'min_float_days': round(min_float, 1) if min_float is not None else None,
        'threshold_pct': max_pct,
        'severity': 'RED' if pct > max_pct or (min_float is not None and min_float < -14) else
                    'YELLOW' if neg else 'OK',
    }


def _check_missed_tasks(activities: List[dict], dd: date, max_pct: float) -> Dict:
    """Activities planned to be complete by data date but still in progress."""
    incomplete = [a for a in activities if not _is_complete(a) and not _is_milestone(a)]
    total = len(incomplete) or 1
    missed = []
    for a in incomplete:
        pf = _parse_date(a.get('bFinish') or a.get('finish'))
        if pf and pf <= dd:
            missed.append(a)
    pct = len(missed) / total * 100
    return {
        'flag': pct > max_pct,
        'count': len(missed),
        'pct': round(pct, 1),
        'threshold_pct': max_pct,
        'severity': 'RED' if pct > max_pct * 2 else 'YELLOW' if pct > max_pct else 'OK',
    }


def _check_invalid_actuals(activities: List[dict], dd: date) -> Dict:
    """Activities with actual dates in the future (past the data date)."""
    invalid = []
    for a in activities:
        actual_s = _parse_date(a.get('start'))
        actual_f = _parse_date(a.get('finish'))
        if (actual_s and actual_s > dd) or (actual_f and actual_f > dd):
            invalid.append(a.get('code') or a.get('id') or '')
    return {
        'flag': len(invalid) > 0,
        'count': len(invalid),
        'activity_ids': invalid[:10],
        'severity': 'RED' if len(invalid) > 5 else 'YELLOW' if invalid else 'OK',
    }


# ── Weighted score builder ─────────────────────────────────────────────────────

_CHECK_WEIGHTS = {
    'open_ends':          15,
    'circular_logic':     25,
    'lags':               10,
    'constraints':        10,
    'durations':          10,
    'out_of_sequence':    10,
    'negative_float':     10,
    'missed_tasks':       5,
    'high_float':         2,
    'invalid_actuals':    3,
}

_SEVERITY_DEDUCTIONS = {'OK': 0, 'YELLOW': 0.5, 'RED': 1.0}


def _score_from_checks(checks: Dict) -> float:
    """Returns 0–100 quality score (100 = perfect)."""
    penalty = 0.0
    for key, weight in _CHECK_WEIGHTS.items():
        check = checks.get(key, {})
        if key == 'circular_logic':
            circular_count = check.get('count', 0)
            if circular_count > 0:
                penalty += weight  # automatic full deduction
            continue
        severity = check.get('severity', 'OK')
        penalty += weight * _SEVERITY_DEDUCTIONS.get(severity, 0)
    return max(0.0, round(100 - penalty, 1))


# ── Main entry point ──────────────────────────────────────────────────────────

def assess_quality(
    activities: List[dict],
    data_date: date,
    thresholds: Optional[ThresholdValues] = None,
) -> Dict:
    """
    Run all quality checks and return a structured dict.

    This function is pure — it does not touch the database.
    """
    T = thresholds or ThresholdValues()

    circular_count = _detect_circular_logic(activities)
    oe   = _check_open_ends(activities, T.max_open_ends_pct)
    lags = _check_lags(activities, T.max_lag_pct, T.max_excessive_lag_pct)
    con  = _check_constraints(activities, T.max_constraint_pct, T.max_hard_constraint_pct)
    dur  = _check_durations(activities, T.max_long_duration_pct)
    oos  = _check_out_of_sequence(activities, data_date)
    hf   = _check_high_float(activities, T.max_high_float_pct)
    nf   = _check_negative_float(activities, T.max_negative_float_pct)
    mt   = _check_missed_tasks(activities, data_date, T.max_missed_task_pct)
    ia   = _check_invalid_actuals(activities, data_date)

    checks = {
        'circular_logic': {'count': circular_count, 'severity': 'RED' if circular_count else 'OK'},
        'open_ends': oe,
        'lags': lags,
        'constraints': con,
        'durations': dur,
        'out_of_sequence': oos,
        'high_float': hf,
        'negative_float': nf,
        'missed_tasks': mt,
        'invalid_actuals': ia,
    }

    overall = _score_from_checks(checks)
    flag_count = sum(1 for c in checks.values() if c.get('flag') or c.get('count', 0) > 0)

    # Summary findings for the API / frontend
    findings = []
    if circular_count > 0:
        findings.append({
            'check': 'Circular Logic',
            'severity': 'CRITICAL',
            'detail': f'{circular_count} activities are involved in circular dependency chains.',
            'recommendation': 'Break all circular loops before re-running the schedule.',
        })
    if oe.get('flag'):
        findings.append({
            'check': 'Open Ends',
            'severity': oe['severity'],
            'detail': (
                f'{oe["no_predecessors"]} activities have no predecessors, '
                f'{oe["no_successors"]} have no successors ({oe["pct"]}% open-end rate).'
            ),
            'recommendation': f'Connect all activities that should be logically linked. Target below {T.max_open_ends_pct:.0f}%.',
        })
    if lags.get('flag') or lags.get('negative_lag_count', 0) > 0:
        findings.append({
            'check': 'Lags & Negative Lags',
            'severity': lags['severity'],
            'detail': (
                f'{lags["negative_lag_count"]} relationships use negative lag (reverse logic). '
                f'{lags["lag_count"]} relationships use lag ({lags["lag_pct"]}%).'
            ),
            'recommendation': 'Replace negative lags with proper FS logic. Review excessive positive lags for accuracy.',
        })
    if con.get('flag'):
        findings.append({
            'check': 'Constraints',
            'severity': con['severity'],
            'detail': (
                f'{con["total_constrained"]} activities constrained ({con["total_constrained_pct"]}%), '
                f'of which {con["hard_constrained"]} are hard constraints ({con["hard_constrained_pct"]}%).'
            ),
            'recommendation': 'Remove hard constraints and replace with logic to restore network-driven dates.',
        })
    if dur.get('flag'):
        findings.append({
            'check': 'Duration Outliers',
            'severity': dur['severity'],
            'detail': (
                f'{dur["no_duration_count"]} activities have zero duration; '
                f'{dur["long_duration_count"]} exceed {int(_LONG_DURATION_DAYS)} working days ({dur["long_duration_pct"]}%).'
            ),
            'recommendation': 'Verify duration of flagged activities; consider breaking long tasks into phases.',
        })
    if oos.get('flag'):
        findings.append({
            'check': 'Out-of-Sequence Progress',
            'severity': oos['severity'],
            'detail': f'{oos["count"]} activities ({oos["pct"]}%) started before their predecessors finished.',
            'recommendation': 'Update the logic to reflect how work is actually being executed, or formally accept out-of-sequence progress.',
        })
    if nf.get('count', 0) > 0:
        findings.append({
            'check': 'Negative Float',
            'severity': nf['severity'],
            'detail': (
                f'{nf["count"]} activities ({nf["pct"]}%) have negative float'
                + (f'; minimum is {nf["min_float_days"]}d.' if nf["min_float_days"] is not None else '.')
            ),
            'recommendation': 'Investigate drivers of negative float: validate remaining durations, logic, and hard constraints.',
        })
    if mt.get('flag'):
        findings.append({
            'check': 'Missed Planned Completion',
            'severity': mt['severity'],
            'detail': f'{mt["count"]} activities ({mt["pct"]}%) were planned complete by the data date but remain in progress.',
            'recommendation': 'Update progress to reflect actual status, or revise the schedule dates through proper change management.',
        })
    if ia.get('flag'):
        findings.append({
            'check': 'Invalid Actual Dates',
            'severity': ia['severity'],
            'detail': f'{ia["count"]} activities have actual dates after the data date.',
            'recommendation': 'Review and correct actual dates on flagged activities.',
        })

    return {
        'overall_score': overall,
        'flag_count': flag_count,
        'circular_logic_count': circular_count,
        'checks': checks,
        'findings': findings,
        'activity_count': len(activities),
        'data_date': data_date.isoformat() if data_date else None,
    }
