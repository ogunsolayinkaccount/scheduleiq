from django.test import SimpleTestCase

from scheduler.calendar_engine import CalendarDefinition, decode_calendar_data
from scheduler.schedule_comparison import compare_schedules
from .fixtures import make_activity

# Mon-Fri 5-day calendar built the same way test_calendar_engine.py does,
# used here to verify compare_schedules()'s optional calendar-aware path.
_STD5_RAW = (
    "(CalendarData(DaysOfWeek(Day(1))(Day(2)(Shift(1)))(Day(3)(Shift(1)))"
    "(Day(4)(Shift(1)))(Day(5)(Shift(1)))(Day(6)(Shift(1)))(Day(7)))(Exceptions))"
)


class ScheduleComparisonTests(SimpleTestCase):
    def test_activity_added_and_removed(self):
        previous = [make_activity('A1'), make_activity('A2')]
        current = [make_activity('A1'), make_activity('A3')]
        result = compare_schedules(previous, current)
        self.assertEqual(result['activities']['addedCount'], 1)
        self.assertEqual(result['activities']['removedCount'], 1)
        self.assertEqual(result['activities']['added'][0]['activityId'], 'A3')
        self.assertEqual(result['activities']['removed'][0]['activityId'], 'A2')

    def test_finish_change_detected_with_delta_and_severity(self):
        previous = [make_activity('A1', b_finish='2026-01-10')]
        current = [make_activity('A1', b_finish='2026-01-25')]   # +15 calendar days
        result = compare_schedules(previous, current)
        finish_changes = [c for c in result['activities']['changes'] if c['fieldKey'] == 'bFinish']
        self.assertEqual(len(finish_changes), 1)
        self.assertEqual(finish_changes[0]['deltaDays'], 15)
        self.assertEqual(finish_changes[0]['deltaUnit'], 'calendar_days')
        self.assertEqual(finish_changes[0]['severity'], 'high')
        self.assertEqual(result['activities']['movedLaterCount'], 1)

    def test_float_deterioration_and_newly_negative(self):
        previous = [make_activity('A1', total_float=5.0, is_critical=False)]
        current = [make_activity('A1', total_float=-3.0, is_critical=True)]
        result = compare_schedules(previous, current)
        self.assertEqual(result['activities']['floatDeterioratedCount'], 1)
        self.assertEqual(result['activities']['newlyNegativeFloatCount'], 1)
        self.assertEqual(result['activities']['newlyCriticalCount'], 1)
        types = {i['type'] for i in result['insights']}
        self.assertIn('newly_negative_float', types)

    def test_no_changes_when_identical(self):
        previous = [make_activity('A1')]
        current = [make_activity('A1')]
        result = compare_schedules(previous, current)
        self.assertEqual(result['activities']['changes'], [])
        self.assertIn('No material differences', result['summaryNarrative'])

    def test_relationship_added_removed_and_lag_changed(self):
        previous = [
            make_activity('A1'),
            make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}]),
            make_activity('A3', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}]),
        ]
        current = [
            make_activity('A1'),
            # A1->A2 removed; lag changed on... use a fresh pair for lag-change clarity
            make_activity('A3', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 3}]),
            make_activity('A4', predecessors=[{'actId': 'A1', 'relType': 'SS', 'lagDays': 0}]),
        ]
        result = compare_schedules(previous, current)
        rel = result['relationships']
        self.assertEqual(rel['removedCount'], 1)   # A1->A2 gone
        self.assertEqual(rel['addedCount'], 1)     # A1->A4 new
        lag_change = next(c for c in rel['changed'] if c['field'] == 'lag')
        self.assertEqual(lag_change['predecessorId'], 'A1')
        self.assertEqual(lag_change['successorId'], 'A3')
        self.assertEqual(lag_change['delta'], 3)

    def test_relationship_type_changed_is_detected(self):
        previous = [make_activity('A1'), make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'FS', 'lagDays': 0}])]
        current = [make_activity('A1'), make_activity('A2', predecessors=[{'actId': 'A1', 'relType': 'SS', 'lagDays': 0}])]
        result = compare_schedules(previous, current)
        type_change = next(c for c in result['relationships']['changed'] if c['field'] == 'relationshipType')
        self.assertEqual(type_change['previous'], 'FS')
        self.assertEqual(type_change['current'], 'SS')

    def test_milestone_movement_and_narrative(self):
        previous = [make_activity('M1', name='Substantial Completion', is_milestone=True, dur=0, b_finish='2026-06-01')]
        current = [make_activity('M1', name='Substantial Completion', is_milestone=True, dur=0, b_finish='2026-06-12')]
        result = compare_schedules(previous, current)
        self.assertEqual(len(result['milestoneMovement']), 1)
        self.assertEqual(result['milestoneMovement'][0]['deltaDays'], 11)
        self.assertIn('Substantial Completion', result['summaryNarrative'])
        self.assertIn('11 calendar days later', result['summaryNarrative'])

    def test_narrative_never_hardcodes_example_project_data(self):
        # Regression guard: the narrative must be built purely from computed
        # facts, never leak the roadmap's own illustrative example values.
        previous = [make_activity('A1', b_finish='2026-01-10')]
        current = [make_activity('A1', b_finish='2026-01-11')]
        result = compare_schedules(previous, current)
        for forbidden in ('Area C', '128 activities', '46 of those'):
            self.assertNotIn(forbidden, result['summaryNarrative'])


class CalendarAwareComparisonTests(SimpleTestCase):
    """Phase D Step 4: deltaWorkingDays is purely additive — omitting the
    `calendars` arg must reproduce prior calendar-day-only behavior exactly,
    and it must never be a guessed figure when no calendar is supplied."""

    def setUp(self):
        self.std5 = CalendarDefinition.from_decoded(decode_calendar_data(_STD5_RAW))

    def test_no_calendars_arg_leaves_delta_working_days_unavailable(self):
        previous = [make_activity('A1', b_finish='2026-01-10', calendarId='CAL1')]
        current = [make_activity('A1', b_finish='2026-01-25', calendarId='CAL1')]
        result = compare_schedules(previous, current)   # no calendars supplied
        finish_change = next(c for c in result['activities']['changes'] if c['fieldKey'] == 'bFinish')
        self.assertEqual(finish_change['deltaDays'], 15)   # calendar-day figure unchanged
        self.assertIsNone(finish_change['deltaWorkingDays'])   # explicitly unavailable, never guessed

    def test_calendar_supplied_but_activity_calendar_unknown_is_unavailable(self):
        previous = [make_activity('A1', b_finish='2026-01-10', calendarId='CAL_UNKNOWN')]
        current = [make_activity('A1', b_finish='2026-01-25', calendarId='CAL_UNKNOWN')]
        result = compare_schedules(previous, current, calendars={'CAL1': self.std5})
        finish_change = next(c for c in result['activities']['changes'] if c['fieldKey'] == 'bFinish')
        self.assertEqual(finish_change['deltaDays'], 15)
        self.assertIsNone(finish_change['deltaWorkingDays'])   # explicitly unavailable, never guessed

    def test_calendar_supplied_and_matched_computes_working_day_delta(self):
        # Fri 2026-01-09 -> Fri 2026-01-23 on a Mon-Fri calendar: 10 calendar
        # weekdays worth of working days in between (2 weekends excluded from
        # the 14 calendar days).
        previous = [make_activity('A1', b_finish='2026-01-09', calendarId='CAL1')]
        current = [make_activity('A1', b_finish='2026-01-23', calendarId='CAL1')]
        result = compare_schedules(previous, current, calendars={'CAL1': self.std5})
        finish_change = next(c for c in result['activities']['changes'] if c['fieldKey'] == 'bFinish')
        self.assertEqual(finish_change['deltaDays'], 14)
        self.assertEqual(finish_change['deltaWorkingDays'], 10)

    def test_milestone_movement_gets_working_day_delta_when_calendar_available(self):
        previous = [make_activity('M1', is_milestone=True, dur=0, b_finish='2026-01-09', calendarId='CAL1')]
        current = [make_activity('M1', is_milestone=True, dur=0, b_finish='2026-01-23', calendarId='CAL1')]
        result = compare_schedules(previous, current, calendars={'CAL1': self.std5})
        move = result['milestoneMovement'][0]
        self.assertEqual(move['deltaDays'], 14)
        self.assertEqual(move['deltaWorkingDays'], 10)

    def test_milestone_movement_unavailable_without_calendars(self):
        previous = [make_activity('M1', is_milestone=True, dur=0, b_finish='2026-01-09', calendarId='CAL1')]
        current = [make_activity('M1', is_milestone=True, dur=0, b_finish='2026-01-23', calendarId='CAL1')]
        result = compare_schedules(previous, current)
        move = result['milestoneMovement'][0]
        self.assertIsNone(move['deltaWorkingDays'])
