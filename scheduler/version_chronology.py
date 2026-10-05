"""
Schedule version chronology - the ONE place that decides which version is
newer than which, and therefore CURRENT / PREVIOUS.

Governing rule (applies to every project):

    Effective Data Date determines schedule chronology.
    Upload timestamp records when ScheduleIQ received the file.

They are not interchangeable: a June schedule imported in September must
never become CURRENT merely because it was uploaded later.

  - Versions are ordered newest-first by effective Data Date
    (ScheduleUpload.data_date - already the override-aware effective date).
  - upload_timestamp is used ONLY as a deterministic tiebreak between
    versions whose ordering date is equal, and as the ordering date of a
    legacy version that has no Data Date at all (never presented as its
    Data Date, and never treated as a Data-Date "conflict").
  - Two versions with the SAME real Data Date but DIFFERENT parsed content
    are an explicit conflict - chronology is not silently taken from upload
    order. Exact content copies are not a conflict (either one is the
    same schedule).
  - When the version immediately before CURRENT is such a conflict,
    PREVIOUS is reported as UNRESOLVED (previous=None + candidates) rather
    than quietly falling back to the next-older update.
  - Explicit user-designated roles still work: the baseline is still chosen
    from the APPROVED/REVISED_BASELINE classification, and explicit
    currentVersion/previousVersion query parameters bypass all of this.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

# Parser-added provenance fields that differ between two imports of the very
# same source file (see project_consolidation) - never real content differences.
# sourceFile in particular is stamped onto every activity from the UPLOADED
# FILENAME alone (xer_to_activities(sections, upload_file.name)) - two
# genuinely identical exports saved/re-uploaded under different filenames
# (extremely common in practice - e.g. a timestamp in the export name) must
# still fingerprint identically, or exact-duplicate-import detection
# (import_commit, Phase 2: Import Protection) could never catch them.
PARSER_PROVENANCE_KEYS = frozenset({'origDurSource', 'sourceFile'})


def content_fingerprint(activities: List[dict]) -> str:
    cleaned = [{k: v for k, v in a.items() if k not in PARSER_PROVENANCE_KEYS} for a in (activities or [])]
    return hashlib.sha256(json.dumps(cleaned, sort_keys=True, default=str, separators=(',', ':')).encode('utf-8')).hexdigest()


def real_data_date(v) -> Optional[date]:
    return v.data_date


def order_date(v) -> date:
    """Ordering date only. A version with no Data Date is ordered by the date
    it was received (legacy behaviour) - that is NOT its Data Date."""
    return v.data_date or v.upload_timestamp.date()


def sort_key(v):
    return (order_date(v), v.upload_timestamp, str(v.id))


def sort_newest_first(versions) -> list:
    return sorted(versions, key=sort_key, reverse=True)


def chronological_versions(project) -> list:
    """Every NON-DELETED version of `project`, newest first by effective
    Data Date. This is the single choke-point CURRENT/PREVIOUS/BASELINE
    resolution, dashboards and comparisons already go through (see
    _assign_version_roles's callers in views.py) — excluding soft-deleted
    versions (Phase 3/4: Import Protection and Schedule Deletion Auditing)
    here means every one of those call sites correctly stops seeing a
    deleted version without needing its own exclusion logic. Use
    project.schedule_versions.filter(is_deleted=True) directly (e.g. the
    Deleted Versions view) when a deleted version is what you actually want."""
    return sort_newest_first(project.schedule_versions.filter(is_deleted=False))


def _distinct_contents(versions) -> int:
    return len({content_fingerprint(v.activities_json) for v in versions})


def data_date_conflicts(versions) -> Dict[date, list]:
    """{data date: versions} for every REAL Data Date shared by versions with
    different parsed content. Exact copies are not conflicts."""
    by_dd: Dict[date, list] = defaultdict(list)
    for v in versions:
        if v.data_date:
            by_dd[v.data_date].append(v)
    return {dd: vs for dd, vs in by_dd.items() if len(vs) > 1 and _distinct_contents(vs) > 1}


def _candidate_info(v) -> Dict[str, Any]:
    return {'versionId': str(v.id), 'versionLabel': v.version_label or v.original_filename,
            'filename': v.original_filename, 'activityCount': v.activity_count,
            'uploadedAt': v.upload_timestamp.isoformat()}


def previous_resolution(ordered: list, current, only=None) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """(previous, unresolved). `ordered` newest-first. Skips versions that share
    CURRENT's own real Data Date (copies/conflicting twins are not "previous").
    If the version just before CURRENT sits on a Data Date shared by several
    DIFFERENT schedules, PREVIOUS is unresolved: returns (None, info)."""
    if current is None:
        return None, None
    pool = [v for v in ordered if v.id != current.id and (only is None or only(v))]
    idx_cur = next((i for i, v in enumerate(ordered) if v.id == current.id), None)
    if idx_cur is None:
        return None, None
    later_pool = [v for v in ordered[idx_cur + 1:] if (only is None or only(v))]
    later_pool = [v for v in later_pool if not (current.data_date and v.data_date == current.data_date)]
    if not later_pool:
        return None, None
    first = later_pool[0]
    if first.data_date:
        group = [v for v in later_pool if v.data_date == first.data_date]
        if len(group) > 1 and _distinct_contents(group) > 1:
            return None, {
                'dataDate': first.data_date.isoformat(),
                'reason': (f'Previous version unresolved - {len(group)} different schedules share the '
                           f'{first.data_date.isoformat()} Data Date. Resolve which is the approved update.'),
                'candidates': [_candidate_info(v) for v in group],
            }
    return first, None


def assign_roles(versions) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """({version_id: CURRENT/PREVIOUS/BASELINE/OTHER}, info). `info` carries
    'previousUnresolved' and 'currentConflict' explanations when chronology
    cannot be decided from Data Dates alone - never silently guessed."""
    ordered = sort_newest_first(versions)
    roles = {str(v.id): 'OTHER' for v in ordered}
    info: Dict[str, Any] = {'previousUnresolved': None, 'currentConflict': None}

    baseline = next((v for v in ordered if v.schedule_classification in ('APPROVED_BASELINE', 'REVISED_BASELINE')), None)
    current_updates = [v for v in ordered if v.schedule_classification == 'CURRENT_UPDATE']
    current = current_updates[0] if current_updates else (ordered[0] if ordered else None)
    previous, unresolved = previous_resolution(
        ordered, current, only=(lambda v: v.schedule_classification == 'CURRENT_UPDATE') if current_updates else None)

    if baseline:
        roles[str(baseline.id)] = 'BASELINE'
    if current:
        roles[str(current.id)] = 'CURRENT'
        twins = [v for v in ordered if v.id != current.id and current.data_date and v.data_date == current.data_date
                 and v.schedule_classification == current.schedule_classification]
        if twins and _distinct_contents([current] + twins) > 1:
            info['currentConflict'] = {
                'dataDate': current.data_date.isoformat(),
                'reason': f'Current version conflict - different schedules share the {current.data_date.isoformat()} Data Date.',
                'candidates': [_candidate_info(v) for v in [current] + twins],
            }
    if previous:
        roles[str(previous.id)] = 'PREVIOUS'
    info['previousUnresolved'] = unresolved
    return roles, info
