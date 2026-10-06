"""
Float Intelligence — ScheduleIQ Master Schedule Analysis, Float, Progress &
Milestone Intelligence phase.

Every function here is a pure aggregation/analytics layer over the rows
already produced by activity_analysis.build_activity_analysis() — nothing
in this module re-derives Total Float, re-matches Activity IDs, or
recomputes a movement/criticality flag. Imported P6 Total Float
(row['currentTotalFloat']/['baselineTotalFloat']/['previousTotalFloat'])
remains source truth throughout; this module only buckets, ranks, and
groups those already-computed values.

Golden rule enforced everywhere a group is summarized: Total Float is
NEVER summed across a group (summing float is not a meaningful project
concept). Group aggregates use count, min, and median only.

Completed-activity float display (P6 behavior): activity_analysis.py
already blanks `currentTotalFloat`/`freeFloat` to `None` for a row whose
activity is definitively Complete — so every function below that filters
or buckets on `currentTotalFloat is not None` (the histogram, both
scatters, the heat map, the deterioration/improvement rankings, newly-
negative/recovered lists) automatically excludes completed activities
from current/actionable float analysis with no extra code here. The one
exception is criticality: `critical` is the untouched imported P6 flag
(preserved for traceability regardless of completion), so summary/heat-
map "current critical" counts use `criticalActionable`
(critical AND NOT finished) instead, so a completed activity never
inflates the actionable critical population merely because its
stored/imported TF or isCritical flag is still zero/true.
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional, Tuple

from .activity_analysis import is_activity_complete

ENGINE_VERSION = '1.0.0'

# Reused, not reinvented: these match risk_register.py's own private
# _SEVERE_NEGATIVE_FLOAT_THRESHOLD / _BASELINE_VARIANCE_THRESHOLD constants
# exactly (same values, same authoritative origin — the existing Risk
# Register severity rules) — the same cross-module constant-duplication
# convention this codebase already uses for NEAR_CRITICAL_FLOAT_THRESHOLD
# (activity_analysis.py/update_intelligence.py/baseline_progress.py each
# independently declare the identical value rather than sharing an import).
# Float Analysis's Schedule Exposure Matrix (Improve Float Analysis
# Visualizations phase) reuses these so its quadrant boundaries are never
# arbitrary frontend values.
SEVERE_NEGATIVE_FLOAT_THRESHOLD = -10.0
BASELINE_VARIANCE_THRESHOLD_DAYS = 10.0

# Default analytical float bands — buckets only, never override imported
# P6 critical status (a row with isCritical=True stays critical regardless
# of which band its float falls in).
DEFAULT_FLOAT_BANDS: List[Tuple[str, Optional[float], Optional[float]]] = [
    ('Severe Negative', None, -20.0),
    ('Negative', -20.0, 0.0),
    ('Zero', 0.0, 0.0),
    ('Near Critical', 0.0, 10.0),
    ('Low', 10.0, 20.0),
    ('Moderate', 20.0, 40.0),
    ('High', 40.0, None),
]


def assign_float_band(tf: Optional[float], bands: Optional[List[Tuple[str, Optional[float], Optional[float]]]] = None) -> Optional[str]:
    if tf is None:
        return None
    bands = bands or DEFAULT_FLOAT_BANDS
    if tf == 0:
        for label, lo, hi in bands:
            if lo == 0.0 and hi == 0.0:
                return label
    for label, lo, hi in bands:
        if lo == 0.0 and hi == 0.0:
            continue  # the exact-zero band is only matched above
        lo_ok = lo is None or tf > lo
        hi_ok = hi is None or tf <= hi
        if lo_ok and hi_ok:
            return label
    return None


def _median(values: List[float]) -> Optional[float]:
    return round(statistics.median(values), 2) if values else None


def compute_float_summary(rows: List[dict]) -> Dict[str, Any]:
    current_tfs = [r['currentTotalFloat'] for r in rows if r['currentTotalFloat'] is not None]
    baseline_tfs = [r['baselineTotalFloat'] for r in rows if r['baselineTotalFloat'] is not None]
    float_changes = [r['floatChangeVsBaseline'] for r in rows if r['floatChangeVsBaseline'] is not None]
    return {
        'engineVersion': ENGINE_VERSION,
        'activitiesAnalyzed': len(rows),
        'negativeFloatCount': sum(1 for r in rows if r['negativeFloat']),
        'zeroFloatCount': sum(1 for r in rows if r['currentTotalFloat'] == 0),
        'nearCriticalCount': sum(1 for r in rows if r['nearCritical']),
        'criticalCount': sum(1 for r in rows if r['criticalActionable']),
        'drivingCount': sum(1 for r in rows if r['driving']),
        'newlyNegativeCount': sum(1 for r in rows if r['newlyNegativeFloat']),
        'recoveredFromNegativeCount': sum(1 for r in rows if r['recoveredFromNegativeFloat']),
        'floatImprovedCount': sum(1 for r in rows if r['floatImproved']),
        'floatDeterioratedCount': sum(1 for r in rows if r['floatDeteriorated']),
        'medianCurrentTotalFloat': _median(current_tfs),
        'minimumCurrentTotalFloat': min(current_tfs) if current_tfs else None,
        'baselineMedianTotalFloat': _median(baseline_tfs),
        'medianFloatChange': _median(float_changes),
        # Visibility into how many rows were excluded from the above
        # actionable counts/distributions purely because they are
        # Complete (their imported P6 Total Float is unchanged and
        # remains available per-row via importedCurrentTotalFloat).
        'completedActivitiesExcluded': sum(1 for r in rows if r.get('finished')),
    }


def compute_float_distribution(
    rows: List[dict], bucket_width: float = 10.0, min_bucket: float = -60.0, max_bucket: float = 100.0,
) -> Dict[str, Any]:
    """Numeric histogram: X = Total Float (bucketed), Y = activity count.
    Current, baseline, and (when available) previous series, so the UI can
    overlay them — never a single fixed-band summary pretending to be a
    histogram."""
    def _bucket(tf: float) -> str:
        clamped = max(min_bucket, min(max_bucket - 0.01, tf))
        lo = min_bucket + bucket_width * int((clamped - min_bucket) // bucket_width)
        hi = lo + bucket_width
        return f'{int(lo)} to {int(hi)}'

    def _order_key(label: str) -> float:
        return float(label.split(' to ')[0])

    buckets: Dict[str, Dict[str, int]] = {}

    def _add(series: str, tf: Optional[float]):
        if tf is None:
            return
        label = _bucket(tf)
        buckets.setdefault(label, {'current': 0, 'baseline': 0, 'previous': 0})[series] += 1

    for r in rows:
        _add('current', r['currentTotalFloat'])
        _add('baseline', r['baselineTotalFloat'])
        _add('previous', r['previousTotalFloat'])

    ordered = sorted(buckets.keys(), key=_order_key)
    return {
        'bucketWidth': bucket_width,
        'buckets': [{'range': label, **buckets[label]} for label in ordered],
        'hasBaseline': any(r['baselineTotalFloat'] is not None for r in rows),
        'hasPrevious': any(r['previousTotalFloat'] is not None for r in rows),
    }


def compute_baseline_vs_current_scatter(rows: List[dict]) -> List[Dict[str, Any]]:
    return [
        {
            'activityId': r['activityId'], 'activityName': r['activityName'],
            'baselineTotalFloat': r['baselineTotalFloat'], 'currentTotalFloat': r['currentTotalFloat'],
            'critical': r['critical'], 'wbs': r['wbs'], 'area': r['area'], 'discipline': r['discipline'],
        }
        for r in rows if r['baselineTotalFloat'] is not None and r['currentTotalFloat'] is not None
    ]


def compute_float_change_distribution(
    rows: List[dict], deterioration_threshold: float = -5.0, improvement_threshold: float = 5.0,
) -> Dict[str, Any]:
    changes = [r for r in rows if r['floatChangeVsBaseline'] is not None]
    deteriorated = [r for r in changes if r['floatChangeVsBaseline'] <= deterioration_threshold]
    improved = [r for r in changes if r['floatChangeVsBaseline'] >= improvement_threshold]
    stable = [r for r in changes if deterioration_threshold < r['floatChangeVsBaseline'] < improvement_threshold]
    return {
        'deteriorationThreshold': deterioration_threshold, 'improvementThreshold': improvement_threshold,
        'deterioratedCount': len(deteriorated), 'stableCount': len(stable), 'improvedCount': len(improved),
        'activitiesWithBaseline': len(changes),
    }


def compute_float_movement(row: dict) -> Dict[str, Any]:
    """Per-activity waterfall: Baseline TF -> Previous TF -> Current TF.
    'Float consumed' is a factual day-count between two of those points —
    never labeled project delay (that's a separate, unsupported claim)."""
    baseline, previous, current = row['baselineTotalFloat'], row['previousTotalFloat'], row['currentTotalFloat']
    consumed = None
    if baseline is not None and current is not None:
        consumed = baseline - current
    return {
        'activityId': row['activityId'], 'activityName': row['activityName'],
        'baselineTotalFloat': baseline, 'previousTotalFloat': previous, 'currentTotalFloat': current,
        'floatConsumed': consumed,
        'unitsNote': 'Float values are the imported P6 Total Float figures for each version — calendar-day-equivalent as stored by P6, not recomputed.',
    }


def compute_float_trend(version_points: List[Dict[str, Any]], activity_id: str) -> Dict[str, Any]:
    """version_points: [{versionId, versionLabel, dataDate, role, activities}]
    ordered oldest-to-newest (or any order — this function does not assume
    order; the caller controls chronology via its own version resolution,
    never via upload recency alone). Each version's own effective Data Date
    accompanies its own Total Float value — a version's point is never
    recomputed against another version's schedule.

    Completed-activity float display: each point's `totalFloat` is blanked
    (None) when the activity was already Complete AT THAT VERSION (the
    same is_activity_complete rule activity_analysis.py uses for the
    master row) — so an activity that only becomes Complete in the most
    recent version keeps its real historical figures on every earlier
    point and shows '—' only for the version(s) where it is Complete. The
    raw imported value is never discarded: `importedTotalFloat` always
    carries it regardless of completion status."""
    points = []
    for vp in version_points:
        act = next((a for a in vp['activities'] if str(a.get('code') or a.get('id') or '') == activity_id), None)
        imported_tf = None
        complete = False
        if act is not None:
            v = act.get('totalFloat')
            try:
                imported_tf = float(v) if v is not None and v != '' else None
            except (TypeError, ValueError):
                imported_tf = None
            complete = is_activity_complete(act)
        tf = None if complete else imported_tf
        points.append({
            'versionId': vp.get('versionId'), 'versionLabel': vp.get('versionLabel'),
            'dataDate': vp.get('dataDate'), 'role': vp.get('role'),
            'totalFloat': tf, 'importedTotalFloat': imported_tf, 'complete': complete,
            'found': act is not None,
        })
    return {'activityId': activity_id, 'points': points}


def compute_float_heatmap(rows: List[dict], group_by: str = 'area') -> Dict[str, Any]:
    """Never sums Total Float — group aggregates are count/min/median only."""
    groups: Dict[str, List[dict]] = {}
    for r in rows:
        key = r.get(group_by) or 'Unassigned'
        groups.setdefault(key, []).append(r)

    cells = []
    for key, group_rows in groups.items():
        tfs = [r['currentTotalFloat'] for r in group_rows if r['currentTotalFloat'] is not None]
        changes = [r['floatChangeVsBaseline'] for r in group_rows if r['floatChangeVsBaseline'] is not None]
        cells.append({
            'group': key,
            'activityCount': len(group_rows),
            'negativeFloatCount': sum(1 for r in group_rows if r['negativeFloat']),
            'zeroFloatCount': sum(1 for r in group_rows if r['currentTotalFloat'] == 0),
            'criticalCount': sum(1 for r in group_rows if r['criticalActionable']),
            'nearCriticalCount': sum(1 for r in group_rows if r['nearCritical']),
            'drivingCount': sum(1 for r in group_rows if r['driving']),
            'deteriorationCount': sum(1 for r in group_rows if r['floatDeteriorated']),
            # Worst-case, never summed — Total Float is not an additive
            # quantity across activities (see module docstring).
            'minimumTotalFloat': min(tfs) if tfs else None,
            'medianTotalFloat': _median(tfs),
            'medianFloatChange': _median(changes),
        })
    cells.sort(key=lambda c: (c['minimumTotalFloat'] if c['minimumTotalFloat'] is not None else float('inf')))
    return {'groupBy': group_by, 'cells': cells}


def compute_float_vs_finish_variance_scatter(rows: List[dict]) -> List[Dict[str, Any]]:
    return [
        {
            'activityId': r['activityId'], 'activityName': r['activityName'],
            'finishVarianceDays': r['finishVarianceDays'], 'currentTotalFloat': r['currentTotalFloat'],
            'critical': r['critical'], 'wbs': r['wbs'], 'area': r['area'],
        }
        for r in rows if r['finishVarianceDays'] is not None and r['currentTotalFloat'] is not None
    ]


def compute_float_vs_remaining_duration_scatter(rows: List[dict]) -> List[Dict[str, Any]]:
    return [
        {
            'activityId': r['activityId'], 'activityName': r['activityName'],
            'remainingDuration': r['remainingDuration'], 'currentTotalFloat': r['currentTotalFloat'],
            'critical': r['critical'], 'wbs': r['wbs'], 'area': r['area'],
        }
        for r in rows if r['remainingDuration'] is not None and r['currentTotalFloat'] is not None
    ]


_EXPOSURE_TABLE_FIELDS = (
    'activityId', 'activityName', 'wbs', 'area', 'currentTotalFloat', 'finishVarianceDays',
    'approvedBaselineVarianceDays', 'currentFinish', 'activityStatus', 'critical', 'driving', 'isMilestone',
)


def _exposure_row(r: dict) -> Dict[str, Any]:
    return {k: r.get(k) for k in _EXPOSURE_TABLE_FIELDS}


def rank_schedule_exposure(rows: List[dict], top_n: Optional[int] = None) -> List[Dict[str, Any]]:
    """Deterministic 'Top Schedule Exposure Activities' ranking — explicitly
    NOT a weighted composite score (Improve Float Analysis Visualizations
    phase's explicit requirement). Candidates are actionable negative-float
    rows only (the same `negativeFloat` flag already computed per row — a
    completed activity's blanked currentTotalFloat can never qualify).

    Sort key, in order:
      1. Most negative current Total Float (primary)
      2. Driving-path relevance (secondary — driving activities surface
         first among equally-negative-float activities)
      3. Larger adverse finish variance (tertiary tie-break; a missing
         variance sorts last, never treated as zero)

    Never truncates unless top_n is given — matches every other ranking
    function in this module; the UI's own Top 10/20/50 selector slices
    client-side like the existing RankTable components already do."""
    candidates = [r for r in rows if r['negativeFloat']]
    candidates.sort(key=lambda r: (
        r['currentTotalFloat'],
        0 if r['driving'] else 1,
        -(r['finishVarianceDays'] if r['finishVarianceDays'] is not None else float('-inf')),
    ))
    out = [_exposure_row(r) for r in candidates]
    return out if top_n is None else out[:top_n]


def compute_exposure_matrix(rows: List[dict], baseline_designated: bool) -> Dict[str, Any]:
    """Schedule Exposure Matrix — X: Approved Baseline Finish Variance
    (`approvedBaselineVarianceDays` — Current Forecast Finish vs the
    DESIGNATED Approved/Revised Baseline VERSION, computed directly in
    activity_analysis.py from the baseline-version match; see that
    module's docstring for the full distinction). Y: current Total Float.

    Deliberately independent of whether a PREVIOUS update exists —
    `baseline_designated` here means exactly one thing: a baseline
    VERSION is designated for this project. A previous update is NEVER
    required to compute Current vs Approved Baseline variance (that
    pairing is only needed for update-to-update MOVEMENT, a different,
    separately-reported concept — updateMovementDays/finishMovementDays
    on the master row — which this matrix does not use).

    This is explicitly NOT the same value as `finishVarianceDays`, which
    compares against each activity's own embedded P6 baseline field
    (bFinish/target_end_date) regardless of whether a ScheduleIQ baseline
    version is designated — the two must never be conflated or
    substituted for each other; see activity_analysis.py's docstring.

    Quadrant boundaries reuse EXISTING authoritative thresholds — never
    arbitrary frontend values: the same `negativeFloat` flag used
    everywhere else in ScheduleIQ splits the Y axis, and
    BASELINE_VARIANCE_THRESHOLD_DAYS (matching risk_register.py's own
    baseline-variance severity rule) splits the X axis.

        HIGH EXPOSURE      = negative float AND variance > threshold
        FLOAT CRITICAL     = negative float AND variance <= threshold
        VARIANCE CRITICAL  = non-negative float AND variance > threshold
        CONTROLLED/MONITOR = non-negative float AND variance <= threshold

    A row is plotted only when BOTH figures are genuinely available for
    it — never a manufactured comparison. When no baseline version is
    designated for this project at all, the whole matrix is reported
    unavailable with an honest reason; the rest of Float Analysis keeps
    working regardless."""
    if not baseline_designated:
        return {
            'available': False,
            'reason': 'Finish variance unavailable — no applicable comparison schedule is configured.',
            'thresholds': {'varianceDays': BASELINE_VARIANCE_THRESHOLD_DAYS},
            'points': [],
        }

    points = []
    for r in rows:
        tf, var = r.get('currentTotalFloat'), r.get('approvedBaselineVarianceDays')
        if tf is None or var is None:
            continue
        if r['negativeFloat']:
            quadrant = 'HIGH_EXPOSURE' if var > BASELINE_VARIANCE_THRESHOLD_DAYS else 'FLOAT_CRITICAL'
        else:
            quadrant = 'VARIANCE_CRITICAL' if var > BASELINE_VARIANCE_THRESHOLD_DAYS else 'CONTROLLED_MONITOR'
        points.append({
            'activityId': r['activityId'], 'activityName': r['activityName'],
            'wbs': r.get('wbs'), 'area': r.get('area'),
            'currentTotalFloat': tf, 'approvedBaselineVarianceDays': var,
            'quadrant': quadrant, 'critical': r.get('critical'), 'driving': r.get('driving'),
            'activityStatus': r.get('activityStatus'),
        })

    return {
        'available': True, 'reason': None,
        'thresholds': {'varianceDays': BASELINE_VARIANCE_THRESHOLD_DAYS},
        'points': points,
    }


_DETERIORATION_TABLE_FIELDS = (
    'activityId', 'activityName', 'wbs', 'baselineTotalFloat', 'previousTotalFloat', 'currentTotalFloat',
    'floatChangeVsBaseline', 'floatChangeVsPrevious', 'finishVarianceDays', 'currentFinish',
    'critical', 'driving', 'isMilestone',
)


def _table_row(r: dict) -> Dict[str, Any]:
    return {k: r.get(k) for k in _DETERIORATION_TABLE_FIELDS}


def rank_float_deterioration(rows: List[dict], top_n: Optional[int] = None) -> List[Dict[str, Any]]:
    candidates = [r for r in rows if r['floatChangeVsBaseline'] is not None and r['floatChangeVsBaseline'] < 0]
    candidates.sort(key=lambda r: r['floatChangeVsBaseline'])
    out = [_table_row(r) for r in candidates]
    return out if top_n is None else out[:top_n]


def rank_float_improvement(rows: List[dict], top_n: Optional[int] = None) -> List[Dict[str, Any]]:
    candidates = [r for r in rows if r['floatChangeVsBaseline'] is not None and r['floatChangeVsBaseline'] > 0]
    candidates.sort(key=lambda r: -r['floatChangeVsBaseline'])
    out = [_table_row(r) for r in candidates]
    return out if top_n is None else out[:top_n]


def list_newly_negative(rows: List[dict]) -> List[Dict[str, Any]]:
    out = [_table_row(r) for r in rows if r['newlyNegativeFloat']]
    out.sort(key=lambda r: r['currentTotalFloat'] if r['currentTotalFloat'] is not None else 0)
    return out


def list_recovered_from_negative(rows: List[dict]) -> List[Dict[str, Any]]:
    """Factual label only — recovery of float does not imply the
    underlying schedule issue has been resolved."""
    return [_table_row(r) for r in rows if r['recoveredFromNegativeFloat']]


def compute_milestone_float_analysis(rows: List[dict], pred_of: Optional[Dict[str, List[dict]]] = None, by_id: Optional[Dict[str, dict]] = None) -> List[Dict[str, Any]]:
    """Per-milestone float posture. `negativeFloatPredecessorExposure` and
    `drivingPredecessorCount` require the relationship maps
    (activity_analysis.build_relationship_maps output) — when not supplied
    those two fields are omitted (Unavailable), never guessed."""
    by_id_rows = {r['activityId']: r for r in rows}
    out = []
    for r in rows:
        if not r['isMilestone']:
            continue
        neg_float_preds = None
        driving_pred_count = None
        if pred_of is not None and by_id is not None:
            preds = pred_of.get(r['activityId'], [])
            neg_float_preds = sum(
                1 for p in preds
                if (by_id.get(p['actId']) or {}).get('totalFloat') is not None and by_id[p['actId']]['totalFloat'] < 0
            )
            driving_pred_count = 1 if r.get('drivingPredecessor') else 0
        out.append({
            'activityId': r['activityId'], 'activityName': r['activityName'],
            'baselineFinish': r['baselineFinish'], 'currentFinish': r['currentFinish'],
            'finishVarianceDays': r['finishVarianceDays'],
            'baselineTotalFloat': r['baselineTotalFloat'], 'previousTotalFloat': r['previousTotalFloat'],
            'currentTotalFloat': r['currentTotalFloat'], 'floatChangeVsBaseline': r['floatChangeVsBaseline'],
            'drivingPredecessorCount': driving_pred_count,
            'negativeFloatPredecessorExposure': neg_float_preds,
            'riskFlagged': bool(r.get('scheduleRisk')),
        })
    out.sort(key=lambda m: (m['currentFinish'] is None, m['currentFinish'] or ''))
    return out
