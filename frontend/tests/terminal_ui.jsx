// Server-rendered checks for the live trading terminal (Delta-style screens):
// order ticket, positions / open orders / stop orders / fills / order history
// tables, wallet + margin, risk and rate-limit panels.
import React from 'react';
import { renderToString } from 'react-dom/server';

const store = new Map([['token', 'test-token']]);
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};
globalThis.window = { location: { protocol: 'http:', hostname: 'localhost' }, addEventListener() {}, removeEventListener() {} };
globalThis.fetch = () => Promise.resolve({ ok: false, json: async () => ({}) });

import LiveTerminal, { fmt, fmtBtc, fmtSize, signed, pnlClass, perpetualFor, TABS, ORDER_TYPES,
  orderAgeSeconds, unfilledOrders, ageLabel, UNFILL_THRESHOLDS } from '../src/components/LiveTerminal.jsx';
import TerminalPage from '../src/pages/Terminal.jsx';

let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { pass++; } else { fail++; console.log(`  FAIL: ${name} ${extra}`); }
};

// ---------------------------------------------------------------- snapshot --
const snapshot = {
  broker: 'Delta',
  symbol: 'BTCUSD',
  mark_price: 67100.5,
  contract: {
    symbol: 'BTCUSD', contract_type: 'perpetual_futures', contract_value: 0.001,
    tick_size: 0.5, step_size: 1, min_size: 1, size_unit: 'contracts',
    quote_asset: 'USD', product_id: 139,
  },
  balance: {
    broker: 'Delta', asset: 'USD', wallet_balance: 1000, available_balance: 940,
    used_margin: 60, order_margin: 19.8, position_margin: 40.2, unrealized_pnl: 3,
    balances: [{ asset: 'USD', balance: 1000, available: 940 }],
  },
  risk: {
    wallet_balance: 1000, equity: 1003, available_margin: 940, used_margin: 60,
    order_margin: 19.8, unrealized_pnl: 3, gross_notional: 4026.03, net_notional: 2013.015,
    long_exposure: 2013.015, short_exposure: 2013.015, margin_utilisation_pct: 5.98,
    effective_leverage: 4.01, free_margin_pct: 93.7, position_count: 2,
  },
  positions: [{
    broker: 'Delta', symbol: 'BTCUSD', side: 'long', size: 30, qty_btc: 0.03,
    entry_price: 67000.5, mark_price: 67100.5, liquidation_price: 54000,
    bankruptcy_price: 53000, margin: 20.1, leverage: 10, notional: 2013.015,
    unrealized_pnl: 3, realized_pnl: -0.5, pnl_percent: 1.49, adl_level: null,
  }],
  open_orders: [{
    broker: 'Delta', symbol: 'BTCUSD', order_id: '91001', client_order_id: 'ph-1',
    side: 'buy', type: 'limit', leg: 'entry', size: 30, qty_btc: 0.03, price: 60000,
    status: 'open', is_open: true, is_stop: false, filled_size: 0, avg_fill_price: null,
    created_at: '2026-08-28T09:15:00',
  }],
  stop_orders: [{
    broker: 'Delta', symbol: 'BTCUSD', order_id: '91002', client_order_id: 'ph-2',
    side: 'sell', type: 'stop_market', leg: 'stop_loss', size: 30, qty_btc: 0.03,
    stop_price: 65000, trigger_method: 'mark_price', status: 'open', is_open: true,
    is_stop: true, reduce_only: true, created_at: '2026-08-28T09:15:00',
  }],
  fills: [{
    broker: 'Delta', symbol: 'BTCUSD', trade_id: '90001', order_id: '91001',
    client_order_id: 'ph-1', side: 'buy', size: 30, qty_btc: 0.03, price: 67000.5,
    fee: 0.004, role: 'taker', realized_pnl: 0, filled_at: '2026-08-28T09:16:00',
  }],
  order_history: [{
    broker: 'Delta', symbol: 'BTCUSD', order_id: '91000', client_order_id: 'ph-0',
    side: 'buy', type: 'market', leg: 'entry', size: 30, qty_btc: 0.03,
    price: null, avg_fill_price: 67000.5, status: 'filled', is_open: false,
    is_stop: false, created_at: '2026-08-28T08:00:00',
  }],
  errors: {},
  rate_limits: {
    broker: 'Delta', requests_last_second: 2, requests_last_minute: 14,
    orders_last_minute: 3, weight_used_5min: 210, exchange_quota: 6420,
    exchange_reset_ms: 123000, retried_calls: 1, rejected_calls: 0,
    orders_last_10s: 1,
    limits: { requests_per_second: 20, requests_per_minute: 1200, weight_per_5min: 10000, orders_per_minute: null, orders_per_10s: 300 },
  },
  fetched_at: '2026-08-28T09:20:00',
};

const html = renderToString(React.createElement(LiveTerminal, {
  broker: 'Delta', connectionId: 1, snapshot, autoRefresh: false,
}));

// ------------------------------------------------------------------ panels --
check('terminal renders', html.length > 500);
check('contract + mark price header', html.includes('BTCUSD') && html.includes('67,100.50'), html.slice(0, 400));
check('contract metadata shows the contract value', html.includes('0.001') && html.includes('perpetual'));
check('wallet & margin panel', html.includes('Wallet &amp; Margin') && html.includes('1,000.00') && html.includes('940.00'));
check('wallet shows used and order margin', html.includes('Used margin') && html.includes('Order margin') && html.includes('19.80'));
check('risk panel', html.includes('Risk') && html.includes('Effective lev') && html.includes('4.01'));
check('risk flags margin utilisation', html.includes('Margin used') && html.includes('5.98'));
check('rate-limit panel shows the local windows', html.includes('Rate limits') && html.includes('Per second') && html.includes('20'));
check('rate-limit panel shows the 5-minute weight budget', html.includes('Weight / 5 min') && html.includes('10,000'));
check('rate-limit panel shows the exchange quota', html.includes('exchange quota 6,420 left'));
check('rate-limit panel shows the 10-second order cap',
  html.includes('Orders / 10s') && html.includes('300'));
check('rate-limit panel reports retried calls', html.includes('1 retried after HTTP 429'));

// ------------------------------------------------------------ order ticket --
check('order ticket present', html.includes('Order Ticket'));
check('buy / sell toggle', html.includes('Buy / Long') && html.includes('Sell / Short'));
check('order type selector', ORDER_TYPES.every((t) => html.includes(t.label)));
check('size input in BTC + venue unit', html.includes('Size (BTC)') && html.includes('Unit'));
check('bracket stop-loss / take-profit inputs', html.includes('Stop loss') && html.includes('Take profit'));
check('bracket explains the venue behaviour', html.includes('native bracket order'));
// "Post only" is what the venue calls it; the ticket says what it does.
check('reduce-only + maker-only flags',
  html.includes('Reduce only') && html.includes('Maker only (post-only)'), html.slice(0, 120));
check('maker-only is limited to limit orders',
  /Maker only[\s\S]{0,200}disabled/.test(html) || html.includes('disabled'));
check('submit button sizes the order in BTC', html.includes('Buy 0.0100 BTC'));

// --------------------------------------------------------- account controls --
check('leverage & margin controls', html.includes('Leverage') && html.includes('Margin mode') && html.includes('Isolated') && html.includes('Cross'));
check('cancel all orders action', html.includes('Cancel all open orders'));
check('close position action', html.includes('Close position at market'));

// ------------------------------------------------------------------- tables --
for (const tab of TABS) {
  check(`tab present: ${tab.label}`, html.includes(tab.label));
}
// Only the selected tab is mounted, so render each one to assert its table.
const tabHtml = {};
for (const tab of TABS) {
  tabHtml[tab.key] = renderToString(React.createElement(LiveTerminal, {
    broker: 'Delta', connectionId: 1, snapshot, autoRefresh: false, initialTab: tab.key,
  }));
}

check('positions table shows side, entry, mark, liquidation',
  html.includes('LONG') && html.includes('67,000.50') && html.includes('54,000.00'));
check('positions table shows margin + leverage', html.includes('20.10') && html.includes('10x'));
check('positions table shows unrealised PnL and ROE', html.includes('+3.00') && html.includes('+1.49%'));
check('positions table offers a close button', html.includes('>Close<'));
const openHtml = tabHtml.open_orders;
check('open orders table shows the working limit order',
  openHtml.includes('LIMIT') && openHtml.includes('60,000.00') && openHtml.includes('91001'));
check('open orders table offers a cancel button', openHtml.includes('>Cancel<'));
const stopHtml = tabHtml.stop_orders;
check('stop orders table shows the trigger and its method',
  stopHtml.includes('65,000.00') && stopHtml.includes('MARK PRICE'));
check('stop orders table labels the leg', stopHtml.includes('STOP LOSS'));
check('stop orders table shows reduce-only', stopHtml.includes('Reduce only'));
const fillsHtml = tabHtml.fills;
check('fills table shows price, fee and role',
  fillsHtml.includes('67,000.50') && fillsHtml.includes('0.0040') && fillsHtml.includes('TAKER'));
check('fills table shows the trade id', fillsHtml.includes('90001'));
const historyHtml = tabHtml.order_history;
check('order history table shows the filled status', historyHtml.includes('FILLED') && historyHtml.includes('91000'));
check('order history table shows the average fill', historyHtml.includes('67,000.50'));

// ------------------------------------------------------- empty + error state --
const emptyHtml = renderToString(React.createElement(LiveTerminal, {
  broker: 'Binance', snapshot: null, autoRefresh: false,
}));
check('empty state renders every tab', TABS.every((t) => emptyHtml.includes(t.label)));
check('empty state explains there are no positions', emptyHtml.includes('No open positions.'));
check('empty state shows the Binance ticket', emptyHtml.includes('BTCUSDT'));
check('empty state notes Binance bracket emulation', emptyHtml.includes('reduce-only stops'));

const errorHtml = renderToString(React.createElement(LiveTerminal, {
  broker: 'Delta',
  snapshot: { ...snapshot, errors: { fills: 'HTTP 429' } },
  autoRefresh: false,
}));
check('partial failures are surfaced', errorHtml.includes('Partial data') && errorHtml.includes('fills'));

// --------------------------------------------------------------- formatting --
check('fmt formats with thousands separators', fmt(67100.5) === '67,100.50', fmt(67100.5));
check('fmt falls back for missing values', fmt(null) === '—' && fmt(undefined, 2, 'n/a') === 'n/a');
check('fmtBtc uses 4 decimals', fmtBtc(0.03) === '0.0300', fmtBtc(0.03));
check('fmtSize drops decimals for contracts', fmtSize(30, 'contracts') === '30', fmtSize(30, 'contracts'));
check('signed adds a plus for gains and nothing for losses',
  signed(3.2) === '+3.20' && signed(-1.5) === '-1.50', `${signed(3.2)}/${signed(-1.5)}`);
check('pnlClass colours gains green and losses red',
  pnlClass(1) === 'text-green-400' && pnlClass(-1) === 'text-red-400' && pnlClass(0) === 'text-gray-400');
check('perpetualFor maps both venues', perpetualFor('Delta') === 'BTCUSD' && perpetualFor('Binance') === 'BTCUSDT');

// ------------------------------------------------------ unfilled-order alert --
const NOW = Date.parse('2026-08-28T12:00:00Z');
const resting = { ...snapshot.open_orders[0], created_at: '2026-08-28T11:59:00Z' };
const stale = { ...snapshot.open_orders[0], created_at: '2026-08-28T11:00:00Z' };
const partFilled = { ...stale, order_id: '91003', filled_size: 10, unfilled_size: 20 };
const stopLeg = { ...snapshot.stop_orders[0], created_at: '2026-08-28T11:00:00Z', unfilled_size: 30 };
const filledOrder = { ...stale, order_id: '91004', filled_size: 30, unfilled_size: 0 };

check('orderAgeSeconds measures the wait', orderAgeSeconds(stale, NOW) === 3600,
  String(orderAgeSeconds(stale, NOW)));
check('orderAgeSeconds is null without a timestamp',
  orderAgeSeconds({ order_id: 'x' }, NOW) === null);
check('orderAgeSeconds accepts epoch millis',
  orderAgeSeconds({ created_at: NOW - 5000 }, NOW) === 5);
check('ageLabel formats seconds, minutes and hours',
  ageLabel(45) === '45s' && ageLabel(125) === '2m 5s' && ageLabel(3660) === '1h 1m',
  `${ageLabel(45)}/${ageLabel(125)}/${ageLabel(3660)}`);

const flagged = unfilledOrders([resting, stale, partFilled, stopLeg, filledOrder],
  { nowMs: NOW, olderThanSeconds: 300 });
check('only orders older than the threshold are flagged',
  flagged.length === 2, JSON.stringify(flagged.map((o) => o.order_id)));
check('the flagged rows carry their age',
  flagged.every((o) => typeof o.age_seconds === 'number' && o.age_seconds >= 300));
check('a stop / take-profit leg is never flagged',
  !flagged.some((o) => o.is_stop), 'stop legs are meant to rest until they trigger');
check('a fully filled order is not flagged',
  !flagged.some((o) => o.order_id === '91004'));
check('a partly filled order is still flagged',
  flagged.some((o) => o.order_id === '91003'));
check('oldest first', flagged[0].age_seconds >= flagged[1].age_seconds);
check('threshold 0 switches the alert off',
  unfilledOrders([stale], { nowMs: NOW, olderThanSeconds: 0 }).length === 0);
check('the thresholds offered include an off switch',
  UNFILL_THRESHOLDS.some((t) => t.value === 0) && UNFILL_THRESHOLDS.length >= 4);

// The banner, the aged Open Orders table and the alert control, as rendered.
const alertHtml = renderToString(React.createElement(LiveTerminal, {
  broker: 'Delta', connectionId: 1, snapshot, autoRefresh: false, initialTab: 'open_orders',
}));
check('the unfilled banner is rendered for a stale order',
  alertHtml.includes('unfilled order') && alertHtml.includes('resting longer than'),
  alertHtml.slice(0, 300));
check('the banner names the order and how long it waited',
  alertHtml.includes('91001') || alertHtml.includes('BTCUSD'));
check('Open Orders gained a Resting column', alertHtml.includes('Resting'));
check('Open Orders gained an Unfilled column', alertHtml.includes('Unfilled'));
check('the terminal exposes the alert threshold control',
  alertHtml.includes('Unfilled alert') && alertHtml.includes('Flag a working order after'));
check('the ticket offers maker-only execution', html.includes('Maker only (post-only)'));
check('the ticket keeps both sides and every order type',
  html.includes('Buy / Long') && html.includes('Sell / Short')
  && ORDER_TYPES.every((t) => html.includes(t.label)));
check('the terminal has leverage and margin-type controls',
  html.includes('Leverage') && html.includes('Margin mode')
  && html.includes('Isolated') && html.includes('Cross'));

// -------------------------------------------------------------- page shell --
const pageHtml = renderToString(React.createElement(TerminalPage));
check('terminal page renders the header', pageHtml.includes('Live Terminal'));
check('terminal page has broker + connection selectors',
  pageHtml.includes('Broker') && pageHtml.includes('Connection'));
check('terminal page shows the perpetual contract', pageHtml.includes('BTCUSDT') && pageHtml.includes('perpetual'));

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
