from django.test import SimpleTestCase

from scheduler.trend_engine import (
    build_comparison,
    build_eac_drift,
    compare_metric,
    detect_consecutive_trend,
    detect_threshold_crossing,
)


def _pt(date, cpi=None, spi=None, cv=None, sv=None, bac=None, ac=None, ev=None, pv=None,
        vac=None, productivity=None, eac_scenarios=None):
    return {
        'versionId': f'v-{date}', 'versionLabel': f'Update {date}', 'dataDate': date,
        'bac': bac, 'pv': pv, 'ev': ev, 'ac': ac, 'cv': cv, 'sv': sv, 'cpi': cpi, 'spi': spi, 'tcpi': None,
        'hoursBac': None, 'hoursEv': None, 'hoursAc': None,
        'budgetedHours': None, 'earnedHours': None, 'actualHours': None, 'remainingHours': None,
        'productivityFactor': productivity,
        'eacScenarios': eac_scenarios or {},
        'vac': vac,
    }


class CompareMetricTests(SimpleTestCase):
    def test_cpi_improving(self):
        r = compare_metric('cpi', 0.86, 0.92)
        self.assertEqual(r['direction'], 'improving')

    def test_cpi_deteriorating(self):
        r = compare_metric('cpi', 0.92, 0.86)
        self.assertEqual(r['direction'], 'deteriorating')

    def test_spi_improving(self):
        r = compare_metric('spi', 0.88, 0.95)
        self.assertEqual(r['direction'], 'improving')

    def test_spi_deteriorating(self):
        r = compare_metric('spi', 0.95, 0.88)
        self.assertEqual(r['direction'], 'deteriorating')

    def test_eac_increasing_is_deteriorating(self):
        r = compare_metric('eac', 10_800_000, 11_400_000)
        self.assertEqual(r['direction'], 'deteriorating')

    def test_eac_decreasing_is_improving(self):
        r = compare_metric('eac', 11_400_000, 10_800_000)
        self.assertEqual(r['direction'], 'improving')

    def test_vac_more_negative_is_deteriorating(self):
        # -$0.8M -> -$1.4M: VAC got worse (more negative)
        r = compare_metric('vac', -800_000, -1_400_000)
        self.assertEqual(r['direction'], 'deteriorating')

    def test_vac_less_negative_is_improving(self):
        r = compare_metric('vac', -1_400_000, -800_000)
        self.assertEqual(r['direction'], 'improving')

    def test_neutral_metric_never_gets_direction(self):
        # AC going up isn't inherently good or bad on its own.
        r = compare_metric('ac', 500_000, 900_000)
        self.assertEqual(r['direction'], 'unavailable')
        self.assertEqual(r['delta'], 400_000)

    def test_unavailable_when_either_side_missing(self):
        self.assertEqual(compare_metric('cpi', None, 0.9)['direction'], 'unavailable')
        self.assertEqual(compare_metric('cpi', 0.9, None)['direction'], 'unavailable')

    def test_small_relative_change_is_unchanged(self):
        r = compare_metric('cpi', 0.900, 0.901)
        self.assertEqual(r['direction'], 'unchanged')

    def test_pct_change_computed(self):
        r = compare_metric('cv', -1_000_000, -1_200_000)
        self.assertAlmostEqual(r['pctChange'], -20.0)


class BuildComparisonTests(SimpleTestCase):
    def test_no_versions_unavailable(self):
        r = build_comparison([])
        self.assertFalse(r['available'])

    def test_single_version_no_previous(self):
        r = build_comparison([_pt('2026-01-01', cpi=0.9)])
        self.assertTrue(r['available'])
        self.assertIsNone(r['previousVersion'])
        self.assertEqual(r['metrics']['cpi']['direction'], 'unavailable')

    def test_multiple_versions_compares_last_two(self):
        series = [
            _pt('2025-11-01', cpi=0.98),
            _pt('2025-12-01', cpi=0.92),
            _pt('2026-01-01', cpi=0.86),
        ]
        r = build_comparison(series)
        self.assertEqual(r['metrics']['cpi']['previous'], 0.92)
        self.assertEqual(r['metrics']['cpi']['current'], 0.86)
        self.assertEqual(r['metrics']['cpi']['direction'], 'deteriorating')

    def test_missing_intermediate_version_never_backfilled(self):
        # A version with cpi=None in the middle must not inherit a
        # neighboring value — build_comparison only looks at the last two
        # points as given, it never fills gaps.
        series = [
            _pt('2025-11-01', cpi=0.95),
            _pt('2025-12-01', cpi=None),   # unavailable this update
            _pt('2026-01-01', cpi=0.90),
        ]
        r = build_comparison(series)
        # previous point (Dec) had no CPI -> comparison must be unavailable, not backfilled from Nov.
        self.assertIsNone(r['metrics']['cpi']['previous'])
        self.assertEqual(r['metrics']['cpi']['direction'], 'unavailable')

    def test_unavailable_ac_reported_not_zero(self):
        series = [_pt('2025-12-01', ac=None), _pt('2026-01-01', ac=None)]
        r = build_comparison(series)
        self.assertIsNone(r['metrics']['ac']['current'])
        self.assertIsNone(r['metrics']['ac']['delta'])


class EacDriftTests(SimpleTestCase):
    def test_no_history_unavailable(self):
        r = build_eac_drift([])
        self.assertFalse(r['available'])

    def test_bottom_up_eac_increase_flagged_deteriorating(self):
        series = [
            _pt('2025-12-01', eac_scenarios={'BOTTOM_UP': {'eac': 11_800_000, 'vac': -800_000, 'label': 'Bottom-up'}}),
            _pt('2026-01-01', eac_scenarios={'BOTTOM_UP': {'eac': 12_220_000, 'vac': -1_220_000, 'label': 'Bottom-up'}}),
        ]
        r = build_eac_drift(series)
        bu = r['scenarios']['BOTTOM_UP']
        self.assertEqual(bu['delta'], 420_000)
        self.assertEqual(bu['direction'], 'deteriorating')

    def test_cpi_based_eac_improvement(self):
        series = [
            _pt('2025-12-01', eac_scenarios={'CPI_BASED': {'eac': 12_000_000, 'vac': -1_000_000, 'label': 'CPI-based'}}),
            _pt('2026-01-01', eac_scenarios={'CPI_BASED': {'eac': 11_820_000, 'vac': -820_000, 'label': 'CPI-based'}}),
        ]
        r = build_eac_drift(series)
        self.assertEqual(r['scenarios']['CPI_BASED']['direction'], 'improving')

    def test_scenarios_independent_not_blended(self):
        series = [
            _pt('2025-12-01', eac_scenarios={
                'CPI_BASED': {'eac': 12_000_000, 'vac': -1_000_000, 'label': 'CPI-based'},
                'BOTTOM_UP': {'eac': 11_500_000, 'vac': -500_000, 'label': 'Bottom-up'},
            }),
            _pt('2026-01-01', eac_scenarios={
                'CPI_BASED': {'eac': 11_800_000, 'vac': -800_000, 'label': 'CPI-based'},   # improved
                'BOTTOM_UP': {'eac': 12_000_000, 'vac': -1_000_000, 'label': 'Bottom-up'},  # deteriorated
            }),
        ]
        r = build_eac_drift(series)
        self.assertEqual(r['scenarios']['CPI_BASED']['direction'], 'improving')
        self.assertEqual(r['scenarios']['BOTTOM_UP']['direction'], 'deteriorating')

    def test_approved_eac_not_a_fabricated_trend(self):
        r = build_eac_drift(
            [_pt('2026-01-01', eac_scenarios={})],
            approved_eac={'cost': 12_400_000.0, 'description': 'PM approved', 'enteredAt': '2026-01-05'},
        )
        self.assertEqual(r['approvedEac']['value'], 12_400_000.0)
        self.assertIn('not tracked as a historical trend', r['approvedEac']['note'])

    def test_no_approved_eac_is_none(self):
        r = build_eac_drift([_pt('2026-01-01')])
        self.assertIsNone(r['approvedEac'])


class ConsecutiveTrendTests(SimpleTestCase):
    def test_cpi_improving_three_consecutive(self):
        series = [_pt('2025-10-01', cpi=0.80), _pt('2025-11-01', cpi=0.85), _pt('2025-12-01', cpi=0.90), _pt('2026-01-01', cpi=0.96)]
        r = detect_consecutive_trend(series, 'cpi')
        self.assertEqual(r['direction'], 'improving')
        self.assertEqual(r['consecutiveCount'], 3)
        self.assertIn('improved for 3 consecutive updates', r['observation'])

    def test_cpi_deteriorating_two_consecutive(self):
        series = [_pt('2025-11-01', cpi=0.98), _pt('2025-12-01', cpi=0.92), _pt('2026-01-01', cpi=0.86)]
        r = detect_consecutive_trend(series, 'cpi')
        self.assertEqual(r['direction'], 'deteriorating')
        self.assertEqual(r['consecutiveCount'], 2)

    def test_streak_broken_by_reversal(self):
        # deteriorating, deteriorating, IMPROVING -> current streak is 1 (improving)
        series = [_pt('2025-11-01', cpi=0.98), _pt('2025-12-01', cpi=0.92), _pt('2026-01-01', cpi=0.94)]
        r = detect_consecutive_trend(series, 'cpi')
        self.assertEqual(r['direction'], 'improving')
        self.assertEqual(r['consecutiveCount'], 1)

    def test_spi_below_one_multiple_updates_direction_tracked(self):
        series = [_pt('2025-11-01', spi=0.95), _pt('2025-12-01', spi=0.90), _pt('2026-01-01', spi=0.85)]
        r = detect_consecutive_trend(series, 'spi')
        self.assertEqual(r['direction'], 'deteriorating')

    def test_insufficient_data_unavailable(self):
        r = detect_consecutive_trend([_pt('2026-01-01', cpi=0.9)], 'cpi')
        self.assertFalse(r['available'])

    def test_missing_points_excluded_not_backfilled(self):
        series = [_pt('2025-11-01', cpi=0.90), _pt('2025-12-01', cpi=None), _pt('2026-01-01', cpi=0.80)]
        r = detect_consecutive_trend(series, 'cpi')
        # Only 2 usable points (Nov, Jan) -> single comparison, not a 2-step streak
        self.assertEqual(r['pointsConsidered'], 2)

    def test_productivity_factor_deterioration(self):
        series = [_pt('2025-12-01', productivity=1.05), _pt('2026-01-01', productivity=0.90)]
        r = detect_consecutive_trend(series, 'productivityFactor')
        self.assertEqual(r['direction'], 'deteriorating')


class ThresholdCrossingTests(SimpleTestCase):
    def test_cpi_crosses_below_threshold(self):
        series = [_pt('2025-12-01', cpi=0.92), _pt('2026-01-01', cpi=0.88)]
        r = detect_threshold_crossing(series, 'cpi', 0.90)
        self.assertTrue(r['crossed'])
        self.assertEqual(r['direction'], 'below')

    def test_spi_recovers_above_threshold(self):
        series = [_pt('2025-12-01', spi=0.88), _pt('2026-01-01', spi=0.96)]
        r = detect_threshold_crossing(series, 'spi', 0.95)
        self.assertTrue(r['crossed'])
        self.assertEqual(r['direction'], 'above')

    def test_no_crossing_when_already_below(self):
        series = [_pt('2025-12-01', cpi=0.80), _pt('2026-01-01', cpi=0.75)]
        r = detect_threshold_crossing(series, 'cpi', 0.90)
        self.assertFalse(r['crossed'])

    def test_insufficient_data(self):
        r = detect_threshold_crossing([_pt('2026-01-01', cpi=0.9)], 'cpi', 0.9)
        self.assertFalse(r['crossed'])
