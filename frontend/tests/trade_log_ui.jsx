// Offline verification of the trade-log UI and the Excel/CSV export.
//
// Renders the real components with the exact trade shape returned by
// GET /backtest/results/{run_id} and asserts what the client asked for:
// which candle the entry filled on, its colour, every entry condition, and the
// exit condition — on screen and in the exported spreadsheet.
//
// Run: cd frontend && npm test
import React from 'react';
import { renderToString } from 'react-dom/server';
import {
  buildTradesCSV, condLabel, fmtCandleTime, TradeLogTable,
  CandleChip, CondChip, atrRegimeRuleFor,
} from '../src/pages/Backtest.jsx';

let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { pass++; } else { fail++; console.log(`  FAIL: ${name} ${extra}`); }
};

// React's server renderer inserts <!-- --> between adjacent text nodes, so the
// visible text has to be normalised before asserting on it.
const render = (el) => renderToString(el).replace(/<!-- -->/g, '');

// The real params shape: a nested entry_conditions with per-direction branches.
const params = {
  atr_regime_ratio: 0.5,
  entry_conditions: {
    use_direction_conditions: false,
    use_direction_macd_hist: false,
    use_direction_atr_floor: true,
    long:  { atr_regime_op: '>=', atr_regime_ratio: 0.5 },
    short: { atr_regime_op: '<',  atr_regime_ratio: 1.2 },
  },
};

// Exactly what the API returned for the first trade of the live run.
const momTrade = {
  direction: -1, setup: 'MOMENTUM',
  signal_candle_time: '2020-06-26T12:41:59.523330', signal_candle_type: 'RED',
  entry_time: '2020-06-26T13:41:59.523330',
  entry_candle_time: '2020-06-26T13:41:59.523330', entry_candle_type: 'GREEN',
  entry_price: 10037.92,
  exit_time: '2020-06-26T14:41:59.523330', exit_candle_type: 'RED',
  exit_price: 10260.25, exit_reason: 'SL',
  exit_detail: 'Stop loss hit — price rose to 10,260.25 ≥ SL 10,240.23 (initial SL 10,240.23)',
  sl_entry: 10240.227, sl: 10240.227, tp: 9897.7, trail_stop: 10240.227,
  atr_at_entry: 20.0, peak_price: 10037.92,
  entry_conditions_detail: [
    'Side: SHORT | Setup: MOMENTUM',
    '1. 4h trend: close 10,037.92 vs EMA50(4h) 10,058.11 -> DOWN; SHORT needs DOWN -> PASS',
    '2. ADX: 13.3 >= min 10.0 -> PASS',
    '3. MACD hist magnitude: not applied — Setup B (momentum) enters on the MACD zero-cross instead -> N/A',
    '4. ATR regime: ATR 20.00 < 1.20 x SMA50(ATR) 19.85 = 23.82 -> PASS',
    '5. DI confirmation: +DI 0.0 vs -DI 24.5 -> needs -DI > +DI -> PASS',
    '6. MACD zero-cross: hist 0.00 -> -0.82 -> needs cross below 0 -> PASS',
    '7. RSI agreement: RSI 0.0 <= 50.0 -> PASS',
  ].join('\n'),
  conditions: { trend_ok: 1, adx_ok: 1, macd_hist_ok: null, atr_regime_ok: 1,
                rsi_ok: 1, macd_confirm_ok: 1, di_ok: 1 },
  trend_4h: 'DOWN', rsi14: 31.2, macd_hist: -0.82, adx: 13.3, atr14: 20.0,
  ema50_1h: 10100.5, ema50_4h: 10058.11,
  lots: 0.03, margin: 4520, notional: 9034, margin_pct_used: 0.226,
  gross_pnl: -6.67, fees: 1.8, net_pnl: -8.47, equity_after: 19991.5,
  drawdown: 0.04, hold_bars: 1,
};
const revTrade = { ...momTrade,
  direction: 1, setup: 'REVERSAL',
  signal_candle_type: 'GREEN', entry_candle_type: 'RED', exit_candle_type: 'GREEN',
  entry_conditions_detail: [
    'Side: LONG | Setup: REVERSAL',
    '5. RSI trigger: prev RSI 31.2 < 40.0 -> PASS',
    '6. Candle colour: GREEN -> needs GREEN -> PASS',
    '7. MACD confirmation: hist -12.0 -> -8.0 -> needs rising -> PASS',
  ].join('\n'),
  conditions: { trend_ok: 1, adx_ok: 1, macd_hist_ok: 1, atr_regime_ok: 1,
                rsi_ok: 1, macd_confirm_ok: 1, di_ok: null },
  exit_reason: 'TP', exit_detail: 'Take profit hit — price reached 10,500.00 ≥ TP 10,480.00' };
const trades = [momTrade, revTrade];

// ---------- 1. the per-side ATR rule text (Feature 1, shown in the log) ------
check('rule text: long uses its own operator/ratio',
  atrRegimeRuleFor(params, 1) === 'ATR ≥ 0.5 × SMA50(ATR)',
  atrRegimeRuleFor(params, 1));
check('rule text: short uses its own operator/ratio',
  atrRegimeRuleFor(params, -1) === 'ATR < 1.2 × SMA50(ATR)',
  atrRegimeRuleFor(params, -1));
const offParams = { atr_regime_ratio: 0.5,
  entry_conditions: { use_direction_atr_floor: false, long: { atr_regime_op: '<', atr_regime_ratio: 9 },
                      short: { atr_regime_op: '<', atr_regime_ratio: 9 } } };
check('rule text: toggle off falls back to the legacy rule for both sides',
  atrRegimeRuleFor(offParams, 1) === 'ATR ≥ 0.5 × SMA50(ATR)'
  && atrRegimeRuleFor(offParams, -1) === 'ATR ≥ 0.5 × SMA50(ATR)');

// ---------- 2. UTC candle time formatting -----------------------------------
check('fmtCandleTime renders UTC to the minute',
  fmtCandleTime('2020-06-26T13:41:59.523330') === '2020-06-26 13:41',
  fmtCandleTime('2020-06-26T13:41:59.523330'));
check('fmtCandleTime with seconds for the export',
  fmtCandleTime('2020-06-26T13:41:59.523330', { seconds: true }) === '2020-06-26 13:41:59');
check('fmtCandleTime ignores the viewer timezone (UTC only)',
  fmtCandleTime('2020-12-31T23:30:00.000000') === '2020-12-31 23:30');
check('fmtCandleTime empty on missing input', fmtCandleTime(null) === '' && fmtCandleTime('') === '');
check('fmtCandleTime empty on garbage', fmtCandleTime('not-a-date') === '');
// Regression: the API returns naive UTC strings with no "Z". JS parses a
// datetime with no timezone designator as LOCAL time, so without appending "Z"
// every candle time shifted by the viewer's offset (this suite runs at
// Asia/Calcutta, so the shift would be -05:30).
check('naive UTC timestamp is read as UTC, not local time',
  fmtCandleTime('2020-06-26T13:41:59.523330') === '2020-06-26 13:41'
  && fmtCandleTime('2020-06-26T13:41:59.523330', { seconds: true }) === '2020-06-26 13:41:59',
  fmtCandleTime('2020-06-26T13:41:59.523330'));
check('timestamp already carrying Z is left alone',
  fmtCandleTime('2020-06-26T13:41:59.000Z') === '2020-06-26 13:41');
check('timestamp with an explicit offset is honoured',
  fmtCandleTime('2020-06-26T13:41:59+05:30') === '2020-06-26 08:11',
  fmtCandleTime('2020-06-26T13:41:59+05:30'));
check('naive timestamp near midnight does not roll to another date',
  fmtCandleTime('2020-06-26T00:30:00.000000') === '2020-06-26 00:30'
  && fmtCandleTime('2020-12-31T23:59:59.000000') === '2020-12-31 23:59',
  `${fmtCandleTime('2020-06-26T00:30:00.000000')} / ${fmtCandleTime('2020-12-31T23:59:59.000000')}`);

// ---------- 3. the table renders, showing candle + colour per trade ----------
const html = render(React.createElement(TradeLogTable, {
  trades, params, expandedTrade: 0, onToggleRow: () => {} }));

check('table renders both trades', html.includes('MOMENTUM') && html.includes('REVERSAL'));
check('header has a Signal Candle column', html.includes('Signal Candle'));
check('header has an Entry Candle column', html.includes('Entry Candle'));
check('legacy single "Candle" header is gone', !html.includes('>Candle</th>'));
check('entry candle colour chip shown', html.includes('▲ GREEN'));
check('signal/exit candle colour chips shown', html.includes('▼ RED'));
check('candle chip title names its role and colour',
  html.includes('title="entry candle colour: GREEN"')
  && html.includes('title="signal candle colour: RED"')
  && html.includes('title="exit candle colour: RED"'));
check('signal candle time rendered in UTC', html.includes('2020-06-26 12:41'));
check('entry candle time rendered in UTC', html.includes('2020-06-26 13:41'));
check('signal and entry candles are different candles',
  html.includes('2020-06-26 12:41') && html.includes('2020-06-26 13:41'));
// expanded row (trade 0) shows the full condition breakdown
check('expanded row labels the entry conditions', html.includes('Entry Conditions'));
check('expanded row shows all 7 condition lines',
  ['1. 4h trend', '2. ADX', '3. MACD hist', '4. ATR regime', '5. DI confirmation',
   '6. MACD zero-cross', '7. RSI agreement'].every(s => html.includes(s)));
check('expanded row labels the exit condition and its candle',
  html.includes('Exit Condition — SL on the RED candle'));
check('expanded row shows the exact exit rule that fired',
  html.includes('Stop loss hit — price rose to 10,260.25'));
check('expanded row shows the per-side ATR rule actually applied (short)',
  html.includes('ATR &lt; 1.2 × SMA50(ATR)'));
check('expanded row shows the stop plan',
  html.includes('SL: ') && html.includes('Trail stop: ') && html.includes('ATR@entry: '));
check('expanded row shows the peak price', html.includes('Peak: '));
check('non-applicable conditions render as N/A, never as FAIL',
  html.includes('N/A') && !html.includes('FAIL'));

// ---------- 4. the chips behave correctly on their own ----------------------
check('CandleChip GREEN', render(React.createElement(CandleChip, { color: 'GREEN', label: 'entry' }))
  .includes('▲ GREEN'));
check('CandleChip DOJI', render(React.createElement(CandleChip, { color: 'DOJI', label: 'entry' }))
  .includes('● DOJI'));
check('CandleChip renders nothing when the colour is unknown',
  render(React.createElement(CandleChip, { color: null, label: 'entry' })) === '');
check('condLabel: truthy -> PASS', condLabel(true) === 'PASS' && condLabel(1) === 'PASS');
check('condLabel: falsy -> FAIL', condLabel(false) === 'FAIL' && condLabel(0) === 'FAIL');
check('condLabel: null/undefined -> N/A',
  condLabel(null) === 'N/A' && condLabel(undefined) === 'N/A');
check('CondChip PASS styling', render(React.createElement(CondChip, { ok: 1, label: 'ADX' }))
  .includes('✓ ADX'));
check('CondChip FAIL styling', render(React.createElement(CondChip, { ok: 0, label: 'ADX' }))
  .includes('✗ ADX'));
check('CondChip N/A shows a middot', render(React.createElement(CondChip, { ok: null, label: 'DI' }))
  .includes('· DI'));

// ---------- 5. the exported spreadsheet ------------------------------------
const csv = buildTradesCSV(trades);
const rows = csv.split('\r\n');
const header = rows[0].split(',').map(s => s.replace(/^"|"$/g, ''));
const parseRow = (line) => line.split('","').map(s => s.replace(/^"|"$/g, ''));
const cells = parseRow(rows[1]);
const col = (name) => cells[header.indexOf(name)];

check('CSV uses CRLF line endings', csv.includes('\r\n'));
check('CSV has one row per trade plus the header', rows.length === 3);
// Pinned so the documented column count cannot drift from the code.
check('CSV has exactly 45 columns', header.length === 45, `got ${header.length}`);
check('CSV has no duplicate column names',
  new Set(header).size === header.length);
check('CSV has no empty column name', header.every(h => h.trim().length > 0));
check('CSV has the signal candle columns',
  header.includes('Signal Candle Time') && header.includes('Signal Candle Colour'));
check('CSV has the entry candle columns',
  header.includes('Entry Candle Time') && header.includes('Entry Candle Colour'));
check('CSV has the exit candle colour column', header.includes('Exit Candle Colour'));
check('CSV has one column per entry condition',
  ['Entry Cond 1 - 4H Trend', 'Entry Cond 2 - ADX', 'Entry Cond 3 - MACD Hist',
   'Entry Cond 4 - ATR Regime', 'Entry Cond 5 - RSI Trigger',
   'Entry Cond 6 - MACD Confirm', 'Entry Cond 7 - DI Confirm'].every(h => header.includes(h)));
check('CSV has the readable condition breakdown column',
  header.includes('All Entry Conditions (detail)'));
check('CSV has both exit condition columns',
  header.includes('Exit Condition') && header.includes('Exit Condition Detail'));

// The actual values — this is what the client reads in Excel.
check('CSV: signal candle colour', col('Signal Candle Colour') === 'RED', col('Signal Candle Colour'));
check('CSV: entry candle colour', col('Entry Candle Colour') === 'GREEN', col('Entry Candle Colour'));
check('CSV: exit candle colour', col('Exit Candle Colour') === 'RED', col('Exit Candle Colour'));
check('CSV: signal candle time is UTC to the second',
  col('Signal Candle Time') === '2020-06-26 12:41:59', col('Signal Candle Time'));
check('CSV: entry candle time is UTC to the second',
  col('Entry Candle Time') === '2020-06-26 13:41:59', col('Entry Candle Time'));
check('CSV: exit time is UTC to the second',
  col('Exit Time') === '2020-06-26 14:41:59', col('Exit Time'));
check('CSV: exit condition code', col('Exit Condition') === 'SL');
check('CSV: exit condition detail', col('Exit Condition Detail').includes('Stop loss hit'));
check('CSV: every applicable entry condition is PASS, the rest N/A',
  ['Entry Cond 1 - 4H Trend', 'Entry Cond 2 - ADX', 'Entry Cond 4 - ATR Regime',
   'Entry Cond 5 - RSI Trigger', 'Entry Cond 6 - MACD Confirm', 'Entry Cond 7 - DI Confirm']
    .every(h => col(h) === 'PASS') && col('Entry Cond 3 - MACD Hist') === 'N/A');
check('CSV: condition breakdown flattened into one cell',
  col('All Entry Conditions (detail)').includes('1. 4h trend')
  && col('All Entry Conditions (detail)').includes('7. RSI agreement')
  && !col('All Entry Conditions (detail)').includes('\n'));
check('CSV: direction and setup', col('Direction') === 'SHORT' && col('Setup') === 'MOMENTUM');
check('CSV: stop plan columns', col('SL at Entry') === '10240.23' && col('ATR at Entry') === '20.00');
check('CSV: quoting survives embedded commas', rows[1].includes('"Stop loss hit — price rose to 10,260.25'));
check('CSV: unicode survives', csv.includes('≥'));
const revCells = parseRow(rows[2]);
const revCol = (name) => revCells[header.indexOf(name)];
check('CSV row 2: reversal trade marks DI confirm as N/A',
  revCol('Entry Cond 7 - DI Confirm') === 'N/A');
check('CSV row 2: reversal trade marks MACD hist as PASS',
  revCol('Entry Cond 3 - MACD Hist') === 'PASS');
check('CSV row 2: exit condition is TP', revCol('Exit Condition') === 'TP');
check('CSV row 2: colours are per-trade, not shared',
  revCol('Entry Candle Colour') === 'RED' && revCol('Signal Candle Colour') === 'GREEN');

// ---------- 6. older runs without the new fields must not break -------------
const legacy = [{ direction: 1, entry_time: '2020-06-26T13:41:59.523330', candle_type: 'GREEN',
  exit_time: '2020-06-26T14:41:59.523330', exit_price: 100, entry_price: 99,
  exit_reason: 'SL', conditions: { trend_ok: 1, adx_ok: 0, macd_hist_ok: 1,
  atr_regime_ok: 1, rsi_ok: 1, macd_confirm_ok: 1 } }];
const legacyHtml = render(React.createElement(TradeLogTable, {
  trades: legacy, params, expandedTrade: 0, onToggleRow: () => {} }));
check('legacy row still renders', legacyHtml.length > 100);
check('legacy row falls back to the stored candle colour', legacyHtml.includes('GREEN'));
check('legacy row falls back to entry_time when signal time is absent',
  legacyHtml.includes('2020-06-26 13:41'));
const legacyCsv = buildTradesCSV(legacy);
check('legacy CSV still builds', legacyCsv.split('\r\n').length === 2);
check('legacy CSV leaves new columns blank rather than "undefined"',
  !legacyCsv.includes('undefined'));

// ---------- 7. empty / missing input ---------------------------------------
check('CSV of no trades is just the header', buildTradesCSV([]).split('\r\n').length === 1);
check('CSV tolerates a null trade list', typeof buildTradesCSV(null) === 'string');
check('table tolerates missing trades/params',
  typeof render(React.createElement(TradeLogTable, {
    trades: undefined, params: undefined, expandedTrade: null,
    onToggleRow: () => {} })) === 'string');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
