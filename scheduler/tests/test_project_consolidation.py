"""
Sync Intelligence consolidation DRY-RUN - reproduces the reported defect
(a version is offered in the selector but Risk & Recovery answers "No
schedule version available for this project") and pins the planner's
safety rules. The planner never writes; nothing here deletes anything.
"""
import json
from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from scheduler import project_consolidation as pc
from scheduler.models import (
    MitigationAction, Project, ProjectControlsReport, RecoveryScenario, ScheduleRisk, ScheduleUpload,
)
from .fixtures import make_activity

NOW = timezone.now()


def acts(prefix, n=30, tf=5.0, extra=None, **kw):
    out = [make_activity(f'{prefix}{i}', total_float=tf, **kw) for i in range(n)]
    for a in out:
        a.update(extra or {})
    return out


def version(project, label, dd, hours_ago, activities, cls='CURRENT_UPDATE', p6='100', p6name='Barn'):
    return ScheduleUpload.objects.create(
        project=project, original_filename=f'{label}.xer', sanitized_filename=f'{label}.xer', file_type='XER',
        version_label=label, schedule_classification=cls, data_date=dd, activities_json=activities,
        activity_count=len(activities), upload_timestamp=NOW - timedelta(hours=hours_ago),
        project_id_in_file=p6, project_name_in_file=p6name,
    )


class _Base(TestCase):
    """Barn-like history: Jan -> Feb -> Mar updates of ONE schedule, each import
    made into its own Project, Mar imported twice (exact copy) + an unrelated schedule."""

    def setUp(self):
        self.pa = Project.objects.create(name='Barn Jan')
        self.pb = Project.objects.create(name='Barn Feb')
        self.pc = Project.objects.create(name='Barn Mar')
        self.pc2 = Project.objects.create(name='Barn Mar (1)')
        self.pz = Project.objects.create(name='Other Job')
        self.jan = version(self.pa, 'Jan', date(2026, 1, 15), 500, acts('A'), p6='1')
        self.feb = version(self.pb, 'Feb', date(2026, 2, 15), 400, acts('A', tf=2.0), p6='2')
        self.mar = version(self.pc, 'Mar', date(2026, 3, 15), 300, acts('A', tf=-3.0), p6='3')
        # Same Mar file imported again LATER by newer parser code (extra provenance key only).
        self.mar_dup = version(self.pc2, 'Mar', date(2026, 3, 15), 100, acts('A', tf=-3.0, extra={'origDurSource': 'IMPORTED'}), p6='3')
        self.other = version(self.pz, 'Other', date(2026, 3, 1), 200, acts('Z'), p6='900', p6name='Warehouse')

    def snapshot(self):
        return (Project.objects.count(), ScheduleUpload.objects.count(),
                sorted((str(v.id), str(v.project_id), v.data_date, v.schedule_classification) for v in ScheduleUpload.objects.all()))

    def plan(self, **kw):
        return pc.build_consolidation_plan(**kw)

    def row(self, plan, version_id):
        return next(r for r in plan['rows'] if r['versionId'] == str(version_id))


class ReportedDefectTests(_Base):
    def test_version_from_a_duplicate_project_is_refused_by_a_different_project_reproduces_the_error(self):
        # The selector shows Feb as the version for one project while the data request
        # goes out against another project - exactly the "valid-looking selection, no data" state.
        resp = self.client.get(f'/api/projects/{self.pa.id}/risk/?version={self.feb.id}')
        self.assertGreaterEqual(resp.status_code, 400)
        self.assertIn('No schedule version available', resp.content.decode())

    def test_after_reconciliation_the_same_selection_resolves_under_the_canonical_project(self):
        plan = self.plan(name_contains='barn')
        canon = next(l for l in plan['lineages'] if len(l['versions']) > 1)['canonicalProject']['projectId']
        val = pc.validate_plan_with_rollback(plan)
        self.assertTrue(val['rolledBack'])
        self.assertTrue(val['allRetainedVersionsResolve'])
        risk_checks = [r for r in val['results'] if r['endpoint'] == 'risk']
        self.assertEqual(len(risk_checks), 3)                                     # Jan, Feb, Mar all resolve
        self.assertTrue(all(r['httpStatus'] == 200 and not r['noVersionError'] for r in risk_checks))
        self.assertTrue(canon)

    def test_version_switching_analyzes_the_explicitly_selected_version_not_current(self):
        plan = self.plan(name_contains='barn')
        val = pc.validate_plan_with_rollback(plan)
        aa = {r['dataDate']: r for r in val['results'] if r['endpoint'] == 'activity-analysis'}
        self.assertEqual(set(aa), {'2026-01-15', '2026-02-15', '2026-03-15'})
        self.assertTrue(all(r['analyzedExactlySelectedVersion'] for r in aa.values()))   # Jan analyzed as Jan even though Mar is CURRENT

    def test_no_cross_project_version_leakage(self):
        plan = self.plan()                                # includes the unrelated Other Job
        val = pc.validate_plan_with_rollback(plan)
        self.assertFalse(val['crossProjectLeakage'])
        # ... and directly: the unrelated project's version cannot be read through the Barn project.
        resp = self.client.get(f'/api/projects/{self.pa.id}/risk/?version={self.other.id}')
        self.assertGreaterEqual(resp.status_code, 400)

    def test_unavailable_comparison_keeps_an_explanatory_state_not_invented_data(self):
        val = pc.validate_plan_with_rollback(self.plan(name_contains='barn'))
        jan_register = next(r for r in val['results'] if r['dataDate'] == '2026-01-15' and r['endpoint'] == 'risk-register')
        self.assertFalse(jan_register['available'])                # the oldest version has no Previous
        self.assertTrue(jan_register['unavailableReason'])
        self.assertTrue(val['everyEndpointAnalyzedTheSelectedVersionOrExplainedUnavailable'])


class DryRunSafetyTests(_Base):
    def test_planning_writes_nothing(self):
        before = self.snapshot()
        plan = self.plan(name_contains='barn')
        self.assertEqual(plan['writesPerformed'], 0)
        self.assertTrue(plan['dryRun'])
        self.assertEqual(self.snapshot(), before)

    def test_validation_simulation_rolls_back_completely(self):
        before = self.snapshot()
        pc.validate_plan_with_rollback(self.plan(name_contains='barn'))
        self.assertEqual(self.snapshot(), before)                  # nothing repointed, nothing deleted

    def test_second_dry_run_is_identical(self):
        a, b = self.plan(name_contains='barn'), self.plan(name_contains='barn')
        self.assertEqual(a['planFingerprint'], b['planFingerprint'])
        self.assertEqual(a, b)

    def test_endpoint_is_read_only_even_if_asked_to_apply(self):
        before = self.snapshot()
        resp = self.client.post('/api/projects/consolidation-plan/',
                                data=json.dumps({'nameContains': 'barn', 'apply': True, 'delete': True, 'validate': True}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['writesPerformed'], 0)
        self.assertIn('validation', body)
        self.assertEqual(self.snapshot(), before)


class PlanClassificationTests(_Base):
    def test_history_groups_into_one_lineage_by_identity_not_name(self):
        plan = self.plan()
        by_lineage = {}
        for r in plan['rows']:
            by_lineage.setdefault(r['lineageId'], set()).add(r['displayName'])
        barn = next(v for v in by_lineage.values() if 'Barn Jan' in v)
        self.assertEqual(barn, {'Barn Jan', 'Barn Feb', 'Barn Mar', 'Barn Mar (1)'})
        self.assertEqual(self.row(plan, self.other.id)['status'], 'KEEP')              # unrelated schedule untouched
        self.assertNotEqual(self.row(plan, self.other.id)['lineageId'], self.row(plan, self.jan.id)['lineageId'])

    def test_filename_alone_never_merges_schedules(self):
        p = Project.objects.create(name='Barn Warehouse Lookalike')
        lookalike = version(p, 'Barn lookalike', date(2026, 3, 20), 50, acts('Q'), p6='777', p6name='Barn')   # "Barn" name, different activities
        plan = self.plan(name_contains='barn')
        self.assertNotEqual(self.row(plan, lookalike.id)['lineageId'], self.row(plan, self.jan.id)['lineageId'])

    def test_exact_copy_is_a_delete_candidate_but_a_copy_always_remains(self):
        plan = self.plan(name_contains='barn')
        statuses = {self.mar.id: self.row(plan, self.mar.id), self.mar_dup.id: self.row(plan, self.mar_dup.id)}
        kept = [r for r in statuses.values() if r['status'] == 'KEEP']
        dups = [r for r in statuses.values() if r['status'] == 'DELETE CANDIDATE']
        self.assertEqual((len(kept), len(dups)), (1, 1))
        self.assertEqual(dups[0]['duplicateOf'], kept[0]['versionId'])

    def test_parser_provenance_field_does_not_make_copies_look_different(self):
        self.assertEqual(pc._fingerprint(self.mar.activities_json), pc._fingerprint(self.mar_dup.activities_json))
        changed = acts('A', tf=-3.0)
        changed[0]['totalFloat'] = 99.0
        self.assertNotEqual(pc._fingerprint(self.mar.activities_json), pc._fingerprint(changed))

    def test_only_version_of_a_schedule_is_never_a_delete_candidate(self):
        plan = self.plan()
        for vid in (self.jan.id, self.feb.id, self.other.id):
            self.assertNotEqual(self.row(plan, vid)['status'], 'DELETE CANDIDATE')

    def test_versions_move_to_one_canonical_project_keeping_data_dates_and_classification(self):
        plan = self.plan(name_contains='barn')
        lin = next(l for l in plan['lineages'] if len(l['versions']) > 1)
        canon = lin['canonicalProject']['projectId']
        moved = [e for e in lin['versions'] if e['moveToCanonical']]
        self.assertTrue(moved)
        for e in lin['versions']:
            v = ScheduleUpload.objects.get(pk=e['versionId'])
            self.assertEqual(e['dataDate'], v.data_date.isoformat())          # Data Date preserved
            self.assertEqual(e['classification'], v.schedule_classification)   # classification preserved
        self.assertEqual({r['backendProjectId'] for r in plan['rows'] if r['moveToCanonical']} & {canon}, set())

    def test_no_baseline_is_invented(self):
        lin = next(l for l in self.plan(name_contains='barn')['lineages'] if len(l['versions']) > 1)
        self.assertFalse(lin['baselineDesignated'])
        self.assertIsNone(lin['proposedRoles']['baselineVersionId'])
        self.assertIn('designate', lin['baselineNote'])

    def test_designated_baseline_is_preserved_separately(self):
        ScheduleUpload.objects.filter(pk=self.jan.pk).update(schedule_classification='APPROVED_BASELINE')
        lin = next(l for l in self.plan(name_contains='barn')['lineages'] if len(l['versions']) > 1)
        self.assertEqual(lin['proposedRoles']['baselineVersionId'], str(self.jan.id))
        self.assertEqual(lin['proposedRoles']['currentVersionId'], next(e['versionId'] for e in lin['versions'] if e['action'] == 'RETAIN' and e['dataDate'] == '2026-03-15'))

    def test_two_different_files_for_one_data_date_are_review_never_auto_merged_or_deleted(self):
        p = Project.objects.create(name='Barn Mar alt')
        alt_acts = acts('A', tf=-3.0)
        alt_acts.append(make_activity('EXTRA1', total_float=1.0))
        alt = version(p, 'Mar alt', date(2026, 3, 15), 250, alt_acts, p6='33')
        plan = self.plan(name_contains='barn')
        self.assertEqual(self.row(plan, alt.id)['status'], 'REVIEW')
        # The two March imports of the SAME file are still exact copies of each other: one is held for review,
        # the other is only a duplicate of that held copy (a copy remains).
        mar_rows = [self.row(plan, v.id) for v in (self.mar, self.mar_dup)]
        self.assertEqual(sorted(r['status'] for r in mar_rows), ['DELETE CANDIDATE', 'REVIEW'])
        lin = next(l for l in plan['lineages'] if len(l['versions']) > 1)
        self.assertEqual(len(lin['pendingDecisions']), 1)
        self.assertEqual(len(lin['pendingDecisions'][0]['candidates']), 2)

    def test_older_data_date_uploaded_last_is_retained_as_history_and_never_becomes_current(self):
        p = Project.objects.create(name='Barn Old But Uploaded Last')
        late_old = version(p, 'Dec', date(2025, 12, 15), 1, acts('A', tf=9.0), p6='0')   # imported AFTER everything else
        plan = self.plan(name_contains='barn')
        self.assertEqual(self.row(plan, late_old.id)['status'], 'KEEP')                  # kept as a historical version
        lin = next(l for l in plan['lineages'] if len(l['versions']) > 1)
        self.assertEqual(lin['proposedRoles']['currentVersionId'],
                         next(e['versionId'] for e in lin['versions'] if e['action'] == 'RETAIN' and e['dataDate'] == '2026-03-15'))
        self.assertEqual([c['dataDate'] for c in lin['retainedChain']][0], '2025-12-15')   # chronology by Data Date
        self.assertEqual(self.row(plan, late_old.id)['proposedRole'], 'OTHER')

    def test_the_copy_the_main_app_holds_is_the_one_retained(self):
        files = [{'projectId': str(self.pc.id), 'scheduleUploadId': str(self.mar.id), 'name': 'Mar'}]
        plan = self.plan(name_contains='barn', app_files=files)
        self.assertEqual(self.row(plan, self.mar.id)['status'], 'KEEP')
        self.assertEqual(self.row(plan, self.mar_dup.id)['status'], 'DELETE CANDIDATE')
        self.assertEqual(self.row(plan, self.mar.id)['frontendScheduleUploadId'], str(self.mar.id))
        self.assertTrue(plan['frontendStateProvided'])


class ConflictResolutionByMainAppTests(_Base):
    def setUp(self):
        super().setUp()
        self.pm = Project.objects.create(name='Barn Feb alt')
        alt = acts('A', tf=2.0)
        alt.append(make_activity('FEB_EXTRA', total_float=1.0))
        alt[3]['totalFloat'] = 40.0
        self.feb_alt = version(self.pm, 'Feb alt', date(2026, 2, 15), 380, alt, p6='22')

    def test_without_app_evidence_both_files_stay_review_and_neither_is_deleted(self):
        plan = self.plan(name_contains='barn')
        for v in (self.feb, self.feb_alt):
            r = self.row(plan, v.id)
            self.assertEqual((r['status'], r['action']), ('REVIEW', 'HOLD'))
        lin = next(l for l in plan['lineages'] if len(l['versions']) > 1)
        self.assertEqual(len(lin['pendingDecisions']), 1)
        self.assertFalse(lin['pendingDecisions'][0]['resolvedByMainAppReference'])

    def test_the_file_the_main_app_holds_is_kept_the_other_stays_review_never_delete(self):
        files = [{'projectId': str(self.pb.id), 'scheduleUploadId': str(self.feb.id), 'name': 'Feb'}]
        plan = self.plan(name_contains='barn', app_files=files)
        self.assertEqual(self.row(plan, self.feb.id)['status'], 'KEEP')
        other = self.row(plan, self.feb_alt.id)
        self.assertEqual((other['status'], other['action']), ('REVIEW', 'HOLD'))
        self.assertNotEqual(other['status'], 'DELETE CANDIDATE')
        self.assertIn('differences', other['reason'])
        lin = next(l for l in plan['lineages'] if len(l['versions']) > 1)
        self.assertTrue(lin['pendingDecisions'][0]['resolvedByMainAppReference'])

    def test_difference_report_is_attached_to_the_conflict(self):
        pd = next(l for l in self.plan(name_contains='barn')['lineages'] if len(l['versions']) > 1)['pendingDecisions'][0]
        rep = pd['differenceReport']
        self.assertTrue(rep['readOnly'])
        self.assertEqual(rep['activities']['addedInB']['count'] + rep['activities']['removedFromA']['count'], 1)
        self.assertGreaterEqual(rep['float']['changeCount'], 1)

    def test_review_versions_move_into_canonical_so_the_conflict_is_visible_to_role_resolution(self):
        plan = self.plan(name_contains='barn')
        lin = next(l for l in plan['lineages'] if len(l['versions']) > 1)
        canon = lin['canonicalProject']['projectId']
        for v in (self.feb, self.feb_alt):
            r = self.row(plan, v.id)
            self.assertTrue(r['moveToCanonical'] or r['backendProjectId'] == canon)
        # Previous of the Mar CURRENT is the conflicted Feb, so it must be unresolved - never Jan.
        self.assertIsNone(lin['proposedRoles']['previousVersionId'])
        self.assertIn('Previous version unresolved', lin['previousUnresolved']['reason'])

    def test_validation_confirms_unresolved_previous_is_reported_not_hidden(self):
        val = pc.validate_plan_with_rollback(self.plan(name_contains='barn'))
        chk = next(r for r in val['results'] if r['endpoint'] == 'previous-unresolved reporting')
        self.assertTrue(chk['previousUnresolvedReported'])
        self.assertTrue(chk['noPreviousRoleAssigned'])
        self.assertTrue(val['allRetainedVersionsResolve'])


class ReferenceRepointTests(_Base):
    def test_references_to_a_duplicate_are_listed_as_repoints_to_the_retained_twin(self):
        # Attach records to BOTH copies of March; whichever copy the plan marks as the duplicate must report them.
        for v, proj in ((self.mar, self.pc), (self.mar_dup, self.pc2)):
            RecoveryScenario.objects.create(project=proj, schedule_upload=v, name=f'on {proj.name}')
            ProjectControlsReport.objects.create(project=proj, schedule_upload=v, report_type='WEEKLY_PROJECT_CONTROLS')
        for proj in (self.pa, self.pb, self.pc, self.pc2):       # whichever becomes canonical, the others are sources
            MitigationAction.objects.create(project=proj, description='act')
        plan = self.plan(name_contains='barn')
        dup_id = next(str(v.id) for v in (self.mar, self.mar_dup) if self.row(plan, v.id)['status'] == 'DELETE CANDIDATE')
        twin = next(str(v.id) for v in (self.mar, self.mar_dup) if str(v.id) != dup_id)
        models = {(r['model'], r['action']) for r in plan['references']}
        self.assertIn(('RecoveryScenario', 'REPOINT'), models)
        self.assertIn(('ProjectControlsReport', 'REPOINT'), models)
        self.assertIn(('MitigationAction', 'REPOINT'), models)
        scen = next(r for r in plan['references'] if r['model'] == 'RecoveryScenario' and r['scope'] == 'version')
        self.assertEqual(scen['from']['versionId'], dup_id)
        self.assertEqual(scen['to']['versionId'], twin)
        self.assertEqual(RecoveryScenario.objects.count(), 2)                     # plan changed nothing

    def test_duplicate_risk_keys_are_flagged_as_merge_conflicts_not_overwritten(self):
        ScheduleRisk.objects.create(project=self.pa, risk_key='A1', owner='alice')
        ScheduleRisk.objects.create(project=self.pb, risk_key='A1', owner='bob')
        plan = self.plan(name_contains='barn')
        conflict = [r for r in plan['references'] if r['action'] == 'MERGE_CONFLICT']
        self.assertTrue(conflict)
        self.assertEqual(ScheduleRisk.objects.filter(risk_key='A1').count(), 2)
        self.assertEqual({r.owner for r in ScheduleRisk.objects.filter(risk_key='A1')}, {'alice', 'bob'})

    def test_would_be_empty_projects_are_delete_candidates_the_canonical_is_kept(self):
        plan = self.plan(name_contains='barn')
        by_name = {p['name']: p for p in plan['projects']}
        canon_id = next(l for l in plan['lineages'] if len(l['versions']) > 1)['canonicalProject']['projectId']
        self.assertEqual(next(p for p in plan['projects'] if p['projectId'] == canon_id)['status'], 'KEEP')
        empties = [p for p in plan['projects'] if p['status'] == 'DELETE CANDIDATE']
        self.assertTrue(all(p['versionsAfter'] == 0 for p in empties))
        self.assertTrue(by_name)
