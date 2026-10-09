"""
Schedule Import Column Mapping — ScheduleIQ Phase 1

Recognises the full set of common P6/MSP/Excel scheduling column names
(including near-variants) so the Import Center can show the user exactly
what was detected, what was mapped, and what was NOT recognised — instead
of silently discarding columns the way a naive importer would.

Pure functions only — no Django/DB dependency, so this is unit-testable
in isolation like the other engines in this package.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import pandas as pd


def normalize_key(value: Any) -> str:
    if value is None:
        return ''
    return re.sub(r'[^a-z0-9]+', '_', str(value).strip().lower()).strip('_')


# ── Canonical schedule fields → recognised synonyms (already-normalized) ──────
# Order matters within a field's list only for documentation; matching uses a
# "does the normalized header contain this token" test, tried longest-first so
# e.g. "actual_start" is preferred over the bare "start" for a column literally
# named "Actual Start".

FIELD_SYNONYMS: Dict[str, List[str]] = {
    'activity_id':            ['activity_id', 'activity_code', 'task_id', 'task_code', 'act_id', 'code', 'id', 'unique_id'],
    'activity_name':          ['activity_name', 'task_name', 'activity', 'description', 'name'],
    'wbs':                    ['wbs_code', 'wbs_name', 'wbs_path', 'work_breakdown_structure', 'wbs'],
    'project_id':             ['project_id', 'proj_id', 'project_code'],
    'project_name':           ['project_name', 'proj_name', 'project', 'portfolio'],
    'original_duration':      ['original_duration', 'orig_duration', 'orig_dur', 'planned_duration', 'duration'],
    'remaining_duration':     ['remaining_duration', 'remain_duration', 'remain_dur', 'rem_dur'],
    'actual_duration':        ['actual_duration', 'act_dur'],
    'start':                  ['early_start', 'start_date', 'start'],
    'finish':                 ['early_finish', 'finish_date', 'end_date', 'finish', 'end'],
    'actual_start':           ['actual_start', 'act_start'],
    'actual_finish':          ['actual_finish', 'act_finish', 'act_end'],
    'remaining_start':        ['remaining_start', 'remain_start'],
    'remaining_finish':       ['remaining_finish', 'remain_finish'],
    'baseline_start':         ['baseline_start', 'bl_start', 'bl1_start', 'target_start', 'planned_start'],
    'baseline_finish':        ['baseline_finish', 'bl_finish', 'bl1_finish', 'target_finish', 'planned_finish'],
    'bl1_start':               ['bl1_start', 'baseline1_start'],
    'bl1_finish':              ['bl1_finish', 'baseline1_finish'],
    'total_float':            ['total_float', 'tf'],
    'free_float':             ['free_float', 'ff'],
    'percent_complete':       ['percent_complete', 'pct_complete', 'percent_done', 'complete'],
    'physical_percent_complete': ['physical_percent_complete', 'phys_pct_complete', 'physical_pct'],
    'duration_percent_complete': ['duration_percent_complete', 'duration_pct_complete', 'dur_pct_complete'],
    'activity_status':        ['activity_status', 'task_status', 'status_id', 'status'],
    'activity_type':          ['activity_type', 'task_type'],
    'calendar':               ['calendar_name', 'calendar_id', 'calendar'],
    'constraint_type':        ['constraint_type', 'primary_constraint_type', 'cstr_type'],
    'constraint_date':        ['constraint_date', 'primary_constraint_date', 'cstr_date'],
    'primary_constraint':     ['primary_constraint'],
    'secondary_constraint':   ['secondary_constraint', 'constraint_type_2', 'cstr_type2'],
    'predecessors':           ['predecessors', 'predecessor'],
    'successors':             ['successors', 'successor'],
    'relationship_type':      ['relationship_type', 'rel_type'],
    'lag':                    ['lag_days', 'lag'],
    'responsible_manager':    ['responsible_manager', 'responsible_person', 'activity_manager', 'resp_manager', 'manager'],
    'contractor':              ['contractor', 'subcontractor'],
    'discipline':              ['discipline'],
    'area':                    ['area'],
    'system':                  ['system'],
    'phase':                   ['phase'],
    'resource':                ['resource_name', 'resource'],
    'cost':                    ['budgeted_cost', 'total_cost', 'cost'],
}

# Fields the import cannot usefully proceed without.
REQUIRED_FIELDS = ['activity_id', 'activity_name']

# Fields strongly recommended for schedule analytics (missing → warning, not error).
RECOMMENDED_FIELDS = ['start', 'finish', 'original_duration', 'total_float', 'percent_complete']

DATE_FIELD_KEYS = {
    'start', 'finish', 'actual_start', 'actual_finish', 'remaining_start',
    'remaining_finish', 'baseline_start', 'baseline_finish', 'bl1_start',
    'bl1_finish', 'constraint_date',
}


def detect_columns(columns: List[str], field_synonyms: Optional[Dict[str, List[str]]] = None) -> Dict[str, str]:
    """
    Map canonical field -> source column name, choosing the best (longest
    synonym match) source column per field. Each source column is used for
    at most one field.

    `field_synonyms` defaults to this module's own schedule-activity
    vocabulary (FIELD_SYNONYMS) — passing a different table (e.g. the
    Weekly Field Report importer's headcount/forecast vocabulary in
    weekly_field_report_mapping.py) reuses this exact same matching
    algorithm for an entirely different field set, rather than
    duplicating it.
    """
    field_synonyms = field_synonyms if field_synonyms is not None else FIELD_SYNONYMS
    norm_cols = [(c, normalize_key(c)) for c in columns]
    mapped: Dict[str, str] = {}
    used_columns: set = set()

    # Build (field, synonym_len, synonym, source_col) candidates, longest synonyms first
    # so e.g. "baseline_start" beats "start" for a column named "Baseline Start".
    candidates = []
    for field, synonyms in field_synonyms.items():
        for syn in synonyms:
            for orig, norm in norm_cols:
                if orig in used_columns:
                    continue
                if norm == syn or (f'_{syn}' in f'_{norm}_') or norm.startswith(syn) or norm.endswith(syn):
                    candidates.append((len(syn), field, syn, orig))

    candidates.sort(key=lambda c: -c[0])
    for _len, field, _syn, orig in candidates:
        if field in mapped or orig in used_columns:
            continue
        mapped[field] = orig
        used_columns.add(orig)

    return mapped


def _parse_date_cell(value) -> Optional[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        parsed = pd.to_datetime(value, errors='coerce')
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def build_excel_preview(df: 'pd.DataFrame', file_name: str) -> Dict[str, Any]:
    """
    Analyse an Excel/CSV DataFrame and return a full import-preview payload:
    detected columns, field mapping, unmapped columns, invalid dates,
    duplicate activity IDs, missing required fields, and warnings.

    Never discards data — unmapped columns are reported, not dropped.
    """
    columns = [str(c) for c in df.columns]
    mapped = detect_columns(columns)
    mapped_source_cols = set(mapped.values())
    unmapped_columns = [c for c in columns if c not in mapped_source_cols]

    warnings: List[str] = []
    missing_required = [f for f in REQUIRED_FIELDS if f not in mapped]
    missing_recommended = [f for f in RECOMMENDED_FIELDS if f not in mapped]

    for f in missing_required:
        warnings.append(f'Required field "{f.replace("_", " ").title()}" was not found in any column.')
    for f in missing_recommended:
        warnings.append(f'Recommended field "{f.replace("_", " ").title()}" was not found — related analytics will be limited.')

    row_count = int(len(df))

    # Duplicate activity IDs
    duplicate_ids: List[str] = []
    if 'activity_id' in mapped:
        id_col = mapped['activity_id']
        counts = df[id_col].astype(str).value_counts()
        duplicate_ids = [v for v, n in counts.items() if n > 1 and v and v.lower() != 'nan'][:50]
        if duplicate_ids:
            warnings.append(f'{len(duplicate_ids)} duplicate Activity ID value(s) found — later rows will overwrite earlier ones unless corrected.')

    # Invalid dates
    invalid_date_rows: List[Dict[str, Any]] = []
    for field in DATE_FIELD_KEYS:
        col = mapped.get(field)
        if not col:
            continue
        for idx, raw in df[col].items():
            if raw is None or (isinstance(raw, float) and pd.isna(raw)) or str(raw).strip() == '':
                continue
            if _parse_date_cell(raw) is None:
                invalid_date_rows.append({'row': int(idx) + 2, 'field': field, 'column': col, 'value': str(raw)})
    if invalid_date_rows:
        warnings.append(f'{len(invalid_date_rows)} date value(s) could not be parsed and will be treated as blank.')

    # Sample rows (first 15), showing both raw and mapped view
    sample_rows: List[Dict[str, Any]] = []
    for idx, row in df.head(15).iterrows():
        mapped_row = {}
        for field, col in mapped.items():
            val = row.get(col)
            if isinstance(val, float) and pd.isna(val):
                val = None
            elif field in DATE_FIELD_KEYS:
                val = _parse_date_cell(val)
            else:
                val = val if val is None else str(val)
            mapped_row[field] = val
        sample_rows.append({'row': int(idx) + 2, 'mapped': mapped_row})

    return {
        'fileName': file_name,
        'rowCount': row_count,
        'detectedColumns': columns,
        'mappedFields': mapped,
        'unmappedColumns': unmapped_columns,
        'missingRequiredFields': missing_required,
        'missingRecommendedFields': missing_recommended,
        'duplicateActivityIds': duplicate_ids,
        'invalidDateCount': len(invalid_date_rows),
        'invalidDateSamples': invalid_date_rows[:25],
        'warnings': warnings,
        'sampleRows': sample_rows,
        'canImport': len(missing_required) == 0,
    }


# Extra descriptive fields (beyond the CPM/EVM core) that must be carried
# through into the committed activity record verbatim — detected by the same
# `mapped` dict the preview shows, so preview and commit never disagree about
# what was found. Value = (canonical field key, output activity key, is_date).
_EXTRA_FIELD_OUTPUT = [
    ('responsible_manager',       'responsibleManager',   False),
    ('contractor',                'contractor',            False),
    ('discipline',                'discipline',            False),
    ('area',                      'area',                  False),
    ('system',                    'system',                False),
    ('phase',                     'phase',                 False),
    ('resource',                  'resource',              False),
    ('calendar',                  'calendar',               False),
    ('constraint_type',           'constraintType',        False),
    ('constraint_date',           'constraintDate',        True),
    ('primary_constraint',        'primaryConstraint',     False),
    ('secondary_constraint',      'secondaryConstraint',   False),
    ('physical_percent_complete', 'physPctComplete',       False),
    ('duration_percent_complete', 'durationPctComplete',   False),
    ('actual_duration',           'actualDuration',        False),
    ('actual_start',              'actualStart',           True),
    ('actual_finish',             'actualFinish',          True),
    ('remaining_start',           'remainStart',           True),
    ('remaining_finish',          'remainFinish',           True),
    ('bl1_start',                 'bl1Start',                True),
    ('bl1_finish',                'bl1Finish',               True),
    ('free_float',                'freeFloat',              False),
    ('predecessors',              'predecessorsRaw',        False),
    ('successors',                'successorsRaw',          False),
    ('relationship_type',         'relationshipTypeRaw',    False),
    ('lag',                       'lagRaw',                  False),
]


def build_activities_from_mapping(df: 'pd.DataFrame', mapped: Dict[str, str], file_name: str) -> List[dict]:
    """
    Build activity dicts from a DataFrame using the exact same field mapping
    build_excel_preview() showed the user — so what the user reviewed is what
    gets imported, and every recognised field (including ones normalize_activities()
    doesn't know about, like Contractor/Discipline/Area/System/Phase) is preserved
    rather than silently dropped.

    Core fields mirror utils.normalize_activities() so downstream engines
    (metrics/quality/status) see the same shape regardless of import path.
    """
    if df is None or df.empty:
        return []

    def cell(row, field):
        col = mapped.get(field)
        if col is None:
            return None
        val = row.get(col)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        if isinstance(val, str) and not val.strip():
            return None
        return val

    def cell_date(row, field):
        v = cell(row, field)
        return None if v is None else pd.to_datetime(v, errors='coerce')

    activities: List[dict] = []
    for index, row in df.iterrows():
        name = cell(row, 'activity_name')
        if name is None or not str(name).strip():
            continue

        code = cell(row, 'activity_id')
        code = str(code) if code is not None else f'A{index + 1:04d}'
        project_name = cell(row, 'project_name') or cell(row, 'project_id') or file_name
        project_id = normalize_key(project_name) or f'proj-{index + 1:03d}'
        wbs = cell(row, 'wbs') or 'General'

        start = cell_date(row, 'baseline_start') or cell_date(row, 'start') or cell_date(row, 'actual_start')
        finish = cell_date(row, 'baseline_finish') or cell_date(row, 'finish') or cell_date(row, 'actual_finish')
        bstart = cell_date(row, 'baseline_start') or cell_date(row, 'bl1_start')
        bfinish = cell_date(row, 'baseline_finish') or cell_date(row, 'bl1_finish')

        def _num(field):
            v = cell(row, field)
            try:
                return float(v) if v is not None and str(v).strip() != '' else None
            except (TypeError, ValueError):
                return None

        dur = _num('original_duration')
        pct_complete = _num('percent_complete')
        total_float = _num('total_float')
        remain = _num('remaining_duration')
        cost = _num('cost') or 0.0
        type_value = cell(row, 'activity_type') or 'Task'
        status = cell(row, 'activity_status') or 'TK_Active'

        start_d = start.date() if start is not None and not pd.isna(start) else None
        finish_d = finish.date() if finish is not None and not pd.isna(finish) else None
        bstart_d = bstart.date() if bstart is not None and not pd.isna(bstart) else None
        bfinish_d = bfinish.date() if bfinish is not None and not pd.isna(bfinish) else None

        if dur is None and bstart_d and bfinish_d:
            dur = max(0.0, float((bfinish_d - bstart_d).days))
        if dur is None and start_d and finish_d:
            dur = max(0.0, float((finish_d - start_d).days))
        if pct_complete is None:
            pct_complete = 100.0 if str(status).lower().startswith('tk_complete') else 0.0
        if total_float is None:
            total_float = -1.0 if pct_complete < 100 and (start_d or finish_d) else 0.0
        if remain is None:
            remain = max(0.0, dur * (1 - pct_complete / 100.0)) if dur is not None else 0.0
        if start_d and not bstart_d:
            bstart_d = start_d
        if finish_d and not bfinish_d:
            bfinish_d = finish_d

        is_milestone = bool(dur == 0 or 'mile' in str(type_value).lower())
        if pct_complete >= 100:
            status = 'TK_Complete'

        activity = {
            'id': f'{project_id}-{code}',
            'code': str(code),
            'name': str(name),
            'projectId': str(project_id),
            'projectName': str(project_name),
            'sourceFile': str(file_name),
            'wbs': str(wbs),
            'type': str(type_value),
            'status': str(status),
            'start': start_d.isoformat() if start_d else None,
            'finish': finish_d.isoformat() if finish_d else None,
            'bStart': bstart_d.isoformat() if bstart_d else None,
            'bFinish': bfinish_d.isoformat() if bfinish_d else None,
            'dur': float(dur or 0.0),
            'remainDur': float(remain or 0.0),
            'totalFloat': float(total_float or 0.0),
            'pctComplete': float(min(max(pct_complete or 0.0, 0.0), 100.0)),
            'cost': float(cost or 0.0),
            'isCritical': bool(total_float is not None and total_float <= 0 and not is_milestone),
            'isMilestone': bool(is_milestone),
        }

        for field_key, out_key, is_date in _EXTRA_FIELD_OUTPUT:
            val = cell(row, field_key)
            if val is None:
                continue
            if is_date:
                parsed = _parse_date_cell(val)
                if parsed:
                    activity[out_key] = parsed
            else:
                activity[out_key] = str(val)

        activities.append(activity)

    return activities


def build_activities_preview(activities: List[dict], file_name: str, file_type: str) -> Dict[str, Any]:
    """
    Preview for already-structured parses (XER / MSP XML / PDF) where column
    mapping doesn't apply — surfaces the same class of diagnostics (duplicates,
    invalid/missing dates, warnings) so the Import Center UX is consistent
    across every file type.
    """
    warnings: List[str] = []
    row_count = len(activities)

    ids = [a.get('code') or a.get('id') or '' for a in activities]
    seen: Dict[str, int] = {}
    for i in ids:
        if not i:
            continue
        seen[i] = seen.get(i, 0) + 1
    duplicate_ids = [k for k, v in seen.items() if v > 1][:50]
    if duplicate_ids:
        warnings.append(f'{len(duplicate_ids)} duplicate Activity ID value(s) found in the parsed file.')

    missing_dates = sum(
        1 for a in activities
        if not a.get('isMilestone') and not (a.get('start') or a.get('bStart'))
        and not (a.get('finish') or a.get('bFinish'))
    )
    if missing_dates:
        warnings.append(f'{missing_dates} activity(ies) have no start or finish date of any kind.')

    no_name = sum(1 for a in activities if not (a.get('name') or '').strip())
    if no_name:
        warnings.append(f'{no_name} activity(ies) are missing a name.')

    return {
        'fileName': file_name,
        'fileType': file_type,
        'rowCount': row_count,
        'duplicateActivityIds': duplicate_ids,
        'invalidDateCount': missing_dates,
        'warnings': warnings,
        'sampleRows': [
            {
                'row': i + 1,
                'mapped': {
                    'activity_id': a.get('code') or a.get('id'),
                    'activity_name': a.get('name'),
                    'wbs': a.get('wbs'),
                    'start': a.get('start'),
                    'finish': a.get('finish'),
                    'baseline_start': a.get('bStart'),
                    'baseline_finish': a.get('bFinish'),
                    'total_float': a.get('totalFloat'),
                    'percent_complete': a.get('pctComplete'),
                },
            }
            for i, a in enumerate(activities[:15])
        ],
        'canImport': row_count > 0,
    }
