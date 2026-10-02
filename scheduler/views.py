import json
import os
import re
import time
from datetime import date as _date_type, timedelta

from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods

from .utils import (
    compute_metrics,
    load_table_from_csv,
    load_table_from_excel,
    normalize_activities,
    parse_date,
)
from .parsers import (
    extract_pdf_text,
    extract_xer_reference_data,
    parse_msp_xml,
    parse_pdf_schedule,
    parse_project_meta_from_xer,
    parse_xer,
    xer_to_activities,
)
from .status_engine import (
    classify_schedule,
    ThresholdValues,
    MilestoneInput,
)
from .quality_engine import assess_quality
from .narrative_engine import generate_narrative
from .column_mapping import build_activities_from_mapping, build_activities_preview, build_excel_preview
from .schedule_metadata import compute_upload_metadata, derive_version_label, PARSER_VERSION
from .data_date_detection import (
    detect_data_date_pdf, detect_data_date_tabular, detect_data_date_xer, resolve_effective_data_date,
)
from .schedule_comparison import compare_schedules
from .calendar_engine import CalendarDefinition
from .progress_curve import compute_progress_curve
from .schedule_risk import compute_risk
from .milestones import build_milestone_report, upcoming_within_horizon
from .ai_context import build_ai_context, classify_focus
from .ai import get_provider
from .ai.prompts import build_chat_system_prompt, build_review_system_prompt
from .recovery_engine import run_scenario
from . import risk_register
from . import driving_chain
from . import activity_analysis
from . import version_chronology
from . import float_intelligence
from .cost_engine import compute_cost_summary, compute_productivity, EV_METHODS, PV_METHODS, GROUP_FIELD_MAP
from . import trend_engine
from . import driver_engine
from . import executive_summary
from . import report_service
from . import baseline_progress
from . import update_intelligence
from . import schedule_identity
from . import contractual_milestones


def serialize_date(value):
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def normalize_upload_data(data):
    activities = []
    for row in data:
        if not isinstance(row, dict):
            continue
        raw = dict(row)
        for key in ["start", "finish", "bStart", "bFinish"]:
            if key in raw and isinstance(raw[key], str):
                raw[key] = parse_date(raw[key])
        activities.append(raw)
    return activities


def error_response(message, status=400):
    return JsonResponse({"error": message}, status=status)


def _effective_data_date(schedule_version):
    """Thin wrapper around data_date_detection.resolve_effective_data_date()
    for the common case where a call site only needs the resolved date (or
    None) — never upload_timestamp, never today. See that function's
    docstring for the full provenance dict when a response needs to surface
    availability/source to the caller."""
    return resolve_effective_data_date(schedule_version)['dataDate']


def _calendars_for(*schedule_uploads):
    """Builds a calendarId -> CalendarDefinition map from one or more
    ScheduleUpload objects' persisted Calendar rows, for passing into
    compare_schedules() as its optional working-day-aware `calendars` arg.
    Only rows with has_detailed_definition True yield an entry — everything
    else is deliberately omitted so compare_schedules() reports
    'unavailable' rather than guessing."""
    calendars = {}
    for su in schedule_uploads:
        if not su:
            continue
        for cal in su.calendars.all():
            cal_def = CalendarDefinition.from_stored(
                cal.standard_workweek, cal.exceptions, cal.has_detailed_definition
            )
            if cal_def is not None:
                calendars[cal.calendar_id] = cal_def
    return calendars


_WEEKDAY_ABBR = {1: 'Mon', 2: 'Tue', 3: 'Wed', 4: 'Thu', 5: 'Fri', 6: 'Sat', 7: 'Sun'}


def _calendar_meta_for(*schedule_uploads):
    """Builds a calendarId -> {name, hoursPerDay, workingDaysPerWeek,
    workweekDescription} map for display purposes (Activity Analysis's
    Calendar columns) — separate from _calendars_for()'s CalendarDefinition
    map, which is for date-math only. Only calendars with a confidently
    decoded standard_workweek get a workweekDescription/workingDaysPerWeek;
    hoursPerDay is only included when the source file actually reported it
    (never assumed 8)."""
    meta = {}
    for su in schedule_uploads:
        if not su:
            continue
        for cal in su.calendars.all():
            working_days_per_week = None
            workweek_description = None
            if cal.has_detailed_definition and cal.standard_workweek:
                working_iso_days = sorted(
                    int(k) for k, v in cal.standard_workweek.items() if v
                )
                working_days_per_week = len(working_iso_days)
                if working_iso_days:
                    workweek_description = ', '.join(_WEEKDAY_ABBR.get(d, str(d)) for d in working_iso_days)
            meta[cal.calendar_id] = {
                'name': cal.name or cal.calendar_id,
                'hoursPerDay': cal.hours_per_day,
                'workingDaysPerWeek': working_days_per_week,
                'workweekDescription': workweek_description,
            }
    return meta


@csrf_exempt
@require_POST
def upload(request):
    upload_file = request.FILES.get("file")
    if not upload_file:
        return error_response("No file uploaded")

    MAX_UPLOAD_MB = 100
    if upload_file.size > MAX_UPLOAD_MB * 1024 * 1024:
        return error_response(
            f"File too large ({upload_file.size // (1024*1024)} MB). "
            f"Maximum allowed size is {MAX_UPLOAD_MB} MB.",
            status=413,
        )

    extension = os.path.splitext(upload_file.name)[1].lower()
    source_label = upload_file.name

    try:
        if extension == ".xer":
            text = upload_file.read().decode("utf-8", errors="ignore")
            sections = parse_xer(text)
            activities = xer_to_activities(sections, upload_file.name)
            if not activities:
                return error_response("Could not parse XER file — no activities found. "
                                      "Ensure the file is a valid Primavera P6 XER export.", status=422)
            # Convert date objects to ISO strings for JSON serialisation
            from datetime import date as date_type
            for a in activities:
                for field in ("start", "finish", "bStart", "bFinish",
                              "earlyStart", "earlyFinish", "lateStart", "lateFinish",
                              "remainStart", "remainFinish"):
                    val = a.get(field)
                    if isinstance(val, date_type):
                        a[field] = val.isoformat()
            # Detect resource / cost loading at file level
            _non_ms = [a for a in activities if not a.get("isMilestone")]
            _total  = len(_non_ms) or 1
            _rsrc_n = sum(1 for a in _non_ms if a.get("isResourceLoaded"))
            _cost_n = sum(1 for a in _non_ms if a.get("isCostLoaded"))
            _rsrc_load_pct = round(_rsrc_n / _total * 100)
            _cost_load_pct = round(_cost_n / _total * 100)
        elif extension == ".xlsx":
            df = load_table_from_excel(upload_file, engine="openpyxl")
            activities = normalize_activities(df, upload_file.name, source_label)
        elif extension == ".xls":
            df = load_table_from_excel(upload_file, engine="xlrd")
            activities = normalize_activities(df, upload_file.name, source_label)
        elif extension == ".csv":
            df = load_table_from_csv(upload_file)
            activities = normalize_activities(df, upload_file.name, source_label)
        elif extension == ".xml":
            xml_text = upload_file.read().decode("utf-8", errors="ignore")
            activities = parse_msp_xml(xml_text, upload_file.name)
            if not activities:
                return error_response(
                    "Could not parse MS Project XML. Ensure the file is exported from "
                    "Microsoft Project via File → Save As → XML Format.",
                    status=422,
                )
        elif extension == ".pdf":
            activities = parse_pdf_schedule(upload_file)
            if not activities:
                return error_response(
                    "Could not extract schedule data from PDF. "
                    "Best results come from PDFs with a clear tabular layout "
                    "(e.g. exported from P6, Excel, or MS Project).",
                    status=422,
                )
        elif extension == ".mpp":
            return error_response(
                "MPP binary files cannot be parsed directly. "
                "Export from Microsoft Project as XML (File → Save As → XML Format) "
                "and upload the .xml file instead.",
                status=415,
            )
        else:
            return error_response(
                "Unsupported file type. Upload .xer, .xlsx, .xls, .csv, .xml (MS Project), or .pdf",
                status=415,
            )
        import time, json as _json
        from datetime import date as _date

        # Ensure every activity value is JSON-safe (catch stray date/datetime objects)
        def _safe(v):
            if isinstance(v, _date):
                return v.isoformat()
            return v

        safe_acts = []
        for a in activities:
            safe_acts.append({k: _safe(v) for k, v in a.items()})

        # _rsrc_n / _total etc. are only set in the XER branch; default for other formats
        _rsrc_n        = locals().get('_rsrc_n', 0)
        _cost_n        = locals().get('_cost_n', 0)
        _total         = locals().get('_total', 1)
        _rsrc_load_pct = locals().get('_rsrc_load_pct', 0)
        _cost_load_pct = locals().get('_cost_load_pct', 0)

        base_name = os.path.splitext(upload_file.name)[0]
        response = {
            "id": f"{base_name}-{int(time.time() * 1000)}",
            "name": base_name,
            "file": upload_file.name,
            "source": extension.lstrip("."),
            "sourceFile": upload_file.name,
            "activities": safe_acts,
            "actCount": len(safe_acts),
            "isResourceLoaded": _rsrc_n / _total >= 0.5,
            "isCostLoaded":     _cost_n / _total >= 0.5,
            "resourceLoadPct":  _rsrc_load_pct,
            "costLoadPct":      _cost_load_pct,
        }
        # Serialise manually so any remaining type errors are caught inside our try/except
        payload = _json.dumps(response, default=str)
        from django.http import HttpResponse
        return HttpResponse(payload, content_type="application/json")
    except Exception as exc:
        import traceback
        return error_response(f"Unable to parse file: {str(exc)} | {traceback.format_exc()[-800:]}", status=422)


@csrf_exempt
@require_POST
def metrics(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return error_response("Request body must be valid JSON")

    activities = payload.get("activities")
    if not isinstance(activities, list):
        return error_response("activities must be a list")

    # Optional data date — lets the frontend run "what-if at next update" analysis
    data_date_str = payload.get("dataDate")
    data_date = parse_date(data_date_str) if data_date_str else None

    normalized = normalize_upload_data(activities)
    try:
        result = compute_metrics(normalized, data_date=data_date)
    except Exception as exc:
        import traceback
        return error_response(f"Metrics computation error: {str(exc)} | {traceback.format_exc()[-500:]}", status=500)

    # Authoritative cost/EVM figures — computed by the same cost_engine.py
    # used by Project Controls, on this same activity list. Additive only:
    # every key above this point is untouched (existing consumers of the
    # legacy evm/manHours fields keep working), but EVMView/HistogramView
    # now read CPI/SPI/EAC/etc from here instead, since the legacy fields
    # can silently default CPI to 1.0 or use duration as a cost proxy —
    # this engine never does (see cost_engine.py's module docstring).
    resolved_date = data_date if isinstance(data_date, _date_type) else _date_type.today()
    try:
        result['authoritativeEvm'] = compute_cost_summary(normalized, resolved_date)
        result['authoritativeProductivity'] = compute_productivity(normalized)
        by_project = {}
        for a in normalized:
            by_project.setdefault(a.get('projectId') or 'unknown', []).append(a)
        result['authoritativeEvmByProject'] = {
            pid: compute_cost_summary(acts, resolved_date) for pid, acts in by_project.items()
        }
    except Exception:
        result['authoritativeEvm'] = None
        result['authoritativeProductivity'] = None
        result['authoritativeEvmByProject'] = {}

    return JsonResponse(result, safe=False)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/analyze/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def analyze(request):
    """
    Run the schedule status engine against the provided activities.

    Body (JSON):
      current_activities   list  required  Activities from the current update
      baseline_activities  list  optional  Activities from the approved baseline
      previous_activities  list  optional  Activities from the previous update
      milestones           list  optional  Milestone definitions with contract dates
      data_date            str   optional  ISO date — overrides data date in activities
      contract_finish_date str   optional  ISO contract completion date
      thresholds           dict  optional  Threshold overrides (any ThresholdValues field)
    """
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    current = payload.get('current_activities') or payload.get('activities')
    if not isinstance(current, list) or not current:
        return error_response('current_activities must be a non-empty list')

    baseline = payload.get('baseline_activities') or []
    previous = payload.get('previous_activities') or []

    raw_milestones = payload.get('milestones') or []
    ms_inputs = []
    for m in raw_milestones:
        try:
            ms_inputs.append(MilestoneInput(
                activity_id=m.get('activityId') or m.get('activity_id') or '',
                description=m.get('description') or m.get('name') or '',
                category=m.get('category') or 'INFORMATIONAL',
                contract_required_date=parse_date(m.get('contractRequiredDate') or m.get('contract_required_date')),
                must_finish_by_date=parse_date(m.get('mustFinishByDate') or m.get('must_finish_by_date')),
                allowable_variance_days=float(m.get('allowableVarianceDays') or m.get('allowable_variance_days') or 0),
                is_contractual=bool(m.get('isContractual') or m.get('is_contractual')),
            ))
        except Exception:
            pass

    data_date = parse_date(payload.get('data_date') or payload.get('dataDate'))
    contract_finish = parse_date(payload.get('contract_finish_date') or payload.get('contractFinishDate'))

    # Build threshold overrides if any were provided
    threshold_overrides = payload.get('thresholds') or {}
    if threshold_overrides:
        T = ThresholdValues(**{k: float(v) for k, v in threshold_overrides.items()
                               if hasattr(ThresholdValues, k)})
    else:
        T = ThresholdValues()

    # Try loading the default profile from the DB — ignored if models aren't migrated
    try:
        from .models import ThresholdProfile
        if not threshold_overrides:
            profile = ThresholdProfile.get_or_create_default()
            T = ThresholdValues.from_model(profile)
    except Exception:
        pass

    def _prep(acts):
        out = []
        for a in (acts or []):
            d = dict(a)
            for f in ('start', 'finish', 'bStart', 'bFinish',
                      'earlyStart', 'earlyFinish', 'lateStart', 'lateFinish',
                      'remainStart', 'remainFinish'):
                if isinstance(d.get(f), str):
                    d[f] = parse_date(d[f])
            out.append(d)
        return out

    try:
        result = classify_schedule(
            current_activities=_prep(current),
            baseline_activities=_prep(baseline) if baseline else None,
            previous_activities=_prep(previous) if previous else None,
            milestone_inputs=ms_inputs or None,
            thresholds=T,
            data_date=data_date,
            contract_finish_date=contract_finish,
        )
        return JsonResponse(result.to_dict())
    except Exception as exc:
        import traceback
        return error_response(
            f'Classification error: {str(exc)} | {traceback.format_exc()[-600:]}',
            status=500,
        )


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/quality/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def quality(request):
    """
    Run only the quality engine against the provided activities.

    Body (JSON):
      activities  list  required  Activity dicts
      data_date   str   optional  ISO date
    """
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    activities = payload.get('activities')
    if not isinstance(activities, list) or not activities:
        return error_response('activities must be a non-empty list')

    data_date = parse_date(payload.get('data_date') or payload.get('dataDate'))
    if data_date is None:
        data_date = _date_type.today()

    def _prep(acts):
        out = []
        for a in acts:
            d = dict(a)
            for f in ('start', 'finish', 'bStart', 'bFinish',
                      'earlyStart', 'earlyFinish', 'lateStart', 'lateFinish',
                      'remainStart', 'remainFinish'):
                if isinstance(d.get(f), str):
                    d[f] = parse_date(d[f])
            out.append(d)
        return out

    try:
        result = assess_quality(_prep(activities), data_date)
        return JsonResponse(result)
    except Exception as exc:
        import traceback
        return error_response(
            f'Quality assessment error: {str(exc)} | {traceback.format_exc()[-600:]}',
            status=500,
        )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/threshold-profiles/
# POST /api/threshold-profiles/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET', 'POST'])
def threshold_profiles(request):
    try:
        from .models import ThresholdProfile
    except ImportError:
        return error_response('Models not available', status=503)

    if request.method == 'GET':
        profiles = ThresholdProfile.objects.all().values()
        data = []
        for p in profiles:
            row = {}
            for k, v in p.items():
                if isinstance(v, (_date_type,)):
                    row[k] = v.isoformat()
                else:
                    row[k] = str(v) if hasattr(v, 'hex') else v
            data.append(row)
        return JsonResponse({'profiles': data})

    # POST — create a new profile
    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    allowed = {f for f in ThresholdValues.__dataclass_fields__}
    allowed |= {'name', 'description', 'is_default', 'created_by'}
    kwargs = {k: v for k, v in body.items() if k in allowed}
    if 'name' not in kwargs:
        kwargs['name'] = 'Custom Profile'

    try:
        profile = ThresholdProfile.objects.create(**kwargs)
        return JsonResponse({'id': str(profile.id), 'name': profile.name}, status=201)
    except Exception as exc:
        return error_response(f'Could not create profile: {exc}', status=400)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/threshold-profiles/<pk>/
# PUT /api/threshold-profiles/<pk>/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET', 'PUT', 'PATCH'])
def threshold_profile_detail(request, pk):
    try:
        from .models import ThresholdProfile
        profile = ThresholdProfile.objects.get(pk=pk)
    except Exception:
        return error_response('Profile not found', status=404)

    if request.method == 'GET':
        data = {k: (str(v) if hasattr(v, 'hex') else v)
                for k, v in profile.__dict__.items()
                if not k.startswith('_')}
        return JsonResponse(data)

    # PUT / PATCH — update thresholds
    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    allowed = {f for f in ThresholdValues.__dataclass_fields__}
    allowed |= {'name', 'description', 'is_default'}
    for k, v in body.items():
        if k in allowed:
            setattr(profile, k, v)
    try:
        profile.save()
        return JsonResponse({'id': str(profile.id), 'name': profile.name})
    except Exception as exc:
        return error_response(f'Could not update profile: {exc}', status=400)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/narrative/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def narrative(request):
    """
    Generate a structured, evidence-based schedule performance narrative.

    Body (JSON):
      current_activities   list  required  Activities from the current update
      baseline_activities  list  optional  Approved baseline activities
      previous_activities  list  optional  Previous update activities
      milestones           list  optional  User-designated milestone definitions
      data_date            str   optional  ISO date (overrides activity data dates)
      contract_finish_date str   optional  ISO contract completion date
      settings             dict  optional  {project_name, company, prepared_by,
                                            report_title, max_findings, near_crit_threshold}
    """
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    current = payload.get('current_activities') or payload.get('activities')
    if not isinstance(current, list) or not current:
        return error_response('current_activities must be a non-empty list')

    baseline = payload.get('baseline_activities') or []
    previous = payload.get('previous_activities') or []
    raw_milestones = payload.get('milestones') or []
    settings = payload.get('settings') or {}

    data_date = parse_date(payload.get('data_date') or payload.get('dataDate'))
    contract_finish = parse_date(
        payload.get('contract_finish_date') or payload.get('contractFinishDate')
    )

    def _prep(acts):
        out = []
        for a in (acts or []):
            d = dict(a)
            for f in ('start', 'finish', 'bStart', 'bFinish',
                      'earlyStart', 'earlyFinish', 'lateStart', 'lateFinish',
                      'remainStart', 'remainFinish'):
                if isinstance(d.get(f), str):
                    d[f] = parse_date(d[f])
            out.append(d)
        return out

    cur_p = _prep(current)
    bl_p = _prep(baseline) if baseline else None
    prev_p = _prep(previous) if previous else None

    # Run quality engine (required for narrative quality section). No
    # today()-fallback: assess_quality's out-of-sequence/missed-task/
    # invalid-actuals checks are genuine Data-Date-dependent CPM status
    # checks, so a missing data_date must leave the quality section
    # unavailable (caught below) rather than fabricate it against today.
    try:
        q_result = assess_quality(cur_p, data_date) if data_date else None
    except Exception:
        q_result = None

    # Run status engine
    try:
        T = ThresholdValues()
        try:
            from .models import ThresholdProfile
            profile = ThresholdProfile.get_or_create_default()
            T = ThresholdValues.from_model(profile)
        except Exception:
            pass

        ms_inputs = []
        for m in raw_milestones:
            try:
                ms_inputs.append(MilestoneInput(
                    activity_id=m.get('activityId') or m.get('activity_id') or '',
                    description=m.get('description') or '',
                    is_contractual=bool(m.get('isContractual') or m.get('is_contractual')),
                    contract_required_date=parse_date(
                        m.get('contractRequiredDate') or m.get('contract_required_date')
                    ),
                ))
            except Exception:
                pass

        s_result = classify_schedule(
            current_activities=cur_p,
            baseline_activities=bl_p,
            previous_activities=prev_p,
            milestone_inputs=ms_inputs or None,
            thresholds=T,
            data_date=data_date,
            contract_finish_date=contract_finish,
        )
        s_dict = s_result.to_dict()
    except Exception:
        s_dict = None

    # Enrich milestone inputs with status from the status result
    ms_with_status = []
    if s_dict and s_dict.get('milestone_results'):
        ms_with_status = s_dict['milestone_results']
    elif raw_milestones:
        ms_with_status = [
            {'activityId': m.get('activityId', ''), 'status': 'UNDETERMINED'}
            for m in raw_milestones
        ]

    try:
        result = generate_narrative(
            current_activities=cur_p,
            baseline_activities=bl_p,
            previous_activities=prev_p,
            milestone_inputs=ms_with_status or None,
            data_date=data_date,
            contract_finish_date=contract_finish,
            settings=settings,
            status_result=s_dict,
            quality_result=q_result,
        )
        # Merge confidence / risk scores from status engine
        if s_dict:
            result['risk_score'] = s_dict.get('risk_score', result.get('risk_score', 0))
            result['confidence_score'] = s_dict.get(
                'confidence_score', result.get('confidence_score', 0)
            )
        return JsonResponse(result)
    except Exception as exc:
        import traceback
        return error_response(
            f'Narrative generation error: {exc} | {traceback.format_exc()[-600:]}',
            status=500,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Import Center — Phase 1
#
# Shares the exact parsers /api/upload/ already uses (parse_xer, xer_to_activities,
# normalize_activities, parse_msp_xml, parse_pdf_schedule) so preview/commit never
# drift from what a plain upload would produce. Kept separate from upload() itself
# so the existing, already-working endpoint is never touched.
# ─────────────────────────────────────────────────────────────────────────────

MAX_UPLOAD_MB = 100

_XER_DATE_FIELDS = (
    'start', 'finish', 'bStart', 'bFinish', 'earlyStart', 'earlyFinish',
    'lateStart', 'lateFinish', 'remainStart', 'remainFinish',
)


class ImportParseError(ValueError):
    pass


def _read_file_for_import(upload_file):
    """
    Parse an uploaded schedule file and build its Import Center preview.

    Returns (activities, extension, preview_dict, extra_dict). `extra_dict`
    carries data import_preview doesn't need but import_commit does for
    persistence: XER project metadata + reference data (calendars/activity
    codes/UDFs), and each format's import_method tag. Empty for formats with
    nothing extra to persist. Raises ImportParseError with a user-facing
    message on failure.
    """
    extension = os.path.splitext(upload_file.name)[1].lower()

    if extension in ('.xlsx', '.xls'):
        engine = 'openpyxl' if extension == '.xlsx' else 'xlrd'
        df = load_table_from_excel(upload_file, engine=engine)
        preview = build_excel_preview(df, upload_file.name)
        # Built from preview['mappedFields'] — what the user reviews is what gets
        # imported, and fields normalize_activities() doesn't know about
        # (Contractor/Discipline/Area/System/Phase/...) are preserved, not dropped.
        activities = build_activities_from_mapping(df, preview['mappedFields'], upload_file.name)
        method = 'excel_column_mapping' if extension == '.xlsx' else 'xls_column_mapping'
        preview['dataDateDetection'] = detect_data_date_tabular(df, 'EXCEL_METADATA')
        return activities, extension, preview, {'import_method': method}

    if extension == '.csv':
        df = load_table_from_csv(upload_file)
        preview = build_excel_preview(df, upload_file.name)
        activities = build_activities_from_mapping(df, preview['mappedFields'], upload_file.name)
        preview['dataDateDetection'] = detect_data_date_tabular(df, 'CSV_METADATA')
        return activities, extension, preview, {'import_method': 'csv_column_mapping'}

    if extension == '.xer':
        text = upload_file.read().decode('utf-8', errors='ignore')
        sections = parse_xer(text)
        activities = xer_to_activities(sections, upload_file.name)
        if not activities:
            raise ImportParseError(
                'Could not parse XER file — no activities found. '
                'Ensure the file is a valid Primavera P6 XER export.'
            )
        for a in activities:
            for field in _XER_DATE_FIELDS:
                val = a.get(field)
                if isinstance(val, _date_type):
                    a[field] = val.isoformat()
        preview = build_activities_preview(activities, upload_file.name, 'XER')
        project_meta = parse_project_meta_from_xer(sections)
        preview['dataDateDetection'] = detect_data_date_xer(project_meta)
        extra = {
            'import_method': 'xer_parser',
            'project_meta': project_meta,
            'reference_data': extract_xer_reference_data(sections),
            'wbs_node_count': len(sections.get('PROJWBS', [])),
        }
        return activities, extension, preview, extra

    if extension == '.xml':
        xml_text = upload_file.read().decode('utf-8', errors='ignore')
        activities = parse_msp_xml(xml_text, upload_file.name)
        if not activities:
            raise ImportParseError(
                'Could not parse MS Project XML. Ensure the file is exported from '
                'Microsoft Project via File → Save As → XML Format.'
            )
        preview = build_activities_preview(activities, upload_file.name, 'XML')
        # No MSP-XML-specific Data Date detection implemented yet — always
        # unavailable here rather than guessed. See report Limitations.
        preview['dataDateDetection'] = {'detectedDataDate': None, 'source': None, 'confidence': None}
        return activities, extension, preview, {'import_method': 'msp_xml_parser'}

    if extension == '.pdf':
        activities = parse_pdf_schedule(upload_file)
        if not activities:
            raise ImportParseError(
                'Could not extract schedule data from PDF. Best results come from '
                'PDFs with a clear tabular layout (e.g. exported from P6, Excel, or MS Project).'
            )
        preview = build_activities_preview(activities, upload_file.name, 'PDF')
        try:
            pdf_text = extract_pdf_text(upload_file).get('text', '')
        except Exception:
            pdf_text = ''
        preview['dataDateDetection'] = detect_data_date_pdf(pdf_text)
        return activities, extension, preview, {'import_method': 'pdf_table_extraction'}

    if extension == '.mpp':
        raise ImportParseError(
            'MPP binary files cannot be parsed directly. Export from Microsoft Project '
            'as XML (File → Save As → XML Format) and upload the .xml file instead.'
        )

    raise ImportParseError(
        'Unsupported file type. Upload .xer, .xlsx, .xls, .csv, .xml (MS Project), or .pdf'
    )


def _check_upload_size(upload_file):
    if upload_file.size > MAX_UPLOAD_MB * 1024 * 1024:
        return error_response(
            f'File too large ({upload_file.size // (1024*1024)} MB). '
            f'Maximum allowed size is {MAX_UPLOAD_MB} MB.',
            status=413,
        )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/import/preview/
# ─────────────────────────────────────────────────────────────────────────────

def _destination_project_context(project_id):
    """
    Looks up an existing Project for the Import Center's "Add Schedule
    Version to Existing Project" workflow and returns identifying context
    for the Import Preview screen: current version label/Data Date (by the
    SAME role-assignment convention every other screen uses — never just
    "most recently uploaded"), version count, and the existing schedule's
    P6 Project ID (from its most recent version that actually has one) for
    the different-project-identity check. Returns None if not found —
    callers must not silently create a new project in that case.
    """
    from .models import Project

    project = Project.objects.filter(pk=project_id).first()
    if not project:
        return None

    ordered = list(version_chronology.chronological_versions(project))
    roles = _assign_version_roles(ordered)
    current = next((v for v in ordered if roles.get(str(v.id)) == 'CURRENT'), None)
    existing_p6_id = next((v.project_id_in_file for v in ordered if v.project_id_in_file), '')
    existing_p6_name = next((v.project_name_in_file for v in ordered if v.project_name_in_file), '')

    return {
        'projectId': str(project.id),
        'projectName': project.name,
        'projectNumber': project.project_number,
        'currentVersionId': str(current.id) if current else None,
        'currentVersionLabel': (current.version_label or current.original_filename) if current else None,
        'currentDataDate': current.data_date.isoformat() if current and current.data_date else None,
        'versionCount': len(ordered),
        'existingP6ProjectId': existing_p6_id or None,
        'existingP6ProjectName': existing_p6_name or None,
    }


def _identity_reference_version(project_id, reference_version_id):
    """
    Fetches the ONE ScheduleUpload row (with its full activities_json) to
    use as the Schedule Identity Evaluator's structural reference — kept
    deliberately separate from _destination_project_context (which stays
    lightweight and activities_json-free for the projects list endpoint).
    Called only from import_preview, where fetching one full version's
    activities is the expected cost of running the identity evaluator.
    """
    from .models import ScheduleUpload

    if not reference_version_id:
        return None
    return ScheduleUpload.objects.filter(pk=reference_version_id, project_id=project_id).first()


def _scan_projects_for_identity_candidate(uploaded_activities, uploaded_p6_id, uploaded_p6_name):
    """
    Import Identity Guard — proactive half. Runs BEFORE the user has chosen
    a destination, so "Create New Project" is no longer the unconditional
    default for a file that structurally matches an existing project's
    current version (the exact gap that let Barn's Sep-25 update land as
    its own Project instead of the canonical Barn project's 8th version —
    see the pre-commit provenance investigation this guard implements).

    Read-only: never creates, attaches, or modifies anything. Runs
    schedule_identity.evaluate_schedule_identity (the one authoritative
    engine — no second, TypeScript-side identity algorithm) against every
    existing project's current version and returns the single best
    SAME_PROJECT/LIKELY_SAME_PROJECT match, or None if nothing qualifies.

    Cost: one full current-version activities_json fetch + evaluation per
    existing project. Fine at this application's current project counts;
    revisit (e.g. a cheap P6-id/name prefilter before the full comparison)
    if the project count grows large enough to make that cost matter.
    """
    from .models import Project

    best = None
    best_ratio = -1.0
    for project in Project.objects.all():
        ctx = _destination_project_context(str(project.id))
        if not ctx or not ctx['currentVersionId']:
            continue
        reference_version = _identity_reference_version(ctx['projectId'], ctx['currentVersionId'])
        if reference_version is None:
            continue
        result = schedule_identity.evaluate_schedule_identity(
            reference_activities=reference_version.activities_json,
            reference_p6_id=ctx['existingP6ProjectId'],
            reference_p6_name=ctx['existingP6ProjectName'],
            uploaded_activities=uploaded_activities,
            uploaded_p6_id=uploaded_p6_id,
            uploaded_p6_name=uploaded_p6_name,
            reference_version_label=ctx['currentVersionLabel'],
        )
        if result['classification'] not in (schedule_identity.SAME_PROJECT, schedule_identity.LIKELY_SAME_PROJECT):
            continue
        ratio = result['signals']['activityIdOverlap']['referenceOverlapRatio'] or 0.0
        if ratio > best_ratio:
            best_ratio = ratio
            best = {
                'projectId': ctx['projectId'],
                'projectName': ctx['projectName'],
                'currentVersionId': ctx['currentVersionId'],
                'currentVersionLabel': ctx['currentVersionLabel'],
                'currentDataDate': ctx['currentDataDate'],
                'identity': result,
            }
    return best


@csrf_exempt
@require_POST
def import_preview(request):
    """
    Parse a schedule file and return an Import Center preview WITHOUT saving
    anything: detected columns, mapped fields, unmapped columns, invalid
    dates, duplicate Activity IDs, and warnings. Nothing is discarded silently.

    Optional `projectId` form field switches this into "Add Schedule Version
    to Existing Project" preview mode: the response additionally carries
    `destination` (the target project's identity + current version),
    `scheduleIdentity` (see schedule_identity.py — the authoritative,
    multi-signal SAME_PROJECT/LIKELY_SAME_PROJECT/UNCERTAIN/
    LIKELY_DIFFERENT_PROJECT assessment; this is what actually gates the
    confirmation requirement now, not a bare P6 Project ID comparison —
    "P6 Project ID is evidence, not identity"), `dataDateWarning` (uploaded
    Data Date is older than the project's current version — see the
    Historical/Out-of-Sequence Import workflow), `p6ProjectIdMismatch`
    (kept for backward compatibility / raw-identity display — the uploaded
    file's own P6 Project ID differs from the destination project's
    established one; item 9 requires the raw IDs never be hidden even when
    scheduleIdentity concludes LIKELY_SAME_PROJECT), and `possibleDuplicate`
    (Data Date + activity count already match an existing version of this
    project — a DIFFERENT question from schedule identity, see item 15:
    "same project" and "duplicate file" are never conflated). None of these
    block the import outright — scheduleIdentity.confirmationRequired is
    the single source of truth for whether the frontend must gate the
    Import button on an explicit acknowledgement.

    When `projectId` is OMITTED (the "Create New Project" path, previously
    unconditional), the response instead carries `identityCandidate` — the
    Import Identity Guard's proactive scan (_scan_projects_for_identity_
    candidate) of every existing project's current version, surfacing the
    single best SAME_PROJECT/LIKELY_SAME_PROJECT match if one exists (else
    null). Same evaluate_schedule_identity engine, no second algorithm.
    Also read-only and non-blocking: it is a recommendation for the
    frontend to offer "Add as Schedule Version" instead of silently
    defaulting to a new project, never an automatic redirect.
    """
    upload_file = request.FILES.get('file')
    if not upload_file:
        return error_response('No file uploaded')

    size_err = _check_upload_size(upload_file)
    if size_err:
        return size_err

    try:
        activities, _extension, preview, extra = _read_file_for_import(upload_file)
    except ImportParseError as exc:
        return error_response(str(exc), status=422)
    except Exception as exc:
        import traceback
        return error_response(f'Unable to parse file: {exc} | {traceback.format_exc()[-800:]}', status=422)

    stats = compute_upload_metadata(activities)
    preview['activityCount'] = stats['activity_count']
    preview['relationshipCount'] = stats['relationship_count']
    preview['milestoneCount'] = stats['milestone_count']

    project_meta = extra.get('project_meta') or {}
    uploaded_p6_id = project_meta.get('project_id') or ''
    preview['projectMeta'] = (
        {'p6ProjectId': uploaded_p6_id or None, 'p6ProjectName': project_meta.get('project_name') or None}
        if project_meta else None
    )

    detected_iso = (preview.get('dataDateDetection') or {}).get('detectedDataDate')
    detected_date = parse_date(detected_iso) if detected_iso else None

    proposed_label = derive_version_label(detected_date, timezone.now())
    proposed_classification = 'CURRENT_UPDATE'

    project_id = request.POST.get('projectId') or None
    destination = None
    data_date_warning = None
    p6_mismatch = None
    possible_duplicate = None
    schedule_identity_result = None
    identity_candidate = None

    if project_id:
        destination = _destination_project_context(project_id)
        if destination is None:
            return error_response('Destination project not found. Choose an existing project or import as a new project.', status=404)

        if destination['currentDataDate'] and detected_date:
            if detected_date < parse_date(destination['currentDataDate']):
                data_date_warning = {
                    'reason': "This schedule's Data Date is earlier than the project's current schedule.",
                    'currentDataDate': destination['currentDataDate'],
                    'uploadedDataDate': detected_date.isoformat(),
                }
                # A historical import must never silently steal the CURRENT
                # role from the genuinely-current version — see
                # _assign_version_roles, which picks CURRENT by upload order
                # among CURRENT_UPDATE-classified versions.
                proposed_classification = 'PREVIOUS_UPDATE'

        if destination['existingP6ProjectId'] and uploaded_p6_id and destination['existingP6ProjectId'] != uploaded_p6_id:
            p6_mismatch = {
                'existingP6ProjectId': destination['existingP6ProjectId'],
                'uploadedP6ProjectId': uploaded_p6_id,
            }

        # Schedule Identity Evaluator — the authoritative multi-signal
        # assessment (item 14: uses the project's genuine reference version,
        # normally CURRENT, falling back to the baseline when that's all
        # that exists — exactly what _destination_project_context's own
        # `current` resolution already computes).
        reference_version = _identity_reference_version(project_id, destination['currentVersionId'])
        if reference_version is not None:
            schedule_identity_result = schedule_identity.evaluate_schedule_identity(
                reference_activities=reference_version.activities_json,
                reference_p6_id=destination['existingP6ProjectId'],
                reference_p6_name=destination['existingP6ProjectName'],
                uploaded_activities=activities,
                uploaded_p6_id=uploaded_p6_id or None,
                uploaded_p6_name=(project_meta.get('project_name') or None),
                reference_version_label=destination['currentVersionLabel'],
            )

        from .models import ScheduleUpload
        for v in ScheduleUpload.objects.filter(project_id=project_id):
            same_date = v.data_date and detected_date and v.data_date == detected_date
            same_count = v.activity_count == stats['activity_count']
            same_p6 = (not v.project_id_in_file or not uploaded_p6_id) or v.project_id_in_file == uploaded_p6_id
            if same_date and same_count and same_p6:
                possible_duplicate = {
                    'matchingVersionId': str(v.id),
                    'matchingVersionLabel': v.version_label or v.original_filename,
                    'reason': f'An existing version ({v.version_label or v.original_filename}) already has the same Data Date and activity count.',
                }
                break
    else:
        # Import Identity Guard — no destination was chosen, so "Create New
        # Project" was about to happen unconditionally. Proactively check
        # whether this upload structurally matches an existing project's
        # current version before defaulting to that. Read-only: nothing is
        # created or attached here — the frontend decides what to do with
        # `identityCandidate`, same as scheduleIdentity above never gated
        # the import outright on its own.
        identity_candidate = _scan_projects_for_identity_candidate(
            uploaded_activities=activities,
            uploaded_p6_id=uploaded_p6_id or None,
            uploaded_p6_name=(project_meta.get('project_name') or None),
        )

    preview['destination'] = destination
    preview['dataDateWarning'] = data_date_warning
    preview['p6ProjectIdMismatch'] = p6_mismatch
    preview['possibleDuplicate'] = possible_duplicate
    preview['scheduleIdentity'] = schedule_identity_result
    preview['identityCandidate'] = identity_candidate
    preview['proposedVersionLabel'] = proposed_label
    preview['proposedClassification'] = proposed_classification

    return JsonResponse(preview, safe=False)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/import/commit/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def import_commit(request):
    """
    Parse a schedule file, persist it as a ScheduleUpload (schedule version)
    attached to a Project, and return the same activity payload shape as
    /api/upload/ so the existing frontend load path keeps working unchanged —
    plus projectId/scheduleUploadId for the Import Center and future phases.

    Body (multipart/form-data):
      file           required  the schedule file
      projectId      optional  attach to an existing Project
      projectName    optional  name for a newly created Project (defaults to filename)
      classification optional  ScheduleUpload.schedule_classification (default CURRENT_UPDATE)
    """
    upload_file = request.FILES.get('file')
    if not upload_file:
        return error_response('No file uploaded')

    size_err = _check_upload_size(upload_file)
    if size_err:
        return size_err

    project_id = request.POST.get('projectId') or None
    project_name = request.POST.get('projectName') or None
    classification = request.POST.get('classification') or 'CURRENT_UPDATE'
    version_label_override = request.POST.get('versionLabel') or None

    try:
        activities, extension, preview, extra = _read_file_for_import(upload_file)
    except ImportParseError as exc:
        return error_response(str(exc), status=422)
    except Exception as exc:
        import traceback
        return error_response(f'Unable to parse file: {exc} | {traceback.format_exc()[-800:]}', status=422)

    def _safe(v):
        return v.isoformat() if isinstance(v, _date_type) else v

    safe_acts = [{k: _safe(v) for k, v in a.items()} for a in activities]

    from .models import (
        ActivityCode, ActivityCodeType, Calendar, Project, SCHEDULE_CLASSIFICATION_CHOICES,
        ScheduleUpload, UDFType,
    )

    if project_id:
        # Explicit "add to existing project" — never silently fall back to
        # creating a new project if the id doesn't resolve (e.g. deleted
        # mid-flow); the caller asked for a specific destination.
        project = Project.objects.filter(pk=project_id).first()
        if project is None:
            return error_response('Destination project not found. Choose an existing project or import as a new project.', status=404)
    else:
        project = Project.objects.create(name=project_name or os.path.splitext(upload_file.name)[0])

    valid_classifications = {c[0] for c in SCHEDULE_CLASSIFICATION_CHOICES}
    if classification not in valid_classifications:
        classification = 'CURRENT_UPDATE'

    # ── Phase 1.5: derive ScheduleUpload metadata from what was actually parsed ──
    stats = compute_upload_metadata(safe_acts)
    project_meta = extra.get('project_meta') or {}
    reference_data = extra.get('reference_data')

    # ── Data Date: detected-from-file, reviewed, optionally overridden ───────
    # preview['dataDateDetection'] (data_date_detection.py) is the single
    # source of "what the file itself says" across every format — XER's
    # PROJECT.last_recalc_date, an Excel/CSV labeled cell, or a PDF's
    # "Data Date:" text. The optional `dataDate` POST field is what the user
    # left in the Import Preview's editable field (defaults to the detected
    # value client-side, but the server never trusts that default blindly —
    # it compares against its own detection to decide data_date_overridden).
    detection = preview.get('dataDateDetection') or {}
    detected_iso = detection.get('detectedDataDate')
    detected_source = detection.get('source') or ''
    detected_confidence = detection.get('confidence') or ''
    source_data_date = parse_date(detected_iso) if detected_iso else None

    user_data_date_raw = (request.POST.get('dataDate') or '').strip()
    if user_data_date_raw:
        user_data_date = parse_date(user_data_date_raw)
        if user_data_date is None:
            return error_response('dataDate could not be parsed. Use YYYY-MM-DD.')
        if source_data_date is not None and user_data_date == source_data_date:
            data_date = user_data_date
            data_date_overridden = False
            data_date_source = detected_source
            data_date_confidence = detected_confidence
        else:
            data_date = user_data_date
            data_date_overridden = True
            data_date_source = 'USER_ENTERED'
            data_date_confidence = ''
    else:
        # No explicit value posted — auto-accept the detected date (or None
        # if nothing was detected; never the upload timestamp or today).
        data_date = source_data_date
        data_date_overridden = False
        data_date_source = detected_source
        data_date_confidence = detected_confidence

    planned_start = project_meta.get('planned_start') or stats.get('planned_start')
    forecast_finish = project_meta.get('forecast_finish') or stats.get('forecast_finish')
    must_finish_by = project_meta.get('must_finish_by')
    baseline_finish = stats.get('baseline_finish')

    calendar_names_from_activities = {
        a.get('calendarName') or a.get('calendar')
        for a in safe_acts if a.get('calendarName') or a.get('calendar')
    }
    calendar_count = (
        len(reference_data['calendars']) if reference_data else len(calendar_names_from_activities)
    )
    wbs_node_count = extra.get('wbs_node_count')
    if wbs_node_count is None:
        wbs_node_count = len({a.get('wbs') for a in safe_acts if a.get('wbs')})

    version_label = version_label_override or derive_version_label(data_date, timezone.now())

    # upload_timestamp drives CURRENT/PREVIOUS role assignment (see
    # _assign_version_roles) via `-upload_timestamp` ordering. On fast
    # imports (rapid successive commits, e.g. scripted bulk import or
    # back-to-back test requests) timezone.now()'s clock resolution can tie
    # two rows, making that ordering non-deterministic. Guarantee strictly
    # increasing timestamps per project so import order is always preserved.
    upload_ts = timezone.now()
    if project is not None:
        latest = ScheduleUpload.objects.filter(project=project).order_by('-upload_timestamp').first()
        if latest is not None and upload_ts <= latest.upload_timestamp:
            upload_ts = latest.upload_timestamp + timedelta(microseconds=1)

    schedule_upload = None
    try:
        schedule_upload = ScheduleUpload.objects.create(
            project=project,
            upload_timestamp=upload_ts,
            original_filename=upload_file.name,
            sanitized_filename=re.sub(r'[^A-Za-z0-9_.-]', '_', upload_file.name),
            file_type=extension.lstrip('.').upper(),
            file_size_bytes=upload_file.size,
            project_id_in_file=project_meta.get('project_id', ''),
            project_name_in_file=project_meta.get('project_name', ''),
            data_date=data_date,
            source_data_date=source_data_date,
            data_date_overridden=data_date_overridden,
            data_date_source=data_date_source,
            data_date_confidence=data_date_confidence,
            planned_start=planned_start,
            forecast_finish=forecast_finish,
            must_finish_by=must_finish_by,
            baseline_finish=baseline_finish,
            activity_count=stats['activity_count'],
            relationship_count=stats['relationship_count'],
            wbs_node_count=wbs_node_count,
            calendar_count=calendar_count,
            milestone_count=stats['milestone_count'],
            completed_count=stats['completed_count'],
            in_progress_count=stats['in_progress_count'],
            not_started_count=stats['not_started_count'],
            critical_count=stats['critical_count'],
            negative_float_count=stats['negative_float_count'],
            min_total_float=stats['min_total_float'],
            max_total_float=stats['max_total_float'],
            avg_total_float=stats['avg_total_float'],
            version_label=version_label,
            import_method=extra.get('import_method', ''),
            parser_version=PARSER_VERSION,
            schedule_classification=classification,
            activities_json=safe_acts,
            parsing_warnings=preview.get('warnings', []),
            missing_required_data=preview.get('missingRequiredFields', []),
            mapped_columns=preview.get('mappedFields', {}),
            unmapped_columns=preview.get('unmappedColumns', []),
            duplicate_activity_ids=preview.get('duplicateActivityIds', []),
            invalid_date_count=preview.get('invalidDateCount', 0),
        )

        # XER reference data — shared calendars/activity-code taxonomy/UDF
        # definitions, persisted as normalized rows rather than repeated
        # inside every activity's JSON.
        if reference_data:
            if reference_data.get('calendars'):
                Calendar.objects.bulk_create([
                    Calendar(schedule_upload=schedule_upload, **c) for c in reference_data['calendars']
                ])
            if reference_data.get('code_types'):
                type_objs = ActivityCodeType.objects.bulk_create([
                    ActivityCodeType(schedule_upload=schedule_upload, **t) for t in reference_data['code_types']
                ])
                type_by_id = {t.code_type_id: t for t in type_objs}
                code_objs = []
                for c in reference_data.get('codes', []):
                    type_obj = type_by_id.get(c['code_type_id'])
                    if not type_obj:
                        continue
                    code_objs.append(ActivityCode(
                        code_type=type_obj, code_id=c['code_id'], code_value=c['code_value'],
                        description=c['description'], parent_code_id=c['parent_code_id'],
                    ))
                if code_objs:
                    ActivityCode.objects.bulk_create(code_objs)
            if reference_data.get('udf_types'):
                UDFType.objects.bulk_create([
                    UDFType(schedule_upload=schedule_upload, **u) for u in reference_data['udf_types']
                ])
    except Exception:
        # Persistence is best-effort — the import must still succeed for the
        # frontend even if the DB write fails (e.g. migrations not yet applied).
        schedule_upload = None

    base_name = os.path.splitext(upload_file.name)[0]
    response = {
        'id': f'{base_name}-{int(time.time() * 1000)}',
        'name': base_name,
        'file': upload_file.name,
        'source': extension.lstrip('.'),
        'sourceFile': upload_file.name,
        'activities': safe_acts,
        'actCount': len(safe_acts),
        'projectId': str(project.id) if project else None,
        'projectName': project.name if project else None,
        'scheduleUploadId': str(schedule_upload.id) if schedule_upload else None,
        'versionLabel': version_label,
        'importWarnings': preview.get('warnings', []),
        # Effective Data Date this version was committed with — lets the
        # frontend update the toolbar Data Date immediately without a
        # redundant follow-up request. See data_date_detection.py.
        'dataDate': data_date.isoformat() if data_date else None,
        'sourceDataDate': source_data_date.isoformat() if source_data_date else None,
        'dataDateOverridden': data_date_overridden,
    }
    payload = json.dumps(response, default=str)
    return HttpResponse(payload, content_type='application/json')


# ─────────────────────────────────────────────────────────────────────────────
# GET  /api/projects/
# POST /api/projects/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET', 'POST'])
def projects(request):
    from .models import Project

    if request.method == 'GET':
        qs = Project.objects.all().order_by('name')
        # withVersions=true: analytical views (Intelligence) only want
        # projects that actually have a schedule to analyze — an emptied
        # project stays visible to the project-management screens (which
        # omit this flag) so it can still be deleted deliberately.
        only_with_versions = request.GET.get('withVersions') in ('true', '1', 'True')
        data = []
        for p in qs:
            ctx = _destination_project_context(str(p.id)) or {}
            if only_with_versions and not ctx.get('versionCount', 0):
                continue
            data.append({
                'id': str(p.id),
                'name': p.name,
                'projectNumber': p.project_number,
                'client': p.client,
                'location': p.location,
                'createdAt': p.created_at.isoformat(),
                'updatedAt': p.updated_at.isoformat(),
                'versionCount': ctx.get('versionCount', 0),
                'currentVersionLabel': ctx.get('currentVersionLabel'),
                'currentDataDate': ctx.get('currentDataDate'),
            })
        return JsonResponse({'projects': data})

    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    name = body.get('name')
    if not name:
        return error_response('name is required')

    project = Project.objects.create(
        name=name,
        project_number=body.get('projectNumber', ''),
        client=body.get('client', ''),
        location=body.get('location', ''),
        description=body.get('description', ''),
    )
    return JsonResponse({'id': str(project.id), 'name': project.name}, status=201)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<pk>/
# ─────────────────────────────────────────────────────────────────────────────

def _project_dependent_counts(project):
    """Counts of the records that go away with a project — reported back
    by DELETE so the caller can show exactly what was removed."""
    return {
        'scheduleVersions': project.schedule_versions.count(),
        'recoveryScenarios': project.recovery_scenarios.count(),
        'scheduleRisks': project.schedule_risks.count(),
        'mitigationActions': project.mitigation_actions.count(),
        'documents': project.documents.count(),
        'controlsReports': project.controls_reports.count(),
        'manualCostEntries': project.manual_cost_entries.count(),
    }


def _delete_project(project):
    """The single project-deletion path (DELETE endpoint and reconcile both
    use it) so cascade behavior is identical everywhere."""
    from django.db import transaction

    pid = str(project.id)
    with transaction.atomic():
        removed = _project_dependent_counts(project)
        name = project.name
        project.delete()
    return {'deleted': True, 'projectId': pid, 'projectName': name, 'removed': removed}


@csrf_exempt
@require_http_methods(['GET', 'DELETE'])
def project_detail(request, pk):
    """GET: project + version list. DELETE: the ONLY way a project stops
    existing — every schedule version and all project-owned analytical/
    derived records are removed via the models' existing on_delete=CASCADE
    rules (no parallel bookkeeping), so Intelligence (which reads the same
    Project/ScheduleUpload rows) stops showing it immediately."""
    from django.db import transaction
    from .models import Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    if request.method == 'DELETE':
        return JsonResponse(_delete_project(project))

    versions = _serialize_versions(project)

    return JsonResponse({
        'id': str(project.id),
        'name': project.name,
        'projectNumber': project.project_number,
        'client': project.client,
        'location': project.location,
        'description': project.description,
        'versions': versions,
    })


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/projects/reconcile/
# ─────────────────────────────────────────────────────────────────────────────

def _norm_name(v):
    import re
    return re.sub(r'\.(xer|xml|mpp|xlsx?)$', '', (v or '').strip().lower())


def classify_projects_against_main_app(app_files):
    """Compare backend Project/ScheduleUpload rows with the files the main
    application actually holds (`app_files`: [{projectId, scheduleUploadId,
    name}], reported by the browser - the only place that list exists).

    KEEP    a version of the project is referenced by a main-app file.
            Versions of a kept project that the app doesn't list are
            reported as REVIEW (never auto-deleted: the app may simply not
            have loaded every historical update).
    NOT_LOADED  nothing in THIS browser's main app references the project or
            any of its versions. This is only "not loaded here" - it is NOT
            evidence the schedule is disposable and never authorizes deletion.
            Only the consolidation planner (exact content duplicates whose
            retained twin is re-verified) can propose deleting a version.
    REVIEW  identity can't be established safely: a main-app file names
            this project id but a different/missing version id, or a
            main-app file with no backend ids has the same name.
    """
    from .models import Project

    app_version_ids = {str(f.get('scheduleUploadId')) for f in app_files if f.get('scheduleUploadId')}
    app_project_ids = {str(f.get('projectId')) for f in app_files if f.get('projectId')}
    unlinked_names = {_norm_name(f.get('name')) for f in app_files
                      if not (f.get('projectId') and f.get('scheduleUploadId')) and f.get('name')}

    out = []
    for p in Project.objects.all().order_by('name', 'created_at'):
        versions = list(p.schedule_versions.all().order_by('upload_timestamp'))
        vinfo = [{'id': str(v.id), 'label': v.version_label or v.original_filename,
                  'activityCount': v.activity_count, 'inMainApp': str(v.id) in app_version_ids} for v in versions]
        matched = [v for v in vinfo if v['inMainApp']]
        if matched:
            cls, reason = 'KEEP', 'A schedule version of this project exists in the main application.'
            for v in vinfo:
                v['classification'] = 'KEEP' if v['inMainApp'] else 'REVIEW'
        elif str(p.id) in app_project_ids:
            cls, reason = 'REVIEW', 'The main application references this project id, but not any of its current versions.'
            for v in vinfo:
                v['classification'] = 'REVIEW'
        elif _norm_name(p.name) in unlinked_names or any(_norm_name(v['label']) in unlinked_names for v in vinfo):
            cls, reason = 'REVIEW', 'A main-application file with no backend link has the same name.'
            for v in vinfo:
                v['classification'] = 'REVIEW'
        else:
            cls, reason = 'NOT_LOADED', 'Not loaded in this browser - REVIEW. Absence from the main application is not a reason to delete a historical schedule.'
            for v in vinfo:
                v['classification'] = 'NOT_LOADED'
        out.append({'projectId': str(p.id), 'name': p.name, 'createdAt': p.created_at.isoformat(),
                    'classification': cls, 'reason': reason, 'versions': vinfo})
    return out


@csrf_exempt
@require_http_methods(['POST'])
def projects_reconcile(request):
    """Body: {mainAppFiles: [{projectId, scheduleUploadId, name}]}. READ-ONLY.

    Reports which backend projects this browser's main application currently
    references (KEEP), which it merely does not have loaded (NOT_LOADED) and
    which cannot be identified safely (REVIEW). There is deliberately NO
    apply mode: "not currently loaded in this browser" never means "safe to
    delete". Removing anything goes through the consolidation planner, which
    only ever proposes exact content duplicates whose retained twin exists."""
    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')
    if body.get('apply'):
        return error_response('Deleting projects because they are absent from the main application is not supported. '
                              'Use the consolidation plan: it deletes only exact duplicate versions whose retained copy is verified.',
                              status=400)
    app_files = body.get('mainAppFiles')
    if not isinstance(app_files, list) or not app_files:
        return error_response('mainAppFiles is empty - refusing to reconcile: with no main-application files loaded, every project would look unreferenced.')

    classified = classify_projects_against_main_app(app_files)
    summary = {c: sum(1 for x in classified if x['classification'] == c) for c in ('KEEP', 'NOT_LOADED', 'REVIEW')}
    return JsonResponse({'dryRun': True, 'mainAppFileCount': len(app_files),
                         'totalProjects': len(classified), 'totalVersions': sum(len(x['versions']) for x in classified),
                         'summary': summary, 'projects': classified})


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/projects/consolidation-plan/   (READ-ONLY dry-run)
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['POST'])
def projects_consolidation_plan(request):
    """Sync Intelligence consolidation DRY-RUN. Body (all optional):
    {nameContains, mainAppFiles: [{projectId, scheduleUploadId, name}], validate: bool}.
    Returns the KEEP / DELETE CANDIDATE / REVIEW plan for collapsing
    duplicate single-version projects into one logical project per schedule
    history. There is no apply mode: this endpoint never writes. With
    validate=true it additionally proves (inside a transaction that is
    always rolled back) that every retained version resolves through the
    real Risk/Activity/Float/Update endpoints once repointed."""
    from . import project_consolidation

    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')
    plan = project_consolidation.build_consolidation_plan(body.get('nameContains'), body.get('mainAppFiles'))
    if body.get('validate'):
        plan['validation'] = project_consolidation.validate_plan_with_rollback(plan)
    return JsonResponse(plan)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/projects/consolidation-simulate/   (always rolled back)
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['POST'])
def projects_consolidation_simulate(request):
    """Runs the exact consolidation for {nameContains, mainAppFiles} inside a
    transaction and reports the resulting state, then ALWAYS rolls back."""
    from . import project_consolidation as pc

    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')
    if not body.get('mainAppFiles'):
        return error_response('mainAppFiles is required')
    return JsonResponse(pc.simulate_apply_with_rollback(body.get('nameContains'), body['mainAppFiles']))


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/projects/consolidation-apply/   (confirmation-gated, all-or-nothing)
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['POST'])
def projects_consolidation_apply(request):
    """Executes a previously reviewed consolidation plan. Requires:
    expectedPlanFingerprint (the plan the user approved), confirmation
    ("CONSOLIDATE") and mainAppFiles (the browser's own file list). Any
    mismatch refuses without changing anything; success is one transaction."""
    from . import project_consolidation as pc

    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')
    try:
        result = pc.apply_consolidation_plan(
            body.get('nameContains'), body.get('mainAppFiles'), body.get('expectedPlanFingerprint'),
            body.get('confirmation'), body.get('newName'))
    except pc.ConsolidationRefused as exc:
        return error_response(str(exc), status=exc.status)
    return JsonResponse(result)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/projects/version-difference-report/   (READ-ONLY)
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['POST'])
def version_difference_report(request):
    """Body: {versionA, versionB, sample?}. Deterministic report of what
    differs between two schedule versions (added/removed activities, dates,
    relationships, durations, constraints, float, Data Date/source metadata).
    Works across projects because both ids are explicit; writes nothing."""
    from .models import ScheduleUpload
    from . import version_difference

    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')
    try:
        a = ScheduleUpload.objects.select_related('project').filter(pk=body.get('versionA')).first()
        b = ScheduleUpload.objects.select_related('project').filter(pk=body.get('versionB')).first()
    except Exception:            # malformed id (not a UUID)
        a = b = None
    if not a or not b:
        return error_response('versionA and versionB must both be existing schedule versions', status=404)
    return JsonResponse(version_difference.build_difference_report(a, b, int(body.get('sample') or version_difference.DEFAULT_SAMPLE)))


# ─────────────────────────────────────────────────────────────────────────────
# Version summaries — shared by project_detail and /api/projects/<id>/versions/
# ─────────────────────────────────────────────────────────────────────────────

def _assign_version_roles(ordered_versions):
    """
    Returns {version_id_str: role} where role is one of
    CURRENT / PREVIOUS / BASELINE / OTHER - a suggestion for the frontend's
    default Compare-Updates selection, not a stored/authoritative field.

    Chronology follows the EFFECTIVE DATA DATE (version_chronology), never
    the upload timestamp: a June schedule imported in September does not
    become CURRENT. See version_chronology for same-Data-Date conflicts.
    """
    roles, _info = version_chronology.assign_roles(ordered_versions)
    return roles


def _assign_version_roles_ex(ordered_versions):
    """(roles, info) - info explains an unresolved PREVIOUS / CURRENT conflict."""
    return version_chronology.assign_roles(ordered_versions)


def _serialize_version_summary(v, role):
    return {
        'id': str(v.id),
        'projectId': str(v.project_id) if v.project_id else None,
        'filename': v.original_filename,
        'fileType': v.file_type,
        'p6ProjectId': v.project_id_in_file or None,
        'p6ProjectName': v.project_name_in_file or None,
        'classification': v.schedule_classification,
        'versionLabel': v.version_label or v.original_filename,
        'role': role,
        'uploadedAt': v.upload_timestamp.isoformat(),
        'dataDate': v.data_date.isoformat() if v.data_date else None,
        'sourceDataDate': v.source_data_date.isoformat() if v.source_data_date else None,
        'dataDateOverridden': v.data_date_overridden,
        'dataDateSource': v.data_date_source or None,
        'dataDateConfidence': v.data_date_confidence or None,
        'plannedStart': v.planned_start.isoformat() if v.planned_start else None,
        'projectFinish': v.forecast_finish.isoformat() if v.forecast_finish else None,
        'baselineFinish': v.baseline_finish.isoformat() if v.baseline_finish else None,
        'activityCount': v.activity_count,
        'relationshipCount': v.relationship_count,
        'wbsNodeCount': v.wbs_node_count,
        'calendarCount': v.calendar_count,
        'milestoneCount': v.milestone_count,
        'completedCount': v.completed_count,
        'inProgressCount': v.in_progress_count,
        'notStartedCount': v.not_started_count,
        'criticalCount': v.critical_count,
        'negativeFloatCount': v.negative_float_count,
        'minTotalFloat': v.min_total_float,
        'maxTotalFloat': v.max_total_float,
        'avgTotalFloat': v.avg_total_float,
        'importMethod': v.import_method,
        'parserVersion': v.parser_version,
        'fileSizeBytes': v.file_size_bytes,
    }


def _serialize_versions(project):
    ordered = list(version_chronology.chronological_versions(project))
    roles, info = _assign_version_roles_ex(ordered)
    conflict_ids = {str(v.id) for vs in version_chronology.data_date_conflicts(ordered).values() for v in vs}
    out = []
    for v in ordered:
        row = _serialize_version_summary(v, roles[str(v.id)])
        row['dataDateConflict'] = str(v.id) in conflict_ids
        if roles[str(v.id)] == 'CURRENT':
            row['previousUnresolved'] = info['previousUnresolved']
            row['currentConflict'] = info['currentConflict']
        out.append(row)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/versions/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET'])
def project_versions(request, pk):
    """
    List every schedule version for a project WITHOUT activities_json — safe
    for a version picker even on projects with many large uploads. Each entry
    is tagged with a suggested role (CURRENT/PREVIOUS/BASELINE/OTHER) so the
    frontend can default the Compare Updates picker sensibly.
    """
    from .models import Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    return JsonResponse({'projectId': str(project.id), 'versions': _serialize_versions(project)})


def _current_role(project, version):
    """Role of one version within its project's FULL version set — never
    compute this from a single-version list, which would always say
    CURRENT regardless of the version's true position."""
    ordered = list(version_chronology.chronological_versions(project))
    roles = _assign_version_roles(ordered)
    return roles.get(str(version.id), 'OTHER')


def _delete_schedule_version(project, version):
    """Deletes ONE schedule version. Dependent records are handled by the
    models' existing on_delete rules: version-owned data (milestone
    definitions, calendars, activity codes, UDFs, cost accounts, analyses
    where it is the current upload, recovery scenarios built on it)
    cascades; project-level records that merely reference it (saved report
    snapshots, documents, manual cost entries, risk first-identified
    pointers, mitigation-action links, analysis baseline/previous
    pointers) are detached (SET_NULL) rather than deleted. Nothing
    belonging to another surviving version is touched.

    CURRENT/PREVIOUS/BASELINE are never stored (see
    _assign_version_roles — computed from classification + upload order),
    so there is nothing to reassign: they re-resolve from the survivors on
    the next read. In particular, deleting the version classified as the
    baseline leaves NO baseline until one is explicitly designated via the
    existing classification PATCH — no version is auto-promoted."""
    from django.db import transaction

    was_role = _current_role(project, version)
    label = version.version_label or version.original_filename
    with transaction.atomic():
        version.delete()

    ordered = list(version_chronology.chronological_versions(project))
    roles = _assign_version_roles(ordered)
    role_ids = {r: None for r in ('CURRENT', 'PREVIOUS', 'BASELINE')}
    for vid, role in roles.items():
        if role in role_ids:
            role_ids[role] = vid
    return JsonResponse({
        'deleted': True, 'projectId': str(project.id), 'versionId': str(version.id) if version.id else None,
        'versionLabel': label, 'deletedRole': was_role,
        'remainingVersionCount': len(ordered),
        'projectNowEmpty': len(ordered) == 0,
        'roles': {'currentVersionId': role_ids['CURRENT'], 'previousVersionId': role_ids['PREVIOUS'],
                  'baselineVersionId': role_ids['BASELINE']},
        'baselineDesignated': role_ids['BASELINE'] is not None,
    })


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/projects/<id>/versions/<version_id>/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['PATCH', 'DELETE'])
def project_version_detail(request, pk, version_id):
    """
    Edits a schedule version after import, without re-uploading the file.
    Never touches activities_json or any other parsed/computed field.

    Body (JSON), exactly one of:
      dataDate           string 'YYYY-MM-DD' — sets a new effective date;
                          source_data_date is preserved unchanged, override flag set True.
      restoreSourceDate  true — resets data_date back to source_data_date
                          (400 if no source_data_date exists to restore).
      classification     one of SCHEDULE_CLASSIFICATION_CHOICES — deliberate
                          role reassignment (e.g. promoting a historical
                          import to CURRENT_UPDATE, or designating a new
                          APPROVED_BASELINE). This is the ONLY role-mutation
                          mechanism — CURRENT/PREVIOUS/BASELINE are always
                          computed fresh from classification + upload order
                          by _assign_version_roles, never stored directly, so
                          changing this one field is sufficient; no cascading
                          updates to other versions are needed or performed.
    """
    from .models import Project, ScheduleUpload, SCHEDULE_CLASSIFICATION_CHOICES

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    version = ScheduleUpload.objects.filter(pk=version_id, project=project).first()
    if not version:
        return error_response('Schedule version not found for this project', status=404)

    if request.method == 'DELETE':
        return _delete_schedule_version(project, version)

    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    if body.get('classification'):
        new_classification = body['classification']
        valid = {c[0] for c in SCHEDULE_CLASSIFICATION_CHOICES}
        if new_classification not in valid:
            return error_response(f'Unknown classification. Valid: {sorted(valid)}')
        version.schedule_classification = new_classification
        version.save(update_fields=['schedule_classification'])
        return JsonResponse(_serialize_version_summary(version, _current_role(project, version)))

    if body.get('restoreSourceDate'):
        if not version.source_data_date:
            return error_response('No source Data Date was detected for this version — nothing to restore.')
        version.data_date = version.source_data_date
        version.data_date_overridden = False
        # data_date_source/confidence already reflect the original detection
        # and were never changed by the override — nothing else to reset.
        version.save(update_fields=['data_date', 'data_date_overridden'])
        return JsonResponse(_serialize_version_summary(version, _current_role(project, version)))

    new_date_raw = (body.get('dataDate') or '').strip()
    if not new_date_raw:
        return error_response('Provide either dataDate or restoreSourceDate:true')

    new_date = parse_date(new_date_raw)
    if new_date is None:
        return error_response('dataDate could not be parsed. Use YYYY-MM-DD.')

    version.data_date = new_date
    if version.source_data_date and new_date == version.source_data_date:
        version.data_date_overridden = False
    else:
        version.data_date_overridden = True
        version.data_date_source = 'USER_ENTERED'
        version.data_date_confidence = ''
    version.save(update_fields=['data_date', 'data_date_overridden', 'data_date_source', 'data_date_confidence'])

    return JsonResponse(_serialize_version_summary(version, _current_role(project, version)))


# ─────────────────────────────────────────────────────────────────────────────
# GET  /api/projects/<id>/documents/
# POST /api/projects/<id>/documents/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET', 'POST'])
def project_documents(request, pk):
    """
    Schedule-document storage (Phase 3) — narrative reports, lookaheads,
    owner/contractor reports. Text is extracted and stored for later AI
    analysis; it is never auto-merged into schedule activities. Structured
    schedule-TABLE PDFs should go through /api/import/preview/ + commit/
    (already handled since Phase 1) — this endpoint is for the document's
    prose, not its data.
    """
    from .models import Project, ScheduleDocument

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    if request.method == 'GET':
        docs = ScheduleDocument.objects.filter(project=project).order_by('-uploaded_at')
        data = [{
            'id': str(d.id),
            'filename': d.filename,
            'fileType': d.file_type,
            'documentType': d.document_type,
            'scheduleUploadId': str(d.schedule_upload_id) if d.schedule_upload_id else None,
            'uploadedAt': d.uploaded_at.isoformat(),
            'pageCount': d.page_count,
            'extractionStatus': d.extraction_status,
            'extractionWarnings': d.extraction_warnings,
            'textLength': len(d.extracted_text or ''),
        } for d in docs]
        return JsonResponse({'projectId': str(project.id), 'documents': data})

    upload_file = request.FILES.get('file')
    if not upload_file:
        return error_response('No file uploaded')

    size_err = _check_upload_size(upload_file)
    if size_err:
        return size_err

    document_type = request.POST.get('documentType') or 'GENERAL_ATTACHMENT'
    valid_types = {c[0] for c in ScheduleDocument._meta.get_field('document_type').choices}
    if document_type not in valid_types:
        document_type = 'GENERAL_ATTACHMENT'
    schedule_upload_id = request.POST.get('scheduleUploadId') or None

    extension = os.path.splitext(upload_file.name)[1].lower()
    if extension == '.pdf':
        extraction = extract_pdf_text(upload_file)
    elif extension in ('.txt', '.md'):
        try:
            extraction = {
                'text': upload_file.read().decode('utf-8', errors='ignore'),
                'page_count': None, 'status': 'SUCCESS', 'warnings': [],
            }
        except Exception as exc:
            extraction = {'text': '', 'page_count': None, 'status': 'FAILED', 'warnings': [str(exc)]}
    else:
        return error_response(
            'Unsupported document type. Upload a .pdf, .txt, or .md file. '
            'Structured schedule files (.xer/.xlsx/.csv/.xml) belong in the Import Center.',
            status=415,
        )

    schedule_upload = None
    if schedule_upload_id:
        from .models import ScheduleUpload
        schedule_upload = ScheduleUpload.objects.filter(pk=schedule_upload_id, project=project).first()

    doc = ScheduleDocument.objects.create(
        project=project,
        schedule_upload=schedule_upload,
        filename=upload_file.name,
        file_type=extension.lstrip('.').upper(),
        document_type=document_type,
        extracted_text=extraction['text'],
        page_count=extraction['page_count'],
        extraction_status=extraction['status'],
        extraction_warnings=extraction['warnings'],
    )

    return JsonResponse({
        'id': str(doc.id),
        'filename': doc.filename,
        'documentType': doc.document_type,
        'extractionStatus': doc.extraction_status,
        'extractionWarnings': doc.extraction_warnings,
        'pageCount': doc.page_count,
        'textLength': len(doc.extracted_text or ''),
    }, status=201)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/projects/<id>/compare/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def project_compare(request, pk):
    """
    Compare two schedule versions belonging to the same project. Full
    comparison is computed once server-side; the activity-level `changes`
    list is filtered/paginated here so large schedules never ship their
    entire diff to the browser in one response.

    Body (JSON):
      previousVersionId  required
      currentVersionId   required
      page               optional  1-indexed, default 1
      pageSize           optional  default 100, max 500
      filters            optional  {area, wbs, contractor, discipline, changeType, severity}
    """
    from .models import Project, ScheduleUpload

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    previous_id = body.get('previousVersionId')
    current_id = body.get('currentVersionId')
    if not previous_id or not current_id:
        return error_response('previousVersionId and currentVersionId are required')

    previous_version = ScheduleUpload.objects.filter(pk=previous_id, project=project).first()
    current_version = ScheduleUpload.objects.filter(pk=current_id, project=project).first()
    if not previous_version or not current_version:
        return error_response(
            'Both versions must belong to this project. One or both version IDs were not found here.',
            status=404,
        )

    try:
        calendars = _calendars_for(previous_version, current_version)
        result = compare_schedules(previous_version.activities_json, current_version.activities_json, calendars)
    except Exception as exc:
        import traceback
        return error_response(f'Comparison error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    # ── Filter the activity-level changes list ──────────────────────────────
    filters = body.get('filters') or {}
    changes = result['activities']['changes']

    if filters.get('changeType'):
        wanted = {str(filters['changeType']).lower()}
        changes = [c for c in changes if c['field'].lower() == list(wanted)[0] or c['fieldKey'].lower() in wanted]
    if filters.get('severity'):
        changes = [c for c in changes if c['severity'] == filters['severity']]

    if any(filters.get(k) for k in ('area', 'wbs', 'contractor', 'discipline')):
        curr_by_id = {
            str(a.get('code') or a.get('id') or ''): a for a in current_version.activities_json
        }
        for meta_field in ('area', 'wbs', 'contractor', 'discipline'):
            wanted = filters.get(meta_field)
            if not wanted:
                continue
            changes = [
                c for c in changes
                if str((curr_by_id.get(c['activityId']) or {}).get(meta_field, '')).lower() == str(wanted).lower()
            ]

    total_changes = len(changes)
    page = max(1, int(body.get('page') or 1))
    page_size = min(500, max(1, int(body.get('pageSize') or 100)))
    start = (page - 1) * page_size
    page_changes = changes[start:start + page_size]

    return JsonResponse({
        'previousVersion': {
            'id': str(previous_version.id), 'versionLabel': previous_version.version_label,
            'dataDate': previous_version.data_date.isoformat() if previous_version.data_date else None,
            'sourceDataDate': previous_version.source_data_date.isoformat() if previous_version.source_data_date else None,
            'dataDateOverridden': previous_version.data_date_overridden,
            'activityCount': previous_version.activity_count,
        },
        'currentVersion': {
            'id': str(current_version.id), 'versionLabel': current_version.version_label,
            'dataDate': current_version.data_date.isoformat() if current_version.data_date else None,
            'sourceDataDate': current_version.source_data_date.isoformat() if current_version.source_data_date else None,
            'dataDateOverridden': current_version.data_date_overridden,
            'activityCount': current_version.activity_count,
        },
        'summary': {
            'activityCountBefore': result['activities']['countBefore'],
            'activityCountAfter': result['activities']['countAfter'],
            'activitiesAdded': result['activities']['addedCount'],
            'activitiesRemoved': result['activities']['removedCount'],
            'activitiesChanged': result['activities']['changedActivityCount'],
            'relationshipsAdded': result['relationships']['addedCount'],
            'relationshipsRemoved': result['relationships']['removedCount'],
            'relationshipsChanged': result['relationships']['changedCount'],
            'movedLater': result['activities']['movedLaterCount'],
            'movedEarlier': result['activities']['movedEarlierCount'],
            'floatDeteriorated': result['activities']['floatDeterioratedCount'],
            'floatImproved': result['activities']['floatImprovedCount'],
            'newlyCritical': result['activities']['newlyCriticalCount'],
            'noLongerCritical': result['activities']['noLongerCriticalCount'],
            'newlyNegativeFloat': result['activities']['newlyNegativeFloatCount'],
            'recoveredFromNegativeFloat': result['activities']['recoveredFromNegativeFloatCount'],
        },
        'added': result['activities']['added'],
        'removed': result['activities']['removed'],
        'changes': page_changes,
        'changesPagination': {
            'page': page, 'pageSize': page_size, 'totalCount': total_changes,
            'totalPages': (total_changes + page_size - 1) // page_size if total_changes else 1,
        },
        'relationshipChanges': result['relationships'],
        'milestoneMovement': result['milestoneMovement'],
        'insights': result['insights'],
        'summaryNarrative': result['summaryNarrative'],
        'engineVersion': result['engineVersion'],
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/progress-curve/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET'])
def project_progress_curve(request, pk):
    """
    Query params:
      currentVersionId   required
      baselineVersionId  optional
      previousVersionId  optional
      weighting          optional  'duration' (default) or 'count'
      period             optional  'monthly' (default) or 'weekly'
    """
    from .models import Project, ScheduleUpload

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    current_id = request.GET.get('currentVersionId')
    if not current_id:
        return error_response('currentVersionId is required')

    current_version = ScheduleUpload.objects.filter(pk=current_id, project=project).first()
    if not current_version:
        return error_response('currentVersionId was not found on this project', status=404)

    baseline_version = None
    baseline_id = request.GET.get('baselineVersionId')
    if baseline_id:
        baseline_version = ScheduleUpload.objects.filter(pk=baseline_id, project=project).first()
        if not baseline_version:
            return error_response('baselineVersionId was not found on this project', status=404)

    previous_version = None
    previous_id = request.GET.get('previousVersionId')
    if previous_id:
        previous_version = ScheduleUpload.objects.filter(pk=previous_id, project=project).first()
        if not previous_version:
            return error_response('previousVersionId was not found on this project', status=404)

    weighting = request.GET.get('weighting') or 'duration'
    period = request.GET.get('period') or 'monthly'

    try:
        result = compute_progress_curve(
            current_version.activities_json,
            weighting=weighting,
            period=period,
            baseline_activities=baseline_version.activities_json if baseline_version else None,
            previous_activities=previous_version.activities_json if previous_version else None,
        )
    except Exception as exc:
        import traceback
        return error_response(f'Progress curve error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    return JsonResponse(result)


def _resolve_latest_version(project, version_id):
    """Look up an explicit ScheduleUpload id, or fall back to the project's
    CURRENT-role version (same _assign_version_roles convention the version
    list/badges already use — classification-aware, not just "most recently
    uploaded"). Only used by endpoints where the API can reasonably default
    to "the current schedule" (risk/milestones/cost/EV/productivity/AI/etc.)
    — the compare/progress-curve endpoints require explicit IDs since
    defaulting a comparison silently would be misleading.

    Before this fix, the fallback was a bare "most recent upload" — which
    meant importing a HISTORICAL/out-of-sequence schedule (an older Data
    Date, uploaded after the genuinely current one) would silently become
    every default-resolved endpoint's "current" version the moment it was
    uploaded, even though the version list's own CURRENT badge (driven by
    _assign_version_roles) correctly kept pointing at the real current
    version. Falling back to the CURRENT-role version here makes every
    consumer agree with that badge. Only when NO version carries the
    CURRENT_UPDATE classification (e.g. a project with only a baseline) do
    we fall back to the newest upload, preserving the original behavior for
    that edge case."""
    from .models import ScheduleUpload
    if version_id:
        return ScheduleUpload.objects.filter(pk=version_id, project=project).first()
    ordered = list(version_chronology.chronological_versions(project))
    if not ordered:
        return None
    roles = _assign_version_roles(ordered)
    current = next((v for v in ordered if roles.get(str(v.id)) == 'CURRENT'), None)
    return current or ordered[0]


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/risk/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET'])
def project_risk(request, pk):
    """
    Query params:
      version          optional  ScheduleUpload id (defaults to the newest version)
      compareVersion   optional  enables finish_slip/newly_negative_float/logic_changes drivers
      group_by         optional  activity | wbs | area | discipline | contractor | system
      threshold        optional  near-critical float threshold in days (default 10)
      area/discipline/contractor/system  optional  pre-filter activities before scoring
    """
    from .models import Project, ScheduleUpload

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    version = _resolve_latest_version(project, request.GET.get('version'))
    if not version:
        return error_response('No schedule version available for this project', status=404)

    activities = version.activities_json
    for meta_field in ('area', 'discipline', 'contractor', 'system'):
        wanted = request.GET.get(meta_field)
        if wanted:
            activities = [a for a in activities if str(a.get(meta_field, '')).lower() == wanted.lower()]

    data_date = _effective_data_date(version)

    comparison_result = None
    compare_version_id = request.GET.get('compareVersion')
    if compare_version_id:
        compare_version = ScheduleUpload.objects.filter(pk=compare_version_id, project=project).first()
        if compare_version:
            calendars = _calendars_for(compare_version, version)
            comparison_result = compare_schedules(compare_version.activities_json, activities, calendars)

    try:
        near_critical_days = float(request.GET.get('threshold') or 10)
    except (TypeError, ValueError):
        near_critical_days = 10.0

    try:
        result = compute_risk(
            activities, data_date, group_by=request.GET.get('group_by'),
            near_critical_days=near_critical_days, comparison_result=comparison_result,
        )
    except Exception as exc:
        import traceback
        return error_response(f'Risk computation error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    return JsonResponse({
        'projectId': str(project.id),
        'versionId': str(version.id),
        'versionLabel': version.version_label,
        'dataDate': data_date.isoformat() if hasattr(data_date, 'isoformat') else data_date,
        **result,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/milestones/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET'])
def project_milestones(request, pk):
    """
    Query params:
      version          optional  ScheduleUpload id (defaults to the newest version)
      baselineVersion  optional  enables baselineFinish/varianceDays
      compareVersion   optional  enables previousUpdateFinish/movementSinceLastUpdateDays
      slipped / negativeFloat / movedThisUpdate / newlyCritical   optional  'true' to filter
      horizonDays      optional  only milestones forecast within N days of the data date
    """
    from .models import Project, ScheduleUpload

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    version = _resolve_latest_version(project, request.GET.get('version'))
    if not version:
        return error_response('No schedule version available for this project', status=404)

    baseline_activities = None
    baseline_id = request.GET.get('baselineVersion')
    if baseline_id:
        bv = ScheduleUpload.objects.filter(pk=baseline_id, project=project).first()
        if bv:
            baseline_activities = bv.activities_json

    comparison_result = None
    compare_id = request.GET.get('compareVersion')
    if compare_id:
        cv = ScheduleUpload.objects.filter(pk=compare_id, project=project).first()
        if cv:
            calendars = _calendars_for(cv, version)
            comparison_result = compare_schedules(cv.activities_json, version.activities_json, calendars)

    try:
        report = build_milestone_report(
            version.activities_json, baseline_activities=baseline_activities,
            comparison_result=comparison_result,
        )
    except Exception as exc:
        import traceback
        return error_response(f'Milestone report error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    if request.GET.get('slipped') == 'true':
        report = [m for m in report if (m['varianceDays'] or 0) > 0]
    if request.GET.get('negativeFloat') == 'true':
        report = [m for m in report if m['totalFloat'] is not None and m['totalFloat'] < 0]
    if request.GET.get('movedThisUpdate') == 'true':
        report = [m for m in report if m['movementSinceLastUpdateDays']]
    if request.GET.get('newlyCritical') == 'true':
        report = [m for m in report if m['isCritical']]

    horizon = request.GET.get('horizonDays')
    if horizon:
        try:
            data_date = _effective_data_date(version)
            report = upcoming_within_horizon(report, data_date, float(horizon))
        except (TypeError, ValueError):
            pass

    return JsonResponse({
        'projectId': str(project.id),
        'versionId': str(version.id),
        'versionLabel': version.version_label,
        'milestoneCount': len(report),
        'milestones': report,
    })


def _resolve_baseline_and_current(project, request):
    """Shared version resolution for the Baseline & Progress endpoints.
    currentVersion defaults to the CURRENT-role version (see
    _resolve_latest_version — classification-aware, immune to a historical
    import silently hijacking "current"). baselineVersion defaults
    to whichever version already carries schedule_classification in
    ('APPROVED_BASELINE', 'REVISED_BASELINE') — i.e. the SAME role the rest
    of ScheduleIQ (_assign_version_roles) already uses — never just "the
    oldest upload". Returns (current, baseline_or_None)."""
    from .models import ScheduleUpload

    current = _resolve_latest_version(project, request.GET.get('currentVersion') or request.GET.get('version'))
    baseline = None
    baseline_id = request.GET.get('baselineVersion')
    if baseline_id:
        baseline = ScheduleUpload.objects.filter(pk=baseline_id, project=project).first()
    else:
        ordered = list(version_chronology.chronological_versions(project))
        roles = _assign_version_roles(ordered)
        baseline_row = next((v for v in ordered if roles.get(str(v.id)) == 'BASELINE'), None)
        baseline = baseline_row
    return current, baseline


def _parse_filters(request):
    filters = {}
    for f in ('wbs', 'area', 'discipline', 'contractor', 'system'):
        v = request.GET.get(f)
        if v:
            filters[f] = v
    for f in ('criticalOnly', 'nearCritical', 'inProgress', 'notStarted', 'completed', 'delayedOnly', 'milestonesOnly'):
        if request.GET.get(f) in ('true', '1', 'True'):
            filters[f] = True
    return filters


def _no_previous_reason(current, default):
    """Explanation for a missing Previous: an unresolved same-Data-Date
    conflict is reported as such, not as "no previous exists"."""
    info = getattr(current, '_previous_unresolved', None)
    return info['reason'] if info else default


def _resolve_previous_and_current(project, request):
    """Version-pair resolution for the Update Intelligence endpoint (Phase
    A — see update_intelligence.py). CURRENT defaults to the CURRENT-role
    version (see _resolve_latest_version — classification-aware); PREVIOUS
    defaults to the immediately preceding version by
    upload order (the SAME upload_timestamp-based ordering the rest of the
    app already uses for CURRENT/PREVIOUS/BASELINE roles — never Data Date).
    PREVIOUS may legitimately resolve to the baseline when no other update
    exists yet between it and CURRENT, but is never *assumed* to be the
    baseline when a genuine intervening update is present — it's just
    whatever comes immediately before CURRENT in upload order. BASELINE is
    resolved independently (same rule as _resolve_baseline_and_current) and
    stays available for baseline-vs-update context regardless of what
    PREVIOUS resolves to. Never compares across projects — everything here
    is scoped to `project.schedule_versions`. Explicit overrides:
    currentVersion, previousVersion, baselineVersion query params."""
    from .models import ScheduleUpload

    ordered = list(version_chronology.chronological_versions(project))

    current = _resolve_latest_version(project, request.GET.get('currentVersion') or request.GET.get('version'))

    previous = None
    previous_id = request.GET.get('previousVersion')
    if previous_id:
        previous = ScheduleUpload.objects.filter(pk=previous_id, project=project).first()
    elif current:
        # Chronology by effective Data Date; an unresolved same-Data-Date
        # conflict just before CURRENT yields previous=None + an explanation
        # (never a silent fall-back to the next-older update).
        previous, unresolved = version_chronology.previous_resolution(ordered, current)
        current._previous_unresolved = unresolved

    baseline = None
    baseline_id = request.GET.get('baselineVersion')
    if baseline_id:
        baseline = ScheduleUpload.objects.filter(pk=baseline_id, project=project).first()
    else:
        roles = _assign_version_roles(ordered)
        baseline = next((v for v in ordered if roles.get(str(v.id)) == 'BASELINE'), None)

    return current, previous, baseline


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/baseline-progress/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET'])
def project_baseline_progress(request, pk):
    """
    Query params:
      currentVersion, baselineVersion   optional  ScheduleUpload ids (see _resolve_baseline_and_current)
      scurveMetric                       optional  activities|duration|hours|cost (auto-picks hours/duration if omitted)
      histogramMetric                    optional  activities|hours|cost (default activities)
      period                             optional  weekly|monthly (default weekly)
      wbs/area/discipline/contractor/system, criticalOnly/nearCritical/inProgress/
      notStarted/completed/delayedOnly/milestonesOnly   optional filters (see baseline_progress.apply_filters)

    Full, unwindowed baseline-vs-current picture: matched activity rows,
    status counts, variance rankings, S-Curve, and histogram — reusing
    schedule comparison's id-matching convention, calendar_engine for
    working-day variance, and cost_engine/progress_curve for hours/cost
    weighting. See baseline_progress.py; no formula here is duplicated.
    """
    from .models import Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    current, baseline = _resolve_baseline_and_current(project, request)
    if not current:
        return error_response('No schedule version available for this project', status=404)

    data_date = _effective_data_date(current)
    calendars = _calendars_for(baseline, current) if baseline else _calendars_for(current)
    filters = _parse_filters(request)

    try:
        result = baseline_progress.build_baseline_progress(
            baseline.activities_json if baseline else [], current.activities_json, data_date, calendars,
            filters, request.GET.get('scurveMetric'), request.GET.get('histogramMetric', 'activities'),
            request.GET.get('period', 'weekly'),
        )
    except Exception as exc:
        import traceback
        return error_response(f'Baseline & progress error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    return JsonResponse({
        'projectId': str(project.id),
        'currentVersionId': str(current.id),
        'currentVersionLabel': current.version_label,
        'baselineVersionId': str(baseline.id) if baseline else None,
        'baselineVersionLabel': baseline.version_label if baseline else None,
        'baselineMessage': None if baseline else 'No baseline schedule has been selected for this project.',
        **result,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/lookahead/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET'])
def project_lookahead(request, pk):
    """
    Query params (all optional): currentVersion, baselineVersion,
    lookaheadWeeks (2/4/6/8/12, default 4), fromDate/toDate (YYYY-MM-DD —
    overrides lookaheadWeeks when both given), histogramMetric, period,
    plus the same filter params as /baseline-progress/.

    The Look-Ahead-windowed view: filtered/windowed rows, summary KPI
    cards, and a matching histogram, all scoped to [fromDate, toDate]
    starting at the project's effective Data Date.
    """
    from .models import Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    current, baseline = _resolve_baseline_and_current(project, request)
    if not current:
        return error_response('No schedule version available for this project', status=404)

    data_date = _effective_data_date(current)
    calendars = _calendars_for(baseline, current) if baseline else _calendars_for(current)
    filters = _parse_filters(request)

    weeks = None
    weeks_raw = request.GET.get('lookaheadWeeks')
    if weeks_raw:
        try:
            weeks = int(weeks_raw)
        except ValueError:
            return error_response('lookaheadWeeks must be an integer')

    from_date = parse_date(request.GET.get('fromDate')) if request.GET.get('fromDate') else None
    to_date = parse_date(request.GET.get('toDate')) if request.GET.get('toDate') else None

    try:
        result = baseline_progress.build_lookahead(
            baseline.activities_json if baseline else [], current.activities_json, data_date, calendars,
            weeks, from_date, to_date, filters,
            request.GET.get('histogramMetric', 'activities'), request.GET.get('period', 'weekly'),
        )
    except Exception as exc:
        import traceback
        return error_response(f'Look-ahead error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    return JsonResponse({
        'projectId': str(project.id),
        'currentVersionId': str(current.id),
        'currentVersionLabel': current.version_label,
        'baselineVersionId': str(baseline.id) if baseline else None,
        'baselineVersionLabel': baseline.version_label if baseline else None,
        'baselineMessage': None if baseline else 'No baseline schedule has been selected for this project.',
        **result,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/baseline-progress/export/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET'])
def project_baseline_progress_export(request, pk):
    """
    Query params: same as /baseline-progress/ and /lookahead/ — currentVersion,
    baselineVersion, wbs/area/discipline/contractor/system + the 7 boolean
    filters, plus scope=all|lookahead (default all) and, when scope=lookahead,
    lookaheadWeeks/fromDate/toDate.

    Streams an .xlsx of exactly the filtered activity rows the Baseline vs
    Forecast Activity Chart is currently showing — see
    baseline_progress_export.py's module docstring for why this is a live
    export rather than a persisted-snapshot one.
    """
    from .models import Project
    from .baseline_progress_export import generate_activity_chart_excel

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    current, baseline = _resolve_baseline_and_current(project, request)
    if not current:
        return error_response('No schedule version available for this project', status=404)

    data_date = _effective_data_date(current)
    calendars = _calendars_for(baseline, current) if baseline else _calendars_for(current)
    filters = _parse_filters(request)
    scope = request.GET.get('scope', 'all')

    try:
        if scope == 'lookahead':
            weeks = None
            weeks_raw = request.GET.get('lookaheadWeeks')
            if weeks_raw:
                try:
                    weeks = int(weeks_raw)
                except ValueError:
                    return error_response('lookaheadWeeks must be an integer')
            from_date = parse_date(request.GET.get('fromDate')) if request.GET.get('fromDate') else None
            to_date = parse_date(request.GET.get('toDate')) if request.GET.get('toDate') else None
            result = baseline_progress.build_lookahead(
                baseline.activities_json if baseline else [], current.activities_json, data_date, calendars,
                weeks, from_date, to_date, filters,
            )
            scope_label = f'Look-Ahead {result["window"].get("weeks") or ""} Weeks'.strip() if result['window'].get('available') else 'Look-Ahead (unavailable)'
        else:
            result = baseline_progress.build_baseline_progress(
                baseline.activities_json if baseline else [], current.activities_json, data_date, calendars, filters,
            )
            scope_label = 'All Activities'
        rows = result['rows']
    except Exception as exc:
        import traceback
        return error_response(f'Export error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    try:
        xlsx_bytes = generate_activity_chart_excel(
            rows, data_date, project.name, current.version_label,
            baseline.version_label if baseline else None, scope_label,
        )
    except Exception as exc:
        import traceback
        return error_response(f'Excel export error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    response = HttpResponse(xlsx_bytes, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    filename = f'{project.name}_baseline_vs_forecast_{data_date.isoformat() if data_date else "export"}.xlsx'.replace(' ', '_')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/update-intelligence/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET'])
def project_update_intelligence(request, pk):
    """
    Query params (all optional): currentVersion, previousVersion,
    baselineVersion (see _resolve_previous_and_current), group_by
    (wbs|area|discipline|contractor|system, default discipline),
    lookaheadWeeks (default 4).

    "What changed since the last Data Date?" — PREVIOUS -> CURRENT, each
    evaluated using its own effective Data Date (see update_intelligence.py;
    never today, never upload timestamp).
    """
    from .models import Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    current, previous, baseline = _resolve_previous_and_current(project, request)
    if not current:
        return error_response('No schedule version available for this project', status=404)

    base_response = {
        'projectId': str(project.id),
        'currentVersionId': str(current.id), 'currentVersionLabel': current.version_label,
        'baselineVersionId': str(baseline.id) if baseline else None,
        'baselineVersionLabel': baseline.version_label if baseline else None,
    }

    if not previous:
        return JsonResponse({
            **base_response, 'previousVersionId': None, 'previousVersionLabel': None,
            'available': False,
            'reason': _no_previous_reason(current, 'No previous schedule version exists yet for this project — Update Intelligence requires at least two versions.'),
            'previousUnresolved': getattr(current, '_previous_unresolved', None),
        })

    current_dd = _effective_data_date(current)
    previous_dd = _effective_data_date(previous)
    calendars = _calendars_for(*(v for v in (baseline, current, previous) if v))
    group_by = request.GET.get('group_by', 'discipline')
    try:
        lookahead_weeks = int(request.GET.get('lookaheadWeeks', 4))
    except (TypeError, ValueError):
        return error_response('lookaheadWeeks must be an integer')

    try:
        result = update_intelligence.build_update_intelligence(
            previous.activities_json, current.activities_json, previous_dd, current_dd,
            baseline.activities_json if baseline else None, calendars, group_by, lookahead_weeks,
        )
    except Exception as exc:
        import traceback
        return error_response(f'Update intelligence error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    return JsonResponse({
        **base_response,
        'previousVersionId': str(previous.id), 'previousVersionLabel': previous.version_label,
        'available': True,
        **result,
    })


_COST_GROUP_FIELD_MAP = {
    'wbs': 'wbs', 'area': 'area', 'discipline': 'discipline',
    'contractor': 'contractor', 'system': 'system',
}


def _validate_ev_pv_methods(request):
    """Returns (ev_method, pv_method, error_response_or_None)."""
    ev_method = request.GET.get('evMethod', 'DURATION_PCT_COMPLETE')
    pv_method = request.GET.get('pvMethod', 'LINEAR_BASELINE_SPREAD')
    if ev_method not in EV_METHODS:
        return None, None, error_response(f'Unknown evMethod. Valid: {list(EV_METHODS)}')
    if pv_method not in PV_METHODS:
        return None, None, error_response(f'Unknown pvMethod. Valid: {list(PV_METHODS)}')
    return ev_method, pv_method, None


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/cost-summary/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET'])
def project_cost_summary(request, pk):
    """
    Query params:
      version    optional  ScheduleUpload id (defaults to newest version)
      evMethod   optional  DURATION_PCT_COMPLETE | PHYSICAL_PCT_COMPLETE | UNITS_PROGRESS
      pvMethod   optional  LINEAR_BASELINE_SPREAD

    Project-controls cost summary: Budget (original/approved changes/
    current=BAC), Actuals, Commitments, and Forecast (ETC/EAC scenarios/
    VAC). Budget and Actuals come from the imported XER (TASKRSRC);
    Approved Budget Changes, Commitments, and an Approved EAC come only
    from ManualCostEntry rows — P6 XER has no source for any of those
    three. Every figure is tagged with its source (imported/manual/
    calculated) so it's never ambiguous where a number came from.
    """
    from .models import Project, ManualCostEntry

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    version = _resolve_latest_version(project, request.GET.get('version'))
    if not version:
        return error_response('No schedule version available for this project', status=404)

    ev_method, pv_method, err = _validate_ev_pv_methods(request)
    if err:
        return err

    data_date = _effective_data_date(version)
    try:
        summary = compute_cost_summary(version.activities_json, data_date, ev_method, pv_method)
    except Exception as exc:
        import traceback
        return error_response(f'Cost summary error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    breakdown = _compute_budget_breakdown(project, summary)

    return JsonResponse({
        'projectId': str(project.id),
        'versionId': str(version.id),
        'versionLabel': version.version_label,
        'dataDate': data_date.isoformat() if hasattr(data_date, 'isoformat') else data_date,
        'evMethod': ev_method,
        'pvMethod': pv_method,
        **breakdown,
    })


def _compute_budget_breakdown(project, summary):
    """Budget (original/approved changes/current=BAC), Actuals, Commitments
    and Forecast/Approved EAC — shared by /cost-summary/ and the Field
    Dashboard's Current Spend/Budget/Earned Value KPIs, so both read the
    exact same ManualCostEntry layering over the same compute_cost_summary
    result rather than two copies of this logic. Budget and Actuals come
    from the imported XER (TASKRSRC); Approved Budget Changes, Commitments
    and Approved EAC come only from ManualCostEntry rows — P6 XER has no
    source for any of those three."""
    from .models import ManualCostEntry

    manual_entries = list(ManualCostEntry.objects.filter(project=project))
    approved_changes = [e for e in manual_entries if e.entry_type == 'APPROVED_BUDGET_CHANGE']
    commitments = [e for e in manual_entries if e.entry_type == 'COMMITMENT']
    budget_overrides = [e for e in manual_entries if e.entry_type == 'ORIGINAL_BUDGET_OVERRIDE']
    approved_eac = next((e for e in manual_entries if e.entry_type == 'APPROVED_EAC'), None)

    original_budget = summary['cost']['bac']
    original_source = 'imported' if original_budget is not None else 'unavailable'
    if original_budget is None and budget_overrides:
        original_budget = sum(e.cost or 0.0 for e in budget_overrides)
        original_source = 'manual'

    approved_changes_total = sum(e.cost or 0.0 for e in approved_changes) if approved_changes else None
    current_budget = (
        (original_budget + (approved_changes_total or 0.0))
        if original_budget is not None else None
    )
    commitments_total = sum(e.cost or 0.0 for e in commitments) if commitments else None

    return {
        'budget': {
            'originalBudget': {'value': original_budget, 'source': original_source},
            'approvedBudgetChanges': {
                'value': approved_changes_total,
                'source': 'manual' if approved_changes else 'unavailable',
            },
            'currentBudget': {
                'value': (round(current_budget, 2) if current_budget is not None else None),
                'source': 'calculated' if current_budget is not None else 'unavailable',
                'formula': 'Original Budget + Approved Budget Changes',
            },
        },
        'actuals': {
            'actualCost': {'value': summary['cost']['ac'], 'source': 'imported' if summary['cost']['ac'] is not None else 'unavailable'},
            'actualHours': {'value': summary['hours']['ac'], 'source': 'imported' if summary['hours']['ac'] is not None else 'unavailable'},
        },
        'commitments': {
            'value': commitments_total,
            'source': 'manual' if commitments else 'unavailable',
            'note': 'P6 XER has no purchase-order/subcontract table — commitments are always manually entered.',
        },
        'forecast': {
            'scenarios': summary['forecast']['scenarios'],
            'approvedEac': (
                {'value': approved_eac.cost, 'source': 'manual', 'enteredAt': approved_eac.entered_at.isoformat(),
                 'description': approved_eac.description}
                if approved_eac else {'value': None, 'source': 'unavailable'}
            ),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/earned-value/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET'])
def project_earned_value(request, pk):
    """
    Query params:
      version    optional  ScheduleUpload id (defaults to newest version)
      evMethod   optional  DURATION_PCT_COMPLETE | PHYSICAL_PCT_COMPLETE | UNITS_PROGRESS
      pvMethod   optional  LINEAR_BASELINE_SPREAD
      group_by   optional  wbs | area | discipline | contractor | system

    Full EVM detail: BAC/PV/EV/AC/CV/SV/CPI/SPI/TCPI plus forecast
    scenarios, with the method and formula used for every derived figure
    (see cost_engine.py for the formulas themselves). Ungrouped by
    default; group_by returns the same breakdown per group alongside the
    project-wide total, matching the risk/productivity endpoints' pattern.
    """
    from .models import Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    version = _resolve_latest_version(project, request.GET.get('version'))
    if not version:
        return error_response('No schedule version available for this project', status=404)

    ev_method, pv_method, err = _validate_ev_pv_methods(request)
    if err:
        return err

    data_date = _effective_data_date(version)
    activities = version.activities_json

    try:
        overall = compute_cost_summary(activities, data_date, ev_method, pv_method)
    except Exception as exc:
        import traceback
        return error_response(f'Earned value error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    result = {
        'projectId': str(project.id),
        'versionId': str(version.id),
        'versionLabel': version.version_label,
        'groupBy': 'project',
        'overall': overall,
    }

    group_by = request.GET.get('group_by')
    if group_by:
        field = _COST_GROUP_FIELD_MAP.get(group_by)
        if not field:
            return error_response(f'Unknown group_by value: {group_by}. Valid: {list(_COST_GROUP_FIELD_MAP)}')
        groups = {}
        for a in activities:
            key = (a.get(field) or '').strip() or 'Unassigned'
            groups.setdefault(key, []).append(a)
        result['groupBy'] = group_by
        try:
            result['groups'] = [
                {'group': key, **compute_cost_summary(acts, data_date, ev_method, pv_method)}
                for key, acts in sorted(groups.items())
            ]
        except Exception as exc:
            import traceback
            return error_response(f'Earned value grouping error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    return JsonResponse(result)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/productivity/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET'])
def project_productivity(request, pk):
    """
    Query params:
      version    optional  ScheduleUpload id (defaults to newest version)
      evMethod   optional  DURATION_PCT_COMPLETE | PHYSICAL_PCT_COMPLETE | UNITS_PROGRESS
                            (drives which activities count as "earned" — see cost_engine.py)
      group_by   optional  wbs | area | discipline | contractor | system

    Budgeted / Earned / Actual / Remaining labor hours and the resulting
    Productivity Factor (Earned Hours / Actual Hours), project-wide and
    optionally per WBS/Area/Discipline/Contractor/System.
    """
    from .models import Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    version = _resolve_latest_version(project, request.GET.get('version'))
    if not version:
        return error_response('No schedule version available for this project', status=404)

    ev_method = request.GET.get('evMethod', 'DURATION_PCT_COMPLETE')
    if ev_method not in EV_METHODS:
        return error_response(f'Unknown evMethod. Valid: {list(EV_METHODS)}')

    group_by = request.GET.get('group_by')
    try:
        result = compute_productivity(version.activities_json, ev_method, group_by)
    except Exception as exc:
        import traceback
        return error_response(f'Productivity error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    return JsonResponse({
        'projectId': str(project.id),
        'versionId': str(version.id),
        'versionLabel': version.version_label,
        **result,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/cost-history/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET'])
def project_cost_history(request, pk):
    """
    Query params:
      evMethod   optional  DURATION_PCT_COMPLETE | PHYSICAL_PCT_COMPLETE | UNITS_PROGRESS

    A simple BAC/AC/EV/CPI/SPI/EAC trend across this project's real,
    persisted schedule versions, ordered by data date. Only versions with
    an actual data_date are included — a version with no data date is
    skipped rather than guessing one, and no synthetic history is
    generated from a single current snapshot (a project with one version
    returns a one-point series, not an invented trend).
    """
    from .models import Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    ev_method = request.GET.get('evMethod', 'DURATION_PCT_COMPLETE')
    if ev_method not in EV_METHODS:
        return error_response(f'Unknown evMethod. Valid: {list(EV_METHODS)}')

    versions = list(
        project.schedule_versions.exclude(data_date=None).order_by('data_date')
    )

    points = []
    for v in versions:
        try:
            summary = compute_cost_summary(v.activities_json, v.data_date, ev_method)
        except Exception:
            continue
        primary_forecast = (summary['forecast']['scenarios'] or [None])[0]
        points.append({
            'versionId': str(v.id),
            'versionLabel': v.version_label,
            'dataDate': v.data_date.isoformat(),
            'bac': summary['cost']['bac'],
            'ac': summary['cost']['ac'],
            'ev': summary['cost']['ev'],
            'cpi': summary['cost']['cpi'],
            'spi': summary['cost']['spi'],
            'eac': primary_forecast['eac'] if primary_forecast else None,
            'eacMethod': primary_forecast['methodology'] if primary_forecast else None,
        })

    return JsonResponse({
        'projectId': str(project.id),
        'evMethod': ev_method,
        'pointCount': len(points),
        'points': points,
        'note': (
            'Only real, persisted schedule versions with a data date are included. '
            'Versions without cost-loaded activities appear with bac/ac/ev/cpi/spi as null, '
            'not zero.'
        ),
    })


# _snapshot_series/_latest_approved_eac used to live here; moved to
# report_service.py (snapshot_series/latest_approved_eac) so the
# executive-summary endpoint and report generation share one
# implementation. Thin aliases kept so this view's existing call sites
# don't need renaming.
_snapshot_series = report_service.snapshot_series
_latest_approved_eac = report_service.latest_approved_eac


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/controls-trends/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET'])
def project_controls_trends(request, pk):
    """
    Query params:
      evMethod, pvMethod   optional, as elsewhere
      lookback             optional  int, max historical points considered for
                            consecutive-trend detection (default 6)

    Current-vs-previous comparison across all tracked metrics, EAC drift per
    forecast methodology, and CPI/SPI/productivity consecutive-trend +
    threshold-crossing signals — built entirely from real, persisted
    schedule versions (see cost_engine.py/trend_engine.py for the
    "never fabricate history" rules this endpoint follows).
    """
    from .models import Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    ev_method, pv_method, err = _validate_ev_pv_methods(request)
    if err:
        return err
    try:
        lookback = max(2, int(request.GET.get('lookback', 6)))
    except (TypeError, ValueError):
        lookback = 6

    try:
        series, versions = _snapshot_series(project, ev_method, pv_method)
    except Exception as exc:
        import traceback
        return error_response(f'Trend computation error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    comparison = trend_engine.build_comparison(series)
    eac_drift = trend_engine.build_eac_drift(series, _latest_approved_eac(project))
    cpi_trend = trend_engine.detect_consecutive_trend(series, 'cpi', lookback)
    spi_trend = trend_engine.detect_consecutive_trend(series, 'spi', lookback)
    productivity_trend = trend_engine.detect_consecutive_trend(series, 'productivityFactor', lookback)

    return JsonResponse({
        'projectId': str(project.id),
        'evMethod': ev_method,
        'pvMethod': pv_method,
        'versionCount': len(versions),
        'series': series,
        'comparison': comparison,
        'eacDrift': eac_drift,
        'cpiTrend': cpi_trend,
        'spiTrend': spi_trend,
        'productivityTrend': productivity_trend,
        'cpiThresholdCrossing': trend_engine.detect_threshold_crossing(series, 'cpi', executive_summary.CPI_HEALTHY_THRESHOLD),
        'spiThresholdCrossing': trend_engine.detect_threshold_crossing(series, 'spi', executive_summary.SPI_HEALTHY_THRESHOLD),
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/controls-drivers/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET'])
def project_controls_drivers(request, pk):
    """
    Query params:
      dimension          cost | schedule | productivity | forecast  (default cost)
      group_by           wbs | area | discipline | contractor | system  (default discipline)
      version             optional  ScheduleUpload id (defaults to newest)
      compareVersion       required for dimension=forecast — the earlier version to diff against
      evMethod, pvMethod, forecastMethod   optional, as elsewhere

    Ranked driver lists — see driver_engine.py. Rankings never claim
    causation; language is limited to "largest contributor" style phrasing.
    """
    from .models import Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    version = _resolve_latest_version(project, request.GET.get('version'))
    if not version:
        return error_response('No schedule version available for this project', status=404)

    ev_method, pv_method, err = _validate_ev_pv_methods(request)
    if err:
        return err
    group_by = request.GET.get('group_by', 'discipline')
    dimension = request.GET.get('dimension', 'cost')
    data_date = _effective_data_date(version)

    try:
        if dimension == 'cost':
            result = driver_engine.rank_cost_drivers(version.activities_json, group_by, data_date, ev_method, pv_method)
        elif dimension == 'schedule':
            result = driver_engine.rank_schedule_drivers(version.activities_json, group_by, data_date, ev_method, pv_method)
        elif dimension == 'productivity':
            result = driver_engine.rank_productivity_drivers(version.activities_json, group_by, ev_method)
        elif dimension == 'forecast':
            compare_id = request.GET.get('compareVersion')
            if not compare_id:
                return error_response('dimension=forecast requires a compareVersion query param')
            from .models import ScheduleUpload
            compare_version = ScheduleUpload.objects.filter(pk=compare_id, project=project).first()
            if not compare_version:
                return error_response('compareVersion not found for this project', status=404)
            forecast_method = request.GET.get('forecastMethod', 'CPI_BASED')
            result = driver_engine.rank_forecast_growth_drivers(
                version.activities_json, compare_version.activities_json, group_by,
                data_date, _effective_data_date(compare_version),
                ev_method, forecast_method,
            )
        else:
            return error_response(f'Unknown dimension: {dimension}. Valid: cost, schedule, productivity, forecast')
    except Exception as exc:
        import traceback
        return error_response(f'Driver ranking error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    return JsonResponse({
        'projectId': str(project.id),
        'versionId': str(version.id),
        'dimension': dimension,
        **result,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/executive-summary/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET'])
def project_executive_summary(request, pk):
    """
    Query params: evMethod, pvMethod, group_by (drivers), lookback

    The single authoritative export-ready payload: project identity,
    reporting date, current controls metrics, previous-update comparison,
    trends, EAC movement, top drivers, productivity, combined control
    signals, and a deterministic narrative — everything one PDF/email/
    dashboard presentation layer would need, traceable back to the engines
    that computed each figure.
    """
    from .models import Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    ev_method, pv_method, err = _validate_ev_pv_methods(request)
    if err:
        return err
    group_by = request.GET.get('group_by', 'discipline')
    try:
        lookback = max(2, int(request.GET.get('lookback', 6)))
    except (TypeError, ValueError):
        lookback = 6

    try:
        payload = report_service.build_executive_payload(project, ev_method, pv_method, group_by, lookback)
    except Exception as exc:
        import traceback
        return error_response(f'Executive summary error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    payload.pop('_versions', None)   # internal only — Django model instances aren't JSON-serializable
    return JsonResponse(payload)


# ─────────────────────────────────────────────────────────────────────────────
# /api/projects/<id>/reports/  and  /api/projects/<id>/reports/<report_id>/
# ─────────────────────────────────────────────────────────────────────────────

def _serialize_report_summary(r):
    return {
        'id': str(r.id), 'projectId': str(r.project_id),
        'scheduleUploadId': str(r.schedule_upload_id) if r.schedule_upload_id else None,
        'dataDate': r.data_date.isoformat() if r.data_date else None,
        'reportType': r.report_type, 'generatedAt': r.generated_at.isoformat(),
        'engineVersion': r.engine_version, 'evMethod': r.ev_method, 'pvMethod': r.pv_method,
        'status': r.status, 'title': r.title,
        'reportingPeriodStart': r.reporting_period_start.isoformat() if r.reporting_period_start else None,
        'reportingPeriodEnd': r.reporting_period_end.isoformat() if r.reporting_period_end else None,
        'generatedBy': r.generated_by,
    }


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def project_reports(request, pk):
    """
    GET   /api/projects/<id>/reports/   list saved report snapshots (summaries only)
    POST  /api/projects/<id>/reports/   generate + persist a new snapshot
          Body: reportType (WEEKLY_PROJECT_CONTROLS | MONTHLY_EXECUTIVE, required),
                version (schedule version id, optional — defaults to newest),
                evMethod, pvMethod, group_by, lookback, title (optional)
    """
    from .models import Project, ProjectControlsReport, ScheduleUpload

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    if request.method == 'GET':
        reports = ProjectControlsReport.objects.filter(project=project)
        report_type = request.GET.get('reportType')
        if report_type:
            reports = reports.filter(report_type=report_type)
        return JsonResponse({'projectId': str(project.id), 'reports': [_serialize_report_summary(r) for r in reports]})

    # POST
    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    report_type = body.get('reportType')
    if report_type not in report_service.REPORT_TYPES:
        return error_response(f'reportType is required. Valid: {list(report_service.REPORT_TYPES)}')

    ev_method = body.get('evMethod', 'DURATION_PCT_COMPLETE')
    pv_method = body.get('pvMethod', 'LINEAR_BASELINE_SPREAD')
    if ev_method not in EV_METHODS:
        return error_response(f'Unknown evMethod. Valid: {list(EV_METHODS)}')
    if pv_method not in PV_METHODS:
        return error_response(f'Unknown pvMethod. Valid: {list(PV_METHODS)}')
    group_by = body.get('group_by', 'discipline')
    try:
        lookback = max(2, int(body.get('lookback', 6)))
    except (TypeError, ValueError):
        lookback = 6

    version_id = body.get('version')
    version = None
    if version_id:
        version = ScheduleUpload.objects.filter(pk=version_id, project=project).first()
        if not version:
            return error_response('version does not belong to this project', status=404)

    baseline_version_id = body.get('baselineVersion')
    if baseline_version_id and not ScheduleUpload.objects.filter(pk=baseline_version_id, project=project).exists():
        return error_response('baselineVersion does not belong to this project', status=404)

    update_previous_version_id = body.get('updatePreviousVersion')
    if update_previous_version_id and not ScheduleUpload.objects.filter(pk=update_previous_version_id, project=project).exists():
        return error_response('updatePreviousVersion does not belong to this project', status=404)

    lookahead_weeks = body.get('lookaheadWeeks')
    if lookahead_weeks is not None:
        try:
            lookahead_weeks = int(lookahead_weeks)
        except (TypeError, ValueError):
            return error_response('lookaheadWeeks must be an integer')

    lookahead_from = parse_date(body.get('lookaheadFrom')) if body.get('lookaheadFrom') else None
    lookahead_to = parse_date(body.get('lookaheadTo')) if body.get('lookaheadTo') else None
    lookahead_filters = body.get('lookaheadFilters') or {}
    try:
        lookahead_top_n = int(body.get('lookaheadTopN', 10))
    except (TypeError, ValueError):
        lookahead_top_n = 10

    try:
        payload = report_service.build_report_payload(
            project, report_type, version_id=str(version.id) if version else None,
            ev_method=ev_method, pv_method=pv_method, group_by=group_by, lookback=lookback,
            enable_ai=bool(body.get('enableAi', False)),
            lookahead_weeks=lookahead_weeks, lookahead_from=lookahead_from, lookahead_to=lookahead_to,
            lookahead_filters=lookahead_filters, baseline_version_id=baseline_version_id,
            lookahead_top_n=lookahead_top_n, update_previous_version_id=update_previous_version_id,
        )
    except ValueError as exc:
        return error_response(str(exc))
    except Exception as exc:
        import traceback
        return error_response(f'Report generation error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    payload.pop('_allDrivers', None)
    trace = payload.get('traceability') or {}
    if version is None and trace.get('scheduleVersionId'):
        version = ScheduleUpload.objects.filter(pk=trace['scheduleVersionId']).first()

    record = ProjectControlsReport.objects.create(
        project=project, schedule_upload=version,
        data_date=version.data_date if version else None,
        report_type=report_type, payload_json=payload,
        engine_version=payload.get('engineVersion', ''), ev_method=ev_method, pv_method=pv_method,
        title=body.get('title', ''),
    )
    return JsonResponse({**_serialize_report_summary(record), 'payload': payload}, status=201)


@csrf_exempt
@require_http_methods(['GET', 'DELETE'])
def project_report_detail(request, pk, report_id):
    from .models import Project, ProjectControlsReport

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    record = ProjectControlsReport.objects.filter(pk=report_id, project=project).first()
    if not record:
        return error_response('Report not found', status=404)

    if request.method == 'DELETE':
        record.delete()
        return JsonResponse({'deleted': True, 'id': str(report_id)})

    return JsonResponse({**_serialize_report_summary(record), 'payload': record.payload_json})


@csrf_exempt
@require_http_methods(['GET'])
def project_report_pdf(request, pk, report_id):
    """Streams a PDF built ONLY from the persisted report snapshot — see
    report_export.py's module docstring."""
    from .models import Project, ProjectControlsReport
    from .report_export import generate_report_pdf

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)
    record = ProjectControlsReport.objects.filter(pk=report_id, project=project).first()
    if not record:
        return error_response('Report not found', status=404)

    try:
        pdf_bytes = generate_report_pdf(record.payload_json)
    except Exception as exc:
        import traceback
        return error_response(f'PDF export error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    filename = f'{project.name}_{record.report_type}_{record.data_date or "report"}.pdf'.replace(' ', '_')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@csrf_exempt
@require_http_methods(['GET'])
def project_report_excel(request, pk, report_id):
    """Streams an .xlsx workbook built ONLY from the persisted report
    snapshot — see report_export.py's module docstring."""
    from .models import Project, ProjectControlsReport
    from .report_export import generate_report_excel

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)
    record = ProjectControlsReport.objects.filter(pk=report_id, project=project).first()
    if not record:
        return error_response('Report not found', status=404)

    try:
        xlsx_bytes = generate_report_excel(record.payload_json)
    except Exception as exc:
        import traceback
        return error_response(f'Excel export error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    response = HttpResponse(xlsx_bytes, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    filename = f'{project.name}_{record.report_type}_{record.data_date or "report"}.xlsx'.replace(' ', '_')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@csrf_exempt
@require_http_methods(['GET'])
def project_reports_compare(request, pk):
    """
    Query params: a=<reportId>, b=<reportId>  (a = earlier, b = later)

    Compares two PERSISTED report snapshots — never recomputes from live
    schedule data, so this reflects exactly what was true when each report
    was generated (see ProjectControlsReport's docstring).
    """
    from .models import Project, ProjectControlsReport

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    a_id, b_id = request.GET.get('a'), request.GET.get('b')
    if not a_id or not b_id:
        return error_response('Both a and b (report ids) are required')

    report_a = ProjectControlsReport.objects.filter(pk=a_id, project=project).first()
    report_b = ProjectControlsReport.objects.filter(pk=b_id, project=project).first()
    if not report_a or not report_b:
        return error_response('One or both reports not found for this project', status=404)

    payload_a = dict(report_a.payload_json, _id=str(report_a.id))
    payload_b = dict(report_b.payload_json, _id=str(report_b.id))
    comparison = report_service.compare_report_snapshots(payload_a, payload_b)
    return JsonResponse({'projectId': str(project.id), **comparison})


# ─────────────────────────────────────────────────────────────────────────────
# /api/projects/<id>/cost-entries/  and  /api/projects/<id>/cost-entries/<entry_id>/
# ─────────────────────────────────────────────────────────────────────────────

def _serialize_cost_entry(e):
    return {
        'id': str(e.id),
        'projectId': str(e.project_id),
        'scheduleUploadId': str(e.schedule_upload_id) if e.schedule_upload_id else None,
        'costAccountId': str(e.cost_account_id) if e.cost_account_id else None,
        'entryType': e.entry_type,
        'cost': e.cost,
        'hours': e.hours,
        'description': e.description,
        'wbsId': e.wbs_id,
        'discipline': e.discipline,
        'area': e.area,
        'system': e.system,
        'costCode': e.cost_code,
        'effectiveDate': e.effective_date.isoformat() if e.effective_date else None,
        'referenceNumber': e.reference_number,
        'notes': e.notes,
        'enteredBy': e.entered_by,
        'enteredAt': e.entered_at.isoformat(),
        'updatedAt': e.updated_at.isoformat(),
    }


def _validate_cost_entry_body(body, partial=False):
    """Returns (cleaned_fields, error_message). `partial` skips required-field
    checks for PATCH, where any subset of fields may be supplied."""
    from .models import MANUAL_COST_ENTRY_TYPE_CHOICES
    valid_types = {c[0] for c in MANUAL_COST_ENTRY_TYPE_CHOICES}
    cleaned = {}

    if 'entryType' in body or not partial:
        entry_type = body.get('entryType')
        if not partial and entry_type is None:
            return None, 'entryType is required'
        if entry_type is not None:
            if entry_type not in valid_types:
                return None, f'Invalid entryType. Valid: {sorted(valid_types)}'
            cleaned['entry_type'] = entry_type

    for json_key, field in (('cost', 'cost'), ('hours', 'hours')):
        if json_key in body:
            v = body.get(json_key)
            if v is not None:
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    return None, f'{json_key} must be a number'
                if v < 0:
                    return None, f'{json_key} cannot be negative'
            cleaned[field] = v

    if not partial and cleaned.get('cost') is None and cleaned.get('hours') is None:
        return None, 'At least one of cost or hours is required'

    for json_key, field, maxlen in (
        ('description', 'description', 500), ('wbsId', 'wbs_id', 50),
        ('discipline', 'discipline', 200), ('area', 'area', 200),
        ('system', 'system', 200), ('costCode', 'cost_code', 100),
        ('referenceNumber', 'reference_number', 100), ('enteredBy', 'entered_by', 200),
    ):
        if json_key in body:
            v = body.get(json_key) or ''
            cleaned[field] = str(v)[:maxlen]

    if 'notes' in body:
        cleaned['notes'] = str(body.get('notes') or '')

    if 'effectiveDate' in body:
        raw = body.get('effectiveDate')
        cleaned['effective_date'] = parse_date(raw) if raw else None

    return cleaned, None


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def project_cost_entries(request, pk):
    """
    GET   /api/projects/<id>/cost-entries/   list all manual cost entries for this project
          Query params: entryType, costAccountId (optional filters)
    POST  /api/projects/<id>/cost-entries/   create one
          Body: entryType (required), cost and/or hours (at least one required),
                description, wbsId, discipline, area, system, costCode,
                effectiveDate, referenceNumber, notes, enteredBy,
                scheduleVersionId (optional), costAccountId (optional)
    """
    from .models import Project, ManualCostEntry, ScheduleUpload, CostAccount

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    if request.method == 'GET':
        qs = ManualCostEntry.objects.filter(project=project)
        entry_type = request.GET.get('entryType')
        if entry_type:
            qs = qs.filter(entry_type=entry_type)
        cost_account_id = request.GET.get('costAccountId')
        if cost_account_id:
            qs = qs.filter(cost_account_id=cost_account_id)
        return JsonResponse({
            'projectId': str(project.id),
            'entries': [_serialize_cost_entry(e) for e in qs],
        })

    # POST
    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    cleaned, err = _validate_cost_entry_body(body, partial=False)
    if err:
        return error_response(err)

    schedule_upload = None
    version_id = body.get('scheduleVersionId')
    if version_id:
        schedule_upload = ScheduleUpload.objects.filter(pk=version_id, project=project).first()
        if not schedule_upload:
            return error_response('scheduleVersionId does not belong to this project', status=404)

    cost_account = None
    cost_account_id = body.get('costAccountId')
    if cost_account_id:
        cost_account = CostAccount.objects.filter(pk=cost_account_id, schedule_upload__project=project).first()
        if not cost_account:
            return error_response('costAccountId does not belong to this project', status=404)

    entry = ManualCostEntry.objects.create(
        project=project, schedule_upload=schedule_upload, cost_account=cost_account, **cleaned,
    )
    return JsonResponse(_serialize_cost_entry(entry), status=201)


@csrf_exempt
@require_http_methods(['PATCH', 'DELETE'])
def project_cost_entry_detail(request, pk, entry_id):
    from .models import Project, ManualCostEntry

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    entry = ManualCostEntry.objects.filter(pk=entry_id, project=project).first()
    if not entry:
        return error_response('Cost entry not found', status=404)

    if request.method == 'DELETE':
        entry.delete()
        return JsonResponse({'deleted': True, 'id': str(entry_id)})

    # PATCH
    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    cleaned, err = _validate_cost_entry_body(body, partial=True)
    if err:
        return error_response(err)

    for field, value in cleaned.items():
        setattr(entry, field, value)
    entry.save()
    return JsonResponse(_serialize_cost_entry(entry))


def _version_meta_dict(version):
    if version is None:
        return None
    return {
        'id': str(version.id),
        'versionLabel': version.version_label or version.original_filename,
        'dataDate': version.data_date.isoformat() if version.data_date else None,
        'sourceDataDate': version.source_data_date.isoformat() if version.source_data_date else None,
        'dataDateOverridden': version.data_date_overridden,
        'projectFinish': version.forecast_finish.isoformat() if version.forecast_finish else None,
        'baselineFinish': version.baseline_finish.isoformat() if version.baseline_finish else None,
        'activityCount': version.activity_count,
        'milestoneCount': version.milestone_count,
        'completedCount': version.completed_count,
        'inProgressCount': version.in_progress_count,
        'notStartedCount': version.not_started_count,
        'criticalCount': version.critical_count,
        'negativeFloatCount': version.negative_float_count,
    }


def _recovery_scenarios_summary(project, limit=5):
    """Bounded, already-serialized recovery scenario summaries for AI
    Context — ai_context.py stays DB-free, so this DB query and the
    trimming happen here. Reads only RecoveryScenario.result (cached
    run_scenario() output) — never recomputes a scenario."""
    from .models import RecoveryScenario

    out = []
    for s in RecoveryScenario.objects.filter(project=project).order_by('-updated_at')[:limit]:
        result = s.result or {}
        out.append({
            'name': s.name, 'status': s.status,
            'currentForecastFinish': result.get('currentForecastFinish'),
            'scenarioForecastFinish': result.get('scenarioForecastFinish'),
            'recoveryDays': result.get('recoveryDays'),
        })
    return out


def _project_meta_dict(project):
    return {
        'id': str(project.id), 'name': project.name,
        'projectNumber': project.project_number, 'client': project.client,
    }


def _recent_documents(project, limit=5):
    from .models import ScheduleDocument
    docs = ScheduleDocument.objects.filter(project=project).order_by('-uploaded_at')[:limit]
    return [{
        'id': str(d.id), 'filename': d.filename,
        'documentType': d.document_type, 'extractedText': d.extracted_text,
    } for d in docs]


# ─────────────────────────────────────────────────────────────────────────────
# GET/POST /api/projects/<id>/ai-review/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET', 'POST'])
def project_ai_review(request, pk):
    """
    Params (GET query string or POST JSON body):
      version           optional  defaults to newest
      compareVersion     optional  enables comparison-derived facts
      baselineVersion     optional  enables baseline variance facts
      focusArea            optional  narrows context to one Area
      reportType            optional  executive | senior_scheduler | weekly | monthly | big_room | risk_review

    Always returns the deterministic `context` even when no AI provider is
    configured, so the frontend can show the underlying facts either way.
    """
    from .models import Project, ScheduleUpload

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    if request.method == 'POST':
        try:
            params = json.loads(request.body.decode('utf-8') or '{}')
        except json.JSONDecodeError:
            return error_response('Request body must be valid JSON')
    else:
        params = request.GET

    version = _resolve_latest_version(project, params.get('version'))
    if not version:
        return error_response('No schedule version available for this project', status=404)

    previous_version = None
    compare_id = params.get('compareVersion')
    if compare_id:
        previous_version = ScheduleUpload.objects.filter(pk=compare_id, project=project).first()

    baseline_version = None
    baseline_id = params.get('baselineVersion')
    if baseline_id:
        baseline_version = ScheduleUpload.objects.filter(pk=baseline_id, project=project).first()

    focus_area = params.get('focusArea')
    focus = {'area': focus_area} if focus_area else None
    report_type = params.get('reportType') or 'executive'

    try:
        context = build_ai_context(
            project_meta=_project_meta_dict(project),
            version_meta=_version_meta_dict(version),
            activities=version.activities_json,
            data_date=_effective_data_date(version),
            previous_version_meta=_version_meta_dict(previous_version),
            previous_activities=previous_version.activities_json if previous_version else None,
            previous_data_date=_effective_data_date(previous_version) if previous_version else None,
            baseline_activities=baseline_version.activities_json if baseline_version else None,
            documents=_recent_documents(project),
            focus=focus,
            recovery_scenarios=_recovery_scenarios_summary(project),
        )
    except Exception as exc:
        import traceback
        return error_response(f'Context build error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    provider = get_provider()
    if not provider.is_configured():
        return JsonResponse({'configured': False, 'message': 'AI analysis is not configured.', 'context': context})

    system_prompt = build_review_system_prompt(report_type)
    user_prompt = json.dumps({'context': context, 'reportType': report_type}, default=str)
    ai_response = provider.complete(system_prompt, user_prompt)

    if not ai_response.ok:
        return JsonResponse({'configured': True, 'error': ai_response.error, 'errorType': getattr(ai_response, 'error_type', None), 'context': context}, status=502)

    try:
        parsed = json.loads(ai_response.text)
    except json.JSONDecodeError:
        return JsonResponse({
            'configured': True, 'error': 'AI provider returned malformed JSON.',
            'rawText': ai_response.text[:2000], 'context': context,
        }, status=502)

    return JsonResponse({'configured': True, 'reportType': report_type, 'review': parsed, 'context': context})


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/projects/<id>/ai-chat/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def project_ai_chat(request, pk):
    """
    Body: {question (required), version, compareVersion, autoCompare}

    The question is classified against real Area/Discipline/Contractor/System
    values already present in the schedule (ai_context.classify_focus) —
    never against invented names — to narrow the deterministic context before
    it reaches the AI provider.
    """
    from .models import Project, ScheduleUpload

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    question = (body.get('question') or '').strip()
    if not question:
        return error_response('question is required')
    question = question[:2000]

    version = _resolve_latest_version(project, body.get('version'))
    if not version:
        return error_response('No schedule version available for this project', status=404)

    previous_version = None
    previous_unresolved = None
    compare_id = body.get('compareVersion')
    if compare_id:
        previous_version = ScheduleUpload.objects.filter(pk=compare_id, project=project).first()
    elif body.get('autoCompare', True):
        previous_version, previous_unresolved = version_chronology.previous_resolution(
            version_chronology.chronological_versions(project), version)

    focus = classify_focus(question, version.activities_json)

    # The DESIGNATED baseline only (same classification-driven rule as
    # _assign_version_roles) - never guessed; None when none is designated.
    ordered_versions = list(version_chronology.chronological_versions(project))
    baseline_roles = _assign_version_roles(ordered_versions)
    baseline_version = next((v for v in ordered_versions if baseline_roles.get(str(v.id)) == 'BASELINE'), None)
    if baseline_version and baseline_version.id == version.id:
        baseline_version = None

    try:
        context = build_ai_context(
            project_meta=_project_meta_dict(project),
            version_meta=_version_meta_dict(version),
            activities=version.activities_json,
            data_date=_effective_data_date(version),
            previous_version_meta=_version_meta_dict(previous_version),
            previous_activities=previous_version.activities_json if previous_version else None,
            previous_data_date=_effective_data_date(previous_version) if previous_version else None,
            baseline_activities=baseline_version.activities_json if baseline_version else None,
            documents=_recent_documents(project, limit=3),
            focus=focus,
            top_n=8,
            recovery_scenarios=_recovery_scenarios_summary(project, limit=3),
            previous_unresolved=previous_unresolved,
        )
    except Exception as exc:
        import traceback
        return error_response(f'Context build error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    provider = get_provider()
    if not provider.is_configured():
        return JsonResponse({
            'configured': False, 'message': 'AI analysis is not configured.',
            'focus': focus, 'context': context,
        })

    system_prompt = build_chat_system_prompt()
    user_prompt = json.dumps({'context': context, 'question': question}, default=str)
    ai_response = provider.complete(system_prompt, user_prompt)

    if not ai_response.ok:
        return JsonResponse({'configured': True, 'error': ai_response.error, 'errorType': getattr(ai_response, 'error_type', None)}, status=502)

    try:
        parsed = json.loads(ai_response.text)
    except json.JSONDecodeError:
        return JsonResponse({
            'configured': True, 'error': 'AI provider returned malformed JSON.',
            'rawText': ai_response.text[:2000],
        }, status=502)

    return JsonResponse({
        'configured': True,
        'answer': parsed.get('answer', ''),
        'references': parsed.get('references', []),
        'confidence': parsed.get('confidence', 'medium'),
        'focus': focus,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET  /api/projects/<id>/recovery-scenarios/
# POST /api/projects/<id>/recovery-scenarios/
# ─────────────────────────────────────────────────────────────────────────────

def _scenario_summary(s):
    result = s.result or {}
    return {
        'id': str(s.id),
        'name': s.name,
        'status': s.status,
        'riskKey': s.risk.risk_key if s.risk_id else None,
        'projectId': str(s.project_id),
        'scheduleUploadId': str(s.schedule_upload_id),
        'sourceVersionLabel': s.schedule_upload.version_label or s.schedule_upload.original_filename,
        'sourceDataDate': s.schedule_upload.data_date.isoformat() if s.schedule_upload.data_date else None,
        'createdAt': s.created_at.isoformat(),
        'updatedAt': s.updated_at.isoformat(),
        'actionCount': len(s.assumptions or []),
        'currentForecastFinish': result.get('currentForecastFinish'),
        'scenarioForecastFinish': result.get('scenarioForecastFinish'),
        'recoveryDays': result.get('recoveryDays'),
        'calendarConfidence': result.get('calendarConfidence'),
        'projectBaselineRecovery': result.get('projectBaselineRecovery'),
    }


def _approved_baseline_activities_for(project):
    """Resolves the project's real Approved Baseline version (same
    _assign_version_roles convention as everywhere else) and returns its
    activities_json, or None when no baseline exists yet — recovery_engine
    then correctly reports projectBaselineRecovery as unavailable rather
    than guessing."""
    ordered = list(version_chronology.chronological_versions(project))
    roles = _assign_version_roles(ordered)
    baseline = next((v for v in ordered if roles.get(str(v.id)) == 'BASELINE'), None)
    return baseline.activities_json if baseline else None


def _run_scenario_for_version(project, version, assumptions):
    """Single place that calls recovery_engine.run_scenario with the full
    calendar + Approved Baseline context — used by both scenario create and
    scenario recalculate, so the two paths never drift apart."""
    data_date = _effective_data_date(version)
    calendars = _calendars_for(version)
    baseline_activities = _approved_baseline_activities_for(project)
    return run_scenario(
        version.activities_json, assumptions, data_date,
        calendars=calendars, approved_baseline_activities=baseline_activities,
    )


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def project_recovery_scenarios(request, pk):
    """
    GET:  list scenario summaries for the project (no full activity data).
    POST: {name, scheduleUploadId, assumptions: [...]} — calculates
          immediately (never modifies the source ScheduleUpload) and persists
          both the assumptions and the resulting estimate.
    """
    from .models import Project, RecoveryScenario, ScheduleUpload

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    if request.method == 'GET':
        scenarios = RecoveryScenario.objects.filter(project=project)
        return JsonResponse({'scenarios': [_scenario_summary(s) for s in scenarios]})

    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    schedule_upload_id = body.get('scheduleUploadId')
    version = ScheduleUpload.objects.filter(pk=schedule_upload_id, project=project).first()
    if not version:
        return error_response('scheduleUploadId was not found on this project', status=404)

    assumptions = body.get('assumptions') or []
    if not isinstance(assumptions, list):
        return error_response('assumptions must be a list')

    risk = None
    risk_key = body.get('riskKey')
    if risk_key:
        from .models import ScheduleRisk
        risk, _ = ScheduleRisk.objects.get_or_create(project=project, risk_key=risk_key)

    try:
        # current_forecast_finish is left for run_scenario to compute via the
        # same CPM pass used for the scenario itself (not version.forecast_finish,
        # which is a simple max(bFinish) stored at import time) — otherwise a
        # zero-action scenario could show a spurious "recovery" that's really
        # just two different calculation methods disagreeing. Calendars and
        # the project's real Approved Baseline are always passed through
        # (see _run_scenario_for_version) — the browser gets the exact same
        # calendar-aware, baseline-aware calculation the tests exercise.
        result = _run_scenario_for_version(project, version, assumptions)
    except Exception as exc:
        import traceback
        return error_response(f'Scenario calculation error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    scenario = RecoveryScenario.objects.create(
        project=project, schedule_upload=version, risk=risk,
        name=body.get('name') or f'Scenario {timezone.now().strftime("%Y-%m-%d %H:%M")}',
        assumptions=assumptions, result=result,
    )

    return JsonResponse({**_scenario_summary(scenario), 'assumptions': scenario.assumptions, 'result': result}, status=201)


# ─────────────────────────────────────────────────────────────────────────────
# GET/PUT/PATCH/DELETE /api/projects/<id>/recovery-scenarios/<scenario_id>/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET', 'PUT', 'PATCH', 'DELETE'])
def project_recovery_scenario_detail(request, pk, scenario_id):
    from .models import Project, RecoveryScenario

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    scenario = RecoveryScenario.objects.filter(pk=scenario_id, project=project).first()
    if not scenario:
        return error_response('Recovery scenario not found', status=404)

    if request.method == 'GET':
        return JsonResponse({
            **_scenario_summary(scenario), 'assumptions': scenario.assumptions, 'result': scenario.result,
            'recoveryTracking': _recovery_tracking(project, scenario),
        })

    if request.method == 'DELETE':
        scenario.delete()
        return JsonResponse({'deleted': True})

    # PUT/PATCH — update assumptions/name/status, recalculate against the
    # scenario's own schedule_upload (never a different, unrelated version —
    # this is what keeps a scenario traceable to the update it was built
    # against even after later imports; see item 23).
    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    if 'name' in body:
        scenario.name = body['name'] or scenario.name

    if 'status' in body:
        from .models import RECOVERY_SCENARIO_STATUS_CHOICES
        valid = {c[0] for c in RECOVERY_SCENARIO_STATUS_CHOICES}
        if body['status'] not in valid:
            return error_response(f'Unknown status. Valid: {sorted(valid)}')
        scenario.status = body['status']

    if 'assumptions' in body:
        assumptions = body['assumptions']
        if not isinstance(assumptions, list):
            return error_response('assumptions must be a list')
        version = scenario.schedule_upload
        try:
            result = _run_scenario_for_version(project, version, assumptions)
        except Exception as exc:
            import traceback
            return error_response(f'Scenario calculation error: {exc} | {traceback.format_exc()[-600:]}', status=500)
        scenario.assumptions = assumptions
        scenario.result = result

    scenario.save()
    return JsonResponse({
        **_scenario_summary(scenario), 'assumptions': scenario.assumptions, 'result': scenario.result,
        'recoveryTracking': _recovery_tracking(project, scenario),
    })


def _recovery_tracking(project, scenario):
    """Item 17/31 — compares an ACCEPTED scenario's predicted milestone
    dates against the project's actual CURRENT version, but only once a
    genuinely NEWER version than the scenario's own source exists (never
    compares a scenario against itself). Deterministic classification only
    — never infers WHY a scenario was or wasn't realized."""
    if scenario.status != 'ACCEPTED':
        return {'available': False, 'reason': 'Recovery tracking applies to ACCEPTED scenarios.'}

    ordered = list(version_chronology.chronological_versions(project))
    roles = _assign_version_roles(ordered)
    current = next((v for v in ordered if roles.get(str(v.id)) == 'CURRENT'), None)
    if not current or current.id == scenario.schedule_upload_id:
        return {'available': False, 'reason': 'No newer schedule update has been imported since this scenario was built.'}

    milestone_impact = (scenario.result or {}).get('milestoneImpact') or []
    if not milestone_impact:
        return {'available': False, 'reason': 'Scenario has no milestone impact data to track.'}

    current_by_id = {(a.get('code') or a.get('id') or ''): a for a in current.activities_json}
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
        if diff <= 0:
            classification = 'ON_TRACK'
        elif diff <= 3:
            classification = 'PARTIALLY_REALIZED'
        else:
            classification = 'NOT_REALIZED'
        comparisons.append({
            'activityId': aid, 'activityName': mi.get('activityName'), 'classification': classification,
            'scenarioTargetFinish': scenario_target, 'nextUpdateForecastFinish': next_finish_d.isoformat(),
            'differenceDays': diff,
        })

    return {
        'available': True,
        'nextVersionId': str(current.id), 'nextVersionLabel': current.version_label or current.original_filename,
        'nextDataDate': _effective_data_date(current).isoformat() if _effective_data_date(current) else None,
        'milestoneComparisons': comparisons,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/risk-register/
# ─────────────────────────────────────────────────────────────────────────────

def _serialize_schedule_risk_workflow(sr):
    return {
        'status': sr.status, 'owner': sr.owner, 'mitigationNotes': sr.mitigation_notes,
        'targetDate': sr.target_date.isoformat() if sr.target_date else None,
        'firstIdentifiedDataDate': sr.first_identified_data_date.isoformat() if sr.first_identified_data_date else None,
        'firstIdentifiedVersionId': str(sr.first_identified_version_id) if sr.first_identified_version_id else None,
    }


@csrf_exempt
@require_http_methods(['GET'])
def project_risk_register(request, pk):
    """
    Query params: currentVersion, previousVersion, baselineVersion (see
    _resolve_previous_and_current — same resolution convention Update
    Intelligence already uses), group_by, lookaheadWeeks.

    Computes the risk register fresh from update_intelligence +
    build_report_lookahead (never independently recalculated — see
    risk_register.py), then joins persisted ScheduleRisk workflow rows
    (status/owner/mitigation/targetDate) by risk_key. A risk with no
    persisted row yet defaults to status OPEN with no owner/mitigation —
    the row is created lazily the first time a scheduler actually edits it
    (see project_risk_detail), never speculatively.
    """
    from .models import Project, ScheduleRisk

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    current, previous, baseline = _resolve_previous_and_current(project, request)
    if not current:
        return error_response('No schedule version available for this project', status=404)
    if not previous:
        return JsonResponse({
            'projectId': str(project.id), 'available': False,
            'reason': _no_previous_reason(current, 'Risk Register requires at least two schedule versions (Previous -> Current) to detect update-to-update signals.'),
            'previousUnresolved': getattr(current, '_previous_unresolved', None),
        })

    current_dd = _effective_data_date(current)
    previous_dd = _effective_data_date(previous)
    calendars = _calendars_for(*(v for v in (baseline, current, previous) if v))
    group_by = request.GET.get('group_by', 'discipline')
    try:
        lookahead_weeks = int(request.GET.get('lookaheadWeeks', 4))
    except (TypeError, ValueError):
        return error_response('lookaheadWeeks must be an integer')

    try:
        ui_result = update_intelligence.build_update_intelligence(
            previous.activities_json, current.activities_json, previous_dd, current_dd,
            baseline.activities_json if baseline else None, calendars, group_by, lookahead_weeks,
        )
        lookahead_result = baseline_progress.build_report_lookahead(
            baseline.activities_json if baseline else [], current.activities_json,
            data_date=current_dd, calendars=calendars, weeks=lookahead_weeks,
        )
        register = risk_register.build_risk_register(
            ui_result, lookahead_result, current.activities_json, current_dd, group_by,
        )
    except Exception as exc:
        import traceback
        return error_response(f'Risk register error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    risk_keys = [r['riskKey'] for r in register['risks']]
    workflow_by_key = {sr.risk_key: sr for sr in ScheduleRisk.objects.filter(project=project, risk_key__in=risk_keys)}
    for r in register['risks']:
        sr = workflow_by_key.get(r['riskKey'])
        r['workflow'] = _serialize_schedule_risk_workflow(sr) if sr else {
            'status': 'OPEN', 'owner': '', 'mitigationNotes': '', 'targetDate': None,
            'firstIdentifiedDataDate': None, 'firstIdentifiedVersionId': None,
        }

    return JsonResponse({
        'projectId': str(project.id), 'available': True,
        'currentVersionId': str(current.id), 'currentVersionLabel': current.version_label or current.original_filename,
        'previousVersionId': str(previous.id), 'previousVersionLabel': previous.version_label or previous.original_filename,
        'baselineVersionId': str(baseline.id) if baseline else None,
        'baselineVersionLabel': (baseline.version_label or baseline.original_filename) if baseline else None,
        **register,
    })


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/projects/<id>/risk-register/<risk_key>/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['PATCH'])
def project_risk_detail(request, pk, risk_key):
    """
    Deliberate workflow updates only — status/owner/mitigationNotes/
    targetDate. Never recomputes or overwrites schedule evidence (that's
    always fresh from project_risk_register). Creates the ScheduleRisk row
    lazily on first edit, stamping first_identified_data_date/_version from
    the project's current version at that moment — never rewritten after.
    """
    from .models import Project, ScheduleRisk, SCHEDULE_RISK_STATUS_CHOICES

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    sr, created = ScheduleRisk.objects.get_or_create(project=project, risk_key=risk_key)
    if created:
        ordered = list(version_chronology.chronological_versions(project))
        roles = _assign_version_roles(ordered)
        current = next((v for v in ordered if roles.get(str(v.id)) == 'CURRENT'), None)
        if current:
            sr.first_identified_version = current
            sr.first_identified_data_date = _effective_data_date(current)

    if 'status' in body:
        valid = {c[0] for c in SCHEDULE_RISK_STATUS_CHOICES}
        if body['status'] not in valid:
            return error_response(f'Unknown status. Valid: {sorted(valid)}')
        sr.status = body['status']
    if 'owner' in body:
        sr.owner = body['owner'] or ''
    if 'mitigationNotes' in body:
        sr.mitigation_notes = body['mitigationNotes'] or ''
    if 'targetDate' in body:
        sr.target_date = parse_date(body['targetDate']) if body['targetDate'] else None

    sr.save()
    return JsonResponse({'riskKey': risk_key, **_serialize_schedule_risk_workflow(sr)})


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/risk-register/<risk_key>/driving-chain/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET'])
def project_risk_driving_chain(request, pk, risk_key):
    """Query params: version (defaults to CURRENT), maxHops."""
    from .models import Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    version = _resolve_latest_version(project, request.GET.get('version'))
    if not version:
        return error_response('No schedule version available for this project', status=404)

    try:
        max_hops = int(request.GET.get('maxHops', driving_chain.DEFAULT_MAX_HOPS))
    except (TypeError, ValueError):
        return error_response('maxHops must be an integer')

    activities = version.activities_json
    path = driving_chain.trace_driving_path(activities, risk_key, max_hops=max_hops)
    blocking = driving_chain.find_blocking_predecessors(activities, risk_key)
    exposure = driving_chain.find_downstream_exposure(activities, risk_key)

    return JsonResponse({
        'projectId': str(project.id), 'versionId': str(version.id), 'riskKey': risk_key,
        'drivingPath': path, 'blockingPredecessors': blocking, 'downstreamExposure': exposure,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET/POST /api/projects/<id>/mitigation-actions/
# GET/PATCH/DELETE /api/projects/<id>/mitigation-actions/<action_id>/
# ─────────────────────────────────────────────────────────────────────────────

def _serialize_mitigation_action(a):
    return {
        'id': str(a.id), 'projectId': str(a.project_id),
        'riskKey': a.risk.risk_key if a.risk_id else None,
        'scenarioId': str(a.scenario_id) if a.scenario_id else None,
        'description': a.description, 'owner': a.owner,
        'dueDate': a.due_date.isoformat() if a.due_date else None,
        'status': a.status, 'notes': a.notes,
        'createdAt': a.created_at.isoformat(),
        'completedAt': a.completed_at.isoformat() if a.completed_at else None,
    }


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def project_mitigation_actions(request, pk):
    from .models import MitigationAction, Project, RecoveryScenario, ScheduleRisk

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    if request.method == 'GET':
        actions = MitigationAction.objects.filter(project=project)
        risk_key = request.GET.get('riskKey')
        if risk_key:
            actions = actions.filter(risk__risk_key=risk_key)
        status_filter = request.GET.get('status')
        if status_filter:
            actions = actions.filter(status=status_filter)
        return JsonResponse({'actions': [_serialize_mitigation_action(a) for a in actions]})

    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    description = (body.get('description') or '').strip()
    if not description:
        return error_response('description is required')

    risk = None
    if body.get('riskKey'):
        risk, _ = ScheduleRisk.objects.get_or_create(project=project, risk_key=body['riskKey'])

    scenario = None
    if body.get('scenarioId'):
        scenario = RecoveryScenario.objects.filter(pk=body['scenarioId'], project=project).first()
        if not scenario:
            return error_response('scenarioId does not belong to this project', status=404)

    action = MitigationAction.objects.create(
        project=project, risk=risk, scenario=scenario, description=description,
        owner=body.get('owner') or '', due_date=parse_date(body['dueDate']) if body.get('dueDate') else None,
        notes=body.get('notes') or '',
    )
    return JsonResponse(_serialize_mitigation_action(action), status=201)


@csrf_exempt
@require_http_methods(['GET', 'PATCH', 'DELETE'])
def project_mitigation_action_detail(request, pk, action_id):
    from .models import MitigationAction, Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    action = MitigationAction.objects.filter(pk=action_id, project=project).first()
    if not action:
        return error_response('Mitigation action not found', status=404)

    if request.method == 'GET':
        return JsonResponse(_serialize_mitigation_action(action))

    if request.method == 'DELETE':
        action.delete()
        return JsonResponse({'deleted': True})

    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    from .models import MITIGATION_ACTION_STATUS_CHOICES
    if 'description' in body and body['description']:
        action.description = body['description']
    if 'owner' in body:
        action.owner = body['owner'] or ''
    if 'dueDate' in body:
        action.due_date = parse_date(body['dueDate']) if body['dueDate'] else None
    if 'notes' in body:
        action.notes = body['notes'] or ''
    if 'status' in body:
        valid = {c[0] for c in MITIGATION_ACTION_STATUS_CHOICES}
        if body['status'] not in valid:
            return error_response(f'Unknown status. Valid: {sorted(valid)}')
        was_complete = action.status == 'COMPLETE'
        action.status = body['status']
        if action.status == 'COMPLETE' and not was_complete:
            action.completed_at = timezone.now()
        elif action.status != 'COMPLETE':
            action.completed_at = None

    action.save()
    return JsonResponse(_serialize_mitigation_action(action))


# ─────────────────────────────────────────────────────────────────────────────
# GET/POST /api/projects/<id>/contractual-milestones/
# GET/PATCH/DELETE /api/projects/<id>/contractual-milestones/<milestone_id>/
# GET /api/projects/<id>/contractual-milestones/<milestone_id>/revisions/
# ─────────────────────────────────────────────────────────────────────────────
# Field Dashboard's Contractual Milestone Tracker configuration workflow.
# Scoped by PROJECT (never by one schedule version — see MilestoneDefinition.
# project in models.py) so the register survives every later re-import.
# A contract_required_date is NEVER inferred here from P6 baseline/forecast
# data; it only exists because a scheduler/PM entered it.

def _serialize_contractual_milestone(m):
    return {
        'id': str(m.id), 'projectId': str(m.project_id) if m.project_id else None,
        'activityId': m.activity_id, 'activityName': m.activity_name,
        'description': m.description, 'category': m.milestone_category,
        'isContractual': m.is_contractual,
        'contractRequiredDate': m.contract_required_date.isoformat() if m.contract_required_date else None,
        'mustFinishByDate': m.must_finish_by_date.isoformat() if m.must_finish_by_date else None,
        'allowableVarianceDays': m.allowable_variance_days,
        'responsibleOrganization': m.responsible_organization,
        'isCriticalMilestone': m.is_critical_milestone,
        'hasDocumentedIssue': m.has_documented_issue,
        'approvalStatus': m.approval_status,
        'approvedBy': m.approved_by,
        'approvedAt': m.approved_at.isoformat() if m.approved_at else None,
        'sourceDocumentReference': m.source_document_reference,
        'notes': m.notes,
    }


def _serialize_milestone_revision(r):
    return {
        'id': str(r.id), 'milestoneId': str(r.milestone_id),
        'previousDate': r.previous_date.isoformat() if r.previous_date else None,
        'newDate': r.new_date.isoformat() if r.new_date else None,
        'sourceDocumentReference': r.source_document_reference, 'reason': r.reason,
        'changedBy': r.changed_by, 'changedAt': r.changed_at.isoformat(),
    }


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def project_contractual_milestones(request, pk):
    from .models import MILESTONE_CATEGORY_CHOICES, MilestoneDefinition, Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    if request.method == 'GET':
        entries = MilestoneDefinition.objects.filter(project=project).order_by('contract_required_date', 'activity_id')
        return JsonResponse({
            'projectId': str(project.id),
            'configured': entries.exists(),
            'milestones': [_serialize_contractual_milestone(m) for m in entries],
        })

    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    activity_id = (body.get('activityId') or '').strip()
    if not activity_id:
        return error_response('activityId is required')

    category = body.get('category') or 'INFORMATIONAL'
    valid_categories = {c[0] for c in MILESTONE_CATEGORY_CHOICES}
    if category not in valid_categories:
        return error_response(f'Unknown category. Valid: {sorted(valid_categories)}')

    contract_required_date = None
    if body.get('contractRequiredDate'):
        contract_required_date = parse_date(body['contractRequiredDate'])
        if contract_required_date is None:
            return error_response('contractRequiredDate could not be parsed. Use YYYY-MM-DD.')

    entry = MilestoneDefinition.objects.create(
        project=project, activity_id=activity_id, activity_name=body.get('activityName') or '',
        description=body.get('description') or '', milestone_category=category,
        contract_required_date=contract_required_date,
        must_finish_by_date=parse_date(body['mustFinishByDate']) if body.get('mustFinishByDate') else None,
        allowable_variance_days=float(body.get('allowableVarianceDays') or 0.0),
        responsible_organization=body.get('responsibleOrganization') or '',
        is_critical_milestone=bool(body.get('isCriticalMilestone')),
        has_documented_issue=bool(body.get('hasDocumentedIssue')),
        approval_status=body.get('approvalStatus') or '',
        approved_by=body.get('approvedBy') or '',
        approved_at=timezone.now() if body.get('approvedBy') else None,
        source_document_reference=body.get('sourceDocumentReference') or '',
        notes=body.get('notes') or '',
    )
    # A newly-created entry's first date is not a "revision" of a prior
    # authorized date — only a CHANGE to an existing entry's
    # contract_required_date is logged (see the PATCH handler below).
    return JsonResponse(_serialize_contractual_milestone(entry), status=201)


@csrf_exempt
@require_http_methods(['GET', 'PATCH', 'DELETE'])
def project_contractual_milestone_detail(request, pk, milestone_id):
    from .models import MILESTONE_CATEGORY_CHOICES, ContractualMilestoneRevision, MilestoneDefinition, Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    entry = MilestoneDefinition.objects.filter(pk=milestone_id, project=project).first()
    if not entry:
        return error_response('Contractual milestone not found', status=404)

    if request.method == 'GET':
        return JsonResponse(_serialize_contractual_milestone(entry))

    if request.method == 'DELETE':
        entry.delete()
        return JsonResponse({'deleted': True})

    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    if 'activityId' in body:
        if not (body['activityId'] or '').strip():
            return error_response('activityId cannot be blank')
        entry.activity_id = body['activityId'].strip()
    if 'activityName' in body:
        entry.activity_name = body['activityName'] or ''
    if 'description' in body:
        entry.description = body['description'] or ''
    if 'category' in body:
        valid_categories = {c[0] for c in MILESTONE_CATEGORY_CHOICES}
        if body['category'] not in valid_categories:
            return error_response(f'Unknown category. Valid: {sorted(valid_categories)}')
        entry.milestone_category = body['category']
    if 'mustFinishByDate' in body:
        entry.must_finish_by_date = parse_date(body['mustFinishByDate']) if body['mustFinishByDate'] else None
    if 'allowableVarianceDays' in body:
        entry.allowable_variance_days = float(body['allowableVarianceDays'] or 0.0)
    if 'responsibleOrganization' in body:
        entry.responsible_organization = body['responsibleOrganization'] or ''
    if 'isCriticalMilestone' in body:
        entry.is_critical_milestone = bool(body['isCriticalMilestone'])
    if 'hasDocumentedIssue' in body:
        entry.has_documented_issue = bool(body['hasDocumentedIssue'])
    if 'approvalStatus' in body:
        entry.approval_status = body['approvalStatus'] or ''
    if 'approvedBy' in body:
        entry.approved_by = body['approvedBy'] or ''
        entry.approved_at = timezone.now() if entry.approved_by else None

    # contractRequiredDate is the one field with authorized-change tracking
    # — an actual change is logged as a ContractualMilestoneRevision,
    # requiring a source document reference/reason so the change is never
    # silent. sourceDocumentReference/notes are updated regardless (they
    # can describe the entry itself without a date change).
    if 'sourceDocumentReference' in body:
        entry.source_document_reference = body['sourceDocumentReference'] or ''
    if 'notes' in body:
        entry.notes = body['notes'] or ''

    if 'contractRequiredDate' in body:
        new_date = parse_date(body['contractRequiredDate']) if body['contractRequiredDate'] else None
        if body['contractRequiredDate'] and new_date is None:
            return error_response('contractRequiredDate could not be parsed. Use YYYY-MM-DD.')
        if new_date != entry.contract_required_date:
            ContractualMilestoneRevision.objects.create(
                milestone=entry, previous_date=entry.contract_required_date, new_date=new_date,
                source_document_reference=body.get('revisionSourceDocumentReference') or body.get('sourceDocumentReference') or '',
                reason=body.get('revisionReason') or '', changed_by=body.get('changedBy') or '',
            )
            entry.contract_required_date = new_date

    entry.save()
    return JsonResponse(_serialize_contractual_milestone(entry))


@csrf_exempt
@require_http_methods(['GET'])
def project_contractual_milestone_revisions(request, pk, milestone_id):
    from .models import MilestoneDefinition, Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    entry = MilestoneDefinition.objects.filter(pk=milestone_id, project=project).first()
    if not entry:
        return error_response('Contractual milestone not found', status=404)

    return JsonResponse({
        'milestoneId': str(entry.id),
        'revisions': [_serialize_milestone_revision(r) for r in entry.revisions.all()],
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/field-dashboard-summary/
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET'])
def project_field_dashboard_summary(request, pk):
    """
    Field Dashboard — a presentation/orchestration layer only, same
    discipline as project_dashboard_summary (Main Dashboard): every number
    traces to an existing authoritative engine; nothing is recalculated.
    Reuses activity_analysis.build_activity_analysis (rows — Contractual
    Milestone Tracker P6 matching and Schedule Health), compute_cost_summary
    + _compute_budget_breakdown (Current Spend/Budget/Earned Value — the
    SAME figures /cost-summary/ shows), compute_productivity (hours only —
    headcount is not computed anywhere in ScheduleIQ and is never derived
    from hours here), float_intelligence.compute_float_summary (Schedule
    Health), baseline_progress.build_baseline_progress with
    scurveMetric='hours' (planned-vs-actual manpower curve, only when
    genuinely resource-loaded), and the project-scoped Contractual
    Milestone register (MilestoneDefinition) via contractual_milestones.py.

    Query params: currentVersion/version, previousVersion, baselineVersion
    (same resolution convention as every other analysis endpoint),
    warningThresholdDays (the Contractual Milestone Tracker's configurable
    near-term driving-path warning — default
    contractual_milestones.DEFAULT_WARNING_THRESHOLD_DAYS, calendar basis
    documented there).
    """
    from .models import MilestoneDefinition, Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    ctx = _resolve_analysis_context(project, request)
    if not ctx:
        return error_response('No schedule version available for this project', status=404)

    current, previous, baseline = ctx['current'], ctx['previous'], ctx['baseline']
    current_dd = ctx['current_dd']

    try:
        warning_threshold_days = float(request.GET.get('warningThresholdDays', contractual_milestones.DEFAULT_WARNING_THRESHOLD_DAYS))
    except (TypeError, ValueError):
        return error_response('warningThresholdDays must be a number')

    result = {
        'projectId': str(project.id), 'available': True,
        'context': {
            'currentVersionId': str(current.id),
            'currentVersionLabel': current.version_label or current.original_filename,
            'currentDataDate': current_dd.isoformat() if current_dd else None,
            'previousVersionId': str(previous.id) if previous else None,
            'previousVersionLabel': (previous.version_label or previous.original_filename) if previous else None,
            'baselineVersionId': str(baseline.id) if baseline else None,
            'baselineVersionLabel': (baseline.version_label or baseline.original_filename) if baseline else None,
            'baselineDesignated': baseline is not None,
        },
    }

    # ── Activity Analysis rows — backs the Contractual Milestone Tracker's
    # P6 matching and Schedule Health ────────────────────────────────────
    rows = None
    try:
        analysis = activity_analysis.build_activity_analysis(
            current.activities_json,
            previous_activities=previous.activities_json if previous else None,
            baseline_activities=baseline.activities_json if baseline else None,
            current_data_date=current_dd, previous_data_date=ctx['previous_dd'],
            calendars=ctx['calendars'], calendar_meta=ctx['calendar_meta'],
            update_intelligence_result=ctx['ui_result'], milestone_report=ctx['milestone_report'],
        )
        rows = analysis['rows']
    except Exception as exc:
        result['rowsError'] = str(exc)

    # ── compute_cost_summary — backs Financials, Manpower SPI/CPI, and
    # Schedule Health ────────────────────────────────────────────────────
    cost_summary = None
    try:
        cost_summary = compute_cost_summary(current.activities_json, current_dd)
    except Exception as exc:
        result['costSummaryError'] = str(exc)

    # ── Contractual Milestone Tracker (Phase 2) ─────────────────────────
    try:
        register_entries = [_serialize_contractual_milestone(m) for m in MilestoneDefinition.objects.filter(project=project)]
        contractual_entries = [e for e in register_entries if e['isContractual']]
        if rows is None:
            result['contractualMilestones'] = {'available': False, 'reason': 'Activity analysis unavailable for this version.'}
        elif not contractual_entries:
            result['contractualMilestones'] = {
                'available': True, 'configured': False,
                'reason': 'Contractual Dates Not Configured',
                'milestones': [], 'counts': {'GREEN': 0, 'YELLOW': 0, 'RED': 0, 'UNVERIFIED': 0},
                'warningThresholdDays': warning_threshold_days,
            }
        else:
            tracker = contractual_milestones.build_contractual_milestone_tracker(
                contractual_entries, rows, warning_threshold_days, ctx['calendars'],
            )
            result['contractualMilestones'] = {'available': True, **tracker}
    except Exception as exc:
        result['contractualMilestones'] = {'available': False, 'reason': str(exc)}

    # ── Current Spend / Budget / Earned Value ───────────────────────────
    try:
        if cost_summary is None:
            result['financials'] = {'available': False, 'reason': 'Cost summary unavailable for this version.'}
        else:
            breakdown = _compute_budget_breakdown(project, cost_summary)
            result['financials'] = {
                'available': True,
                'currentSpend': breakdown['actuals']['actualCost'],
                'budget': breakdown['budget']['currentBudget'],
                'originalBudget': breakdown['budget']['originalBudget'],
                'approvedBudgetChanges': breakdown['budget']['approvedBudgetChanges'],
                'earnedValue': {'value': cost_summary['cost']['ev'], 'source': 'imported' if cost_summary['cost']['ev'] is not None else 'unavailable'},
                'commitments': breakdown['commitments'],
                'forecast': breakdown['forecast'],
                'cpi': cost_summary['cost']['cpi'], 'spi': cost_summary['cost']['spi'],
            }
    except Exception as exc:
        result['financials'] = {'available': False, 'reason': str(exc)}

    # ── Manpower (hours only — no headcount engine exists anywhere in
    # ScheduleIQ; never derived from hours) ─────────────────────────────
    try:
        prod = compute_productivity(current.activities_json)
        result['manpower'] = {
            'available': bool(prod['overall']['available']),
            'reason': None if prod['overall']['available'] else 'Unavailable — Schedule is not resource loaded.',
            'budgetedHours': prod['overall']['budgetedHours'], 'earnedHours': prod['overall']['earnedHours'],
            'actualHours': prod['overall']['actualHours'], 'remainingHours': prod['overall']['remainingHours'],
            'spi': cost_summary['hours']['spi'] if cost_summary else None,
            'cpi': cost_summary['hours']['cpi'] if cost_summary else None,
            'headcount': {
                'available': False,
                'reason': 'Current headcount is not tracked by ScheduleIQ — only man-hours. Provide verified staffing data to enable this.',
            },
        }
    except Exception as exc:
        result['manpower'] = {'available': False, 'reason': str(exc)}

    # ── Planned vs Actual manpower curve — real time-phased data only ───
    try:
        bp = baseline_progress.build_baseline_progress(
            baseline.activities_json if baseline else [], current.activities_json, current_dd, ctx['calendars'],
            scurve_metric='hours',
        )
        scurve = bp['scurve']
        result['manpowerCurve'] = {
            'available': bool(scurve.get('available')),
            'reason': scurve.get('reason'),
            'periods': scurve.get('periods') if scurve.get('available') else [],
        }
    except Exception as exc:
        result['manpowerCurve'] = {'available': False, 'reason': str(exc)}

    # ── Spend vs Budget / Earned vs Spend trend — real persisted versions,
    # never a synthetic trend from one snapshot (same rule project_cost_
    # history already follows) ──────────────────────────────────────────
    try:
        versions = list(project.schedule_versions.exclude(data_date=None).order_by('data_date'))
        points = []
        for v in versions:
            try:
                s = compute_cost_summary(v.activities_json, v.data_date)
            except Exception:
                continue
            points.append({
                'versionId': str(v.id), 'versionLabel': v.version_label, 'dataDate': v.data_date.isoformat(),
                'bac': s['cost']['bac'], 'ac': s['cost']['ac'], 'ev': s['cost']['ev'],
            })
        result['spendTrend'] = {'available': len(points) > 0, 'points': points}
    except Exception as exc:
        result['spendTrend'] = {'available': False, 'reason': str(exc)}

    # ── Schedule Health ──────────────────────────────────────────────────
    try:
        if rows is None:
            result['scheduleHealth'] = {'available': False, 'reason': 'Activity analysis unavailable for this version.'}
        else:
            fs = float_intelligence.compute_float_summary(rows)
            result['scheduleHealth'] = {
                'available': True,
                'criticalCount': fs['criticalCount'], 'drivingCount': fs['drivingCount'],
                'negativeFloatCount': fs['negativeFloatCount'],
                'spi': cost_summary['cost']['spi'] if cost_summary else None,
            }
    except Exception as exc:
        result['scheduleHealth'] = {'available': False, 'reason': str(exc)}

    # ── Integration Sources — only Primavera P6 import genuinely exists;
    # never claim a connection that isn't real ───────────────────────────
    result['integrationSources'] = {
        'primaveraP6': {'status': 'CONNECTED', 'detail': 'Imported schedule data drives every ScheduleIQ workspace.'},
        'shelby': {'status': 'NOT_CONNECTED', 'detail': 'No Shelby integration exists in ScheduleIQ.'},
        'fieldData': {'status': 'NOT_CONNECTED', 'detail': 'No field-data integration exists in ScheduleIQ.'},
    }

    # ── Project Issues — no issue-tracking engine exists yet; never
    # fabricate example issues ───────────────────────────────────────────
    result['projectIssues'] = {
        'available': False, 'configured': False, 'issues': [],
        'reason': 'Project issue tracking is not yet configured in ScheduleIQ.',
    }

    return JsonResponse(result)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/projects/<id>/activity-analysis/
# GET /api/projects/<id>/float-analysis/
# GET /api/projects/<id>/float-trend/
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_analysis_context(project, request):
    """Shared version/calendar/prereq resolution for Activity Analysis and
    Float Analysis — same three-version convention already established by
    Risk & Recovery's project_risk_register (_resolve_previous_and_current),
    so 'Current'/'Previous'/'Baseline' mean the same thing on every page."""
    current, previous, baseline = _resolve_previous_and_current(project, request)
    if not current:
        return None
    current_dd = _effective_data_date(current)
    previous_dd = _effective_data_date(previous) if previous else None
    calendars = _calendars_for(*(v for v in (baseline, current, previous) if v))
    calendar_meta = _calendar_meta_for(*(v for v in (baseline, current, previous) if v))

    ui_result = None
    milestone_report = None
    if previous:
        try:
            ui_result = update_intelligence.build_update_intelligence(
                previous.activities_json, current.activities_json, previous_dd, current_dd,
                baseline.activities_json if baseline else None, calendars,
            )
        except Exception:
            ui_result = None
    try:
        comparison_result = compare_schedules(previous.activities_json, current.activities_json, calendars) if previous else None
        milestone_report = build_milestone_report(
            current.activities_json, baseline.activities_json if baseline else None, comparison_result,
        )
    except Exception:
        milestone_report = None

    return {
        'current': current, 'previous': previous, 'baseline': baseline,
        'current_dd': current_dd, 'previous_dd': previous_dd,
        'calendars': calendars, 'calendar_meta': calendar_meta,
        'ui_result': ui_result, 'milestone_report': milestone_report,
    }


def _filter_analysis_rows(rows, request):
    """Cross-filtering support shared by Activity Analysis and Float
    Analysis — filtering the SAME master rows both already consume, so
    'Area = C' means the same subset on both pages rather than each page
    defining its own filter semantics. Query params: wbs/area/discipline/
    contractor/system (exact match, case-insensitive), criticalOnly,
    negativeFloatOnly, zeroFloatOnly, nearCriticalOnly, milestonesOnly.
    Unset filters are no-ops; every filter is additive (AND'd).

    zeroFloatOnly/nearCriticalOnly read currentTotalFloat/nearCritical —
    the SAME per-row fields activity_analysis.build_activity_analysis
    already computed (nearCritical against the one canonical
    NEAR_CRITICAL_FLOAT_THRESHOLD, never a second/hard-coded threshold
    here), and currentTotalFloat is already None for a completed activity
    under the canonical completion rule, so neither filter can pick up a
    completed activity's stale imported float."""
    out = rows
    for field in ('wbs', 'area', 'discipline', 'contractor', 'system'):
        wanted = request.GET.get(field)
        if wanted:
            out = [r for r in out if str(r.get(field, '')).strip().lower() == wanted.strip().lower()]
    if request.GET.get('criticalOnly') in ('true', '1', 'True'):
        # criticalActionable, not the raw imported flag — a completed
        # activity should not satisfy "critical only" scoping merely
        # because its stored isCritical/TF is still true/zero.
        out = [r for r in out if r['criticalActionable']]
    if request.GET.get('negativeFloatOnly') in ('true', '1', 'True'):
        out = [r for r in out if r['negativeFloat']]
    if request.GET.get('zeroFloatOnly') in ('true', '1', 'True'):
        out = [r for r in out if r.get('currentTotalFloat') == 0]
    if request.GET.get('nearCriticalOnly') in ('true', '1', 'True'):
        out = [r for r in out if r.get('nearCritical')]
    if request.GET.get('milestonesOnly') in ('true', '1', 'True'):
        out = [r for r in out if r['isMilestone']]
    return out


def _rows_with_completed_float_included(rows):
    """Audit-only override (?includeCompleted=true) for Float Analysis.
    Restores a completed activity's CURRENT actionable Total Float/Free
    Float to its imported P6 value (instead of the default '—' display)
    and recomputes the classifications that value feeds — negativeFloat/
    newlyNegativeFloat/recoveredFromNegativeFloat/nearCritical/
    criticalActionable/floatChangeVs* — for THAT row only. Never mutates
    the imported value (it was already unchanged either way) and never
    touches incomplete rows. Off by default: completed activities do not
    mix into actionable current float analysis unless explicitly asked
    for."""
    out = []
    for r in rows:
        if not r.get('finished'):
            out.append(r)
            continue
        r2 = dict(r)
        tf = r2.get('importedCurrentTotalFloat')
        ff = r2.get('importedFreeFloat')
        baseline_tf, previous_tf = r2.get('baselineTotalFloat'), r2.get('previousTotalFloat')
        r2['currentTotalFloat'] = tf
        r2['freeFloat'] = ff
        r2['floatChangeVsBaseline'] = (tf - baseline_tf) if (tf is not None and baseline_tf is not None) else None
        r2['floatChangeVsPrevious'] = (tf - previous_tf) if (tf is not None and previous_tf is not None) else None
        r2['negativeFloat'] = tf is not None and tf < 0
        r2['newlyNegativeFloat'] = previous_tf is not None and tf is not None and previous_tf >= 0 and tf < 0
        r2['recoveredFromNegativeFloat'] = previous_tf is not None and tf is not None and previous_tf < 0 and tf >= 0
        r2['nearCritical'] = tf is not None and 0 < tf <= activity_analysis.NEAR_CRITICAL_FLOAT_THRESHOLD
        r2['criticalActionable'] = bool(r2.get('critical'))
        out.append(r2)
    return out


@csrf_exempt
@require_http_methods(['GET'])
def project_activity_analysis(request, pk):
    """Master Activity Analysis — one authoritative analytical row per
    activity (activity_analysis.build_activity_analysis), consumed by
    Activity Analysis, Float Analysis, and Progress/Milestones alike. Never
    truncated — the full analyzed population is always returned; any
    row-count limiting happens only in the UI's own virtualized rendering.
    Query params: currentVersion/version, previousVersion, baselineVersion
    (same resolution convention as Update Intelligence/Risk Register)."""
    from .models import Project, ScheduleRisk

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    ctx = _resolve_analysis_context(project, request)
    if not ctx:
        return error_response('No schedule version available for this project', status=404)

    include_risk = request.GET.get('includeRisk') in ('true', '1', 'True')
    risk_by_key = None
    if include_risk and ctx['ui_result']:
        try:
            register = risk_register.build_risk_register(
                ctx['ui_result'], None, ctx['current'].activities_json, ctx['current_dd'],
            )
            risk_keys = [r['riskKey'] for r in register['risks']]
            workflow_by_key = {sr.risk_key: sr for sr in ScheduleRisk.objects.filter(project=project, risk_key__in=risk_keys)}
            risk_by_key = {}
            for r in register['risks']:
                sr = workflow_by_key.get(r['riskKey'])
                risk_by_key[r['riskKey']] = {
                    **r,
                    'workflow': _serialize_schedule_risk_workflow(sr) if sr else {'status': 'OPEN'},
                }
        except Exception:
            risk_by_key = None

    try:
        result = activity_analysis.build_activity_analysis(
            ctx['current'].activities_json,
            previous_activities=ctx['previous'].activities_json if ctx['previous'] else None,
            baseline_activities=ctx['baseline'].activities_json if ctx['baseline'] else None,
            current_data_date=ctx['current_dd'], previous_data_date=ctx['previous_dd'],
            calendars=ctx['calendars'], calendar_meta=ctx['calendar_meta'],
            update_intelligence_result=ctx['ui_result'], milestone_report=ctx['milestone_report'],
            risk_by_key=risk_by_key,
        )
    except Exception as exc:
        import traceback
        return error_response(f'Activity analysis error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    return JsonResponse({
        'projectId': str(project.id), 'available': True,
        'currentVersionId': str(ctx['current'].id), 'currentVersionLabel': ctx['current'].version_label or ctx['current'].original_filename,
        'currentDataDate': ctx['current_dd'].isoformat() if ctx['current_dd'] else None,
        'previousVersionId': str(ctx['previous'].id) if ctx['previous'] else None,
        'previousVersionLabel': (ctx['previous'].version_label or ctx['previous'].original_filename) if ctx['previous'] else None,
        'previousUnresolved': getattr(ctx['current'], '_previous_unresolved', None),
        'baselineVersionId': str(ctx['baseline'].id) if ctx['baseline'] else None,
        'baselineVersionLabel': (ctx['baseline'].version_label or ctx['baseline'].original_filename) if ctx['baseline'] else None,
        **result,
    })


@csrf_exempt
@require_http_methods(['GET'])
def project_float_analysis(request, pk):
    """Float Intelligence workspace — summary, distribution histogram,
    baseline-vs-current scatter, float-change distribution, heat map,
    deterioration/improvement rankings, newly-negative/recovered lists, and
    milestone float posture — all derived from the SAME master rows
    Activity Analysis uses (never a second, competing float calculation).
    Query params: same version-resolution as project_activity_analysis,
    plus heatmapGroupBy (default 'area'), topN (default None = all)."""
    from .models import Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    ctx = _resolve_analysis_context(project, request)
    if not ctx:
        return error_response('No schedule version available for this project', status=404)

    try:
        analysis = activity_analysis.build_activity_analysis(
            ctx['current'].activities_json,
            previous_activities=ctx['previous'].activities_json if ctx['previous'] else None,
            baseline_activities=ctx['baseline'].activities_json if ctx['baseline'] else None,
            current_data_date=ctx['current_dd'], previous_data_date=ctx['previous_dd'],
            calendars=ctx['calendars'], calendar_meta=ctx['calendar_meta'],
            update_intelligence_result=ctx['ui_result'], milestone_report=ctx['milestone_report'],
        )
        rows = _filter_analysis_rows(analysis['rows'], request)
        include_completed = request.GET.get('includeCompleted') in ('true', '1', 'True')
        if include_completed:
            rows = _rows_with_completed_float_included(rows)

        heatmap_group_by = request.GET.get('heatmapGroupBy', 'area')
        top_n_param = request.GET.get('topN')
        top_n = int(top_n_param) if top_n_param else None

        by_id, pred_of, _succ_of = activity_analysis.build_relationship_maps(ctx['current'].activities_json)

        result = {
            'engineVersion': float_intelligence.ENGINE_VERSION,
            'includeCompleted': include_completed,
            'nearCriticalThresholdDays': activity_analysis.NEAR_CRITICAL_FLOAT_THRESHOLD,
            'summary': float_intelligence.compute_float_summary(rows),
            'distribution': float_intelligence.compute_float_distribution(rows),
            'baselineVsCurrentScatter': float_intelligence.compute_baseline_vs_current_scatter(rows),
            'floatChangeDistribution': float_intelligence.compute_float_change_distribution(rows),
            'heatmap': float_intelligence.compute_float_heatmap(rows, heatmap_group_by),
            'floatVsFinishVarianceScatter': float_intelligence.compute_float_vs_finish_variance_scatter(rows),
            'floatVsRemainingDurationScatter': float_intelligence.compute_float_vs_remaining_duration_scatter(rows),
            'topDeterioration': float_intelligence.rank_float_deterioration(rows, top_n),
            'topImprovement': float_intelligence.rank_float_improvement(rows, top_n),
            'newlyNegative': float_intelligence.list_newly_negative(rows),
            'recoveredFromNegative': float_intelligence.list_recovered_from_negative(rows),
            'milestoneFloatAnalysis': float_intelligence.compute_milestone_float_analysis(rows, pred_of, by_id),
            'calendarConfidence': analysis['calendarConfidence'],
        }
    except Exception as exc:
        import traceback
        return error_response(f'Float analysis error: {exc} | {traceback.format_exc()[-600:]}', status=500)

    return JsonResponse({
        'projectId': str(project.id), 'available': True,
        'currentVersionId': str(ctx['current'].id), 'currentVersionLabel': ctx['current'].version_label or ctx['current'].original_filename,
        'currentDataDate': ctx['current_dd'].isoformat() if ctx['current_dd'] else None,
        'previousVersionId': str(ctx['previous'].id) if ctx['previous'] else None,
        'previousVersionLabel': (ctx['previous'].version_label or ctx['previous'].original_filename) if ctx['previous'] else None,
        'previousUnresolved': getattr(ctx['current'], '_previous_unresolved', None),
        'baselineVersionId': str(ctx['baseline'].id) if ctx['baseline'] else None,
        'baselineVersionLabel': (ctx['baseline'].version_label or ctx['baseline'].original_filename) if ctx['baseline'] else None,
        **result,
    })


def _dashboard_top_drivers(rows, risk_by_key, top_n=10):
    """Top Schedule Drivers — a presentation-only ranking over rows
    activity_analysis.py already computed (criticalActionable/driving/
    negativeFloat, currentTotalFloat, finishMovementDays). No new
    criticality/float/risk judgment is made here."""
    candidates = [
        r for r in rows
        if not r.get('finished') and (r.get('criticalActionable') or r.get('driving') or r.get('negativeFloat'))
    ]
    candidates.sort(key=lambda r: (
        r['currentTotalFloat'] if r.get('currentTotalFloat') is not None else float('inf'),
        -(r.get('finishMovementDays') or 0),
    ))
    out = []
    for r in candidates[:top_n]:
        risk = (risk_by_key or {}).get(r['activityId'])
        out.append({
            'activityId': r['activityId'], 'activityName': r['activityName'],
            'wbs': r.get('wbs'), 'area': r.get('area'),
            'forecastFinish': r.get('forecastFinish') or r.get('currentFinish'),
            'currentTotalFloat': r.get('currentTotalFloat'),
            'updateMovementDays': r.get('finishMovementDays'),
            'critical': r.get('criticalActionable'), 'driving': r.get('driving'), 'negativeFloat': r.get('negativeFloat'),
            'riskSeverity': risk.get('severity') if risk else None,
            'riskUrgency': risk.get('urgency') if risk else None,
        })
    return out


def _dashboard_milestone_forecast(milestone_report, top_n=5):
    """Highest-priority milestones from the SAME milestone report
    Risk & Milestones/Activity Analysis already consume (milestones.py
    build_milestone_report) — risk level, variance and float are read
    straight off it, never recomputed."""
    if not milestone_report:
        return []
    priority = {'Critical': 0, 'At Risk': 1, 'Watch': 2, 'Healthy': 3}
    ordered = sorted(
        milestone_report,
        key=lambda m: (priority.get(m.get('riskLevel'), 4), -(abs(m.get('varianceDays') or 0))),
    )
    return [
        {
            'activityId': m['activityId'], 'activityName': m['activityName'],
            'baselineFinish': m.get('baselineFinish'), 'currentForecast': m.get('currentFinish'),
            'varianceDays': m.get('varianceDays'), 'totalFloat': m.get('totalFloat'),
            'riskLevel': m.get('riskLevel'), 'status': m.get('status'),
        }
        for m in ordered[:top_n]
    ]


def _dashboard_intelligence_bullets(ui_result, register):
    """Deterministic summary statements over numbers ScheduleIQ's own
    engines already produced (update_intelligence.py / risk_register.py).
    Never a new calculation, never an LLM call — this panel is explicitly
    'ScheduleIQ Intelligence', not 'AI Chat'."""
    bullets = []
    if ui_result:
        fm = ui_result.get('floatMovement') or {}
        if fm.get('newlyNegativeFloatCount'):
            bullets.append(f"{fm['newlyNegativeFloatCount']} activities became negative float since the previous update.")
        mc = ui_result.get('movementCounts') or {}
        if mc.get('completedThisPeriod'):
            bullets.append(f"{mc['completedThisPeriod']} activities completed since the previous update.")
        if mc.get('slipped'):
            bullets.append(f"{mc['slipped']} activities moved later this update.")
        mm = ui_result.get('milestoneMovement') or {}
        top_slipped = mm.get('topSlippedMilestones') or []
        if top_slipped:
            m = top_slipped[0]
            unit = 'working day' if m.get('movementWorkingDays') is not None else 'calendar day'
            days = m.get('movementWorkingDays') if m.get('movementWorkingDays') is not None else m.get('movementDays')
            if days:
                bullets.append(f"{m['activityName']} moved {days} {unit}{'s' if days != 1 else ''} later.")
    if register and register.get('available', True):
        cells = (register.get('heatmap') or {}).get('cells') or []
        worst = max(cells, key=lambda c: c.get('negativeFloatCount', 0), default=None)
        if worst and worst.get('negativeFloatCount'):
            bullets.append(f"{worst['group']} contains the largest current negative-float exposure ({worst['negativeFloatCount']} activities).")
    return bullets


@csrf_exempt
@require_http_methods(['GET'])
def project_dashboard_summary(request, pk):
    """
    Main Dashboard — a presentation/orchestration layer only. Every number
    here is read from an existing authoritative engine result (the same
    ones Activity Analysis, Float Analysis, Baseline & Progress, Update
    Analysis, Risk & Milestones and Project Controls already call); nothing
    is recalculated. This exists purely to avoid the dashboard issuing 8+
    separate requests (and Activity Analysis's full per-row payload) just
    to populate a handful of KPI cards — see the Main Dashboard directive's
    performance section.

    Query params: same version-resolution convention as every other
    project-scoped analysis endpoint (currentVersion/version,
    previousVersion, baselineVersion).

    Each panel is independently try/except-wrapped: one engine raising
    (e.g. Risk Register with no Previous version) degrades that one
    section to {'available': False, 'reason': ...} rather than failing the
    whole dashboard.
    """
    from .models import Project, ScheduleRisk

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    ctx = _resolve_analysis_context(project, request)
    if not ctx:
        return error_response('No schedule version available for this project', status=404)

    current, previous, baseline = ctx['current'], ctx['previous'], ctx['baseline']
    current_dd, previous_dd = ctx['current_dd'], ctx['previous_dd']
    ui_result, milestone_report = ctx['ui_result'], ctx['milestone_report']

    result = {
        'projectId': str(project.id), 'available': True,
        'context': {
            'currentVersionId': str(current.id),
            'currentVersionLabel': current.version_label or current.original_filename,
            'currentDataDate': current_dd.isoformat() if current_dd else None,
            'previousVersionId': str(previous.id) if previous else None,
            'previousVersionLabel': (previous.version_label or previous.original_filename) if previous else None,
            'previousDataDate': previous_dd.isoformat() if previous_dd else None,
            'previousUnresolved': getattr(current, '_previous_unresolved', None),
            'baselineVersionId': str(baseline.id) if baseline else None,
            'baselineVersionLabel': (baseline.version_label or baseline.original_filename) if baseline else None,
            'baselineDesignated': baseline is not None,
        },
    }

    # ── Activity Analysis rows — backs KPIs, Float Health, Top Drivers ────
    rows = None
    try:
        analysis = activity_analysis.build_activity_analysis(
            current.activities_json,
            previous_activities=previous.activities_json if previous else None,
            baseline_activities=baseline.activities_json if baseline else None,
            current_data_date=current_dd, previous_data_date=previous_dd,
            calendars=ctx['calendars'], calendar_meta=ctx['calendar_meta'],
            update_intelligence_result=ui_result, milestone_report=milestone_report,
        )
        rows = analysis['rows']
        calendar_confidence = analysis['calendarConfidence']
    except Exception as exc:
        calendar_confidence = None
        result['rowsError'] = str(exc)

    # ── KPI row ─────────────────────────────────────────────────────────
    try:
        if rows is not None:
            float_summary = float_intelligence.compute_float_summary(rows)
            overdue_count = sum(1 for r in rows if r.get('overdue'))
            milestones_at_risk = sum(1 for m in (milestone_report or []) if m.get('riskLevel') in ('Critical', 'At Risk'))
            result['kpis'] = {
                'available': True,
                'totalActivities': len(current.activities_json),
                'totalMilestones': len(milestone_report) if milestone_report is not None else None,
                'currentForecastFinish': current.forecast_finish.isoformat() if current.forecast_finish else None,
                # Deliberately NOT current.baseline_finish — that field is a
                # per-VERSION snapshot (max of each activity's own bFinish
                # in whichever version happens to be "current"), populated
                # regardless of whether a baseline version has been
                # designated at the project level. "Baseline Finish" must
                # only ever reflect the version the project has actually
                # designated as BASELINE (see _resolve_baseline_and_current)
                # — never silently borrow a number from an undesignated one.
                'baselineFinish': (baseline.forecast_finish.isoformat() if (baseline and baseline.forecast_finish) else None),
                'finishVarianceDays': (
                    (current.forecast_finish - baseline.forecast_finish).days
                    if (baseline and current.forecast_finish and baseline.forecast_finish) else None
                ),
                'minimumCurrentTotalFloat': float_summary['minimumCurrentTotalFloat'],
                'medianCurrentTotalFloat': float_summary['medianCurrentTotalFloat'],
                'criticalActivities': float_summary['criticalCount'],
                'overdueActivities': overdue_count,
                'milestonesAtRisk': milestones_at_risk,
                'dataQuality': calendar_confidence,
            }
        else:
            result['kpis'] = {'available': False, 'reason': 'Activity analysis unavailable for this version.'}
    except Exception as exc:
        result['kpis'] = {'available': False, 'reason': str(exc)}

    # ── Baseline vs Current Forecast (reuses Baseline & Progress's own S-Curve) ──
    try:
        bp = baseline_progress.build_baseline_progress(
            baseline.activities_json if baseline else [], current.activities_json, current_dd, ctx['calendars'],
        )
        result['baselineVsForecast'] = {
            'available': bp['hasBaseline'],
            'reason': None if bp['hasBaseline'] else 'No baseline schedule has been selected for this project.',
            'scurve': bp['scurve'],
        }
    except Exception as exc:
        result['baselineVsForecast'] = {'available': False, 'reason': str(exc)}

    # ── Float Health (Float Analysis's own summary, no client-side threshold) ──
    try:
        if rows is not None:
            fs = float_intelligence.compute_float_summary(rows)
            positive = max(0, fs['activitiesAnalyzed'] - fs['negativeFloatCount'] - fs['zeroFloatCount'] - fs['nearCriticalCount'])
            result['floatHealth'] = {
                'available': True,
                'negativeFloatCount': fs['negativeFloatCount'], 'zeroFloatCount': fs['zeroFloatCount'],
                'nearCriticalCount': fs['nearCriticalCount'], 'positiveFloatCount': positive,
                'nearCriticalThresholdDays': activity_analysis.NEAR_CRITICAL_FLOAT_THRESHOLD,
                'completedActivitiesExcluded': fs['completedActivitiesExcluded'],
            }
        else:
            result['floatHealth'] = {'available': False, 'reason': 'Activity analysis unavailable for this version.'}
    except Exception as exc:
        result['floatHealth'] = {'available': False, 'reason': str(exc)}

    # ── Update Intelligence ────────────────────────────────────────────
    try:
        if ui_result:
            result['updateIntelligence'] = {
                'available': True,
                'previousVersionLabel': previous.version_label or previous.original_filename,
                'currentVersionLabel': current.version_label or current.original_filename,
                'movementCounts': ui_result['movementCounts'],
                # Counts only — floatMovement's improved/deteriorated/
                # newlyNegativeFloat/etc. are full per-activity lists,
                # exactly the kind of massive payload this endpoint exists
                # to avoid shipping. Drill into Update Analysis for the lists.
                'floatMovement': {
                    k: v for k, v in ui_result['floatMovement'].items() if k.endswith('Count')
                },
                'milestoneMovement': {
                    'topSlippedMilestones': (ui_result.get('milestoneMovement') or {}).get('topSlippedMilestones', [])[:5],
                },
                'logicChanges': {
                    'addedCount': ui_result['logicChanges']['addedCount'],
                    'removedCount': ui_result['logicChanges']['removedCount'],
                    'changedCount': ui_result['logicChanges']['changedCount'],
                },
            }
        else:
            result['updateIntelligence'] = {
                'available': False,
                'reason': _no_previous_reason(current, 'Update Intelligence requires a Previous version.'),
            }
    except Exception as exc:
        result['updateIntelligence'] = {'available': False, 'reason': str(exc)}

    # ── Milestone Forecast ──────────────────────────────────────────────
    try:
        result['milestoneForecast'] = {
            'available': milestone_report is not None,
            'topMilestones': _dashboard_milestone_forecast(milestone_report),
        }
    except Exception as exc:
        result['milestoneForecast'] = {'available': False, 'reason': str(exc)}

    # ── Top Schedule Drivers (rows + optional Risk Register join) ───────
    register = None
    try:
        if rows is not None:
            risk_by_key = None
            if ui_result:
                try:
                    lookahead_result = baseline_progress.build_report_lookahead(
                        baseline.activities_json if baseline else [], current.activities_json,
                        data_date=current_dd, calendars=ctx['calendars'],
                    )
                    register = risk_register.build_risk_register(ui_result, lookahead_result, current.activities_json, current_dd)
                    risk_by_key = {r['riskKey']: r for r in register['risks']}
                except Exception:
                    register = None
            result['topDrivers'] = {'available': True, 'drivers': _dashboard_top_drivers(rows, risk_by_key)}
        else:
            result['topDrivers'] = {'available': False, 'reason': 'Activity analysis unavailable for this version.'}
    except Exception as exc:
        result['topDrivers'] = {'available': False, 'reason': str(exc)}

    # ── 4-Week Look Ahead ────────────────────────────────────────────────
    try:
        la = baseline_progress.build_lookahead(
            baseline.activities_json if baseline else [], current.activities_json, data_date=current_dd, calendars=ctx['calendars'],
        )
        result['lookAhead'] = {'available': True, 'window': la['window'], 'summaryCards': la['summaryCards']}
    except Exception as exc:
        result['lookAhead'] = {'available': False, 'reason': str(exc)}

    # ── Manpower & Productivity (authoritative cost_engine.py only) ─────
    try:
        prod = compute_productivity(current.activities_json)
        cost = compute_cost_summary(current.activities_json, current_dd)
        result['manpower'] = {
            'available': bool(prod['overall']['available']),
            'reason': None if prod['overall']['available'] else 'Unavailable — Schedule is not resource loaded.',
            'budgetedHours': prod['overall']['budgetedHours'], 'earnedHours': prod['overall']['earnedHours'],
            'actualHours': prod['overall']['actualHours'], 'remainingHours': prod['overall']['remainingHours'],
            'cpi': cost['hours']['cpi'], 'spi': cost['hours']['spi'],
            'eac': (cost['hoursForecast']['scenarios'][0]['eac'] if cost['hoursForecast']['scenarios'] else None),
        }
    except Exception as exc:
        result['manpower'] = {'available': False, 'reason': str(exc)}

    # ── ScheduleIQ Intelligence (deterministic summary of the above) ────
    try:
        result['intelligence'] = {'available': True, 'bullets': _dashboard_intelligence_bullets(ui_result, register)}
    except Exception as exc:
        result['intelligence'] = {'available': False, 'reason': str(exc)}

    return JsonResponse(result)


@csrf_exempt
@require_http_methods(['GET'])
def project_float_trend(request, pk):
    """Float Trend Across Versions — Imported P6 Total Float for one
    activity/milestone across every schedule version of this project, each
    point using that version's own effective Data Date (never
    recomputed against today's schedule). Query param: activityId
    (required)."""
    from .models import Project

    try:
        project = Project.objects.get(pk=pk)
    except Exception:
        return error_response('Project not found', status=404)

    activity_id = request.GET.get('activityId')
    if not activity_id:
        return error_response('activityId is required')

    ordered = list(version_chronology.chronological_versions(project))
    if not ordered:
        return error_response('No schedule version available for this project', status=404)
    roles = _assign_version_roles(ordered)

    version_points = []
    for v in ordered:
        dd = _effective_data_date(v)
        version_points.append({
            'versionId': str(v.id), 'versionLabel': v.version_label or v.original_filename,
            'dataDate': dd.isoformat() if dd else None, 'role': roles.get(str(v.id), 'OTHER'),
            'activities': v.activities_json,
        })
    # Chronological order (oldest first) for a left-to-right trend chart —
    # driven by each version's own Data Date, never upload recency alone.
    version_points.sort(key=lambda p: (p['dataDate'] is None, p['dataDate'] or ''))

    result = float_intelligence.compute_float_trend(version_points, activity_id)
    # Strip the (potentially large) activities list back out of the echoed
    # points — the trend result already carries only the resolved value.
    return JsonResponse({
        'projectId': str(project.id), 'activityId': activity_id,
        'points': result['points'],
    })
