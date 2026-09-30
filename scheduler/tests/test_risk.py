from datetime import date

from django.test import SimpleTestCase

from scheduler.schedule_comparison import compare_schedules
from scheduler.schedule_risk import compute_risk, risk_level, score_activity, score_group
from .fixtures import make_activity

DD = date(2026, 6, 1)


class RiskLevelTests(SimpleTestCase):
    def test_bands(self):
        self.assertEqual(risk_level(0), 'Healthy')
        self.assertEqual(risk_level(24.9), 'Healthy')
        self.assertEqual(risk_level(25), 'Watch')
        self.assertEqual(risk_level(49.9), 'Watch')
        self.assertEqual(risk_level(50), 'At Risk')
        self.assertEqual(risk_level(74.9), 'At Risk')
        self.assertEqual(risk_level(75), 'Critical')


class ScoreActivityTests(SimpleTestCase):
    def test_negative_float_increases_score(self):
        healthy = make_activity('A1', total_float=20.0, is_critical=False)
        negative = make_activity('A2', total_float=-10.0, is_critical=True)
        self.assertLess(score_activity(healthy, DD)['score'], score_activity(negative, DD)['score'])

    def test_healthy_activity_stays_low(self):
        a = make_activity('A1', total_float=30.0, is_critical=False, pct_complete=20.0,
                           b_start='2026-05-01', b_finish='2026-07-01')
        result = score_activity(a, DD)
        self.assertEqual(result['level'], 'Healthy')
        self.assertEqual(result['factors'], [])

    def test_complete_activity_is_never_scored(self):
        a = make_activity('A1', total_float=-20.0, pct_complete=100.0)
        result = score_activity(a, DD)
        self.assertEqual(result['score'], 0.0)

    def test_milestone_is_never_scored(self):
        a = make_activity('M1', is_milestone=True, total_float=-20.0, dur=0)
        result = score_activity(a, DD)
        self.assertEqual(result['score'], 0.0)

    def test_overdue_activity_flagged(self):
        a = make_activity('A1', b_finish='2026-05-01', pct_complete=50.0, total_float=5.0)
        result = score_activity(a, DD)
        types = {f['type'] for f in result['factors']}
        self.assertIn('overdue', types)


class ScoreGroupTests(SimpleTestCase):
    def test_all_healthy_group_scores_low(self):
        acts = [make_activity(f'A{i}', total_float=20.0, is_critical=False, pct_complete=10.0,
                               b_start='2026-05-01', b_finish='2026-08-01') for i in range(5)]
        result = score_group(acts, DD)
        self.assertEqual(result['level'], 'Healthy')

    def test_negative_float_concentration_drives_score_up(self):
        acts = [make_activity(f'A{i}', total_float=-10.0, is_critical=True) for i in range(5)]
        result = score_group(acts, DD)
        self.assertGreater(result['score'], 30)
        driver_types = {d['type'] for d in result['drivers']}
        self.assertIn('negative_float', driver_types)

    def test_missing_optional_metadata_handled_safely(self):
        # No area/discipline/contractor fields at all — must not crash.
        acts = [make_activity(f'A{i}', total_float=0.0) for i in range(3)]
        result = score_group(acts, DD)
        self.assertIn('score', result)

    def test_comparison_adds_finish_slip_driver(self):
        previous = [make_activity('A1', b_finish='2026-06-01')]
        current = [make_activity('A1', b_finish='2026-06-20')]
        cmp = compare_schedules(previous, current)
        result = score_group(current, DD, comparison_result=cmp)
        types = {d['type'] for d in result['drivers']}
        self.assertIn('finish_slip', types)


class ComputeRiskTests(SimpleTestCase):
    def test_group_by_area(self):
        acts = [
            make_activity('A1', total_float=-5.0, is_critical=True, area='Area C'),
            make_activity('A2', total_float=-5.0, is_critical=True, area='Area C'),
            make_activity('A3', total_float=20.0, is_critical=False, area='Area A'),
        ]
        result = compute_risk(acts, DD, group_by='area')
        by_key = {g['key']: g for g in result['groups']}
        self.assertIn('Area C', by_key)
        self.assertGreater(by_key['Area C']['score'], by_key['Area A']['score'])

    def test_group_by_contractor_with_ungrouped(self):
        acts = [
            make_activity('A1', total_float=-5.0, contractor='Acme'),
            make_activity('A2', total_float=5.0),   # no contractor set
        ]
        result = compute_risk(acts, DD, group_by='contractor')
        self.assertEqual(result['ungroupedActivityCount'], 1)

    def test_group_by_activity_returns_top_n(self):
        acts = [make_activity(f'A{i}', total_float=-1.0 * i) for i in range(1, 10)]
        result = compute_risk(acts, DD, group_by='activity', top_n=3)
        self.assertEqual(len(result['groups']), 3)
        scores = [g['score'] for g in result['groups']]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_no_group_by_returns_overall_only(self):
        acts = [make_activity('A1', total_float=5.0)]
        result = compute_risk(acts, DD)
        self.assertEqual(result['groups'], [])
        self.assertIn('overall', result)

    def test_unknown_group_by_does_not_crash(self):
        acts = [make_activity('A1')]
        result = compute_risk(acts, DD, group_by='bogus')
        self.assertEqual(result['groups'], [])
        self.assertIn('error', result)
