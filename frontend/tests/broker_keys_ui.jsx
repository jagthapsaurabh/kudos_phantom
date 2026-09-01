// Offline checks for the "API key rejected by the exchange" surface:
//   * Broker Settings can REPLACE the keys on a saved connection (the fix the
//     banner tells you to go and do) and prefill-blank so the masked value can
//     never be saved back as the key;
//   * Check key reports which environment accepts it;
//   * the live instance card shows a credential state, a Reload keys action and
//     a deadman switch that is HELD (parked) rather than FAIL (broken);
//   * the terminal banner links both actions instead of telling anyone to
//     restart the process.
//
// Run: cd frontend && npm test
import React from 'react';
import { renderToString } from 'react-dom/server';

const store = new Map([['token', 'test-token'], ['role', 'admin']]);
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};
globalThis.window = { location: { protocol: 'http:', hostname: 'localhost' }, addEventListener() {}, removeEventListener() {} };

// What the server would answer for this login: one Delta connection whose
// account read failed with the venue's invalid_api_key text.
const CONNECTIONS = [{
  id: 7, broker_code: 'Delta', label: 'Delta Nishant sir', api_key: 'de12••••••••9f3a',
  has_secret: true, is_testnet: true, is_active: true,
  account_settings: { margin_mode: null, leverage: null, error: 'Delta HTTP 401: {"code": "invalid_api_key"}' },
  account_settings_at: '2026-08-29T16:33:24',
}];
globalThis.fetch = (url) => {
  if (String(url).includes('broker-definitions')) {
    return Promise.resolve({ ok: true, json: async () => [{ code: 'Delta', name: 'Delta Exchange' },
                                                           { code: 'Binance', name: 'Binance Futures' }] });
  }
  if (String(url).includes('broker-connections')) {
    return Promise.resolve({ ok: true, json: async () => CONNECTIONS });
  }
  return Promise.resolve({ ok: true, json: async () => ({}) });
};

import BrokerSettings, { ConnectionCard } from '../src/pages/BrokerSettings.jsx';
import LiveTrade, { CredentialsBadge, HeartbeatBadge, FeedBadge } from '../src/pages/LiveTrade.jsx';
import LiveTerminal from '../src/components/LiveTerminal.jsx';

let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { pass++; } else { fail++; console.log(`  FAIL: ${name} ${extra}`); }
};
const render = (el) => renderToString(el).replace(/<!-- -->/g, '');

// --------------------------------------------------------- Broker Settings --
// The page itself loads connections in an effect, so the card is rendered
// directly — that is where the operator's two actions live.
console.log('\n== Broker Settings: replace keys + check key ==');
let brokerHtml = '';
try {
  brokerHtml = render(React.createElement(BrokerSettings));
} catch (e) {
  check('BrokerSettings renders', false, e.message);
}
check('BrokerSettings renders', brokerHtml.length > 500);

const cardProps = {
  c: CONNECTIONS[0], busy: false, keysOpen: true, probing: false, probeResult: null,
  keyForm: { label: 'Delta Nishant sir', api_key: '', api_secret: '', is_testnet: true },
  setKeyForm: () => {}, onToggleKeys: () => {}, onSaveKeys: () => {}, onProbe: () => {},
  onRefresh: () => {}, onRemove: () => {},
};
const card = render(React.createElement(ConnectionCard, cardProps));
const collapsed = render(React.createElement(ConnectionCard, { ...cardProps, keysOpen: false }));
check('a saved connection can have its keys replaced', collapsed.includes('Replace keys'));
check('the editor offers both fields, the testnet flip and no restart',
  card.includes('API key') && card.includes('API secret')
  && card.includes('testnet / demo environment'), card.slice(0, 200));
check('the save tells the operator it reaches running instances',
  card.includes('Save and hand to running instances') && card.includes('no restart'));
check('a blank secret is explained, not silently overwritten',
  card.includes('keeps the stored one'));
check('and its key checked against the environments', card.includes('Check key'));
check('a rejected key is shown on the connection, not swallowed',
  card.includes('Could not read account details') && card.includes('invalid_api_key'));
check('the testnet flag is visible next to the connection', card.includes('· testnet'));
// The mask is only a placeholder: prefilling the field with it and saving would
// store the mask as the key — a self-inflicted invalid_api_key.
check('the edit form does not prefill the masked key as a value',
  card.includes('placeholder="de12') && !/value="de12••••/.test(card));
check('collapsed card keeps the actions available', collapsed.includes('Check key'));
const probed = render(React.createElement(ConnectionCard, {
  ...cardProps, keysOpen: false,
  probeResult: { accepted: true, summary: 'This key is accepted by TESTNET.',
                 fix: 'The connection is flagged production but the key only works on TESTNET.',
                 rows: [{ name: 'TESTNET', state: 'ok', detail: 'accepted', base_url: 'https://cdn-ind.testnet.deltaex.org' },
                        { name: 'PRODUCTION', state: 'rejected', detail: 'invalid_api_key', base_url: 'https://api.india.delta.exchange' }] },
}));
const probedText = probed.replace(/<[^>]*>/g, '').replace(/\s+/g, ' ');
check('the check result names both environments and the fix',
  probedText.includes('accepts TESTNET') && probedText.includes('rejects PRODUCTION')
  && probedText.includes('only works on TESTNET'), probedText.slice(0, 300));

// ---- Full connection test (read-only battery) ------------------------------
// The report a rejected-but-live key actually needs: market data fine, key
// accepted by Delta Global production while the connection is Delta testnet.
const tested = render(React.createElement(ConnectionCard, {
  ...cardProps, keysOpen: false,
  onTest: () => {}, testing: false, onApplyEnv: () => {},
  testResult: {
    detected: { broker_code: 'DeltaGlobal', testnet: false, name: 'GLOBAL-PRODUCTION',
                base_url: 'https://api.delta.exchange' },
    verdict: {
      ok: false,
      message: 'Connection test found issues — read the steps above.',
      problems: ['The connection targets Delta Delta but the key belongs to Delta DeltaGlobal — India and Global keep separate key stores.'],
      fixes: ['Repoint the connection to DeltaGlobal production (Test connection → Use this environment, or Edit in Broker Settings).'],
    },
    steps: [
      { name: 'market_data', title: 'Public market data (no key needed)', state: 'ok', detail: 'public ticker answered in 12 ms' },
      { name: 'clock', title: 'Server clock', state: 'ok', detail: 'local clock vs exchange: +0.40s' },
      { name: 'environment', title: 'Which Delta environment accepts this key?', state: 'ok', detail: 'accepted by GLOBAL-PRODUCTION',
        rows: [{ name: 'GLOBAL-PRODUCTION', base_url: 'https://api.delta.exchange', state: 'ok', detail: 'wallet balances OK' },
               { name: 'INDIA-TESTNET', base_url: 'https://cdn-ind.testnet.deltaex.org', state: 'auth', detail: 'invalid_api_key' }] },
      { name: 'balance', title: 'Account balance', state: 'ok', detail: 'signed call accepted',
        endpoint: 'GET /v2/wallet/balances' },
      // A step that never got an answer has to show which endpoint was asked
      // for: a host built out of "GET " + path reads as DNS trouble otherwise.
      { name: 'positions', title: 'Open positions', state: 'unreachable',
        detail: "Delta request failed: ConnectionError: Failed to resolve",
        endpoint: 'GET /v2/positions/margined?product_symbol=BTCUSD' },
    ],
  },
}));
const testedText = tested.replace(/<[^>]*>/g, '').replace(/\s+/g, ' ');
check('a failing step names the exact endpoint that was tried',
  testedText.includes('tried GET /v2/positions/margined?product_symbol=BTCUSD'), testedText.slice(0, 400));
check('a step that passed does not clutter the report with its endpoint',
  !testedText.includes('tried GET /v2/wallet/balances'));
check('the card offers a full connection test next to Check key', tested.includes('Test connection'));
check('the report explains a key that is live but on the wrong Delta family',
  testedText.includes('GLOBAL-PRODUCTION') && testedText.includes('separate key stores'));
check('and offers the one-click repoint for running instances', testedText.includes('Use this environment'));
check('the check key button remains for quick probes', tested.includes('Check key'));

// ---- Align to a named environment (deployment decision, no key check) -----
// When the operator already knows where the key belongs (Delta India
// production), the card offers to repoint broker + environment directly —
// the detection flow needs the venue to accept the key first.
const misalignedCard = render(React.createElement(ConnectionCard, {
  ...cardProps, keysOpen: false, onAlign: () => {},
  c: { ...CONNECTIONS[0], broker_code: 'DeltaGlobal' },
}));
check('a DeltaGlobal connection offers the India-production align',
  misalignedCard.includes('Align to India production'), misalignedCard.slice(0, 300));
const testnetCard = render(React.createElement(ConnectionCard, {
  ...cardProps, keysOpen: false, onAlign: () => {},
}));
check('a Delta testnet connection offers the India-production align',
  testnetCard.includes('Align to India production'));
const alignedCard = render(React.createElement(ConnectionCard, {
  ...cardProps, keysOpen: false, onAlign: () => {},
  c: { ...CONNECTIONS[0], is_testnet: false },
}));
check('a Delta production connection hides the align action (already there)',
  !alignedCard.includes('Align to India production'));
const noAlignProp = render(React.createElement(ConnectionCard, { ...cardProps, keysOpen: false }));
check('without a handler the align action is not offered (older surfaces)',
  !noAlignProp.includes('Align to India production</button>'));

// ---- The official key/API rule is shown on misaligned Delta rows ---------
const globalCard = render(React.createElement(ConnectionCard, {
  ...cardProps, keysOpen: false, onAlign: () => {},
  c: { ...CONNECTIONS[0], broker_code: 'DeltaGlobal', is_testnet: false },
}));
check('a Global connection names api.delta.exchange and says it is not used here',
  globalCard.includes('api.delta.exchange') && globalCard.includes('not used by this deployment'),
  globalCard.slice(0, 400));
const demoCard = render(React.createElement(ConnectionCard, {
  ...cardProps, keysOpen: false, onAlign: () => {},
}));
check('a testnet connection states the production-only rule for India keys',
  demoCard.includes('api.india.delta.exchange') && demoCard.includes('production keys work only'),
  demoCard.slice(0, 400));

// ------------------------------------------------------- Instance badges ----
console.log('\n== live instance: credential state + parked deadman switch ==');
const rejected = {
  state: 'rejected', error: 'Delta HTTP 401: {"code": "invalid_api_key"}', since: '2026-08-29T16:33:24',
  environment: 'testnet', base_url: 'https://cdn-ind.testnet.deltaex.org', key: '8693a2d0',
  connection_id: 7, connection_label: 'Delta Nishant sir', rejections: 9, retry_in_seconds: 42.5,
  entries_held: 37, reloads: 4, last_reload: { verified: false }, heartbeat_stood_down: true,
};
let badge = '';
try {
  badge = render(React.createElement(CredentialsBadge, { credentials: rejected, onReload: () => {} }));
} catch (e) { check('CredentialsBadge renders', false, e.message); }
check('a rejected key is announced on the instance card', badge.includes('KEY REJECTED'));
check('with the venue error verbatim in the tooltip',
  badge.includes('invalid_api_key') && badge.includes('TESTNET'), badge.slice(0, 200));
check('with the reload action that replaces a restart', badge.includes('Reload keys'));
check('and how much of the pause is counted',
  badge.includes('9') /* rejections visible in title */ || badge.includes('37'));
check('a healthy connection shows nothing at all',
  render(React.createElement(CredentialsBadge, { credentials: { state: 'ok' } })) === '');
check('a single rejection is a warning, not a verdict',
  render(React.createElement(CredentialsBadge, { credentials: { state: 'suspect', error: 'x' } }))
    .includes('KEY SUSPECT'));

const held = render(React.createElement(HeartbeatBadge, {
  heartbeat: { enabled: false, created: true, stood_down: true, stood_down_reason: 'Delta rejected the API key',
               acks: 12, failures: 1, ttl_ms: 30000, stale: true },
}));
check('a parked switch reads HELD, not FAIL', held.includes('DEADMAN HELD') && !held.includes('FAIL'));
check('and says why, so it is not mistaken for a broken safety net',
  held.includes('rejected the API key'), held.slice(0, 200));
check('a live switch still reads ON',
  render(React.createElement(HeartbeatBadge, {
    heartbeat: { enabled: true, created: true, acks: 40, failures: 0, ttl_ms: 30000, stale: false,
                 product_symbols: ['BTCUSD'] } })).includes('DEADMAN ON'));
check('and skipped acks are mentioned once they exist',
  render(React.createElement(HeartbeatBadge, {
    heartbeat: { enabled: true, created: true, acks: 40, skipped_acks: 3, failures: 0, ttl_ms: 30000,
                 stale: false, product_symbols: ['BTCUSD'] } })).includes('3 acks skipped'));
check('no feed badge noise when the feed is off',
  render(React.createElement(FeedBadge, { feed: { mode: 'off' } })) === '');

// ----------------------------------------------------- Terminal banner ------
console.log('\n== terminal banner: two actions, not a restart ==');
const deadSnapshot = {
  broker: 'Delta', symbol: 'BTCUSD', mark_price: 77981.87, contract: {}, balance: {},
  risk: { wallet_balance: 0, equity: 0, available_margin: 0, used_margin: 0, order_margin: 0,
          unrealized_pnl: 0, gross_notional: 0, net_notional: 0, long_exposure: 0, short_exposure: 0,
          margin_utilisation_pct: 0, effective_leverage: 0, free_margin_pct: 0, position_count: 0 },
  account_settings: { error: 'Delta HTTP 401: {"code": "invalid_api_key"}' },
  positions: [], open_orders: [], stop_orders: [], fills: [], order_history: [],
  errors: { balance: 'Delta HTTP 401: {"code": "invalid_api_key"}' },
  auth_error: 'Delta rejected this API key on every authenticated call (Delta HTTP 401: '
    + '{"code": "invalid_api_key"}). Replace the key on this connection in Broker Settings — '
    + "'Check key' there says which environment accepts it — and running live instances "
    + 're-read the saved credentials by themselves (or use Reload keys on the instance), so a '
    + 'key fix no longer needs a restart.',
  rate_limits: { credential_health: { state: 'rejected', error: 'Delta HTTP 401: invalid_api_key',
                                      retry_in_seconds: 41.7 } },
};
let termHtml = '';
try {
  termHtml = render(React.createElement(LiveTerminal, {
    broker: 'Delta', connectionId: 7, snapshot: deadSnapshot, autoRefresh: false,
  }));
} catch (e) { check('LiveTerminal renders a dead key', false, e.message); }
check('the banner names the failure', termHtml.includes('API key rejected by the exchange'));
check('it offers the fix, not just the diagnosis',
  termHtml.includes('Replace the key in Broker Settings') && termHtml.includes('/broker'));
check('it offers the action that replaces a restart',
  termHtml.includes('Reload keys on the instance') && termHtml.includes('href="/live"'));
check('it shows the backoff the worker is sitting in', termHtml.includes('42s') || termHtml.includes('41s'),
  termHtml.slice(termHtml.indexOf('Signed calls are held') - 100, termHtml.indexOf('Signed calls are held') + 200));
check('market data is still rendered — this is why the state was invisible before',
  termHtml.includes('77,981.87'));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
