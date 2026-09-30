"""
Driver-Ranking Engine — ScheduleIQ Project Controls Phase

Deterministic, DB-free (same pattern as cost_engine.py/trend_engine.py) —
ranks WBS/Area/Discipline/Contractor/System groups by their contribution to
cost variance, schedule variance, productivity shortfall, or forecast
growth, using cost_engine.py's existing group_by-capable functions as the
sole source of truth. No new calculation logic — this module only sorts,
filters, and computes contribution percentages on numbers cost_engine.py
already produced.

Explicitly NOT causal analysis: rankings identify where the largest
unfavorable numbers currently sit, which is where a controls analyst should
look next — not proof that a group *caused* the project-level variance.
Every ranking result and the executive narrative built on top of it (see
executive_summary.py) uses language like "largest contributor" /
"strongest current signal", never "caused".
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from .cost_engine import GROUP_FIELD_MAP, compute_cost_summary, compute_productivity

ENGINE_VERSION = '1.0.0'

_RECONCILE_TOLERANCE = 1.0   # dollars/hours — rounding slack, not a real discrepancy


def _group_activities(activities: List[dict], group_by: str) -> Dict[str, List[dict]]:
    field = GROUP_FIELD_MAP.get(group_by)
    if not field:
        return {}
    groups: Dict[str, List[dict]] = {}
    for a in activities:
        key = (a.get(field) or '').strip() or 'Unassigned'
        groups.setdefault(key, []).append(a)
    return groups


def _reconciliation(total: Optional[float], contributors: List[Optional[float]]) -> Dict[str, Any]:
    if total is None:
        return {'reconciled': None, 'reason': 'Project-level total unavailable — nothing to reconcile against.'}
    summed = sum(v for v in contributors if v is not None)
    delta = round(total - summed, 2)
    return {
        'reconciled': abs(delta) <= _RECONCILE_TOLERANCE,
        'projectTotal': round(total, 2),
        'sumOfIncludedGroups': round(summed, 2),
        'delta': delta,
    }


def rank_cost_drivers(
    activities: List[dict], group_by: str, data_date: Optional[date],
    ev_method: str = 'DURATION_PCT_COMPLETE', pv_method: str = 'LINEAR_BASELINE_SPREAD',
) -> Dict[str, Any]:
    """Ranks groups by Cost Variance (CV), most unfavorable (most negative)
    first. Only groups with an available CV are ranked — a group with no
    cost-loaded activities contributes nothing to project CV and is excluded
    rather than shown as a misleading zero."""
    if group_by not in GROUP_FIELD_MAP:
        return {'error': f'Unknown group_by value: {group_by}. Valid: {list(GROUP_FIELD_MAP)}', 'drivers': []}

    project_total = compute_cost_summary(activities, data_date, ev_method, pv_method)
    groups = _group_activities(activities, group_by)

    ranked = []
    for key, acts in groups.items():
        summary = compute_cost_summary(acts, data_date, ev_method, pv_method)
        cv = summary['cost']['cv']
        if cv is None:
            continue
        ranked.append({
            'group': key, 'cv': cv, 'vac': None,
            'bac': summary['cost']['bac'], 'ev': summary['cost']['ev'], 'ac': summary['cost']['ac'],
            'cpi': summary['cost']['cpi'],
            'activityCount': len(acts),
        })
    ranked.sort(key=lambda g: g['cv'])   # most negative (worst) first

    total_cv = project_total['cost']['cv']
    for g in ranked:
        g['contributionPct'] = (round(g['cv'] / total_cv * 100.0, 1) if total_cv else None)

    return {
        'engineVersion': ENGINE_VERSION, 'groupBy': group_by, 'rankedBy': 'cv',
        'projectCv': total_cv,
        'drivers': ranked,
        'reconciliation': _reconciliation(total_cv, [g['cv'] for g in ranked]),
        'excludedGroupCount': len(groups) - len(ranked),
    }


def rank_schedule_drivers(
    activities: List[dict], group_by: str, data_date: Optional[date],
    ev_method: str = 'DURATION_PCT_COMPLETE', pv_method: str = 'LINEAR_BASELINE_SPREAD',
) -> Dict[str, Any]:
    """Ranks groups by Schedule Variance (SV), most unfavorable first."""
    if group_by not in GROUP_FIELD_MAP:
        return {'error': f'Unknown group_by value: {group_by}. Valid: {list(GROUP_FIELD_MAP)}', 'drivers': []}

    project_total = compute_cost_summary(activities, data_date, ev_method, pv_method)
    groups = _group_activities(activities, group_by)

    ranked = []
    for key, acts in groups.items():
        summary = compute_cost_summary(acts, data_date, ev_method, pv_method)
        sv = summary['cost']['sv']
        if sv is None:
            continue
        ranked.append({
            'group': key, 'sv': sv,
            'pv': summary['cost']['pv'], 'ev': summary['cost']['ev'], 'spi': summary['cost']['spi'],
            'activityCount': len(acts),
        })
    ranked.sort(key=lambda g: g['sv'])

    total_sv = project_total['cost']['sv']
    for g in ranked:
        g['contributionPct'] = (round(g['sv'] / total_sv * 100.0, 1) if total_sv else None)

    return {
        'engineVersion': ENGINE_VERSION, 'groupBy': group_by, 'rankedBy': 'sv',
        'projectSv': total_sv,
        'drivers': ranked,
        'reconciliation': _reconciliation(total_sv, [g['sv'] for g in ranked]),
        'excludedGroupCount': len(groups) - len(ranked),
    }


def rank_productivity_drivers(
    activities: List[dict], group_by: str, ev_method: str = 'DURATION_PCT_COMPLETE',
) -> Dict[str, Any]:
    """Ranks groups by Productivity Factor, worst (lowest) first — the
    groups where the least budgeted-work-equivalent is being earned per
    hour actually spent."""
    if group_by not in GROUP_FIELD_MAP:
        return {'error': f'Unknown group_by value: {group_by}. Valid: {list(GROUP_FIELD_MAP)}', 'drivers': []}

    result = compute_productivity(activities, ev_method, group_by)
    if 'error' in result:
        return {'error': result['error'], 'drivers': []}

    ranked = [
        {
            'group': g['group'], 'productivityFactor': g['productivityFactor'],
            'budgetedHours': g['budgetedHours'], 'earnedHours': g['earnedHours'],
            'actualHours': g['actualHours'], 'hoursVariance': g['hoursVariance'],
            'activityCount': g['activityCount'],
        }
        for g in result.get('groups', []) if g.get('productivityFactor') is not None
    ]
    ranked.sort(key=lambda g: g['productivityFactor'])

    return {
        'engineVersion': ENGINE_VERSION, 'groupBy': group_by, 'rankedBy': 'productivityFactor',
        'projectProductivityFactor': result['overall'].get('productivityFactor'),
        'drivers': ranked,
        'excludedGroupCount': len(result.get('groups', [])) - len(ranked),
    }


def rank_forecast_growth_drivers(
    current_activities: List[dict], previous_activities: List[dict], group_by: str,
    current_date: Optional[date], previous_date: Optional[date],
    ev_method: str = 'DURATION_PCT_COMPLETE', eac_methodology: str = 'CPI_BASED',
) -> Dict[str, Any]:
    """Ranks groups by EAC growth (current EAC - previous EAC, using one
    named methodology) between two real schedule versions, most growth
    first. Only groups where BOTH versions produced that EAC scenario are
    ranked — a group that only appears cost-loaded in one version can't
    honestly show a "growth" figure."""
    if group_by not in GROUP_FIELD_MAP:
        return {'error': f'Unknown group_by value: {group_by}. Valid: {list(GROUP_FIELD_MAP)}', 'drivers': []}

    curr_groups = _group_activities(current_activities, group_by)
    prev_groups = _group_activities(previous_activities, group_by)

    def _group_eac(acts: List[dict], d: Optional[date]) -> Optional[float]:
        summary = compute_cost_summary(acts, d, ev_method)
        scenario = next((s for s in summary['forecast']['scenarios'] if s['methodology'] == eac_methodology), None)
        return scenario['eac'] if scenario else None

    all_keys = set(curr_groups) | set(prev_groups)
    ranked = []
    for key in all_keys:
        curr_eac = _group_eac(curr_groups.get(key, []), current_date) if key in curr_groups else None
        prev_eac = _group_eac(prev_groups.get(key, []), previous_date) if key in prev_groups else None
        if curr_eac is None or prev_eac is None:
            continue
        ranked.append({
            'group': key, 'currentEac': curr_eac, 'previousEac': prev_eac,
            'eacGrowth': round(curr_eac - prev_eac, 2),
        })
    ranked.sort(key=lambda g: -g['eacGrowth'])   # biggest growth (most unfavorable) first

    return {
        'engineVersion': ENGINE_VERSION, 'groupBy': group_by, 'rankedBy': 'eacGrowth',
        'eacMethodology': eac_methodology,
        'drivers': ranked,
        'excludedGroupCount': len(all_keys) - len(ranked),
    }
