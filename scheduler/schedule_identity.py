"""
Schedule Identity Evaluator — SCHEDULEIQ Schedule Identity & Version UX
Hardening phase.

Governing principle: P6 PROJECT ID IS EVIDENCE — NOT IDENTITY. A raw P6
`proj_id` match or mismatch is one signal among several, never definitive
on its own (real P6 re-exports of the SAME project can carry a different
internal `proj_id`, and two DIFFERENT projects could coincidentally share
project-name conventions). This module is the single authoritative place
that combines multiple deterministic structural signals — Activity ID
overlap (the strongest), WBS overlap, milestone overlap, P6 Project ID,
and normalized project name — into one explainable classification.

Pure function, no DB dependency (same pattern as update_intelligence.py /
schedule_comparison.py / baseline_progress.py): everything here operates
on plain activity dicts and primitive project-identity fields, so it is
independently unit-testable and reusable anywhere identity needs to be
assessed (currently: import_preview).

No AI/LLM judgment is used anywhere in this module — every classification
traces to one of the documented, fixed thresholds below.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

ENGINE_VERSION = '1.0.0'

SAME_PROJECT = 'SAME_PROJECT'
LIKELY_SAME_PROJECT = 'LIKELY_SAME_PROJECT'
UNCERTAIN = 'UNCERTAIN'
LIKELY_DIFFERENT_PROJECT = 'LIKELY_DIFFERENT_PROJECT'

# ── Deterministic thresholds (documented here; see the final report for the
# rationale behind each value — derived from inspecting the real AWP2025
# same-project pair, which showed ~100% Activity ID overlap despite a
# changed P6 Project ID, and from the synthetic same-proj_id/different-
# structure and different-proj_id/same-structure safety cases). ──────────
MIN_POPULATION_FOR_ASSESSMENT = 3       # below this on either side: insufficient data
STRONG_OVERLAP_THRESHOLD = 0.90         # near-total Activity ID overlap
MODERATE_OVERLAP_THRESHOLD = 0.60       # substantial structural continuity
LOW_OVERLAP_THRESHOLD = 0.15            # essentially unrelated activity populations

# Population-size ratio (smaller activity count / larger activity count)
# below which two SAME_PROJECT/LIKELY_SAME_PROJECT schedules are flagged as
# scope-divergent rather than treated as a straightforward chronological
# update — derived from the real Barn case this phase exists to solve: a
# 1,769-activity scoped "-SE" export against its 6,114-activity master
# schedule (ratio 0.29, high one-directional overlap) versus an ordinary
# forward update like Sep-23 -> Sep-25 (2,127 -> 2,282 activities, ratio
# 0.93). Generic — never compares against a hard-coded activity count.
SCOPE_POPULATION_RATIO_THRESHOLD = 0.60

NOT_APPLICABLE = 'NOT_APPLICABLE'
COMPATIBLE = 'COMPATIBLE'
SCOPE_DIVERGENT = 'SCOPE_DIVERGENT'


def _act_id(a: dict) -> str:
    return str(a.get('code') or a.get('id') or '')


def _normalize_name(name: Optional[str]) -> str:
    """Lowercase, strip surrounding whitespace, collapse internal whitespace
    and punctuation differences — so "AWP2025-BL-1" and "awp2025 bl 1"
    compare equal, but genuinely different names still don't."""
    if not name:
        return ''
    s = name.strip().lower()
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _id_set(activities: List[dict]) -> Set[str]:
    return {_act_id(a) for a in activities if _act_id(a)}


def _wbs_set(activities: List[dict]) -> Set[str]:
    return {str(a['wbs']).strip() for a in activities if a.get('wbs') and str(a['wbs']).strip()}


def _milestone_id_set(activities: List[dict]) -> Set[str]:
    return {_act_id(a) for a in activities if a.get('isMilestone') and _act_id(a)}


def _scope_divergence_signal(reference_count: int, uploaded_count: int) -> Dict[str, Any]:
    """Population-size divergence between the two activity sets — the
    signal that separates 'same underlying project' from 'same version
    lineage'. A scoped subset/superset export (e.g. a discipline-filtered
    schedule pulled from a larger master schedule) can share the same P6
    Project ID and near-total one-directional Activity ID overlap with its
    master schedule while having a fraction of its activity count — that is
    NOT the same signature as a normal week-to-week update, which moves the
    population by single-digit percentages either way."""
    if reference_count <= 0 or uploaded_count <= 0:
        return {'available': False, 'referenceCount': reference_count, 'uploadedCount': uploaded_count,
                'populationRatio': None, 'diverges': False}
    smaller, larger = min(reference_count, uploaded_count), max(reference_count, uploaded_count)
    ratio = round(smaller / larger, 4)
    return {
        'available': True,
        'referenceCount': reference_count,
        'uploadedCount': uploaded_count,
        'populationRatio': ratio,
        'diverges': ratio < SCOPE_POPULATION_RATIO_THRESHOLD,
    }


def _overlap_signal(reference_set: Set[str], uploaded_set: Set[str]) -> Dict[str, Any]:
    """O(n + m) — builds two sets once, intersects once. Never a nested
    activity-to-activity scan, so this stays linear at any scale (2,500
    through 20,000+ activities)."""
    if not reference_set and not uploaded_set:
        return {'available': False, 'referenceCount': 0, 'uploadedCount': 0, 'overlapCount': 0,
                'referenceOverlapRatio': None, 'uploadedOverlapRatio': None, 'jaccard': None}
    intersection = reference_set & uploaded_set
    union = reference_set | uploaded_set
    return {
        'available': True,
        'referenceCount': len(reference_set),
        'uploadedCount': len(uploaded_set),
        'overlapCount': len(intersection),
        'referenceOverlapRatio': (len(intersection) / len(reference_set)) if reference_set else None,
        'uploadedOverlapRatio': (len(intersection) / len(uploaded_set)) if uploaded_set else None,
        'jaccard': (len(intersection) / len(union)) if union else None,
    }


def evaluate_schedule_identity(
    reference_activities: List[dict],
    reference_p6_id: Optional[str],
    reference_p6_name: Optional[str],
    uploaded_activities: List[dict],
    uploaded_p6_id: Optional[str],
    uploaded_p6_name: Optional[str],
    reference_version_label: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compares an uploaded schedule against an existing project's reference
    version (see views._identity_reference_version — normally the CURRENT
    version, falling back to the baseline when that's all that exists) and
    returns a structured, explainable identity assessment. Never returns a
    bare boolean — every classification carries the signals that produced
    it, so a scheduler can see exactly why ScheduleIQ reached its answer.
    """
    activity_signal = _overlap_signal(_id_set(reference_activities), _id_set(uploaded_activities))
    wbs_signal = _overlap_signal(_wbs_set(reference_activities), _wbs_set(uploaded_activities))
    milestone_signal = _overlap_signal(_milestone_id_set(reference_activities), _milestone_id_set(uploaded_activities))

    p6_id_match: Optional[bool] = None
    if reference_p6_id and uploaded_p6_id:
        p6_id_match = (reference_p6_id == uploaded_p6_id)

    name_match: Optional[bool] = None
    ref_norm, up_norm = _normalize_name(reference_p6_name), _normalize_name(uploaded_p6_name)
    if ref_norm and up_norm:
        name_match = (ref_norm == up_norm)

    ref_count = activity_signal['referenceCount']
    up_count = activity_signal['uploadedCount']
    ratio = activity_signal['referenceOverlapRatio']

    warnings: List[str] = []
    explanation_parts: List[str] = []

    # ── Rule 0: insufficient data ───────────────────────────────────────
    if ref_count < MIN_POPULATION_FOR_ASSESSMENT or up_count < MIN_POPULATION_FOR_ASSESSMENT:
        classification = UNCERTAIN
        confirmation_required = True
        explanation_parts.append(
            f'Insufficient activity data to assess schedule identity confidently '
            f'(reference has {ref_count} activities, uploaded has {up_count} — '
            f'at least {MIN_POPULATION_FOR_ASSESSMENT} are needed on each side).'
        )
    # ── Rule 1: near-total Activity ID overlap ──────────────────────────
    elif ratio is not None and ratio >= STRONG_OVERLAP_THRESHOLD:
        if p6_id_match is False:
            classification = LIKELY_SAME_PROJECT
            explanation_parts.append(
                f'The internal Primavera Project ID changed between schedule exports '
                f'({reference_p6_id} → {uploaded_p6_id}), but Activity ID overlap is '
                f'{ratio * 100:.1f}% — strong structural evidence this is another version '
                f'of the same underlying schedule.'
            )
        else:
            classification = SAME_PROJECT
            explanation_parts.append(
                f'Activity ID overlap is {ratio * 100:.1f}% against the reference version — '
                f'strong evidence this is another version of the same underlying schedule.'
            )
        confirmation_required = False
    # ── Rule 2: moderate-strong overlap — legitimate schedule evolution ─
    elif ratio is not None and ratio >= MODERATE_OVERLAP_THRESHOLD:
        classification = LIKELY_SAME_PROJECT
        confirmation_required = False
        explanation_parts.append(
            f'Activity ID overlap is {ratio * 100:.1f}% against the reference version — '
            f'substantial structural continuity consistent with a normal schedule update '
            f'(new/removed activities are expected and do not by themselves indicate a '
            f'different project).'
        )
    # ── Rule 3: very low overlap ─────────────────────────────────────────
    elif ratio is not None and ratio < LOW_OVERLAP_THRESHOLD:
        if p6_id_match is True and name_match is True:
            # Two identity signals agree but structure is almost entirely
            # different — genuinely ambiguous (e.g. a full schedule rebuild
            # under the same P6 project). Never silently assume SAME_PROJECT
            # merely because proj_id matched — see item 23 of the directive.
            classification = UNCERTAIN
            confirmation_required = True
            explanation_parts.append(
                f'P6 Project ID and project name both match the existing project, but '
                f'Activity ID overlap is only {ratio * 100:.1f}% — the schedule structure '
                f'has changed too much to confirm identity from matching identifiers alone.'
            )
        else:
            classification = LIKELY_DIFFERENT_PROJECT
            confirmation_required = True
            explanation_parts.append(
                f'Activity ID overlap is only {ratio * 100:.1f}% against the reference '
                f'version, and no other identity signal strongly supports continuity — '
                f'this schedule does not structurally match the selected project\'s '
                f'existing versions.'
            )
    # ── Rule 4: mixed/ambiguous middle band ─────────────────────────────
    else:
        classification = UNCERTAIN
        confirmation_required = True
        if ratio is None:
            explanation_parts.append('Activity ID overlap could not be computed.')
        else:
            explanation_parts.append(
                f'Activity ID overlap is {ratio * 100:.1f}% — evidence is mixed and does not '
                f'clearly support either continuity or a different project.'
            )

    if p6_id_match is False:
        warnings.append(f'P6 Project ID changed: {reference_p6_id} → {uploaded_p6_id}')
    if name_match is False:
        warnings.append(f'Project name differs: "{reference_p6_name}" vs "{uploaded_p6_name}"')

    # ── Scope divergence: a SEPARATE question from project identity ──────
    # "Same underlying project" (classification above) and "safe to treat
    # as the next chronological version" are not the same claim — a scoped
    # subset/superset export can legitimately share a project's identity
    # while representing a different schedule scope (see module docstring
    # governing principle). This never changes `classification` or
    # `confirmationRequired` above; it is an additional, independent signal
    # a caller must check before recommending "Add as Schedule Version".
    scope_divergence = _scope_divergence_signal(ref_count, up_count)
    if classification in (SAME_PROJECT, LIKELY_SAME_PROJECT):
        version_lineage_compatibility = SCOPE_DIVERGENT if scope_divergence['diverges'] else COMPATIBLE
    else:
        version_lineage_compatibility = NOT_APPLICABLE
    if version_lineage_compatibility == SCOPE_DIVERGENT:
        warnings.append(
            f'Scope divergence: population ratio {scope_divergence["populationRatio"] * 100:.1f}% '
            f'({scope_divergence["referenceCount"]} vs {scope_divergence["uploadedCount"]} activities) '
            f'despite matching project identity — this may be a scoped subset/superset of the same '
            f'underlying project rather than the next chronological update.'
        )

    return {
        'engineVersion': ENGINE_VERSION,
        'classification': classification,
        'confirmationRequired': confirmation_required,
        'referenceVersionLabel': reference_version_label,
        # projectIdentity mirrors classification/confirmationRequired in one
        # object for callers that want the "same underlying project"
        # question addressed on its own, separate from version-lineage
        # safety below — additive, does not replace the flat fields above.
        'projectIdentity': {
            'classification': classification,
            'confirmationRequired': confirmation_required,
        },
        'versionLineageCompatibility': version_lineage_compatibility,
        'scopeDivergence': scope_divergence,
        'signals': {
            'p6ProjectId': {
                'match': p6_id_match,
                'reference': reference_p6_id or None,
                'uploaded': uploaded_p6_id or None,
            },
            'projectName': {
                'match': name_match,
                'reference': reference_p6_name or None,
                'uploaded': uploaded_p6_name or None,
            },
            'activityIdOverlap': activity_signal,
            'wbsOverlap': wbs_signal,
            'milestoneOverlap': milestone_signal,
        },
        'warnings': warnings,
        'explanation': ' '.join(explanation_parts),
    }
