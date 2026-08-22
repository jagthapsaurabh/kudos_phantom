import React, { useState, useEffect, useRef } from 'react';
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts';
import { createChart } from 'lightweight-charts';
import { API_URL } from '../api';
import { Activity, TrendingUp, AlertCircle, RotateCcw, Trash2, Tag } from 'lucide-react';

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

const Backtest = () => {
  const DEFAULT_PARAMS = {
    trend_ema_period: 50,
    rsi_oversold: 40, rsi_overbought: 60, adx_min: 10, macd_hist_min: 5,
    atr_regime_ratio: 0.5, enable_momentum_entry: true, cooldown_bars: 0,
    stop_loss_atr: 1.2, take_profit_atr: 14.0, trail_activation_atr: 0.8,
    trail_distance_atr: 0.3, breakeven_atr: 0.75,
    leverage: 2, margin_pct: 0.15,
    dd_soft_pct: 8.0, dd_halt_pct: 100.0, dd_resume_pct: 100.0,
  };
  const [selectedStrategyId, setSelectedStrategyId] = useState('PhantomV2');
  const [strategies, setStrategies] = useState([]);
  const [params, setParams] = useState({ ...DEFAULT_PARAMS });
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [dates, setDates] = useState({ start: '2020-07-04', end: '2026-07-04' });
  const [history, setHistory] = useState([]);
  const [showHistory, setShowHistory] = useState(false);
  const [expandedTrade, setExpandedTrade] = useState(null);
  const [runName, setRunName] = useState('');
  const [confirm, setConfirm] = useState(null); // { type, runId, ... }

  // Chart Refs
  const chartContainerRef = useRef();
  const chartRef = useRef();
  const seriesRef = useRef();

  const paramGroups = {
    "Trend & Regime": ["trend_ema_period", "atr_regime_ratio", "cooldown_bars"],
    "Entries (v3)": ["rsi_oversold", "rsi_overbought", "adx_min", "macd_hist_min", "enable_momentum_entry"],
    "Risk & Exit Model": ["stop_loss_atr", "take_profit_atr", "trail_activation_atr", "trail_distance_atr", "breakeven_atr"],
    "Sizing & Drawdown Guard": ["leverage", "margin_pct", "dd_soft_pct", "dd_halt_pct", "dd_resume_pct"],
  };

  const authHeaders = () => ({ 'Authorization': `Bearer ${localStorage.getItem('token')}` });

  const fetchStrategies = async () => {
    try {
      const res = await fetch(`${API_URL}/strategies`, { headers: authHeaders() });
      const data = await res.json();
      setStrategies(data);
    } catch (e) { }
  };

  const fetchHistory = async () => {
    try {
      const res = await fetch(`${API_URL}/backtest/history`, { headers: authHeaders() });
      const data = await res.json();
      setHistory(data);
      if (data && data.length > 0) setShowHistory(true);
    } catch (e) { }
  };

  useEffect(() => {
    if (results && results.equity_curve) {
      const timer = setTimeout(() => { initEquityChart(results.equity_curve); }, 100);
      return () => clearTimeout(timer);
    }
  }, [results]);

  const resetParams = () => setParams({ ...DEFAULT_PARAMS });

  const exportTradesCSV = () => {
    if (!results?.trades?.length) return;
    const cols = [
      'signal_candle_time', 'entry_time', 'exit_time', 'direction', 'setup',
      'candle_type', 'trend_4h', 'rsi14', 'macd_hist', 'adx', 'atr14', 'ema50_1h', 'ema50_4h',
      'entry_price', 'sl', 'tp', 'exit_price', 'exit_reason', 'lots', 'margin', 'notional',
      'gross_pnl', 'fees', 'net_pnl', 'equity_after', 'drawdown', 'hold_bars',
    ];
    const condCols = ['cond_adx_ok', 'cond_macd_hist_ok', 'cond_atr_regime_ok', 'cond_rsi_ok', 'cond_macd_confirm_ok'];
    const header = [...cols, ...condCols];
    const rows = results.trades.map(t => {
      const conds = t.conditions || {};
      const mapped = cols.map(c => t[c] ?? '');
      const mappedConds = [conds.adx_ok, conds.macd_hist_ok, conds.atr_regime_ok, conds.rsi_ok, conds.macd_confirm_ok]
        .map(v => v === undefined || v === null ? '' : (v ? 'TRUE' : 'FALSE'));
      return [...mapped, ...mappedConds];
    });
    const csv = [header.join(','), ...rows.map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(','))].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `phantom_trades_run_${results.name || 'export'}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const runBacktest = async () => {
    setLoading(true);
    try {
      const strategyName = runName.trim() || (selectedStrategyId === 'PhantomV2' ? 'Phantom Optimization' : `Custom Run ${selectedStrategyId}`);
      const response = await fetch(`${API_URL}/backtest`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          params: params,
          strategy_id: selectedStrategyId,
          start_date: dates.start,
          end_date: dates.end,
          strategy_name: strategyName,
        }),
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || "Backtest failed");
      }

      const data = await response.json();
      const runId = data.run_id;

      // Poll for results
      const pollInterval = setInterval(async () => {
        try {
          const res = await fetch(`${API_URL}/backtest/results/${runId}`, { headers: authHeaders() });
          const resultData = await res.json();

          if (resultData.run_details && resultData.run_details.total_trades !== undefined && resultData.run_details.total_trades !== 0) {
            setResults({
              ...resultData.run_details,
              final_equity_inr: resultData.run_details.final_equity,
              trades: resultData.trades
            });
            setLoading(false);
            clearInterval(pollInterval);
            fetchHistory();
            setRunName('');
          }
        } catch (e) { console.error("Polling error:", e); }
      }, 2000);

    } catch (error) {
      alert(error.message);
      setLoading(false);
    }
  };

  const initEquityChart = (equityData) => {
    if (!chartContainerRef.current) return;
    if (chartRef.current) chartRef.current.remove();
    const chart = createChart(chartContainerRef.current, {
      layout: { background: { color: '#111827' }, textColor: '#9ca3af' },
      grid: { vertLines: { color: '#1f2937' }, horzLines: { color: '#1f2937' } },
      width: chartContainerRef.current.clientWidth,
      height: 400,
    });
    const areaSeries = chart.addAreaSeries({
      lineColor: '#3b82f6',
      topColor: 'rgba(59, 130, 246, 0.4)',
      bottomColor: 'rgba(59, 130, 246, 0)',
      lineWidth: 2,
    });
    areaSeries.setData(equityData.map((val, idx) => ({ time: idx, value: val })));
    chart.timeScale().fitContent();
    chartRef.current = chart;
  };

  const loadRun = async (runId) => {
    try {
      const res = await fetch(`${API_URL}/backtest/results/${runId}`, { headers: authHeaders() });
      if (!res.ok) throw new Error((await res.json()).detail || "Run not found");
      const data = await res.json();
      if (!data.run_details) throw new Error("Run details missing");
      setResults({ ...data.run_details, final_equity_inr: data.run_details.final_equity, trades: data.trades });
      setShowHistory(false);
    } catch (e) { alert(`Error loading run: ${e.message}`); }
  };

  const requestDeleteRun = (runId, name) => {
    setConfirm({ type: 'deleteRun', runId, name });
  };

  const requestClearAll = () => {
    setConfirm({ type: 'clearAll' });
  };

  const doConfirm = async () => {
    if (!confirm) return;
    try {
      if (confirm.type === 'deleteRun') {
        const res = await fetch(`${API_URL}/backtest/${confirm.runId}`, { method: 'DELETE', headers: authHeaders() });
        if (res.ok) {
          setHistory(h => h.filter(r => r.id !== confirm.runId));
          if (results && results.id === confirm.runId) setResults(null);
        }
      } else if (confirm.type === 'clearAll') {
        const res = await fetch(`${API_URL}/backtest/clear`, { method: 'DELETE', headers: authHeaders() });
        if (res.ok) { setHistory([]); setResults(null); }
      }
    } catch (e) { alert(e.message); }
    setConfirm(null);
  };

  const stats = results ? {
    totalTrades: results.total_trades,
    finalEquity: results.final_equity_inr,
    netProfit: results.final_equity_inr - 20000,
    roi: results.roi,
    winRate: results.win_rate,
    profitFactor: results.profit_factor,
    sharpe: results.sharpe_ratio,
    maxDD: results.max_drawdown,
    exitDist: results.trades?.reduce((acc, t) => { acc[t.exit_reason] = (acc[t.exit_reason] || 0) + 1; return acc; }, {}),
    directionDist: results.trades?.reduce((acc, t) => { const dir = t.direction === 1 ? 'Long' : 'Short'; acc[dir] = (acc[dir] || 0) + 1; return acc; }, {}),
    rejections: results.rejected_reasons || {}
  } : null;

  const pieData = stats?.exitDist ? Object.entries(stats.exitDist).map(([name, value]) => ({ name, value })) : [];
  const COLORS = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6'];

  useEffect(() => { fetchStrategies(); fetchHistory(); }, []);

  return (
    <div className="ml-64 p-8 bg-gray-900 text-white min-h-screen font-sans">
      <ConfirmModal
        open={!!confirm}
        title={confirm?.type === 'deleteRun' ? 'Delete Backtest Run?' : confirm?.type === 'clearAll' ? 'Clear All Backtest History?' : 'Confirm'}
        message={confirm?.type === 'deleteRun' ? `This will permanently delete "${confirm?.name}" and all its trade data.` : confirm?.type === 'clearAll' ? 'This will permanently delete ALL backtest runs and their trade data. This cannot be undone.' : ''}
        confirmLabel={confirm?.type === 'clearAll' ? 'Yes, Clear All' : 'Yes, Delete'}
        confirmColor="bg-red-600 hover:bg-red-500"
        onCancel={() => setConfirm(null)}
        onConfirm={doConfirm}
      />

      <div className="flex justify-between items-center mb-8">
        <div>
          <h1 className="text-3xl font-extrabold text-blue-400 tracking-tight">Strategy Optimizer</h1>
          <p className="text-gray-500 text-sm">Refine PHANTOM v2.5 parameters and validate equity growth</p>
        </div>
        <div className="flex gap-3">
          <button onClick={requestClearAll} className="bg-red-900/20 text-red-400 px-4 py-2 rounded-lg border border-red-900/50 hover:bg-red-900/40 transition text-sm font-semibold flex items-center gap-2">
            <Trash2 size={14} /> Clear History
          </button>
          <button onClick={() => setShowHistory(!showHistory)} className="bg-gray-800 px-4 py-2 rounded-lg border border-gray-700 hover:bg-gray-700 transition text-sm font-semibold">
            {showHistory ? 'Close History' : '📜 View History'}
          </button>
        </div>
      </div>

      {showHistory && (
        <div className="mb-8 bg-gray-800 p-6 rounded-2xl border border-gray-700 animate-in slide-in-from-top-4">
          <h2 className="text-xl font-semibold mb-4 text-gray-300">Backtest History ({history.length} runs)</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {history.map(run => (
              <div key={run.id} className="p-4 bg-gray-900 rounded-xl border border-gray-700 hover:border-blue-500 transition group relative">
                <div className="cursor-pointer" onClick={() => loadRun(run.id)}>
                  <div className="font-bold text-gray-200 group-hover:text-blue-400 transition">{run.name || 'Unnamed Run'}</div>
                  <div className="text-xs text-gray-500">{run.start_date?.split('T')[0]} → {run.end_date?.split('T')[0]}</div>
                  <div className={`font-mono text-sm mt-2 ${(run.roi || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>ROI: {(run.roi || 0).toFixed(2)}%</div>
                </div>
                <button onClick={(e) => { e.stopPropagation(); requestDeleteRun(run.id, run.name); }}
                        className="absolute top-3 right-3 p-1.5 rounded-lg text-gray-600 hover:text-red-400 hover:bg-red-900/20 transition opacity-0 group-hover:opacity-100"
                        title="Delete this run">
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
          {history.length === 0 && <p className="text-gray-500 text-center py-4">No backtest runs yet.</p>}
        </div>
      )}

      <div className="bg-gray-800 p-8 rounded-2xl border border-gray-700 mb-8 shadow-xl">
        <div className="flex flex-wrap items-start gap-8">
          <div className="flex flex-col gap-4 w-full lg:w-auto shrink-0">
            <div className="flex gap-4">
              <div className="flex flex-col">
                <label className="text-[10px] text-gray-500 uppercase font-bold mb-1">Start Date</label>
                <input type="date" value={dates.start} onChange={e => setDates({ ...dates, start: e.target.value })}
                  className="bg-gray-900 p-2 rounded-lg border border-gray-700 text-white text-sm outline-none focus:ring-2 focus:ring-blue-500 transition" />
              </div>
              <div className="flex flex-col">
                <label className="text-[10px] text-gray-500 uppercase font-bold mb-1">End Date</label>
                <input type="date" value={dates.end} onChange={e => setDates({ ...dates, end: e.target.value })}
                  className="bg-gray-900 p-2 rounded-lg border border-gray-700 text-white text-sm outline-none focus:ring-2 focus:ring-blue-500 transition" />
              </div>
            </div>
            <div className="flex flex-col">
              <label className="text-[10px] text-gray-500 uppercase font-bold mb-1">Test Strategy</label>
              <select value={selectedStrategyId} onChange={e => setSelectedStrategyId(e.target.value)}
                className="bg-gray-900 p-2 rounded-lg border border-gray-700 text-white text-sm outline-none focus:ring-2 focus:ring-blue-500 transition">
                <option value="PhantomV2">Phantom V2.5 (Default)</option>
                {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </div>
            <div className="flex flex-col">
              <label className="text-[10px] text-gray-500 uppercase font-bold mb-1 flex items-center gap-1"><Tag size={10} /> Run Name (optional)</label>
              <input type="text" placeholder="e.g. Aggressive RSI Test" value={runName} onChange={e => setRunName(e.target.value)}
                className="bg-gray-900 p-2 rounded-lg border border-gray-700 text-white text-sm outline-none focus:ring-2 focus:ring-blue-500 transition" maxLength={60} />
            </div>
          </div>

          {selectedStrategyId === 'PhantomV2' && (
            <div className="flex-1 grid grid-cols-1 md:grid-cols-3 gap-6 border-l border-gray-700 pl-8">
              {Object.entries(paramGroups).map(([groupName, fields]) => (
                <div key={groupName} className="space-y-3">
                  <h3 className="text-xs font-bold text-blue-400 uppercase tracking-wider">{groupName}</h3>
                  <div className="grid grid-cols-1 gap-3">
                    {fields.map(field => (
                      <div key={field} className="flex flex-col">
                        <label className="text-[10px] text-gray-500 uppercase mb-1">{field.replace(/_/g, ' ')}</label>
                        {field === 'enable_momentum_entry' ? (
                          <label className="flex items-center gap-2 bg-gray-900 p-2 rounded-lg border border-gray-700 text-xs text-gray-300 cursor-pointer">
                            <input type="checkbox" checked={!!params[field]}
                              onChange={e => setParams({ ...params, [field]: e.target.checked })}
                              className="accent-blue-500" />
                            Momentum entries
                          </label>
                        ) : (
                          <input type="number" step="0.01" value={params[field]}
                            onChange={e => setParams({ ...params, [field]: parseFloat(e.target.value) })}
                            className="bg-gray-900 p-2 rounded-lg border border-gray-700 text-white text-xs outline-none focus:border-blue-500 transition" />
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="flex flex-col justify-end gap-3 shrink-0">
            <button onClick={resetParams} className="flex items-center justify-center gap-2 text-gray-500 hover:text-white text-xs transition py-2">
              <RotateCcw size={14} /> Reset to Defaults
            </button>
            <button onClick={runBacktest} disabled={loading} className="bg-blue-600 px-8 py-3 rounded-xl font-bold hover:bg-blue-500 disabled:opacity-50 transition shadow-lg shadow-blue-900/20 flex items-center justify-center gap-2">
              {loading ? <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div> : '🚀 Run Backtest'}
            </button>
          </div>
        </div>
      </div>

      {results ? (
        <div className="space-y-8 animate-in fade-in duration-500">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <StatCard label="Final Equity" value={`₹${stats?.finalEquity?.toLocaleString()}`} color={stats?.finalEquity >= 20000 ? 'text-green-400' : 'text-red-400'} />
            <StatCard label="Net Profit" value={`₹${stats?.netProfit?.toLocaleString()}`} color={stats?.netProfit >= 0 ? 'text-green-400' : 'text-red-400'} />
            <StatCard label="ROI" value={`${stats?.roi?.toFixed(2)}%`} color={stats?.roi >= 0 ? 'text-green-400' : 'text-red-400'} />
            <StatCard label="Win Rate" value={`${stats?.winRate?.toFixed(2)}%`} color="text-purple-400" />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700 shadow-xl flex flex-col">
              <h3 className="text-sm font-semibold text-gray-400 mb-4">Exit Distribution</h3>
              <div className="flex-1">
                <ResponsiveContainer width="100%" height={250}>
                  <PieChart>
                    <Pie data={pieData} cx="50%" cy="50%" innerRadius={60} outerRadius={80} paddingAngle={5} dataKey="value">
                      {pieData.map((entry, index) => <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />)}
                    </Pie>
                    <Tooltip contentStyle={{ backgroundColor: '#111827', borderColor: '#374151', color: '#fff' }} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="mt-4 space-y-2">
                {Object.entries(stats?.exitDist || {}).map(([reason, count]) => (
                  <div key={reason} className="flex justify-between text-xs">
                    <span className="text-gray-500">{reason}</span>
                    <span className="font-mono font-bold">{count}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 bg-gray-800 rounded-2xl border border-gray-700 overflow-hidden shadow-xl">
              <div className="p-4 border-b border-gray-700 bg-gray-700/30 flex justify-between items-center">
                <h3 className="font-bold text-gray-200">Detailed Trade Logs <span className="text-xs text-gray-500 font-normal">(entry conditions per candle)</span></h3>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] bg-gray-900 px-2 py-1 rounded text-gray-400">{results.trades?.length || 0} trades</span>
                  <button onClick={exportTradesCSV} className="text-[10px] bg-blue-600 hover:bg-blue-500 px-3 py-1 rounded font-bold transition">⬇ CSV Export</button>
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="bg-gray-900 text-gray-500 uppercase">
                    <tr>
                      <th className="p-3 font-semibold">Signal Candle</th>
                      <th className="p-3 font-semibold">Entry</th>
                      <th className="p-3 font-semibold">Exit</th>
                      <th className="p-3 font-semibold">Dir</th>
                      <th className="p-3 font-semibold">Setup</th>
                      <th className="p-3 font-semibold">Candle</th>
                      <th className="p-3 font-semibold">4H Trend</th>
                      <th className="p-3 font-semibold">RSI</th>
                      <th className="p-3 font-semibold">ADX</th>
                      <th className="p-3 font-semibold">Net PnL</th>
                      <th className="p-3 font-semibold">Reason</th>
                      <th className="p-3 font-semibold">Cond.</th>
                    </tr>
                  </thead>
                  <tbody>
                    {results.trades?.map((t, i) => (
                      <React.Fragment key={i}>
                        <tr className="border-b border-gray-700 hover:bg-gray-700/30 transition cursor-pointer"
                            onClick={() => setExpandedTrade(expandedTrade === i ? null : i)}>
                          <td className="p-3 font-mono text-gray-400">{t.signal_candle_time ? new Date(t.signal_candle_time).toLocaleString() : (t.entry_time ? new Date(t.entry_time).toLocaleString() : 'N/A')}</td>
                          <td className="p-3">{(t.entry_price || 0).toFixed(2)}</td>
                          <td className="p-3">{(t.exit_price || 0).toFixed(2)}</td>
                          <td className={`p-3 font-bold ${t.direction === 1 ? 'text-green-400' : 'text-red-400'}`}>{t.direction === 1 ? 'L' : 'S'}</td>
                          <td className="p-3"><span className={`px-2 py-0.5 rounded text-[10px] font-bold ${t.setup === 'MOMENTUM' ? 'bg-purple-900/40 text-purple-300' : 'bg-blue-900/40 text-blue-300'}`}>{t.setup || '—'}</span></td>
                          <td className={`p-3 ${t.candle_type === 'GREEN' ? 'text-green-400' : t.candle_type === 'RED' ? 'text-red-400' : 'text-gray-400'}`}>{t.candle_type || '—'}</td>
                          <td className={`p-3 ${t.trend_4h === 'UP' ? 'text-green-400' : 'text-red-400'}`}>{t.trend_4h || '—'}</td>
                          <td className="p-3">{t.rsi14 != null ? t.rsi14.toFixed(1) : '—'}</td>
                          <td className="p-3">{t.adx != null ? t.adx.toFixed(1) : '—'}</td>
                          <td className={`p-3 font-bold ${t.net_pnl > 0 ? 'text-green-400' : 'text-red-400'}`}>₹{(t.net_pnl || 0).toFixed(2)}</td>
                          <td className="p-3"><span className="bg-gray-900 px-2 py-1 rounded text-[10px] text-gray-400 border border-gray-700">{t.exit_reason || 'N/A'}</span></td>
                          <td className="p-3 text-gray-500">{expandedTrade === i ? '▼' : '▶'}</td>
                        </tr>
                        {expandedTrade === i && (
                          <tr className="bg-gray-900/60 border-b border-gray-700">
                            <td colSpan={12} className="p-4">
                              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-[11px]">
                                <div>
                                  <div className="text-gray-500 uppercase text-[9px] font-bold mb-1">Entry Conditions</div>
                                  <div className="flex flex-wrap gap-1">
                                    <CondChip ok={t.conditions?.adx_ok} label={`ADX≥min (${t.adx?.toFixed(1) ?? '?'})`} />
                                    <CondChip ok={t.conditions?.macd_hist_ok} label="MACD-hist mag" />
                                    <CondChip ok={t.conditions?.atr_regime_ok} label="ATR regime" />
                                    <CondChip ok={t.conditions?.rsi_ok} label="RSI trigger" />
                                    <CondChip ok={t.conditions?.macd_confirm_ok} label="MACD confirm" />
                                  </div>
                                </div>
                                <div>
                                  <div className="text-gray-500 uppercase text-[9px] font-bold mb-1">Indicators @ Signal</div>
                                  <div className="font-mono text-gray-300 space-y-0.5">
                                    <div>MACD-hist: {t.macd_hist?.toFixed(2) ?? '—'}</div>
                                    <div>ATR14: {t.atr14?.toFixed(2) ?? '—'}</div>
                                    <div>EMA50 1h: {t.ema50_1h?.toFixed(2) ?? '—'}</div>
                                    <div>EMA50 4h: {t.ema50_4h?.toFixed(2) ?? '—'}</div>
                                  </div>
                                </div>
                                <div>
                                  <div className="text-gray-500 uppercase text-[9px] font-bold mb-1">Risk Model</div>
                                  <div className="font-mono text-gray-300 space-y-0.5">
                                    <div>SL: {t.sl?.toFixed(2) ?? '—'}</div>
                                    <div>TP: {t.tp?.toFixed(2) ?? '—'}</div>
                                    <div>Margin: ₹{(t.margin || 0).toFixed(0)} ({((t.margin_pct_used || 0) * 100).toFixed(1)}%)</div>
                                    <div>Lots: {(t.lots || 0).toFixed(4)} • DD@entry: {(t.entry_dd_pct || 0).toFixed(1)}%</div>
                                  </div>
                                </div>
                                <div>
                                  <div className="text-gray-500 uppercase text-[9px] font-bold mb-1">Result</div>
                                  <div className="font-mono text-gray-300 space-y-0.5">
                                    <div>Gross: ₹{(t.gross_pnl || 0).toFixed(2)} • Fees: ₹{(t.fees || 0).toFixed(2)}</div>
                                    <div className={t.net_pnl > 0 ? 'text-green-400' : 'text-red-400'}>Net: ₹{(t.net_pnl || 0).toFixed(2)}</div>
                                    <div>Exit: {t.exit_time ? new Date(t.exit_time).toLocaleString() : '—'} ({t.hold_bars || 0} bars)</div>
                                    <div>Equity: ₹{(t.equity_after || 0).toFixed(0)} • DD: {(t.drawdown || 0).toFixed(2)}%</div>
                                  </div>
                                </div>
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700 shadow-xl">
              <h3 className="text-sm font-semibold text-gray-400 mb-6 flex items-center gap-2">
                <Activity size={16} /> Core Metrics
              </h3>
              <div className="space-y-4">
                <MetricRow label="Total Trades" value={stats?.totalTrades} />
                <MetricRow label="Profit Factor" value={`${stats?.profitFactor?.toFixed(2)}`} />
                <MetricRow label="Sharpe Ratio" value={`${stats?.sharpe?.toFixed(2)}`} />
                <MetricRow label="Max Drawdown" value={`${stats?.maxDD?.toFixed(2)}%`} color="text-red-400" />
                <MetricRow label="Longs" value={`${stats?.directionDist?.Long || 0} (${((stats?.directionDist?.Long || 0) / stats?.totalTrades * 100).toFixed(1)}%)`} />
                <MetricRow label="Shorts" value={`${stats?.directionDist?.Short || 0} (${((stats?.directionDist?.Short || 0) / stats?.totalTrades * 100).toFixed(1)}%)`} />

                <div className="border-t border-gray-700 mt-6 pt-6">
                  <div className="flex justify-between items-center mb-3">
                    <span className="text-xs font-bold text-gray-500 uppercase">Rejected Signals</span>
                    <span className="bg-red-900/30 text-red-400 px-2 py-0.5 rounded text-xs font-bold border border-red-900/50">
                      {Object.values(stats?.rejections || {}).reduce((a, b) => a + b, 0)}
                    </span>
                  </div>
                  <div className="space-y-2">
                    {Object.entries(stats?.rejections || {}).map(([reason, count]) => (
                      <div key={reason} className="flex justify-between items-center p-2 bg-gray-900 rounded-lg border border-gray-700/50">
                        <span className="text-[10px] text-gray-500 italic">{reason}</span>
                        <span className="text-xs font-mono font-bold text-gray-300">{count}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="bg-gray-800 p-20 rounded-2xl border border-gray-700 text-center shadow-inner">
          <div className="bg-gray-900 w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-4 text-gray-600">
            <TrendingUp size={32} />
          </div>
          <h3 className="text-xl font-bold text-gray-400">No Backtest Data</h3>
          <p className="text-gray-600 text-sm mt-2 max-w-md mx-auto">Configure your strategy parameters and date range, then hit "Run Backtest" to analyze the equity curve.</p>
        </div>
      )}
    </div>
  );
};

const StatCard = ({ label, value, color }) => (
  <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700 text-center shadow-lg hover:border-blue-500/50 transition-all group">
    <div className="text-gray-500 text-xs uppercase font-bold mb-2 group-hover:text-gray-400 transition">{label}</div>
    <div className={`text-2xl font-extrabold font-mono ${color}`}>{value}</div>
  </div>
);

const MetricRow = ({ label, value, color = "text-gray-200" }) => (
  <div className="flex justify-between items-center py-1">
    <span className="text-xs text-gray-500">{label}</span>
    <span className={`text-xs font-mono font-bold ${color}`}>{value}</span>
  </div>
);

const CondChip = ({ ok, label }) => (
  <span className={`px-2 py-0.5 rounded text-[10px] font-semibold border ${
    ok ? 'bg-green-900/30 text-green-400 border-green-800/40'
       : ok === false || ok === 0
         ? 'bg-red-900/30 text-red-400 border-red-800/40'
         : 'bg-gray-800 text-gray-500 border-gray-700'}`}>
    {ok ? '✓' : (ok === false || ok === 0 ? '✗' : '·')} {label}
  </span>
);

export default Backtest;
