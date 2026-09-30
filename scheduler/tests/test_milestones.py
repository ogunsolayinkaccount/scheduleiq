from datetime import date

from django.test import SimpleTestCase

from scheduler.milestones import build_milestone_report, detect_milestones, upcoming_within_horizon
from scheduler.schedule_comparison import compare_schedules
from .fixtures import make_activity


class DetectMilestonesTests(SimpleTestCase):
    def test_only_milestone_flagged_activities_returned(self):
        acts = [
            make_activity('A1', is_milestone=False),
            make_activity('M1', is_milestone=True, dur=0),
        ]
        result = detect_milestones(acts)
        self.assertEqual([m['code'] for m in result], ['M1'])


class MilestoneReportTests(SimpleTestCase):
    def test_baseline_variance_computed_when_baseline_supplied(self):
        current = [make_activity('M1', name='Substantial Completion', is_milestone=True, dur=0, b_finish='2026-06-15')]
        baseline = [make_activity('M1', name='Substantial Completion', is_milestone=True, dur=0, b_finish='2026-06-01')]
        report = build_milestone_report(current, baseline_activities=baseline)
        self.assertEqual(report[0]['varianceDays'], 14)
        self.assertEqual(report[0]['baselineFinish'], '2026-06-01')

    def test_variance_is_none_without_baseline(self):
        current = [make_activity('M1', is_milestone=True, dur=0, b_finish='2026-06-15')]
        report = build_milestone_report(current)
        self.assertIsNone(report[0]['varianceDays'])
        self.assertIsNone(report[0]['baselineFinish'])

    def test_movement_from_comparison_result(self):
        previous = [make_activity('M1', name='TCO', is_milestone=True, dur=0, b_finish='2026-06-01')]
        current = [make_activity('M1', name='TCO', is_milestone=True, dur=0, b_finish='2026-06-12')]
        cmp = compare_schedules(previous, current)
        report = build_milestone_report(current, comparison_result=cmp)
        self.assertEqual(report[0]['movementSinceLastUpdateDays'], 11)
        self.assertEqual(report[0]['previousUpdateFinish'], '2026-06-01')

    def test_driving_predecessor_resolved_from_raw_task_id(self):
        # Real XER data: a relationship's actId is the source file's raw
        # internal task_id, not the human Activity Code — milestones.py
        # previously looked up predecessors by raw actId directly against a
        # code-keyed map, silently finding nothing on real P6 data. Fixed to
        # resolve via schedule_comparison._id_to_code_name, matching the
        # convention already used by driving_chain.py/recovery_engine.py.
        pred = make_activity('A1', id='RAW_TASK_1', name='Driving Activity', total_float=3.0,
                              successors=[{'actId': 'RAW_TASK_2', 'relType': 'FS', 'lagDays': 0}])
        milestone = make_activity('M1', id='RAW_TASK_2', name='TCO', is_milestone=True, dur=0,
                                   predecessors=[{'actId': 'RAW_TASK_1', 'relType': 'FS', 'lagDays': 0}])
        report = build_milestone_report([pred, milestone])
        driving = report[0]['drivingPredecessor']
        self.assertIsNotNone(driving)
        self.assertEqual(driving['activityId'], 'A1')
        self.assertEqual(driving['totalFloat'], 3.0)

    def test_negative_float_milestone_is_critical(self):
        current = [make_activity('M1', is_milestone=True, dur=0, total_float=-3.0)]
        report = build_milestone_report(current)
        self.assertEqual(report[0]['riskLevel'], 'Critical')

    def test_driving_predecessor_picks_lowest_float(self):
        current = [
            make_activity('P1', total_float=10.0),
            make_activity('P2', total_float=-2.0),
            make_activity('M1', is_milestone=True, dur=0, predecessors=[
                {'actId': 'P1', 'relType': 'FS', 'lagDays': 0},
                {'actId': 'P2', 'relType': 'FS', 'lagDays': 0},
            ]),
        ]
        report = build_milestone_report(current)
        milestone = next(m for m in report if m['activityId'] == 'M1')
        self.assertEqual(milestone['drivingPredecessor']['activityId'], 'P2')


class UpcomingWithinHorizonTests(SimpleTestCase):
    def test_filters_by_configurable_horizon(self):
        report = [
            {'activityId': 'M1', 'currentFinish': '2026-06-10', 'status': 'Not Started'},
            {'activityId': 'M2', 'currentFinish': '2026-09-01', 'status': 'Not Started'},
            {'activityId': 'M3', 'currentFinish': '2026-06-05', 'status': 'Complete'},
        ]
        result = upcoming_within_horizon(report, date(2026, 6, 1), horizon_days=14)
        self.assertEqual([m['activityId'] for m in result], ['M1'])

    def test_different_horizon_values_change_result(self):
        report = [{'activityId': 'M1', 'currentFinish': '2026-07-01', 'status': 'Not Started'}]
        self.assertEqual(upcoming_within_horizon(report, date(2026, 6, 1), horizon_days=14), [])
        self.assertEqual(len(upcoming_within_horizon(report, date(2026, 6, 1), horizon_days=60)), 1)
