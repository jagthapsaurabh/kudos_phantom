import React from 'react';
import { renderToString } from 'react-dom/server';

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
  PHANTOM_PRESETS, PHANTOM_DEFAULT_ID, parsePhantomVariant, isPhantomBuiltin, isPhantomPreset,
  builtinStrategyName, strategyDisplayName, phantomPresetId, phantomPresetName, macdLineRuleText,
  macdLineRulesActive, DEFAULT_MACD_LINE_RULES, setupModeLabel, tradeDirectionLabel,
} from '../src/utils/phantomPresets.js';
import PhantomPresetOptions from '../src/components/PhantomPresetOptions.jsx';
import StrategyConfigSummary from '../src/components/StrategyConfigSummary.jsx';
import Backtest from '../src/pages/Backtest.jsx';
import PaperTrade from '../src/pages/PaperTrade.jsx';
import LiveTrade from '../src/pages/LiveTrade.jsx';
import ChartPage from '../src/pages/Chart.jsx';
import TradingPage from '../src/pages/TradingPage.jsx';
import PhantomStrategy from '../src/pages/PhantomStrategy.jsx';
import StrategyExplainedTab from '../src/pages/StrategyExplainedTab.jsx';

let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { pass++; } else { fail++; console.log(`  FAIL: ${name} ${extra}`); }
};

// ------------------------------------------------------------- id helpers --
check('8 curated presets, none is the plain default',
  PHANTOM_PRESETS.length === 8 && PHANTOM_PRESETS.every(p => p.id !== PHANTOM_DEFAULT_ID));
check('preset ids are unique', new Set(PHANTOM_PRESETS.map(p => p.id)).size === 8);
check('preset ids mirror the backend scheme',
  PHANTOM_PRESETS.some(p => p.id === 'PhantomV2:reversal')
  && PHANTOM_PRESETS.some(p => p.id === 'PhantomV2:long')
  && PHANTOM_PRESETS.some(p => p.id === 'PhantomV2:momentum:short'));
check('preset names match the backend names',
  phantomPresetName('reversal', 'both') === 'Kudos — Reversal only'
  && phantomPresetName('both', 'long') === 'Kudos — Long only'
  && phantomPresetName('momentum', 'short') === 'Kudos — Momentum · Short only'
  && phantomPresetName('both', 'both') === 'Kudos V2.5 (Default)');
check('phantomPresetId round-trips', phantomPresetId('reversal', 'long') === 'PhantomV2:reversal:long'
  && phantomPresetId('both', 'both') === 'PhantomV2');
check('parsePhantomVariant: default', JSON.stringify(parsePhantomVariant('PhantomV2')) === JSON.stringify({ setup_mode: 'both', trade_direction: 'both' }));
check('parsePhantomVariant: setup+direction', JSON.stringify(parsePhantomVariant('PhantomV2:momentum:long')) === JSON.stringify({ setup_mode: 'momentum', trade_direction: 'long' }));
check('parsePhantomVariant: alt separators', ['-', '.', '/'].every(sep =>
  JSON.stringify(parsePhantomVariant(`PhantomV2${sep}short`)) === JSON.stringify({ setup_mode: 'both', trade_direction: 'short' })));
check('parsePhantomVariant: rejects unknown ids',
  parsePhantomVariant('FastTest') === null && parsePhantomVariant('12') === null
  && parsePhantomVariant('PhantomV2:foo') === null && parsePhantomVariant('PhantomV2:long:short') === null
  && parsePhantomVariant(null) === null);
check('isPhantomBuiltin / isPhantomPreset', isPhantomBuiltin('PhantomV2') && isPhantomBuiltin('PhantomV2:long')
  && !isPhantomBuiltin('7') && !isPhantomPreset('PhantomV2') && isPhantomPreset('PhantomV2:reversal'));
check('builtinStrategyName', builtinStrategyName('PhantomV2') === 'Kudos V2.5 (Default)'
  && builtinStrategyName('PhantomV2:reversal:short') === 'Kudos — Reversal · Short only'
  && builtinStrategyName('FastTest') === 'Fast Test Strategy' && builtinStrategyName('9') === null);
check('strategyDisplayName prefers the saved name, then built-in, then id',
  strategyDisplayName('3', [{ id: 3, name: 'My strat' }]) === 'My strat'
  && strategyDisplayName('PhantomV2:long', []) === 'Kudos — Long only'
  && strategyDisplayName('42', []) === '42');
check('labels', setupModeLabel('reversal') === 'Reversal only' && tradeDirectionLabel('short') === 'Short only'
  && setupModeLabel('both') === 'Reversal + Momentum');

// ------------------------------------------------------- MACD rule text ----
const off = { macd_line_rules: { ...DEFAULT_MACD_LINE_RULES } };
check('MACD line rules default to off', macdLineRuleText(off, 1) === 'off' && !macdLinesActiveSafe(off));
function macdLinesActiveSafe(p) { return macdLineRulesActive(p); }
const shared = { macd_line_rules: { ...DEFAULT_MACD_LINE_RULES, enabled: true, line_vs_signal: 'above_below', line_vs_zero: 'cross', line_min: 10 } };
check('shared rules long text', macdLineRuleText(shared, 1) === 'MACD line > signal; MACD line crosses above 0; MACD line ≥ 10', macdLineRuleText(shared, 1));
check('shared rules short text (mirrored, signed level)', macdLineRuleText(shared, -1) === 'MACD line < signal; MACD line crosses below 0; MACD line ≤ -10', macdLineRuleText(shared, -1));
check('enabled with no rule is still off', macdLineRuleText({ macd_line_rules: { ...DEFAULT_MACD_LINE_RULES, enabled: true } }, 1) === 'off');
const perSide = {
  macd_line_rules: { ...DEFAULT_MACD_LINE_RULES, enabled: true, line_vs_signal: 'above_below' },
  entry_conditions: { use_direction_macd_line: true, long: { macd_line_vs_signal: 'off' }, short: { macd_signal_vs_zero: 'above_below', macd_line_min: -25 } },
};
check('per-side: long override to off', macdLineRuleText(perSide, 1) === 'off', macdLineRuleText(perSide, 1));
check('per-side: short inherits shared + own', macdLineRuleText(perSide, -1) === 'MACD line < signal; signal line < 0; MACD line ≤ -25', macdLineRuleText(perSide, -1));
check('per-side settings inert while block disabled',
  macdLineRuleText({ ...perSide, macd_line_rules: { ...perSide.macd_line_rules, enabled: false } }, -1) === 'off');

// ------------------------------------------------------------ components --
const optHtml = renderToString(<select><PhantomPresetOptions /></select>);
check('PhantomPresetOptions renders an optgroup with every preset',
  optHtml.includes('<optgroup') && PHANTOM_PRESETS.every(p => optHtml.includes(`value="${p.id}"`)) && optHtml.includes('Reversal only'));

const savedStrategies = [{
  id: 5, name: 'Momentum shorts', rules: {
    rsi_oversold: 40, macd_fast: 8, macd_slow: 21, macd_signal: 5, macd_hist_min: 3, enable_momentum_entry: true,
    setup_mode: 'momentum', trade_direction: 'short',
    macd_line_rules: { enabled: true, line_vs_zero: 'above_below' },
  },
}];
// Strip React's SSR text-boundary markers so adjacent text nodes can be matched as one string.
const flat = (h) => h.replace(/<!-- -->/g, '');
const summaryHtml = flat(renderToString(<StrategyConfigSummary strategyId="5" strategies={savedStrategies} />));
check('StrategyConfigSummary renders for a saved Kudos strategy', summaryHtml.includes('strategy-config-summary'));
check('summary shows the MACD periods', summaryHtml.includes('8/21/5'));
check('summary shows setup and direction', summaryHtml.includes('Momentum only') && summaryHtml.includes('Short only'));
check('summary shows the MACD line rules per side', summaryHtml.includes('MACD line &gt; 0') && summaryHtml.includes('MACD line &lt; 0'));
check('summary shows the hist threshold per side', summaryHtml.includes('Long ≥ 3') && summaryHtml.includes('Short ≤ -3'));
const plainSummary = renderToString(<StrategyConfigSummary strategyId="5" strategies={[{ id: 5, name: 'x', rules: { rsi_oversold: 40, macd_hist_min: 5 } }]} />);
check('summary says line rules are off for an untouched strategy', plainSummary.includes('Off — only the histogram threshold'));
check('summary renders nothing for a Chartink rule strategy',
  renderToString(<StrategyConfigSummary strategyId="6" strategies={[{ id: 6, name: 'rules', rules: [{ a: 1 }] }]} />) === '');
check('summary renders nothing for a built-in id before /phantom/config answers',
  renderToString(<StrategyConfigSummary strategyId="PhantomV2:long" strategies={[]} />) === '');

// ------------------------------------------------------------------ pages --
const html = (Comp) => { try { return renderToString(React.createElement(Comp)); } catch (e) { return `RENDER ERROR ${e.message}`; } };
const bt = html(Backtest);
check('Backtest renders', !bt.startsWith('RENDER ERROR') && bt.length > 200, bt.slice(0, 120));
check('Backtest dropdown lists the presets next to the default',
  bt.includes('Kudos V2.5 (Default)') && bt.includes('value="PhantomV2:reversal"') && bt.includes('Kudos — Long only'));
check('Backtest form has the Setup / Direction selectors', bt.includes('strategy-separation')
  && bt.includes('Reversal + Momentum') && bt.includes('Long + Short'));
check('Backtest form has the MACD line / signal block, collapsed by default',
  bt.includes('macd-line-rules') && bt.includes('Enable MACD line rules') && !bt.includes('MACD line vs signal line</label>'));
check('Backtest still exposes the MACD periods', bt.includes('MACD fast (EMA)') && bt.includes('MACD slow (EMA)') && bt.includes('MACD signal'));
for (const [name, Comp] of [['PaperTrade', PaperTrade], ['LiveTrade', LiveTrade], ['Chart', ChartPage], ['TradingPage', TradingPage]]) {
  const h = html(Comp);
  check(`${name} renders`, !h.startsWith('RENDER ERROR') && h.length > 200, h.slice(0, 120));
  check(`${name} dropdown offers the presets`, h.includes('value="PhantomV2:momentum"') && h.includes('Kudos — Momentum only'));
  check(`${name} keeps the default option first`, h.indexOf('value="PhantomV2"') < h.indexOf('value="PhantomV2:reversal"'));
}
const ps = html(PhantomStrategy);
check('PhantomStrategy renders', !ps.startsWith('RENDER ERROR'), ps.slice(0, 120));
check('Strategy Rules documents MACD settings and separation',
  ps.includes('MACD Settings') && ps.includes('MACD line / signal line rules') && ps.includes('Strategy Separation')
  && ps.includes('PhantomV2:reversal:long'));
const ex = renderToString(<StrategyExplainedTab champion={{ config: { macd_fast: 12, macd_slow: 26, macd_signal: 9, macd_hist_min: 5 } }} />);
check('Strategy Explained documents the MACD line rules and the splits',
  ex.includes('macd-line-rules-doc') && ex.includes('setup_mode') && ex.includes('trade_direction') && ex.includes('line_vs_signal'));
check('Strategy Explained no longer claims the periods are hidden', !ex.includes('not shown as inputs'));

console.log(`\nphantom_presets_ui: ${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
