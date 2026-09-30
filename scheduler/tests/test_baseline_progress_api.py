from datetime import date, datetime, timezone as dt_timezone

from django.test import TestCase

from scheduler.models import Project, ScheduleUpload


def _act(code, name, bs, bf, pct=0.0, **extra):
    d = {
        'id': code, 'code': code, 'name': name, 'dur': 10.0, 'remainDur': 10.0 * (1 - pct / 100),
        'pctComplete': pct, 'bStart': bs, 'bFinish': bf, 'totalFloat': 0.0, 'isCritical': True,
        'isMilestone': False, 'wbs': 'Area A',
    }
    d.update(extra)
    return d


class BaselineProgressApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='BP API Test')
        baseline_acts = [
            _act('A1', 'On Plan', '2026-03-01', '2026-03-10'),
            _act('A2', 'Delayed', '2026-01-01', '2026-01-15'),
        ]
        current_acts = [
            _act('A1', 'On Plan', '2026-03-01', '2026-03-10'),
            _act('A2', 'Delayed', '2026-01-01', '2026-01-15', earlyFinish='2026-01-25'),
        ]
        self.baseline = ScheduleUpload.objects.create(
            project=self.project, original_filename='baseline.xer', sanitized_filename='baseline.xer',
            file_type='XER', data_date=date(2026, 1, 1), version_label='Baseline',
            schedule_classification='APPROVED_BASELINE', activities_json=baseline_acts,
            upload_timestamp=datetime(2026, 1, 1, tzinfo=dt_timezone.utc),
        )
        self.current = ScheduleUpload.objects.create(
            project=self.project, original_filename='current.xer', sanitized_filename='current.xer',
            file_type='XER', data_date=date(2026, 2, 1), version_label='Current',
            schedule_classification='CURRENT_UPDATE', activities_json=current_acts,
            upload_timestamp=datetime(2026, 2, 1, tzinfo=dt_timezone.utc),
        )

    def test_baseline_auto_detected_by_classification_not_oldest_upload(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/baseline-progress/')
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body['baselineVersionId'], str(self.baseline.id))
        self.assertIsNone(body['baselineMessage'])

    def test_no_baseline_message_when_none_designated(self):
        self.baseline.schedule_classification = 'CURRENT_UPDATE'
        self.baseline.save(update_fields=['schedule_classification'])
        resp = self.client.get(f'/api/projects/{self.project.id}/baseline-progress/')
        body = resp.json()
        self.assertIsNone(body['baselineVersionId'])
        self.assertEqual(body['baselineMessage'], 'No baseline schedule has been selected for this project.')
        self.assertFalse(body['hasBaseline'])

    def test_activity_rows_and_status_counts(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/baseline-progress/')
        body = resp.json()
        by_id = {r['activityId']: r for r in body['rows']}
        self.assertEqual(by_id['A1']['status'], 'ON_PLAN')
        self.assertEqual(by_id['A2']['status'], 'SHOULD_HAVE_FINISHED')

    def test_explicit_baseline_version_param_overrides_auto_detection(self):
        other_baseline = ScheduleUpload.objects.create(
            project=self.project, original_filename='other.xer', sanitized_filename='other.xer',
            file_type='XER', data_date=date(2025, 12, 1), version_label='Other Baseline',
            schedule_classification='CURRENT_UPDATE', activities_json=[],
        )
        resp = self.client.get(f'/api/projects/{self.project.id}/baseline-progress/?baselineVersion={other_baseline.id}')
        self.assertEqual(resp.json()['baselineVersionId'], str(other_baseline.id))

    def test_filter_by_wbs_via_query_param(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/baseline-progress/?wbs=Area+A')
        self.assertEqual(resp.json()['activityCount'], 2)
        resp2 = self.client.get(f'/api/projects/{self.project.id}/baseline-progress/?wbs=Nonexistent')
        self.assertEqual(resp2.json()['activityCount'], 0)

    def test_lookahead_default_four_weeks(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/lookahead/')
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body['window']['weeks'], 4)
        self.assertEqual(body['window']['fromDate'], '2026-02-01')
        self.assertEqual(body['window']['toDate'], '2026-03-01')

    def test_lookahead_two_weeks(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/lookahead/?lookaheadWeeks=2')
        self.assertEqual(resp.json()['window']['toDate'], '2026-02-15')

    def test_lookahead_custom_range(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/lookahead/?fromDate=2026-05-01&toDate=2026-05-10')
        body = resp.json()
        self.assertEqual(body['window']['fromDate'], '2026-05-01')
        self.assertEqual(body['window']['toDate'], '2026-05-10')

    def test_lookahead_summary_cards_present(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/lookahead/')
        self.assertIn('summaryCards', resp.json())
        self.assertIn('histogram', resp.json())

    def test_project_not_found_returns_404(self):
        resp = self.client.get('/api/projects/00000000-0000-0000-0000-000000000000/baseline-progress/')
        self.assertEqual(resp.status_code, 404)

    def test_invalid_lookahead_weeks_returns_400(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/lookahead/?lookaheadWeeks=notanumber')
        self.assertEqual(resp.status_code, 400)
