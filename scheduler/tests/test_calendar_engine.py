from datetime import date

from django.test import SimpleTestCase

from scheduler.calendar_engine import (
    CalendarDefinition,
    add_working_days,
    decode_calendar_data,
    is_working_day,
    next_working_day,
    previous_working_day,
    working_days_between,
)


def _day_block(p6_day: int, working: bool) -> str:
    """Builds a Day(n)(...) node. A working day gets a dummy non-empty
    child so the block isn't empty; a non-working day is an empty block."""
    return f"(Day({p6_day})" + ("(Shift(1))" if working else "") + ")"


def _build_clndr_data(working_p6_days, exceptions=None) -> str:
    """working_p6_days: iterable of P6 day indices (1=Sun..7=Sat) that are
    working. exceptions: list of (serial, is_working) tuples."""
    days = "".join(_day_block(d, d in working_p6_days) for d in range(1, 8))
    excp = "".join(
        f"(Excp({serial})" + ("(Shift(1))" if working else "") + ")"
        for serial, working in (exceptions or [])
    )
    return f"(CalendarData(DaysOfWeek{days})(Exceptions{excp}))"


# Standard Mon-Fri 5-day calendar: P6 days 2,3,4,5,6 (Mon..Fri) working.
STANDARD_5_DAY = _build_clndr_data([2, 3, 4, 5, 6])

# 6-day construction calendar: Mon-Sat working, Sunday off.
SIX_DAY = _build_clndr_data([2, 3, 4, 5, 6, 7])

# 7-day calendar: every day working.
SEVEN_DAY = _build_clndr_data([1, 2, 3, 4, 5, 6, 7])


def _serial(d: date) -> int:
    return (d - date(1899, 12, 30)).days


class DecodeCalendarDataTests(SimpleTestCase):
    def test_empty_raw_is_none_confidence(self):
        result = decode_calendar_data('')
        self.assertEqual(result['confidence'], 'none')
        self.assertIsNone(result['standardWorkweek'])
        self.assertEqual(result['exceptions'], [])

    def test_none_raw_is_none_confidence(self):
        result = decode_calendar_data(None)
        self.assertEqual(result['confidence'], 'none')

    def test_garbage_raw_is_none_confidence_not_raise(self):
        result = decode_calendar_data('not a calendar at all just text')
        self.assertEqual(result['confidence'], 'none')

    def test_unbalanced_parens_does_not_raise(self):
        result = decode_calendar_data('(CalendarData(DaysOfWeek(Day(1)')
        self.assertEqual(result['confidence'], 'none')

    def test_standard_5_day_decodes_high_confidence(self):
        result = decode_calendar_data(STANDARD_5_DAY)
        self.assertEqual(result['confidence'], 'high')
        week = result['standardWorkweek']
        # ISO weekday: 1=Mon..7=Sun
        self.assertTrue(week[1])   # Monday
        self.assertTrue(week[5])   # Friday
        self.assertFalse(week[6])  # Saturday
        self.assertFalse(week[7])  # Sunday

    def test_six_day_calendar_saturday_working(self):
        result = decode_calendar_data(SIX_DAY)
        week = result['standardWorkweek']
        self.assertTrue(week[6])   # Saturday working
        self.assertFalse(week[7])  # Sunday off

    def test_seven_day_calendar_all_working(self):
        result = decode_calendar_data(SEVEN_DAY)
        week = result['standardWorkweek']
        self.assertTrue(all(week.values()))

    def test_holiday_exception_decoded(self):
        holiday = date(2026, 12, 25)
        raw = _build_clndr_data([2, 3, 4, 5, 6], exceptions=[(_serial(holiday), False)])
        result = decode_calendar_data(raw)
        self.assertEqual(result['confidence'], 'high')
        exc = {e['date']: e['isWorkingDay'] for e in result['exceptions']}
        self.assertEqual(exc['2026-12-25'], False)

    def test_exception_workday_decoded(self):
        makeup_saturday = date(2026, 11, 28)
        raw = _build_clndr_data([2, 3, 4, 5, 6], exceptions=[(_serial(makeup_saturday), True)])
        result = decode_calendar_data(raw)
        exc = {e['date']: e['isWorkingDay'] for e in result['exceptions']}
        self.assertEqual(exc['2026-11-28'], True)

    def test_missing_days_block_is_partial_or_none(self):
        raw = "(CalendarData(Exceptions(Excp(46000)(Shift(1)))))"
        result = decode_calendar_data(raw)
        self.assertIn(result['confidence'], ('partial', 'none'))
        self.assertIsNone(result['standardWorkweek'])

    def test_missing_exceptions_block_is_partial(self):
        raw = "(CalendarData(DaysOfWeek" + "".join(_day_block(d, d in (2, 3, 4, 5, 6)) for d in range(1, 8)) + "))"
        result = decode_calendar_data(raw)
        self.assertEqual(result['confidence'], 'partial')
        self.assertIsNotNone(result['standardWorkweek'])


class CalendarDefinitionFromDecodedTests(SimpleTestCase):
    def test_none_confidence_yields_no_calendar_definition(self):
        decoded = decode_calendar_data('garbage')
        self.assertIsNone(CalendarDefinition.from_decoded(decoded))

    def test_high_confidence_yields_calendar_definition(self):
        decoded = decode_calendar_data(STANDARD_5_DAY)
        cal = CalendarDefinition.from_decoded(decoded)
        self.assertIsNotNone(cal)


class IsWorkingDayTests(SimpleTestCase):
    def setUp(self):
        self.std5 = CalendarDefinition.from_decoded(decode_calendar_data(STANDARD_5_DAY))
        self.six = CalendarDefinition.from_decoded(decode_calendar_data(SIX_DAY))
        self.seven = CalendarDefinition.from_decoded(decode_calendar_data(SEVEN_DAY))

    def test_unavailable_calendar_returns_none_not_false(self):
        self.assertIsNone(is_working_day(date(2026, 1, 5), None))

    def test_standard_calendar_monday_is_working(self):
        # 2026-01-05 is a Monday
        self.assertTrue(is_working_day(date(2026, 1, 5), self.std5))

    def test_standard_calendar_saturday_is_not_working(self):
        # 2026-01-03 is a Saturday
        self.assertFalse(is_working_day(date(2026, 1, 3), self.std5))

    def test_six_day_calendar_saturday_is_working(self):
        self.assertTrue(is_working_day(date(2026, 1, 3), self.six))

    def test_six_day_calendar_sunday_is_not_working(self):
        # 2026-01-04 is a Sunday
        self.assertFalse(is_working_day(date(2026, 1, 4), self.six))

    def test_seven_day_calendar_all_days_working(self):
        for d in (date(2026, 1, 3), date(2026, 1, 4), date(2026, 1, 5)):
            self.assertTrue(is_working_day(d, self.seven))

    def test_holiday_exception_overrides_standard_workweek(self):
        # 2026-12-25 is a Friday — normally working on std5 — but a holiday exception.
        christmas = date(2026, 12, 25)
        raw = _build_clndr_data([2, 3, 4, 5, 6], exceptions=[(_serial(christmas), False)])
        cal = CalendarDefinition.from_decoded(decode_calendar_data(raw))
        self.assertTrue(is_working_day(date(2026, 12, 24), cal))
        self.assertFalse(is_working_day(christmas, cal))

    def test_exception_workday_overrides_standard_weekend(self):
        # 2026-11-28 is a Saturday — normally off on std5 — but a makeup workday.
        makeup = date(2026, 11, 28)
        raw = _build_clndr_data([2, 3, 4, 5, 6], exceptions=[(_serial(makeup), True)])
        cal = CalendarDefinition.from_decoded(decode_calendar_data(raw))
        self.assertTrue(is_working_day(makeup, cal))

    def test_leap_year_feb_29_handled(self):
        # 2028-02-29 is a Tuesday (leap year).
        self.assertTrue(is_working_day(date(2028, 2, 29), self.std5))

    def test_month_year_boundary(self):
        # 2026-12-31 (Thu) working, 2027-01-01 (Fri) working on std5.
        self.assertTrue(is_working_day(date(2026, 12, 31), self.std5))
        self.assertTrue(is_working_day(date(2027, 1, 1), self.std5))


class NextPreviousWorkingDayTests(SimpleTestCase):
    def setUp(self):
        self.std5 = CalendarDefinition.from_decoded(decode_calendar_data(STANDARD_5_DAY))

    def test_next_working_day_skips_weekend(self):
        # Friday 2026-01-02 -> next working day is Monday 2026-01-05
        result = next_working_day(date(2026, 1, 2), self.std5)
        self.assertEqual(result, date(2026, 1, 5))

    def test_previous_working_day_skips_weekend(self):
        # Monday 2026-01-05 -> previous working day is Friday 2026-01-02
        result = previous_working_day(date(2026, 1, 5), self.std5)
        self.assertEqual(result, date(2026, 1, 2))

    def test_unavailable_calendar_returns_none(self):
        self.assertIsNone(next_working_day(date(2026, 1, 2), None))
        self.assertIsNone(previous_working_day(date(2026, 1, 2), None))


class AddWorkingDaysTests(SimpleTestCase):
    def setUp(self):
        self.std5 = CalendarDefinition.from_decoded(decode_calendar_data(STANDARD_5_DAY))
        self.six = CalendarDefinition.from_decoded(decode_calendar_data(SIX_DAY))

    def test_zero_days_returns_same_date(self):
        d = date(2026, 1, 5)
        self.assertEqual(add_working_days(d, 0, self.std5), d)

    def test_positive_movement_within_week(self):
        # Monday 2026-01-05 + 3 working days = Thursday 2026-01-08
        result = add_working_days(date(2026, 1, 5), 3, self.std5)
        self.assertEqual(result, date(2026, 1, 8))

    def test_positive_movement_crosses_weekend(self):
        # Thursday 2026-01-08 + 3 working days -> Fri(1) Mon(2) Tue(3) = 2026-01-13
        result = add_working_days(date(2026, 1, 8), 3, self.std5)
        self.assertEqual(result, date(2026, 1, 13))

    def test_negative_movement_crosses_weekend(self):
        # Monday 2026-01-12 - 3 working days -> Fri(1) Thu(2) Wed(3) = 2026-01-07
        result = add_working_days(date(2026, 1, 12), -3, self.std5)
        self.assertEqual(result, date(2026, 1, 7))

    def test_six_day_calendar_moves_through_saturday(self):
        # Friday 2026-01-02 + 1 working day on 6-day calendar = Saturday 2026-01-03
        result = add_working_days(date(2026, 1, 2), 1, self.six)
        self.assertEqual(result, date(2026, 1, 3))

    def test_unavailable_calendar_returns_none(self):
        self.assertIsNone(add_working_days(date(2026, 1, 5), 3, None))

    def test_month_boundary(self):
        # Friday 2026-01-30 + 1 working day = Monday 2026-02-02
        result = add_working_days(date(2026, 1, 30), 1, self.std5)
        self.assertEqual(result, date(2026, 2, 2))

    def test_leap_year_boundary(self):
        # Thu 2028-02-24 (leap year Feb) + 5 working days -> Fri(1)25 Mon(2)28 Tue(3)29 Wed(4)Mar1...
        # Just assert it lands on a working weekday and is 2028-03-02.
        result = add_working_days(date(2028, 2, 24), 5, self.std5)
        self.assertEqual(result, date(2028, 3, 2))
        self.assertEqual(result.isoweekday(), 4)  # Thursday


class WorkingDaysBetweenTests(SimpleTestCase):
    def setUp(self):
        self.std5 = CalendarDefinition.from_decoded(decode_calendar_data(STANDARD_5_DAY))

    def test_same_date_is_zero(self):
        d = date(2026, 1, 5)
        self.assertEqual(working_days_between(d, d, self.std5), 0)

    def test_forward_count_excludes_weekend(self):
        # Mon 2026-01-05 to Mon 2026-01-12: working days in between (exclusive start,
        # inclusive finish) = Tue,Wed,Thu,Fri,Mon = 5
        result = working_days_between(date(2026, 1, 5), date(2026, 1, 12), self.std5)
        self.assertEqual(result, 5)

    def test_backward_count_is_negative(self):
        result = working_days_between(date(2026, 1, 12), date(2026, 1, 5), self.std5)
        self.assertEqual(result, -5)

    def test_unavailable_calendar_returns_none(self):
        self.assertIsNone(working_days_between(date(2026, 1, 5), date(2026, 1, 12), None))

    def test_calendar_day_span_including_only_weekend_is_zero_working_days(self):
        # Sat 2026-01-03 to Sun 2026-01-04: no working days between.
        result = working_days_between(date(2026, 1, 3), date(2026, 1, 4), self.std5)
        self.assertEqual(result, 0)
