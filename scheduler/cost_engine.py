"""
Cost & Earned Value Engine — ScheduleIQ Project Controls Phase

Deterministic, DB-free (same pattern as status_engine.py / quality_engine.py
/ schedule_risk.py) — operates on plain activity dicts as already produced
by xer_to_activities()/column_mapping.py, which already carry per-activity
budgetedCost/actualCost/remainingCost/budgetedHours/actualHours/
remainingHours/isCostLoaded/isResourceLoaded (see parsers.py). No new
parsing is required for cost/hours themselves — this module is the
calculation layer on top of data that import already captures.

Golden rule carried over from the rest of the app: never fabricate a
number. Every figure here is either summed directly from source fields,
or explicitly marked unavailable with a reason — never guessed, never
backed into from an assumed rate.

── Earned Value: BAC/AC always available when cost-loaded activities exist.
   EV depends on an explicit, labeled METHOD (never silently chosen):
     DURATION_PCT_COMPLETE — fraction = (dur - remainDur) / dur
     PHYSICAL_PCT_COMPLETE — fraction = physPctComplete / 100, only for
                              activities where P6 itself was configured to
                              track physical % (pctCompleteType == 'CP_Phys')
     UNITS_PROGRESS        — fraction = (budgetedHours - remainingHours)
                              / budgetedHours (from TASKRSRC's own
                              remain_qty — independent of both of the above)
   Activities where the chosen method's inputs are missing are excluded
   from that computation and reported in `activitiesExcluded`, so a
   partial-coverage EV is never presented as if it covered 100% of BAC.

── Planned Value: LINEAR_BASELINE_SPREAD is the only method implemented —
   a calendar-day-linear spread of each activity's budgeted cost/hours
   across its baseline start/finish window, evaluated at the data date.
   This is explicitly NOT real P6 time-phased cost (P6's period-phased
   budget tables aren't parsed by this app) — every PV result carries
   `method` and a `warning` saying so, per explicit instruction not to
   represent it as imported P6 phasing.

── Forecast: EAC is returned as multiple labeled scenarios (never "the"
   forecast) — CPI-based, bottom-up (AC + XER's own remaining-cost
   estimate), and a CPI×SPI composite. An analyst-approved EAC
   (ManualCostEntry, entry_type=APPROVED_EAC) is surfaced separately by
   the view layer, never computed here.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

ENGINE_VERSION = '1.0.0'

EV_METHODS = ('DURATION_PCT_COMPLETE', 'PHYSICAL_PCT_COMPLETE', 'UNITS_PROGRESS')
PV_METHODS = ('LINEAR_BASELINE_SPREAD',)

GROUP_FIELD_MAP = {
    'wbs': 'wbs', 'area': 'area', 'discipline': 'discipline',
    'contractor': 'contractor', 'system': 'system',
}


# ── Shared helpers ──────────────────────────────────────────────────────────

def _num(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


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


def _safe_div(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _earned_fraction(a: dict, method: str) -> Optional[float]:
    """Returns the earned-progress fraction (0..1) for one activity under
    the given EV method, or None if that method's inputs aren't available
    for this activity. This single fraction drives both earned cost and
    earned hours, per standard EVM practice."""
    if method == 'DURATION_PCT_COMPLETE':
        dur = _num(a.get('dur'))
        remain = _num(a.get('remainDur'))
        if dur is None or remain is None or dur <= 0:
            # Zero-duration (milestone) activities: complete once actually finished.
            if dur == 0 and a.get('finish'):
                return 1.0
            if dur == 0:
                return 0.0
            return None
        frac = (dur - remain) / dur
        return max(0.0, min(1.0, frac))

    if method == 'PHYSICAL_PCT_COMPLETE':
        if (a.get('pctCompleteType') or '').strip() != 'CP_Phys':
            return None
        phys = _num(a.get('physPctComplete'))
        if phys is None:
            return None
        return max(0.0, min(1.0, phys / 100.0))

    if method == 'UNITS_PROGRESS':
        bgt = _num(a.get('budgetedHours'))
        rem = _num(a.get('remainingHours'))
        if bgt is None or rem is None or bgt <= 0:
            return None
        frac = (bgt - rem) / bgt
        return max(0.0, min(1.0, frac))

    raise ValueError(f'Unknown EV method: {method}')


def _planned_fraction(a: dict, data_date: date) -> Optional[float]:
    """LINEAR_BASELINE_SPREAD: fraction of this activity's baseline
    calendar-day window elapsed as of data_date. None if baseline dates
    are missing — never assumed."""
    bs, bf = _parse_date(a.get('bStart')), _parse_date(a.get('bFinish'))
    if bs is None or bf is None:
        return None
    if data_date <= bs:
        return 0.0
    if data_date >= bf:
        return 1.0
    span = (bf - bs).days
    if span <= 0:
        return 1.0
    return (data_date - bs).days / span


# ── Earned Value ────────────────────────────────────────────────────────────

def compute_earned_value(activities: List[dict], method: str = 'DURATION_PCT_COMPLETE') -> Dict[str, Any]:
    """
    BAC / EV / AC in both cost and labor-hour terms, plus coverage
    reporting. Cost figures are gated on isCostLoaded — if no activity in
    scope is cost-loaded, cost figures are None ('unavailable'), never a
    fabricated number derived from hours. Hour figures are gated
    separately on isResourceLoaded, so an hours-only (no cost) schedule
    still gets a full labor-hours picture.
    """
    if method not in EV_METHODS:
        raise ValueError(f'Unknown EV method: {method}. Valid: {EV_METHODS}')

    cost_loaded = [a for a in activities if a.get('isCostLoaded')]
    rsrc_loaded = [a for a in activities if a.get('isResourceLoaded')]

    bac_cost = sum(_num(a.get('budgetedCost')) or 0.0 for a in cost_loaded) if cost_loaded else None
    ac_cost = sum(_num(a.get('actualCost')) or 0.0 for a in cost_loaded) if cost_loaded else None
    bac_hours = sum(_num(a.get('budgetedHours')) or 0.0 for a in rsrc_loaded) if rsrc_loaded else None
    ac_hours = sum(_num(a.get('actualHours')) or 0.0 for a in rsrc_loaded) if rsrc_loaded else None

    ev_cost = 0.0
    ev_hours = 0.0
    used_cost, excluded_cost = [], []
    used_hours, excluded_hours = [], []

    for a in cost_loaded:
        frac = _earned_fraction(a, method)
        aid = a.get('code') or a.get('id') or ''
        if frac is None:
            excluded_cost.append(aid)
            continue
        ev_cost += frac * (_num(a.get('budgetedCost')) or 0.0)
        used_cost.append(aid)

    for a in rsrc_loaded:
        frac = _earned_fraction(a, method)
        aid = a.get('code') or a.get('id') or ''
        if frac is None:
            excluded_hours.append(aid)
            continue
        ev_hours += frac * (_num(a.get('budgetedHours')) or 0.0)
        used_hours.append(aid)

    return {
        'engineVersion': ENGINE_VERSION,
        'method': method,
        'cost': {
            'available': bac_cost is not None,
            'bac': bac_cost,
            # None (not 0.0) when the method produced no usable fraction for
            # ANY cost-loaded activity — e.g. PHYSICAL_PCT_COMPLETE on a
            # schedule that never used P6's physical-% tracking. A literal
            # 0.0 would misrepresent "method not applicable" as "no progress".
            'ev': (round(ev_cost, 2) if used_cost else None),
            'ac': ac_cost,
            'cv': (round(ev_cost - ac_cost, 2) if used_cost and ac_cost is not None else None),
            'cpi': (_safe_div(ev_cost, ac_cost) if used_cost else None),
            'activitiesUsed': len(used_cost),
            'activitiesExcluded': len(excluded_cost),
            'excludedActivityIds': excluded_cost[:50],
        },
        'hours': {
            'available': bac_hours is not None,
            'bac': bac_hours,
            'ev': (round(ev_hours, 2) if used_hours else None),
            'ac': ac_hours,
            'cv': (round(ev_hours - ac_hours, 2) if used_hours and ac_hours is not None else None),
            'cpi': (_safe_div(ev_hours, ac_hours) if used_hours else None),
            'activitiesUsed': len(used_hours),
            'activitiesExcluded': len(excluded_hours),
            'excludedActivityIds': excluded_hours[:50],
        },
    }


# ── Planned Value ───────────────────────────────────────────────────────────

def compute_planned_value(
    activities: List[dict], data_date: date, method: str = 'LINEAR_BASELINE_SPREAD'
) -> Dict[str, Any]:
    if method not in PV_METHODS:
        raise ValueError(f'Unknown PV method: {method}. Valid: {PV_METHODS}')
    if data_date is None:
        # Same 'cost'/'hours' shape as the normal return below (each with
        # its own `available`/`pv`/coverage keys) — callers like
        # compute_cost_summary()'s _dimension() index into pv[section]
        # unconditionally, so this early-return must not be a differently
        # shaped payload.
        unavailable = {'available': False, 'pv': None, 'activitiesUsed': 0, 'activitiesExcluded': 0}
        return {
            'engineVersion': ENGINE_VERSION, 'method': method,
            'warning': 'No data date supplied — planned value cannot be evaluated at a point in time.',
            'cost': dict(unavailable), 'hours': dict(unavailable),
        }

    cost_loaded = [a for a in activities if a.get('isCostLoaded')]
    rsrc_loaded = [a for a in activities if a.get('isResourceLoaded')]

    pv_cost, used_cost, excluded_cost = 0.0, [], []
    for a in cost_loaded:
        frac = _planned_fraction(a, data_date)
        aid = a.get('code') or a.get('id') or ''
        if frac is None:
            excluded_cost.append(aid)
            continue
        pv_cost += frac * (_num(a.get('budgetedCost')) or 0.0)
        used_cost.append(aid)

    pv_hours, used_hours, excluded_hours = 0.0, [], []
    for a in rsrc_loaded:
        frac = _planned_fraction(a, data_date)
        aid = a.get('code') or a.get('id') or ''
        if frac is None:
            excluded_hours.append(aid)
            continue
        pv_hours += frac * (_num(a.get('budgetedHours')) or 0.0)
        used_hours.append(aid)

    return {
        'engineVersion': ENGINE_VERSION,
        'method': method,
        'warning': (
            'Planned Value is a calendar-day linear spread of budgeted cost/hours '
            'across each activity\'s baseline start/finish window — not imported '
            'P6 time-phased cost data (P6 period-phased budget tables are not '
            'parsed by this application).'
        ),
        'cost': {
            'available': bool(used_cost),
            'pv': (round(pv_cost, 2) if used_cost else None),
            'activitiesUsed': len(used_cost),
            'activitiesExcluded': len(excluded_cost),
        },
        'hours': {
            'available': bool(used_hours),
            'pv': (round(pv_hours, 2) if used_hours else None),
            'activitiesUsed': len(used_hours),
            'activitiesExcluded': len(excluded_hours),
        },
    }


# ── Forecast (EAC scenarios) ────────────────────────────────────────────────

def _forecast_scenarios(
    bac: Optional[float], ev: Optional[float], ac: Optional[float],
    cpi: Optional[float], spi: Optional[float],
    activities: List[dict], loaded_key: str, remaining_field: str,
) -> List[Dict[str, Any]]:
    """Shared EAC-scenario logic for both cost and hours — see
    compute_forecast()/compute_cost_summary() for the public entry points."""
    scenarios: List[Dict[str, Any]] = []

    if bac is not None and cpi:   # cpi==0.0 makes BAC/CPI undefined — correctly excluded
        eac = bac / cpi
        scenarios.append({
            'methodology': 'CPI_BASED',
            'label': 'CPI-based (assumes current cost performance continues)',
            'formula': 'EAC = BAC / CPI',
            'eac': round(eac, 2),
            'vac': round(bac - eac, 2),
        })

    loaded = [a for a in activities if a.get(loaded_key)]
    if ac is not None and loaded:
        etc = sum(_num(a.get(remaining_field)) or 0.0 for a in loaded)
        eac = ac + etc
        scenarios.append({
            'methodology': 'BOTTOM_UP',
            'label': 'Bottom-up (actual + P6\'s own remaining estimate)',
            'formula': f'EAC = AC + ETC (ETC = sum of TASKRSRC {remaining_field})',
            'etc': round(etc, 2),
            'eac': round(eac, 2),
            'vac': (round(bac - eac, 2) if bac is not None else None),
        })

    if cpi and spi and bac is not None and ev is not None and ac is not None:
        denom = cpi * spi
        if denom:
            eac = ac + (bac - ev) / denom
            scenarios.append({
                'methodology': 'CPI_SPI_COMPOSITE',
                'label': 'CPI x SPI composite (assumes both cost and schedule performance persist)',
                'formula': 'EAC = AC + (BAC - EV) / (CPI x SPI)',
                'eac': round(eac, 2),
                'vac': round(bac - eac, 2),
            })

    return scenarios


def compute_forecast(ev_result: Dict[str, Any], activities: List[dict]) -> Dict[str, Any]:
    """
    Returns cost EAC as multiple labeled scenarios — never a single "the"
    forecast. `approvedEac` (from ManualCostEntry) is layered in by the
    view, not here, since this module has no DB access. (This function
    predates SPI being in scope, so its CPI x SPI composite is only added
    when compute_cost_summary() calls _forecast_scenarios() directly with
    both indices; calling compute_forecast() alone yields CPI_BASED/
    BOTTOM_UP only.)
    """
    cost = ev_result.get('cost', {})
    scenarios = _forecast_scenarios(
        cost.get('bac'), cost.get('ev'), cost.get('ac'), cost.get('cpi'), None,
        activities, 'isCostLoaded', 'remainingCost',
    )
    return {
        'engineVersion': ENGINE_VERSION,
        'scenarios': scenarios,
        'note': 'Each EAC figure is a labeled scenario, not a single designated forecast.',
    }


# ── Labor Productivity ──────────────────────────────────────────────────────

def compute_productivity(
    activities: List[dict], method: str = 'DURATION_PCT_COMPLETE', group_by: Optional[str] = None
) -> Dict[str, Any]:
    """
    Budgeted / earned / actual / remaining hours and productivity factor
    (Earned Hours / Actual Hours), optionally aggregated by WBS/Area/
    Discipline/Contractor/System — same group_by vocabulary as
    schedule_risk.py's compute_risk() for consistency across the app.
    """
    if method not in EV_METHODS:
        raise ValueError(f'Unknown EV method: {method}. Valid: {EV_METHODS}')

    def _rollup(acts: List[dict]) -> Dict[str, Any]:
        rsrc_loaded = [a for a in acts if a.get('isResourceLoaded')]
        if not rsrc_loaded:
            return {
                'available': False, 'budgetedHours': None, 'earnedHours': None,
                'actualHours': None, 'remainingHours': None,
                'productivityFactor': None, 'hoursVariance': None,
                'activityCount': len(acts), 'resourceLoadedCount': 0,
            }
        budgeted = sum(_num(a.get('budgetedHours')) or 0.0 for a in rsrc_loaded)
        actual = sum(_num(a.get('actualHours')) or 0.0 for a in rsrc_loaded)
        remaining = sum(_num(a.get('remainingHours')) or 0.0 for a in rsrc_loaded)
        earned = 0.0
        used = 0
        for a in rsrc_loaded:
            frac = _earned_fraction(a, method)
            if frac is None:
                continue
            earned += frac * (_num(a.get('budgetedHours')) or 0.0)
            used += 1
        productivity_factor = _safe_div(earned, actual) if actual else None
        return {
            'available': True,
            'budgetedHours': round(budgeted, 2),
            'earnedHours': round(earned, 2),
            'actualHours': round(actual, 2),
            'remainingHours': round(remaining, 2),
            'productivityFactor': (round(productivity_factor, 3) if productivity_factor is not None else None),
            'hoursVariance': round(earned - actual, 2),
            'activityCount': len(acts),
            'resourceLoadedCount': len(rsrc_loaded),
            'earnedHoursCoverage': used,
        }

    result: Dict[str, Any] = {
        'engineVersion': ENGINE_VERSION, 'method': method, 'groupBy': group_by or 'project',
        'overall': _rollup(activities),
    }

    if group_by and group_by != 'project':
        field = GROUP_FIELD_MAP.get(group_by)
        if not field:
            result['error'] = f'Unknown group_by value: {group_by}'
            result['groups'] = []
            return result
        groups: Dict[str, List[dict]] = {}
        for a in activities:
            key = (a.get(field) or '').strip() or 'Unassigned'
            groups.setdefault(key, []).append(a)
        result['groups'] = [
            {'group': key, **_rollup(acts)}
            for key, acts in sorted(groups.items())
        ]

    return result


# ── Orchestrator ─────────────────────────────────────────────────────────────

def compute_cost_summary(
    activities: List[dict],
    data_date: Optional[date],
    ev_method: str = 'DURATION_PCT_COMPLETE',
    pv_method: str = 'LINEAR_BASELINE_SPREAD',
) -> Dict[str, Any]:
    """Main entry point — BAC/PV/EV/AC/CV/SV/CPI/SPI/TCPI plus forecast
    scenarios, all in one traceable payload. DB-free and pure; manual
    entries (approved budget changes, commitments, approved EAC) are
    layered on by the view, which has DB access this module deliberately
    doesn't."""
    ev = compute_earned_value(activities, ev_method)
    pv = compute_planned_value(activities, data_date, pv_method)

    def _dimension(section: str, loaded_key: str, remaining_field: str) -> Dict[str, Any]:
        cost = ev[section]
        bac, ev_v, ac, cpi = cost['bac'], cost['ev'], cost['ac'], cost['cpi']
        pv_v = pv[section]['pv']
        sv = round(ev_v - pv_v, 2) if (ev_v is not None and pv_v is not None) else None
        spi = _safe_div(ev_v, pv_v)
        tcpi = _safe_div(
            (bac - ev_v) if (bac is not None and ev_v is not None) else None,
            (bac - ac) if (bac is not None and ac is not None) else None,
        )
        scenarios = _forecast_scenarios(bac, ev_v, ac, cpi, spi, activities, loaded_key, remaining_field)
        return {
            'values': {
                'bac': bac, 'pv': pv_v, 'ev': ev_v, 'ac': ac,
                'cv': cost['cv'], 'sv': sv,
                'cpi': (round(cpi, 3) if cpi is not None else None),
                'spi': (round(spi, 3) if spi is not None else None),
                'tcpi': (round(tcpi, 3) if tcpi is not None else None),
            },
            'forecast': {
                'engineVersion': ENGINE_VERSION,
                'scenarios': scenarios,
                'note': 'Each EAC figure is a labeled scenario, not a single designated forecast.',
            },
            'coverage': {
                'activitiesUsed': cost['activitiesUsed'],
                'activitiesExcluded': cost['activitiesExcluded'],
            },
        }

    cost_dim = _dimension('cost', 'isCostLoaded', 'remainingCost')
    hours_dim = _dimension('hours', 'isResourceLoaded', 'remainingHours')

    return {
        'engineVersion': ENGINE_VERSION,
        'dataDate': data_date.isoformat() if data_date else None,
        'evMethod': ev_method,
        'pvMethod': pv_method,
        'pvWarning': pv['warning'],
        'cost': cost_dim['values'],
        'hours': hours_dim['values'],
        'coverage': {
            'costActivitiesUsed': cost_dim['coverage']['activitiesUsed'],
            'costActivitiesExcluded': cost_dim['coverage']['activitiesExcluded'],
            'hoursActivitiesUsed': hours_dim['coverage']['activitiesUsed'],
            'hoursActivitiesExcluded': hours_dim['coverage']['activitiesExcluded'],
        },
        'forecast': cost_dim['forecast'],
        'hoursForecast': hours_dim['forecast'],
    }
