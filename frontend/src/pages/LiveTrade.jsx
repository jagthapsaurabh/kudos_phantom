import React, { useState, useEffect } from 'react';
import { Play, StopCircle, Activity, ShieldCheck, AlertCircle, TrendingUp, Wallet } from 'lucide-react';

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
  const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  useEffect(() => {
    fetch(`${API_URL}/strategies`, { headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } })
      .then(res => res.json())
      .then(data => setStrategies(data));
  }, []);

  const startTrade = async () => {
    setLoading(true);
    try {
      await fetch(`${API_URL}/live-trade/start` , {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${localStorage.getItem('token')}` },
        body: JSON.stringify({ strategy_id: selectedStrategy })
      });
    } catch (e) { console.error(e); }
    setLoading(false);
  };

  const stopTrade = async (instanceKey) => {
    try {
      await fetch(`${API_URL}/live-trade/stop`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` },
        body: JSON.stringify({ instance_key: instanceKey })
      });
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
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  const myInstances = status.filter(s => s.strategy_id === selectedStrategy);
  const activeTrades = myInstances.flatMap(inst => inst.active_trades.map(t => ({...t, instance_key: inst.instance_key})));
  const marginUsed = activeTrades.reduce((sum, t) => sum + t.margin, 0);

  return (
    <div className="ml-64 p-8 bg-gray-900 text-white min-h-screen">
      <div className="flex justify-between items-center mb-8">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-3 text-green-400">
            <ShieldCheck size={32} /> Live Trading
          </h1>
          <p className="text-gray-400 text-sm mt-1">Executing real trades on your broker account</p>
        </div>
        <div className="flex gap-4 items-center">
          <div className="flex flex-col">
            <label className="text-xs text-gray-500 uppercase font-bold mb-1">Active Strategy</label>
            <select value={selectedStrategy} onChange={e => setSelectedStrategy(e.target.value)}
                    className="bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-green-500">
              <option value="PhantomV2">Phantom V2.5 (Default)</option>
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
                    <span className="text-xs font-mono">{inst.instance_key.split('_').pop()}</span>
                  </div>
                  <button onClick={() => stopTrade(inst.instance_key)} className="text-red-400 hover:text-red-300 p-1">
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
                <p>No live positions open. Scanning for institutional entries...</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default LiveTrade;
