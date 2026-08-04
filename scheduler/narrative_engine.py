"""
Schedule Performance Narrative Engine — ScheduleIQ v1.0.0

Generates structured, evidence-based schedule performance narratives.
Every statement is traceable to calculated schedule data.

Design principles:
  - No hard-coded unsupported industry benchmarks
  - Negative float treated as potentially shared, not always individual
  - BEI only reported when baseline is valid and activity matching is reliable
  - Status derived from status_engine, not from heuristic thresholds
  - Recommendations drawn from triggered conditions only
  - Longest path ≠ all float-critical activities
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

ENGINE_VERSION = "1.0.0"


# ── Date helpers ───────────────────────────────────────────────────────────────

def _parse(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str) and v:
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            pass
    return None


def _fmt(d: Optional[date]) -> str:
    """Format date as 'Month D, YYYY' — safe on Windows (no %-d)."""
    if not d:
        return "—"
    months = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    return f"{months[d.month - 1]} {d.day}, {d.year}"


def _days_label(n: int) -> str:
    return "day" if abs(n) == 1 else "days"


# ── Activity predicates ────────────────────────────────────────────────────────

def _complete(a: dict) -> bool:
    return (a.get("pctComplete") or 0) >= 100


def _ms(a: dict) -> bool:
    return bool(a.get("isMilestone"))


def _float_val(a: dict) -> Optional[float]:
    tf = a.get("totalFloat")
    return float(tf) if tf is not None else None


def _fc(a: dict) -> Optional[date]:
    for k in ("finish", "earlyFinish", "remainFinish", "lateFinish"):
        d = _parse(a.get(k))
        if d:
            return d
    return None


def _top_wbs(a: dict) -> str:
    import re
    w = a.get("wbsPath") or a.get("wbs") or ""
    parts = re.split(r"[>./\\]", w.strip())
    first = parts[0].strip() if parts else ""
    return first if first else "Unclassified"


# ── Narrative rules (machine-readable, stable IDs) ────────────────────────────

NARRATIVE_RULES: List[Dict] = [
    {
        "rule_id": "CONTRACT_FINISH_LATE",
        "metric": "contract_finish_variance_days",
        "operator": ">",
        "threshold": 0,
        "severity": "CRITICAL",
        "priority": 100,
        "required_data": ["contract_finish_date", "project_forecast_finish"],
        "title": "Current Forecast Exceeds Contract Completion Date",
        "finding_template": (
            "The current forecast completion ({fc_finish}) is {variance} calendar {ddays} "
            "later than the approved contract completion date ({contract_date})."
        ),
        "recommendation_template": (
            "Develop and validate a logic-based recovery plan addressing the current longest path. "
            "Any proposed recovery should be incorporated into the CPM schedule and validated "
            "against the controlling contractual milestones before submission."
        ),
    },
    {
        "rule_id": "CONTRACTUAL_MILESTONE_DELAYED",
        "metric": "contractual_milestones_delayed",
        "operator": ">",
        "threshold": 0,
        "severity": "CRITICAL",
        "priority": 95,
        "required_data": ["milestone_inputs"],
        "title": "Contractual Milestone(s) Delayed or At Risk",
        "finding_template": (
            "{count} contractual {milestones} {is_are} currently forecast or confirmed "
            "beyond {their} approved date{s}. "
            "Management should confirm whether contractual notices are required."
        ),
        "recommendation_template": (
            "Review the controlling path to each delayed contractual milestone. "
            "Develop measurable recovery actions and confirm with the project owner "
            "whether schedule relief or acceleration is required."
        ),
    },
    {
        "rule_id": "NEGATIVE_FLOAT_PRESENT",
        "metric": "neg_float_count",
        "operator": ">",
        "threshold": 0,
        "severity": "HIGH",
        "priority": 85,
        "required_data": [],
        "title": "Negative Total Float Detected",
        "finding_template": (
            "{neg_float_count} incomplete {acts} currently carry negative total float, "
            "with the most negative value at {min_float:.1f} working days. {clustering_note}"
        ),
        "recommendation_template": (
            "Identify the milestone or constraint driving negative float and validate "
            "the logic, remaining durations, and constraints on the driving path before "
            "treating each negative-float activity as an independent cause of delay."
        ),
    },
    {
        "rule_id": "CRITICAL_FLOAT_LOW",
        "metric": "min_critical_float",
        "operator": "<",
        "threshold": 14,
        "severity": "HIGH",
        "priority": 80,
        "required_data": [],
        "title": "Critical-Path Float Below Warning Threshold",
        "finding_template": (
            "The minimum total float on the critical path is {min_critical_float:.1f} working {dflt}. "
            "Any additional delay on the controlling path may directly affect the project completion date."
        ),
        "recommendation_template": (
            "Validate remaining durations, logic, and calendars on the controlling path. "
            "Review hard constraints that may be reducing available float artificially."
        ),
    },
    {
        "rule_id": "BEI_LOW",
        "metric": "bei",
        "operator": "<",
        "threshold": 0.85,
        "severity": "HIGH",
        "priority": 75,
        "required_data": ["approved_baseline", "bei_valid"],
        "title": "Below-Target Baseline Execution Index",
        "finding_template": (
            "The Baseline Execution Index (BEI) is {bei:.2f}. "
            "Of {bei_planned_count} activities planned to be complete by the data date, "
            "{bei_actual_count} have been confirmed complete. "
            "This result is based on matched activities between the baseline and current update."
        ),
        "recommendation_template": (
            "Verify that actual progress is statused through the current data date. "
            "Review activities that remain incomplete beyond their baseline finish dates "
            "and identify root causes. Confirm whether the baseline dates reflect the "
            "formally approved schedule before relying on BEI for performance conclusions."
        ),
    },
    {
        "rule_id": "OVERDUE_ACTIVITIES",
        "metric": "overdue_count",
        "operator": ">",
        "threshold": 0,
        "severity": "MEDIUM",
        "priority": 70,
        "required_data": [],
        "title": "Activities Past Planned Finish Date",
        "finding_template": (
            "{overdue_count} incomplete {acts} are past their current planned finish dates "
            "({overdue_pct_of_incomplete:.0f}% of all incomplete activities). "
            "These activities may be affecting successor logic and downstream milestones."
        ),
        "recommendation_template": (
            "Review status, remaining duration, and successor impacts for all activities "
            "past their planned finish. Confirm whether remaining durations and logic reflect "
            "the current execution plan."
        ),
    },
    {
        "rule_id": "NEAR_CRITICAL_CONVERGENCE",
        "metric": "near_critical_count",
        "operator": ">=",
        "threshold": 3,
        "severity": "MEDIUM",
        "priority": 60,
        "required_data": [],
        "title": "Multiple Near-Critical Paths Converging",
        "finding_template": (
            "{near_critical_count} activities are in the near-critical range "
            "(between 0 and {near_crit_threshold:.0f} working days of total float). "
            "Multiple converging near-critical paths increase the combined probability "
            "of schedule delay."
        ),
        "recommendation_template": (
            "Monitor near-critical activities closely at the next update cycle. "
            "Identify predecessor chains at risk of consuming remaining float "
            "and develop contingency plans for the most vulnerable paths."
        ),
    },
    {
        "rule_id": "QUALITY_CONCERN",
        "metric": "quality_score",
        "operator": "<",
        "threshold": 70,
        "severity": "MEDIUM",
        "priority": 50,
        "required_data": ["quality_result"],
        "title": "Schedule Quality Below Acceptable Threshold",
        "finding_template": (
            "The schedule quality score is {quality_score:.0f} out of 100. "
            "{quality_issues}"
        ),
        "recommendation_template": (
            "Resolve material schedule-quality issues before relying on the forecast. "
            "Prioritise open-end activities, hard constraints, and any circular logic."
        ),
    },
]


# ── Context builder ────────────────────────────────────────────────────────────

def _extract_context(
    current: List[dict],
    baseline: Optional[List[dict]],
    previous: Optional[List[dict]],
    milestone_inputs: Optional[List[dict]],
    data_date: Optional[date],
    contract_finish_date: Optional[date],
    status_result: Optional[dict],
    quality_result: Optional[dict],
    near_crit_threshold: float = 5.0,
) -> dict:
    """Derive all computable facts from activity lists. Every fact is either
    calculated or explicitly labelled as unavailable."""

    ctx: dict = {
        "has_baseline": bool(baseline),
        "has_previous": bool(previous),
        "has_milestones": bool(milestone_inputs),
        "has_data_date": bool(data_date),
        "has_contract_finish": bool(contract_finish_date),
        "data_date": data_date,
        "contract_finish_date": contract_finish_date,
        "near_crit_threshold": near_crit_threshold,
        "limitations": [],
    }

    # ── Basic counts ──────────────────────────────────────────────────────────
    all_acts = current or []
    non_ms = [a for a in all_acts if not _ms(a)]
    incomplete = [a for a in non_ms if not _complete(a)]
    complete_acts = [a for a in non_ms if _complete(a)]
    in_progress = [a for a in non_ms if a.get("start") and not _complete(a)]
    not_started = [a for a in non_ms if not a.get("start") and not _complete(a)]
    milestones = [a for a in all_acts if _ms(a)]

    ctx.update({
        "total": len(all_acts),
        "total_non_ms": len(non_ms),
        "complete_count": len(complete_acts),
        "in_progress_count": len(in_progress),
        "not_started_count": len(not_started),
        "milestone_count": len(milestones),
        "incomplete_count": len(incomplete),
    })

    # Project names from activity data
    names = sorted({
        a.get("projectName") or a.get("projectId") or ""
        for a in all_acts
        if (a.get("projectName") or a.get("projectId") or "")
    })
    ctx["project_name"] = ", ".join(names) if names else "Unknown Project"

    # ── Forecast finish ───────────────────────────────────────────────────────
    fc_dates = [_fc(a) for a in incomplete if _fc(a)]
    ctx["project_forecast_finish"] = max(fc_dates) if fc_dates else None

    # ── Contract variance ─────────────────────────────────────────────────────
    ctx["contract_finish_variance_days"] = None
    if contract_finish_date and ctx["project_forecast_finish"]:
        ctx["contract_finish_variance_days"] = (
            ctx["project_forecast_finish"] - contract_finish_date
        ).days
    if not contract_finish_date:
        ctx["limitations"].append(
            "No contract completion date was designated. "
            "Contractual date comparison is unavailable."
        )

    # ── Negative float analysis ───────────────────────────────────────────────
    neg_float_acts = [a for a in incomplete if (_float_val(a) or 0) < 0]
    ctx["neg_float_count"] = len(neg_float_acts)

    if neg_float_acts:
        floats = [_float_val(a) for a in neg_float_acts if _float_val(a) is not None]
        ctx["min_float"] = min(floats) if floats else None

        # Clustering: activities sharing the same value share a common driver
        float_rounded = [round(f, 0) for f in floats]
        most_common = Counter(float_rounded).most_common(3)
        top_val = most_common[0][0] if most_common else None
        top_cnt = most_common[0][1] if most_common else 0
        ctx["neg_float_clustering"] = {
            "most_common_value": top_val,
            "most_common_count": top_cnt,
            "clustering_pct": round(top_cnt / len(neg_float_acts) * 100, 0) if neg_float_acts else 0,
            "top_values": [(v, c) for v, c in most_common],
        }

        # WBS concentration
        wbs_map: Dict[str, int] = defaultdict(int)
        for a in neg_float_acts:
            wbs_map[_top_wbs(a)] += 1
        ctx["neg_float_by_wbs"] = sorted(wbs_map.items(), key=lambda x: -x[1])[:5]

        # Most negative five activities
        sorted_neg = sorted(neg_float_acts, key=lambda a: _float_val(a) or 0)
        ctx["worst_neg_float_acts"] = [
            {
                "code": a.get("code"),
                "name": a.get("name"),
                "float": _float_val(a),
                "wbs": _top_wbs(a),
            }
            for a in sorted_neg[:5]
        ]

        # Controlling milestones (milestones with negative float)
        neg_ms = [a for a in milestones if (_float_val(a) or 0) < 0]
        ctx["neg_float_milestones"] = [
            {"code": a.get("code"), "name": a.get("name"), "float": _float_val(a)}
            for a in sorted(neg_ms, key=lambda a: _float_val(a) or 0)[:3]
        ]
    else:
        ctx.update({
            "min_float": None,
            "neg_float_clustering": None,
            "neg_float_by_wbs": [],
            "worst_neg_float_acts": [],
            "neg_float_milestones": [],
        })

    # ── Critical and near-critical ────────────────────────────────────────────
    crit_acts = [a for a in incomplete if a.get("isCritical")]
    float_crit = [
        a for a in incomplete
        if (_float_val(a) or 999) <= 0 and not a.get("isCritical")
    ]
    near_crit = [
        a for a in incomplete
        if 0 < (_float_val(a) or 999) <= near_crit_threshold
    ]

    ctx.update({
        "critical_count": len(crit_acts),
        "float_critical_count": len(float_crit),
        "near_critical_count": len(near_crit),
        "critical_pct": round(len(crit_acts) / len(non_ms) * 100, 1) if non_ms else 0,
    })

    crit_floats = [_float_val(a) for a in crit_acts if _float_val(a) is not None]
    ctx["min_critical_float"] = min(crit_floats) if crit_floats else None

    # ── Overdue activities ────────────────────────────────────────────────────
    if data_date:
        overdue = [
            a for a in incomplete
            if _parse(a.get("finish") or a.get("earlyFinish"))
            and _parse(a.get("finish") or a.get("earlyFinish")) < data_date
        ]
        ctx["overdue_count"] = len(overdue)
        ctx["overdue_pct_of_incomplete"] = (
            round(len(overdue) / len(incomplete) * 100, 1) if incomplete else 0
        )
    else:
        ctx.update({"overdue_count": 0, "overdue_pct_of_incomplete": 0})

    # ── BEI calculation with explicit validation ──────────────────────────────
    ctx.update({
        "bei": None,
        "bei_valid": False,
        "bei_planned_count": 0,
        "bei_actual_count": 0,
        "bei_limitation": None,
    })

    if not baseline:
        ctx["bei_limitation"] = (
            "BEI was not calculated because no approved baseline was provided."
        )
        ctx["limitations"].append(
            "No approved baseline was designated. BEI calculation and "
            "baseline date comparison are unavailable."
        )
    elif not data_date:
        ctx["bei_limitation"] = (
            "BEI was not calculated because no valid data date was provided."
        )
    else:
        planned_by_dd = [
            a for a in baseline
            if not _ms(a)
            and _parse(a.get("bFinish") or a.get("finish"))
            and _parse(a.get("bFinish") or a.get("finish")) <= data_date
        ]
        if not planned_by_dd:
            ctx["bei_limitation"] = (
                "BEI was not calculated because no baseline activities were "
                "planned to complete by the data date."
            )
        else:
            curr_idx = {
                (a.get("code") or a.get("id") or ""): a for a in current
            }
            matched = 0
            actually_complete = 0
            for bl in planned_by_dd:
                code = bl.get("code") or bl.get("id") or ""
                curr = curr_idx.get(code)
                if curr:
                    matched += 1
                    if _complete(curr):
                        actually_complete += 1

            match_pct = matched / len(planned_by_dd) if planned_by_dd else 0
            if match_pct < 0.5:
                ctx["bei_limitation"] = (
                    f"BEI reliability is low: only {matched} of {len(planned_by_dd)} "
                    "baseline activities could be matched in the current update."
                )
                ctx["limitations"].append(ctx["bei_limitation"])
            if matched > 0:
                ctx["bei"] = actually_complete / len(planned_by_dd)
                ctx["bei_valid"] = match_pct >= 0.5
                ctx["bei_planned_count"] = len(planned_by_dd)
                ctx["bei_actual_count"] = actually_complete

    # ── Baseline finish comparison ────────────────────────────────────────────
    ctx["baseline_forecast_finish"] = None
    ctx["baseline_finish_variance_days"] = None
    if baseline:
        bl_fc = [_fc(a) for a in baseline if not _complete(a) and _fc(a)]
        ctx["baseline_forecast_finish"] = max(bl_fc) if bl_fc else None
    if ctx["baseline_forecast_finish"] and ctx["project_forecast_finish"]:
        ctx["baseline_finish_variance_days"] = (
            ctx["project_forecast_finish"] - ctx["baseline_forecast_finish"]
        ).days

    # ── Previous update comparison ────────────────────────────────────────────
    ctx.update({
        "previous_forecast_finish": None,
        "previous_finish_variance_days": None,
        "previous_neg_float_count": None,
        "previous_critical_count": None,
        "previous_overdue_count": None,
    })
    if previous:
        prev_incomplete = [a for a in previous if not _complete(a) and not _ms(a)]
        prev_fc = [_fc(a) for a in prev_incomplete if _fc(a)]
        ctx["previous_forecast_finish"] = max(prev_fc) if prev_fc else None
        if ctx["previous_forecast_finish"] and ctx["project_forecast_finish"]:
            ctx["previous_finish_variance_days"] = (
                ctx["project_forecast_finish"] - ctx["previous_forecast_finish"]
            ).days
        ctx["previous_neg_float_count"] = sum(
            1 for a in prev_incomplete if (_float_val(a) or 0) < 0
        )
        ctx["previous_critical_count"] = sum(
            1 for a in prev_incomplete if a.get("isCritical")
        )
        if data_date:
            ctx["previous_overdue_count"] = sum(
                1 for a in prev_incomplete
                if _parse(a.get("finish") or a.get("earlyFinish"))
                and _parse(a.get("finish") or a.get("earlyFinish")) < data_date
            )
    else:
        ctx["limitations"].append(
            "No previous schedule update was provided. "
            "Update-to-update trend analysis is unavailable."
        )

    # ── Missed starts / finishes vs baseline ──────────────────────────────────
    ctx.update({"missed_start_count": 0, "missed_finish_count": 0})
    if baseline and data_date:
        bl_idx = {(a.get("code") or a.get("id") or ""): a for a in baseline}
        for a in incomplete:
            code = a.get("code") or a.get("id") or ""
            bl = bl_idx.get(code)
            if not bl:
                continue
            bl_s = _parse(bl.get("bStart") or bl.get("start"))
            bl_f = _parse(bl.get("bFinish") or bl.get("finish"))
            if bl_s and bl_s <= data_date and not a.get("start"):
                ctx["missed_start_count"] += 1
            if bl_f and bl_f <= data_date and not _complete(a):
                ctx["missed_finish_count"] += 1

    # ── Contractual milestones delayed ────────────────────────────────────────
    ctx["contractual_milestones_delayed"] = 0
    if milestone_inputs:
        for m in milestone_inputs:
            if isinstance(m, dict):
                is_c = m.get("is_contractual") or m.get("isContractual")
                if is_c and m.get("status") == "DELAYED":
                    ctx["contractual_milestones_delayed"] += 1

    # ── WBS areas ────────────────────────────────────────────────────────────
    ctx["top_wbs_areas"] = Counter(_top_wbs(a) for a in all_acts).most_common(5)

    # ── Status from pre-computed result ──────────────────────────────────────
    if status_result:
        ctx["status"] = status_result.get("status", "UNDETERMINED")
        ctx["risk_score"] = status_result.get("risk_score", 0)
        ctx["confidence_score"] = status_result.get("confidence_score", 0)
    else:
        # Conservative fallback when no status engine result
        var = ctx.get("contract_finish_variance_days")
        neg = ctx["neg_float_count"]
        if var is not None and var > 30:
            ctx["status"] = "OFF_TRACK"
        elif neg > 0 or ctx["overdue_count"] > max(len(incomplete) * 0.1, 1):
            ctx["status"] = "AT_RISK"
        elif not data_date:
            ctx["status"] = "UNDETERMINED"
        else:
            ctx["status"] = "ON_TRACK"
        ctx["risk_score"] = 0
        ctx["confidence_score"] = 0

    # ── Quality ───────────────────────────────────────────────────────────────
    if quality_result:
        ctx["quality_score"] = quality_result.get("overall_score")
        ctx["quality_findings"] = quality_result.get("findings", [])
        ctx["circular_logic_count"] = quality_result.get("circular_logic_count", 0)
    else:
        ctx.update({
            "quality_score": None,
            "quality_findings": [],
            "circular_logic_count": 0,
        })

    return ctx


# ── Section generators ─────────────────────────────────────────────────────────

def _gen_executive_summary(ctx: dict, settings: dict) -> str:
    status = ctx["status"]
    labels = {
        "ON_TRACK": "On Track",
        "AT_RISK": "At Risk",
        "OFF_TRACK": "Off Track",
        "UNDETERMINED": "Undetermined",
    }
    label = labels.get(status, status)
    dd_str = _fmt(ctx["data_date"]) if ctx["data_date"] else "the current data date"
    parts: List[str] = []

    # Opening — classify and give the primary reason
    if status == "UNDETERMINED":
        parts.append(
            f"Based on the {dd_str} schedule update, the overall schedule status is "
            "Undetermined. Insufficient data is available to support a definitive classification."
        )
    elif status == "OFF_TRACK":
        var = ctx.get("contract_finish_variance_days")
        if var and ctx.get("contract_finish_date"):
            parts.append(
                f"Based on the {dd_str} schedule update, the schedule is classified as "
                f"Off Track. The current forecast completion of {_fmt(ctx['project_forecast_finish'])} "
                f"is {var} calendar {_days_label(var)} beyond the approved contract completion "
                f"date of {_fmt(ctx['contract_finish_date'])}."
            )
        else:
            parts.append(
                f"Based on the {dd_str} schedule update, the schedule is classified as "
                "Off Track. Key schedule indicators have exceeded configured alert thresholds."
            )
    elif status == "AT_RISK":
        parts.append(
            f"Based on the {dd_str} schedule update, the schedule is classified as At Risk."
        )
        reasons: List[str] = []
        if ctx.get("neg_float_count", 0) > 0:
            n = ctx["neg_float_count"]
            reasons.append(
                f"{n} {'activity' if n == 1 else 'activities'} carry negative total float"
            )
        if ctx.get("overdue_count", 0) > 0:
            n = ctx["overdue_count"]
            reasons.append(
                f"{n} {'activity is' if n == 1 else 'activities are'} past their planned finish"
            )
        mf = ctx.get("min_critical_float")
        if mf is not None and mf < 14:
            reasons.append(
                f"critical-path float has declined to {mf:.1f} working {_days_label(int(mf))}"
            )
        if reasons:
            parts.append(
                "The primary schedule concerns include: " + "; ".join(reasons) + "."
            )
    else:  # ON_TRACK
        parts.append(
            f"Based on the {dd_str} schedule update, the schedule is classified as On Track."
        )
        var = ctx.get("contract_finish_variance_days")
        if var is not None and var <= 0 and ctx.get("contract_finish_date"):
            parts.append(
                f"The current forecast completion of {_fmt(ctx['project_forecast_finish'])} "
                f"is within the approved contract completion date of "
                f"{_fmt(ctx['contract_finish_date'])}."
            )

    # Negative float — clarify whether it is clustered
    n = ctx.get("neg_float_count", 0)
    if n > 0:
        cl = ctx.get("neg_float_clustering") or {}
        cpct = cl.get("clustering_pct", 0)
        if cpct > 40 and cl.get("most_common_value") is not None:
            parts.append(
                f"A total of {n} {'activity' if n == 1 else 'activities'} carry negative "
                f"total float. Approximately {cpct:.0f}% of these share a common float "
                f"value of {cl['most_common_value']:.0f} days, suggesting that the variance "
                "may be inherited from a common downstream milestone or constraint rather "
                "than representing independent causes of delay."
            )
        else:
            parts.append(
                f"A total of {n} {'activity' if n == 1 else 'activities'} "
                "carry negative total float."
            )

    # BEI — only when valid
    if ctx.get("bei") is not None and ctx.get("bei_valid"):
        bei = ctx["bei"]
        parts.append(
            f"The Baseline Execution Index (BEI — completed activities against those "
            f"planned to be complete by the data date) is {bei:.2f}, based on "
            f"{ctx['bei_planned_count']} planned-complete activities in the matched baseline."
        )

    # Update-to-update trend
    pv = ctx.get("previous_finish_variance_days")
    if pv is not None:
        if pv > 0:
            parts.append(
                f"Compared with the previous schedule update, forecast completion has "
                f"deteriorated by {pv} calendar {_days_label(pv)}."
            )
        elif pv < 0:
            parts.append(
                f"Compared with the previous schedule update, forecast completion has "
                f"improved by {abs(pv)} calendar {_days_label(abs(pv))}."
            )
        else:
            parts.append(
                "Forecast completion has remained stable since the previous schedule update."
            )

    # Management action
    if status == "OFF_TRACK":
        parts.append(
            "Immediate management attention should focus on developing a logic-based "
            "recovery plan that demonstrates how contractual completion will be achieved."
        )
    elif status == "AT_RISK":
        parts.append(
            "Management attention should focus on validating the driving path to "
            "controlling milestones, updating remaining durations, and developing "
            "measurable recovery actions for affected workstreams."
        )
    elif status == "ON_TRACK":
        parts.append(
            "Continued monitoring of schedule progress and near-critical activities "
            "is recommended."
        )

    return " ".join(parts)


def _gen_current_position(ctx: dict) -> str:
    parts: List[str] = []
    fc = ctx.get("project_forecast_finish")
    bl_fc = ctx.get("baseline_forecast_finish")
    cf = ctx.get("contract_finish_date")
    prev_fc = ctx.get("previous_forecast_finish")

    if fc:
        parts.append(
            f"The current schedule forecasts project completion on {_fmt(fc)}."
        )
    else:
        parts.append(
            "A forecast completion date cannot be determined from the current schedule data."
        )

    if cf:
        var = ctx.get("contract_finish_variance_days")
        if var is not None:
            if var > 0:
                parts.append(
                    f"This is {var} calendar {_days_label(var)} later than the approved "
                    f"contract completion date of {_fmt(cf)}."
                )
            elif var < 0:
                parts.append(
                    f"This is {abs(var)} calendar {_days_label(abs(var))} earlier than the "
                    f"approved contract completion date of {_fmt(cf)}."
                )
            else:
                parts.append(
                    f"The forecast aligns with the approved contract completion date of {_fmt(cf)}."
                )
        else:
            parts.append(f"The approved contract completion date is {_fmt(cf)}.")
    else:
        parts.append(
            "No contract completion date has been designated. "
            "Contractual variance analysis is therefore unavailable."
        )

    if bl_fc:
        var = ctx.get("baseline_finish_variance_days")
        if var is not None:
            if var > 0:
                parts.append(
                    f"Compared with the approved baseline forecast completion of {_fmt(bl_fc)}, "
                    f"the current forecast is {var} calendar {_days_label(var)} later."
                )
            elif var < 0:
                parts.append(
                    f"Compared with the approved baseline forecast completion of {_fmt(bl_fc)}, "
                    f"the current forecast is {abs(var)} calendar {_days_label(abs(var))} earlier."
                )
            else:
                parts.append(
                    f"The current forecast completion aligns with the approved baseline "
                    f"completion of {_fmt(bl_fc)}."
                )
        else:
            parts.append(
                f"The approved baseline forecast completion is {_fmt(bl_fc)}."
            )
    else:
        parts.append(
            "No approved baseline has been identified. The analysis measures current schedule "
            "condition and update-to-update movement but cannot make a definitive baseline "
            "comparison."
        )

    if prev_fc:
        pv = ctx.get("previous_finish_variance_days")
        if pv is not None:
            if pv > 0:
                parts.append(
                    f"Compared with the previous schedule update forecast of {_fmt(prev_fc)}, "
                    f"the current forecast has moved {pv} calendar {_days_label(pv)} later."
                )
            elif pv < 0:
                parts.append(
                    f"Compared with the previous schedule update forecast of {_fmt(prev_fc)}, "
                    f"the current forecast has moved {abs(pv)} calendar {_days_label(abs(pv))} earlier."
                )
            else:
                parts.append(
                    f"Forecast completion has remained stable since the previous update "
                    f"({_fmt(prev_fc)})."
                )
    else:
        parts.append(
            "No previous schedule update was provided for update-to-update comparison."
        )

    return " ".join(parts)


def _gen_critical_path(ctx: dict) -> str:
    parts: List[str] = []
    crit = ctx.get("critical_count", 0)
    non_ms = ctx.get("total_non_ms", 1) or 1
    near_crit = ctx.get("near_critical_count", 0)
    mf = ctx.get("min_critical_float")
    float_crit = ctx.get("float_critical_count", 0)
    crit_pct = ctx.get("critical_pct", 0)
    thr = ctx.get("near_crit_threshold", 5)

    if crit == 0:
        parts.append(
            "No activities are currently identified as P6-critical in the schedule. "
            "This may indicate that the total float calculation was not run, or that "
            "all activities have positive float."
        )
    else:
        parts.append(
            f"The current schedule contains {crit} P6-critical "
            f"{'activity' if crit == 1 else 'activities'} "
            f"({crit_pct:.1f}% of all non-milestone activities)."
        )
        if mf is not None:
            if mf < 0:
                parts.append(
                    f"The minimum total float on the critical path is {mf:.1f} working days, "
                    "indicating schedule pressure against a downstream controlling "
                    "milestone or constraint."
                )
            else:
                parts.append(
                    f"The minimum total float on the critical path is {mf:.1f} working days."
                )

    if float_crit > 0:
        parts.append(
            f"An additional {float_crit} "
            f"{'activity' if float_crit == 1 else 'activities'} "
            "have zero or negative total float but are not flagged as P6-critical. "
            "These should be reviewed to confirm that the critical flag and float "
            "calculation are consistent."
        )

    if near_crit > 0:
        parts.append(
            f"{near_crit} near-critical {'activity' if near_crit == 1 else 'activities'} "
            f"currently have between 0 and {thr:.0f} working days of total float. "
            "These paths could join the critical path if additional delays occur."
        )

    parts.append(
        "The longest driving path to project completion is distinct from the set of all "
        "P6-critical activities. Activities are identified as longest-path activities only "
        "when driving-path analysis confirms this."
    )

    return " ".join(parts)


def _gen_negative_float(ctx: dict) -> str:
    n = ctx.get("neg_float_count", 0)
    if n == 0:
        return "No incomplete activities currently carry negative total float."

    parts: List[str] = []
    mf = ctx.get("min_float")
    cl = ctx.get("neg_float_clustering") or {}
    wbs_dist = ctx.get("neg_float_by_wbs") or []
    neg_ms = ctx.get("neg_float_milestones") or []

    parts.append(
        f"A total of {n} incomplete {'activity' if n == 1 else 'activities'} "
        f"currently carry negative total float, "
        f"with the most negative value at {mf:.1f} working days."
    )

    cpct = cl.get("clustering_pct", 0)
    if cpct > 40 and cl.get("most_common_value") is not None:
        cnt = cl["most_common_count"]
        val = cl["most_common_value"]
        parts.append(
            f"Approximately {cpct:.0f}% of these activities ({cnt} "
            f"{'activity' if cnt == 1 else 'activities'}) share a common float value "
            f"of {val:.0f} days. This pattern typically indicates that the variance is "
            "inherited from a single controlling downstream milestone or constraint, "
            f"rather than representing {n} independent causes of schedule delay. "
            "The controlling milestone and driving path should be identified before "
            "prescribing recovery actions."
        )

    if neg_ms:
        names = "; ".join(
            f"\"{m.get('name') or m.get('code')}\" ({m['float']:.0f}d)"
            for m in neg_ms[:2]
        )
        parts.append(
            f"The following milestones also carry negative float and may be the "
            f"controlling constraint: {names}."
        )

    if wbs_dist:
        top_area, top_count = wbs_dist[0]
        if len(wbs_dist) > 1:
            second_area, second_count = wbs_dist[1]
            parts.append(
                f"Negative float is most concentrated in {top_area} "
                f"({top_count} {'activity' if top_count == 1 else 'activities'}) "
                f"and {second_area} "
                f"({second_count} {'activity' if second_count == 1 else 'activities'})."
            )
        else:
            parts.append(
                f"Negative float is concentrated in {top_area} "
                f"({top_count} {'activity' if top_count == 1 else 'activities'})."
            )

    return " ".join(parts)


def _gen_progress(ctx: dict) -> str:
    total = ctx.get("total_non_ms", 0)
    comp = ctx.get("complete_count", 0)
    in_p = ctx.get("in_progress_count", 0)
    not_s = ctx.get("not_started_count", 0)
    overdue = ctx.get("overdue_count", 0)
    opct = ctx.get("overdue_pct_of_incomplete", 0)

    if total == 0:
        return "No activities are available for progress analysis."

    spc = round(comp / total * 100, 1) if total else 0
    parts: List[str] = []

    parts.append(
        f"The schedule contains {total:,} non-milestone "
        f"{'activity' if total == 1 else 'activities'}. "
        f"Of these, {comp:,} ({spc:.1f}%) are complete, "
        f"{in_p:,} are in progress, and {not_s:,} have not yet started."
    )

    if overdue > 0:
        parts.append(
            f"{overdue} incomplete {'activity' if overdue == 1 else 'activities'} "
            f"({opct:.1f}% of all incomplete activities) are past their current "
            "planned finish dates. These activities should be reviewed to confirm "
            "remaining durations, status, and successor impacts."
        )

    ms_s = ctx.get("missed_start_count", 0)
    ms_f = ctx.get("missed_finish_count", 0)
    if (ms_s > 0 or ms_f > 0) and ctx.get("has_baseline"):
        items: List[str] = []
        if ms_s > 0:
            items.append(
                f"{ms_s} planned {'start' if ms_s == 1 else 'starts'} without an actual start"
            )
        if ms_f > 0:
            items.append(
                f"{ms_f} planned "
                f"{'completion' if ms_f == 1 else 'completions'} without an actual finish"
            )
        parts.append(
            "Compared with the approved baseline through the data date, "
            "the current update shows: " + " and ".join(items) + "."
        )

    # BEI — only when valid and baseline available
    bei = ctx.get("bei")
    valid = ctx.get("bei_valid", False)
    lim = ctx.get("bei_limitation")
    bp = ctx.get("bei_planned_count", 0)
    ba = ctx.get("bei_actual_count", 0)

    if bei is not None and valid:
        interp = (
            "BEI is within an acceptable range, indicating execution is broadly "
            "aligned with the baseline plan."
            if bei >= 0.95
            else (
                f"For every 100 activities planned complete by the data date, "
                f"{round(bei * 100)} have been confirmed complete in the current update. "
                "This result should be interpreted alongside the schedule quality findings "
                "and the extent to which actual progress has been fully statused."
            )
        )
        parts.append(
            f"\nBaseline Execution Index (BEI): {bei:.2f}\n"
            f"Definition: Completed baseline-planned activities ÷ Activities planned to be "
            f"complete by the data date\n"
            f"Numerator: {ba} activities confirmed complete\n"
            f"Denominator: {bp} baseline activities planned to be complete by the data date\n"
            f"Interpretation: {interp}"
        )
    elif lim:
        parts.append(f"\n{lim}")

    return " ".join(parts)


def _gen_quality(ctx: dict) -> str:
    score = ctx.get("quality_score")
    findings = ctx.get("quality_findings") or []
    circular = ctx.get("circular_logic_count", 0)

    if score is None:
        return (
            "Schedule quality assessment was not performed for this analysis. "
            "To enable quality scoring, use the ScheduleIQ Status Engine."
        )

    if score >= 90:
        verdict = "The schedule demonstrates strong technical quality."
    elif score >= 75:
        verdict = (
            "The schedule demonstrates acceptable technical quality "
            "with some areas for improvement."
        )
    elif score >= 60:
        verdict = (
            "The schedule has quality issues that may affect forecast reliability."
        )
    else:
        verdict = (
            "The schedule has significant quality concerns that reduce confidence "
            "in the calculated forecast."
        )

    parts = [
        f"The schedule quality score is {score:.0f} out of 100. {verdict}"
    ]

    if circular > 0:
        parts.append(
            f"CRITICAL: Circular logic has been detected in {circular} "
            f"{'activity' if circular == 1 else 'activities'}. "
            "Circular dependencies prevent reliable CPM analysis and must be resolved "
            "before the schedule forecast can be considered valid."
        )

    for f in findings[:6]:
        parts.append(
            f"{f.get('check', '')}: {f.get('detail', '')} "
            f"{f.get('recommendation', '')}"
        )

    return "\n\n".join(parts)


def _gen_trend(ctx: dict) -> str:
    if not ctx.get("has_previous"):
        return (
            "No previous schedule update was provided. "
            "Update-to-update trend analysis is unavailable."
        )

    parts: List[str] = []
    pv = ctx.get("previous_finish_variance_days")
    pn = ctx.get("previous_neg_float_count")
    cn = ctx.get("neg_float_count", 0)
    pc = ctx.get("previous_critical_count")
    cc = ctx.get("critical_count", 0)
    po = ctx.get("previous_overdue_count")
    co = ctx.get("overdue_count", 0)

    if pv is not None:
        if pv > 0:
            parts.append(
                f"Since the previous update, forecast completion has deteriorated "
                f"by {pv} calendar {_days_label(pv)}."
            )
        elif pv < 0:
            parts.append(
                f"Since the previous update, forecast completion has improved "
                f"by {abs(pv)} calendar {_days_label(abs(pv))}."
            )
        else:
            parts.append("Forecast completion has remained stable since the previous update.")

    if pn is not None:
        delta = cn - pn
        if delta > 0:
            parts.append(
                f"Negative-float activities have increased by {delta} "
                f"(from {pn} to {cn})."
            )
        elif delta < 0:
            parts.append(
                f"Negative-float activities have decreased by {abs(delta)} "
                f"(from {pn} to {cn})."
            )
        else:
            parts.append(
                f"The negative-float activity count has remained stable at {cn}."
            )

    if pc is not None:
        delta = cc - pc
        if delta > 0:
            parts.append(
                f"The critical activity count has increased by {delta} "
                f"(from {pc} to {cc})."
            )
        elif delta < 0:
            parts.append(
                f"The critical activity count has decreased by {abs(delta)} "
                f"(from {pc} to {cc})."
            )
        else:
            parts.append(f"The critical activity count has remained stable at {cc}.")

    if po is not None:
        delta = co - po
        if delta > 0:
            parts.append(
                f"Overdue activities have increased by {delta} "
                f"(from {po} to {co})."
            )
        elif delta < 0:
            parts.append(
                f"Overdue activities have decreased by {abs(delta)} "
                f"(from {po} to {co})."
            )

    return " ".join(parts) if parts else "Trend data is insufficient to detect update-to-update movement."


# ── Rule engine ───────────────────────────────────────────────────────────────

def _apply_rule(rule: dict, ctx: dict) -> Optional[dict]:
    """Evaluate a single narrative rule; return a finding dict or None."""
    metric = rule["metric"]
    op = rule["operator"]
    threshold = rule["threshold"]

    # Required-data guard
    for req in rule.get("required_data", []):
        if req == "approved_baseline" and not ctx.get("has_baseline"):
            return None
        if req == "bei_valid" and not ctx.get("bei_valid"):
            return None
        if req == "contract_finish_date" and not ctx.get("has_contract_finish"):
            return None
        if req == "project_forecast_finish" and not ctx.get("project_forecast_finish"):
            return None
        if req == "milestone_inputs" and not ctx.get("has_milestones"):
            return None
        if req == "quality_result" and ctx.get("quality_score") is None:
            return None

    value = ctx.get(metric)
    if value is None:
        return None

    triggered = (
        (op == ">" and value > threshold)
        or (op == ">=" and value >= threshold)
        or (op == "<" and value < threshold)
        or (op == "<=" and value <= threshold)
        or (op == "==" and value == threshold)
    )
    if not triggered:
        return None

    # Build the finding text
    cl = ctx.get("neg_float_clustering") or {}
    clustering_note = ""
    if ctx.get("neg_float_count", 0) > 0 and cl.get("clustering_pct", 0) > 40:
        clustering_note = (
            f"Approximately {cl['clustering_pct']:.0f}% share a common float value of "
            f"{cl['most_common_value']:.0f} days, suggesting a common controlling constraint."
        )

    quality_issues = ""
    qf = ctx.get("quality_findings") or []
    if qf:
        tops = [f.get("check") for f in qf[:3] if f.get("check")]
        if tops:
            quality_issues = "Material issues include: " + ", ".join(tops) + "."

    def _safe_fmt(**kwargs) -> str:
        try:
            return rule["finding_template"].format(**kwargs)
        except (KeyError, ValueError):
            return rule["finding_template"]

    var = ctx.get("contract_finish_variance_days") or 0
    mf = ctx.get("min_critical_float") or 0
    n = ctx.get("neg_float_count", 0)
    od = ctx.get("overdue_count", 0)
    nc = ctx.get("near_critical_count", 0)
    thr = ctx.get("near_crit_threshold", 5)

    finding_text = _safe_fmt(
        variance=abs(var),
        ddays=_days_label(abs(var)),
        fc_finish=_fmt(ctx.get("project_forecast_finish")),
        contract_date=_fmt(ctx.get("contract_finish_date")),
        neg_float_count=n,
        acts="activity" if n == 1 else "activities",
        min_float=ctx.get("min_float") or 0,
        clustering_note=clustering_note,
        count=value,
        milestones="milestone" if value == 1 else "milestones",
        is_are="is" if value == 1 else "are",
        their="its" if value == 1 else "their",
        s="" if value == 1 else "s",
        near_critical_count=nc,
        near_crit_threshold=thr,
        min_critical_float=mf,
        dflt=_days_label(int(abs(mf))),
        bei=ctx.get("bei") or 0,
        bei_planned_count=ctx.get("bei_planned_count", 0),
        bei_actual_count=ctx.get("bei_actual_count", 0),
        overdue_count=od,
        overdue_pct_of_incomplete=ctx.get("overdue_pct_of_incomplete", 0),
        quality_score=ctx.get("quality_score") or 0,
        quality_issues=quality_issues,
    )

    return {
        "rule_id": rule["rule_id"],
        "severity": rule["severity"],
        "priority": rule.get("priority", 50),
        "title": rule.get("title", rule["rule_id"].replace("_", " ").title()),
        "finding": finding_text,
        "impact": "",
        "recommendation": rule["recommendation_template"],
        "metric": metric,
        "value": value,
        "threshold": threshold,
    }


def _gen_findings(ctx: dict, max_findings: int = 7) -> List[dict]:
    raw: List[dict] = []
    for rule in NARRATIVE_RULES:
        f = _apply_rule(rule, ctx)
        if f:
            raw.append(f)

    # Sort by priority descending
    raw.sort(key=lambda f: -f.get("priority", 50))

    # Deduplicate by metric (keep highest priority per metric)
    seen: set = set()
    deduped: List[dict] = []
    for f in raw:
        key = f["metric"]
        if key not in seen:
            seen.add(key)
            deduped.append(f)

    return deduped[: max(3, min(max_findings, len(deduped)))]


def _gen_recommendations(ctx: dict, findings: List[dict]) -> List[str]:
    recs: List[str] = []
    seen: set = set()
    for f in findings:
        r = f.get("recommendation", "")
        if r and r not in seen:
            seen.add(r)
            recs.append(r)
    for qf in (ctx.get("quality_findings") or [])[:2]:
        r = qf.get("recommendation", "")
        if r and r not in seen:
            seen.add(r)
            recs.append(r)
    return recs


def _gen_conclusion(ctx: dict) -> str:
    status = ctx["status"]
    labels = {
        "ON_TRACK": "On Track",
        "AT_RISK": "At Risk",
        "OFF_TRACK": "Off Track",
        "UNDETERMINED": "Undetermined",
    }
    label = labels.get(status, status)
    conf = ctx.get("confidence_score", 0)
    conf_level = "high" if conf >= 75 else "moderate" if conf >= 50 else "low"
    parts = [f"The schedule is currently classified as {label}."]

    if status == "OFF_TRACK":
        var = ctx.get("contract_finish_variance_days")
        if var:
            parts.append(
                f"The current forecast completion exceeds the approved contract completion "
                f"date by {var} calendar {_days_label(var)}."
            )
        parts.append(
            "Immediate attention should focus on developing a logic-based recovery plan "
            "that demonstrates the path to contractual completion."
        )
    elif status == "AT_RISK":
        parts.append(
            "Although the contractual completion date has not yet moved beyond the approved "
            "milestone, schedule conditions present risk that warrants proactive management action."
        )
        parts.append(
            "Immediate attention should focus on validating the driving logic, updating "
            "remaining durations, and developing measurable recovery actions for affected "
            "workstreams."
        )
    elif status == "ON_TRACK":
        parts.append(
            "All primary schedule health indicators are within acceptable tolerances. "
            "Continued monitoring is recommended."
        )
    else:
        parts.append(
            "Insufficient data is available to support a definitive classification. "
            "Provide an approved baseline and contractual milestone dates to enable "
            "full schedule status analysis."
        )

    lims = ctx.get("limitations") or []
    if lims:
        parts.append(
            f"Confidence in this assessment is {conf_level}. "
            + "; ".join(lims[:2])
            + "."
        )
    else:
        parts.append(f"Confidence in this assessment is {conf_level}.")

    return " ".join(parts)


def _confidence_label(score: float) -> str:
    if score >= 75:
        return "HIGH"
    if score >= 50:
        return "MODERATE"
    return "LOW"


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_narrative(
    current_activities: List[dict],
    baseline_activities: Optional[List[dict]] = None,
    previous_activities: Optional[List[dict]] = None,
    milestone_inputs: Optional[List[dict]] = None,
    data_date: Optional[date] = None,
    contract_finish_date: Optional[date] = None,
    settings: Optional[dict] = None,
    status_result: Optional[dict] = None,
    quality_result: Optional[dict] = None,
) -> dict:
    """
    Generate a structured, evidence-based schedule performance narrative.

    Every section is traceable to calculated schedule data.
    No hard-coded industry benchmarks or unsupported conclusions are included.

    Returns a dict structured for the API and the frontend renderer.
    """
    settings = settings or {}

    ctx = _extract_context(
        current=current_activities,
        baseline=baseline_activities,
        previous=previous_activities,
        milestone_inputs=milestone_inputs,
        data_date=data_date,
        contract_finish_date=contract_finish_date,
        status_result=status_result,
        quality_result=quality_result,
        near_crit_threshold=float(settings.get("near_crit_threshold", 5)),
    )

    findings = _gen_findings(ctx, max_findings=int(settings.get("max_findings", 7)))
    recommendations = _gen_recommendations(ctx, findings)
    conf = ctx.get("confidence_score", 0)

    result: dict = {
        # Header fields
        "project_name": settings.get("project_name") or ctx["project_name"],
        "company": settings.get("company", ""),
        "prepared_by": settings.get("prepared_by", "ScheduleIQ"),
        "report_title": settings.get("report_title", "Schedule Performance Narrative"),
        "data_date": data_date.isoformat() if data_date else None,
        "report_date": date.today().isoformat(),
        "status": ctx["status"],
        "risk_score": round(ctx.get("risk_score", 0), 1),
        "confidence_score": round(conf, 1),
        "confidence_level": _confidence_label(conf),
        "contract_finish_date": (
            contract_finish_date.isoformat() if contract_finish_date else None
        ),
        "baseline_finish_date": (
            ctx["baseline_forecast_finish"].isoformat()
            if ctx.get("baseline_forecast_finish")
            else None
        ),
        "project_forecast_finish": (
            ctx["project_forecast_finish"].isoformat()
            if ctx.get("project_forecast_finish")
            else None
        ),
        "contract_finish_variance_days": ctx.get("contract_finish_variance_days"),
        "baseline_finish_variance_days": ctx.get("baseline_finish_variance_days"),
        "previous_finish_variance_days": ctx.get("previous_finish_variance_days"),
        # Sections
        "executive_summary": _gen_executive_summary(ctx, settings),
        "current_position": _gen_current_position(ctx),
        "critical_path_narrative": _gen_critical_path(ctx),
        "negative_float_narrative": _gen_negative_float(ctx),
        "progress_narrative": _gen_progress(ctx),
        "quality_narrative": _gen_quality(ctx),
        "trend_narrative": _gen_trend(ctx),
        # Findings and recommendations
        "findings": findings,
        "recommendations": recommendations,
        "conclusion": _gen_conclusion(ctx),
        "limitations": ctx.get("limitations", []),
        # Supporting data for frontend tables and appendix
        "summary_stats": {
            "total": ctx["total"],
            "total_non_ms": ctx["total_non_ms"],
            "complete_count": ctx["complete_count"],
            "in_progress_count": ctx["in_progress_count"],
            "not_started_count": ctx["not_started_count"],
            "milestone_count": ctx["milestone_count"],
            "critical_count": ctx["critical_count"],
            "critical_pct": ctx["critical_pct"],
            "near_critical_count": ctx["near_critical_count"],
            "neg_float_count": ctx["neg_float_count"],
            "min_float": ctx.get("min_float"),
            "overdue_count": ctx["overdue_count"],
            "overdue_pct_of_incomplete": ctx["overdue_pct_of_incomplete"],
            "bei": ctx.get("bei"),
            "bei_valid": ctx.get("bei_valid", False),
            "bei_planned_count": ctx.get("bei_planned_count", 0),
            "bei_actual_count": ctx.get("bei_actual_count", 0),
            "missed_start_count": ctx.get("missed_start_count", 0),
            "missed_finish_count": ctx.get("missed_finish_count", 0),
            "quality_score": ctx.get("quality_score"),
        },
        "worst_neg_float_acts": ctx.get("worst_neg_float_acts", []),
        "neg_float_by_wbs": ctx.get("neg_float_by_wbs", []),
        "top_wbs_areas": ctx.get("top_wbs_areas", []),
        "engine_version": ENGINE_VERSION,
        "settings_used": settings,
    }

    return result
