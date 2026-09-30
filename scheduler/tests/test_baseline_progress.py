from datetime import date

from django.test import SimpleTestCase

from scheduler.baseline_progress import (
    apply_filters,
    build_baseline_progress,
    build_histogram,
    build_lookahead,
    compute_lookahead_window,
    compute_summary_cards,
    filter_lookahead,
    match_baseline_current,
    rank_variance,
)
from scheduler.calendar_engine import CalendarDefinition
from .fixtures import make_activity

DD = date(2026, 2, 1)

# Standard 5-day (Mon-Fri) calendar for working-day variance tests.
_FIVE_DAY = CalendarDefinition({1: True, 2: True, 3: True, 4: True, 5: True, 6: False, 7: False}, [])


class MatchingTests(SimpleTestCase):
    def test_matched_activity_basic_fields(self):
        baseline = [make_activity('A1', name='Pour Slab', wbs='Area A', b_start='2026-03-01', b_finish='2026-03-10')]
        current = [make_activity('A1', name='Pour Slab', wbs='Area A', b_start='2026-03-01', b_finish='2026-03-10')]
        rows = match_baseline_current(baseline, current, DD)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r['matchStatus'], 'MATCHED')
        self.assertEqual(r['baselineStart'], '2026-03-01')
        self.assertEqual(r['currentFinish'], '2026-03-10')

    def test_added_since_baseline(self):
        baseline = []
        current = [make_activity('NEW1', b_start='2026-03-01', b_finish='2026-03-10')]
        rows = match_baseline_current(baseline, current, DD)
        self.assertEqual(rows[0]['matchStatus'], 'ADDED_SINCE_BASELINE')
        self.assertEqual(rows[0]['status'], 'ADDED_SINCE_BASELINE')
        self.assertIsNone(rows[0]['baselineStart'])

    def test_removed_from_current(self):
        baseline = [make_activity('GONE1', b_start='2026-03-01', b_finish='2026-03-10')]
        current = []
        rows = match_baseline_current(baseline, current, DD)
        self.assertEqual(rows[0]['matchStatus'], 'REMOVED_FROM_CURRENT')
        self.assertEqual(rows[0]['status'], 'REMOVED_FROM_CURRENT')
        self.assertIsNone(rows[0]['currentFinish'])

    def test_finish_variance_calendar_days(self):
        baseline = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10')]
        current = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10', earlyFinish='2026-03-17')]
        rows = match_baseline_current(baseline, current, DD)
        self.assertEqual(rows[0]['finishVarianceDays'], 7)

    def test_working_day_variance_when_calendar_available(self):
        calendars = {'CAL1': _FIVE_DAY}
        baseline = [make_activity('A1', b_start='2026-03-02', b_finish='2026-03-06', calendarId='CAL1')]  # Mon-Fri
        current = [make_activity('A1', b_start='2026-03-02', b_finish='2026-03-06', earlyFinish='2026-03-13', calendarId='CAL1')]  # +1 calendar week
        rows = match_baseline_current(baseline, current, DD, calendars)
        self.assertEqual(rows[0]['finishVarianceDays'], 7)
        self.assertEqual(rows[0]['finishVarianceWorkingDays'], 5)   # a full Mon-Fri week = 5 working days
        self.assertTrue(rows[0]['workingDayCalendarAvailable'])

    def test_working_day_variance_unavailable_without_calendar(self):
        baseline = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10')]
        current = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10', earlyFinish='2026-03-17')]
        rows = match_baseline_current(baseline, current, DD)   # no calendars passed
        self.assertIsNone(rows[0]['finishVarianceWorkingDays'])
        self.assertFalse(rows[0]['workingDayCalendarAvailable'])


class StatusClassificationTests(SimpleTestCase):
    def test_on_plan(self):
        baseline = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10')]
        current = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10')]
        rows = match_baseline_current(baseline, current, DD)
        self.assertEqual(rows[0]['status'], 'ON_PLAN')

    def test_delayed_start(self):
        baseline = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10')]
        current = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10', earlyStart='2026-03-05')]
        rows = match_baseline_current(baseline, current, DD)
        self.assertEqual(rows[0]['status'], 'DELAYED_START')

    def test_delayed_finish(self):
        baseline = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10')]
        current = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10', earlyFinish='2026-03-15')]
        rows = match_baseline_current(baseline, current, DD)
        self.assertEqual(rows[0]['status'], 'DELAYED_FINISH')

    def test_ahead(self):
        baseline = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10')]
        current = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10', earlyStart='2026-02-25', earlyFinish='2026-03-05')]
        rows = match_baseline_current(baseline, current, DD)
        self.assertEqual(rows[0]['status'], 'AHEAD')

    def test_in_progress(self):
        baseline = [make_activity('A1', b_start='2026-01-25', b_finish='2026-02-10')]
        current = [make_activity('A1', b_start='2026-01-25', b_finish='2026-02-10', start='2026-01-28')]
        rows = match_baseline_current(baseline, current, DD)
        self.assertEqual(rows[0]['status'], 'IN_PROGRESS')

    def test_should_have_started(self):
        baseline = [make_activity('A1', b_start='2026-01-01', b_finish='2026-03-01')]
        current = [make_activity('A1', b_start='2026-01-01', b_finish='2026-03-01')]
        rows = match_baseline_current(baseline, current, DD)
        self.assertEqual(rows[0]['status'], 'SHOULD_HAVE_STARTED')

    def test_should_have_finished(self):
        baseline = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-15')]
        current = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-15')]
        rows = match_baseline_current(baseline, current, DD)
        self.assertEqual(rows[0]['status'], 'SHOULD_HAVE_FINISHED')

    def test_complete_via_actual_finish(self):
        baseline = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-15')]
        current = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-15', finish='2026-01-14', pct_complete=100.0)]
        rows = match_baseline_current(baseline, current, DD)
        self.assertEqual(rows[0]['status'], 'COMPLETE')

    def test_complete_via_pct_complete_without_finish_date(self):
        baseline = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-15')]
        current = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-15', pct_complete=100.0)]
        rows = match_baseline_current(baseline, current, DD)
        self.assertEqual(rows[0]['status'], 'COMPLETE')

    def test_should_have_finished_takes_priority_over_in_progress(self):
        # Started, but baseline finish already passed -> more urgent status wins.
        baseline = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-15')]
        current = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-15', start='2026-01-05')]
        rows = match_baseline_current(baseline, current, DD)
        self.assertEqual(rows[0]['status'], 'SHOULD_HAVE_FINISHED')


class LookaheadWindowTests(SimpleTestCase):
    def test_two_week_window(self):
        w = compute_lookahead_window(DD, weeks=2)
        self.assertEqual(w['fromDate'], '2026-02-01')
        self.assertEqual(w['toDate'], '2026-02-15')

    def test_four_week_window_default(self):
        w = compute_lookahead_window(DD)
        self.assertEqual(w['weeks'], 4)
        self.assertEqual(w['toDate'], '2026-03-01')

    def test_custom_window_overrides_weeks(self):
        w = compute_lookahead_window(DD, weeks=4, from_date=date(2026, 5, 1), to_date=date(2026, 5, 10))
        self.assertEqual(w['fromDate'], '2026-05-01')
        self.assertEqual(w['toDate'], '2026-05-10')

    def test_unavailable_without_data_date_or_custom_range(self):
        w = compute_lookahead_window(None)
        self.assertFalse(w['available'])
        self.assertIsNone(w['fromDate'])


class LookaheadFilterTests(SimpleTestCase):
    def test_activity_spanning_window_included(self):
        baseline = [make_activity('A1', b_start='2026-01-20', b_finish='2026-02-20')]
        current = [make_activity('A1', b_start='2026-01-20', b_finish='2026-02-20')]
        rows = match_baseline_current(baseline, current, DD)
        windowed = filter_lookahead(rows, date(2026, 2, 1), date(2026, 2, 15))
        self.assertEqual(len(windowed), 1)   # starts before window, finishes after start of window -> spans it

    def test_overdue_should_have_finished_always_included(self):
        baseline = [make_activity('A1', b_start='2025-12-01', b_finish='2025-12-15')]
        current = [make_activity('A1', b_start='2025-12-01', b_finish='2025-12-15')]
        rows = match_baseline_current(baseline, current, DD)
        windowed = filter_lookahead(rows, date(2026, 2, 1), date(2026, 2, 28))
        self.assertEqual(len(windowed), 1)

    def test_far_future_activity_excluded(self):
        baseline = [make_activity('A1', b_start='2027-01-01', b_finish='2027-01-10')]
        current = [make_activity('A1', b_start='2027-01-01', b_finish='2027-01-10')]
        rows = match_baseline_current(baseline, current, DD)
        windowed = filter_lookahead(rows, date(2026, 2, 1), date(2026, 2, 28))
        self.assertEqual(len(windowed), 0)

    def test_removed_from_current_never_in_lookahead(self):
        baseline = [make_activity('GONE', b_start='2026-02-05', b_finish='2026-02-10')]
        current = []
        rows = match_baseline_current(baseline, current, DD)
        windowed = filter_lookahead(rows, date(2026, 2, 1), date(2026, 2, 28))
        self.assertEqual(len(windowed), 0)


class FilterTests(SimpleTestCase):
    def _rows(self):
        baseline = [
            make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10', wbs='Area A', area='Area A', discipline='Electrical'),
            make_activity('A2', b_start='2026-03-01', b_finish='2026-03-10', wbs='Area B', area='Area B', discipline='Mechanical'),
        ]
        current = [
            make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10', wbs='Area A', area='Area A', discipline='Electrical', earlyFinish='2026-03-20'),
            make_activity('A2', b_start='2026-03-01', b_finish='2026-03-10', wbs='Area B', area='Area B', discipline='Mechanical'),
        ]
        return match_baseline_current(baseline, current, DD)

    def test_filter_by_wbs(self):
        out = apply_filters(self._rows(), {'wbs': 'Area A'})
        self.assertEqual([r['activityId'] for r in out], ['A1'])

    def test_filter_by_discipline(self):
        out = apply_filters(self._rows(), {'discipline': 'Mechanical'})
        self.assertEqual([r['activityId'] for r in out], ['A2'])

    def test_filter_delayed_only(self):
        out = apply_filters(self._rows(), {'delayedOnly': True})
        self.assertEqual([r['activityId'] for r in out], ['A1'])

    def test_no_filters_returns_all(self):
        out = apply_filters(self._rows(), None)
        self.assertEqual(len(out), 2)


class VarianceRankingTests(SimpleTestCase):
    def test_worst_first(self):
        baseline = [
            make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10'),
            make_activity('A2', b_start='2026-03-01', b_finish='2026-03-10'),
        ]
        current = [
            make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10', earlyFinish='2026-03-12'),   # +2
            make_activity('A2', b_start='2026-03-01', b_finish='2026-03-10', earlyFinish='2026-03-25'),   # +15
        ]
        rows = match_baseline_current(baseline, current, DD)
        ranked = rank_variance(rows, 'finishVarianceDays')
        self.assertEqual([r['activityId'] for r in ranked], ['A2', 'A1'])

    def test_top_n_limit(self):
        baseline = [make_activity(f'A{i}', b_start='2026-03-01', b_finish='2026-03-10') for i in range(5)]
        current = [make_activity(f'A{i}', b_start='2026-03-01', b_finish='2026-03-10', earlyFinish='2026-03-15') for i in range(5)]
        rows = match_baseline_current(baseline, current, DD)
        ranked = rank_variance(rows, 'finishVarianceDays', top_n=2)
        self.assertEqual(len(ranked), 2)


class HistogramTests(SimpleTestCase):
    def test_activity_count_weekly_buckets(self):
        baseline = [make_activity('A1', b_start='2026-02-01', b_finish='2026-02-05')]
        current = [make_activity('A1', b_start='2026-02-01', b_finish='2026-02-05')]
        h = build_histogram(baseline, current, metric='activities', period='weekly')
        self.assertTrue(h['available'])
        self.assertEqual(sum(b['baseline'] for b in h['buckets']), 1)

    def test_hours_unavailable_without_resource_loading(self):
        baseline = [make_activity('A1', b_start='2026-02-01', b_finish='2026-02-05')]
        current = [make_activity('A1', b_start='2026-02-01', b_finish='2026-02-05')]
        h = build_histogram(baseline, current, metric='hours')
        self.assertFalse(h['available'])
        self.assertEqual(h['buckets'], [])

    def test_hours_available_with_resource_loading(self):
        baseline = [make_activity('A1', b_start='2026-02-01', b_finish='2026-02-05', isResourceLoaded=True, budgetedHours=40.0)]
        current = [make_activity('A1', b_start='2026-02-01', b_finish='2026-02-05', isResourceLoaded=True, budgetedHours=40.0)]
        h = build_histogram(baseline, current, metric='hours')
        self.assertTrue(h['available'])
        self.assertEqual(sum(b['baseline'] for b in h['buckets']), 40.0)

    def test_data_date_boundary_window_filters_buckets(self):
        baseline = [
            make_activity('A1', b_start='2026-01-01', b_finish='2026-01-05'),
            make_activity('A2', b_start='2026-03-01', b_finish='2026-03-05'),
        ]
        current = baseline
        h = build_histogram(baseline, current, metric='activities', from_date=date(2026, 2, 1), to_date=date(2026, 2, 28))
        self.assertEqual(sum(b['baseline'] for b in h['buckets']), 0)   # neither activity's bFinish falls in Feb


class SummaryCardTests(SimpleTestCase):
    def test_counts_and_no_fake_zeros_for_hours(self):
        baseline = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-10')]
        current = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-10')]
        rows = match_baseline_current(baseline, current, DD)
        cards = compute_summary_cards(rows, DD, date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(cards['activitiesInWindow'], 1)
        self.assertIsNone(cards['plannedHours'])   # no resource data -> unavailable, not 0


class ScurveTests(SimpleTestCase):
    def test_defaults_to_duration_without_resource_data(self):
        from scheduler.baseline_progress import build_scurve
        baseline = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-10')]
        current = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-10', pct_complete=50.0)]
        result = build_scurve(baseline, current, DD)
        self.assertEqual(result['metric'], 'duration')
        self.assertTrue(result['available'])

    def test_defaults_to_hours_when_resource_loaded(self):
        from scheduler.baseline_progress import build_scurve
        baseline = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-10', isResourceLoaded=True, budgetedHours=40.0)]
        current = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-10', isResourceLoaded=True, budgetedHours=40.0, pct_complete=50.0)]
        result = build_scurve(baseline, current, DD)
        self.assertEqual(result['metric'], 'hours')

    def test_cost_unavailable_without_cost_loading(self):
        from scheduler.baseline_progress import build_scurve
        baseline = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-10')]
        current = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-10')]
        result = build_scurve(baseline, current, DD, metric='cost')
        self.assertFalse(result['available'])

    def test_past_future_split_on_data_date(self):
        from scheduler.baseline_progress import build_scurve
        baseline = [
            make_activity('A1', b_start='2026-01-01', b_finish='2026-01-10'),
            make_activity('A2', b_start='2026-03-01', b_finish='2026-03-10'),
        ]
        current = baseline
        result = build_scurve(baseline, current, DD, metric='duration')
        past = [p for p in result['periods'] if p['isPast']]
        future = [p for p in result['periods'] if not p['isPast']]
        self.assertTrue(past)
        self.assertTrue(future)

    def test_daily_period_supported_and_not_collapsed_to_monthly(self):
        # Master Schedule Analysis phase — Daily S-Curve, same engine.
        from scheduler.baseline_progress import build_scurve
        baseline = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-05')]
        current = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-05', pct_complete=100.0)]
        result = build_scurve(baseline, current, DD, metric='duration', period='daily')
        self.assertEqual(result['period'], 'daily')
        self.assertTrue(result['available'])
        self.assertEqual(result['periods'][0]['periodEnd'], '2026-01-05')

    def test_weekly_period_still_defaults_correctly(self):
        from scheduler.baseline_progress import build_scurve
        baseline = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-10')]
        current = [make_activity('A1', b_start='2026-01-01', b_finish='2026-01-10', pct_complete=50.0)]
        result = build_scurve(baseline, current, DD, metric='duration', period='weekly')
        self.assertEqual(result['period'], 'weekly')


class OrchestratorTests(SimpleTestCase):
    def test_build_baseline_progress_full(self):
        baseline = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10')]
        current = [make_activity('A1', b_start='2026-03-01', b_finish='2026-03-10')]
        result = build_baseline_progress(baseline, current, DD)
        self.assertEqual(result['activityCount'], 1)
        self.assertEqual(result['statusCounts']['ON_PLAN'], 1)

    def test_build_lookahead_full(self):
        baseline = [make_activity('A1', b_start='2026-02-05', b_finish='2026-02-10')]
        current = [make_activity('A1', b_start='2026-02-05', b_finish='2026-02-10')]
        result = build_lookahead(baseline, current, DD, weeks=4)
        self.assertEqual(result['window']['toDate'], '2026-03-01')
        self.assertEqual(result['activityCount'], 1)
        self.assertIn('histogram', result)
        self.assertIn('summaryCards', result)

    def test_no_baseline_still_returns_current_only_rows(self):
        current = [make_activity('A1', b_start='2026-02-05', b_finish='2026-02-10')]
        result = build_baseline_progress([], current, DD)
        self.assertFalse(result['hasBaseline'])
        self.assertEqual(result['rows'][0]['status'], 'ADDED_SINCE_BASELINE')
