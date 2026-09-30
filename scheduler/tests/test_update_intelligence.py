"""
Update-to-Update Intelligence & Critical/Driving Path Movement — tests for
update_intelligence.py (pure engine) and the /update-intelligence/ API.
"""

from datetime import date, datetime, timezone as dt_timezone
from unittest import mock

from django.test import SimpleTestCase, TestCase

from scheduler import update_intelligence as ui
from scheduler.models import Project, ScheduleUpload
from scheduler.calendar_engine import CalendarDefinition
from .fixtures import make_activity

DD_PREV = date(2026, 8, 14)
DD_CURR = date(2026, 8, 21)
_FIVE_DAY = CalendarDefinition({1: True, 2: True, 3: True, 4: True, 5: True, 6: False, 7: False}, [])


class ActivityMovementTests(SimpleTestCase):
    def test_slipped_activity(self):
        prev = [make_activity('A1', b_start='2026-08-01', b_finish='2026-08-10')]
        curr = [make_activity('A1', b_start='2026-08-01', b_finish='2026-08-10', earlyFinish='2026-08-17')]
        rows = ui.build_activity_movement(prev, curr)
        self.assertIn('SLIPPED', rows[0]['flags'])
        self.assertEqual(rows[0]['finishMovementDays'], 7)

    def test_improved_activity(self):
        prev = [make_activity('A1', b_start='2026-08-01', b_finish='2026-08-10')]
        curr = [make_activity('A1', b_start='2026-08-01', b_finish='2026-08-10', earlyFinish='2026-08-05')]
        rows = ui.build_activity_movement(prev, curr)
        self.assertIn('IMPROVED', rows[0]['flags'])
        self.assertEqual(rows[0]['finishMovementDays'], -5)

    def test_unchanged_activity(self):
        prev = [make_activity('A1', b_start='2026-08-01', b_finish='2026-08-10')]
        curr = [make_activity('A1', b_start='2026-08-01', b_finish='2026-08-10')]
        rows = ui.build_activity_movement(prev, curr)
        self.assertIn('UNCHANGED', rows[0]['flags'])
        self.assertEqual(rows[0]['finishMovementDays'], 0)

    def test_added_activity(self):
        prev = [make_activity('A1')]
        curr = [make_activity('A1'), make_activity('A2')]
        rows = ui.build_activity_movement(prev, curr)
        new_row = next(r for r in rows if r['activityId'] == 'A2')
        self.assertEqual(new_row['matchStatus'], 'NEW')
        self.assertIn('NEW', new_row['flags'])

    def test_removed_activity(self):
        prev = [make_activity('A1'), make_activity('A2')]
        curr = [make_activity('A1')]
        rows = ui.build_activity_movement(prev, curr)
        removed_row = next(r for r in rows if r['activityId'] == 'A2')
        self.assertEqual(removed_row['matchStatus'], 'REMOVED')
        self.assertIn('REMOVED', removed_row['flags'])

    def test_started_this_period(self):
        prev = [make_activity('A1', b_start='2026-08-01', b_finish='2026-08-10')]
        curr = [make_activity('A1', b_start='2026-08-01', b_finish='2026-08-10', start='2026-08-15')]
        rows = ui.build_activity_movement(prev, curr)
        self.assertIn('STARTED_THIS_PERIOD', rows[0]['flags'])

    def test_completed_this_period(self):
        prev = [make_activity('A1', b_start='2026-08-01', b_finish='2026-08-10', start='2026-08-02')]
        curr = [make_activity('A1', b_start='2026-08-01', b_finish='2026-08-10', start='2026-08-02', finish='2026-08-09', pct_complete=100.0)]
        rows = ui.build_activity_movement(prev, curr)
        self.assertIn('COMPLETED_THIS_PERIOD', rows[0]['flags'])

    def test_activity_may_carry_multiple_flags(self):
        # Started AND slipped in the same period.
        prev = [make_activity('A1', b_start='2026-08-01', b_finish='2026-08-10')]
        curr = [make_activity('A1', b_start='2026-08-01', b_finish='2026-08-10', start='2026-08-15', earlyFinish='2026-08-20')]
        rows = ui.build_activity_movement(prev, curr)
        self.assertIn('STARTED_THIS_PERIOD', rows[0]['flags'])
        self.assertIn('SLIPPED', rows[0]['flags'])

    def test_working_day_movement_present_with_calendar(self):
        prev = [make_activity('A1', b_finish='2026-08-05', calendarId='C1')]
        curr = [make_activity('A1', b_finish='2026-08-05', calendarId='C1', earlyFinish='2026-08-12')]
        rows = ui.build_activity_movement(prev, curr, calendars={'C1': _FIVE_DAY})
        self.assertTrue(rows[0]['workingDayCalendarAvailable'])
        self.assertIsNotNone(rows[0]['finishMovementWorkingDays'])


class ForecastCommitmentTests(SimpleTestCase):
    def test_forecast_start_met(self):
        prev = [make_activity('A1', b_start='2026-08-16', b_finish='2026-08-20')]
        curr = [make_activity('A1', b_start='2026-08-16', b_finish='2026-08-20', start='2026-08-17')]
        rows = ui.build_activity_movement(prev, curr)
        commitment = ui.compute_forecast_commitment(rows, prev, curr, DD_PREV, DD_CURR)
        self.assertEqual(commitment['startCommitments'][0]['status'], 'FORECAST_START_MET')

    def test_forecast_start_missed(self):
        prev = [make_activity('A1', b_start='2026-08-16', b_finish='2026-08-20')]
        curr = [make_activity('A1', b_start='2026-08-16', b_finish='2026-08-20')]
        rows = ui.build_activity_movement(prev, curr)
        commitment = ui.compute_forecast_commitment(rows, prev, curr, DD_PREV, DD_CURR)
        self.assertEqual(commitment['startCommitments'][0]['status'], 'FORECAST_START_MISSED')

    def test_forecast_finish_met(self):
        prev = [make_activity('A1', b_start='2026-08-01', b_finish='2026-08-18', start='2026-08-01')]
        curr = [make_activity('A1', b_start='2026-08-01', b_finish='2026-08-18', start='2026-08-01', finish='2026-08-17', pct_complete=100.0)]
        rows = ui.build_activity_movement(prev, curr)
        commitment = ui.compute_forecast_commitment(rows, prev, curr, DD_PREV, DD_CURR)
        self.assertEqual(commitment['finishCommitments'][0]['status'], 'FORECAST_FINISH_MET')

    def test_forecast_finish_missed(self):
        prev = [make_activity('A1', b_start='2026-08-01', b_finish='2026-08-18', start='2026-08-01')]
        curr = [make_activity('A1', b_start='2026-08-01', b_finish='2026-08-18', start='2026-08-01')]
        rows = ui.build_activity_movement(prev, curr)
        commitment = ui.compute_forecast_commitment(rows, prev, curr, DD_PREV, DD_CURR)
        self.assertEqual(commitment['finishCommitments'][0]['status'], 'FORECAST_FINISH_MISSED')

    def test_unavailable_without_both_data_dates(self):
        rows = ui.build_activity_movement([], [])
        commitment = ui.compute_forecast_commitment(rows, [], [], None, DD_CURR)
        self.assertFalse(commitment['available'])


class ReliabilityTests(SimpleTestCase):
    def test_zero_planned_starts_unavailable(self):
        commitment = {'available': True, 'startCommitments': [], 'finishCommitments': []}
        reliability = ui.compute_reliability(commitment)
        self.assertIsNone(reliability['startReliabilityPct'])
        self.assertEqual(reliability['plannedStarts'], 0)

    def test_zero_planned_finishes_unavailable(self):
        commitment = {'available': True, 'startCommitments': [], 'finishCommitments': []}
        reliability = ui.compute_reliability(commitment)
        self.assertIsNone(reliability['finishReliabilityPct'])

    def test_reliability_percentage_computed(self):
        commitment = {
            'available': True,
            'startCommitments': [{'status': 'FORECAST_START_MET'}, {'status': 'FORECAST_START_MET'}, {'status': 'FORECAST_START_MISSED'}],
            'finishCommitments': [{'status': 'FORECAST_FINISH_MET'}],
        }
        reliability = ui.compute_reliability(commitment)
        self.assertEqual(reliability['plannedStarts'], 3)
        self.assertEqual(reliability['actualStarts'], 2)
        self.assertAlmostEqual(reliability['startReliabilityPct'], 66.7, places=1)
        self.assertEqual(reliability['finishReliabilityPct'], 100.0)

    def test_never_fabricates_100_percent(self):
        commitment = {'available': True, 'startCommitments': [], 'finishCommitments': []}
        reliability = ui.compute_reliability(commitment)
        self.assertNotEqual(reliability['startReliabilityPct'], 100.0)
        self.assertIsNone(reliability['startReliabilityPct'])


class FloatMovementTests(SimpleTestCase):
    def test_float_improved(self):
        prev = [make_activity('A1', total_float=2.0)]
        curr = [make_activity('A1', total_float=8.0)]
        rows = ui.build_activity_movement(prev, curr)
        result = ui.compute_float_movement(rows, prev, curr)
        self.assertEqual(result['improvedCount'], 1)

    def test_float_deteriorated(self):
        prev = [make_activity('A1', total_float=8.0)]
        curr = [make_activity('A1', total_float=2.0)]
        rows = ui.build_activity_movement(prev, curr)
        result = ui.compute_float_movement(rows, prev, curr)
        self.assertEqual(result['deterioratedCount'], 1)

    def test_newly_negative_float(self):
        prev = [make_activity('A1', total_float=1.0, is_critical=False)]
        curr = [make_activity('A1', total_float=-2.0, is_critical=True)]
        rows = ui.build_activity_movement(prev, curr)
        result = ui.compute_float_movement(rows, prev, curr)
        self.assertEqual(result['newlyNegativeFloatCount'], 1)

    def test_recovered_from_negative_float(self):
        prev = [make_activity('A1', total_float=-2.0, is_critical=True)]
        curr = [make_activity('A1', total_float=1.0, is_critical=False)]
        rows = ui.build_activity_movement(prev, curr)
        result = ui.compute_float_movement(rows, prev, curr)
        self.assertEqual(result['recoveredFromNegativeFloatCount'], 1)

    def test_newly_critical(self):
        prev = [make_activity('A1', total_float=5.0, is_critical=False)]
        curr = [make_activity('A1', total_float=5.0, is_critical=True)]
        rows = ui.build_activity_movement(prev, curr)
        result = ui.compute_float_movement(rows, prev, curr)
        self.assertEqual(result['newlyCriticalCount'], 1)

    def test_left_critical_path(self):
        prev = [make_activity('A1', total_float=0.0, is_critical=True)]
        curr = [make_activity('A1', total_float=5.0, is_critical=False)]
        rows = ui.build_activity_movement(prev, curr)
        result = ui.compute_float_movement(rows, prev, curr)
        self.assertEqual(result['leftCriticalPathCount'], 1)

    def test_prefers_imported_driving_path_flag_over_total_float(self):
        # onLongestPath explicitly False even though totalFloat<=0 would
        # otherwise imply critical — the imported P6 signal wins.
        prev = [make_activity('A1', total_float=0.0, onLongestPath=False)]
        curr = [make_activity('A1', total_float=0.0, onLongestPath=True)]
        rows = ui.build_activity_movement(prev, curr)
        result = ui.compute_float_movement(rows, prev, curr)
        self.assertEqual(result['newlyCriticalCount'], 1)


class LogicChangeTests(SimpleTestCase):
    def test_predecessor_added(self):
        prev = [make_activity('A1'), make_activity('A2')]
        curr = [make_activity('A1'), make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}])]
        result = ui.compute_logic_changes(prev, curr)
        self.assertEqual(result['addedCount'], 1)

    def test_predecessor_removed(self):
        prev = [make_activity('A1'), make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}])]
        curr = [make_activity('A1'), make_activity('A2')]
        result = ui.compute_logic_changes(prev, curr)
        self.assertEqual(result['removedCount'], 1)

    def test_relationship_type_changed(self):
        prev = [make_activity('A1'), make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}])]
        curr = [make_activity('A1'), make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'SS', 'lagDays': 0}])]
        result = ui.compute_logic_changes(prev, curr)
        change = next(c for c in result['changed'] if c['field'] == 'relationshipType')
        self.assertEqual(change['previous'], 'FS')
        self.assertEqual(change['current'], 'SS')

    def test_lag_changed(self):
        prev = [make_activity('A1'), make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}])]
        curr = [make_activity('A1'), make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 5}])]
        result = ui.compute_logic_changes(prev, curr)
        change = next(c for c in result['changed'] if c['field'] == 'lag')
        self.assertEqual(change['delta'], 5)

    def test_task_id_resolves_to_activity_id_and_name(self):
        # Simulates a real XER-style activity: `id` is the raw task_id,
        # `code` is the human-readable Activity ID, and the predecessor's
        # actId references the raw task_id (matching real P6 export shape).
        prev = [
            {'id': '9001', 'code': 'A1000', 'name': 'Mobilize Site', 'predecessors': [], 'successors': []},
            {'id': '9002', 'code': 'A1010', 'name': 'Excavate', 'predecessors': [{'actId': '9001', 'relType': 'FS', 'lagDays': 0}], 'successors': []},
        ]
        curr = [
            {'id': '9001', 'code': 'A1000', 'name': 'Mobilize Site', 'predecessors': [], 'successors': []},
            {'id': '9002', 'code': 'A1010', 'name': 'Excavate', 'predecessors': [{'actId': '9001', 'relType': 'SS', 'lagDays': 2}], 'successors': []},
        ]
        result = ui.compute_logic_changes(prev, curr)
        type_change = next(c for c in result['changed'] if c['field'] == 'relationshipType')
        self.assertEqual(type_change['predecessorId'], 'A1000')   # resolved code, not raw task_id 9001
        self.assertEqual(type_change['successorId'], 'A1010')
        # byActivity groups the same change under both endpoints, with a
        # human-readable label resolved from the id -> name map.
        labeled = next(c for c in result['byActivity']['A1010'] if c['field'] == 'relationshipType')
        self.assertIn('Mobilize Site', labeled['label'])


class ConstraintChangeTests(SimpleTestCase):
    def test_constraint_added(self):
        prev = [make_activity('A1', constraintType=None, constraintDate=None)]
        curr = [make_activity('A1', constraintType='CS_MEO', constraintDate='2026-09-15')]
        changes = ui.compute_constraint_changes(prev, curr)
        self.assertEqual(changes[0]['changeType'], 'ADDED')

    def test_constraint_removed(self):
        prev = [make_activity('A1', constraintType='CS_MEO', constraintDate='2026-09-15')]
        curr = [make_activity('A1', constraintType=None, constraintDate=None)]
        changes = ui.compute_constraint_changes(prev, curr)
        self.assertEqual(changes[0]['changeType'], 'REMOVED')

    def test_constraint_date_changed(self):
        prev = [make_activity('A1', constraintType='CS_MEO', constraintDate='2026-09-15')]
        curr = [make_activity('A1', constraintType='CS_MEO', constraintDate='2026-09-22')]
        changes = ui.compute_constraint_changes(prev, curr)
        self.assertEqual(changes[0]['changeType'], 'DATE_CHANGED')
        self.assertIn('2026-09-15', changes[0]['description'])
        self.assertIn('2026-09-22', changes[0]['description'])


class DurationChangeTests(SimpleTestCase):
    def test_original_duration_change(self):
        prev = [make_activity('A1', dur=20.0)]
        curr = [make_activity('A1', dur=30.0)]
        result = ui.compute_duration_changes(prev, curr)
        self.assertEqual(len(result['originalDurationChanges']), 1)
        self.assertEqual(result['originalDurationChanges'][0]['delta'], 10.0)

    def test_remaining_duration_change_separate_from_original(self):
        prev = [make_activity('A1', dur=20.0, pct_complete=0.0)]   # remainDur = 20
        curr = dict(make_activity('A1', dur=20.0, pct_complete=0.0))
        curr['remainDur'] = 14.0
        result = ui.compute_duration_changes(prev, [curr])
        self.assertEqual(len(result['originalDurationChanges']), 0)
        self.assertEqual(len(result['remainingDurationChanges']), 1)
        self.assertEqual(result['remainingDurationChanges'][0]['delta'], -6.0)


class MilestoneMovementTests(SimpleTestCase):
    def test_milestone_slipped(self):
        prev = [make_activity('M1', name='Turnover', is_milestone=True, b_finish='2026-09-01')]
        curr = [make_activity('M1', name='Turnover', is_milestone=True, b_finish='2026-09-01', earlyFinish='2026-09-10')]
        result = ui.compute_milestone_movement(prev, curr, DD_CURR)
        self.assertEqual(result['rows'][0]['classification'], 'SLIPPED')

    def test_milestone_improved(self):
        prev = [make_activity('M1', name='Turnover', is_milestone=True, b_finish='2026-09-10')]
        curr = [make_activity('M1', name='Turnover', is_milestone=True, b_finish='2026-09-10', earlyFinish='2026-09-01')]
        result = ui.compute_milestone_movement(prev, curr, DD_CURR)
        self.assertEqual(result['rows'][0]['classification'], 'IMPROVED')

    def test_milestone_completed(self):
        prev = [make_activity('M1', name='Turnover', is_milestone=True, b_finish='2026-08-01')]
        curr = [make_activity('M1', name='Turnover', is_milestone=True, b_finish='2026-08-01', finish='2026-08-01', pct_complete=100.0)]
        result = ui.compute_milestone_movement(prev, curr, DD_CURR)
        self.assertEqual(result['rows'][0]['classification'], 'COMPLETED_THIS_PERIOD')
        self.assertEqual(len(result['milestonesCompletedThisPeriod']), 1)

    def test_milestone_baseline_variance_distinct_from_this_update_movement(self):
        baseline = [make_activity('M1', name='Turnover', is_milestone=True, b_finish='2026-08-01')]
        prev = [make_activity('M1', name='Turnover', is_milestone=True, b_finish='2026-08-01', earlyFinish='2026-08-15')]
        curr = [make_activity('M1', name='Turnover', is_milestone=True, b_finish='2026-08-01', earlyFinish='2026-08-22')]
        result = ui.compute_milestone_movement(prev, curr, DD_CURR, baseline_activities=baseline)
        row = result['rows'][0]
        self.assertEqual(row['baselineVarianceDays'], 21)   # Aug 1 -> Aug 22 (vs approved baseline)
        self.assertEqual(row['movementDays'], 7)             # Aug 15 -> Aug 22 (this update only)
        self.assertNotEqual(row['baselineVarianceDays'], row['movementDays'])


class CriticalPathMovementTests(SimpleTestCase):
    def test_stayed_critical(self):
        prev = [make_activity('A1', total_float=0.0, is_critical=True, b_finish='2026-08-10')]
        curr = [make_activity('A1', total_float=0.0, is_critical=True, b_finish='2026-08-10')]
        rows = ui.build_activity_movement(prev, curr)
        result = ui.compute_critical_path_movement(rows, prev, curr)
        self.assertEqual(result['stayedCriticalCount'], 1)

    def test_became_critical(self):
        prev = [make_activity('A1', total_float=8.0, is_critical=False)]
        curr = [make_activity('A1', total_float=0.0, is_critical=True)]
        rows = ui.build_activity_movement(prev, curr)
        result = ui.compute_critical_path_movement(rows, prev, curr)
        self.assertEqual(result['becameCriticalCount'], 1)

    def test_left_critical(self):
        prev = [make_activity('A1', total_float=0.0, is_critical=True)]
        curr = [make_activity('A1', total_float=8.0, is_critical=False)]
        rows = ui.build_activity_movement(prev, curr)
        result = ui.compute_critical_path_movement(rows, prev, curr)
        self.assertEqual(result['leftCriticalPathCount'], 1)

    def test_critical_path_delay_uses_driving_endpoint_not_sum(self):
        # Two critical activities each slip 10 days along the SAME chain —
        # the driving-path delay must be ~10 days (the endpoint's own
        # movement), never 20 (a naive sum across both activities).
        prev = [
            make_activity('A1', total_float=0.0, is_critical=True, b_start='2026-08-01', b_finish='2026-08-10'),
            make_activity('A2', total_float=0.0, is_critical=True, b_start='2026-08-10', b_finish='2026-08-20'),
        ]
        curr = [
            make_activity('A1', total_float=0.0, is_critical=True, b_start='2026-08-01', b_finish='2026-08-10', earlyFinish='2026-08-20'),
            make_activity('A2', total_float=0.0, is_critical=True, b_start='2026-08-10', b_finish='2026-08-20', earlyFinish='2026-08-30'),
        ]
        rows = ui.build_activity_movement(prev, curr)
        result = ui.compute_critical_path_movement(rows, prev, curr)
        self.assertEqual(result['criticalPathForecastDelayDays'], 10)

    def test_methodology_labeled_as_trace_not_cpm(self):
        result = ui.compute_critical_path_movement([], [], [])
        self.assertIn('Trace', result['methodologyNote'])
        self.assertNotIn('CPM recalculation', result['methodologyNote'].replace('not a ScheduleIQ CPM recalculation', ''))


class DriverAnalysisTests(SimpleTestCase):
    def test_ranks_by_slipped_count(self):
        prev = [make_activity('A1', discipline='Electrical', b_finish='2026-08-10'), make_activity('A2', discipline='Mechanical', b_finish='2026-08-10')]
        curr = [make_activity('A1', discipline='Electrical', b_finish='2026-08-10', earlyFinish='2026-08-20'),
                make_activity('A2', discipline='Mechanical', b_finish='2026-08-10')]
        rows = ui.build_activity_movement(prev, curr)
        float_result = ui.compute_float_movement(rows, prev, curr)
        commitment = {'available': True, 'startCommitments': [], 'finishCommitments': [], 'missedStarts': [], 'missedFinishes': []}
        result = ui.rank_deterioration_drivers(rows, float_result, commitment, 'discipline')
        self.assertEqual(result['drivers'][0]['group'], 'Electrical')
        self.assertEqual(result['drivers'][0]['slippedCount'], 1)

    def test_methodology_note_present(self):
        result = ui.rank_deterioration_drivers([], {'newlyNegativeFloat': [], 'newlyCritical': []}, {'missedStarts': [], 'missedFinishes': []}, 'area')
        self.assertIn('not a claim about overall project completion delay', result['methodologyNote'])


class BaselineVsMovementDistinctionTests(SimpleTestCase):
    def test_baseline_variance_distinct_from_update_movement(self):
        baseline = [make_activity('A1', b_finish='2026-08-01')]
        prev = [make_activity('A1', b_finish='2026-08-01', earlyFinish='2026-08-15')]
        curr = [make_activity('A1', b_finish='2026-08-01', earlyFinish='2026-08-22')]
        result = ui.build_update_intelligence(prev, curr, DD_PREV, DD_CURR, baseline_activities=baseline)
        row = result['movementRows'][0]
        self.assertEqual(row['baselineVarianceDays'], 21)   # Aug 1 -> Aug 22
        self.assertEqual(row['finishMovementDays'], 7)      # Aug 15 -> Aug 22 (this update only)
        self.assertNotEqual(row['baselineVarianceDays'], row['finishMovementDays'])


class SystemDateIndependenceTests(TestCase):
    """Critical regression: mocking system today must never change the
    result when the two versions' Data Dates are unchanged."""

    def test_result_identical_across_two_different_real_world_dates(self):
        prev = [make_activity('A1', b_finish='2026-08-10', total_float=5.0),
                make_activity('M1', name='Turnover', is_milestone=True, b_finish='2026-08-20')]
        curr = [make_activity('A1', b_finish='2026-08-10', earlyFinish='2026-08-17', total_float=-1.0, is_critical=True, start='2026-08-01'),
                make_activity('M1', name='Turnover', is_milestone=True, b_finish='2026-08-20', earlyFinish='2026-08-25')]

        def _run():
            return ui.build_update_intelligence(prev, curr, DD_PREV, DD_CURR)

        with mock.patch('scheduler.narrative_engine.date') as mocked:
            mocked.today.return_value = date(2020, 1, 1)
            mocked.side_effect = lambda *a, **kw: date(*a, **kw)
            result_a = _run()
        with mock.patch('scheduler.narrative_engine.date') as mocked:
            mocked.today.return_value = date(2031, 12, 31)
            mocked.side_effect = lambda *a, **kw: date(*a, **kw)
            result_b = _run()
        self.assertEqual(result_a, result_b)


class LookaheadChangeTests(SimpleTestCase):
    def test_carryover_activity(self):
        acts_prev = [make_activity('A1', b_start='2026-08-15', b_finish='2026-08-25')]
        acts_curr = [make_activity('A1', b_start='2026-08-15', b_finish='2026-08-25')]
        result = ui.compute_lookahead_change([], acts_prev, acts_curr, DD_PREV, DD_CURR, weeks=4)
        self.assertEqual(result['carryoverCount'], 1)

    def test_newly_entering_activity(self):
        acts_prev = [make_activity('A1', b_start='2027-06-01', b_finish='2027-06-10')]   # far future, not in prev window
        acts_curr = [make_activity('A1', b_start='2026-08-22', b_finish='2026-08-25')]   # now within curr's window
        result = ui.compute_lookahead_change([], acts_prev, acts_curr, DD_PREV, DD_CURR, weeks=4)
        self.assertEqual(result['newlyEnteringCount'], 1)

    def test_pushed_out_activity(self):
        acts_prev = [make_activity('A1', b_start='2026-08-15', b_finish='2026-08-20')]
        acts_curr = [make_activity('A1', b_start='2027-01-01', b_finish='2027-01-10')]   # pushed far out, incomplete
        result = ui.compute_lookahead_change([], acts_prev, acts_curr, DD_PREV, DD_CURR, weeks=4)
        self.assertEqual(result['pushedOutCount'], 1)

    def test_unavailable_without_data_dates(self):
        result = ui.compute_lookahead_change([], [], [], None, DD_CURR)
        self.assertFalse(result['available'])


class OrchestratorTests(SimpleTestCase):
    def test_full_payload_shape(self):
        prev = [make_activity('A1', b_finish='2026-08-10')]
        curr = [make_activity('A1', b_finish='2026-08-10', earlyFinish='2026-08-17')]
        result = ui.build_update_intelligence(prev, curr, DD_PREV, DD_CURR)
        for key in ('movementRows', 'movementCounts', 'forecastCommitment', 'reliability', 'floatMovement',
                    'logicChanges', 'constraintChanges', 'durationChanges', 'milestoneMovement',
                    'criticalPathMovement', 'criticalPathShiftSummary', 'driverAnalysis', 'narrative', 'lookaheadChange'):
            self.assertIn(key, result)
        self.assertEqual(result['previousDataDate'], '2026-08-14')
        self.assertEqual(result['currentDataDate'], '2026-08-21')
        self.assertEqual(result['periodDays'], 7)


class LargeScaleNoTruncationTests(SimpleTestCase):
    """
    Proves the update_intelligence.py ENGINE itself — not just the Excel
    export layer — never silently truncates, samples, or head-limits a
    large schedule. 12,000 activities is intentionally well above every
    documented performance benchmark (2,500/5,000/10,000) to prove those
    numbers are benchmarks, not limits.
    """
    N = 12000

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.prev = [
            make_activity(f'ACT-{i:05d}', name=f'Activity {i}', wbs=f'WBS-{i % 25}',
                          b_finish='2026-08-10', total_float=5.0)
            for i in range(cls.N)
        ]
        # Current version: every matched activity slips 3 days; last 50 codes
        # are dropped (REMOVED); 40 brand-new codes appended (NEW) — a
        # realistic mixed-population update, not a uniform copy.
        cls.curr = [
            make_activity(f'ACT-{i:05d}', name=f'Activity {i}', wbs=f'WBS-{i % 25}',
                          b_finish='2026-08-10', earlyFinish='2026-08-13', total_float=5.0)
            for i in range(cls.N - 50)
        ] + [
            make_activity(f'NEW-{i:05d}', name=f'New Activity {i}', wbs='WBS-0', b_finish='2026-08-20')
            for i in range(40)
        ]

    def test_movement_rows_reconcile_matched_new_removed(self):
        rows = ui.build_activity_movement(self.prev, self.curr)
        matched = [r for r in rows if r['matchStatus'] == 'MATCHED']
        new = [r for r in rows if r['matchStatus'] == 'NEW']
        removed = [r for r in rows if r['matchStatus'] == 'REMOVED']
        # Every previous or current activity must be accounted for exactly once.
        self.assertEqual(len(matched) + len(removed), len(self.prev))
        self.assertEqual(len(matched) + len(new), len(self.curr))
        self.assertEqual(len(rows), len(matched) + len(new) + len(removed))
        self.assertEqual(len(new), 40)
        self.assertEqual(len(removed), 50)
        self.assertEqual(len(matched), self.N - 50)

    def test_known_activity_near_end_of_dataset_is_analyzed(self):
        rows = ui.build_activity_movement(self.prev, self.curr)
        by_id = {r['activityId']: r for r in rows}
        # ACT-11999 is the very last surviving code (N-1=11999, matched side
        # only drops the *last 50* i.e. ACT-11950..ACT-11999 are REMOVED —
        # pick one from just before that boundary to assert MATCHED, and one
        # from inside the removed tail to prove tail activities are still
        # present in the output, just correctly classified.)
        self.assertIn('ACT-11949', by_id)
        self.assertEqual(by_id['ACT-11949']['matchStatus'], 'MATCHED')
        self.assertEqual(by_id['ACT-11949']['flags'], ['SLIPPED'])
        self.assertIn('ACT-11999', by_id)
        self.assertEqual(by_id['ACT-11999']['matchStatus'], 'REMOVED')

    def test_orchestrator_does_not_cap_movement_rows(self):
        result = ui.build_update_intelligence(self.prev, self.curr, DD_PREV, DD_CURR)
        # movementRows carries MATCHED + NEW + REMOVED — the complete union
        # of both versions' populations, never a truncated subset.
        self.assertEqual(len(result['movementRows']), self.N + 40)
        self.assertEqual(result['movementCounts']['slipped'], self.N - 50)
        self.assertEqual(result['movementCounts']['new'], 40)
        self.assertEqual(result['movementCounts']['removed'], 50)

    def test_orchestrator_does_not_cap_driver_analysis_full_population(self):
        result = ui.build_update_intelligence(self.prev, self.curr, DD_PREV, DD_CURR)
        drivers = result['driverAnalysis']['drivers']
        total_activities_counted = sum(d['totalActivityCount'] for d in drivers)
        self.assertEqual(total_activities_counted, self.N + 40)


# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────

def _act(code, name, bs, bf, wbs='Area A', pct=0.0, milestone=False, **extra):
    d = {
        'id': code, 'code': code, 'name': name, 'dur': 10.0, 'remainDur': 10.0 * (1 - pct / 100),
        'pctComplete': pct, 'bStart': bs, 'bFinish': bf, 'totalFloat': 0.0, 'isCritical': True,
        'isMilestone': milestone, 'wbs': wbs,
    }
    d.update(extra)
    return d


class VersionPairResolutionApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='UI Version Pair Project')
        acts = [_act('A1', 'X', '2026-01-01', '2026-01-10')]
        self.baseline = ScheduleUpload.objects.create(
            project=self.project, original_filename='bl.xer', sanitized_filename='bl.xer', file_type='XER',
            data_date=date(2026, 1, 9), version_label='Baseline', schedule_classification='APPROVED_BASELINE',
            activities_json=acts, upload_timestamp=datetime(2026, 1, 9, tzinfo=dt_timezone.utc),
        )
        self.u1 = ScheduleUpload.objects.create(
            project=self.project, original_filename='u1.xer', sanitized_filename='u1.xer', file_type='XER',
            data_date=date(2026, 8, 7), version_label='Update 01', schedule_classification='CURRENT_UPDATE',
            activities_json=acts, upload_timestamp=datetime(2026, 8, 7, tzinfo=dt_timezone.utc),
        )
        self.u2 = ScheduleUpload.objects.create(
            project=self.project, original_filename='u2.xer', sanitized_filename='u2.xer', file_type='XER',
            data_date=date(2026, 8, 14), version_label='Update 02', schedule_classification='CURRENT_UPDATE',
            activities_json=acts, upload_timestamp=datetime(2026, 8, 14, tzinfo=dt_timezone.utc),
        )
        self.u3 = ScheduleUpload.objects.create(
            project=self.project, original_filename='u3.xer', sanitized_filename='u3.xer', file_type='XER',
            data_date=date(2026, 8, 21), version_label='Update 03', schedule_classification='CURRENT_UPDATE',
            activities_json=acts, upload_timestamp=datetime(2026, 8, 21, tzinfo=dt_timezone.utc),
        )
        self.base = f'/api/projects/{self.project.id}/update-intelligence/'

    def test_automatic_previous_resolution_skips_baseline_when_update_exists(self):
        resp = self.client.get(self.base, {'currentVersion': str(self.u3.id)})
        body = resp.json()
        self.assertEqual(body['previousVersionLabel'], 'Update 02')
        self.assertEqual(body['baselineVersionLabel'], 'Baseline')

    def test_previous_falls_back_to_baseline_when_no_update_exists(self):
        resp = self.client.get(self.base, {'currentVersion': str(self.u1.id)})
        body = resp.json()
        self.assertEqual(body['previousVersionLabel'], 'Baseline')

    def test_explicit_version_override(self):
        resp = self.client.get(self.base, {'currentVersion': str(self.u3.id), 'previousVersion': str(self.u1.id)})
        body = resp.json()
        self.assertEqual(body['previousVersionLabel'], 'Update 01')

    def test_no_previous_version_returns_unavailable_not_error(self):
        empty_project = Project.objects.create(name='UI Single Version Project')
        ScheduleUpload.objects.create(
            project=empty_project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            data_date=date(2026, 8, 21), version_label='Only Update', schedule_classification='CURRENT_UPDATE',
            activities_json=[_act('A1', 'X', '2026-01-01', '2026-01-10')],
        )
        resp = self.client.get(f'/api/projects/{empty_project.id}/update-intelligence/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['available'])

    def test_never_compares_across_projects(self):
        other_project = Project.objects.create(name='Other Project')
        other_version = ScheduleUpload.objects.create(
            project=other_project, original_filename='o.xer', sanitized_filename='o.xer', file_type='XER',
            data_date=date(2026, 8, 20), version_label='Other', schedule_classification='CURRENT_UPDATE',
            activities_json=[_act('A1', 'X', '2026-01-01', '2026-01-10')],
        )
        # previousVersion from a different project must not resolve.
        resp = self.client.get(self.base, {'currentVersion': str(self.u3.id), 'previousVersion': str(other_version.id)})
        body = resp.json()
        self.assertFalse(body['available'])


class UpdateIntelligenceApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='UI API Project')
        prev_acts = [_act('A1', 'Slips This Update', '2026-08-01', '2026-08-10')]
        curr_acts = [_act('A1', 'Slips This Update', '2026-08-01', '2026-08-10', earlyFinish='2026-08-17')]
        self.prev = ScheduleUpload.objects.create(
            project=self.project, original_filename='p.xer', sanitized_filename='p.xer', file_type='XER',
            data_date=DD_PREV, version_label='Update 02', schedule_classification='CURRENT_UPDATE',
            activities_json=prev_acts, upload_timestamp=datetime(2026, 8, 14, tzinfo=dt_timezone.utc),
        )
        self.curr = ScheduleUpload.objects.create(
            project=self.project, original_filename='c.xer', sanitized_filename='c.xer', file_type='XER',
            data_date=DD_CURR, version_label='Update 03', schedule_classification='CURRENT_UPDATE',
            activities_json=curr_acts, upload_timestamp=datetime(2026, 8, 21, tzinfo=dt_timezone.utc),
        )
        self.base = f'/api/projects/{self.project.id}/update-intelligence/'

    def test_response_shape_and_data_dates(self):
        resp = self.client.get(self.base)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['available'])
        self.assertEqual(body['previousDataDate'], DD_PREV.isoformat())
        self.assertEqual(body['currentDataDate'], DD_CURR.isoformat())
        self.assertEqual(body['movementCounts']['slipped'], 1)

    def test_project_not_found_404(self):
        resp = self.client.get('/api/projects/00000000-0000-0000-0000-000000000000/update-intelligence/')
        self.assertEqual(resp.status_code, 404)

    def test_invalid_lookahead_weeks_400(self):
        resp = self.client.get(self.base, {'lookaheadWeeks': 'nope'})
        self.assertEqual(resp.status_code, 400)
