"""
Schedule Risk, Recovery & Mitigation Intelligence — API layer tests.
Covers the Risk Register endpoint, risk workflow PATCH, driving-chain
endpoint, mitigation-action CRUD, and the extended recovery-scenario
endpoints (status, calendar/baseline-aware calculation, recovery tracking).
"""
import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from scheduler.models import MitigationAction, Project, RecoveryScenario, ScheduleRisk, ScheduleUpload


def _act(code, name='Activity', b_start='2026-01-01', b_finish='2026-01-10', **extra):
    d = {
        'id': code, 'code': code, 'name': name, 'wbs': 'Area A', 'discipline': 'Electrical',
        'bStart': b_start, 'bFinish': b_finish, 'dur': 9.0, 'remainDur': 9.0, 'pctComplete': 0.0,
        'totalFloat': 0.0, 'isCritical': True, 'isMilestone': False,
        'predecessors': [], 'successors': [],
    }
    d.update(extra)
    return d


class RiskRegisterApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Risk API Test')
        now = timezone.now()
        self.prev = ScheduleUpload.objects.create(
            project=self.project, original_filename='p.xer', sanitized_filename='p.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-01',
            activities_json=[_act('A1', b_finish='2026-08-10', total_float=2.0, isCritical=False)],
            upload_timestamp=now - timedelta(days=7),
        )
        self.curr = ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('A1', b_finish='2026-08-10', earlyFinish='2026-08-25', totalFloat=-14.0, isCritical=True, onLongestPath=True)],
            upload_timestamp=now,
        )

    def test_risk_register_returns_computed_risks(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/risk-register/')
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertTrue(body['available'])
        self.assertEqual(len(body['risks']), 1)
        risk = body['risks'][0]
        self.assertEqual(risk['activityId'], 'A1')
        self.assertIn('severity', risk)
        self.assertIn('riskSignals', risk)
        # Default workflow state — no ScheduleRisk row exists yet.
        self.assertEqual(risk['workflow']['status'], 'OPEN')

    def test_single_version_project_unavailable_not_error(self):
        self.curr.delete()
        resp = self.client.get(f'/api/projects/{self.project.id}/risk-register/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['available'])

    def test_workflow_state_joined_from_persisted_row(self):
        ScheduleRisk.objects.create(project=self.project, risk_key='A1', status='MITIGATION_PLANNED', owner='Electrical Sub')
        resp = self.client.get(f'/api/projects/{self.project.id}/risk-register/')
        risk = resp.json()['risks'][0]
        self.assertEqual(risk['workflow']['status'], 'MITIGATION_PLANNED')
        self.assertEqual(risk['workflow']['owner'], 'Electrical Sub')

    def test_baseline_version_label_included_for_traceability(self):
        # UI acceptance pass finding: the endpoint returned baselineVersionId
        # but no human-readable baselineVersionLabel, unlike every other
        # version-resolution endpoint in this codebase — the Risk & Recovery
        # workspace's source-traceability header had nothing to display.
        ScheduleUpload.objects.create(
            project=self.project, original_filename='bl.xer', sanitized_filename='bl.xer', file_type='XER',
            schedule_classification='APPROVED_BASELINE', data_date='2026-07-01', version_label='Approved Baseline v1',
            activities_json=[_act('A1', b_finish='2026-08-10')],
            upload_timestamp=timezone.now() - timedelta(days=30),
        )
        resp = self.client.get(f'/api/projects/{self.project.id}/risk-register/')
        body = resp.json()
        self.assertIsNotNone(body['baselineVersionId'])
        self.assertEqual(body['baselineVersionLabel'], 'Approved Baseline v1')


class RiskDetailPatchTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Risk Detail Test')
        self.version = ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15',
            activities_json=[_act('A1')],
        )

    def test_patch_creates_row_lazily_and_stamps_first_identified(self):
        resp = self.client.patch(
            f'/api/projects/{self.project.id}/risk-register/A1/',
            data=json.dumps({'status': 'UNDER_REVIEW', 'owner': 'Jane'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body['status'], 'UNDER_REVIEW')
        self.assertEqual(body['owner'], 'Jane')
        self.assertEqual(body['firstIdentifiedDataDate'], '2026-08-15')

        sr = ScheduleRisk.objects.get(project=self.project, risk_key='A1')
        self.assertEqual(sr.first_identified_version_id, self.version.id)

    def test_second_patch_does_not_rewrite_first_identified(self):
        self.client.patch(f'/api/projects/{self.project.id}/risk-register/A1/', data=json.dumps({'status': 'OPEN'}), content_type='application/json')
        sr = ScheduleRisk.objects.get(project=self.project, risk_key='A1')
        original_date = sr.first_identified_data_date

        # A newer version now exists — first_identified must stay pinned to
        # the original moment, not silently move forward.
        ScheduleUpload.objects.create(
            project=self.project, original_filename='c2.xer', sanitized_filename='c2.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-09-01',
            activities_json=[_act('A1')], upload_timestamp=timezone.now() + timedelta(days=1),
        )
        self.client.patch(f'/api/projects/{self.project.id}/risk-register/A1/', data=json.dumps({'owner': 'Bob'}), content_type='application/json')
        sr.refresh_from_db()
        self.assertEqual(sr.first_identified_data_date, original_date)

    def test_invalid_status_rejected(self):
        resp = self.client.patch(
            f'/api/projects/{self.project.id}/risk-register/A1/',
            data=json.dumps({'status': 'NOT_A_REAL_STATUS'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_target_date_settable(self):
        resp = self.client.patch(
            f'/api/projects/{self.project.id}/risk-register/A1/',
            data=json.dumps({'targetDate': '2026-09-30'}), content_type='application/json',
        )
        self.assertEqual(resp.json()['targetDate'], '2026-09-30')


class DrivingChainApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Driving Chain API Test')
        acts = [
            _act('A1', successors=[{'actId': 'A2', 'relType': 'FS', 'lagDays': 0}]),
            _act('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}], successors=[{'actId': 'M1', 'relType': 'FS', 'lagDays': 0}]),
            _act('M1', is_milestone=True, isMilestone=True, predecessors=[{'actId': 'A2', 'relType': 'FS', 'lagDays': 0}]),
        ]
        self.version = ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-08-15', activities_json=acts,
        )

    def test_driving_chain_endpoint_returns_all_three_analyses(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/risk-register/A2/driving-chain/')
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertTrue(body['drivingPath']['available'])
        self.assertTrue(body['blockingPredecessors']['available'])
        self.assertTrue(body['downstreamExposure']['available'])
        self.assertEqual(len(body['downstreamExposure']['exposedMilestones']), 1)


class MitigationActionApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Mitigation API Test')

    def test_create_list_update_delete_action(self):
        create_resp = self.client.post(
            f'/api/projects/{self.project.id}/mitigation-actions/',
            data=json.dumps({'description': 'Confirm crew availability', 'riskKey': 'A1', 'owner': 'PM'}),
            content_type='application/json',
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.content)
        action_id = create_resp.json()['id']
        self.assertEqual(create_resp.json()['riskKey'], 'A1')

        list_resp = self.client.get(f'/api/projects/{self.project.id}/mitigation-actions/')
        self.assertEqual(len(list_resp.json()['actions']), 1)

        patch_resp = self.client.patch(
            f'/api/projects/{self.project.id}/mitigation-actions/{action_id}/',
            data=json.dumps({'status': 'COMPLETE'}), content_type='application/json',
        )
        self.assertEqual(patch_resp.json()['status'], 'COMPLETE')
        self.assertIsNotNone(patch_resp.json()['completedAt'])

        del_resp = self.client.delete(f'/api/projects/{self.project.id}/mitigation-actions/{action_id}/')
        self.assertTrue(del_resp.json()['deleted'])
        self.assertEqual(MitigationAction.objects.filter(project=self.project).count(), 0)

    def test_missing_description_rejected(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/mitigation-actions/',
            data=json.dumps({'owner': 'PM'}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_filter_by_risk_key_and_status(self):
        self.client.post(f'/api/projects/{self.project.id}/mitigation-actions/', data=json.dumps({'description': 'A', 'riskKey': 'R1'}), content_type='application/json')
        self.client.post(f'/api/projects/{self.project.id}/mitigation-actions/', data=json.dumps({'description': 'B', 'riskKey': 'R2'}), content_type='application/json')
        resp = self.client.get(f'/api/projects/{self.project.id}/mitigation-actions/?riskKey=R1')
        self.assertEqual(len(resp.json()['actions']), 1)


class RecoveryScenarioExtendedApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Scenario Extended API Test')
        self.baseline = ScheduleUpload.objects.create(
            project=self.project, original_filename='bl.xer', sanitized_filename='bl.xer', file_type='XER',
            schedule_classification='APPROVED_BASELINE', data_date='2026-01-01',
            activities_json=[_act('A1', b_start='2025-12-12', b_finish='2026-01-01', dur=20.0, remainDur=20.0)],
            upload_timestamp=timezone.now() - timedelta(days=30),
        )
        self.current = ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-01-01',
            activities_json=[_act('A1', b_start='2025-12-12', b_finish='2026-01-01', dur=20.0, remainDur=20.0)],
        )

    def test_created_scenario_includes_baseline_recovery(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/recovery-scenarios/',
            data=json.dumps({'scheduleUploadId': str(self.current.id), 'assumptions': [
                {'type': 'reduce_duration', 'activityId': 'A1', 'newDuration': 8},
            ]}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertIsNotNone(body['result']['projectBaselineRecovery'])
        self.assertEqual(body['result']['projectBaselineRecovery']['varianceRecoveredDays'], 12)
        self.assertEqual(body['status'], 'DRAFT')

    def test_scenario_status_transition(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/recovery-scenarios/',
            data=json.dumps({'scheduleUploadId': str(self.current.id), 'assumptions': []}),
            content_type='application/json',
        )
        scenario_id = resp.json()['id']
        patch_resp = self.client.patch(
            f'/api/projects/{self.project.id}/recovery-scenarios/{scenario_id}/',
            data=json.dumps({'status': 'ACCEPTED'}), content_type='application/json',
        )
        self.assertEqual(patch_resp.json()['status'], 'ACCEPTED')

    def test_scenario_with_risk_key_links_to_schedule_risk(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/recovery-scenarios/',
            data=json.dumps({'scheduleUploadId': str(self.current.id), 'assumptions': [], 'riskKey': 'A1'}),
            content_type='application/json',
        )
        self.assertEqual(resp.json()['riskKey'], 'A1')
        self.assertTrue(ScheduleRisk.objects.filter(project=self.project, risk_key='A1').exists())

    def test_recovery_tracking_unavailable_until_accepted(self):
        resp = self.client.post(
            f'/api/projects/{self.project.id}/recovery-scenarios/',
            data=json.dumps({'scheduleUploadId': str(self.current.id), 'assumptions': []}),
            content_type='application/json',
        )
        scenario_id = resp.json()['id']
        detail = self.client.get(f'/api/projects/{self.project.id}/recovery-scenarios/{scenario_id}/')
        self.assertFalse(detail.json()['recoveryTracking']['available'])

    def test_recovery_tracking_compares_against_next_update(self):
        scenario = RecoveryScenario.objects.create(
            project=self.project, schedule_upload=self.current, status='ACCEPTED', assumptions=[],
            result={'milestoneImpact': [{'activityId': 'M1', 'activityName': 'M1', 'scenarioForecastFinish': '2026-01-15'}]},
        )
        # A genuinely newer version, with M1 forecast at 2026-01-20 (5 days after scenario target).
        ScheduleUpload.objects.create(
            project=self.project, original_filename='c2.xer', sanitized_filename='c2.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-01-08',
            activities_json=[_act('M1', is_milestone=True, finish='2026-01-20')],
            upload_timestamp=timezone.now() + timedelta(days=1),
        )
        detail = self.client.get(f'/api/projects/{self.project.id}/recovery-scenarios/{scenario.id}/')
        tracking = detail.json()['recoveryTracking']
        self.assertTrue(tracking['available'])
        comp = tracking['milestoneComparisons'][0]
        self.assertEqual(comp['classification'], 'NOT_REALIZED')
        self.assertEqual(comp['differenceDays'], 5)

    def test_recovery_tracking_on_track_when_target_met_or_beaten(self):
        scenario = RecoveryScenario.objects.create(
            project=self.project, schedule_upload=self.current, status='ACCEPTED', assumptions=[],
            result={'milestoneImpact': [{'activityId': 'M1', 'activityName': 'M1', 'scenarioForecastFinish': '2026-01-15'}]},
        )
        ScheduleUpload.objects.create(
            project=self.project, original_filename='c2.xer', sanitized_filename='c2.xer', file_type='XER',
            schedule_classification='CURRENT_UPDATE', data_date='2026-01-08',
            activities_json=[_act('M1', is_milestone=True, finish='2026-01-14')],
            upload_timestamp=timezone.now() + timedelta(days=1),
        )
        detail = self.client.get(f'/api/projects/{self.project.id}/recovery-scenarios/{scenario.id}/')
        comp = detail.json()['recoveryTracking']['milestoneComparisons'][0]
        self.assertEqual(comp['classification'], 'ON_TRACK')
