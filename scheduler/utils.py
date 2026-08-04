import io
import json
import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

DATE_FIELDS = [
    "start",
    "finish",
    "baseline_start",
    "baseline_finish",
    "bstart",
    "bfinish",
    "planned_start",
    "planned_finish",
    "actual_start",
    "actual_finish",
]

STATUS_CANDIDATES = [
    "status",
    "task_status",
    "activity_status",
    "status_id",
]

CODE_FIELDS = ["code", "task_id", "activity_id", "id", "unique_id"]
NAME_FIELDS = [
    "name",
    "activity_name",
    "task_name",
    "description",
    "activity",
]
PROJECT_FIELDS = ["project_name", "project", "proj_name", "portfolio"]
WBS_FIELDS = ["wbs", "wbs_name", "work_breakdown_structure"]
DUR_FIELDS = ["duration", "dur", "duration_days"]
REMAIN_FIELDS = ["remaining_duration", "remain_dur", "remain", "remaining"]
PCT_FIELDS = [
    "pct_complete",
    "percent_complete",
    "percent done",
    "%_complete",
    "%complete",
]
FLOAT_FIELDS = ["total_float", "float", "free_float", "remaining_float"]
COST_FIELDS = ["cost", "planned_cost", "budget"]
TYPE_FIELDS = ["type", "activity_type", "task_type"]
MILESTONE_FIELDS = ["milestone", "is_milestone", "type"]


def normalize_key(value):
    if value is None:
        return None
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower())


def parse_date(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, (datetime, date)):
        return value.date() if isinstance(value, datetime) else value
    value = str(value).strip()
    if not value:
        return None
    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y", "%Y.%m.%d"]:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    try:
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()
    except Exception:
        return None


def first_match(row, keys):
    for key in keys:
        if key in row and pd.notna(row[key]):
            candidate = row[key]
            if isinstance(candidate, str) and candidate.strip() == "":
                continue
            return candidate
    return None


def parse_xer(text: str) -> pd.DataFrame:
    project_name = "XER Project"
    headers = {}
    rows = []
    current_project = {}
    for line in text.splitlines():
        line = line.strip("\r")
        if not line or line.startswith("\""):
            continue
        parts = line.split("\t")
        if not parts:
            continue
        record_type = parts[0].upper()
        if record_type in ("PROJECT", "TASK", "WBS"):
            if record_type not in headers:
                headers[record_type] = [normalize_key(p) for p in parts]
                continue
            header = headers[record_type]
            row = {header[i]: parts[i] for i in range(min(len(header), len(parts)))}
            if record_type == "PROJECT":
                project_name = row.get("project_name") or row.get("name") or project_name
                current_project = row
            if record_type == "TASK":
                row["project_name"] = project_name
                rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def load_table_from_excel(file_obj, engine="openpyxl"):
    return pd.read_excel(file_obj, engine=engine)


def load_table_from_csv(file_obj):
    return pd.read_csv(file_obj)


def normalize_activities(df: pd.DataFrame, source_file: str, source_label: str):
    activities = []
    df = df.rename(columns={normalize_key(c): c for c in df.columns})
    for index, row in df.iterrows():
        row = {normalize_key(k): v for k, v in row.items()}
        code = first_match(row, CODE_FIELDS) or f"A{index + 1:04d}"
        name = first_match(row, NAME_FIELDS) or f"Activity {index + 1}"
        project_name = first_match(row, PROJECT_FIELDS) or source_label or source_file
        project_id = normalize_key(project_name) or f"proj-{index + 1:03d}"
        wbs = first_match(row, WBS_FIELDS) or "General"
        start = parse_date(first_match(row, ["baseline_start", "bstart", "planned_start", "start", "actual_start"]))
        finish = parse_date(first_match(row, ["baseline_finish", "bfinish", "planned_finish", "finish", "actual_finish"]))
        bstart = parse_date(first_match(row, ["baseline_start", "bstart", "planned_start"]))
        bfinish = parse_date(first_match(row, ["baseline_finish", "bfinish", "planned_finish"]))
        dur = first_match(row, DUR_FIELDS)
        dur = float(dur) if pd.notna(dur) and str(dur).strip() != "" else None
        pct_complete = first_match(row, PCT_FIELDS)
        pct_complete = float(pct_complete) if pd.notna(pct_complete) and str(pct_complete).strip() != "" else None
        total_float = first_match(row, FLOAT_FIELDS)
        total_float = float(total_float) if pd.notna(total_float) and str(total_float).strip() != "" else None
        remain = first_match(row, REMAIN_FIELDS)
        remain = float(remain) if pd.notna(remain) and str(remain).strip() != "" else None
        cost = first_match(row, COST_FIELDS)
        cost = float(cost) if pd.notna(cost) and str(cost).strip() != "" else 0.0
        type_value = first_match(row, TYPE_FIELDS) or "Task"
        status = first_match(row, STATUS_CANDIDATES) or "TK_Active"

        if dur is None and bstart and bfinish:
            dur = max(0.0, float((bfinish - bstart).days))
        if dur is None and start and finish:
            dur = max(0.0, float((finish - start).days))
        if pct_complete is None:
            if status and str(status).lower().startswith("tk_complete"):
                pct_complete = 100.0
            else:
                pct_complete = 0.0
        if total_float is None:
            total_float = -1.0 if pct_complete < 100 and (start or finish) else 0.0
        if remain is None:
            remain = max(0.0, dur * (1 - pct_complete / 100.0)) if dur is not None else 0.0
        if start and not bstart:
            bstart = start
        if finish and not bfinish:
            bfinish = finish

        is_milestone = bool(
            first_match(row, MILESTONE_FIELDS) in (1, "1", "true", "True", True)
            or (dur == 0)
            or "mile" in str(type_value).lower()
        )
        if isinstance(status, str) and pct_complete >= 100:
            status = "TK_Complete"

        activities.append(
            {
                "id": f"{project_id}-{code}",
                "code": str(code),
                "name": str(name),
                "projectId": str(project_id),
                "projectName": str(project_name),
                "sourceFile": str(source_file),
                "wbs": str(wbs),
                "type": str(type_value),
                "status": str(status),
                "start": start.isoformat() if start else None,
                "finish": finish.isoformat() if finish else None,
                "bStart": bstart.isoformat() if bstart else None,
                "bFinish": bfinish.isoformat() if bfinish else None,
                "dur": float(dur or 0.0),
                "remainDur": float(remain or 0.0),
                "totalFloat": float(total_float or 0.0),
                "pctComplete": float(min(max(pct_complete or 0.0, 0.0), 100.0)),
                "cost": float(cost or 0.0),
                "isCritical": bool(total_float is not None and total_float <= 0 and not is_milestone),
                "isMilestone": bool(is_milestone),
            }
        )
    return activities


def ensure_list(value):
    if isinstance(value, list):
        return value
    return [value] if value is not None else []


def month_label(dt: date):
    return dt.strftime("%b %y")


def bucket_counts(values, buckets, colors=None):
    out = []
    for label, low, high, color in buckets:
        count = sum(1 for v in values if v is not None and low <= v <= high)
        out.append({"range": label, "count": count, "fill": color})
    return out


def project_health(value):
    if value >= 90:
        return {"label": "Excellent", "color": "#22c55e"}
    if value >= 70:
        return {"label": "Good", "color": "#f59e0b"}
    return {"label": "At Risk", "color": "#ef4444"}


def _planned_pct(activity, today) -> float:
    """Fraction of baseline duration elapsed as of today (0.0–1.0)."""
    bs = activity.get("bStart")
    bf = activity.get("bFinish")
    if not bs or not bf:
        return 0.0
    if today >= bf:
        return 1.0
    if today <= bs:
        return 0.0
    total_days = max(1, (bf - bs).days)
    return min(1.0, max(0.0, (today - bs).days / total_days))


def _proj_finish(activity, today: date) -> Optional[date]:
    """
    P6-compliant projected finish:
      - Complete      → actual finish (locked)
      - In-progress   → Data Date + remaining duration (work projects from DD)
      - Not-started   → max(baseline start, Data Date) + original duration
                        (cannot start before Data Date)
    """
    pct = activity.get("pctComplete", 0.0) or 0.0
    if pct >= 100:
        return activity.get("finish")
    remain = int(round(activity.get("remainDur") or activity.get("dur") or 0))
    dur    = int(round(activity.get("dur") or 0))
    b_start = activity.get("bStart")
    if activity.get("start"):          # in-progress
        return today + timedelta(days=remain)
    else:                               # not-started: cannot forecast before DD
        proj_start = max(b_start, today) if b_start else today
        return proj_start + timedelta(days=dur)


def _proj_start(activity, today: date) -> Optional[date]:
    """
    P6-compliant projected start:
      - Complete / in-progress → actual start (locked)
      - Not-started            → max(baseline start, Data Date)
    """
    if activity.get("start"):
        return activity.get("start") if isinstance(activity.get("start"), date) else None
    b_start = activity.get("bStart")
    if b_start:
        return max(b_start, today)
    return today


def _finish_variance(activity, today: date) -> Optional[int]:
    """Finish variance in calendar days (positive = late). Uses P6-projected finish."""
    bf = activity.get("bFinish")
    if not bf:
        return None
    pf = _proj_finish(activity, today)
    return (pf - bf).days if pf else None


def _start_variance(activity, today: date = None) -> Optional[int]:
    """Start variance in calendar days (positive = late)."""
    bs = activity.get("bStart")
    if not bs:
        return None
    pct = activity.get("pctComplete", 0.0) or 0.0
    if pct >= 100 or activity.get("start"):
        as_ = activity.get("start")
        return (as_ - bs).days if as_ else None
    # Not-started: start slip = max(0, Data Date - bStart)
    if today:
        return max(0, (today - bs).days)
    return None


def compute_metrics(activities, data_date=None):
    today = data_date if isinstance(data_date, date) else date.today()
    if not activities:
        return {
            "total": 0,
            "completed": 0,
            "inProgress": 0,
            "notStarted": 0,
            "critical": 0,
            "overdue": 0,
            "milestones": 0,
            "schedPct": 0.0,
            "BEI": 0.0,
            "nearCritical": 0,
            "projects": [],
            "statusPie": [],
            "criticalPie": [],
            "monthlyTrend": [],
            "sCurve": [],
            "floatHist": [],
            "durHist": [],
            "pctHist": [],
            "wbsDist": [],
            "fb": {"negative": 0, "zero": 0, "medium": 0, "high": 0},
            "negFloatActs": [],
            "criticalActs": [],
            "dataDate": today.isoformat(),
        }

    for activity in activities:
        if isinstance(activity.get("start"), str):
            activity["start"] = parse_date(activity["start"])
        if isinstance(activity.get("finish"), str):
            activity["finish"] = parse_date(activity["finish"])
        if isinstance(activity.get("bStart"), str):
            activity["bStart"] = parse_date(activity["bStart"])
        if isinstance(activity.get("bFinish"), str):
            activity["bFinish"] = parse_date(activity["bFinish"])

    projects = {}
    for activity in activities:
        pid = activity.get("projectId") or "unknown"
        if pid not in projects:
            projects[pid] = {
                "id": pid, "name": activity.get("projectName") or pid,
                "count": 0, "complete": 0, "inProgress": 0, "notStarted": 0,
                "critical": 0, "overdue": 0, "negFloat": 0, "nearCritical": 0,
                "duration": 0.0, "earned": 0.0, "planned": 0.0,
                # EVM
                "BAC": 0.0, "BCWP": 0.0, "BCWS": 0.0, "ACWP": 0.0,
                # Man-hours
                "bgtHrs": 0.0, "actHrs": 0.0, "remHrs": 0.0, "ernHrs": 0.0,
                "activities": [],
            }
        project = projects[pid]
        project["count"] += 1
        project["activities"].append(activity)
        pct_complete = activity.get("pctComplete", 0.0) or 0.0
        dur = activity.get("dur", 0.0) or 0.0
        project["earned"]  += dur * pct_complete / 100.0
        project["planned"] += dur
        if pct_complete >= 100:
            project["complete"] += 1
        elif activity.get("start") is None:
            project["notStarted"] += 1
        else:
            project["inProgress"] += 1
        if activity.get("isCritical"):
            project["critical"] += 1
        if (activity.get("totalFloat") or 0.0) < 0:
            project["negFloat"] += 1
        if 1 <= (activity.get("totalFloat") or 0.0) <= 5:
            project["nearCritical"] += 1
        finish = activity.get("bFinish") or activity.get("finish")
        if finish and finish < today and pct_complete < 100 and not activity.get("isMilestone"):
            project["overdue"] += 1
        # EVM rollup
        bac = activity.get("budgetedCost", 0.0) or 0.0
        project["BAC"]  += bac
        project["BCWP"] += bac * pct_complete / 100.0
        project["BCWS"] += bac * _planned_pct(activity, today)
        project["ACWP"] += activity.get("actualCost", 0.0) or 0.0
        # Man-hours rollup
        project["bgtHrs"] += activity.get("budgetedHours",  0.0) or 0.0
        project["actHrs"] += activity.get("actualHours",    0.0) or 0.0
        project["remHrs"] += activity.get("remainingHours", 0.0) or 0.0
        project["ernHrs"] += (activity.get("budgetedHours", 0.0) or 0.0) * pct_complete / 100.0

    portfolio_total = len(activities)
    portfolio_complete = sum(1 for a in activities if (a.get("pctComplete") or 0.0) >= 100)
    portfolio_not_started = sum(1 for a in activities if a.get("start") is None)
    portfolio_in_progress = portfolio_total - portfolio_complete - portfolio_not_started
    portfolio_critical = sum(1 for a in activities if a.get("isCritical") and not a.get("isMilestone"))
    portfolio_overdue = sum(
        1
        for a in activities
        if (a.get("bFinish") or a.get("finish")) and (a.get("bFinish") or a.get("finish")) < today and (a.get("pctComplete") or 0.0) < 100 and not a.get("isMilestone")
    )
    portfolio_milestones = sum(1 for a in activities if a.get("isMilestone"))

    total_duration = sum((a.get("dur") or 0.0) for a in activities)
    total_earned = sum((a.get("dur") or 0.0) * ((a.get("pctComplete") or 0.0) / 100.0) for a in activities)
    sched_pct = (total_earned / total_duration * 100.0) if total_duration > 0 else 0.0
    BEI = (total_earned / total_duration) if total_duration > 0 else 0.0

    status_pie = [
        {"name": "Complete", "value": portfolio_complete, "fill": "#22c55e"},
        {"name": "Active", "value": portfolio_in_progress, "fill": "#38bdf8"},
        {"name": "Not Started", "value": portfolio_not_started, "fill": "#60778f"},
    ]
    critical_pie = [
        {"name": "Critical", "value": portfolio_critical, "fill": "#fb923c"},
        {"name": "Non-critical", "value": portfolio_total - portfolio_critical, "fill": "#22c55e"},
    ]

    months = {}
    for activity in activities:
        date_key = activity.get("bFinish") or activity.get("finish") or activity.get("bStart") or activity.get("start")
        if not date_key:
            continue
        key = (date_key.year, date_key.month)
        months.setdefault(key, {"month": month_label(date_key), "complete": 0, "inProgress": 0, "notStarted": 0})
        if (activity.get("pctComplete") or 0.0) >= 100:
            months[key]["complete"] += 1
        elif activity.get("start") is None:
            months[key]["notStarted"] += 1
        else:
            months[key]["inProgress"] += 1
    monthly_trend = [months[k] for k in sorted(months.keys())]

    s_curve = []
    monthly_planned = {}
    monthly_actual = {}
    for activity in activities:
        date_key = activity.get("bFinish") or activity.get("finish")
        if not date_key:
            continue
        key = (date_key.year, date_key.month)
        monthly_planned.setdefault(key, 0.0)
        monthly_actual.setdefault(key, 0.0)
        monthly_planned[key] += (activity.get("dur") or 0.0)
        if (activity.get("pctComplete") or 0.0) >= 100:
            monthly_actual[key] += (activity.get("dur") or 0.0)
    cumulative_planned = 0.0
    cumulative_actual = 0.0
    sorted_keys = sorted(set(list(monthly_planned.keys()) + list(monthly_actual.keys())))
    for key in sorted_keys:
        cumulative_planned += monthly_planned.get(key, 0.0)
        cumulative_actual += monthly_actual.get(key, 0.0)
        total_sum = total_duration or 1.0
        s_curve.append(
            {
                "month": month_label(date(key[0], key[1], 1)),
                "pctPlanned": min(100.0, cumulative_planned / total_sum * 100.0),
                "pctActual": min(100.0, cumulative_actual / total_sum * 100.0),
            }
        )

    float_hist = bucket_counts(
        [(a.get("totalFloat") or 0.0) for a in activities],
        [
            ("Neg float", -9999, -1, "#ef4444"),
            ("0-5d", 0, 5, "#f59e0b"),
            ("6-15d", 6, 15, "#22c55e"),
            ("16+d", 16, 9999, "#38bdf8"),
        ],
    )
    dur_hist = bucket_counts(
        [(a.get("dur") or 0.0) for a in activities],
        [
            ("0-5d", 0, 5, "#38bdf8"),
            ("6-10d", 6, 10, "#22c55e"),
            ("11-20d", 11, 20, "#f59e0b"),
            ("21-40d", 21, 40, "#fb923c"),
            ("41+d", 41, 9999, "#a855f7"),
        ],
    )
    pct_hist = bucket_counts(
        [(a.get("pctComplete") or 0.0) for a in activities],
        [
            ("0-20%", 0, 20, "#ef4444"),
            ("21-40%", 21, 40, "#fb923c"),
            ("41-60%", 41, 60, "#f59e0b"),
            ("61-80%", 61, 80, "#22c55e"),
            ("81-100%", 81, 100, "#38bdf8"),
        ],
    )

    wbs_counts = {}
    for activity in activities:
        wbs = activity.get("wbs") or "Unassigned"
        wbs_counts[wbs] = wbs_counts.get(wbs, 0) + 1
    wbs_dist = [{"name": k, "count": v} for k, v in sorted(wbs_counts.items(), key=lambda x: x[1], reverse=True)[:10]]

    fb = {
        "negative": sum(1 for a in activities if (a.get("totalFloat") or 0.0) < 0),
        "zero": sum(1 for a in activities if (a.get("totalFloat") or 0.0) == 0),
        "medium": sum(1 for a in activities if 1 <= (a.get("totalFloat") or 0.0) <= 15),
        "high": sum(1 for a in activities if (a.get("totalFloat") or 0.0) > 15),
    }

    neg_float_acts = sorted(
        [a for a in activities if (a.get("totalFloat") or 0.0) < 0],
        key=lambda x: (x.get("totalFloat") or 0.0)
    )[:20]

    # ALL critical activities sorted most-critical → least-critical (most negative float first)
    critical_acts = sorted(
        [a for a in activities if a.get("isCritical") and not a.get("isMilestone")],
        key=lambda x: x.get("totalFloat", 0.0)   # ascending: -10 before -5 before 0
    )

    # ── Portfolio-level EVM ──────────────────────────────────────────────────
    total_BAC  = sum(a.get("budgetedCost", 0.0) or 0.0 for a in activities)
    total_BCWP = sum((a.get("budgetedCost", 0.0) or 0.0) * (a.get("pctComplete", 0.0) or 0.0) / 100.0 for a in activities)
    total_BCWS = sum((a.get("budgetedCost", 0.0) or 0.0) * _planned_pct(a, today) for a in activities)
    total_ACWP = sum(a.get("actualCost",   0.0) or 0.0 for a in activities)

    # Fall back to duration-based EVM when no cost data is loaded
    is_cost_loaded = total_BAC > 0
    if not is_cost_loaded:
        total_BAC  = total_duration
        total_BCWP = total_earned
        total_BCWS = sum((a.get("dur") or 0.0) * _planned_pct(a, today) for a in activities)
        total_ACWP = 0.0   # no actual cost available

    SPI  = round(total_BCWP / total_BCWS,  4) if total_BCWS  > 0 else 1.0
    CPI  = round(total_BCWP / total_ACWP,  4) if total_ACWP  > 0 else 1.0
    CV   = round(total_BCWP - total_ACWP,  2)
    SV   = round(total_BCWP - total_BCWS,  2)
    EAC  = round(total_ACWP + (total_BAC - total_BCWP) / CPI, 2) if (CPI > 0 and is_cost_loaded) else round(total_BAC, 2)
    ETC  = round(EAC - total_ACWP, 2)
    VAC  = round(total_BAC - EAC,  2)
    TCPI = round((total_BAC - total_BCWP) / (total_BAC - total_ACWP), 4) \
           if (total_BAC - total_ACWP) > 0 else 1.0

    # Monthly EVM curve (cumulative BCWS, BCWP, ACWP)
    evm_monthly: Dict[tuple, dict] = {}
    for a in activities:
        bf = a.get("bFinish") or a.get("finish")
        if not bf:
            continue
        key = (bf.year, bf.month)
        bac_a  = (a.get("budgetedCost", 0.0) or 0.0) if is_cost_loaded else (a.get("dur", 0.0) or 0.0)
        bcwp_a = bac_a * (a.get("pctComplete", 0.0) or 0.0) / 100.0
        acwp_a = (a.get("actualCost",  0.0) or 0.0) if is_cost_loaded else 0.0
        if key not in evm_monthly:
            evm_monthly[key] = {"bcws": 0.0, "bcwp": 0.0, "acwp": 0.0}
        evm_monthly[key]["bcws"] += bac_a          # planned: full BAC by bFinish
        evm_monthly[key]["bcwp"] += bcwp_a
        evm_monthly[key]["acwp"] += acwp_a

    evm_curve = []
    cum = {"bcws": 0.0, "bcwp": 0.0, "acwp": 0.0}
    for key in sorted(evm_monthly):
        for k in cum:
            cum[k] += evm_monthly[key][k]
        denom = total_BAC or 1.0
        evm_curve.append({
            "month":    month_label(date(key[0], key[1], 1)),
            "BCWS":     round(cum["bcws"], 2),
            "BCWP":     round(cum["bcwp"], 2),
            "ACWP":     round(cum["acwp"], 2),
            "pctBCWS":  round(cum["bcws"] / denom * 100, 1),
            "pctBCWP":  round(cum["bcwp"] / denom * 100, 1),
            "pctACWP":  round(cum["acwp"] / denom * 100, 1) if is_cost_loaded else 0.0,
        })

    # ── Portfolio-level Man-hours ────────────────────────────────────────────
    total_bgt_hrs = sum(a.get("budgetedHours",  0.0) or 0.0 for a in activities)
    total_act_hrs = sum(a.get("actualHours",    0.0) or 0.0 for a in activities)
    total_rem_hrs = sum(a.get("remainingHours", 0.0) or 0.0 for a in activities)
    total_ern_hrs = sum((a.get("budgetedHours", 0.0) or 0.0) * (a.get("pctComplete", 0.0) or 0.0) / 100.0 for a in activities)
    is_hr_loaded  = total_bgt_hrs > 0
    # EVM data-quality classification from per-activity loading flags
    _non_ms_cnt   = len([a for a in activities if not a.get("isMilestone")]) or 1
    _rsrc_n       = sum(1 for a in activities if a.get("isResourceLoaded") and not a.get("isMilestone"))
    _cost_n       = sum(1 for a in activities if a.get("isCostLoaded")     and not a.get("isMilestone"))
    resource_load_pct = round(_rsrc_n / _non_ms_cnt * 100)
    cost_load_pct     = round(_cost_n / _non_ms_cnt * 100)
    is_resource_loaded_file = resource_load_pct >= 50
    evm_data_quality  = (
        "cost_loaded"     if is_cost_loaded and cost_load_pct     >= 50 else
        "resource_loaded" if is_hr_loaded   and resource_load_pct >= 50 else
        "duration_only"
    )
    mh_pct        = round(total_ern_hrs / total_bgt_hrs * 100.0, 1) if total_bgt_hrs > 0 else 0.0
    mh_CPI        = round(total_ern_hrs / total_act_hrs, 4) if total_act_hrs > 0 else 1.0
    mh_SPI        = round(total_ern_hrs / (sum((a.get("budgetedHours", 0.0) or 0.0) * _planned_pct(a, today) for a in activities) or 1.0), 4)
    mh_EAC        = round(total_bgt_hrs / mh_CPI, 1) if (mh_CPI > 0 and is_hr_loaded) else total_bgt_hrs

    # MH by WBS
    mh_wbs: Dict[str, dict] = {}
    for a in activities:
        w = a.get("wbs") or "Unassigned"
        if w not in mh_wbs:
            mh_wbs[w] = {"wbs": w, "budgeted": 0.0, "actual": 0.0, "remaining": 0.0, "earned": 0.0}
        mh_wbs[w]["budgeted"]  += a.get("budgetedHours",  0.0) or 0.0
        mh_wbs[w]["actual"]    += a.get("actualHours",    0.0) or 0.0
        mh_wbs[w]["remaining"] += a.get("remainingHours", 0.0) or 0.0
        mh_wbs[w]["earned"]    += (a.get("budgetedHours", 0.0) or 0.0) * (a.get("pctComplete", 0.0) or 0.0) / 100.0
    mh_by_wbs = sorted(mh_wbs.values(), key=lambda x: x["budgeted"], reverse=True)[:12]

    # MH histogram — activities by budgeted hours band
    mh_hist_values = [
        a.get("budgetedHours", 0.0) or 0.0
        for a in activities
        if not a.get("isMilestone") and (a.get("budgetedHours") or 0.0) > 0
    ]
    mh_hist = bucket_counts(
        mh_hist_values,
        [
            ("< 4 h",    0,     3.9,  "#6b7280"),
            ("4 – 8 h",  4,     7.9,  "#38bdf8"),
            ("8 – 40 h", 8,    39.9,  "#00c8f0"),
            ("40 – 200 h", 40, 199.9, "#00e5a0"),
            ("> 200 h",  200,  9999,  "#c084fc"),
        ],
    )

    # MH cumulative S-curve (monthly)
    _mh_monthly: Dict[tuple, dict] = {}
    for a in activities:
        dt = a.get("bFinish") or a.get("finish")
        if not dt:
            continue
        key = (dt.year, dt.month)
        bgt = a.get("budgetedHours", 0.0) or 0.0
        act = a.get("actualHours",   0.0) or 0.0
        ern = bgt * (a.get("pctComplete", 0.0) or 0.0) / 100.0
        if key not in _mh_monthly:
            _mh_monthly[key] = {"budgeted": 0.0, "actual": 0.0, "earned": 0.0}
        _mh_monthly[key]["budgeted"] += bgt
        _mh_monthly[key]["actual"]   += act
        _mh_monthly[key]["earned"]   += ern
    mh_curve = []
    _mh_cum = {"budgeted": 0.0, "actual": 0.0, "earned": 0.0}
    _mh_denom = total_bgt_hrs or 1.0
    for key in sorted(_mh_monthly):
        for k in _mh_cum:
            _mh_cum[k] += _mh_monthly[key][k]
        mh_curve.append({
            "month":      month_label(date(key[0], key[1], 1)),
            "budgeted":   round(_mh_cum["budgeted"],  1),
            "actual":     round(_mh_cum["actual"],    1),
            "earned":     round(_mh_cum["earned"],    1),
            "pctBudgeted":round(_mh_cum["budgeted"] / _mh_denom * 100, 1),
            "pctActual":  round(_mh_cum["actual"]   / _mh_denom * 100, 1),
            "pctEarned":  round(_mh_cum["earned"]   / _mh_denom * 100, 1),
        })

    # MH by resource type (from primaryResourceType field)
    _type_label = {
        "RT_Labor":    "Labour",
        "RT_Nonlabor": "Non-Labour",
        "RT_Mat":      "Material",
        "RT_Crew":     "Crew",
    }
    _mh_type: Dict[str, float] = {}
    for a in activities:
        if a.get("isMilestone"):
            continue
        bgt = a.get("budgetedHours", 0.0) or 0.0
        if bgt <= 0:
            continue
        rt    = a.get("primaryResourceType") or ""
        label = _type_label.get(rt, rt or "Unassigned")
        _mh_type[label] = _mh_type.get(label, 0.0) + bgt
    mh_by_type = [
        {"type": k, "hours": round(v, 1)}
        for k, v in sorted(_mh_type.items(), key=lambda x: -x[1])
    ]

    # ── Variance statistics ──────────────────────────────────────────────────
    # Attach projectedFinish / adjustedFloat to every activity (in-place) so
    # downstream serialisation and the frontend can use them directly.
    for a in activities:
        pf = _proj_finish(a, today)
        a["projectedFinish"] = pf.isoformat() if pf else None
        bf = a.get("bFinish")
        a["finishVariance"] = (pf - bf).days if (pf and bf) else None
        a["startVariance"]  = _start_variance(a, today)
        # Adjusted float: late finish (bFinish + originalFloat) - projectedFinish
        orig_float = a.get("totalFloat")
        if orig_float is not None and bf and pf:
            late_finish = bf + timedelta(days=int(round(orig_float)))
            a["adjustedFloat"] = (late_finish - pf).days
        else:
            a["adjustedFloat"] = orig_float

    fin_vars = [a["finishVariance"] for a in activities
                if not a.get("isMilestone") and a["finishVariance"] is not None]
    start_vars = [a["startVariance"] for a in activities
                  if not a.get("isMilestone") and a["startVariance"] is not None]

    var_hist = bucket_counts(
        fin_vars,
        [
            ("< -14d",  -9999, -15, "#22c55e"),
            ("-14 to -1d", -14, -1, "#86efac"),
            ("On Time ±0", 0, 0, "#38bdf8"),
            ("+1 to +7d",  1,  7, "#f59e0b"),
            ("+8 to +14d", 8, 14, "#fb923c"),
            ("+15 to +30d",15, 30, "#ef4444"),
            ("> +30d",    31, 9999, "#9f1239"),
        ],
    )

    # Top delayed and most-advanced activities (exclude milestones)
    delayed_acts = sorted(
        [a for a in activities if not a.get("isMilestone") and (a.get("finishVariance") or 0) > 0],
        key=lambda a: a.get("finishVariance") or 0, reverse=True
    )[:20]
    advanced_acts = sorted(
        [a for a in activities if not a.get("isMilestone") and (a.get("finishVariance") or 0) < 0],
        key=lambda a: a.get("finishVariance") or 0
    )[:10]

    # Variance by WBS (use pre-computed finishVariance attached above)
    var_wbs: Dict[str, dict] = {}
    for a in activities:
        if a.get("isMilestone"):
            continue
        w = a.get("wbs") or "Unassigned"
        fv = a.get("finishVariance")
        if w not in var_wbs:
            var_wbs[w] = {"wbs": w, "count": 0, "sumFV": 0, "delayed": 0, "onTime": 0, "advanced": 0}
        var_wbs[w]["count"] += 1
        if fv is not None:
            var_wbs[w]["sumFV"] += fv
            if fv > 0: var_wbs[w]["delayed"] += 1
            elif fv < 0: var_wbs[w]["advanced"] += 1
            else: var_wbs[w]["onTime"] += 1
    var_by_wbs = sorted(
        [dict(x, avgFV=round(x["sumFV"]/x["count"], 1) if x["count"] else 0) for x in var_wbs.values()],
        key=lambda x: x["delayed"], reverse=True
    )[:12]

    # ── Project list (extended with EVM) ────────────────────────────────────
    project_list = []
    for project_id, project in projects.items():
        p_spi  = round(project["BCWP"] / project["BCWS"],  4) if project["BCWS"]  > 0 else 1.0
        p_cpi  = round(project["BCWP"] / project["ACWP"],  4) if project["ACWP"]  > 0 else 1.0
        p_sv   = round(project["BCWP"] - project["BCWS"],  2)
        p_cv   = round(project["BCWP"] - project["ACWP"],  2)
        p_eac  = round(project["ACWP"] + (project["BAC"] - project["BCWP"]) / p_cpi, 2) \
                 if (p_cpi > 0 and is_cost_loaded) else round(project["BAC"], 2)
        p_vac  = round(project["BAC"] - p_eac, 2)
        p_mh_pct = round(project["ernHrs"] / project["bgtHrs"] * 100.0, 1) \
                   if project["bgtHrs"] > 0 else 0.0
        project_list.append({
            "id": project_id,
            "name": project["name"],
            "total": project["count"],
            "complete": project["complete"],
            "inProgress": project["inProgress"],
            "notStarted": project["notStarted"],
            "critical": project["critical"],
            "nearCritical": project["nearCritical"],
            "overdue": project["overdue"],
            "negFloat": project["negFloat"],
            "pctComplete": min(100.0, project["earned"] / project["planned"] * 100.0) if project["planned"] > 0 else 0.0,
            "BEI": project["earned"] / project["planned"] if project["planned"] > 0 else 0.0,
            "health": project_health(project["earned"] / project["planned"] * 100.0 if project["planned"] > 0 else 0.0),
            # EVM
            "BAC": round(project["BAC"], 2),
            "BCWP": round(project["BCWP"], 2),
            "BCWS": round(project["BCWS"], 2),
            "ACWP": round(project["ACWP"], 2),
            "SPI": p_spi, "CPI": p_cpi, "SV": p_sv, "CV": p_cv,
            "EAC": p_eac, "VAC": p_vac,
            # Man-hours
            "bgtHrs": round(project["bgtHrs"], 1),
            "actHrs": round(project["actHrs"], 1),
            "remHrs": round(project["remHrs"], 1),
            "ernHrs": round(project["ernHrs"], 1),
            "mhPct": p_mh_pct,
        })

    return {
        "total": portfolio_total,
        "completed": portfolio_complete,
        "inProgress": portfolio_in_progress,
        "notStarted": portfolio_not_started,
        "critical": portfolio_critical,
        "overdue": portfolio_overdue,
        "milestones": portfolio_milestones,
        "schedPct": round(sched_pct, 1),
        "BEI": round(BEI, 2),
        "nearCritical": sum(1 for a in activities if 1 <= (a.get("totalFloat") or 0.0) <= 5),
        "projects": project_list,
        "statusPie": status_pie,
        "criticalPie": critical_pie,
        "monthlyTrend": monthly_trend,
        "sCurve": s_curve,
        "floatHist": float_hist,
        "durHist": dur_hist,
        "pctHist": pct_hist,
        "wbsDist": wbs_dist,
        "fb": fb,
        "negFloatActs": neg_float_acts,
        "criticalActs": critical_acts,
        "dataDate": today.isoformat(),
        # EVM
        "isCostLoaded":       is_cost_loaded,
        "isHrLoaded":         is_hr_loaded,
        "isResourceLoaded":   is_resource_loaded_file,
        "resourceLoadPct":    resource_load_pct,
        "costLoadPct":        cost_load_pct,
        "evmDataQuality":     evm_data_quality,   # "cost_loaded" | "resource_loaded" | "duration_only"
        "evm": {
            "BAC": round(total_BAC, 2), "BCWP": round(total_BCWP, 2),
            "BCWS": round(total_BCWS, 2), "ACWP": round(total_ACWP, 2),
            "SPI": SPI, "CPI": CPI, "CV": CV, "SV": SV,
            "EAC": EAC, "ETC": ETC, "VAC": VAC, "TCPI": TCPI,
            "percentSpent": round(total_ACWP / total_BAC * 100, 1) if total_BAC > 0 else 0.0,
            "dataQuality": evm_data_quality,
        },
        "evmCurve": evm_curve,
        "manHours": {
            "budgeted": round(total_bgt_hrs, 1), "actual": round(total_act_hrs, 1),
            "remaining": round(total_rem_hrs, 1), "earned": round(total_ern_hrs, 1),
            "pctComplete": mh_pct, "CPI": mh_CPI, "SPI": mh_SPI, "EAC": mh_EAC,
            "byWBS": mh_by_wbs,
            "curve":  mh_curve,
            "hist":   mh_hist,
            "byType": mh_by_type,
        },
        # Variance
        "varHist": var_hist,
        "delayedActs": delayed_acts,
        "advancedActs": advanced_acts,
        "varByWBS": var_by_wbs,
        "avgFinishVar": round(sum(fin_vars) / len(fin_vars), 1) if fin_vars else 0.0,
        "avgStartVar":  round(sum(start_vars) / len(start_vars), 1) if start_vars else 0.0,
        "pctDelayed":   round(sum(1 for v in fin_vars if v > 0) / len(fin_vars) * 100, 1) if fin_vars else 0.0,
    }
