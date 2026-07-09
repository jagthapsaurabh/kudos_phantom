import React, { useState, useEffect } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';

const Backtest = () => {
  const [selectedStrategyId, setSelectedStrategyId] = useState('PhantomV2');
  const [strategies, setStrategies] = useState([]);
  const [params, setParams] = useState({
    trend_ema_period: 50, rsi_oversold: 30, rsi_overbought: 70, adx_min: 22,
    macd_hist_min: 25, atr_regime_ratio: 0.5, stop_loss_atr: 2.0, take_profit_atr: 1.2,
    trail_activation_atr: 1.5, trail_distance_atr: 0.5,
  });
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [dates, setDates] = useState({ start: '', end: '' });
  const [history, setHistory] = useState([]);
  const [showHistory, setShowHistory] = useState(false);
  const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  const fetchStrategies = async () => {
    try {
      const res = await fetch(`${API_URL}/strategies`, { 
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } 
      });
      const data = await res.json();
      setStrategies(data);
    } catch (e) {}
  };

  const fetchHistory = async () => {
    try {
      const res = await fetch(`${API_URL}/backtest/history`, { 
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } 
      });
      const data = await res.json();
      setHistory(data);
      if(data){
        setShowHistory(true);
      }
    } catch (e) {}
  };

  useEffect(() => { 
    fetchStrategies();
    fetchHistory(); 
  }, []);

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
          strategy_id: selectedStrategyId, // Pass the selected strategy
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
      setResults(data.results);
      fetchHistory();
    } catch (error) {
      alert(error.message);
    }
    setLoading(false);
  };

  const loadRun = async (runId) => {
    try {
      const res = await fetch(`${API_URL}/backtest/results/${runId}`, { 
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } 
      });
      const data = await res.json();
      setResults({
        ...data.run_details,
        final_equity_inr: data.run_details.final_equity,
        trades: data.trades
      });
      setShowHistory(false);
    } catch (e) { alert("Error loading run"); }
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
    }, {})
  } : null;

  const pieData = stats?.exitDist ? Object.entries(stats.exitDist).map(([name, value]) => ({ name, value })) : [];
  const COLORS = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6'];

  const chartData = results?.equity_curve?.map((val, idx) => ({
    time: idx,
    equity: val
  }));

  return (
    <div className="ml-64 p-8 bg-gray-900 text-white min-h-screen">
      <div className="flex justify-between items-center mb-8">
        <h1 className="text-3xl font-bold text-blue-400">Strategy Optimizer</h1>
        <button onClick={() => setShowHistory(!showHistory)} className="bg-gray-800 px-4 py-2 rounded-lg border border-gray-700 hover:bg-gray-700 transition">
          {showHistory ? 'Close History' : '📜 View History'}
        </button>
      </div>

      {showHistory && (
        <div className="mb-8 bg-gray-800 p-6 rounded-2xl border border-gray-700 animate-in slide-in-from-top-4">
          <h2 className="text-xl font-semibold mb-4">Backtest History</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {history.map(run => (
              <div key={run.id} onClick={() => loadRun(run.id)} className="p-4 bg-gray-700 rounded-xl border border-gray-600 cursor-pointer hover:border-blue-500 transition">
                <div className="font-bold">{run.name}</div>
                <div className="text-xs text-gray-400">{run.start_date?.split('T')[0]} to {run.end_date?.split('T')[0]}</div>
                <div className="text-green-400 font-mono text-sm">ROI: {run.roi?.toFixed(2)}%</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="bg-gray-800 p-6 rounded-xl border border-gray-700 mb-8">
        <div className="flex flex-wrap items-end gap-6">
          <div className="flex gap-4 shrink-0">
            <div className="flex flex-col">
              <label className="text-xs text-gray-400 mb-1">Start Date</label>
              <input type="date" value={dates.start} onChange={e => setDates({...dates, start: e.target.value})}
                     className="bg-gray-700 p-2 rounded border border-gray-600 text-white text-sm outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div className="flex flex-col">
              <label className="text-xs text-gray-400 mb-1">End Date</label>
              <input type="date" value={dates.end} onChange={e => setDates({...dates, end: e.target.value})}
                     className="bg-gray-700 p-2 rounded border border-gray-600 text-white text-sm outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          </div>
          
          <div className="flex flex-col w-48">
            <label className="text-xs text-gray-400 mb-1">Test Strategy</label>
            <select value={selectedStrategyId} onChange={e => setSelectedStrategyId(e.target.value)}
                    className="bg-gray-700 p-2 rounded border border-gray-600 text-white text-sm outline-none focus:ring-2 focus:ring-blue-500">
              <option value="PhantomV2">Phantom V2.5 (Default)</option>
              {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>

          <div className="flex flex-wrap gap-4 flex-1 border-l border-gray-700 pl-6">
            {Object.keys(params).map(key => (
              <div key={key} className="flex  w-32">
                <label className="text-[10px] text-gray-400 uppercase font-bold mb-1">{key.replace('_', ' ')}</label>
                <input type="number" step="0.01" value={params[key]} 
                       onChange={e => setParams({...params, [key]: parseFloat(e.target.value)})}
                       className="bg-gray-700 p-1.5 rounded border border-gray-600 text-white text-xs outline-none focus:ring-1 focus:ring-blue-500" />
              </div>
            ))}
          </div>

          <button onClick={runBacktest} disabled={loading} className="bg-blue-600 px-8 py-2 rounded-lg font-bold hover:bg-blue-500 disabled:opacity-50 transition shrink-0">
            {loading ? 'Computing...' : '🚀 Run Backtest'}
          </button>
        </div>
      </div>

      {results ? (
        <div className="space-y-8">
          <div className="grid grid-cols-4 gap-4">
            <div className="bg-gray-800 p-4 rounded-xl border border-gray-700 text-center">
              <div className="text-gray-400 text-xs">Final Equity</div>
              <div className={`text-xl font-bold ${stats?.finalEquity >= 20000 ? 'text-green-400' : 'text-red-400'}`}>₹{stats?.finalEquity?.toLocaleString()}</div>
            </div>
            <div className="bg-gray-800 p-4 rounded-xl border border-gray-700 text-center">
              <div className="text-gray-400 text-xs">Net Profit</div>
              <div className={`text-xl font-bold ${stats?.netProfit >= 0 ? 'text-green-400' : 'text-red-400'}`}>₹{stats?.netProfit?.toLocaleString()}</div>
            </div>
            <div className="bg-gray-800 p-4 rounded-xl border border-gray-700 text-center">
              <div className="text-gray-400 text-xs">ROI</div>
              <div className={`text-xl font-bold ${stats?.roi >= 0 ? 'text-green-400' : 'text-red-400'}`}>{stats?.roi?.toFixed(2)}%</div>
            </div>
            <div className="bg-gray-800 p-4 rounded-xl border border-gray-700 text-center">
              <div className="text-gray-400 text-xs">Win Rate</div>
              <div className="text-xl font-bold text-purple-400">{stats?.winRate?.toFixed(2)}%</div>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-6">
            <div className="bg-gray-800 p-6 rounded-xl border border-gray-700 h-80">
              <h3 className="text-sm font-semibold text-gray-400 mb-4">Equity Curve</h3>
              <ResponsiveContainer width="100%" height="90%">
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                  <XAxis dataKey="time" hide />
                  <YAxis stroke="#9ca3af" fontSize={10} domain={['auto', 'auto']} />
                  <Tooltip contentStyle={{ backgroundColor: '#1f2937', borderColor: '#374151' }} />
                  <Line type="monotone" dataKey="equity" stroke="#3b82f6" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div className="bg-gray-800 p-6 rounded-xl border border-gray-700 h-80">
              <h3 className="text-sm font-semibold text-gray-400 mb-4">Exit Distribution</h3>
              <ResponsiveContainer width="100%" height="90%">
                <PieChart>
                  <Pie data={pieData} cx="50%" cy="50%" innerRadius={60} outerRadius={80} paddingAngle={5} dataKey="value">
                    {pieData.map((entry, index) => <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />)}
                  </Pie>
                  <Tooltip />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="bg-gray-800 p-6 rounded-xl border border-gray-700">
              <h3 className="text-sm font-semibold text-gray-400 mb-4">Core Metrics</h3>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between"><span className="text-gray-500">Total Trades</span> <span className="font-mono">{stats?.totalTrades}</span></div>
                <div className="flex justify-between"><span className="text-gray-500">Profit Factor</span> <span className="font-mono">{stats?.profitFactor?.toFixed(2)}</span></div>
                <div className="flex justify-between"><span className="text-gray-500">Sharpe Ratio</span> <span className="font-mono">{stats?.sharpe?.toFixed(2)}</span></div>
                <div className="flex justify-between"><span className="text-gray-500">Max DD</span> <span className="font-mono text-red-400">{stats?.maxDD?.toFixed(2)}%</span></div>
                <div className="flex justify-between"><span className="text-gray-500">Longs</span> <span className="font-mono">{stats?.directionDist?.Long || 0} ({((stats?.directionDist?.Long || 0)/stats?.totalTrades * 100).toFixed(1)}%)</span></div>
                <div className="flex justify-between"><span className="text-gray-500">Shorts</span> <span className="font-mono">{stats?.directionDist?.Short || 0} ({((stats?.directionDist?.Short || 0)/stats?.totalTrades * 100).toFixed(1)}%)</span></div>
              </div>
            </div>
          </div>

          <div className="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden">
            <div className="p-4 border-b border-gray-700 bg-gray-700/50">
              <h3 className="font-semibold">Detailed Trade Logs</h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="bg-gray-900 text-gray-400 uppercase">
                  <tr>
                    <th className="p-3">Entry Time</th>
                    <th className="p-3">Exit Time</th>
                    <th className="p-3">Dir</th>
                    <th className="p-3">Entry</th>
                    <th className="p-3">Exit</th>
                    <th className="p-3">Lots</th>
                    <th className="p-3">Margin</th>
                    <th className="p-3">Net PnL</th>
                    <th className="p-3">Reason</th>
                    <th className="p-3">Equity</th>
                    <th className="p-3">Hold</th>
                  </tr>
                </thead>
                <tbody>
                  {results.trades?.map((t, i) => (
                    <tr key={i} className="border-b border-gray-700 hover:bg-gray-700/30 transition">
                      <td className="p-3 font-mono">{t.entry_time ? new Date(t.entry_time).toLocaleString() : 'N/A'}</td>
                      <td className="p-3 font-mono">{t.exit_time ? new Date(t.exit_time).toLocaleString() : 'N/A'}</td>
                      <td className={`p-3 font-bold ${t.direction === 1 ? 'text-green-400' : 'text-red-400'}`}>{t.direction === 1 ? 'LONG' : 'SHORT'}</td>
                      <td className="p-3">{(t.entry_price || 0).toFixed(2)}</td>
                      <td className="p-3">{(t.exit_price || 0).toFixed(2)}</td>
                      <td className="p-3">{(t.lots || 0).toFixed(4)}</td>
                      <td className="p-3">₹{(t.margin || 0).toFixed(0)}</td>
                      <td className={`p-3 font-bold ${t.net_pnl > 0 ? 'text-green-400' : 'text-red-400'}`}>₹{(t.net_pnl || 0).toFixed(2)}</td>
                      <td className="p-3"><span className="bg-gray-900 px-2 py-1 rounded text-[10px]">{t.exit_reason || 'N/A'}</span></td>
                      <td className="p-3">₹{(t.equity_after || 0).toFixed(0)}</td>
                      <td className="p-3">{t.hold_bars || 0}b</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      ) : <div className="bg-gray-800 p-12 rounded-xl border border-gray-700 text-center text-gray-500">Run backtest or select from history to see results.</div>}
    </div>
  );
};

export default Backtest;
