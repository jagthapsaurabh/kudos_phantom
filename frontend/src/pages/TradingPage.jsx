import React, { useState, useEffect, useCallback } from 'react';
import { Play, StopCircle, Activity, ShieldCheck, AlertCircle, TrendingUp, Wallet, RefreshCw } from 'lucide-react';
import api from '../api';
import useToast from '../hooks/useToast';
import ToastContainer from '../components/ToastContainer';
import ErrorBoundary from '../components/ErrorBoundary';
import { useVisibilityPause } from '../hooks/useVisibilityPause';

const TradeCard = ({ trade, type }) => {
  const pnl = trade.pnl ?? trade.unrealized_pnl ?? 0;
  const entry = trade.entry ?? trade.entry_price ?? 0;
  const current = trade.current ?? trade.current_price ?? trade.mark ?? entry;
  const margin = trade.margin ?? trade.margin_inr ?? 0;
  return (
    <div className={`p-4 rounded-xl border ${type === 'live' ? 'border-green-500/30 bg-green-500/5' : 'border-blue-500/30 bg-blue-500/5'} transition hover:scale-[1.01]`}>
      <div className="flex justify-between items-start mb-3">
        <div>
          <div className="text-xs text-gray-500 uppercase font-bold">{trade.symbol || 'BTCUSDT'}</div>
          <div className={`text-lg font-bold ${trade.direction === 1 ? 'text-green-400' : 'text-red-400'}`}>
            {trade.direction === 1 ? 'LONG' : 'SHORT'}
          </div>
        </div>
        <div className="text-right">
          <div className="text-xs text-gray-500 uppercase font-bold">PnL</div>
          <div className={`text-lg font-mono font-bold ${pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {pnl >= 0 ? '+' : ''}{Number(pnl).toFixed(2)}
          </div>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2 text-center text-[10px] uppercase font-medium text-gray-400">
        <div className="bg-gray-800/50 p-1 rounded">Entry: <span className="text-white">{Number(entry).toFixed(2)}</span></div>
        <div className="bg-gray-800/50 p-1 rounded">Current: <span className="text-white">{Number(current).toFixed(2)}</span></div>
        <div className="bg-gray-800/50 p-1 rounded">Margin: <span className="text-white">₹{Number(margin).toFixed(0)}</span></div>
      </div>
      {trade.entry_time && (
        <div className="mt-2 text-[10px] text-gray-500">Entry: {new Date(trade.entry_time).toLocaleString()}</div>
      )}
    </div>
  );
};

const TradingPageInner = ({ type }) => {
  // Pause polling when the tab is hidden to avoid UI lag and wasted bandwidth.
  const isVisible = useVisibilityPause();
  const [activeTrades, setActiveTrades] = useState([]);
  const [isRunning, setIsRunning] = useState(false);
  const [instances, setInstances] = useState([]);
  const [selectedStrategy, setSelectedStrategy] = useState('PhantomV2');
  const [strategies, setStrategies] = useState([]);
  const [loading, setLoading] = useState(false);
  const [statusLoading, setStatusLoading] = useState(false);
  const [accountSummary, setAccountSummary] = useState({ equity: 20000, marginUsed: 0, unrealizedPnl: 0 });
  const [history, setHistory] = useState([]);
  const { toasts, addToast, removeToast, toastFromError } = useToast();

  const isLive = type === 'live';
  const statusEndpoint = isLive ? '/live-trade/status' : '/paper-trade/status';
  const historyEndpoint = isLive ? null : '/paper-trade/history';
  const startEndpoint = isLive ? '/live-trade/start' : '/paper-trade/start';
  const stopEndpoint = isLive ? '/live-trade/stop' : '/paper-trade/stop';

  const fetchStrategies = useCallback(async () => {
    try {
      const res = await api.get('/strategies');
      setStrategies(res.data || []);
    } catch (e) {
      // Non-critical: strategies list optional
    }
  }, []);

  const fetchStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      const res = await api.get(statusEndpoint);
      const data = res.data || [];
      setInstances(data);
      // Aggregate active trades from all instances
      const allTrades = data.flatMap(inst => (inst.active_trades || []).map(t => ({ ...t, instance_key: inst.instance_key })));
      setActiveTrades(allTrades);
      setIsRunning(data.some(inst => inst.is_running));
      // Compute account summary
      const totalEquity = data.reduce((sum, inst) => sum + (inst.equity_inr || 0), 0);
      const totalMargin = allTrades.reduce((sum, t) => sum + (t.margin || t.margin_inr || 0), 0);
      const totalPnl = allTrades.reduce((sum, t) => sum + (t.pnl || t.unrealized_pnl || 0), 0);
      if (data.length > 0) {
        setAccountSummary({ equity: totalEquity, marginUsed: totalMargin, unrealizedPnl: totalPnl });
      }
    } catch (e) {
      if (e.response?.status !== 401) {
        toastFromError(e, 'Failed to fetch trading status');
      }
    }
    setStatusLoading(false);
  }, [statusEndpoint, toastFromError]);

  const fetchHistory = useCallback(async () => {
    if (!historyEndpoint) return;
    try {
      const res = await api.get(historyEndpoint);
      setHistory(res.data || []);
    } catch (e) {
      // Non-critical
    }
  }, [historyEndpoint]);

  useEffect(() => {
    if (!isVisible) return;
    fetchStrategies();
    fetchStatus();
    fetchHistory();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, [fetchStatus, fetchHistory, fetchStrategies, isVisible]);

  const toggleTrade = async () => {
    setLoading(true);
    try {
      if (!isRunning) {
        const res = await api.post(startEndpoint, { strategy_id: selectedStrategy });
        if (res.data) {
          addToast(`${isLive ? 'Live' : 'Paper'} trading started`, { type: 'success' });
          setIsRunning(true);
          setTimeout(fetchStatus, 1000);
        }
      } else {
        // Stop all running instances
        const running = instances.filter(i => i.is_running);
        if (running.length === 0) {
          setIsRunning(false);
        } else {
          for (const inst of running) {
            try {
              await api.post(`${stopEndpoint}?instance_key=${encodeURIComponent(inst.instance_key)}`);
            } catch (e) {
              toastFromError(e, `Failed to stop ${inst.instance_key}`);
            }
          }
          addToast(`${running.length} instance(s) stopped`, { type: 'info' });
          setIsRunning(false);
          setTimeout(fetchStatus, 1000);
        }
      }
    } catch (e) {
      toastFromError(e, `Failed to ${!isRunning ? 'start' : 'stop'} trading`);
    }
    setLoading(false);
  };

  return (
    <div className="page-shell">
      <ToastContainer toasts={toasts} onRemove={removeToast} />

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
            <select
              value={selectedStrategy}
              onChange={e => setSelectedStrategy(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="PhantomV2">Kudos V2.5 (Default)</option>
              {strategies.map(s => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </div>
          <button
            onClick={toggleTrade}
            disabled={loading}
            className={`px-6 py-2 rounded-lg font-bold transition flex items-center gap-2 disabled:opacity-50 ${isRunning ? 'bg-red-600 hover:bg-red-500' : 'bg-green-600 hover:bg-green-500'}`}
          >
            {isRunning ? <><StopCircle size={18} /> Stop Engine</> : <><Play size={18} /> Start Engine</>}
          </button>
          <button
            onClick={fetchStatus}
            disabled={statusLoading}
            className="p-2 rounded-lg bg-gray-800 border border-gray-700 hover:border-gray-600 transition"
            title="Refresh status"
          >
            <RefreshCw size={16} className={statusLoading ? 'animate-spin' : ''} />
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
                <span className="text-gray-500 text-xs">Total Equity</span>
                <span className="font-mono font-bold">₹{accountSummary.equity.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-gray-500 text-xs">Used Margin</span>
                <span className="font-mono font-bold text-blue-400">₹{accountSummary.marginUsed.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-gray-500 text-xs">Unrealized PnL</span>
                <span className={`font-mono font-bold ${accountSummary.unrealizedPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {accountSummary.unrealizedPnl >= 0 ? '+' : ''}₹{accountSummary.unrealizedPnl.toFixed(2)}
                </span>
              </div>
            </div>
          </div>

          <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
            <h3 className="text-sm font-bold text-gray-400 uppercase mb-4 flex items-center gap-2">
              <AlertCircle size={16} /> Engine Status
            </h3>
            <div className="flex items-center gap-3 p-3 bg-gray-900 rounded-lg border border-gray-700">
              <div className={`w-3 h-3 rounded-full ${isRunning ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`}></div>
              <span className="text-sm">{isRunning ? `Active (${instances.filter(i => i.is_running).length} running)` : 'Idle'}</span>
            </div>
            {instances.length > 0 && (
              <div className="mt-3 space-y-1">
                {instances.map(inst => (
                  <div key={inst.instance_key} className="text-[11px] text-gray-500 flex justify-between">
                    <span className="truncate">{inst.strategy_name || inst.strategy_id}</span>
                    <span className={inst.is_running ? 'text-green-400' : 'text-gray-600'}>{inst.is_running ? 'Running' : 'Stopped'}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {history.length > 0 && (
            <div className="bg-gray-800 p-4 rounded-2xl border border-gray-700">
              <h3 className="text-xs font-bold text-gray-400 uppercase mb-3">Recent Sessions</h3>
              <div className="space-y-2">
                {history.slice(0, 5).map(s => (
                  <div key={s.id} className="text-[11px] flex justify-between text-gray-500">
                    <span className="truncate">{s.strategy_name || s.strategy_id}</span>
                    <span className={s.status === 'running' ? 'text-green-400' : 'text-gray-400'}>{s.status}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="lg:col-span-3">
          <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700 min-h-[600px]">
            <div className="flex justify-between items-center mb-6">
              <h3 className="text-lg font-bold flex items-center gap-2">
                <Activity size={20} className="text-blue-400" />
                Active Positions {activeTrades.length > 0 && <span className="text-sm text-gray-500">({activeTrades.length})</span>}
              </h3>
              <div className="text-xs text-gray-500">Updating every 5s</div>
            </div>

            {activeTrades.length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {activeTrades.map((t, i) => <TradeCard key={`${t.instance_key || ''}-${t.symbol || ''}-${i}`} trade={t} type={type} />)}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-[400px] text-gray-600">
                <TrendingUp size={48} className="mb-4 opacity-20" />
                <p>{isRunning ? 'No active positions. Engine is scanning for entries...' : 'Engine idle. Start trading to see positions.'}</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

const TradingPage = (props) => (
  <ErrorBoundary fallbackMessage="Trading page failed to load. Please refresh.">
    <TradingPageInner {...props} />
  </ErrorBoundary>
);

export default TradingPage;
