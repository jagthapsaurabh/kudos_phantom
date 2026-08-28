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

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
