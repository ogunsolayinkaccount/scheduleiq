"""
Report Service — ScheduleIQ Automated Project Controls Reporting Phase

This module builds the ONE authoritative report payload consumed by the
Reports UI, PDF export, and Excel export. It performs no calculation of
its own — every number here comes from cost_engine.py, trend_engine.py,
driver_engine.py, executive_summary.py, schedule_comparison.py, or fields
already computed and stored on ScheduleUpload at import time. This module
only assembles, labels, and filters those existing facts into report
sections. If a number can't be traced back to one of those sources, it
does not belong in a report.

`build_executive_payload()` is the same payload the
`/api/projects/<id>/executive-summary/` endpoint returns — moved here
from views.py so the endpoint and report generation share one
implementation rather than risking drift between two copies.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from .cost_engine import compute_cost_summary, compute_productivity
from . import version_chronology
from . import trend_engine
from . import driver_engine
from . import executive_summary
from . import baseline_progress
from . import update_intelligence
from . import risk_register
from . import activity_analysis
from . import float_intelligence
from . import milestones
from .data_date_detection import resolve_effective_data_date
from .schedule_comparison import compare_schedules

ENGINE_VERSION = '1.1.0'

REPORT_TYPES = ('WEEKLY_PROJECT_CONTROLS', 'MONTHLY_EXECUTIVE')


# ── Shared snapshot/EAC helpers (also used by views.py's controls-trends) ──

def snapshot_series(project, ev_method: str, pv_method: str):
    """Chronological trend_engine snapshots for every real, persisted
    schedule version of this project that has a data date."""
    versions = list(project.schedule_versions.exclude(data_date=None).order_by('data_date'))
    series = []
    for v in versions:
        summary = compute_cost_summary(v.activities_json, v.data_date, ev_method, pv_method)
        productivity = compute_productivity(v.activities_json, ev_method)
        series.append(trend_engine.snapshot_from_cost_summary(
            str(v.id), v.version_label, v.data_date.isoformat(), summary, productivity,
        ))
    return series, versions


def latest_approved_eac(project) -> Optional[Dict[str, Any]]:
    from .models import ManualCostEntry
    entry = ManualCostEntry.objects.filter(project=project, entry_type='APPROVED_EAC').order_by('-entered_at').first()
    if not entry:
        return None
    return {'cost': entry.cost, 'description': entry.description, 'enteredAt': entry.entered_at.isoformat()}


# ── Executive payload (shared with /executive-summary/) ────────────────────

def build_executive_payload(
    project, ev_method: str = 'DURATION_PCT_COMPLETE', pv_method: str = 'LINEAR_BASELINE_SPREAD',
    group_by: str = 'discipline', lookback: int = 6,
) -> Dict[str, Any]:
    series, versions = snapshot_series(project, ev_method, pv_method)

    comparison = trend_engine.build_comparison(series)
    eac_drift = trend_engine.build_eac_drift(series, latest_approved_eac(project))
    cpi_trend = trend_engine.detect_consecutive_trend(series, 'cpi', lookback)
    spi_trend = trend_engine.detect_consecutive_trend(series, 'spi', lookback)
    productivity_trend = trend_engine.detect_consecutive_trend(series, 'productivityFactor', lookback)

    cost_drivers = schedule_drivers = productivity_drivers = None
    latest_version = versions[-1] if versions else None
    if latest_version:
        data_date = latest_version.data_date
        cost_drivers = driver_engine.rank_cost_drivers(latest_version.activities_json, group_by, data_date, ev_method, pv_method)
        schedule_drivers = driver_engine.rank_schedule_drivers(latest_version.activities_json, group_by, data_date, ev_method, pv_method)
        productivity_drivers = driver_engine.rank_productivity_drivers(latest_version.activities_json, group_by, ev_method)

    control_signals = executive_summary.evaluate_control_signals(comparison, productivity_trend)
    forecast_signals = executive_summary.evaluate_forecast_deterioration(eac_drift)
    narrative = executive_summary.build_executive_narrative(
        comparison, eac_drift, cpi_trend, spi_trend, cost_drivers, productivity_drivers,
    )

    warnings = []
    if not versions:
        warnings.append('No persisted schedule versions with a data date exist for this project — no trend data available.')
    elif len(versions) == 1:
        warnings.append('Only one schedule version exists — current-vs-previous comparisons are unavailable until a second update is imported.')
    if comparison.get('available') and comparison['metrics'].get('cpi', {}).get('current') is None:
        warnings.append('No cost-loaded activities in the latest version — cost EVM metrics are unavailable.')

    return {
        'engineVersion': executive_summary.ENGINE_VERSION,
        'project': {'id': str(project.id), 'name': project.name},
        'reportingDate': latest_version.data_date.isoformat() if latest_version and latest_version.data_date else None,
        'evMethod': ev_method, 'pvMethod': pv_method, 'groupBy': group_by,
        'currentMetrics': comparison.get('metrics') if comparison.get('available') else None,
        'comparison': comparison,
        'eacDrift': eac_drift,
        'trends': {'cpi': cpi_trend, 'spi': spi_trend, 'productivity': productivity_trend},
        'drivers': {'cost': cost_drivers, 'schedule': schedule_drivers, 'productivity': productivity_drivers},
        'controlSignals': control_signals,
        'forecastSignals': forecast_signals,
        'narrative': narrative,
        'warnings': warnings,
        'methodology': {
            'evMethod': ev_method, 'pvMethod': pv_method,
            'cpiHealthyThreshold': executive_summary.CPI_HEALTHY_THRESHOLD,
            'spiHealthyThreshold': executive_summary.SPI_HEALTHY_THRESHOLD,
            'productivityHealthyThreshold': executive_summary.PRODUCTIVITY_HEALTHY_THRESHOLD,
        },
        # Internal — not serialized to the API, used by build_report_payload()
        '_versions': versions,
    }


# ── Additional report sections (Phase A) ────────────────────────────────────

def build_project_info_section(project, current_version, previous_version) -> Dict[str, Any]:
    return {
        'projectName': project.name,
        'projectNumber': project.project_number or None,
        'client': project.client or None,
        'scheduleVersion': current_version.version_label if current_version else None,
        'dataDate': current_version.data_date.isoformat() if current_version and current_version.data_date else None,
        'previousUpdateDate': previous_version.data_date.isoformat() if previous_version and previous_version.data_date else None,
        'sourceFile': current_version.original_filename if current_version else None,
        'importMethod': current_version.import_method if current_version else None,
    }


def build_schedule_performance_section(version) -> Dict[str, Any]:
    """Reuses fields already computed by the parser/status engine at
    import time (schedule_metadata.py) — no recomputation here."""
    if not version:
        return {'available': False, 'reason': 'No schedule version available.'}
    return {
        'available': True,
        'classification': version.schedule_classification or None,
        'activityCount': version.activity_count,
        'milestoneCount': version.milestone_count,
        'criticalCount': version.critical_count,
        'negativeFloatCount': version.negative_float_count,
        'minTotalFloat': version.min_total_float,
        'maxTotalFloat': version.max_total_float,
        'avgTotalFloat': version.avg_total_float,
        'completedCount': version.completed_count,
        'inProgressCount': version.in_progress_count,
        'notStartedCount': version.not_started_count,
    }


def build_update_comparison_section(current_version, previous_version, calendars=None) -> Dict[str, Any]:
    """Reuses schedule_comparison.compare_schedules() — the same engine
    behind the Compare Updates / Update Analysis tab."""
    if not current_version or not previous_version:
        return {
            'available': False,
            'reason': 'A previous schedule version is required to compute update comparison — only one version exists.',
        }
    result = compare_schedules(previous_version.activities_json, current_version.activities_json, calendars)
    activities = result['activities']
    return {
        'available': True,
        'previousVersionLabel': previous_version.version_label,
        'currentVersionLabel': current_version.version_label,
        'addedCount': activities['addedCount'],
        'removedCount': activities['removedCount'],
        'changedActivityCount': activities['changedActivityCount'],
        'movedLaterCount': activities['movedLaterCount'],
        'movedEarlierCount': activities['movedEarlierCount'],
        'floatDeterioratedCount': activities['floatDeterioratedCount'],
        'floatImprovedCount': activities['floatImprovedCount'],
        'newlyCriticalCount': activities['newlyCriticalCount'],
        'newlyNegativeFloatCount': activities['newlyNegativeFloatCount'],
        'milestoneMovement': result['milestoneMovement'][:10],
        'summaryNarrative': result['summaryNarrative'],
    }


def _calendars_for_versions(*schedule_uploads) -> Dict[str, Any]:
    """Same purpose as views.py's _calendars_for() — kept as a small local
    copy per this codebase's established sibling-engine convention (see
    baseline_progress.py's module docstring) rather than importing a
    view-layer private helper into the engine/report layer."""
    from .calendar_engine import CalendarDefinition
    calendars: Dict[str, Any] = {}
    for su in schedule_uploads:
        if not su:
            continue
        for cal in su.calendars.all():
            cal_def = CalendarDefinition.from_stored(cal.standard_workweek, cal.exceptions, cal.has_detailed_definition)
            if cal_def is not None:
                calendars[cal.calendar_id] = cal_def
    return calendars


def _find_baseline_version(versions_desc):
    """Same baseline auto-detection rule as views.py's _assign_version_roles()
    — the first (most-recent) version classified APPROVED_BASELINE or
    REVISED_BASELINE. `versions_desc` must be ordered most-recent-first."""
    return next((v for v in versions_desc if v.schedule_classification in ('APPROVED_BASELINE', 'REVISED_BASELINE')), None)


def _lookahead_scope_label(filters: Optional[Dict[str, Any]], window: Dict[str, Any]) -> str:
    weeks = window.get('weeks')
    range_label = f'{weeks}-Week Look Ahead' if weeks else 'Look Ahead'
    for dim in baseline_progress.GROUP_FIELDS:
        if filters and filters.get(dim):
            return f'{filters[dim]} — {range_label}'
    return f'All — {range_label}'


def build_lookahead_section(
    project, current_version,
    weeks: Optional[int] = None, from_date: Optional[date] = None, to_date: Optional[date] = None,
    filters: Optional[Dict[str, Any]] = None, baseline_version_id: Optional[str] = None,
    top_delayed_n: int = baseline_progress.DEFAULT_TOP_DELAYED_N,
    management_list_n: int = baseline_progress.DEFAULT_MANAGEMENT_LIST_N,
) -> Dict[str, Any]:
    """Builds the Weekly Project Controls Report's '4-Week Look Ahead'
    section by calling baseline_progress.build_report_lookahead() — no
    separate look-ahead calculation exists here. This function only
    resolves the baseline version and P6 calendars (Django-model concerns
    baseline_progress.py deliberately knows nothing about) and adds version
    labels/scope label for display."""
    if not current_version:
        return {'available': False, 'reason': 'No schedule version available.'}

    ordered = list(version_chronology.chronological_versions(project))
    baseline_version = None
    if baseline_version_id:
        baseline_version = next((v for v in ordered if str(v.id) == str(baseline_version_id)), None)
    else:
        baseline_version = _find_baseline_version(ordered)

    if not baseline_version:
        return {
            'available': False,
            'reason': 'No baseline schedule has been selected for this project.',
            'currentVersionId': str(current_version.id), 'currentVersionLabel': current_version.version_label,
        }

    data_date = resolve_effective_data_date(current_version)['dataDate']
    calendars = _calendars_for_versions(baseline_version, current_version)

    result = baseline_progress.build_report_lookahead(
        baseline_version.activities_json, current_version.activities_json, data_date, calendars,
        weeks=weeks, from_date=from_date, to_date=to_date, filters=filters,
        top_delayed_n=top_delayed_n, management_list_n=management_list_n,
    )
    return {
        'available': True,
        'currentVersionId': str(current_version.id), 'currentVersionLabel': current_version.version_label,
        'baselineVersionId': str(baseline_version.id), 'baselineVersionLabel': baseline_version.version_label,
        'scopeLabel': _lookahead_scope_label(filters, result['window']),
        **result,
    }


def build_update_intelligence_section(
    project, current_version, previous_version_id: Optional[str] = None,
    group_by: str = 'discipline', lookahead_weeks: int = 4,
) -> Dict[str, Any]:
    """Builds the Weekly Report's 'Schedule Update Performance' section by
    calling update_intelligence.build_update_intelligence() — no separate
    comparison logic exists here. PREVIOUS defaults to the immediately
    preceding version by upload order (same convention as
    views.py's _resolve_previous_and_current() — kept as a small local
    resolution here for the same reason _find_baseline_version/
    _calendars_for_versions already are)."""
    if not current_version:
        return {'available': False, 'reason': 'No schedule version available.'}

    ordered = list(version_chronology.chronological_versions(project))
    previous_version = None
    if previous_version_id:
        previous_version = next((v for v in ordered if str(v.id) == str(previous_version_id)), None)
    else:
        previous_version, _previous_unresolved = version_chronology.previous_resolution(ordered, current_version)

    if not previous_version:
        return {
            'available': False,
            'reason': 'No previous schedule version exists yet for this project — Schedule Update Performance requires at least two versions.',
            'currentVersionId': str(current_version.id), 'currentVersionLabel': current_version.version_label,
        }

    baseline_version = _find_baseline_version(ordered)
    current_dd = resolve_effective_data_date(current_version)['dataDate']
    previous_dd = resolve_effective_data_date(previous_version)['dataDate']
    calendars = _calendars_for_versions(baseline_version, current_version, previous_version)

    result = update_intelligence.build_update_intelligence(
        previous_version.activities_json, current_version.activities_json, previous_dd, current_dd,
        baseline_version.activities_json if baseline_version else None, calendars, group_by, lookahead_weeks,
    )
    return {
        'available': True,
        'currentVersionId': str(current_version.id), 'currentVersionLabel': current_version.version_label,
        'previousVersionId': str(previous_version.id), 'previousVersionLabel': previous_version.version_label,
        'baselineVersionId': str(baseline_version.id) if baseline_version else None,
        'baselineVersionLabel': baseline_version.version_label if baseline_version else None,
        **result,
    }


def _recovery_tracking_for_report(scenario, current_version, current_dd) -> Dict[str, Any]:
    """Same classification rule as views.py's _recovery_tracking() (ON_TRACK /
    PARTIALLY_REALIZED / NOT_REALIZED / UNAVAILABLE) — duplicated here as a
    small report-layer wrapper per this file's own convention (see
    _find_baseline_version/_calendars_for_versions), not a competing
    calculation: the diff-days/threshold logic is identical."""
    from .utils import parse_date

    if scenario.status != 'ACCEPTED':
        return {'available': False, 'reason': 'Recovery tracking applies to ACCEPTED scenarios.'}
    if not current_version or current_version.id == scenario.schedule_upload_id:
        return {'available': False, 'reason': 'No newer schedule update has been imported since this scenario was built.'}

    milestone_impact = (scenario.result or {}).get('milestoneImpact') or []
    if not milestone_impact:
        return {'available': False, 'reason': 'Scenario has no milestone impact data to track.'}

    current_by_id = {(a.get('code') or a.get('id') or ''): a for a in current_version.activities_json}
    comparisons = []
    for mi in milestone_impact:
        aid = mi['activityId']
        cur_act = current_by_id.get(aid)
        scenario_target = mi.get('scenarioForecastFinish')
        if not cur_act or not scenario_target:
            comparisons.append({'activityId': aid, 'activityName': mi.get('activityName'), 'classification': 'UNAVAILABLE', 'scenarioTargetFinish': scenario_target, 'nextUpdateForecastFinish': None, 'differenceDays': None})
            continue
        next_finish = cur_act.get('finish') or cur_act.get('earlyFinish') or cur_act.get('remainFinish') or cur_act.get('bFinish')
        next_finish_d = parse_date(next_finish) if next_finish else None
        scenario_target_d = parse_date(scenario_target)
        if not next_finish_d or not scenario_target_d:
            comparisons.append({'activityId': aid, 'activityName': mi.get('activityName'), 'classification': 'UNAVAILABLE', 'scenarioTargetFinish': scenario_target, 'nextUpdateForecastFinish': next_finish, 'differenceDays': None})
            continue
        diff = (next_finish_d - scenario_target_d).days
        classification = 'ON_TRACK' if diff <= 0 else ('PARTIALLY_REALIZED' if diff <= 3 else 'NOT_REALIZED')
        comparisons.append({
            'activityId': aid, 'activityName': mi.get('activityName'), 'classification': classification,
            'scenarioTargetFinish': scenario_target, 'nextUpdateForecastFinish': next_finish_d.isoformat(), 'differenceDays': diff,
        })

    return {
        'available': True, 'nextVersionId': str(current_version.id), 'nextVersionLabel': current_version.version_label,
        'nextDataDate': current_dd.isoformat() if current_dd else None, 'milestoneComparisons': comparisons,
    }


def build_schedule_risk_recovery_section(
    project, current_version, previous_version_id: Optional[str] = None,
    group_by: str = 'discipline', lookahead_weeks: int = 4,
) -> Dict[str, Any]:
    """Builds the Weekly Report's 'Schedule Risk & Recovery' section from
    risk_register.build_risk_register() (identical computation the Risk
    Register API uses — never recalculated here) plus the project's
    persisted ScheduleRisk workflow rows, RecoveryScenario records, and
    MitigationAction rows. Every risk/scenario/action returned here is the
    FULL applicable population for this version — the PDF renderer decides
    what to summarize; the Excel exporter is expected to render all of it.
    This function never truncates."""
    from .models import MitigationAction, RecoveryScenario, ScheduleRisk

    if not current_version:
        return {'available': False, 'reason': 'No schedule version available.'}

    ordered = list(version_chronology.chronological_versions(project))
    previous_version = None
    if previous_version_id:
        previous_version = next((v for v in ordered if str(v.id) == str(previous_version_id)), None)
    else:
        previous_version, _previous_unresolved = version_chronology.previous_resolution(ordered, current_version)

    if not previous_version:
        return {
            'available': False,
            'reason': 'Schedule Risk & Recovery requires at least two schedule versions (Previous -> Current) to detect update-to-update risk signals.',
            'currentVersionId': str(current_version.id), 'currentVersionLabel': current_version.version_label,
        }

    baseline_version = _find_baseline_version(ordered)
    current_dd = resolve_effective_data_date(current_version)['dataDate']
    previous_dd = resolve_effective_data_date(previous_version)['dataDate']
    calendars = _calendars_for_versions(baseline_version, current_version, previous_version)

    ui_result = update_intelligence.build_update_intelligence(
        previous_version.activities_json, current_version.activities_json, previous_dd, current_dd,
        baseline_version.activities_json if baseline_version else None, calendars, group_by, lookahead_weeks,
    )
    lookahead_result = baseline_progress.build_report_lookahead(
        baseline_version.activities_json if baseline_version else [], current_version.activities_json,
        data_date=current_dd, calendars=calendars, weeks=lookahead_weeks,
    )
    register = risk_register.build_risk_register(ui_result, lookahead_result, current_version.activities_json, current_dd, group_by)

    risk_keys = [r['riskKey'] for r in register['risks']]
    workflow_by_key = {sr.risk_key: sr for sr in ScheduleRisk.objects.filter(project=project, risk_key__in=risk_keys)}
    for r in register['risks']:
        sr = workflow_by_key.get(r['riskKey'])
        r['workflow'] = {
            'status': sr.status, 'owner': sr.owner, 'mitigationNotes': sr.mitigation_notes,
            'targetDate': sr.target_date.isoformat() if sr.target_date else None,
        } if sr else {'status': 'OPEN', 'owner': '', 'mitigationNotes': '', 'targetDate': None}

    scenarios = RecoveryScenario.objects.filter(project=project).select_related('risk', 'schedule_upload')
    scenario_rows = []
    for s in scenarios:
        result = s.result or {}
        scenario_rows.append({
            'id': str(s.id), 'name': s.name, 'status': s.status,
            'riskKey': s.risk.risk_key if s.risk_id else None,
            'sourceVersionLabel': s.schedule_upload.version_label or s.schedule_upload.original_filename,
            'sourceDataDate': s.schedule_upload.data_date.isoformat() if s.schedule_upload.data_date else None,
            'actionCount': len(s.assumptions or []), 'assumptions': s.assumptions,
            'currentForecastFinish': result.get('currentForecastFinish'),
            'scenarioForecastFinish': result.get('scenarioForecastFinish'),
            'recoveryDays': result.get('recoveryDays'),
            'projectBaselineRecovery': result.get('projectBaselineRecovery'),
            'milestoneImpact': result.get('milestoneImpact') or [],
            'sideEffects': result.get('sideEffects'),
            'warnings': result.get('warnings') or [],
            'recoveryTracking': _recovery_tracking_for_report(s, current_version, current_dd) if s.status == 'ACCEPTED' else None,
        })
    accepted_scenarios = [s for s in scenario_rows if s['status'] == 'ACCEPTED']

    actions = MitigationAction.objects.filter(project=project).select_related('risk', 'scenario')
    action_rows = [{
        'id': str(a.id), 'riskKey': a.risk.risk_key if a.risk_id else None,
        'scenarioId': str(a.scenario_id) if a.scenario_id else None,
        'description': a.description, 'owner': a.owner,
        'dueDate': a.due_date.isoformat() if a.due_date else None,
        'status': a.status, 'notes': a.notes,
        'overdue': bool(a.due_date and current_dd and a.due_date < current_dd and a.status not in ('COMPLETE', 'CANCELLED')),
    } for a in actions]
    open_actions = [a for a in action_rows if a['status'] not in ('COMPLETE', 'CANCELLED')]
    overdue_actions = [a for a in open_actions if a['overdue']]

    return {
        'available': True,
        'engineVersion': register['engineVersion'],
        'currentVersionId': str(current_version.id), 'currentVersionLabel': current_version.version_label,
        'previousVersionId': str(previous_version.id), 'previousVersionLabel': previous_version.version_label,
        'baselineVersionId': str(baseline_version.id) if baseline_version else None,
        'currentDataDate': current_dd.isoformat() if current_dd else None,
        'risks': register['risks'],
        'summary': register['summary'],
        'scenarios': scenario_rows,
        'acceptedScenarios': accepted_scenarios,
        'mitigationActions': action_rows,
        'openMitigationActionCount': len(open_actions),
        'overdueMitigationActionCount': len(overdue_actions),
    }


def build_float_intelligence_section(
    project, current_version, previous_version_id: Optional[str] = None, group_by: str = 'area',
) -> Dict[str, Any]:
    """Builds the Weekly Report's 'Float Intelligence' section from
    activity_analysis.build_activity_analysis() + float_intelligence.py —
    the exact same master rows and float aggregation the Float Analysis
    workspace and its API use. No second float calculation exists here."""
    if not current_version:
        return {'available': False, 'reason': 'No schedule version available.'}

    ordered = list(version_chronology.chronological_versions(project))
    previous_version = None
    if previous_version_id:
        previous_version = next((v for v in ordered if str(v.id) == str(previous_version_id)), None)
    else:
        previous_version, _previous_unresolved = version_chronology.previous_resolution(ordered, current_version)

    baseline_version = _find_baseline_version(ordered)
    current_dd = resolve_effective_data_date(current_version)['dataDate']
    calendars = _calendars_for_versions(baseline_version, current_version, previous_version)

    analysis = activity_analysis.build_activity_analysis(
        current_version.activities_json,
        previous_activities=previous_version.activities_json if previous_version else None,
        baseline_activities=baseline_version.activities_json if baseline_version else None,
        current_data_date=current_dd, calendars=calendars,
    )
    rows = analysis['rows']
    by_id, pred_of, _succ_of = activity_analysis.build_relationship_maps(current_version.activities_json)

    return {
        'available': True,
        'currentVersionId': str(current_version.id), 'currentVersionLabel': current_version.version_label,
        'previousVersionId': str(previous_version.id) if previous_version else None,
        'previousVersionLabel': previous_version.version_label if previous_version else None,
        'baselineVersionId': str(baseline_version.id) if baseline_version else None,
        'baselineVersionLabel': baseline_version.version_label if baseline_version else None,
        'currentDataDate': current_dd.isoformat() if current_dd else None,
        'summary': float_intelligence.compute_float_summary(rows),
        'distribution': float_intelligence.compute_float_distribution(rows),
        'heatmap': float_intelligence.compute_float_heatmap(rows, group_by),
        'topDeterioration': float_intelligence.rank_float_deterioration(rows),
        'topImprovement': float_intelligence.rank_float_improvement(rows),
        'newlyNegative': float_intelligence.list_newly_negative(rows),
        'recoveredFromNegative': float_intelligence.list_recovered_from_negative(rows),
        'milestoneFloatAnalysis': float_intelligence.compute_milestone_float_analysis(rows, pred_of, by_id),
        'calendarConfidence': analysis['calendarConfidence'],
    }


def build_progress_milestone_section(
    project, current_version, baseline_version_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Builds the Weekly Report's 'Progress & Milestones' section from
    baseline_progress.build_scurve()/milestones.build_milestone_report() —
    the exact same engines the Progress & Milestones workspace uses. No
    second progress-curve or milestone calculation exists here."""
    if not current_version:
        return {'available': False, 'reason': 'No schedule version available.'}

    ordered = list(version_chronology.chronological_versions(project))
    baseline_version = None
    if baseline_version_id:
        baseline_version = next((v for v in ordered if str(v.id) == str(baseline_version_id)), None)
    else:
        baseline_version = _find_baseline_version(ordered)

    if not baseline_version:
        return {
            'available': False, 'reason': 'No baseline schedule has been selected for this project.',
            'currentVersionId': str(current_version.id), 'currentVersionLabel': current_version.version_label,
        }

    current_dd = resolve_effective_data_date(current_version)['dataDate']
    scurve = baseline_progress.build_scurve(
        baseline_version.activities_json, current_version.activities_json, current_dd, period='weekly',
    )
    milestone_report = milestones.build_milestone_report(
        current_version.activities_json, baseline_version.activities_json,
    )
    movements = [m for m in milestone_report if m.get('varianceDays') is not None]

    return {
        'available': True,
        'currentVersionId': str(current_version.id), 'currentVersionLabel': current_version.version_label,
        'baselineVersionId': str(baseline_version.id), 'baselineVersionLabel': baseline_version.version_label,
        'currentDataDate': current_dd.isoformat() if current_dd else None,
        'scurve': scurve,
        'milestones': milestone_report,
        'milestoneCount': len(milestone_report),
        'upcomingMilestoneCount': sum(1 for m in milestone_report if m['status'] != 'Complete'),
        'slippedMilestoneCount': sum(1 for m in movements if (m['varianceDays'] or 0) > 0),
    }


def build_management_actions(executive_payload: Dict[str, Any]) -> List[Dict[str, str]]:
    """Deterministic 'where to look next' statements — see
    executive_summary.py's module docstring for the no-causal-claims rule
    this also follows. Phrased as Review/Investigate/Confirm/Evaluate,
    never as a diagnosis."""
    actions: List[Dict[str, str]] = []
    drivers = executive_payload.get('drivers') or {}
    cost_drivers = (drivers.get('cost') or {}).get('drivers') or []
    productivity_drivers = (drivers.get('productivity') or {}).get('drivers') or []

    worst_cost = next((d for d in cost_drivers if d['cv'] < 0), None)
    if worst_cost:
        actions.append({
            'type': 'COST_VARIANCE',
            'action': f'Investigate {worst_cost["group"]} — the largest unfavorable cost variance at {_fmt_money(worst_cost["cv"])}.',
        })

    if productivity_drivers:
        worst_prod = productivity_drivers[0]
        if worst_prod['productivityFactor'] is not None and worst_prod['productivityFactor'] < executive_summary.PRODUCTIVITY_HEALTHY_THRESHOLD:
            actions.append({
                'type': 'PRODUCTIVITY',
                'action': f'Review {worst_prod["group"]} — the lowest productivity factor at {worst_prod["productivityFactor"]:.2f}.',
            })

    eac_drift = executive_payload.get('eacDrift') or {}
    approved = eac_drift.get('approvedEac')
    cpi_scenario = (eac_drift.get('scenarios') or {}).get('CPI_BASED')
    if approved and cpi_scenario and cpi_scenario.get('current') is not None:
        diff = approved['value'] - cpi_scenario['current']
        if abs(diff) > 0.05 * max(abs(approved['value']), 1):
            actions.append({
                'type': 'FORECAST_RECONCILIATION',
                'action': f'Confirm forecast — approved EAC ({_fmt_money(approved["value"])}) differs from the CPI-based calculated scenario ({_fmt_money(cpi_scenario["current"])}) by {_fmt_money(diff)}.',
            })

    cpi_trend = (executive_payload.get('trends') or {}).get('cpi') or {}
    if cpi_trend.get('direction') == 'deteriorating' and cpi_trend.get('consecutiveCount', 0) >= 2:
        actions.append({
            'type': 'COST_TREND',
            'action': f'Evaluate cost performance — CPI has deteriorated for {cpi_trend["consecutiveCount"]} consecutive updates.',
        })

    milestone_movement = (executive_payload.get('_updateComparison') or {}).get('milestoneMovement') or []
    slipping = [m for m in milestone_movement if m.get('deltaDays', 0) > 0]
    if slipping:
        worst_ms = max(slipping, key=lambda m: m['deltaDays'])
        actions.append({
            'type': 'MILESTONE',
            'action': f'Review milestone "{worst_ms["activityName"]}" — slipped {worst_ms["deltaDays"]} calendar days this update.',
        })

    return actions


def compare_report_snapshots(report_a: Dict[str, Any], report_b: Dict[str, Any]) -> Dict[str, Any]:
    """Compares two PERSISTED report payloads (report_a = earlier,
    report_b = later) — reads only what's already in each snapshot,
    recomputes nothing, and reuses trend_engine.compare_metric() so the
    same metric-aware improving/deteriorating rules apply here as
    everywhere else in the app."""
    perf_a = report_a.get('currentPerformance') or {}
    perf_b = report_b.get('currentPerformance') or {}
    kpi_keys = ('cpi', 'spi', 'cv', 'sv', 'vac', 'bac', 'ac', 'ev', 'pv')
    kpi_movement = {
        k: trend_engine.compare_metric(k, (perf_a.get(k) or {}).get('current'), (perf_b.get(k) or {}).get('current'))
        for k in kpi_keys
    }

    scenarios_a = ((report_a.get('forecast') or {}).get('eacDrift') or {}).get('scenarios') or {}
    scenarios_b = ((report_b.get('forecast') or {}).get('eacDrift') or {}).get('scenarios') or {}
    eac_movement = {
        method: trend_engine.compare_metric('eac', scenarios_a.get(method, {}).get('current'), scenarios_b.get(method, {}).get('current'))
        for method in set(scenarios_a) | set(scenarios_b)
    }

    signals_a = {s['type'] for s in (report_a.get('executiveSummary') or {}).get('controlSignals', [])}
    signals_b = {s['type'] for s in (report_b.get('executiveSummary') or {}).get('controlSignals', [])}
    quality_a = set(report_a.get('dataQuality') or [])
    quality_b = set(report_b.get('dataQuality') or [])

    def _driver_groups(payload, dim):
        d = (payload.get('costProductivityDrivers') or {}).get(dim) or {}
        return {g['group'] for g in d.get('drivers') or []}

    driver_changes = {}
    for dim in ('cost', 'schedule', 'productivity'):
        groups_a, groups_b = _driver_groups(report_a, dim), _driver_groups(report_b, dim)
        driver_changes[dim] = {
            'newlyAppeared': sorted(groups_b - groups_a),
            'noLongerRanked': sorted(groups_a - groups_b),
        }

    return {
        'reportA': {'id': report_a.get('_id'), 'dataDate': (report_a.get('projectInfo') or {}).get('dataDate')},
        'reportB': {'id': report_b.get('_id'), 'dataDate': (report_b.get('projectInfo') or {}).get('dataDate')},
        'kpiMovement': kpi_movement,
        'eacMovement': eac_movement,
        'newlyRaisedSignals': sorted(signals_b - signals_a),
        'resolvedSignals': sorted(signals_a - signals_b),
        'newlyUnavailableData': sorted(quality_b - quality_a),
        'newlyAvailableData': sorted(quality_a - quality_b),
        'driverChanges': driver_changes,
        'narrativeA': (report_a.get('executiveSummary') or {}).get('narrative'),
        'narrativeB': (report_b.get('executiveSummary') or {}).get('narrative'),
    }


def _fmt_money(v: Optional[float]) -> str:
    if v is None:
        return 'unavailable'
    abs_v = abs(v)
    sign = '-' if v < 0 else ''
    if abs_v >= 1e6:
        return f'{sign}${abs_v / 1e6:.2f}M'
    if abs_v >= 1e3:
        return f'{sign}${abs_v / 1e3:.1f}K'
    return f'{sign}${abs_v:.0f}'


def build_data_quality_section(executive_payload: Dict[str, Any], schedule_perf: Dict[str, Any]) -> List[str]:
    """Consolidates every 'unavailable' condition already surfaced
    elsewhere into one plain-English list — no new checks, just collection."""
    items = list(executive_payload.get('warnings') or [])
    metrics = executive_payload.get('currentMetrics') or {}
    if metrics.get('ac', {}).get('current') is None and 'No cost-loaded' not in ' '.join(items):
        items.append('Actual cost not available for the current version.')
    if not (executive_payload.get('eacDrift') or {}).get('approvedEac'):
        items.append('No approved EAC has been entered for this project.')
    if not schedule_perf.get('available'):
        items.append('Schedule performance metrics unavailable — no schedule version found.')
    return items


# ── Top-level report builder (Phase A + C) ──────────────────────────────────

# Weekly vs Monthly differ only in which sections are emphasized/included —
# both are built from the exact same underlying facts (Phase C requirement:
# "different presentation configurations of the same authoritative data").
_REPORT_TYPE_SECTIONS = {
    'WEEKLY_PROJECT_CONTROLS': (
        'projectInfo', 'executiveSummary', 'currentPerformance', 'forecast', 'lookAhead',
        'updateIntelligence', 'floatIntelligence', 'progressMilestones', 'scheduleRiskRecovery',
        'schedulePerformance', 'updateComparison', 'managementAttention', 'dataQuality',
    ),
    'MONTHLY_EXECUTIVE': (
        'projectInfo', 'executiveSummary', 'currentPerformance', 'forecast',
        'trendHistory', 'costProductivityDrivers', 'schedulePerformance',
        'managementAttention', 'dataQuality',
    ),
}


def build_report_payload(
    project, report_type: str = 'WEEKLY_PROJECT_CONTROLS', version_id: Optional[str] = None,
    ev_method: str = 'DURATION_PCT_COMPLETE', pv_method: str = 'LINEAR_BASELINE_SPREAD',
    group_by: str = 'discipline', lookback: int = 6, enable_ai: bool = False,
    lookahead_weeks: Optional[int] = None, lookahead_from: Optional[date] = None,
    lookahead_to: Optional[date] = None, lookahead_filters: Optional[Dict[str, Any]] = None,
    baseline_version_id: Optional[str] = None,
    lookahead_top_n: int = baseline_progress.DEFAULT_TOP_DELAYED_N,
    update_previous_version_id: Optional[str] = None,
) -> Dict[str, Any]:
    if report_type not in REPORT_TYPES:
        raise ValueError(f'Unknown report_type: {report_type}. Valid: {REPORT_TYPES}')

    exec_payload = build_executive_payload(project, ev_method, pv_method, group_by, lookback)
    versions = exec_payload.pop('_versions')
    current_version = None
    if version_id:
        current_version = next((v for v in versions if str(v.id) == str(version_id)), None)
    if current_version is None:
        current_version = versions[-1] if versions else None
    previous_version = None
    if current_version and versions:
        idx = [str(v.id) for v in versions].index(str(current_version.id))
        previous_version = versions[idx - 1] if idx > 0 else None

    schedule_perf = build_schedule_performance_section(current_version)
    update_comparison = build_update_comparison_section(current_version, previous_version)
    exec_payload['_updateComparison'] = update_comparison   # used by build_management_actions
    management_actions = build_management_actions(exec_payload)
    data_quality = build_data_quality_section(exec_payload, schedule_perf)
    del exec_payload['_updateComparison']

    sections = _REPORT_TYPE_SECTIONS[report_type]

    lookahead_section = None
    if 'lookAhead' in sections:
        lookahead_section = build_lookahead_section(
            project, current_version, weeks=lookahead_weeks, from_date=lookahead_from, to_date=lookahead_to,
            filters=lookahead_filters, baseline_version_id=baseline_version_id,
            top_delayed_n=lookahead_top_n, management_list_n=lookahead_top_n,
        )
        if not lookahead_section.get('available'):
            data_quality.append(lookahead_section.get('reason') or 'Look-ahead section unavailable.')

    update_intelligence_section = None
    if 'updateIntelligence' in sections:
        update_intelligence_section = build_update_intelligence_section(
            project, current_version, previous_version_id=update_previous_version_id, group_by=group_by,
            lookahead_weeks=(lookahead_weeks or baseline_progress.DEFAULT_LOOKAHEAD_WEEKS),
        )
        if not update_intelligence_section.get('available'):
            data_quality.append(update_intelligence_section.get('reason') or 'Schedule Update Performance section unavailable.')

    float_intelligence_section = None
    if 'floatIntelligence' in sections:
        float_intelligence_section = build_float_intelligence_section(
            project, current_version, previous_version_id=update_previous_version_id, group_by=group_by,
        )
        if not float_intelligence_section.get('available'):
            data_quality.append(float_intelligence_section.get('reason') or 'Float Intelligence section unavailable.')

    progress_milestones_section = None
    if 'progressMilestones' in sections:
        progress_milestones_section = build_progress_milestone_section(
            project, current_version, baseline_version_id=baseline_version_id,
        )
        if not progress_milestones_section.get('available'):
            data_quality.append(progress_milestones_section.get('reason') or 'Progress & Milestones section unavailable.')

    schedule_risk_recovery_section = None
    if 'scheduleRiskRecovery' in sections:
        schedule_risk_recovery_section = build_schedule_risk_recovery_section(
            project, current_version, previous_version_id=update_previous_version_id, group_by=group_by,
            lookahead_weeks=(lookahead_weeks or baseline_progress.DEFAULT_LOOKAHEAD_WEEKS),
        )
        if not schedule_risk_recovery_section.get('available'):
            data_quality.append(schedule_risk_recovery_section.get('reason') or 'Schedule Risk & Recovery section unavailable.')

    # Deterministic narrative is ALWAYS the source of truth (payload
    # narrative == exec_payload['narrative'] unconditionally). AI rewrite is
    # opt-in and additive only — see executive_summary.enhance_narrative_with_ai.
    ai_narrative = executive_summary.enhance_narrative_with_ai(exec_payload['narrative']) if enable_ai else None

    payload = {
        'engineVersion': ENGINE_VERSION,
        'reportType': report_type,
        'sections': list(sections),
        'projectInfo': build_project_info_section(project, current_version, previous_version),
        'executiveSummary': {
            'narrative': exec_payload['narrative'],
            'aiNarrative': ai_narrative,
            'controlSignals': exec_payload['controlSignals'],
            'warnings': exec_payload['warnings'],
        },
        'currentPerformance': exec_payload['currentMetrics'],
        'forecast': {
            'eacDrift': exec_payload['eacDrift'],
            'forecastSignals': exec_payload['forecastSignals'],
        },
        'lookAhead': lookahead_section,
        'updateIntelligence': update_intelligence_section,
        'scheduleRiskRecovery': schedule_risk_recovery_section,
        'floatIntelligence': float_intelligence_section,
        'progressMilestones': progress_milestones_section,
        'trendHistory': exec_payload['trends'] if 'trendHistory' in sections else None,
        'schedulePerformance': schedule_perf,
        'costProductivityDrivers': exec_payload['drivers'] if 'costProductivityDrivers' in sections else None,
        'updateComparison': update_comparison if 'updateComparison' in sections else None,
        'managementAttention': management_actions,
        'dataQuality': data_quality,
        'methodology': exec_payload['methodology'],
        'traceability': {
            'project': {'id': str(project.id), 'name': project.name},
            'scheduleVersionId': str(current_version.id) if current_version else None,
            'sourceUploadFilename': current_version.original_filename if current_version else None,
            'dataDate': current_version.data_date.isoformat() if current_version and current_version.data_date else None,
            'engineVersion': ENGINE_VERSION,
            'evMethod': ev_method, 'pvMethod': pv_method,
            'forecastMethod': 'CPI_BASED / BOTTOM_UP / CPI_SPI_COMPOSITE (all independently tracked)',
            'lookAhead': {
                'baselineVersionId': lookahead_section.get('baselineVersionId'),
                'currentVersionId': lookahead_section.get('currentVersionId'),
                'dataDate': lookahead_section.get('dataDate'),
                'window': lookahead_section.get('window'),
                'filters': lookahead_section.get('filters'),
                'histogramMetric': (lookahead_section.get('histogram') or {}).get('metric'),
                'scurveMetric': (lookahead_section.get('scurve') or {}).get('metric'),
                'topDelayedN': lookahead_section.get('topDelayedN'),
                'managementListN': lookahead_section.get('managementListN'),
            } if (lookahead_section and lookahead_section.get('available')) else None,
        },
        # Drivers are always included in raw form for the "Cost & Productivity
        # Drivers" section even on the weekly report's underlying data, in
        # case a future section wants them — the *visible* section list
        # above controls what a presentation layer actually renders.
        '_allDrivers': exec_payload['drivers'],
    }
    return payload
