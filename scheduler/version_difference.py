"""
Version difference report - READ-ONLY, deterministic.

Answers "what is actually different between these two schedule versions?"
for two versions that claim the same Data Date (or any two versions), so a
scheduler can decide which is authoritative. Built on the existing engines
(schedule_comparison.compare_activities / compare_relationships and
schedule_identity) - no second comparison methodology.

Direction: version A is the reference, B is compared against it. Nothing is
"previous" or "current" here; roles are not implied.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from . import schedule_identity
from .schedule_comparison import compare_activities, compare_relationships

ENGINE_VERSION = '1.0.0'
DEFAULT_SAMPLE = 25

# Extra date fields the standard comparison does not track (CPM / remaining dates).
_EXTRA_DATE_FIELDS = (
    ('earlyStart', 'Early Start'), ('earlyFinish', 'Early Finish'), ('lateStart', 'Late Start'),
    ('lateFinish', 'Late Finish'), ('remainStart', 'Remaining Start'), ('remainFinish', 'Remaining Finish'),
    ('expectedFinish', 'Expected Finish'),
)
_EXTRA_CONSTRAINT_FIELDS = (('constraint2Type', 'Secondary Constraint Type'), ('constraint2Date', 'Secondary Constraint Date'))

_CATEGORY_OF = {
    'bStart': 'dates', 'bFinish': 'dates', 'start': 'dates', 'finish': 'dates',
    'dur': 'durations', 'remainDur': 'durations',
    'totalFloat': 'float', 'freeFloat': 'float',
    'constraintType': 'constraints', 'constraintDate': 'constraints',
    'pctComplete': 'progress',
}


def _act_id(a: dict) -> str:
    return str(a.get('code') or a.get('id') or '')


def _pd(v) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and v:
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _meta(v) -> Dict[str, Any]:
    return {
        'versionId': str(v.id), 'projectId': str(v.project_id), 'projectName': v.project.name if v.project_id else None,
        'filename': v.original_filename, 'versionLabel': v.version_label or v.original_filename,
        'dataDate': v.data_date.isoformat() if v.data_date else None,
        'sourceDataDate': v.source_data_date.isoformat() if v.source_data_date else None,
        'dataDateOverridden': v.data_date_overridden, 'dataDateSource': v.data_date_source or None,
        'dataDateConfidence': v.data_date_confidence or None,
        'p6ProjectId': v.project_id_in_file or None, 'p6ProjectName': v.project_name_in_file or None,
        'classification': v.schedule_classification, 'uploadedAt': v.upload_timestamp.isoformat(),
        'activityCount': v.activity_count, 'relationshipCount': v.relationship_count,
        'calendarCount': v.calendar_count, 'milestoneCount': v.milestone_count,
        'plannedStart': v.planned_start.isoformat() if v.planned_start else None,
        'projectFinish': v.forecast_finish.isoformat() if v.forecast_finish else None,
        'parserVersion': v.parser_version or None, 'importMethod': v.import_method or None,
    }


def build_difference_report(a, b, sample: int = DEFAULT_SAMPLE) -> Dict[str, Any]:
    acts_a, acts_b = a.activities_json or [], b.activities_json or []
    ma, mb = _meta(a), _meta(b)
    meta_diff = [{'field': k, 'a': ma[k], 'b': mb[k]} for k in ma
                 if k not in ('versionId', 'projectId', 'projectName') and ma[k] != mb[k]]

    by_a = {_act_id(x): x for x in acts_a if _act_id(x)}
    by_b = {_act_id(x): x for x in acts_b if _act_id(x)}
    added = [i for i in by_b if i not in by_a]            # in B, not in A
    removed = [i for i in by_a if i not in by_b]          # in A, not in B

    cmp_res = compare_activities(acts_a, acts_b)
    changes: List[dict] = list(cmp_res['changes'])

    # CPM / remaining dates and secondary constraint, compared the same way.
    for aid in by_a.keys() & by_b.keys():
        pa, pb = by_a[aid], by_b[aid]
        for key, label in _EXTRA_DATE_FIELDS:
            da, db = _pd(pa.get(key)), _pd(pb.get(key))
            if da != db:
                changes.append({'activityId': aid, 'activityName': pb.get('name') or pa.get('name') or '', 'field': label,
                                'fieldKey': key, 'category': 'dates', 'previous': da.isoformat() if da else None,
                                'current': db.isoformat() if db else None,
                                'deltaDays': (db - da).days if (da and db) else None})
        for key, label in _EXTRA_CONSTRAINT_FIELDS:
            va, vb = pa.get(key) or None, pb.get(key) or None
            if va != vb:
                changes.append({'activityId': aid, 'activityName': pb.get('name') or pa.get('name') or '', 'field': label,
                                'fieldKey': key, 'category': 'constraints', 'previous': va, 'current': vb})

    def cat(c):
        return _CATEGORY_OF.get(c['fieldKey']) or (c.get('category') if c.get('category') in ('dates', 'constraints') else 'other')

    buckets: Dict[str, List[dict]] = {k: [] for k in ('dates', 'durations', 'float', 'constraints', 'progress', 'other')}
    for c in changes:
        buckets[cat(c)].append(c)

    def magnitude(c):
        return abs(c.get('deltaDays') if c.get('deltaDays') is not None else (c.get('delta') or 0))

    def summarize(items: List[dict]) -> Dict[str, Any]:
        by_field: Dict[str, int] = {}
        for c in items:
            by_field[c['field']] = by_field.get(c['field'], 0) + 1
        top = sorted(items, key=lambda c: (-magnitude(c), c['activityId'], c['fieldKey']))[:sample]
        return {'changeCount': len(items), 'activitiesAffected': len({c['activityId'] for c in items}),
                'byField': dict(sorted(by_field.items())),
                'largest': [{k: c.get(k) for k in ('activityId', 'activityName', 'field', 'previous', 'current', 'delta', 'deltaDays')}
                            for c in top]}

    rel = compare_relationships(acts_a, acts_b)
    identity = schedule_identity.evaluate_schedule_identity(
        acts_a, ma['p6ProjectId'], ma['p6ProjectName'], acts_b, mb['p6ProjectId'], mb['p6ProjectName'])

    def names(ids, src):
        return [{'activityId': i, 'activityName': src[i].get('name') or ''} for i in sorted(ids)[:sample]]

    common = len(by_a.keys() & by_b.keys())
    activities_changed = len({c['activityId'] for c in changes})
    summary_lines = [
        f"A: {ma['filename']} ({ma['activityCount']} activities, Data Date {ma['dataDate']}, P6 {ma['p6ProjectId']}); "
        f"B: {mb['filename']} ({mb['activityCount']} activities, Data Date {mb['dataDate']}, P6 {mb['p6ProjectId']}).",
        f'{len(added)} activities only in B, {len(removed)} only in A, {common} in both; {activities_changed} of the common activities differ in at least one tracked field.',
        f"Relationships: {rel['addedCount']} added in B, {rel['removedCount']} removed, {rel['changedCount']} changed (type/lag).",
        f"Identity: {identity['classification']} (Activity-ID overlap {round(identity['signals']['activityIdOverlap']['jaccard'] or 0, 3)}).",
    ]
    return {
        'engineVersion': ENGINE_VERSION, 'readOnly': True, 'sampleLimit': sample,
        'versionA': ma, 'versionB': mb,
        'sameContent': not (added or removed or changes or rel['addedCount'] or rel['removedCount'] or rel['changedCount']),
        'metadata': {'differences': meta_diff},
        'activities': {
            'commonCount': common, 'addedInB': {'count': len(added), 'sample': names(added, by_b)},
            'removedFromA': {'count': len(removed), 'sample': names(removed, by_a)},
            'changedActivityCount': activities_changed,
        },
        'dates': summarize(buckets['dates']), 'durations': summarize(buckets['durations']),
        'float': summarize(buckets['float']), 'constraints': summarize(buckets['constraints']),
        'progress': summarize(buckets['progress']), 'otherFields': summarize(buckets['other']),
        'relationships': {
            'addedInB': rel['addedCount'], 'removedFromA': rel['removedCount'], 'changed': rel['changedCount'],
            'sample': {'added': rel['added'][:sample], 'removed': rel['removed'][:sample], 'changed': rel['changed'][:sample]},
        },
        'identity': {'classification': identity['classification'], 'signals': identity['signals'], 'explanation': identity['explanation']},
        'summary': summary_lines,
    }
