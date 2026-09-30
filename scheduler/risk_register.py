"""
Schedule Risk Register — ScheduleIQ Schedule Risk, Recovery & Mitigation
Intelligence phase.

Deterministic, project-controls risk register — NOT a generic manually-
entered corporate risk log and NOT predictive AI. Every risk originates
from measurable schedule evidence already computed by the authoritative
engines this module consumes:

  - update_intelligence.build_update_intelligence()  — movement, float,
    critical-path, milestone, duration, constraint, logic, reliability,
    and driver signals.
  - baseline_progress.build_report_lookahead()        — should-have-
    started/finished, overdue-day counts.
  - driving_chain.find_downstream_exposure()          — milestone exposure,
    computed lazily only for activities that already carry >=1 signal.

This module performs NO independent recalculation of any of those facts —
it aggregates them per activity into one risk record with multiple
traceable `riskSignals`, then applies transparent, documented severity/
urgency rules. No score is presented to the user without the rule that
produced it.

Complexity: builds a handful of {activityId: ...} index dicts once — O(n)
— then does O(1) lookups per source list. Downstream-exposure tracing is
only performed for the (typically much smaller) subset of activities that
already qualify as risks, and driving_chain's own BFS is bounded/iterative,
so the whole register stays close to O(n + r) for realistic risk counts.
"""

from __future__ import annotations

import statistics
from datetime import date
from typing import Any, Dict, List, Optional, Set

from . import driving_chain

ENGINE_VERSION = '1.0.0'

SEVERITY_LEVELS = ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
URGENCY_LEVELS = ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')

RISK_STATUS_CHOICES = (
    'OPEN', 'UNDER_REVIEW', 'MITIGATION_PLANNED', 'MITIGATION_IN_PROGRESS', 'MONITORING', 'CLOSED',
)

# ── Deterministic severity scoring — documented here, never an opaque
# number shown alone in the UI; every point traces to a named signal. ─────
_SEV_DRIVING = 3
_SEV_NEGATIVE_FLOAT = 2
_SEV_SEVERELY_NEGATIVE_FLOAT = 1   # additional, on top of _SEV_NEGATIVE_FLOAT, when totalFloat <= -10
_SEV_MILESTONE_EXPOSURE = 2
_SEV_BASELINE_VARIANCE = 1
_SEV_BASELINE_VARIANCE_SEVERE = 1  # additional, when baselineVarianceDays > 20
_SEV_UPDATE_DETERIORATION = 1
_SEV_UPDATE_DETERIORATION_SEVERE = 1  # additional, when finishMovementDays > 10
_SEV_MISSED_COMMITMENT = 1
_SEV_DURATION_GROWTH = 1

_BASELINE_VARIANCE_THRESHOLD = 10
_BASELINE_VARIANCE_SEVERE_THRESHOLD = 20
_DETERIORATION_THRESHOLD = 5
_DETERIORATION_SEVERE_THRESHOLD = 10
_SEVERE_NEGATIVE_FLOAT_THRESHOLD = -10
_DURATION_GROWTH_THRESHOLD_PCT = 20.0  # remaining duration grew by >20% this update

_URGENCY_OVERDUE_ON_DATA_DATE = 'CRITICAL'
_URGENCY_NEAR_TERM_DAYS = 7
_URGENCY_MID_TERM_DAYS = 21


def _act_id_fields(a: dict) -> Dict[str, Any]:
    return {
        'wbs': a.get('wbs') or '', 'area': a.get('area') or '', 'discipline': a.get('discipline') or '',
        'contractor': a.get('contractor') or '', 'system': a.get('system') or '',
    }


def _parse_date(v) -> Optional[date]:
    if isinstance(v, date):
        return v
    if isinstance(v, str) and v:
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _new_risk_entry(activity_id: str, activity_name: str, fields: dict) -> Dict[str, Any]:
    return {
        'riskKey': activity_id, 'activityId': activity_id, 'activityName': activity_name,
        **fields,
        'riskSignals': [], 'riskType': None,
        'baselineStart': None, 'baselineFinish': None, 'baselineVarianceDays': None,
        'currentStart': None, 'currentFinish': None, 'updateMovementDays': None,
        'totalFloat': None, 'previousTotalFloat': None, 'floatMovement': None,
        'critical': False, 'driving': False, 'isMilestone': False,
        'milestoneExposure': None,
    }


def _add_signal(risk: dict, signal_type: str, description: str, evidence: dict) -> None:
    risk['riskSignals'].append({'type': signal_type, 'description': description, 'evidence': evidence})


def build_risk_register(
    update_intelligence_result: Dict[str, Any],
    lookahead_result: Optional[Dict[str, Any]],
    current_activities: List[dict],
    current_data_date: Optional[date],
    group_by: str = 'discipline',
) -> Dict[str, Any]:
    """
    Returns {'engineVersion', 'currentDataDate', 'risks': [...], 'summary': {...}}.

    `update_intelligence_result` is the full dict from
    update_intelligence.build_update_intelligence() (already computed by
    the caller — never recomputed here). `lookahead_result` is the dict
    from baseline_progress.build_report_lookahead() (optional — Look-Ahead
    overdue signals are simply omitted when unavailable, never fabricated).
    `current_activities` is the CURRENT version's raw activity list, needed
    only for downstream-milestone-exposure tracing via driving_chain.
    """
    risks: Dict[str, Dict[str, Any]] = {}
    activities_by_id = {(a.get('code') or a.get('id') or ''): a for a in current_activities}

    def _get(aid, name, fields):
        if aid not in risks:
            risks[aid] = _new_risk_entry(aid, name, fields)
        return risks[aid]

    # ── Signal 1: SLIPPED / newly negative float / deteriorated (movementRows) ──
    for row in (update_intelligence_result.get('movementRows') or []):
        if row.get('matchStatus') != 'MATCHED':
            continue
        flags = row.get('flags') or []
        tf = row.get('currentTotalFloat')
        is_negative_float = tf is not None and tf < 0
        finish_move = row.get('finishMovementDays')
        slipped_materially = 'SLIPPED' in flags and finish_move and finish_move > _DETERIORATION_THRESHOLD

        if not (is_negative_float or slipped_materially):
            continue

        aid = row['activityId']
        r = _get(aid, row.get('activityName') or '', _act_id_fields(row))
        r['baselineFinish'] = row.get('baselineFinish')
        r['baselineVarianceDays'] = row.get('baselineVarianceDays')
        r['currentStart'] = row.get('currentStart')
        r['currentFinish'] = row.get('currentFinish')
        r['updateMovementDays'] = finish_move
        r['totalFloat'] = tf
        r['previousTotalFloat'] = row.get('previousTotalFloat')
        r['floatMovement'] = row.get('floatMovement')
        r['isMilestone'] = bool(row.get('isMilestone'))

        if is_negative_float:
            _add_signal(r, 'NEGATIVE_TOTAL_FLOAT', f'Total Float {tf}d', {'totalFloat': tf})
        if slipped_materially:
            _add_signal(r, 'FINISH_SLIP', f'Finish slipped {finish_move}d this update', {'finishMovementDays': finish_move})
        if row.get('baselineVarianceDays') is not None and row['baselineVarianceDays'] > _BASELINE_VARIANCE_THRESHOLD:
            _add_signal(r, 'BASELINE_VARIANCE', f'{row["baselineVarianceDays"]}d late vs Approved Baseline', {'baselineVarianceDays': row['baselineVarianceDays']})

    # ── Signal 2: newly negative float / severe float deterioration (floatMovement) ──
    fm = update_intelligence_result.get('floatMovement') or {}
    for entry in (fm.get('newlyNegativeFloat') or []):
        aid = entry['activityId']
        r = _get(aid, entry.get('activityName') or '', _act_id_fields(entry))
        r['totalFloat'] = entry.get('currentTotalFloat')
        r['previousTotalFloat'] = entry.get('previousTotalFloat')
        r['floatMovement'] = entry.get('floatMovement')
        _add_signal(r, 'NEWLY_NEGATIVE_FLOAT', f'Float deteriorated {entry.get("floatMovement")}d and went negative', entry)
    for entry in (fm.get('deteriorated') or []):
        move = entry.get('floatMovement')
        if move is not None and move < -5:
            aid = entry['activityId']
            r = _get(aid, entry.get('activityName') or '', _act_id_fields(entry))
            r['totalFloat'] = entry.get('currentTotalFloat')
            r['previousTotalFloat'] = entry.get('previousTotalFloat')
            r['floatMovement'] = move
            _add_signal(r, 'FLOAT_DETERIORATION', f'Float deteriorated {move}d this update', entry)

    # ── Signal 3: driving/critical membership + slip (criticalPathMovement) ──
    cpm = update_intelligence_result.get('criticalPathMovement') or {}
    driving_ids: Set[str] = {e['activityId'] for e in (cpm.get('stayedCritical') or [])} | \
        {e['activityId'] for e in (cpm.get('becameCritical') or [])}
    for entry in (cpm.get('criticalActivitySlipped') or []):
        aid = entry['activityId']
        r = _get(aid, entry.get('activityName') or '', _act_id_fields(entry))
        r['driving'] = True
        r['critical'] = True
        r['updateMovementDays'] = entry.get('finishMovementDays')
        _add_signal(r, 'CRITICAL_ACTIVITY_SLIP', f'Driving/critical activity slipped {entry.get("finishMovementDays")}d', entry)
    for entry in (cpm.get('becameCritical') or []):
        aid = entry['activityId']
        r = _get(aid, entry.get('activityName') or '', _act_id_fields(entry))
        r['driving'] = True
        r['critical'] = True
        if not any(s['type'] == 'NEWLY_CRITICAL' for s in r['riskSignals']):
            _add_signal(r, 'NEWLY_CRITICAL', 'Newly on the driving/critical path this update', entry)
    # Mark driving/critical for any risk already flagged for another reason.
    for aid in list(risks.keys()):
        if aid in driving_ids:
            risks[aid]['driving'] = True
            risks[aid]['critical'] = True

    # ── Signal 4: missed forecast start/finish (reliability) ──
    reliability = update_intelligence_result.get('reliability') or {}
    for entry in (reliability.get('missedStarts') or []):
        aid = entry.get('activityId')
        if not aid:
            continue
        r = _get(aid, entry.get('activityName') or '', _act_id_fields(entry))
        _add_signal(r, 'MISSED_FORECAST_START', 'Forecast start was missed this update period', entry)
    for entry in (reliability.get('missedFinishes') or []):
        aid = entry.get('activityId')
        if not aid:
            continue
        r = _get(aid, entry.get('activityName') or '', _act_id_fields(entry))
        _add_signal(r, 'MISSED_FORECAST_FINISH', 'Forecast finish was missed this update period', entry)

    # ── Signal 5: remaining/original duration growth (durationChanges) ──
    dc = update_intelligence_result.get('durationChanges') or {}
    for entry in (dc.get('remainingDurationChanges') or []):
        prev, cur = entry.get('previousDuration'), entry.get('currentDuration')
        if prev and cur and prev > 0 and ((cur - prev) / prev * 100.0) > _DURATION_GROWTH_THRESHOLD_PCT:
            aid = entry['activityId']
            r = _get(aid, entry.get('activityName') or '', _act_id_fields(entry))
            _add_signal(r, 'REMAINING_DURATION_GROWTH', f'Remaining duration grew from {prev}d to {cur}d', entry)
    for entry in (dc.get('originalDurationChanges') or []):
        delta = entry.get('delta')
        if delta and delta > 0:
            aid = entry['activityId']
            if aid in risks:  # only surface on activities already flagged for another reason
                _add_signal(risks[aid], 'ORIGINAL_DURATION_CHANGE', f'Original Duration changed by {delta:+}d', entry)

    # ── Signal 6: constraint changes (constraintChanges) — only on already-flagged activities ──
    for entry in (update_intelligence_result.get('constraintChanges') or []):
        aid = entry.get('activityId')
        if aid in risks:
            _add_signal(risks[aid], 'CONSTRAINT_CHANGE', entry.get('description') or 'Constraint changed this update', entry)

    # ── Signal 7: logic changes (logicChanges.byActivity) — only on already-flagged activities ──
    logic_by_activity = (update_intelligence_result.get('logicChanges') or {}).get('byActivity') or {}
    for aid, changes in logic_by_activity.items():
        if aid in risks and changes:
            _add_signal(risks[aid], 'LOGIC_CHANGE', f'{len(changes)} relationship change(s) this update', {'count': len(changes)})

    # ── Signal 8: milestone slip / negative float (milestoneMovement) ──
    mm = update_intelligence_result.get('milestoneMovement') or {}
    at_risk_milestone_ids: Set[str] = set()
    for row in (mm.get('rows') or []):
        classification = row.get('classification')
        if classification in ('SLIPPED', 'NEWLY_NEGATIVE_FLOAT') or (row.get('currentTotalFloat') is not None and row['currentTotalFloat'] < 0):
            at_risk_milestone_ids.add(row['activityId'])
        if classification not in ('SLIPPED', 'NEWLY_NEGATIVE_FLOAT'):
            continue
        aid = row['activityId']
        r = _get(aid, row.get('activityName') or '', _act_id_fields(row))
        r['isMilestone'] = True
        r['baselineFinish'] = row.get('baselineDate')
        r['baselineVarianceDays'] = row.get('baselineVarianceDays')
        r['currentFinish'] = row.get('currentDate')
        r['updateMovementDays'] = row.get('movementDays')
        r['totalFloat'] = row.get('currentTotalFloat')
        r['previousTotalFloat'] = row.get('previousTotalFloat')
        _add_signal(r, 'MILESTONE_SLIP', f'Milestone {classification.replace("_", " ").title()}', row)

    # ── Signal 9: Look-Ahead overdue (Should Have Started/Finished) ──
    if lookahead_result:
        for entry in (lookahead_result.get('shouldHaveStarted') or []):
            aid = entry.get('activityId')
            if not aid:
                continue
            r = _get(aid, entry.get('activityName') or '', _act_id_fields(entry))
            r['currentStart'] = entry.get('currentStart')
            _add_signal(r, 'SHOULD_HAVE_STARTED', f'{entry.get("daysOverdue", "?")}d overdue to start', entry)
        for entry in (lookahead_result.get('shouldHaveFinished') or []):
            aid = entry.get('activityId')
            if not aid:
                continue
            r = _get(aid, entry.get('activityName') or '', _act_id_fields(entry))
            r['currentFinish'] = entry.get('currentFinish')
            _add_signal(r, 'SHOULD_HAVE_FINISHED', f'{entry.get("daysOverdue", "?")}d overdue to finish', entry)

    # ── Milestone exposure (item 13) — batch-computed once for the whole
    # register (see driving_chain.compute_milestone_reachability_map),
    # never one forward BFS per risk activity. That per-risk approach is
    # O(risky_activities x n) — pathological on a genuinely bad update
    # where a large fraction of activities are risks; the batch reverse-
    # BFS-per-milestone approach is O(milestones x n), which stays cheap
    # since milestone count is normally small and roughly constant
    # regardless of how many activities are currently flagged as risks. ──
    if current_activities and risks:
        reach_map = driving_chain.compute_milestone_reachability_map(current_activities)
        for aid, r in risks.items():
            if r['isMilestone']:
                continue  # a milestone doesn't need its own downstream-exposure trace
            reachable_milestone_ids = reach_map.get(aid) or {}
            if not reachable_milestone_ids:
                r['milestoneExposure'] = {'connectedMilestoneCount': 0, 'forecastImpactingMilestones': []}
                continue
            impacting = []
            for mid in reachable_milestone_ids:
                if mid not in at_risk_milestone_ids:
                    continue
                ma = activities_by_id.get(mid)
                if not ma:
                    continue
                impacting.append({
                    'activityId': mid, 'activityName': ma.get('name') or '',
                    'currentFinish': ma.get('finish') or ma.get('earlyFinish') or ma.get('remainFinish'),
                    'totalFloat': ma.get('totalFloat'), 'forecastImpacting': True,
                })
            r['milestoneExposure'] = {
                'connectedMilestoneCount': len(reachable_milestone_ids),
                'forecastImpactingMilestones': impacting,
            }
            if impacting:
                names = ', '.join(m['activityName'] or m['activityId'] for m in impacting[:3])
                _add_signal(r, 'MILESTONE_EXPOSURE', f'Drives at-risk milestone(s): {names}', {'milestones': impacting})

    # ── Deterministic severity + urgency ──
    for r in risks.values():
        r['severity'], r['severityReason'] = _compute_severity(r)
        r['urgency'], r['urgencyReason'] = _compute_urgency(r, current_data_date)
        r['riskReason'] = f'{r["severity"]} — {r["severityReason"]}'
        r['riskType'] = r['riskSignals'][0]['type'] if r['riskSignals'] else None

    ordered = sorted(
        risks.values(),
        key=lambda r: (-SEVERITY_LEVELS.index(r['severity']), -URGENCY_LEVELS.index(r['urgency']) if r['urgency'] in URGENCY_LEVELS else 0, r['activityId']),
    )

    summary = _build_summary(ordered, group_by)
    heatmap = compute_risk_heatmap(ordered, group_by)

    return {
        'engineVersion': ENGINE_VERSION,
        'currentDataDate': current_data_date.isoformat() if current_data_date else None,
        'risks': ordered,
        'summary': summary,
        'heatmap': heatmap,
    }


def _compute_severity(r: dict) -> (str, str):
    score = 0
    reasons = []

    if r.get('driving'):
        score += _SEV_DRIVING
        reasons.append('on the current driving/critical path')

    tf = r.get('totalFloat')
    if tf is not None and tf < 0:
        score += _SEV_NEGATIVE_FLOAT
        reasons.append(f'Total Float {tf}d')
        if tf <= _SEVERE_NEGATIVE_FLOAT_THRESHOLD:
            score += _SEV_SEVERELY_NEGATIVE_FLOAT

    exposure = r.get('milestoneExposure') or {}
    if exposure.get('forecastImpactingMilestones'):
        score += _SEV_MILESTONE_EXPOSURE
        names = ', '.join(m['activityName'] or m['activityId'] for m in exposure['forecastImpactingMilestones'][:2])
        reasons.append(f'exposes {names}')

    bv = r.get('baselineVarianceDays')
    if bv is not None and bv > _BASELINE_VARIANCE_THRESHOLD:
        score += _SEV_BASELINE_VARIANCE
        reasons.append(f'baseline variance +{bv}d')
        if bv > _BASELINE_VARIANCE_SEVERE_THRESHOLD:
            score += _SEV_BASELINE_VARIANCE_SEVERE

    move = r.get('updateMovementDays')
    if move is not None and move > _DETERIORATION_THRESHOLD:
        score += _SEV_UPDATE_DETERIORATION
        reasons.append(f'finish deteriorated {move}d this update')
        if move > _DETERIORATION_SEVERE_THRESHOLD:
            score += _SEV_UPDATE_DETERIORATION_SEVERE

    if any(s['type'] in ('MISSED_FORECAST_START', 'MISSED_FORECAST_FINISH') for s in r['riskSignals']):
        score += _SEV_MISSED_COMMITMENT
        reasons.append('missed a forecast commitment')

    if any(s['type'] == 'REMAINING_DURATION_GROWTH' for s in r['riskSignals']):
        score += _SEV_DURATION_GROWTH
        reasons.append('remaining duration grew materially')

    if score >= 6:
        level = 'CRITICAL'
    elif score >= 4:
        level = 'HIGH'
    elif score >= 2:
        level = 'MEDIUM'
    else:
        level = 'LOW'

    reason_text = '; '.join(reasons) if reasons else 'flagged by schedule evidence below the material-severity threshold'
    return level, reason_text


def _compute_urgency(r: dict, current_data_date: Optional[date]):
    if any(s['type'] in ('SHOULD_HAVE_STARTED', 'SHOULD_HAVE_FINISHED') for s in r['riskSignals']):
        return _URGENCY_OVERDUE_ON_DATA_DATE, 'already overdue relative to the current Data Date'

    tf = r.get('totalFloat')
    if tf is not None and tf < 0:
        return 'CRITICAL', f'Total Float already negative ({tf}d)'

    if not current_data_date:
        return 'UNAVAILABLE', 'current Data Date not available'

    target = _parse_date(r.get('currentStart')) or _parse_date(r.get('currentFinish'))
    if not target:
        return 'UNAVAILABLE', 'no forecast start/finish date available'

    days_out = (target - current_data_date).days
    if days_out <= 0:
        return 'CRITICAL', 'forecast date is at or before the current Data Date'
    if days_out <= _URGENCY_NEAR_TERM_DAYS:
        return 'HIGH', f'{days_out}d from the current Data Date'
    if days_out <= _URGENCY_MID_TERM_DAYS:
        return 'MEDIUM', f'{days_out}d from the current Data Date'
    return 'LOW', f'{days_out}d from the current Data Date'


def _build_summary(ordered_risks: List[dict], group_by: str) -> Dict[str, Any]:
    by_severity = {lvl: 0 for lvl in SEVERITY_LEVELS}
    for r in ordered_risks:
        by_severity[r['severity']] += 1

    by_group: Dict[str, Dict[str, int]] = {}
    for r in ordered_risks:
        g = r.get(group_by) or 'Unassigned'
        entry = by_group.setdefault(g, {'total': 0, 'critical': 0, 'high': 0})
        entry['total'] += 1
        if r['severity'] == 'CRITICAL':
            entry['critical'] += 1
        elif r['severity'] == 'HIGH':
            entry['high'] += 1

    return {
        'totalRisks': len(ordered_risks),
        'bySeverity': by_severity,
        'byGroup': {'groupBy': group_by, 'groups': by_group},
        'drivingRiskCount': sum(1 for r in ordered_risks if r.get('driving')),
        'negativeFloatCount': sum(1 for r in ordered_risks if (r.get('totalFloat') or 0) < 0),
        'milestoneExposureCount': sum(1 for r in ordered_risks if (r.get('milestoneExposure') or {}).get('forecastImpactingMilestones')),
    }


def compute_risk_heatmap(ordered_risks: List[dict], group_by: str = 'area') -> Dict[str, Any]:
    """Dashboard Consolidation Phase 1 — replaces schedule_risk.py's
    opaque point-scored heat map (the "Risk Heat Map" tab previously used a
    different engine than the "Risk Register" tab in the same workspace).

    Groups the register's already-flagged risks (never the whole activity
    population — a risk entry only exists when a real signal fired) by
    area/wbs/discipline/contractor/system. Cells are colored by discrete
    severity counts (CRITICAL/HIGH/MEDIUM/LOW), the SAME tiers the Risk
    Register list already shows — not a new continuous score, so there is
    still exactly one severity model in the app. Total Float is never
    summed across a group — count/min/median only, the same governance
    float_intelligence.compute_float_heatmap() already established."""
    groups: Dict[str, List[dict]] = {}
    for r in ordered_risks:
        key = r.get(group_by) or 'Unassigned'
        groups.setdefault(key, []).append(r)

    cells = []
    for key, grp in groups.items():
        tfs = [r['totalFloat'] for r in grp if r.get('totalFloat') is not None]
        cells.append({
            'group': key,
            'riskCount': len(grp),
            'criticalCount': sum(1 for r in grp if r['severity'] == 'CRITICAL'),
            'highCount': sum(1 for r in grp if r['severity'] == 'HIGH'),
            'mediumCount': sum(1 for r in grp if r['severity'] == 'MEDIUM'),
            'lowCount': sum(1 for r in grp if r['severity'] == 'LOW'),
            'drivingCount': sum(1 for r in grp if r.get('driving')),
            'negativeFloatCount': sum(1 for r in grp if (r.get('totalFloat') or 0) < 0),
            'milestoneExposureCount': sum(1 for r in grp if (r.get('milestoneExposure') or {}).get('forecastImpactingMilestones')),
            'minimumTotalFloat': min(tfs) if tfs else None,
            'medianTotalFloat': round(statistics.median(tfs), 2) if tfs else None,
        })
    cells.sort(key=lambda c: (-c['criticalCount'], -c['highCount'], c['group']))
    return {'groupBy': group_by, 'cells': cells, 'totalRisks': len(ordered_risks)}
