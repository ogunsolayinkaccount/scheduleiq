from datetime import date

from django.test import SimpleTestCase

from scheduler.status_engine import (
    MilestoneInput,
    RC,
    ThresholdValues,
    classify_schedule,
)
from .fixtures import make_activity

DD = date(2026, 6, 1)


def _healthy_chain():
    return [
        # A completed activity needs real actual start/finish dates set (every
        # real parser sets these once pctComplete reaches 100) — omitting them
        # makes the progress-score calculation see it as "never started" and
        # drags the score below the AT_RISK threshold for the wrong reason.
        make_activity('A1', total_float=20.0, is_critical=False, pct_complete=100.0,
                       b_start='2026-01-01', b_finish='2026-01-10',
                       start='2026-01-01', finish='2026-01-10',
                       predecessors=[], successors=[{'actId': 'A2', 'relType': 'FS', 'lagDays': 0}]),
        make_activity('A2', total_float=20.0, is_critical=False, pct_complete=0.0,
                       b_start='2026-06-05', b_finish='2026-06-20',
                       predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}], successors=[]),
    ]


class GateTests(SimpleTestCase):
    def test_no_data_date_returns_undetermined(self):
        result = classify_schedule(current_activities=_healthy_chain(), data_date=None)
        self.assertEqual(result.status, 'UNDETERMINED')
        self.assertIn(RC.NO_DATA_DATE, result.reason_codes)

    def test_no_activities_returns_undetermined(self):
        result = classify_schedule(current_activities=[], data_date=DD)
        self.assertEqual(result.status, 'UNDETERMINED')
        self.assertIn(RC.NO_ACTIVITIES, result.reason_codes)

    def test_circular_logic_returns_undetermined(self):
        acts = [
            make_activity('A1', predecessors=[{'actId': 'A2', 'relType': 'FS', 'lagDays': 0}],
                           successors=[{'actId': 'A2', 'relType': 'FS', 'lagDays': 0}]),
            make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}],
                           successors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}]),
        ]
        result = classify_schedule(current_activities=acts, data_date=DD)
        self.assertEqual(result.status, 'UNDETERMINED')
        self.assertIn(RC.CIRCULAR_LOGIC, result.reason_codes)

    def test_low_quality_with_no_baseline_or_contract_is_undetermined(self):
        # Stack enough RED-severity quality checks (open ends + zero duration +
        # negative float + hard constraints) to push the score below the
        # min_schedule_quality_score gate, with nothing to compare against.
        acts = [
            make_activity(
                f'A{i}', predecessors=[], successors=[], dur=0.0, total_float=-5.0,
                pct_complete=0.0, constraintType='CS_MANDSTART',
            )
            for i in range(30)
        ]
        result = classify_schedule(current_activities=acts, data_date=DD)
        self.assertEqual(result.status, 'UNDETERMINED')
        self.assertIn(RC.QUALITY_BELOW_MINIMUM, result.reason_codes)


class ClassificationTests(SimpleTestCase):
    def test_healthy_schedule_with_baseline_is_on_track(self):
        current = _healthy_chain()
        baseline = _healthy_chain()
        result = classify_schedule(current_activities=current, baseline_activities=baseline, data_date=DD)
        self.assertEqual(result.status, 'ON_TRACK')

    def test_negative_float_beyond_threshold_is_off_track(self):
        acts = [
            make_activity('A1', total_float=-20.0, is_critical=True, pct_complete=20.0,
                           b_start='2026-01-01', b_finish='2026-05-01'),
        ]
        result = classify_schedule(current_activities=acts, data_date=DD,
                                    thresholds=ThresholdValues(milestone_red_days=14.0))
        self.assertEqual(result.status, 'OFF_TRACK')
        self.assertIn(RC.NEGATIVE_FLOAT_BEYOND_THRESHOLD, result.reason_codes)

    def test_contract_finish_exceeded_is_off_track(self):
        acts = [
            make_activity('A1', total_float=5.0, pct_complete=0.0,
                           b_start='2026-06-01', b_finish='2026-08-01'),
        ]
        result = classify_schedule(
            current_activities=acts, data_date=DD,
            contract_finish_date=date(2026, 6, 15),
        )
        self.assertEqual(result.status, 'OFF_TRACK')
        self.assertIn(RC.CONTRACT_FINISH_EXCEEDED, result.reason_codes)

    def test_delayed_contractual_milestone_is_off_track(self):
        acts = [make_activity('M1', is_milestone=True, dur=0.0, pct_complete=0.0)]
        milestones = [MilestoneInput(
            activity_id='M1', is_contractual=True,
            contract_required_date=date(2026, 5, 1),
        )]
        result = classify_schedule(current_activities=acts, milestone_inputs=milestones, data_date=DD)
        self.assertEqual(result.status, 'OFF_TRACK')

    def test_multiple_near_critical_paths_is_at_risk(self):
        acts = [make_activity(f'A{i}', total_float=3.0, is_critical=False, pct_complete=0.0) for i in range(5)]
        result = classify_schedule(current_activities=acts, data_date=DD,
                                    baseline_activities=acts, contract_finish_date=date(2026, 12, 1))
        self.assertEqual(result.status, 'AT_RISK')
        self.assertIn(RC.MULTIPLE_NEAR_CRITICAL_PATHS, result.reason_codes)


class MilestoneAnalysisTests(SimpleTestCase):
    def test_completed_milestone_within_tolerance(self):
        acts = [make_activity('M1', is_milestone=True, dur=0.0, pct_complete=100.0, finish='2026-05-01')]
        milestones = [MilestoneInput(activity_id='M1', contract_required_date=date(2026, 5, 5))]
        result = classify_schedule(current_activities=acts, milestone_inputs=milestones, data_date=DD)
        self.assertEqual(result.milestone_results[0]['status'], 'COMPLETE')

    def test_milestone_not_found_in_schedule(self):
        acts = [make_activity('A1')]
        milestones = [MilestoneInput(activity_id='DOES_NOT_EXIST', contract_required_date=date(2026, 5, 5))]
        result = classify_schedule(current_activities=acts, milestone_inputs=milestones, data_date=DD)
        self.assertIn('not found', result.milestone_results[0]['summary'])


class ConfidenceScoreTests(SimpleTestCase):
    def test_confidence_increases_with_baseline_previous_and_milestones(self):
        acts = _healthy_chain()
        bare = classify_schedule(current_activities=acts, data_date=DD)
        enriched = classify_schedule(
            current_activities=acts, baseline_activities=acts, previous_activities=acts,
            milestone_inputs=[MilestoneInput(activity_id='A1', is_contractual=True, contract_required_date=date(2026, 1, 10))],
            data_date=DD,
        )
        self.assertGreater(enriched.confidence_score, bare.confidence_score)


class RiskScoreBreakdownTests(SimpleTestCase):
    def test_risk_score_breakdown_contributions_sum_to_total(self):
        result = classify_schedule(current_activities=_healthy_chain(), data_date=DD)
        total_contribution = sum(v['contribution'] for v in result.risk_score_breakdown.values())
        self.assertAlmostEqual(result.risk_score, round(total_contribution, 1), places=1)

    def test_all_risk_components_present(self):
        result = classify_schedule(current_activities=_healthy_chain(), data_date=DD)
        expected = {'milestones', 'project_finish', 'critical_path', 'progress', 'float_health', 'quality', 'trend'}
        self.assertEqual(set(result.risk_score_breakdown.keys()), expected)


class SingleActivityAndMilestoneOnlyTests(SimpleTestCase):
    def test_single_completed_activity_no_crash(self):
        acts = [make_activity('A1', pct_complete=100.0, total_float=0.0, finish='2026-01-01')]
        result = classify_schedule(current_activities=acts, data_date=DD)
        self.assertIn(result.status, ('ON_TRACK', 'AT_RISK', 'OFF_TRACK', 'UNDETERMINED'))

    def test_milestone_only_schedule_no_crash(self):
        acts = [make_activity('M1', is_milestone=True, dur=0.0, pct_complete=0.0)]
        result = classify_schedule(current_activities=acts, data_date=DD)
        self.assertIn(result.status, ('ON_TRACK', 'AT_RISK', 'OFF_TRACK', 'UNDETERMINED'))

    def test_no_critical_activities_at_all(self):
        acts = [make_activity(f'A{i}', total_float=40.0, is_critical=False, pct_complete=50.0) for i in range(3)]
        result = classify_schedule(current_activities=acts, data_date=DD, baseline_activities=acts)
        self.assertIsNone(result.critical_path_float_days)
        self.assertEqual(result.critical_activity_count, 0)
