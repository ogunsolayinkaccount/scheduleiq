from datetime import date

from django.test import SimpleTestCase

from scheduler.narrative_engine import generate_narrative
from .fixtures import make_activity

DD = date(2026, 6, 1)


class MinimalInputTests(SimpleTestCase):
    def test_no_baseline_no_previous_no_contract_does_not_crash(self):
        acts = [make_activity('A1', total_float=10.0, pct_complete=50.0)]
        result = generate_narrative(current_activities=acts, data_date=DD)
        self.assertIn('executive_summary', result)
        self.assertTrue(any('No approved baseline' in lim or 'No previous' in lim for lim in result['limitations']))

    def test_single_activity_schedule(self):
        acts = [make_activity('A1', pct_complete=0.0)]
        result = generate_narrative(current_activities=acts, data_date=DD)
        self.assertEqual(result['summary_stats']['total_non_ms'], 1)

    def test_milestone_only_schedule(self):
        acts = [make_activity('M1', is_milestone=True, dur=0.0, pct_complete=0.0)]
        result = generate_narrative(current_activities=acts, data_date=DD)
        self.assertEqual(result['summary_stats']['total_non_ms'], 0)
        self.assertEqual(result['summary_stats']['milestone_count'], 1)


class StatusDrivenNarrativeTests(SimpleTestCase):
    def test_on_track_status_mentions_on_track(self):
        acts = [make_activity('A1', pct_complete=100.0)]
        result = generate_narrative(current_activities=acts, data_date=DD, status_result={'status': 'ON_TRACK', 'risk_score': 5, 'confidence_score': 80})
        self.assertIn('On Track', result['executive_summary'])

    def test_off_track_with_contract_variance_mentions_days_late(self):
        # For an incomplete activity, _fc() reads the P6 CPM forecast fields
        # (finish/earlyFinish/remainFinish/lateFinish) — not bFinish, which is
        # the baseline. earlyFinish is what a real XER import populates here.
        acts = [make_activity('A1', pct_complete=0.0, earlyFinish='2026-08-01')]
        result = generate_narrative(
            current_activities=acts, data_date=DD,
            contract_finish_date=date(2026, 6, 15),
            status_result={'status': 'OFF_TRACK', 'risk_score': 90, 'confidence_score': 60},
        )
        self.assertIn('Off Track', result['executive_summary'])
        self.assertEqual(result['contract_finish_variance_days'], 47)  # 2026-08-01 - 2026-06-15


class NegativeFloatClusteringTests(SimpleTestCase):
    def test_clustered_negative_float_produces_clustering_note(self):
        acts = [make_activity(f'A{i}', total_float=-5.0, pct_complete=20.0) for i in range(10)]
        result = generate_narrative(current_activities=acts, data_date=DD)
        self.assertIn('common float value', result['negative_float_narrative'])

    def test_no_negative_float_produces_clean_statement(self):
        acts = [make_activity('A1', total_float=10.0, pct_complete=20.0)]
        result = generate_narrative(current_activities=acts, data_date=DD)
        self.assertIn('No incomplete activities currently carry negative total float', result['negative_float_narrative'])


class BeiTests(SimpleTestCase):
    def test_bei_unavailable_without_baseline(self):
        acts = [make_activity('A1', pct_complete=50.0)]
        result = generate_narrative(current_activities=acts, data_date=DD)
        self.assertIsNone(result['summary_stats']['bei'])
        self.assertFalse(result['summary_stats']['bei_valid'])

    def test_bei_calculated_with_matching_baseline(self):
        baseline = [make_activity('A1', b_finish='2026-01-01', pct_complete=0.0)]
        current = [make_activity('A1', b_finish='2026-01-01', pct_complete=100.0)]
        result = generate_narrative(current_activities=current, baseline_activities=baseline, data_date=DD)
        self.assertIsNotNone(result['summary_stats']['bei'])
        self.assertTrue(result['summary_stats']['bei_valid'])
        self.assertEqual(result['summary_stats']['bei'], 1.0)

    def test_bei_low_match_rate_flagged_as_limitation(self):
        baseline = [make_activity(f'B{i}', b_finish='2026-01-01', pct_complete=0.0) for i in range(10)]
        current = [make_activity('B0', b_finish='2026-01-01', pct_complete=100.0)]  # only 1 of 10 matches
        result = generate_narrative(current_activities=current, baseline_activities=baseline, data_date=DD)
        self.assertFalse(result['summary_stats']['bei_valid'])
        self.assertTrue(any('reliability is low' in lim for lim in result['limitations']))


class FindingsAndRecommendationsTests(SimpleTestCase):
    def test_findings_sorted_by_priority_descending(self):
        acts = [make_activity(f'A{i}', total_float=-5.0, pct_complete=10.0) for i in range(5)]
        result = generate_narrative(
            current_activities=acts, data_date=DD,
            contract_finish_date=date(2026, 1, 1),  # forces a finish overrun -> CONTRACT_FINISH_LATE (priority 100)
        )
        priorities = [f['priority'] for f in result['findings']]
        self.assertEqual(priorities, sorted(priorities, reverse=True))

    def test_findings_deduplicated_by_metric(self):
        acts = [make_activity(f'A{i}', total_float=-5.0, pct_complete=10.0) for i in range(5)]
        result = generate_narrative(current_activities=acts, data_date=DD)
        metrics = [f['metric'] for f in result['findings']]
        self.assertEqual(len(metrics), len(set(metrics)))

    def test_max_findings_respected(self):
        acts = [make_activity(f'A{i}', total_float=-5.0, pct_complete=10.0, dur=0.0) for i in range(5)]
        result = generate_narrative(
            current_activities=acts, data_date=DD,
            contract_finish_date=date(2026, 1, 1),
            settings={'max_findings': 2},
        )
        self.assertLessEqual(len(result['findings']), 2)


class TrendNarrativeTests(SimpleTestCase):
    def test_trend_unavailable_without_previous(self):
        acts = [make_activity('A1')]
        result = generate_narrative(current_activities=acts, data_date=DD)
        self.assertIn('No previous schedule update', result['trend_narrative'])

    def test_trend_deterioration_detected(self):
        previous = [make_activity('A1', pct_complete=0.0, earlyFinish='2026-06-01')]
        current = [make_activity('A1', pct_complete=0.0, earlyFinish='2026-06-20')]
        result = generate_narrative(current_activities=current, previous_activities=previous, data_date=DD)
        self.assertIn('deteriorated', result['trend_narrative'])


class StructureTests(SimpleTestCase):
    def test_returns_all_expected_top_level_keys(self):
        acts = [make_activity('A1')]
        result = generate_narrative(current_activities=acts, data_date=DD)
        expected_keys = {
            'project_name', 'company', 'prepared_by', 'report_title', 'data_date',
            'report_date', 'status', 'risk_score', 'confidence_score', 'confidence_level',
            'executive_summary', 'current_position', 'critical_path_narrative',
            'negative_float_narrative', 'progress_narrative', 'quality_narrative',
            'trend_narrative', 'findings', 'recommendations', 'conclusion', 'limitations',
            'summary_stats', 'engine_version',
        }
        self.assertTrue(expected_keys.issubset(result.keys()))

    def test_settings_override_project_name_and_report_title(self):
        acts = [make_activity('A1')]
        result = generate_narrative(
            current_activities=acts, data_date=DD,
            settings={'project_name': 'Custom Project', 'report_title': 'Custom Title'},
        )
        self.assertEqual(result['project_name'], 'Custom Project')
        self.assertEqual(result['report_title'], 'Custom Title')
