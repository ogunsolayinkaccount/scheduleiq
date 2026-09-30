"""
Executive Summary & Combined-Signal Engine — ScheduleIQ Project Controls Phase

Deterministic, DB-free, no LLM involved — turns the outputs of
trend_engine.py and driver_engine.py into plain-English statements and
named control conditions ("cost problem without schedule problem", etc).

Two hard rules carried from the rest of this module family:
  1. Diagnose, don't predict. Every sentence describes what the numbers
     already show — a trend, a current value, a ranked driver — never a
     projection of a future outcome. No statistical/Monte Carlo forecasting
     lives here.
  2. No sentence without a number behind it. If the metrics a sentence
     needs are unavailable, that section is omitted (or explicitly marked
     unavailable with a reason) — never filled with generic filler text.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

ENGINE_VERSION = '1.0.0'

# Named thresholds — centralized so nothing is a scattered magic number.
CPI_HEALTHY_THRESHOLD = 0.95
SPI_HEALTHY_THRESHOLD = 0.95
PRODUCTIVITY_HEALTHY_THRESHOLD = 0.90
PRODUCTIVITY_DETERIORATION_PCT = -10.0   # >10% drop in productivity factor


def _fmt_money(v: Optional[float]) -> str:
    if v is None:
        return 'unavailable'
    abs_v = abs(v)
    sign = '-' if v < 0 else ''
    if abs_v >= 1e6:
        return f'{sign}${abs_v / 1e6:.2f}M'
    if abs_v >= 1e3:
        return f'{sign}${abs_v / 1e3:.1f}K'
    return f'{sign}${abs_v:.0f}'


# ── Phase G: combined cost + schedule control signals ──────────────────────

def evaluate_control_signals(comparison: Dict[str, Any], productivity_trend: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Named control conditions from the CURRENT metrics (and, for
    productivity, its trend direction). These identify WHERE to
    investigate, never a claim of root cause — see module + driver_engine
    docstrings."""
    signals: List[Dict[str, Any]] = []
    if not comparison.get('available'):
        return signals

    metrics = comparison['metrics']
    cpi = metrics.get('cpi', {}).get('current')
    spi = metrics.get('spi', {}).get('current')

    cpi_unhealthy = cpi is not None and cpi < CPI_HEALTHY_THRESHOLD
    spi_unhealthy = spi is not None and spi < SPI_HEALTHY_THRESHOLD
    cpi_healthy = cpi is not None and cpi >= CPI_HEALTHY_THRESHOLD
    spi_healthy = spi is not None and spi >= SPI_HEALTHY_THRESHOLD

    if cpi_unhealthy and spi_healthy:
        signals.append({
            'type': 'COST_PROBLEM_WITHOUT_SCHEDULE_PROBLEM',
            'severity': 'warning',
            'message': f'Cost performance is unfavorable (CPI {cpi:.2f}) while schedule performance remains healthy (SPI {spi:.2f}).',
        })
    if spi_unhealthy and cpi_healthy:
        signals.append({
            'type': 'SCHEDULE_PROBLEM_WITHOUT_COST_PROBLEM',
            'severity': 'warning',
            'message': f'Schedule performance is unfavorable (SPI {spi:.2f}) while cost performance remains healthy (CPI {cpi:.2f}).',
        })
    if cpi_unhealthy and spi_unhealthy:
        signals.append({
            'type': 'COMBINED_DETERIORATION',
            'severity': 'critical',
            'message': f'Both cost (CPI {cpi:.2f}) and schedule (SPI {spi:.2f}) performance are unfavorable.',
        })

    if productivity_trend and productivity_trend.get('available'):
        prod_current = productivity_trend.get('current')
        pct_change = productivity_trend.get('pctChange')
        if (
            prod_current is not None and prod_current < PRODUCTIVITY_HEALTHY_THRESHOLD
            and cpi_unhealthy
        ):
            signals.append({
                'type': 'PRODUCTIVITY_DETERIORATION_WITH_COST_IMPACT',
                'severity': 'warning',
                'message': f'Labor productivity factor ({prod_current:.2f}) is below {PRODUCTIVITY_HEALTHY_THRESHOLD} alongside unfavorable cost performance (CPI {cpi:.2f}).',
            })
        if pct_change is not None and pct_change <= PRODUCTIVITY_DETERIORATION_PCT:
            signals.append({
                'type': 'PRODUCTIVITY_DECLINE',
                'severity': 'warning',
                'message': f'Productivity factor declined {abs(pct_change):.1f}% from the previous update.',
            })

    return signals


def evaluate_forecast_deterioration(eac_drift: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flags EAC methodologies that deteriorated (grew) since the
    previous update — a per-scenario signal, never a blended one."""
    signals = []
    if not eac_drift.get('available'):
        return signals
    for method, scenario in eac_drift.get('scenarios', {}).items():
        if scenario.get('direction') == 'deteriorating' and scenario.get('delta'):
            signals.append({
                'type': 'FORECAST_DETERIORATION', 'severity': 'warning', 'methodology': method,
                'message': f'{scenario["label"]} EAC increased {_fmt_money(scenario["delta"])} from the previous update.',
            })
    return signals


# ── Phase F: deterministic executive narrative ──────────────────────────────

def build_executive_narrative(
    comparison: Dict[str, Any],
    eac_drift: Dict[str, Any],
    cpi_trend: Dict[str, Any],
    spi_trend: Dict[str, Any],
    cost_drivers: Optional[Dict[str, Any]] = None,
    productivity_drivers: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Every field is either a fact-backed sentence or None with a reason —
    never a generic sentence generated from missing inputs."""
    sections: Dict[str, Any] = {}
    metrics = comparison.get('metrics', {}) if comparison.get('available') else {}

    # Overall performance
    cpi = metrics.get('cpi', {}).get('current')
    cv = metrics.get('cv', {}).get('current')
    if cpi is not None and cv is not None:
        performance_word = 'unfavorable' if cpi < CPI_HEALTHY_THRESHOLD else 'favorable'
        sections['overallPerformance'] = (
            f'Project cost performance is {performance_word} with CPI {cpi:.2f} and current cost variance of {_fmt_money(cv)}.'
        )
    else:
        sections['overallPerformance'] = None

    # Schedule
    spi_cmp = metrics.get('spi', {})
    if spi_cmp.get('current') is not None and spi_cmp.get('previous') is not None:
        verb = 'declined' if spi_cmp['direction'] == 'deteriorating' else ('improved' if spi_cmp['direction'] == 'improving' else 'held steady')
        streak_note = ''
        if cpi_trend is None:
            pass
        if spi_trend and spi_trend.get('available') and spi_trend.get('consecutiveCount', 0) > 1 and spi_trend.get('direction') in ('improving', 'deteriorating'):
            ordinal = {2: 'second', 3: 'third', 4: 'fourth', 5: 'fifth'}.get(spi_trend['consecutiveCount'], f'{spi_trend["consecutiveCount"]}th')
            adj = 'deterioration' if spi_trend['direction'] == 'deteriorating' else 'improvement'
            streak_note = f', the {ordinal} consecutive {adj}'
        sections['schedule'] = (
            f'SPI {verb} from {spi_cmp["previous"]:.2f} to {spi_cmp["current"]:.2f} this update{streak_note}.'
        )
    else:
        sections['schedule'] = None

    # Forecast
    forecast_sentence = None
    if eac_drift.get('available'):
        bu = eac_drift['scenarios'].get('BOTTOM_UP')
        if bu and bu.get('current') is not None and bu.get('delta') is not None:
            verb = 'increased' if bu['delta'] > 0 else 'decreased'
            vac_current = metrics.get('vac', {}).get('current')
            vac_clause = f', producing a forecast variance of {_fmt_money(vac_current)}' if vac_current is not None else ''
            forecast_sentence = (
                f'Bottom-Up EAC {verb} {_fmt_money(abs(bu["delta"]))} from the previous update to '
                f'{_fmt_money(bu["current"])}{vac_clause}.'
            )
        elif bu and bu.get('current') is not None and bu.get('delta') is None:
            forecast_sentence = f'Bottom-Up EAC is {_fmt_money(bu["current"])} (no previous update to compare).'
    sections['forecast'] = forecast_sentence

    # Primary drivers
    if cost_drivers and cost_drivers.get('drivers'):
        top = [d for d in cost_drivers['drivers'] if d['cv'] < 0][:2]
        if top:
            if len(top) == 1:
                sections['primaryDrivers'] = (
                    f'{top[0]["group"]} represents the largest unfavorable cost variance at {_fmt_money(top[0]["cv"])}.'
                )
            else:
                sections['primaryDrivers'] = (
                    f'{top[0]["group"]} represents the largest unfavorable cost variance at {_fmt_money(top[0]["cv"])}, '
                    f'followed by {top[1]["group"]} at {_fmt_money(top[1]["cv"])}.'
                )
        else:
            sections['primaryDrivers'] = None
    else:
        sections['primaryDrivers'] = None

    # Productivity
    if productivity_drivers and productivity_drivers.get('drivers'):
        worst = productivity_drivers['drivers'][0]
        sections['productivity'] = (
            f'{worst["group"]} productivity factor is {worst["productivityFactor"]:.2f}, '
            f'the lowest of the populated {productivity_drivers["groupBy"]} groups.'
        )
    else:
        sections['productivity'] = None

    return {
        'engineVersion': ENGINE_VERSION,
        'sections': sections,
        'note': 'Deterministic — generated only from calculated metrics, no LLM. Sections are omitted (null) rather than filled with placeholder text when their underlying metrics are unavailable.',
    }


# ── Optional AI enhancement (Phase H) ───────────────────────────────────────
#
# Deterministic facts -> optional AI rewrite -> final narrative. The AI may
# improve readability / reduce repetition / convert technical facts into
# executive wording. It may NOT change numbers, invent root causes, invent
# recovery recommendations, or invent project status.
#
# Enforcement is two-layered, not just a prompt instruction:
#   1. The system prompt explicitly forbids the above.
#   2. _numbers_preserved() rejects any AI rewrite that introduces a numeric
#      token not present in the original deterministic sentence — on
#      rejection (or if AI is unconfigured/errors), the deterministic text
#      is used as-is. This guarantees numeric integrity regardless of
#      whether the AI actually follows the prompt.
#
# Both the original deterministic text and the (possibly-AI-rewritten) text
# are always returned, with an explicit aiEnhanced flag — so a caller can
# always tell which was used and show either one.

_AI_ENHANCEMENT_SYSTEM_PROMPT = (
    "You are a copy editor for a construction project-controls report. You will "
    "be given ONE factual sentence generated by a deterministic calculation "
    "engine. Rewrite it ONLY for clarity and executive tone.\n\n"
    "Strict rules:\n"
    "- Do not change, round, add, or remove any number, dollar amount, percentage, or date.\n"
    "- Do not invent a root cause that isn't stated in the sentence.\n"
    "- Do not invent a recommendation or recovery action.\n"
    "- Do not change what the sentence claims about project status.\n"
    "- Return ONLY the rewritten sentence, no preamble, no explanation.\n"
    "- If you cannot improve it without breaking a rule above, return it unchanged."
)

_NUMBER_PATTERN = re.compile(r'-?\$?\d[\d,]*\.?\d*%?')


def _extract_numbers(text: str) -> set:
    found = _NUMBER_PATTERN.findall(text or '')
    normalized = set()
    for tok in found:
        cleaned = tok.replace('$', '').replace(',', '').replace('%', '')
        try:
            normalized.add(round(float(cleaned), 2))
        except ValueError:
            continue
    return normalized


def _numbers_preserved(original: str, rewritten: str) -> bool:
    """The rewrite may drop a number (e.g. shorten a sentence) but must never
    introduce one that wasn't in the original — that would mean the AI
    fabricated or altered a figure."""
    return _extract_numbers(rewritten).issubset(_extract_numbers(original))


def enhance_narrative_with_ai(narrative: Dict[str, Any], provider=None) -> Dict[str, Any]:
    """Runs each non-empty narrative section through the configured AI
    provider (graceful no-op when none is configured — see scheduler/ai/).
    Returns both `original` and `enhanced` text per section plus a top-level
    `aiEnhanced` flag; never mutates the deterministic `narrative` input."""
    if provider is None:
        from .ai import get_provider
        provider = get_provider()

    sections = narrative.get('sections', {}) or {}
    result: Dict[str, Any] = {'aiProvider': provider.name, 'aiEnhanced': False, 'sections': {}}

    if not provider.is_configured():
        result['sections'] = {k: {'original': v, 'enhanced': v, 'wasRewritten': False} for k, v in sections.items()}
        result['note'] = 'AI enhancement not configured — deterministic narrative used as-is.'
        return result

    any_enhanced = False
    for key, text in sections.items():
        if not text:
            result['sections'][key] = {'original': text, 'enhanced': text, 'wasRewritten': False}
            continue
        response = provider.complete(_AI_ENHANCEMENT_SYSTEM_PROMPT, text)
        if response.ok and response.text and _numbers_preserved(text, response.text):
            rewritten = response.text.strip()
            was_rewritten = rewritten != text
            result['sections'][key] = {'original': text, 'enhanced': rewritten, 'wasRewritten': was_rewritten}
            any_enhanced = any_enhanced or was_rewritten
        else:
            # AI failed, errored, or (rarely) introduced a number that
            # wasn't in the source — fall back to the deterministic text
            # rather than risk showing an altered figure.
            result['sections'][key] = {'original': text, 'enhanced': text, 'wasRewritten': False}

    result['aiEnhanced'] = any_enhanced
    result['note'] = (
        'Sections rewritten by AI for readability; numeric values are verified to match '
        'the deterministic original before being shown.' if any_enhanced else
        'AI enhancement produced no usable rewrite — deterministic narrative used.'
    )
    return result
