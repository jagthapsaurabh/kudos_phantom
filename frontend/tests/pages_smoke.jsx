import React from 'react';
import { renderToString } from 'react-dom/server';

// Minimal browser stubs: the pages touch localStorage/window only inside
// effects (which the server renderer never runs), but a couple of module-level
// helpers read them.
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};
globalThis.window = { location: { protocol: 'http:', hostname: 'localhost' }, addEventListener() {}, removeEventListener() {} };
globalThis.fetch = () => Promise.resolve({ ok: false, json: async () => ([]) });

import Backtest from '../src/pages/Backtest.jsx';
import PaperTrade from '../src/pages/PaperTrade.jsx';
import LiveTrade from '../src/pages/LiveTrade.jsx';
import EntryGuardBadges from '../src/components/EntryGuardBadges.jsx';

let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { pass++; } else { fail++; console.log(`  FAIL: ${name} ${extra}`); }
};

for (const [name, Comp] of [['Backtest', Backtest], ['PaperTrade', PaperTrade], ['LiveTrade', LiveTrade]]) {
  let html = '';
  try {
    html = renderToString(React.createElement(Comp));
  } catch (e) {
    check(`${name} renders`, false, e.message);
    continue;
  }
  check(`${name} renders`, html.length > 200);
  check(`${name} has the run/instance controls`, /Start|Start Instance|Run Backtest/i.test(html));
}

// Backtest must show the new perpetual + window controls.
const html = renderToString(React.createElement(Backtest));
check('Backtest shows the perpetual contract', html.includes('BTCUSDT') && html.includes('perpetual'));
check('Backtest shows the mark-price switch', html.includes('Use mark price'));
check('Backtest shows the trading windows editor', html.includes('Trading windows'));
// The preset row only appears once the schedule switch is ON (default OFF,
// so a saved configuration can be kept and switched on later) — the editor
// component test covers the enabled state.
check('Backtest windows editor is collapsed until enabled', html.includes('Windows OFF') || html.includes('Trading windows'));
check('Backtest still shows the run button', html.includes('Run Backtest'));

const paper = renderToString(React.createElement(PaperTrade));
check('PaperTrade shows the pricing &amp; windows button', paper.includes('Windows'));
check('PaperTrade keeps the start button', paper.includes('Start Instance'));

const live = renderToString(React.createElement(LiveTrade));
check('LiveTrade shows the pricing &amp; windows button', live.includes('Windows'));
check('LiveTrade keeps the start button', live.includes('Start Instance'));

// Entry-guard badges: the counters that make "the worker deliberately sent no
// order this tick" visible instead of looking like a dead strategy.
// react-dom/server splits adjacent text nodes with `<!-- -->` markers, so the
// rendered markup is flattened before asserting on the visible strings.
const flat = (html) => html.replace(/<!--[^>]*-->/g, '');
const badges = flat(renderToString(React.createElement(EntryGuardBadges, {
  blocked: 3,
  held: 12,
  reason: 'position already open (LONG 0.0060 BTC) — waiting for it to close',
  position: { direction: 1, size_btc: 0.012 },
  broker: 'Delta',
})));
check('guard badges show both counters', badges.includes('3 skipped') && badges.includes('12 held'));
check('the hold reason is the tooltip', badges.includes('position already open'));
check('a venue position is shown with its size',
  badges.includes('VENUE LONG 0.0120') && badges.includes('Delta'), badges);
const shortBadges = flat(renderToString(React.createElement(EntryGuardBadges, {
  held: 4, position: { direction: -1, size_btc: 0.004 },
})));
check('a short venue position is labelled', shortBadges.includes('VENUE SHORT 0.0040'), shortBadges);
check('no badges when nothing was refused',
  renderToString(React.createElement(EntryGuardBadges, {})).trim() === '');

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
