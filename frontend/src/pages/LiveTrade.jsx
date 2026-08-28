import React, { useState, useEffect } from 'react';
import { Play, StopCircle, Activity, ShieldCheck, AlertCircle, TrendingUp, Wallet } from 'lucide-react';
import TradeScheduleControl from '../components/TradeScheduleControl';
import { API_URL } from '../api';

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
      <div className="bg-gray-800/50 p-1 rounded">Entry: <span className="text-white">{(trade.entry || 0).toFixed(2)}</span></div>
      <div className="bg-gray-800/50 p-1 rounded">Entry Mark: <span className="text-white">{(trade.entry_mark || trade.entry || 0).toFixed(2)}</span></div>
      <div className="bg-gray-800/50 p-1 rounded">Current Mark: <span className="text-white">{(trade.current_mark ?? trade.current ?? 0).toFixed(2)}</span></div>
      <div className="bg-gray-800/50 p-1 rounded">Current Trade: <span className="text-white">{(trade.current || 0).toFixed(2)}</span></div>
      <div className="bg-gray-800/50 p-1 rounded">Chg: <span className={`text-white ${(trade.chg_pct || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>{(trade.chg_pct || 0).toFixed(2)}%</span></div>
      <div className="bg-gray-800/50 p-1 rounded">Margin: <span className="text-white">₹{(trade.margin || 0).toFixed(0)}</span></div>
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
  const [tradeSchedule, setTradeSchedule] = useState({
    skip_new_trades: false,
    skip_days: [],
    skip_blocks: [{ start_day: 'Saturday', start_time: '17:30', end_day: 'Sunday', end_time: '17:30' }],
  });
  const [confirm, setConfirm] = useState(null); // { instanceKey }

  useEffect(() => {
    fetch(`${API_URL}/broker-definitions`, { headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } }).then(r => r.ok ? r.json() : []).then(list => {
      if (Array.isArray(list) && list.length) setSources(list.map(x => ({ code: x.code, name: x.name })));
    }).catch(() => {});
    fetch(`${API_URL}/broker-connections`, { headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } }).then(r => r.ok ? r.json() : []).then(setConnections).catch(() => {});
    fetch(`${API_URL}/broker-settings`, { headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } }).then(r => r.ok ? r.json() : null).then(data => {
      if (data) { setDataSource(data.broker_name || 'Binance'); setCapital(data.initial_capital || 20000); setMarginPct(data.margin_deployment_pct || 25); }
    }).catch(() => {});
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
          skip_new_trades: !!tradeSchedule.skip_new_trades,
          skip_days: tradeSchedule.skip_days || [],
          skip_blocks: tradeSchedule.skip_blocks || [] })
      });
      if (!res.ok) { const err = await res.json().catch(() => ({})); alert(err.detail || 'Could not start live trade'); }
    } catch (e) { console.error(e); alert(e.message); }
    setLoading(false);
  };

  const requestStop = (instanceKey) => setConfirm({ instanceKey });

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
            <select value={dataSource} onChange={e => { setDataSource(e.target.value); setConnectionId(''); }} className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm outline-none">
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
          <button onClick={startTrade} disabled={loading}
                  className="px-6 py-2 rounded-lg font-bold transition bg-green-600 hover:bg-green-500 flex items-center gap-2">
            <Play size={18} /> Start Instance
          </button>
        </div>
      </div>

      {/* Weekly skip schedule (IST) for new entries */}
      <div className="mb-6 rounded-2xl border border-gray-700 bg-gray-800 p-4">
        <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-blue-400">Weekly New-Trade Skip</h3>
        <p className="mb-3 text-[10px] leading-snug text-gray-500">
          When enabled, new live entries are suppressed during the configured IST windows. Open positions continue to be managed.
        </p>
        <TradeScheduleControl value={tradeSchedule} onChange={setTradeSchedule} />
      </div>

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
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div>
                    <span className="text-xs font-mono">{inst.broker_name || 'Binance'} · {inst.instance_key.split('_').pop()}</span>
                  </div>
                  <button onClick={() => requestStop(inst.instance_key)} className="text-red-400 hover:text-red-300 p-1" title="Stop instance">
                    <StopCircle size={16} />
                  </button>
                </div>
              ))}
              {myInstances.length === 0 && <p className="text-xs text-gray-500 text-center">No active instances for this strategy.</p>}
            </div>
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
