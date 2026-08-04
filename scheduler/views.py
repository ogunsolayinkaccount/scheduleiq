import json
import os
from datetime import date as _date_type

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods

from .utils import (
    compute_metrics,
    load_table_from_csv,
    load_table_from_excel,
    normalize_activities,
    parse_date,
)
from .parsers import parse_xer, xer_to_activities, parse_msp_xml, parse_pdf_schedule
from .status_engine import (
    classify_schedule,
    ThresholdValues,
    MilestoneInput,
)
from .quality_engine import assess_quality
from .narrative_engine import generate_narrative


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

    # Run quality engine (required for narrative quality section)
    from datetime import date as _date_cls
    dd = data_date or _date_cls.today()
    try:
        q_result = assess_quality(cur_p, dd)
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
