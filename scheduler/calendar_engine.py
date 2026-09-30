"""
P6 Calendar Intelligence — ScheduleIQ Phase D

═══════════════════════════════════════════════════════════════════════════
STEP 1 — CURRENT DATA FLOW (documented before anything below was changed)
═══════════════════════════════════════════════════════════════════════════
scheduler/parsers.py's `extract_xer_reference_data()` reads the XER CALENDAR
table into `Calendar` model rows. Before this module existed, it captured
ONLY the directly-tabulated columns:
    clndr_id, clndr_name, clndr_type, default_flag,
    day_hr_cnt, week_hr_cnt, month_hr_cnt, year_hr_cnt
and stored the `clndr_data` column verbatim in `raw_definition` WITHOUT
decoding it — `has_detailed_definition` was hardcoded False. Every activity
in `xer_to_activities()` already carries `calendarId` (clndr_id) and, since
the gap-fill phase, `calendarName` — but nothing in ScheduleIQ has ever used
the calendar to compute a working day. All "variance" figures throughout
the app (schedule_comparison.py, narrative_engine.py, status_engine.py,
utils.py) are calendar-DAY deltas.

═══════════════════════════════════════════════════════════════════════════
STEP 2 — WHAT THIS MODULE DECODES, AND WHY THE LINE IS DRAWN WHERE IT IS
═══════════════════════════════════════════════════════════════════════════
P6's `clndr_data` is a proprietary nested-parenthesis format. Its outer
grammar (a paren-delimited tree of `Keyword(args)(children)` nodes) is
mechanically parseable and this module does parse it generically — that
part carries no risk of misinterpretation because it doesn't depend on
knowing every field's meaning, only balanced-paren structure.

Within that tree, this module trusts exactly two structural conventions
that are consistently documented across P6 XER implementations:
  1. A `Day(<n>)(...)` node exists per ISO weekday (1=Monday..7=Sunday in
     ScheduleIQ's normalized output; P6 itself uses 1=Sunday..7=Saturday,
     converted here). An EMPTY child list means that weekday has no working
     shifts at all — i.e. it is a non-working day. A NON-empty child list
     means it has at least one working shift.
  2. An `Excp(<serial_date>)(...)` node exists per calendar exception
     (holiday or modified workday), keyed by a serial day-count date
     (the same epoch, 1899-12-30, used by Lotus/Excel serial dates — P6
     inherited this convention). An empty child list means the exception
     day is wholly non-working (a holiday); non-empty means a modified
     (but still at least partially working) day.

What this module deliberately does NOT attempt: decoding the exact
shift START/end clock times embedded inside a working Day/Excp block, or
per-weekday hour variation. Working-day hour totals use the calendar's
own `hours_per_day` field (already reliably captured) uniformly across
working days. This is a real simplification (a calendar with different
hours on, say, Friday vs. Monday would not be captured precisely), but it
never produces a WRONG working/non-working classification — only a
possible imprecision in hour totals, which this codebase does not currently
report at the sub-day level anywhere.

Confidence is reported explicitly on every decode result via `confidence`:
  'high'    — DaysOfWeek and Exceptions blocks both parsed cleanly.
  'partial' — DaysOfWeek parsed; Exceptions missing/unparseable (or vice
              versa) — is_working_day() still works, exception-only
              questions return None.
  'none'    — clndr_data was empty, absent, or did not match the expected
              structural markers at all. No result here should be treated
              as calendar-aware; the standard `has_detailed_definition`
              flag on the Calendar model stays False.

IMPORTANT CAVEAT: this decoder has not been validated against a real
captured P6 XER export in this environment (no sample was available to
test against byte-for-byte). It is built from the structural conventions
documented and cross-implemented across the P6 XER ecosystem, defensively
coded to degrade to 'none'/'partial' confidence rather than raise or
silently misparse on anything unexpected. Treat 'high'-confidence results
as provisional until validated against a real exported calendar, and never
present a 'none'-confidence result as a working-day fact — the calendar
service below enforces this by returning None rather than guessing.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

P6_SERIAL_EPOCH = date(1899, 12, 30)
_SERIAL_SANITY_MIN = date(1980, 1, 1)
_SERIAL_SANITY_MAX = date(2100, 1, 1)


def _p6_serial_to_date(serial: Any) -> Optional[date]:
    try:
        n = int(str(serial).strip())
    except (TypeError, ValueError):
        return None
    try:
        d = P6_SERIAL_EPOCH + timedelta(days=n)
    except OverflowError:
        return None
    if d < _SERIAL_SANITY_MIN or d > _SERIAL_SANITY_MAX:
        return None
    return d


# ── Generic balanced-parenthesis tokenizer ─────────────────────────────────
# Turns "(A(1,2)(B()(C)))" into nested Python lists/strings. This part has no
# semantic knowledge of P6's grammar — it just recovers the tree structure,
# which is unambiguous regardless of what each node means.

def _tokenize(raw: str) -> Optional[list]:
    """Returns a nested-list parse of the OUTERMOST paren group, or None on
    unbalanced/empty input. Does not raise."""
    if not raw or '(' not in raw:
        return None
    pos = 0
    length = len(raw)

    def parse_node(p: int) -> Tuple[Any, int]:
        # Expects raw[p] == '('
        p += 1
        header_start = p
        depth = 0
        while p < length and raw[p] not in '()':
            p += 1
        header = raw[header_start:p]
        children: list = []
        while p < length and raw[p] == '(':
            child, p = parse_node(p)
            children.append(child)
        if p < length and raw[p] == ')':
            p += 1
        else:
            raise ValueError('unbalanced parentheses')
        return {'header': header, 'children': children}, p

    try:
        if raw[pos] != '(':
            # Skip leading whitespace/garbage up to the first '('
            idx = raw.find('(')
            if idx == -1:
                return None
            pos = idx
        node, _end = parse_node(pos)
        return node
    except (ValueError, IndexError):
        return None


def _find_child(node: dict, header_prefix: str) -> Optional[dict]:
    if not node:
        return None
    for child in node.get('children', []):
        if isinstance(child, dict) and child.get('header', '').startswith(header_prefix):
            return child
    return None


def _node_arg(node: dict) -> Optional[str]:
    """Returns the header string of a node's first child — the tokenizer's
    representation of a node's parenthesized "argument", e.g. for
    "(Day(2)(Shift(1)))" the Day node's first child has header '2'."""
    children = node.get('children', [])
    if not children:
        return None
    return children[0].get('header', '')


def _find_all_children(node: dict, header_prefix: str) -> List[dict]:
    if not node:
        return []
    return [
        c for c in node.get('children', [])
        if isinstance(c, dict) and c.get('header', '').startswith(header_prefix)
    ]


# P6 numbers weekdays 1=Sunday..7=Saturday internally; ScheduleIQ (and Python's
# date.isoweekday()) uses 1=Monday..7=Sunday. This maps P6's Day(n) index to
# ISO weekday.
_P6_DAY_TO_ISO = {1: 7, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6}


def decode_calendar_data(raw: Optional[str]) -> Dict[str, Any]:
    """
    Best-effort structural decode of a P6 clndr_data blob.

    Returns:
      {
        'confidence': 'high' | 'partial' | 'none',
        'standardWorkweek': {1: bool, ..., 7: bool} | None,   # ISO weekday -> is a working day
        'exceptions': [{'date': iso_str, 'isWorkingDay': bool}],
        'warnings': [str],
      }
    Never raises. 'none' confidence with empty structures is the honest
    result for anything that doesn't match the expected markers.
    """
    warnings: List[str] = []
    if not raw or not raw.strip():
        return {'confidence': 'none', 'standardWorkweek': None, 'exceptions': [], 'warnings': []}

    tree = _tokenize(raw)
    if tree is None:
        return {
            'confidence': 'none', 'standardWorkweek': None, 'exceptions': [],
            'warnings': ['clndr_data did not parse as a balanced-parenthesis structure.'],
        }

    calendar_data_node = _find_child(tree, 'CalendarData') or tree
    days_node = _find_child(calendar_data_node, 'DaysOfWeek')
    exceptions_node = _find_child(calendar_data_node, 'Exceptions')

    standard_workweek: Optional[Dict[int, bool]] = None
    if days_node:
        standard_workweek = {}
        day_nodes = _find_all_children(days_node, 'Day')
        for dn in day_nodes:
            # The tokenizer splits "(Day(2)(Shift(1)))" into a 'Day' node
            # whose FIRST child is the "(2)" argument node (its header holds
            # the digit) and whose remaining children are the working shifts.
            arg = _node_arg(dn)
            if arg is None or not arg.isdigit():
                continue
            p6_day = int(arg)
            iso_day = _P6_DAY_TO_ISO.get(p6_day)
            if iso_day is None:
                continue
            standard_workweek[iso_day] = len(dn.get('children', [])) > 1
        if len(standard_workweek) < 7:
            warnings.append(
                f'Only {len(standard_workweek)} of 7 weekdays were found in DaysOfWeek — '
                'standard workweek may be incomplete.'
            )
    else:
        warnings.append('No DaysOfWeek block found in clndr_data.')

    exceptions: List[Dict[str, Any]] = []
    if exceptions_node:
        excp_nodes = _find_all_children(exceptions_node, 'Excp')
        unparsed = 0
        for en in excp_nodes:
            arg = _node_arg(en)
            d = _p6_serial_to_date(arg) if arg is not None else None
            if d is None:
                unparsed += 1
                continue
            exceptions.append({
                'date': d.isoformat(),
                'isWorkingDay': len(en.get('children', [])) > 1,
            })
        if unparsed:
            warnings.append(f'{unparsed} exception entr{"y" if unparsed == 1 else "ies"} could not be decoded.')
    else:
        warnings.append('No Exceptions block found in clndr_data.')

    if standard_workweek and exceptions_node is not None:
        confidence = 'high'
    elif standard_workweek or exceptions:
        confidence = 'partial'
    else:
        confidence = 'none'

    return {
        'confidence': confidence,
        'standardWorkweek': standard_workweek,
        'exceptions': exceptions,
        'warnings': warnings,
    }


# ── Calendar service ─────────────────────────────────────────────────────
# Operates on the *decoded* structure above. Every function returns None
# (never a guessed Mon-Fri fallback) when the calendar isn't confidently
# decoded — callers must display "unavailable", never silently substitute
# a simple weekday assumption, per the explicit product requirement.

class CalendarDefinition:
    """A decoded, queryable calendar. Construct via from_calendar_row()."""

    def __init__(self, standard_workweek: Dict[int, bool], exceptions: List[Dict[str, Any]]):
        self.standard_workweek = standard_workweek
        self.exceptions: Dict[date, bool] = {}
        for e in exceptions:
            try:
                d = date.fromisoformat(e['date'])
            except (KeyError, ValueError):
                continue
            self.exceptions[d] = bool(e.get('isWorkingDay'))

    @classmethod
    def from_decoded(cls, decoded: Dict[str, Any]) -> Optional['CalendarDefinition']:
        if decoded.get('confidence') not in ('high', 'partial') or not decoded.get('standardWorkweek'):
            return None
        return cls(decoded['standardWorkweek'], decoded.get('exceptions') or [])

    @classmethod
    def from_stored(
        cls,
        standard_workweek: Optional[Dict[Any, bool]],
        exceptions: Optional[List[dict]],
        has_detailed_definition: bool,
    ) -> Optional['CalendarDefinition']:
        """Builds a CalendarDefinition from a persisted Calendar model row's
        fields. A JSONField round-trip turns the standard_workweek dict's int
        keys into strings, so they're normalized back here."""
        if not has_detailed_definition or not standard_workweek:
            return None
        try:
            normalized = {int(k): bool(v) for k, v in standard_workweek.items()}
        except (TypeError, ValueError):
            return None
        return cls(normalized, exceptions or [])

    def is_working_day(self, d: date) -> bool:
        if d in self.exceptions:
            return self.exceptions[d]
        return bool(self.standard_workweek.get(d.isoweekday(), False))


def is_working_day(d: date, calendar: Optional[CalendarDefinition]) -> Optional[bool]:
    """Returns None (not False) when no confidently-decoded calendar is available."""
    if calendar is None:
        return None
    return calendar.is_working_day(d)


def next_working_day(d: date, calendar: Optional[CalendarDefinition]) -> Optional[date]:
    if calendar is None:
        return None
    cur = d + timedelta(days=1)
    for _ in range(3660):  # ~10 years safety bound
        if calendar.is_working_day(cur):
            return cur
        cur += timedelta(days=1)
    return None


def previous_working_day(d: date, calendar: Optional[CalendarDefinition]) -> Optional[date]:
    if calendar is None:
        return None
    cur = d - timedelta(days=1)
    for _ in range(3660):
        if calendar.is_working_day(cur):
            return cur
        cur -= timedelta(days=1)
    return None


def add_working_days(d: date, n: int, calendar: Optional[CalendarDefinition]) -> Optional[date]:
    """Adds n working days (n may be negative) to d. Returns None if the
    calendar isn't available. n == 0 returns d unchanged (even if d itself
    is a non-working day — callers wanting "the next working day on/after d"
    should combine with next_working_day())."""
    if calendar is None:
        return None
    if n == 0:
        return d
    cur = d
    step = 1 if n > 0 else -1
    remaining = abs(n)
    guard = 0
    while remaining > 0:
        cur += timedelta(days=step)
        guard += 1
        if guard > 36600:  # ~100 years safety bound
            return None
        if calendar.is_working_day(cur):
            remaining -= 1
    return cur


def working_days_between(start: date, finish: date, calendar: Optional[CalendarDefinition]) -> Optional[int]:
    """Count of working days strictly between start and finish (exclusive of
    start, inclusive of finish, matching how a duration/slip is normally
    counted) — sign-aware: negative when finish < start. Returns None
    without a decoded calendar."""
    if calendar is None:
        return None
    if start == finish:
        return 0
    forward = finish > start
    lo, hi = (start, finish) if forward else (finish, start)
    count = 0
    cur = lo + timedelta(days=1)
    while cur <= hi:
        if calendar.is_working_day(cur):
            count += 1
        cur += timedelta(days=1)
    return count if forward else -count
