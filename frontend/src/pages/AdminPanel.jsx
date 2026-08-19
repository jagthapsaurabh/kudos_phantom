import React, { useState, useEffect, useCallback } from 'react';
import { API_URL } from '../api';
import { Users, BookOpen, Activity, Plus, RefreshCw, Key, Eye, EyeOff, ShieldCheck, ShieldOff, ChevronDown, ChevronRight } from 'lucide-react';

const authHeaders = () => ({ 'Authorization': `Bearer ${localStorage.getItem('token')}`, 'Content-Type': 'application/json' });

// ---------------------------------------------------------------- Clients --
const AddClientForm = ({ onCreated }) => {
  const [form, setForm] = useState({ username: '', password: '', initial_capital: 20000, margin_deployment_pct: 25, can_paper: true, can_live: false });
  const [msg, setMsg] = useState(null);
  const [showPw, setShowPw] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setMsg(null);
    const res = await fetch(`${API_URL}/admin/clients`, {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({ ...form, initial_capital: parseFloat(form.initial_capital), margin_deployment_pct: parseFloat(form.margin_deployment_pct) }),
    });
    const data = await res.json();
    if (res.ok) {
      setMsg({ ok: true, text: `Client '${data.client.username}' created` });
      setForm({ username: '', password: '', initial_capital: 20000, margin_deployment_pct: 25, can_paper: true, can_live: false });
      onCreated();
    } else {
      setMsg({ ok: false, text: data.detail || 'Failed to create client' });
    }
  };

  return (
    <form onSubmit={submit} className="bg-gray-800 p-6 rounded-2xl border border-gray-700 mb-6">
      <h3 className="text-sm font-bold text-gray-300 uppercase tracking-wider mb-4 flex items-center gap-2"><Plus size={16} /> Add New Client</h3>
      <div className="grid grid-cols-1 md:grid-cols-6 gap-3 items-end">
        <div className="md:col-span-2">
          <label className="text-[10px] text-gray-500 uppercase block mb-1">Username</label>
          <input required value={form.username} onChange={e => setForm({ ...form, username: e.target.value })}
            className="w-full bg-gray-900 p-2 rounded-lg border border-gray-700 text-white text-sm" />
        </div>
        <div className="md:col-span-2 relative">
          <label className="text-[10px] text-gray-500 uppercase block mb-1">Password</label>
          <input required type={showPw ? 'text' : 'password'} value={form.password} onChange={e => setForm({ ...form, password: e.target.value })}
            className="w-full bg-gray-900 p-2 rounded-lg border border-gray-700 text-white text-sm pr-8" />
          <button type="button" onClick={() => setShowPw(!showPw)} className="absolute right-2 bottom-2 text-gray-500 hover:text-gray-300">
            {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
        </div>
        <div>
          <label className="text-[10px] text-gray-500 uppercase block mb-1">Capital (₹)</label>
          <input type="number" value={form.initial_capital} onChange={e => setForm({ ...form, initial_capital: e.target.value })}
            className="w-full bg-gray-900 p-2 rounded-lg border border-gray-700 text-white text-sm" />
        </div>
        <div>
          <label className="text-[10px] text-gray-500 uppercase block mb-1">Margin %</label>
          <input type="number" value={form.margin_deployment_pct} onChange={e => setForm({ ...form, margin_deployment_pct: e.target.value })}
            className="w-full bg-gray-900 p-2 rounded-lg border border-gray-700 text-white text-sm" />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-6 mt-4">
        <label className="flex items-center gap-2 text-xs text-gray-300"><input type="checkbox" className="accent-green-500" checked={form.can_paper} onChange={e => setForm({ ...form, can_paper: e.target.checked })} /> Paper Trading</label>
        <label className="flex items-center gap-2 text-xs text-gray-300"><input type="checkbox" className="accent-red-500" checked={form.can_live} onChange={e => setForm({ ...form, can_live: e.target.checked })} /> Live Trading</label>
        <button className="bg-blue-600 hover:bg-blue-500 px-6 py-2 rounded-lg font-bold text-sm transition">Create Client</button>
        {msg && <span className={`text-xs font-semibold ${msg.ok ? 'text-green-400' : 'text-red-400'}`}>{msg.text}</span>}
      </div>
    </form>
  );
};

const ClientRow = ({ client, onChanged }) => {
  const [busy, setBusy] = useState(false);
  const [newPw, setNewPw] = useState('');
  const [activity, setActivity] = useState(null);
  const [expanded, setExpanded] = useState(false);

  const patch = async (payload) => {
    setBusy(true);
    await fetch(`${API_URL}/admin/clients/${client.id}`, { method: 'PUT', headers: authHeaders(), body: JSON.stringify(payload) });
    setBusy(false);
    onChanged();
  };

  const loadActivity = async () => {
    if (!expanded) {
      const res = await fetch(`${API_URL}/admin/clients/${client.id}/activity`, { headers: authHeaders() });
      if (res.ok) setActivity(await res.json());
    }
    setExpanded(!expanded);
  };

  const Toggle = ({ value, onClick, okColor, title }) => (
    <button disabled={busy} onClick={onClick} title={title}
      className={`px-2 py-1 rounded text-[10px] font-bold border transition ${value ? okColor : 'bg-gray-900 text-gray-500 border-gray-700 hover:text-white'}`}>
      {value ? 'ON' : 'OFF'}
    </button>
  );

  return (
    <>
      <tr className="border-b border-gray-800 hover:bg-gray-800/40">
        <td className="p-3">
          <div className="font-bold text-gray-200">{client.username}</div>
          <div className={`text-[10px] uppercase font-bold ${client.role === 'admin' ? 'text-purple-400' : 'text-blue-400'}`}>{client.role}</div>
        </td>
        <td className="p-3 font-mono text-gray-300">₹{(client.initial_capital || 0).toLocaleString()}</td>
        <td className="p-3 font-mono text-gray-400">{client.margin_deployment_pct}%</td>
        <td className="p-3">
          <Toggle value={!!client.can_paper} onClick={() => patch({ can_paper: !client.can_paper })}
            okColor="bg-green-900/40 text-green-400 border-green-800/50" title="Allow paper trading" />
        </td>
        <td className="p-3">
          <Toggle value={!!client.can_live} onClick={() => patch({ can_live: !client.can_live })}
            okColor="bg-red-900/40 text-red-400 border-red-800/50" title="Allow live trading" />
        </td>
        <td className="p-3">
          <Toggle value={!!client.is_active} onClick={() => patch({ is_active: !client.is_active })}
            okColor="bg-blue-900/40 text-blue-400 border-blue-800/50" title="Account enabled" />
        </td>
        <td className="p-3 text-center">{client.has_api_keys ? <Key size={14} className="text-yellow-400 inline" /> : <span className="text-gray-600">—</span>}</td>
        <td className="p-3">
          <div className="flex items-center gap-1">
            <input placeholder="reset pw" value={newPw} onChange={e => setNewPw(e.target.value)}
              className="w-20 bg-gray-900 border border-gray-700 rounded px-1 py-1 text-[10px] text-white" />
            <button disabled={busy || !newPw} onClick={() => { patch({ password: newPw }); setNewPw(''); }}
              className="bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded text-[10px] font-bold disabled:opacity-40">Set</button>
            <button onClick={loadActivity} className="bg-blue-900/40 hover:bg-blue-900/60 text-blue-300 px-2 py-1 rounded text-[10px] font-bold flex items-center gap-1">
              {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />} Activity
            </button>
          </div>
        </td>
      </tr>
      {expanded && activity && (
        <tr className="bg-gray-900/60 border-b border-gray-800">
          <td colSpan={8} className="p-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-[11px]">
              <div>
                <div className="text-gray-500 uppercase font-bold text-[9px] mb-1">Paper Sessions ({activity.paper_sessions.length})</div>
                {activity.paper_sessions.map(s => (
                  <div key={s.instance_key} className="font-mono text-gray-300">{s.strategy_id} — ₹{(s.equity_inr || 0).toFixed(0)} {s.is_running ? '🟢' : '🔴'} ({s.open_trades} open)</div>
                ))}
                {activity.paper_sessions.length === 0 && <div className="text-gray-600">No sessions</div>}
              </div>
              <div>
                <div className="text-gray-500 uppercase font-bold text-[9px] mb-1">Live Sessions ({activity.live_sessions.length})</div>
                {activity.live_sessions.map(s => (
                  <div key={s.instance_key} className="font-mono text-gray-300">{s.strategy_id} — ₹{(s.equity_inr || 0).toFixed(0)} {s.is_running ? '🟢' : '🔴'} ({s.open_trades} open)</div>
                ))}
                {activity.live_sessions.length === 0 && <div className="text-gray-600">No sessions</div>}
              </div>
              <div>
                <div className="text-gray-500 uppercase font-bold text-[9px] mb-1">Recent Backtests ({activity.recent_backtests.length})</div>
                {activity.recent_backtests.map(r => (
                  <div key={r.id} className="font-mono text-gray-300">#{r.id} {r.name} — ROI {(r.roi || 0).toFixed(1)}% ({r.total_trades} trades)</div>
                ))}
                {activity.recent_backtests.length === 0 && <div className="text-gray-600">No runs yet</div>}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
};

const ClientsTab = () => {
  const [clients, setClients] = useState([]);
  const load = useCallback(async () => {
    const res = await fetch(`${API_URL}/admin/clients`, { headers: authHeaders() });
    if (res.ok) setClients(await res.json());
  }, []);
  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <AddClientForm onCreated={load} />
      <div className="bg-gray-800 rounded-2xl border border-gray-700 overflow-hidden">
        <div className="p-4 border-b border-gray-700 flex justify-between items-center">
          <h3 className="font-bold text-gray-200">Client Accounts ({clients.length})</h3>
          <button onClick={load} className="text-gray-400 hover:text-white"><RefreshCw size={16} /></button>
        </div>
        <table className="w-full text-left text-xs">
          <thead className="bg-gray-900 text-gray-500 uppercase">
            <tr>
              <th className="p-3">Client</th><th className="p-3">Capital</th><th className="p-3">Margin</th>
              <th className="p-3">Paper</th><th className="p-3">Live</th><th className="p-3">Active</th>
              <th className="p-3 text-center">API Keys</th><th className="p-3">Actions</th>
            </tr>
          </thead>
          <tbody>
            {clients.map(c => <ClientRow key={c.id} client={c} onChanged={load} />)}
          </tbody>
        </table>
      </div>
    </div>
  );
};

// ------------------------------------------------------ Strategy docs tab --
const DocSection = ({ title, color, children }) => (
  <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
    <h3 className={`text-sm font-bold uppercase tracking-wider mb-3 ${color}`}>{title}</h3>
    <div className="space-y-2 text-sm text-gray-300">{children}</div>
  </div>
);

const Rule = ({ name, children }) => (
  <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50">
    <span className="font-mono text-blue-300 text-xs font-bold">{name}</span>
    <div className="text-gray-400 text-xs mt-1 leading-relaxed">{children}</div>
  </div>
);

const StrategyTab = ({ profile, champion }) => {
  const cfg = champion?.config || {};
  const fmt = (v) => (typeof v === 'boolean' ? (v ? 'ON' : 'OFF') : v);
  const interesting = [
    'adx_min', 'macd_hist_min', 'rsi_oversold', 'rsi_overbought', 'atr_regime_ratio',
    'enable_momentum_entry', 'trend_ema_period', 'stop_loss_atr', 'take_profit_atr',
    'trail_activation_atr', 'trail_distance_atr', 'breakeven_atr', 'timeout_bars',
    'cooldown_bars', 'leverage', 'margin_pct', 'reduced_margin_pct',
    'dd_soft_pct', 'dd_halt_pct', 'dd_resume_pct',
  ];
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <DocSection title="📈 Entry Conditions — Setup A: RSI Reversal (LONG)" color="text-green-400">
        <p className="text-xs text-gray-500">All five filters must pass on the same 1h candle; entry executes on the next candle's open.</p>
        <Rule name="1. Trend alignment (4h)">Close(1h) &gt; EMA50(4h) — longs only with the macro uptrend. Shorts require Close &lt; EMA50(4h).</Rule>
        <Rule name="2. ADX filter">ADX(14) ≥ {cfg.adx_min ?? 10} — market must be trending, not chopping.</Rule>
        <Rule name="3. MACD magnitude">|MACD-hist(12,26,9)| ≥ {cfg.macd_hist_min ?? 5} — enough momentum behind the move.</Rule>
        <Rule name="4. ATR volatility regime">ATR(14) ≥ {cfg.atr_regime_ratio ?? 0.5} × SMA50(ATR) — volatility must be alive.</Rule>
        <Rule name="5. Reversal trigger">Prev candle RSI(14) &lt; {cfg.rsi_oversold ?? 40} (long) / &gt; {cfg.rsi_overbought ?? 60} (short) <b>and</b> current candle closes green (long) / red (short).</Rule>
        <Rule name="6. MACD confirmation">MACD-hist rising vs previous bar (long) / falling (short).</Rule>
      </DocSection>

      <DocSection title="⚡ Entry Conditions — Setup B: Momentum Continuation (v3)" color="text-purple-400">
        <p className="text-xs text-gray-500">Adds trend-continuation trades so the strategy trades far more often than reversal-only. Currently <b>{fmt(cfg.enable_momentum_entry)}</b>.</p>
        <Rule name="1. Trend alignment (4h)">Same EMA50(4h) trend filter as Setup A.</Rule>
        <Rule name="2. ADX filter">ADX(14) ≥ {cfg.adx_min ?? 10}.</Rule>
        <Rule name="3. ATR regime">Same volatility regime filter as Setup A.</Rule>
        <Rule name="4. DI confirmation">+DI &gt; −DI (long) / −DI &gt; +DI (short) — directional agreement.</Rule>
        <Rule name="5. MACD zero-cross">MACD-hist crosses above 0 (long) / below 0 (short) on this candle — fresh momentum burst.</Rule>
        <Rule name="6. RSI agreement">RSI(14) ≥ {cfg.momentum_rsi_min ?? 50} (long) / ≤ {cfg.momentum_rsi_min ? (100 - cfg.momentum_rsi_min) : 50} (short).</Rule>
      </DocSection>

      <DocSection title="🛡️ Risk, Exits & Drawdown Guard" color="text-red-400">
        <Rule name="Stop loss">{cfg.stop_loss_atr ?? 1.2}×ATR from entry, with a hard floor of {(cfg.sl_floor_pct ?? 0.016) * 100}% of price.</Rule>
        <Rule name="Take profit">{cfg.take_profit_atr ?? 14}×ATR from entry (maker fee on TP fills).</Rule>
        <Rule name="Trailing stop">Activates after +{cfg.trail_activation_atr ?? 0.8}×ATR; trails the peak at {cfg.trail_distance_atr ?? 0.3}×ATR.</Rule>
        <Rule name="Breakeven stop (v3)">After +{cfg.breakeven_atr ?? 0.75}×ATR in profit, the stop is ratcheted to the entry price — winners can't become losers.</Rule>
        <Rule name="Time stop">Positions older than {cfg.timeout_bars ?? 72} bars are closed at market ("MH").</Rule>
        <Rule name="Cooldown">{cfg.cooldown_bars ?? 0} bar(s) after every close before a new entry is allowed.</Rule>
        <Rule name="Drawdown guard (v3)">Past {cfg.dd_soft_pct ?? 8}% equity drawdown, position size drops to {(cfg.reduced_margin_pct ?? 0.075) * 100}% margin. At {cfg.dd_halt_pct ?? 100}% DD new entries halt entirely and resume below {cfg.dd_resume_pct ?? 100}% DD (100 = guard off).</Rule>
        <Rule name="Position sizing">{(cfg.margin_pct ?? 0.15) * 100}% of equity as margin × {cfg.leverage ?? 2} leverage, quantized to 0.001 BTC lots.</Rule>
        <Rule name="Signal validator">Entries are rejected if price drifts &gt;1% between signal close and next open (gap protection).</Rule>
      </DocSection>

      <DocSection title={`⚙️ Live Champion Config (${profile || 'loading…'})`} color="text-yellow-400">
        <p className="text-xs text-gray-500">This is the exact tuned parameter set currently powering backtests and the signal overlay.</p>
        <div className="grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs">
          {interesting.map(k => (
            <div key={k} className="flex justify-between border-b border-gray-700/50 py-1">
              <span className="text-gray-500">{k}</span>
              <span className="text-gray-200 font-bold">{fmt(cfg[k])}</span>
            </div>
          ))}
        </div>
        <div className="mt-4 bg-yellow-900/20 border border-yellow-800/40 rounded-lg p-3 text-xs text-yellow-300">
          Measured on the full dataset: <b>1,081 trades</b>, win rate <b>59.5%</b>, profit factor <b>1.83</b>,
          Sharpe <b>2.40</b>, max drawdown <b>4.17%</b> (baseline v2.5: 263 trades, 30.34% DD).
        </div>
      </DocSection>
    </div>
  );
};

// -------------------------------------------------------------- Paper tab --
const PaperTab = () => {
  const [paperStatus, setPaperStatus] = useState([]);
  const [msg, setMsg] = useState('');

  const fetchStatus = async () => {
    try {
      const res = await fetch(`${API_URL}/paper-trade/status`, { headers: authHeaders() });
      if (res.ok) setPaperStatus(await res.json());
    } catch (e) {}
  };
  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  const start = async (strategy_id) => {
    const res = await fetch(`${API_URL}/paper-trade/start`, { method: 'POST', headers: authHeaders(), body: JSON.stringify({ strategy_id }) });
    const data = await res.json();
    setMsg(res.ok ? `Started: ${data.instance_key}` : (data.detail || 'Start failed'));
    fetchStatus();
  };
  const stop = async (instance_key) => {
    await fetch(`${API_URL}/paper-trade/stop?instance_key=${encodeURIComponent(instance_key)}`, { method: 'POST', headers: authHeaders() });
    fetchStatus();
  };

  return (
    <div className="space-y-6">
      <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700 flex items-center gap-4 flex-wrap">
        <button onClick={() => start('PhantomV2')} className="bg-green-600 hover:bg-green-500 px-6 py-3 rounded-xl font-bold">▶ Start Phantom v3 Paper</button>
        <button onClick={() => start('FastTest')} className="bg-gray-600 hover:bg-gray-500 px-6 py-3 rounded-xl font-bold">▶ Start FastTest (debug)</button>
        {msg && <span className="text-xs text-gray-400 font-mono">{msg}</span>}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {paperStatus.map(s => (
          <div key={s.instance_key} className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
            <div className="flex justify-between items-center mb-2">
              <div className="font-bold text-gray-200">{s.strategy_id}</div>
              <div className={`text-xs font-bold ${s.is_running ? 'text-green-400' : 'text-red-400'}`}>{s.is_running ? 'RUNNING' : 'STOPPED'}</div>
            </div>
            <div className="text-2xl font-mono text-yellow-400 mb-3">₹{(s.equity_inr || 0).toLocaleString()}</div>
            <div className="text-xs text-gray-500 mb-3">{s.active_trades?.length || 0} open trade(s)</div>
            <button onClick={() => stop(s.instance_key)} className="bg-red-900/40 hover:bg-red-900/60 text-red-300 px-4 py-2 rounded-lg text-xs font-bold">Stop</button>
          </div>
        ))}
        {paperStatus.length === 0 && <div className="text-gray-600 text-sm p-6">No paper sessions running for your account.</div>}
      </div>
    </div>
  );
};

// ------------------------------------------------------------------- Main --
const AdminPanel = () => {
  const [tab, setTab] = useState('clients');
  const [champion, setChampion] = useState(null);

  useEffect(() => {
    fetch(`${API_URL}/phantom/config`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(setChampion)
      .catch(() => {});
  }, []);

  const tabs = [
    { id: 'clients', label: 'Client Management', icon: <Users size={16} /> },
    { id: 'strategy', label: 'Phantom Strategy', icon: <BookOpen size={16} /> },
    { id: 'paper', label: 'Paper Control', icon: <Activity size={16} /> },
  ];

  return (
    <div className="ml-64 p-8 bg-gray-900 text-white min-h-screen font-sans">
      <header className="mb-8 flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-extrabold text-blue-400 tracking-tight">Admin Panel</h1>
          <p className="text-gray-500 text-sm">Client accounts, strategy transparency & session control — PHANTOM v3</p>
        </div>
        <div className="flex items-center gap-2 text-xs bg-gray-800 border border-gray-700 px-3 py-2 rounded-lg">
          <ShieldCheck size={14} className="text-purple-400" />
          <span className="text-gray-400">Signed in as</span>
          <span className="font-bold text-purple-300">{localStorage.getItem('username')}</span>
        </div>
      </header>

      <div className="flex gap-2 mb-6 border-b border-gray-800 pb-3">
        {tabs.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition ${tab === t.id ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-white'}`}>
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {tab === 'clients' && <ClientsTab />}
      {tab === 'strategy' && <StrategyTab profile={champion?.profile} champion={champion} />}
      {tab === 'paper' && <PaperTab />}
    </div>
  );
};

export default AdminPanel;
