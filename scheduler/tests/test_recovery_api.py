import copy
import json
from datetime import date

from django.test import TestCase

from scheduler.models import Project, RecoveryScenario, ScheduleUpload
from .fixtures import make_activity


def _chain():
    a1 = make_activity('A1', dur=10.0, predecessors=[], successors=[{'actId': 'A2', 'relType': 'FS', 'lagDays': 0}])
    a2 = make_activity('A2', dur=10.0, predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}], successors=[])
    return [a1, a2]


class RecoveryScenarioApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Recovery API Test Project')
        self.version = ScheduleUpload.objects.create(
            project=self.project, original_filename='curr.xer', file_type='XER',
            data_date=date(2026, 1, 1), activities_json=_chain(),
            # forecast_finish set deliberately, and to a DIFFERENT date than the
            # real CPM finish (2026-01-21) would compute — reproduces the bug
            # where the view compared a CPM scenario finish against this naive
            # stored metric and reported a spurious "recovery" on zero actions.
            forecast_finish=date(2026, 2, 1),
        )

    def test_zero_action_scenario_reports_zero_recovery_even_with_stale_forecast_finish(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/recovery-scenarios/',
            data=json.dumps({'scheduleUploadId': str(self.version.id), 'assumptions': []}),
            content_type='application/json',
        )
        body = resp.json()
        self.assertEqual(body['result']['currentForecastFinish'], body['result']['scenarioForecastFinish'])
        self.assertEqual(body['result']['recoveryDays'], 0)

    def test_create_scenario_computes_recovery(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/recovery-scenarios/',
            data=json.dumps({
                'name': 'Cut A1 duration',
                'scheduleUploadId': str(self.version.id),
                'assumptions': [{'type': 'reduce_duration', 'activityId': 'A1', 'newDuration': 5}],
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual(body['result']['recoveryDays'], 5)
        self.assertIn('disclaimer', body['result'])

    def test_scenario_never_modifies_source_schedule_upload(self):
        original_snapshot = copy.deepcopy(self.version.activities_json)
        self.client.post(
            f'/api/projects/{self.project.id}/recovery-scenarios/',
            data=json.dumps({
                'scheduleUploadId': str(self.version.id),
                'assumptions': [{'type': 'reduce_duration', 'activityId': 'A1', 'newDuration': 1}],
            }),
            content_type='application/json',
        )
        self.version.refresh_from_db()
        self.assertEqual(self.version.activities_json, original_snapshot)

    def test_invalid_activity_id_handled_without_error(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/recovery-scenarios/',
            data=json.dumps({
                'scheduleUploadId': str(self.version.id),
                'assumptions': [{'type': 'reduce_duration', 'activityId': 'NOPE', 'newDuration': 1}],
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.json()['result']['warnings'])

    def test_list_scenarios(self):
        RecoveryScenario.objects.create(project=self.project, schedule_upload=self.version, name='S1')
        resp = self.client.get(f'/api/projects/{self.project.id}/recovery-scenarios/')
        self.assertEqual(len(resp.json()['scenarios']), 1)

    def test_update_assumptions_recalculates(self):
        create_resp = self.client.post(
            f'/api/projects/{self.project.id}/recovery-scenarios/',
            data=json.dumps({'scheduleUploadId': str(self.version.id), 'assumptions': []}),
            content_type='application/json',
        )
        scenario_id = create_resp.json()['id']
        self.assertEqual(create_resp.json()['result']['recoveryDays'], 0)

        patch_resp = self.client.patch(
            f'/api/projects/{self.project.id}/recovery-scenarios/{scenario_id}/',
            data=json.dumps({'assumptions': [{'type': 'reduce_duration', 'activityId': 'A2', 'newDuration': 4}]}),
            content_type='application/json',
        )
        self.assertEqual(patch_resp.json()['result']['recoveryDays'], 6)

    def test_reset_scenario_to_empty_assumptions(self):
        create_resp = self.client.post(
            f'/api/projects/{self.project.id}/recovery-scenarios/',
            data=json.dumps({
                'scheduleUploadId': str(self.version.id),
                'assumptions': [{'type': 'reduce_duration', 'activityId': 'A1', 'newDuration': 2}],
            }),
            content_type='application/json',
        )
        scenario_id = create_resp.json()['id']
        self.assertGreater(create_resp.json()['result']['recoveryDays'], 0)

        reset_resp = self.client.patch(
            f'/api/projects/{self.project.id}/recovery-scenarios/{scenario_id}/',
            data=json.dumps({'assumptions': []}),
            content_type='application/json',
        )
        self.assertEqual(reset_resp.json()['result']['recoveryDays'], 0)

    def test_delete_scenario(self):
        scenario = RecoveryScenario.objects.create(project=self.project, schedule_upload=self.version, name='ToDelete')
        resp = self.client.delete(f'/api/projects/{self.project.id}/recovery-scenarios/{scenario.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(RecoveryScenario.objects.filter(pk=scenario.id).exists())

    def test_scenario_not_found_returns_404(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/recovery-scenarios/00000000-0000-0000-0000-000000000000/')
        self.assertEqual(resp.status_code, 404)

    def test_schedule_upload_from_other_project_rejected(self):
        other_project = Project.objects.create(name='Other Project')
        other_version = ScheduleUpload.objects.create(
            project=other_project, original_filename='other.xer', file_type='XER', activities_json=_chain(),
        )
        resp = self.client.post(
            f'/api/projects/{self.project.id}/recovery-scenarios/',
            data=json.dumps({'scheduleUploadId': str(other_version.id), 'assumptions': []}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)
