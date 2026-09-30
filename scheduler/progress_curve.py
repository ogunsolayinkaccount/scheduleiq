"""
Progress Curve (S-Curve) Engine — ScheduleIQ Phase 7

Pure, DB-free service (same pattern as status_engine.py) that computes real
cumulative progress from actual imported activity data — never demo/fake
values. "Actual %" buckets each activity's earned progress into the period
containing its own current planned finish (bFinish), the same convention
already used by utils.compute_metrics's s_curve/evm_curve — this is a
snapshot-based approximation (there is no daily historical replay), not a
true day-by-day progress history, and is documented as such rather than
oversold as more precise than it is.

"Baseline Planned %" and "Previous Update %" are only returned when the
caller actually supplies a distinct baseline/previous activity list —
never fabricated when only one schedule version is available.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

WEIGHTING_MODES = ('duration', 'count', 'hours', 'cost')
PERIODS = ('daily', 'weekly', 'monthly')


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


def weight_activity(a: dict, mode: str) -> float:
    """The per-activity weight used to build a cumulative curve for a given
    metric — public so other engines (baseline_progress.py) can build their
    own curves on the exact same formula rather than a re-derived one.
    'hours'/'cost' return 0 for an activity that isn't resource/cost-loaded
    (callers are expected to gate metric availability themselves — see
    baseline_progress.build_scurve() — rather than silently mixing loaded
    and unloaded activities into one total)."""
    if mode == 'count':
        return 1.0
    if mode == 'hours':
        return float(a.get('budgetedHours') or 0.0) if a.get('isResourceLoaded') else 0.0
    if mode == 'cost':
        return float(a.get('budgetedCost') or 0.0) if a.get('isCostLoaded') else 0.0
    return float(a.get('dur') or 0.0)


_weight = weight_activity   # internal alias — keeps this module's own call sites unchanged


def _end_of_month(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1) - timedelta(days=1)
    return date(d.year, d.month + 1, 1) - timedelta(days=1)


def _iso_week_start(year: int, week: int) -> date:
    jan4 = date(year, 1, 4)
    week1_monday = jan4 - timedelta(days=jan4.isoweekday() - 1)
    return week1_monday + timedelta(weeks=week - 1)


def _period_key(d: date, period: str) -> Tuple[int, ...]:
    if period == 'daily':
        return (d.year, d.month, d.day)
    if period == 'weekly':
        iso = d.isocalendar()
        return (iso[0], iso[1])
    return (d.year, d.month)


def _period_label(key: Tuple[int, ...], period: str) -> str:
    if period == 'daily':
        return date(*key).strftime('%b %d, %Y')
    if period == 'weekly':
        year, week = key
        return f'{year}-W{week:02d}'
    year, month = key
    return date(year, month, 1).strftime('%b %y')


def _period_end(key: Tuple[int, ...], period: str) -> date:
    if period == 'daily':
        return date(*key)
    if period == 'weekly':
        year, week = key
        return _iso_week_start(year, week) + timedelta(days=6)
    year, month = key
    return _end_of_month(date(year, month, 1))


def _cumulative_series(activities: List[dict], weighting: str, period: str) -> List[Dict[str, Any]]:
    total_weight = sum(_weight(a, weighting) for a in activities) or 1.0
    buckets: Dict[Tuple[int, ...], Dict[str, float]] = {}

    for a in activities:
        d = _parse_date(a.get('bFinish') or a.get('finish'))
        if not d:
            continue
        key = _period_key(d, period)
        b = buckets.setdefault(key, {'planned': 0.0, 'earned': 0.0})
        w = _weight(a, weighting)
        b['planned'] += w
        b['earned'] += w * (float(a.get('pctComplete') or 0.0) / 100.0)

    series = []
    cum_planned = cum_earned = 0.0
    for key in sorted(buckets):
        cum_planned += buckets[key]['planned']
        cum_earned += buckets[key]['earned']
        series.append({
            'periodKey': key,
            'period': _period_label(key, period),
            'periodEnd': _period_end(key, period).isoformat(),
            'plannedPct': round(min(100.0, cum_planned / total_weight * 100.0), 1),
            'actualPct': round(min(100.0, cum_earned / total_weight * 100.0), 1),
        })
    return series


def compute_progress_curve(
    current_activities: List[dict],
    weighting: str = 'duration',
    period: str = 'monthly',
    baseline_activities: Optional[List[dict]] = None,
    previous_activities: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    if weighting not in WEIGHTING_MODES:
        weighting = 'duration'
    if period not in PERIODS:
        period = 'monthly'

    by_key: Dict[Tuple[int, ...], Dict[str, Any]] = {}

    for s in _cumulative_series(current_activities, weighting, period):
        by_key[s['periodKey']] = {
            'period': s['period'],
            'periodEnd': s['periodEnd'],
            'currentPlanned': s['plannedPct'],
            'currentUpdateActual': s['actualPct'],
            'baselinePlanned': None,
            'previousUpdateActual': None,
        }

    if baseline_activities:
        for s in _cumulative_series(baseline_activities, weighting, period):
            entry = by_key.setdefault(s['periodKey'], {
                'period': s['period'], 'periodEnd': s['periodEnd'],
                'currentPlanned': None, 'currentUpdateActual': None, 'previousUpdateActual': None,
            })
            entry['baselinePlanned'] = s['plannedPct']

    if previous_activities:
        for s in _cumulative_series(previous_activities, weighting, period):
            entry = by_key.setdefault(s['periodKey'], {
                'period': s['period'], 'periodEnd': s['periodEnd'],
                'currentPlanned': None, 'currentUpdateActual': None, 'baselinePlanned': None,
            })
            entry['previousUpdateActual'] = s['actualPct']

    periods = []
    prev_planned = prev_actual = 0.0
    for key in sorted(by_key.keys()):
        p = dict(by_key[key])
        cp = p.get('currentPlanned')
        ca = p.get('currentUpdateActual')
        p['variance'] = round(ca - cp, 1) if (cp is not None and ca is not None) else None
        p['incrementalPlanned'] = round(cp - prev_planned, 1) if cp is not None else None
        p['incrementalActual'] = round(ca - prev_actual, 1) if ca is not None else None
        prev_planned = cp if cp is not None else prev_planned
        prev_actual = ca if ca is not None else prev_actual
        periods.append(p)

    forecast_finish = None
    fc_dates = [_parse_date(a.get('bFinish') or a.get('finish')) for a in current_activities if not (a.get('pctComplete') or 0) >= 100]
    fc_dates = [d for d in fc_dates if d]
    if fc_dates:
        forecast_finish = max(fc_dates).isoformat()

    return {
        'weighting': weighting,
        'period': period,
        'periods': periods,
        'hasBaseline': bool(baseline_activities),
        'hasPreviousUpdate': bool(previous_activities),
        'forecastFinish': forecast_finish,
        'methodologyNote': (
            "Actual % reflects each activity's earned progress bucketed into the period "
            "containing its own current planned finish date — a snapshot approximation, "
            "not a day-by-day historical replay. Earned % (cost/resource-weighted) is not "
            "yet supported and is intentionally omitted rather than estimated."
        ),
    }
