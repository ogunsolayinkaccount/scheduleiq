"""
Project Issue Tracking — schedule-linkage exposure engine.

Computes, fresh at request time, what the LIVE schedule says about an
Issue's linked activities. Never stores exposure, never alters it, and
NEVER feeds back into Issue.severity (a human field) or into any P6
calculation. Same discipline as risk_register.py: this module recomputes
nothing that another engine already owns —

  - per-activity status/float/criticality: read directly off the
    activity's own imported fields (same convention activity_analysis.py,
    update_intelligence.py, and baseline_progress.py each already use
    independently with the identical NEAR_CRITICAL_FLOAT_THRESHOLD = 10.0
    rule — there is no single shared helper for this in the codebase to
    import, so this follows the same established inline rule rather than
    inventing a different one).
  - milestone reachability: driving_chain.compute_milestone_reachability_map
    (batch, already proven by risk_register.py) — never a new BFS.
  - activity lookup / "not found" shape: driving_chain.build_adjacency's
    by_id map and driving_chain's own
    {'available': False, 'reason': f'Activity {id} not found in this
    schedule version.'} convention, copied verbatim.

Pure functions, no DB dependency — same pattern as risk_register.py /
schedule_identity.py / baseline_progress.py.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from . import driving_chain

# Matches activity_analysis.NEAR_CRITICAL_FLOAT_THRESHOLD /
# update_intelligence.NEAR_CRITICAL_FLOAT_DAYS — the same deterministic
# 10-day rule every other engine in this codebase already applies
# independently to a single activity's own Total Float.
NEAR_CRITICAL_FLOAT_THRESHOLD = 10.0


def _float_condition(total_float: Optional[float]) -> str:
    """NEGATIVE / ZERO / NEAR_CRITICAL / NORMAL / UNAVAILABLE — a flat,
    deterministic classification of ONE activity's own Total Float. Never
    a severity judgment; that stays the human-set Issue.severity field."""
    if total_float is None:
        return 'UNAVAILABLE'
    if total_float < 0:
        return 'NEGATIVE'
    if total_float == 0:
        return 'ZERO'
    if total_float <= NEAR_CRITICAL_FLOAT_THRESHOLD:
        return 'NEAR_CRITICAL'
    return 'NORMAL'


def compute_activity_exposure(
    activity_id: str,
    by_id: Dict[str, dict],
    reach_map: Dict[str, Dict[str, bool]],
    contractual_milestone_activity_ids: Set[str],
) -> Dict[str, Any]:
    """One linked activity's current, live exposure — or the standard
    "not found" shape (copied from driving_chain.py) when the id no longer
    resolves against the CURRENT version (an issue is never required to
    update its linked ids across re-imports; this is reported honestly,
    never silently dropped or fabricated)."""
    act = by_id.get(activity_id)
    if not act:
        return {
            'activityId': activity_id, 'available': False,
            'reason': f'Activity {activity_id} not found in selected schedule version.',
        }

    total_float = act.get('totalFloat')
    reached = reach_map.get(activity_id) or {}
    reached_contractual = sorted(set(reached.keys()) & contractual_milestone_activity_ids)

    return {
        'activityId': activity_id, 'available': True,
        'activityName': act.get('name') or '',
        'wbs': act.get('wbs') or '', 'area': act.get('area') or '', 'discipline': act.get('discipline') or '',
        'status': act.get('status') or None,
        'pctComplete': act.get('pctComplete'),
        'currentStart': act.get('start') or act.get('earlyStart') or act.get('remainStart') or act.get('bStart'),
        'currentFinish': act.get('finish') or act.get('earlyFinish') or act.get('remainFinish') or act.get('bFinish'),
        'totalFloat': total_float,
        'floatCondition': _float_condition(total_float),
        'isCritical': bool(act.get('isCritical')),
        'onDrivingPath': bool(act.get('onLongestPath')),
        'isMilestone': bool(act.get('isMilestone')),
        'reachableMilestoneCount': len(reached),
        'reachesContractualMilestoneActivityIds': reached_contractual,
    }


def assemble_exposure(
    linked_activity_ids: List[str],
    by_id: Dict[str, dict],
    reach_map: Dict[str, Dict[str, bool]],
    contractual_milestone_activity_ids: Set[str],
    current_data_date,
) -> Dict[str, Any]:
    """Same result shape as compute_issue_schedule_exposure, but takes
    ALREADY-BUILT adjacency/reach-map maps — the Issue Register LIST view
    builds those ONCE per request (driving_chain.build_adjacency and
    compute_milestone_reachability_map are both O(n) over the whole
    schedule) and calls this per issue, rather than paying that cost once
    per issue in the register."""
    if not linked_activity_ids:
        return {
            'hasLinkedActivities': False, 'currentDataDate': current_data_date.isoformat() if current_data_date else None,
            'activities': [],
        }
    activities = [
        compute_activity_exposure(aid, by_id, reach_map, contractual_milestone_activity_ids)
        for aid in linked_activity_ids
    ]
    return {
        'hasLinkedActivities': True,
        'currentDataDate': current_data_date.isoformat() if current_data_date else None,
        'activities': activities,
        # Deterministic rollups — never a severity label, just facts an
        # Issue Register UI can show alongside the human-set severity.
        'anyNegativeFloat': any(a.get('floatCondition') == 'NEGATIVE' for a in activities if a['available']),
        'anyOnDrivingPath': any(a.get('onDrivingPath') for a in activities if a['available']),
        'anyReachesContractualMilestone': any(a.get('reachesContractualMilestoneActivityIds') for a in activities if a['available']),
        'anyNotFound': any(not a['available'] for a in activities),
    }


def compute_issue_schedule_exposure(
    linked_activity_ids: List[str],
    current_activities: List[dict],
    current_data_date,
    contractual_milestone_activity_ids: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Convenience wrapper for a SINGLE issue (the detail endpoint): builds
    the adjacency/reach-map fresh, then delegates to assemble_exposure.
    Callers resolve the version themselves via the app's existing
    version-resolution helpers (views._resolve_latest_version et al.) —
    this function never touches the database and never decides which
    version is "current". For a whole register (many issues), call
    driving_chain.build_adjacency/compute_milestone_reachability_map ONCE
    and use assemble_exposure directly instead of this wrapper."""
    if not linked_activity_ids:
        return {
            'hasLinkedActivities': False, 'currentDataDate': current_data_date.isoformat() if current_data_date else None,
            'activities': [],
        }
    by_id, _pred_of, _succ_of = driving_chain.build_adjacency(current_activities)
    reach_map = driving_chain.compute_milestone_reachability_map(current_activities)
    return assemble_exposure(
        linked_activity_ids, by_id, reach_map, contractual_milestone_activity_ids or set(), current_data_date,
    )


def is_issue_overdue(issue_status: str, target_resolution_date, today) -> bool:
    """A single, deterministic rule reused by both the API summary counts
    and the Field Dashboard: a target date in the past on a still-ACTIVE
    issue (see models.ISSUE_ACTIVE_STATUSES) is overdue. Resolved/Closed
    issues are never "overdue" regardless of their target date — that
    question stops applying once the issue is no longer active."""
    from .models import ISSUE_ACTIVE_STATUSES
    if issue_status not in ISSUE_ACTIVE_STATUSES:
        return False
    if not target_resolution_date:
        return False
    return target_resolution_date < today


# Deterministic dashboard-priority ranking — NEVER AI-generated, every
# input is a fact already computed above or stored on the Issue row.
_SEVERITY_RANK = {'CRITICAL': 3, 'HIGH': 2, 'MEDIUM': 1, 'LOW': 0}


def priority_rank(issue_summary: Dict[str, Any]) -> tuple:
    """Sort key (higher = shown first) for the Field Dashboard's "highest-
    priority active issues" list. Factors, in order: overdue action,
    contractual-milestone exposure, linked-schedule exposure (negative
    float or driving-path), then severity, then how long the issue has
    been open. Pure function of fields already on the summary dict built
    by _serialize_project_issue in views.py — no recomputation here."""
    return (
        1 if issue_summary.get('overdue') else 0,
        1 if issue_summary.get('exposure', {}).get('anyReachesContractualMilestone') else 0,
        1 if (issue_summary.get('exposure', {}).get('anyNegativeFloat') or issue_summary.get('exposure', {}).get('anyOnDrivingPath')) else 0,
        _SEVERITY_RANK.get(issue_summary.get('severity'), -1),
        issue_summary.get('createdAt') or '',
    )
