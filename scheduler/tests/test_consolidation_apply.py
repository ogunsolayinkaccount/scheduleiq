"""
Confirmation-gated consolidation APPLY + the version difference report.
Runs only against the test database.
"""
import json
from datetime import date
from unittest.mock import patch

from django.test import TestCase

from scheduler import project_consolidation as pc
from scheduler.models import (
    ContractualMilestoneRevision, MilestoneDefinition, MitigationAction, Project, ProjectControlsReport,
    RecoveryScenario, ScheduleRisk, ScheduleUpload,
)
from .fixtures import make_activity
from .test_project_consolidation import _Base, acts, version


class ApplyBase(_Base):
    def files(self, *versions):
        return [{'projectId': str(v.project_id), 'scheduleUploadId': str(v.id), 'name': v.version_label} for v in versions]

    def approve(self, app_files):
        plan = pc.build_consolidation_plan('barn', app_files)
        return plan, dict(nameContains='barn', mainAppFiles=app_files, expectedPlanFingerprint=plan['planFingerprint'],
                          confirmation='CONSOLIDATE')

    def post(self, payload):
        return self.client.post('/api/projects/consolidation-apply/', data=json.dumps(payload), content_type='application/json')

    def counts(self):
        return (Project.objects.count(), ScheduleUpload.objects.count(), RecoveryScenario.objects.count(),
                ProjectControlsReport.objects.count(), ScheduleRisk.objects.count())


class ApplyRefusalTests(ApplyBase):
    def test_refuses_without_the_typed_confirmation_and_changes_nothing(self):
        _, payload = self.approve(self.files(self.jan, self.feb, self.mar))
        before, snap = self.counts(), self.snapshot()
        payload.pop('confirmation')
        self.assertEqual(self.post(payload).status_code, 400)
        payload['confirmation'] = 'yes'
        self.assertEqual(self.post(payload).status_code, 400)
        self.assertEqual((self.counts(), self.snapshot()), (before, snap))

    def test_refuses_without_the_main_app_file_list(self):
        plan = pc.build_consolidation_plan('barn')
        resp = self.post(dict(nameContains='barn', expectedPlanFingerprint=plan['planFingerprint'], confirmation='CONSOLIDATE'))
        self.assertEqual(resp.status_code, 400)
        self.assertIn('mainAppFiles', resp.json()['error'])

    def test_refuses_when_the_plan_is_not_the_one_approved(self):
        _, payload = self.approve(self.files(self.jan, self.feb, self.mar))
        payload['expectedPlanFingerprint'] = 'deadbeef'
        before = self.snapshot()
        resp = self.post(payload)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_refuses_if_data_changed_after_approval(self):
        _, payload = self.approve(self.files(self.jan, self.feb, self.mar))
        Project.objects.create(name='Barn stray')
        ScheduleUpload.objects.filter(pk=self.feb.pk).update(activity_count=999)     # plan inputs changed
        resp = self.post(payload)
        self.assertEqual(resp.status_code, 409)

    def test_refuses_on_unresolved_risk_merge_conflict(self):
        ScheduleRisk.objects.create(project=self.pa, risk_key='A1', owner='a')
        ScheduleRisk.objects.create(project=self.pb, risk_key='A1', owner='b')
        _, payload = self.approve(self.files(self.jan, self.feb, self.mar))
        before = self.counts()
        self.assertEqual(self.post(payload).status_code, 409)
        self.assertEqual(self.counts(), before)

    def test_dry_run_endpoints_never_apply(self):
        before = self.snapshot()
        self.client.post('/api/projects/consolidation-plan/', data=json.dumps({'nameContains': 'barn', 'confirmation': 'CONSOLIDATE', 'apply': True}),
                         content_type='application/json')
        self.assertEqual(self.snapshot(), before)


class ApplyExecutionTests(ApplyBase):
    def setUp(self):
        super().setUp()
        self.files_ = self.files(self.jan, self.feb, self.mar)     # the main app holds Jan, Feb, Mar (not the Mar copy)
        # Records tied to the duplicate Mar copy and to a project that will be emptied.
        self.scen = RecoveryScenario.objects.create(project=self.pc2, schedule_upload=self.mar_dup, name='on dup')
        self.report = ProjectControlsReport.objects.create(project=self.pc2, schedule_upload=self.mar_dup, report_type='WEEKLY_PROJECT_CONTROLS')
        self.risk = ScheduleRisk.objects.create(project=self.pb, risk_key='A7', owner='sam', first_identified_version=self.feb)
        self.action = MitigationAction.objects.create(project=self.pa, description='keep me', scenario=None)
        # Field Dashboard's Contractual Milestone register — project-scoped
        # (schedule_upload intentionally left unset, per MilestoneDefinition's
        # own design), so it must follow the same project-level REPOINT path
        # as MitigationAction/ManualCostEntry, never get left behind on the
        # emptied source project.
        self.milestone = MilestoneDefinition.objects.create(
            project=self.pa, activity_id='MS1', description='Area B turnover',
            milestone_category='CONTRACTUAL_INTERIM', contract_required_date=date(2026, 12, 15),
        )
        self.revision = ContractualMilestoneRevision.objects.create(
            milestone=self.milestone, previous_date=date(2026, 11, 1), new_date=date(2026, 12, 15), reason='owner-approved',
        )

    def apply(self):
        plan, payload = self.approve(self.files_)
        resp = self.post(payload)
        self.assertEqual(resp.status_code, 200, resp.content)
        return plan, resp.json()

    def test_apply_consolidates_into_one_project_and_removes_only_the_duplicate(self):
        plan, res = self.apply()
        canon_id = next(l for l in plan['lineages'] if len(l['versions']) > 1)['canonicalProject']['projectId']
        self.assertEqual(ScheduleUpload.objects.filter(project_id=canon_id).count(), 3)          # Jan, Feb, Mar
        self.assertEqual({v.data_date for v in ScheduleUpload.objects.filter(project_id=canon_id)},
                         {date(2026, 1, 15), date(2026, 2, 15), date(2026, 3, 15)})
        self.assertEqual(ScheduleUpload.objects.count(), 4)                                       # 5 versions - 1 duplicate
        self.assertFalse(ScheduleUpload.objects.filter(pk=self.mar_dup.pk).exists())
        self.assertTrue(ScheduleUpload.objects.filter(pk=self.mar.pk).exists())                   # a copy always remains
        self.assertTrue(ScheduleUpload.objects.filter(pk=self.other.pk).exists())                 # unrelated schedule untouched
        self.assertEqual(Project.objects.filter(pk__in=[self.pa.pk, self.pb.pk, self.pc.pk, self.pc2.pk]).count(), 1)
        self.assertEqual(str(canon_id), str(Project.objects.get(pk=canon_id).id))

    def test_data_dates_and_classifications_are_preserved(self):
        before = {str(v.id): (v.data_date, v.schedule_classification, v.upload_timestamp) for v in ScheduleUpload.objects.exclude(pk=self.mar_dup.pk)}
        self.apply()
        after = {str(v.id): (v.data_date, v.schedule_classification, v.upload_timestamp) for v in ScheduleUpload.objects.all()}
        self.assertEqual(after, before)

    def test_references_follow_their_versions_and_nothing_dangles(self):
        plan, _ = self.apply()
        canon_id = next(l for l in plan['lineages'] if len(l['versions']) > 1)['canonicalProject']['projectId']
        self.scen.refresh_from_db(); self.report.refresh_from_db(); self.risk.refresh_from_db(); self.action.refresh_from_db()
        self.assertEqual(str(self.scen.project_id), canon_id)
        self.assertEqual(self.scen.schedule_upload_id, self.mar.id)       # repointed from the deleted copy to its twin
        self.assertEqual(str(self.report.project_id), canon_id)
        self.assertEqual(self.report.schedule_upload_id, self.mar.id)
        self.assertEqual((str(self.risk.project_id), self.risk.owner), (canon_id, 'sam'))   # workflow preserved
        self.assertEqual(str(self.action.project_id), canon_id)
        self.assertEqual(RecoveryScenario.objects.filter(schedule_upload__isnull=True).count(), 0)

    def test_contractual_milestone_register_and_its_revision_history_move_to_canonical(self):
        plan, _ = self.apply()
        canon_id = next(l for l in plan['lineages'] if len(l['versions']) > 1)['canonicalProject']['projectId']
        self.milestone.refresh_from_db()
        self.assertEqual(str(self.milestone.project_id), canon_id)
        self.assertEqual(MilestoneDefinition.objects.filter(project_id=canon_id, activity_id='MS1').count(), 1)
        # The revision never had its own project pointer — it follows its
        # parent MilestoneDefinition automatically, and nothing about its
        # own history is altered by the move.
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.previous_date, date(2026, 11, 1))
        self.assertEqual(self.revision.new_date, date(2026, 12, 15))
        self.assertEqual(self.revision.reason, 'owner-approved')
        self.assertEqual(ContractualMilestoneRevision.objects.filter(milestone__project_id=canon_id).count(), 1)

    def test_roles_after_apply_follow_data_date(self):
        plan, _ = self.apply()
        canon_id = next(l for l in plan['lineages'] if len(l['versions']) > 1)['canonicalProject']['projectId']
        roles = {v['dataDate']: v['role'] for v in self.client.get(f'/api/projects/{canon_id}/versions/').json()['versions']}
        self.assertEqual(roles['2026-03-15'], 'CURRENT')
        self.assertEqual(roles['2026-02-15'], 'PREVIOUS')
        self.assertNotIn('BASELINE', roles.values())                       # none invented

    def test_apply_is_idempotent(self):
        self.apply()
        snap = (self.counts(), self.snapshot())
        plan2 = pc.build_consolidation_plan('barn', self.files_)
        self.assertEqual(plan2['summary']['versionsDeleteCandidate'], 0)
        self.assertEqual(plan2['summary']['referencesToRepoint'], 0)
        self.assertFalse(any(r['moveToCanonical'] for r in plan2['rows']))
        resp = self.post(dict(nameContains='barn', mainAppFiles=self.files_, expectedPlanFingerprint=plan2['planFingerprint'], confirmation='CONSOLIDATE'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual((self.counts(), self.snapshot()), snap)           # second run changed nothing
        self.assertEqual(resp.json()['lineages'][0]['duplicatesDeleted'], [])

    def test_all_or_nothing_if_a_step_fails(self):
        _, payload = self.approve(self.files_)
        before, snap = self.counts(), self.snapshot()
        with patch('scheduler.views._delete_project', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                self.post(payload)
        self.assertEqual((self.counts(), self.snapshot()), (before, snap))  # rolled back completely
        self.assertTrue(ScheduleUpload.objects.filter(pk=self.mar_dup.pk).exists())

    def test_a_project_that_still_owns_records_is_never_deleted(self):
        # A saved report on the soon-empty project moves to canonical; but a record we do not migrate would keep it alive.
        with patch.object(Project, 'schedule_versions', create=True):
            pass
        self.apply()
        self.assertTrue(ProjectControlsReport.objects.filter(pk=self.report.pk).exists())


class ApplyKeepsReviewConflictsTests(ApplyBase):
    def setUp(self):
        super().setUp()
        self.pm = Project.objects.create(name='Barn Feb alt')
        alt = acts('A', tf=2.0)
        alt.append(make_activity('FEB_EXTRA', total_float=1.0))
        self.feb_alt = version(self.pm, 'Feb alt', date(2026, 2, 15), 380, alt, p6='22')

    def test_conflicting_versions_are_moved_not_deleted_and_previous_stays_unresolved(self):
        files = self.files(self.jan, self.feb, self.mar)          # main app holds Feb, not the alt
        plan, payload = self.approve(files)
        self.assertEqual(self.post(payload).status_code, 200)
        self.assertTrue(ScheduleUpload.objects.filter(pk=self.feb_alt.pk).exists())          # REVIEW version survived
        canon = next(l for l in plan['lineages'] if len(l['versions']) > 1)['canonicalProject']['projectId']
        self.assertEqual(str(ScheduleUpload.objects.get(pk=self.feb_alt.pk).project_id), canon)
        vers = {v['versionLabel']: v for v in self.client.get(f'/api/projects/{canon}/versions/').json()['versions']}
        self.assertTrue(vers['Feb']['dataDateConflict'] and vers['Feb alt']['dataDateConflict'])
        self.assertIn('Previous version unresolved', vers['Mar']['previousUnresolved']['reason'])
        self.assertNotIn('PREVIOUS', {v['role'] for v in vers.values()})


class DifferenceReportTests(TestCase):
    def setUp(self):
        from django.utils import timezone
        self.p = Project.objects.create(name='Diff')
        base = acts('A', n=10)
        other = acts('A', n=10)
        other = other[:-1] + [make_activity('NEW1', total_float=3.0)]          # A9 removed, NEW1 added
        other[0]['totalFloat'] = 30.0
        other[1]['dur'] = 99.0
        other[2]['bFinish'] = '2026-03-10'
        other[3]['constraintType'] = 'CS_MSO'
        other[4]['predecessors'] = [{'actId': 'A0', 'relType': 'FS', 'lagDays': 2}]
        self.a = version(self.p, 'A', date(2026, 8, 26), 10, base, p6='73088')
        self.b = version(self.p, 'B', date(2026, 8, 26), 5, other, p6='4642')

    def report(self, a, b):
        return self.client.post('/api/projects/version-difference-report/', data=json.dumps({'versionA': str(a.id), 'versionB': str(b.id)}),
                                content_type='application/json')

    def test_reports_every_requested_category(self):
        r = self.report(self.a, self.b).json()
        self.assertTrue(r['readOnly'])
        self.assertEqual(r['activities']['addedInB']['count'], 1)
        self.assertEqual(r['activities']['removedFromA']['count'], 1)
        self.assertGreaterEqual(r['float']['changeCount'], 1)
        self.assertGreaterEqual(r['durations']['changeCount'], 1)
        self.assertGreaterEqual(r['dates']['changeCount'], 1)
        self.assertGreaterEqual(r['constraints']['changeCount'], 1)
        self.assertGreaterEqual(r['relationships']['addedInB'] + r['relationships']['changed'], 1)
        fields = {d['field'] for d in r['metadata']['differences']}
        self.assertIn('p6ProjectId', fields)
        self.assertIn('uploadedAt', fields)
        self.assertNotIn('dataDate', fields)                       # same Data Date - not a difference
        self.assertFalse(r['sameContent'])
        self.assertTrue(r['summary'])

    def test_identical_versions_report_same_content(self):
        twin = version(self.p, 'A2', date(2026, 8, 26), 1, acts('A', n=10), p6='73088')
        self.assertTrue(self.report(self.a, twin).json()['sameContent'])

    def test_missing_version_is_404_and_nothing_is_written(self):
        before = ScheduleUpload.objects.count()
        resp = self.client.post('/api/projects/version-difference-report/', data=json.dumps({'versionA': str(self.a.id), 'versionB': 'nope'}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(ScheduleUpload.objects.count(), before)


class AbsenceNeverAuthorizesDeletionTests(ApplyBase):
    """The browser holds ONE file. Everything it does not hold stays - only exact copies of a retained twin may go."""

    def test_only_the_exact_duplicate_is_deleted_when_the_browser_holds_a_single_file(self):
        files = self.files(self.mar)                       # the reported situation: only the newest file is loaded
        plan, payload = self.approve(files)
        resp = self.post(payload)
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body['versionsDeleted'], [str(self.mar_dup.id)])
        for v in (self.jan, self.feb, self.mar, self.other):
            self.assertTrue(ScheduleUpload.objects.filter(pk=v.pk).exists(), v.version_label)   # historical schedules survive
        self.assertEqual(ScheduleUpload.objects.count(), 4)

    def test_deleted_versions_are_a_subset_of_the_plans_duplicate_allowlist(self):
        plan, payload = self.approve(self.files(self.mar))
        body = self.post(payload).json()
        self.assertTrue(set(body['versionsDeleted']) <= set(body['deletionAllowlist']))
        for vid in body['versionsRetained']:
            self.assertTrue(ScheduleUpload.objects.filter(pk=vid).exists())

    def test_refuses_to_delete_a_copy_the_main_app_itself_references(self):
        _, payload = self.approve(self.files(self.mar, self.mar_dup))     # the app holds BOTH copies
        before = self.snapshot()
        resp = self.post(payload)
        self.assertEqual(resp.status_code, 409)
        self.assertIn('references', resp.json()['error'])
        self.assertEqual(self.snapshot(), before)

    def test_a_project_not_marked_delete_candidate_is_never_removed(self):
        # 'Other Job' is unrelated and not loaded in the browser - it must survive an apply.
        _, payload = self.approve(self.files(self.mar))
        self.post(payload)
        self.assertTrue(Project.objects.filter(pk=self.pz.pk).exists())


class SimulationTests(ApplyBase):
    def simulate(self, files):
        return self.client.post('/api/projects/consolidation-simulate/',
                                data=json.dumps({'nameContains': 'barn', 'mainAppFiles': files}), content_type='application/json')

    def test_simulation_reports_the_result_and_rolls_back_completely(self):
        before = (self.counts(), self.snapshot())
        res = self.simulate(self.files(self.mar)).json()
        self.assertTrue(res['rolledBackCompletely'])
        self.assertEqual((self.counts(), self.snapshot()), before)
        self.assertEqual(len(res['before']['versions']) - len(res['after']['versions']), 1)     # exactly the one exact copy
        self.assertEqual(res['applyReport']['versionsDeleted'], [str(self.mar_dup.id)])
        self.assertTrue(all(c['httpStatus'] == 200 and c['noVersionError'] for c in res['checks']))
        self.assertTrue(all(c['analyzedSelectedVersion'] for c in res['checks'] if c['endpoint'] != 'risk-register' or c['available']))
        self.assertEqual(set(res['integrity'].values()), {0})

    def test_simulation_requires_the_main_app_file_list(self):
        resp = self.client.post('/api/projects/consolidation-simulate/', data=json.dumps({'nameContains': 'barn'}), content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_simulation_covers_ai_context_for_every_retained_version(self):
        res = self.simulate(self.files(self.mar)).json()
        ai = [c for c in res['checks'] if c['endpoint'] == 'ai-context']
        self.assertEqual(len(ai), len(res['after']['versions']))
        self.assertTrue(all(c['analyzedSelectedVersion'] for c in ai))
