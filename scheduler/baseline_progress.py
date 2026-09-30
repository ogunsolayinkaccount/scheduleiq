"""
Baseline & Progress / Look-Ahead Engine — ScheduleIQ

Deterministic, DB-free (same pattern as schedule_comparison.py /
progress_curve.py / schedule_risk.py) — matches a BASELINE activity list
against a CURRENT activity list by Activity ID, exposes the full
baseline-vs-current-vs-actual picture per activity, classifies each into a
single construction-friendly status, and supports Look-Ahead windowing,
variance ranking, weekly histograms, and summary KPIs on top of that same
matched row set. Everything downstream (S-Curve, histogram, Gantt bars,
variance chart, summary cards, tooltips) is built from these same rows —
no separate matching or variance formula exists anywhere else.

Field terminology (matches the rest of ScheduleIQ, e.g.
schedule_comparison.py's FIELD_SPECS and narrative_engine.py's `_fc()`):
  Baseline Start/Finish   — the BASELINE version's own bStart/bFinish
                            (P6 target_start_date/target_end_date as
                            recorded in that specific baselined version).
  Current Start/Finish    — the CURRENT version's own forecast/schedule
                            dates, using the same fallback chain
                            narrative_engine._fc() already established:
                            finish -> earlyFinish -> remainFinish ->
                            lateFinish -> bStart/bFinish as the last resort
                            (only reached when nothing else was captured).
  Actual Start/Finish     — start/finish, populated only once real work
                            began/finished.
  Remaining Start/Finish  — remainStart/remainFinish (P6 restart_date/
                            reend_date), where remaining work is scheduled.

Variance is always reported in calendar days. A `*WorkingDays` variance is
additionally included ONLY when a confidently-decoded P6 calendar was
supplied for that activity (see calendar_engine.py) — otherwise it is
explicitly None, never a guessed Mon-Fri figure, and callers should render
"Working-day variance: unavailable" per this project's established rule.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .calendar_engine import CalendarDefinition, working_days_between
from .progress_curve import compute_progress_curve

ENGINE_VERSION = '1.1.0'

GROUP_FIELDS = ('wbs', 'area', 'discipline', 'contractor', 'system')
LOOKAHEAD_PRESETS = {'2w': 2, '4w': 4, '6w': 6, '8w': 8, '12w': 12}
DEFAULT_LOOKAHEAD_WEEKS = 4
DEFAULT_TOP_DELAYED_N = 10
DEFAULT_MANAGEMENT_LIST_N = 10
MILESTONE_APPROACHING_DAYS = 14

STATUS_VALUES = (
    'ADDED_SINCE_BASELINE', 'REMOVED_FROM_CURRENT', 'COMPLETE',
    'SHOULD_HAVE_FINISHED', 'SHOULD_HAVE_STARTED', 'IN_PROGRESS',
    'DELAYED_FINISH', 'DELAYED_START', 'AHEAD', 'ON_PLAN',
)


# ── Shared date/id helpers (small local copies — see schedule_risk.py's
# module docstring for why these mirror sibling engines rather than import
# a private helper from one of them) ───────────────────────────────────────

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


# Canonical rule, centralized (this file's own copy already matched it -
# behavior unchanged, now a single source of truth instead of a duplicate).
from .activity_analysis import is_activity_complete as _is_complete


def _num(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _calendar_for(a: dict, calendars: Optional[Dict[str, CalendarDefinition]]) -> Optional[CalendarDefinition]:
    if not calendars:
        return None
    cal_id = a.get('calendarId')
    return calendars.get(cal_id) if cal_id else None


def _variance(base: Optional[date], curr: Optional[date], cal: Optional[CalendarDefinition]) -> Tuple[Optional[int], Optional[int]]:
    """Returns (calendarDayVariance, workingDayVariance) — positive means
    curr is later than base (a slip); negative means earlier (ahead)."""
    if base is None or curr is None:
        return None, None
    calendar_days = (curr - base).days
    working_days = working_days_between(base, curr, cal) if cal else None
    return calendar_days, working_days


# ── Status classification ───────────────────────────────────────────────────
#
# Exactly one status per activity, in this priority order (highest first —
# the first matching rule wins, deliberately, so an activity that is both
# "in progress" and "should have finished" is reported as the more urgent
# SHOULD_HAVE_FINISHED, not the less urgent IN_PROGRESS):
#
#   1. ADDED_SINCE_BASELINE   — no baseline match at all
#   2. COMPLETE               — Actual Finish is set, or % Complete >= 100
#   3. SHOULD_HAVE_FINISHED   — Baseline Finish < Data Date, activity incomplete
#   4. SHOULD_HAVE_STARTED    — Baseline Start < Data Date, no Actual Start, incomplete
#   5. IN_PROGRESS            — Actual Start is set, activity incomplete,
#                               and neither "should have" condition above applies
#   6. DELAYED_FINISH         — Current Finish > Baseline Finish (both present)
#   7. DELAYED_START          — Current Start > Baseline Start (both present)
#   8. AHEAD                  — Current Start < Baseline Start, or
#                               Current Finish < Baseline Finish
#   9. ON_PLAN                — none of the above; current timing matches
#                               baseline (the default when nothing else fits)
#
# "REMOVED_FROM_CURRENT" activities (baseline-only, no current match) are
# reported with that as both matchStatus and status — no timing status
# applies to work that no longer exists in the current schedule.
def _classify_status(row: Dict[str, Any], data_date: Optional[date]) -> str:
    if row['matchStatus'] == 'ADDED_SINCE_BASELINE':
        return 'ADDED_SINCE_BASELINE'
    if row['matchStatus'] == 'REMOVED_FROM_CURRENT':
        return 'REMOVED_FROM_CURRENT'

    complete = row['_complete']
    if complete:
        return 'COMPLETE'

    bl_start, bl_finish = row['_baselineStartDate'], row['_baselineFinishDate']
    cu_start, cu_finish = row['_currentStartDate'], row['_currentFinishDate']
    actual_start = row['_actualStartDate']

    if data_date and bl_finish and bl_finish < data_date:
        return 'SHOULD_HAVE_FINISHED'
    if data_date and bl_start and bl_start < data_date and not actual_start:
        return 'SHOULD_HAVE_STARTED'
    if actual_start:
        return 'IN_PROGRESS'
    if bl_finish and cu_finish and cu_finish > bl_finish:
        return 'DELAYED_FINISH'
    if bl_start and cu_start and cu_start > bl_start:
        return 'DELAYED_START'
    if (bl_start and cu_start and cu_start < bl_start) or (bl_finish and cu_finish and cu_finish < bl_finish):
        return 'AHEAD'
    return 'ON_PLAN'


# ── Activity-level matching (Phase 1/2) ─────────────────────────────────────

def match_baseline_current(
    baseline_activities: List[dict],
    current_activities: List[dict],
    data_date: Optional[date] = None,
    calendars: Optional[Dict[str, CalendarDefinition]] = None,
) -> List[Dict[str, Any]]:
    """One row per activity matched by Activity ID between baseline and
    current, plus baseline-only ("Removed / Missing From Current") and
    current-only ("Added Since Baseline") rows. Never fabricates a field —
    every date/duration/float here comes straight from the source activity
    dict, or is None."""
    baseline_by_id = {_act_id(a): a for a in baseline_activities if _act_id(a)}
    current_by_id = {_act_id(a): a for a in current_activities if _act_id(a)}
    all_ids = set(baseline_by_id) | set(current_by_id)

    rows: List[Dict[str, Any]] = []
    for aid in all_ids:
        b = baseline_by_id.get(aid)
        c = current_by_id.get(aid)
        primary = c or b   # metadata (name/wbs/etc) prefers the current version when available

        match_status = 'MATCHED' if (b and c) else ('ADDED_SINCE_BASELINE' if c else 'REMOVED_FROM_CURRENT')

        bl_start = _parse_date(b.get('bStart')) if b else None
        bl_finish = _parse_date(b.get('bFinish')) if b else None
        bl_dur = _num(b.get('dur')) if b else None

        cu_start = _current_start(c) if c else None
        cu_finish = _current_finish(c) if c else None
        actual_start = _parse_date(c.get('start')) if c else None
        actual_finish = _parse_date(c.get('finish')) if c else None
        remain_start = _parse_date(c.get('remainStart')) if c else None
        remain_finish = _parse_date(c.get('remainFinish')) if c else None
        orig_dur = _num(c.get('dur')) if c else None
        remain_dur = _num(c.get('remainDur')) if c else None
        pct_complete = _num(c.get('pctComplete')) if c else None
        total_float = _num(c.get('totalFloat')) if c else None

        cal = _calendar_for(c or b or {}, calendars)
        start_var_days, start_var_wd = _variance(bl_start, cu_start, cal)
        finish_var_days, finish_var_wd = _variance(bl_finish, cu_finish, cal)

        row: Dict[str, Any] = {
            'activityId': aid,
            'activityName': primary.get('name', '') if primary else '',
            'wbs': primary.get('wbs', '') if primary else '',
            'area': primary.get('area', '') if primary else '',
            'discipline': primary.get('discipline', '') if primary else '',
            'contractor': primary.get('contractor', '') if primary else '',
            'system': primary.get('system', '') if primary else '',
            'isMilestone': bool(primary.get('isMilestone')) if primary else False,
            'isCritical': bool(c.get('isCritical')) if c else False,
            'calendarId': (c or b or {}).get('calendarId') or '',
            'calendarName': (c or b or {}).get('calendarName') or (c or b or {}).get('calendar') or '',
            # Passed through verbatim for the activity detail drawer (Baseline
            # vs Forecast Activity Chart phase) — not a new calculation, just
            # carrying the current version's own relationship data forward.
            'predecessors': (c or {}).get('predecessors') or [],
            'successors': (c or {}).get('successors') or [],
            'matchStatus': match_status,

            'baselineStart': bl_start.isoformat() if bl_start else None,
            'baselineFinish': bl_finish.isoformat() if bl_finish else None,
            'baselineDuration': bl_dur,
            'currentStart': cu_start.isoformat() if cu_start else None,
            'currentFinish': cu_finish.isoformat() if cu_finish else None,
            'actualStart': actual_start.isoformat() if actual_start else None,
            'actualFinish': actual_finish.isoformat() if actual_finish else None,
            'remainingStart': remain_start.isoformat() if remain_start else None,
            'remainingFinish': remain_finish.isoformat() if remain_finish else None,
            'originalDuration': orig_dur,
            'remainingDuration': remain_dur,
            'pctComplete': pct_complete,
            'totalFloat': total_float,

            'startVarianceDays': start_var_days,
            'finishVarianceDays': finish_var_days,
            'startVarianceWorkingDays': start_var_wd,
            'finishVarianceWorkingDays': finish_var_wd,
            'workingDayCalendarAvailable': cal is not None,

            # Hours, only when genuinely resource-loaded — see compute_summary_cards().
            'baselineHours': (_num(b.get('budgetedHours')) if b and b.get('isResourceLoaded') else None),
            'forecastHours': (_num(c.get('budgetedHours')) if c and c.get('isResourceLoaded') else None),
            'actualHours': (_num(c.get('actualHours')) if c and c.get('isResourceLoaded') else None),

            # Internal-only fields used by _classify_status(), stripped before return.
            '_complete': _is_complete(c) if c else False,
            '_baselineStartDate': bl_start, '_baselineFinishDate': bl_finish,
            '_currentStartDate': cu_start, '_currentFinishDate': cu_finish,
            '_actualStartDate': actual_start,
        }
        row['status'] = _classify_status(row, data_date)
        rows.append(row)

    for row in rows:
        for k in list(row.keys()):
            if k.startswith('_'):
                del row[k]

    rows.sort(key=lambda r: (r['baselineStart'] or r['currentStart'] or '9999-99-99', r['activityId']))
    return rows


# ── Look-Ahead window (Phase 5) ─────────────────────────────────────────────

def compute_lookahead_window(
    data_date: Optional[date], weeks: Optional[int] = None,
    from_date: Optional[date] = None, to_date: Optional[date] = None,
) -> Dict[str, Any]:
    """Custom from/to wins when both are supplied. Otherwise the window is
    [data_date, data_date + weeks*7 days). Returns unavailable (nulls) when
    there is no data_date and no explicit from/to — never defaults to today."""
    if from_date and to_date:
        return {'fromDate': from_date.isoformat(), 'toDate': to_date.isoformat(), 'weeks': None, 'available': True}
    if data_date:
        w = weeks or DEFAULT_LOOKAHEAD_WEEKS
        end = data_date + timedelta(weeks=w)
        return {'fromDate': data_date.isoformat(), 'toDate': end.isoformat(), 'weeks': w, 'available': True}
    return {'fromDate': None, 'toDate': None, 'weeks': weeks, 'available': False}


def filter_lookahead(rows: List[Dict[str, Any]], from_date: date, to_date: date) -> List[Dict[str, Any]]:
    """Includes any activity whose baseline OR current window overlaps
    [from_date, to_date] at all — planned/forecast to start or finish in
    the window, already underway and continuing through it, or baselined
    to have finished before the window but still incomplete (so overdue
    work doesn't silently disappear from the look-ahead)."""
    out = []
    for r in rows:
        if r['matchStatus'] == 'REMOVED_FROM_CURRENT':
            continue
        spans = []
        for s_key, f_key in (('currentStart', 'currentFinish'), ('baselineStart', 'baselineFinish')):
            s = _parse_date(r.get(s_key))
            f = _parse_date(r.get(f_key))
            if s or f:
                spans.append((s or f, f or s))
        overdue = r['status'] in ('SHOULD_HAVE_STARTED', 'SHOULD_HAVE_FINISHED')
        in_window = any(s <= to_date and f >= from_date for s, f in spans if s and f)
        if in_window or overdue:
            out.append(r)
    return out


# ── Variance ranking (Phase 10) ─────────────────────────────────────────────

def rank_variance(rows: List[Dict[str, Any]], field: str = 'finishVarianceDays', top_n: Optional[int] = None) -> List[Dict[str, Any]]:
    if field not in ('finishVarianceDays', 'startVarianceDays'):
        field = 'finishVarianceDays'
    ranked = sorted(
        [r for r in rows if r.get(field) is not None],
        key=lambda r: -r[field],   # worst (most positive/late) first
    )
    return ranked[:top_n] if top_n else ranked


# ── Weekly/monthly histogram (Phase 8) ──────────────────────────────────────

def _iso_week_key(d: date) -> Tuple[int, int]:
    iso = d.isocalendar()
    return (iso[0], iso[1])


def _week_start(key: Tuple[int, int]) -> date:
    year, week = key
    jan4 = date(year, 1, 4)
    week1_monday = jan4 - timedelta(days=jan4.isoweekday() - 1)
    return week1_monday + timedelta(weeks=week - 1)


def _bucket_key(d: date, period: str) -> Any:
    return _iso_week_key(d) if period == 'weekly' else (d.year, d.month)


def _bucket_label(key: Any, period: str) -> str:
    if period == 'weekly':
        return _week_start(key).strftime('%b %d')
    return date(key[0], key[1], 1).strftime('%b %y')


def _metric_value(a: dict, metric: str) -> float:
    if metric == 'hours':
        return _num(a.get('budgetedHours')) or 0.0
    if metric == 'cost':
        return _num(a.get('budgetedCost')) or 0.0
    return 1.0   # 'activities'


def build_histogram(
    baseline_activities: List[dict], current_activities: List[dict],
    metric: str = 'activities', period: str = 'weekly',
    from_date: Optional[date] = None, to_date: Optional[date] = None,
) -> Dict[str, Any]:
    """Baseline Planned vs Current/Forecast (and Actual, where actual dates
    exist) bars per period, bucketed by each activity's own finish date —
    never converts duration into hours; hours/cost bars are simply
    unavailable when the source isn't resource/cost-loaded."""
    if metric not in ('activities', 'hours', 'cost'):
        metric = 'activities'
    if period not in ('weekly', 'monthly'):
        period = 'weekly'

    if metric == 'hours' and not any(a.get('isResourceLoaded') for a in current_activities):
        return {'metric': metric, 'period': period, 'available': False, 'reason': 'No resource-loaded activities — hours are unavailable, not zero.', 'buckets': []}
    if metric == 'cost' and not any(a.get('isCostLoaded') for a in current_activities):
        return {'metric': metric, 'period': period, 'available': False, 'reason': 'No cost-loaded activities — cost is unavailable, not zero.', 'buckets': []}

    buckets: Dict[Any, Dict[str, float]] = {}

    def _add(activities: List[dict], date_field: str, series_key: str, filter_fn=None):
        for a in activities:
            if filter_fn and not filter_fn(a):
                continue
            d = _parse_date(a.get(date_field))
            if not d:
                continue
            if from_date and to_date and not (from_date <= d <= to_date):
                continue
            key = _bucket_key(d, period)
            b = buckets.setdefault(key, {'baseline': 0.0, 'currentForecast': 0.0, 'actual': 0.0})
            b[series_key] += _metric_value(a, metric)

    _add(baseline_activities, 'bFinish', 'baseline')
    _add(current_activities, 'bFinish', 'currentForecast')
    _add(current_activities, 'finish', 'actual', filter_fn=lambda a: bool(a.get('finish')))

    out = []
    for key in sorted(buckets.keys()):
        b = buckets[key]
        out.append({
            'period': _bucket_label(key, period),
            'periodStart': (_week_start(key) if period == 'weekly' else date(key[0], key[1], 1)).isoformat(),
            'baseline': round(b['baseline'], 2),
            'currentForecast': round(b['currentForecast'], 2),
            'actual': round(b['actual'], 2) if b['actual'] else None,
        })

    return {'metric': metric, 'period': period, 'available': True, 'buckets': out}


# ── Summary KPI cards (Phase 12) ────────────────────────────────────────────

def compute_summary_cards(rows: List[Dict[str, Any]], data_date: Optional[date],
                           from_date: Optional[date], to_date: Optional[date]) -> Dict[str, Any]:
    def _in_range(iso: Optional[str]) -> bool:
        if not (from_date and to_date):
            return False
        d = _parse_date(iso)
        return bool(d and from_date <= d <= to_date)

    matched = [r for r in rows if r['matchStatus'] != 'REMOVED_FROM_CURRENT']
    finish_variances = [r['finishVarianceDays'] for r in rows if r.get('finishVarianceDays') is not None]

    cards: Dict[str, Any] = {
        'activitiesInWindow': len(matched),
        'plannedStarts': sum(1 for r in rows if _in_range(r.get('baselineStart'))),
        'forecastStarts': sum(1 for r in rows if _in_range(r.get('currentStart'))),
        'plannedFinishes': sum(1 for r in rows if _in_range(r.get('baselineFinish'))),
        'forecastFinishes': sum(1 for r in rows if _in_range(r.get('currentFinish'))),
        'behindBaseline': sum(1 for r in rows if r['status'] in ('DELAYED_START', 'DELAYED_FINISH', 'SHOULD_HAVE_STARTED', 'SHOULD_HAVE_FINISHED')),
        'shouldHaveStarted': sum(1 for r in rows if r['status'] == 'SHOULD_HAVE_STARTED'),
        'shouldHaveFinished': sum(1 for r in rows if r['status'] == 'SHOULD_HAVE_FINISHED'),
        'inProgress': sum(1 for r in rows if r['status'] == 'IN_PROGRESS'),
        'criticalActivities': sum(1 for r in rows if r['isCritical']),
        'averageFinishVarianceDays': (round(sum(finish_variances) / len(finish_variances), 1) if finish_variances else None),
    }

    baseline_hours = [r['baselineHours'] for r in rows if r.get('baselineHours') is not None]
    forecast_hours = [r['forecastHours'] for r in rows if r.get('forecastHours') is not None]
    actual_hours = [r['actualHours'] for r in rows if r.get('actualHours') is not None]
    cards['plannedHours'] = round(sum(baseline_hours), 1) if baseline_hours else None
    cards['forecastHours'] = round(sum(forecast_hours), 1) if forecast_hours else None
    cards['actualHours'] = round(sum(actual_hours), 1) if actual_hours else None
    return cards


# ── S-Curve (Phase 3) ────────────────────────────────────────────────────────
#
# Deliberately a thin wrapper around progress_curve.compute_progress_curve()
# — the cumulative-curve math (bucketing, weighting, baseline/current/
# previous series) already exists there and is reused as-is; this function
# only picks the right default metric, gates hours/cost availability, and
# marks each period as past (statused, before the effective Data Date) or
# future (forecast, after it) so the frontend can render the historical/
# forecast split the Baseline & Progress workspace needs.

def build_scurve(
    baseline_activities: List[dict], current_activities: List[dict],
    data_date: Optional[date] = None, metric: Optional[str] = None, period: str = 'weekly',
) -> Dict[str, Any]:
    if metric is None:
        metric = 'hours' if any(a.get('isResourceLoaded') for a in current_activities) else 'duration'
    if metric == 'hours' and not any(a.get('isResourceLoaded') for a in current_activities):
        return {'metric': metric, 'period': period, 'available': False, 'reason': 'No resource-loaded activities — Labor Hours is unavailable, not zero.', 'dataDate': data_date.isoformat() if data_date else None, 'periods': []}
    if metric == 'cost' and not any(a.get('isCostLoaded') for a in current_activities):
        return {'metric': metric, 'period': period, 'available': False, 'reason': 'No cost-loaded activities — Cost is unavailable, not zero.', 'dataDate': data_date.isoformat() if data_date else None, 'periods': []}

    curve_period = period if period in ('daily', 'weekly') else 'monthly'
    curve = compute_progress_curve(
        current_activities, weighting=metric, period=curve_period, baseline_activities=(baseline_activities or None),
    )
    for p in curve['periods']:
        end = _parse_date(p.get('periodEnd'))
        p['isPast'] = bool(data_date and end and end <= data_date)

    return {
        'metric': metric, 'period': curve_period, 'available': True,
        'dataDate': data_date.isoformat() if data_date else None,
        'periods': curve['periods'],
        'hasBaseline': curve['hasBaseline'],
        'methodologyNote': curve['methodologyNote'],
    }


# ── Global filters (Phase 11) ───────────────────────────────────────────────

def _filtered_activity_lists(
    baseline_activities: List[dict], current_activities: List[dict],
    filtered_rows: Optional[List[Dict[str, Any]]],
) -> Tuple[List[dict], List[dict]]:
    """Projects apply_filters()'s already-filtered row set back onto the
    raw activity dicts build_scurve()/build_histogram() accept, so those
    two views honor the same global filter state as the activity table and
    summary cards (`filtered_rows=None` means "no filters active" — return
    the lists unchanged). No new filtering logic is introduced here; this
    only restricts the inputs to functions that already exist."""
    if filtered_rows is None:
        return baseline_activities, current_activities
    allowed = {r['activityId'] for r in filtered_rows}
    return (
        [a for a in baseline_activities if _act_id(a) in allowed],
        [a for a in current_activities if _act_id(a) in allowed],
    )


def apply_filters(rows: List[Dict[str, Any]], filters: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """filters: any of wbs/area/discipline/contractor/system (exact match,
    case-insensitive), criticalOnly, nearCritical (0 < totalFloat <= 10),
    inProgress, notStarted, completed, delayedOnly (DELAYED_START/
    DELAYED_FINISH/SHOULD_HAVE_*), milestonesOnly. Every filter is additive
    (AND'd together). Unset/False filters are no-ops."""
    if not filters:
        return rows
    out = rows
    for field in GROUP_FIELDS:
        wanted = filters.get(field)
        if wanted:
            out = [r for r in out if str(r.get(field, '')).strip().lower() == str(wanted).strip().lower()]
    if filters.get('criticalOnly'):
        out = [r for r in out if r['isCritical']]
    if filters.get('nearCritical'):
        out = [r for r in out if r.get('totalFloat') is not None and 0 < r['totalFloat'] <= 10]
    if filters.get('inProgress'):
        out = [r for r in out if r['status'] == 'IN_PROGRESS']
    if filters.get('notStarted'):
        out = [r for r in out if r.get('actualStart') is None and r['status'] not in ('COMPLETE', 'ADDED_SINCE_BASELINE', 'REMOVED_FROM_CURRENT')]
    if filters.get('completed'):
        out = [r for r in out if r['status'] == 'COMPLETE']
    if filters.get('delayedOnly'):
        out = [r for r in out if r['status'] in ('DELAYED_START', 'DELAYED_FINISH', 'SHOULD_HAVE_STARTED', 'SHOULD_HAVE_FINISHED')]
    if filters.get('milestonesOnly'):
        out = [r for r in out if r['isMilestone']]
    return out


# ── Top-level orchestrators (Phase 14 API) ──────────────────────────────────

def build_baseline_progress(
    baseline_activities: List[dict], current_activities: List[dict],
    data_date: Optional[date] = None, calendars: Optional[Dict[str, CalendarDefinition]] = None,
    filters: Optional[Dict[str, Any]] = None,
    scurve_metric: Optional[str] = None, histogram_metric: str = 'activities', period: str = 'weekly',
) -> Dict[str, Any]:
    """Full, unwindowed baseline-vs-current picture — the activity table,
    variance rankings, status counts, S-Curve, and histogram across the
    whole schedule (not look-ahead-windowed — see build_lookahead() for
    the windowed view)."""
    rows = match_baseline_current(baseline_activities, current_activities, data_date, calendars)
    filtered = apply_filters(rows, filters)

    status_counts: Dict[str, int] = {s: 0 for s in STATUS_VALUES}
    for r in filtered:
        status_counts[r['status']] += 1

    # S-Curve/histogram must reflect the same active filters as the table
    # and status counts above (e.g. "Area C" only) — see
    # _filtered_activity_lists()'s docstring.
    baseline_f, current_f = _filtered_activity_lists(baseline_activities, current_activities, filtered if filters else None)

    return {
        'engineVersion': ENGINE_VERSION,
        'dataDate': data_date.isoformat() if data_date else None,
        'hasBaseline': bool(baseline_activities),
        'activityCount': len(filtered),
        'rows': filtered,
        'statusCounts': status_counts,
        'topFinishVariance': rank_variance(filtered, 'finishVarianceDays', top_n=25),
        'topStartVariance': rank_variance(filtered, 'startVarianceDays', top_n=25),
        'scurve': build_scurve(baseline_f, current_f, data_date, scurve_metric, period),
        'histogram': build_histogram(baseline_f, current_f, histogram_metric, period),
    }


def build_lookahead(
    baseline_activities: List[dict], current_activities: List[dict],
    data_date: Optional[date] = None, calendars: Optional[Dict[str, CalendarDefinition]] = None,
    weeks: Optional[int] = None, from_date: Optional[date] = None, to_date: Optional[date] = None,
    filters: Optional[Dict[str, Any]] = None, histogram_metric: str = 'activities', histogram_period: str = 'weekly',
) -> Dict[str, Any]:
    """The Look-Ahead-windowed view: filtered activity rows, summary KPI
    cards, and a matching histogram — all scoped to the same [from, to]
    window derived from the effective Data Date."""
    window = compute_lookahead_window(data_date, weeks, from_date, to_date)
    rows = match_baseline_current(baseline_activities, current_activities, data_date, calendars)
    rows = apply_filters(rows, filters)

    win_from = _parse_date(window['fromDate'])
    win_to = _parse_date(window['toDate'])
    windowed = filter_lookahead(rows, win_from, win_to) if (win_from and win_to) else []

    status_counts: Dict[str, int] = {s: 0 for s in STATUS_VALUES}
    for r in windowed:
        status_counts[r['status']] += 1

    # Histogram must reflect the same active filters as the windowed rows
    # above — see _filtered_activity_lists()'s docstring.
    baseline_f, current_f = _filtered_activity_lists(baseline_activities, current_activities, rows if filters else None)
    histogram = build_histogram(
        baseline_f, current_f, histogram_metric, histogram_period, win_from, win_to,
    ) if (win_from and win_to) else {'metric': histogram_metric, 'period': histogram_period, 'available': False, 'reason': 'No look-ahead window available.', 'buckets': []}

    return {
        'engineVersion': ENGINE_VERSION,
        'dataDate': data_date.isoformat() if data_date else None,
        'window': window,
        'activityCount': len(windowed),
        'rows': windowed,
        'statusCounts': status_counts,
        'summaryCards': compute_summary_cards(windowed, data_date, win_from, win_to),
        'histogram': histogram,
        'topFinishVariance': rank_variance(windowed, 'finishVarianceDays', top_n=25),
    }


# ── Report integration (Weekly Look-Ahead Report Integration phase) ────────
#
# Everything below composes the primitives above into the exact shape the
# Weekly Project Controls Report's "4-Week Look Ahead" section needs. No
# new matching, variance, windowing, or bucketing logic is introduced here
# — this is strictly ranking/grouping of rows build_lookahead() already
# produces, so the report can never drift from the Baseline & Progress
# workspace it's summarizing.

def rank_delayed(rows: List[Dict[str, Any]], field: str = 'finishVarianceDays', top_n: Optional[int] = None) -> List[Dict[str, Any]]:
    """Like rank_variance(), but scoped to activities that are genuinely
    delayed (positive variance = later than baseline) rather than the full
    variance spectrum, which also includes ahead-of-baseline rows — this is
    what "Top Delayed Activities" means. Worst (most late) first."""
    if field not in ('finishVarianceDays', 'startVarianceDays'):
        field = 'finishVarianceDays'
    delayed = [r for r in rows if r.get(field) is not None and r[field] > 0]
    return sorted(delayed, key=lambda r: -r[field])[:top_n] if top_n else sorted(delayed, key=lambda r: -r[field])


def rank_overdue(rows: List[Dict[str, Any]], status: str, data_date: Optional[date], top_n: Optional[int] = None) -> List[Dict[str, Any]]:
    """Ranks SHOULD_HAVE_STARTED / SHOULD_HAVE_FINISHED rows worst-first by
    how many days overdue they are relative to the effective Data Date
    (Data Date minus the relevant baseline date) — the management-attention
    lists the report needs. Adds a `daysOverdue` field to each returned row
    (a copy; the input rows are not mutated)."""
    field = 'baselineStart' if status == 'SHOULD_HAVE_STARTED' else 'baselineFinish'

    def _days_overdue(r: Dict[str, Any]) -> int:
        d = _parse_date(r.get(field))
        if not d or not data_date:
            return 0
        return (data_date - d).days

    candidates = [r for r in rows if r['status'] == status]
    ranked = sorted(candidates, key=_days_overdue, reverse=True)
    out = []
    for r in ranked:
        item = dict(r)
        item['daysOverdue'] = _days_overdue(r)
        out.append(item)
    return out[:top_n] if top_n else out


def build_milestone_lookahead(rows: List[Dict[str, Any]], data_date: Optional[date]) -> List[Dict[str, Any]]:
    """Milestones already inside the look-ahead-windowed row set (pass
    build_lookahead()'s `rows`), flagged for delay / negative float /
    approaching-within-14-days — never inventing urgency when a date or
    float value is unavailable. Sorted chronologically by current (else
    baseline) finish."""
    milestones = [r for r in rows if r.get('isMilestone')]
    out = []
    for r in milestones:
        curr = _parse_date(r.get('currentFinish')) or _parse_date(r.get('currentStart'))
        days_until = (curr - data_date).days if (curr and data_date) else None
        item = dict(r)
        item['isDelayed'] = bool(r.get('finishVarianceDays') is not None and r['finishVarianceDays'] > 0)
        item['isNegativeFloat'] = bool(r.get('totalFloat') is not None and r['totalFloat'] < 0)
        item['isApproaching'] = bool(days_until is not None and 0 <= days_until <= MILESTONE_APPROACHING_DAYS)
        item['daysUntil'] = days_until
        out.append(item)
    out.sort(key=lambda r: r.get('currentFinish') or r.get('baselineFinish') or '9999-99-99')
    return out


def build_report_lookahead(
    baseline_activities: List[dict], current_activities: List[dict],
    data_date: Optional[date] = None, calendars: Optional[Dict[str, CalendarDefinition]] = None,
    weeks: Optional[int] = None, from_date: Optional[date] = None, to_date: Optional[date] = None,
    filters: Optional[Dict[str, Any]] = None,
    top_delayed_n: int = DEFAULT_TOP_DELAYED_N, management_list_n: int = DEFAULT_MANAGEMENT_LIST_N,
) -> Dict[str, Any]:
    """The single authoritative payload for the report's '4-Week Look
    Ahead' section — built entirely from build_lookahead()'s output plus
    the ranking helpers above. `delayedActivities`/`shouldHaveStarted`/
    `shouldHaveFinished` are pre-ranked so PDF/Excel never re-sort or
    re-filter; `topDelayedN`/`managementListN` are persisted alongside so a
    reproduced snapshot always shows the same "Showing top N of X" note it
    showed when generated."""
    # Same preference the S-Curve already applies: Labor Hours when the
    # current schedule is genuinely resource-loaded, otherwise Activities
    # (the histogram engine has no "duration" bar concept — that fallback
    # is what build_scurve() uses instead).
    histogram_metric = 'hours' if any(a.get('isResourceLoaded') for a in current_activities) else 'activities'
    base = build_lookahead(
        baseline_activities, current_activities, data_date, calendars,
        weeks, from_date, to_date, filters,
        histogram_metric=histogram_metric, histogram_period='weekly',
    )
    rows = base['rows']

    delayed_activities = rank_delayed(rows, 'finishVarianceDays')
    should_have_started = rank_overdue(rows, 'SHOULD_HAVE_STARTED', data_date, top_n=management_list_n)
    should_have_finished = rank_overdue(rows, 'SHOULD_HAVE_FINISHED', data_date, top_n=management_list_n)
    milestones = build_milestone_lookahead(rows, data_date)

    # S-Curve is not window-clipped (it shows the whole project's cumulative
    # progress with the Data Date marked) but DOES honor the same global
    # filters as everything else in this section.
    all_rows_filtered = apply_filters(
        match_baseline_current(baseline_activities, current_activities, data_date, calendars), filters,
    ) if filters else None
    baseline_f, current_f = _filtered_activity_lists(baseline_activities, current_activities, all_rows_filtered)
    scurve = build_scurve(baseline_f, current_f, data_date, None, 'weekly')

    return {
        'engineVersion': ENGINE_VERSION,
        'dataDate': base['dataDate'],
        'window': base['window'],
        'activityCount': base['activityCount'],
        'rows': rows,
        'summaryCards': base['summaryCards'],
        'delayedActivities': delayed_activities,
        'topDelayedN': top_delayed_n,
        'shouldHaveStarted': should_have_started,
        'shouldHaveStartedTotalCount': sum(1 for r in rows if r['status'] == 'SHOULD_HAVE_STARTED'),
        'shouldHaveFinished': should_have_finished,
        'shouldHaveFinishedTotalCount': sum(1 for r in rows if r['status'] == 'SHOULD_HAVE_FINISHED'),
        'managementListN': management_list_n,
        'milestones': milestones,
        'histogram': base['histogram'],
        'scurve': scurve,
        'filters': filters or {},
    }
