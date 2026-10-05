"""
Baseline Detection and Intelligence Enhancement — detects ADDITIONAL XER
PROJECT RECORDS structurally present within a single uploaded XER file
(multiple `%T PROJECT` rows), entirely separate from ScheduleIQ's own
baseline DESIGNATION (ScheduleUpload.schedule_classification, a human
decision — see version_chronology.assign_roles, completely unmodified by
this module; nothing here is read by baseline_progress.py's variance
calculation, EVM, S-curves, or any dashboard KPI).

TERMINOLOGY — read this before touching a label anywhere this data flows:
An additional PROJECT row is commonly produced by Primavera P6's "Export
with Baselines" option, so it is OFTEN a baseline snapshot in practice —
but standard XER format does not carry a documented, version-stable field
that lets this module VERIFY that link from file content alone. This
module therefore never calls an additional record a "baseline" outright.
It reports:
  - "additional XER project record(s)" — the neutral, structural fact
    (a second/third/... PROJECT row exists in the file), always accurate.
  - "potential baseline reference(s)" — the SAME records when explaining
    WHY they're worth surfacing to a scheduler, used only in prose/captions,
    never as a bare count standing in for "confirmed baseline."
Every API/JSON key and every UI label built on this module's output must
follow the same rule — see BaselineInformationPanel in
frontend/BaselineProgress.tsx for the UI-side half of this contract.
Nothing here ever becomes a ScheduleUpload's schedule_classification
automatically, and no formal baseline-approval workflow is implied.

Every field value is read defensively (`.get(...)`, never a default that
looks like real data); anything the file doesn't supply comes back as
None, which the frontend renders as "Not Available" — never a guessed
value.

Pure functions, no DB dependency — same pattern as schedule_identity.py /
update_intelligence.py / baseline_progress.py.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def detect_additional_xer_project_records(
    sections: Dict[str, List[dict]],
    primary_proj_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    `sections`: the raw parse_xer() output for the WHOLE file (every table,
    not just PROJECT) — TASK rows are cross-referenced per project id to
    tell a bare record (a PROJECT row with no accompanying schedule data
    in this file) apart from one with a complete schedule attached (PROJECT
    row + its own TASK rows, all present in this same file).

    `primary_proj_id`: the proj_id of the project actually being imported.
    Defaults to the first PROJECT row's proj_id — the same "first row is
    the project" convention parse_project_meta_from_xer already uses, so
    this never changes which row ScheduleIQ treats as the import target.
    Every OTHER proj_id found in the PROJECT table is reported as an
    additional project record.

    Returns (all keys always present; lists/None instead of omitted keys,
    so a caller never needs a `.get(..., default)` dance):
      projectRecordCount             int   — total %T PROJECT rows in the file.
      primaryProjectId               str|None
      additionalProjectRecordCount   int   — projectRecordCount minus the
                                              primary row (and minus any row
                                              with no proj_id at all). These
                                              are POTENTIAL baseline
                                              references, never confirmed
                                              ones — see module docstring.
      additionalProjectRecords       list  — one entry per OTHER proj_id found:
        projectId                str
        projectName              str|None  — proj_short_name if present.
        dataDate                 str|None  — last_recalc_date, RAW string as the
                                              file wrote it (not parsed/validated
                                              here — this module never guesses a
                                              date format; the caller's own
                                              detection utilities own that).
        assignmentType            None     — ALWAYS None: standard XER's PROJECT
                                              table does not carry a documented
                                              "this is baseline slot N" field this
                                              module can verify — reported as
                                              unavailable, never invented.
        hasCompleteScheduleData   bool     — True iff sections['TASK'] has at
                                              least one row for this proj_id.
        activityCount             int      — count of such TASK rows (0 when
                                              hasCompleteScheduleData is False).
    """
    project_rows = sections.get('PROJECT', []) or []
    if not project_rows:
        return {
            'projectRecordCount': 0,
            'primaryProjectId': None,
            'additionalProjectRecordCount': 0,
            'additionalProjectRecords': [],
        }

    primary_id = primary_proj_id or (project_rows[0].get('proj_id') or None)
    task_rows = sections.get('TASK', []) or []

    task_count_by_proj: Dict[str, int] = {}
    for t in task_rows:
        pid = t.get('proj_id')
        if pid:
            task_count_by_proj[pid] = task_count_by_proj.get(pid, 0) + 1

    seen_ids = set()
    additional_records: List[Dict[str, Any]] = []
    for row in project_rows:
        pid = row.get('proj_id') or None
        if pid is None or pid == primary_id or pid in seen_ids:
            continue
        seen_ids.add(pid)
        activity_count = task_count_by_proj.get(pid, 0)
        additional_records.append({
            'projectId': pid,
            'projectName': row.get('proj_short_name') or None,
            'dataDate': row.get('last_recalc_date') or None,
            'assignmentType': None,
            'hasCompleteScheduleData': activity_count > 0,
            'activityCount': activity_count,
        })

    return {
        'projectRecordCount': len(project_rows),
        'primaryProjectId': primary_id,
        'additionalProjectRecordCount': len(additional_records),
        'additionalProjectRecords': additional_records,
    }
