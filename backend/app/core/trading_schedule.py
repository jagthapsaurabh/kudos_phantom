"""Weekly new-trade skip schedule (India Standard Time).

The client wants to optionally stop *new entries* during a weekly window while
leaving already-open positions fully managed. The schedule is evaluated in IST
(UTC+5:30) no matter where the server runs, because that is the timezone the
clients quote (e.g. "Saturday 5:30 pm Indian time to Sunday 5:30 pm Indian
time").
"""
from datetime import timedelta, timezone
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

IST_OFFSET = timedelta(hours=5, minutes=30)

# Python ``datetime.weekday()`` uses Monday=0; the UI speaks day names.
WEEKDAY_TO_MIN = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
WEEKDAYS = tuple("Monday Tuesday Wednesday Thursday Friday Saturday Sunday".split())
DAY_ALIASES = {
    "mon": "Monday", "tue": "Tuesday", "tues": "Tuesday", "wed": "Wednesday",
    "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday",
    "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
    **{d.lower(): d for d in WEEKDAYS},
}


def normalize_day(value) -> str:
    """Return a canonical Monday..Sunday day name."""
    if value is None:
        raise ValueError("day is required")
    key = str(value).strip().lower()
    canonical = DAY_ALIASES.get(key, key)
    if canonical not in WEEKDAYS:
        raise ValueError(f"unknown weekday '{value}' (expected Monday..Sunday)")
    return canonical


def _parse_time(value) -> tuple[int, int]:
    """Parse ``HH:MM`` (or ``H:MM``) into (hour, minute)."""
    if value is None:
        raise ValueError("time is required")
    text = str(value).strip()
    if text.lower().endswith(("am", "pm")):
        # Accept "5:30pm"/"5:30 pm" too, although the UI sends 24h strings.
        import re
        m = re.match(r"^(\d{1,2}):(\d{2})\s*([ap]m)$", text.lower())
        if not m:
            raise ValueError(f"invalid time '{value}'")
        hour, minute = int(m.group(1)), int(m.group(2))
        if m.group(3) == "pm" and hour != 12:
            hour += 12
        if m.group(3) == "am" and hour == 12:
            hour = 0
    else:
        parts = text.split(":")
        if len(parts) != 2:
            raise ValueError(f"invalid time '{value}' (expected HH:MM)")
        hour = int(parts[0])
        minute = int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"invalid time '{value}'")
    return hour, minute


def _day_minutes(day: str, time_str: str) -> int:
    hour, minute = _parse_time(time_str)
    return WEEKDAY_TO_MIN[normalize_day(day).lower()] * 1440 + hour * 60 + minute


class TradeSkipWindow(BaseModel):
    """One weekly block during which new trades are skipped.

    ``start_day``/``end_day`` are Monday..Sunday and the times are HH:MM in
    India Standard Time. A window may cross midnight and/or the week boundary
    (e.g. Saturday 17:30 -> Sunday 17:30), which is how the client describes
    the weekend pause.
    """
    start_day: str = Field(default="Saturday")
    start_time: str = Field(default="17:30")
    end_day: str = Field(default="Sunday")
    end_time: str = Field(default="17:30")

    @field_validator("start_day", "end_day")
    @classmethod
    def _valid_day(cls, value):
        return normalize_day(value)

    @field_validator("start_time", "end_time")
    @classmethod
    def _valid_time(cls, value):
        return f"{_parse_time(value)[0]:02d}:{_parse_time(value)[1]:02d}"

    def in_window(self, ist_weekday: int, ist_minutes: int) -> bool:
        start = _day_minutes(self.start_day, self.start_time)
        end = _day_minutes(self.end_day, self.end_time)
        if start < end:
            # Half-open: [start, end) so the exact end time opens new entries.
            return start <= ist_minutes < end
        # Crosses midnight and/or the week boundary (or is a zero-length block).
        return ist_minutes >= start or ist_minutes < end if start != end else False


def _ist(dt):
    """Convert a naive-UTC (or offset-aware UTC) datetime to naive IST."""
    if dt is None:
        raise ValueError("datetime required")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt + IST_OFFSET


def is_new_trade_blocked(dt, skip_new_trades: bool = False, skip_days: Optional[List[str]] = None,
                         skip_blocks: Optional[List[TradeSkipWindow]] = None) -> bool:
    """Whether ``dt`` (UTC) falls inside a configured no-new-entry block (IST)."""
    if not skip_new_trades:
        return False
    ist = _ist(dt)
    weekday = ist.weekday()
    minutes = weekday * 1440 + ist.hour * 60 + ist.minute

    for day in (skip_days or []):
        if WEEKDAY_TO_MIN.get(normalize_day(day).lower()) == weekday:
            return True

    for block in (skip_blocks or []):
        if block.in_window(weekday, minutes):
            return True
    return False
