import re
import io
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Optional, List, Dict, Any

import pandas as pd

from .calendar_engine import decode_calendar_data


# ── Date parsing ──────────────────────────────────────────────────────────────

def parse_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v

    s = str(v).strip()
    if not s or s.lower() in ('nan', 'none', 'null', ''):
        return None

    # P6 XER format: DD-Mon-YY or DD-Mon-YYYY (e.g. "12-MAR-24")
    m = re.match(r'^(\d{1,2})-([A-Za-z]{3})-(\d{2,4})$', s)
    if m:
        months = {
            'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
            'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
        }
        day, mon_str, yr = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        month = months.get(mon_str)
        if yr < 100:
            yr += 2000
        if month:
            try:
                return date(yr, month, day)
            except ValueError:
                return None

    for fmt in (
        '%Y-%m-%d',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%S.%f',
        '%m/%d/%Y',
        '%d/%m/%Y',
        '%d.%m.%Y',
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    return None


# ── XER parser ────────────────────────────────────────────────────────────────

def parse_xer(text: str) -> Dict[str, List[dict]]:
    sections: Dict[str, List[dict]] = {}
    current: Optional[str] = None
    headers: List[str] = []

    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith('%T'):
            current = line[3:].strip()
            sections[current] = []
            headers = []
        elif line.startswith('%F'):
            headers = line[3:].strip().split('\t')
        elif line.startswith('%R') and current is not None:
            values = line[3:].split('\t')
            row = {headers[i]: (values[i] if i < len(values) else '') for i in range(len(headers))}
            sections[current].append(row)

    return sections


def xer_to_activities(sections: Dict[str, List[dict]], file_name: str) -> List[dict]:
    # WBS hierarchy lookup
    wbs_rows_by_id: Dict[str, dict] = {w.get('wbs_id', ''): w for w in sections.get('PROJWBS', [])}
    wbs_name_map: Dict[str, str] = {
        wid: w.get('wbs_name') or w.get('wbs_short_name') or ''
        for wid, w in wbs_rows_by_id.items()
    }
    wbs_parent_map: Dict[str, str] = {
        wid: w.get('parent_wbs_id', '') or ''
        for wid, w in wbs_rows_by_id.items()
    }
    # WBS short-name (code) and sequence-number maps
    wbs_short_map: Dict[str, str] = {
        wid: w.get('wbs_short_name') or ''
        for wid, w in wbs_rows_by_id.items()
    }
    wbs_seq_map: Dict[str, int] = {}
    for _wid, _w in wbs_rows_by_id.items():
        try:
            wbs_seq_map[_wid] = int(_w.get('seq_num') or 0)
        except (ValueError, TypeError):
            wbs_seq_map[_wid] = 0

    # Keep simple map for backward compat
    wbs_map: Dict[str, str] = wbs_name_map

    # Build full WBS ancestor path ("Root > Parent > Node")
    _path_cache: Dict[str, str] = {}
    def get_wbs_path(wbs_id: str) -> str:
        if not wbs_id or wbs_id not in wbs_rows_by_id:
            return ''
        if wbs_id in _path_cache:
            return _path_cache[wbs_id]
        parts: list = []
        cur = wbs_id
        visited: set = set()
        while cur and cur not in visited and cur in wbs_rows_by_id:
            visited.add(cur)
            name = wbs_name_map.get(cur, '')
            if name:
                parts.insert(0, name)
            cur = wbs_parent_map.get(cur, '') or ''
        result = ' > '.join(parts)
        _path_cache[wbs_id] = result
        return result

    # Build WBS code path using short_names ("AREA.MECH.EQUIP")
    _code_cache: Dict[str, str] = {}
    def get_wbs_code(wbs_id: str) -> str:
        if not wbs_id or wbs_id not in wbs_rows_by_id:
            return ''
        if wbs_id in _code_cache:
            return _code_cache[wbs_id]
        parts: list = []
        cur = wbs_id
        visited: set = set()
        while cur and cur not in visited and cur in wbs_rows_by_id:
            visited.add(cur)
            code = wbs_short_map.get(cur, '')
            if code:
                parts.insert(0, code)
            cur = wbs_parent_map.get(cur, '') or ''
        result = '.'.join(parts)
        _code_cache[wbs_id] = result
        return result

    # Build zero-padded sequence sort key (preserves P6 ordering)
    _sort_cache: Dict[str, str] = {}
    def get_wbs_sort_key(wbs_id: str) -> str:
        if not wbs_id or wbs_id not in wbs_rows_by_id:
            return ''
        if wbs_id in _sort_cache:
            return _sort_cache[wbs_id]
        parts: list = []
        cur = wbs_id
        visited: set = set()
        while cur and cur not in visited and cur in wbs_rows_by_id:
            visited.add(cur)
            parts.insert(0, f'{wbs_seq_map.get(cur, 0):010d}')
            cur = wbs_parent_map.get(cur, '') or ''
        result = '.'.join(parts)
        _sort_cache[wbs_id] = result
        return result

    # Depth of a WBS node (root = 1)
    _level_cache: Dict[str, int] = {}
    def get_wbs_level(wbs_id: str) -> int:
        if not wbs_id or wbs_id not in wbs_rows_by_id:
            return 0
        if wbs_id in _level_cache:
            return _level_cache[wbs_id]
        level, cur, visited = 0, wbs_id, set()
        while cur and cur not in visited and cur in wbs_rows_by_id:
            visited.add(cur); level += 1
            cur = wbs_parent_map.get(cur, '') or ''
        _level_cache[wbs_id] = level
        return level

    # ── Resource master (RSRC table) — name, type, unit per resource ID ────────
    rsrc_master: Dict[str, dict] = {}
    for r in sections.get('RSRC', []):
        rid = r.get('rsrc_id', '')
        if rid:
            rsrc_master[rid] = {
                'name': r.get('rsrc_name') or r.get('rsrc_short_name') or rid,
                'type': r.get('rsrc_type') or 'RT_Labor',  # RT_Labor / RT_Nonlabor / RT_Mat
            }

    # ── Role master (ROLE table) — discipline / crew roles ───────────────────
    role_master: Dict[str, str] = {}
    for r in sections.get('ROLE', []):
        rid = r.get('role_id', '')
        if rid:
            role_master[rid] = r.get('role_name') or r.get('role_short_name') or rid

    # ── Detailed resource rollup per task: EVM + man-hours ───────────────────
    _zero = lambda: {
        'bgt_cost': 0.0, 'act_cost': 0.0, 'rem_cost': 0.0,
        'bgt_hrs':  0.0, 'act_hrs':  0.0, 'rem_hrs':  0.0,
        # EVM loading flags
        'has_rsrc_load': False, 'has_cost_load': False,
        # Primary resource (for display)
        'primary_rsrc_name': '', 'primary_rsrc_type': '',
        'rsrc_count': 0,
    }
    rsrc_map: Dict[str, dict] = {}
    for r in sections.get('TASKRSRC', []):
        tid     = r.get('task_id', '')
        rsrc_id = r.get('rsrc_id', '') or ''
        role_id = r.get('role_id', '') or ''
        # P6 stores 'Y'/'N'; some exports use '1'/'0'
        is_primary = str(r.get('is_primary_rsrc', '')).strip().upper() in ('Y', '1', 'TRUE')

        if tid not in rsrc_map:
            rsrc_map[tid] = _zero()
        d = rsrc_map[tid]

        bgt_qty  = float(r.get('target_qty')    or 0)   # Budgeted Units
        act_qty  = (float(r.get('act_reg_qty')  or 0)   # Actual Regular Units
                  + float(r.get('act_ot_qty')   or 0))  # Actual Overtime Units
        rem_qty  = float(r.get('remain_qty')    or 0)   # Remaining Units
        bgt_cost = float(r.get('target_cost')   or 0)   # Budgeted Cost
        act_cost = (float(r.get('act_reg_cost') or 0)   # Actual Regular Cost
                  + float(r.get('act_ot_cost')  or 0))  # Actual Overtime Cost
        rem_cost = float(r.get('remain_cost')   or 0)   # Remaining Cost

        d['bgt_cost'] += bgt_cost
        d['act_cost'] += act_cost
        d['rem_cost'] += rem_cost
        d['bgt_hrs']  += bgt_qty
        d['act_hrs']  += act_qty
        d['rem_hrs']  += rem_qty

        # Resource-loaded: at least one assignment has budgeted units
        if bgt_qty  > 0: d['has_rsrc_load'] = True
        # Cost-loaded: at least one assignment has budgeted cost
        if bgt_cost > 0: d['has_cost_load'] = True

        d['rsrc_count'] += 1

        # Primary resource: explicit flag wins, otherwise first assignment fills in
        if is_primary or not d['primary_rsrc_name']:
            if rsrc_id and rsrc_id in rsrc_master:
                d['primary_rsrc_name'] = rsrc_master[rsrc_id]['name']
                d['primary_rsrc_type'] = rsrc_master[rsrc_id]['type']
            elif role_id:
                d['primary_rsrc_name'] = role_master.get(role_id, role_id)
                d['primary_rsrc_type'] = 'RT_Crew'

    # ── Per-task resource assignment detail ────────────────────────────────────
    def _flt(v, d: float = 0.0) -> float:
        try:
            return float(v) if v else d
        except (ValueError, TypeError):
            return d

    def _isodate(raw) -> Optional[str]:
        d = parse_date(raw)
        return d.isoformat() if d else None

    rsrc_detail_map: Dict[str, list] = {}
    for r in sections.get('TASKRSRC', []):
        tid = r.get('task_id', '')
        if not tid:
            continue
        rsrc_id = r.get('rsrc_id', '') or ''
        role_id = r.get('role_id', '') or ''
        is_primary = str(r.get('is_primary_rsrc', '')).strip().upper() in ('Y', '1', 'TRUE')
        ri = rsrc_master.get(rsrc_id, {})
        rn = role_master.get(role_id, '') if role_id else ''
        rsrc_detail_map.setdefault(tid, []).append({
            'rsrcId':            rsrc_id,
            'rsrcName':          ri.get('name') or rn or rsrc_id or role_id or 'Unknown',
            'rsrcType':          ri.get('type', '') or r.get('rsrc_type', ''),
            'roleId':            role_id,
            'roleName':          rn,
            'isPrimary':         is_primary,
            # Units
            'budgetedUnits':     _flt(r.get('target_qty')),
            'actualRegUnits':    _flt(r.get('act_reg_qty')),
            'actualOTUnits':     _flt(r.get('act_ot_qty')),
            'actualUnits':       _flt(r.get('act_reg_qty')) + _flt(r.get('act_ot_qty')),
            'remainingUnits':    _flt(r.get('remain_qty')),
            # Cost
            'budgetedCost':      _flt(r.get('target_cost')),
            'actualCost':        _flt(r.get('act_reg_cost')) + _flt(r.get('act_ot_cost')),
            'remainingCost':     _flt(r.get('remain_cost')),
            # Lag
            'lagHrs':            _flt(r.get('lag_drtn_hr_cnt')),
            # Dates
            'actualStart':       _isodate(r.get('act_start_date')),
            'actualFinish':      _isodate(r.get('act_end_date')),
            'remainEarlyStart':  _isodate(r.get('remain_early_start_date')),
            'remainEarlyEnd':    _isodate(r.get('remain_early_end_date')),
            'remainLateStart':   _isodate(r.get('remain_late_start_date')),
            'remainLateEnd':     _isodate(r.get('remain_late_end_date')),
            'targetStart':       _isodate(r.get('target_start_date')),
            'targetEnd':         _isodate(r.get('target_end_date')),
        })

    # Project name lookup
    proj_meta: Dict[str, dict] = {}
    for p in sections.get('PROJECT', []):
        proj_meta[p.get('proj_id', '')] = {
            'name': p.get('proj_short_name') or p.get('proj_id', '')
        }

    # Predecessor / successor relationships from TASKPRED
    # Each TASKPRED row: task_id = successor, pred_task_id = predecessor
    _xer_rel_type = {'PR_FS': 'FS', 'PR_SS': 'SS', 'PR_FF': 'FF', 'PR_SF': 'SF'}
    preds_map: Dict[str, list] = {}   # task_id -> [{actId, relType, lagDays}]
    succs_map: Dict[str, list] = {}   # task_id -> [{actId, relType, lagDays}]
    for rel in sections.get('TASKPRED', []):
        succ_id  = rel.get('task_id', '')
        pred_id  = rel.get('pred_task_id', '')
        rel_type = _xer_rel_type.get(rel.get('pred_type', ''), 'FS')
        lag_days = round(float(rel.get('lag_hr_cnt') or 0) / 8, 1)
        if succ_id and pred_id:
            preds_map.setdefault(succ_id, []).append({'actId': pred_id, 'relType': rel_type, 'lagDays': lag_days})
            succs_map.setdefault(pred_id, []).append({'actId': succ_id, 'relType': rel_type, 'lagDays': lag_days})

    # Reference data (calendars / activity codes / UDFs) — gap-fill, Phase 2.
    ref = extract_xer_reference_data(sections)
    calendar_name_by_id = ref['calendar_name_by_id']
    task_codes = ref['task_codes']
    task_udfs = ref['task_udfs']

    activities = []
    for t in sections.get('TASK', []):
        task_id   = t.get('task_id', '')
        task_type = t.get('task_type', 'TT_Task')
        proj_id   = t.get('proj_id') or file_name
        proj_name = proj_meta.get(proj_id, {}).get('name', proj_id)

        tf             = float(t.get('total_float_hr_cnt') or 0) / 8
        free_tf        = float(t.get('free_float_hr_cnt')  or 0) / 8
        orig_dur_hrs   = float(t.get('target_drtn_hr_cnt') or 0)
        remain_dur_hrs = float(t.get('remain_drtn_hr_cnt') or 0)
        dur            = (orig_dur_hrs or remain_dur_hrs) / 8
        remain_dur     = remain_dur_hrs / 8
        # Provenance for `dur` — additive only, does not change `dur` itself.
        # `dur` silently falls back to Remaining Duration when P6's own
        # target_drtn_hr_cnt is blank/zero (see line above); this flag lets
        # callers distinguish a confidently-imported Original Duration from
        # a fallback value before presenting it as "Original Duration".
        if orig_dur_hrs > 0:
            orig_dur_source = 'IMPORTED'
        elif remain_dur_hrs > 0:
            orig_dur_source = 'FALLBACK_REMAINING'
        else:
            orig_dur_source = 'UNAVAILABLE'

        # Respect P6's % Complete Type field (CP_Drtn / CP_Phys / CP_Units)
        pct_type  = (t.get('complete_pct_type') or '').strip()
        phys_pct  = float(t.get('phys_complete_pct') or 0)
        if pct_type == 'CP_Phys':
            pct_complete = phys_pct
        else:
            # Duration % (P6 default) = (Orig − Remain) / Orig × 100
            if orig_dur_hrs > 0:
                pct_complete = max(0.0, min(100.0,
                    (orig_dur_hrs - remain_dur_hrs) / orig_dur_hrs * 100.0))
            else:
                pct_complete = phys_pct
        # Actual finish date is definitive: task is 100 % complete
        if t.get('act_end_date'):
            pct_complete = 100.0
            remain_dur   = 0.0

        r = rsrc_map.get(task_id, _zero())
        activities.append({
            'id':           task_id,
            'code':         t.get('task_code') or task_id,
            'name':         t.get('task_name') or '',
            'projectId':    proj_id,
            'projectName':  proj_name,
            'sourceFile':   file_name,
            'wbs':          wbs_map.get(t.get('wbs_id', ''), ''),
            'wbsPath':      get_wbs_path(t.get('wbs_id', '')),
            'wbsId':        t.get('wbs_id', '') or '',
            'wbsCode':      get_wbs_code(t.get('wbs_id', '')),
            'wbsLevel':     get_wbs_level(t.get('wbs_id', '')),
            'wbsSortKey':   get_wbs_sort_key(t.get('wbs_id', '')),
            'type':         task_type,
            'status':       t.get('status_code') or 'TK_NotStart',
            # ── P6 actual dates (only set once work is underway/complete) ─────
            'start':        parse_date(t.get('act_start_date')),
            'finish':       parse_date(t.get('act_end_date')),
            # ── P6 baseline dates (from assigned primary baseline snapshot) ───
            'bStart':       parse_date(t.get('target_start_date')),
            'bFinish':      parse_date(t.get('target_end_date')),
            # ── P6 CPM schedule dates (forward/backward pass results) ─────────
            'earlyStart':   parse_date(t.get('early_start_date')),
            'earlyFinish':  parse_date(t.get('early_end_date')),
            'lateStart':    parse_date(t.get('late_start_date')),
            'lateFinish':   parse_date(t.get('late_end_date')),
            # ── P6 remaining dates (where remaining work starts/finishes) ──────
            'remainStart':  parse_date(t.get('restart_date')),
            'remainFinish': parse_date(t.get('reend_date')),
            'dur':          dur,
            'origDurSource': orig_dur_source,
            'remainDur':    remain_dur,
            'totalFloat':   tf,
            'freeFloat':    free_tf,
            'pctComplete':  pct_complete,
            # Legacy combined cost field (backward compat)
            'cost':         r['act_cost'] + r['rem_cost'],
            # Detailed cost breakdown
            'budgetedCost': r['bgt_cost'],
            'actualCost':   r['act_cost'],
            'remainingCost':r['rem_cost'],
            # Man-hours
            'budgetedHours': r['bgt_hrs'],
            'actualHours':   r['act_hrs'],
            'remainingHours':r['rem_hrs'],
            # EVM loading detection
            'primaryResource':     r.get('primary_rsrc_name', ''),
            'primaryResourceType': r.get('primary_rsrc_type', ''),
            'isResourceLoaded':    r.get('has_rsrc_load', False),
            'isCostLoaded':        r.get('has_cost_load', False),
            'resourceCount':       r.get('rsrc_count', 0),
            'isCritical':   tf <= 0,
            'isMilestone':  'Mile' in task_type,
            'predecessors': preds_map.get(task_id, []),
            'successors':   succs_map.get(task_id, []),
            'predCount':    len(preds_map.get(task_id, [])),
            'succCount':    len(succs_map.get(task_id, [])),
            # ── Full per-activity resource assignments (TASKRSRC) ─────────────
            'resourceAssignments': rsrc_detail_map.get(task_id, []),
            # ── Additional TASK fields ────────────────────────────────────────
            'constraintType':    t.get('cstr_type') or '',
            'constraintDate':    parse_date(t.get('cstr_date')),
            'constraint2Type':   t.get('cstr_type2') or '',
            'constraint2Date':   parse_date(t.get('cstr_date2')),
            'onLongestPath':     str(t.get('driving_path_flag', 'N')).upper() in ('Y', 'TRUE', '1'),
            'floatPath':         (int(t['float_path']) if t.get('float_path') and str(t.get('float_path', '0')).strip().lstrip('-').isdigit() else None),
            'priority':          (int(t['priority_num']) if t.get('priority_num') and str(t.get('priority_num', '0')).strip().isdigit() else None),
            'calendarId':        t.get('clndr_id') or '',
            'expectedFinish':    parse_date(t.get('expect_end_date')),
            'suspendDate':       parse_date(t.get('suspend_date')),
            'resumeDate':        parse_date(t.get('resume_date')),
            'budgetedWork':      float(t.get('target_work_qty') or 0) / 8,
            'actualWork':        float(t.get('act_work_qty') or 0) / 8,
            'remainingWork':     float(t.get('remain_work_qty') or 0) / 8,
            'pctCompleteType':   pct_type or 'CP_Drtn',
            'physPctComplete':   phys_pct,
            # ── Gap-fill: calendar name, activity codes, UDFs (Phase 2) ────────
            'calendarName':      calendar_name_by_id.get(t.get('clndr_id') or '', ''),
            'activityCodes':     {c['typeName']: c['value'] for c in task_codes.get(task_id, [])},
            'udfs':              dict(task_udfs.get(task_id, {})),
        })
        # Canonical filter fields (area/discipline/contractor/system/phase/
        # responsibleManager) — same keys Excel/CSV import produces, so
        # filtering/comparison work uniformly regardless of source format.
        for code in task_codes.get(task_id, []):
            canon = _canonical_code_key(code['typeName'])
            if canon and canon not in activities[-1]:
                activities[-1][canon] = code['value']

    return activities


# ── XER project metadata ──────────────────────────────────────────────────────

def parse_project_meta_from_xer(sections: Dict[str, List[dict]]) -> Dict[str, Any]:
    """
    Extract project-level fields from the XER PROJECT table. Takes the first
    PROJECT row — XER exports are almost always single-project; multi-project
    exports would need per-activity proj_id scoping, which is out of scope here.
    Returns {} (not fabricated values) when the PROJECT table is missing fields.
    """
    rows = sections.get('PROJECT', [])
    if not rows:
        return {}
    p = rows[0]
    return {
        'project_id': p.get('proj_id') or '',
        'project_name': p.get('proj_short_name') or p.get('proj_id') or '',
        'data_date': parse_date(p.get('last_recalc_date')),
        'planned_start': parse_date(p.get('plan_start_date')),
        'forecast_finish': parse_date(p.get('scd_end_date')) or parse_date(p.get('plan_end_date')),
        'must_finish_by': parse_date(p.get('plan_end_date')) if p.get('must_finish_date') is None else parse_date(p.get('must_finish_date')),
        'default_calendar_id': p.get('clndr_id') or '',
    }


# ── XER reference data: calendars, activity codes, UDFs ───────────────────────

def extract_xer_reference_data(sections: Dict[str, List[dict]]) -> Dict[str, Any]:
    """
    Parse CALENDAR / ACTVTYPE / ACTVCODE / UDFTYPE into plain-dict rows ready
    for bulk_create into the Calendar / ActivityCodeType / ActivityCode /
    UDFType Django models.

    XER's clndr_data blob (per-day working hours / holiday exceptions) is
    run through calendar_engine.decode_calendar_data(), which only trusts
    well-documented structural markers (DaysOfWeek/Day, Exceptions/Excp) and
    reports its own confidence ('high'/'partial'/'none') rather than
    guessing. has_detailed_definition is set True only when that decode
    yields a usable standard_workweek, so callers still never treat an
    undecoded or low-confidence calendar as a full working calendar.
    """
    calendars = []
    for c in sections.get('CALENDAR', []):
        def _hrs(key):
            try:
                v = c.get(key)
                return float(v) if v not in (None, '') else None
            except (TypeError, ValueError):
                return None
        raw_clndr_data = c.get('clndr_data') or ''
        decoded = decode_calendar_data(raw_clndr_data)
        has_detail = decoded['confidence'] in ('high', 'partial') and bool(decoded['standardWorkweek'])
        calendars.append({
            'calendar_id': c.get('clndr_id') or '',
            'name': c.get('clndr_name') or '',
            'calendar_type': c.get('clndr_type') or '',
            'is_default': str(c.get('default_flag', '')).strip().upper() in ('Y', '1', 'TRUE'),
            'hours_per_day': _hrs('day_hr_cnt'),
            'hours_per_week': _hrs('week_hr_cnt'),
            'hours_per_month': _hrs('month_hr_cnt'),
            'hours_per_year': _hrs('year_hr_cnt'),
            'has_detailed_definition': has_detail,
            'standard_workweek': decoded['standardWorkweek'] or {},
            'exceptions': decoded['exceptions'],
            'raw_definition': raw_clndr_data,
        })

    code_types = []
    code_type_name_by_id: Dict[str, str] = {}
    for t in sections.get('ACTVTYPE', []):
        type_id = t.get('actv_code_type_id') or ''
        name = t.get('actv_code_type') or type_id
        code_type_name_by_id[type_id] = name
        code_types.append({
            'code_type_id': type_id,
            'name': name,
            'is_global': str(t.get('actv_code_type_scope', '')).strip().upper() in ('AS_GLOBAL', 'GLOBAL'),
        })

    codes = []
    code_value_by_id: Dict[str, dict] = {}   # actv_code_id -> {typeId, value}
    for c in sections.get('ACTVCODE', []):
        code_id = c.get('actv_code_id') or ''
        type_id = c.get('actv_code_type_id') or ''
        value = c.get('short_name') or c.get('actv_code_name') or code_id
        codes.append({
            'code_type_id': type_id,
            'code_id': code_id,
            'code_value': value,
            'description': c.get('actv_code_name') or '',
            'parent_code_id': c.get('parent_actv_code_id') or '',
        })
        code_value_by_id[code_id] = {'typeId': type_id, 'typeName': code_type_name_by_id.get(type_id, type_id), 'value': value}

    # task_id -> [{typeName, value}] via TASKACTV assignment rows
    task_codes: Dict[str, List[dict]] = {}
    for a in sections.get('TASKACTV', []):
        task_id = a.get('task_id') or ''
        code_id = a.get('actv_code_id') or ''
        info = code_value_by_id.get(code_id)
        if task_id and info:
            task_codes.setdefault(task_id, []).append({'typeName': info['typeName'], 'value': info['value']})

    udf_types = []
    udf_type_name_by_id: Dict[str, str] = {}
    for u in sections.get('UDFTYPE', []):
        udf_id = u.get('udf_type_id') or ''
        field_name = u.get('udf_type_label') or u.get('udf_type_name') or udf_id
        udf_type_name_by_id[udf_id] = field_name
        udf_types.append({
            'udf_type_id': udf_id,
            'field_name': field_name,
            'subject_area': u.get('table_name') or '',
            'data_type': u.get('logical_data_type') or '',
        })

    # task_id -> {fieldName: value} via UDFVALUE rows (subject_area TASK only —
    # project-level UDFs would need proj_id scoping, out of scope for the
    # per-activity enrichment this feeds).
    task_udfs: Dict[str, Dict[str, str]] = {}
    for v in sections.get('UDFVALUE', []):
        task_id = v.get('fk_id') or ''
        udf_id = v.get('udf_type_id') or ''
        field_name = udf_type_name_by_id.get(udf_id)
        if not (task_id and field_name):
            continue
        value = (
            v.get('udf_text') or v.get('udf_number') or v.get('udf_date')
            or v.get('udf_code_id') or ''
        )
        if value:
            task_udfs.setdefault(task_id, {})[field_name] = value

    return {
        'calendars': calendars,
        'code_types': code_types,
        'codes': codes,
        'udf_types': udf_types,
        'task_codes': task_codes,   # not persisted directly — used to enrich activities in xer_to_activities
        'task_udfs': task_udfs,     # not persisted directly — used to enrich activities in xer_to_activities
        'calendar_name_by_id': {c['calendar_id']: c['name'] for c in calendars},
    }


# Activity-code type names that map onto ScheduleIQ's canonical filter fields
# (the same ones Excel/CSV import recognises via column_mapping.py), so a
# project's Area/Discipline/Contractor/etc. filter the same way regardless of
# whether it came in as XER activity codes or an Excel column.
_CANONICAL_CODE_TYPE_MAP = {
    'area': 'area', 'system': 'system', 'phase': 'phase',
    'discipline': 'discipline', 'contractor': 'contractor', 'subcontractor': 'contractor',
    'responsible': 'responsibleManager', 'manager': 'responsibleManager', 'location': 'area',
}


def _canonical_code_key(type_name: str) -> Optional[str]:
    norm = re.sub(r'[^a-z]+', '', str(type_name).lower())
    for token, canonical in _CANONICAL_CODE_TYPE_MAP.items():
        if token in norm:
            return canonical
    return None


# ── MS Project XML parser ─────────────────────────────────────────────────────

def _msp_duration_to_days(dur_str: str) -> float:
    """Convert ISO-8601 duration (PT8H0M0S / P5DT0H0M0S) to working days (8 h/d)."""
    if not dur_str:
        return 0.0
    day_m = re.search(r'(\d+)D', dur_str)
    hr_m  = re.search(r'T(\d+)H', dur_str)
    mn_m  = re.search(r'H(\d+)M', dur_str)
    days  = int(day_m.group(1)) if day_m else 0
    hours = int(hr_m.group(1))  if hr_m  else 0
    mins  = int(mn_m.group(1))  if mn_m  else 0
    return days + (hours + mins / 60.0) / 8.0


def parse_msp_xml(xml_text: str, file_name: str) -> List[dict]:
    """Parse a Microsoft Project XML export (.xml) into a list of activity dicts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    ns_m = re.match(r'\{([^}]+)\}', root.tag)
    ns   = f'{{{ns_m.group(1)}}}' if ns_m else ''

    def gv(node, tag):
        el = node.find(f'{ns}{tag}')
        return el.text.strip() if (el is not None and el.text) else ''

    proj_name = gv(root, 'Name') or re.sub(r'\.xml$', '', file_name, flags=re.IGNORECASE)
    proj_id   = file_name

    tasks_el = root.find(f'{ns}Tasks')
    if tasks_el is None:
        return []

    wbs_label: Dict[str, str] = {}
    for t in tasks_el.findall(f'{ns}Task'):
        if gv(t, 'Summary') == '1':
            wbs_label[gv(t, 'WBS')] = gv(t, 'Name')

    # Build predecessor / successor relationships from PredecessorLink elements
    # MSP Type codes: 0=FF, 1=FS, 2=SF, 3=SS
    _msp_rel_type = {'0': 'FF', '1': 'FS', '2': 'SF', '3': 'SS'}
    msp_preds_map: Dict[str, list] = {}   # uid -> [{actId, relType, lagDays}]
    msp_succs_map: Dict[str, list] = {}   # uid -> [{actId, relType, lagDays}]
    for t in tasks_el.findall(f'{ns}Task'):
        uid   = gv(t, 'UID')
        links = t.findall(f'{ns}PredecessorLink')
        for link in links:
            pred_uid_el = link.find(f'{ns}PredecessorUID')
            if pred_uid_el is None or not pred_uid_el.text:
                continue
            pred_uid  = pred_uid_el.text.strip()
            rel_code  = (link.find(f'{ns}Type') or type('_', (), {'text': '1'})()).text or '1'
            rel_type  = _msp_rel_type.get(str(rel_code).strip(), 'FS')
            lag_el    = link.find(f'{ns}LagValue')
            lag_days  = round(_msp_duration_to_days(lag_el.text.strip() if lag_el is not None and lag_el.text else ''), 1)
            msp_preds_map.setdefault(uid,      []).append({'actId': f'{proj_id}-{pred_uid}', 'relType': rel_type, 'lagDays': lag_days})
            msp_succs_map.setdefault(pred_uid, []).append({'actId': f'{proj_id}-{uid}',      'relType': rel_type, 'lagDays': lag_days})

    activities: List[dict] = []
    for t in tasks_el.findall(f'{ns}Task'):
        if gv(t, 'UID') == '0' or gv(t, 'Summary') == '1':
            continue

        name = gv(t, 'Name')
        if not name:
            continue

        uid          = gv(t, 'UID')
        task_id      = gv(t, 'ID') or uid
        is_mile      = gv(t, 'Milestone') == '1'
        is_critical  = gv(t, 'Critical')  == '1'
        wbs_code     = gv(t, 'WBS')
        dur          = _msp_duration_to_days(gv(t, 'Duration'))
        total_float  = _msp_duration_to_days(gv(t, 'TotalSlack')) if gv(t, 'TotalSlack') else 0.0
        pct_complete = float(gv(t, 'PercentComplete') or 0)

        act_start  = parse_date(gv(t, 'ActualStart')    or gv(t, 'Start'))
        act_finish = parse_date(gv(t, 'ActualFinish')   or gv(t, 'Finish'))
        b_start    = parse_date(gv(t, 'BaselineStart')  or gv(t, 'Start'))
        b_finish   = parse_date(gv(t, 'BaselineFinish') or gv(t, 'Finish'))

        parent_wbs = '.'.join(wbs_code.split('.')[:-1])
        wbs_name   = wbs_label.get(parent_wbs) or wbs_label.get(wbs_code) or wbs_code or 'General'

        status = ('TK_Complete' if pct_complete >= 100
                  else 'TK_Active'   if act_start
                  else 'TK_NotStart')

        activities.append({
            'id':          f'{proj_id}-{uid}',
            'code':        task_id,
            'name':        name,
            'projectId':   proj_id,
            'projectName': proj_name,
            'sourceFile':  file_name,
            'wbs':         wbs_name,
            'type':        'TT_Mile' if is_mile else 'TT_Task',
            'status':      status,
            'start':       act_start.isoformat()  if act_start                           else None,
            'finish':      act_finish.isoformat() if (act_finish and pct_complete >= 100) else None,
            'bStart':      b_start.isoformat()    if b_start                             else None,
            'bFinish':     b_finish.isoformat()   if b_finish                            else None,
            'dur':         round(dur, 2),
            'remainDur':   round(max(0.0, dur * (1 - pct_complete / 100.0)), 2),
            'totalFloat':  round(total_float, 2),
            'pctComplete': float(min(100.0, max(0.0, pct_complete))),
            'cost':        0.0,
            'isCritical':  bool(is_critical or total_float <= 0),
            'isMilestone': bool(is_mile),
            'predecessors': msp_preds_map.get(uid, []),
            'successors':   msp_succs_map.get(uid, []),
            'predCount':   len(msp_preds_map.get(uid, [])),
            'succCount':   len(msp_succs_map.get(uid, [])),
        })

    return activities


# ── PDF schedule parser ───────────────────────────────────────────────────────

def _rows_to_activities(rows: List[list], headers: Optional[list],
                        file_name: str, proj_name: str) -> List[dict]:
    """Convert table rows (with a header list) into activity dicts."""
    if not rows or headers is None:
        return []

    col: Dict[str, int] = {}
    for i, h in enumerate(headers):
        hn = str(h or '').lower().strip()
        if any(k in hn for k in ('activity name', 'task name', 'name', 'description')) and 'name' not in col:
            col['name']  = i
        elif any(k in hn for k in ('activity id', 'task id', 'activity code', 'code', 'id')) and 'code' not in col:
            col['code']  = i
        elif 'start' in hn and 'name' not in hn:
            col['start'] = i
        elif any(k in hn for k in ('finish', 'end')) and 'name' not in hn:
            col['finish']= i
        elif any(k in hn for k in ('duration', 'orig dur', 'dur')):
            col['dur']   = i
        elif any(k in hn for k in ('total float', 'float', ' tf')):
            col['float'] = i
        elif any(k in hn for k in ('% complete', 'percent', 'pct')):
            col['pct']   = i
        elif 'wbs' in hn:
            col['wbs']   = i

    if 'name' not in col:
        return []

    activities: List[dict] = []
    for idx, row in enumerate(rows):
        if not row:
            continue

        def cell(key: str, default: str = '') -> str:
            ci = col.get(key)
            return str(row[ci] or '').strip() if (ci is not None and ci < len(row)) else default

        name = cell('name')
        if not name or name.lower() in ('none', 'nan', ''):
            continue

        code     = cell('code', f'A{idx + 1:04d}')
        start_d  = parse_date(cell('start'))
        finish_d = parse_date(cell('finish'))
        wbs      = cell('wbs', 'General') or 'General'

        try:
            dur = float(re.sub(r'[^\d.]', '', cell('dur', '0')) or 0)
        except ValueError:
            dur = 0.0
        if dur == 0 and start_d and finish_d:
            dur = max(0.0, float((finish_d - start_d).days))

        try:
            total_float = float(re.sub(r'[^\d.\-]', '', cell('float', '0')) or 0)
        except ValueError:
            total_float = 0.0

        try:
            pct = float(re.sub(r'[^\d.]', '', cell('pct', '0')) or 0)
        except ValueError:
            pct = 0.0

        activities.append({
            'id':          f'{file_name}-{idx}',
            'code':        code,
            'name':        name,
            'projectId':   file_name,
            'projectName': proj_name,
            'sourceFile':  file_name,
            'wbs':         wbs,
            'type':        'TT_Task',
            'status':      ('TK_Complete' if pct >= 100
                            else 'TK_Active' if start_d else 'TK_NotStart'),
            'start':       start_d.isoformat()  if start_d  else None,
            'finish':      finish_d.isoformat() if (finish_d and pct >= 100) else None,
            'bStart':      start_d.isoformat()  if start_d  else None,
            'bFinish':     finish_d.isoformat() if finish_d else None,
            'dur':         round(dur, 2),
            'remainDur':   round(max(0.0, dur * (1 - pct / 100.0)), 2),
            'totalFloat':  round(total_float, 2),
            'pctComplete': float(min(100.0, max(0.0, pct))),
            'cost':        0.0,
            'isCritical':  bool(total_float <= 0),
            'isMilestone': bool(dur == 0),
        })

    return activities


def _pdf_rows_by_position(pdf_file_obj, max_pages: int = 60) -> List[str]:
    """
    Reconstruct table rows from a PDF by grouping LTTextBox elements that share
    the same Y coordinate (within ROW_TOL points), then sorting each group by X.
    This correctly handles P6/Gantt PDFs where each column is a separate text box.
    """
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LAParams, LTTextBox

    # char_margin=1.0 keeps adjacent columns separate (not merged into one box)
    laparams = LAParams(char_margin=1.0, line_margin=0.3, word_margin=0.1,
                        detect_vertical=False)
    ROW_TOL = 4  # points — elements within 4 pt vertically → same row

    pieces: List[tuple] = []   # (global_y, x, text)
    y_offset: float = 0.0

    for page_num, page in enumerate(
            extract_pages(pdf_file_obj, laparams=laparams, maxpages=max_pages)):
        for elem in page:
            if not isinstance(elem, LTTextBox):
                continue
            txt = elem.get_text().replace('\n', ' ').strip()
            if not txt:
                continue
            # global_y increases downward (top of page 1 = 0)
            global_y = y_offset + (page.height - elem.y1)
            pieces.append((global_y, elem.x0, txt))
        y_offset += page.height + 50   # gap between pages

    if not pieces:
        return []

    pieces.sort(key=lambda p: (p[0], p[1]))

    rows: List[str] = []
    cur_y: float    = pieces[0][0]
    cur_row: List[tuple] = []

    for gy, x, txt in pieces:
        if (gy - cur_y) > ROW_TOL:
            cur_row.sort(key=lambda p: p[0])
            line = '  '.join(p[1] for p in cur_row).strip()
            if line:
                rows.append(line)
            cur_row = [(x, txt)]
            cur_y   = gy
        else:
            cur_row.append((x, txt))
            cur_y = max(cur_y, gy)

    if cur_row:
        cur_row.sort(key=lambda p: p[0])
        line = '  '.join(p[1] for p in cur_row).strip()
        if line:
            rows.append(line)

    return rows


def parse_pdf_schedule(pdf_file_obj) -> List[dict]:
    """
    Extract schedule activities from a PDF.

    Strategy (in order):
      0. pdfplumber table extraction — best for structured tables (Excel PDFs,
         P6 tabular reports, MS Project exports).
      1. Position-aware row reconstruction via pdfminer — handles P6 Gantt PDFs
         where each column is a separate text box at a fixed X position.
      2. Simple line-by-line scan of extract_text() output — plain text fallback.

    Processes up to 60 pages.  Returns [] for scanned / image-only PDFs.
    """
    file_name = getattr(pdf_file_obj, 'name', 'schedule.pdf')
    proj_name = (re.sub(r'\.pdf$', '', file_name, flags=re.IGNORECASE)
                 .replace('-', ' ').replace('_', ' '))

    date_pat = re.compile(
        r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}'
        r'|\d{4}-\d{2}-\d{2}'
        r'|\d{1,2}-[A-Za-z]{3}-\d{2,4})\b'
    )
    pct_pat = re.compile(r'\b(\d{1,3}(?:\.\d+)?)%')

    def _make_activity(idx, code, name, start_d, finish_d, bstart_d, bfinish_d,
                       pct, dur, wbs, total_float_raw):
        pct = min(100.0, max(0.0, float(pct or 0)))
        dur = float(dur or 0)
        # Honour explicit totalFloat from PDF; completed → null (P6 convention)
        if pct >= 100:
            total_float = None
        elif total_float_raw is not None:
            total_float = float(total_float_raw)
        else:
            total_float = 0.0  # unknown but not complete → default 0
        return {
            'id':          f'{file_name}-{idx}',
            'code':        code or f'A{idx + 1:04d}',
            'name':        name,
            'projectId':   file_name,
            'projectName': proj_name,
            'sourceFile':  file_name,
            'wbs':         wbs or 'General',
            'type':        'TT_Mile' if dur == 0 else 'TT_Task',
            'status':      ('TK_Complete' if pct >= 100
                            else 'TK_Active' if pct > 0 else 'TK_NotStart'),
            'start':       start_d.isoformat()  if (start_d  and pct > 0)  else None,
            'finish':      finish_d.isoformat() if pct >= 100               else None,
            'bStart':      (bstart_d  or start_d).isoformat()  if (bstart_d  or start_d)  else None,
            'bFinish':     (bfinish_d or finish_d).isoformat() if (bfinish_d or finish_d) else None,
            'dur':         round(dur, 2),
            'remainDur':   round(max(0.0, dur * (1 - pct / 100.0)), 2),
            'totalFloat':  total_float,
            'pctComplete': pct,
            'cost':        0.0,
            'isCritical':  total_float is not None and total_float <= 0,
            'isMilestone': dur == 0,
            'predCount':   0,
            'succCount':   0,
        }

    # ── Pass 0: pdfplumber table extraction ───────────────────────────────────
    try:
        import pdfplumber

        if hasattr(pdf_file_obj, 'seek'):
            pdf_file_obj.seek(0)
        pdf_bytes = pdf_file_obj.read()
        if hasattr(pdf_file_obj, 'seek'):
            pdf_file_obj.seek(0)

        # Column header synonyms → canonical key
        _HDR = [
            ('code',        re.compile(r'act.*id|activity.*id|task.*id|^id$|^code$', re.I)),
            ('name',        re.compile(r'activity.*name|task.*name|^name$|description', re.I)),
            ('start',       re.compile(r'^(actual\s+)?start$|early\s+start', re.I)),
            ('finish',      re.compile(r'^(actual\s+)?finish$|^(actual\s+)?end$|early\s+finish|completion', re.I)),
            ('bStart',      re.compile(r'baseline.*start|bl[\s._-]*start|planned.*start|orig.*start', re.I)),
            ('bFinish',     re.compile(r'baseline.*finish|bl[\s._-]*finish|planned.*finish|orig.*finish', re.I)),
            ('totalFloat',  re.compile(r'total\s*float|^float$|^tf$', re.I)),
            ('pct',         re.compile(r'%\s*comp|percent.*comp|complete|^pct$|^done$', re.I)),
            ('dur',         re.compile(r'orig.*dur|duration|^dur$', re.I)),
            ('wbs',         re.compile(r'^wbs|wbs.*name|work.*package', re.I)),
        ]

        def _map_headers(header_row):
            col_map: Dict[str, int] = {}
            for i, cell in enumerate(header_row):
                h = str(cell).strip() if cell else ''
                for key, pat in _HDR:
                    if key not in col_map and pat.search(h):
                        col_map[key] = i
                        break
            return col_map

        def _cell(row, idx):
            if idx is None or idx >= len(row):
                return None
            v = row[idx]
            return str(v).strip() if v else None

        def _num(s):
            if not s:
                return None
            m = re.search(r'-?\d+(?:\.\d+)?', s)
            return float(m.group()) if m else None

        all_acts: List[dict] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:60]:
                for table in (page.extract_tables() or []):
                    if not table or len(table) < 2:
                        continue
                    # Find the header row (first row containing a name-like header)
                    hdr_idx = None
                    col_map: Dict[str, int] = {}
                    for ri, row in enumerate(table[:5]):
                        if not row:
                            continue
                        cm = _map_headers(row)
                        if 'name' in cm or 'code' in cm:
                            hdr_idx = ri
                            col_map = cm
                            break
                    if hdr_idx is None or 'name' not in col_map:
                        continue
                    for row in table[hdr_idx + 1:]:
                        if not row or all(not c for c in row):
                            continue
                        name = _cell(row, col_map.get('name'))
                        if not name or len(name) < 3:
                            continue
                        start_d  = parse_date(_cell(row, col_map.get('start')))
                        finish_d = parse_date(_cell(row, col_map.get('finish')))
                        bstart_d = parse_date(_cell(row, col_map.get('bStart')))
                        bfinish_d= parse_date(_cell(row, col_map.get('bFinish')))
                        pct_raw  = _cell(row, col_map.get('pct'))
                        pct      = _num(pct_raw) or 0.0
                        tf_raw   = _num(_cell(row, col_map.get('totalFloat')))
                        dur_raw  = _num(_cell(row, col_map.get('dur')))
                        dur      = dur_raw if dur_raw is not None else (
                            float((finish_d - start_d).days) if (start_d and finish_d) else 0.0
                        )
                        all_acts.append(_make_activity(
                            len(all_acts),
                            _cell(row, col_map.get('code')),
                            name, start_d, finish_d, bstart_d, bfinish_d,
                            pct, dur, _cell(row, col_map.get('wbs')), tf_raw,
                        ))
        if all_acts:
            return all_acts
    except Exception:
        pass

    # ── Pass 1: pdfminer position-aware row reconstruction (P6 Gantt PDFs) ───
    def _activities_from_rows(rows: List[str]) -> List[dict]:
        acts: List[dict] = []
        skip = re.compile(
            r'^(page\s+\d|report\s+date|activity\s+id|act\s+id|'
            r'id\s+name|resource\s+name|primavera|oracle|\d+\s*$)',
            re.I,
        )
        for line in rows:
            line = line.strip()
            if not line or len(line) < 8 or skip.match(line):
                continue
            dates_found = date_pat.findall(line)
            if len(dates_found) < 2:
                continue
            cleaned = date_pat.sub('', line)
            cleaned = re.sub(r'\b\d+(?:\.\d+)?%?\s*(?:d|days?)?\b', '', cleaned, flags=re.I)
            tokens  = [t for t in cleaned.split()
                       if len(t) > 1 and re.search(r'[A-Za-z]', t)]
            name = ' '.join(tokens[:15]).strip()
            if not name or len(name) < 3:
                continue
            parsed   = [parse_date(d) for d in dates_found if parse_date(d)]
            start_d  = parsed[0] if len(parsed) > 0 else None
            finish_d = parsed[1] if len(parsed) > 1 else None
            if not start_d and not finish_d:
                continue
            pct_m = pct_pat.search(line)
            pct   = min(100.0, float(pct_m.group(1))) if pct_m else 0.0
            dur   = float((finish_d - start_d).days) if (start_d and finish_d) else 0.0
            acts.append(_make_activity(
                len(acts), None, name, start_d, finish_d, None, None,
                pct, dur, None, None,
            ))
        return acts

    try:
        rows = _pdf_rows_by_position(pdf_file_obj, max_pages=60)
        acts = _activities_from_rows(rows)
        if acts:
            return acts
    except Exception:
        pass

    # ── Pass 2: simple line-by-line fallback ─────────────────────────────────
    try:
        if hasattr(pdf_file_obj, 'seek'):
            pdf_file_obj.seek(0)
        from pdfminer.high_level import extract_text
        text = extract_text(pdf_file_obj, maxpages=60)
        if text and len(text.strip()) > 50:
            acts = _activities_from_rows(text.splitlines())
            if acts:
                return acts
    except Exception:
        pass

    return []


# ── PDF narrative/document text extraction ────────────────────────────────────

def extract_pdf_text(pdf_file_obj, max_pages: int = 200) -> Dict[str, Any]:
    """
    Extract plain text from a PDF for narrative-document storage (schedule
    narratives, lookaheads, owner/contractor reports) — NOT for structured
    activity parsing (that's parse_pdf_schedule).

    Uses pdfminer's embedded-text extraction only, matching the rest of
    ScheduleIQ's PDF handling — no OCR. A scanned/image-only PDF will
    legitimately extract little or no text; that's reported via status/
    warnings rather than silently returning an empty success.
    """
    warnings: List[str] = []
    try:
        if hasattr(pdf_file_obj, 'seek'):
            pdf_file_obj.seek(0)
        from pdfminer.high_level import extract_text
        from pdfminer.pdfpage import PDFPage

        text = extract_text(pdf_file_obj, maxpages=max_pages) or ''

        page_count = None
        try:
            if hasattr(pdf_file_obj, 'seek'):
                pdf_file_obj.seek(0)
            page_count = sum(1 for _ in PDFPage.get_pages(pdf_file_obj, maxpages=max_pages))
        except Exception:
            pass

        stripped = text.strip()
        if not stripped:
            warnings.append(
                'No embedded text could be extracted — this PDF may be scanned/image-only. '
                'OCR is not enabled.'
            )
            status = 'FAILED'
        elif len(stripped) < 200:
            warnings.append('Very little text was extracted — extraction may be incomplete.')
            status = 'PARTIAL'
        else:
            status = 'SUCCESS'

        return {'text': text, 'page_count': page_count, 'status': status, 'warnings': warnings}
    except Exception as exc:
        return {
            'text': '', 'page_count': None, 'status': 'FAILED',
            'warnings': [f'PDF text extraction failed: {exc}'],
        }


# ── Excel / CSV parser ────────────────────────────────────────────────────────

def excel_to_activities(df: pd.DataFrame, file_name: str) -> List[dict]:
    if df is None or df.empty:
        return []

    # Normalise column names for fuzzy matching
    col_lower = {str(c).lower().strip(): c for c in df.columns}

    def find_col(*keys) -> Optional[str]:
        for k in keys:
            for norm, orig in col_lower.items():
                if k in norm:
                    return orig
        return None

    col_name   = find_col('activity name', 'name', 'task name', 'description')
    col_id     = find_col('activity id', 'id', 'task code', 'code')
    col_start  = find_col('actual start', 'early start', 'start')
    col_finish = find_col('actual finish', 'early finish', 'finish', 'end')
    col_dur    = find_col('original duration', 'orig dur', 'duration')
    col_remain = find_col('remaining duration', 'remain dur')
    col_float  = find_col('total float', 'float', 'tf')
    col_pct    = find_col('% complete', 'percent complete', 'pct')
    col_status = find_col('status')
    col_proj   = find_col('project', 'proj name')
    col_wbs    = find_col('wbs', 'work breakdown')

    if col_name is None:
        return []

    proj_name = re.sub(r'\.(xlsx?|csv)$', '', file_name, flags=re.IGNORECASE)
    proj_name = proj_name.replace('-', ' ').replace('_', ' ')

    def cell(row, col, default=''):
        if col is None:
            return default
        val = row.get(col, default)
        if pd.isna(val) if isinstance(val, float) else False:
            return default
        return val

    activities = []
    for idx, row in df.iterrows():
        name_val = cell(row, col_name)
        if not str(name_val).strip():
            continue

        row_id = str(cell(row, col_id, idx))
        row_proj = str(cell(row, col_proj, proj_name))
        tf = float(cell(row, col_float, 0) or 0)

        activities.append({
            'id': row_id,
            'code': row_id if col_id else f'A{idx}',
            'name': str(name_val),
            'projectId': row_proj,
            'projectName': row_proj,
            'sourceFile': file_name,
            'wbs': str(cell(row, col_wbs, '')),
            'type': 'TT_Task',
            'status': str(cell(row, col_status, '')),
            'start':   parse_date(cell(row, col_start)),
            'finish':  parse_date(cell(row, col_finish)),
            'bStart':  parse_date(cell(row, col_start)),
            'bFinish': parse_date(cell(row, col_finish)),
            'dur':        float(cell(row, col_dur, 0) or 0),
            'remainDur':  float(cell(row, col_remain, 0) or 0),
            'totalFloat': tf,
            'pctComplete': float(cell(row, col_pct, 0) or 0),
            'cost': 0.0,
            'isCritical': tf <= 0,
            'isMilestone': False,
        })

    return activities
