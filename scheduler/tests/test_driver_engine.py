from datetime import date

from django.test import SimpleTestCase

from scheduler.driver_engine import (
    rank_cost_drivers,
    rank_forecast_growth_drivers,
    rank_productivity_drivers,
    rank_schedule_drivers,
)
from .fixtures import make_activity

DD = date(2026, 1, 6)


def _cost_act(code, budgeted, actual, remaining, group_field, group_value, pct_complete=50.0, **extra):
    kwargs = {group_field: group_value}
    kwargs.update(extra)
    return make_activity(
        code, dur=10.0, pct_complete=pct_complete, b_start='2026-01-01', b_finish='2026-01-11',
        budgetedCost=budgeted, actualCost=actual, remainingCost=remaining, isCostLoaded=True, **kwargs,
    )


class CostDriverRankingTests(SimpleTestCase):
    def test_ranked_worst_first(self):
        acts = [
            _cost_act('A1', 1_000_000, 1_450_000, -450_000, 'area', 'Area C'),   # CV = -450K
            _cost_act('A2', 1_000_000, 1_310_000, -310_000, 'area', 'Area D'),   # CV = -310K
            _cost_act('A3', 1_000_000, 900_000, 100_000, 'area', 'Area A'),      # CV = +100K (favorable)
        ]
        result = rank_cost_drivers(acts, 'area', DD)
        groups_in_order = [d['group'] for d in result['drivers']]
        self.assertEqual(groups_in_order, ['Area C', 'Area D', 'Area A'])

    def test_contribution_percentage(self):
        # pct_complete=100 -> EV == budgetedCost exactly, so CV = BAC - AC, easy to verify by hand.
        acts = [
            _cost_act('A1', 1_000_000, 1_450_000, -450_000, 'area', 'Area C', pct_complete=100.0),   # CV = -450K
            _cost_act('A2', 1_000_000, 1_310_000, -310_000, 'area', 'Area D', pct_complete=100.0),   # CV = -310K
        ]
        result = rank_cost_drivers(acts, 'area', DD)
        total_cv = result['projectCv']
        self.assertAlmostEqual(total_cv, -760_000, delta=1)
        area_c = next(d for d in result['drivers'] if d['group'] == 'Area C')
        self.assertAlmostEqual(area_c['contributionPct'], -450_000 / -760_000 * 100, places=1)

    def test_reconciliation_matches(self):
        acts = [
            _cost_act('A1', 1_000_000, 1_450_000, -450_000, 'area', 'Area C', pct_complete=100.0),
            _cost_act('A2', 1_000_000, 1_310_000, -310_000, 'area', 'Area D', pct_complete=100.0),
        ]
        result = rank_cost_drivers(acts, 'area', DD)
        self.assertTrue(result['reconciliation']['reconciled'])

    def test_groups_without_cost_data_excluded_not_zeroed(self):
        acts = [
            _cost_act('A1', 1_000_000, 1_450_000, -450_000, 'discipline', 'Electrical'),
            make_activity('A2', dur=10.0, pct_complete=50.0, discipline='Mechanical'),   # no cost load
        ]
        result = rank_cost_drivers(acts, 'discipline', DD)
        groups = [d['group'] for d in result['drivers']]
        self.assertNotIn('Mechanical', groups)
        self.assertEqual(result['excludedGroupCount'], 1)

    def test_unknown_group_by_returns_error(self):
        result = rank_cost_drivers([make_activity('A1')], 'not_a_field', DD)
        self.assertIn('error', result)

    def test_ranking_by_discipline(self):
        acts = [
            _cost_act('A1', 500_000, 700_000, -200_000, 'discipline', 'Mechanical'),
            _cost_act('A2', 500_000, 550_000, -50_000, 'discipline', 'Electrical'),
        ]
        result = rank_cost_drivers(acts, 'discipline', DD)
        self.assertEqual(result['drivers'][0]['group'], 'Mechanical')


class ScheduleDriverRankingTests(SimpleTestCase):
    def test_ranked_by_sv_worst_first(self):
        acts = [
            _cost_act('A1', 1_000_000, 500_000, 500_000, 'wbs', 'Behind', pct_complete=30.0),
            _cost_act('A2', 1_000_000, 500_000, 500_000, 'wbs', 'OnTrack', pct_complete=70.0),
        ]
        result = rank_schedule_drivers(acts, 'wbs', DD)
        self.assertEqual(result['drivers'][0]['group'], 'Behind')

    def test_wbs_ranking(self):
        acts = [_cost_act('A1', 1_000_000, 500_000, 500_000, 'wbs', 'Foundations', pct_complete=20.0)]
        result = rank_schedule_drivers(acts, 'wbs', DD)
        self.assertEqual(result['groupBy'], 'wbs')
        self.assertEqual(len(result['drivers']), 1)


def _hours_act(code, budgeted, actual, remaining, group_field, group_value, pct_complete=50.0):
    kwargs = {group_field: group_value}
    return make_activity(
        code, dur=10.0, pct_complete=pct_complete,
        budgetedHours=budgeted, actualHours=actual, remainingHours=remaining, isResourceLoaded=True, **kwargs,
    )


class ProductivityDriverRankingTests(SimpleTestCase):
    def test_worst_productivity_first(self):
        acts = [
            _hours_act('A1', 100, 100, 50, 'discipline', 'Branch Power', pct_complete=50.0),   # earned=50, factor=0.5
            _hours_act('A2', 100, 40, 60, 'discipline', 'Civil', pct_complete=50.0),            # earned=50, factor=1.25
        ]
        result = rank_productivity_drivers(acts, 'discipline')
        self.assertEqual(result['drivers'][0]['group'], 'Branch Power')

    def test_unavailable_groups_excluded(self):
        acts = [
            _hours_act('A1', 100, 100, 50, 'discipline', 'Branch Power', pct_complete=50.0),
            make_activity('A2', dur=10.0, pct_complete=50.0, discipline='NoHours'),
        ]
        result = rank_productivity_drivers(acts, 'discipline')
        groups = [d['group'] for d in result['drivers']]
        self.assertNotIn('NoHours', groups)


class ForecastGrowthDriverRankingTests(SimpleTestCase):
    def test_growth_ranked_descending(self):
        current = [
            _cost_act('A1', 1_000_000, 900_000, 300_000, 'area', 'Area C', pct_complete=60.0),
            _cost_act('A2', 1_000_000, 500_000, 500_000, 'area', 'Area D', pct_complete=50.0),
        ]
        previous = [
            _cost_act('A1', 1_000_000, 500_000, 600_000, 'area', 'Area C', pct_complete=40.0),
            _cost_act('A2', 1_000_000, 480_000, 520_000, 'area', 'Area D', pct_complete=48.0),
        ]
        result = rank_forecast_growth_drivers(current, previous, 'area', DD, date(2025, 12, 1))
        self.assertGreaterEqual(len(result['drivers']), 1)
        # Largest growth first
        growths = [d['eacGrowth'] for d in result['drivers']]
        self.assertEqual(growths, sorted(growths, reverse=True))

    def test_group_missing_from_one_version_excluded(self):
        current = [_cost_act('A1', 1_000_000, 900_000, 300_000, 'area', 'Area C', pct_complete=60.0)]
        previous = [_cost_act('A1', 1_000_000, 500_000, 600_000, 'area', 'Area D', pct_complete=40.0)]
        result = rank_forecast_growth_drivers(current, previous, 'area', DD, date(2025, 12, 1))
        self.assertEqual(result['drivers'], [])
