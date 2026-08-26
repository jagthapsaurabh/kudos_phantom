import React, { useState, useEffect } from 'react';
import { Play, StopCircle, Activity, ShieldCheck, AlertCircle, TrendingUp, TrendingDown, Wallet } from 'lucide-react';

const TradeCard = ({ trade, type }) => (
  <div className={`p-4 rounded-xl border ${type === 'live' ? 'border-green-500/30 bg-green-500/5' : 'border-blue-500/30 bg-blue-500/5'} transition hover:scale-[1.01]`}>
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

const TradingPage = ({ type }) => {
  const [activeTrades, setActiveTrades] = useState([]);
  const [isRunning, setIsRunning] = useState(false);
  const [selectedStrategy, setSelectedStrategy] = useState('PhantomV2');
  const [loading, setLoading] = useState(false);

  const isLive = type === 'live';

  const toggleTrade = async () => {
    setLoading(true);
    const endpoint = isLive ? '/live-trade/start' : '/paper-trade/start';
    const stopEndpoint = isLive ? '/live-trade/stop' : '/paper-trade/stop';
    
    try {
      if (!isRunning) {
        const res = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${localStorage.getItem('token')}` },
          body: JSON.stringify({ strategy_id: selectedStrategy })
        });
        if (res.ok) setIsRunning(true);
      } else {
        // Note: Simplified stop. In real app, we'd use the instance_key.
        await fetch(stopEndpoint, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` },
          body: JSON.stringify({ strategy_id: selectedStrategy })
        });
        setIsRunning(false);
      }
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  };

  const fetchStatus = async () => {
    const endpoint = isLive ? '/live-trade/status' : '/paper-trade/status';
    try {
      const res = await fetch(endpoint, { headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } });
      const data = await res.json();
      // Mocking the trades for UI demo since the backend returns simple status
      setActiveTrades([
        { symbol: 'BTCUSDT', direction: 1, entry: 64200, current: 64500, pnl: 300, margin: 5000 },
        { symbol: 'ETHUSDT', direction: -1, entry: 3400, current: 3350, pnl: 50, margin: 2000 },
      ]);
    } catch (e) {}
  };

  useEffect(() => {
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="page-shell">
      <div className="flex justify-between items-center mb-8">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-3">
            {isLive ? <ShieldCheck className="text-green-500" size={32} /> : <Activity className="text-blue-500" size={32} />}
            {isLive ? 'Live Trading' : 'Paper Trading'}
          </h1>
          <p className="text-gray-400 text-sm mt-1">
            {isLive ? 'Executing real trades on broker account' : 'Simulating trades with real-time data'}
          </p>
        </div>
        
        <div className="flex gap-4 items-center">
          <div className="flex flex-col">
            <label className="text-xs text-gray-500 uppercase font-bold mb-1">Select Strategy</label>
            <select value={selectedStrategy} onChange={e => setSelectedStrategy(e.target.value)}
                    className="bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500">
              <option value="PhantomV2">Phantom V2.5 (Default)</option>
              <option value="Custom">Custom Strategy</option>
            </select>
          </div>
          <button onClick={toggleTrade} disabled={loading}
                  className={`px-6 py-2 rounded-lg font-bold transition flex items-center gap-2 ${isRunning ? 'bg-red-600 hover:bg-red-500' : 'bg-green-600 hover:bg-green-500'}`}>
            {isRunning ? <><StopCircle size={18} /> Stop Engine</> : <><Play size={18} /> Start Engine</>}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <div className="lg:col-span-1 space-y-6">
          <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
            <h3 className="text-sm font-bold text-gray-400 uppercase mb-4 flex items-center gap-2">
              <Wallet size={16} /> Account Summary
            </h3>
            <div className="space-y-4">
              <div className="flex justify-between items-center">
                <span className="text-gray-500 text-xs">Available Margin</span>
                <span className="font-mono font-bold">₹20,000.00</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-gray-500 text-xs">Used Margin</span>
                <span className="font-mono font-bold text-blue-400">₹7,000.00</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-gray-500 text-xs">Unrealized PnL</span>
                <span className="font-mono font-bold text-green-400">+₹350.00</span>
              </div>
            </div>
          </div>

          <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
            <h3 className="text-sm font-bold text-gray-400 uppercase mb-4 flex items-center gap-2">
              <AlertCircle size={16} /> Engine Status
            </h3>
            <div className="flex items-center gap-3 p-3 bg-gray-900 rounded-lg border border-gray-700">
              <div className={`w-3 h-3 rounded-full ${isRunning ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`}></div>
              <span className="text-sm">{isRunning ? 'Active & Monitoring' : 'Idle'}</span>
            </div>
          </div>
        </div>

        <div className="lg:col-span-3">
          <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700 min-h-[600px]">
            <div className="flex justify-between items-center mb-6">
              <h3 className="text-lg font-bold flex items-center gap-2">
                <Activity size={20} className="text-blue-400" /> 
                Active Positions
              </h3>
              <div className="text-xs text-gray-500">Updating every 5s</div>
            </div>

            {activeTrades.length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {activeTrades.map((t, i) => <TradeCard key={i} trade={t} type={type} />)}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-[400px] text-gray-600">
                <TrendingUp size={48} className="mb-4 opacity-20" />
                <p>No active positions. Engine is scanning for entries...</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default TradingPage;
