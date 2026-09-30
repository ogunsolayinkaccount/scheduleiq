from datetime import date

from django.test import SimpleTestCase

from scheduler.cost_engine import (
    compute_cost_summary,
    compute_earned_value,
    compute_forecast,
    compute_planned_value,
    compute_productivity,
)
from .fixtures import make_activity


def _cost_act(code, budgeted_cost, actual_cost, remaining_cost, dur=10.0, pct_complete=0.0, **extra):
    """A cost-loaded activity: budgetedCost/actualCost/remainingCost set
    directly (not derived), isCostLoaded=True, with a baseline window for
    PV testing (bStart=2026-01-01, bFinish=2026-01-11 by default — a
    10-calendar-day span)."""
    return make_activity(
        code, dur=dur, pct_complete=pct_complete,
        b_start=extra.pop('b_start', '2026-01-01'), b_finish=extra.pop('b_finish', '2026-01-11'),
        budgetedCost=budgeted_cost, actualCost=actual_cost, remainingCost=remaining_cost,
        isCostLoaded=True, **extra,
    )


def _hours_act(code, budgeted_hours, actual_hours, remaining_hours, dur=10.0, pct_complete=0.0, **extra):
    return make_activity(
        code, dur=dur, pct_complete=pct_complete,
        budgetedHours=budgeted_hours, actualHours=actual_hours, remainingHours=remaining_hours,
        isResourceLoaded=True, **extra,
    )


DD = date(2026, 1, 6)   # 5 of 10 baseline calendar days elapsed -> planned fraction 0.5


class EarnedValueCoreTests(SimpleTestCase):
    def test_on_plan_project_cpi_and_spi_near_one(self):
        # 50% complete (duration method), 50% of budget spent, 50% of baseline elapsed.
        acts = [_cost_act('A1', 10000, 5000, 5000, dur=10.0, pct_complete=50.0)]
        result = compute_cost_summary(acts, DD)
        self.assertAlmostEqual(result['cost']['bac'], 10000)
        self.assertAlmostEqual(result['cost']['ev'], 5000)
        self.assertAlmostEqual(result['cost']['pv'], 5000)
        self.assertAlmostEqual(result['cost']['ac'], 5000)
        self.assertAlmostEqual(result['cost']['cpi'], 1.0)
        self.assertAlmostEqual(result['cost']['spi'], 1.0)

    def test_over_budget_project_cpi_below_one(self):
        acts = [_cost_act('A1', 10000, 8000, 2000, dur=10.0, pct_complete=50.0)]
        result = compute_earned_value(acts)
        self.assertEqual(result['cost']['cpi'], 5000 / 8000)
        self.assertLess(result['cost']['cpi'], 1.0)

    def test_under_budget_project_cpi_above_one(self):
        acts = [_cost_act('A1', 10000, 3000, 7000, dur=10.0, pct_complete=50.0)]
        result = compute_earned_value(acts)
        self.assertEqual(result['cost']['cpi'], 5000 / 3000)
        self.assertGreater(result['cost']['cpi'], 1.0)

    def test_ahead_of_schedule_spi_above_one(self):
        # 70% complete vs 50% of baseline elapsed.
        acts = [_cost_act('A1', 10000, 7000, 3000, dur=10.0, pct_complete=70.0)]
        result = compute_cost_summary(acts, DD)
        self.assertAlmostEqual(result['cost']['ev'], 7000)
        self.assertAlmostEqual(result['cost']['pv'], 5000)
        self.assertGreater(result['cost']['spi'], 1.0)

    def test_behind_schedule_spi_below_one(self):
        # 30% complete vs 50% of baseline elapsed.
        acts = [_cost_act('A1', 10000, 3000, 7000, dur=10.0, pct_complete=30.0)]
        result = compute_cost_summary(acts, DD)
        self.assertAlmostEqual(result['cost']['ev'], 3000)
        self.assertAlmostEqual(result['cost']['pv'], 5000)
        self.assertLess(result['cost']['spi'], 1.0)

    def test_zero_ac_cpi_unavailable_not_zero(self):
        # Not started: EV=0, AC=0 -> CPI must be None, never a fabricated 0 or inf.
        acts = [_cost_act('A1', 10000, 0, 10000, dur=10.0, pct_complete=0.0)]
        result = compute_earned_value(acts)
        self.assertIsNone(result['cost']['cpi'])

    def test_zero_pv_spi_unavailable_when_data_date_at_baseline_start(self):
        acts = [_cost_act('A1', 10000, 4000, 6000, dur=10.0, pct_complete=40.0)]
        result = compute_cost_summary(acts, date(2026, 1, 1))   # == bStart -> PV fraction 0
        self.assertEqual(result['cost']['pv'], 0.0)
        self.assertIsNone(result['cost']['spi'])   # division by zero guarded, never inf/None-masquerading-as-0

    def test_no_budget_bac_unavailable(self):
        acts = [make_activity('A1', dur=10.0, pct_complete=50.0)]   # no cost fields at all
        result = compute_earned_value(acts)
        self.assertFalse(result['cost']['available'])
        self.assertIsNone(result['cost']['bac'])
        self.assertIsNone(result['cost']['ev'])
        self.assertIsNone(result['cost']['ac'])

    def test_cost_loaded_but_zero_actual_cost_is_real_zero_not_unavailable(self):
        # Distinct from "no budget": cost-loaded, genuinely no spend yet.
        acts = [_cost_act('A1', 5000, 0, 5000, dur=10.0, pct_complete=0.0)]
        result = compute_earned_value(acts)
        self.assertTrue(result['cost']['available'])
        self.assertEqual(result['cost']['ac'], 0.0)

    def test_cost_data_without_hours(self):
        acts = [_cost_act('A1', 5000, 2000, 3000, dur=10.0, pct_complete=40.0)]
        result = compute_earned_value(acts)
        self.assertTrue(result['cost']['available'])
        self.assertFalse(result['hours']['available'])
        self.assertIsNone(result['hours']['bac'])

    def test_hours_without_cost(self):
        # Mirrors the real AWP2025-BL project found during the audit: resource
        # (hours) loaded, but zero cost-loaded assignments.
        acts = [_hours_act('A1', 204, 0, 204, dur=10.0, pct_complete=0.0)]
        result = compute_earned_value(acts)
        self.assertFalse(result['cost']['available'])
        self.assertIsNone(result['cost']['bac'])
        self.assertTrue(result['hours']['available'])
        self.assertEqual(result['hours']['bac'], 204)

    def test_partially_complete_activity_fraction_between_zero_and_one(self):
        acts = [_cost_act('A1', 1000, 400, 600, dur=10.0, pct_complete=40.0)]
        result = compute_earned_value(acts)
        self.assertAlmostEqual(result['cost']['ev'], 400.0)

    def test_completed_activity_earns_full_budget(self):
        acts = [_cost_act('A1', 1000, 1000, 0, dur=10.0, pct_complete=100.0)]
        result = compute_earned_value(acts)
        self.assertAlmostEqual(result['cost']['ev'], 1000.0)

    def test_activity_with_no_ev_method_input_is_excluded_not_zeroed(self):
        # PHYSICAL_PCT_COMPLETE requires pctCompleteType == 'CP_Phys'; this
        # activity never set it, so it must be excluded, and EV must be
        # reported unavailable (None), never a fabricated 0.0.
        acts = [_cost_act('A1', 1000, 500, 500, dur=10.0, pct_complete=50.0)]
        result = compute_earned_value(acts, method='PHYSICAL_PCT_COMPLETE')
        self.assertIsNone(result['cost']['ev'])
        self.assertEqual(result['cost']['activitiesExcluded'], 1)
        self.assertEqual(result['cost']['activitiesUsed'], 0)

    def test_physical_pct_complete_method_used_when_flagged_trustworthy(self):
        acts = [_cost_act(
            'A1', 1000, 600, 400, dur=10.0, pct_complete=50.0,
            pctCompleteType='CP_Phys', physPctComplete=65.0,
        )]
        result = compute_earned_value(acts, method='PHYSICAL_PCT_COMPLETE')
        self.assertAlmostEqual(result['cost']['ev'], 650.0)   # 65% of 1000, NOT the 50% duration pct

    def test_units_progress_method_uses_remaining_hours_not_duration(self):
        acts = [_cost_act(
            'A1', 1000, 300, 700, dur=10.0, pct_complete=20.0,   # duration says 20%
            isResourceLoaded=True, budgetedHours=100, remainingHours=70,   # units say 30% earned
        )]
        result = compute_earned_value(acts, method='UNITS_PROGRESS')
        self.assertAlmostEqual(result['cost']['ev'], 300.0)   # 30% of 1000, not 20%

    def test_methods_never_silently_mixed(self):
        acts = [_cost_act('A1', 1000, 500, 500, dur=10.0, pct_complete=50.0)]
        r1 = compute_earned_value(acts, method='DURATION_PCT_COMPLETE')
        r2 = compute_earned_value(acts, method='UNITS_PROGRESS')   # no hours data -> excluded, not a fallback
        self.assertEqual(r1['method'], 'DURATION_PCT_COMPLETE')
        self.assertEqual(r2['method'], 'UNITS_PROGRESS')
        self.assertIsNotNone(r1['cost']['ev'])
        self.assertIsNone(r2['cost']['ev'])


class PlannedValueTests(SimpleTestCase):
    def test_no_baseline_dates_pv_unavailable(self):
        acts = [make_activity('A1', b_start=None, b_finish=None, budgetedCost=1000, isCostLoaded=True, dur=10.0)]
        result = compute_planned_value(acts, DD)
        self.assertFalse(result['cost']['available'])
        self.assertIsNone(result['cost']['pv'])

    def test_data_date_before_baseline_start_is_zero(self):
        acts = [_cost_act('A1', 1000, 0, 1000, dur=10.0)]
        result = compute_planned_value(acts, date(2025, 12, 1))
        self.assertEqual(result['cost']['pv'], 0.0)

    def test_data_date_after_baseline_finish_is_full_budget(self):
        acts = [_cost_act('A1', 1000, 900, 100, dur=10.0, pct_complete=90.0)]
        result = compute_planned_value(acts, date(2026, 2, 1))
        self.assertEqual(result['cost']['pv'], 1000.0)

    def test_pv_warning_always_present_and_labels_the_approximation(self):
        acts = [_cost_act('A1', 1000, 0, 1000, dur=10.0)]
        result = compute_planned_value(acts, DD)
        self.assertIn('linear spread', result['warning'].lower())
        self.assertIn('not imported', result['warning'].lower())


class ForecastTests(SimpleTestCase):
    def test_different_eac_methodologies_all_present(self):
        acts = [_cost_act('A1', 10000, 6000, 5000, dur=10.0, pct_complete=50.0)]
        ev = compute_earned_value(acts)
        forecast = compute_forecast(ev, acts)
        methods = {s['methodology'] for s in forecast['scenarios']}
        self.assertIn('CPI_BASED', methods)
        self.assertIn('BOTTOM_UP', methods)

    def test_cpi_based_eac_formula(self):
        acts = [_cost_act('A1', 10000, 6000, 5000, dur=10.0, pct_complete=50.0)]
        ev = compute_earned_value(acts)
        forecast = compute_forecast(ev, acts)
        cpi_scenario = next(s for s in forecast['scenarios'] if s['methodology'] == 'CPI_BASED')
        # CPI = EV/AC = 5000/6000; EAC = BAC/CPI = 10000 / (5000/6000) = 12000
        self.assertAlmostEqual(cpi_scenario['eac'], 12000.0, places=1)

    def test_bottom_up_eac_uses_xer_remaining_cost(self):
        acts = [_cost_act('A1', 10000, 6000, 5000, dur=10.0, pct_complete=50.0)]
        ev = compute_earned_value(acts)
        forecast = compute_forecast(ev, acts)
        bu = next(s for s in forecast['scenarios'] if s['methodology'] == 'BOTTOM_UP')
        self.assertAlmostEqual(bu['eac'], 6000 + 5000)   # AC + ETC(remaining_cost)

    def test_composite_eac_scenario_via_cost_summary(self):
        acts = [_cost_act('A1', 10000, 3000, 7000, dur=10.0, pct_complete=30.0)]
        result = compute_cost_summary(acts, DD)   # 30% earned vs 50% planned -> SPI<1, CPI>1
        methods = {s['methodology'] for s in result['forecast']['scenarios']}
        self.assertIn('CPI_SPI_COMPOSITE', methods)

    def test_no_forecast_scenarios_when_not_cost_loaded(self):
        acts = [make_activity('A1', dur=10.0, pct_complete=50.0)]
        ev = compute_earned_value(acts)
        forecast = compute_forecast(ev, acts)
        self.assertEqual(forecast['scenarios'], [])


class ProductivityTests(SimpleTestCase):
    def test_productivity_factor_formula(self):
        acts = [_hours_act('A1', 100, 40, 60, dur=10.0, pct_complete=50.0)]   # earned = 50
        result = compute_productivity(acts)
        self.assertAlmostEqual(result['overall']['earnedHours'], 50.0)
        self.assertAlmostEqual(result['overall']['productivityFactor'], 50.0 / 40.0)
        self.assertAlmostEqual(result['overall']['hoursVariance'], 10.0)

    def test_zero_actual_hours_productivity_factor_unavailable(self):
        acts = [_hours_act('A1', 100, 0, 100, dur=10.0, pct_complete=0.0)]
        result = compute_productivity(acts)
        self.assertIsNone(result['overall']['productivityFactor'])

    def test_no_resource_loaded_activities_unavailable(self):
        acts = [make_activity('A1', dur=10.0, pct_complete=50.0)]
        result = compute_productivity(acts)
        self.assertFalse(result['overall']['available'])

    def test_aggregation_by_wbs(self):
        acts = [
            _hours_act('A1', 100, 50, 50, dur=10.0, pct_complete=50.0, wbs='Area A'),
            _hours_act('A2', 200, 100, 100, dur=10.0, pct_complete=50.0, wbs='Area B'),
        ]
        result = compute_productivity(acts, group_by='wbs')
        groups = {g['group']: g for g in result['groups']}
        self.assertEqual(groups['Area A']['budgetedHours'], 100)
        self.assertEqual(groups['Area B']['budgetedHours'], 200)

    def test_aggregation_by_discipline(self):
        acts = [
            _hours_act('A1', 100, 50, 50, dur=10.0, pct_complete=50.0, discipline='Electrical'),
            _hours_act('A2', 50, 50, 0, dur=10.0, pct_complete=100.0, discipline='Mechanical'),
        ]
        result = compute_productivity(acts, group_by='discipline')
        groups = {g['group']: g for g in result['groups']}
        self.assertEqual(groups['Electrical']['activityCount'], 1)
        self.assertEqual(groups['Mechanical']['earnedHours'], 50.0)

    def test_unknown_group_by_returns_error_not_crash(self):
        acts = [_hours_act('A1', 100, 50, 50, dur=10.0, pct_complete=50.0)]
        result = compute_productivity(acts, group_by='not_a_real_field')
        self.assertIn('error', result)
        self.assertEqual(result['groups'], [])


class HoursSideEvmTests(SimpleTestCase):
    """Locks in the hours-dimension CPI/SPI/EAC that reconcile.py's legacy
    manHours.CPI/SPI/EAC (which defaulted to 1.0/BAC when unavailable) —
    the authoritative replacement must never fabricate a fallback."""

    def test_hours_cpi_unavailable_when_zero_actual_hours(self):
        acts = [_hours_act('A1', 100, 0, 100, dur=10.0, pct_complete=0.0)]
        result = compute_cost_summary(acts, DD)
        self.assertIsNone(result['hours']['cpi'])

    def test_hours_spi_unavailable_when_no_baseline(self):
        acts = [make_activity('A1', b_start=None, b_finish=None, budgetedHours=100, isResourceLoaded=True, dur=10.0, pct_complete=50.0)]
        result = compute_cost_summary(acts, DD)
        self.assertIsNone(result['hours']['spi'])

    def test_hours_cpi_reflects_real_performance(self):
        acts = [_hours_act('A1', 100, 60, 40, dur=10.0, pct_complete=50.0)]   # earned=50, actual=60
        result = compute_cost_summary(acts, DD)
        self.assertAlmostEqual(result['hours']['cpi'], 50 / 60, places=3)

    def test_hours_forecast_scenarios_present_when_resource_loaded(self):
        acts = [_hours_act('A1', 100, 60, 40, dur=10.0, pct_complete=50.0)]
        result = compute_cost_summary(acts, DD)
        methods = {s['methodology'] for s in result['hoursForecast']['scenarios']}
        self.assertIn('CPI_BASED', methods)
        self.assertIn('BOTTOM_UP', methods)

    def test_hours_forecast_empty_when_not_resource_loaded(self):
        acts = [_cost_act('A1', 1000, 500, 500, dur=10.0, pct_complete=50.0)]   # cost-loaded only
        result = compute_cost_summary(acts, DD)
        self.assertEqual(result['hoursForecast']['scenarios'], [])

    def test_hours_and_cost_dimensions_are_independent(self):
        # Cost-loaded and resource-loaded with deliberately different
        # performance profiles — hours CPI must not leak into cost CPI or vice versa.
        acts = [make_activity(
            'A1', dur=10.0, pct_complete=50.0, b_start='2026-01-01', b_finish='2026-01-11',
            isCostLoaded=True, budgetedCost=1000, actualCost=1000,      # cost CPI = 0.5
            isResourceLoaded=True, budgetedHours=100, actualHours=25,  # hours CPI = 2.0
        )]
        result = compute_cost_summary(acts, DD)
        self.assertAlmostEqual(result['cost']['cpi'], 0.5)
        self.assertAlmostEqual(result['hours']['cpi'], 2.0)


class CostSummaryOrchestratorTests(SimpleTestCase):
    def test_tcpi_formula(self):
        acts = [_cost_act('A1', 10000, 4000, 6000, dur=10.0, pct_complete=40.0)]
        result = compute_cost_summary(acts, DD)
        # TCPI = (BAC - EV) / (BAC - AC) = (10000-4000)/(10000-4000) = 1.0
        self.assertAlmostEqual(result['cost']['tcpi'], 1.0)

    def test_tcpi_unavailable_when_bac_equals_ac(self):
        acts = [_cost_act('A1', 10000, 10000, 0, dur=10.0, pct_complete=50.0)]
        result = compute_cost_summary(acts, DD)
        self.assertIsNone(result['cost']['tcpi'])

    def test_engine_version_and_methods_present(self):
        acts = [_cost_act('A1', 1000, 500, 500, dur=10.0, pct_complete=50.0)]
        result = compute_cost_summary(acts, DD)
        self.assertEqual(result['evMethod'], 'DURATION_PCT_COMPLETE')
        self.assertEqual(result['pvMethod'], 'LINEAR_BASELINE_SPREAD')
        self.assertIn('engineVersion', result)
