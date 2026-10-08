"""
Variance Intelligence — ScheduleIQ.

Scheduler-level interpretation of variance that ALREADY exists on
activity_analysis.py's master row. This module calculates nothing new
about dates, float, criticality, or driving-path status — it only:

  1. SELECTS which of the three already-computed comparison-basis field
     sets (Approved Baseline / Embedded P6 Baseline / Previous Update) a
     caller has asked to view, never conflating one with another.
  2. CLASSIFIES the selected variance into scheduler-friendly language
     (Favorable/Unfavorable/No Movement/Unavailable) from the actual sign
     of a real date subtraction already performed upstream.
  3. CLASSIFIES overall exposure (FAVORABLE/MONITOR/WARNING/HIGH_EXPOSURE)
     from already-computed facts (direction, Total Float, driving,
     contractual-milestone reachability) — a transparent, explainable
     rule table, never an opaque weighted score.
  4. RANKS and AGGREGATES those classifications — reusing the exact same
     "never sum Total Float, count/min/median only" discipline
     float_intelligence.py already established.

Three distinct, never-conflated comparison concepts (see
activity_analysis.py's own docstring for the full reasoning):

  Baseline Variance            = Current vs the DESIGNATED Approved/
                                  Revised Baseline version
                                  (approvedBaselineVarianceDays).
  Embedded P6 Baseline Variance = Current vs each activity's own
                                  P6-assigned baseline snapshot
                                  (finishVarianceDays) — present
                                  regardless of whether a baseline
                                  version is designated.
  Update Movement               = Current vs the PREVIOUS schedule
                                  version (finishMovementDays) — a
                                  different concept from either baseline
                                  comparison; never called "baseline
                                  variance".

Contract Variance (vs a REGISTERED contractual milestone date) is a
FOURTH, separate concept again, reused as-is from
contractual_milestones.py — never conflated with any schedule
comparison basis.
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional, Set

from . import driving_chain
from .activity_analysis import NEAR_CRITICAL_FLOAT_THRESHOLD
from .float_intelligence import SEVERE_NEGATIVE_FLOAT_THRESHOLD

ENGINE_VERSION = '1.0.0'

# ── Comparison basis selection — pure field-name lookup, zero calculation ──

_BASIS_FIELDS = {
    'approvedBaseline': {
        'comparisonStart': 'approvedBaselineStart', 'comparisonFinish': 'approvedBaselineFinish',
        'startVarianceDays': 'approvedBaselineStartVarianceDays', 'finishVarianceDays': 'approvedBaselineVarianceDays',
        'startVarianceWorkingDays': 'approvedBaselineStartVarianceWorkingDays',
        'finishVarianceWorkingDays': 'approvedBaselineVarianceWorkingDays',
    },
    'embeddedBaseline': {
        'comparisonStart': 'embeddedBaselineStart', 'comparisonFinish': 'embeddedBaselineFinish',
        'startVarianceDays': 'startVarianceDays', 'finishVarianceDays': 'finishVarianceDays',
        'startVarianceWorkingDays': 'startVarianceWorkingDays', 'finishVarianceWorkingDays': 'finishVarianceWorkingDays',
    },
    'previous': {
        'comparisonStart': 'previousStart', 'comparisonFinish': 'previousFinish',
        'startVarianceDays': 'startMovementDays', 'finishVarianceDays': 'finishMovementDays',
        'startVarianceWorkingDays': 'startMovementWorkingDays', 'finishVarianceWorkingDays': 'finishMovementWorkingDays',
    },
}
COMPARISON_BASES = tuple(_BASIS_FIELDS.keys())
_BASIS_LABELS = {
    'approvedBaseline': 'Current vs Approved Baseline',
    'embeddedBaseline': 'Current vs Embedded P6 Baseline',
    'previous': 'Current vs Previous Update',
}


def available_comparison_bases(baseline_designated: bool, previous_available: bool) -> List[Dict[str, Any]]:
    """Only legitimately available bases are ever offered — an unavailable
    one says exactly why, never silently disappears or is inferred from
    column names/dates (e.g. no UNDO/non-UNDO scenario comparison is
    offered here because ScheduleIQ has no such stored, registered
    scenario concept anywhere in the codebase)."""
    return [
        {'key': 'approvedBaseline', 'label': _BASIS_LABELS['approvedBaseline'], 'available': baseline_designated,
         'reason': None if baseline_designated else 'No Approved/Revised Baseline version is designated for this project.'},
        {'key': 'embeddedBaseline', 'label': _BASIS_LABELS['embeddedBaseline'], 'available': True, 'reason': None},
        {'key': 'previous', 'label': _BASIS_LABELS['previous'], 'available': previous_available,
         'reason': None if previous_available else 'No previous schedule version resolves for this update.'},
    ]


def resolve_basis(row: dict, basis: str) -> Dict[str, Any]:
    """One row's {comparisonStart, comparisonFinish, startVarianceDays,
    finishVarianceDays, *WorkingDays} for the chosen basis — a pure
    dict-key rename, never a recalculation."""
    f = _BASIS_FIELDS[basis]
    return {k: row.get(v) for k, v in f.items()}


# ── Direction — from the actual sign of an already-performed date
# subtraction (current − comparison), never inferred any other way ────────

def classify_direction(variance_days: Optional[float]) -> str:
    if variance_days is None:
        return 'UNAVAILABLE'
    if variance_days > 0:
        return 'UNFAVORABLE'
    if variance_days < 0:
        return 'FAVORABLE'
    return 'NO_MOVEMENT'


_DIRECTION_LABEL = {
    'FAVORABLE': 'Favorable', 'UNFAVORABLE': 'Unfavorable', 'NO_MOVEMENT': 'No Movement', 'UNAVAILABLE': 'Unavailable',
}


def direction_interpretation(variance_days: Optional[float], unit: str = 'calendar') -> str:
    """Scheduler-language sentence, grounded in the actual signed value —
    never a bare 'negative=bad' assumption."""
    direction = classify_direction(variance_days)
    if direction == 'UNAVAILABLE':
        return 'Unavailable — no comparison date for this activity under the selected basis.'
    if direction == 'NO_MOVEMENT':
        return 'No movement — forecast finish matches the comparison basis exactly.'
    days = abs(variance_days)
    noun = 'working day' if unit == 'working' else 'calendar day'
    if direction == 'UNFAVORABLE':
        return f'Unfavorable — forecast finish is {days:g} {noun}{"s" if days != 1 else ""} later than the comparison basis.'
    return f'Favorable — forecast finish is {days:g} {noun}{"s" if days != 1 else ""} earlier than the comparison basis.'


# ── Exposure classification — transparent rule table, never a weighted
# score. Every tier is explainable directly from displayed fields. ────────

def classify_exposure(
    direction: str, current_total_float: Optional[float], driving: bool, reaches_contractual_milestone: bool,
) -> str:
    if direction in ('FAVORABLE', 'NO_MOVEMENT'):
        return 'FAVORABLE'
    if direction == 'UNAVAILABLE':
        return 'UNAVAILABLE'
    # UNFAVORABLE from here down.
    negative_float = current_total_float is not None and current_total_float < 0
    low_or_zero_float = current_total_float is not None and current_total_float <= NEAR_CRITICAL_FLOAT_THRESHOLD
    if negative_float and (driving or reaches_contractual_milestone):
        return 'HIGH_EXPOSURE'
    if low_or_zero_float or reaches_contractual_milestone:
        return 'WARNING'
    return 'MONITOR'


_EXPOSURE_RANK = {'HIGH_EXPOSURE': 3, 'WARNING': 2, 'MONITOR': 1, 'FAVORABLE': 0, 'UNAVAILABLE': -1}


# ── Per-row augmentation — combines resolve_basis + classify_direction +
# classify_exposure into one record per activity, for a chosen basis ──────

def build_variance_rows(
    rows: List[dict], basis: str, contractual_milestone_activity_ids: Optional[Set[str]] = None,
    reach_map: Optional[Dict[str, Dict[str, bool]]] = None,
) -> List[Dict[str, Any]]:
    contractual_ids = contractual_milestone_activity_ids or set()
    reach_map = reach_map or {}
    out = []
    for r in rows:
        b = resolve_basis(r, basis)
        direction = classify_direction(b['finishVarianceDays'])
        reached = reach_map.get(r['activityId']) or {}
        reaches_contractual = bool(set(reached.keys()) & contractual_ids)
        exposure = classify_exposure(direction, r.get('currentTotalFloat'), bool(r.get('driving')), reaches_contractual)
        out.append({
            'activityId': r['activityId'], 'activityName': r.get('activityName'),
            'wbs': r.get('wbs'), 'area': r.get('area'), 'discipline': r.get('discipline'),
            'contractor': r.get('contractor'), 'system': r.get('system'),
            'activityStatus': r.get('activityStatus'), 'finished': r.get('finished'),
            'isMilestone': r.get('isMilestone'),
            'comparisonStart': b['comparisonStart'], 'currentStart': r.get('currentStart'), 'startVarianceDays': b['startVarianceDays'],
            'comparisonFinish': b['comparisonFinish'], 'currentFinish': r.get('currentFinish'), 'finishVarianceDays': b['finishVarianceDays'],
            'finishVarianceWorkingDays': b['finishVarianceWorkingDays'],
            'currentTotalFloat': r.get('currentTotalFloat'), 'negativeFloat': r.get('negativeFloat'),
            'nearCritical': r.get('nearCritical'), 'criticalActionable': r.get('criticalActionable'),
            'driving': r.get('driving'),
            'direction': direction, 'directionLabel': _DIRECTION_LABEL[direction],
            'reachesContractualMilestone': reaches_contractual,
            'assessment': exposure,
        })
    return out


# ── Ranking — Top Schedule Variance Exposure. Primary key is the exposure
# TIER (never raw variance magnitude alone), exactly matching the
# requirement's own example: a 30-day-late, +100-float activity must not
# outrank a 10-day-late, -10-float activity driving a key milestone —
# tiering already guarantees that (HIGH_EXPOSURE always ranks above
# MONITOR regardless of day counts). Within a tier: most negative float
# first, then larger adverse finish variance as a further tie-break. ──────

def rank_variance_exposure(variance_rows: List[dict], top_n: Optional[int] = None) -> List[Dict[str, Any]]:
    candidates = [r for r in variance_rows if r['assessment'] not in ('FAVORABLE', 'UNAVAILABLE')]
    candidates.sort(key=lambda r: (
        -_EXPOSURE_RANK[r['assessment']],
        r['currentTotalFloat'] if r['currentTotalFloat'] is not None else float('inf'),
        -(r['finishVarianceDays'] if r['finishVarianceDays'] is not None else float('-inf')),
    ))
    ranked = [{'rank': i + 1, **r} for i, r in enumerate(candidates)]
    return ranked if top_n is None else ranked[:top_n]


# ── Area/WBS aggregation — same "never sum Total Float, count/min/median
# only" discipline as float_intelligence.compute_float_heatmap ───────────

def _median(values: List[float]) -> Optional[float]:
    return round(statistics.median(values), 2) if values else None


def aggregate_variance_by_group(variance_rows: List[dict], group_by: str = 'area') -> Dict[str, Any]:
    groups: Dict[str, List[dict]] = {}
    for r in variance_rows:
        key = r.get(group_by) or 'Unassigned'
        groups.setdefault(key, []).append(r)

    cells = []
    for key, grows in groups.items():
        finish_vars = [r['finishVarianceDays'] for r in grows if r['finishVarianceDays'] is not None]
        tfs = [r['currentTotalFloat'] for r in grows if r['currentTotalFloat'] is not None]
        cells.append({
            'group': key,
            'activityCount': len(grows),
            'unfavorableCount': sum(1 for r in grows if r['direction'] == 'UNFAVORABLE'),
            'favorableCount': sum(1 for r in grows if r['direction'] == 'FAVORABLE'),
            'negativeFloatCount': sum(1 for r in grows if r['negativeFloat']),
            'zeroFloatCount': sum(1 for r in grows if r['currentTotalFloat'] == 0),
            'nearCriticalCount': sum(1 for r in grows if r['nearCritical']),
            'drivingCount': sum(1 for r in grows if r['driving']),
            'exposedMilestoneCount': sum(1 for r in grows if r['isMilestone'] and r['assessment'] in ('WARNING', 'HIGH_EXPOSURE')),
            'worstFinishVarianceDays': max(finish_vars) if finish_vars else None,  # most adverse (largest positive)
            'medianFinishVarianceDays': _median(finish_vars),
            'worstTotalFloat': min(tfs) if tfs else None,  # never summed
        })
    cells.sort(key=lambda c: (c['worstFinishVarianceDays'] is None, -(c['worstFinishVarianceDays'] or float('-inf'))))
    return {'groupBy': group_by, 'cells': cells}


# ── Milestone Variance — schedule comparison variance (this module) kept
# strictly separate from Contract Variance (contractual_milestones.py's
# own, already-computed result — reused verbatim, never recomputed) ──────

def build_milestone_variance(
    variance_rows: List[dict], contractual_tracker_by_activity_id: Optional[Dict[str, dict]] = None,
) -> List[Dict[str, Any]]:
    contractual_by_id = contractual_tracker_by_activity_id or {}
    out = []
    for r in variance_rows:
        if not r['isMilestone']:
            continue
        contract = contractual_by_id.get(r['activityId'])
        out.append({
            'activityId': r['activityId'], 'activityName': r['activityName'],
            'currentFinish': r['currentFinish'], 'comparisonFinish': r['comparisonFinish'],
            'finishVarianceDays': r['finishVarianceDays'], 'direction': r['direction'], 'directionLabel': r['directionLabel'],
            'currentTotalFloat': r['currentTotalFloat'], 'driving': r['driving'],
            'contractRequiredDate': contract.get('contractRequiredDate') if contract else None,
            'contractVarianceDays': contract.get('varianceCalendarDays') if contract else None,
            'contractStatus': contract.get('status') if contract else None,
            'assessment': r['assessment'],
        })
    out.sort(key=lambda m: (m['currentFinish'] is None, m['currentFinish'] or ''))
    return out


# ── Project completion assessment ──────────────────────────────────────────
#
# P6 has no standard field marking "this activity is the project's
# completion milestone" — it is a human/business convention, if it exists
# at all. ScheduleIQ therefore uses a TWO-TIER rule, same precedence
# discipline as "Approved Baseline" elsewhere in this codebase (always
# prefer a human-registered designation over an inferred one):
#
#   1. REGISTERED — a MilestoneDefinition registered with
#      milestone_category='CONTRACTUAL_COMPLETION' for this project,
#      matched to its activity in the current version. Authoritative: a
#      deliberate human designation, not an inference.
#   2. LATEST_FINISH_HEURISTIC — when nothing is registered, the milestone
#      activity with the latest current Finish among this version's own
#      milestones. This is NOT assumed to be the true project-completion
#      milestone — real schedules can and do carry a later-finishing
#      ancillary/utility-interconnection milestone with large float well
#      past the activity that actually represents project completion
#      (confirmed empirically: a real acceptance dataset surfaced a
#      "Power Delivery" utility milestone with 676d of float ranking above
#      an activity literally named "... Project Complete"). The result is
#      labeled "Latest Project Forecast Milestone" everywhere it is shown,
#      never "Project Completion", whenever selectionBasis is this tier.
#
# None when no milestone exists at all — never substituted with a
# non-milestone activity, in either tier. ──────────────────────────────────

def identify_completion_milestone(
    variance_rows: List[dict], registered_completion_activity_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    milestones = [r for r in variance_rows if r['isMilestone'] and r['currentFinish']]
    if not milestones:
        return None
    if registered_completion_activity_id:
        registered = next((r for r in milestones if r['activityId'] == registered_completion_activity_id), None)
        if registered is not None:
            return {**registered, 'selectionBasis': 'REGISTERED'}
    latest = max(milestones, key=lambda r: r['currentFinish'])
    return {**latest, 'selectionBasis': 'LATEST_FINISH_HEURISTIC'}


def completion_interpretation(milestone: Optional[dict], unit: str = 'calendar') -> str:
    if milestone is None:
        return 'No milestone activity with a usable current finish date was found in this schedule version — project completion cannot be automatically identified.'
    is_registered = milestone.get('selectionBasis') == 'REGISTERED'
    noun_subject = 'The registered project-completion milestone' if is_registered else 'The latest-finishing milestone in this schedule (not registered as the project-completion milestone)'
    var = milestone['finishVarianceDays']
    direction = milestone['direction']
    if direction == 'UNAVAILABLE':
        base = f'{noun_subject}: finish variance against the selected comparison basis is unavailable.'
    elif direction == 'NO_MOVEMENT':
        base = f'{noun_subject} matches the selected comparison basis exactly — no finish movement.'
    else:
        days = abs(var)
        noun = 'working day' if unit == 'working' else 'calendar day'
        word = 'later' if direction == 'UNFAVORABLE' else 'earlier'
        base = f'{noun_subject} is {days:g} {noun}{"s" if days != 1 else ""} {word} than the selected comparison basis.'
    tf = milestone['currentTotalFloat']
    if tf is None:
        float_note = ' Total Float at this milestone is unavailable.'
    elif tf < 0:
        float_note = f' This milestone has {tf:g}d of Total Float — already behind the controlling path.'
    elif tf == 0:
        float_note = ' This milestone has 0 days Total Float, indicating no remaining schedule flexibility.'
    else:
        float_note = f' This milestone retains {tf:g}d of Total Float.'
    note = '' if is_registered else ' No milestone is registered in ScheduleIQ as the Contractual Completion milestone for this project — register one to replace this heuristic selection with an authoritative one.'
    return base + float_note + note


# ── Update Movement — Current vs Previous only, kept out of the chosen
# comparison basis entirely so it's never mislabeled as baseline variance.
# Every field here is already computed per-row by activity_analysis.py /
# update_intelligence.py — this only filters/groups them. ─────────────────

def build_update_movement_summary(rows: List[dict]) -> Dict[str, Any]:
    return {
        'finishSlipped': [r['activityId'] for r in rows if r.get('finishMovementDays') is not None and r['finishMovementDays'] > 0],
        'finishImproved': [r['activityId'] for r in rows if r.get('finishMovementDays') is not None and r['finishMovementDays'] < 0],
        'startSlipped': [r['activityId'] for r in rows if r.get('startMovementDays') is not None and r['startMovementDays'] > 0],
        'startImproved': [r['activityId'] for r in rows if r.get('startMovementDays') is not None and r['startMovementDays'] < 0],
        'newlyNegativeFloat': [r['activityId'] for r in rows if r.get('newlyNegativeFloat')],
        'recoveredFromNegativeFloat': [r['activityId'] for r in rows if r.get('recoveredFromNegativeFloat')],
        'newlyCritical': [r['activityId'] for r in rows if r.get('newlyCritical')],
        'noLongerCritical': [r['activityId'] for r in rows if r.get('noLongerCritical')],
        'logicChanged': [r['activityId'] for r in rows if r.get('logicChanged')],
    }


# ── Scheduler narrative — deterministic, template-based sentences built
# ONLY from already-computed fields. Works fully without AI; an AI
# provider (when configured) may rephrase these facts, but the facts
# themselves — and the page's basic function — never depend on it. ────────

def build_deterministic_narrative(
    completion_milestone: Optional[dict], variance_rows: List[dict], area_summary: Dict[str, Any], basis_label: str,
) -> List[str]:
    sentences: List[str] = []
    sentences.append(f'Comparison basis: {basis_label}.')
    sentences.append(completion_interpretation(completion_milestone))

    unfavorable_exposed = [r for r in variance_rows if r['assessment'] in ('WARNING', 'HIGH_EXPOSURE')]
    if not unfavorable_exposed:
        sentences.append('No activities show unfavorable finish movement combined with low/negative float or milestone exposure under this basis.')
    else:
        worst_groups = sorted(
            (c for c in area_summary.get('cells', []) if c['unfavorableCount'] > 0),
            key=lambda c: (c['worstFinishVarianceDays'] is None, -(c['worstFinishVarianceDays'] or float('-inf'))),
        )[:3]
        area_phrase = ', '.join(c['group'] for c in worst_groups) if worst_groups else 'no single concentrated area'
        n = len(unfavorable_exposed)
        sentences.append(
            f'{n} activit{"y shows" if n == 1 else "ies show"} unfavorable finish movement with reduced schedule flexibility '
            f'(low/zero/negative float, driving-path status, or milestone exposure). Exposure is concentrated in: {area_phrase}.'
        )
    high = sum(1 for r in variance_rows if r['assessment'] == 'HIGH_EXPOSURE')
    if high:
        sentences.append(f'{high} activit{"y is" if high == 1 else "ies are"} classified HIGH EXPOSURE — unfavorable movement combined with negative Total Float on a driving path or reaching a contractual milestone.')
    return sentences
