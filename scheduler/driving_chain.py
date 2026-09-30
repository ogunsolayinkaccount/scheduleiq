"""
Driving Chain / Path Trace — ScheduleIQ Schedule Risk, Recovery & Mitigation
Intelligence phase.

Multi-hop port of frontend/pathTrace.ts's `tracePath`/`pickDrivingRel`
heuristic — the SAME "lowest total float wins" driving-neighbour rule, so
the backend and frontend never disagree about which relationship is
"driving". This is explicitly a relationship trace using each activity's
own imported P6 Total Float, NOT a recalculated CPM — every caller must
label results "Driving Path Trace" / "Critical/Driving Path Trace", never
a new CPM computation (see update_intelligence.py's identical convention).

Two distinct operations, matching the directive's two distinct questions:

  - trace_driving_path(): "what is THE driving chain through this
    activity?" — a single linear path (one predecessor/successor per hop,
    the lowest-float one), for the chain visualization.

  - find_downstream_exposure(): "what does this activity's lateness
    connect to?" — a full bounded reachability walk through ALL successors
    (not just the driving one), so nothing downstream is missed. Each
    reachable milestone is tagged "forecast-impacting" only when it is
    independently already known to be at-risk (compute_milestone_movement's
    own classification) — never inferred from float algebra this module
    doesn't perform, since that would risk claiming a delay float actually
    absorbs.

Both use raw-task_id -> code resolution (schedule_comparison._id_to_code_name)
so they work correctly on real XER imports, not just id==code fixtures —
the same relationship-resolution fix applied to recovery_engine.py.

Complexity: O(n + r) to build the adjacency maps once; each trace is
O(hops) for the driving path (bounded by max_hops) or O(reachable nodes)
for the downstream exposure walk (bounded by max_nodes and a visited set —
iterative, never recursive, so it cannot blow the stack on a large or
even cyclic-by-mistake real-world network).
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from .schedule_comparison import _id_to_code_name

DEFAULT_MAX_HOPS = 50
DEFAULT_MAX_EXPOSURE_NODES = 5000


def _act_id(a: dict) -> str:
    return str(a.get('code') or a.get('id') or '')


def _resolve(raw_id: Optional[str], id_map: Dict[str, Tuple[str, str]]) -> str:
    if not raw_id:
        return ''
    return id_map.get(raw_id, (raw_id, ''))[0] or raw_id


def build_adjacency(activities: List[dict]) -> Tuple[Dict[str, dict], Dict[str, list], Dict[str, list]]:
    """One-time O(n + r) build: (by_id, pred_of, succ_of), every relationship
    endpoint resolved to a real Activity Code."""
    id_map = _id_to_code_name(activities)
    by_id = {_act_id(a): a for a in activities}
    pred_of: Dict[str, list] = {}
    succ_of: Dict[str, list] = {}
    for a in activities:
        aid = _act_id(a)
        pred_of[aid] = [
            {'actId': _resolve(str(p.get('actId') or ''), id_map), 'relType': p.get('relType') or 'FS', 'lagDays': p.get('lagDays') or 0}
            for p in (a.get('predecessors') or [])
        ]
        succ_of[aid] = [
            {'actId': _resolve(str(s.get('actId') or ''), id_map), 'relType': s.get('relType') or 'FS', 'lagDays': s.get('lagDays') or 0}
            for s in (a.get('successors') or [])
        ]
    return by_id, pred_of, succ_of


def _activity_summary(a: dict) -> Dict[str, Any]:
    return {
        'activityId': _act_id(a), 'activityName': a.get('name') or '',
        'wbs': a.get('wbs') or '', 'area': a.get('area') or '', 'discipline': a.get('discipline') or '',
        'currentStart': a.get('start') or a.get('earlyStart') or a.get('remainStart') or a.get('bStart'),
        'currentFinish': a.get('finish') or a.get('earlyFinish') or a.get('remainFinish') or a.get('bFinish'),
        'remainingDuration': a.get('remainDur'), 'totalFloat': a.get('totalFloat'),
        'isCritical': bool(a.get('isCritical')), 'onLongestPath': a.get('onLongestPath'),
        'isMilestone': bool(a.get('isMilestone')), 'pctComplete': a.get('pctComplete'),
    }


def _pick_driving(rels: List[dict], by_id: Dict[str, dict]) -> Optional[Dict[str, Any]]:
    """Same rule as frontend pathTrace.ts::pickDrivingRel — lowest Total
    Float among the available neighbours wins; unknown float sorts last."""
    best = None
    best_float = float('inf')
    for r in rels:
        act = by_id.get(r['actId'])
        if not act:
            continue
        tf = act.get('totalFloat')
        f = float('inf') if tf is None else float(tf)
        if f < best_float:
            best_float = f
            best = {'rel': r, 'activity': act}
    return best


def trace_driving_path(
    activities: List[dict], start_activity_id: str, max_hops: int = DEFAULT_MAX_HOPS,
) -> Dict[str, Any]:
    """Single linear driving-path trace both upstream and downstream from
    start_activity_id — the "Driving/Critical Path Trace" visualization.
    Never a CPM recalculation; every hop is chosen from the schedule's own
    imported relationships and Total Float."""
    by_id, pred_of, succ_of = build_adjacency(activities)
    start = by_id.get(start_activity_id)
    if not start:
        return {'available': False, 'reason': f'Activity {start_activity_id} not found in this schedule version.'}

    def _walk(adjacency, direction_label):
        walk = [{'activity': start, 'rel': None}]
        visited = {start_activity_id}
        current = start
        for _ in range(max_hops):
            rels = adjacency.get(_act_id(current), [])
            pick = _pick_driving(rels, by_id)
            if not pick:
                break
            next_id = _act_id(pick['activity'])
            if next_id in visited:
                break  # defensive cycle guard — should not occur in valid P6 data
            visited.add(next_id)
            walk.append({'activity': pick['activity'], 'rel': pick['rel']})
            current = pick['activity']
        return walk

    upstream_walk = _walk(pred_of, 'predecessors')
    downstream_walk = _walk(succ_of, 'successors')

    def _rel_dict(rel):
        return {'relType': rel['relType'], 'lagDays': rel['lagDays']} if rel else None

    def _serialize_downstream(walk):
        # walk = [start(rel=None), hop1(rel=start->hop1), hop2(rel=hop1->hop2), ...] —
        # already in display order (selected activity first).
        out = []
        for node in walk:
            entry = _activity_summary(node['activity'])
            entry['relationshipFromPrevious'] = _rel_dict(node['rel'])
            out.append(entry)
        return out

    def _serialize_upstream(walk):
        # walk = [start(rel=None), pred1(rel=pred1->start), pred2(rel=pred2->pred1), ...] —
        # reverse for display (farthest predecessor first, selected activity last). After
        # reversal, the relationship connecting displayed node i to node i-1 is the ORIGINAL
        # walk's node at index (len(walk) - i), since that's the node one step closer to
        # `start` in the original (unreversed) walk.
        out = []
        n = len(walk)
        for i, node in enumerate(reversed(walk)):
            entry = _activity_summary(node['activity'])
            entry['relationshipFromPrevious'] = _rel_dict(walk[n - i]['rel']) if i > 0 else None
            out.append(entry)
        return out

    upstream_chain = _serialize_upstream(upstream_walk)      # farthest predecessor first, selected activity last
    downstream_chain = _serialize_downstream(downstream_walk)  # selected activity first, farthest successor last

    return {
        'available': True,
        'methodologyNote': (
            'Driving/Critical Path Trace — based on each activity\'s own imported P6 '
            'Total Float (lowest float wins at each hop), not a ScheduleIQ CPM recalculation.'
        ),
        'selectedActivity': _activity_summary(start),
        'upstreamChain': upstream_chain,     # excludes the selected activity itself... actually includes it as last element
        'downstreamChain': downstream_chain,  # includes the selected activity as first element
        'upstreamTruncated': len(upstream_walk) >= max_hops,
        'downstreamTruncated': len(downstream_walk) >= max_hops,
    }


def find_blocking_predecessors(activities: List[dict], start_activity_id: str, current_data_date=None) -> Dict[str, Any]:
    """Item 9/12 — factual predecessor conditions, never labeled a root
    cause. Looks only at the IMMEDIATE predecessors (the ones that
    structurally constrain the selected activity's start), each annotated
    with the specific, checkable conditions the directive names."""
    by_id, pred_of, _succ_of = build_adjacency(activities)
    start = by_id.get(start_activity_id)
    if not start:
        return {'available': False, 'reason': f'Activity {start_activity_id} not found in this schedule version.'}

    out = []
    for p in pred_of.get(start_activity_id, []):
        pred = by_id.get(p['actId'])
        if not pred:
            continue
        conditions = []
        pct = pred.get('pctComplete') or 0
        if pct < 100:
            conditions.append('UNFINISHED')
        tf = pred.get('totalFloat')
        if tf is not None and tf < 0:
            conditions.append('NEGATIVE_FLOAT')
        if p.get('lagDays'):
            conditions.append('LAG')
        if pred.get('constraintType'):
            conditions.append('CONSTRAINT')
        out.append({
            'activityId': _act_id(pred), 'activityName': pred.get('name') or '',
            'relationshipType': p['relType'], 'lagDays': p['lagDays'],
            'currentFinish': pred.get('finish') or pred.get('earlyFinish') or pred.get('remainFinish'),
            'remainingDuration': pred.get('remainDur'), 'totalFloat': tf,
            'pctComplete': pct, 'conditions': conditions,
        })
    return {
        'available': True,
        'methodologyNote': 'Potential Schedule Constraints — factual predecessor conditions, not an inferred root cause.',
        'blockingPredecessors': out,
    }


def find_downstream_exposure(
    activities: List[dict], start_activity_id: str,
    at_risk_milestone_ids: Optional[set] = None,
    max_nodes: int = DEFAULT_MAX_EXPOSURE_NODES,
) -> Dict[str, Any]:
    """Item 13 — full bounded reachability through ALL successors (not just
    the single driving path), answering "if this activity remains late,
    what does it expose?" Every downstream milestone found is returned as
    "connected"; it is additionally marked "forecastImpacting" only when
    `at_risk_milestone_ids` (from compute_milestone_movement's own
    classification — SLIPPED / NEWLY_NEGATIVE_FLOAT / milestonesAtRisk)
    independently says so — this module never computes float absorption
    itself, so it never claims a downstream delay that available float may
    in fact absorb. Iterative BFS with a visited set: safe on large or
    accidentally-cyclic real-world networks, never recursive."""
    by_id, _pred_of, succ_of = build_adjacency(activities)
    start = by_id.get(start_activity_id)
    if not start:
        return {'available': False, 'reason': f'Activity {start_activity_id} not found in this schedule version.'}

    at_risk_milestone_ids = at_risk_milestone_ids or set()
    visited = {start_activity_id}
    queue = deque([start_activity_id])
    reachable_milestones = []
    reachable_count = 0
    truncated = False

    while queue:
        cur_id = queue.popleft()
        reachable_count += 1
        if reachable_count > max_nodes:
            truncated = True
            break
        cur = by_id.get(cur_id)
        if cur and cur.get('isMilestone') and cur_id != start_activity_id:
            reachable_milestones.append({
                'activityId': cur_id, 'activityName': cur.get('name') or '',
                'currentFinish': cur.get('finish') or cur.get('earlyFinish') or cur.get('remainFinish'),
                'totalFloat': cur.get('totalFloat'),
                'forecastImpacting': cur_id in at_risk_milestone_ids,
            })
        for s in succ_of.get(cur_id, []):
            sid = s['actId']
            if sid and sid not in visited and by_id.get(sid):
                visited.add(sid)
                queue.append(sid)

    return {
        'available': True,
        'methodologyNote': (
            'Downstream exposure via the imported relationship network — "Connected downstream" lists every '
            'reachable milestone; "Forecast-impacting" is set only for milestones independently identified as '
            'at-risk by milestone movement analysis. Available float may absorb a delay before it reaches a '
            'connected-but-not-forecast-impacting milestone.'
        ),
        'reachableActivityCount': reachable_count,
        'exposedMilestones': reachable_milestones,
        'truncated': truncated,
    }


def compute_milestone_reachability_map(
    activities: List[dict], max_nodes_per_milestone: int = DEFAULT_MAX_EXPOSURE_NODES,
) -> Dict[str, Dict[str, bool]]:
    """Batch alternative to calling find_downstream_exposure once per
    activity. Building milestone exposure for EVERY risk activity via its
    own forward BFS is O(risky_activities x n) — pathological when a large
    fraction of activities are risks (e.g. a genuinely bad update). This
    computes the same "which milestones does this activity's lateness
    reach" answer in O(milestones x n) instead, by walking BACKWARD
    (through predecessors) once per milestone rather than forward once per
    risk. Milestone count is normally small and roughly constant regardless
    of schedule size (unlike risk count, which can spike), so this stays
    cheap even when most activities are flagged as risks.

    Returns {activity_id: {milestone_id: True, ...}} — an activity present
    as a key can reach every milestone_id in its dict (via ANY path, not
    just the driving one, matching find_downstream_exposure's semantics).
    An activity reachable from no milestone is simply absent as a key."""
    by_id, pred_of, _succ_of = build_adjacency(activities)
    milestone_ids = [aid for aid, a in by_id.items() if a.get('isMilestone')]

    reach: Dict[str, Dict[str, bool]] = {}
    for mid in milestone_ids:
        visited = {mid}
        queue = deque([mid])
        count = 0
        while queue:
            cur_id = queue.popleft()
            count += 1
            if count > max_nodes_per_milestone:
                break
            if cur_id != mid:
                reach.setdefault(cur_id, {})[mid] = True
            for p in pred_of.get(cur_id, []):
                pid = p['actId']
                if pid and pid not in visited and by_id.get(pid):
                    visited.add(pid)
                    queue.append(pid)
    return reach
