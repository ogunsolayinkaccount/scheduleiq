"""
AI Context Service — ScheduleIQ

Assembles a bounded, structured, fully-traceable set of FACTS from the
deterministic engines (schedule_comparison, schedule_risk, milestones) for
an LLM to write about. This module never calls an LLM and never invents
anything — it is pure aggregation of numbers/records the rest of ScheduleIQ
already computed and verified.

Design rules (enforced by shape, not just convention):
  - Every named activity/milestone fact carries its own activityId so a
    response can always be traced back to a real record.
  - Nothing here sends the full activity list to a caller — only bounded
    top-N lists (topRiskActivities, topFinishSlips, ...), matching "don't
    ship 50,000 activities to the model."
  - Optional inputs (previous_version, baseline, documents, focus) are
    genuinely optional — every section they'd populate is simply absent
    (not fabricated) when the input isn't supplied, exactly like every
    other engine in this codebase.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from . import baseline_progress
from .activity_analysis import build_activity_analysis, is_activity_complete
from .driving_chain import trace_driving_path
from .milestones import build_milestone_report
from .risk_register import build_risk_register, compute_risk_heatmap
from .schedule_comparison import compare_schedules
from .schedule_risk import compute_risk
from .update_intelligence import build_update_intelligence

MAX_DOCUMENT_EXCERPT_CHARS = 2000
DEFAULT_TOP_N = 10


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


def _iso(v) -> Optional[str]:
    d = _parse_date(v)
    return d.isoformat() if d else None


def apply_focus_filter(activities: List[dict], focus: Optional[Dict[str, str]]) -> List[dict]:
    """Narrow the activity list to a metadata focus (area/discipline/contractor/
    system/wbs), used both for AI Review's 'focus area' param and AI Chat's
    topic classification. Unknown/absent focus fields are ignored, never
    causing a crash or an empty result."""
    if not focus:
        return activities
    out = activities
    for field in ('area', 'discipline', 'contractor', 'system', 'wbs'):
        wanted = focus.get(field)
        if wanted:
            out = [a for a in out if str(a.get(field, '')).lower() == str(wanted).lower()]
    return out


def _top_finish_slips(comparison_result: Optional[dict], n: int) -> List[Dict[str, Any]]:
    if not comparison_result:
        return []
    changes = [
        c for c in comparison_result['activities']['changes']
        if c['fieldKey'] == 'bFinish' and isinstance(c.get('deltaDays'), (int, float)) and c['deltaDays'] > 0
    ]
    changes.sort(key=lambda c: -c['deltaDays'])
    return [
        {'activityId': c['activityId'], 'activityName': c['activityName'],
         'previousFinish': c['previous'], 'currentFinish': c['current'], 'deltaDays': c['deltaDays']}
        for c in changes[:n]
    ]


def _top_float_deterioration(comparison_result: Optional[dict], n: int, exclude_ids: Optional[set] = None) -> List[Dict[str, Any]]:
    """exclude_ids: Complete activities — their Current Total Float is
    Unavailable for actionable analysis (see activity_analysis.py), so the
    legacy comparison's raw stored-float delta must not be presented."""
    if not comparison_result:
        return []
    exclude_ids = exclude_ids or set()
    changes = [
        c for c in comparison_result['activities']['changes']
        if c['fieldKey'] == 'totalFloat' and isinstance(c.get('delta'), (int, float)) and c['delta'] < 0
        and c['activityId'] not in exclude_ids
    ]
    changes.sort(key=lambda c: c['delta'])
    return [
        {'activityId': c['activityId'], 'activityName': c['activityName'],
         'previousFloat': c['previous'], 'currentFloat': c['current'], 'delta': c['delta']}
        for c in changes[:n]
    ]


def _bounded_comparison_summary(comparison_result: dict, n: int) -> Dict[str, Any]:
    """The comparison's aggregate counts plus at most `n` added/removed
    activities. The raw per-field `changes` list (one entry per changed
    field per activity - over 1 MB on a ~2,000-activity schedule) is never
    sent to the model: the ranked, bounded views of it (topFinishSlips,
    topFloatDeterioration, comparisonInsights, updateIntelligenceSummary)
    are sent instead, so the payload stays bounded as schedules grow."""
    acts = comparison_result['activities']
    out = {k: v for k, v in acts.items() if k not in ('changes', 'added', 'removed')}
    added, removed = acts.get('added') or [], acts.get('removed') or []
    out['added'] = added[:n]
    out['removed'] = removed[:n]
    out['addedListTruncated'] = len(added) > n
    out['removedListTruncated'] = len(removed) > n
    return out


_FOCUS_FIELDS = ('area', 'discipline', 'contractor', 'system')

# A value this short (e.g. Area "C", "E", "BE") is indistinguishable from an
# ordinary letter/word, so it only counts when the question names the field
# right before it ("Area C"). Never matched as a bare token or a substring.
_SHORT_VALUE_MAX_LEN = 2

# Real contractor/system values can coincide with ordinary English words
# (e.g. a contractor called "Related"). Those need the field label too.
_COMMON_WORD_VALUES = {
    'related', 'current', 'critical', 'change', 'changed', 'next', 'new', 'open', 'all', 'main',
    'general', 'other', 'none', 'total', 'first', 'last', 'north', 'south', 'east', 'west',
}


def _tokens(text: str) -> List[str]:
    import re
    return re.findall(r'[a-z0-9]+', (text or '').lower())


def _run_positions(q_tokens: List[str], v_tokens: List[str]) -> List[int]:
    n = len(v_tokens)
    if n == 0:
        return []
    return [i for i in range(len(q_tokens) - n + 1) if q_tokens[i:i + n] == v_tokens]


def classify_focus(question: str, activities: List[dict]) -> Optional[Dict[str, str]]:
    """
    Ground AI Chat's topic detection in real data — token-based, never
    substring-based. Only area/discipline/contractor/system values that
    actually exist in this schedule can match, and only as a whole-token
    run in the question:

      - "What is happening in Area C?"  -> {'area': 'C'}
      - "What changed since the previous update?" -> None (the letter "c"
        inside words is never a match)
      - Short values (<= 2 characters, e.g. Area "C", "BE") and values that
        are ordinary English words additionally require the field label
        immediately before them ("area c", "discipline e"); a bare "C" or
        "E" in a question is deliberately NOT treated as an area, because
        that cannot be told apart from an ordinary letter.
      - If one field matches more than one distinct value (e.g. "compare
        Area B and Area C"), that field is ambiguous and is skipped rather
        than guessing — the full schedule is used instead.

    Never invents a name; returns None when nothing matches unambiguously,
    and the full (unfiltered) schedule is used.
    """
    q_tokens = _tokens(question)
    if not q_tokens:
        return None
    for field in _FOCUS_FIELDS:
        values = {str(a.get(field)) for a in activities if a.get(field)}
        matched = set()
        for v in values:
            v_tokens = _tokens(v)
            if not v_tokens:
                continue
            needs_label = (
                len(''.join(v_tokens)) <= _SHORT_VALUE_MAX_LEN
                or (len(v_tokens) == 1 and v_tokens[0] in _COMMON_WORD_VALUES)
            )
            for i in _run_positions(q_tokens, v_tokens):
                if needs_label and not (i > 0 and q_tokens[i - 1] == field):
                    # The value itself may already carry the label ("Area C").
                    if not (v_tokens[0] == field):
                        continue
                matched.add(v)
                break
        if len(matched) == 1:
            return {field: next(iter(matched))}
    return None


def build_ai_context(
    project_meta: Dict[str, Any],
    version_meta: Dict[str, Any],
    activities: List[dict],
    data_date: date,
    previous_version_meta: Optional[Dict[str, Any]] = None,
    previous_activities: Optional[List[dict]] = None,
    baseline_activities: Optional[List[dict]] = None,
    documents: Optional[List[Dict[str, Any]]] = None,
    focus: Optional[Dict[str, str]] = None,
    top_n: int = DEFAULT_TOP_N,
    previous_data_date: Optional[date] = None,
    recovery_scenarios: Optional[List[Dict[str, Any]]] = None,
    previous_unresolved: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    project_meta:  {'id','name','projectNumber','client'}
    version_meta:  {'id','versionLabel','dataDate','projectFinish','baselineFinish',
                     'activityCount','milestoneCount','completedCount','inProgressCount',
                     'notStartedCount','criticalCount','negativeFloatCount'}
    documents:     [{'id','filename','documentType','extractedText'}] — text is
                   truncated here; callers should not pre-truncate.
    focus:         {'area'|'discipline'|'contractor'|'system'|'wbs': value} —
                    narrows every section below to that slice of the schedule.
    previous_data_date: the previous version's own EFFECTIVE Data Date (a
                   real date, resolved by the caller the same way `data_date`
                   is — e.g. _effective_data_date(previous_version) — never
                   inferred from previous_version_meta's serialized display
                   field, which may not reflect a Data Date override).
                   Required for Update Intelligence/Look-Ahead-change/Risk
                   Register grounding below; those sections are simply
                   absent (not fabricated) when omitted.
    recovery_scenarios: caller-supplied, already-bounded, already-serialized
                   RecoveryScenario summaries for this project — this module
                   stays DB-free, so it never queries for these itself.
    """
    scoped_activities = apply_focus_filter(activities, focus)
    scoped_previous = apply_focus_filter(previous_activities, focus) if previous_activities else None

    comparison_result = None
    if scoped_previous is not None:
        comparison_result = compare_schedules(scoped_previous, scoped_activities)

    # riskOverall is a continuous 0-100 "how hot is this schedule right now"
    # gauge (schedule_risk.py) - a genuinely different, legitimate concept
    # from the Risk Register's discrete severity tiers, so it is NOT
    # replaced. topRiskAreas/Disciplines/Contractors and topRiskActivities
    # below ARE re-sourced from the Risk Register (risk_register.py) further
    # down, once it's available (Dashboard Consolidation Phase 1) - these
    # schedule_risk.py-based values are used ONLY as the fallback for a
    # project with no previous version, where the Risk Register cannot run
    # (it requires Previous -> Current signals).
    risk_overall = compute_risk(scoped_activities, data_date, comparison_result=comparison_result)['overall']

    risk_by_area = compute_risk(activities, data_date, group_by='area', comparison_result=comparison_result)
    risk_by_discipline = compute_risk(activities, data_date, group_by='discipline', comparison_result=comparison_result)
    risk_by_contractor = compute_risk(activities, data_date, group_by='contractor', comparison_result=comparison_result)

    def _top_groups(risk_result, n=5):
        return [
            {'key': g['key'], 'score': g['score'], 'level': g['level'],
             'drivers': g['drivers'][:3], 'affectedActivityCount': g['affectedActivityCount']}
            for g in risk_result['groups'][:n] if g['score'] > 0
        ]

    scoped_baseline = apply_focus_filter(baseline_activities, focus) if baseline_activities else None

    milestone_report = build_milestone_report(
        scoped_activities, baseline_activities=scoped_baseline,
        comparison_result=comparison_result,
    )
    def _milestone_for_ai(m: Dict[str, Any]) -> Dict[str, Any]:
        # milestones.py predates the completed-activity float rule: a
        # Complete milestone still carries its stored TF/isCritical. Apply
        # the same rule here so it is never described as currently critical.
        out = dict(m)
        if out.get('status') == 'Complete':
            out['totalFloat'] = None
            out['isCritical'] = False
            out['activityComplete'] = True
        return out

    milestone_report = [_milestone_for_ai(m) for m in milestone_report]
    slipped_milestones = [m for m in milestone_report if (m['varianceDays'] or 0) > 0 or (m['movementSinceLastUpdateDays'] or 0) > 0]
    critical_milestones = [m for m in milestone_report if m['riskLevel'] == 'Critical' and not m.get('activityComplete')]
    milestone_summary = {
        'milestoneCount': len(milestone_report),
        'completeCount': sum(1 for m in milestone_report if m.get('activityComplete')),
        'slippedVsBaselineCount': sum(1 for m in milestone_report if (m['varianceDays'] or 0) > 0),
        'movedLaterSinceLastUpdateCount': sum(1 for m in milestone_report if (m['movementSinceLastUpdateDays'] or 0) > 0),
        'slippedEitherCount': len(slipped_milestones),
        'criticalCount': len([m for m in milestone_report if m['riskLevel'] == 'Critical' and not m.get('activityComplete')]),
    }
    _dd_iso = _iso(data_date)
    upcoming_milestones = sorted(
        (m for m in milestone_report
         if not m.get('activityComplete') and m.get('currentFinish') and _dd_iso and str(m['currentFinish'])[:10] >= _dd_iso),
        key=lambda m: str(m['currentFinish'])[:10],
    )

    # Fallback only (see note above) - overridden by the Risk Register below when available.
    top_risk_activities = compute_risk(scoped_activities, data_date, group_by='activity', top_n=top_n)['groups']
    completed_ids = {_act_id(a) for a in scoped_activities if is_activity_complete(a)}

    # ── Master activity analysis (Master Schedule Analysis, Float,
    # Progress & Milestone Intelligence phase) — the SAME completion-aware
    # master rows Activity Analysis/Float Analysis use, so AI Chat never
    # describes a Complete activity's stored/imported Total Float as if it
    # were still an actionable current CPM signal (currentTotalFloat is
    # None/Unavailable for a Complete activity here, exactly as it
    # displays in the UI — see activity_analysis.py's completed-activity
    # float display rule).
    master_analysis = build_activity_analysis(
        scoped_activities, previous_activities=scoped_previous, baseline_activities=scoped_baseline,
        current_data_date=data_date, previous_data_date=previous_data_date,
    )
    master_rows = master_analysis['rows']
    master_row_by_id = {r['activityId']: r for r in master_rows}

    master_activity_summary = {
        'activitiesAnalyzed': len(master_rows),
        'negativeFloatCount': sum(1 for r in master_rows if r['negativeFloat']),
        'newlyNegativeFloatCount': sum(1 for r in master_rows if r['newlyNegativeFloat']),
        'criticalActionableCount': sum(1 for r in master_rows if r['criticalActionable']),
        'nearCriticalCount': sum(1 for r in master_rows if r['nearCritical']),
        'completedActivitiesExcludedFromCurrentFloat': sum(1 for r in master_rows if r['finished']),
        'shouldHaveStartedCount': sum(1 for r in master_rows if r['shouldHaveStarted']),
        'shouldHaveFinishedCount': sum(1 for r in master_rows if r['shouldHaveFinished']),
        'logicChangedCount': sum(1 for r in master_rows if r['logicChanged']),
        'constraintChangedCount': sum(1 for r in master_rows if r['constraintChanged']),
        'drivingActivityCount': sum(1 for r in master_rows if r['driving']),
    }

    def _trim(rows):
        return rows[:top_n]

    newly_negative_sorted = sorted(
        (r for r in master_rows if r['newlyNegativeFloat']),
        key=lambda r: (r['currentTotalFloat'] if r['currentTotalFloat'] is not None else 0),
    )
    top_newly_negative_float = _trim([
        {'activityId': r['activityId'], 'activityName': r['activityName'],
         'previousTotalFloat': r['previousTotalFloat'], 'currentTotalFloat': r['currentTotalFloat']}
        for r in newly_negative_sorted
    ])

    top_should_have_started = _trim([
        {'activityId': r['activityId'], 'activityName': r['activityName'], 'baselineStart': r['baselineStart']}
        for r in master_rows if r['shouldHaveStarted']
    ])
    top_should_have_finished = _trim([
        {'activityId': r['activityId'], 'activityName': r['activityName'],
         'baselineFinish': r['baselineFinish'], 'currentTotalFloat': r['currentTotalFloat']}
        for r in master_rows if r['shouldHaveFinished']
    ])

    top_driving_activities = _trim([
        {'activityId': r['activityId'], 'activityName': r['activityName'],
         'currentTotalFloat': r['currentTotalFloat'], 'currentFinish': r['currentFinish'],
         'activityComplete': r['finished']}
        for r in master_rows if r['driving']
    ])

    duration_growth_sorted = sorted(
        (r for r in master_rows if (r['remainingDurationGrowth'] or 0) > 0),
        key=lambda r: -(r['remainingDurationGrowth'] or 0),
    )
    top_remaining_duration_growth = _trim([
        {'activityId': r['activityId'], 'activityName': r['activityName'],
         'remainingDurationChange': r['remainingDurationChange']}
        for r in duration_growth_sorted
    ])

    # ── Update Intelligence + Risk Register (reused, never recomputed) —
    # only available when a previous version's own effective Data Date was
    # supplied, exactly like comparison_result above.
    update_intelligence_result = None
    if scoped_previous is not None and previous_data_date is not None:
        update_intelligence_result = build_update_intelligence(
            scoped_previous, scoped_activities, previous_data_date, data_date,
            baseline_activities=scoped_baseline,
        )

    update_intelligence_summary = None
    schedule_risks = []
    risk_register_areas = risk_register_disciplines = risk_register_contractors = None
    if update_intelligence_result:
        ui = update_intelligence_result
        logic = ui.get('logicChanges') or {}
        lookahead_change = ui.get('lookaheadChange') or {}
        update_intelligence_summary = {
            'narrative': ui.get('narrative'),
            'movementCounts': ui.get('movementCounts'),
            'criticalPathShiftSummary': ui.get('criticalPathShiftSummary'),
            'logicChangeCount': len(logic.get('added') or []) + len(logic.get('removed') or []) + len(logic.get('changed') or []),
            'constraintChangeCount': len(ui.get('constraintChanges') or []),
            'lookaheadChange': {
                'available': lookahead_change.get('available', False),
                'newlyEnteringCount': lookahead_change.get('newlyEnteringCount'),
                'pushedOutCount': lookahead_change.get('pushedOutCount'),
                'newlyCriticalNearTermCount': lookahead_change.get('newlyCriticalNearTermCount'),
            },
        }

        # Schedule risks (risk_register.py — Risk & Recovery phase engine).
        # Total Float shown per risk is re-grounded against the
        # completion-aware master row above (never risk_register's own raw
        # totalFloat), so a Complete activity is never described here as
        # currently critical merely because its stored/imported TF is 0.
        register = build_risk_register(update_intelligence_result, None, scoped_activities, data_date)
        for r in (register.get('risks') or [])[:top_n]:
            mrow = master_row_by_id.get(r['activityId'])
            schedule_risks.append({
                'activityId': r['activityId'], 'activityName': r.get('activityName'),
                'severity': r.get('severity'), 'urgency': r.get('urgency'), 'riskReason': r.get('riskReason'),
                'currentTotalFloat': mrow['currentTotalFloat'] if mrow else None,
                'activityComplete': bool(mrow['finished']) if mrow else False,
            })

        # ── Dashboard Consolidation Phase 1 — the Risk Register is now
        # authoritative for "top risk activities" / grouped risk views,
        # replacing the older schedule_risk.py-derived defaults above (which
        # remain the fallback ONLY when no previous version exists and the
        # register genuinely cannot run). This is the SAME engine the Risk
        # Register tab and the re-sourced Risk Heat Map tab already use, so
        # AI Chat can never name a "top risk" the Risk & Milestones
        # workspace itself wouldn't also show.
        top_risk_activities = list(schedule_risks)
        risk_register_areas = compute_risk_heatmap(register.get('risks') or [], 'area')['cells'][:top_n]
        risk_register_disciplines = compute_risk_heatmap(register.get('risks') or [], 'discipline')['cells'][:top_n]
        risk_register_contractors = compute_risk_heatmap(register.get('risks') or [], 'contractor')['cells'][:top_n]

    # ── Look Ahead (baseline_progress.build_lookahead — reused, never a
    # competing status engine). Near-term (next 4 weeks from the effective
    # Data Date) activities only; Total Float is deliberately omitted here
    # — that belongs to the float sections above, which already apply the
    # completion-aware display rule.
    lookahead_result = baseline_progress.build_lookahead(scoped_baseline or [], scoped_activities, data_date, weeks=4)
    has_baseline_for_status = bool(scoped_baseline)
    lookahead_summary = {
        'window': lookahead_result.get('window'),
        # Without a designated baseline every activity would be classed
        # "added since baseline" - meaningless - so status is withheld
        # (Unavailable) rather than presented.
        'statusCounts': lookahead_result.get('statusCounts') if has_baseline_for_status else None,
        'statusUnavailableReason': None if has_baseline_for_status else 'No baseline version is designated, so Look-Ahead status classification is unavailable.',
        'activityCountInWindow': lookahead_result.get('activityCount'),
        'activities': _trim([
            {'activityId': r['activityId'], 'activityName': r['activityName'],
             'status': r['status'] if has_baseline_for_status else None,
             'currentStart': r['currentStart'], 'currentFinish': r['currentFinish'],
             'isCritical': r['isCritical'], 'isMilestone': r['isMilestone']}
            for r in (lookahead_result.get('rows') or [])
        ]),
    }

    # Plain-language definition + count for every ranked list, so a list is
    # only ever used to answer the question it actually describes (e.g. an
    # empty topShouldHaveStarted means NONE - it must not be answered from
    # topShouldHaveFinished, which is a different population).
    list_definitions = {
        'topRiskActivities': ("The Risk Register's top activities (risk_register.py) when a previous version is "
                              "available - the SAME engine and figures the Risk & Milestones workspace shows, so this "
                              "must always agree with what a user sees there. Falls back to a standalone single-version "
                              "risk scorer (schedule_risk.py) ONLY when no previous version exists yet."),
        'topRiskAreas': "Risk counts by Area from the Risk Register when available (see topRiskActivities); a continuous-score fallback otherwise.",
        'topRiskDisciplines': "Risk counts by Discipline from the Risk Register when available; a continuous-score fallback otherwise.",
        'topRiskContractors': "Risk counts by Contractor from the Risk Register when available; a continuous-score fallback otherwise.",
        'riskOverall': "A continuous 0-100 whole-schedule risk gauge (schedule_risk.py) - a different concept from the Risk Register's discrete severity tiers (see riskRegisterSummary/scheduleRisks), not a substitute for them.",
        'topNewlyNegativeFloat': f"Incomplete activities whose Total Float was >= 0 in the previous update and is < 0 now. Total: {master_activity_summary['newlyNegativeFloatCount']}.",
        'topShouldHaveStarted': f"Not-yet-started activities whose BASELINE START is before the Data Date. Total: {master_activity_summary['shouldHaveStartedCount']}. An empty list means there are none.",
        'topShouldHaveFinished': f"Started-but-unfinished activities whose BASELINE FINISH is before the Data Date (a different population from should-have-started). Total: {master_activity_summary['shouldHaveFinishedCount']}.",
        'topDrivingActivities': f"Activities flagged on the imported P6 longest/driving path (NOT specific to any one milestone). Total: {master_activity_summary['drivingActivityCount']}.",
        'slippedMilestones': "A capped sample (top N) of milestones later than baseline or later than the previous update. The TOTAL counts are in milestoneSummary - always quote totals from there, never count this list.",
        'nextMilestone': "The earliest not-yet-complete milestone on/after the Data Date, with its traced driving chain (upstream, farthest predecessor first). THIS is the answer to 'what is driving the next milestone' - not topDrivingActivities.",
        'upcomingMilestones': "Not-yet-complete milestones finishing on/after the Data Date, earliest first. Each drivingPredecessor is the predecessor driving THAT milestone - use it to answer what is driving a specific/next milestone.",
        'topRemainingDurationGrowth': "Activities whose Remaining Duration grew since the previous update.",
        'scheduleRisks': "Risk Register entries (severity/urgency from ScheduleIQ's deterministic Risk Register); currentTotalFloat is null for Complete activities.",
    }

    # ── Next milestone + its traced driving chain (driving_chain.py — the
    # existing engine, never a second trace). The direct answer source for
    # "what is driving the next milestone".
    next_milestone = None
    if upcoming_milestones:
        nm = upcoming_milestones[0]
        trace = trace_driving_path(scoped_activities, nm['activityId'], max_hops=8)
        chain = []
        if trace.get('available'):
            for step in (trace.get('upstreamChain') or [])[:-1]:      # last element is the milestone itself
                mrow = master_row_by_id.get(step.get('activityId'))
                chain.append({
                    'activityId': step.get('activityId'), 'activityName': step.get('activityName'),
                    'currentTotalFloat': mrow['currentTotalFloat'] if mrow else None,
                    'activityComplete': bool(mrow['finished']) if mrow else False,
                    'relationshipToNext': (step.get('relationshipFromPrevious') or {}).get('relationshipType'),
                })
        next_milestone = {
            'activityId': nm['activityId'], 'activityName': nm.get('activityName'),
            'currentFinish': nm.get('currentFinish'), 'varianceDays': nm.get('varianceDays'),
            'currentTotalFloat': nm.get('totalFloat'), 'riskLevel': nm.get('riskLevel'),
            'drivingPredecessor': nm.get('drivingPredecessor'),
            'drivingChainUpstreamFarthestFirst': chain,
            'drivingChainTruncated': bool(trace.get('upstreamTruncated')) if trace.get('available') else None,
        }

    doc_excerpts = []
    for d in (documents or []):
        text = (d.get('extractedText') or '')[:MAX_DOCUMENT_EXCERPT_CHARS]
        if text:
            doc_excerpts.append({
                'documentId': d.get('id'), 'filename': d.get('filename', ''),
                'documentType': d.get('documentType', ''), 'excerpt': text,
            })

    return {
        'project': {
            'id': project_meta.get('id'), 'name': project_meta.get('name', ''),
            'projectNumber': project_meta.get('projectNumber', ''), 'client': project_meta.get('client', ''),
        },
        'currentVersion': version_meta,
        'previousVersion': previous_version_meta,
        'dataDate': _iso(data_date),
        'focus': focus or None,
        'scopedActivityCount': len(scoped_activities),
        'riskOverall': risk_overall,
        'riskRegisterSummary': register.get('summary') if update_intelligence_result else None,
        'topRiskAreas': risk_register_areas if risk_register_areas is not None else _top_groups(risk_by_area),
        'topRiskDisciplines': risk_register_disciplines if risk_register_disciplines is not None else _top_groups(risk_by_discipline),
        'topRiskContractors': risk_register_contractors if risk_register_contractors is not None else _top_groups(risk_by_contractor),
        'topRiskActivities': top_risk_activities,
        'milestoneCount': len(milestone_report),
        'slippedMilestones': slipped_milestones[:top_n],
        'criticalMilestones': critical_milestones[:top_n],
        'upcomingMilestones': upcoming_milestones[:top_n],
        'nextMilestone': next_milestone,
        'milestoneSummary': milestone_summary,
        'topFinishSlips': _top_finish_slips(comparison_result, top_n),
        'topFloatDeterioration': _top_float_deterioration(comparison_result, top_n, completed_ids),
        'comparisonSummary': _bounded_comparison_summary(comparison_result, top_n) if comparison_result else None,
        'comparisonNarrative': comparison_result['summaryNarrative'] if comparison_result else None,
        'comparisonInsights': comparison_result['insights'][:top_n] if comparison_result else [],
        'narrativeDocuments': doc_excerpts,
        'hasComparison': comparison_result is not None,
        'hasBaseline': bool(baseline_activities),
        'masterActivitySummary': master_activity_summary,
        'topNewlyNegativeFloat': top_newly_negative_float,
        'topShouldHaveStarted': top_should_have_started,
        'topShouldHaveFinished': top_should_have_finished,
        'topDrivingActivities': top_driving_activities,
        'topRemainingDurationGrowth': top_remaining_duration_growth,
        'updateIntelligenceSummary': update_intelligence_summary,
        'scheduleRisks': schedule_risks,
        'lookAhead': lookahead_summary,
        'activeRecoveryScenarios': (recovery_scenarios or [])[:top_n],
        'hasUpdateIntelligence': update_intelligence_result is not None,
        'previousVersionUnresolved': previous_unresolved,
        'listDefinitions': list_definitions,
    }
