from django.test import SimpleTestCase

from scheduler.ai.providers.base import AIProvider, AIResponse
from scheduler.ai.providers.null_provider import NullProvider
from scheduler.executive_summary import (
    build_executive_narrative,
    enhance_narrative_with_ai,
    evaluate_control_signals,
    evaluate_forecast_deterioration,
)


def _comparison(cpi=None, spi=None, cv=None, sv=None, vac=None, cpi_prev=None, spi_prev=None):
    def _m(curr, prev):
        return {'current': curr, 'previous': prev, 'delta': None, 'pctChange': None,
                'direction': 'unavailable' if curr is None or prev is None else 'n/a'}
    return {
        'available': True,
        'currentVersion': {'id': 'v2', 'label': 'Update 2', 'dataDate': '2026-01-01'},
        'previousVersion': {'id': 'v1', 'label': 'Update 1', 'dataDate': '2025-12-01'},
        'metrics': {
            'cpi': _m(cpi, cpi_prev), 'spi': _m(spi, spi_prev), 'cv': _m(cv, None),
            'sv': _m(sv, None), 'vac': _m(vac, None),
        },
    }


class ControlSignalsTests(SimpleTestCase):
    def test_cost_problem_without_schedule_problem(self):
        comparison = _comparison(cpi=0.85, spi=0.98)
        signals = evaluate_control_signals(comparison)
        types = {s['type'] for s in signals}
        self.assertIn('COST_PROBLEM_WITHOUT_SCHEDULE_PROBLEM', types)
        self.assertNotIn('SCHEDULE_PROBLEM_WITHOUT_COST_PROBLEM', types)
        self.assertNotIn('COMBINED_DETERIORATION', types)

    def test_schedule_problem_without_cost_problem(self):
        comparison = _comparison(cpi=1.02, spi=0.80)
        signals = evaluate_control_signals(comparison)
        types = {s['type'] for s in signals}
        self.assertIn('SCHEDULE_PROBLEM_WITHOUT_COST_PROBLEM', types)
        self.assertNotIn('COST_PROBLEM_WITHOUT_SCHEDULE_PROBLEM', types)

    def test_combined_deterioration(self):
        comparison = _comparison(cpi=0.82, spi=0.79)
        signals = evaluate_control_signals(comparison)
        types = {s['type'] for s in signals}
        self.assertIn('COMBINED_DETERIORATION', types)

    def test_no_signals_when_both_healthy(self):
        comparison = _comparison(cpi=1.02, spi=1.01)
        signals = evaluate_control_signals(comparison)
        self.assertEqual(signals, [])

    def test_no_signals_when_metrics_unavailable(self):
        comparison = _comparison(cpi=None, spi=None)
        signals = evaluate_control_signals(comparison)
        self.assertEqual(signals, [])

    def test_productivity_deterioration_with_cost_impact(self):
        comparison = _comparison(cpi=0.85, spi=1.0)
        productivity_trend = {'available': True, 'current': 0.80, 'previous': 0.95, 'pctChange': -15.8}
        signals = evaluate_control_signals(comparison, productivity_trend)
        types = {s['type'] for s in signals}
        self.assertIn('PRODUCTIVITY_DETERIORATION_WITH_COST_IMPACT', types)
        self.assertIn('PRODUCTIVITY_DECLINE', types)

    def test_productivity_signal_absent_when_decline_under_threshold(self):
        comparison = _comparison(cpi=1.0, spi=1.0)
        productivity_trend = {'available': True, 'current': 0.98, 'previous': 1.0, 'pctChange': -2.0}
        signals = evaluate_control_signals(comparison, productivity_trend)
        self.assertEqual(signals, [])

    def test_forecast_deterioration_signal(self):
        eac_drift = {
            'available': True,
            'scenarios': {
                'BOTTOM_UP': {'label': 'Bottom-up', 'current': 12_200_000, 'previous': 11_800_000,
                              'delta': 400_000, 'direction': 'deteriorating'},
                'CPI_BASED': {'label': 'CPI-based', 'current': 11_800_000, 'previous': 12_000_000,
                              'delta': -200_000, 'direction': 'improving'},
            },
        }
        signals = evaluate_forecast_deterioration(eac_drift)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]['methodology'], 'BOTTOM_UP')

    def test_no_forecast_deterioration_when_all_improving(self):
        eac_drift = {'available': True, 'scenarios': {
            'CPI_BASED': {'label': 'CPI-based', 'current': 11_800_000, 'previous': 12_000_000,
                          'delta': -200_000, 'direction': 'improving'},
        }}
        self.assertEqual(evaluate_forecast_deterioration(eac_drift), [])


class ExecutiveNarrativeTests(SimpleTestCase):
    def test_full_data_produces_all_sections(self):
        comparison = _comparison(cpi=0.88, spi=0.90, cv=-1_200_000, sv=-500_000, vac=-1_600_000, cpi_prev=0.92, spi_prev=0.94)
        eac_drift = {
            'available': True,
            'scenarios': {'BOTTOM_UP': {'label': 'Bottom-up', 'current': 12_400_000, 'previous': 11_780_000, 'delta': 620_000, 'direction': 'deteriorating'}},
        }
        cpi_trend = {'available': True, 'direction': 'deteriorating', 'consecutiveCount': 2}
        spi_trend = {'available': True, 'direction': 'deteriorating', 'consecutiveCount': 2}
        cost_drivers = {'drivers': [
            {'group': 'Area C', 'cv': -470_000}, {'group': 'Area D', 'cv': -310_000},
        ]}
        productivity_drivers = {'groupBy': 'discipline', 'drivers': [{'group': 'Branch Power', 'productivityFactor': 0.76}]}

        result = build_executive_narrative(comparison, eac_drift, cpi_trend, spi_trend, cost_drivers, productivity_drivers)
        s = result['sections']
        self.assertIn('0.88', s['overallPerformance'])
        self.assertIn('unfavorable', s['overallPerformance'])
        self.assertIn('0.94', s['schedule'])
        self.assertIn('0.90', s['schedule'])
        self.assertIn('second consecutive', s['schedule'])
        self.assertIn('$620.0K', s['forecast'])
        self.assertIn('Area C', s['primaryDrivers'])
        self.assertIn('Area D', s['primaryDrivers'])
        self.assertIn('Branch Power', s['productivity'])
        self.assertIn('0.76', s['productivity'])

    def test_partial_data_omits_unavailable_sections(self):
        comparison = _comparison(cpi=None, spi=None, cv=None)
        eac_drift = {'available': False, 'scenarios': {}}
        result = build_executive_narrative(comparison, eac_drift, {}, {}, None, None)
        s = result['sections']
        self.assertIsNone(s['overallPerformance'])
        self.assertIsNone(s['schedule'])
        self.assertIsNone(s['forecast'])
        self.assertIsNone(s['primaryDrivers'])
        self.assertIsNone(s['productivity'])

    def test_no_fabricated_narrative_from_unavailable_metrics(self):
        # Only CPI/CV available -> overallPerformance produced, everything else stays None.
        comparison = _comparison(cpi=0.9, spi=None, cv=-100_000)
        result = build_executive_narrative(comparison, {'available': False, 'scenarios': {}}, {}, {}, None, None)
        s = result['sections']
        self.assertIsNotNone(s['overallPerformance'])
        self.assertIsNone(s['schedule'])
        self.assertIsNone(s['forecast'])

    def test_narrative_never_uses_causal_language(self):
        comparison = _comparison(cpi=0.85, spi=0.90, cv=-500_000, sv=-100_000, vac=-500_000, cpi_prev=0.9, spi_prev=0.95)
        cost_drivers = {'drivers': [{'group': 'Area C', 'cv': -500_000}]}
        result = build_executive_narrative(comparison, {'available': False, 'scenarios': {}}, {}, {}, cost_drivers, None)
        for text in result['sections'].values():
            if text:
                self.assertNotIn('caused', text.lower())


class _StubProvider(AIProvider):
    """Test double — returns a fixed rewrite (or error) without any
    network call, so these tests never depend on a real AI provider."""
    name = 'stub'

    def __init__(self, rewrite_map=None, ok=True, error=None):
        self.rewrite_map = rewrite_map or {}
        self.ok = ok
        self.error = error

    def is_configured(self):
        return True

    def complete(self, system_prompt, user_prompt):
        if not self.ok:
            return AIResponse(ok=False, error=self.error or 'stub failure')
        rewritten = self.rewrite_map.get(user_prompt, user_prompt)
        return AIResponse(ok=True, text=rewritten)


class AiNarrativeEnhancementTests(SimpleTestCase):
    def _narrative(self):
        return {
            'sections': {
                'overallPerformance': 'Project cost performance is unfavorable with CPI 0.88 and current cost variance of -$1.2M.',
                'schedule': None,
            },
        }

    def test_ai_unavailable_falls_back_to_deterministic(self):
        result = enhance_narrative_with_ai(self._narrative(), provider=NullProvider())
        self.assertFalse(result['aiEnhanced'])
        self.assertEqual(
            result['sections']['overallPerformance']['enhanced'],
            result['sections']['overallPerformance']['original'],
        )

    def test_ai_rewrite_used_when_numbers_preserved(self):
        original = self._narrative()['sections']['overallPerformance']
        provider = _StubProvider(rewrite_map={
            original: 'Cost performance is trending unfavorably at a CPI of 0.88, reflecting a -$1.2M variance.',
        })
        result = enhance_narrative_with_ai(self._narrative(), provider=provider)
        self.assertTrue(result['aiEnhanced'])
        self.assertNotEqual(
            result['sections']['overallPerformance']['enhanced'],
            result['sections']['overallPerformance']['original'],
        )
        self.assertEqual(result['sections']['overallPerformance']['original'], original)

    def test_ai_rewrite_rejected_when_it_introduces_a_new_number(self):
        original = self._narrative()['sections']['overallPerformance']
        # AI hallucinated a different CV figure — must be rejected.
        provider = _StubProvider(rewrite_map={
            original: 'Project cost performance is unfavorable with CPI 0.88 and current cost variance of -$1.5M.',
        })
        result = enhance_narrative_with_ai(self._narrative(), provider=provider)
        self.assertFalse(result['sections']['overallPerformance']['wasRewritten'])
        self.assertEqual(result['sections']['overallPerformance']['enhanced'], original)

    def test_ai_error_falls_back_to_deterministic(self):
        provider = _StubProvider(ok=False, error='rate limited')
        result = enhance_narrative_with_ai(self._narrative(), provider=provider)
        self.assertFalse(result['aiEnhanced'])
        self.assertEqual(
            result['sections']['overallPerformance']['enhanced'],
            result['sections']['overallPerformance']['original'],
        )

    def test_empty_section_never_sent_to_ai(self):
        provider = _StubProvider()
        result = enhance_narrative_with_ai(self._narrative(), provider=provider)
        self.assertIsNone(result['sections']['schedule']['enhanced'])
        self.assertFalse(result['sections']['schedule']['wasRewritten'])

    def test_original_always_preserved_verbatim(self):
        original = self._narrative()['sections']['overallPerformance']
        provider = _StubProvider(rewrite_map={original: 'A shorter rewrite with no numbers at all.'})
        result = enhance_narrative_with_ai(self._narrative(), provider=provider)
        self.assertEqual(result['sections']['overallPerformance']['original'], original)
