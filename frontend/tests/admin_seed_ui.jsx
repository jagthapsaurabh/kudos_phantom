import React from 'react';
import { renderToString } from 'react-dom/server';

// Minimal browser stubs (effects never run under the server renderer).
const store = new Map([['role', 'admin'], ['token', 'test-token']]);
globalThis.localStorage = {
  getItem: k => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: k => store.delete(k),
};
globalThis.window = { location: { protocol: 'http:', hostname: 'localhost' }, addEventListener() {}, removeEventListener() {} };
globalThis.fetch = () => Promise.resolve({ ok: true, json: async () => ({}) });

import { SeedDataTab } from '../src/pages/AdminPanel.jsx';

let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { pass++; } else { fail++; console.log(`  FAIL: ${name} ${extra}`); }
};

let html = '';
try {
  html = renderToString(React.createElement(SeedDataTab));
} catch (e) {
  check('SeedDataTab renders', false, e.message);
}
check('SeedDataTab renders', html.length > 500);

// The client's corrupted Binance seed is fixed by a one-click full-history
// fetch straight from the exchange — it must be offered next to Delta's.
check('Binance full-history preset offered', html.includes('Binance 2020 → today'));
check('Delta full-history preset still offered', html.includes('Delta 2020 → today'));
check('preset announces the 2020 start', html.includes('2020'));
check('repair pass is exposed in the form', /Repair first/.test(html));
check('repair explains what it removes', /duplicate/i.test(html) && /off-grid/i.test(html));
check('standalone repair action available', /Repair existing candles/.test(html));
check('daily refresh action still available', /Run daily refresh now/.test(html));

// Daily candles are part of the seed plan.
check('daily (1d) interval selectable', html.includes('1d'));
check('fetch-all label mentions the 2020 → today range', /Fetch all date windows \(2020 → today\)/.test(html));

// The data-health columns make a corrupt seed visible at a glance.
check('duplicate column in status table', /th[^>]*>Duplicates</.test(html));
check('off-grid column in status table', /th[^>]*>Off-grid</.test(html));

// Binance full-history mode explains the fetch (1500-candle windows, upsert,
// repair-first) instead of only the generic daily-refresh note.
check('Binance history mode explainer present', html.includes('Binance full-history mode') || html.includes('straight from Binance'));

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
