import React from 'react';
import { renderToString } from 'react-dom/server';
import { readFileSync } from 'node:fs';

// Browser stubs (effects never run under the server renderer, but a few
// module-level helpers read these).
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};
globalThis.window = { location: { protocol: 'http:', hostname: 'localhost', search: '' }, addEventListener() {}, removeEventListener() {} };
globalThis.fetch = () => Promise.resolve({ ok: false, json: async () => ([]) });

import {
  TradeConditionDetail, EntryConditionBlock, ExitConditionBlock, FastTestV1AuditBlock,
  tradeLogRow, tradesToCSV,
} from '../src/components/TradeConditionDetail.jsx';
import { buildTradesCSV } from '../src/pages/Backtest.jsx';

const readSource = (rel) => {
  for (const base of ['', 'frontend/']) {
    try { return readFileSync(`${base}${rel}`, 'utf8'); } catch { /* next */ }
  }
  return '';
};

let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { pass++; } else { fail++; console.log(`  FAIL: ${name} ${extra}`); }
};
const flat = (html) => String(html).replace(/<!-- -->/g, '');

// A closed paper/live trade exactly as the workers now record it: the
// entry-condition snapshot is spread onto the record, the readable breakdown
// is `entry_conditions_detail`, and the V1 audit fields ride alongside.
const paperTrade = {
  symbol: 'BTCUSDT', direction: 1,
  entry: 100.0, exit: 100.9,
  entry_trade_price: 100.0, exit_trade_price: 100.9,
  entry_mark_price: 100.0, exit_mark_price: 100.9,
  mark_price_basis: true,
  pnl: 120.5, gross_pnl: 130.0, fees: 9.5,
  reason: 'TP090', exit_detail: '+0.90% profit booking — price rose to 100.90 ≥ 100.90 (entry 100.00 × 1.0090). Full position booked.',
  sl: 98.0, sl_final: 100.0, tp: 140.0, trail_stop: 99.0,
  atr_at_entry: 1.2, peak_price: 100.95, lots: 0.01,
  margin_inr: 5000, notional_usd: 1000,
  entry_time: '2026-08-28T10:15:00+05:30', exit_time: '2026-08-28T12:15:00+05:30',
  bars_held: 2,
  setup: 'REVERSAL', trend_4h: 'UP',
  rsi14: 41.5, macd_hist: -3.2, adx: 21.0, atr14: 1.2, ema50_1h: 99.0, ema50_4h: 97.5,
  signal_candle_time: '2026-08-28T04:45:00+00:00', signal_candle_type: 'GREEN',
  entry_candle_time: '2026-08-28T04:45:00+00:00', entry_candle_type: 'GREEN',
  exit_candle_type: 'RED',
  cond_trend_ok: 1, cond_adx_ok: 1, cond_macd_hist_ok: 1, cond_atr_regime_ok: 1,
  cond_rsi_ok: 1, cond_macd_confirm_ok: 1, cond_di_ok: null, cond_macd_line_ok: null,
  entry_conditions_detail: [
    'Side: LONG | Setup: REVERSAL',
    '1. 4h trend: close 100.00 vs EMA50(4h) 97.50 -> UP; LONG needs UP -> PASS',
    '2. ADX: 21.0 >= min 10.0 -> PASS',
    '3. MACD hist: -3.20 >= threshold 5.00 -> FAIL',
    '4. ATR regime: ATR 1.20 >= 0.50 x SMA50(ATR) 1.00 = 0.50 -> PASS',
  ].join('\n'),
  validation_status: 'TP_090_HIT', validation_close: 100.5, validation_threshold: 100.35,
  tp090_hit: 1, validation_exit: 0, final_exit_reason: 'TP090', final_net_pnl: 120.5,
};

// -------------------------------------------------- record -> log-row map ----
const row = tradeLogRow(paperTrade);
check('paper/live record maps onto the backtest trade-log row shape',
  row.direction === 1 && row.entry_price === 100.0 && row.exit_price === 100.9
  && row.margin === 5000 && row.notional === 1000 && row.net_pnl === 120.5
  && row.hold_bars === 2 && row.exit_reason === 'TP090' && row.exit_candle_type === 'RED');
check('condition flags are grouped like the backtest row',
  row.conditions.trend_ok === 1 && row.conditions.macd_hist_ok === 1
  && row.conditions.di_ok === null && row.conditions.macd_line_ok === null);
check('the entry-condition detail and V1 audit fields survive the map',
  row.entry_conditions_detail === paperTrade.entry_conditions_detail
  && row.validation_status === 'TP_090_HIT' && row.tp090_hit === 1
  && row.final_net_pnl === 120.5);

// ------------------------------------------------------------- CSV export ----
const csv = tradesToCSV([paperTrade]);
const header = (csv.split('\r\n')[0] || csv.split('\n')[0]).replace(/^\uFEFF/, '');
check('the export is the Backtest spreadsheet — same header as buildTradesCSV',
  header === buildTradesCSV([{}]).split('\r\n')[0]);
check('the export carries the entry-condition columns and the V1 audit block',
  header.includes('"All Entry Conditions (detail)"') && header.includes('"Entry Cond 1 - 4H Trend"')
  && header.includes('"Exit Condition Detail"') && header.includes('"Validation Status"')
  && header.includes('"Final Net P&L"'));
check('the exported row contains the readable conditions and the exit rule',
  csv.includes('4h trend: close 100.00') && csv.includes('Setup: REVERSAL')
  && csv.includes('+0.90% profit booking'));
check('the CSV starts with the UTF-8 BOM so ₹ / ≥ survive Excel',
  csv.charCodeAt(0) === 0xFEFF || csv.startsWith('\uFEFF'));

// ------------------------------------------------------------ SSR render ----
const html = flat(renderToString(<TradeConditionDetail trade={paperTrade} />));
check('the analysis panel renders the entry conditions block', html.includes('Entry Conditions')
  && html.includes('4h trend: close 100.00 vs EMA50(4h) 97.50'));
check('PASS lines and FAIL lines are colour-coded', html.includes('text-green-400')
  && html.includes('text-red-400'));
check('the panel names both candles and the exit candle', html.includes('GREEN')
  && html.includes('RED') && html.includes('Entry candle:') && html.includes('Exit candle:'));
check('the exit-condition block shows the exact rule that closed the trade',
  html.includes('Exit Condition — TP090') && html.includes('+0.90% profit booking'));
check('the V1 audit block renders the seven fields', html.includes('Validation Status')
  && html.includes('TP 0.90% Hit') && html.includes('Final Net P&amp;L'));
check('blocks are individually addressable for tests',
  flat(renderToString(<EntryConditionBlock trade={paperTrade} />)).includes('trade-entry-conditions')
  && flat(renderToString(<ExitConditionBlock trade={paperTrade} />)).includes('trade-exit-conditions')
  && flat(renderToString(<FastTestV1AuditBlock trade={paperTrade} />)).includes('trade-v1-audit'));

// Legacy records (saved before this feature) must still render, and must say
// so instead of inventing conditions.
const legacy = { direction: -1, entry: 50, exit: 49, pnl: 10, reason: 'SL',
  entry_time: '2025-01-01T00:00:00+00:00', exit_time: '2025-01-01T02:00:00+00:00', bars_held: 2 };
const legacyHtml = flat(renderToString(<TradeConditionDetail trade={legacy} />));
check('a legacy record renders an honest empty state', legacyHtml.includes('trade-conditions-empty')
  && legacyHtml.includes('No condition detail was recorded'));
check('a metadata-less strategy still shows its exit rule',
  flat(renderToString(<TradeConditionDetail trade={{ ...legacy, exit_detail: 'Stop loss hit — price fell to 49.00' }} />))
    .includes('Stop loss hit'));
check('FastTest V1 only renders the audit block when the fields exist',
  !flat(renderToString(<TradeConditionDetail trade={legacy} />)).includes('trade-v1-audit'));

// -------------------------------------------------- page wiring (source) ----
const paperSrc = readSource('src/pages/PaperTrade.jsx');
const liveSrc = readSource('src/pages/LiveTrade.jsx');
check('the Paper page renders the shared condition detail per closed trade',
  paperSrc.includes('TradeConditionDetail') && paperSrc.includes('downloadTradesCSV')
  && paperSrc.includes('Trade Reply / Closed Trades'));
check('the saved-session export is the same Backtest-style spreadsheet',
  paperSrc.includes('kudos_paper_session_'));
check('the Live page shows closed trades with the same analysis + export',
  liveSrc.includes('LiveClosedTradesPanel') && liveSrc.includes('Closed trades (live)')
  && liveSrc.includes('downloadTradesCSV'));
check('the Live export uses the closed trades from /live-trade/status',
  liveSrc.includes('inst.closed_trades'));
check('the export filename does not claim one strategy when the panel lists all instances',
  liveSrc.includes('kudos_${exportName}_trades.csv') && !liveSrc.includes('live_${selectedStrategy}'));
check('the Sessions detail also shows the entry conditions',
  readSource('src/pages/Sessions.jsx').includes('EntryConditionBlock'));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
