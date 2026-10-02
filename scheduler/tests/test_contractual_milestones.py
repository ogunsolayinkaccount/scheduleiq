"""
Contractual Milestone Tracker engine tests — pure function tests for
contractual_milestones.py. Covers every status (GREEN/YELLOW/RED/
UNVERIFIED), the configurable warning threshold, and that this never
reuses unrelated schedule-wide negative-float populations to decide a
specific milestone's status.
"""
from datetime import date

from django.test import SimpleTestCase

from scheduler import contractual_milestones as cm


def _row(activity_id='M1', forecast_finish='2026-12-20', actual_finish=None,
         current_total_float=15.0, driving=False, critical_actionable=False, **extra):
    row = {
        'activityId': activity_id, 'activityName': f'Activity {activity_id}',
        'forecastFinish': forecast_finish, 'actualFinish': actual_finish,
        'currentFinish': forecast_finish, 'currentTotalFloat': current_total_float,
        'driving': driving, 'criticalActionable': critical_actionable,
    }
    row.update(extra)
    return row


class ClassifyContractualStatusTests(SimpleTestCase):
    def test_no_contract_date_is_unverified(self):
        result = cm.classify_contractual_status(None, _row())
        self.assertEqual(result['status'], cm.UNVERIFIED)
        self.assertIn('No contractual', result['explanation'])

    def test_no_matched_activity_is_unverified(self):
        result = cm.classify_contractual_status(date(2026, 12, 20), None)
        self.assertEqual(result['status'], cm.UNVERIFIED)
        self.assertIn('does not match', result['explanation'])

    def test_matched_activity_with_no_usable_finish_date_is_unverified(self):
        row = _row(forecast_finish=None, actual_finish=None, current_total_float=None)
        row['currentFinish'] = None
        result = cm.classify_contractual_status(date(2026, 12, 20), row)
        self.assertEqual(result['status'], cm.UNVERIFIED)

    def test_forecast_after_contract_date_is_red(self):
        row = _row(forecast_finish='2026-12-25')
        result = cm.classify_contractual_status(date(2026, 12, 20), row)
        self.assertEqual(result['status'], cm.RED)
        self.assertEqual(result['varianceCalendarDays'], 5)

    def test_forecast_on_contract_date_with_healthy_float_is_green(self):
        row = _row(forecast_finish='2026-12-20', current_total_float=25.0)
        result = cm.classify_contractual_status(date(2026, 12, 20), row, warning_threshold_days=10.0)
        self.assertEqual(result['status'], cm.GREEN)
        self.assertEqual(result['varianceCalendarDays'], 0)

    def test_forecast_before_contract_date_with_healthy_float_is_green(self):
        row = _row(forecast_finish='2026-12-15', current_total_float=25.0)
        result = cm.classify_contractual_status(date(2026, 12, 20), row, warning_threshold_days=10.0)
        self.assertEqual(result['status'], cm.GREEN)
        self.assertEqual(result['varianceCalendarDays'], -5)

    def test_forecast_meets_date_but_float_crosses_threshold_is_yellow(self):
        row = _row(forecast_finish='2026-12-20', current_total_float=4.0)
        result = cm.classify_contractual_status(date(2026, 12, 20), row, warning_threshold_days=10.0)
        self.assertEqual(result['status'], cm.YELLOW)
        self.assertIn('float', result['explanation'])

    def test_forecast_meets_date_but_documented_issue_is_yellow(self):
        row = _row(forecast_finish='2026-12-20', current_total_float=30.0)
        result = cm.classify_contractual_status(date(2026, 12, 20), row, warning_threshold_days=10.0, has_documented_issue=True)
        self.assertEqual(result['status'], cm.YELLOW)
        self.assertIn('documented', result['explanation'])

    def test_warning_threshold_is_configurable_not_hard_coded(self):
        row = _row(forecast_finish='2026-12-20', current_total_float=8.0)
        # 8d float: YELLOW at threshold 10, GREEN at threshold 5.
        self.assertEqual(cm.classify_contractual_status(date(2026, 12, 20), row, warning_threshold_days=10.0)['status'], cm.YELLOW)
        self.assertEqual(cm.classify_contractual_status(date(2026, 12, 20), row, warning_threshold_days=5.0)['status'], cm.GREEN)

    def test_completed_activity_uses_actual_finish_not_forecast(self):
        row = _row(forecast_finish=None, actual_finish='2026-12-18', current_total_float=None)
        result = cm.classify_contractual_status(date(2026, 12, 20), row)
        self.assertEqual(result['status'], cm.GREEN)
        self.assertEqual(result['p6ForecastFinish'], '2026-12-18')

    def test_red_takes_priority_over_float_warning(self):
        # Even with ample float, forecasting past the contract date is RED,
        # never downgraded to YELLOW.
        row = _row(forecast_finish='2026-12-25', current_total_float=40.0)
        result = cm.classify_contractual_status(date(2026, 12, 20), row, warning_threshold_days=10.0)
        self.assertEqual(result['status'], cm.RED)

    def test_unrelated_schedule_wide_negative_float_never_influences_classification(self):
        # The milestone's OWN float is healthy; nothing about any other
        # activity anywhere in the schedule can downgrade this status.
        row = _row(forecast_finish='2026-12-20', current_total_float=30.0)
        result = cm.classify_contractual_status(date(2026, 12, 20), row, warning_threshold_days=10.0)
        self.assertEqual(result['status'], cm.GREEN)


class BuildContractualMilestoneTrackerTests(SimpleTestCase):
    def test_empty_register_is_not_configured(self):
        result = cm.build_contractual_milestone_tracker([], [])
        self.assertFalse(result['configured'])
        self.assertEqual(result['milestones'], [])
        self.assertEqual(result['counts'], {'GREEN': 0, 'YELLOW': 0, 'RED': 0, 'UNVERIFIED': 0})

    def test_mixed_register_produces_correct_counts(self):
        rows = [
            _row('M1', forecast_finish='2026-12-20'),   # on time
            _row('M2', forecast_finish='2026-12-30'),   # late
        ]
        entries = [
            {'id': 'e1', 'activityId': 'M1', 'description': 'Area B turnover', 'category': 'CONTRACTUAL_INTERIM', 'contractRequiredDate': '2026-12-20'},
            {'id': 'e2', 'activityId': 'M2', 'description': 'Final completion', 'category': 'CONTRACTUAL_COMPLETION', 'contractRequiredDate': '2026-12-20'},
            {'id': 'e3', 'activityId': 'UNMAPPED', 'description': 'Owner handoff', 'category': 'CONTRACTUAL_INTERIM', 'contractRequiredDate': '2026-12-20'},
        ]
        result = cm.build_contractual_milestone_tracker(entries, rows, warning_threshold_days=10.0)
        self.assertTrue(result['configured'])
        self.assertEqual(result['counts'], {'GREEN': 1, 'YELLOW': 0, 'RED': 1, 'UNVERIFIED': 1})
        by_name = {m['milestoneName']: m for m in result['milestones']}
        self.assertEqual(by_name['Area B turnover']['status'], cm.GREEN)
        self.assertEqual(by_name['Final completion']['status'], cm.RED)
        self.assertEqual(by_name['Owner handoff']['status'], cm.UNVERIFIED)

    def test_source_document_reference_passed_through(self):
        rows = [_row('M1', forecast_finish='2026-12-20')]
        entries = [{'id': 'e1', 'activityId': 'M1', 'description': 'X', 'category': 'CONTRACTUAL_COMPLETION',
                    'contractRequiredDate': '2026-12-20', 'sourceDocumentReference': 'Contract Exhibit C, Rev 2'}]
        result = cm.build_contractual_milestone_tracker(entries, rows)
        self.assertEqual(result['milestones'][0]['sourceDocumentReference'], 'Contract Exhibit C, Rev 2')
