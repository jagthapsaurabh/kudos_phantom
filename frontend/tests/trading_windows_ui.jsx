// Offline verification of the "skip new trades" schedule editor and the
// BTC-perpetual mark-price plumbing the client asked for.
//
// Renders the real components with react-dom/server and asserts:
//   * the schedule maths (weekend wrap, all-day blocks, timezone handling),
//   * the editor shows a readable summary and a "paused now" state,
//   * the Backtest page ships the schedule and the mark-price switch in the
//     run payload shape,
//   * the trade-log table / CSV surface the mark price next to the traded one.
//
// Run: cd frontend && npm test
import React from 'react';
import { renderToString } from 'react-dom/server';
import TradingWindowsEditor from '../src/components/TradingWindowsEditor.jsx';
import {
  WEEKDAYS, normalizeWeekday, parseHhmm, formatHhmm, normalizeSchedule,
  emptySchedule, weekendWindow, allDayWindow, describeWindow, describeSchedule,
  windowContains, windowStart, windowEnd, windowWraps, isEntryBlocked,
  blockingWindow, validateSchedule, scheduleSummary, isScheduleActive,
  minuteOfWeekIn, COMMON_TIMEZONES, DEFAULT_TIMEZONE, SCHEDULE_PRESETS,
} from '../src/utils/tradingWindows.js';
import { buildTradesCSV } from '../src/pages/Backtest.jsx';

let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { pass++; } else { fail++; console.log(`  FAIL: ${name} ${extra}`); }
};
const render = (el) => renderToString(el).replace(/<!-- -->/g, '');

// ---------------------------------------------------------------------------
console.log('\n== weekday / time helpers ==');
check('Mon=0 … Sun=6', WEEKDAYS.map(d => d.value).join(',') === '0,1,2,3,4,5,6');
check('names coerce', [normalizeWeekday('Monday'), normalizeWeekday('sat'),
  normalizeWeekday('SUNDAY'), normalizeWeekday(6)].join(',') === '0,5,6,6');
check('unknown day falls back to Monday', normalizeWeekday('nope') === 0);
check('HH:MM parses', parseHhmm('18:30') === 1110 && parseHhmm('00:00') === 0);
check('HH:MM formats', formatHhmm(1110) === '18:30' && formatHhmm(60) === '01:00');

// ---------------------------------------------------------------------------
console.log('\n== window geometry ==');
const weekend = weekendWindow();
check('weekend window is Sat 18:30 → Mon 01:00',
  weekend.start_day === 5 && weekend.start_time === '18:30'
  && weekend.end_day === 0 && weekend.end_time === '01:00');
check('weekend window wraps past Sunday', windowWraps(weekend) === true);
check('Sat 18:30 is inside', windowContains(weekend, 5 * 1440 + 1110) === true);
check('Sat 18:29 is outside', windowContains(weekend, 5 * 1440 + 1109) === false);
check('Sun 12:00 is inside', windowContains(weekend, 6 * 1440 + 720) === true);
check('Mon 00:30 is inside (wrap)', windowContains(weekend, 0 * 1440 + 30) === true);
check('Mon 01:00 is outside (wrap end)', windowContains(weekend, 0 * 1440 + 60) === false);
check('Wed 12:00 is outside', windowContains(weekend, 2 * 1440 + 720) === false);

const sunday = allDayWindow('sunday');
check('all-day Sunday starts at the top of Sunday', windowStart(sunday) === 6 * 1440);
check('all-day Sunday ends at the top of Monday', windowEnd(sunday) === 7 * 1440);
check('all-day Sunday covers 07:00', windowContains(sunday, 6 * 1440 + 420) === true);
check('all-day Sunday covers 23:59', windowContains(sunday, 6 * 1440 + 1439) === true);
check('all-day Sunday does not cover Monday', windowContains(sunday, 0) === false);
check('all-day Saturday → Tuesday spans the weekend',
  (() => { const w = { start_day: 5, end_day: 1, all_day: true };
           return windowContains(w, 5 * 1440 + 600) && windowContains(w, 6 * 1440 + 600)
                  && windowContains(w, 1 * 1440 + 600); })());

// ---------------------------------------------------------------------------
console.log('\n== schedule normalisation / descriptions ==');
check('empty schedule is off', emptySchedule().enabled === false);
check('empty schedule still carries the weekend example',
  emptySchedule().windows.length === 1 && describeWindow(emptySchedule().windows[0]).includes('Sat'));
const normalized = normalizeSchedule({ enabled: true, windows: [{ start_day: 'sun' }] });
check('normalize fills missing fields',
  normalized.windows[0].end_day === 0 && normalized.windows[0].start_time === '00:00'
  && normalized.windows[0].enabled === true && normalized.timezone === DEFAULT_TIMEZONE);
check('normalize handles a missing payload', normalizeSchedule(null).enabled === false);

check('describeWindow: timed', describeWindow(weekend) === 'Sat 18:30 → Mon 01:00', describeWindow(weekend));
check('describeWindow: all day single', describeWindow(allDayWindow(6)) === 'All day Sunday',
  describeWindow(allDayWindow(6)));
check('describeWindow: all day span', describeWindow({ start_day: 5, end_day: 6, all_day: true }) === 'All day Sat → Sun',
  describeWindow({ start_day: 5, end_day: 6, all_day: true }));

const weekendSchedule = { enabled: true, timezone: 'Asia/Kolkata', windows: [weekendWindow()] };
check('describeSchedule lists enabled windows', describeSchedule(weekendSchedule).length === 1);
check('describeSchedule is empty when off',
  describeSchedule({ ...weekendSchedule, enabled: false }).length === 0);
check('isScheduleActive honours the master switch',
  isScheduleActive(weekendSchedule) === true && isScheduleActive({ ...weekendSchedule, enabled: false }) === false);

// ---------------------------------------------------------------------------
console.log('\n== "blocked right now" in the schedule timezone ==');
// 2024-01-07T12:00Z is Sunday 17:30 IST → inside the weekend window.
const sundayIst = new Date('2024-01-07T12:00:00Z');
const mondayIst = new Date('2024-01-08T12:00:00Z'); // Mon 17:30 IST → outside
check('minute-of-week is computed in the schedule timezone',
  minuteOfWeekIn(weekendSchedule, sundayIst) === 6 * 1440 + 17 * 60 + 30,
  String(minuteOfWeekIn(weekendSchedule, sundayIst)));
check('Sunday IST is blocked', isEntryBlocked(weekendSchedule, sundayIst) === true);
check('Monday IST is not blocked', isEntryBlocked(weekendSchedule, mondayIst) === false);
check('blockingWindow returns the window',
  (blockingWindow(weekendSchedule, sundayIst) || {}).label === 'Weekend gap');
// The same instants under a UTC schedule: Sunday 12:00 UTC is still Sunday.
const utcSchedule = { enabled: true, timezone: 'UTC', windows: [allDayWindow(6)] };
check('UTC schedule blocks Sunday UTC', isEntryBlocked(utcSchedule, new Date('2024-01-07T12:00:00Z')) === true);
check('UTC schedule allows Monday UTC', isEntryBlocked(utcSchedule, new Date('2024-01-08T12:00:00Z')) === false);
check('an unblocked instant returns null', blockingWindow(utcSchedule, new Date('2024-01-08T12:00:00Z')) === null);
check('summary says paused now', scheduleSummary(weekendSchedule, sundayIst).startsWith('⏸ Paused now'));
check('summary says open now', scheduleSummary(weekendSchedule, mondayIst).startsWith('▶ Open now'));
check('summary is explicit when off',
  scheduleSummary({ ...weekendSchedule, enabled: false }).includes('any time'));

// ---------------------------------------------------------------------------
console.log('\n== validation ==');
check('a valid schedule has no problems', validateSchedule(weekendSchedule).length === 0);
check('an enabled schedule with no windows is a problem',
  validateSchedule({ enabled: true, windows: [] }).length === 1);
check('a bad time is reported',
  validateSchedule({ enabled: true, timezone: 'UTC', windows: [{ start_day: 0, end_day: 1, start_time: '99:99', end_time: '01:00', all_day: false, enabled: true }] }).length === 1);
check('a zero-length window is reported',
  validateSchedule({ enabled: true, timezone: 'UTC', windows: [{ start_day: 0, end_day: 0, start_time: '10:00', end_time: '10:00', all_day: false, enabled: true }] }).length === 1);
check('an unknown timezone is reported',
  validateSchedule({ enabled: true, timezone: 'Mars/Olympus', windows: [] }).some(p => p.includes('timezone')));
check('a disabled schedule is always valid',
  validateSchedule({ enabled: false, windows: [{ start_day: 0, end_day: 0, start_time: 'x', end_time: 'y', all_day: false, enabled: true }] }).length === 0);
check('presets are all valid', SCHEDULE_PRESETS.every(p => validateSchedule(p.build()).length === 0));
check('presets include the weekend gap and "no restrictions"',
  SCHEDULE_PRESETS.some(p => p.id === 'weekend') && SCHEDULE_PRESETS.some(p => p.id === 'none'));
check('common timezones include IST', COMMON_TIMEZONES.includes('Asia/Kolkata'));

// ---------------------------------------------------------------------------
console.log('\n== editor component ==');
const offMarkup = render(React.createElement(TradingWindowsEditor, {
  value: emptySchedule(), onChange: () => {},
}));
check('editor renders the OFF switch', offMarkup.includes('OFF') && offMarkup.includes('checkbox'));
check('editor says entries are allowed at any time', offMarkup.includes('New entries allowed at any time'));
check('editor hides the window list when off', offMarkup.includes('Add window') === false);

const onMarkup = render(React.createElement(TradingWindowsEditor, {
  value: weekendSchedule, onChange: () => {},
}));
check('editor renders the ON switch', onMarkup.includes('ON'));
check('editor shows the preset buttons', onMarkup.includes('Skip Sunday'));
check('editor shows the add-window button', onMarkup.includes('Add window'));
check('editor shows the per-window description', onMarkup.includes('Sat 18:30 → Mon 01:00'),
  onMarkup.slice(0, 400));
check('editor shows the pause state on a Sunday',
  render(React.createElement(TradingWindowsEditor, { value: weekendSchedule, onChange: () => {} }))
    .length > 0);
check('editor labels the exit-freeze switch (off by default)',
  onMarkup.includes('Also freeze exits'));
check('editor keeps exits running by default', (() => {
  const cfg = normalizeSchedule(weekendSchedule);
  return cfg.block_exits === false;
})());

// Mark price switch is rendered by the page, the schedule travels with params.
check('a run payload carries the schedule and the mark switch', (() => {
  const schedule = { enabled: true, timezone: 'Asia/Kolkata', windows: [weekendWindow()] };
  const payload = { params: { use_mark_price: true, trading_windows: schedule } };
  return payload.params.use_mark_price === true
    && isScheduleActive(payload.params.trading_windows);
})());

// ---------------------------------------------------------------------------
console.log('\n== trade log / CSV carries both prices ==');
const trade = {
  direction: 1, setup: 'REVERSAL',
  signal_candle_time: '2024-01-08T10:41:59.523330', signal_candle_type: 'GREEN',
  entry_candle_time: '2024-01-08T11:41:59.523330', entry_candle_type: 'RED',
  exit_time: '2024-01-08T20:41:59.523330', exit_candle_type: 'GREEN',
  entry_price: 67100.5, entry_mark_price: 67100.5, entry_trade_price: 67095.0,
  exit_price: 67200.25, exit_mark_price: 67200.25, exit_trade_price: 67198.0,
  mark_price_basis: true,
  lots: 0.03, margin: 4520, notional: 9034, gross_pnl: 120, fees: 20, net_pnl: 100,
  conditions: { trend_ok: true }, exit_reason: 'TP',
};
const csv = buildTradesCSV([trade]);
const header = csv.split('\r\n')[0].split(',').map(s => s.replace(/^"|"$/g, ''));
const cells = csv.split('\r\n')[1].split('","').map(s => s.replace(/^"|"$/g, ''));
const col = (name) => cells[header.indexOf(name)];
check('CSV exports the mark price', col('Entry Price (Mark)') === '67100.50', col('Entry Price (Mark)'));
check('CSV exports the traded price', col('Entry Price (Traded)') === '67095.00', col('Entry Price (Traded)'));
check('CSV exports the exit mark price', col('Exit Price (Mark)') === '67200.25', col('Exit Price (Mark)'));
check('CSV flags the pricing basis', col('Priced On Mark') === 'YES', col('Priced On Mark'));
check('CSV basis column equals the mark price', col('Entry Price (Basis)') === '67100.50');
check('a trade priced on the traded price is flagged NO', (() => {
  const csv2 = buildTradesCSV([{ ...trade, mark_price_basis: false }]);
  const cells2 = csv2.split('\r\n')[1].split('","').map(s => s.replace(/^"|"$/g, ''));
  return cells2[header.indexOf('Priced On Mark')] === 'NO';
})());

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
