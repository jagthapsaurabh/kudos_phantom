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
import LiveTrade, { FeedBadge } from '../src/pages/LiveTrade.jsx';
import EntryGuardBadges from '../src/components/EntryGuardBadges.jsx';
import ConnectionCheck from '../src/components/ConnectionCheck.jsx';

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

// ------------------------------------------- shared broker account (3-4 runs) --
// One futures account carries ONE netted position per contract, so several
// live runs on the same API key take turns. The badge is how an operator sees
// that an idle strategy is queued rather than broken.
const waitingBadges = flat(renderToString(React.createElement(EntryGuardBadges, {
  held: 5,
  reason: "'Alpha' holds this account's position (LONG 0.0060 BTC) — queued behind 1 other strategy",
  shared: {
    strategies_on_account: 3, queue_position: 2, position_held_by: 'Alpha',
    holds_account_position: false, other_strategies: ['Alpha', 'Gamma'],
    note: 'one netted position per account — only one strategy can hold a trade at a time; the rest wait their turn',
  },
})));
check('a queued strategy shows its place in line',
  waitingBadges.includes('QUEUED 2/3'), waitingBadges);
check('a queued strategy is not labelled as holding the account',
  !waitingBadges.includes('HOLDS ACCOUNT'), waitingBadges);
check('the queue badge names the instance holding the position',
  waitingBadges.includes('Position currently held by: Alpha'), waitingBadges);
check('the queue badge explains the one-position rule',
  waitingBadges.includes('one netted position per account'), waitingBadges);
check('the queue badge lists the other runs on the account',
  waitingBadges.includes('Alpha, Gamma'), waitingBadges);

const holdingBadges = flat(renderToString(React.createElement(EntryGuardBadges, {
  shared: {
    strategies_on_account: 4, queue_position: 1, position_held_by: 'Alpha',
    holds_account_position: true, other_strategies: ['Beta', 'Gamma', 'Delta'],
    note: 'one netted position per account',
  },
})));
check('the instance carrying the trade says it holds the account',
  holdingBadges.includes('HOLDS ACCOUNT · 4 SHARED'), holdingBadges);
check('the holder is not shown as queued',
  !holdingBadges.includes('QUEUED'), holdingBadges);

const soloBadges = flat(renderToString(React.createElement(EntryGuardBadges, {
  held: 2, reason: 'cooldown',
  shared: { strategies_on_account: 1, queue_position: 1, position_held_by: null,
            holds_account_position: false, other_strategies: [], note: 'x' },
})));
check('a strategy alone on its key shows no shared-account badge',
  !soloBadges.includes('SHARED') && !soloBadges.includes('QUEUED'), soloBadges);
check('a missing shared_account payload renders nothing extra',
  !flat(renderToString(React.createElement(EntryGuardBadges, { held: 1 })))
    .includes('QUEUED'));

// ------------------------------------------------------- live price feed --
// The feed badge is how an operator sees that exits are being re-checked on
// live ticks -- and, more importantly, that a dropped socket has silently
// fallen back to the 60-second cadence.
const wsLive = flat(renderToString(React.createElement(FeedBadge, {
  feed: { mode: 'websocket', kind: 'websocket', connected: true, stale: false,
          age_seconds: 0.4, messages: 1200, reconnects: 0, tick_interval: 5 },
})));
check('a live websocket feed is labelled', wsLive.includes('TICK·WS'), wsLive);
check('a live websocket feed is not flagged stale', !wsLive.includes('STALE'), wsLive);
check('the tooltip reports message and reconnect counts',
  wsLive.includes('1200 messages') && wsLive.includes('0 reconnects'), wsLive);
check('the tooltip notes entries still wait for a candle',
  wsLive.includes('closed 1h candle'), wsLive);

const restLive = flat(renderToString(React.createElement(FeedBadge, {
  feed: { mode: 'rest', kind: 'rest', connected: true, stale: false,
          age_seconds: 1.2, messages: 40, reconnects: 0, tick_interval: 5 },
})));
check('a polling feed is labelled distinctly', restLive.includes('TICK·REST'), restLive);

const dead = flat(renderToString(React.createElement(FeedBadge, {
  feed: { mode: 'websocket', kind: 'websocket', connected: false, stale: true,
          age_seconds: 42.5, messages: 900, reconnects: 7, tick_interval: 5,
          last_error: 'ConnectionClosed: sent 1006' },
})));
check('a stale feed is flagged STALE', dead.includes('TICK·WS STALE'), dead);
check('a stale feed says it fell back to the 60s cadence',
  dead.includes('60-second cadence'), dead);
check('a stale feed surfaces the last socket error',
  dead.includes('ConnectionClosed: sent 1006'), dead);
check('a stale feed reports how old the price is',
  dead.includes('42.5s'), dead);

check('no badge when the feed is off',
  renderToString(React.createElement(FeedBadge, {
    feed: { mode: 'off' },
  })).trim() === '');
check('no badge when the payload is absent',
  renderToString(React.createElement(FeedBadge, { feed: null })).trim() === '');
check('no badge when the mode is missing',
  renderToString(React.createElement(FeedBadge, { feed: {} })).trim() === '');

// ------------------------------------------------- broker connection check --
// The panel that answers "I added the broker, why does it say no API keys?".
const readyReport = {
  broker: 'Binance', account: 'client1',
  definition: { code: 'Binance', name: 'Binance Futures', kind: 'binance', enabled: true, is_builtin: true },
  connections: [{ id: 3, label: 'primary', stored_code: 'Binance Futures', resolved_code: 'Binance',
                  api_key: 'KEY1••••1234', has_secret: true, is_active: true, is_testnet: false }],
  legacy_account_keys: false, ready: true, problems: [],
};
const ready = flat(renderToString(React.createElement(ConnectionCheck, { report: readyReport })));
check('a ready broker shows the READY badge', ready.includes('Ready to trade'), ready.slice(0, 160));
check('the check names the broker and the login',
  ready.includes('Binance') && ready.includes('client1'));
check('the check separates Registry from connection',
  ready.includes('Exchange Registry:') && ready.includes('primary'));
check('a code saved under another spelling is shown resolving',
  ready.includes('Binance Futures → Binance'), ready.slice(0, 400));
check('the secret itself is never rendered', !ready.includes('SECRET'));

const brokenReport = {
  broker: 'Binance', account: 'client2', definition: readyReport.definition,
  connections: [{ id: 4, label: 'primary', stored_code: 'Binance', resolved_code: 'Binance',
                  api_key: '', has_secret: false, is_active: false, is_testnet: false }],
  legacy_account_keys: false, ready: false,
  problems: ["The Binance connection 'primary' on the account 'client2' is switched off."],
};
const broken = flat(renderToString(React.createElement(ConnectionCheck, { report: brokenReport })));
check('a broken broker shows NOT READY', broken.includes('Not ready'));
check('the exact problem is printed', broken.includes('switched off'), broken.slice(-260));
check('a connection with no secret is flagged', broken.includes('NO SECRET'));
check('a switched-off connection reads as off', broken.includes('off'));

const empty = flat(renderToString(React.createElement(ConnectionCheck, {
  report: { broker: 'Delta', account: 'solo', definition: null, connections: [],
            legacy_account_keys: false, ready: false,
            problems: ["No broker connection saved on the account 'solo'."] },
})));
check('a missing registry entry is called out', empty.includes('not registered'));
check('no connections is stated plainly', empty.includes('No broker connection saved on this login'));

check('loading and empty states render',
  renderToString(React.createElement(ConnectionCheck, { loading: true })).includes('Checking connection')
  && renderToString(React.createElement(ConnectionCheck, {})).includes('No connection check yet'));

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
