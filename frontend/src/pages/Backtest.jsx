import React, { useState, useEffect, useRef } from 'react';
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts';
import { createChart } from 'lightweight-charts';
import { API_URL } from '../api';
import { Activity, TrendingUp, AlertCircle, RotateCcw } from 'lucide-react';

const Backtest = () => {
  const [selectedStrategyId, setSelectedStrategyId] = useState('PhantomV2');
  const [strategies, setStrategies] = useState([]);
  const [params, setParams] = useState({
    trend_ema_period: 50, rsi_oversold: 30, rsi_overbought: 70, adx_min: 22,
    macd_hist_min: 25, atr_regime_ratio: 0.5, stop_loss_atr: 2.0, take_profit_atr: 10.0,
    trail_activation_atr: 1.5, trail_distance_atr: 0.5,
  });
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [dates, setDates] = useState({ start: '2023-04-01', end: '2026-04-01' });
  const [history, setHistory] = useState([]);
  const [showHistory, setShowHistory] = useState(false);

  // Chart Refs
  const chartContainerRef = useRef();
  const chartRef = useRef();
  const seriesRef = useRef();

  const paramGroups = {
    "Trend Alignment": ["trend_ema_period"],
    "Momentum & Volatility": ["rsi_oversold", "rsi_overbought", "adx_min", "macd_hist_min", "atr_regime_ratio"],
    "Risk & Exit Model": ["stop_loss_atr", "take_profit_atr", "trail_activation_atr", "trail_distance_atr"]
  };

  const fetchStrategies = async () => {
    try {
      const res = await fetch(`${API_URL}/strategies`, {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` }
      });
      const data = await res.json();
      setStrategies(data);
    } catch (e) { }
  };

  const fetchHistory = async () => {
    try {
      const res = await fetch(`${API_URL}/backtest/history`, {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` }
      });
      const data = await res.json();
      setHistory(data);
      if (data) {
        setShowHistory(true);
      }
    } catch (e) { }
  };

  useEffect(() => {
    if (results && results.equity_curve) {
      const timer = setTimeout(() => {
        initEquityChart(results.equity_curve);
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [results]);

  const resetParams = () => {
    setParams({
      trend_ema_period: 50, rsi_oversold: 30, rsi_overbought: 70, adx_min: 22,
      macd_hist_min: 25, atr_regime_ratio: 0.5, stop_loss_atr: 2.0, take_profit_atr: 10.0,
      trail_activation_atr: 1.5, trail_distance_atr: 0.5,
    });
  };

  const runBacktest = async () => {
    setLoading(true);
    try {
      const response = await fetch(`${API_URL}/backtest`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('token')}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          params: params,
          strategy_id: selectedStrategyId,
          start_date: dates.start,
          end_date: dates.end,
          strategy_name: selectedStrategyId === 'PhantomV2' ? "Phantom Optimization" : `Custom Run ${selectedStrategyId}`,
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
          const res = await fetch(`${API_URL}/backtest/results/${runId}`, {
            headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` }
          });
          const resultData = await res.json();

          // Check if results are fully computed (ROI should not be 0.0 if trades occurred)
          if (resultData.run_details && resultData.run_details.total_trades !== undefined && resultData.run_details.total_trades !== 0) {
            setResults({
              ...resultData.run_details,
              final_equity_inr: resultData.run_details.final_equity,
              trades: resultData.trades
            });
            initEquityChart(resultData.run_details.equity_curve);
            setLoading(false);
            clearInterval(pollInterval);
            fetchHistory();
          }
        } catch (e) {
          console.error("Polling error:", e);
        }
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

    const formattedData = equityData.map((val, idx) => ({
      time: idx,
      value: val,
    }));

    areaSeries.setData(formattedData);
    chart.timeScale().fitContent();
    chartRef.current = chart;
  };

  const loadRun = async (runId) => {
    try {
      const res = await fetch(`${API_URL}/backtest/results/${runId}`, {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` }
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Run not found on server");
      }

      const data = await res.json();

      if (!data.run_details) {
        throw new Error("Run details are missing from the server response");
      }

      setResults({
        ...data.run_details,
        final_equity_inr: data.run_details.final_equity,
        trades: data.trades
      });
      setShowHistory(false);

      // if (data.run_details.equity_curve && Array.isArray(data.run_details.equity_curve)) {
      //   initEquityChart(data.run_details.equity_curve);
      // } else {
      //   console.warn("No equity curve data available for this run.");
      // }
    } catch (e) {
      console.error("Load Run Error:", e);
      alert(`Error loading run: ${e.message}`);
    }
  };

  const clearHistory = async () => {
    if (!window.confirm("Are you sure you want to clear all backtest history?")) return;
    try {
      const res = await fetch(`${API_URL}/backtest/clear`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` }
      });
      if (res.ok) {
        setHistory([]);
        setResults(null);
      }
    } catch (e) { alert(e.message); }
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
    exitDist: results.trades?.reduce((acc, t) => {
      acc[t.exit_reason] = (acc[t.exit_reason] || 0) + 1;
      return acc;
    }, {}),
    directionDist: results.trades?.reduce((acc, t) => {
      const dir = t.direction === 1 ? 'Long' : 'Short';
      acc[dir] = (acc[dir] || 0) + 1;
      return acc;
    }, {}),
    rejections: results.rejected_reasons || {}
  } : null;

  const pieData = stats?.exitDist ? Object.entries(stats.exitDist).map(([name, value]) => ({ name, value })) : [];
  const COLORS = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6'];

  useEffect(() => {
    fetchStrategies();
    fetchHistory();
  }, []);

  return (
    <div className="ml-64 p-8 bg-gray-900 text-white min-h-screen font-sans">
      <div className="flex justify-between items-center mb-8">
        <div>
          <h1 className="text-3xl font-extrabold text-blue-400 tracking-tight">Strategy Optimizer</h1>
          <p className="text-gray-500 text-sm">Refine PHANTOM v2.5 parameters and validate equity growth</p>
        </div>
        <div className="flex gap-3">
          <button onClick={clearHistory} className="bg-red-900/20 text-red-400 px-4 py-2 rounded-lg border border-red-900/50 hover:bg-red-900/40 transition text-sm font-semibold">
            🗑️ Clear History
          </button>
          <button onClick={() => setShowHistory(!showHistory)} className="bg-gray-800 px-4 py-2 rounded-lg border border-gray-700 hover:bg-gray-700 transition text-sm font-semibold">
            {showHistory ? 'Close History' : '📜 View History'}
          </button>
        </div>
      </div>

      {showHistory && (
        <div className="mb-8 bg-gray-800 p-6 rounded-2xl border border-gray-700 animate-in slide-in-from-top-4">
          <h2 className="text-xl font-semibold mb-4 text-gray-300">Backtest History</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {history.map(run => (
              <div key={run.id} onClick={() => loadRun(run.id)} className="p-4 bg-gray-900 rounded-xl border border-gray-700 cursor-pointer hover:border-blue-500 transition group">
                <div className="font-bold text-gray-200 group-hover:text-blue-400 transition">{run.name}</div>
                <div className="text-xs text-gray-500">{run.start_date?.split('T')[0]} to {run.end_date?.split('T')[0]}</div>
                <div className="text-green-400 font-mono text-sm mt-2">ROI: {run.roi?.toFixed(2)}%</div>
              </div>
            ))}
          </div>
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
          </div>

          {selectedStrategyId === 'PhantomV2' && (
            <div className="flex-1 grid grid-cols-1 md:grid-cols-3 gap-6 border-l border-gray-700 pl-8">
              {Object.entries(paramGroups).map(([groupName, fields]) => (
                <div key={groupName} className="space-y-3">
                  <h3 className="text-xs font-bold text-blue-400 uppercase tracking-wider">{groupName}</h3>
                  <div className="grid grid-cols-1 gap-3">
                    {fields.map(field => (
                      <div key={field} className="flex flex-col">
                        <label className="text-[10px] text-gray-500 uppercase mb-1">{field.replace('_', ' ')}</label>
                        <input type="number" step="0.01" value={params[field]}
                          onChange={e => setParams({ ...params, [field]: parseFloat(e.target.value) })}
                          className="bg-gray-900 p-2 rounded-lg border border-gray-700 text-white text-xs outline-none focus:border-blue-500 transition" />
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
            {/* <div className="lg:col-span-2 bg-gray-800 p-6 rounded-2xl border border-gray-700 shadow-xl">
              <div className="flex justify-between items-center mb-4">
                <h3 className="text-sm font-semibold text-gray-400 flex items-center gap-2">
                  <TrendingUp size={16} /> Equity Growth Map
                </h3>
                <span className="text-[10px] text-gray-500 uppercase">Interactive TradingView-style Chart</span>
              </div>
              <div ref={chartContainerRef} className="w-full" />
            </div> */}

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
                <h3 className="font-bold text-gray-200">Detailed Trade Logs</h3>
                <span className="text-[10px] bg-gray-900 px-2 py-1 rounded text-gray-400">Sorted by Time</span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="bg-gray-900 text-gray-500 uppercase">
                    <tr>
                      <th className="p-4 font-semibold">Entry Time</th>
                      <th className="p-4 font-semibold">Exit Time</th>
                      <th className="p-4 font-semibold">Dir</th>
                      <th className="p-4 font-semibold">Entry</th>
                      <th className="p-4 font-semibold">Exit</th>
                      <th className="p-4 font-semibold">Lots</th>
                      <th className="p-4 font-semibold">Margin</th>
                      <th className="p-4 font-semibold">Net PnL</th>
                      <th className="p-4 font-semibold">Reason</th>
                      <th className="p-4 font-semibold">Equity</th>
                      <th className="p-4 font-semibold">Hold</th>
                    </tr>
                  </thead>
                  <tbody>
                    {results.trades?.map((t, i) => (
                      <tr key={i} className="border-b border-gray-700 hover:bg-gray-700/30 transition">
                        <td className="p-4 font-mono text-gray-400">{t.entry_time ? new Date(t.entry_time).toLocaleString() : 'N/A'}</td>
                        <td className="p-4 font-mono text-gray-400">{t.exit_time ? new Date(t.exit_time).toLocaleString() : 'N/A'}</td>
                        <td className={`p-4 font-bold ${t.direction === 1 ? 'text-green-400' : 'text-red-400'}`}>{t.direction === 1 ? 'LONG' : 'SHORT'}</td>
                        <td className="p-4">{(t.entry_price || 0).toFixed(2)}</td>
                        <td className="p-4">{(t.exit_price || 0).toFixed(2)}</td>
                        <td className="p-4">{(t.lots || 0).toFixed(4)}</td>
                        <td className="p-4">₹{(t.margin || 0).toFixed(0)}</td>
                        <td className={`p-4 font-bold ${t.net_pnl > 0 ? 'text-green-400' : 'text-red-400'}`}>₹{(t.net_pnl || 0).toFixed(2)}</td>
                        <td className="p-4"><span className="bg-gray-900 px-2 py-1 rounded text-[10px] text-gray-400 border border-gray-700">{t.exit_reason || 'N/A'}</span></td>
                        <td className="p-4">₹{(t.equity_after || 0).toFixed(0)}</td>
                        <td className="p-4">{t.hold_bars || 0}b</td>
                      </tr>
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

export default Backtest;
