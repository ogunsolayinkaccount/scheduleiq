"""
Baseline vs Forecast Activity Chart phase — backend regression coverage.

This phase is almost entirely a frontend presentation layer (table +
synchronized timeline) built on baseline_progress.py's EXISTING matched-row
output — no new calculation engine was introduced. These tests protect the
two things that actually changed on the backend: (1) three new passthrough
fields on each row (predecessors/successors/calendarName, needed by the new
activity detail drawer) and (2) the exact date-source contract the chart's
two-layer bars depend on (baseline dates never move; current/forecast dates
follow the existing canonical fallback per activity status) — already
exercised indirectly elsewhere, made explicit and value-level here.
"""

from datetime import date

from django.test import SimpleTestCase

from scheduler.baseline_progress import match_baseline_current
from scheduler.calendar_engine import CalendarDefinition
from .fixtures import make_activity

DD = date(2026, 2, 1)
_FIVE_DAY = CalendarDefinition({1: True, 2: True, 3: True, 4: True, 5: True, 6: False, 7: False}, [])


class RowPassthroughFieldsTests(SimpleTestCase):
    """New fields the activity detail drawer needs — plain passthrough of
    the CURRENT activity's own data, not a new calculation."""

    def test_predecessors_and_successors_passed_through_from_current(self):
        preds = [{'actId': 'A0', 'relType': 'FS', 'lagDays': 0}]
        succs = [{'actId': 'A2', 'relType': 'SS', 'lagDays': 2}]
        baseline = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10')]
        current = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10', predecessors=preds, successors=succs)]
        rows = match_baseline_current(baseline, current, DD)
        self.assertEqual(rows[0]['predecessors'], preds)
        self.assertEqual(rows[0]['successors'], succs)

    def test_predecessors_default_to_empty_list_not_none(self):
        baseline = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10')]
        current = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10')]
        rows = match_baseline_current(baseline, current, DD)
        self.assertEqual(rows[0]['predecessors'], [])
        self.assertEqual(rows[0]['successors'], [])

    def test_removed_from_current_has_no_predecessors_from_baseline(self):
        # Predecessors are a CURRENT-schedule concept here — a removed
        # activity (baseline-only) must not surface baseline relationship
        # data as if it were the current schedule's own.
        baseline = [make_activity('GONE', b_start='2026-02-05', b_finish='2026-02-10', predecessors=[{'actId': 'X', 'relType': 'FS', 'lagDays': 0}])]
        rows = match_baseline_current(baseline, [], DD)
        self.assertEqual(rows[0]['predecessors'], [])

    def test_calendar_name_passed_through(self):
        baseline = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10')]
        current = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10', calendarName='Standard 5 Day Workweek')]
        rows = match_baseline_current(baseline, current, DD)
        self.assertEqual(rows[0]['calendarName'], 'Standard 5 Day Workweek')

    def test_calendar_name_falls_back_to_calendar_field(self):
        baseline = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10')]
        current = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10', calendar='7 Day Calendar')]
        rows = match_baseline_current(baseline, current, DD)
        self.assertEqual(rows[0]['calendarName'], '7 Day Calendar')


class DateSourceContractTests(SimpleTestCase):
    """The exact date-source contract the chart's baseline bar and current/
    forecast bar rely on — value-level, not just status-level."""

    def test_baseline_bar_uses_only_baseline_versions_own_dates(self):
        baseline = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-20')]
        current = [make_activity('A1', b_start='2026-01-05', b_finish='2026-01-25', start='2026-01-05', earlyFinish='2026-02-10')]
        rows = match_baseline_current(baseline, current, DD)
        r = rows[0]
        # Baseline dates come from the BASELINE activity, never the current
        # version's own bStart/bFinish, and never moved to Data Date.
        self.assertEqual(r['baselineStart'], '2026-01-01')
        self.assertEqual(r['baselineFinish'], '2026-01-20')

    def test_completed_activity_current_finish_is_actual_finish(self):
        baseline = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-20')]
        current = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-20',
                                  start='2026-01-02', finish='2026-01-18', pct_complete=100.0)]
        rows = match_baseline_current(baseline, current, DD)
        r = rows[0]
        self.assertEqual(r['status'], 'COMPLETE')
        self.assertEqual(r['currentStart'], '2026-01-02')
        self.assertEqual(r['currentFinish'], '2026-01-18')
        self.assertEqual(r['actualFinish'], '2026-01-18')

    def test_in_progress_activity_uses_actual_start_and_forecast_finish(self):
        baseline = [make_activity('A1', b_start='2026-01-25', b_finish='2026-02-10')]
        current = [make_activity('A1', b_start='2026-01-25', b_finish='2026-02-10',
                                  start='2026-01-28', pct_complete=40.0, earlyFinish='2026-02-15')]
        rows = match_baseline_current(baseline, current, DD)
        r = rows[0]
        self.assertEqual(r['status'], 'IN_PROGRESS')
        self.assertEqual(r['currentStart'], '2026-01-28')     # actual start
        self.assertIsNone(r['actualFinish'])                   # not yet complete
        self.assertEqual(r['currentFinish'], '2026-02-15')     # forecast, not baseline finish

    def test_not_started_activity_uses_current_forecast_not_baseline(self):
        baseline = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10')]
        current = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10',
                                  earlyStart='2026-03-05', earlyFinish='2026-03-14')]
        rows = match_baseline_current(baseline, current, DD)
        r = rows[0]
        self.assertIsNone(r['actualStart'])
        # Forecast dates come from the current version's own early dates —
        # not silently defaulted back to baseline while real current data exists.
        self.assertEqual(r['currentStart'], '2026-03-05')
        self.assertEqual(r['currentFinish'], '2026-03-14')

    def test_working_day_variance_present_when_calendar_available(self):
        baseline = [make_activity('A1', b_start='2026-02-02', b_finish='2026-02-06', calendarId='C1')]  # Mon-Fri
        current = [make_activity('A1', b_start='2026-02-02', b_finish='2026-02-06', calendarId='C1', earlyFinish='2026-02-13')]  # +5 cal, +5 wd (Fri->Fri)
        rows = match_baseline_current(baseline, current, DD, calendars={'C1': _FIVE_DAY})
        r = rows[0]
        self.assertTrue(r['workingDayCalendarAvailable'])
        self.assertIsNotNone(r['finishVarianceWorkingDays'])
        self.assertEqual(r['finishVarianceDays'], 7)

    def test_calendar_day_fallback_when_no_calendar_decoded(self):
        baseline = [make_activity('A1', b_start='2026-02-02', b_finish='2026-02-06')]
        current = [make_activity('A1', b_start='2026-02-02', b_finish='2026-02-06', earlyFinish='2026-02-13')]
        rows = match_baseline_current(baseline, current, DD)   # no calendars supplied
        r = rows[0]
        self.assertFalse(r['workingDayCalendarAvailable'])
        self.assertIsNone(r['finishVarianceWorkingDays'])
        self.assertEqual(r['finishVarianceDays'], 7)   # calendar-day figure still present, clearly not mislabeled


class MilestoneBaselineShiftTests(SimpleTestCase):
    """Milestone rows must carry both a baseline date and a current date so
    the chart can render both diamonds and show the shift between them."""

    def test_milestone_baseline_and_current_dates_both_present_and_distinct(self):
        baseline = [make_activity('MS1', name='Substantial Completion', b_start='2026-02-15', b_finish='2026-02-15', is_milestone=True)]
        current = [make_activity('MS1', name='Substantial Completion', b_start='2026-02-15', b_finish='2026-02-15',
                                  is_milestone=True, earlyFinish='2026-02-20', totalFloat=-3.0)]
        rows = match_baseline_current(baseline, current, DD)
        r = rows[0]
        self.assertTrue(r['isMilestone'])
        self.assertEqual(r['baselineFinish'], '2026-02-15')
        self.assertEqual(r['currentFinish'], '2026-02-20')
        self.assertNotEqual(r['baselineFinish'], r['currentFinish'])
        self.assertEqual(r['finishVarianceDays'], 5)

    def test_milestone_on_plan_baseline_and_current_match(self):
        baseline = [make_activity('MS1', name='Kickoff', b_start='2026-01-05', b_finish='2026-01-05', is_milestone=True)]
        current = [make_activity('MS1', name='Kickoff', b_start='2026-01-05', b_finish='2026-01-05', is_milestone=True)]
        rows = match_baseline_current(baseline, current, DD)
        r = rows[0]
        self.assertEqual(r['baselineFinish'], r['currentFinish'])
