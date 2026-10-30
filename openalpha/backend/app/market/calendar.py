"""NYSE regular-session clock for 2026 and 2027.

The holiday and early-close lists are taken from trader-skill by Jake Schincariol
(MIT, see NOTICE.md), which cites nyse.com; they match my own recollection of the
NYSE calendar but I could not open nyse.com to confirm. Other years raise
CalendarUnknown rather than guess.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..errors import OpenAlphaError

NY = ZoneInfo("America/New_York")
OPEN, CLOSE, EARLY_CLOSE = time(9, 30), time(16, 0), time(13, 0)

HOLIDAYS: dict[int, frozenset[date]] = {
    2026: frozenset({
        date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
        date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
        date(2026, 11, 26), date(2026, 12, 25),
    }),
    2027: frozenset({
        date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
        date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
        date(2027, 11, 25), date(2027, 12, 24),
    }),
}
EARLY_CLOSES: dict[int, frozenset[date]] = {
    2026: frozenset({date(2026, 11, 27), date(2026, 12, 24)}),
    2027: frozenset({date(2027, 11, 26)}),
}


class CalendarUnknown(OpenAlphaError):
    """The holiday list does not cover this year."""


def session(day: date) -> tuple[datetime, datetime] | None:
    """(open, close) in New York time, or None when the market is closed all day."""
    if day.year not in HOLIDAYS:
        raise CalendarUnknown(f"no NYSE holiday list for {day.year}")
    if day.weekday() >= 5 or day in HOLIDAYS[day.year]:
        return None
    close = EARLY_CLOSE if day in EARLY_CLOSES[day.year] else CLOSE
    return datetime.combine(day, OPEN, NY), datetime.combine(day, close, NY)


def is_open(now: datetime) -> bool:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(NY)
    window = session(local.date())
    return window is not None and window[0] <= local < window[1]


def next_open(now: datetime) -> datetime:
    """The next regular-session open strictly after ``now``."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(NY)
    for offset in range(0, 15):
        window = session(local.date() + timedelta(days=offset))
        if window is not None and window[0] > local:
            return window[0]
    raise CalendarUnknown("no open session found within 15 days")
