from datetime import date

from django.test import SimpleTestCase

from scheduler.quality_engine import assess_quality
from scheduler.status_engine import ThresholdValues
from .fixtures import make_activity

DD = date(2026, 6, 1)


class EmptyAndSingleActivityTests(SimpleTestCase):
    def test_empty_schedule_does_not_crash_and_scores_perfect(self):
        result = assess_quality([], DD)
        self.assertEqual(result['activity_count'], 0)
        self.assertEqual(result['overall_score'], 100.0)
        self.assertEqual(result['circular_logic_count'], 0)
        self.assertEqual(result['findings'], [])

    def test_single_activity_with_no_relationships_flags_open_ends(self):
        acts = [make_activity('A1', predecessors=[], successors=[])]
        result = assess_quality(acts, DD)
        self.assertTrue(result['checks']['open_ends']['flag'])
        self.assertEqual(result['checks']['open_ends']['pct'], 100.0)
        self.assertLess(result['overall_score'], 100.0)


class OpenEndsTests(SimpleTestCase):
    def test_missing_predecessors_and_successors_counted(self):
        acts = [
            make_activity('A1', predecessors=[], successors=[{'actId': 'A2', 'relType': 'FS', 'lagDays': 0}]),
            make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}], successors=[]),
        ]
        result = assess_quality(acts, DD)
        oe = result['checks']['open_ends']
        # A1 has no predecessor, A2 has no successor — both are legitimate
        # chain ends, not "missing logic", but the check counts them
        # structurally (it only forgives milestone start/end nodes).
        self.assertEqual(oe['no_predecessors'], 1)
        self.assertEqual(oe['no_successors'], 1)

    def test_start_and_end_milestones_are_not_penalized_as_open_ends(self):
        acts = [
            make_activity('M1', is_milestone=True, dur=0, predecessors=[], successors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}]),
            make_activity('A1', predecessors=[{'actId': 'M1', 'relType': 'FS', 'lagDays': 0}], successors=[{'actId': 'M2', 'relType': 'FS', 'lagDays': 0}]),
            make_activity('M2', is_milestone=True, dur=0, predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}], successors=[]),
        ]
        result = assess_quality(acts, DD)
        # Only the milestones are open-ended, and both are forgiven — the
        # single real activity (A1) is fully linked, so open ends should be 0.
        self.assertEqual(result['checks']['open_ends']['count'], 0)


class NegativeFloatAndCriticalTests(SimpleTestCase):
    def test_negative_float_flagged_and_reported_in_findings(self):
        acts = [make_activity(f'A{i}', total_float=-5.0, pct_complete=20.0) for i in range(5)]
        result = assess_quality(acts, DD)
        nf = result['checks']['negative_float']
        self.assertEqual(nf['count'], 5)
        self.assertEqual(nf['min_float_days'], -5.0)
        self.assertTrue(any(f['check'] == 'Negative Float' for f in result['findings']))

    def test_positive_float_within_range_not_flagged(self):
        acts = [make_activity(f'A{i}', total_float=5.0, pct_complete=20.0) for i in range(5)]
        result = assess_quality(acts, DD)
        self.assertEqual(result['checks']['negative_float']['count'], 0)
        self.assertFalse(result['checks']['negative_float']['flag'])

    def test_completed_activities_excluded_from_negative_float_check(self):
        acts = [make_activity('A1', total_float=-20.0, pct_complete=100.0)]
        result = assess_quality(acts, DD)
        self.assertEqual(result['checks']['negative_float']['count'], 0)

    def test_high_float_outliers_flagged(self):
        acts = [make_activity(f'A{i}', total_float=60.0, pct_complete=10.0) for i in range(10)]
        result = assess_quality(acts, DD, ThresholdValues(max_high_float_pct=5.0))
        self.assertTrue(result['checks']['high_float']['flag'])
        self.assertEqual(result['checks']['high_float']['count'], 10)


class DurationTests(SimpleTestCase):
    def test_unusually_long_duration_flagged(self):
        acts = [make_activity(f'A{i}', dur=45.0) for i in range(5)]
        result = assess_quality(acts, DD)
        dur = result['checks']['durations']
        self.assertEqual(dur['long_duration_count'], 5)
        self.assertTrue(dur['flag'])

    def test_zero_duration_non_milestone_flagged_as_no_duration(self):
        acts = [make_activity('A1', dur=0.0, is_milestone=False)]
        result = assess_quality(acts, DD)
        self.assertEqual(result['checks']['durations']['no_duration_count'], 1)
        self.assertEqual(result['checks']['durations']['severity'], 'RED')

    def test_milestones_excluded_from_duration_checks(self):
        acts = [make_activity('M1', is_milestone=True, dur=0.0)]
        result = assess_quality(acts, DD)
        self.assertEqual(result['checks']['durations']['no_duration_count'], 0)


class LagAndConstraintTests(SimpleTestCase):
    def test_negative_lag_flagged_as_red(self):
        acts = [
            make_activity('A1'),
            make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': -3}]),
        ]
        result = assess_quality(acts, DD)
        lags = result['checks']['lags']
        self.assertEqual(lags['negative_lag_count'], 1)
        self.assertEqual(lags['severity'], 'RED')

    def test_hard_constraint_flagged(self):
        acts = [make_activity(f'A{i}', constraintType='CS_MANDSTART') for i in range(3)]
        result = assess_quality(acts, DD)
        con = result['checks']['constraints']
        self.assertEqual(con['hard_constrained'], 3)
        self.assertEqual(con['severity'], 'RED')


class CircularLogicTests(SimpleTestCase):
    def test_circular_logic_detected(self):
        acts = [
            make_activity('A1', predecessors=[{'actId': 'A2', 'relType': 'FS', 'lagDays': 0}]),
            make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}]),
        ]
        result = assess_quality(acts, DD)
        self.assertEqual(result['circular_logic_count'], 2)
        self.assertTrue(any(f['check'] == 'Circular Logic' for f in result['findings']))
        # Circular logic is an automatic full 25-point deduction regardless of
        # every other check passing.
        self.assertLessEqual(result['overall_score'], 75.0)

    def test_no_circular_logic_in_a_normal_chain(self):
        acts = [
            make_activity('A1', predecessors=[]),
            make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}]),
            make_activity('A3', predecessors=[{'actId': 'A2', 'relType': 'FS', 'lagDays': 0}]),
        ]
        result = assess_quality(acts, DD)
        self.assertEqual(result['circular_logic_count'], 0)


class OutOfSequenceAndInvalidActualsTests(SimpleTestCase):
    def test_out_of_sequence_progress_flagged(self):
        acts = [
            make_activity('A1', pct_complete=0.0),  # predecessor still incomplete
            make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}], start='2026-05-01'),
        ]
        result = assess_quality(acts, DD)
        self.assertEqual(result['checks']['out_of_sequence']['count'], 1)

    def test_invalid_actual_dates_after_data_date_flagged(self):
        acts = [make_activity('A1', start='2026-07-01')]  # after DD of 2026-06-01
        result = assess_quality(acts, DD)
        ia = result['checks']['invalid_actuals']
        self.assertEqual(ia['count'], 1)
        self.assertIn('A1', ia['activity_ids'])


class OverallScoreTests(SimpleTestCase):
    def test_healthy_schedule_scores_high(self):
        acts = [
            make_activity('A1', predecessors=[], successors=[{'actId': 'A2', 'relType': 'FS', 'lagDays': 0}],
                           total_float=10.0, dur=5.0, pct_complete=100.0,
                           b_start='2026-01-01', b_finish='2026-01-06'),
            make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}], successors=[],
                           total_float=10.0, dur=5.0, pct_complete=0.0,
                           b_start='2026-06-10', b_finish='2026-06-15'),
        ]
        result = assess_quality(acts, DD)
        self.assertGreaterEqual(result['overall_score'], 85.0)

    def test_score_never_negative(self):
        # Stack every possible red flag onto a tiny schedule.
        acts = [
            make_activity('A1', predecessors=[{'actId': 'A2', 'relType': 'FS', 'lagDays': -5}],
                           total_float=-30.0, dur=0.0, constraintType='CS_MANDSTART'),
            make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}],
                           total_float=-30.0, dur=0.0, constraintType='CS_MANDSTART'),
        ]
        result = assess_quality(acts, DD)
        self.assertGreaterEqual(result['overall_score'], 0.0)
