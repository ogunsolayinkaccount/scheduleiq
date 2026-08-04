"""
Schedule Status Engine — ScheduleIQ v1.0.0

Classifies a schedule as ON_TRACK / AT_RISK / OFF_TRACK / UNDETERMINED
using CPM principles, milestone governance, progress analysis, and
configurable thresholds. All inputs are plain Python dicts/dates —
no Django models required, making this fully unit-testable in isolation.

Classification precedence (highest wins):
  UNDETERMINED → OFF_TRACK → AT_RISK → ON_TRACK
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

ENGINE_VERSION = '1.0.0'


# ── Reason codes (machine-readable, stable) ───────────────────────────────────

class RC:
    # UNDETERMINED triggers
    NO_DATA_DATE = 'NO_DATA_DATE'
    NO_ACTIVITIES = 'NO_ACTIVITIES'
    CIRCULAR_LOGIC = 'CIRCULAR_LOGIC'
    QUALITY_BELOW_MINIMUM = 'QUALITY_BELOW_MINIMUM'
    NO_APPROVED_BASELINE = 'NO_APPROVED_BASELINE'
    CONFIDENCE_BELOW_MINIMUM = 'CONFIDENCE_BELOW_MINIMUM'
    EXCESSIVE_INCOMPLETE_BEFORE_DD = 'EXCESSIVE_INCOMPLETE_BEFORE_DD'
    CRITICAL_PATH_NOT_CALCULABLE = 'CRITICAL_PATH_NOT_CALCULABLE'

    # OFF_TRACK triggers (red — always override yellow/green)
    CONTRACT_FINISH_EXCEEDED = 'CONTRACT_FINISH_EXCEEDED'
    CONTRACTUAL_MILESTONE_MISSED = 'CONTRACTUAL_MILESTONE_MISSED'
    CONTRACTUAL_MILESTONE_DELAYED = 'CONTRACTUAL_MILESTONE_DELAYED'
    NEGATIVE_FLOAT_BEYOND_THRESHOLD = 'NEGATIVE_FLOAT_BEYOND_THRESHOLD'
    DATA_DATE_PAST_REQUIRED_MILESTONE = 'DATA_DATE_PAST_REQUIRED_MILESTONE'

    # AT_RISK triggers (yellow)
    CRITICAL_FLOAT_BELOW_WARNING = 'CRITICAL_FLOAT_BELOW_WARNING'
    LONGEST_PATH_SLIPPED = 'LONGEST_PATH_SLIPPED'
    MISSED_CRITICAL_STARTS = 'MISSED_CRITICAL_STARTS'
    MISSED_CRITICAL_FINISHES = 'MISSED_CRITICAL_FINISHES'
    NEAR_CRITICAL_PATH_CONVERGENCE = 'NEAR_CRITICAL_PATH_CONVERGENCE'
    FLOAT_CONSUMING_FASTER_THAN_PROGRESS = 'FLOAT_CONSUMING_FASTER_THAN_PROGRESS'
    REMAINING_DURATION_INCREASED = 'REMAINING_DURATION_INCREASED'
    INCOMPLETE_WORK_BEFORE_DATA_DATE = 'INCOMPLETE_WORK_BEFORE_DATA_DATE'
    MILESTONE_YELLOW_VARIANCE = 'MILESTONE_YELLOW_VARIANCE'
    EXCESSIVE_CONSTRAINTS = 'EXCESSIVE_CONSTRAINTS'
    PROGRESS_BEHIND_PLAN = 'PROGRESS_BEHIND_PLAN'
    MULTIPLE_NEAR_CRITICAL_PATHS = 'MULTIPLE_NEAR_CRITICAL_PATHS'
    REMAINING_DURATION_INCREASE_ON_CRITICAL = 'REMAINING_DURATION_INCREASE_ON_CRITICAL'

    # ON_TRACK confirmation failure
    UNCONFIRMED_GREEN_CONDITIONS = 'UNCONFIRMED_GREEN_CONDITIONS'

    _MESSAGES: Dict[str, str] = {}


RC._MESSAGES = {
    RC.NO_DATA_DATE: 'No valid data date was provided. Analysis cannot be performed.',
    RC.NO_ACTIVITIES: 'No activities were found in the uploaded schedule.',
    RC.CIRCULAR_LOGIC: 'Circular logic detected. CPM analysis is not reliable.',
    RC.QUALITY_BELOW_MINIMUM: 'Schedule quality score is below the configured minimum.',
    RC.NO_APPROVED_BASELINE: 'No approved baseline or contractual dates are available.',
    RC.CONFIDENCE_BELOW_MINIMUM: 'Analysis confidence is below the configured minimum.',
    RC.EXCESSIVE_INCOMPLETE_BEFORE_DD: 'Excessive incomplete work exists prior to the data date.',
    RC.CRITICAL_PATH_NOT_CALCULABLE: 'The critical path cannot be reliably calculated from the available data.',
    RC.CONTRACT_FINISH_EXCEEDED: 'Current forecast completion exceeds the contractual completion date beyond tolerance.',
    RC.CONTRACTUAL_MILESTONE_MISSED: 'A contractual milestone date has passed without completion.',
    RC.CONTRACTUAL_MILESTONE_DELAYED: 'A contractual milestone is forecast beyond its approved date.',
    RC.NEGATIVE_FLOAT_BEYOND_THRESHOLD: 'Negative float on the critical path exceeds the configured threshold.',
    RC.DATA_DATE_PAST_REQUIRED_MILESTONE: 'The data date has passed a required milestone date without an actual finish.',
    RC.CRITICAL_FLOAT_BELOW_WARNING: 'Critical-path float is declining and below the warning threshold.',
    RC.LONGEST_PATH_SLIPPED: 'The longest path to project completion slipped since the previous update.',
    RC.MISSED_CRITICAL_STARTS: 'Critical activities have missed their planned start dates.',
    RC.MISSED_CRITICAL_FINISHES: 'Critical activities have missed their planned finish dates.',
    RC.NEAR_CRITICAL_PATH_CONVERGENCE: 'Multiple near-critical paths are converging, increasing combined schedule risk.',
    RC.INCOMPLETE_WORK_BEFORE_DATA_DATE: 'Significant incomplete work exists prior to the data date.',
    RC.MILESTONE_YELLOW_VARIANCE: 'A milestone forecast variance exceeds the yellow threshold.',
    RC.PROGRESS_BEHIND_PLAN: 'Actual progress is behind the approved baseline plan.',
    RC.MULTIPLE_NEAR_CRITICAL_PATHS: 'Three or more near-critical paths increase the probability of overall delay.',
    RC.REMAINING_DURATION_INCREASE_ON_CRITICAL: 'Remaining duration increased on one or more critical-path activities.',
    RC.UNCONFIRMED_GREEN_CONDITIONS: 'Not all ON TRACK conditions could be confirmed.',
}


# ── Threshold data class ──────────────────────────────────────────────────────

@dataclass
class ThresholdValues:
    """
    Flat container for all configurable thresholds.
    Populated from a ThresholdProfile model instance or left at defaults.
    Defaults are labelled as configurable project-management rules,
    not universal facts.
    """
    critical_float_days: float = 0.0
    near_critical_float_days: float = 5.0
    float_warning_days: float = 14.0
    max_negative_float_pct: float = 5.0
    max_high_float_pct: float = 15.0
    milestone_yellow_days: float = 5.0
    milestone_red_days: float = 14.0
    project_finish_yellow_days: float = 14.0
    project_finish_red_days: float = 30.0
    max_open_ends_pct: float = 5.0
    max_constraint_pct: float = 15.0
    max_hard_constraint_pct: float = 5.0
    max_lag_pct: float = 20.0
    max_excessive_lag_pct: float = 5.0
    max_long_duration_pct: float = 5.0
    max_invalid_progress_pct: float = 5.0
    max_missed_task_pct: float = 10.0
    max_forecast_date_change_days: float = 14.0
    max_float_consumption_days: float = 5.0
    max_critical_path_slippage_days: float = 5.0
    min_schedule_quality_score: float = 60.0
    min_completeness_score: float = 70.0
    min_confidence_score: float = 50.0
    weight_milestone: float = 25.0
    weight_project_finish: float = 15.0
    weight_critical_path: float = 20.0
    weight_progress: float = 15.0
    weight_float_health: float = 10.0
    weight_quality: float = 10.0
    weight_trend: float = 5.0

    @classmethod
    def from_model(cls, profile) -> 'ThresholdValues':
        if profile is None:
            return cls()
        fields = [f for f in cls.__dataclass_fields__]
        kwargs = {}
        for f in fields:
            if hasattr(profile, f):
                kwargs[f] = getattr(profile, f)
        return cls(**kwargs)


# ── Milestone input ────────────────────────────────────────────────────────────

@dataclass
class MilestoneInput:
    activity_id: str
    description: str = ''
    category: str = 'INFORMATIONAL'
    contract_required_date: Optional[date] = None
    must_finish_by_date: Optional[date] = None
    allowable_variance_days: float = 0.0
    is_contractual: bool = False


# ── Result ─────────────────────────────────────────────────────────────────────

@dataclass
class StatusResult:
    status: str
    risk_score: float = 0.0
    confidence_score: float = 0.0
    schedule_quality_score: float = 0.0
    primary_reason: str = ''
    reason_codes: List[str] = field(default_factory=list)
    triggered_thresholds: List[Dict] = field(default_factory=list)
    data_limitations: List[str] = field(default_factory=list)
    recommended_actions: List[str] = field(default_factory=list)
    contract_finish_variance_days: Optional[float] = None
    baseline_finish_variance_days: Optional[float] = None
    previous_update_finish_variance_days: Optional[float] = None
    critical_path_float_days: Optional[float] = None
    near_critical_path_count: int = 0
    milestone_results: List[Dict] = field(default_factory=list)
    quality_details: Dict = field(default_factory=dict)
    risk_score_breakdown: Dict = field(default_factory=dict)
    missed_starts_count: int = 0
    missed_finishes_count: int = 0
    critical_activity_count: int = 0
    near_critical_activity_count: int = 0
    incomplete_before_dd_count: int = 0
    project_forecast_finish: Optional[str] = None
    engine_version: str = ENGINE_VERSION

    def to_dict(self) -> Dict:
        d = {
            'status': self.status,
            'risk_score': self.risk_score,
            'confidence_score': self.confidence_score,
            'schedule_quality_score': self.schedule_quality_score,
            'primary_reason': self.primary_reason,
            'reason_codes': self.reason_codes,
            'triggered_thresholds': self.triggered_thresholds,
            'data_limitations': self.data_limitations,
            'recommended_actions': self.recommended_actions,
            'contract_finish_variance_days': self.contract_finish_variance_days,
            'baseline_finish_variance_days': self.baseline_finish_variance_days,
            'previous_update_finish_variance_days': self.previous_update_finish_variance_days,
            'critical_path_float_days': self.critical_path_float_days,
            'near_critical_path_count': self.near_critical_path_count,
            'milestone_results': self.milestone_results,
            'quality_details': self.quality_details,
            'risk_score_breakdown': self.risk_score_breakdown,
            'missed_starts_count': self.missed_starts_count,
            'missed_finishes_count': self.missed_finishes_count,
            'critical_activity_count': self.critical_activity_count,
            'near_critical_activity_count': self.near_critical_activity_count,
            'incomplete_before_dd_count': self.incomplete_before_dd_count,
            'project_forecast_finish': self.project_forecast_finish,
            'engine_version': self.engine_version,
        }
        return d


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _parse_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str) and v:
        s = v[:10]
        try:
            return date.fromisoformat(s)
        except ValueError:
            pass
    return None


def _float_days(a: dict) -> Optional[float]:
    """Total float in working days (parsers.py already converts hours→days)."""
    tf = a.get('totalFloat')
    return float(tf) if tf is not None else None


def _is_complete(a: dict) -> bool:
    return (a.get('pctComplete') or 0) >= 100


def _is_milestone(a: dict) -> bool:
    return bool(a.get('isMilestone'))


def _is_critical(a: dict, critical_days: float = 0.0) -> bool:
    if a.get('isCritical'):
        return True
    tf = _float_days(a)
    return tf is not None and tf <= critical_days


def _is_near_critical(a: dict, crit: float, near_crit: float) -> bool:
    tf = _float_days(a)
    return tf is not None and crit < tf <= near_crit


def _forecast_finish(a: dict) -> Optional[date]:
    for key in ('finish', 'earlyFinish', 'remainFinish', 'bFinish'):
        d = _parse_date(a.get(key))
        if d:
            return d
    return None


def _day_delta(d1: Optional[date], d2: Optional[date]) -> Optional[float]:
    """d2 - d1 in calendar days. Positive = d2 is later."""
    if d1 is None or d2 is None:
        return None
    return float((d2 - d1).days)


def _find_by_code(activities: List[dict], code: str) -> Optional[dict]:
    for a in activities:
        if (a.get('code') or a.get('id') or '') == code:
            return a
    return None


def _before_dd(val, dd: date) -> bool:
    d = _parse_date(val)
    return d is not None and d < dd


# ── Confidence score ──────────────────────────────────────────────────────────

def _confidence(
    current: List[dict],
    baseline: Optional[List[dict]],
    previous: Optional[List[dict]],
    milestones: List[MilestoneInput],
    limitations: List[str],
) -> float:
    score = 0.0

    if baseline:
        score += 25
    else:
        limitations.append('No approved baseline provided — date-comparison analysis is unavailable.')

    if previous:
        score += 15
    else:
        limitations.append('No previous schedule update provided — trend analysis is unavailable.')

    contractual = [m for m in milestones if m.is_contractual or m.contract_required_date]
    if contractual:
        score += 15
    else:
        limitations.append('No contractual milestones designated — status cannot be confirmed against contract obligations.')

    # Data date confirmed (+10 — caller already validated it)
    score += 10

    # Logic completeness
    total = len(current)
    incomplete = [a for a in current if not _is_milestone(a) and not _is_complete(a)]
    open_s = sum(1 for a in incomplete if not (a.get('predecessors') or []))
    open_f = sum(1 for a in incomplete if not (a.get('successors') or []))
    open_pct = (open_s + open_f) / (len(incomplete) * 2 + 1) * 100
    if open_pct < 5:
        score += 10
    elif open_pct < 15:
        score += 5

    # Calendar assignments
    if any(a.get('calendarId') for a in current):
        score += 5

    # Hard-constraint rate
    hard_types = {'CS_MANDFIN', 'CS_MANDSTART', 'MSO', 'MFO', 'MANDATORY_START', 'MANDATORY_FINISH'}
    hc = sum(1 for a in incomplete if a.get('constraintType', '') in hard_types)
    hc_pct = hc / (len(incomplete) + 1) * 100
    if hc_pct < 5:
        score += 10
    elif hc_pct < 15:
        score += 5

    # Critical path exists
    if any(_is_critical(a) for a in current):
        score += 10

    return min(100.0, score)


# ── Milestone analysis ────────────────────────────────────────────────────────

def _analyze_milestone(ms: MilestoneInput, activity: Optional[dict], dd: date, T: ThresholdValues) -> Dict:
    res: Dict = {
        'activity_id': ms.activity_id,
        'description': ms.description,
        'category': ms.category,
        'is_contractual': ms.is_contractual or bool(ms.contract_required_date),
        'contract_required_date': ms.contract_required_date.isoformat() if ms.contract_required_date else None,
        'allowable_variance_days': ms.allowable_variance_days,
        'status': 'UNDETERMINED',
        'variance_days': None,
        'summary': '',
    }

    if activity is None:
        res['summary'] = f'Activity {ms.activity_id} not found in current schedule.'
        return res

    if _is_complete(activity):
        actual = _parse_date(activity.get('finish') or activity.get('start'))
        res['actual_finish'] = actual.isoformat() if actual else None
        if ms.contract_required_date and actual:
            var = (actual - ms.contract_required_date).days
            res['variance_days'] = var
            if var > ms.allowable_variance_days:
                res['status'] = 'DELAYED'
                res['summary'] = f'Milestone completed {var:.0f}d late vs. contract date.'
            else:
                res['status'] = 'COMPLETE'
                res['summary'] = f'Milestone complete within tolerance ({var:.0f}d vs. {ms.allowable_variance_days:.0f}d allowed).'
        else:
            res['status'] = 'COMPLETE'
            res['summary'] = 'Milestone is complete.'
        return res

    # Data date has passed contract date without completion
    if ms.contract_required_date and dd > ms.contract_required_date:
        var = (dd - ms.contract_required_date).days
        res['status'] = 'DELAYED'
        res['variance_days'] = var
        res['summary'] = (
            f'Data date {dd} has passed contract date {ms.contract_required_date} '
            f'without completion (+{var}d).'
        )
        return res

    # Forecast vs. contract
    fc = _forecast_finish(activity)
    res['forecast_date'] = fc.isoformat() if fc else None
    if fc and ms.contract_required_date:
        var = (fc - ms.contract_required_date).days
        res['variance_days'] = var
        effective_red = T.milestone_red_days + ms.allowable_variance_days
        if var > effective_red:
            res['status'] = 'DELAYED'
            res['summary'] = f'Forecast {fc} is {var:.0f}d beyond contract date (red threshold {effective_red:.0f}d).'
        elif var > ms.allowable_variance_days:
            res['status'] = 'AT_RISK'
            res['summary'] = f'Forecast {fc} is {var:.0f}d beyond contract date (within warning range).'
        else:
            res['status'] = 'ON_TRACK'
            res['summary'] = f'Forecast {fc} is within the contract date {ms.contract_required_date}.'
    else:
        res['summary'] = 'No contract date or forecast date available for comparison.'

    return res


# ── Progress score ────────────────────────────────────────────────────────────

def _progress_score(current: List[dict], baseline: Optional[List[dict]], dd: date) -> float:
    """0–100: how well actual progress tracks the baseline plan at the data date."""
    if not baseline:
        return 70.0  # neutral — no comparison possible

    bl_idx = {(a.get('code') or a.get('id') or ''): a for a in baseline}
    acts = [a for a in current if (a.get('code') or a.get('id') or '') in bl_idx and not _is_milestone(a)]

    total_planned_s = total_planned_f = 0
    actual_s = actual_f = 0

    for a in acts:
        code = a.get('code') or a.get('id') or ''
        bl = bl_idx[code]
        bl_start = _parse_date(bl.get('bStart') or bl.get('start'))
        bl_finish = _parse_date(bl.get('bFinish') or bl.get('finish'))

        if bl_start and bl_start <= dd:
            total_planned_s += 1
            if a.get('start'):
                actual_s += 1

        if bl_finish and bl_finish <= dd:
            total_planned_f += 1
            if _is_complete(a):
                actual_f += 1

    sr = actual_s / total_planned_s * 100 if total_planned_s else 100.0
    fr = actual_f / total_planned_f * 100 if total_planned_f else 100.0
    return (sr + fr) / 2


# ── Risk score ────────────────────────────────────────────────────────────────

def _risk_score(
    milestone_results: List[Dict],
    finish_variance: Optional[float],
    previous_variance: Optional[float],
    min_critical_float: Optional[float],
    near_critical_count: int,
    quality_score: float,
    progress: float,
    T: ThresholdValues,
) -> Tuple[float, Dict]:
    bd: Dict = {}

    # Milestones (25%)
    ms_delayed = sum(1 for m in milestone_results if m.get('status') == 'DELAYED')
    ms_at_risk = sum(1 for m in milestone_results if m.get('status') == 'AT_RISK')
    ms_n = len(milestone_results) or 1
    if ms_delayed > 0:
        ms_s = 100.0
    elif ms_at_risk > 0:
        ms_s = 50.0 + ms_at_risk / ms_n * 30
    else:
        ms_s = 0.0
    bd['milestones'] = {'score': ms_s, 'weight': T.weight_milestone,
                        'contribution': ms_s * T.weight_milestone / 100}

    # Project finish (15%)
    if finish_variance is None:
        pf_s = 30.0
    elif finish_variance <= 0:
        pf_s = 0.0
    elif finish_variance <= T.project_finish_yellow_days:
        pf_s = 25.0
    elif finish_variance <= T.project_finish_red_days:
        pf_s = 65.0
    else:
        pf_s = 100.0
    bd['project_finish'] = {'score': pf_s, 'weight': T.weight_project_finish,
                             'contribution': pf_s * T.weight_project_finish / 100}

    # Critical path (20%)
    if min_critical_float is None:
        cp_s = 40.0
    elif min_critical_float < 0:
        cp_s = min(100.0, 60 + abs(min_critical_float) * 2)
    elif min_critical_float < T.near_critical_float_days:
        cp_s = 55.0
    elif min_critical_float < T.float_warning_days:
        cp_s = 25.0
    else:
        cp_s = 0.0
    bd['critical_path'] = {'score': cp_s, 'weight': T.weight_critical_path,
                            'contribution': cp_s * T.weight_critical_path / 100}

    # Progress (15%)
    if progress >= 90:
        pr_s = 0.0
    elif progress >= 70:
        pr_s = 20.0
    elif progress >= 50:
        pr_s = 55.0
    else:
        pr_s = 85.0
    bd['progress'] = {'score': pr_s, 'weight': T.weight_progress,
                      'contribution': pr_s * T.weight_progress / 100}

    # Float health (10%)
    if near_critical_count == 0:
        fh_s = 0.0
    elif near_critical_count <= 2:
        fh_s = 20.0
    elif near_critical_count <= 5:
        fh_s = 50.0
    else:
        fh_s = min(100.0, 50 + near_critical_count * 3)
    bd['float_health'] = {'score': fh_s, 'weight': T.weight_float_health,
                           'contribution': fh_s * T.weight_float_health / 100}

    # Quality (10%)
    q_s = max(0.0, 100 - quality_score)
    bd['quality'] = {'score': q_s, 'weight': T.weight_quality,
                     'contribution': q_s * T.weight_quality / 100}

    # Trend (5%)
    if previous_variance is None:
        tr_s = 20.0
    elif previous_variance <= 0:
        tr_s = 0.0
    elif previous_variance <= T.max_critical_path_slippage_days:
        tr_s = 30.0
    else:
        tr_s = min(100.0, 30 + previous_variance * 2)
    bd['trend'] = {'score': tr_s, 'weight': T.weight_trend,
                   'contribution': tr_s * T.weight_trend / 100}

    total = round(sum(v['contribution'] for v in bd.values()), 1)
    return total, bd


# ── Green-conditions check ────────────────────────────────────────────────────

def _all_green(
    finish_var: Optional[float],
    milestone_results: List[Dict],
    quality: float,
    min_float: Optional[float],
    incomplete_before_pct: float,
    T: ThresholdValues,
) -> bool:
    """All ON TRACK conditions must be satisfied simultaneously."""
    if finish_var is not None and finish_var > T.project_finish_yellow_days:
        return False
    if any(m.get('status') in ('DELAYED', 'AT_RISK') for m in milestone_results):
        return False
    if quality < T.min_schedule_quality_score:
        return False
    if min_float is not None and min_float < 0:
        return False
    if incomplete_before_pct > 10:
        return False
    return True


# ── Main entry point ──────────────────────────────────────────────────────────

def classify_schedule(
    current_activities: List[dict],
    baseline_activities: Optional[List[dict]] = None,
    previous_activities: Optional[List[dict]] = None,
    milestone_inputs: Optional[List[MilestoneInput]] = None,
    thresholds: Optional[ThresholdValues] = None,
    data_date: Optional[date] = None,
    contract_finish_date: Optional[date] = None,
) -> StatusResult:
    """
    Classify a schedule as ON_TRACK / AT_RISK / OFF_TRACK / UNDETERMINED.

    Args:
        current_activities:  List of activity dicts from the current schedule update.
        baseline_activities: List of activity dicts from the approved baseline (optional).
        previous_activities: List of activity dicts from the previous update (optional).
        milestone_inputs:    User-designated milestones with contract dates (optional).
        thresholds:          ThresholdValues instance; defaults used when None.
        data_date:           Schedule data date.
        contract_finish_date: Contract completion date (optional).

    Returns:
        StatusResult dataclass with full classification evidence.
    """
    T = thresholds or ThresholdValues()
    milestones: List[MilestoneInput] = milestone_inputs or []
    limitations: List[str] = []
    reason_codes: List[str] = []
    triggered: List[Dict] = []
    recommendations: List[str] = []
    red_triggered = False
    yellow_triggered = False
    red_codes: List[str] = []
    yellow_codes: List[str] = []

    # ── Gate 1: data date ─────────────────────────────────────────────────────

    if not data_date:
        return StatusResult(
            status='UNDETERMINED',
            primary_reason=RC._MESSAGES[RC.NO_DATA_DATE],
            reason_codes=[RC.NO_DATA_DATE],
            data_limitations=['Data date is required to determine what work is forecast versus actual.'],
            recommended_actions=['Confirm the schedule data date in the P6 project settings and re-upload.'],
        )

    # ── Gate 2: activities present ────────────────────────────────────────────

    if not current_activities:
        return StatusResult(
            status='UNDETERMINED',
            primary_reason=RC._MESSAGES[RC.NO_ACTIVITIES],
            reason_codes=[RC.NO_ACTIVITIES],
            data_limitations=['The uploaded file contained no parseable activities.'],
        )

    non_ms = [a for a in current_activities if not _is_milestone(a)]
    incomplete = [a for a in non_ms if not _is_complete(a)]
    total_non_ms = len(non_ms)
    total_incomplete = len(incomplete)

    # ── Confidence ────────────────────────────────────────────────────────────

    confidence = _confidence(current_activities, baseline_activities, previous_activities, milestones, limitations)

    # ── Quality ───────────────────────────────────────────────────────────────

    from .quality_engine import assess_quality
    quality_result = assess_quality(current_activities, data_date, T)
    quality_score = quality_result['overall_score']

    # Circular logic → UNDETERMINED
    if quality_result.get('circular_logic_count', 0) > 0:
        return StatusResult(
            status='UNDETERMINED',
            confidence_score=confidence,
            schedule_quality_score=quality_score,
            primary_reason=RC._MESSAGES[RC.CIRCULAR_LOGIC],
            reason_codes=[RC.CIRCULAR_LOGIC],
            data_limitations=[
                f'Circular logic detected in {quality_result["circular_logic_count"]} activities.'
            ],
            recommended_actions=['Identify and break all circular logic loops before re-submitting.'],
            quality_details=quality_result,
        )

    # ── Incomplete work before data date ──────────────────────────────────────

    incomplete_before_dd = [
        a for a in incomplete if _before_dd(a.get('bFinish') or a.get('finish'), data_date)
    ]
    inc_before_pct = len(incomplete_before_dd) / (total_incomplete + 1) * 100

    if inc_before_pct > 25 and total_incomplete > 20:
        reason_codes.append(RC.EXCESSIVE_INCOMPLETE_BEFORE_DD)
        limitations.append(
            f'{len(incomplete_before_dd)} activities ({inc_before_pct:.0f}%) are past their '
            'planned finish but incomplete.'
        )

    # ── Gate 3: insufficient quality + no contract reference ─────────────────

    if (quality_score < T.min_schedule_quality_score
            and not baseline_activities
            and not contract_finish_date
            and not any(m.contract_required_date for m in milestones)):
        return StatusResult(
            status='UNDETERMINED',
            confidence_score=confidence,
            schedule_quality_score=quality_score,
            primary_reason=(
                'Schedule quality is insufficient for reliable assessment and no '
                'approved contract dates are available.'
            ),
            reason_codes=[RC.QUALITY_BELOW_MINIMUM, RC.NO_APPROVED_BASELINE],
            data_limitations=limitations,
            recommended_actions=[
                'Improve schedule quality: resolve open ends, remove circular logic, reduce hard constraints.',
                'Associate an approved baseline or enter contractual milestone dates.',
            ],
            quality_details=quality_result,
        )

    # ── Project finish forecast ───────────────────────────────────────────────

    fc_dates = [_forecast_finish(a) for a in incomplete if _forecast_finish(a)]
    project_forecast = max(fc_dates) if fc_dates else None

    # ── Milestone analysis ────────────────────────────────────────────────────

    milestone_results: List[Dict] = []
    for ms in milestones:
        act = _find_by_code(current_activities, ms.activity_id)
        res = _analyze_milestone(ms, act, data_date, T)
        milestone_results.append(res)

        if res['status'] == 'DELAYED' and (ms.is_contractual or ms.contract_required_date):
            red_triggered = True
            code = (RC.CONTRACTUAL_MILESTONE_MISSED
                    if res.get('variance_days') and res['variance_days'] > 0 and
                    _parse_date(res.get('actual_finish')) is None
                    else RC.CONTRACTUAL_MILESTONE_DELAYED)
            red_codes.append(code)
            triggered.append({
                'code': code,
                'milestone_id': ms.activity_id,
                'value': res.get('variance_days'),
                'threshold': T.milestone_red_days,
                'message': res.get('summary', ''),
            })
            recommendations.append(
                f'Contractual milestone {ms.activity_id} is delayed. '
                'Develop a recovery plan or submit a formal baseline revision.'
            )
        elif res['status'] == 'AT_RISK':
            yellow_triggered = True
            yellow_codes.append(RC.MILESTONE_YELLOW_VARIANCE)
            triggered.append({
                'code': RC.MILESTONE_YELLOW_VARIANCE,
                'milestone_id': ms.activity_id,
                'value': res.get('variance_days'),
                'threshold': T.milestone_yellow_days,
                'message': res.get('summary', ''),
            })
            recommendations.append(
                f'Monitor milestone {ms.activity_id}: approaching tolerance threshold.'
            )

    # ── Contract finish variance ──────────────────────────────────────────────

    finish_var: Optional[float] = None
    if contract_finish_date and project_forecast:
        finish_var = _day_delta(contract_finish_date, project_forecast)
        if finish_var is not None:
            if finish_var > T.project_finish_red_days:
                red_triggered = True
                red_codes.append(RC.CONTRACT_FINISH_EXCEEDED)
                triggered.append({
                    'code': RC.CONTRACT_FINISH_EXCEEDED,
                    'value': finish_var,
                    'threshold': T.project_finish_red_days,
                    'message': (
                        f'Forecast completion {project_forecast} is {finish_var:.0f} days '
                        f'beyond the contract date {contract_finish_date}.'
                    ),
                })
                recommendations.append(
                    'Develop a logic-based recovery plan and submit for approval.'
                )
            elif finish_var > T.project_finish_yellow_days:
                yellow_triggered = True
                yellow_codes.append(RC.CRITICAL_FLOAT_BELOW_WARNING)
                triggered.append({
                    'code': RC.CRITICAL_FLOAT_BELOW_WARNING,
                    'value': finish_var,
                    'threshold': T.project_finish_yellow_days,
                    'message': (
                        f'Forecast completion is {finish_var:.0f} days beyond the contract date — '
                        'within yellow tolerance.'
                    ),
                })

    # ── Baseline finish variance ──────────────────────────────────────────────

    baseline_var: Optional[float] = None
    if baseline_activities and project_forecast:
        bl_fc = [_forecast_finish(a) for a in baseline_activities if _forecast_finish(a)]
        if bl_fc:
            baseline_var = _day_delta(max(bl_fc), project_forecast)

    # ── Previous update variance ──────────────────────────────────────────────

    previous_var: Optional[float] = None
    if previous_activities and project_forecast:
        prev_incomplete = [a for a in previous_activities if not _is_complete(a)]
        prev_fc = [_forecast_finish(a) for a in prev_incomplete if _forecast_finish(a)]
        if prev_fc:
            previous_var = _day_delta(max(prev_fc), project_forecast)
            if previous_var and previous_var > T.max_critical_path_slippage_days:
                yellow_triggered = True
                yellow_codes.append(RC.LONGEST_PATH_SLIPPED)
                triggered.append({
                    'code': RC.LONGEST_PATH_SLIPPED,
                    'value': previous_var,
                    'threshold': T.max_critical_path_slippage_days,
                    'message': (
                        f'Project finish slipped {previous_var:.0f} days '
                        'since the previous schedule update.'
                    ),
                })
                recommendations.append(
                    'Review the driving path to completion: identify what changed since the previous update.'
                )

    # ── Critical and near-critical path ──────────────────────────────────────

    critical_acts = [a for a in incomplete if _is_critical(a, T.critical_float_days)]
    near_critical_acts = [
        a for a in incomplete
        if _is_near_critical(a, T.critical_float_days, T.near_critical_float_days)
    ]

    c_floats = [_float_days(a) for a in critical_acts if _float_days(a) is not None]
    min_crit_float: Optional[float] = min(c_floats) if c_floats else None

    if min_crit_float is not None:
        if min_crit_float < 0 and abs(min_crit_float) > T.milestone_red_days:
            red_triggered = True
            red_codes.append(RC.NEGATIVE_FLOAT_BEYOND_THRESHOLD)
            triggered.append({
                'code': RC.NEGATIVE_FLOAT_BEYOND_THRESHOLD,
                'value': min_crit_float,
                'threshold': -T.milestone_red_days,
                'message': (
                    f'Critical path has {min_crit_float:.1f}d of negative float — '
                    f'exceeds red threshold of {T.milestone_red_days:.0f}d.'
                ),
            })
            recommendations.append(
                'Resolve negative float: validate remaining durations, logic, and constraints on the critical path.'
            )
        elif min_crit_float < 0:
            yellow_triggered = True
            yellow_codes.append(RC.CRITICAL_FLOAT_BELOW_WARNING)
            triggered.append({
                'code': RC.CRITICAL_FLOAT_BELOW_WARNING,
                'value': min_crit_float,
                'threshold': 0,
                'message': f'Critical-path float is {min_crit_float:.1f}d (negative but within red threshold).',
            })
        elif min_crit_float < T.float_warning_days:
            yellow_triggered = True
            yellow_codes.append(RC.CRITICAL_FLOAT_BELOW_WARNING)
            triggered.append({
                'code': RC.CRITICAL_FLOAT_BELOW_WARNING,
                'value': min_crit_float,
                'threshold': T.float_warning_days,
                'message': (
                    f'Critical-path float is {min_crit_float:.1f}d — '
                    f'below warning threshold of {T.float_warning_days:.0f}d.'
                ),
            })

    # Near-critical convergence
    if len(near_critical_acts) >= 3:
        yellow_triggered = True
        yellow_codes.append(RC.MULTIPLE_NEAR_CRITICAL_PATHS)
        triggered.append({
            'code': RC.MULTIPLE_NEAR_CRITICAL_PATHS,
            'value': len(near_critical_acts),
            'threshold': 3,
            'message': (
                f'{len(near_critical_acts)} near-critical activities — '
                'multiple paths may converge onto the critical path.'
            ),
        })
        recommendations.append(
            'Review multiple converging near-critical paths for combined schedule risk.'
        )

    # ── Missed starts and finishes ────────────────────────────────────────────

    missed_starts: List[dict] = []
    missed_finishes: List[dict] = []
    for a in incomplete:
        pl_s = _parse_date(a.get('bStart'))
        pl_f = _parse_date(a.get('bFinish'))
        has_start = bool(a.get('start'))
        if pl_s and pl_s < data_date and not has_start:
            missed_starts.append(a)
        if pl_f and pl_f < data_date and not _is_complete(a):
            missed_finishes.append(a)

    crit_missed_s = [a for a in missed_starts if _is_critical(a, T.critical_float_days)]
    crit_missed_f = [a for a in missed_finishes if _is_critical(a, T.critical_float_days)]

    if crit_missed_s and baseline_activities:
        yellow_triggered = True
        yellow_codes.append(RC.MISSED_CRITICAL_STARTS)
        triggered.append({
            'code': RC.MISSED_CRITICAL_STARTS,
            'value': len(crit_missed_s),
            'threshold': 0,
            'message': f'{len(crit_missed_s)} critical activities have missed their planned start.',
        })
        recommendations.append(
            'Validate remaining durations for delayed critical activities and assess recovery options.'
        )

    if crit_missed_f and baseline_activities:
        yellow_triggered = True
        yellow_codes.append(RC.MISSED_CRITICAL_FINISHES)
        triggered.append({
            'code': RC.MISSED_CRITICAL_FINISHES,
            'value': len(crit_missed_f),
            'threshold': 0,
            'message': f'{len(crit_missed_f)} critical activities have missed their planned finish.',
        })

    # ── Progress vs. baseline ─────────────────────────────────────────────────

    progress = _progress_score(current_activities, baseline_activities, data_date)
    if progress < 60 and baseline_activities:
        yellow_triggered = True
        yellow_codes.append(RC.PROGRESS_BEHIND_PLAN)
        triggered.append({
            'code': RC.PROGRESS_BEHIND_PLAN,
            'value': round(progress, 1),
            'threshold': 60,
            'message': f'Progress performance score is {progress:.0f}% — below 60% threshold.',
        })
        recommendations.append(
            'Verify that actual progress is statused through the current data date.'
        )

    # ── Incomplete before data date ───────────────────────────────────────────

    if inc_before_pct > 10 and total_incomplete > 10:
        yellow_triggered = True
        yellow_codes.append(RC.INCOMPLETE_WORK_BEFORE_DATA_DATE)
        if RC.INCOMPLETE_WORK_BEFORE_DATA_DATE not in [t['code'] for t in triggered]:
            triggered.append({
                'code': RC.INCOMPLETE_WORK_BEFORE_DATA_DATE,
                'value': round(inc_before_pct, 1),
                'threshold': 10,
                'message': (
                    f'{len(incomplete_before_dd)} activities ({inc_before_pct:.0f}%) '
                    'are past their planned finish but incomplete.'
                ),
            })
        recommendations.append(
            f'Update actual progress for {len(incomplete_before_dd)} activities '
            'whose planned finish has passed.'
        )

    # ── Risk score ────────────────────────────────────────────────────────────

    risk, risk_bd = _risk_score(
        milestone_results, finish_var, previous_var,
        min_crit_float, len(near_critical_acts),
        quality_score, progress, T,
    )

    # ── Final classification ──────────────────────────────────────────────────

    all_codes = list(dict.fromkeys(reason_codes + red_codes + yellow_codes))

    if confidence < T.min_confidence_score and not red_triggered:
        limitations.append(
            f'Analysis confidence is {confidence:.0f}% — below the configured minimum of '
            f'{T.min_confidence_score:.0f}%. Results should be interpreted with caution.'
        )

    if red_triggered:
        status = 'OFF_TRACK'
        primary_code = red_codes[0]
    elif yellow_triggered:
        status = 'AT_RISK'
        primary_code = yellow_codes[0]
    elif not _all_green(finish_var, milestone_results, quality_score, min_crit_float, inc_before_pct, T):
        status = 'AT_RISK'
        primary_code = RC.UNCONFIRMED_GREEN_CONDITIONS
        all_codes.append(RC.UNCONFIRMED_GREEN_CONDITIONS)
    else:
        status = 'ON_TRACK'
        primary_code = ''

    # Primary reason text
    if primary_code:
        primary_text = next(
            (t['message'] for t in triggered if t.get('code') == primary_code),
            RC._MESSAGES.get(primary_code, primary_code),
        )
    else:
        primary_text = 'All schedule health indicators are within acceptable tolerances.'

    # Default recommendations when none triggered
    if not recommendations:
        if status == 'ON_TRACK':
            recommendations.append('Continue monitoring the schedule at the next update cycle.')
        elif status == 'AT_RISK':
            recommendations += [
                'Review the driving path to contractual completion.',
                'Develop contingency plans for identified at-risk activities.',
            ]
        elif status == 'OFF_TRACK':
            recommendations += [
                'Develop and submit a logic-based recovery plan demonstrating the path to contractual completion.',
                'Confirm whether a revised baseline has been formally approved.',
            ]

    return StatusResult(
        status=status,
        risk_score=risk,
        confidence_score=round(confidence, 1),
        schedule_quality_score=round(quality_score, 1),
        primary_reason=primary_text,
        reason_codes=all_codes,
        triggered_thresholds=triggered,
        data_limitations=limitations,
        recommended_actions=recommendations,
        contract_finish_variance_days=finish_var,
        baseline_finish_variance_days=baseline_var,
        previous_update_finish_variance_days=previous_var,
        critical_path_float_days=min_crit_float,
        near_critical_path_count=len(near_critical_acts),
        milestone_results=milestone_results,
        quality_details=quality_result,
        risk_score_breakdown=risk_bd,
        missed_starts_count=len(missed_starts),
        missed_finishes_count=len(missed_finishes),
        critical_activity_count=len(critical_acts),
        near_critical_activity_count=len(near_critical_acts),
        incomplete_before_dd_count=len(incomplete_before_dd),
        project_forecast_finish=project_forecast.isoformat() if project_forecast else None,
    )
