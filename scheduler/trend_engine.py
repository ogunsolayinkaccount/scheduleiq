"""
Trend & Forecast-Drift Engine — ScheduleIQ Project Controls Phase

Deterministic, DB-free (same pattern as cost_engine.py) — turns a
chronological series of cost_engine.py snapshots (one per real, persisted
schedule version) into comparisons and trend observations. This module
never touches the database directly; views.py assembles the snapshot
series from ScheduleUpload rows and passes plain data in.

Design principle carried over from the rest of this project: diagnose,
don't predict. This module reports what already happened — "CPI has
deteriorated for 3 consecutive updates" — never a projection of what will
happen. No statistical forecasting or Monte Carlo methods are implemented
here; EAC scenarios themselves come from cost_engine.py's existing labeled
methodologies, this module only tracks how those already-computed figures
moved between historical updates.

Golden rule unchanged: a metric unavailable in a historical version stays
unavailable — never backfilled from the current value, never interpolated.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

ENGINE_VERSION = '1.0.0'

# "Higher is better" vs "lower is better" — direction classification is
# metric-aware, never a blind "bigger number = improving".
_HIGHER_IS_BETTER = {'cpi', 'spi', 'cv', 'sv', 'vac', 'productivityFactor', 'tcpi'}
_LOWER_IS_BETTER = {'eac'}
# Metrics that are magnitudes, not performance signals — delta/pct only, no direction.
_NEUTRAL_METRICS = {'bac', 'pv', 'ev', 'ac', 'budgetedHours', 'earnedHours', 'actualHours', 'remainingHours'}

# Relative-change threshold below which a metric is called "unchanged"
# rather than a fabricated-precision improving/deteriorating call on noise.
_UNCHANGED_RELATIVE_THRESHOLD = 0.005

DIRECTIONS = ('improving', 'deteriorating', 'unchanged', 'unavailable')


def _direction(metric: str, previous: Optional[float], current: Optional[float]) -> str:
    if previous is None or current is None:
        return 'unavailable'
    if metric in _NEUTRAL_METRICS:
        return 'unavailable'   # no "better/worse" concept for a raw magnitude
    delta = current - previous
    scale = max(abs(previous), abs(current), 1e-9)
    if abs(delta) / scale < _UNCHANGED_RELATIVE_THRESHOLD:
        return 'unchanged'
    if metric in _HIGHER_IS_BETTER:
        return 'improving' if delta > 0 else 'deteriorating'
    if metric in _LOWER_IS_BETTER:
        return 'improving' if delta < 0 else 'deteriorating'
    return 'unavailable'


def compare_metric(metric: str, previous: Optional[float], current: Optional[float]) -> Dict[str, Any]:
    """One metric's current-vs-previous comparison: value, delta, % change,
    and a metric-aware direction. Never fabricates a direction for a metric
    that has no defined 'better/worse' sense (see _NEUTRAL_METRICS)."""
    delta = (round(current - previous, 4) if (previous is not None and current is not None) else None)
    pct = None
    if previous is not None and current is not None and previous != 0:
        pct = round((current - previous) / abs(previous) * 100.0, 2)
    return {
        'current': current,
        'previous': previous,
        'delta': delta,
        'pctChange': pct,
        'direction': _direction(metric, previous, current),
    }


# ── Snapshot construction ───────────────────────────────────────────────────

def snapshot_from_cost_summary(version_id: str, version_label: str, data_date: str, summary: Dict[str, Any],
                                productivity: Dict[str, Any]) -> Dict[str, Any]:
    """Flattens one cost_engine.compute_cost_summary()/compute_productivity()
    pair (for one real schedule version) into the flat metric set this
    module tracks historically. Pure data reshaping — no calculation."""
    cost = summary['cost']
    hours = summary['hours']
    prod = productivity.get('overall', {}) if productivity else {}
    scenarios = {s['methodology']: s for s in summary['forecast']['scenarios']}

    return {
        'versionId': version_id,
        'versionLabel': version_label,
        'dataDate': data_date,
        'bac': cost['bac'], 'pv': cost['pv'], 'ev': cost['ev'], 'ac': cost['ac'],
        'cv': cost['cv'], 'sv': cost['sv'], 'cpi': cost['cpi'], 'spi': cost['spi'], 'tcpi': cost['tcpi'],
        'hoursBac': hours['bac'], 'hoursEv': hours['ev'], 'hoursAc': hours['ac'],
        'budgetedHours': prod.get('budgetedHours'), 'earnedHours': prod.get('earnedHours'),
        'actualHours': prod.get('actualHours'), 'remainingHours': prod.get('remainingHours'),
        'productivityFactor': prod.get('productivityFactor'),
        'eacScenarios': {
            key: {'eac': s['eac'], 'vac': s.get('vac'), 'label': s['label']}
            for key, s in scenarios.items()
        },
        # VAC is scenario-dependent (BAC - that scenario's EAC); expose the
        # CPI-based scenario's VAC as the default single "vac" trend point
        # when present, since it's the most commonly cited single figure —
        # every other scenario's VAC remains available under eacScenarios.
        'vac': scenarios.get('CPI_BASED', {}).get('vac'),
    }


TRACKED_METRICS = (
    'bac', 'pv', 'ev', 'ac', 'cv', 'sv', 'cpi', 'spi', 'tcpi', 'vac',
    'budgetedHours', 'earnedHours', 'actualHours', 'remainingHours', 'productivityFactor',
)


def build_comparison(series: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Current-vs-previous comparison using the LAST TWO points in a
    chronologically-sorted snapshot series. Returns 'unavailable' comparisons
    throughout (never a crash) when fewer than 2 points exist."""
    if not series:
        return {'available': False, 'reason': 'No persisted schedule versions with cost/hours data.', 'metrics': {}}
    current = series[-1]
    previous = series[-2] if len(series) >= 2 else None

    metrics = {}
    for m in TRACKED_METRICS:
        curr_v = current.get(m)
        prev_v = previous.get(m) if previous else None
        metrics[m] = compare_metric(m, prev_v, curr_v)

    return {
        'available': True,
        'currentVersion': {'id': current['versionId'], 'label': current['versionLabel'], 'dataDate': current['dataDate']},
        'previousVersion': (
            {'id': previous['versionId'], 'label': previous['versionLabel'], 'dataDate': previous['dataDate']}
            if previous else None
        ),
        'metrics': metrics,
    }


# ── EAC drift (Phase B) ──────────────────────────────────────────────────────

EAC_METHODOLOGIES = ('CPI_BASED', 'BOTTOM_UP', 'CPI_SPI_COMPOSITE')


def build_eac_drift(series: List[Dict[str, Any]], approved_eac: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Independent drift tracking per EAC methodology — never blended into
    one unexplained line. `approved_eac` (optional) is the latest
    ManualCostEntry APPROVED_EAC fact — it is a point-in-time entered value,
    not itself historically versioned, so it gets a single current figure
    and an explicit note rather than a fabricated trend."""
    if not series:
        return {'available': False, 'scenarios': {}, 'approvedEac': None}

    current = series[-1]
    previous = series[-2] if len(series) >= 2 else None

    scenarios = {}
    for method in EAC_METHODOLOGIES:
        curr_s = current.get('eacScenarios', {}).get(method)
        prev_s = previous.get('eacScenarios', {}).get(method) if previous else None
        curr_eac = curr_s['eac'] if curr_s else None
        prev_eac = prev_s['eac'] if prev_s else None
        comparison = compare_metric('eac', prev_eac, curr_eac)
        scenarios[method] = {
            'label': (curr_s or prev_s or {}).get('label', method),
            **comparison,
        }

    return {
        'available': True,
        'scenarios': scenarios,
        'approvedEac': (
            {'value': approved_eac['cost'], 'description': approved_eac.get('description'),
             'enteredAt': approved_eac.get('enteredAt'),
             'note': 'Analyst-entered point-in-time value — not tracked as a historical trend.'}
            if approved_eac else None
        ),
    }


# ── CPI/SPI/productivity trend intelligence (Phase C) ──────────────────────

def detect_consecutive_trend(series: List[Dict[str, Any]], metric: str, lookback: int = 5) -> Dict[str, Any]:
    """Finds the longest CURRENT streak (ending at the latest version) of
    consecutive improving or deteriorating moves for `metric`, over at most
    the last `lookback` points. Returns a control-indicator observation, not
    a forecast — see module docstring."""
    values = [pt.get(metric) for pt in series[-lookback:] if pt.get(metric) is not None]
    if len(values) < 2:
        return {'available': False, 'observation': None, 'consecutiveCount': 0, 'direction': 'unavailable'}

    directions = []
    for i in range(1, len(values)):
        d = _direction(metric, values[i - 1], values[i])
        directions.append(d)

    # Walk backwards from the most recent move, counting consecutive moves
    # (not points) in the same direction as the latest move.
    last_dir = directions[-1]
    if last_dir not in ('improving', 'deteriorating'):
        streak = 1
    else:
        streak = 0
        for d in reversed(directions):
            if d == last_dir:
                streak += 1
            else:
                break

    if last_dir == 'unchanged':
        observation = f'{metric.upper()} has been stable across the last {len(values)} updates.'
    elif last_dir == 'unavailable':
        observation = None
    else:
        verb = 'improved' if last_dir == 'improving' else 'deteriorated'
        observation = f'{metric.upper()} has {verb} for {streak} consecutive update{"s" if streak != 1 else ""}.'

    return {
        'available': True,
        'observation': observation,
        'consecutiveCount': streak,
        'direction': last_dir,
        'pointsConsidered': len(values),
    }


def detect_threshold_crossing(series: List[Dict[str, Any]], metric: str, threshold: float) -> Dict[str, Any]:
    """Did `metric` cross `threshold` between the last two available
    readings (in either direction)? e.g. CPI crossing below 0.90."""
    values = [(pt['dataDate'], pt.get(metric)) for pt in series if pt.get(metric) is not None]
    if len(values) < 2:
        return {'crossed': False, 'direction': None}
    (prev_date, prev_v), (curr_date, curr_v) = values[-2], values[-1]
    if prev_v is None or curr_v is None:
        return {'crossed': False, 'direction': None}
    if prev_v >= threshold > curr_v:
        return {'crossed': True, 'direction': 'below', 'threshold': threshold, 'from': prev_v, 'to': curr_v, 'atDate': curr_date}
    if prev_v < threshold <= curr_v:
        return {'crossed': True, 'direction': 'above', 'threshold': threshold, 'from': prev_v, 'to': curr_v, 'atDate': curr_date}
    return {'crossed': False, 'direction': None}
