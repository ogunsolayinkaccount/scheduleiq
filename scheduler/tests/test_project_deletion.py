"""
Intelligence follows the main Project/ScheduleUpload records — there is no
separate Intelligence-owned project list. These tests pin the deletion
lifecycle: DELETE project / DELETE version, role re-resolution, and that no
analytical endpoint can retrieve a deleted project or version.
"""
from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from scheduler.ai import NullProvider
from scheduler.models import (
    MilestoneDefinition, MitigationAction, Project, ProjectControlsReport, RecoveryScenario,
    ScheduleRisk, ScheduleUpload,
)


def _act(code, tf=0.0, **extra):
    d = {
        'id': code, 'code': code, 'name': f'Activity {code}', 'wbs': 'W', 'area': 'Area A', 'discipline': 'ELE',
        'bStart': '2026-01-01', 'bFinish': '2026-01-20', 'dur': 20.0, 'remainDur': 20.0, 'pctComplete': 0.0,
        'totalFloat': tf, 'isCritical': False, 'isMilestone': False, 'predecessors': [], 'successors': [],
    }
    d.update(extra)
    return d


class _Base(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Barn — BLDG 2 COMP 1')
        now = timezone.now()

        def mk(label, classification, dd, days_ago, tf):
            return ScheduleUpload.objects.create(
                project=self.project, original_filename=f'{label}.xer', sanitized_filename=f'{label}.xer',
                file_type='XER', version_label=label, schedule_classification=classification,
                data_date=dd, activities_json=[_act('A1', tf=tf)], upload_timestamp=now - timedelta(days=days_ago),
            )

        self.baseline = mk('Baseline', 'APPROVED_BASELINE', date(2026, 1, 1), 40, 20.0)
        self.u1 = mk('11-Aug-26 Update', 'CURRENT_UPDATE', date(2026, 8, 11), 30, 12.0)
        self.u2 = mk('19-Aug-26 Update', 'CURRENT_UPDATE', date(2026, 8, 19), 20, 6.0)
        self.u3 = mk('26-Aug-26 Update', 'CURRENT_UPDATE', date(2026, 8, 26), 10, -2.0)

    def project_list(self, with_versions=True):
        url = '/api/projects/' + ('?withVersions=true' if with_versions else '')
        return self.client.get(url).json()['projects']

    def versions(self):
        return self.client.get(f'/api/projects/{self.project.id}/versions/').json()['versions']

    def delete_version(self, v):
        return self.client.delete(f'/api/projects/{self.project.id}/versions/{v.id}/')


class ProjectLifecycleTests(_Base):
    def test_new_project_appears_in_intelligence_project_list_and_analysis(self):
        ids = [p['id'] for p in self.project_list()]
        self.assertIn(str(self.project.id), ids)
        resp = self.client.get(f'/api/projects/{self.project.id}/activity-analysis/')
        self.assertEqual(resp.status_code, 200)

    def test_delete_project_removes_it_everywhere(self):
        resp = self.client.delete(f'/api/projects/{self.project.id}/')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['deleted'])
        self.assertEqual(body['removed']['scheduleVersions'], 4)
        self.assertNotIn(str(self.project.id), [p['id'] for p in self.project_list()])
        self.assertNotIn(str(self.project.id), [p['id'] for p in self.project_list(with_versions=False)])
        self.assertEqual(self.client.get(f'/api/projects/{self.project.id}/').status_code, 404)
        self.assertEqual(ScheduleUpload.objects.filter(project_id=self.project.id).count(), 0)

    def test_deleted_project_unreachable_from_every_analysis_endpoint(self):
        pid = self.project.id
        self.client.delete(f'/api/projects/{pid}/')
        for path in ('activity-analysis/', 'float-analysis/', 'float-trend/?activityId=A1', 'update-intelligence/',
                     'baseline-progress/', 'risk-register/', 'milestones/', 'lookahead/', 'recovery-scenarios/'):
            resp = self.client.get(f'/api/projects/{pid}/{path}')
            self.assertEqual(resp.status_code, 404, path)
        with patch('scheduler.views.get_provider', return_value=NullProvider()):
            self.assertEqual(self.client.get(f'/api/projects/{pid}/ai-review/').status_code, 404)
            resp = self.client.post(f'/api/projects/{pid}/ai-chat/', data='{"question":"x"}', content_type='application/json')
            self.assertEqual(resp.status_code, 404)

    def test_delete_unknown_project_is_404(self):
        self.assertEqual(self.client.delete('/api/projects/00000000-0000-0000-0000-000000000000/').status_code, 404)

    def test_project_delete_leaves_no_orphaned_dependents(self):
        scenario = RecoveryScenario.objects.create(project=self.project, schedule_upload=self.u3, name='S')
        ScheduleRisk.objects.create(project=self.project, risk_key='A1')
        MitigationAction.objects.create(project=self.project, description='d', scenario=scenario)
        ProjectControlsReport.objects.create(project=self.project, schedule_upload=self.u3, report_type='WEEKLY_PROJECT_CONTROLS')
        MilestoneDefinition.objects.create(schedule_upload=self.u3, activity_id='M1')
        self.client.delete(f'/api/projects/{self.project.id}/')
        self.assertEqual(RecoveryScenario.objects.count(), 0)
        self.assertEqual(ScheduleRisk.objects.count(), 0)
        self.assertEqual(MitigationAction.objects.count(), 0)
        self.assertEqual(ProjectControlsReport.objects.count(), 0)
        self.assertEqual(MilestoneDefinition.objects.count(), 0)


class VersionDeletionTests(_Base):
    def test_delete_version_removes_only_that_version(self):
        resp = self.delete_version(self.u2)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['remainingVersionCount'], 3)
        self.assertFalse(body['projectNowEmpty'])
        labels = [v['versionLabel'] for v in self.versions()]
        self.assertNotIn('19-Aug-26 Update', labels)
        for survivor in ('Baseline', '11-Aug-26 Update', '26-Aug-26 Update'):
            self.assertIn(survivor, labels)
        self.assertIn(str(self.project.id), [p['id'] for p in self.project_list()])  # project survives

    def test_deleting_current_reresolves_current_from_survivors(self):
        self.assertEqual(next(v for v in self.versions() if v['role'] == 'CURRENT')['id'], str(self.u3.id))
        body = self.delete_version(self.u3).json()
        self.assertEqual(body['deletedRole'], 'CURRENT')
        self.assertEqual(body['roles']['currentVersionId'], str(self.u2.id))    # newest surviving update
        self.assertEqual(body['roles']['previousVersionId'], str(self.u1.id))
        self.assertEqual(body['roles']['baselineVersionId'], str(self.baseline.id))
        roles = [v['role'] for v in self.versions()]
        self.assertEqual(roles.count('CURRENT'), 1)  # exactly one, never invalid

    def test_deleting_previous_makes_comparison_handle_missing_previous(self):
        body = self.delete_version(self.u2).json()  # u2 was PREVIOUS
        self.assertEqual(body['deletedRole'], 'PREVIOUS')
        self.assertEqual(body['roles']['currentVersionId'], str(self.u3.id))
        self.assertEqual(body['roles']['previousVersionId'], str(self.u1.id))  # next-older update takes over
        # Explicitly asking for the deleted version as Previous yields "no previous", never a stand-in.
        resp = self.client.get(
            f'/api/projects/{self.project.id}/activity-analysis/?currentVersion={self.u3.id}&previousVersion={self.u2.id}'
        )
        self.assertEqual(resp.status_code, 200)
        row = resp.json()['rows'][0]
        self.assertIsNone(row['previousTotalFloat'])
        self.assertIsNone(resp.json()['previousVersionId'])

    def test_deleting_baseline_leaves_no_baseline_and_promotes_nothing(self):
        body = self.delete_version(self.baseline).json()
        self.assertEqual(body['deletedRole'], 'BASELINE')
        self.assertFalse(body['baselineDesignated'])
        self.assertIsNone(body['roles']['baselineVersionId'])
        self.assertNotIn('BASELINE', [v['role'] for v in self.versions()])
        resp = self.client.get(f'/api/projects/{self.project.id}/activity-analysis/')
        data = resp.json()
        self.assertIsNone(data['baselineVersionId'])
        self.assertIsNone(data['rows'][0]['baselineTotalFloat'])   # unavailable, not an arbitrary version's value
        self.assertIsNone(self.client.get(f'/api/projects/{self.project.id}/float-analysis/').json()['baselineVersionId'])

    def test_baseline_can_be_redesignated_explicitly_afterwards(self):
        self.delete_version(self.baseline)
        resp = self.client.patch(
            f'/api/projects/{self.project.id}/versions/{self.u1.id}/',
            data='{"classification":"APPROVED_BASELINE"}', content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(next(v for v in self.versions() if v['role'] == 'BASELINE')['id'], str(self.u1.id))

    def test_deleted_version_cannot_be_retrieved_by_analysis_endpoints(self):
        vid = self.u2.id
        self.delete_version(self.u2)
        base = f'/api/projects/{self.project.id}'
        for path in (f'activity-analysis/?currentVersion={vid}', f'float-analysis/?currentVersion={vid}',
                     f'update-intelligence/?currentVersion={vid}', f'milestones/?version={vid}',
                     f'lookahead/?version={vid}', f'baseline-progress/?currentVersion={vid}'):
            resp = self.client.get(f'{base}/{path}')
            self.assertIn(resp.status_code, (400, 404), f'{path} -> {resp.status_code}')

    def test_float_trend_excludes_deleted_version(self):
        self.delete_version(self.u2)
        pts = self.client.get(f'/api/projects/{self.project.id}/float-trend/?activityId=A1').json()['points']
        self.assertEqual([p['versionLabel'] for p in pts], ['Baseline', '11-Aug-26 Update', '26-Aug-26 Update'])
        self.assertEqual([p['totalFloat'] for p in pts], [20.0, 12.0, -2.0])

    def test_ai_context_cannot_retrieve_deleted_version(self):
        vid = self.u2.id
        self.delete_version(self.u2)
        with patch('scheduler.views.get_provider', return_value=NullProvider()):
            resp = self.client.post(
                f'/api/projects/{self.project.id}/ai-chat/', data=f'{{"question":"x","version":"{vid}"}}',
                content_type='application/json',
            )
            self.assertEqual(resp.status_code, 404)
            self.assertEqual(self.client.get(f'/api/projects/{self.project.id}/ai-review/?version={vid}').status_code, 404)
            # Default (no explicit version) chat still works and never mentions the deleted version.
            ok = self.client.post(
                f'/api/projects/{self.project.id}/ai-chat/', data='{"question":"x"}', content_type='application/json',
            ).json()
            labels = {ok['context']['currentVersion']['versionLabel'], (ok['context']['previousVersion'] or {}).get('versionLabel')}
            self.assertNotIn('19-Aug-26 Update', labels)

    def test_version_delete_leaves_other_versions_dependents_and_detaches_project_level_records(self):
        gone = RecoveryScenario.objects.create(project=self.project, schedule_upload=self.u2, name='on deleted')
        kept = RecoveryScenario.objects.create(project=self.project, schedule_upload=self.u3, name='on survivor')
        action = MitigationAction.objects.create(project=self.project, description='d', scenario=gone)
        MilestoneDefinition.objects.create(schedule_upload=self.u2, activity_id='M-gone')
        MilestoneDefinition.objects.create(schedule_upload=self.u3, activity_id='M-kept')
        report = ProjectControlsReport.objects.create(project=self.project, schedule_upload=self.u2, report_type='WEEKLY_PROJECT_CONTROLS')
        risk = ScheduleRisk.objects.create(project=self.project, risk_key='A1', first_identified_version=self.u2)

        self.delete_version(self.u2)

        self.assertFalse(RecoveryScenario.objects.filter(pk=gone.pk).exists())            # version-owned: removed
        self.assertTrue(RecoveryScenario.objects.filter(pk=kept.pk).exists())            # survivor untouched
        self.assertFalse(MilestoneDefinition.objects.filter(activity_id='M-gone').exists())
        self.assertTrue(MilestoneDefinition.objects.filter(activity_id='M-kept').exists())
        # Project-level records survive but no longer point at the deleted version — no dangling references.
        action.refresh_from_db(); report.refresh_from_db(); risk.refresh_from_db()
        self.assertIsNone(action.scenario)
        self.assertIsNone(report.schedule_upload)
        self.assertIsNone(risk.first_identified_version)

    def test_deleting_last_version_keeps_project_but_hides_it_from_intelligence(self):
        for v in (self.baseline, self.u1, self.u2):
            self.delete_version(v)
        body = self.delete_version(self.u3).json()
        self.assertTrue(body['projectNowEmpty'])
        self.assertNotIn(str(self.project.id), [p['id'] for p in self.project_list()])                 # Intelligence view
        self.assertIn(str(self.project.id), [p['id'] for p in self.project_list(with_versions=False)])  # management view
        self.assertEqual(self.client.delete(f'/api/projects/{self.project.id}/').status_code, 200)     # deliberately deletable

    def test_delete_unknown_version_is_404(self):
        resp = self.client.delete(f'/api/projects/{self.project.id}/versions/00000000-0000-0000-0000-000000000000/')
        self.assertEqual(resp.status_code, 404)

    def test_version_of_another_project_cannot_be_deleted_through_this_one(self):
        other = Project.objects.create(name='Other')
        foreign = ScheduleUpload.objects.create(project=other, original_filename='o.xer', file_type='XER', activities_json=[])
        resp = self.client.delete(f'/api/projects/{self.project.id}/versions/{foreign.id}/')
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(ScheduleUpload.objects.filter(pk=foreign.pk).exists())
