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
  FAST_TEST_ID, FAST_TEST_NAME, FAST_TEST_V1_ID, FAST_TEST_V1_NAME,
  isFastTestV1, builtinStrategyName, strategyDisplayName, parsePhantomVariant,
  isPhantomBuiltin, isPhantomPreset,
} from '../src/utils/phantomPresets.js';
import Backtest, { buildTradesCSV, TradeLogTable, validationChipClass, fastTestV1Summary }
  from '../src/pages/Backtest.jsx';
import PaperTrade from '../src/pages/PaperTrade.jsx';
import LiveTrade from '../src/pages/LiveTrade.jsx';
import ChartPage from '../src/pages/Chart.jsx';

// esbuild bundles this file to CJS (no import.meta.url), so the source path is
// resolved from the runner's working directory — frontend/ under npm test.
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
// SSR inserts <!-- --> between text and expressions; strip it before matching.
const flat = (html) => String(html).replace(/<!-- -->/g, '');

// ------------------------------------------------------------- id helpers --
check('FastTestV1 is its own id, separate from FastTest',
  FAST_TEST_V1_ID === 'FastTestV1' && FAST_TEST_V1_ID !== FAST_TEST_ID);
check('isFastTestV1 matches the V1 id only',
  isFastTestV1('FastTestV1') && !isFastTestV1('FastTest') && !isFastTestV1('PhantomV2'));
check('the V1 id is NOT a Kudos preset (it does not touch the Phantom parser)',
  parsePhantomVariant(FAST_TEST_V1_ID) === null
  && !isPhantomBuiltin(FAST_TEST_V1_ID) && !isPhantomPreset(FAST_TEST_V1_ID));
check('display name for the V1 strategy',
  builtinStrategyName(FAST_TEST_V1_ID) === FAST_TEST_V1_NAME
  && FAST_TEST_V1_NAME === 'Fast Test Strategy V1.0');
check('display name resolution falls back through builtins',
  strategyDisplayName(FAST_TEST_V1_ID) === FAST_TEST_V1_NAME
  && strategyDisplayName(FAST_TEST_ID) === FAST_TEST_NAME
  && strategyDisplayName('PhantomV2') === 'Kudos V2.5 (Default)');
check('a saved strategy with the same numeric id still wins',
  strategyDisplayName(7, [{ id: 7, name: 'My Saved' }]) === 'My Saved');

// -------------------------------------------------------------- dropdowns --
const paperHtml = flat(renderToString(React.createElement(PaperTrade)));
const liveHtml = flat(renderToString(React.createElement(LiveTrade)));
const chartHtml = flat(renderToString(React.createElement(ChartPage)));
const backtestHtml = flat(renderToString(React.createElement(Backtest)));

check('Paper Trade offers FastTest V1.0',
  paperHtml.includes('value="FastTestV1"') && paperHtml.includes('Fast Test Strategy V1.0'));
check('Live Trade offers FastTest V1.0',
  liveHtml.includes('value="FastTestV1"') && liveHtml.includes('Fast Test Strategy V1.0'));
check('Chart overlay offers FastTest V1.0',
  chartHtml.includes('value="FastTestV1"') && chartHtml.includes('FastTest V1.0 (debug + validation)'));
check('Backtest offers FastTest V1.0',
  backtestHtml.includes('value="FastTestV1"') && backtestHtml.includes('Fast Test Strategy V1.0'));
check('the original FastTest option is untouched everywhere',
  paperHtml.includes('value="FastTest"') && liveHtml.includes('value="FastTest"')
  && chartHtml.includes('value="FastTest"'));
check('the Admin panel can start a V1 debug session',
  readSource('src/pages/AdminPanel.jsx').includes("start('FastTestV1')"));

// -------------------------------------------- configurable debug values ----
// Both debug strategies are configurable from Backtest -> Strategy
// Configuration, exactly like Kudos: the panel shows the fields the backend
// reads for the selected strategy, and Save strategy remembers the family so
// Paper / Live run the same values.
const backtestSrc = readSource('src/pages/Backtest.jsx');
check('Backtest offers the debug strategy as well',
  backtestHtml.includes('value="FastTest"') && backtestHtml.includes('Fast Test Strategy (debug — configurable)'));
check('the panel shows the debug groups',
  backtestSrc.includes('data-testid="fast-test-config"')
  && backtestSrc.includes('data-testid="fast-test-v1-rules"')
  && backtestSrc.includes('data-testid="fast-test-config-note"'));
check('the debug panel exposes the risk & exit model editor with the same editor component',
  backtestSrc.includes('<RiskExitModelEditor params={params} setParams={setParams} />'));
check('the debug panel exposes the timing / sizing fields the backend reads',
  ['timeout_bars', 'cooldown_bars', 'leverage', 'margin_pct', 'lot_size_btc', 'reduced_margin_pct',
   'dd_soft_pct', 'dd_halt_pct', 'dd_resume_pct', 'sl_floor_pct']
    .every(f => backtestSrc.includes(`'${f}'`) || backtestSrc.includes(f)));
check('the V1.0 rules are editable (window, validation close, booking)',
  ['validation_bars', 'validation_close_pct', 'profit_book_pct'].every(f => backtestSrc.includes(f)));
check('the V1 rules show as numbers the client types (percent in the form, fraction in the payload)',
  backtestSrc.includes('meta.percent ? asPercent(value)')
  && backtestSrc.includes('toFraction(e.target.value)'));
check('the Kudos form keeps its own groups (the debug panel is a separate branch)',
  backtestSrc.includes('Object.entries(sharedParamGroups).map') && backtestSrc.includes('fastTestFamily ? ('));
check('the Kudos-only blocks are hidden for the debug family',
  backtestSrc.includes('{!fastTestFamily && ('));
check('the form detects the family of a saved strategy too',
  backtestSrc.includes("const stored = saved && saved.rules && typeof saved.rules === 'object' ? saved.rules.strategy_id : ''"));
check('the section title follows the selected strategy',
  backtestSrc.includes('title={fastTestFamily ?') && backtestSrc.includes('FAST_TEST_V1_NAME : FAST_TEST_NAME} — Configuration'));
check('Save strategy records which family the values are for',
  backtestSrc.includes('strategy_id: strategyFamily || undefined'));
check('the saved-strategy marker never leaks into the run parameters',
  backtestSrc.includes('delete merged.strategy_id;'));
check('the chart overlay follows the selected strategy',
  backtestSrc.includes('strategy_id: results.strategy_id || selectedStrategyId'));
check('the debug defaults are the backend defaults (nothing changes until edited)',
  /timeout_bars:\s*72/.test(backtestSrc) && /lot_size_btc:\s*0\.001/.test(backtestSrc)
  && /sl_floor_pct:\s*0\.016/.test(backtestSrc) && /reduced_margin_pct:\s*0\.125/.test(backtestSrc)
  && /validation_bars:\s*2/.test(backtestSrc) && /validation_close_pct:\s*0\.0035/.test(backtestSrc)
  && /profit_book_pct:\s*0\.009/.test(backtestSrc));
check('a saved debug strategy is labelled in every dropdown',
  backtestSrc.includes('isFastTestV1(s.strategy_id)')
  && readSource('src/pages/PaperTrade.jsx').includes('isFastTestV1(s.strategy_id)')
  && readSource('src/pages/LiveTrade.jsx').includes('isFastTestV1(s.strategy_id)'));
check('the Strategies list says which family a saved strategy is',
  readSource('src/pages/Strategies.jsx').includes('familyLabel(s.strategy_id)')
  && readSource('src/pages/Strategies.jsx').includes('Parameter-based${familyLabel(s.strategy_id)}'));

// ------------------------------- editable entry & exit rules (Task 10) ----
check('the panel offers the entry rule with its own numbers',
  backtestSrc.includes('data-testid="fast-test-entry-rule"')
  && ['entry_rsi_period', 'entry_rsi_long_max', 'entry_rsi_short_min']
      .every(f => backtestSrc.includes(`'${f}'`)));
check('the entry rule exposes the direction filter',
  backtestSrc.includes('data-testid="param-trade_direction"')
  && backtestSrc.includes('value="long"') && backtestSrc.includes('value="short"'));
check('the entry rule shows the rule it is running',
  backtestSrc.includes('Now: RSI({params.entry_rsi_period})'));
check('the panel offers every protective exit as a switch',
  backtestSrc.includes('data-testid="fast-test-exit-rule"')
  && ['use_stop_loss', 'use_take_profit', 'use_trailing_stop', 'use_breakeven', 'use_timeout']
      .every(f => backtestSrc.includes(`'${f}'`)));
check('every exit condition is toggleable',
  ['exit_on_opposite', 'exit_rsi_enabled', 'exit_macd_flip_enabled'].every(
    f => backtestSrc.includes(`'${f}'`))
  && backtestSrc.includes('data-testid="toggle-exit_rsi_enabled"')
  && backtestSrc.includes('data-testid={`toggle-${field}`}'));
check('the RSI exit level is only shown while that condition is on',
  backtestSrc.includes("{params.exit_rsi_enabled && (")
  && backtestSrc.includes("renderNumberInput('exit_rsi_level'"));
check('the rule defaults are the shipped ones',
  /entry_rsi_period:\s*14/.test(backtestSrc) && /entry_rsi_long_max:\s*50/.test(backtestSrc)
  && /entry_rsi_short_min:\s*50/.test(backtestSrc)
  && /use_stop_loss:\s*true/.test(backtestSrc) && /use_timeout:\s*true/.test(backtestSrc)
  && /exit_on_opposite:\s*false/.test(backtestSrc) && /exit_rsi_level:\s*50/.test(backtestSrc)
  && /exit_macd_flip_enabled:\s*false/.test(backtestSrc));
check('the panel no longer tells the client the entry rule is fixed',
  !backtestSrc.includes('The entry rule is fixed')
  && backtestSrc.includes('are yours to change'));
// The debug panels only render for a debug selection, so render the page with
// that selection. This is the actual HTML the client sees.
const debugHtml = flat(renderToString(React.createElement(Backtest, { initialStrategyId: 'FastTest' })));
const v1DebugHtml = flat(renderToString(React.createElement(Backtest, { initialStrategyId: 'FastTestV1' })));
check('the debug selection renders the entry rule with its fields',
  debugHtml.includes('fast-test-entry-rule') && debugHtml.includes('Entry rule')
  && ['entry_rsi_period', 'entry_rsi_long_max', 'entry_rsi_short_min']
      .every(f => debugHtml.includes(`param-${f}`))
  && debugHtml.includes('param-trade_direction'));
check('the debug selection renders the exit rule with every switch and condition',
  debugHtml.includes('fast-test-exit-rule')
  && ['use_stop_loss', 'use_take_profit', 'use_trailing_stop', 'use_breakeven', 'use_timeout',
      'exit_on_opposite', 'exit_rsi_enabled', 'exit_macd_flip_enabled']
      .every(f => debugHtml.includes(`toggle-${f}`)));
check('the panel states the rule it is running',
  /RSI\(14\)[^<]*long below 50, short at\/above 50/.test(debugHtml));
check('the debug panel is free of the Kudos-only blocks and copy',
  !debugHtml.includes('strategy-separation') && !debugHtml.includes('macd-line-rules')
  && !debugHtml.includes('Preview Filters') && !debugHtml.includes('The entry rule is fixed')
  && debugHtml.includes('runs the entry / exit rules above'));
check('the debug panel still offers the shared Risk & Exit editor',
  debugHtml.includes('risk-exit-model'));
check('the V1 selection also renders its validation / booking block',
  v1DebugHtml.includes('fast-test-v1-rules') && v1DebugHtml.includes('fast-test-entry-rule')
  && v1DebugHtml.includes('fast-test-exit-rule'));
check('the new exit conditions are labelled in the paper / sessions trade logs',
  readSource('src/pages/PaperTrade.jsx').includes("OPP: { label: 'Opposite Signal'")
  && readSource('src/pages/PaperTrade.jsx').includes("RSIX: { label: 'RSI Exit'")
  && readSource('src/pages/PaperTrade.jsx').includes("MFLIP: { label: 'MACD Flip'")
  && readSource('src/pages/Sessions.jsx').includes("OPP: { label: 'Opposite Signal'"));

// ------------------------------------------------------------------- CSV ----
const v1Trade = {
  direction: 1, setup: 'FASTTEST V1',
  signal_candle_time: '2026-01-01T00:00:00Z', entry_candle_time: '2026-01-01T01:00:00Z',
  entry_time: '2026-01-01T01:00:00Z', exit_time: '2026-01-01T03:00:00Z',
  entry_price: 100, exit_price: 100.2, lots: 1, margin: 1000, notional: 7000,
  gross_pnl: 200, fees: 4, net_pnl: 196, equity_after: 20196,
  exit_reason: 'VALFAIL', exit_detail: '2H validation failed',
  validation_status: 'FAILED', validation_close: 100.2, validation_threshold: 100.35,
  tp090_hit: 0, validation_exit: 1, final_exit_reason: 'VALFAIL', final_net_pnl: 196,
};
const legacyTrade = {
  direction: -1, entry_price: 100, exit_price: 99, lots: 1, margin: 1000, notional: 7000,
  gross_pnl: 100, fees: 4, net_pnl: 96, exit_reason: 'SL',
};
const csv = buildTradesCSV([v1Trade, legacyTrade]);
const header = csv.split('\r\n')[0].replace(/"/g, '').split(',');
const rowFor = (name) => {
  const rows = csv.split('\r\n').map(r => r.replace(/"/g, '').split(','));
  return header.reduce((acc, h, i) => { acc[h] = rows[1][i]; return acc; }, {});
};
const auditOf = (trade, idx) => {
  const rows = buildTradesCSV([trade]).split('\r\n').map(r => r.replace(/"/g, '').split(','));
  return header.reduce((acc, h, i) => { acc[h] = rows[idx][i]; return acc; }, {});
};
check('the seven V1 audit columns are the last columns of the sheet',
  header.slice(-7).join('|') === 'Validation Status|Validation Close|Validation Threshold|'
    + 'TP 0.90% Hit|Validation Exit|Final Exit Reason|Final Net P&L', header.slice(-7).join('|'));
check('the original layout keeps its positions (Bars Held still index 49)',
  header.indexOf('Bars Held') === 49 && header.indexOf('MACD Line') === 50);
const v1Csv = auditOf(v1Trade, 1);
check('CSV: validation status / close / threshold',
  v1Csv['Validation Status'] === 'FAILED' && v1Csv['Validation Close'] === '100.20'
  && v1Csv['Validation Threshold'] === '100.35');
check('CSV: TP 0.90% hit and validation exit flags read YES/NO',
  v1Csv['TP 0.90% Hit'] === 'NO' && v1Csv['Validation Exit'] === 'YES');
check('CSV: final exit reason and final net P&L',
  v1Csv['Final Exit Reason'] === 'VALFAIL' && v1Csv['Final Net P&L'] === '196.00');
const legacyCsv = auditOf(legacyTrade, 1);
check('CSV: a non-V1 trade leaves the validation columns blank, never "undefined"',
  legacyCsv['Validation Status'] === '' && legacyCsv['Validation Close'] === ''
  && legacyCsv['Validation Threshold'] === ''
  && legacyCsv['TP 0.90% Hit'] === '' && legacyCsv['Validation Exit'] === ''
  && !csv.includes('undefined'));
check('CSV: final reason / net P&L fall back to the ordinary exit fields',
  legacyCsv['Final Exit Reason'] === 'SL' && legacyCsv['Final Net P&L'] === '96.00');

// ------------------------------------------------------------ trade table ---
const html = flat(renderToString(React.createElement(TradeLogTable, {
  trades: [v1Trade], params: {}, expandedTrade: 0, onToggleRow: () => {},
})));
check('the trade row shows a V1 validation chip',
  html.includes('FAILED') && html.includes('val'));  // chip text present in the row
check('the expanded row shows the V1 audit block',
  html.includes('data-testid="v1-validation-audit"')
  && html.includes('Validation Status') && html.includes('Validation Threshold')
  && html.includes('Final Net P&amp;L') && html.includes('Final Exit Reason'));
check('the audit block shows the recorded numbers',
  html.includes('100.20') && html.includes('100.35') && html.includes('196.00'));
const bookedHtml = flat(renderToString(React.createElement(TradeLogTable, {
  trades: [{ ...v1Trade, exit_reason: 'TP090', final_exit_reason: 'TP090',
             validation_status: 'TP_090_HIT', tp090_hit: 1, validation_exit: 0 }],
  params: {}, expandedTrade: 0, onToggleRow: () => {},
})));
check('a booked trade is flagged as +0.90% HIT',
  bookedHtml.includes('+0.90% HIT') && bookedHtml.includes('TP 0.90% Hit'));
check('a legacy trade renders no V1 audit block',
  !flat(renderToString(React.createElement(TradeLogTable, {
    trades: [legacyTrade], params: {}, expandedTrade: 0, onToggleRow: () => {} })))
    .includes('v1-validation-audit'));

// ----------------------------------------------------------- run summary ----
check('chip colours are mapped per status',
  validationChipClass('VALIDATED').includes('green')
  && validationChipClass('FAILED').includes('red')
  && validationChipClass('TP_090_HIT').includes('emerald')
  && validationChipClass('NOT_REACHED').includes('gray'));
check('run summary counts every V1 outcome',
  JSON.stringify(fastTestV1Summary([
    { validation_status: 'VALIDATED', validation_exit: 0, tp090_hit: 0 },
    { validation_status: 'FAILED', validation_exit: 1, tp090_hit: 0 },
    { validation_status: 'TP_090_HIT', validation_exit: 0, tp090_hit: 1 },
    { validation_status: 'NOT_REACHED', validation_exit: 0, tp090_hit: 0 },
  ])) === JSON.stringify({ trades: 4, validated: 1, failed: 1, booked: 1, notReached: 1 }));
check('no V1 trades -> no summary strip', fastTestV1Summary([legacyTrade]) === null
  && fastTestV1Summary([]) === null && fastTestV1Summary(null) === null);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
