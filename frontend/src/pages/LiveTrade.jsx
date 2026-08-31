import React, { useState, useEffect } from 'react';
import { Play, StopCircle, Activity, ShieldCheck, AlertCircle, TrendingUp, Wallet, CalendarClock, PauseCircle, TerminalSquare, Download, HeartPulse } from 'lucide-react';
import { API_URL } from '../api';
import TradingWindowsEditor from '../components/TradingWindowsEditor';
import EntryGuardBadges from '../components/EntryGuardBadges';
import {
  emptySchedule, normalizeSchedule, isScheduleActive, describeSchedule,
} from '../utils/tradingWindows';

// The tool trades the BTC *perpetual* on every venue: Binance lists it as
// BTCUSDT, Delta as BTCUSD.
const perpetualFor = (source) => (String(source || '').toLowerCase() === 'delta' ? 'BTCUSD' : 'BTCUSDT');

// ---------- Live price feed badge ----------
// Shows how open positions are being re-priced. A websocket that has dropped
// and gone stale must be visible here: otherwise the instance silently falls
// back to the 60-second cadence and a stop that the operator believes is being
// watched continuously is not.
export const FeedBadge = ({ feed }) => {
  if (!feed || !feed.mode || feed.mode === 'off') return null;
  const stale = feed.stale;
  const label = feed.kind === 'websocket' ? 'TICK·WS' : 'TICK·REST';
  const title = stale
    ? `Live feed is STALE — no price for ${feed.age_seconds == null ? 'an unknown' : feed.age_seconds + 's'}. `
      + `Exit checks have fallen back to the 60-second cadence.${feed.last_error ? `\nLast error: ${feed.last_error}` : ''}`
    : `Live price feed (${feed.kind}) — exits re-checked every ${feed.tick_interval}s.`
      + ` Last price ${feed.age_seconds}s ago, ${feed.messages} messages, ${feed.reconnects} reconnects.`
      + `\nEntries still wait for a closed 1h candle.`;
  return (
    <span className={`rounded border px-1.5 py-0.5 text-[9px] font-bold ${
        stale ? 'border-red-800/60 bg-red-900/20 text-red-300'
              : 'border-emerald-800/60 bg-emerald-900/20 text-emerald-300'}`}
          title={title}>
      {stale ? `${label} STALE` : label}
    </span>
  );
};

// Deadman switch badge. Required on Delta Exchange India: if the worker
// stops acknowledging, the exchange cancels open orders. A stale/failed
// heartbeat must be visible — otherwise the operator thinks the safety
// net is up when it is not.
export const HeartbeatBadge = ({ heartbeat }) => {
  if (!heartbeat) return null;
  // Parked on purpose (a rejected API key is the usual reason): the ack cannot
  // land, and saying DEADMAN HELD instead of HEARTBEAT FAIL is the difference
  // between "fix the key" and "the safety net broke". `created` staying true is
  // what keeps the resting stop-loss / take-profit legs protecting the position.
  if (heartbeat.stood_down) {
    return (
      <span className="rounded border border-amber-800/60 bg-amber-900/20 px-1.5 py-0.5 text-[9px] font-bold text-amber-300"
            title={`Deadman switch is parked, not broken${heartbeat.stood_down_reason ? `: ${heartbeat.stood_down_reason}` : ''}. `
                   + 'Acks resume automatically once the venue accepts the key again. '
                   + `${heartbeat.created ? 'It stays registered venue-side, so its cancel_orders action is intact.' : 'Never armed — nothing was created while the key was rejected.'}`}>
        DEADMAN HELD
      </span>
    );
  }
  if (!heartbeat.enabled && !heartbeat.created) return null;
  const stale = heartbeat.stale || heartbeat.failures > 0;
  const label = stale ? 'HEARTBEAT FAIL' : 'DEADMAN ON';
  const title = stale
    ? `Deadman switch is STALE — last ack ${heartbeat.age_seconds == null ? 'never' : heartbeat.age_seconds + 's ago'}.`
      + ` Open orders will be cancelled if the exchange does not hear from us.`
      + `${heartbeat.last_error ? `\nLast error: ${heartbeat.last_error}` : ''}`
    : `Deadman switch alive — ${heartbeat.acks} acks, TTL ${heartbeat.ttl_ms}ms.`
      + ` Missed beat cancels open orders on ${ (heartbeat.product_symbols || []).join(', ') || 'the contract' }`
      + `${heartbeat.skipped_acks ? `. ${heartbeat.skipped_acks} acks skipped while the key was rejected` : ''}.`;
  return (
    <span className={`rounded border px-1.5 py-0.5 text-[9px] font-bold ${
        stale ? 'border-red-800/60 bg-red-900/20 text-red-300'
              : 'border-cyan-800/60 bg-cyan-900/20 text-cyan-300'}`}
          title={title}>
      {label}
    </span>
  );
};

// ---------- Credential badge ----------
// The failure this badge exists for is the quiet one: every signed call answers
// invalid_api_key while the public candles keep streaming, so the instance is
// "running", the chart is moving, and nothing trades. The worker holds entries
// and re-reads its saved connection on a backoff, which is a state the operator
// has to be able to see — and act on without restarting the process.
export const CredentialsBadge = ({ credentials, onReload, busy }) => {
  const creds = credentials || {};
  const rejected = creds.state === 'rejected';
  const suspect = creds.state === 'suspect';
  if (!rejected && !suspect) return null;
  const wait = Number(creds.retry_in_seconds || 0);
  const label = rejected ? 'KEY REJECTED' : 'KEY SUSPECT';
  const title = rejected
    ? `The venue rejects this API key on every signed call (${creds.error || 'no error text'}). `
      + `New entries are held; positions already open keep being marked to market. `
      + `It re-reads the key saved in Broker Settings in ${wait.toFixed(0)}s `
      + `(${creds.entries_held || 0} tick${creds.entries_held === 1 ? '' : 's'} held so far, `
      + `${creds.reloads || 0} reloads).${creds.environment === 'testnet' ? ' This connection points at the TESTNET — a production key 401s here.' : ''}`
    : `One signed call was rejected (${creds.error || ''}) but the account still answers — `
      + 'often a key permission on one endpoint, not a dead key.';
  return (
    <span className="flex items-center gap-1">
      <span className={`rounded border px-1.5 py-0.5 text-[9px] font-bold ${
          rejected ? 'border-red-800/60 bg-red-900/20 text-red-300'
                   : 'border-amber-800/60 bg-amber-900/20 text-amber-300'}`}
            title={title}>
        {label}
      </span>
      {rejected && onReload && (
        <button onClick={onReload} disabled={busy}
                className="rounded border border-red-800/60 bg-red-900/20 px-1.5 py-0.5 text-[9px] font-bold text-red-300 transition hover:bg-red-900/40 disabled:opacity-40"
                title="Re-read the key saved on this connection now instead of waiting for the next retry">
          {busy ? 'Reloading…' : 'Reload keys'}
        </button>
      )}
    </span>
  );
};

// ---------- Confirmation modal ----------
const ConfirmModal = ({ open, title, message, confirmLabel, confirmColor, onCancel, onConfirm }) => {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onCancel}>
      <div className="bg-gray-800 border border-gray-700 rounded-2xl shadow-2xl p-6 max-w-md w-full mx-4" onClick={e => e.stopPropagation()}>
        <h3 className="text-lg font-bold text-white mb-2">{title}</h3>
        <p className="text-sm text-gray-400 mb-6">{message}</p>
        <div className="flex justify-end gap-3">
          <button onClick={onCancel} className="px-4 py-2 rounded-lg text-sm font-semibold text-gray-300 hover:text-white bg-gray-700 hover:bg-gray-600 transition">Cancel</button>
          <button onClick={onConfirm} className={`px-4 py-2 rounded-lg text-sm font-bold text-white transition ${confirmColor}`}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
};

const TradeCard = ({ trade }) => (
  <div className={`p-4 rounded-xl border border-green-500/30 bg-green-500/5 transition hover:scale-[1.01]`}>
    <div className="flex justify-between items-start mb-3">
      <div>
        <div className="text-xs text-gray-500 uppercase font-bold">{trade.symbol}</div>
        <div className={`text-lg font-bold ${trade.direction === 1 ? 'text-green-400' : 'text-red-400'}`}>
          {trade.direction === 1 ? 'LONG' : 'SHORT'}
        </div>
      </div>
      <div className="text-right">
        <div className="text-xs text-gray-500 uppercase font-bold">PnL</div>
        <div className={`text-lg font-mono font-bold ${trade.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
          {trade.pnl >= 0 ? '+' : ''}{trade.pnl.toFixed(2)}
        </div>
      </div>
    </div>
    <div className="grid grid-cols-3 gap-2 text-center text-[10px] uppercase font-medium text-gray-400">
      <div className="bg-gray-800/50 p-1 rounded">Entry: <span className="text-white">{trade.entry.toFixed(2)}</span></div>
      <div className="bg-gray-800/50 p-1 rounded">Current: <span className="text-white">{trade.current.toFixed(2)}</span></div>
      <div className="bg-gray-800/50 p-1 rounded">Margin: <span className="text-white">₹{trade.margin.toFixed(0)}</span></div>
    </div>
  </div>
);

const LiveTrade = () => {
  const [status, setStatus] = useState([]);
  const [selectedStrategy, setSelectedStrategy] = useState('PhantomV2');
  const [loading, setLoading] = useState(false);
  const [strategies, setStrategies] = useState([]);
  const [dataSource, setDataSource] = useState('Binance');
  const [sources, setSources] = useState([{ code: 'Binance', name: 'Binance Futures' }, { code: 'Delta', name: 'Delta Exchange' }]);
  const [connections, setConnections] = useState([]);
  const [connectionId, setConnectionId] = useState('');
  const [capital, setCapital] = useState(20000);
  const [marginPct, setMarginPct] = useState(25);
  const [confirm, setConfirm] = useState(null); // { instanceKey }
  // BTC perpetual pricing + "skip new trades" schedule for new instances.
  const [useMarkPrice, setUseMarkPrice] = useState(true);
  // Live price feed for exit checks. "off" keeps the original 60-second cadence;
  // the others re-check open positions on every live price so a stop is acted
  // on in seconds instead of up to a minute late. Entries still wait for a
  // closed 1h candle either way.
  const [priceFeed, setPriceFeed] = useState('off');
  const [tickInterval, setTickInterval] = useState(5);
  const [tradingWindows, setTradingWindows] = useState(() => emptySchedule());
  const [showWindows, setShowWindows] = useState(false);
  // Deadman switch: ON by default for Delta (must-have), OFF for others.
  const [heartbeat, setHeartbeat] = useState(false);

  useEffect(() => {
    fetch(`${API_URL}/broker-definitions`, { headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } }).then(r => r.ok ? r.json() : []).then(list => {
      if (Array.isArray(list) && list.length) setSources(list.map(x => ({ code: x.code, name: x.name })));
    }).catch(() => {});
    fetch(`${API_URL}/broker-connections`, { headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } }).then(r => r.ok ? r.json() : []).then(setConnections).catch(() => {});
    fetch(`${API_URL}/broker-settings`, { headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } }).then(r => r.ok ? r.json() : null).then(data => {
      if (data) {
        const broker = data.broker_name || 'Binance';
        setDataSource(broker);
        setHeartbeat(String(broker).toLowerCase() === 'delta');
        setCapital(data.initial_capital || 20000);
        setMarginPct(data.margin_deployment_pct || 25);
        if (data.use_mark_price !== undefined && data.use_mark_price !== null) setUseMarkPrice(!!data.use_mark_price);
        if (data.trading_windows) setTradingWindows(normalizeSchedule(data.trading_windows));
      }
    }).catch(() => {});
    // Account-level defaults for the mark-price switch and the schedule.
    fetch(`${API_URL}/trading-windows`, { headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } })
      .then(r => (r.ok ? r.json() : null))
      .then(data => {
        if (!data) return;
        setTradingWindows(normalizeSchedule(data));
        if (data.use_mark_price !== undefined && data.use_mark_price !== null) setUseMarkPrice(!!data.use_mark_price);
      })
      .catch(() => {});
    fetch(`${API_URL}/strategies`, { headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } })
      .then(res => res.json())
      .then(data => setStrategies(data));
  }, []);

  const startTrade = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/live-trade/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${localStorage.getItem('token')}` },
        body: JSON.stringify({ strategy_id: selectedStrategy, broker_name: dataSource, data_source: dataSource,
          connection_id: connectionId ? Number(connectionId) : null, initial_capital: Number(capital), margin_pct: Number(marginPct),
          use_mark_price: useMarkPrice, trading_windows: tradingWindows,
          price_feed: priceFeed, tick_interval: Number(tickInterval),
          heartbeat })
      });
      if (!res.ok) { const err = await res.json().catch(() => ({})); alert(err.detail || 'Could not start live trade'); }
    } catch (e) { console.error(e); alert(e.message); }
    setLoading(false);
  };

  const requestStop = (instanceKey) => setConfirm({ instanceKey });

  // Reload one instance's saved broker credentials without restarting it. The
  // worker re-reads its connection row, swaps the client, and resumes on the
  // next tick; this just skips the backoff wait after a key is fixed.
  const [reloading, setReloading] = useState(null);
  const [reloadNote, setReloadNote] = useState(null);
  const reloadKeys = async (instanceKey) => {
    setReloading(instanceKey); setReloadNote(null);
    try {
      const res = await fetch(`${API_URL}/live-trade/reload-credentials`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ instance_key: instanceKey }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Could not reload credentials');
      const state = (data.credentials || {}).state || 'unknown';
      setReloadNote({ ok: state === 'ok',
                      text: state === 'ok'
                        ? `${instanceKey.split('_').pop()}: key accepted again — entries resume on the next tick.`
                        : `${instanceKey.split('_').pop()}: still rejected (${data.credentials?.error || data.reason || 'no error'}). `
                          + 'Replace the key on the connection in Broker Settings.' });
      fetchStatus();
    } catch (e) { setReloadNote({ ok: false, text: e.message }); }
    setReloading(null);
  };

  const stopTrade = async () => {
    if (!confirm) return;
    try {
      await fetch(`${API_URL}/live-trade/stop?instance_key=${encodeURIComponent(confirm.instanceKey)}`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` },
      });
      setConfirm(null);
    } catch (e) { console.error(e); }
  };

  const fetchStatus = async () => {
    try {
      const res = await fetch(`${API_URL}/live-trade/status`, { headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } });
      const data = await res.json();
      setStatus(data);
    } catch (e) {}
  };

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  // Every broker/strategy worker is independent; show all of them so an
  // operator can monitor Binance and Delta concurrently.
  const myInstances = status;
  const activeTrades = myInstances.flatMap(inst => (inst.active_trades || []).map(t => ({...t, instance_key: inst.instance_key})));
  const marginUsed = activeTrades.reduce((sum, t) => sum + t.margin, 0);

  return (
    <div className="page-shell">
      <ConfirmModal
        open={!!confirm}
        title="Stop Live Trade Instance?"
        message={`This will stop instance "${confirm?.instanceKey?.split('_').pop()}" and attempt to close any open positions. Confirmation required.`}
        confirmLabel="Yes, Stop"
        confirmColor="bg-red-600 hover:bg-red-500"
        onCancel={() => setConfirm(null)}
        onConfirm={stopTrade}
      />
      <div className="flex flex-col xl:flex-row xl:justify-between xl:items-start gap-4 mb-8">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold flex items-center gap-3 text-green-400">
            <ShieldCheck size={28} /> Live Trading
          </h1>
          <p className="text-gray-400 text-sm mt-1">Executing real trades on your broker account</p>
        </div>
        <div className="flex gap-3 items-end flex-wrap">
          <div className="flex flex-col">
            <label className="text-xs text-gray-500 uppercase font-bold mb-1">Broker / Data</label>
            <select value={dataSource} onChange={e => {
                const next = e.target.value;
                setDataSource(next);
                setConnectionId('');
                setHeartbeat(String(next).toLowerCase() === 'delta');
              }} className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm outline-none">
              {sources.map(s => <option key={s.code} value={s.code}>{s.name}</option>)}
            </select>
          </div>
          <div className="flex flex-col">
            <label className="text-xs text-gray-500 uppercase font-bold mb-1">Connection</label>
            <select value={connectionId} onChange={e => setConnectionId(e.target.value)} className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm outline-none">
              <option value="">Primary / legacy</option>
              {connections.filter(c => c.broker_code === dataSource).map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select>
          </div>
          <div className="flex flex-col">
            <label className="text-xs text-gray-500 uppercase font-bold mb-1">Capital (₹)</label>
            <input type="number" value={capital} onChange={e => setCapital(e.target.value)} className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm w-28" />
          </div>
          <div className="flex flex-col">
            <label className="text-xs text-gray-500 uppercase font-bold mb-1">Margin %</label>
            <input type="number" value={marginPct} onChange={e => setMarginPct(e.target.value)} className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm w-20" />
          </div>
          <div className="flex flex-col">
            <label className="text-xs text-gray-500 uppercase font-bold mb-1">Active Strategy</label>
            <select value={selectedStrategy} onChange={e => setSelectedStrategy(e.target.value)}
                    className="bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-green-500">
              <option value="PhantomV2">Kudos V2.5 (Default)</option>
              <option value="FastTest">Fast Test Strategy (Quick Signals)</option>
              {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
          <div className="flex flex-col">
            <label className="text-xs text-gray-500 uppercase font-bold mb-1">Pricing &amp; windows</label>
            <button onClick={() => setShowWindows(!showWindows)}
                    className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm outline-none transition ${
                      showWindows || isScheduleActive(tradingWindows) || !useMarkPrice
                        ? 'border-amber-600 bg-amber-900/20 text-amber-300'
                        : 'border-gray-700 bg-gray-800 text-gray-300 hover:border-gray-600'}`}>
              <CalendarClock size={15} />
              {isScheduleActive(tradingWindows) ? 'Windows ON' : 'Windows OFF'}
              <span className="text-[10px] opacity-70">{useMarkPrice ? '· MARK' : '· TRADE'}</span>
            </button>
          </div>
          <div className="flex flex-col">
            <label className="text-xs text-gray-500 uppercase font-bold mb-1">Exit checks</label>
            <select value={priceFeed} onChange={e => setPriceFeed(e.target.value)}
                    className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm outline-none"
                    title="How often open positions are re-checked against the live price. Entries always wait for a closed 1h candle.">
              <option value="off">Every 60s (default)</option>
              <option value="websocket">Live ticks · WebSocket</option>
              <option value="rest">Live ticks · polling</option>
            </select>
          </div>
          {priceFeed !== 'off' && (
            <div className="flex flex-col">
              <label className="text-xs text-gray-500 uppercase font-bold mb-1">Tick interval (s)</label>
              <input type="number" min="1" max="60" step="1" value={tickInterval}
                     onChange={e => setTickInterval(e.target.value)}
                     className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm w-24 outline-none" />
            </div>
          )}
          {String(dataSource).toLowerCase() === 'delta' && (
            <label className="flex items-center gap-2 rounded-lg border border-cyan-800/60 bg-cyan-900/10 px-3 py-2 text-xs text-cyan-200"
                   title="If the worker crashes or disconnects, Delta cancels open orders. Required by the live-trading safety spec.">
              <input type="checkbox" checked={heartbeat}
                     onChange={e => setHeartbeat(e.target.checked)}
                     className="h-3.5 w-3.5 accent-cyan-500" />
              <HeartPulse size={14} /> Deadman
            </label>
          )}
          <button onClick={async () => {
              try {
                const token = localStorage.getItem('token');
                const url = `${API_URL}/live-account/fills/export?broker=${encodeURIComponent(dataSource)}&format=kudos`;
                const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
                if (!res.ok) { const err = await res.json().catch(() => ({})); alert(err.detail || 'Could not export fills'); return; }
                const blob = await res.blob();
                const href = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = href;
                a.download = `kudos_${String(dataSource).toLowerCase()}_trades.csv`;
                a.click();
                URL.revokeObjectURL(href);
              } catch (e) { alert(e.message); }
            }}
            className="px-4 py-2 rounded-lg font-bold transition border border-gray-700 bg-gray-800 text-gray-300 hover:border-green-500 hover:text-white flex items-center gap-2 text-sm"
            title="Download live fills as a Kudos/backtest-style CSV">
            <Download size={16} /> Export fills
          </button>
          <a href="/terminal"
             className="px-4 py-2 rounded-lg font-bold transition border border-gray-700 bg-gray-800 text-gray-300 hover:border-blue-500 hover:text-white flex items-center gap-2 text-sm">
            <TerminalSquare size={16} /> Terminal
          </a>
          <button onClick={startTrade} disabled={loading}
                  className="px-6 py-2 rounded-lg font-bold transition bg-green-600 hover:bg-green-500 flex items-center gap-2">
            <Play size={18} /> Start Instance
          </button>
        </div>
      </div>

      {/* Pricing basis + "skip new trades" schedule for new instances */}
      {showWindows && (
        <div className="mb-8 grid grid-cols-1 gap-4 xl:grid-cols-3">
          <div className="rounded-xl border border-gray-700 bg-gray-800 p-4">
            <div className="mb-2 text-xs font-bold uppercase tracking-wider text-gray-400">
              BTC perpetual pricing
            </div>
            <div className="mb-2 rounded-lg border border-gray-700 bg-gray-900 px-2.5 py-1.5 font-mono text-xs text-white">
              {perpetualFor(dataSource)} <span className="text-[10px] text-gray-500">perpetual</span>
            </div>
            <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-gray-700 bg-gray-900 p-2.5 text-[11px] text-gray-300">
              <input type="checkbox" checked={useMarkPrice}
                     onChange={e => setUseMarkPrice(e.target.checked)}
                     className="mt-0.5 h-3.5 w-3.5 accent-amber-500" />
              <span>
                <span className="block font-bold text-white">Use mark price</span>
                <span className="mt-0.5 block text-gray-500">
                  Stops, targets, trailing and PnL run on the exchange mark price.
                  The traded fill price is stored on every trade too.
                </span>
              </span>
            </label>
            <button onClick={async () => {
                try {
                  await fetch(`${API_URL}/trading-windows`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${localStorage.getItem('token')}` },
                    body: JSON.stringify(tradingWindows),
                  });
                } catch (e) { /* saving the default is a convenience only */ }
              }}
                    className="mt-2 w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-1.5 text-[11px] font-semibold text-gray-300 transition hover:border-blue-500 hover:text-white">
              Save as my account default
            </button>
          </div>
          <div className="xl:col-span-2">
            <TradingWindowsEditor
              value={tradingWindows}
              onChange={setTradingWindows}
              title="Skip new trades"
              subtitle="New live instances started from this page use this schedule. Positions already open keep their stop, target and trail inside a window."
            />
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <div className="lg:col-span-1 space-y-6">
          <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
            <h3 className="text-sm font-bold text-gray-400 uppercase mb-4 flex items-center gap-2">
              <Wallet size={16} /> Broker Balance
            </h3>
            <div className="space-y-4">
              <div className="flex justify-between items-center">
                <span className="text-gray-500 text-xs">Total Equity</span>
                <span className="font-mono font-bold">Connecting...</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-gray-500 text-xs">Used Margin</span>
                <span className="font-mono font-bold text-green-400">₹{marginUsed.toLocaleString(undefined, {minimumFractionDigits: 2})}</span>
              </div>
            </div>
          </div>
          <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
            <h3 className="text-sm font-bold text-gray-400 uppercase mb-4 flex items-center gap-2">
              <AlertCircle size={16} /> Live Instances
            </h3>
            <div className="space-y-3">
              {myInstances.map(inst => (
                <div key={inst.instance_key} className="flex items-center justify-between p-3 bg-gray-900 rounded-lg border border-gray-700">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div>
                      <span className="text-xs font-mono">{inst.broker_name || 'Binance'} · {inst.instance_key.split('_').pop()}</span>
                      {inst.account_label && inst.account_label !== 'Primary' && (
                        <span className="max-w-[140px] truncate rounded border border-blue-800/60 bg-blue-900/20 px-1.5 py-0.5 text-[9px] font-bold text-blue-300"
                              title={`Trading on the broker connection '${inst.account_label}'`}>
                          {inst.account_label}
                        </span>
                      )}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-1">
                      <span className={`rounded border px-1.5 py-0.5 text-[9px] font-bold ${
                        inst.mark_price_basis ? 'border-amber-700/60 bg-amber-900/20 text-amber-300'
                                              : 'border-gray-700 bg-gray-900 text-gray-400'}`}
                            title={inst.mark_price_basis ? 'Priced on the exchange mark price' : 'Priced on the traded price'}>
                        {inst.mark_price_basis ? 'MARK' : 'TRADE'}
                      </span>
                      {inst.entry_paused && (
                        <span className="flex items-center gap-1 rounded border border-amber-700/60 bg-amber-900/20 px-1.5 py-0.5 text-[9px] font-bold text-amber-300">
                          <PauseCircle size={9} /> ENTRIES PAUSED
                        </span>
                      )}
                      {isScheduleActive(inst.trading_windows) && !inst.entry_paused && (
                        <span className="max-w-[150px] truncate rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 text-[9px] text-gray-400"
                              title={describeSchedule(inst.trading_windows).join(' · ')}>
                          ⏱ {describeSchedule(inst.trading_windows).join(' · ')}
                        </span>
                      )}
                      <EntryGuardBadges
                        blocked={inst.blocked_entries || 0}
                        held={inst.skipped_entries || 0}
                        reason={inst.last_skip_reason}
                        position={inst.exchange_position}
                        broker={inst.broker_name || 'the broker'}
                        shared={inst.shared_account}
                      />
                      <FeedBadge feed={inst.price_feed} />
                      <CredentialsBadge credentials={inst.credentials}
                                          busy={reloading === inst.instance_key}
                                          onReload={() => reloadKeys(inst.instance_key)} />
                    </div>
                    {inst.credentials && inst.credentials.state === 'rejected' && (
                      <div className="mt-1 text-[9px] leading-snug text-red-300/90">
                        {inst.credentials.entries_held || 0} entries held · deadman switch stood down ·
                        re-reads the saved key in {Math.round(inst.credentials.retry_in_seconds || 0)}s —
                        fix it in <a href="/broker" className="underline">Broker Settings</a>
                      </div>
                    )}
                  </div>
                  <button onClick={() => requestStop(inst.instance_key)} className="text-red-400 hover:text-red-300 p-1" title="Stop instance">
                    <StopCircle size={16} />
                  </button>
                </div>
              ))}
              {myInstances.length === 0 && <p className="text-xs text-gray-500 text-center">No active instances for this strategy.</p>}
            </div>
            {reloadNote && (
              <p className={`mt-2 text-[10px] font-semibold ${reloadNote.ok ? 'text-green-400' : 'text-red-400'}`}>
                {reloadNote.text}
              </p>
            )}
          </div>
        </div>

        <div className="lg:col-span-3">
          <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700 min-h-[600px]">
            <div className="flex justify-between items-center mb-6">
              <h3 className="text-lg font-bold flex items-center gap-2">
                <ShieldCheck size={20} className="text-green-400" /> Live Positions
              </h3>
            </div>
            {activeTrades.length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {activeTrades.map((t, i) => <TradeCard key={i} trade={t} />)}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-[400px] text-gray-600">
                <TrendingUp size={48} className="mb-4 opacity-20" />
                <p>No live positions open. Scanning {dataSource} for institutional entries...</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default LiveTrade;
