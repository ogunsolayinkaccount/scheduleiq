"""
Project consolidation planner - SYNC INTELLIGENCE, READ-ONLY.

Problem it addresses: every import created its own Project, so one real
schedule history (e.g. Barn 11-Aug -> 19-Aug -> 26-Aug -> 16-Sep) is spread
across many single-version Projects, several of them exact duplicate
re-imports of the same file. This module PLANS how those would collapse into
one logical project with its updates as versions. It never writes:
build_consolidation_plan() only reads; nothing here has an apply mode.

Identity is decided by the existing Schedule Identity engine
(schedule_identity.evaluate_schedule_identity) - Activity-ID overlap is the
strongest signal, P6 project id/name are evidence only - NEVER by filename.
An exact duplicate is decided by a SHA-256 of the parsed activity content
(file_checksum is empty on these records), not by name.

Rules baked into the plan:
  - The earliest-uploaded copy of each (Data Date, content) is retained.
  - Only an EXACT content duplicate of a retained/held copy is a DELETE
    CANDIDATE, so the only copy of a schedule version is never a candidate.
  - Two DIFFERENT files claiming the same Data Date are REVIEW - never
    auto-merged, never deleted.
  - Each version keeps its own Data Date and classification; no baseline is
    ever invented (baselineDesignated=False when none is classified).
  - Deterministic: the same data always yields the identical plan
    (planFingerprint), so running it twice is safe and comparable.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional

from . import schedule_identity
from . import version_difference
from .schedule_identity import LIKELY_SAME_PROJECT, SAME_PROJECT

PLAN_ENGINE_VERSION = '1.0.0'
_LINK_CLASSES = {SAME_PROJECT, LIKELY_SAME_PROJECT}

KEEP = 'KEEP'
DELETE_CANDIDATE = 'DELETE CANDIDATE'
REVIEW = 'REVIEW'


from .version_chronology import assign_roles, content_fingerprint as _fingerprint  # noqa: E402  (shared with role resolution)


def _jaccard(a: set, b: set) -> Optional[float]:
    return round(len(a & b) / len(a | b), 3) if (a or b) else None


def _iso(d) -> Optional[str]:
    return d.isoformat() if d else None


def _sort_key(v: dict):
    return (v['dataDate'] or '9999-12-31', v['uploadedAt'] or '', v['versionId'])


class _UnionFind:
    def __init__(self, items):
        self.p = {i: i for i in items}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


_DATEISH = re.compile(r'^(\d+|jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|up|copy|my)$')


def _suggest_name(p6_names: List[str]) -> Optional[str]:
    """Words present in EVERY P6 project name of the lineage, in the first
    name's order, minus date-like tokens. A suggestion only - the user names
    the project."""
    token_lists = [re.findall(r'[A-Za-z]+|\d+', n or '') for n in p6_names if n]
    if not token_lists:
        return None
    common = set.intersection(*[{t.lower() for t in tl} for tl in token_lists])
    out = [t for t in token_lists[0] if t.lower() in common and not _DATEISH.match(t.lower())]
    return ' '.join(t.capitalize() if t.islower() or t.isupper() and len(t) > 3 else t for t in out) or None


def build_consolidation_plan(name_contains: Optional[str] = None,
                             app_files: Optional[List[dict]] = None) -> Dict[str, Any]:
    from .models import (
        ActivityCodeType, Calendar, CostAccount, ManualCostEntry, MilestoneDefinition, MitigationAction,
        Project, ProjectControlsReport, RecoveryScenario, ScheduleAnalysis, ScheduleDocument, ScheduleRisk,
        ScheduleUpload, UDFType,
    )
    from . import views as V

    needle = (name_contains or '').strip().lower()
    projects = list(Project.objects.all().order_by('created_at', 'id'))
    versions_qs = list(ScheduleUpload.objects.select_related('project').order_by('data_date', 'upload_timestamp', 'id'))
    if needle:
        keep_ids = {
            p.id for p in projects
            if needle in (p.name or '').lower()
            or any(needle in ((v.original_filename or '') + ' ' + (v.project_name_in_file or '')).lower()
                   for v in versions_qs if v.project_id == p.id)
        }
        projects = [p for p in projects if p.id in keep_ids]
        versions_qs = [v for v in versions_qs if v.project_id in keep_ids]

    proj_by_id = {str(p.id): p for p in projects}
    app_by_version = {str(f.get('scheduleUploadId')): f for f in (app_files or []) if f.get('scheduleUploadId')}

    # ── per-version facts ──
    vinfo: Dict[str, dict] = {}
    obj: Dict[str, Any] = {}
    for v in versions_qs:
        vid = str(v.id)
        acts = v.activities_json or []
        obj[vid] = v
        vinfo[vid] = {
            'versionId': vid, 'projectId': str(v.project_id), 'projectName': v.project.name,
            'filename': v.original_filename, 'versionLabel': v.version_label or v.original_filename,
            'dataDate': _iso(v.data_date), 'uploadedAt': v.upload_timestamp.isoformat(),
            'classification': v.schedule_classification, 'activityCount': v.activity_count or len(acts),
            'p6ProjectId': v.project_id_in_file or None, 'p6ProjectName': v.project_name_in_file or None,
            'fingerprint': _fingerprint(acts),
        }

    # ── lineage grouping via the Schedule Identity engine (never filename) ──
    order = sorted(vinfo, key=lambda k: _sort_key(vinfo[k]))
    uf = _UnionFind(order)
    pair_evidence: Dict[tuple, dict] = {}
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            res = schedule_identity.evaluate_schedule_identity(
                obj[a].activities_json, vinfo[a]['p6ProjectId'], vinfo[a]['p6ProjectName'],
                obj[b].activities_json, vinfo[b]['p6ProjectId'], vinfo[b]['p6ProjectName'],
            )
            pair_evidence[(a, b)] = res
            if res['classification'] in _LINK_CLASSES:
                uf.union(a, b)

    groups: Dict[str, List[str]] = defaultdict(list)
    for vid in order:
        groups[uf.find(vid)].append(vid)
    lineage_lists = sorted(groups.values(), key=lambda g: _sort_key(vinfo[g[0]]))

    rows: List[dict] = []
    lineages: List[dict] = []
    retained_by_lineage: Dict[int, List[str]] = {}
    version_status: Dict[str, dict] = {}
    twin_of: Dict[str, str] = {}      # duplicate version id -> retained/held twin id

    for li, members in enumerate(lineage_lists, start=1):
        members = sorted(members, key=lambda k: _sort_key(vinfo[k]))
        by_date: Dict[Optional[str], List[str]] = defaultdict(list)
        for vid in members:
            by_date[vinfo[vid]['dataDate']].append(vid)

        date_keys = sorted(by_date, key=lambda x: x or '9999')
        newest_two = set(date_keys[-2:])
        retained: List[str] = []
        held: List[str] = []
        pending: List[dict] = []
        for dd in date_keys:
            same_dd = by_date[dd]
            by_fp: Dict[str, List[str]] = defaultdict(list)
            for vid in same_dd:
                by_fp[vinfo[vid]['fingerprint']].append(vid)
            distinct = sorted(by_fp.values(), key=lambda g: _sort_key(vinfo[g[0]]))
            in_app_groups = [g for g in distinct if any(v in app_by_version for v in g)]
            for group in distinct:
                in_app = [v for v in group if v in app_by_version]
                if in_app:
                    chosen, why = in_app[0], 'the copy the main application holds'
                elif dd in newest_two and len(group) > 1:
                    chosen, why = group[-1], 'the latest-uploaded copy'
                else:
                    chosen, why = group[0], 'the earliest-uploaded copy'
                if len(distinct) == 1:
                    retained.append(chosen)
                    version_status[chosen] = {'status': KEEP, 'action': 'RETAIN', 'reason': f'Canonical version for this Data Date - {why}.'}
                elif len(in_app_groups) == 1 and group is in_app_groups[0]:
                    # The main application's own copy is the strongest evidence of the authoritative file.
                    retained.append(chosen)
                    version_status[chosen] = {'status': KEEP, 'action': 'RETAIN',
                                              'reason': f'{len(distinct)} DIFFERENT files claim Data Date {dd}; this is the one the main application holds, so it is retained. The other stays REVIEW (not deleted) until its differences are examined.'}
                else:
                    held.append(chosen)
                    reason = (f'{len(distinct)} DIFFERENT files claim Data Date {dd}; ScheduleIQ will not choose between them or delete either. Review the difference report.'
                              if not in_app_groups else
                              f'Differs from the file the main application holds for Data Date {dd}. It will NOT be deleted until its differences are examined (see the difference report).')
                    version_status[chosen] = {'status': REVIEW, 'action': 'HOLD', 'reason': reason}
                for dup in group:
                    if dup == chosen:
                        continue
                    twin_of[dup] = chosen
                    note = ' NOTE: the main application currently references this copy - repoint that file to the retained twin first.' if dup in app_by_version else ''
                    version_status[dup] = {'status': DELETE_CANDIDATE, 'action': 'DUPLICATE', 'duplicateOf': chosen,
                                           'reason': 'Identical parsed source content (ignoring parser-provenance fields) to another import of the same schedule; a copy remains.' + note}
            if len(distinct) > 1:
                pending.append({'dataDate': dd, '_dd': dd, '_groups': [g for g in distinct]})

        by_dd = sorted(retained, key=lambda k: _sort_key(vinfo[k]))

        # Conflict evidence + the difference report a scheduler needs to decide.
        conflict_ids: List[str] = []
        for pd in pending:
            groups = pd.pop('_groups')
            dd = pd.pop('_dd')
            reps = [next((v for v in g if v in retained or v in held), g[0]) for g in groups]
            conflict_ids += reps
            before = [v for v in by_dd if (vinfo[v]['dataDate'] or '') < (dd or '')]
            after = [v for v in by_dd if (vinfo[v]['dataDate'] or '') > (dd or '')]
            pv, nx = (before[-1] if before else None), (after[0] if after else None)
            authoritative = [r for r in reps if r in retained]
            pd['reason'] = ('Two different schedules share this Data Date. ' +
                            ('The main application holds one of them (retained); the other stays REVIEW.' if authoritative
                             else 'Neither is referenced by the main application - a scheduler must choose which is the real update.'))
            pd['resolvedByMainAppReference'] = bool(authoritative)
            pd['candidates'] = [
                {'versionId': g, 'filename': vinfo[g]['filename'], 'uploadedAt': vinfo[g]['uploadedAt'],
                 'activityCount': vinfo[g]['activityCount'], 'p6ProjectId': vinfo[g]['p6ProjectId'], 'p6ProjectName': vinfo[g]['p6ProjectName'],
                 'inMainApp': g in app_by_version or any(v in app_by_version for v in groups[gi]),
                 'activityIdsNotInTheOtherCandidate': (len(_ids(obj[g]) - _ids(obj[reps[1 - gi]])) if len(reps) == 2 else None),
                 'overlapWithPreviousRetained': _jaccard(_ids(obj[g]), _ids(obj[pv])) if pv else None,
                 'overlapWithNextRetained': _jaccard(_ids(obj[g]), _ids(obj[nx])) if nx else None}
                for gi, g in enumerate(reps)
            ]
            pd['previousRetainedVersionId'], pd['nextRetainedVersionId'] = pv, nx
            if len(reps) == 2:
                pd['differenceReport'] = version_difference.build_difference_report(obj[reps[0]], obj[reps[1]], sample=10)

        anchor_pool = retained or held
        anchor = max(anchor_pool, key=lambda k: _sort_key(vinfo[k])) if anchor_pool else members[-1]

        # Canonical project: prefer one hosting a main-app-referenced version, then the earliest-created.
        member_hosts = {vinfo[v]['projectId'] for v in (retained + held or members)}
        app_hosts = {vinfo[v]['projectId'] for v in (retained + held or members) if v in app_by_version}
        pool = [proj_by_id[i] for i in (app_hosts or member_hosts)]
        canonical = sorted(pool, key=lambda p: (p.created_at, str(p.id)))[0]
        p6_names = [vinfo[v]['p6ProjectName'] for v in members if vinfo[v]['p6ProjectName']]

        # Every non-duplicate lineage member lives in the canonical project - including the
        # REVIEW ones, so a same-Data-Date conflict is VISIBLE to role resolution instead of hidden
        # in a stray project (which would let the next-older update pose as Previous).
        for vid in retained + held:
            version_status[vid]['moveToCanonical'] = vinfo[vid]['projectId'] != str(canonical.id)

        def _identity_vs_anchor(vid):
            if vid == anchor:
                return {'classification': 'ANCHOR', 'activityIdOverlap': None}
            key = (vid, anchor) if (vid, anchor) in pair_evidence else (anchor, vid)
            r = pair_evidence[key]
            ov = r['signals']['activityIdOverlap']
            return {'classification': r['classification'],
                    'activityIdOverlap': round(ov['jaccard'], 3) if ov.get('jaccard') is not None else None}

        # Roles via the SAME chronology rule the application uses (effective Data Date; conflicts explicit).
        final_members = retained + held
        roles, role_info = assign_roles([obj[v] for v in final_members]) if final_members else ({}, {'previousUnresolved': None, 'currentConflict': None})
        role_ids = {'CURRENT': None, 'PREVIOUS': None, 'BASELINE': None}
        for vid, role in roles.items():
            if role in role_ids:
                role_ids[role] = vid
        order_warnings: List[str] = []
        if role_info.get('currentConflict'):
            order_warnings.append(role_info['currentConflict']['reason'])
        if role_info.get('previousUnresolved'):
            order_warnings.append(role_info['previousUnresolved']['reason'])
        by_dd = sorted(final_members, key=lambda k: _sort_key(vinfo[k]))
        inversions = [
            {'versionId': b, 'dataDate': vinfo[b]['dataDate'], 'uploadedAt': vinfo[b]['uploadedAt'],
             'note': 'Uploaded before an older-Data-Date version - informational only: chronology follows the Data Date, not upload time.'}
            for a, b in zip(by_dd, by_dd[1:]) if vinfo[b]['uploadedAt'] < vinfo[a]['uploadedAt']
        ]

        entries = []
        for vid in members:
            st = version_status[vid]
            row = dict(vinfo[vid])
            row.update({
                'lineageId': f'L{li}', 'status': st['status'], 'action': st['action'], 'reason': st['reason'],
                'moveToCanonical': bool(st.get('moveToCanonical')), 'duplicateOf': st.get('duplicateOf'),
                'identityVsAnchor': _identity_vs_anchor(vid),
                'currentRoleInOwnProject': None, 'proposedRole': roles.get(vid), 'inDataDateConflict': vid in conflict_ids,
            })
            ov = row['identityVsAnchor']['activityIdOverlap']
            if ov is not None and ov < 0.90 and row['action'] != 'DUPLICATE':
                row['identityNote'] = (f"Same lineage by the Schedule Identity engine but only {ov} Activity-ID overlap with the newest version "
                                       f"- confirm it is the schedule you want in this history (e.g. a subset/scope variant).")
            row['fingerprint'] = row['fingerprint'][:12]
            entries.append(row)

        retained_by_lineage[li] = retained
        lineages.append({
            'lineageId': f'L{li}',
            'canonicalProject': {
                'projectId': str(canonical.id), 'currentName': canonical.name,
                'suggestedName': _suggest_name(p6_names),
                'p6Identity': {'projectIds': sorted({vinfo[v]['p6ProjectId'] for v in members if vinfo[v]['p6ProjectId']}),
                               'projectNames': sorted(set(p6_names))},
            },
            'anchorVersionId': anchor,
            'versions': entries,
            'retainedChain': [{'versionId': v, 'dataDate': vinfo[v]['dataDate'], 'filename': vinfo[v]['filename'],
                               'activityCount': vinfo[v]['activityCount'], 'proposedRole': roles.get(v),
                               'status': version_status[v]['status'], 'inDataDateConflict': v in conflict_ids} for v in by_dd],
            'previousUnresolved': role_info.get('previousUnresolved'),
            'currentConflict': role_info.get('currentConflict'),
            'proposedRoles': {'currentVersionId': role_ids['CURRENT'], 'previousVersionId': role_ids['PREVIOUS'],
                              'baselineVersionId': role_ids['BASELINE']},
            'baselineDesignated': role_ids['BASELINE'] is not None,
            'baselineNote': None if role_ids['BASELINE'] else
            'No version in this lineage is classified as an Approved/Revised Baseline. None will be assumed - designate one explicitly after consolidation.',
            'uploadOrderInversions': inversions,
            'roleResolutionWarnings': order_warnings,
            'pendingDecisions': pending,
        })

    # ── strongest identity evidence BETWEEN different lineages (why they were not merged) ──
    root_of = {vid: uf.find(vid) for vid in order}
    cross = []
    for (a, b), r in pair_evidence.items():
        if root_of[a] == root_of[b]:
            continue
        ov = r['signals']['activityIdOverlap']
        p6m = r['signals']['p6ProjectId']['match']
        if p6m or (ov.get('jaccard') or 0) >= 0.10:
            cross.append({'a': a, 'aFile': vinfo[a]['filename'], 'aDataDate': vinfo[a]['dataDate'], 'b': b, 'bFile': vinfo[b]['filename'],
                          'bDataDate': vinfo[b]['dataDate'], 'classification': r['classification'], 'p6ProjectIdMatch': p6m,
                          'activityIdJaccard': round(ov['jaccard'], 3) if ov.get('jaccard') is not None else None,
                          'overlapOfReference': round(ov['referenceOverlapRatio'], 3) if ov.get('referenceOverlapRatio') is not None else None,
                          'overlapOfOther': round(ov['uploadedOverlapRatio'], 3) if ov.get('uploadedOverlapRatio') is not None else None,
                          'explanation': r['explanation']})
    cross.sort(key=lambda x: (-(x['activityIdJaccard'] or 0), x['a'], x['b']))
    cross = cross[:8]

    # ── projects: fate after the plan ──
    version_final_project: Dict[str, str] = {}
    canon_by_lineage = {l['lineageId']: l['canonicalProject']['projectId'] for l in lineages}
    for l in lineages:
        for e in l['versions']:
            if e['action'] == 'RETAIN':
                version_final_project[e['versionId']] = canon_by_lineage[l['lineageId']]
            elif e['action'] == 'HOLD':
                version_final_project[e['versionId']] = canon_by_lineage[l['lineageId']]   # moves too, so the conflict stays visible
    project_rows = []
    for p in projects:
        pid = str(p.id)
        own = [vid for vid, vi in vinfo.items() if vi['projectId'] == pid]
        remaining = [vid for vid in version_final_project if version_final_project[vid] == pid]
        is_canonical = pid in canon_by_lineage.values()
        if remaining:
            status, reason = (KEEP, 'Canonical project for its schedule history.' if is_canonical
                              else 'Still hosts a held (REVIEW) version.')
        else:
            status, reason = DELETE_CANDIDATE, 'Would be empty: every version is a duplicate or moves to the canonical project.'
        project_rows.append({'projectId': pid, 'name': p.name, 'createdAt': p.created_at.isoformat(),
                             'versionsToday': len(own), 'versionsAfter': len(remaining),
                             'status': status, 'reason': reason})

    # ── references that would be repointed / removed ──
    refs: List[dict] = []

    def _add(model, count, scope, frm, to, action, note=''):
        if count:
            refs.append({'model': model, 'count': count, 'scope': scope, 'from': frm, 'to': to, 'action': action, 'note': note})

    dup_ids = [vid for vid, s in version_status.items() if s['action'] == 'DUPLICATE']
    for dup in sorted(dup_ids):
        twin = twin_of[dup]
        frm, to = {'versionId': dup}, {'versionId': twin}
        _add('RecoveryScenario', RecoveryScenario.objects.filter(schedule_upload_id=dup).count(), 'version', frm, to, 'REPOINT')
        _add('ProjectControlsReport', ProjectControlsReport.objects.filter(schedule_upload_id=dup).count(), 'version', frm, to, 'REPOINT', 'saved report snapshots')
        _add('ScheduleDocument', ScheduleDocument.objects.filter(schedule_upload_id=dup).count(), 'version', frm, to, 'REPOINT')
        _add('ManualCostEntry', ManualCostEntry.objects.filter(schedule_upload_id=dup).count(), 'version', frm, to, 'REPOINT')
        _add('ScheduleAnalysis', ScheduleAnalysis.objects.filter(current_upload_id=dup).count(), 'version', frm, to, 'REPOINT', 'comparison references (current)')
        _add('ScheduleAnalysis', ScheduleAnalysis.objects.filter(baseline_upload_id=dup).count() + ScheduleAnalysis.objects.filter(previous_upload_id=dup).count(),
             'version', frm, to, 'REPOINT', 'comparison references (baseline/previous)')
        _add('ScheduleRisk', ScheduleRisk.objects.filter(first_identified_version_id=dup).count(), 'version', frm, to, 'REPOINT', 'first-identified version pointer')
        for mdl, label in ((MilestoneDefinition, 'MilestoneDefinition'), (CostAccount, 'CostAccount'), (Calendar, 'Calendar'),
                           (ActivityCodeType, 'ActivityCodeType'), (UDFType, 'UDFType')):
            _add(label, mdl.objects.filter(schedule_upload_id=dup).count(), 'version', frm, to, 'REMOVED_WITH_DUPLICATE',
                 'version-owned import data; the retained twin carries its own identical copy')

    for l in lineages:
        canon = l['canonicalProject']['projectId']
        canon_risk_keys = set(ScheduleRisk.objects.filter(project_id=canon).values_list('risk_key', flat=True))
        source_projects = sorted({e['projectId'] for e in l['versions']} - {canon})
        for sp in source_projects:
            frm, to = {'projectId': sp}, {'projectId': canon}
            _add('RecoveryScenario', RecoveryScenario.objects.filter(project_id=sp).count(), 'project', frm, to, 'REPOINT')
            _add('MitigationAction', MitigationAction.objects.filter(project_id=sp).count(), 'project', frm, to, 'REPOINT')
            _add('ProjectControlsReport', ProjectControlsReport.objects.filter(project_id=sp).count(), 'project', frm, to, 'REPOINT', 'saved report snapshots')
            _add('ScheduleDocument', ScheduleDocument.objects.filter(project_id=sp).count(), 'project', frm, to, 'REPOINT')
            _add('ManualCostEntry', ManualCostEntry.objects.filter(project_id=sp).count(), 'project', frm, to, 'REPOINT')
            src_keys = set(ScheduleRisk.objects.filter(project_id=sp).values_list('risk_key', flat=True))
            conflict = src_keys & canon_risk_keys
            _add('ScheduleRisk', len(src_keys - conflict), 'project', frm, to, 'REPOINT', 'risk workflow rows (owner/status/notes)')
            _add('ScheduleRisk', len(conflict), 'project', frm, to, 'MERGE_CONFLICT',
                 'same risk_key already exists on the canonical project (unique per project) - needs a decision')
            canon_risk_keys |= src_keys
        for e in l['versions']:
            if e['action'] in ('RETAIN', 'HOLD') and e['moveToCanonical']:
                refs.append({'model': 'ScheduleUpload', 'count': 1, 'scope': 'version',
                             'from': {'projectId': e['projectId'], 'versionId': e['versionId']},
                             'to': {'projectId': canon, 'versionId': e['versionId']}, 'action': 'REPOINT',
                             'note': f"version {e['dataDate']} moves into the canonical project; Data Date, classification and upload time unchanged"})

    # ── flat table: every version as shown today ──
    for l in lineages:
        for e in l['versions']:
            f = app_by_version.get(e['versionId'])
            rows.append({
                'displayName': e['projectName'], 'lineageId': e['lineageId'],
                'frontendProjectId': f.get('projectId') if f else None,
                'frontendScheduleUploadId': f.get('scheduleUploadId') if f else None,
                'inMainApp': (bool(f) if app_files else None),
                'backendProjectId': e['projectId'], 'versionId': e['versionId'],
                'roleTodayInOwnProject': _role_in_own_project(V, obj, e),
                'dataDate': e['dataDate'], 'activityCount': e['activityCount'],
                'p6ProjectId': e['p6ProjectId'], 'p6ProjectName': e['p6ProjectName'],
                'identityVsAnchor': e['identityVsAnchor']['classification'],
                'activityIdOverlapVsAnchor': e['identityVsAnchor']['activityIdOverlap'],
                'status': e['status'], 'action': e['action'], 'reason': e['reason'],
                'moveToCanonical': e['moveToCanonical'], 'duplicateOf': e['duplicateOf'],
                'proposedRole': e.get('proposedRole'), 'inDataDateConflict': e.get('inDataDateConflict'), 'identityNote': e.get('identityNote'),
            })

    plan = {
        'engineVersion': PLAN_ENGINE_VERSION, 'dryRun': True, 'writesPerformed': 0,
        'scope': {'nameContains': name_contains or None, 'projects': len(projects), 'versions': len(vinfo)},
        'frontendStateProvided': bool(app_files),
        'summary': {
            'lineages': len(lineages),
            'versionsKeep': sum(1 for r in rows if r['status'] == KEEP),
            'versionsDeleteCandidate': sum(1 for r in rows if r['status'] == DELETE_CANDIDATE),
            'versionsReview': sum(1 for r in rows if r['status'] == REVIEW),
            'projectsKeep': sum(1 for p in project_rows if p['status'] == KEEP),
            'projectsDeleteCandidate': sum(1 for p in project_rows if p['status'] == DELETE_CANDIDATE),
            'projectsReview': sum(1 for p in project_rows if p['status'] == REVIEW),
            'referencesToRepoint': sum(r['count'] for r in refs if r['action'] == 'REPOINT'),
            'mergeConflicts': sum(r['count'] for r in refs if r['action'] == 'MERGE_CONFLICT'),
        },
        'lineages': lineages, 'crossLineageEvidence': cross, 'projects': project_rows, 'references': refs, 'rows': rows,
    }
    plan['planFingerprint'] = hashlib.sha256(json.dumps(plan, sort_keys=True, default=str).encode()).hexdigest()
    return plan


def _ids(version) -> set:
    return {str(a.get('code') or a.get('id') or '') for a in (version.activities_json or [])} - {''}


def _role_in_own_project(V, obj, entry) -> Optional[str]:
    v = obj[entry['versionId']]
    return V._current_role(v.project, v)


def validate_plan_with_rollback(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Proves the proposed consolidation removes the version-resolution
    failure WITHOUT persisting anything: inside one transaction it repoints
    the RETAIN versions to their canonical project, issues GET requests for
    every retained version through the real endpoints with the explicit
    version id, then ALWAYS rolls the transaction back. No DELETE is issued
    and nothing survives the rollback."""
    from django.db import transaction
    from django.test import Client
    from .models import ScheduleUpload

    results: List[dict] = []
    marker = 'No schedule version available'
    try:
        with transaction.atomic():
            for l in plan['lineages']:
                canon = l['canonicalProject']['projectId']
                for e in l['versions']:
                    if e['action'] in ('RETAIN', 'HOLD') and e['moveToCanonical']:
                        ScheduleUpload.objects.filter(pk=e['versionId']).update(project_id=canon)
            c = Client()
            for l in plan['lineages']:
                canon = l['canonicalProject']['projectId']
                held_elsewhere = [e for e in l['versions'] if e['action'] == 'DUPLICATE' and e['projectId'] != canon]
                for e in l['versions']:
                    if e['action'] != 'RETAIN':
                        continue
                    vid = e['versionId']
                    checks = {
                        'risk': f'/api/projects/{canon}/risk/?version={vid}',
                        'risk-register': f'/api/projects/{canon}/risk-register/?currentVersion={vid}',
                        'activity-analysis': f'/api/projects/{canon}/activity-analysis/?currentVersion={vid}',
                        'float-analysis': f'/api/projects/{canon}/float-analysis/?currentVersion={vid}',
                        'update-intelligence': f'/api/projects/{canon}/update-intelligence/?currentVersion={vid}',
                    }
                    for name, url in checks.items():
                        resp = c.get(url)
                        body = resp.content.decode('utf-8', 'ignore')
                        try:
                            data = json.loads(body)
                        except ValueError:
                            data = {}
                        analyzed = data.get('versionId') or data.get('currentVersionId')
                        results.append({'lineageId': l['lineageId'], 'versionId': vid, 'dataDate': e['dataDate'], 'endpoint': name,
                                        'httpStatus': resp.status_code, 'noVersionError': marker in body,
                                        'analyzedExactlySelectedVersion': analyzed == vid,
                                        'available': data.get('available'), 'unavailableReason': data.get('reason')})
                # cross-project leakage: a version that is NOT in the canonical project must be refused
                for e in held_elsewhere[:1]:
                    resp = c.get(f'/api/projects/{canon}/risk/?version={e["versionId"]}')
                    results.append({'lineageId': l['lineageId'], 'versionId': e['versionId'], 'dataDate': e['dataDate'],
                                    'endpoint': 'risk (cross-project version - must be refused)', 'httpStatus': resp.status_code,
                                    'noVersionError': marker in resp.content.decode('utf-8', 'ignore'),
                                    'refusedAsExpected': resp.status_code >= 400})
            # The canonical project must report an unresolved Previous instead of hiding it.
            for l in plan['lineages']:
                if not l.get('previousUnresolved'):
                    continue
                canon = l['canonicalProject']['projectId']
                vers = c.get(f'/api/projects/{canon}/versions/').json().get('versions', [])
                cur = next((v for v in vers if v['role'] == 'CURRENT'), None)
                ui = c.get(f'/api/projects/{canon}/update-intelligence/').json()
                results.append({'lineageId': l['lineageId'], 'versionId': cur['id'] if cur else None,
                                'dataDate': cur['dataDate'] if cur else None, 'endpoint': 'previous-unresolved reporting',
                                'httpStatus': 200, 'noVersionError': False, 'analyzedExactlySelectedVersion': True,
                                'available': ui.get('available'), 'unavailableReason': ui.get('reason'),
                                'previousUnresolvedReported': bool(cur and cur.get('previousUnresolved')) and 'unresolved' in (ui.get('reason') or '').lower(),
                                'noPreviousRoleAssigned': not any(v['role'] == 'PREVIOUS' for v in vers)})
            raise _Rollback()
    except _Rollback:
        pass
    ok_rows = [r for r in results if 'refusedAsExpected' not in r]
    return {
        'rolledBack': True, 'checks': len(results),
        'allRetainedVersionsResolve': all(r['httpStatus'] == 200 and not r['noVersionError'] for r in ok_rows),
        # An endpoint that legitimately cannot compare (e.g. Risk Register with no previous version)
        # must say so with an explanatory unavailable state - never silently analyze another version.
        'everyEndpointAnalyzedTheSelectedVersionOrExplainedUnavailable': all(
            r['analyzedExactlySelectedVersion'] or (r['available'] is False and bool(r['unavailableReason'])) for r in ok_rows),
        'crossProjectLeakage': any(r.get('refusedAsExpected') is False for r in results),
        'results': results,
    }


class _Rollback(Exception):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# APPLY - confirmation-gated. Never runs from a dry-run; never runs without the
# exact plan the user approved (fingerprint) plus the browser's file list.
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_CONFIRMATION = 'CONSOLIDATE'


class ConsolidationRefused(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def apply_consolidation_plan(name_contains: Optional[str], app_files: Optional[List[dict]],
                             expected_fingerprint: Optional[str], confirmation: Optional[str],
                             new_name: Optional[str] = None) -> Dict[str, Any]:
    """Executes the plan that produced `expected_fingerprint`, all-or-nothing.

    Per lineage: (1) references to each exact-duplicate version are repointed
    to its retained twin; (2) every non-duplicate version (KEEP and REVIEW)
    moves into the canonical project keeping its Data Date, classification
    and upload time; (3) project-level records of the emptied source projects
    move to the canonical project; (4) each duplicate version is deleted ONLY
    after re-verifying its twin still exists with identical content; (5) a
    source project is deleted only if it is now completely empty. REVIEW
    versions are moved, never deleted. Refuses on any mismatch."""
    from django.db import transaction
    from .models import (
        ManualCostEntry, MitigationAction, Project, ProjectControlsReport, RecoveryScenario, ScheduleAnalysis,
        ScheduleDocument, ScheduleRisk, ScheduleUpload,
    )
    from . import views as V

    if confirmation != REQUIRED_CONFIRMATION:
        raise ConsolidationRefused(f'Confirmation required: send confirmation="{REQUIRED_CONFIRMATION}".')
    if not app_files:
        raise ConsolidationRefused('mainAppFiles is required - the main application\'s own file list is the deciding evidence and cannot be omitted.')
    plan = build_consolidation_plan(name_contains, app_files)
    if not expected_fingerprint or plan['planFingerprint'] != expected_fingerprint:
        raise ConsolidationRefused('The data has changed since the plan you approved (fingerprint mismatch). Re-run the dry-run and approve the new plan.', status=409)
    if plan['summary']['mergeConflicts']:
        raise ConsolidationRefused('The plan has unresolved merge conflicts (same risk_key on more than one project). Resolve them first.', status=409)

    # THE deletion allowlist: only exact content duplicates the PLAN classified DELETE CANDIDATE, each with a
    # retained twin. Absence from mainAppFiles (or from any list) is never, by itself, a reason to delete.
    allowlist: Dict[str, str] = {e['versionId']: e['duplicateOf'] for l in plan['lineages'] for e in l['versions']
                                 if e['action'] == 'DUPLICATE' and e['status'] == DELETE_CANDIDATE and e.get('duplicateOf')}
    referenced_by_app = {str(f.get('scheduleUploadId')) for f in app_files if f.get('scheduleUploadId')}
    if referenced_by_app & set(allowlist):
        raise ConsolidationRefused('The main application itself references a version this plan would delete as a duplicate. '
                                   'Repoint that file to the retained copy first.', status=409)
    deletable_projects = {p['projectId'] for p in plan['projects'] if p['status'] == DELETE_CANDIDATE}
    retained_ids = [e['versionId'] for l in plan['lineages'] for e in l['versions'] if e['action'] in ('RETAIN', 'HOLD')]

    report: Dict[str, Any] = {'applied': True, 'planFingerprint': plan['planFingerprint'], 'lineages': [],
                              'deletionAllowlist': sorted(allowlist), 'versionsRetained': sorted(retained_ids), 'versionsDeleted': [],
                              'versionsBefore': ScheduleUpload.objects.count(), 'projectsBefore': Project.objects.count()}
    with transaction.atomic():
        for l in plan['lineages']:
            canon_id = l['canonicalProject']['projectId']
            canon = Project.objects.get(pk=canon_id)
            entry = {'lineageId': l['lineageId'], 'canonicalProjectId': canon_id, 'versionsMoved': [], 'duplicatesDeleted': [],
                     'projectsDeleted': [], 'referencesRepointed': {}}

            def bump(name, n):
                if n:
                    entry['referencesRepointed'][name] = entry['referencesRepointed'].get(name, 0) + n

            dups = [e for e in l['versions'] if e['action'] == 'DUPLICATE']
            for e in dups:
                dup_id, twin_id = e['versionId'], e['duplicateOf']
                bump('RecoveryScenario', RecoveryScenario.objects.filter(schedule_upload_id=dup_id).update(schedule_upload_id=twin_id, project_id=canon_id))
                bump('ProjectControlsReport', ProjectControlsReport.objects.filter(schedule_upload_id=dup_id).update(schedule_upload_id=twin_id, project_id=canon_id))
                bump('ScheduleDocument', ScheduleDocument.objects.filter(schedule_upload_id=dup_id).update(schedule_upload_id=twin_id, project_id=canon_id))
                bump('ManualCostEntry', ManualCostEntry.objects.filter(schedule_upload_id=dup_id).update(schedule_upload_id=twin_id, project_id=canon_id))
                bump('ScheduleAnalysis', ScheduleAnalysis.objects.filter(current_upload_id=dup_id).update(current_upload_id=twin_id))
                bump('ScheduleAnalysis', ScheduleAnalysis.objects.filter(baseline_upload_id=dup_id).update(baseline_upload_id=twin_id))
                bump('ScheduleAnalysis', ScheduleAnalysis.objects.filter(previous_upload_id=dup_id).update(previous_upload_id=twin_id))
                bump('ScheduleRisk', ScheduleRisk.objects.filter(first_identified_version_id=dup_id).update(first_identified_version_id=twin_id))

            source_projects = sorted({e['projectId'] for e in l['versions']} - {canon_id})
            for e in l['versions']:
                if e['action'] in ('RETAIN', 'HOLD') and e['moveToCanonical']:
                    ScheduleUpload.objects.filter(pk=e['versionId']).update(project_id=canon_id)
                    entry['versionsMoved'].append({'versionId': e['versionId'], 'dataDate': e['dataDate'], 'status': e['status']})

            for sp in source_projects:
                bump('RecoveryScenario', RecoveryScenario.objects.filter(project_id=sp).update(project_id=canon_id))
                bump('MitigationAction', MitigationAction.objects.filter(project_id=sp).update(project_id=canon_id))
                bump('ProjectControlsReport', ProjectControlsReport.objects.filter(project_id=sp).update(project_id=canon_id))
                bump('ScheduleDocument', ScheduleDocument.objects.filter(project_id=sp).update(project_id=canon_id))
                bump('ManualCostEntry', ManualCostEntry.objects.filter(project_id=sp).update(project_id=canon_id))
                bump('ScheduleRisk', ScheduleRisk.objects.filter(project_id=sp).update(project_id=canon_id))

            for e in dups:
                dup, twin = ScheduleUpload.objects.filter(pk=e['versionId']).first(), ScheduleUpload.objects.filter(pk=e['duplicateOf']).first()
                if dup is None:
                    continue
                if e['versionId'] not in allowlist or allowlist[e['versionId']] != e['duplicateOf']:
                    raise ConsolidationRefused(f"Refusing to delete {e['versionId']}: it is not on the plan's exact-duplicate allowlist.", status=409)
                if twin is None or _fingerprint(twin.activities_json) != _fingerprint(dup.activities_json):
                    raise ConsolidationRefused(f"Refusing to delete {e['versionId']}: its retained twin is missing or no longer identical.", status=409)
                dup.delete()
                entry['duplicatesDeleted'].append(e['versionId'])
                report['versionsDeleted'].append(e['versionId'])

            for sp in source_projects:
                proj = Project.objects.filter(pk=sp).first()
                if proj is None or proj.schedule_versions.exists() or sp not in deletable_projects:
                    continue
                leftovers = (proj.recovery_scenarios.count() + proj.schedule_risks.count() + proj.mitigation_actions.count()
                             + proj.documents.count() + proj.controls_reports.count() + proj.manual_cost_entries.count())
                if leftovers:
                    continue                      # never delete a project that still owns records
                V._delete_project(proj)
                entry['projectsDeleted'].append({'projectId': sp})

            if new_name and len(plan['lineages']) == 1:
                canon.name = new_name
                canon.save(update_fields=['name'])

            # Post-condition: every non-duplicate version now lives in the canonical project.
            for e in l['versions']:
                if e['action'] in ('RETAIN', 'HOLD'):
                    if str(ScheduleUpload.objects.get(pk=e['versionId']).project_id) != canon_id:
                        raise ConsolidationRefused(f"Post-check failed for version {e['versionId']}; nothing was changed.", status=409)
            report['lineages'].append(entry)
    assert set(report['versionsDeleted']) <= set(allowlist)          # nothing outside the allowlist was removed
    report['versionsAfter'] = ScheduleUpload.objects.count()
    report['projectsAfter'] = Project.objects.count()
    return report


def reference_integrity() -> Dict[str, int]:
    """Counts of records whose project and version references disagree (all 0 when clean)."""
    from django.db.models import F
    from .models import (ManualCostEntry, MitigationAction, Project, ProjectControlsReport, RecoveryScenario,
                         ScheduleDocument, ScheduleRisk)
    return {
        'recoveryScenarioProjectMismatch': RecoveryScenario.objects.exclude(project_id=F('schedule_upload__project_id')).count(),
        'reportProjectMismatch': ProjectControlsReport.objects.filter(schedule_upload__isnull=False).exclude(project_id=F('schedule_upload__project_id')).count(),
        'documentProjectMismatch': ScheduleDocument.objects.filter(schedule_upload__isnull=False, project__isnull=False).exclude(project_id=F('schedule_upload__project_id')).count(),
        'costEntryProjectMismatch': ManualCostEntry.objects.filter(schedule_upload__isnull=False).exclude(project_id=F('schedule_upload__project_id')).count(),
        'riskFirstVersionProjectMismatch': ScheduleRisk.objects.filter(first_identified_version__isnull=False).exclude(project_id=F('first_identified_version__project_id')).count(),
        'mitigationScenarioProjectMismatch': MitigationAction.objects.filter(scenario__isnull=False).exclude(project_id=F('scenario__project_id')).count(),
        'projectsWithNoVersions': Project.objects.filter(schedule_versions__isnull=True).count(),
    }


def simulate_apply_with_rollback(name_contains: Optional[str], app_files: List[dict]) -> Dict[str, Any]:
    """Executes the EXACT plan for these inputs through apply_consolidation_plan inside a transaction,
    inspects the resulting state (roles, every retained version through the real endpoints, AI context,
    reference integrity), then ALWAYS rolls back. Nothing is persisted."""
    from django.db import transaction
    from django.test import Client
    from unittest.mock import patch
    from .models import Project, ScheduleUpload

    plan = build_consolidation_plan(name_contains, app_files)

    def state():
        return {
            'projects': sorted((str(p.id), p.name, p.schedule_versions.count()) for p in Project.objects.all()),
            'versions': sorted(str(i) for i in ScheduleUpload.objects.values_list('id', flat=True)),
        }

    before = state()
    out: Dict[str, Any] = {'planFingerprint': plan['planFingerprint'], 'before': before}

    class _Unconfigured:
        def is_configured(self):
            return False

    try:
        with transaction.atomic():
            report = apply_consolidation_plan(name_contains, app_files, plan['planFingerprint'], REQUIRED_CONFIRMATION)
            out['applyReport'] = report
            out['after'] = state()
            c = Client()
            projects_out = []
            for p in Project.objects.all().order_by('name'):
                vers = c.get(f'/api/projects/{p.id}/versions/').json().get('versions', [])
                projects_out.append({
                    'projectId': str(p.id), 'name': p.name,
                    'versions': [{'versionId': v['id'], 'dataDate': v['dataDate'], 'role': v['role'], 'activityCount': v['activityCount'],
                                  'filename': v['filename'], 'dataDateConflict': v.get('dataDateConflict'),
                                  'previousUnresolved': bool(v.get('previousUnresolved'))} for v in vers],
                })
            out['projectsAfter'] = projects_out
            checks = []
            marker = 'No schedule version available'
            for po in projects_out:
                for v in po['versions']:
                    vid = v['versionId']
                    for name, url in (
                        ('activity-analysis', f"/api/projects/{po['projectId']}/activity-analysis/?currentVersion={vid}"),
                        ('float-analysis', f"/api/projects/{po['projectId']}/float-analysis/?currentVersion={vid}"),
                        ('update-intelligence', f"/api/projects/{po['projectId']}/update-intelligence/?currentVersion={vid}"),
                        ('risk', f"/api/projects/{po['projectId']}/risk/?version={vid}"),
                        ('risk-register', f"/api/projects/{po['projectId']}/risk-register/?currentVersion={vid}"),
                    ):
                        resp = c.get(url)
                        body = resp.content.decode('utf-8', 'ignore')
                        try:
                            data = resp.json()
                        except ValueError:
                            data = {}
                        checks.append({'projectId': po['projectId'], 'versionId': vid, 'dataDate': v['dataDate'], 'endpoint': name,
                                       'httpStatus': resp.status_code, 'noVersionError': marker not in body,
                                       'analyzedSelectedVersion': (data.get('versionId') or data.get('currentVersionId')) == vid,
                                       'available': data.get('available'), 'reason': data.get('reason')})
                    with patch('scheduler.views.get_provider', return_value=_Unconfigured()):
                        r = c.post(f"/api/projects/{po['projectId']}/ai-chat/", data=json.dumps({'question': 'status?', 'version': vid}),
                                   content_type='application/json')
                    ctx = (r.json() or {}).get('context') or {}
                    checks.append({'projectId': po['projectId'], 'versionId': vid, 'dataDate': v['dataDate'], 'endpoint': 'ai-context',
                                   'httpStatus': r.status_code, 'noVersionError': r.status_code == 200,
                                   'analyzedSelectedVersion': (ctx.get('currentVersion') or {}).get('id') == vid,
                                   'previousVersionId': (ctx.get('previousVersion') or {}).get('id'),
                                   'previousUnresolved': bool(ctx.get('previousVersionUnresolved'))})
            out['checks'] = checks
            out['integrity'] = reference_integrity()
            raise _Rollback()
    except _Rollback:
        pass
    after_rollback = state()
    out['rolledBackCompletely'] = after_rollback == before
    return out
