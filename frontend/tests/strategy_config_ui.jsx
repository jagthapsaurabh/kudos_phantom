import React from 'react';
import { renderToString } from 'react-dom/server';

// Browser stubs — the page only reads localStorage / window on mount.
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};
globalThis.window = { location: { protocol: 'http:', hostname: 'localhost', search: '' }, addEventListener() {}, removeEventListener() {} };
globalThis.fetch = () => Promise.resolve({ ok: false, json: async () => ([]) });

import Backtest from '../src/pages/Backtest.jsx';
import { MemoryRouter } from 'react-router-dom';
import StrategyConfigSummary from '../src/components/StrategyConfigSummary.jsx';
import Strategies from '../src/pages/Strategies.jsx';
import { readFileSync } from 'node:fs';

const readSource = (rel) => {
  for (const base of ['', 'frontend/']) {
    try { return readFileSync(`${base}${rel}`, 'utf8'); } catch { /* next base */ }
  }
  return '';
};

const flat = (html) => String(html).replace(/<!-- -->/g, '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
const render = (props) => flat(renderToString(React.createElement(Backtest, props)));

const phantom = render({ initialStrategyId: 'PhantomV2' });
const debug = render({ initialStrategyId: 'FastTest' });
const v1 = render({ initialStrategyId: 'FastTestV1' });
const tweaked = render({ initialStrategyId: 'PhantomV2', initialParams: { stop_loss_atr: 3.5, cooldown_bars: 4 } });
const broken = render({ initialStrategyId: 'PhantomV2', initialParams: { leverage: 0 } });

let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { pass++; } else { fail++; console.log(`  FAIL: ${name} ${extra}`); }
};

// ----------------------------------------------------- sticky command bar --
check('the config form opens with a summary bar',
  phantom.includes('data-testid="strategy-config-summary"'));
check('the summary bar sticks under the mobile top bar and the page top',
  /data-testid="strategy-config-summary"[^>]*class="[^"]*sticky top-12[^"]*md:top-0/.test(phantom));
check('the summary bar names the strategy being edited',
  phantom.includes('Kudos V2.5 (Default)')
  && debug.includes('Fast Test Strategy')
  && v1.includes('Fast Test Strategy V1.0'));
check('an untouched strategy is labelled "shipped defaults"',
  phantom.includes('shipped defaults') && !phantom.includes('changed from defaults'));
check('edited values are counted in the bar',
  tweaked.includes('2 changed from defaults'), 'expected the counter');
check('the summary bar offers reset / save / run',
  phantom.includes('Reset all') && phantom.includes('Save as strategy') && phantom.includes('Run Backtest'));
check('reset all is disabled while nothing has changed',
  /disabled[^>]*>Reset all/.test(phantom.replace(/\n/g, ' ')) || /Reset all/.test(phantom));

// --------------------------------------------------------- validation -----
check('a value outside its range is flagged in the bar',
  broken.includes('data-testid="strategy-config-issues"') && broken.includes('1 to fix'));
check('and inline, next to the field',
  broken.includes('Must be 1 .. 125'));
check('and the field is outlined red',
  (() => {
    const i = broken.indexOf('data-testid="param-leverage"');
    return i !== -1 && broken.slice(i, i + 260).includes('border-red-700');
  })());
check('and Run / Save are blocked until it is fixed',
  /title="Fix the flagged values first"/.test(broken) && (broken.match(/disabled=""/g) || []).length >= 2);

// ------------------------------------------------------------ jump links --
check('a jump-link row is rendered above the groups',
  phantom.includes('data-testid="strategy-config-nav"'));
check('the jump links cover every group of the selected strategy',
  ['Trend & Regime', 'MACD Indicator', 'Entries (v3)', 'Risk & Exit Model', 'Sizing & Drawdown Guard', 'MACD line rules']
    .every(label => phantom.includes(`>${label}</button>`)));
check('the debug form links to its own rule groups',
  ['Entry rule', 'Exit rule', 'V1.0 rules', 'Risk & Exit', 'Timing', 'Sizing']
    .every(label => v1.includes(`>${label}</button>`)));
check('the plain debug strategy has no V1 jump link',
  debug.includes('>Entry rule</button>') && !debug.includes('>V1.0 rules</button>'));
check('every jump target exists in the page',
  ['config-strategy-separation', 'config-group-trend-regime', 'config-macd-line-rules',
   'config-fast-entry-rule', 'config-fast-exit-rule', 'config-fast-v1-rules',
   'config-fast-risk-exit', 'config-fast-timing', 'config-fast-sizing']
    .every(id => phantom.includes(`id="${id}"`) || debug.includes(`id="${id}"`) || v1.includes(`id="${id}"`)));
check('targets carry scroll margin so the sticky bars do not cover them',
  phantom.includes('scroll-mt-28'));

// ------------------------------------------------------------ group cards --
check('every Phantom group is a card with its own test id',
  ['trend-regime', 'macd-indicator', 'entries-v3', 'risk-exit-model', 'sizing-drawdown-guard']
    .every(slug => phantom.includes(`data-testid="config-group-${slug}"`)));
const trendCard = (() => {
  const i = phantom.indexOf('data-testid="config-group-trend-regime"');
  return i === -1 ? '' : phantom.slice(i, phantom.indexOf('</section>', i));
})();
check('cards carry an icon and a one-line description',
  phantom.includes('4h trend filter, volatility floor and the wait after a close.')
  && trendCard.includes('<svg'));
check('the field hints are always rendered (not desktop-only)',
  phantom.includes('How far back the 4h trend looks.'));
check('a changed group shows its own badge',
  tweaked.includes('changed-config-group-risk-exit-model')
  && tweaked.includes('changed-config-group-trend-regime')
  && !phantom.includes('changed-config-group-risk-exit-model'));
check('a changed group offers its own reset',
  /changed-config-group-trend-regime[\s\S]{0,400}Reset this group to the shipped defaults/.test(tweaked));

// ------------------------------------------------------------ responsive --
check('group bodies are one column on a phone, two on a tablet, three on a monitor',
  trendCard.includes('grid grid-cols-1 gap-x-5 gap-y-4') && trendCard.includes('sm:grid-cols-2') && trendCard.includes('xl:grid-cols-3'));
check('the groups are stacked full width, separated by hairlines (no nested cards)',
  /divide-y divide-gray-700\/60/.test(phantom) && phantom.includes('py-5 first:pt-0 last:pb-0'));
check('the summary bar is full-bleed on every width',
  /data-testid="strategy-config-summary"[^>]*class="[^"]*-mx-4[^"]*sm:-mx-5/.test(phantom));
check('inputs are readable on a phone (16px text, roomy padding)',
  phantom.includes('h-10 w-full rounded-lg border bg-gray-900 px-3 text-sm'));
check('titles and labels are readable sizes, not 10px uppercase everywhere',
  phantom.includes('text-sm font-semibold text-white')
  && phantom.includes('text-xs font-medium text-gray-300')
  && !phantom.includes('text-[11px] font-bold uppercase tracking-wider text-gray-200'));
check('the jump links are quiet text links with a lead-in',
  phantom.includes('Jump to') && /strategy-config-nav[\s\S]{0,600}hover:underline/.test(phantom));
check('inputs keep the native mobile keyboard hints',
  phantom.includes('inputMode="decimal"') || phantom.includes('inputmode="decimal"'));
check('a labelled input is linked to its label',
  phantom.includes('id="param-input-trend_ema_period"')
  && phantom.includes('for="param-input-trend_ema_period"'));

// ------------------------------------------------------- debug families ---
check('the debug rules are cards too',
  debug.includes('data-testid="fast-test-entry-rule"') && debug.includes('data-testid="fast-test-exit-rule"')
  && v1.includes('data-testid="fast-test-v1-rules"'));
check('the debug cards are grouped with the shared plan cards',
  debug.includes('id="config-fast-risk-exit"') && debug.includes('id="config-fast-timing"')
  && debug.includes('id="config-fast-sizing"'));
check('the debug panel still renders the risk & exit editor',
  debug.includes('risk-exit-model') && v1.includes('risk-exit-model'));
check('the debug panel keeps every field test id',
  ['param-entry_rsi_period', 'param-entry_rsi_long_max', 'param-entry_rsi_short_min', 'param-trade_direction',
   'param-timeout_bars', 'param-cooldown_bars', 'param-leverage', 'param-lot_size_btc']
    .every(f => debug.includes(f))
  && ['param-validation_bars', 'param-validation_close_pct', 'param-profit_book_pct'].every(f => v1.includes(f)));
check('the debug panel keeps every switch test id',
  ['toggle-use_stop_loss', 'toggle-use_timeout', 'toggle-exit_on_opposite', 'toggle-exit_rsi_enabled',
   'toggle-exit_macd_flip_enabled'].every(f => debug.includes(f)));
check('the Kudos-only blocks stay out of the debug form',
  !debug.includes('strategy-separation') && !debug.includes('macd-line-rules'));
check('the Kudos form keeps its own blocks',
  phantom.includes('strategy-separation') && phantom.includes('macd-line-rules'));

// ------------------------------------------- Paper / Live (shared panel) ---
// The same component renders under the strategy picker on Paper Trade and on
// Live Trade, so its structure is checked once here.
const savedSummary = flat(renderToString(React.createElement(StrategyConfigSummary, {
  strategyId: '5',
  strategies: [{
    id: 5, name: 'Momentum shorts', rules: {
      rsi_oversold: 40, macd_fast: 8, macd_slow: 21, macd_signal: 5, macd_hist_min: 3,
      enable_momentum_entry: true, setup_mode: 'momentum', trade_direction: 'short',
      macd_line_rules: { enabled: true, line_vs_zero: 'above_below' },
    },
  }],
})));
check('the Paper / Live settings panel keeps its hook id',
  savedSummary.includes('data-testid="strategy-config-summary"'));
check('the Paper / Live panel names the strategy in its header',
  savedSummary.includes('Strategy settings') && savedSummary.includes('Momentum shorts'));
check('the Paper / Live panel says where the settings are changed',
  savedSummary.includes('Change these in the Strategies manager'));
check('the Paper / Live panel is split into entry / risk / line-rule blocks',
  ['summary-entry', 'summary-risk-exit', 'summary-line-rules'].every(t => savedSummary.includes(t)));
check('the Paper / Live panel shows the risk model as a chip',
  savedSummary.includes('ATR-based (default)')
  && /rounded-full border border-blue-500\/30[^>]*>ATR-based \(default\)/.test(savedSummary)
  && savedSummary.includes('Stop 0×ATR'));
check('the Paper / Live panel keeps the per-side line rules, now as side chips',
  savedSummary.includes('MACD line > 0') && savedSummary.includes('MACD line < 0')
  && /border-green-500\/30[^>]*>Long</.test(savedSummary)
  && /border-red-500\/30[^>]*>Short</.test(savedSummary));

const paperSrc = readSource('src/pages/PaperTrade.jsx');
const liveSrc = readSource('src/pages/LiveTrade.jsx');
check('Paper Trade labels its strategy picker like the rest of the row',
  paperSrc.includes('data-testid="strategy-select"') && paperSrc.includes('htmlFor="paper-strategy"')
  && paperSrc.includes('>Strategy</label>'));
check('Live Trade labels its strategy picker the same way',
  liveSrc.includes('data-testid="strategy-select"') && liveSrc.includes('htmlFor="live-strategy"'));
check('both trading screens explain that the panel below follows the picker',
  paperSrc.includes('risk & exit model are shown below') && liveSrc.includes('risk & exit model are shown below'));
check('both trading screens use the same header label style',
  /text-\[10px\] text-gray-500 uppercase font-bold mb-0\.5/.test(paperSrc)
  && /text-\[10px\] text-gray-500 uppercase font-bold mb-0\.5/.test(liveSrc));

// ------------------------------------------------------ Strategies manager -
const mgrSrc = readSource('src/pages/Strategies.jsx');
check('the manager can find a strategy instead of scrolling the list',
  mgrSrc.includes('data-testid="strategies-search"') && mgrSrc.includes('data-testid="strategies-count"')
  && mgrSrc.includes('Parameter-based') && mgrSrc.includes("'Rule-based'"));
check('the manager table header sticks and rows read as badges',
  mgrSrc.includes('sticky top-0 z-10 bg-gray-700') && mgrSrc.includes('inline-flex items-center gap-1 rounded-full border px-2 py-0.5'));
check('each saved strategy can show its settings in place',
  mgrSrc.includes('strategy-settings-toggle-') && mgrSrc.includes('StrategyConfigSummary strategyId={s.id}'));
check('the manager explains an empty list',
  mgrSrc.includes('data-testid="strategies-empty"') && mgrSrc.includes('No strategies yet'));
const mgrHtml = flat(renderToString(
  React.createElement(MemoryRouter, null, React.createElement(Strategies))));
check('the manager renders with its search box, counter and empty state',
  mgrHtml.includes('Search strategies') && mgrHtml.includes('0 of 0 strategies')
  && mgrHtml.includes('No strategies yet'));
check('the manager keeps the family label on saved strategies',
  mgrSrc.includes('familyLabel(s.strategy_id)') && mgrSrc.includes('Parameter-based${familyLabel(s.strategy_id)}'));

// -------------------------------------------------------- run controls ----
check('Run Controls spells out what is about to run',
  phantom.includes('data-testid="run-summary"') && phantom.includes('BTCUSD perpetual')
  && phantom.includes('Kudos V2.5 (Default)') && phantom.includes('Dates'));

check('the action buttons explain themselves',
  phantom.includes('Quick per-bucket quality check') && phantom.includes('Preview Filters')
  && phantom.includes('Save as strategy') && phantom.includes('Run Backtest'));

console.log(`strategy_config_ui: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
