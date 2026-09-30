from datetime import date

from django.test import SimpleTestCase

from scheduler import activity_analysis
from scheduler.update_intelligence import build_update_intelligence
from .fixtures import make_activity


def _row(rows, activity_id):
    return next(r for r in rows if r['activityId'] == activity_id)


class DurationTests(SimpleTestCase):
    def test_original_and_remaining_duration_are_distinct(self):
        acts = [make_activity('A1', dur=20.0, remainDur=8.0)]
        result = activity_analysis.build_activity_analysis(acts)
        r = _row(result['rows'], 'A1')
        self.assertEqual(r['originalDuration'], 20.0)
        self.assertEqual(r['remainingDuration'], 8.0)
        self.assertNotEqual(r['originalDuration'], r['remainingDuration'])

    def test_original_duration_source_surfaced_from_parser_when_present(self):
        acts = [make_activity('A1', dur=20.0, remainDur=8.0, origDurSource='FALLBACK_REMAINING')]
        result = activity_analysis.build_activity_analysis(acts)
        r = _row(result['rows'], 'A1')
        self.assertEqual(r['originalDurationSource'], 'FALLBACK_REMAINING')

    def test_original_duration_source_unknown_when_parser_did_not_report_it(self):
        acts = [make_activity('A1', dur=20.0, remainDur=8.0)]
        result = activity_analysis.build_activity_analysis(acts)
        r = _row(result['rows'], 'A1')
        self.assertEqual(r['originalDurationSource'], 'UNKNOWN')

    def test_actual_duration_always_unavailable_never_invented(self):
        # act_drtn_hr_cnt is not present in real XER exports (confirmed
        # against a real AWP2025 export) — never derive this from dates.
        acts = [make_activity('A1', dur=20.0, remainDur=0.0, pct_complete=100.0,
                               start='2026-01-01', finish='2026-01-20')]
        result = activity_analysis.build_activity_analysis(acts)
        r = _row(result['rows'], 'A1')
        self.assertIsNone(r['actualDuration'])

    def test_remaining_duration_change_and_growth_reduction_arithmetic(self):
        prev = [make_activity('A1', remainDur=10.0)]
        curr = [make_activity('A1', remainDur=14.0)]
        result = activity_analysis.build_activity_analysis(curr, previous_activities=prev)
        r = _row(result['rows'], 'A1')
        self.assertEqual(r['remainingDurationChange'], 4.0)
        self.assertEqual(r['remainingDurationGrowth'], 4.0)
        self.assertEqual(r['remainingDurationReduction'], 0.0)

        prev2 = [make_activity('A1', remainDur=14.0)]
        curr2 = [make_activity('A1', remainDur=9.0)]
        result2 = activity_analysis.build_activity_analysis(curr2, previous_activities=prev2)
        r2 = _row(result2['rows'], 'A1')
        self.assertEqual(r2['remainingDurationChange'], -5.0)
        self.assertEqual(r2['remainingDurationGrowth'], 0.0)
        self.assertEqual(r2['remainingDurationReduction'], 5.0)

    def test_missing_remaining_duration_change_is_none_not_zero(self):
        # No previous version supplied — must be Unavailable, not 0.
        curr = [make_activity('A1', remainDur=9.0)]
        result = activity_analysis.build_activity_analysis(curr)
        r = _row(result['rows'], 'A1')
        self.assertIsNone(r['remainingDurationChange'])
        self.assertIsNone(r['remainingDurationGrowth'])


class FloatTests(SimpleTestCase):
    def test_baseline_previous_current_float_all_separately_identifiable(self):
        baseline = [make_activity('A1', total_float=20.0)]
        previous = [make_activity('A1', total_float=10.0)]
        current = [make_activity('A1', total_float=-3.0)]
        result = activity_analysis.build_activity_analysis(
            current, previous_activities=previous, baseline_activities=baseline,
        )
        r = _row(result['rows'], 'A1')
        self.assertEqual(r['baselineTotalFloat'], 20.0)
        self.assertEqual(r['previousTotalFloat'], 10.0)
        self.assertEqual(r['currentTotalFloat'], -3.0)

    def test_float_change_arithmetic(self):
        baseline = [make_activity('A1', total_float=20.0)]
        previous = [make_activity('A1', total_float=10.0)]
        current = [make_activity('A1', total_float=-3.0)]
        result = activity_analysis.build_activity_analysis(
            current, previous_activities=previous, baseline_activities=baseline,
        )
        r = _row(result['rows'], 'A1')
        self.assertEqual(r['floatChangeVsBaseline'], -23.0)
        self.assertEqual(r['floatChangeVsPrevious'], -13.0)

    def test_newly_negative_float(self):
        previous = [make_activity('A1', total_float=2.0)]
        current = [make_activity('A1', total_float=-1.0)]
        result = activity_analysis.build_activity_analysis(current, previous_activities=previous)
        r = _row(result['rows'], 'A1')
        self.assertTrue(r['newlyNegativeFloat'])
        self.assertFalse(r['recoveredFromNegativeFloat'])

    def test_recovered_from_negative_float(self):
        previous = [make_activity('A1', total_float=-4.0)]
        current = [make_activity('A1', total_float=1.0)]
        result = activity_analysis.build_activity_analysis(current, previous_activities=previous)
        r = _row(result['rows'], 'A1')
        self.assertTrue(r['recoveredFromNegativeFloat'])
        self.assertFalse(r['newlyNegativeFloat'])

    def test_missing_float_is_unavailable_not_zero(self):
        # No baseline/previous supplied at all.
        current = [make_activity('A1', total_float=5.0)]
        result = activity_analysis.build_activity_analysis(current)
        r = _row(result['rows'], 'A1')
        self.assertIsNone(r['baselineTotalFloat'])
        self.assertIsNone(r['previousTotalFloat'])
        self.assertIsNone(r['floatChangeVsBaseline'])
        self.assertIsNone(r['floatChangeVsPrevious'])
        self.assertFalse(r['newlyNegativeFloat'])
        self.assertFalse(r['recoveredFromNegativeFloat'])

    def test_imported_total_float_never_overwritten(self):
        current = [make_activity('A1', total_float=7.5)]
        result = activity_analysis.build_activity_analysis(current)
        r = _row(result['rows'], 'A1')
        self.assertEqual(r['currentTotalFloat'], 7.5)


class CompletedActivityFloatDisplayTests(SimpleTestCase):
    """P6 behavior: a definitively Complete activity's CURRENT/actionable
    Total Float and Free Float display as Unavailable ('—'), never the
    stored/imported value (which is commonly 0 once complete and would
    otherwise misleadingly read as critical). The imported figure itself
    is preserved unchanged for source traceability."""

    def test_complete_with_imported_tf_zero_is_blank_for_display(self):
        current = [make_activity('A1', total_float=0.0, pct_complete=100.0, finish='2026-01-20')]
        result = activity_analysis.build_activity_analysis(current)
        r = _row(result['rows'], 'A1')
        self.assertTrue(r['finished'])
        self.assertIsNone(r['currentTotalFloat'])
        self.assertIsNone(r['freeFloat'])
        # Source traceability: the imported value is untouched, not 0->None.
        self.assertEqual(r['importedCurrentTotalFloat'], 0.0)
        self.assertEqual(r['importedFreeFloat'], 0.0)

    def test_complete_with_imported_tf_negative_is_blank_for_display(self):
        current = [make_activity('A1', total_float=-6.0, pct_complete=100.0, finish='2026-01-20')]
        result = activity_analysis.build_activity_analysis(current)
        r = _row(result['rows'], 'A1')
        self.assertIsNone(r['currentTotalFloat'])
        self.assertEqual(r['importedCurrentTotalFloat'], -6.0)
        self.assertFalse(r['negativeFloat'])  # must not count as actionable negative float

    def test_incomplete_with_tf_zero_still_displays_zero(self):
        current = [make_activity('A1', total_float=0.0, pct_complete=40.0, start='2026-01-05')]
        result = activity_analysis.build_activity_analysis(current)
        r = _row(result['rows'], 'A1')
        self.assertFalse(r['finished'])
        self.assertEqual(r['currentTotalFloat'], 0.0)

    def test_incomplete_with_tf_negative_still_displays_negative(self):
        current = [make_activity('A1', total_float=-10.0, pct_complete=40.0, start='2026-01-05')]
        result = activity_analysis.build_activity_analysis(current)
        r = _row(result['rows'], 'A1')
        self.assertEqual(r['currentTotalFloat'], -10.0)
        self.assertTrue(r['negativeFloat'])

    def test_complete_does_not_inflate_actionable_critical_population(self):
        # isCritical defaults True when total_float<=0 (see fixtures.make_activity).
        current = [make_activity('A1', total_float=0.0, pct_complete=100.0, finish='2026-01-20')]
        result = activity_analysis.build_activity_analysis(current)
        r = _row(result['rows'], 'A1')
        self.assertTrue(r['critical'])            # imported P6 flag preserved, unmutated
        self.assertFalse(r['criticalActionable'])  # but not actionable-current-critical

    def test_complete_excluded_from_near_critical(self):
        current = [make_activity('A1', total_float=5.0, pct_complete=100.0, finish='2026-01-20', is_critical=False)]
        result = activity_analysis.build_activity_analysis(current)
        r = _row(result['rows'], 'A1')
        self.assertFalse(r['nearCritical'])

    def test_historical_baseline_and_previous_float_retained_when_current_complete(self):
        baseline = [make_activity('A1', total_float=18.0)]
        previous = [make_activity('A1', total_float=-3.0)]
        current = [make_activity('A1', total_float=0.0, pct_complete=100.0, finish='2026-01-20')]
        result = activity_analysis.build_activity_analysis(
            current, previous_activities=previous, baseline_activities=baseline,
        )
        r = _row(result['rows'], 'A1')
        self.assertEqual(r['baselineTotalFloat'], 18.0)
        self.assertEqual(r['previousTotalFloat'], -3.0)
        self.assertIsNone(r['currentTotalFloat'])  # only the CURRENT figure is blanked

    def test_imported_p6_tf_never_mutated_by_completion_rule(self):
        current = [make_activity('A1', total_float=0.0, pct_complete=100.0, finish='2026-01-20')]
        result = activity_analysis.build_activity_analysis(current)
        r = _row(result['rows'], 'A1')
        self.assertEqual(r['importedCurrentTotalFloat'], 0.0)
        # And the source activity dict itself is never touched either.
        self.assertEqual(current[0]['totalFloat'], 0.0)


class LogicTests(SimpleTestCase):
    def test_driving_predecessor_resolved_from_raw_task_id(self):
        pred = make_activity('A1', id='RAW1', total_float=2.0,
                              successors=[{'actId': 'RAW2', 'relType': 'FS', 'lagDays': 0}])
        curr = make_activity('A2', id='RAW2',
                              predecessors=[{'actId': 'RAW1', 'relType': 'FS', 'lagDays': 0}])
        result = activity_analysis.build_activity_analysis([pred, curr])
        r = _row(result['rows'], 'A2')
        self.assertIsNotNone(r['drivingPredecessor'])
        self.assertEqual(r['drivingPredecessor']['activityId'], 'A1')

    def test_relationship_counts_and_lag_flags(self):
        a = make_activity(
            'A1',
            predecessors=[{'actId': 'P1', 'relType': 'FS', 'lagDays': 2}],
            successors=[{'actId': 'S1', 'relType': 'SS', 'lagDays': -1}],
        )
        result = activity_analysis.build_activity_analysis([a])
        r = _row(result['rows'], 'A1')
        self.assertEqual(r['predecessorCount'], 1)
        self.assertEqual(r['successorCount'], 1)
        self.assertEqual(r['relationshipCount'], 2)
        self.assertTrue(r['positiveLag'])
        self.assertTrue(r['negativeLag'])

    def test_open_start_and_open_finish(self):
        a = make_activity('A1', predecessors=[], successors=[])
        result = activity_analysis.build_activity_analysis([a])
        r = _row(result['rows'], 'A1')
        self.assertTrue(r['openStart'])
        self.assertTrue(r['openFinish'])

    def test_milestone_never_flagged_open_start_or_finish(self):
        m = make_activity('M1', is_milestone=True, predecessors=[], successors=[])
        result = activity_analysis.build_activity_analysis([m])
        r = _row(result['rows'], 'M1')
        self.assertFalse(r['openStart'])
        self.assertFalse(r['openFinish'])


class CalendarTests(SimpleTestCase):
    def test_no_blind_8hr_conversion_working_days_unavailable_without_calendar(self):
        a = make_activity('A1', b_start='2026-01-01', b_finish='2026-01-10', earlyFinish='2026-01-20')
        result = activity_analysis.build_activity_analysis([a])
        r = _row(result['rows'], 'A1')
        self.assertFalse(r['calendarConfident'])
        self.assertIsNone(r['finishVarianceWorkingDays'])
        self.assertIsNotNone(r['finishVarianceDays'])  # calendar-day figure still available

    def test_calendar_confidence_aggregate_reflects_no_calendars_supplied(self):
        acts = [make_activity('A1'), make_activity('A2')]
        result = activity_analysis.build_activity_analysis(acts)
        cc = result['calendarConfidence']
        self.assertEqual(cc['activitiesWithCalendar'], 0)
        self.assertEqual(cc['totalActivities'], 2)
        self.assertFalse(cc['available'])


class UpdateIntelligenceReuseTests(SimpleTestCase):
    def test_movement_flags_reused_not_recomputed(self):
        prev = [make_activity('A1', b_finish='2026-08-10', total_float=2.0)]
        curr = [make_activity('A1', b_finish='2026-08-10', earlyFinish='2026-08-25', total_float=-5.0, isCritical=True)]
        ui_result = build_update_intelligence(prev, curr, date(2026, 8, 1), date(2026, 8, 15))
        result = activity_analysis.build_activity_analysis(
            curr, previous_activities=prev, current_data_date=date(2026, 8, 15),
            update_intelligence_result=ui_result,
        )
        r = _row(result['rows'], 'A1')
        self.assertTrue(r['slipped'])


class DataDateGovernanceTests(SimpleTestCase):
    def test_no_system_clock_reference_in_module_source(self):
        import inspect
        source = inspect.getsource(activity_analysis)
        self.assertNotIn('date.today(', source)
        self.assertNotIn('datetime.now(', source)
        self.assertNotIn('timezone.now(', source)

    def test_identical_result_across_repeated_runs(self):
        acts = [make_activity('A1', total_float=3.0)]
        r1 = activity_analysis.build_activity_analysis(acts, current_data_date=date(2026, 8, 15))
        r2 = activity_analysis.build_activity_analysis(acts, current_data_date=date(2026, 8, 15))
        self.assertEqual(r1, r2)


class NoTruncationTests(SimpleTestCase):
    def test_full_population_returned_no_row_cap(self):
        acts = [make_activity(f'A{i}') for i in range(3000)]
        result = activity_analysis.build_activity_analysis(acts)
        self.assertEqual(result['rowCount'], 3000)
        self.assertEqual(len(result['rows']), 3000)


class PerformanceRegressionTests(SimpleTestCase):
    def test_large_population_with_calendar_completes_quickly(self):
        # Regression guard: working_days_between() is a day-by-day loop, the
        # one genuinely complexity-sensitive path in this module. A 3,000-
        # activity population with a real calendar and realistic baseline/
        # current variance ranges must still build in well under a second —
        # protects against a future change silently reintroducing an
        # O(n x large-date-range) or O(n^2) regression here.
        import time
        from datetime import date as _date
        from scheduler.calendar_engine import CalendarDefinition
        cal = CalendarDefinition({1: True, 2: True, 3: True, 4: True, 5: True, 6: False, 7: False}, [])
        calendars = {'CAL1': cal}
        n = 3000
        prev = [
            make_activity(f'A{i}', b_start='2024-01-01', b_finish='2024-01-11', calendarId='CAL1',
                          totalFloat=float(i % 15 - 5))
            for i in range(n)
        ]
        curr = [
            make_activity(f'A{i}', b_start='2024-01-01', b_finish='2024-01-11', calendarId='CAL1',
                          earlyFinish='2024-08-01' if i % 50 == 0 else '2024-01-12',
                          totalFloat=float(i % 15 - 5) - (1 if i % 3 == 0 else 0))
            for i in range(n)
        ]
        t0 = time.perf_counter()
        result = activity_analysis.build_activity_analysis(
            curr, previous_activities=prev, current_data_date=_date(2026, 1, 1), calendars=calendars,
        )
        elapsed = time.perf_counter() - t0
        self.assertEqual(result['rowCount'], n)
        self.assertLess(elapsed, 2.0, f'build_activity_analysis took {elapsed:.2f}s on {n} calendar-aware activities')
