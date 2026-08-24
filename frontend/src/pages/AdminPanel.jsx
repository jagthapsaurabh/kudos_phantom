import React, { useState, useEffect, useCallback } from 'react';
import { API_URL } from '../api';
import { Users, BookOpen, Activity, Plus, RefreshCw, Key, Eye, EyeOff, ShieldCheck, ChevronDown, ChevronRight, Lock, Trash2 } from 'lucide-react';

const authHeaders = () => ({ 'Authorization': `Bearer ${localStorage.getItem('token')}`, 'Content-Type': 'application/json' });

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

// ---------------------------------------------------------------- Clients --
const AddClientForm = ({ onCreated }) => {
  const initialForm = { username: '', password: '', full_name: '', mobile: '', email: '', company: '', notes: '', initial_capital: 20000, margin_deployment_pct: 25, can_paper: true, can_live: false };
  const [form, setForm] = useState(initialForm);
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
      setForm(initialForm);
      onCreated();
    } else {
      setMsg({ ok: false, text: data.detail || 'Failed to create client' });
    }
  };

  const field = 'bg-gray-900 p-2 rounded-lg border border-gray-700 text-white text-sm w-full';

  return (
    <form onSubmit={submit} className="bg-gray-800 p-6 rounded-2xl border border-gray-700 mb-6">
      <h3 className="text-sm font-bold text-gray-300 uppercase tracking-wider mb-4 flex items-center gap-2"><Plus size={16} /> Add New Client</h3>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 items-end">
        <div>
          <label className="text-[10px] text-gray-500 uppercase block mb-1">Username *</label>
          <input required value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} className={field} />
        </div>
        <div>
          <label className="text-[10px] text-gray-500 uppercase block mb-1">Password *</label>
          <div className="relative">
            <input required type={showPw ? 'text' : 'password'} value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} className={`${field} pr-8`} />
            <button type="button" onClick={() => setShowPw(!showPw)} className="absolute right-2 bottom-2 text-gray-500 hover:text-gray-300">
              {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          </div>
        </div>
        <div>
          <label className="text-[10px] text-gray-500 uppercase block mb-1">Full Name</label>
          <input value={form.full_name} onChange={e => setForm({ ...form, full_name: e.target.value })} className={field} placeholder="e.g. Rahul Sharma" />
        </div>
        <div>
          <label className="text-[10px] text-gray-500 uppercase block mb-1">Mobile No.</label>
          <input value={form.mobile} onChange={e => setForm({ ...form, mobile: e.target.value })} className={field} placeholder="+91 98xxxxxxx" />
        </div>
        <div>
          <label className="text-[10px] text-gray-500 uppercase block mb-1">Email</label>
          <input type="email" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} className={field} placeholder="client@example.com" />
        </div>
        <div>
          <label className="text-[10px] text-gray-500 uppercase block mb-1">Company / Firm</label>
          <input value={form.company} onChange={e => setForm({ ...form, company: e.target.value })} className={field} />
        </div>
        <div>
          <label className="text-[10px] text-gray-500 uppercase block mb-1">Capital (₹)</label>
          <input type="number" value={form.initial_capital} onChange={e => setForm({ ...form, initial_capital: e.target.value })} className={field} />
        </div>
        <div>
          <label className="text-[10px] text-gray-500 uppercase block mb-1">Margin %</label>
          <input type="number" value={form.margin_deployment_pct} onChange={e => setForm({ ...form, margin_deployment_pct: e.target.value })} className={field} />
        </div>
        <div>
          <label className="text-[10px] text-gray-500 uppercase block mb-1">Notes</label>
          <input value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} className={field} />
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

const ClientRow = ({ client, onChanged, onConfirm }) => {
  const [busy, setBusy] = useState(false);
  const [newPw, setNewPw] = useState('');
  const [activity, setActivity] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const isAdmin = client.role === 'admin';

  const patch = async (payload) => {
    setBusy(true);
    const res = await fetch(`${API_URL}/admin/clients/${client.id}`, { method: 'PUT', headers: authHeaders(), body: JSON.stringify(payload) });
    const data = await res.json();
    if (!res.ok) {
      alert(data.detail || 'Update failed');
    }
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

  const Toggle = ({ value, onClick, okColor, title, disabled }) => (
    <button disabled={busy || disabled} onClick={onClick} title={title}
      className={`px-2 py-1 rounded text-[10px] font-bold border transition ${disabled ? 'opacity-40 cursor-not-allowed bg-gray-900 text-gray-600 border-gray-800' : value ? okColor : 'bg-gray-900 text-gray-500 border-gray-700 hover:text-white'}`}>
      {disabled ? 'LOCKED' : (value ? 'ON' : 'OFF')}
    </button>
  );

  return (
    <>
      <tr className={`border-b border-gray-800 hover:bg-gray-800/40 ${isAdmin ? 'bg-purple-900/10' : ''}`}>
        <td className="p-3">
          <div className="font-bold text-gray-200">{client.full_name || client.username}</div>
          <div className="text-[10px] text-gray-500">@{client.username} · <span className={`uppercase font-bold ${isAdmin ? 'text-purple-400' : 'text-blue-400'}`}>{client.role}</span></div>
          {(client.mobile || client.email) && (
            <div className="text-[10px] text-gray-500 mt-0.5">
              {client.mobile && <span>{client.mobile}</span>}
              {client.mobile && client.email && <span> · </span>}
              {client.email && <span>{client.email}</span>}
            </div>
          )}
        </td>
        <td className="p-3 font-mono text-gray-300">₹{(client.initial_capital || 0).toLocaleString()}</td>
        <td className="p-3 font-mono text-gray-400">{client.margin_deployment_pct}%</td>
        <td className="p-3">
          <Toggle value={!!client.can_paper}
            onClick={() => onConfirm({ type: 'toggle', field: 'can_paper', clientId: client.id, username: client.username, newValue: !client.can_paper, label: 'paper trading' })}
            okColor="bg-green-900/40 text-green-400 border-green-800/50" title="Allow paper trading" disabled={isAdmin} />
        </td>
        <td className="p-3">
          <Toggle value={!!client.can_live}
            onClick={() => onConfirm({ type: 'toggle', field: 'can_live', clientId: client.id, username: client.username, newValue: !client.can_live, label: 'live trading' })}
            okColor="bg-red-900/40 text-red-400 border-red-800/50" title="Allow live trading" disabled={isAdmin} />
        </td>
        <td className="p-3">
          <Toggle value={!!client.is_active}
            onClick={() => onConfirm({ type: 'deactivate', clientId: client.id, username: client.username, newValue: !client.is_active })}
            okColor="bg-blue-900/40 text-blue-400 border-blue-800/50" title={isAdmin ? 'Admin accounts cannot be deactivated' : 'Account enabled'} disabled={isAdmin} />
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

const ClientsTab = ({ onConfirm }) => {
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
            {clients.map(c => <ClientRow key={c.id} client={c} onChanged={load} onConfirm={onConfirm} />)}
          </tbody>
        </table>
      </div>
    </div>
  );
};

// ------------------------------------------------------ Change Password --
const ChangePasswordTab = () => {
  const [form, setForm] = useState({ current_password: '', new_password: '', confirm_password: '' });
  const [showCurrent, setShowCurrent] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [msg, setMsg] = useState(null);
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setMsg(null);
    if (form.new_password !== form.confirm_password) {
      setMsg({ ok: false, text: 'New passwords do not match' });
      return;
    }
    if (form.new_password.length < 6) {
      setMsg({ ok: false, text: 'New password must be at least 6 characters' });
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/auth/change-password`, {
        method: 'POST', headers: authHeaders(),
        body: JSON.stringify({ current_password: form.current_password, new_password: form.new_password }),
      });
      const data = await res.json();
      if (res.ok) {
        setMsg({ ok: true, text: 'Password changed successfully' });
        setForm({ current_password: '', new_password: '', confirm_password: '' });
      } else {
        setMsg({ ok: false, text: data.detail || 'Failed to change password' });
      }
    } catch (e) {
      setMsg({ ok: false, text: 'Network error' });
    }
    setLoading(false);
  };

  return (
    <div className="max-w-lg">
      <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
        <h3 className="text-sm font-bold text-gray-300 uppercase tracking-wider mb-4 flex items-center gap-2"><Lock size={16} /> Change Your Password</h3>
        <form onSubmit={submit} className="space-y-4">
          <div className="relative">
            <label className="text-[10px] text-gray-500 uppercase block mb-1">Current Password</label>
            <input required type={showCurrent ? 'text' : 'password'} value={form.current_password} onChange={e => setForm({ ...form, current_password: e.target.value })}
              className="w-full bg-gray-900 p-3 rounded-lg border border-gray-700 text-white text-sm pr-10" />
            <button type="button" onClick={() => setShowCurrent(!showCurrent)} className="absolute right-3 bottom-3 text-gray-500 hover:text-gray-300">
              {showCurrent ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          </div>
          <div className="relative">
            <label className="text-[10px] text-gray-500 uppercase block mb-1">New Password</label>
            <input required type={showNew ? 'text' : 'password'} value={form.new_password} onChange={e => setForm({ ...form, new_password: e.target.value })}
              className="w-full bg-gray-900 p-3 rounded-lg border border-gray-700 text-white text-sm pr-10" />
            <button type="button" onClick={() => setShowNew(!showNew)} className="absolute right-3 bottom-3 text-gray-500 hover:text-gray-300">
              {showNew ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          </div>
          <div>
            <label className="text-[10px] text-gray-500 uppercase block mb-1">Confirm New Password</label>
            <input required type="password" value={form.confirm_password} onChange={e => setForm({ ...form, confirm_password: e.target.value })}
              className="w-full bg-gray-900 p-3 rounded-lg border border-gray-700 text-white text-sm" />
          </div>
          <div className="flex items-center gap-4">
            <button disabled={loading} className="bg-blue-600 hover:bg-blue-500 px-6 py-2.5 rounded-lg font-bold text-sm transition disabled:opacity-50">
              {loading ? 'Changing…' : 'Change Password'}
            </button>
            {msg && <span className={`text-xs font-semibold ${msg.ok ? 'text-green-400' : 'text-red-400'}`}>{msg.text}</span>}
          </div>
        </form>
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
      </DocSection>
    </div>
  );
};

// -------------------------------------------------------------- Paper tab --
const PaperTab = () => {
  const [paperStatus, setPaperStatus] = useState([]);
  const [msg, setMsg] = useState('');
  const [confirm, setConfirm] = useState(null);

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

  const requestStop = (instance_key) => {
    setConfirm({ type: 'stop', key: instance_key });
  };

  const doStop = async () => {
    if (!confirm) return;
    await fetch(`${API_URL}/paper-trade/stop?instance_key=${encodeURIComponent(confirm.key)}`, { method: 'POST', headers: authHeaders() });
    setConfirm(null);
    fetchStatus();
  };

  return (
    <div className="space-y-6">
      <ConfirmModal
        open={!!confirm}
        title="Stop Paper Trade?"
        message={`This will stop instance "${confirm?.key?.split('_').pop()}" and close any open positions.`}
        confirmLabel="Yes, Stop"
        confirmColor="bg-red-600 hover:bg-red-500"
        onCancel={() => setConfirm(null)}
        onConfirm={doStop}
      />
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
            {s.is_running && (
              <button onClick={() => requestStop(s.instance_key)} className="bg-red-900/40 hover:bg-red-900/60 text-red-300 px-4 py-2 rounded-lg text-xs font-bold">Stop</button>
            )}
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
  const [confirm, setConfirm] = useState(null);

  useEffect(() => {
    fetch(`${API_URL}/phantom/config`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(setChampion)
      .catch(() => {});
  }, []);

  const doConfirm = async () => {
    if (!confirm) return;
    try {
      if (confirm.type === 'deactivate') {
        await fetch(`${API_URL}/admin/clients/${confirm.clientId}`, {
          method: 'PUT', headers: authHeaders(),
          body: JSON.stringify({ is_active: confirm.newValue }),
        });
        // Trigger reload by toggling tab briefly
        window.location.reload();
      } else if (confirm.type === 'toggle') {
        await fetch(`${API_URL}/admin/clients/${confirm.clientId}`, {
          method: 'PUT', headers: authHeaders(),
          body: JSON.stringify({ [confirm.field]: confirm.newValue }),
        });
        window.location.reload();
      }
    } catch (e) {
      alert(e.message || 'Action failed');
    }
    setConfirm(null);
  };

  const tabs = [
    { id: 'clients', label: 'Client Management', icon: <Users size={16} /> },
    { id: 'password', label: 'Change Password', icon: <Lock size={16} /> },
    { id: 'strategy', label: 'Phantom Strategy', icon: <BookOpen size={16} /> },
    { id: 'paper', label: 'Paper Control', icon: <Activity size={16} /> },
  ];

  return (
    <div className="ml-64 p-8 bg-gray-900 text-white min-h-screen font-sans">
      <ConfirmModal
        open={!!confirm}
        title={
          confirm?.type === 'deactivate'
            ? (confirm?.newValue === false ? 'Deactivate Client?' : 'Reactivate Client?')
            : confirm?.type === 'toggle'
              ? (confirm?.newValue === false ? `Disable ${confirm?.label}?` : `Enable ${confirm?.label}?`)
              : 'Confirm'
        }
        message={
          confirm?.type === 'deactivate'
            ? (confirm?.newValue === false ? `Are you sure you want to deactivate "${confirm?.username}"? They will not be able to log in.` : `Reactivate "${confirm?.username}"? They will be able to log in again.`)
            : confirm?.type === 'toggle'
              ? (confirm?.newValue === false ? `Disable ${confirm?.label} for "${confirm?.username}"? They will lose this capability.` : `Enable ${confirm?.label} for "${confirm?.username}"?`)
              : ''
        }
        confirmLabel={
          confirm?.type === 'deactivate'
            ? (confirm?.newValue === false ? 'Yes, Deactivate' : 'Yes, Reactivate')
            : confirm?.type === 'toggle'
              ? (confirm?.newValue === false ? 'Yes, Disable' : 'Yes, Enable')
              : 'Confirm'
        }
        confirmColor={(confirm?.newValue === false ? 'bg-red-600 hover:bg-red-500' : 'bg-green-600 hover:bg-green-500')}
        onCancel={() => setConfirm(null)}
        onConfirm={doConfirm}
      />

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

      {tab === 'clients' && <ClientsTab onConfirm={setConfirm} />}
      {tab === 'password' && <ChangePasswordTab />}
      {tab === 'strategy' && <StrategyTab profile={champion?.profile} champion={champion} />}
      {tab === 'paper' && <PaperTab />}
    </div>
  );
};

export default AdminPanel;
