import React, { useState, useEffect, useCallback } from 'react';
import { API_URL } from '../api';
import { Users, Activity, Plus, RefreshCw, Key, Eye, EyeOff, ShieldCheck, ChevronDown, ChevronRight, Lock, Percent, Database, Upload, Save, Pencil, X, Trash2, StopCircle } from 'lucide-react';
import DateInput from '../components/DateInput';

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

const EditClientModal = ({ client, onClose, onSaved }) => {
  const [form, setForm] = useState({
    full_name: client.full_name || '',
    mobile: client.mobile || '',
    email: client.email || '',
    company: client.company || '',
    notes: client.notes || '',
    initial_capital: client.initial_capital ?? 20000,
    margin_deployment_pct: client.margin_deployment_pct ?? 25,
  });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const field = 'w-full rounded-lg border border-gray-700 bg-gray-900 p-2.5 text-sm text-white outline-none focus:border-blue-500';
  const update = (key, value) => setForm(previous => ({ ...previous, [key]: value }));

  const submit = async e => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const res = await fetch(`${API_URL}/admin/clients/${client.id}`, {
        method: 'PUT',
        headers: authHeaders(),
        body: JSON.stringify({
          full_name: form.full_name,
          mobile: form.mobile,
          email: form.email,
          company: form.company,
          notes: form.notes,
          initial_capital: Number(form.initial_capital),
          margin_deployment_pct: Number(form.margin_deployment_pct),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Could not update client');
      onSaved();
      onClose();
    } catch (error) {
      setMessage({ ok: false, text: error.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm" onClick={onClose}>
      <form onSubmit={submit} onClick={e => e.stopPropagation()} className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-gray-700 bg-gray-800 p-6 shadow-2xl">
        <div className="mb-5 flex items-start justify-between gap-4">
          <div>
            <h3 className="flex items-center gap-2 text-lg font-bold text-white"><Pencil size={17} className="text-blue-400" /> Edit client details</h3>
            <p className="mt-1 text-xs text-gray-500">Update the profile and paper-trading defaults for @{client.username}.</p>
          </div>
          <button type="button" onClick={onClose} className="rounded-lg p-1.5 text-gray-500 transition hover:bg-gray-700 hover:text-white" aria-label="Close edit form"><X size={17} /></button>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <label className="mb-1 block text-[10px] font-bold uppercase text-gray-500">Username</label>
            <input value={client.username} disabled className={`${field} cursor-not-allowed opacity-60`} />
          </div>
          <div>
            <label className="mb-1 block text-[10px] font-bold uppercase text-gray-500">Full name</label>
            <input value={form.full_name} onChange={e => update('full_name', e.target.value)} className={field} />
          </div>
          <div>
            <label className="mb-1 block text-[10px] font-bold uppercase text-gray-500">Mobile</label>
            <input value={form.mobile} onChange={e => update('mobile', e.target.value)} className={field} />
          </div>
          <div>
            <label className="mb-1 block text-[10px] font-bold uppercase text-gray-500">Email</label>
            <input type="email" value={form.email} onChange={e => update('email', e.target.value)} className={field} />
          </div>
          <div>
            <label className="mb-1 block text-[10px] font-bold uppercase text-gray-500">Company / firm</label>
            <input value={form.company} onChange={e => update('company', e.target.value)} className={field} />
          </div>
          <div>
            <label className="mb-1 block text-[10px] font-bold uppercase text-gray-500">Capital (₹)</label>
            <input type="number" min="0" step="100" value={form.initial_capital} onChange={e => update('initial_capital', e.target.value)} className={field} />
          </div>
          <div>
            <label className="mb-1 block text-[10px] font-bold uppercase text-gray-500">Margin deployment %</label>
            <input type="number" min="0" max="100" step="0.5" value={form.margin_deployment_pct} onChange={e => update('margin_deployment_pct', e.target.value)} className={field} />
          </div>
          <div className="sm:col-span-2">
            <label className="mb-1 block text-[10px] font-bold uppercase text-gray-500">Notes</label>
            <textarea rows="3" value={form.notes} onChange={e => update('notes', e.target.value)} className={field} />
          </div>
        </div>
        <div className="mt-5 flex flex-wrap items-center justify-end gap-3">
          {message && <span className="mr-auto text-xs font-semibold text-red-400">{message.text}</span>}
          <button type="button" onClick={onClose} className="rounded-lg bg-gray-700 px-4 py-2 text-sm font-semibold text-gray-300 transition hover:bg-gray-600 hover:text-white">Cancel</button>
          <button disabled={busy} className="flex items-center gap-2 rounded-lg bg-blue-600 px-5 py-2 text-sm font-bold text-white transition hover:bg-blue-500 disabled:opacity-50"><Save size={14} /> {busy ? 'Saving…' : 'Save changes'}</button>
        </div>
      </form>
    </div>
  );
};

const ClientRow = ({ client, onEdit, onConfirm }) => {
  const [busy, setBusy] = useState(false);
  const [activity, setActivity] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const isAdmin = client.role === 'admin';

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
          <div className="flex flex-wrap items-center gap-1">
            <button onClick={() => onEdit(client)}
              className="flex items-center gap-1 rounded bg-blue-900/40 px-2 py-1 text-[10px] font-bold text-blue-300 transition hover:bg-blue-900/60">
              <Pencil size={11} /> Edit
            </button>
            <button onClick={loadActivity} className="flex items-center gap-1 rounded bg-gray-700 px-2 py-1 text-[10px] font-bold text-gray-300 transition hover:bg-gray-600 hover:text-white">
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
                  <div key={s.instance_key} className="font-mono text-gray-300">{s.strategy_name || s.strategy_id} — ₹{(s.equity_inr || 0).toFixed(0)} {s.is_running ? '🟢' : '🔴'} ({s.open_trades} open)</div>
                ))}
                {activity.paper_sessions.length === 0 && <div className="text-gray-600">No sessions</div>}
              </div>
              <div>
                <div className="text-gray-500 uppercase font-bold text-[9px] mb-1">Live Sessions ({activity.live_sessions.length})</div>
                {activity.live_sessions.map(s => (
                  <div key={s.instance_key} className="font-mono text-gray-300">{s.strategy_name || s.strategy_id} — ₹{(s.equity_inr || 0).toFixed(0)} {s.is_running ? '🟢' : '🔴'} ({s.open_trades} open)</div>
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
  const [editingClient, setEditingClient] = useState(null);
  const load = useCallback(async () => {
    const res = await fetch(`${API_URL}/admin/clients`, { headers: authHeaders() });
    if (res.ok) setClients(await res.json());
  }, []);
  useEffect(() => { load(); }, [load]);

  return (
    <div>
      {editingClient && <EditClientModal client={editingClient} onClose={() => setEditingClient(null)} onSaved={load} />}
      <AddClientForm onCreated={load} />
      <div className="bg-gray-800 rounded-2xl border border-gray-700 overflow-hidden">
        <div className="p-4 border-b border-gray-700 flex justify-between items-center">
          <h3 className="font-bold text-gray-200">Client Accounts ({clients.length})</h3>
          <button onClick={load} className="text-gray-400 hover:text-white"><RefreshCw size={16} /></button>
        </div>
        <div className="overflow-x-auto">
        <table className="w-full text-left text-xs min-w-[860px]">
          <thead className="bg-gray-900 text-gray-500 uppercase">
            <tr>
              <th className="p-3">Client</th><th className="p-3">Capital</th><th className="p-3">Margin</th>
              <th className="p-3">Paper</th><th className="p-3">Live</th><th className="p-3">Active</th>
              <th className="p-3 text-center">API Keys</th><th className="p-3">Actions</th>
            </tr>
          </thead>
          <tbody>
            {clients.map(c => <ClientRow key={c.id} client={c} onEdit={setEditingClient} onConfirm={onConfirm} />)}
          </tbody>
        </table>
        </div>
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

  const requestStop = (instance_key, name) => {
    setConfirm({ type: 'stop', key: instance_key, name });
  };

  const requestDelete = (instance_key, name) => {
    setConfirm({ type: 'delete', key: instance_key, name });
  };

  const doPaperAction = async () => {
    if (!confirm) return;
    const isDelete = confirm.type === 'delete';
    const url = isDelete
      ? `${API_URL}/paper-trade/${encodeURIComponent(confirm.key)}`
      : `${API_URL}/paper-trade/stop?instance_key=${encodeURIComponent(confirm.key)}`;
    await fetch(url, { method: isDelete ? 'DELETE' : 'POST', headers: authHeaders() });
    setConfirm(null);
    fetchStatus();
  };

  return (
    <div className="space-y-6">
      <ConfirmModal
        open={!!confirm}
        title={confirm?.type === 'delete' ? 'Delete Paper Trade?' : 'Stop Paper Trade?'}
        message={confirm?.type === 'delete'
          ? `Delete "${confirm?.name || confirm?.key?.split('_').pop()}" and remove its session history?`
          : `Stop "${confirm?.name || confirm?.key?.split('_').pop()}" and close any open positions.`}
        confirmLabel={confirm?.type === 'delete' ? 'Yes, Delete' : 'Yes, Stop'}
        confirmColor="bg-red-600 hover:bg-red-500"
        onCancel={() => setConfirm(null)}
        onConfirm={doPaperAction}
      />
      <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700 flex items-center gap-4 flex-wrap">
        <button onClick={() => start('PhantomV2')} className="bg-green-600 hover:bg-green-500 px-6 py-3 rounded-xl font-bold">▶ Start Kudos v3 Paper</button>
        <button onClick={() => start('FastTest')} className="bg-gray-600 hover:bg-gray-500 px-6 py-3 rounded-xl font-bold">▶ Start FastTest (debug)</button>
        {msg && <span className="text-xs text-gray-400 font-mono">{msg}</span>}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {paperStatus.map((s, index) => {
          const strategyName = s.strategy_name || s.strategy_id;
          return <div key={s.instance_key} className="bg-gray-800 p-5 rounded-2xl border border-gray-700">
            <div className="mb-2 flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-[9px] font-bold uppercase tracking-wider text-gray-500">Session {index + 1}</div>
                <div className="truncate font-bold text-gray-200" title={strategyName}>{strategyName}</div>
                <div className="text-[10px] text-blue-300">{s.data_source || 'Binance'}</div>
              </div>
              <div className={`shrink-0 text-xs font-bold ${s.is_running ? 'text-green-400' : 'text-red-400'}`}>{s.is_running ? 'RUNNING' : 'STOPPED'}</div>
            </div>
            <div className="text-2xl font-mono text-yellow-400 mb-1">₹{(s.equity_inr || 0).toLocaleString()}</div>
            <div className="mb-3 text-xs text-gray-500">{s.active_trades?.length || 0} open trade(s) · #{s.instance_key.split('_').pop()}</div>
            <div className="flex gap-2">
              {s.is_running && (
                <button onClick={() => requestStop(s.instance_key, strategyName)} className="flex items-center gap-1 rounded-lg bg-red-900/40 px-3 py-2 text-xs font-bold text-red-300 transition hover:bg-red-900/60"><StopCircle size={13} /> Stop</button>
              )}
              <button onClick={() => requestDelete(s.instance_key, strategyName)} className="flex items-center gap-1 rounded-lg bg-gray-700 px-3 py-2 text-xs font-bold text-gray-300 transition hover:bg-red-900/40 hover:text-red-300"><Trash2 size={13} /> Delete</button>
            </div>
          </div>;
        })}
        {paperStatus.length === 0 && <div className="text-gray-600 text-sm p-6">No paper sessions running for your account.</div>}
      </div>
    </div>
  );
};

// ------------------------------------------------------------- Fee schedules --
const FeeSettingsTab = () => {
  const [fees, setFees] = useState([]);
  const [brokers, setBrokers] = useState([]);
  const [msg, setMsg] = useState(null);
  const load = async () => {
    const [f, b] = await Promise.all([
      fetch(`${API_URL}/admin/fee-settings`, { headers: authHeaders() }).then(r => r.json()),
      fetch(`${API_URL}/admin/brokers`, { headers: authHeaders() }).then(r => r.json()),
    ]);
    setFees(Array.isArray(f) ? f : []); setBrokers(Array.isArray(b) ? b : []);
  };
  useEffect(() => { load(); }, []);
  const update = (id, key, value) => setFees(rows => rows.map(row => row.id === id ? { ...row, [key]: value } : row));
  const save = async row => {
    const res = await fetch(`${API_URL}/admin/fee-settings`, { method: 'POST', headers: authHeaders(), body: JSON.stringify({
      broker_code: row.broker_code, mode: row.mode, taker_fee_bps: Number(row.taker_fee_bps), maker_fee_bps: Number(row.maker_fee_bps), enabled: !!row.enabled,
    }) });
    setMsg(res.ok ? { ok: true, text: `${row.broker_code} ${row.mode} schedule saved.` } : { ok: false, text: (await res.json()).detail || 'Save failed' });
    if (res.ok) load();
  };
  return <div className="space-y-5 max-w-6xl">
    <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700"><h3 className="text-sm font-bold text-gray-200 uppercase flex items-center gap-2"><Percent size={16} className="text-yellow-400" /> Exchange fee schedules</h3><p className="text-xs text-gray-500 mt-2">These basis-point values are applied to every new backtest, paper session and live session. TP exits use the maker rate; all other fills use the taker rate. Existing runs keep their recorded schedule.</p></div>
    <div className="bg-gray-800 rounded-2xl border border-gray-700 overflow-hidden"><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="bg-gray-900 text-gray-500 text-[10px] uppercase"><tr><th className="p-3">Broker / data</th><th className="p-3">Mode</th><th className="p-3">Taker (bps)</th><th className="p-3">Maker (bps)</th><th className="p-3">Status</th><th className="p-3"></th></tr></thead><tbody>{fees.map(row => <tr key={row.id} className="border-t border-gray-700"><td className="p-3 font-bold text-gray-200">{row.broker_code}</td><td className="p-3"><span className="px-2 py-1 rounded bg-blue-900/30 text-blue-300 text-xs uppercase">{row.mode}</span></td><td className="p-3"><input type="number" min="0" step="0.01" value={row.taker_fee_bps} onChange={e => update(row.id, 'taker_fee_bps', e.target.value)} className="w-28 bg-gray-900 border border-gray-700 rounded p-2" /></td><td className="p-3"><input type="number" min="0" step="0.01" value={row.maker_fee_bps} onChange={e => update(row.id, 'maker_fee_bps', e.target.value)} className="w-28 bg-gray-900 border border-gray-700 rounded p-2" /></td><td className="p-3"><button onClick={() => update(row.id, 'enabled', !row.enabled)} className={`text-[10px] font-bold px-2 py-1 rounded border ${row.enabled ? 'text-green-400 border-green-800 bg-green-900/20' : 'text-gray-500 border-gray-700'}`}>{row.enabled ? 'ACTIVE' : 'DISABLED'}</button></td><td className="p-3"><button onClick={() => save(row)} className="bg-blue-600 hover:bg-blue-500 px-3 py-2 rounded text-xs font-bold flex items-center gap-1"><Save size={12} /> Save</button></td></tr>)}</tbody></table></div>{fees.length === 0 && <div className="p-8 text-center text-gray-500 text-sm">No schedules found. Restart the API to seed the built-in defaults.</div>}</div>
    {msg && <div className={msg.ok ? 'text-green-400 text-xs font-semibold' : 'text-red-400 text-xs font-semibold'}>{msg.text}</div>}
  </div>;
};

// --------------------------------------------------------------- Seed data --
const SeedDataTab = () => {
  const ALL_INTERVALS = ['1m', '5m', '15m', '1h', '4h', '1d'];
  const DELTA_INTERVALS = ['15m', '1h', '4h', '1d'];
  const builtInDefs = [
    { code: 'Binance', name: 'Binance Futures', kind: 'binance' },
    { code: 'Delta', name: 'Delta Exchange', kind: 'delta' },
  ];
  const today = () => {
    const now = new Date();
    const pad = value => String(value).padStart(2, '0');
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  };

  const [defs, setDefs] = useState([]);
  const [fetchAll, setFetchAll] = useState(false);
  const [source, setSource] = useState('Binance');
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [intervals, setIntervals] = useState(ALL_INTERVALS);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [limit, setLimit] = useState(1000);
  const [status, setStatus] = useState([]);
  const [progress, setProgress] = useState([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [file, setFile] = useState(null);
  const [csvInterval, setCsvInterval] = useState('1h');
  const [testResult, setTestResult] = useState(null);
  const f = 'w-full rounded-lg border border-gray-700 bg-gray-900 p-2 text-sm text-white outline-none focus:border-blue-500';
  const sourceOptions = defs.length ? defs : builtInDefs;
  const selectedDef = sourceOptions.find(item => item.code === source);
  const isDeltaSource = source === 'Delta' || selectedDef?.kind === 'delta';
  const availableIntervals = isDeltaSource ? DELTA_INTERVALS : ALL_INTERVALS;

  const load = async () => {
    try {
      const [definitions, datasets, seedProgress] = await Promise.all([
        fetch(`${API_URL}/broker-definitions`, { headers: authHeaders() }).then(r => r.json()),
        fetch(`${API_URL}/admin/market-data/status`, { headers: authHeaders() }).then(r => r.json()),
        fetch(`${API_URL}/admin/market-data/progress`, { headers: authHeaders() }).then(r => r.json()),
      ]);
      setDefs(Array.isArray(definitions) ? definitions : []);
      setStatus(Array.isArray(datasets) ? datasets : []);
      setProgress(Array.isArray(seedProgress) ? seedProgress : []);
    } catch (error) {
      setMsg({ ok: false, text: `Could not load seed status: ${error.message}` });
    }
  };
  useEffect(() => { load(); }, []);

  const applyDeltaPreset = () => {
    const delta = sourceOptions.find(item => item.kind === 'delta' || item.code === 'Delta');
    setSource(delta?.code || 'Delta');
    setSymbol('BTCUSDT');
    setIntervals(DELTA_INTERVALS);
    setStart('2020-01-01');
    setEnd(today());
    setLimit(2000);
    setFetchAll(true);
    setMsg({ ok: true, text: 'Delta full-history preset ready: 15m, 1h, 4h and 1d through today.' });
  };

  const handleSourceChange = nextSource => {
    const nextDef = sourceOptions.find(item => item.code === nextSource);
    const nextIsDelta = nextSource === 'Delta' || nextDef?.kind === 'delta';
    setSource(nextSource);
    setTestResult(null);
    if (nextIsDelta) {
      setIntervals(DELTA_INTERVALS);
      setStart('2020-01-01');
      setEnd(today());
      setLimit(2000);
      setFetchAll(true);
      if (csvInterval === '1m' || csvInterval === '5m') setCsvInterval('1h');
    } else {
      setIntervals(ALL_INTERVALS);
      setStart('');
      setEnd('');
      setLimit(1000);
      setFetchAll(false);
    }
  };

  const formatSummary = (data, verb) => {
    const summary = Array.isArray(data.summary) ? data.summary : [];
    const fetched = summary.reduce((total, row) => total + (row.fetched || 0), 0);
    const failures = summary
      .filter(row => row.error)
      .map(row => `${row.source || ''}${row.interval ? ` ${row.interval}` : ''}: ${row.error}`);
    if (failures.length) return { ok: false, text: `${data.status || verb} — ${fetched.toLocaleString()} candles. ${failures.join(' | ')}` };
    return { ok: true, text: `${data.status || verb} — ${fetched.toLocaleString()} candles processed.` };
  };

  const seed = async event => {
    event.preventDefault();
    if (!intervals.length) {
      setMsg({ ok: false, text: 'Select at least one candle interval.' });
      return;
    }
    setBusy(true);
    setMsg(null);
    setTestResult(null);
    const requestIntervals = isDeltaSource ? DELTA_INTERVALS : intervals;
    try {
      const res = await fetch(`${API_URL}/admin/market-data/seed`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          source,
          symbol: symbol.toUpperCase(),
          intervals: requestIntervals,
          start_date: isDeltaSource ? (start || '2020-01-01') : (start || null),
          end_date: isDeltaSource ? (end || today()) : (end || null),
          limit: Number(limit),
          fetch_all: isDeltaSource || fetchAll,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Seed failed');
      setMsg(formatSummary(data, 'Seed completed'));
      await load();
    } catch (error) {
      setMsg({ ok: false, text: error.message });
    } finally {
      setBusy(false);
    }
  };

  const syncNow = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const res = await fetch(`${API_URL}/admin/market-data/sync-now`, {
        method: 'POST', headers: authHeaders(),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Daily refresh failed');
      setMsg(formatSummary(data, 'Daily refresh completed'));
      await load();
    } catch (error) {
      setMsg({ ok: false, text: error.message });
    } finally {
      setBusy(false);
    }
  };

  const testSource = async () => {
    setBusy(true);
    setMsg(null);
    setTestResult(null);
    try {
      const probeInterval = isDeltaSource ? '1h' : (intervals[0] || '1h');
      const res = await fetch(`${API_URL}/admin/market-data/test?source=${encodeURIComponent(source)}&symbol=${encodeURIComponent(symbol.toUpperCase())}&interval=${probeInterval}`, { headers: authHeaders() });
      const data = await res.json().catch(() => ({}));
      setTestResult(res.ok ? data : { ok: false, detail: data.detail || 'Test failed' });
    } catch (error) {
      setTestResult({ ok: false, detail: error.message });
    } finally {
      setBusy(false);
    }
  };

  const upload = async event => {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setMsg(null);
    try {
      const body = new FormData();
      body.append('file', file);
      body.append('source', source);
      body.append('symbol', symbol.toUpperCase());
      body.append('interval', csvInterval);
      const res = await fetch(`${API_URL}/admin/market-data/seed-csv`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${localStorage.getItem('token')}` },
        body,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'CSV import failed');
      setMsg({ ok: true, text: `Imported ${(data.summary?.fetched || 0).toLocaleString()} candles with volume.` });
      setFile(null);
      await load();
    } catch (error) {
      setMsg({ ok: false, text: error.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="max-w-7xl space-y-5">
      <div className="rounded-2xl border border-gray-700 bg-gray-800 p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-bold uppercase text-gray-200"><Database size={16} className="text-blue-400" /> Seed market data</h3>
            <p className="mt-2 max-w-4xl text-xs text-gray-500">Seed OHLCV candles separately for each exchange. Existing candles are upserted by source, symbol, interval and timestamp. Volume is required for every candle.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={applyDeltaPreset} disabled={busy} className="rounded-lg border border-orange-800/60 bg-orange-900/20 px-3 py-2 text-xs font-bold text-orange-300 transition hover:bg-orange-900/40 disabled:opacity-50">Delta 2020 → today</button>
            <button type="button" onClick={syncNow} disabled={busy} className="flex items-center gap-2 rounded-lg border border-blue-800/60 bg-blue-900/20 px-3 py-2 text-xs font-bold text-blue-300 transition hover:bg-blue-900/40 disabled:opacity-50"><RefreshCw size={13} /> Run daily refresh now</button>
          </div>
        </div>

        {isDeltaSource ? (
          <div className="mt-5 rounded-xl border border-orange-800/50 bg-orange-900/15 p-4 text-xs text-orange-200">
            <div className="font-bold">Delta Exchange history mode</div>
            <p className="mt-1 text-orange-300/80">Delta returns a maximum of 2,000 candles per request. The backend splits 1 Jan 2020 → today into safe date windows, retries rate limits, and continues through empty pre-listing windows. 1m and 5m are intentionally excluded; this screen will seed 15m, 1h, 4h and 1d only.</p>
          </div>
        ) : (
          <div className="mt-5 rounded-xl border border-blue-900/40 bg-blue-900/10 p-3 text-xs text-gray-400">
            <span className="font-bold text-blue-300">Daily refresh:</span> the API automatically refreshes Binance and every enabled Binance-compatible or Delta-compatible broker once per day. Generic integrations need a compatible adapter before they can be fetched.
          </div>
        )}

        <form onSubmit={seed} className="mt-5 grid grid-cols-1 items-end gap-4 md:grid-cols-4">
          <div>
            <label className="mb-1 block text-[10px] font-bold uppercase text-gray-500">Source</label>
            <select className={f} value={source} onChange={event => handleSourceChange(event.target.value)}>
              {sourceOptions.map(item => <option key={item.code} value={item.code}>{item.name || item.code}</option>)}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-[10px] font-bold uppercase text-gray-500">Symbol</label>
            <input className={f} value={symbol} onChange={event => setSymbol(event.target.value)} />
          </div>
          <div>
            <label className="mb-1 block text-[10px] font-bold uppercase text-gray-500">From</label>
            <DateInput value={start} onChange={event => setStart(event.target.value)} />
          </div>
          <div>
            <label className="mb-1 block text-[10px] font-bold uppercase text-gray-500">To</label>
            <DateInput value={end} onChange={event => setEnd(event.target.value)} />
          </div>

          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 md:col-span-3">
            <span className="text-[10px] font-bold uppercase text-gray-500">Intervals</span>
            {availableIntervals.map(interval => (
              <label key={interval} className="flex items-center gap-1 text-xs text-gray-300">
                <input type="checkbox" checked={intervals.includes(interval)} onChange={event => setIntervals(event.target.checked ? [...new Set([...intervals, interval])] : intervals.filter(item => item !== interval))} className="accent-blue-500" /> {interval}
              </label>
            ))}
            {isDeltaSource && <span className="text-[10px] font-semibold text-orange-400">1m / 5m excluded by Delta history plan</span>}
            {!isDeltaSource && <label className="flex items-center gap-2 text-xs text-gray-400"><input type="checkbox" checked={fetchAll} onChange={event => setFetchAll(event.target.checked)} className="accent-blue-500" /> Fetch all date windows</label>}
          </div>
          <div>
            <label className="mb-1 block text-[10px] font-bold uppercase text-gray-500">Candles per API request</label>
            <input type="number" min="10" max="2000" className={f} value={limit} onChange={event => setLimit(event.target.value)} />
          </div>

          <div className="flex flex-wrap gap-2 md:col-span-4">
            <button disabled={busy} className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-bold transition hover:bg-blue-500 disabled:opacity-50">{busy ? 'Working…' : isDeltaSource ? 'Seed Delta history' : 'Fetch & seed OHLCV'}</button>
            <button type="button" disabled={busy} onClick={testSource} className="rounded-lg bg-gray-700 px-5 py-2.5 text-sm font-bold transition hover:bg-gray-600 disabled:opacity-50" title="Probe one safe 1h request before a long seed">Test connection</button>
          </div>
        </form>
      </div>

      {testResult && (
        <div className={`rounded-xl border p-3 text-xs font-mono ${testResult.ok ? 'border-green-800 bg-green-900/20 text-green-300' : 'border-red-800 bg-red-900/20 text-red-300'}`}>
          <div className="mb-1 font-bold">{testResult.ok ? '✓ Connection OK' : '✗ Connection problem'}</div>
          <div>{testResult.detail}</div>
          {testResult.ok && testResult.rows > 0 && <div className="mt-1 opacity-80">rows={testResult.rows} · first={testResult.first} · last={testResult.last}</div>}
        </div>
      )}

      <form onSubmit={upload} className="flex flex-wrap items-end gap-4 rounded-2xl border border-gray-700 bg-gray-800 p-6">
        <div>
          <label className="mb-1 block text-[10px] font-bold uppercase text-gray-500">CSV file (event_time, open, high, low, close, volume)</label>
          <input type="file" accept=".csv" onChange={event => setFile(event.target.files?.[0] || null)} className="mt-2 text-sm text-gray-400" />
        </div>
        <div>
          <label className="mb-1 block text-[10px] font-bold uppercase text-gray-500">CSV interval</label>
          <select className={f} value={csvInterval} onChange={event => setCsvInterval(event.target.value)}>
            {(isDeltaSource ? DELTA_INTERVALS : ALL_INTERVALS).map(interval => <option key={interval}>{interval}</option>)}
          </select>
        </div>
        <button disabled={!file || busy} className="flex items-center gap-2 rounded-lg bg-gray-600 px-5 py-2.5 text-sm font-bold transition hover:bg-gray-500 disabled:opacity-50"><Upload size={14} /> Import CSV</button>
      </form>

      {msg && <div className={`text-xs font-semibold ${msg.ok ? 'text-green-400' : 'text-red-400'}`}>{msg.text}</div>}

      {progress.length > 0 && (
        <div className="overflow-hidden rounded-2xl border border-gray-700 bg-gray-800">
          <div className="border-b border-gray-700 p-4"><h3 className="font-bold text-gray-300">Historical seed progress</h3><p className="mt-1 text-[10px] text-gray-600">Each committed window advances a durable cursor. Re-running the same range resumes at the next uncommitted window and completed ranges are skipped.</p></div>
          <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead className="bg-gray-900 text-gray-500 uppercase"><tr><th className="p-3">Source</th><th className="p-3">Interval</th><th className="p-3">Status</th><th className="p-3">Windows</th><th className="p-3">Cursor</th><th className="p-3">Error</th></tr></thead><tbody>{progress.slice(0, 20).map(row => <tr key={`${row.source}-${row.definition}-${row.symbol}-${row.interval}-${row.requested_start}-${row.requested_end}`} className="border-t border-gray-700"><td className="p-3 font-bold text-blue-300">{row.source}</td><td className="p-3">{row.interval}</td><td className={`p-3 font-bold ${row.status === 'completed' ? 'text-green-400' : row.status === 'failed' ? 'text-red-400' : 'text-orange-300'}`}>{row.status}</td><td className="p-3 font-mono">{row.pages} <span className="text-gray-600">({row.fetched?.toLocaleString?.() || 0} candles)</span></td><td className="p-3 text-gray-500">{row.next_start?.split('T')[0]} / {row.requested_end?.split('T')[0]}</td><td className="max-w-md truncate p-3 text-red-300" title={row.last_error || ''}>{row.last_error || '—'}</td></tr>)}</tbody></table></div>
        </div>
      )}

      <div className="overflow-hidden rounded-2xl border border-gray-700 bg-gray-800">
        <div className="flex items-center justify-between border-b border-gray-700 p-4"><div><h3 className="font-bold text-gray-300">Seeded datasets</h3><p className="mt-1 text-[10px] text-gray-600">Daily refresh runs automatically after the API starts, then every 24 hours.</p></div><button onClick={load} className="text-gray-400 transition hover:text-white" title="Refresh dataset status"><RefreshCw size={15} /></button></div>
        <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead className="bg-gray-900 text-gray-500 uppercase"><tr><th className="p-3">Source</th><th className="p-3">Symbol</th><th className="p-3">Interval</th><th className="p-3">Candles</th><th className="p-3">With volume</th><th className="p-3">Range</th></tr></thead><tbody>{status.map(row => <tr key={`${row.source}-${row.symbol}-${row.interval}`} className="border-t border-gray-700"><td className="p-3 font-bold text-blue-300">{row.source}</td><td className="p-3">{row.symbol}</td><td className="p-3">{row.interval}</td><td className="p-3 font-mono">{Number(row.count || 0).toLocaleString()}</td><td className="p-3 text-green-400">{row.volume_rows}/{row.count}</td><td className="p-3 text-gray-500">{row.first?.split('T')[0]} → {row.last?.split('T')[0]}</td></tr>)}</tbody></table></div>
        {status.length === 0 && <div className="p-8 text-center text-sm text-gray-500">No seeded data yet.</div>}
      </div>
    </div>
  );
};

// ------------------------------------------------------------------- Main --
const AdminPanel = () => {
  const [tab, setTab] = useState('clients');
  const [confirm, setConfirm] = useState(null);

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
    { id: 'clients', label: 'Clients', icon: <Users size={16} /> },
    { id: 'paper', label: 'Paper Control', icon: <Activity size={16} /> },
    { id: 'fees', label: 'Fees', icon: <Percent size={16} /> },
    { id: 'seed', label: 'Seed Data', icon: <Database size={16} /> },
    { id: 'password', label: 'Password', icon: <Lock size={16} /> },
  ];

  return (
    <div className="page-shell font-sans">
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

      <header className="mb-8 flex flex-wrap justify-between items-center gap-3">
        <div>
          <h1 className="text-2xl sm:text-3xl font-extrabold text-blue-400 tracking-tight">Admin Panel</h1>
          <p className="text-gray-500 text-sm mt-1">Client accounts, fees, market data & session control — Kudos v3</p>
        </div>
        <div className="flex items-center gap-2 text-xs bg-gray-800 border border-gray-700 px-3 py-2 rounded-lg">
          <ShieldCheck size={14} className="text-purple-400" />
          <span className="text-gray-400">Signed in as</span>
          <span className="font-bold text-purple-300">{localStorage.getItem('username')}</span>
        </div>
      </header>

      <div className="flex flex-wrap gap-2 mb-6 border-b border-gray-800 pb-4">
        {tabs.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition ${tab === t.id ? 'bg-blue-600 text-white shadow-lg shadow-blue-900/30' : 'bg-gray-800 text-gray-400 hover:text-white hover:bg-gray-700'}`}>
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {tab === 'clients' && <ClientsTab onConfirm={setConfirm} />}
      {tab === 'paper' && <PaperTab />}
      {tab === 'fees' && <FeeSettingsTab />}
      {tab === 'seed' && <SeedDataTab />}
      {tab === 'password' && <ChangePasswordTab />}
    </div>
  );
};

export default AdminPanel;
