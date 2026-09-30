"""
CPM & Data Date Governance Audit — regression coverage.

Proves the single global principle this audit enforces: every time-
dependent analysis engine uses the SPECIFIC schedule version's own
effective Data Date as its statusing boundary — never today's date, never
upload_timestamp, never another version's/project's Data Date, and never
silently substitutes anything when no Data Date is available.
"""

import json
from datetime import date, datetime, timezone as dt_timezone
from unittest import mock

from django.test import TestCase

from scheduler import baseline_progress, report_service
from scheduler.cost_engine import compute_cost_summary
from scheduler.data_date_detection import resolve_effective_data_date
from scheduler.models import Project, ScheduleUpload
from scheduler.schedule_risk import compute_risk
from scheduler.status_engine import ThresholdValues, classify_schedule


def _act(code, name, bs, bf, wbs='Area A', pct=0.0, milestone=False, **extra):
    d = {
        'id': code, 'code': code, 'name': name, 'dur': 10.0, 'remainDur': 10.0 * (1 - pct / 100),
        'pctComplete': pct, 'bStart': bs, 'bFinish': bf, 'totalFloat': 0.0, 'isCritical': True,
        'isMilestone': milestone, 'wbs': wbs,
    }
    d.update(extra)
    return d


def _cost_act(code, budgeted, actual, remaining, pct, bs='2026-01-01', bf='2026-01-31'):
    return {
        'id': code, 'code': code, 'name': code, 'dur': 30.0, 'remainDur': 30.0 * (1 - pct / 100),
        'pctComplete': pct, 'bStart': bs, 'bFinish': bf, 'totalFloat': 0.0, 'isCritical': True,
        'isMilestone': False, 'isCostLoaded': True, 'budgetedCost': budgeted,
        'actualCost': actual, 'remainingCost': remaining, 'wbs': 'Area A',
    }


class TodayIndependenceTests(TestCase):
    """Section 27's critical regression test: the same schedule Data Date
    must produce identical results regardless of the real-world date the
    analysis happens to run on."""

    def setUp(self):
        self.project = Project.objects.create(name='Clock Independence Project')
        dd = date(2026, 2, 1)
        baseline_acts = [
            _act('A1', 'On Plan', '2026-02-05', '2026-02-10'),
            _act('A2', 'Late', '2026-01-01', '2026-01-20'),
        ]
        current_acts = [
            _act('A1', 'On Plan', '2026-02-05', '2026-02-10'),
            _act('A2', 'Late', '2026-01-01', '2026-01-20', pct=50.0, start='2026-01-01', earlyFinish='2026-01-25'),
        ]
        self.baseline = ScheduleUpload.objects.create(
            project=self.project, original_filename='b.xer', sanitized_filename='b.xer', file_type='XER',
            data_date=date(2026, 1, 1), version_label='Baseline', schedule_classification='APPROVED_BASELINE',
            activities_json=baseline_acts, upload_timestamp=datetime(2026, 1, 1, tzinfo=dt_timezone.utc),
        )
        self.current = ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            data_date=dd, version_label='Current', schedule_classification='CURRENT_UPDATE',
            activities_json=current_acts, upload_timestamp=datetime(2026, 2, 1, tzinfo=dt_timezone.utc),
        )

    def _run(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/baseline-progress/')
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_baseline_progress_identical_across_two_different_real_world_dates(self):
        with mock.patch('scheduler.narrative_engine.date') as mocked:
            mocked.today.return_value = date(2020, 1, 1)
            mocked.side_effect = lambda *a, **kw: date(*a, **kw)
            result_a = self._run()
        with mock.patch('scheduler.narrative_engine.date') as mocked:
            mocked.today.return_value = date(2031, 12, 31)
            mocked.side_effect = lambda *a, **kw: date(*a, **kw)
            result_b = self._run()
        self.assertEqual(result_a, result_b)

    def test_report_payload_lookahead_section_identical_across_two_different_real_world_dates(self):
        def _generate():
            payload = report_service.build_report_payload(self.project, 'WEEKLY_PROJECT_CONTROLS')
            return payload['lookAhead']

        with mock.patch('scheduler.narrative_engine.date') as mocked:
            mocked.today.return_value = date(2020, 1, 1)
            mocked.side_effect = lambda *a, **kw: date(*a, **kw)
            la_a = _generate()
        with mock.patch('scheduler.narrative_engine.date') as mocked:
            mocked.today.return_value = date(2031, 12, 31)
            mocked.side_effect = lambda *a, **kw: date(*a, **kw)
            la_b = _generate()
        self.assertEqual(la_a, la_b)


class VersionSpecificDataDateTests(TestCase):
    """Section 27's Version A / Version B scenario: identical activities,
    two different Data Dates, status results must differ appropriately."""

    def setUp(self):
        self.project = Project.objects.create(name='Version AB Project')
        # A single activity baselined Aug 1–14. As of 08/07 it should still
        # be "on plan" (not yet due); as of 08/21 it should be overdue
        # (should-have-finished) since it's incomplete and its baseline
        # finish has passed.
        self.acts = [_act('A1', 'Test Activity', '2026-08-01', '2026-08-14', pct=30.0, start='2026-08-01')]

        self.version_a = ScheduleUpload.objects.create(
            project=self.project, original_filename='a.xer', sanitized_filename='a.xer', file_type='XER',
            data_date=date(2026, 8, 7), version_label='Update A', schedule_classification='CURRENT_UPDATE',
            activities_json=self.acts, upload_timestamp=datetime(2026, 8, 7, tzinfo=dt_timezone.utc),
        )
        self.version_b = ScheduleUpload.objects.create(
            project=self.project, original_filename='b.xer', sanitized_filename='b.xer', file_type='XER',
            data_date=date(2026, 8, 21), version_label='Update B', schedule_classification='CURRENT_UPDATE',
            activities_json=self.acts, upload_timestamp=datetime(2026, 8, 21, tzinfo=dt_timezone.utc),
        )
        self.baseline = ScheduleUpload.objects.create(
            project=self.project, original_filename='bl.xer', sanitized_filename='bl.xer', file_type='XER',
            data_date=date(2026, 7, 1), version_label='Baseline', schedule_classification='APPROVED_BASELINE',
            activities_json=self.acts, upload_timestamp=datetime(2026, 7, 1, tzinfo=dt_timezone.utc),
        )

    def test_status_differs_between_versions_with_different_data_dates(self):
        rows_a = baseline_progress.match_baseline_current(self.acts, self.acts, date(2026, 8, 7))
        rows_b = baseline_progress.match_baseline_current(self.acts, self.acts, date(2026, 8, 21))
        self.assertEqual(rows_a[0]['status'], 'IN_PROGRESS')
        self.assertEqual(rows_b[0]['status'], 'SHOULD_HAVE_FINISHED')

    def test_lookahead_window_anchored_to_each_versions_own_data_date(self):
        resp_a = self.client.get(f'/api/projects/{self.project.id}/lookahead/?currentVersion={self.version_a.id}&baselineVersion={self.baseline.id}')
        resp_b = self.client.get(f'/api/projects/{self.project.id}/lookahead/?currentVersion={self.version_b.id}&baselineVersion={self.baseline.id}')
        self.assertEqual(resp_a.json()['window']['fromDate'], '2026-08-07')
        self.assertEqual(resp_b.json()['window']['fromDate'], '2026-08-21')

    def test_switching_active_version_changes_effective_date_no_stale_state(self):
        """Section 23: selecting a different version must fully replace the
        prior version's Data Date and status — nothing carries over."""
        resp_a = self.client.get(f'/api/projects/{self.project.id}/baseline-progress/?currentVersion={self.version_a.id}&baselineVersion={self.baseline.id}')
        resp_b = self.client.get(f'/api/projects/{self.project.id}/baseline-progress/?currentVersion={self.version_b.id}&baselineVersion={self.baseline.id}')
        self.assertEqual(resp_a.json()['dataDate'], '2026-08-07')
        self.assertEqual(resp_b.json()['dataDate'], '2026-08-21')
        self.assertNotEqual(resp_a.json()['rows'][0]['status'], resp_b.json()['rows'][0]['status'])

    def test_baseline_dates_never_change_between_versions(self):
        """Section 2: the baseline's own dates must stay the approved plan
        regardless of which current version/Data Date is being analyzed."""
        rows_a = baseline_progress.match_baseline_current(self.acts, self.acts, date(2026, 8, 7))
        rows_b = baseline_progress.match_baseline_current(self.acts, self.acts, date(2026, 8, 21))
        self.assertEqual(rows_a[0]['baselineStart'], rows_b[0]['baselineStart'])
        self.assertEqual(rows_a[0]['baselineFinish'], rows_b[0]['baselineFinish'])
        self.assertEqual(rows_a[0]['baselineStart'], '2026-08-01')


class NoDataDateTests(TestCase):
    """Section 26: when a schedule version has no trustworthy effective
    Data Date, time-dependent analysis must clearly report unavailable —
    never silently substitute upload_timestamp or today."""

    def setUp(self):
        self.project = Project.objects.create(name='No Data Date Project')
        self.acts = [_cost_act('A1', 1_000_000.0, 400_000.0, 600_000.0, 40.0)]
        self.version = ScheduleUpload.objects.create(
            project=self.project, original_filename='t.xer', sanitized_filename='t.xer', file_type='XER',
            activities_json=self.acts,   # deliberately no data_date
        )

    def test_resolver_reports_unavailable(self):
        result = resolve_effective_data_date(self.version)
        self.assertFalse(result['available'])
        self.assertIsNone(result['dataDate'])
        self.assertIn('Data Date', result['reason'])

    def test_resolver_never_falls_back_to_upload_timestamp(self):
        result = resolve_effective_data_date(self.version)
        self.assertNotEqual(result['dataDate'], self.version.upload_timestamp.date())

    def test_resolver_none_version(self):
        result = resolve_effective_data_date(None)
        self.assertFalse(result['available'])

    def test_baseline_progress_endpoint_does_not_crash_and_reports_no_date(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/baseline-progress/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()['dataDate'])

    def test_lookahead_endpoint_window_unavailable(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/lookahead/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['window']['available'])

    def test_cost_summary_endpoint_does_not_crash_pv_unavailable(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/cost-summary/')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body['dataDate'])
        # BAC/AC (not Data-Date-dependent) still work — only PV is affected.
        self.assertIsNotNone(body['budget']['originalBudget']['value'])

    def test_earned_value_endpoint_pv_null_not_zero(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/earned-value/')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body['overall']['cost']['pv'])
        self.assertIsNone(body['overall']['cost']['sv'])
        self.assertIsNone(body['overall']['cost']['spi'])
        # AC/BAC/CV are not PV-dependent and remain available.
        self.assertIsNotNone(body['overall']['cost']['ac'])

    def test_risk_endpoint_does_not_crash(self):
        resp = self.client.get(f'/api/projects/{self.project.id}/risk/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()['dataDate'])

    def test_weekly_report_lookahead_section_unavailable_reason_surfaced(self):
        payload = report_service.build_report_payload(self.project, 'WEEKLY_PROJECT_CONTROLS')
        self.assertFalse(payload['lookAhead']['available'])
        self.assertIn(payload['lookAhead']['reason'], payload['dataQuality'])

    def test_analyze_endpoint_returns_undetermined_not_today_based_status(self):
        resp = self.client.post('/api/analyze/', data=json.dumps({'current_activities': self.acts}), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['status'], 'UNDETERMINED')
        self.assertIn('NO_DATA_DATE', body.get('reason_codes', []))

    def test_narrative_endpoint_quality_section_unavailable_without_data_date(self):
        resp = self.client.post('/api/narrative/', data=json.dumps({'current_activities': self.acts}), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body.get('data_date'))
        self.assertEqual(body.get('status'), 'UNDETERMINED')

    def test_compute_cost_summary_direct_call_no_crash_and_correct_shape(self):
        """Regression test for the shape-mismatch bug this audit found:
        compute_planned_value's data_date=None branch previously returned a
        differently-shaped dict than its normal return, crashing
        compute_cost_summary's _dimension() with a KeyError."""
        summary = compute_cost_summary(self.acts, None)
        self.assertIsNone(summary['cost']['pv'])
        self.assertIsNone(summary['cost']['sv'])
        self.assertIsNotNone(summary['cost']['ac'])
        self.assertIsNotNone(summary['cost']['bac'])

    def test_compute_risk_none_safe(self):
        result = compute_risk(self.acts, None)
        self.assertIsInstance(result, dict)
        self.assertIn('overall', result)

    def test_classify_schedule_undetermined_gate(self):
        result = classify_schedule(current_activities=self.acts, thresholds=ThresholdValues(), data_date=None)
        self.assertEqual(result.status, 'UNDETERMINED')


class MultiProjectPortfolioTests(TestCase):
    """Section 22: each project's metrics must be calculated as of its own
    Data Date — never normalized to another project's or the newest."""

    def setUp(self):
        self.project_a = Project.objects.create(name='Portfolio Project A')
        self.project_b = Project.objects.create(name='Portfolio Project B')
        self.project_c = Project.objects.create(name='Portfolio Project C')

        acts = [_act('A1', 'Test', '2026-08-01', '2026-08-20', pct=30.0, start='2026-08-01')]

        self.v_a = ScheduleUpload.objects.create(
            project=self.project_a, original_filename='a.xer', sanitized_filename='a.xer', file_type='XER',
            data_date=date(2026, 8, 21), version_label='A', schedule_classification='CURRENT_UPDATE', activities_json=acts,
        )
        self.v_b = ScheduleUpload.objects.create(
            project=self.project_b, original_filename='b.xer', sanitized_filename='b.xer', file_type='XER',
            data_date=date(2026, 8, 28), version_label='B', schedule_classification='CURRENT_UPDATE', activities_json=acts,
        )
        self.v_c = ScheduleUpload.objects.create(
            project=self.project_c, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            data_date=date(2026, 8, 14), version_label='C', schedule_classification='CURRENT_UPDATE', activities_json=acts,
        )

    def test_each_project_reflects_its_own_data_date_independently(self):
        resp_a = self.client.get(f'/api/projects/{self.project_a.id}/risk/')
        resp_b = self.client.get(f'/api/projects/{self.project_b.id}/risk/')
        resp_c = self.client.get(f'/api/projects/{self.project_c.id}/risk/')
        self.assertEqual(resp_a.json()['dataDate'], '2026-08-21')
        self.assertEqual(resp_b.json()['dataDate'], '2026-08-28')
        self.assertEqual(resp_c.json()['dataDate'], '2026-08-14')
        # Never normalized to the newest (B's 08-28) across all three.
        self.assertNotEqual(resp_a.json()['dataDate'], resp_b.json()['dataDate'])
        self.assertNotEqual(resp_c.json()['dataDate'], resp_b.json()['dataDate'])

    def test_project_c_activity_not_yet_overdue_project_b_would_be(self):
        # Same activity (baseline finish 08-20, incomplete): as of Project
        # C's own Data Date (08-14) it is not yet due; the SAME activity
        # data evaluated as of Project B's Data Date (08-28) is overdue.
        # Proves neither project borrows the other's Data Date.
        rows_c = baseline_progress.match_baseline_current(
            self.v_c.activities_json, self.v_c.activities_json, self.v_c.data_date,
        )
        rows_b = baseline_progress.match_baseline_current(
            self.v_b.activities_json, self.v_b.activities_json, self.v_b.data_date,
        )
        self.assertNotIn(rows_c[0]['status'], ('SHOULD_HAVE_FINISHED',))
        self.assertEqual(rows_b[0]['status'], 'SHOULD_HAVE_FINISHED')
