from django.test import SimpleTestCase

from scheduler.progress_curve import compute_progress_curve
from .fixtures import make_activity


class ProgressCurveTests(SimpleTestCase):
    def test_basic_monthly_duration_weighted(self):
        activities = [
            make_activity('A1', b_finish='2026-01-15', dur=10, pct_complete=100.0),
            make_activity('A2', b_finish='2026-02-15', dur=10, pct_complete=50.0),
        ]
        result = compute_progress_curve(activities, weighting='duration', period='monthly')
        self.assertEqual(result['weighting'], 'duration')
        self.assertEqual(len(result['periods']), 2)
        jan, feb = result['periods']
        self.assertEqual(jan['currentPlanned'], 50.0)     # 10 of 20 total duration due by Jan
        self.assertEqual(jan['currentUpdateActual'], 50.0)  # A1 fully earned by Jan
        self.assertEqual(feb['currentPlanned'], 100.0)
        self.assertEqual(feb['currentUpdateActual'], 75.0)  # (10 + 5) / 20

    def test_count_weighted_mode(self):
        activities = [
            make_activity('A1', b_finish='2026-01-15', dur=100, pct_complete=100.0),
            make_activity('A2', b_finish='2026-01-20', dur=1, pct_complete=0.0),
        ]
        result = compute_progress_curve(activities, weighting='count', period='monthly')
        self.assertEqual(result['periods'][0]['currentPlanned'], 100.0)   # both activities finish in Jan
        self.assertEqual(result['periods'][0]['currentUpdateActual'], 50.0)  # 1 of 2 activities complete

    def test_baseline_and_previous_are_none_when_not_supplied(self):
        activities = [make_activity('A1', b_finish='2026-01-15', dur=10, pct_complete=100.0)]
        result = compute_progress_curve(activities)
        self.assertFalse(result['hasBaseline'])
        self.assertFalse(result['hasPreviousUpdate'])
        self.assertIsNone(result['periods'][0]['baselinePlanned'])
        self.assertIsNone(result['periods'][0]['previousUpdateActual'])

    def test_baseline_and_previous_populate_when_supplied(self):
        current = [make_activity('A1', b_finish='2026-01-15', dur=10, pct_complete=80.0)]
        baseline = [make_activity('A1', b_finish='2026-01-10', dur=10, pct_complete=0.0)]
        previous = [make_activity('A1', b_finish='2026-01-15', dur=10, pct_complete=40.0)]
        result = compute_progress_curve(current, baseline_activities=baseline, previous_activities=previous)
        self.assertTrue(result['hasBaseline'])
        self.assertTrue(result['hasPreviousUpdate'])
        jan = result['periods'][0]
        self.assertEqual(jan['baselinePlanned'], 100.0)
        self.assertEqual(jan['previousUpdateActual'], 40.0)

    def test_invalid_weighting_falls_back_to_duration(self):
        activities = [make_activity('A1', b_finish='2026-01-15', dur=10, pct_complete=100.0)]
        result = compute_progress_curve(activities, weighting='bogus')
        self.assertEqual(result['weighting'], 'duration')

    def test_no_earned_pct_field_is_fabricated(self):
        activities = [make_activity('A1', b_finish='2026-01-15', dur=10, pct_complete=100.0)]
        result = compute_progress_curve(activities)
        for period in result['periods']:
            self.assertNotIn('earnedPct', period)

    def test_daily_period_uses_same_weighting_engine(self):
        # Master Schedule Analysis phase — Daily is approved provided it
        # reuses the exact same weighting/cumulative math, not a separate
        # calculation. Two activities finishing on different days must
        # produce two distinct daily buckets with correctly cumulative %s.
        activities = [
            make_activity('A1', b_finish='2026-01-15', dur=10, pct_complete=100.0),
            make_activity('A2', b_finish='2026-01-16', dur=10, pct_complete=50.0),
        ]
        result = compute_progress_curve(activities, weighting='duration', period='daily')
        self.assertEqual(result['period'], 'daily')
        self.assertEqual(len(result['periods']), 2)
        day1, day2 = result['periods']
        self.assertEqual(day1['periodEnd'], '2026-01-15')
        self.assertEqual(day1['currentPlanned'], 50.0)
        self.assertEqual(day1['currentUpdateActual'], 50.0)
        self.assertEqual(day2['periodEnd'], '2026-01-16')
        self.assertEqual(day2['currentPlanned'], 100.0)
        self.assertEqual(day2['currentUpdateActual'], 75.0)

    def test_daily_period_supports_baseline_and_previous_same_as_weekly_monthly(self):
        current = [make_activity('A1', b_finish='2026-01-15', dur=10, pct_complete=80.0)]
        baseline = [make_activity('A1', b_finish='2026-01-10', dur=10, pct_complete=0.0)]
        result = compute_progress_curve(current, baseline_activities=baseline, period='daily')
        self.assertTrue(result['hasBaseline'])
        periods_by_end = {p['periodEnd']: p for p in result['periods']}
        self.assertEqual(periods_by_end['2026-01-10']['baselinePlanned'], 100.0)
        self.assertEqual(periods_by_end['2026-01-15']['currentUpdateActual'], 80.0)

    def test_invalid_period_falls_back_to_monthly(self):
        activities = [make_activity('A1', b_finish='2026-01-15', dur=10, pct_complete=100.0)]
        result = compute_progress_curve(activities, period='bogus')
        self.assertEqual(result['period'], 'monthly')
