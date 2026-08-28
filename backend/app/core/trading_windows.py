"""Configurable "no new trades" windows (weekend/holiday blackouts).

PHANTOM can be told to keep running — managing stops, trailing and exits on
positions that are already open — while refusing to *open* new ones during
chosen periods. The client asked for the classic crypto weekend gap window
(Saturday 18:30 IST → Monday 01:00 IST) but the model is deliberately general:

* any number of windows,
* each window spans an arbitrary day/time range (it may cross midnight and it
  may wrap across the end of the week, e.g. Saturday → Monday),
* each window can be a whole-day block (Sunday) or a timed block,
* the schedule is interpreted in any IANA timezone (Asia/Kolkata by default),
* entries only are blocked; managing/closing open trades is controlled by the
  separate ``block_exits`` switch (off by default, which is the requested
  behaviour: "already working trade will work same").

The same config object is used by the backtest engine, the paper trader and the
live trader, and it is persisted with the run / session so a result can always
be reproduced.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field, field_validator

try:  # Python 3.9+
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - zoneinfo is stdlib on every supported run
    ZoneInfo = None  # type: ignore

# ---------------------------------------------------------------------------
# Weekday handling — Python's Monday=0 convention is used everywhere.
# ---------------------------------------------------------------------------
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAY_LONG_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                      "Saturday", "Sunday"]
WEEKDAY_ALIASES = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}

MINUTES_PER_HOUR = 60
MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR
MINUTES_PER_WEEK = 7 * MINUTES_PER_DAY

DEFAULT_TIMEZONE = "Asia/Kolkata"
# Used only when the host has no IANA tz database installed.
DEFAULT_UTC_OFFSET_MINUTES = 330  # IST

# Rejected-signal key reported by the engine / traders when an entry is skipped.
BLOCK_REASON = "TRADING_WINDOW"


def normalize_weekday(value: Any) -> int:
    """Coerce a weekday to ``0=Monday … 6=Sunday``.

    Accepts an int in either convention is ambiguous, so only the Python
    convention (Mon=0) is accepted for numbers; use the names for anything
    else. Names are matched case-insensitively and may be abbreviated.
    """
    if isinstance(value, bool):
        raise ValueError("weekday must be a name or an integer 0-6")
    if isinstance(value, int):
        if 0 <= value <= 6:
            return int(value)
        raise ValueError(f"weekday must be 0-6 (Mon=0), got {value}")
    text = str(value).strip().lower()
    if not text:
        raise ValueError("weekday is required")
    if text.isdigit():
        number = int(text)
        if 0 <= number <= 6:
            return number
        raise ValueError(f"weekday must be 0-6 (Mon=0), got {value}")
    if text in WEEKDAY_ALIASES:
        return WEEKDAY_ALIASES[text]
    raise ValueError(
        f"unknown weekday '{value}'. Use Mon, Tue, Wed, Thu, Fri, Sat or Sun "
        f"(0=Mon … 6=Sun)."
    )


def weekday_name(index: int, long: bool = False) -> str:
    names = WEEKDAY_LONG_NAMES if long else WEEKDAY_NAMES
    try:
        return names[int(index) % 7]
    except (TypeError, ValueError):
        return "?"


def parse_hhmm(value: Any, default_minutes: int = 0) -> int:
    """Parse ``"HH:MM"`` (24h) into minutes past midnight.

    Blank/absent resolves to ``default_minutes`` so a window can omit a time.
    ``"24:00"`` is accepted as end-of-day.
    """
    if value is None:
        return int(default_minutes)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(max(0, min(MINUTES_PER_DAY, round(float(value)))))
    text = str(value).strip()
    if not text:
        return int(default_minutes)
    parts = text.split(":")
    if len(parts) < 2:
        raise ValueError(f"time must be HH:MM (24h), got '{value}'")
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
    except ValueError:
        raise ValueError(f"time must be HH:MM (24h), got '{value}'")
    if len(parts) > 2:  # tolerate HH:MM:SS, seconds are ignored
        pass
    total = hours * MINUTES_PER_HOUR + minutes
    if not (0 <= total <= MINUTES_PER_DAY):
        raise ValueError(f"time must be between 00:00 and 24:00, got '{value}'")
    return int(total)


def format_hhmm(minutes: int) -> str:
    minutes = int(minutes) % MINUTES_PER_DAY
    return f"{minutes // MINUTES_PER_HOUR:02d}:{minutes % MINUTES_PER_HOUR:02d}"


def _tz_or_fallback(name: str, utc_offset_minutes: int):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name or DEFAULT_TIMEZONE)
        except Exception:
            pass
    return timezone(timedelta(minutes=int(utc_offset_minutes or DEFAULT_UTC_OFFSET_MINUTES)))


def to_local(dt: Optional[datetime], tz, utc_offset_minutes: int = DEFAULT_UTC_OFFSET_MINUTES) -> Optional[datetime]:
    """Interpret ``dt`` (naive values are UTC, as stored in the DB) as local time."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(tz)
    except Exception:
        return dt.astimezone(_tz_or_fallback(DEFAULT_TIMEZONE, utc_offset_minutes))


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
class TradingWindow(BaseModel):
    """One blocked period.

    ``all_day`` windows cover every minute of ``start_day`` through the end of
    ``end_day`` (so Sunday→Sunday blocks all of Sunday). Timed windows run from
    ``start_time`` on ``start_day`` to ``end_time`` on ``end_day``; when the end
    precedes the start the window wraps through the end of the week, which is
    how "Saturday 18:30 → Monday 01:00" is expressed.
    """

    label: str = ""
    start_day: int = 5           # Saturday
    start_time: str = "18:30"
    end_day: int = 0             # Monday
    end_time: str = "01:00"
    all_day: bool = False
    enabled: bool = True

    _norm_day = field_validator("start_day", "end_day", mode="before")(normalize_weekday)

    @field_validator("start_time", "end_time")
    @classmethod
    def _validate_time(cls, value):
        # Blank is allowed (means 00:00 / whole-day), anything else must parse.
        if value is None or str(value).strip() == "":
            return "00:00"
        parse_hhmm(value)
        return str(value).strip()

    # -- geometry ------------------------------------------------------
    @property
    def start_minute(self) -> int:
        base = int(self.start_day) * MINUTES_PER_DAY
        return base + (0 if self.all_day else parse_hhmm(self.start_time))

    @property
    def end_minute_exclusive(self) -> int:
        """Minute-of-week at which the window ends (exclusive)."""
        if self.all_day:
            return (int(self.end_day) + 1) * MINUTES_PER_DAY
        end = int(self.end_day) * MINUTES_PER_DAY + parse_hhmm(self.end_time)
        if end == self.start_minute:
            # A zero-length timed window is almost certainly meant as a
            # 24h block from the start time (e.g. 18:30 → 18:30 next day).
            end = self.start_minute + MINUTES_PER_DAY
        return end

    @property
    def wraps(self) -> bool:
        return self.end_minute_exclusive <= self.start_minute

    def contains(self, minute_of_week: int) -> bool:
        """Whether ``minute_of_week`` (0 = Monday 00:00 local) is inside."""
        start = self.start_minute
        end = self.end_minute_exclusive
        mow = int(minute_of_week) % MINUTES_PER_WEEK
        if end > start:
            return start <= mow < end
        # Wraps past Sunday into the next week (or spans the whole week).
        return mow >= start or mow < end

    def duration_minutes(self) -> int:
        end = self.end_minute_exclusive
        start = self.start_minute
        return (end - start) % MINUTES_PER_WEEK or MINUTES_PER_WEEK

    def describe(self) -> str:
        if self.all_day:
            if self.start_day == self.end_day:
                span = weekday_name(self.start_day, long=True)
            else:
                span = f"{weekday_name(self.start_day)} → {weekday_name(self.end_day)} (all day)"
            return f"All day {span}"
        return (f"{weekday_name(self.start_day)} {format_hhmm(parse_hhmm(self.start_time))}"
                f" → {weekday_name(self.end_day)} {format_hhmm(parse_hhmm(self.end_time))}")


class TradingWindowConfig(BaseModel):
    """Schedule-level switch plus the list of windows.

    ``enabled`` is the master toggle the UI exposes ("Skip new trades on
    selected days"). With it off every window is ignored, so a saved
    configuration can be kept and switched on later.
    """

    enabled: bool = False
    timezone: str = Field(default=DEFAULT_TIMEZONE)
    # Only used when the host has no IANA timezone database.
    utc_offset_minutes: int = Field(default=DEFAULT_UTC_OFFSET_MINUTES)
    # New entries are always gated. Exits keep running by default so an open
    # position is still managed during the block (the requested behaviour).
    block_exits: bool = False
    windows: List[TradingWindow] = Field(default_factory=list)

    @field_validator("windows", mode="before")
    @classmethod
    def _coerce_windows(cls, value):
        if value is None:
            return []
        if isinstance(value, dict):  # tolerate a single window object
            return [value]
        return list(value)

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value):
        text = str(value or "").strip()
        if not text:
            return DEFAULT_TIMEZONE
        if ZoneInfo is not None:
            try:
                ZoneInfo(text)
                return text
            except Exception:
                pass
        # Fall back to IST rather than rejecting: the app is India-facing and a
        # bad zone name should not stop a run from starting.
        return DEFAULT_TIMEZONE

    # ------------------------------------------------------------------
    @property
    def active_windows(self) -> List[TradingWindow]:
        if not self.enabled:
            return []
        return [w for w in (self.windows or []) if w.enabled]

    def describe(self) -> List[str]:
        return [w.describe() + (f" — {w.label}" if w.label else "") for w in self.active_windows]


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------
def weekend_window(start_time: str = "18:30", end_time: str = "01:00",
                   start_day: Any = 5, end_day: Any = 0, label: str = "Weekend gap") -> TradingWindow:
    """The window the client asked for: Saturday 18:30 → Monday 01:00 IST."""
    return TradingWindow(label=label, start_day=start_day, start_time=start_time,
                         end_day=end_day, end_time=end_time, all_day=False, enabled=True)


def all_day_window(day: Any, label: str = "") -> TradingWindow:
    day_index = normalize_weekday(day)
    return TradingWindow(label=label, start_day=day_index, end_day=day_index,
                         start_time="00:00", end_time="00:00",
                         all_day=True, enabled=True)


def default_config() -> TradingWindowConfig:
    """Off by default, but carrying the documented weekend example."""
    return TradingWindowConfig(enabled=False, timezone=DEFAULT_TIMEZONE,
                               windows=[weekend_window()])


WEEKEND_PRESET = {
    "label": "Weekend (Sat 18:30 → Mon 01:00 IST)",
    "start_day": 5, "start_time": "18:30",
    "end_day": 0, "end_time": "01:00", "all_day": False,
}


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------
class TradingWindowGuard:
    """Answers "may I open a new trade right now?" for one configuration."""

    def __init__(self, config: Optional[TradingWindowConfig] = None):
        self.config = config if isinstance(config, TradingWindowConfig) else (
            TradingWindowConfig(**config) if isinstance(config, dict) else TradingWindowConfig()
        )
        self._tz = _tz_or_fallback(self.config.timezone, self.config.utc_offset_minutes)

    # -- construction ---------------------------------------------------
    @classmethod
    def from_any(cls, value) -> "TradingWindowGuard":
        if isinstance(value, TradingWindowGuard):
            return value
        if value is None:
            return cls(TradingWindowConfig())
        if isinstance(value, TradingWindowConfig):
            return cls(value)
        if isinstance(value, dict):
            return cls(TradingWindowConfig(**value))
        config = getattr(value, "trading_windows", None)
        if isinstance(config, TradingWindowConfig):
            return cls(config)
        if isinstance(config, dict):
            return cls(TradingWindowConfig(**config))
        return cls(TradingWindowConfig())

    # -- state ----------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled) and bool(self.config.active_windows)

    @property
    def timezone(self):
        return self._tz

    def local_now(self) -> datetime:
        return datetime.now(self._tz)

    def to_local(self, dt: Optional[datetime]) -> Optional[datetime]:
        return to_local(dt, self._tz, self.config.utc_offset_minutes)

    def minute_of_week(self, dt: Optional[datetime]) -> Optional[int]:
        local = self.to_local(dt)
        if local is None:
            return None
        return local.weekday() * MINUTES_PER_DAY + local.hour * MINUTES_PER_HOUR + local.minute

    # -- decisions ------------------------------------------------------
    def blocking_window(self, dt: Optional[datetime]) -> Optional[TradingWindow]:
        """Return the window that blocks ``dt`` (or ``None`` when allowed)."""
        if not self.enabled or dt is None:
            return None
        mow = self.minute_of_week(dt)
        if mow is None:
            return None
        for window in self.config.active_windows:
            if window.contains(mow):
                return window
        return None

    def is_blocked(self, dt: Optional[datetime]) -> bool:
        return self.blocking_window(dt) is not None

    def allows_new_entry(self, dt: Optional[datetime]) -> bool:
        return not self.is_blocked(dt)

    def allows_exit(self, dt: Optional[datetime] = None) -> bool:
        """Exits stay enabled unless the operator opted into blocking them."""
        return not (self.config.block_exits and self.is_blocked(dt))

    def blocked_reason(self, dt: Optional[datetime]) -> Optional[str]:
        window = self.blocking_window(dt)
        if window is None:
            return None
        label = window.label or window.describe()
        return f"{BLOCK_REASON}: {label}"

    def next_open_from(self, dt: Optional[datetime] = None) -> Optional[datetime]:
        """Next local datetime at which entries are allowed again.

        ``None`` when the schedule is off (entries are already allowed at any
        time), or when no opening could be found inside the next week.
        """
        if not self.enabled:
            return None
        when = self.to_local(dt if dt is not None else self.local_now())
        if when is None:
            return None
        mow = self.minute_of_week(when)
        if mow is None:
            return None
        best_delta = None
        for _ in range(2):
            for window in self.config.active_windows:
                for boundary in (window.end_minute_exclusive,):
                    delta = (boundary - mow) % MINUTES_PER_WEEK
                    if delta == 0:
                        delta = MINUTES_PER_WEEK
                    if self._is_open_at((mow + delta) % MINUTES_PER_WEEK):
                        best_delta = delta if best_delta is None else min(best_delta, delta)
            if best_delta is not None:
                break
        if best_delta is None:
            return None
        return when + timedelta(minutes=best_delta)

    def _is_open_at(self, mow: int) -> bool:
        return not any(w.contains(mow) for w in self.config.active_windows)

    # -- presentation ---------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        return {
            "enabled": bool(self.config.enabled),
            "active": self.enabled,
            "timezone": self.config.timezone,
            "utc_offset_minutes": int(self.config.utc_offset_minutes),
            "block_exits": bool(self.config.block_exits),
            "windows": [w.model_dump() for w in (self.config.windows or [])],
            "descriptions": self.describe(),
        }

    def describe(self) -> List[str]:
        return self.config.describe()


def merge_windows(base, override) -> TradingWindowConfig:
    """Return ``override`` when it carries an explicit schedule, else ``base``.

    Paper / live start requests may omit the schedule entirely; in that case
    the account-level default (saved from the UI) is used.
    """
    if override is None:
        return base if isinstance(base, TradingWindowConfig) else TradingWindowConfig()
    if isinstance(override, TradingWindowGuard):
        return override.config
    if isinstance(override, TradingWindowConfig):
        return override
    if isinstance(override, dict):
        if not override:
            return base if isinstance(base, TradingWindowConfig) else TradingWindowConfig()
        return TradingWindowConfig(**override)
    return base if isinstance(base, TradingWindowConfig) else TradingWindowConfig()
