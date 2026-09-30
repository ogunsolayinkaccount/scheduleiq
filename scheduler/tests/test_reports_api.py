import json
from datetime import date
from unittest import mock

from django.test import TestCase

from scheduler.models import ProjectControlsReport, Project, ScheduleUpload


def _cost_activity(code, budgeted, actual, remaining, pct_complete, discipline='Electrical'):
    return {
        'id': code, 'code': code, 'name': f'Activity {code}', 'dur': 10.0,
        'remainDur': 10.0 * (1 - pct_complete / 100.0), 'pctComplete': pct_complete,
        'bStart': '2026-01-01', 'bFinish': '2026-01-11',
        'isCostLoaded': True, 'budgetedCost': budgeted, 'actualCost': actual, 'remainingCost': remaining,
        'discipline': discipline, 'totalFloat': 0.0, 'isCritical': True, 'isMilestone': False,
    }


class ReportsApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='API Report Project')
        self.v1 = ScheduleUpload.objects.create(
            project=self.project, original_filename='v1.xer', sanitized_filename='v1.xer', file_type='XER',
            data_date=date(2025, 12, 1), version_label='Dec Update',
            activities_json=[_cost_activity('A1', 1_000_000, 400_000, 600_000, 40.0)],
        )
        self.v2 = ScheduleUpload.objects.create(
            project=self.project, original_filename='v2.xer', sanitized_filename='v2.xer', file_type='XER',
            data_date=date(2026, 1, 1), version_label='Jan Update',
            activities_json=[_cost_activity('A1', 1_000_000, 900_000, 100_000, 85.0)],
        )
        self.base = f'/api/projects/{self.project.id}/reports/'

    def test_generate_weekly_report(self):
        resp = self.client.post(self.base, data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS'}), content_type='application/json')
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body['reportType'], 'WEEKLY_PROJECT_CONTROLS')
        self.assertIn('payload', body)
        self.assertEqual(ProjectControlsReport.objects.count(), 1)

    def test_generate_monthly_report(self):
        resp = self.client.post(self.base, data=json.dumps({'reportType': 'MONTHLY_EXECUTIVE'}), content_type='application/json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['reportType'], 'MONTHLY_EXECUTIVE')

    def test_invalid_report_type_returns_400(self):
        resp = self.client.post(self.base, data=json.dumps({'reportType': 'NOT_REAL'}), content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_list_reports(self):
        self.client.post(self.base, data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS'}), content_type='application/json')
        self.client.post(self.base, data=json.dumps({'reportType': 'MONTHLY_EXECUTIVE'}), content_type='application/json')
        resp = self.client.get(self.base)
        self.assertEqual(len(resp.json()['reports']), 2)

    def test_get_report_detail(self):
        create_resp = self.client.post(self.base, data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS'}), content_type='application/json')
        report_id = create_resp.json()['id']
        resp = self.client.get(f'{self.base}{report_id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('payload', resp.json())

    def test_delete_report(self):
        create_resp = self.client.post(self.base, data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS'}), content_type='application/json')
        report_id = create_resp.json()['id']
        resp = self.client.delete(f'{self.base}{report_id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ProjectControlsReport.objects.count(), 0)

    def test_pdf_export_endpoint(self):
        create_resp = self.client.post(self.base, data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS'}), content_type='application/json')
        report_id = create_resp.json()['id']
        resp = self.client.get(f'{self.base}{report_id}/pdf/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertTrue(resp.content.startswith(b'%PDF'))

    def test_excel_export_endpoint(self):
        create_resp = self.client.post(self.base, data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS'}), content_type='application/json')
        report_id = create_resp.json()['id']
        resp = self.client.get(f'{self.base}{report_id}/excel/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.content.startswith(b'PK'))

    def test_pdf_uses_persisted_snapshot_not_live_recompute(self):
        """The critical guarantee: PDF/Excel export must read the SAVED
        payload, never recompute from current ScheduleUpload data."""
        create_resp = self.client.post(self.base, data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS'}), content_type='application/json')
        report_id = create_resp.json()['id']
        record = ProjectControlsReport.objects.get(pk=report_id)

        # Tamper with the persisted payload directly (simulating "what if the
        # live data changed") and confirm the PDF reflects the STORED value.
        record.payload_json['projectInfo']['projectName'] = 'TAMPERED NAME'
        record.save()

        with mock.patch('scheduler.report_export.generate_report_pdf', wraps=__import__('scheduler.report_export', fromlist=['generate_report_pdf']).generate_report_pdf) as spy:
            self.client.get(f'{self.base}{report_id}/pdf/')
            called_payload = spy.call_args[0][0]
            self.assertEqual(called_payload['projectInfo']['projectName'], 'TAMPERED NAME')

    def test_report_does_not_change_after_new_schedule_upload(self):
        """The core Phase B guarantee: generating a report, then importing a
        THIRD schedule version, must not alter the already-persisted report."""
        create_resp = self.client.post(self.base, data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS'}), content_type='application/json')
        report_id = create_resp.json()['id']
        original_payload = create_resp.json()['payload']

        # Import a third, very different update to the same project.
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v3.xer', sanitized_filename='v3.xer', file_type='XER',
            data_date=date(2026, 2, 1), version_label='Feb Update',
            activities_json=[_cost_activity('A1', 5_000_000, 4_000_000, 1_000_000, 10.0)],
        )

        stored = ProjectControlsReport.objects.get(pk=report_id)
        self.assertEqual(stored.payload_json['currentPerformance']['cpi']['current'], original_payload['currentPerformance']['cpi']['current'])
        self.assertEqual(stored.payload_json['projectInfo']['dataDate'], original_payload['projectInfo']['dataDate'])

        # And re-fetching the report via the API returns the SAME frozen numbers.
        detail_resp = self.client.get(f'{self.base}{report_id}/')
        self.assertEqual(detail_resp.json()['payload']['currentPerformance']['cpi']['current'], original_payload['currentPerformance']['cpi']['current'])

    def test_compare_two_reports(self):
        r1 = self.client.post(self.base, data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS', 'version': str(self.v1.id)}), content_type='application/json').json()
        r2 = self.client.post(self.base, data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS', 'version': str(self.v2.id)}), content_type='application/json').json()
        resp = self.client.get(f'{self.base}compare/?a={r1["id"]}&b={r2["id"]}')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn('kpiMovement', body)
        cpi_movement = body['kpiMovement']['cpi']
        self.assertIn(cpi_movement['direction'], ('improving', 'deteriorating', 'unchanged', 'unavailable'))
        self.assertIn('current', cpi_movement)
        self.assertIn('previous', cpi_movement)

    def test_compare_missing_report_returns_404(self):
        r1 = self.client.post(self.base, data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS'}), content_type='application/json').json()
        resp = self.client.get(f'{self.base}compare/?a={r1["id"]}&b=00000000-0000-0000-0000-000000000000')
        self.assertEqual(resp.status_code, 404)

    def test_report_scoped_to_project(self):
        other = Project.objects.create(name='Other Project')
        resp = self.client.get(f'/api/projects/{other.id}/reports/')
        self.assertEqual(resp.json()['reports'], [])

    def test_ai_disabled_by_default(self):
        resp = self.client.post(self.base, data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS'}), content_type='application/json')
        payload = resp.json()['payload']
        self.assertIsNone(payload['executiveSummary']['aiNarrative'])

    def test_ai_enabled_uses_null_provider_gracefully_without_config(self):
        # No AI_PROVIDER env configured in tests -> NullProvider -> graceful no-op, not an error.
        resp = self.client.post(self.base, data=json.dumps({'reportType': 'WEEKLY_PROJECT_CONTROLS', 'enableAi': True}), content_type='application/json')
        self.assertEqual(resp.status_code, 201)
        ai_narrative = resp.json()['payload']['executiveSummary']['aiNarrative']
        self.assertIsNotNone(ai_narrative)
        self.assertFalse(ai_narrative['aiEnhanced'])
