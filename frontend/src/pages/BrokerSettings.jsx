import React, { useEffect, useState } from 'react';
import { API_URL } from '../api';
import { KeyRound, Plus, Trash2, ShieldCheck, RefreshCw, Wallet, Plug } from 'lucide-react';
import ConnectionCheck from '../components/ConnectionCheck';

const auth = () => ({ Authorization: `Bearer ${localStorage.getItem('token')}`, 'Content-Type': 'application/json' });
const field = 'w-full bg-gray-900 p-3 rounded-lg border border-gray-700 text-white text-sm outline-none focus:border-blue-500';
const lbl = 'block text-xs text-gray-400 mb-1';
const card = 'bg-gray-800 p-6 rounded-2xl border border-gray-700';

/* ----------------------------------------------------- Exchange registry -- */
/* Admin-only: the named-source registry (formerly the Admin > Broker        */
/* Integrations tab). Merged here so all broker configuration lives in one   */
/* place.                                                                    */
const ExchangeRegistry = () => {
  const blank = { code: '', name: '', kind: 'generic', market_data_url: '', trading_api_url: '', notes: '' };
  const [form, setForm] = useState(blank);
  const [rows, setRows] = useState([]);
  const [msg, setMsg] = useState(null);

  const load = () => fetch(`${API_URL}/admin/brokers`, { headers: auth() }).then(r => r.json()).then(setRows).catch(() => {});
  useEffect(() => { load(); }, []);

  const add = async (e) => {
    e.preventDefault();
    const res = await fetch(`${API_URL}/admin/brokers`, { method: 'POST', headers: auth(), body: JSON.stringify(form) });
    const data = await res.json();
    if (!res.ok) setMsg({ ok: false, text: data.detail || 'Could not add integration' });
    else { setMsg({ ok: true, text: `${form.name} added.` }); setForm(blank); load(); }
  };

  const toggle = async (row) => {
    const res = await fetch(`${API_URL}/admin/brokers/${row.id}`, { method: 'PUT', headers: auth(), body: JSON.stringify({ ...row, enabled: !row.enabled }) });
    if (res.ok) load();
  };

  const f = 'w-full bg-gray-900 p-2 rounded-lg border border-gray-700 text-white text-sm';

  // ---- Per-broker rate limits + trading defaults --------------------------
  const [editing, setEditing] = useState(null);
  const [limitMsg, setLimitMsg] = useState(null);
  const limitsOf = (row) => ({
    rate_limit_per_second: row.rate_limit_per_second ?? '',
    rate_limit_per_minute: row.rate_limit_per_minute ?? '',
    quota_per_5min: row.quota_per_5min ?? '',
    orders_per_minute: row.orders_per_minute ?? '',
    default_leverage: row.default_leverage ?? '',
    margin_mode: row.margin_mode ?? '',
    contract_value: row.contract_value ?? '',
    tick_size: row.tick_size ?? '',
  });
  const [limits, setLimits] = useState({});
  const num = (value) => (value === '' || value === null || value === undefined ? null : Number(value));

  const startEdit = (row) => { setEditing(row.id); setLimits(limitsOf(row)); setLimitMsg(null); };
  const saveLimits = async (row) => {
    const body = {
      ...row,
      rate_limit_per_second: num(limits.rate_limit_per_second),
      rate_limit_per_minute: num(limits.rate_limit_per_minute),
      quota_per_5min: num(limits.quota_per_5min),
      orders_per_minute: num(limits.orders_per_minute),
      default_leverage: num(limits.default_leverage),
      margin_mode: limits.margin_mode || null,
      contract_value: num(limits.contract_value),
      tick_size: num(limits.tick_size),
    };
    const res = await fetch(`${API_URL}/admin/brokers/${row.id}`, { method: 'PUT', headers: auth(), body: JSON.stringify(body) });
    if (!res.ok) setLimitMsg({ ok: false, text: (await res.json()).detail || 'Could not save limits' });
    else { setLimitMsg({ ok: true, text: `${row.name} limits saved.` }); setEditing(null); load(); }
  };

  return (
    <section className="mt-8">
      <div className="flex items-center gap-2 mb-1">
        <Plug size={18} className="text-green-400" />
        <h2 className="text-lg font-bold text-gray-200">Exchange Registry</h2>
        <span className="text-[10px] font-bold uppercase bg-purple-900/50 text-purple-300 px-2 py-0.5 rounded">Admin</span>
      </div>
      <p className="text-xs text-gray-500 mb-1 max-w-3xl">
        <b className="text-gray-300">The integration itself — not your API keys.</b> The registry names a venue and
        picks the adapter and URLs it talks to. Adding an entry here does <b className="text-gray-300">not</b> let anyone
        trade: each login still needs its own key and secret under <b className="text-gray-300">Add broker connection</b>.
      </p>
      <p className="text-xs text-gray-500 mb-4 max-w-3xl">
        Binance Futures and Delta Exchange are ready-to-use adapters. Register another provider here so it is
        available as a named source for every client. Choose <b className="text-gray-300">Binance-compatible</b> or
        <b className="text-gray-300"> Delta-compatible</b> for automatic daily OHLC refresh; Generic / custom
        sources remain disabled for market-data sync until a runtime adapter is added.
      </p>

      <form onSubmit={add} className={`${card} mb-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 items-end`}>
        <div><label className={lbl}>Code *</label><input required placeholder="Bybit" className={f} value={form.code} onChange={e => setForm({ ...form, code: e.target.value })} /></div>
        <div><label className={lbl}>Display name *</label><input required placeholder="Bybit Futures" className={f} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></div>
        <div><label className={lbl}>Adapter kind</label><select className={f} value={form.kind} onChange={e => setForm({ ...form, kind: e.target.value })}><option value="generic">Generic / custom</option><option value="binance">Binance-compatible</option><option value="delta">Delta-compatible</option></select></div>
        <div><label className={lbl}>Market data URL</label><input className={f} placeholder="https://…" value={form.market_data_url} onChange={e => setForm({ ...form, market_data_url: e.target.value })} /></div>
        <div><label className={lbl}>Trading API URL</label><input className={f} placeholder="https://…" value={form.trading_api_url} onChange={e => setForm({ ...form, trading_api_url: e.target.value })} /></div>
        <div><label className={lbl}>Notes</label><input className={f} value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} /></div>
        <div className="sm:col-span-2 lg:col-span-3">
          <button className="bg-blue-600 hover:bg-blue-500 px-5 py-2 rounded-lg font-bold text-sm w-full sm:w-fit">Add Integration</button>
          {msg && <span className={`ml-3 text-xs font-semibold ${msg.ok ? 'text-green-400' : 'text-red-400'}`}>{msg.text}</span>}
        </div>
      </form>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {rows.map(row => (
          <div key={row.id} className={`p-4 rounded-xl border bg-gray-800 flex flex-col gap-2 ${row.enabled ? 'border-gray-700' : 'border-gray-800 opacity-70'}`}>
            <div className="flex justify-between items-start gap-2">
              <div className="min-w-0">
                <div className="font-bold text-gray-200 text-sm truncate">{row.name} <span className="text-[10px] text-gray-500 font-mono">{row.code}</span></div>
                <div className="text-[11px] text-gray-500 mt-0.5">{row.kind} adapter · {row.is_builtin ? 'built-in' : 'admin configured'}</div>
              </div>
              <button onClick={() => toggle(row)} className={`shrink-0 px-2 py-1 text-[10px] rounded border ${row.enabled ? 'text-green-400 border-green-800 bg-green-900/20' : 'text-gray-500 border-gray-700'}`}>
                {row.enabled ? 'ENABLED' : 'DISABLED'}
              </button>
            </div>
            <div className="text-[10px] text-gray-600 break-all">{row.market_data_url || 'No market endpoint configured'}</div>

            <div className="flex items-center justify-between gap-2 text-[10px] text-gray-500">
              <span>
                {row.rate_limit_per_second ? `${row.rate_limit_per_second} req/s` : 'default req/s'} ·{' '}
                {row.quota_per_5min ? `${row.quota_per_5min} / 5min` : (row.orders_per_minute ? `${row.orders_per_minute} orders/min` : 'venue budget')}
              </span>
              <button onClick={() => (editing === row.id ? setEditing(null) : startEdit(row))}
                      className="rounded border border-gray-700 px-2 py-0.5 font-bold text-gray-400 transition hover:border-blue-500 hover:text-white">
                {editing === row.id ? 'Close' : 'Limits'}
              </button>
            </div>

            {editing === row.id && (
              <div className="mt-1 space-y-2 rounded-lg border border-gray-700 bg-gray-900/60 p-3">
                <div className="text-[10px] text-gray-500">
                  Blank = venue default (Delta 10 000 weight / 5 min, Binance 2 400 weight + 1 200 orders / min).
                  Every request the app sends is throttled against these numbers.
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <label className="block text-[10px] uppercase text-gray-500">Req / second
                    <input type="number" min="0" step="0.5" className={f} value={limits.rate_limit_per_second}
                           onChange={e => setLimits(l => ({ ...l, rate_limit_per_second: e.target.value }))} /></label>
                  <label className="block text-[10px] uppercase text-gray-500">Req / minute
                    <input type="number" min="0" step="1" className={f} value={limits.rate_limit_per_minute}
                           onChange={e => setLimits(l => ({ ...l, rate_limit_per_minute: e.target.value }))} /></label>
                  <label className="block text-[10px] uppercase text-gray-500">Quota / 5 min (Delta)
                    <input type="number" min="0" step="100" className={f} value={limits.quota_per_5min}
                           onChange={e => setLimits(l => ({ ...l, quota_per_5min: e.target.value }))} /></label>
                  <label className="block text-[10px] uppercase text-gray-500">Orders / minute
                    <input type="number" min="0" step="10" className={f} value={limits.orders_per_minute}
                           onChange={e => setLimits(l => ({ ...l, orders_per_minute: e.target.value }))} /></label>
                  <label className="block text-[10px] uppercase text-gray-500">Default leverage
                    <input type="number" min="1" max="125" className={f} value={limits.default_leverage}
                           onChange={e => setLimits(l => ({ ...l, default_leverage: e.target.value }))} /></label>
                  <label className="block text-[10px] uppercase text-gray-500">Margin mode
                    <select className={f} value={limits.margin_mode}
                            onChange={e => setLimits(l => ({ ...l, margin_mode: e.target.value }))}>
                      <option value="">Venue default</option>
                      <option value="isolated">Isolated</option>
                      <option value="cross">Cross</option>
                    </select></label>
                  <label className="block text-[10px] uppercase text-gray-500">Contract value (BTC)
                    <input type="number" min="0" step="0.0001" className={f} value={limits.contract_value}
                           onChange={e => setLimits(l => ({ ...l, contract_value: e.target.value }))} /></label>
                  <label className="block text-[10px] uppercase text-gray-500">Tick size
                    <input type="number" min="0" step="0.01" className={f} value={limits.tick_size}
                           onChange={e => setLimits(l => ({ ...l, tick_size: e.target.value }))} /></label>
                </div>
                <button onClick={() => saveLimits(row)}
                        className="w-full rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-bold text-white transition hover:bg-blue-500">
                  Save limits &amp; defaults
                </button>
              </div>
            )}
          </div>
        ))}
        {rows.length === 0 && <div className="text-xs text-gray-500">No integrations registered yet.</div>}
      </div>
      {limitMsg && <div className={`mt-3 text-xs font-semibold ${limitMsg.ok ? 'text-green-400' : 'text-red-400'}`}>{limitMsg.text}</div>}
    </section>
  );
};

/* ------------------------------------------------------ Connection card -- */
/* Exported for the offline UI test: the page loads its connections in an
   effect, which a server render never runs, so the actions an operator needs
   on a rejected key — Replace keys, Check key — have to be reachable
   directly rather than through the whole page. */
export const ConnectionCard = ({ c, busy, keysOpen, keyForm, setKeyForm, onToggleKeys,
                   onSaveKeys, onProbe, probing, probeResult, onRefresh,
                   onRemove }) => (
  <div className="p-3 bg-gray-900 rounded-lg border border-gray-700">
  <div className="flex justify-between items-center gap-2">
    <div className="min-w-0">
      <div className="font-bold text-sm text-white truncate">{c.label}</div>
      <div className="text-[11px] text-gray-500 truncate">{c.broker_code} · {c.api_key || 'key saved'} {c.is_testnet ? '· testnet' : ''}</div>
    </div>
    <div className="flex items-center gap-1 shrink-0">
      <button onClick={onToggleKeys}
              className="rounded border border-gray-700 px-2 py-1 text-[10px] font-bold text-gray-400 transition hover:border-blue-500 hover:text-white"
              title="Paste a new key/secret, or flip the testnet toggle. Blank secret = keep the stored one.">
        {keysOpen ? 'Close' : 'Replace keys'}
      </button>
      <button onClick={onRefresh} disabled={busy}
              className="text-gray-400 hover:text-white p-2 disabled:opacity-40"
              title="Re-read margin mode, leverage and sub-accounts from the exchange">
        <RefreshCw size={14} className={busy ? 'animate-spin' : ''} />
      </button>
      <button onClick={onRemove} className="text-red-400 hover:text-red-300 p-2 shrink-0" title="Remove"><Trash2 size={15} /></button>
    </div>
  </div>

  {keysOpen && (
    <div className="mt-2 space-y-2 rounded-lg border border-gray-700 bg-gray-800/60 p-3">
      <div className="text-[10px] text-gray-500">
        A key the venue rejects is usually a rotated, half-pasted, or
        wrong-environment key. Paste both fields from the panel of the environment
        you want to trade; leaving <b>API secret</b> empty keeps the stored one.
        Running live instances pick this up on save — no restart.
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <label className="block text-[10px] uppercase text-gray-500">Connection label
          <input className={field} value={keyForm.label}
                 onChange={e => setKeyForm(f => ({ ...f, label: e.target.value }))} /></label>
        <label className="block text-[10px] uppercase text-gray-500">API key
          <input className={field} type="password" placeholder={`${c.api_key || 'current key'} — paste to replace`}
                 onChange={e => setKeyForm(f => ({ ...f, api_key: e.target.value }))} /></label>
        <label className="block text-[10px] uppercase text-gray-500">API secret
          <input className={field} type="password" placeholder="kept as saved unless typed in"
                 onChange={e => setKeyForm(f => ({ ...f, api_secret: e.target.value }))} /></label>
      </div>
      <label className="flex items-center gap-2 text-[11px] text-gray-300">
        <input type="checkbox" className="accent-blue-500" checked={keyForm.is_testnet}
               onChange={e => setKeyForm(f => ({ ...f, is_testnet: e.target.checked }))} />
        This key is for the testnet / demo environment
      </label>
      <button onClick={onSaveKeys} disabled={busy}
              className="w-full rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-bold text-white transition hover:bg-blue-500 disabled:opacity-50">
        Save and hand to running instances
      </button>
    </div>
  )}

  <button onClick={onProbe} disabled={probing}
          className="mt-2 rounded border border-amber-800/60 bg-amber-900/20 px-2 py-1 text-[10px] font-bold text-amber-300 transition hover:bg-amber-900/40 disabled:opacity-40">
    {probing ? 'Checking…' : 'Check key'}
  </button>
  {probeResult && (
    <div className="mt-1.5 rounded-lg border border-gray-700 bg-gray-800/60 p-2 text-[10px] leading-relaxed text-gray-400">
      <div className={`font-bold ${probeResult.accepted ? 'text-green-400' : 'text-red-400'}`}>
        {probeResult.summary}
      </div>
      {(probeResult.rows || []).map(r => (
        <div key={r.name} className="mt-0.5 truncate" title={r.detail}>
          <span className={r.state === 'ok' ? 'text-green-400' : r.state === 'unreachable' ? 'text-gray-500' : 'text-red-400'}>
            {r.state === 'ok' ? 'accepts' : r.state === 'unreachable' ? 'unreachable' : 'rejects'}
          </span>{' '}
          {r.name} <span className="text-gray-600">{r.base_url}</span>
        </div>
      ))}
      {probeResult.fix && <div className="mt-1 text-amber-300">{probeResult.fix}</div>}
    </div>
  )}
  {c.account_settings && !c.account_settings.error && (
    <div className="mt-1.5 text-[10px] text-gray-400 leading-relaxed">
      <span className="font-bold text-green-400">{c.account_settings.margin_mode || 'margin mode unknown'}</span>
      {c.account_settings.leverage ? ` · ${c.account_settings.leverage}x` : ''}
      {c.account_settings.accounts && c.account_settings.accounts.length > 1
        ? ` · ${c.account_settings.accounts.length} accounts on this key` : ''}
      {c.account_settings_at ? ` · verified ${String(c.account_settings_at).slice(0, 16).replace('T', ' ')}` : ''}
    </div>
  )}
  {c.account_settings && c.account_settings.error && (
    <div className="mt-1.5 text-[10px] text-amber-500 leading-relaxed">
      Could not read account details: {String(c.account_settings.error).slice(0, 140)}
    </div>
  )}
  </div>
);

/* ------------------------------------------------------------- Page shell -- */
const BrokerSettings = () => {
  const isAdmin = (localStorage.getItem('role') || '') === 'admin';
  const [definitions, setDefinitions] = useState([]);
  const [connections, setConnections] = useState([]);
  const [capital, setCapital] = useState(20000);
  const [margin, setMargin] = useState(25);
  const [defaultBroker, setDefaultBroker] = useState('Binance');
  const [form, setForm] = useState({ broker_code: 'Binance', label: '', api_key: '', api_secret: '', passphrase: '', is_testnet: false });
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);
  // `GET /broker-connections/diagnose` — what the server sees for this login,
  // so "API keys not configured" can be answered on the page instead of guessed.
  const [check, setCheck] = useState(null);
  const [checkBusy, setCheckBusy] = useState(false);

  const loadCheck = async (broker) => {
    setCheckBusy(true);
    try {
      const res = await fetch(`${API_URL}/broker-connections/diagnose?broker=${encodeURIComponent(broker)}`,
                              { headers: auth() });
      setCheck(res.ok ? await res.json() : null);
    } catch (e) { setCheck(null); }
    setCheckBusy(false);
  };

  const load = async () => {
    let broker = defaultBroker;
    try {
      const [defs, cons, settings] = await Promise.all([
        fetch(`${API_URL}/broker-definitions`, { headers: auth() }).then(r => r.json()),
        fetch(`${API_URL}/broker-connections`, { headers: auth() }).then(r => r.json()),
        fetch(`${API_URL}/broker-settings`, { headers: auth() }).then(r => r.json()),
      ]);
      if (Array.isArray(defs) && defs.length) setDefinitions(defs);
      setConnections(Array.isArray(cons) ? cons : []);
      if (settings) {
        setCapital(settings.initial_capital || 20000);
        setMargin(settings.margin_deployment_pct || 25);
        broker = settings.broker_name || 'Binance';
        setDefaultBroker(broker);
        setForm(f => ({ ...f, broker_code: broker }));
      }
    } catch (e) { setMessage({ ok: false, text: 'Could not load broker settings' }); }
    loadCheck(broker);
  };
  useEffect(() => { load(); }, []);
  useEffect(() => { loadCheck(defaultBroker); }, [defaultBroker]);

  const addConnection = async (e) => {
    e.preventDefault(); setBusy(true); setMessage(null);
    try {
      const res = await fetch(`${API_URL}/broker-connections`, { method: 'POST', headers: auth(), body: JSON.stringify(form) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Unable to save connection');
      const s = data.account_settings || {};
      const read = s.error
        ? ` Could not read account details: ${String(s.error).slice(0, 120)}`
        : ` Account read from the exchange: ${s.margin_mode || 'margin mode unknown'}${s.leverage ? ` · ${s.leverage}x` : ''}.`;
      setMessage({ ok: true, text: `${form.broker_code} connection added.${read} Secrets are stored server-side and masked here.` });
      setForm(f => ({ ...f, label: '', api_key: '', api_secret: '', passphrase: '' }));
      load();
      loadCheck(form.broker_code);
    } catch (e) { setMessage({ ok: false, text: e.message }); }
    setBusy(false);
  };

  /* ---- Replace the keys on a saved connection --------------------------- */
  /* The one action a rejected key needs, and until now the one thing this page
     did not have: `PUT /broker-connections/{id}` existed with "blank means keep"
     semantics, but the only way to change a key was to delete the connection and
     add a new one — which orphans every instance trading on it. Saving here also
     hands the new key to those running instances (the response counts them), so
     fixing a 401 is one form and no restart. */
  const [keysFor, setKeysFor] = useState(null);
  const [keyForm, setKeyForm] = useState({ label: '', api_key: '', api_secret: '', is_testnet: false });
  // Deliberately starts EMPTY rather than prefilled with the masked `ab••••yz`
  // the API returns: sending that back would store the mask as the key — one of
  // the ways a working connection ends up rejected on every signed call.
  const startKeys = (c) => {
    setKeysFor(c.id);
    setKeyForm({ label: c.label || '', api_key: '', api_secret: '', is_testnet: !!c.is_testnet });
  };
  const saveKeys = async (c) => {
    setBusy(true); setMessage(null);
    try {
      const res = await fetch(`${API_URL}/broker-connections/${c.id}`, {
        method: 'PUT', headers: auth(),
        body: JSON.stringify({ broker_code: c.broker_code, label: keyForm.label,
                               api_key: keyForm.api_key, api_secret: keyForm.api_secret,
                               // null = leave the stored passphrase alone; and do not
                               // silently switch a connection back on while editing it.
                               passphrase: null, is_testnet: keyForm.is_testnet,
                               is_active: c.is_active !== false }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Could not update the connection');
      const s = data.account_settings || {};
      const live = data.live_instances || {};
      const picked = live.verified
        ? ` ${live.verified} running instance${live.verified === 1 ? '' : 's'} picked it up and resumed.`
        : (live.notified ? ` ${live.notified} running instance${live.notified === 1 ? '' : 's'} re-read it${s.error ? ' and is still rejected' : ''}.` : '');
      setMessage({
        ok: !s.error,
        text: s.error
          ? `Saved, but the exchange still rejects this key: ${String(s.error).slice(0, 160)}. Use "Check key" to see which environment accepts it.`
          : `${data.label}: account read from the exchange (${s.margin_mode || 'margin mode unknown'}${s.leverage ? `, ${s.leverage}x` : ''}).${picked}`,
      });
      setKeysFor(null);
      load();
    } catch (e) { setMessage({ ok: false, text: e.message }); }
    setBusy(false);
  };

  /* ---- Which environment accepts this key? ------------------------------ */
  const [probing, setProbing] = useState(null);
  const [probeResults, setProbeResults] = useState({});
  const probeConnection = async (id) => {
    setProbing(id); setMessage(null);
    try {
      const res = await fetch(`${API_URL}/broker-connections/${id}/probe`, { method: 'POST', headers: auth() });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Could not reach the exchange to probe');
      setProbeResults(p => ({ ...p, [id]: data }));
    } catch (e) { setMessage({ ok: false, text: `Key check failed: ${e.message}` }); }
    setProbing(null);
  };

  const refreshConnection = async (id) => {
    setBusy(true); setMessage(null);
    try {
      const res = await fetch(`${API_URL}/broker-connections/${id}/refresh`, { method: 'POST', headers: auth() });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Could not read the account');
      const s = data.account_settings || {};
      if (s.error) throw new Error(s.error);
      setMessage({ ok: true, text: `${data.label}: ${s.margin_mode || 'margin mode unknown'}${s.leverage ? ` · ${s.leverage}x` : ''} read from the exchange.` });
      load();
    } catch (e) { setMessage({ ok: false, text: e.message }); }
    setBusy(false);
  };

  const removeConnection = async (id) => {
    if (!window.confirm('Remove this broker connection? Running instances are not changed.')) return;
    const res = await fetch(`${API_URL}/broker-connections/${id}`, { method: 'DELETE', headers: auth() });
    if (res.ok) { load(); loadCheck(defaultBroker); } else setMessage({ ok: false, text: 'Could not remove connection' });
  };

  const saveCapital = async () => {
    setBusy(true); setMessage(null);
    try {
      const res = await fetch(`${API_URL}/broker-settings`, { method: 'POST', headers: auth(), body: JSON.stringify({
        broker_name: defaultBroker, initial_capital: Number(capital), margin_pct: Number(margin),
      }) });
      if (!res.ok) throw new Error((await res.json()).detail || 'Could not save defaults');
      setMessage({ ok: true, text: 'Default broker and trading defaults saved.' });
    } catch (e) { setMessage({ ok: false, text: e.message }); }
    setBusy(false);
  };

  const activeConnections = connections.filter(c => !c.legacy);

  return (
    <div className="page-shell">
      <div className="flex flex-col sm:flex-row sm:justify-between sm:items-start gap-3 mb-8">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold text-blue-400 flex items-center gap-3"><KeyRound size={28} /> Broker & Data Sources</h1>
          <p className="text-gray-400 text-sm mt-1">Connect more than one exchange. Each paper/live instance can use a different source.</p>
        </div>
        <button onClick={load} className="text-gray-400 hover:text-white flex items-center gap-2 text-xs"><RefreshCw size={14} /> Refresh</button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 items-start">
        <div className={card}>
          <h2 className="text-lg font-bold text-gray-200 mb-1 flex items-center gap-2"><Wallet size={18} className="text-yellow-400" /> Trading defaults</h2>
          <p className="text-xs text-gray-500 mb-4">Used as the starting point for new backtests and paper/live instances. You can still override per run.</p>
          <div className="space-y-3">
            <div>
              <label className={lbl}>Default broker</label>
              <select className={field} value={defaultBroker} onChange={e => setDefaultBroker(e.target.value)}>
                {(definitions.length ? definitions : [{ code: 'Binance', name: 'Binance' }]).map(d => <option key={d.code} value={d.code}>{d.name}</option>)}
              </select>
            </div>
            <div>
              <label className={lbl}>Initial capital (₹)</label>
              <input type="number" min="1000" step="1000" className={field} value={capital} onChange={e => setCapital(e.target.value)} />
            </div>
            <div>
              <label className={lbl}>Margin deployment (%)</label>
              <input type="number" min="1" max="100" className={field} value={margin} onChange={e => setMargin(e.target.value)} />
            </div>
          </div>
          <button onClick={saveCapital} disabled={busy} className="mt-4 w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-6 py-2.5 rounded-lg font-bold text-sm">
            {busy ? 'Saving…' : 'Save Defaults'}
          </button>
        </div>

        <div className={card}>
          <h2 className="text-lg font-bold text-gray-200 mb-4 flex items-center gap-2"><Plug size={18} className="text-blue-400" /> Saved connections</h2>
          <div className="space-y-3">
            {activeConnections.map(c => (
              <ConnectionCard key={c.id} c={c} busy={busy}
                              keysOpen={keysFor === c.id} keyForm={keyForm} setKeyForm={setKeyForm}
                              onToggleKeys={() => (keysFor === c.id ? setKeysFor(null) : startKeys(c))}
                              onSaveKeys={() => saveKeys(c)} onProbe={() => probeConnection(c.id)}
                              probing={probing === c.id} probeResult={probeResults[c.id]}
                              onRefresh={() => refreshConnection(c.id)} onRemove={() => removeConnection(c.id)}
              />
            ))}
            {activeConnections.length === 0 && <div className="text-xs text-gray-500">No saved connections yet.</div>}
          </div>
          <div className="mt-4">
            <ConnectionCheck report={check} loading={checkBusy} />
          </div>
          <div className="mt-5 p-3 bg-green-900/10 border border-green-900/30 rounded-lg text-xs text-gray-400 flex gap-2">
            <ShieldCheck size={15} className="text-green-400 shrink-0 mt-0.5" /> Multiple connections can run at the same time from Paper Trade or Live Trade.
          </div>
        </div>

        <form onSubmit={addConnection} className={`${card} md:col-span-2 lg:col-span-1`}>
          <h2 className="text-lg font-bold text-gray-200 mb-1 flex items-center gap-2"><Plus size={18} className="text-green-400" /> Add broker connection</h2>
          <p className="text-xs text-gray-500 mb-1">
            <b className="text-gray-300">Your API keys, for this login.</b> This is what Live Trade and the Terminal
            actually use. Connections are saved per account — keys added while signed in as someone else are not shared.
          </p>
          <p className="text-xs text-gray-500 mb-4">API secrets are never returned in full. Use read/write trading permissions only when live trading is enabled.</p>
          <div className="space-y-3">
            <div>
              <label className={lbl}>Exchange / broker</label>
              <select className={field} value={form.broker_code} onChange={e => setForm({ ...form, broker_code: e.target.value })}>
                {definitions.map(d => <option key={d.code} value={d.code}>{d.name}</option>)}
              </select>
            </div>
            <div><label className={lbl}>Connection label</label><input className={field} placeholder="Primary, Delta live…" value={form.label} onChange={e => setForm({ ...form, label: e.target.value })} /></div>
            <div><label className={lbl}>API key *</label><input required className={field} type="password" value={form.api_key} onChange={e => setForm({ ...form, api_key: e.target.value })} /></div>
            <div><label className={lbl}>API secret *</label><input required className={field} type="password" value={form.api_secret} onChange={e => setForm({ ...form, api_secret: e.target.value })} /></div>
            <div><label className={lbl}>Passphrase (if required)</label><input className={field} type="password" value={form.passphrase} onChange={e => setForm({ ...form, passphrase: e.target.value })} /></div>
          </div>
          <label className="flex items-center gap-2 text-xs text-gray-300 mt-4">
            <input type="checkbox" className="accent-blue-500" checked={form.is_testnet} onChange={e => setForm({ ...form, is_testnet: e.target.checked })} />
            Use testnet / sandbox where supported
          </label>
          <button disabled={busy} className="mt-4 w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-6 py-2.5 rounded-lg font-bold text-sm">
            {busy ? 'Saving…' : 'Save Connection'}
          </button>
        </form>
      </div>

      {isAdmin && <ExchangeRegistry />}

      {message && <div className={`mt-5 text-sm font-semibold ${message.ok ? 'text-green-400' : 'text-red-400'}`}>{message.text}</div>}
    </div>
  );
};
export default BrokerSettings;
