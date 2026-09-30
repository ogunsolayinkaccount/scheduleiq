"""
Dashboard Consolidation Phase 1 - the shared completion semantic.

ScheduleIQ's approved rule (activity_analysis.is_activity_complete, already
governing the completed-activity float display rule): an activity is
Complete when an actual finish date is present, OR pctComplete >= 100.

Before this phase, six modules each had their own private completion
helper, and two disagreed with the approved rule (pctComplete >= 100 only,
ignoring a present actual finish date). This file proves every engine now
delegates to the one canonical function, and pins the resulting behavior
change for the three engines that were narrower (status_engine.py,
schedule_risk.py, narrative_engine.py).
"""
from datetime import date

from django.test import SimpleTestCase

from scheduler import baseline_progress, narrative_engine, schedule_risk, status_engine, update_intelligence
from scheduler.activity_analysis import is_activity_complete
from .fixtures import make_activity

# An activity with an actual finish date recorded but understated/stale
# pctComplete - the exact case the six-implementations audit flagged as a
# real disagreement risk.
FINISHED_BUT_LOW_PCT = make_activity('A1', total_float=0.0, pct_complete=40.0, start='2026-01-01', finish='2026-01-20')
INCOMPLETE = make_activity('A2', total_float=5.0, pct_complete=40.0, start='2026-01-01')
FULLY_REPORTED = make_activity('A3', total_float=0.0, pct_complete=100.0)


class CanonicalRuleTests(SimpleTestCase):
    def test_finish_date_present_is_complete_even_with_low_pct(self):
        self.assertTrue(is_activity_complete(FINISHED_BUT_LOW_PCT))

    def test_pct_100_is_complete_without_a_finish_date(self):
        self.assertTrue(is_activity_complete(FULLY_REPORTED))

    def test_neither_is_incomplete(self):
        self.assertFalse(is_activity_complete(INCOMPLETE))


class EveryEngineDelegatesToTheCanonicalRuleTests(SimpleTestCase):
    """Identity check - proves each module's local name is the SAME function
    object as the canonical one, not a coincidentally-matching copy."""

    def test_status_engine(self):
        self.assertIs(status_engine._is_complete, is_activity_complete)

    def test_schedule_risk(self):
        self.assertIs(schedule_risk._is_complete, is_activity_complete)

    def test_narrative_engine(self):
        self.assertIs(narrative_engine._complete, is_activity_complete)

    def test_baseline_progress(self):
        self.assertIs(baseline_progress._is_complete, is_activity_complete)

    def test_update_intelligence(self):
        self.assertIs(update_intelligence._is_complete, is_activity_complete)


class BehaviorChangePinnedTests(SimpleTestCase):
    """status_engine.py, schedule_risk.py and narrative_engine.py previously
    used pctComplete >= 100 ONLY. These pin the corrected behavior: an
    activity with a real finish date now counts as complete everywhere,
    even if pctComplete is stale/understated - the intentional fix.

    FINISHED_BUT_LOW_PCT carries totalFloat=0 (critical-looking) and 40%
    reported complete. Under the OLD narrow rule it would still count as
    "incomplete" and therefore be eligible for critical-activity counts;
    under the approved rule it is Complete and must be excluded."""

    DD = date(2026, 6, 1)

    def test_schedule_risk_excludes_finished_low_pct_activity_from_incomplete_population(self):
        acts = [FINISHED_BUT_LOW_PCT, INCOMPLETE]
        self.assertFalse(schedule_risk._is_complete(INCOMPLETE))
        self.assertTrue(schedule_risk._is_complete(FINISHED_BUT_LOW_PCT))
        overall = schedule_risk.compute_risk(acts, self.DD)['overall']
        # Non-milestone incomplete denominator (schedule_risk.py:174) must
        # count only A2 now, not both.
        self.assertEqual(overall['totalActivityCount'], 2)  # population size unaffected...
        # ...but the risk driver population (incomplete only) must exclude A1.
        non_ms_incomplete = [a for a in acts if not a.get('isMilestone') and not schedule_risk._is_complete(a)]
        self.assertEqual([a['code'] for a in non_ms_incomplete], ['A2'])

    def test_status_engine_excludes_finished_low_pct_critical_looking_activity(self):
        acts = [FINISHED_BUT_LOW_PCT, INCOMPLETE]
        result = status_engine.classify_schedule(acts, self.DD)
        # A1 has totalFloat=0 (critical by float) but is now Complete, so it
        # must not inflate the critical-activity count; A2 (float=5) isn't
        # critical either way, so the count is 0 either way UNLESS the old
        # narrow rule wrongly let A1 through.
        self.assertEqual(result.critical_activity_count, 0)

    def test_narrative_engine_counts_finished_low_pct_activity_as_complete(self):
        acts = [FINISHED_BUT_LOW_PCT, INCOMPLETE]
        ctx = narrative_engine._extract_context(acts, None, None, None, self.DD, None, None, None)
        self.assertEqual(ctx['complete_count'], 1)
        self.assertEqual(ctx['incomplete_count'], 1)
