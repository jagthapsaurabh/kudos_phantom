// "Skip new trades" schedule helpers — shared by Backtest, Paper Trade and
// Live Trade.
//
// The backend (app/core/trading_windows.py) is the authority: it decides
// whether a new entry is allowed. This module mirrors the same rules in the
// browser so the UI can show a readable description and a "paused right now"
// badge without a round trip.
//
// Weekdays use the backend convention: 0 = Monday … 6 = Sunday.
export const WEEKDAYS = [
  { value: 0, short: 'Mon', long: 'Monday' },
  { value: 1, short: 'Tue', long: 'Tuesday' },
  { value: 2, short: 'Wed', long: 'Wednesday' },
  { value: 3, short: 'Thu', long: 'Thursday' },
  { value: 4, short: 'Fri', long: 'Friday' },
  { value: 5, short: 'Sat', long: 'Saturday' },
  { value: 6, short: 'Sun', long: 'Sunday' },
];

export const WEEKDAY_ALIASES = {
  mon: 0, monday: 0, tue: 1, tues: 1, tuesday: 1,
  wed: 2, wednesday: 2, thu: 3, thur: 3, thurs: 3, thursday: 3,
  fri: 4, friday: 4, sat: 5, saturday: 5, sun: 6, sunday: 6,
};

export const DEFAULT_TIMEZONE = 'Asia/Kolkata';
export const COMMON_TIMEZONES = [
  'Asia/Kolkata',
  'UTC',
  'Asia/Dubai',
  'Asia/Singapore',
  'Europe/London',
  'America/New_York',
  'Australia/Sydney',
];

const MINUTES_PER_DAY = 24 * 60;
const MINUTES_PER_WEEK = 7 * MINUTES_PER_DAY;

export const weekdayShort = (value) => {
  const index = normalizeWeekday(value);
  return (WEEKDAYS[index] || WEEKDAYS[0]).short;
};

export const weekdayLong = (value) => {
  const index = normalizeWeekday(value);
  return (WEEKDAYS[index] || WEEKDAYS[0]).long;
};

/** Coerce a weekday to 0=Mon … 6=Sun. Accepts names and 0-6 in any form. */
export function normalizeWeekday(value) {
  if (value === null || value === undefined || value === '') return 0;
  const asNumber = Number(value);
  if (Number.isInteger(asNumber) && asNumber >= 0 && asNumber <= 6) return asNumber;
  const key = String(value).trim().toLowerCase();
  if (key in WEEKDAY_ALIASES) return WEEKDAY_ALIASES[key];
  return 0;
}

/** "18:30" -> 1110 minutes. Blank resolves to 0 (midnight). */
export function parseHhmm(value) {
  if (value === null || value === undefined || value === '') return 0;
  if (typeof value === 'number') return Math.max(0, Math.min(MINUTES_PER_DAY, Math.round(value)));
  const parts = String(value).trim().split(':');
  const hours = parseInt(parts[0], 10);
  const minutes = parseInt(parts[1] || '0', 10);
  if (Number.isNaN(hours) || Number.isNaN(minutes)) return 0;
  return Math.max(0, Math.min(MINUTES_PER_DAY, hours * 60 + minutes));
}

export const formatHhmm = (minutes) => {
  const total = ((Number(minutes) || 0) % MINUTES_PER_DAY + MINUTES_PER_DAY) % MINUTES_PER_DAY;
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
};

/** Preset the client asked for: Saturday 18:30 → Monday 01:00. */
export const weekendWindow = () => ({
  label: 'Weekend gap',
  start_day: 5, start_time: '18:30',
  end_day: 0, end_time: '01:00',
  all_day: false, enabled: true,
});

export const allDayWindow = (day) => ({
  label: '',
  start_day: normalizeWeekday(day), end_day: normalizeWeekday(day),
  start_time: '00:00', end_time: '00:00',
  all_day: true, enabled: true,
});

export const emptySchedule = () => ({
  enabled: false,
  timezone: DEFAULT_TIMEZONE,
  utc_offset_minutes: 330,
  block_exits: false,
  windows: [weekendWindow()],
});

const isObj = (v) => v && typeof v === 'object' && !Array.isArray(v);

export const normalizeWindow = (raw = {}) => ({
  label: raw.label != null ? String(raw.label) : '',
  start_day: normalizeWeekday(raw.start_day ?? 5),
  start_time: raw.start_time || '00:00',
  end_day: normalizeWeekday(raw.end_day ?? 0),
  end_time: raw.end_time || '00:00',
  all_day: Boolean(raw.all_day),
  enabled: raw.enabled === undefined ? true : Boolean(raw.enabled),
});

/** Fill in anything a saved run / server response omitted. */
export function normalizeSchedule(raw) {
  if (!isObj(raw)) return emptySchedule();
  const windows = Array.isArray(raw.windows) ? raw.windows.map(normalizeWindow) : [];
  return {
    enabled: Boolean(raw.enabled),
    timezone: raw.timezone || DEFAULT_TIMEZONE,
    utc_offset_minutes: raw.utc_offset_minutes === undefined ? 330 : Number(raw.utc_offset_minutes),
    block_exits: Boolean(raw.block_exits),
    windows,
  };
}

/** Minute-of-week the window starts at (0 = Monday 00:00 local). */
export const windowStart = (w) => normalizeWeekday(w.start_day) * MINUTES_PER_DAY
  + (w.all_day ? 0 : parseHhmm(w.start_time));

/** Minute-of-week the window ends at, exclusive. */
export function windowEnd(w) {
  if (w.all_day) return (normalizeWeekday(w.end_day) + 1) * MINUTES_PER_DAY;
  const end = normalizeWeekday(w.end_day) * MINUTES_PER_DAY + parseHhmm(w.end_time);
  // A zero-length timed window means "24 hours from the start time".
  return end === windowStart(w) ? windowStart(w) + MINUTES_PER_DAY : end;
}

export const windowWraps = (w) => windowEnd(w) <= windowStart(w);

/** Is `minuteOfWeek` inside this window? Mirrors the Python `contains`. */
export function windowContains(w, minuteOfWeek) {
  const start = windowStart(w);
  const end = windowEnd(w);
  const mow = ((Number(minuteOfWeek) || 0) % MINUTES_PER_WEEK + MINUTES_PER_WEEK) % MINUTES_PER_WEEK;
  if (end > start) return mow >= start && mow < end;
  return mow >= start || mow < end;
}

/** Readable one-liner for one window: "Sat 18:30 → Mon 01:00". */
export function describeWindow(w) {
  const win = normalizeWindow(w);
  if (win.all_day) {
    if (win.start_day === win.end_day) return `All day ${weekdayLong(win.start_day)}`;
    return `All day ${weekdayShort(win.start_day)} → ${weekdayShort(win.end_day)}`;
  }
  return `${weekdayShort(win.start_day)} ${formatHhmm(parseHhmm(win.start_time))}`
    + ` → ${weekdayShort(win.end_day)} ${formatHhmm(parseHhmm(win.end_time))}`;
}

/** Human list of the enabled windows; empty when the schedule is off. */
export function describeSchedule(schedule) {
  const cfg = normalizeSchedule(schedule);
  if (!cfg.enabled) return [];
  return cfg.windows.filter((w) => w.enabled).map(describeWindow);
}

export const activeWindows = (schedule) => {
  const cfg = normalizeSchedule(schedule);
  return cfg.enabled ? cfg.windows.filter((w) => w.enabled) : [];
};

export const isScheduleActive = (schedule) => activeWindows(schedule).length > 0;

/** The window blocking `date` (or null). `date` may be a Date or anything
 *  Date can parse (naive backend strings are read as UTC). */
export function blockingWindow(schedule, date = new Date()) {
  const windows = activeWindows(schedule);
  if (!windows.length) return null;
  const mow = minuteOfWeekIn(schedule, date);
  if (mow === null) return null;
  return windows.find((w) => windowContains(w, mow)) || null;
}

export const isEntryBlocked = (schedule, date = new Date()) => blockingWindow(schedule, date) !== null;

/** Minute-of-week for `date` in the schedule's timezone. */
export function minuteOfWeekIn(schedule, date = new Date()) {
  const cfg = normalizeSchedule(schedule);
  const when = date instanceof Date ? date : new Date(date);
  if (Number.isNaN(when.getTime())) return null;
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: cfg.timezone, weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(when);
    const get = (type) => (parts.find((p) => p.type === type) || {}).value || '';
    const dayIndex = WEEKDAY_ALIASES[String(get('weekday')).toLowerCase()];
    let hour = parseInt(get('hour'), 10);
    if (hour === 24) hour = 0;             // some engines emit "24" for midnight
    const minute = parseInt(get('minute'), 10) || 0;
    if (dayIndex === undefined || Number.isNaN(hour)) return null;
    return dayIndex * MINUTES_PER_DAY + hour * 60 + minute;
  } catch {
    return null;
  }
}

/** Short timezone label for the summary line, e.g. "IST" / "UTC". */
export function timezoneLabel(schedule, date = new Date()) {
  const cfg = normalizeSchedule(schedule);
  try {
    const parts = new Intl.DateTimeFormat('en-US', { timeZone: cfg.timezone, timeZoneName: 'short' })
      .formatToParts(date instanceof Date ? date : new Date(date));
    return (parts.find((p) => p.type === 'timeZoneName') || {}).value || cfg.timezone;
  } catch {
    return cfg.timezone;
  }
}

/** One-line summary used by the backtest / paper / live cards. */
export function scheduleSummary(schedule, date = new Date()) {
  const cfg = normalizeSchedule(schedule);
  if (!isScheduleActive(cfg)) return 'New entries allowed at any time';
  const tz = timezoneLabel(cfg, date);
  const blocked = blockingWindow(cfg, date);
  const list = describeSchedule(cfg).join(' · ');
  return `${blocked ? '⏸ Paused now' : '▶ Open now'} — skip new entries: ${list} (${tz})`;
}

/** Client-side validation; returns an array of human-readable problems. */
export function validateSchedule(schedule) {
  const cfg = normalizeSchedule(schedule);
  const problems = [];
  if (!cfg.enabled) return problems;
  if (!cfg.windows.length) problems.push('Turn the switch off, or add at least one window.');
  const hhmm = (value) => {
    if (!/^\d{1,2}:\d{2}$/.test(String(value || '').trim())) return null;
    const [h, m] = String(value).trim().split(':').map((n) => parseInt(n, 10));
    if (h > 23 || m > 59) return null;
    return h * 60 + m;
  };
  cfg.windows.forEach((w, index) => {
    const label = w.label || `Window ${index + 1}`;
    if (!w.all_day && (hhmm(w.start_time) === null || hhmm(w.end_time) === null)) {
      problems.push(`${label}: times must be HH:MM (24h).`);
    }
    if (!w.all_day && w.start_day === w.end_day && w.start_time === w.end_time) {
      problems.push(`${label}: start and end are the same — use "all day" for a 24h block.`);
    }
  });
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: cfg.timezone });
  } catch {
    problems.push(`Unknown timezone "${cfg.timezone}".`);
  }
  return problems;
}

// ---------------------------------------------------------------------------
// Quick presets (one click setups the UI offers next to the editor)
// ---------------------------------------------------------------------------
export const SCHEDULE_PRESETS = [
  {
    id: 'weekend',
    label: 'Weekend (Sat 18:30 → Mon 01:00)',
    build: () => ({
      enabled: true, timezone: DEFAULT_TIMEZONE, block_exits: false,
      windows: [weekendWindow()],
    }),
  },
  {
    id: 'sunday',
    label: 'Skip Sunday',
    build: () => ({
      enabled: true, timezone: DEFAULT_TIMEZONE, block_exits: false,
      windows: [allDayWindow(6)],
    }),
  },
  {
    id: 'weekend_days',
    label: 'Skip Saturday & Sunday',
    build: () => ({
      enabled: true, timezone: DEFAULT_TIMEZONE, block_exits: false,
      windows: [allDayWindow(5), allDayWindow(6)],
    }),
  },
  {
    id: 'friday_night',
    label: 'Fri 18:30 → Sat 02:00',
    build: () => ({
      enabled: true, timezone: DEFAULT_TIMEZONE, block_exits: false,
      windows: [{ label: 'Friday night', start_day: 4, start_time: '18:30',
                  end_day: 5, end_time: '02:00', all_day: false, enabled: true }],
    }),
  },
  {
    id: 'none',
    label: 'No restrictions',
    build: () => emptySchedule(),
  },
];
