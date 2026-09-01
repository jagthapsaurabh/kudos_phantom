import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, Ban, BellRing, Clock, Crosshair,
  DollarSign, Gauge, History, Layers, Percent, RefreshCw, ShieldAlert, ShieldCheck, Sliders,
  Trash2, TrendingUp, Wallet, XCircle, Zap,
} from 'lucide-react';
import { API_URL } from '../api';

// The tool trades the BTC *perpetual* on every venue: Binance lists it as
// BTCUSDT, Delta as BTCUSD.
export const perpetualFor = (source) =>
  (String(source || '').toLowerCase() === 'delta' ? 'BTCUSD' : 'BTCUSDT');

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------
export const fmt = (value, dp = 2, fallback = '—') => {
  // null / '' must read as "no data", not as zero — a missing mark price or an
  // unavailable margin figure would otherwise look like a real 0.00 balance.
  if (value === null || value === undefined || value === '') return fallback;
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return n.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });
};

export const fmtBtc = (value) => fmt(value, 4, '—');
export const fmtSize = (value, unit) => fmt(value, unit === 'contracts' ? 0 : 4, '—');

export const pnlClass = (value) =>
  Number(value) > 0 ? 'text-green-400' : Number(value) < 0 ? 'text-red-400' : 'text-gray-400';

export const signed = (value, dp = 2) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return `${n > 0 ? '+' : ''}${fmt(n, dp)}`;
};

export const shortTime = (value) => {
  if (!value) return '—';
  const text = String(value).replace('T', ' ').slice(0, 19);
  return text;
};

export const ORDER_TYPES = [
  { value: 'market', label: 'Market' },
  { value: 'limit', label: 'Limit' },
  { value: 'stop_market', label: 'Stop Market' },
  { value: 'stop_limit', label: 'Stop Limit' },
  { value: 'take_profit_market', label: 'Take Profit' },
  { value: 'trailing_stop', label: 'Trailing Stop' },
];

// How long a resting order may sit unfilled before the terminal shouts about it.
export const UNFILL_THRESHOLDS = [
  { value: 0, label: 'Unfilled alert off' },
  { value: 30, label: '30 seconds' },
  { value: 60, label: '1 minute' },
  { value: 300, label: '5 minutes' },
  { value: 900, label: '15 minutes' },
];

/**
 * Seconds an order has been resting, or ``null`` when the venue gave no time.
 *
 * ``created_at`` arrives as an ISO string from either venue; a bare number is
 * accepted too (epoch ms) so a raw payload still ages correctly.
 */
export const orderAgeSeconds = (order, nowMs = Date.now()) => {
  const raw = order && order.created_at;
  if (!raw) return null;
  const ms = typeof raw === 'number' ? raw : Date.parse(String(raw));
  if (!Number.isFinite(ms)) return null;
  return Math.max(0, (Number(nowMs) - ms) / 1000);
};

/**
 * Size still waiting to fill.
 *
 * The API sends ``unfilled_size``, but it is derived from ``size`` and
 * ``filled_size``, so a payload that only carries those two still ages and
 * alerts correctly instead of reading as fully filled.
 */
export const remainingSize = (order) => {
  if (!order) return 0;
  const direct = Number(order.unfilled_size);
  if (Number.isFinite(direct)) return direct;
  const size = Number(order.size) || 0;
  const filled = Number(order.filled_size) || 0;
  return Math.max(0, size - filled);
};

/**
 * Open (or partly filled) orders that are still unfilled and older than the
 * threshold — the "unfilled alert" the terminal shows above the tables.
 *
 * A stop / take-profit leg is deliberately excluded: it is *meant* to rest
 * until price reaches the trigger, so flagging it would be noise. Only working
 * entries that should have filled are reported. Each row carries `age_seconds`
 * so the caller can show how long it has been waiting.
 */
export const unfilledOrders = (orders, { nowMs = Date.now(), olderThanSeconds = 60 } = {}) => {
  const threshold = Number(olderThanSeconds);
  if (!Number.isFinite(threshold) || threshold <= 0 || !Array.isArray(orders)) return [];
  return orders
    .filter((o) => o && !o.is_stop && remainingSize(o) > 0)
    .map((o) => ({ ...o, age_seconds: orderAgeSeconds(o, nowMs) }))
    .filter((o) => o.age_seconds !== null && o.age_seconds >= threshold)
    .sort((a, b) => b.age_seconds - a.age_seconds);
};


export const ageLabel = (seconds) => {
  const n = Number(seconds);
  if (!Number.isFinite(n)) return '—';
  if (n < 60) return `${Math.floor(n)}s`;
  if (n < 3600) return `${Math.floor(n / 60)}m ${Math.floor(n % 60)}s`;
  return `${Math.floor(n / 3600)}h ${Math.floor((n % 3600) / 60)}m`;
};

export const TABS = [
  { key: 'positions', label: 'Positions', icon: Layers },
  { key: 'open_orders', label: 'Open Orders', icon: Activity },
  { key: 'stop_orders', label: 'Stop Orders', icon: ShieldAlert },
  { key: 'fills', label: 'Fills', icon: Crosshair },
  { key: 'order_history', label: 'Order History', icon: History },
];

const EMPTY = {
  broker: '', symbol: 'BTCUSDT', mark_price: null,
  contract: {}, balance: {}, risk: {}, positions: [], open_orders: [],
  stop_orders: [], fills: [], order_history: [], errors: {}, rate_limits: {},
};

// ---------------------------------------------------------------------------
// Small building blocks
// ---------------------------------------------------------------------------
const Field = ({ label, children, hint }) => (
  <label className="block">
    <span className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-gray-500">{label}</span>
    {children}
    {hint ? <span className="mt-1 block text-[10px] text-gray-600">{hint}</span> : null}
  </label>
);

const Stat = ({ label, value, tone = 'text-white', hint }) => (
  <div className="rounded-lg border border-gray-700 bg-gray-900/60 px-3 py-2">
    <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">{label}</div>
    <div className={`font-mono text-sm font-bold ${tone}`}>{value}</div>
    {hint ? <div className="text-[10px] text-gray-600">{hint}</div> : null}
  </div>
);

const Table = ({ columns, rows, empty }) => (
  <div className="overflow-x-auto">
    <table className="w-full min-w-[720px] text-left text-xs">
      <thead className="text-[10px] uppercase tracking-wider text-gray-500">
        <tr className="border-b border-gray-700">
          {columns.map((c) => (
            <th key={c.key} className={`px-3 py-2 font-bold ${c.className || ''}`}>{c.label}</th>
          ))}
        </tr>
      </thead>
      <tbody className="font-mono text-gray-300">
        {rows.length === 0 && (
          <tr><td className="px-3 py-6 text-center text-gray-600" colSpan={columns.length}>{empty}</td></tr>
        )}
        {rows.map((row, index) => (
          <tr key={row.__key || index} className="border-b border-gray-800/70 hover:bg-gray-900/50">
            {columns.map((c) => (
              <td key={c.key} className={`px-3 py-2 ${c.className || ''}`}>
                {typeof c.render === 'function' ? c.render(row, index) : row[c.key]}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

const Badge = ({ children, tone = 'gray', title }) => {
  // Text is uppercased in the markup too, so it reads the same when copied out
  // of the page as it does on screen.
  const tones = {
    gray: 'border-gray-700 bg-gray-900 text-gray-400',
    green: 'border-green-800 bg-green-900/20 text-green-300',
    red: 'border-red-900 bg-red-900/20 text-red-300',
    amber: 'border-amber-800 bg-amber-900/20 text-amber-300',
    blue: 'border-blue-800 bg-blue-900/20 text-blue-300',
  };
  return (
    <span title={title} className={`rounded border px-1.5 py-0.5 text-[10px] font-bold uppercase ${tones[tone] || tones.gray}`}>
      {String(children ?? '').toUpperCase()}
    </span>
  );
};

// ---------------------------------------------------------------------------
// Order ticket
// ---------------------------------------------------------------------------
const OrderTicket = ({ broker, symbol, contract, markPrice, disabled, onSubmit, busy, notice }) => {
  const [side, setSide] = useState('buy');
  const [orderType, setOrderType] = useState('market');
  const [size, setSize] = useState('0.01');
  const [sizeUnit, setSizeUnit] = useState('btc');
  const [price, setPrice] = useState('');
  const [stopPrice, setStopPrice] = useState('');
  const [stopLoss, setStopLoss] = useState('');
  const [takeProfit, setTakeProfit] = useState('');
  const [reduceOnly, setReduceOnly] = useState(false);
  const [postOnly, setPostOnly] = useState(false);

  const isContracts = String(contract?.size_unit || '').toLowerCase() === 'contracts';
  const contractValue = Number(contract?.contract_value) || 1;
  const mark = Number(markPrice) || 0;
  const sizeNumber = Number(size) || 0;
  const qtyBtc = sizeUnit === 'btc' ? sizeNumber
    : isContracts ? sizeNumber * contractValue
      : sizeNumber;
  const notional = qtyBtc * (Number(price) || mark);

  const submit = (event) => {
    event.preventDefault();
    if (!sizeNumber || sizeNumber <= 0) return;
    onSubmit({
      side,
      order_type: orderType,
      size: sizeNumber,
      size_in_btc: sizeUnit === 'btc',
      price: price === '' ? null : Number(price),
      stop_price: stopPrice === '' ? null : Number(stopPrice),
      stop_loss: stopLoss === '' ? null : Number(stopLoss),
      take_profit: takeProfit === '' ? null : Number(takeProfit),
      reduce_only: reduceOnly,
      post_only: postOnly,
    });
  };

  const needsPrice = ['limit', 'stop_limit'].includes(orderType);
  const needsStop = ['stop_market', 'stop_limit', 'take_profit_market', 'trailing_stop'].includes(orderType);

  return (
    <form onSubmit={submit} className="rounded-2xl border border-gray-700 bg-gray-800 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-bold uppercase tracking-wider text-gray-400">
          <Sliders size={15} /> Order Ticket
        </h3>
        <span className="rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 font-mono text-[10px] text-gray-400">
          {symbol || perpetualFor(broker)}
        </span>
      </div>

      <div className="mb-3 grid grid-cols-2 gap-2">
        <button type="button" onClick={() => setSide('buy')}
                className={`rounded-lg py-2 text-sm font-bold transition ${side === 'buy' ? 'bg-green-600 text-white' : 'bg-gray-900 text-green-400 border border-green-900'}`}>
          <ArrowUpRight size={14} className="mr-1 inline" /> Buy / Long
        </button>
        <button type="button" onClick={() => setSide('sell')}
                className={`rounded-lg py-2 text-sm font-bold transition ${side === 'sell' ? 'bg-red-600 text-white' : 'bg-gray-900 text-red-400 border border-red-900'}`}>
          <ArrowDownRight size={14} className="mr-1 inline" /> Sell / Short
        </button>
      </div>

      <div className="space-y-3">
        <Field label="Order type">
          <select value={orderType} onChange={(e) => setOrderType(e.target.value)}
                  className="w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-white outline-none focus:border-blue-500">
            {ORDER_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </Field>

        <div className="grid grid-cols-3 gap-2">
          <div className="col-span-2">
            <Field label={sizeUnit === 'btc' ? 'Size (BTC)' : `Size (${isContracts ? 'contracts' : 'lots'})`}>
              <input type="number" step="any" min="0" value={size}
                     onChange={(e) => setSize(e.target.value)}
                     className="w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 font-mono text-sm text-white outline-none focus:border-blue-500" />
            </Field>
          </div>
          <Field label="Unit">
            <select value={sizeUnit} onChange={(e) => setSizeUnit(e.target.value)}
                    className="w-full rounded-lg border border-gray-700 bg-gray-900 px-2 py-2 text-sm text-white outline-none focus:border-blue-500">
              <option value="btc">BTC</option>
              <option value="venue">{isContracts ? 'Contracts' : 'Lots'}</option>
            </select>
          </Field>
        </div>

        {needsPrice && (
          <Field label="Limit price">
            <input type="number" step="any" min="0" value={price} placeholder={mark ? fmt(mark, 2) : '0.00'}
                   onChange={(e) => setPrice(e.target.value)}
                   className="w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 font-mono text-sm text-white outline-none focus:border-blue-500" />
          </Field>
        )}
        {needsStop && (
          <Field label={orderType === 'trailing_stop' ? 'Activation / trail price' : 'Stop price'}>
            <input type="number" step="any" min="0" value={stopPrice}
                   onChange={(e) => setStopPrice(e.target.value)}
                   className="w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 font-mono text-sm text-white outline-none focus:border-blue-500" />
          </Field>
        )}

        <div className="rounded-lg border border-gray-700 bg-gray-900/60 p-3">
          <div className="mb-2 flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-gray-500">
            <ShieldAlert size={12} /> Bracket (stop-loss / take-profit)
          </div>
          <div className="grid grid-cols-2 gap-2">
            <input type="number" step="any" min="0" value={stopLoss} placeholder="Stop loss"
                   onChange={(e) => setStopLoss(e.target.value)}
                   className="rounded-lg border border-gray-700 bg-gray-900 px-2 py-1.5 font-mono text-xs text-white outline-none focus:border-red-500" />
            <input type="number" step="any" min="0" value={takeProfit} placeholder="Take profit"
                   onChange={(e) => setTakeProfit(e.target.value)}
                   className="rounded-lg border border-gray-700 bg-gray-900 px-2 py-1.5 font-mono text-xs text-white outline-none focus:border-green-500" />
          </div>
          <p className="mt-1.5 text-[10px] text-gray-600">
            {broker === 'Delta'
              ? 'Sent as a native bracket order — the unused leg is cancelled for you.'
              : 'Binance has no bracket endpoint: the protection legs are placed as reduce-only stops.'}
          </p>
        </div>

        <div className="flex flex-wrap gap-4 text-[11px] text-gray-400">
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={reduceOnly} onChange={(e) => setReduceOnly(e.target.checked)}
                   className="h-3.5 w-3.5 accent-blue-500" /> Reduce only
          </label>
          <label className="flex items-center gap-1.5"
                 title="Maker only: the venue rejects the order instead of taking liquidity, so it never pays a taker fee. Limit orders only.">
            <input type="checkbox" checked={postOnly} onChange={(e) => setPostOnly(e.target.checked)}
                   disabled={orderType !== 'limit'}
                   className="h-3.5 w-3.5 accent-blue-500" />
            Maker only (post-only)
          </label>
        </div>
        {orderType !== 'limit' && postOnly && (
          <p className="text-[10px] text-amber-400">Maker only applies to limit orders — it will be ignored for {orderType.replace(/_/g, ' ')}.</p>
        )}

        <div className="flex items-center justify-between rounded-lg border border-gray-700 bg-gray-900/60 px-3 py-2 text-[11px]">
          <span className="text-gray-500">Notional</span>
          <span className="font-mono text-white">{fmt(notional, 2)} {contract?.quote_asset || 'USD'}</span>
        </div>

        <button type="submit" disabled={disabled || busy || sizeNumber <= 0}
                className={`w-full rounded-lg py-2.5 text-sm font-bold text-white transition disabled:opacity-40 ${
                  side === 'buy' ? 'bg-green-600 hover:bg-green-500' : 'bg-red-600 hover:bg-red-500'}`}>
          {busy ? 'Sending…' : `${side === 'buy' ? 'Buy' : 'Sell'} ${qtyBtc ? fmtBtc(qtyBtc) : '0'} BTC`}
        </button>
        {notice ? (
          <p className={`text-[11px] ${notice.ok ? 'text-green-400' : 'text-red-400'}`}>{notice.text}</p>
        ) : null}
      </div>
    </form>
  );
};

// ---------------------------------------------------------------------------
// Terminal
// ---------------------------------------------------------------------------
const LiveTerminal = ({ broker = 'Delta', connectionId = null, snapshot: initialSnapshot = null,
                        autoRefresh = true, refreshMs = 10000, initialTab = 'positions' }) => {
  const [snapshot, setSnapshot] = useState(initialSnapshot || EMPTY);
  const [tab, setTab] = useState(initialTab);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const [error, setError] = useState(null);
  const [lastRefresh, setLastRefresh] = useState(null);
  // Broker not ready (no keys / switched-off connection): the snapshot API
  // would 400 on every poll, so we surface the fix instead of hammering it.
  const [configRequired, setConfigRequired] = useState(false);
  const [configProblems, setConfigProblems] = useState([]);
  const [leverage, setLeverage] = useState(10);
  const [marginMode, setMarginMode] = useState('isolated');
  // Margin mode + leverage now come from the exchange account itself (see
  // account_settings on the snapshot) — the select/input only keep a local
  // override once the user touches them. Switching broker or connection
  // (main account vs a sub-account, each with its own margin mode) re-syncs.
  const touched = useRef({ margin: false, leverage: false });
  // "Unfilled alert": how long a working order may rest before the terminal
  // flags it. 0 switches the alert off.
  const [unfillAfter, setUnfillAfter] = useState(60);
  const timer = useRef(null);

  const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;
  const headers = useMemo(() => ({
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }), [token]);

  const call = useCallback(async (path, body, method = 'POST') => {
    const response = await fetch(`${API_URL}${path}`, {
      method, headers, ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || payload.error || `HTTP ${response.status}`);
    return payload;
  }, [headers]);

  const refresh = useCallback(async () => {
    if (!broker) return;
    setLoading(true);
    try {
      // Confirm the broker is actually usable before hitting the account API.
      // Without credentials the snapshot endpoint 400s on every poll and the
      // console fills with "Bad Request"; the diagnose response says precisely
      // what to fix instead.
      try {
        const diagUrl = `${API_URL}/broker-connections/diagnose?broker=${encodeURIComponent(broker)}`
          + (connectionId ? `&connection_id=${connectionId}` : '');
        const diagRes = await fetch(diagUrl, { headers });
        const diag = diagRes.ok ? await diagRes.json() : null;
        if (diag && diag.ready === false) {
          setConfigRequired(true);
          setConfigProblems(Array.isArray(diag.problems) ? diag.problems : []);
          setError(null);
          setNotice(null);
          setSnapshot(EMPTY);
          setLastRefresh(null);
          return;
        }
        if (diag && diag.ready) setConfigRequired(false);
      } catch (_) {
        // If the readiness probe itself fails, fall through to the snapshot
        // call rather than showing a misleading "not configured" state.
      }

      const data = await call('/live-account/snapshot', {
        broker, connection_id: connectionId || null, symbol: 'BTCUSDT',
        include_history: true, history_limit: 50,
      });
      setSnapshot(data || EMPTY);
      setError(null);
      setConfigRequired(false);
      setLastRefresh(new Date().toISOString().slice(11, 19));
    } catch (err) {
      setError(err.message || 'Could not load the account');
    } finally {
      setLoading(false);
    }
  }, [broker, connectionId, call, headers]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    refresh();
    timer.current = setInterval(refresh, refreshMs);
    return () => clearInterval(timer.current);
  }, [autoRefresh, refresh, refreshMs]);

  const act = async (fn, okText) => {
    setBusy(true);
    setNotice(null);
    try {
      await fn();
      setNotice({ ok: true, text: okText });
      await refresh();
    } catch (err) {
      setNotice({ ok: false, text: err.message || 'Request failed' });
    } finally {
      setBusy(false);
    }
  };

  const placeOrder = (order) => act(
    () => call('/live-account/orders', { broker, connection_id: connectionId || null, symbol: 'BTCUSDT', ...order }),
    'Order sent to the exchange.',
  );
  const cancelOrder = (row) => act(
    () => call('/live-account/orders/cancel', {
      broker, connection_id: connectionId || null, symbol: 'BTCUSDT',
      order_id: row.order_id || null, client_order_id: row.client_order_id || null,
    }),
    'Order cancelled.',
  );
  const cancelAll = () => act(
    () => call('/live-account/orders/cancel-all', { broker, connection_id: connectionId || null, symbol: 'BTCUSDT' }),
    'All open orders cancelled.',
  );
  const closePosition = (row) => act(
    () => call('/live-account/positions/close', {
      broker, connection_id: connectionId || null, symbol: 'BTCUSDT',
      size: row && row.qty_btc ? row.qty_btc : null, size_in_btc: true,
    }),
    'Position closed at market.',
  );
  const applyLeverage = () => act(
    async () => {
      await call('/live-account/leverage', { broker, connection_id: connectionId || null, symbol: 'BTCUSDT', leverage: Number(leverage) });
      // Re-sync from the venue on the refresh that follows, so the input
      // settles on what the exchange actually applied.
      touched.current.leverage = false;
    },
    `Leverage set to ${leverage}x.`,
  );
  const applyMarginMode = () => act(
    async () => {
      await call('/live-account/margin-mode', { broker, connection_id: connectionId || null, symbol: 'BTCUSDT', mode: marginMode });
      touched.current.margin = false;
    },
    `Margin mode set to ${marginMode}.`,
  );

  const data = snapshot || EMPTY;
  const contract = data.contract || {};
  const balance = data.balance || {};
  const risk = data.risk || {};
  const account = data.account_settings || null;

  // Adopt the venue's account settings (margin mode, leverage) until the
  // user overrides them locally. Re-runs on every refresh but is a no-op
  // once touched, so it never fights the operator.
  useEffect(() => {
    if (!account || account.error) return;
    if (!touched.current.margin && account.margin_family) setMarginMode(account.margin_family);
    if (!touched.current.leverage && account.leverage) setLeverage(String(account.leverage));
  }, [account]);
  // Different connection = different (sub)account with its own settings.
  useEffect(() => {
    touched.current = { margin: false, leverage: false };
  }, [broker, connectionId]);
  const rate = (data.rate_limits || {}).limits || {};
  const usage = data.rate_limits || {};
  const unit = contract.size_unit === 'contracts' ? 'contracts' : 'lots';
  const rows = {
    positions: data.positions || [],
    open_orders: data.open_orders || [],
    stop_orders: data.stop_orders || [],
    fills: data.fills || [],
    order_history: data.order_history || [],
  };
  // Working entries still unfilled past the chosen age — shown as a banner and
  // aged inline in the Open Orders table.
  const unfilled = unfilledOrders(rows.open_orders, { olderThanSeconds: unfillAfter });

  const columns = {
    positions: [
      { key: 'symbol', label: 'Contract', render: (r) => (
        <span className="flex items-center gap-2">
          <Badge tone={r.side === 'long' ? 'green' : 'red'}>{r.side}</Badge>
          <span className="text-white">{r.symbol}</span>
        </span>) },
      { key: 'qty_btc', label: 'Size (BTC)', render: (r) => fmtBtc(r.qty_btc) },
      { key: 'size', label: `Size (${unit})`, render: (r) => fmtSize(r.size, unit) },
      { key: 'entry_price', label: 'Entry', render: (r) => fmt(r.entry_price, 2) },
      { key: 'mark_price', label: 'Mark', render: (r) => fmt(r.mark_price, 2) },
      { key: 'liquidation_price', label: 'Liquidation', render: (r) => (
        <span className="text-amber-400">{fmt(r.liquidation_price, 2)}</span>) },
      { key: 'margin', label: 'Margin', render: (r) => fmt(r.margin, 2) },
      { key: 'leverage', label: 'Lev', render: (r) => (r.leverage ? `${r.leverage}x` : '—') },
      { key: 'unrealized_pnl', label: 'uPnL', render: (r) => (
        <span className={pnlClass(r.unrealized_pnl)}>{signed(r.unrealized_pnl, 2)}</span>) },
      { key: 'pnl_percent', label: 'ROE %', render: (r) => (
        <span className={pnlClass(r.pnl_percent)}>{`${signed(r.pnl_percent, 2)}%`}</span>) },
      { key: '_close', label: '', render: (r) => (
        <button onClick={() => closePosition(r)} disabled={busy}
                className="rounded border border-red-900 bg-red-900/20 px-2 py-1 text-[10px] font-bold text-red-300 transition hover:bg-red-900/40">
          Close
        </button>) },
    ],
    open_orders: [
      { key: 'created_at', label: 'Time', render: (r) => shortTime(r.created_at) },
      { key: '_age', label: 'Resting', render: (r) => {
        const age = orderAgeSeconds(r);
        if (age === null) return <span className="text-gray-600">—</span>;
        const stale = unfillAfter > 0 && age >= unfillAfter;
        return <span className={stale ? 'font-bold text-amber-400' : 'text-gray-400'}
                     title={stale ? `Unfilled for ${ageLabel(age)} — past the ${ageLabel(unfillAfter)} alert threshold` : `Waiting ${ageLabel(age)}`}>
          {ageLabel(age)}{stale ? ' ⚠' : ''}
        </span>;
      } },
      { key: 'symbol', label: 'Contract', render: (r) => r.symbol },
      { key: 'type', label: 'Type', render: (r) => <span className="uppercase">{String(r.type || '').replace(/_/g, ' ').toUpperCase()}</span> },
      { key: 'side', label: 'Side', render: (r) => <Badge tone={r.side === 'buy' ? 'green' : 'red'}>{r.side}</Badge> },
      { key: 'qty_btc', label: 'Size (BTC)', render: (r) => fmtBtc(r.qty_btc) },
      { key: 'price', label: 'Price', render: (r) => fmt(r.price, 2) },
      { key: 'filled_size', label: 'Filled', render: (r) => fmtSize(r.filled_size, unit) },
      { key: 'unfilled_size', label: 'Unfilled', render: (r) => (
        <span className={remainingSize(r) > 0 ? 'text-amber-400' : 'text-gray-500'}>
          {fmtSize(remainingSize(r), unit)}
        </span>) },
      { key: 'order_id', label: 'Order ID', render: (r) => <span className="text-gray-500">{r.order_id}</span> },
      { key: '_cancel', label: '', render: (r) => (
        <button onClick={() => cancelOrder(r)} disabled={busy}
                className="rounded border border-gray-700 px-2 py-1 text-[10px] font-bold text-gray-300 transition hover:border-red-700 hover:text-red-300">
          Cancel
        </button>) },
    ],
    stop_orders: [
      { key: 'created_at', label: 'Time', render: (r) => shortTime(r.created_at) },
      { key: 'leg', label: 'Leg', render: (r) => (
        <Badge tone={r.leg === 'take_profit' ? 'green' : 'amber'}>
          {String(r.leg || '').replace(/_/g, ' ')}
        </Badge>) },
      { key: 'side', label: 'Side', render: (r) => <Badge tone={r.side === 'buy' ? 'green' : 'red'}>{r.side}</Badge> },
      { key: 'qty_btc', label: 'Size (BTC)', render: (r) => fmtBtc(r.qty_btc) },
      { key: 'stop_price', label: 'Trigger', render: (r) => fmt(r.stop_price, 2) },
      { key: 'price', label: 'Limit', render: (r) => fmt(r.price, 2) },
      { key: 'trigger_method', label: 'Triggered by', render: (r) => (
        <span className="uppercase text-gray-400">{String(r.trigger_method || '').replace(/_/g, ' ').toUpperCase()}</span>) },
      { key: 'reduce_only', label: 'Reduce only', render: (r) => (r.reduce_only ? 'yes' : 'no') },
      { key: '_cancel', label: '', render: (r) => (
        <button onClick={() => cancelOrder(r)} disabled={busy}
                className="rounded border border-gray-700 px-2 py-1 text-[10px] font-bold text-gray-300 transition hover:border-red-700 hover:text-red-300">
          Cancel
        </button>) },
    ],
    fills: [
      { key: 'filled_at', label: 'Time', render: (r) => shortTime(r.filled_at) },
      { key: 'symbol', label: 'Contract', render: (r) => r.symbol },
      { key: 'side', label: 'Side', render: (r) => <Badge tone={r.side === 'buy' ? 'green' : 'red'}>{r.side}</Badge> },
      { key: 'qty_btc', label: 'Size (BTC)', render: (r) => fmtBtc(r.qty_btc) },
      { key: 'price', label: 'Price', render: (r) => fmt(r.price, 2) },
      { key: 'fee', label: 'Fee', render: (r) => fmt(r.fee, 4) },
      { key: 'role', label: 'Role', render: (r) => <span className="uppercase text-gray-400">{String(r.role || '—').toUpperCase()}</span> },
      { key: 'realized_pnl', label: 'Realised PnL', render: (r) => (
        <span className={pnlClass(r.realized_pnl)}>{signed(r.realized_pnl, 2)}</span>) },
      { key: 'trade_id', label: 'Trade ID', render: (r) => <span className="text-gray-500">{r.trade_id}</span> },
    ],
    order_history: [
      { key: 'created_at', label: 'Time', render: (r) => shortTime(r.created_at) },
      { key: 'symbol', label: 'Contract', render: (r) => r.symbol },
      { key: 'type', label: 'Type', render: (r) => <span className="uppercase">{String(r.type || '').replace(/_/g, ' ').toUpperCase()}</span> },
      { key: 'side', label: 'Side', render: (r) => <Badge tone={r.side === 'buy' ? 'green' : 'red'}>{r.side}</Badge> },
      { key: 'qty_btc', label: 'Size (BTC)', render: (r) => fmtBtc(r.qty_btc) },
      { key: 'price', label: 'Price', render: (r) => fmt(r.price, 2) },
      { key: 'avg_fill_price', label: 'Avg fill', render: (r) => fmt(r.avg_fill_price, 2) },
      { key: 'status', label: 'Status', render: (r) => (
        <Badge tone={r.status === 'filled' ? 'green' : r.status === 'cancelled' ? 'gray' : 'blue'}>{r.status}</Badge>) },
      { key: 'order_id', label: 'Order ID', render: (r) => <span className="text-gray-500">{r.order_id}</span> },
    ],
  };

  const emptyLabels = {
    positions: 'No open positions.',
    open_orders: 'No working orders.',
    stop_orders: 'No stop-loss or take-profit orders.',
    fills: 'No fills yet.',
    order_history: 'No order history returned by the exchange.',
  };

  return (
    <div className="space-y-6" data-testid="live-terminal">
      {/* ---------------- top strip: mark price, wallet, risk -------------- */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-4">
        <div className="rounded-2xl border border-gray-700 bg-gray-800 p-4">
          <div className="flex items-center justify-between">
            <h3 className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-gray-400">
              <TrendingUp size={14} /> {data.symbol || perpetualFor(broker)}
            </h3>
            <button onClick={refresh} disabled={loading}
                    className="flex items-center gap-1 rounded border border-gray-700 px-2 py-1 text-[10px] font-bold text-gray-400 transition hover:text-white">
              <RefreshCw size={11} className={loading ? 'animate-spin' : ''} /> Refresh
            </button>
          </div>
          <div className="mt-2 font-mono text-2xl font-bold text-white">{fmt(data.mark_price, 2)}</div>
          <div className="text-[10px] uppercase tracking-wider text-gray-500">
            Mark price · {contract.contract_type || 'perpetual'}
            {contract.contract_value ? ` · 1 ${unit.slice(0, -1)} = ${contract.contract_value} BTC` : ''}
          </div>
          {lastRefresh ? <div className="mt-1 text-[10px] text-gray-600">Updated {lastRefresh} UTC</div> : null}
        </div>

        <div className="rounded-2xl border border-gray-700 bg-gray-800 p-4">
          <h3 className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-gray-400">
            <Wallet size={14} /> Wallet &amp; Margin
          </h3>
          <div className="grid grid-cols-2 gap-2">
            <Stat label="Balance" value={fmt(balance.wallet_balance, 2) + ' ' + (balance.asset || '')} />
            <Stat label="Available" value={fmt(balance.available_balance, 2)} tone="text-green-400" />
            <Stat label="Used margin" value={fmt(risk.used_margin, 2)} tone="text-amber-400" />
            <Stat label="Order margin" value={fmt(risk.order_margin, 2)} />
            <Stat label="uPnL" value={signed(risk.unrealized_pnl, 2)} tone={pnlClass(risk.unrealized_pnl)} />
            <Stat label="Equity" value={fmt(risk.equity, 2)} />
          </div>
        </div>

        <div className="rounded-2xl border border-gray-700 bg-gray-800 p-4">
          <h3 className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-gray-400">
            <Gauge size={14} /> Risk
          </h3>
          <div className="grid grid-cols-2 gap-2">
            <Stat label="Margin used" value={`${fmt(risk.margin_utilisation_pct, 2)}%`}
                  tone={Number(risk.margin_utilisation_pct) > 80 ? 'text-red-400' : 'text-white'} />
            <Stat label="Effective lev" value={`${fmt(risk.effective_leverage, 2)}x`} />
            <Stat label="Long exposure" value={fmt(risk.long_exposure, 2)} tone="text-green-400" />
            <Stat label="Short exposure" value={fmt(risk.short_exposure, 2)} tone="text-red-400" />
            <Stat label="Net exposure" value={fmt(risk.net_notional, 2)} />
            <Stat label="Open positions" value={fmt(risk.position_count, 0, '0')} />
          </div>
        </div>

        <div className="rounded-2xl border border-gray-700 bg-gray-800 p-4">
          <h3 className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-gray-400">
            <Zap size={14} /> Rate limits
          </h3>
          <div className="grid grid-cols-2 gap-2">
            <Stat label="Per second" value={`${fmt(usage.requests_last_second, 0, '0')} / ${fmt(rate.requests_per_second, 0)}`}
                  tone={Number(usage.requests_last_second) >= Number(rate.requests_per_second || 1) ? 'text-red-400' : 'text-white'}
                  hint="local sliding window" />
            <Stat label="Per minute" value={`${fmt(usage.requests_last_minute, 0, '0')} / ${fmt(rate.requests_per_minute, 0)}`} />
            <Stat label="Orders / min" value={`${fmt(usage.orders_last_minute, 0, '0')} / ${fmt(rate.orders_per_minute, 0)}`} />
            <Stat label="Orders / 10s" value={`${fmt(usage.orders_last_10s, 0, '0')} / ${fmt(rate.orders_per_10s, 0)}`}
                  hint="Binance second order cap" />
            <Stat label="Weight / 5 min" value={`${fmt(usage.weight_used_5min, 0, '0')} / ${fmt(rate.weight_per_5min, 0)}`}
                  hint={usage.exchange_quota != null ? `exchange quota ${fmt(usage.exchange_quota, 0)} left` : 'Delta fixed window'} />
          </div>
          {(usage.retried_calls || 0) > 0 && (
            <div className="mt-2 text-[10px] text-amber-400">
              {`${usage.retried_calls || 0} retried after HTTP 429 · ${usage.rejected_calls || 0} gave up`}
            </div>
          )}
        </div>
      </div>

      {configRequired && (
        <div className="flex items-start gap-2 rounded-2xl border border-amber-800 bg-amber-900/20 p-4 text-sm text-amber-300">
          <ShieldCheck size={16} className="mt-0.5 shrink-0" />
          <div>
            <div className="font-bold">Connect this broker to see the account</div>
            <div className="mt-1 text-xs leading-relaxed">
              The live terminal pulls your positions, orders and fills straight from the exchange.
              Add your API key and secret in Broker Settings, or check that the selected connection is switched on.
            </div>
            {configProblems.length > 0 && (
              <ul className="mt-1 list-inside list-disc text-[11px] text-amber-200/80">
                {configProblems.slice(0, 4).map((p, i) => <li key={i}>{p}</li>)}
              </ul>
            )}
            <a href="/broker" className="mt-2 inline-flex items-center gap-1 rounded-lg border border-amber-700 bg-amber-900/30 px-3 py-1.5 text-[11px] font-bold text-amber-200 transition hover:bg-amber-900/50">
              Open Broker Settings
            </a>
          </div>
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 rounded-2xl border border-red-900 bg-red-900/20 p-4 text-sm text-red-300">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <div>
            <div className="font-bold">Could not load the live account</div>
            <div className="text-xs">{error}</div>
          </div>
        </div>
      )}

      {data.auth_error ? (
        <div className="flex items-start gap-2 rounded-2xl border border-red-900 bg-red-900/20 p-4 text-sm text-red-300">
          <ShieldAlert size={16} className="mt-0.5 shrink-0" />
          <div>
            <div className="font-bold">API key rejected by the exchange</div>
            <div className="mt-1 text-xs leading-relaxed">{data.auth_error}</div>
            {/* The quota line is the proof this is the key and not the account:
                the weight was spent on calls the venue never even authorised. */}
            {(data.rate_limits?.credential_health?.state === 'rejected') && (
              <div className="mt-1 text-[10px] text-red-400/90">
                Signed calls are being held for {Math.round(data.rate_limits.credential_health.retry_in_seconds || 0)}s
                · {data.rate_limits.credential_health.error}
              </div>
            )}
            <div className="mt-2 flex items-center gap-2">
              <a href="/broker" className="inline-flex items-center gap-1 rounded-lg border border-red-700 bg-red-900/30 px-3 py-1.5 text-[11px] font-bold text-red-200 transition hover:bg-red-900/50">
                Replace the key in Broker Settings
              </a>
              <a href="/live" className="inline-flex items-center gap-1 rounded-lg border border-gray-700 px-3 py-1.5 text-[11px] font-bold text-gray-300 transition hover:border-blue-500 hover:text-white">
                Reload keys on the instance
              </a>
            </div>
          </div>
        </div>
      ) : Object.keys(data.errors || {}).length > 0 && (
        <div className="rounded-2xl border border-amber-900 bg-amber-900/10 p-3 text-[11px] text-amber-300">
          Partial data: {Object.entries(data.errors).map(([k, v]) => `${k} (${v})`).join(' · ')}
        </div>
      )}

      {/* ---------------- order ticket + account controls ---------------- */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-4">
        <OrderTicket
          broker={broker}
          symbol={data.symbol || perpetualFor(broker)}
          contract={contract}
          markPrice={data.mark_price}
          disabled={!broker}
          busy={busy}
          onSubmit={placeOrder}
          notice={notice}
        />

        <div className="space-y-4">
          <div className="rounded-2xl border border-gray-700 bg-gray-800 p-4">
            <h3 className="mb-3 flex items-center gap-2 text-sm font-bold uppercase tracking-wider text-gray-400">
              <Percent size={15} /> Leverage &amp; margin
            </h3>
            <div className="flex items-end gap-2">
              <div className="flex-1">
                <Field label="Leverage">
                  <input type="number" min="1" max="125" value={leverage}
                         onChange={(e) => { touched.current.leverage = true; setLeverage(e.target.value); }}
                         className="w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 font-mono text-sm text-white outline-none focus:border-blue-500" />
                </Field>
              </div>
              <button onClick={applyLeverage} disabled={busy}
                      className="rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-xs font-bold text-gray-300 transition hover:border-blue-500 hover:text-white">
                Apply
              </button>
            </div>
            <div className="mt-3 flex items-end gap-2">
              <div className="flex-1">
                <Field label="Margin mode">
                  <select value={marginMode} onChange={(e) => { touched.current.margin = true; setMarginMode(e.target.value); }}
                          className="w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-white outline-none focus:border-blue-500">
                    <option value="isolated">Isolated</option>
                    <option value="cross">Cross</option>
                  </select>
                </Field>
              </div>
              <button onClick={applyMarginMode} disabled={busy}
                      className="rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-xs font-bold text-gray-300 transition hover:border-blue-500 hover:text-white">
                Apply
              </button>
            </div>
            <p className="mt-2 text-[10px] leading-relaxed text-gray-500">
              {account && !account.error && (account.margin_mode || account.leverage)
                ? <>Read from your exchange account: <b className="text-gray-300">{account.margin_mode || '—'}</b>
                   {account.leverage ? <> · <b className="text-gray-300">{account.leverage}x</b></> : null}
                   {account.accounts && account.accounts.length > 1
                     ? <> · {account.accounts.length} accounts on this key</> : null}
                   {account.margin_mode === 'portfolio' ? ' (portfolio margin)' : ''}</>
                : account && account.error
                  ? <span className="text-amber-500">Account settings unavailable: {String(account.error).slice(0, 120)}</span>
                  : 'Margin mode and leverage load from the exchange once the account can be read.'}
            </p>
          </div>

          <div className="rounded-2xl border border-gray-700 bg-gray-800 p-4">
            <h3 className="mb-3 flex items-center gap-2 text-sm font-bold uppercase tracking-wider text-gray-400">
              <BellRing size={15} /> Unfilled alert
            </h3>
            <Field label="Flag a working order after"
                   hint="Applies to open entries only — stop / take-profit legs are meant to rest until they trigger.">
              <select value={unfillAfter} onChange={(e) => setUnfillAfter(Number(e.target.value))}
                      className="w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-white outline-none focus:border-blue-500">
                {UNFILL_THRESHOLDS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </Field>
            <p className="mt-2 text-[11px] text-gray-500">
              {unfillAfter > 0
                ? `${unfilled.length} order${unfilled.length === 1 ? '' : 's'} unfilled for more than ${ageLabel(unfillAfter)}.`
                : 'Alert is off — the Open Orders table still shows how long each order has been resting.'}
            </p>
          </div>

          <div className="rounded-2xl border border-gray-700 bg-gray-800 p-4">
            <h3 className="mb-3 flex items-center gap-2 text-sm font-bold uppercase tracking-wider text-gray-400">
              <ShieldAlert size={15} /> Risk actions
            </h3>
            <div className="space-y-2">
              <button onClick={cancelAll} disabled={busy}
                      className="flex w-full items-center justify-center gap-2 rounded-lg border border-amber-800 bg-amber-900/20 px-3 py-2 text-xs font-bold text-amber-300 transition hover:bg-amber-900/40">
                <Ban size={14} /> Cancel all open orders
              </button>
              <button onClick={() => closePosition(null)} disabled={busy}
                      className="flex w-full items-center justify-center gap-2 rounded-lg border border-red-900 bg-red-900/20 px-3 py-2 text-xs font-bold text-red-300 transition hover:bg-red-900/40">
                <XCircle size={14} /> Close position at market
              </button>
              <p className="text-[10px] leading-relaxed text-gray-600">
                Closing uses a reduce-only market order. Protection legs left over from a bracket are
                cancelled with it, so a stale stop cannot reopen the other side.
              </p>
            </div>
          </div>
        </div>

        {/* ---------------- tabs ---------------------------------------- */}
        <div className="xl:col-span-2">
          {/* Unfilled alert: a working order that should have filled by now. */}
          {unfilled.length > 0 && (
            <div className="mb-3 rounded-2xl border border-amber-800 bg-amber-900/20 p-3">
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <BellRing size={15} className="text-amber-400" />
                <span className="text-xs font-bold uppercase tracking-wider text-amber-300">
                  {unfilled.length} unfilled order{unfilled.length > 1 ? 's' : ''}
                </span>
                <span className="text-[11px] text-amber-200/70">
                  resting longer than {ageLabel(unfillAfter)}
                </span>
                <button onClick={() => setTab('open_orders')}
                        className="ml-auto rounded border border-amber-800 px-2 py-1 text-[10px] font-bold text-amber-300 transition hover:bg-amber-900/40">
                  Open Orders
                </button>
              </div>
              <ul className="space-y-0.5">
                {unfilled.slice(0, 4).map((o) => (
                  <li key={o.order_id || o.client_order_id} className="font-mono text-[11px] text-amber-200/80">
                    {String(o.side || '').toUpperCase()} {o.symbol} · {String(o.type || '').replace(/_/g, ' ')} @ {fmt(o.price, 2)}
                    {' '}· {fmtSize(o.unfilled_size, unit)} left · waiting {ageLabel(o.age_seconds)}
                  </li>
                ))}
                {unfilled.length > 4 && (
                  <li className="text-[11px] text-amber-200/60">+{unfilled.length - 4} more…</li>
                )}
              </ul>
            </div>
          )}
          <div className="rounded-2xl border border-gray-700 bg-gray-800">
            <div className="flex flex-wrap gap-1 border-b border-gray-700 p-2">
              {TABS.map(({ key, label, icon: Icon }) => (
                <button key={key} onClick={() => setTab(key)}
                        className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-bold transition ${
                          tab === key ? 'bg-gray-700 text-white' : 'text-gray-400 hover:text-white'}`}>
                  <Icon size={13} /> {label}
                  <span className="rounded bg-gray-900 px-1 text-[10px] text-gray-400">{rows[key].length}</span>
                </button>
              ))}
            </div>
            <Table columns={columns[tab]} rows={rows[tab]} empty={emptyLabels[tab]} />
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-4 text-[11px] text-gray-600">
        <span className="flex items-center gap-1"><Clock size={11} /> Auto-refresh every {Math.round(refreshMs / 1000)}s</span>
        <span className="flex items-center gap-1"><DollarSign size={11} /> Sizes shown in BTC and in the venue's own unit</span>
        <span className="flex items-center gap-1"><Trash2 size={11} /> Every call is throttled to the broker's limits</span>
      </div>
    </div>
  );
};

export default LiveTerminal;
