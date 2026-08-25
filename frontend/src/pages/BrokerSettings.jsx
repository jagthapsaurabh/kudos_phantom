import React, { useEffect, useState } from 'react';
import { API_URL } from '../api';
import { KeyRound, Plus, Trash2, ShieldCheck, RefreshCw } from 'lucide-react';

const auth = () => ({ Authorization: `Bearer ${localStorage.getItem('token')}`, 'Content-Type': 'application/json' });
const field = 'w-full bg-gray-900 p-3 rounded-lg border border-gray-700 text-white text-sm outline-none focus:border-blue-500';

const BrokerSettings = () => {
  const [definitions, setDefinitions] = useState([]);
  const [connections, setConnections] = useState([]);
  const [capital, setCapital] = useState(20000);
  const [margin, setMargin] = useState(25);
  const [form, setForm] = useState({ broker_code: 'Binance', label: '', api_key: '', api_secret: '', passphrase: '', is_testnet: false });
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const [defs, cons, settings] = await Promise.all([
        fetch(`${API_URL}/broker-definitions`, { headers: auth() }).then(r => r.json()),
        fetch(`${API_URL}/broker-connections`, { headers: auth() }).then(r => r.json()),
        fetch(`${API_URL}/broker-settings`, { headers: auth() }).then(r => r.json()),
      ]);
      if (Array.isArray(defs) && defs.length) setDefinitions(defs);
      setConnections(Array.isArray(cons) ? cons : []);
      if (settings) { setCapital(settings.initial_capital || 20000); setMargin(settings.margin_deployment_pct || 25); setForm(f => ({ ...f, broker_code: settings.broker_name || 'Binance' })); }
    } catch (e) { setMessage({ ok: false, text: 'Could not load broker settings' }); }
  };
  useEffect(() => { load(); }, []);

  const addConnection = async (e) => {
    e.preventDefault(); setBusy(true); setMessage(null);
    try {
      const res = await fetch(`${API_URL}/broker-connections`, { method: 'POST', headers: auth(), body: JSON.stringify(form) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Unable to save connection');
      setMessage({ ok: true, text: `${form.broker_code} connection added. Secrets are stored server-side and masked here.` });
      setForm(f => ({ ...f, label: '', api_key: '', api_secret: '', passphrase: '' }));
      load();
    } catch (e) { setMessage({ ok: false, text: e.message }); }
    setBusy(false);
  };

  const removeConnection = async (id) => {
    if (!window.confirm('Remove this broker connection? Running instances are not changed.')) return;
    const res = await fetch(`${API_URL}/broker-connections/${id}`, { method: 'DELETE', headers: auth() });
    if (res.ok) load(); else setMessage({ ok: false, text: 'Could not remove connection' });
  };

  const saveCapital = async () => {
    setBusy(true); setMessage(null);
    try {
      const res = await fetch(`${API_URL}/broker-settings`, { method: 'POST', headers: auth(), body: JSON.stringify({
        broker_name: form.broker_code, initial_capital: Number(capital), margin_pct: Number(margin),
      }) });
      if (!res.ok) throw new Error((await res.json()).detail || 'Could not save capital');
      setMessage({ ok: true, text: 'Capital and margin defaults saved.' });
    } catch (e) { setMessage({ ok: false, text: e.message }); }
    setBusy(false);
  };

  return (
    <div className="ml-64 p-8 bg-gray-900 text-white min-h-screen">
      <div className="flex justify-between items-start mb-8">
        <div>
          <h1 className="text-3xl font-bold text-blue-400 flex items-center gap-3"><KeyRound size={30} /> Broker & Data Sources</h1>
          <p className="text-gray-400 text-sm mt-1">Connect more than one exchange. Each paper/live instance can use a different source.</p>
        </div>
        <button onClick={load} className="text-gray-400 hover:text-white flex items-center gap-2 text-xs"><RefreshCw size={14} /> Refresh</button>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 max-w-7xl">
        <form onSubmit={addConnection} className="xl:col-span-2 bg-gray-800 p-6 rounded-2xl border border-gray-700 space-y-4">
          <h2 className="text-lg font-bold text-gray-200 flex items-center gap-2"><Plus size={18} className="text-green-400" /> Add broker connection</h2>
          <p className="text-xs text-gray-500">API secrets are never returned in full. Use read/write trading permissions only when live trading is enabled.</p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div><label className="text-xs text-gray-400">Exchange / broker</label><select className={field} value={form.broker_code} onChange={e => setForm({ ...form, broker_code: e.target.value })}>{definitions.map(d => <option key={d.code} value={d.code}>{d.name}</option>)}</select></div>
            <div><label className="text-xs text-gray-400">Connection label</label><input className={field} placeholder="Primary, Delta live…" value={form.label} onChange={e => setForm({ ...form, label: e.target.value })} /></div>
            <div><label className="text-xs text-gray-400">API key</label><input required className={field} type="password" value={form.api_key} onChange={e => setForm({ ...form, api_key: e.target.value })} /></div>
            <div><label className="text-xs text-gray-400">API secret</label><input required className={field} type="password" value={form.api_secret} onChange={e => setForm({ ...form, api_secret: e.target.value })} /></div>
            <div><label className="text-xs text-gray-400">Passphrase (if required)</label><input className={field} type="password" value={form.passphrase} onChange={e => setForm({ ...form, passphrase: e.target.value })} /></div>
          </div>
          <label className="flex items-center gap-2 text-xs text-gray-300"><input type="checkbox" className="accent-blue-500" checked={form.is_testnet} onChange={e => setForm({ ...form, is_testnet: e.target.checked })} /> Use testnet / sandbox where supported</label>
          <button disabled={busy} className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-6 py-2.5 rounded-lg font-bold text-sm">{busy ? 'Saving…' : 'Save Connection'}</button>
        </form>

        <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
          <h2 className="text-lg font-bold text-gray-200 mb-4">Saved connections</h2>
          <div className="space-y-3">
            {connections.filter(c => !c.legacy).map(c => <div key={c.id} className="p-3 bg-gray-900 rounded-lg border border-gray-700 flex justify-between items-center">
              <div><div className="font-bold text-sm text-white">{c.label}</div><div className="text-[11px] text-gray-500">{c.broker_code} · {c.api_key || 'key saved'} {c.is_testnet ? '· testnet' : ''}</div></div>
              <button onClick={() => removeConnection(c.id)} className="text-red-400 hover:text-red-300 p-2" title="Remove"><Trash2 size={15} /></button>
            </div>)}
            {connections.filter(c => !c.legacy).length === 0 && <div className="text-xs text-gray-500">No saved connections yet.</div>}
          </div>
          <div className="mt-5 p-3 bg-green-900/10 border border-green-900/30 rounded-lg text-xs text-gray-400 flex gap-2"><ShieldCheck size={15} className="text-green-400 shrink-0" /> Multiple connections can run at the same time from Paper Trade or Live Trade.</div>
        </div>

        <div className="xl:col-span-3 bg-gray-800 p-6 rounded-2xl border border-gray-700 max-w-xl">
          <h2 className="text-lg font-bold text-gray-200 mb-2">Trading defaults</h2>
          <p className="text-xs text-gray-500 mb-4">Used when starting a new backtest or trading instance.</p>
          <div className="grid grid-cols-2 gap-4">
            <div><label className="text-xs text-gray-400">Initial capital (₹)</label><input type="number" className={field} value={capital} onChange={e => setCapital(e.target.value)} /></div>
            <div><label className="text-xs text-gray-400">Margin deployment (%)</label><input type="number" min="1" max="100" className={field} value={margin} onChange={e => setMargin(e.target.value)} /></div>
          </div>
          <button onClick={saveCapital} disabled={busy} className="mt-4 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-6 py-2 rounded-lg font-bold text-sm">Save Defaults</button>
        </div>
      </div>
      {message && <div className={`mt-5 text-sm font-semibold ${message.ok ? 'text-green-400' : 'text-red-400'}`}>{message.text}</div>}
    </div>
  );
};
export default BrokerSettings;
