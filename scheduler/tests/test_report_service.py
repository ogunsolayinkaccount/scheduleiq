from datetime import date

from django.test import TestCase

from scheduler import report_service
from scheduler.models import ManualCostEntry, Project, ScheduleUpload


def _cost_activity(code, budgeted, actual, remaining, pct_complete, area='Area A'):
    return {
        'id': code, 'code': code, 'name': f'Activity {code}', 'dur': 10.0,
        'remainDur': 10.0 * (1 - pct_complete / 100.0), 'pctComplete': pct_complete,
        'bStart': '2026-01-01', 'bFinish': '2026-01-11',
        'isCostLoaded': True, 'budgetedCost': budgeted, 'actualCost': actual, 'remainingCost': remaining,
        'area': area, 'totalFloat': 0.0, 'isCritical': True, 'isMilestone': False,
    }


class ReportServiceSectionTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Report Test Project', project_number='PN-100', client='Acme Co')
        self.v1 = ScheduleUpload.objects.create(
            project=self.project, original_filename='v1.xer', sanitized_filename='v1.xer', file_type='XER',
            data_date=date(2025, 12, 1), version_label='2025-12-01 Update',
            activity_count=10, milestone_count=2, critical_count=4, negative_float_count=0,
            activities_json=[_cost_activity('A1', 1_000_000, 400_000, 600_000, 40.0)],
        )
        self.v2 = ScheduleUpload.objects.create(
            project=self.project, original_filename='v2.xer', sanitized_filename='v2.xer', file_type='XER',
            data_date=date(2026, 1, 1), version_label='2026-01-01 Update',
            activity_count=10, milestone_count=2, critical_count=5, negative_float_count=1,
            activities_json=[_cost_activity('A1', 1_000_000, 900_000, 100_000, 85.0)],
        )

    def test_project_info_section(self):
        info = report_service.build_project_info_section(self.project, self.v2, self.v1)
        self.assertEqual(info['projectName'], 'Report Test Project')
        self.assertEqual(info['projectNumber'], 'PN-100')
        self.assertEqual(info['scheduleVersion'], self.v2.version_label)
        self.assertEqual(info['previousUpdateDate'], '2025-12-01')

    def test_schedule_performance_section_reuses_stored_fields(self):
        perf = report_service.build_schedule_performance_section(self.v2)
        self.assertTrue(perf['available'])
        self.assertEqual(perf['criticalCount'], 5)
        self.assertEqual(perf['negativeFloatCount'], 1)

    def test_schedule_performance_unavailable_without_version(self):
        perf = report_service.build_schedule_performance_section(None)
        self.assertFalse(perf['available'])

    def test_update_comparison_reuses_compare_schedules(self):
        comp = report_service.build_update_comparison_section(self.v2, self.v1)
        self.assertTrue(comp['available'])
        self.assertIn('summaryNarrative', comp)

    def test_update_comparison_unavailable_with_single_version(self):
        comp = report_service.build_update_comparison_section(self.v1, None)
        self.assertFalse(comp['available'])

    def test_build_executive_payload_shape(self):
        payload = report_service.build_executive_payload(self.project)
        self.assertIn('narrative', payload)
        self.assertIn('drivers', payload)
        self.assertEqual(payload['project']['name'], 'Report Test Project')

    def test_weekly_report_payload(self):
        payload = report_service.build_report_payload(self.project, 'WEEKLY_PROJECT_CONTROLS')
        self.assertEqual(payload['reportType'], 'WEEKLY_PROJECT_CONTROLS')
        self.assertIn('updateComparison', payload['sections'])
        self.assertIsNotNone(payload['updateComparison'])
        self.assertIsNone(payload['trendHistory'])   # not part of weekly sections

    def test_monthly_report_payload(self):
        payload = report_service.build_report_payload(self.project, 'MONTHLY_EXECUTIVE')
        self.assertEqual(payload['reportType'], 'MONTHLY_EXECUTIVE')
        self.assertIn('trendHistory', payload['sections'])
        self.assertIsNotNone(payload['trendHistory'])
        self.assertIsNotNone(payload['costProductivityDrivers'])

    def test_report_payload_with_no_cost_data(self):
        v = ScheduleUpload.objects.create(
            project=self.project, original_filename='v3.xer', sanitized_filename='v3.xer', file_type='XER',
            data_date=date(2026, 2, 1), version_label='2026-02-01 Update',
            activities_json=[{'id': 'B1', 'code': 'B1', 'dur': 10.0, 'remainDur': 5.0, 'pctComplete': 50.0}],
        )
        payload = report_service.build_report_payload(self.project, 'WEEKLY_PROJECT_CONTROLS', version_id=str(v.id))
        # The new version itself has no cost-loaded activities, so ITS cpi/ac
        # are None — but the metrics dict structure stays present (previous
        # version's values are legitimately preserved for comparison).
        self.assertIsNone(payload['currentPerformance']['cpi']['current'])
        self.assertIsNone(payload['currentPerformance']['ac']['current'])
        self.assertTrue(any('cost-loaded' in msg or 'Actual cost' in msg for msg in payload['dataQuality']))

    def test_report_payload_with_partial_data_single_version(self):
        solo_project = Project.objects.create(name='Solo Version Project')
        ScheduleUpload.objects.create(
            project=solo_project, original_filename='only.xer', sanitized_filename='only.xer', file_type='XER',
            data_date=date(2026, 1, 1), version_label='Only Update',
            activities_json=[_cost_activity('A1', 500_000, 200_000, 300_000, 40.0)],
        )
        payload = report_service.build_report_payload(solo_project, 'WEEKLY_PROJECT_CONTROLS')
        self.assertFalse(payload['updateComparison']['available'])
        self.assertIn('Only one schedule version exists', ' '.join(payload['dataQuality']))

    def test_invalid_report_type_raises(self):
        with self.assertRaises(ValueError):
            report_service.build_report_payload(self.project, 'NOT_A_TYPE')

    def test_traceability_present(self):
        payload = report_service.build_report_payload(self.project, 'WEEKLY_PROJECT_CONTROLS')
        trace = payload['traceability']
        self.assertEqual(trace['scheduleVersionId'], str(self.v2.id))
        self.assertEqual(trace['evMethod'], 'DURATION_PCT_COMPLETE')
        self.assertIn('engineVersion', trace)


class ManagementActionsTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Actions Project')
        self.v1 = ScheduleUpload.objects.create(
            project=self.project, original_filename='v1.xer', sanitized_filename='v1.xer', file_type='XER',
            data_date=date(2025, 12, 1), version_label='Dec Update',
            activities_json=[
                _cost_activity('A1', 1_000_000, 700_000, 300_000, 40.0, area='Area C'),
                _cost_activity('A2', 500_000, 300_000, 200_000, 40.0, area='Area D'),
            ],
        )
        self.v2 = ScheduleUpload.objects.create(
            project=self.project, original_filename='v2.xer', sanitized_filename='v2.xer', file_type='XER',
            data_date=date(2026, 1, 1), version_label='Jan Update',
            activities_json=[
                _cost_activity('A1', 1_000_000, 950_000, 50_000, 60.0, area='Area C'),   # deep overrun
                _cost_activity('A2', 500_000, 350_000, 150_000, 60.0, area='Area D'),
            ],
        )

    def test_cost_variance_action_generated(self):
        payload = report_service.build_executive_payload(self.project, group_by='area')
        actions = report_service.build_management_actions(payload)
        types = {a['type'] for a in actions}
        self.assertIn('COST_VARIANCE', types)
        cv_action = next(a for a in actions if a['type'] == 'COST_VARIANCE')
        self.assertIn('Investigate', cv_action['action'])
        self.assertIn('Area C', cv_action['action'])

    def test_actions_use_review_investigate_confirm_evaluate_language(self):
        payload = report_service.build_executive_payload(self.project)
        actions = report_service.build_management_actions(payload)
        for a in actions:
            self.assertTrue(any(a['action'].startswith(verb) for verb in ('Review', 'Investigate', 'Confirm', 'Evaluate')))

    def test_no_actions_fabricated_without_data(self):
        empty_project = Project.objects.create(name='Empty Project')
        payload = report_service.build_executive_payload(empty_project)
        actions = report_service.build_management_actions(payload)
        self.assertEqual(actions, [])

    def test_forecast_reconciliation_action_when_approved_eac_diverges(self):
        ManualCostEntry.objects.create(project=self.project, entry_type='APPROVED_EAC', cost=2_500_000.0)
        payload = report_service.build_executive_payload(self.project)
        actions = report_service.build_management_actions(payload)
        types = {a['type'] for a in actions}
        self.assertIn('FORECAST_RECONCILIATION', types)
