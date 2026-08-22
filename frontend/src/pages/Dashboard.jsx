import React, { useState, useEffect } from 'react';
import { API_URL } from '../api';
import { TrendingUp, BarChart3, Target, Zap, Shield } from 'lucide-react';

const Dashboard = () => {
  const [stats, setStats] = useState({ best_roi: 0, total_runs: 0, avg_win_rate: 0 });
  const [paperStatus, setPaperStatus] = useState([]);
  const username = localStorage.getItem('username') || '—';
  const role = localStorage.getItem('role') || 'client';

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const res = await fetch(`${API_URL}/dashboard/stats`, {
          headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` }
        });
        const data = await res.json();
        setStats(data);
      } catch (e) {}
    };
    const fetchPaper = async () => {
      try {
        const res = await fetch(`${API_URL}/paper-trade/status`, {
          headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` }
        });
        if (res.ok) setPaperStatus(await res.json());
      } catch (e) {}
    };
    fetchStats();
    fetchPaper();
    const interval = setInterval(fetchPaper, 10000);
    return () => clearInterval(interval);
  }, []);

  const runningInstances = paperStatus.filter(s => s.is_running).length;
  const openTrades = paperStatus.flatMap(s => s.active_trades || []).length;

  return (
    <div className="ml-64 p-8 bg-gray-900 text-white min-h-screen">
      {/* Hero */}
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-blue-400">Welcome back, <span className="text-white">{username}</span></h1>
          <p className="text-gray-400 text-sm mt-1">Here's your trading command center overview</p>
        </div>
        <div className="flex items-center gap-2 bg-gray-800 border border-gray-700 px-4 py-2 rounded-lg text-xs">
          <Shield size={14} className={role === 'admin' ? 'text-purple-400' : 'text-blue-400'} />
          <span className="text-gray-400">Role:</span>
          <span className={`font-bold uppercase ${role === 'admin' ? 'text-purple-400' : 'text-blue-400'}`}>{role}</span>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <div className="bg-gradient-to-br from-green-900/20 to-gray-800 p-6 rounded-2xl border border-green-900/30 hover:border-green-700/50 transition-all">
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp size={18} className="text-green-400" />
            <span className="text-gray-400 text-xs uppercase font-bold">Best ROI</span>
          </div>
          <div className="text-3xl font-mono font-bold text-green-400">+{stats.best_roi?.toFixed(2)}%</div>
        </div>
        <div className="bg-gradient-to-br from-blue-900/20 to-gray-800 p-6 rounded-2xl border border-blue-900/30 hover:border-blue-700/50 transition-all">
          <div className="flex items-center gap-2 mb-3">
            <BarChart3 size={18} className="text-blue-400" />
            <span className="text-gray-400 text-xs uppercase font-bold">Total Runs</span>
          </div>
          <div className="text-3xl font-mono font-bold text-white">{stats.total_runs}</div>
        </div>
        <div className="bg-gradient-to-br from-yellow-900/20 to-gray-800 p-6 rounded-2xl border border-yellow-900/30 hover:border-yellow-700/50 transition-all">
          <div className="flex items-center gap-2 mb-3">
            <Target size={18} className="text-yellow-400" />
            <span className="text-gray-400 text-xs uppercase font-bold">Avg Win Rate</span>
          </div>
          <div className="text-3xl font-mono font-bold text-yellow-400">{stats.avg_win_rate?.toFixed(2)}%</div>
        </div>
        <div className="bg-gradient-to-br from-purple-900/20 to-gray-800 p-6 rounded-2xl border border-purple-900/30 hover:border-purple-700/50 transition-all">
          <div className="flex items-center gap-2 mb-3">
            <Zap size={18} className="text-purple-400" />
            <span className="text-gray-400 text-xs uppercase font-bold">Live Sessions</span>
          </div>
          <div className="text-3xl font-mono font-bold text-purple-400">{runningInstances} <span className="text-sm text-gray-500">({openTrades} trades)</span></div>
        </div>
      </div>

      {/* Active Paper Trades */}
      {paperStatus.length > 0 && (
        <div className="mb-8">
          <h2 className="text-lg font-bold text-gray-200 mb-4 flex items-center gap-2">
            <Zap size={18} className="text-purple-400" /> Active Paper Trading Sessions
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {paperStatus.map(s => (
              <div key={s.instance_key} className="bg-gray-800 p-5 rounded-xl border border-gray-700 hover:border-purple-500/30 transition-all">
                <div className="flex justify-between items-center mb-2">
                  <span className="font-bold text-gray-200 text-sm">{s.strategy_id}</span>
                  <span className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded ${s.is_running ? 'bg-green-900/40 text-green-400' : 'bg-red-900/40 text-red-400'}`}>
                    {s.is_running ? 'Running' : 'Stopped'}
                  </span>
                </div>
                <div className="text-xl font-mono text-yellow-400 mb-1">₹{(s.equity_inr || 0).toLocaleString(undefined, {minimumFractionDigits: 2})}</div>
                <div className="text-xs text-gray-500">{(s.active_trades || []).length} open position(s)</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* System Health */}
      <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
        <h2 className="text-lg font-bold text-gray-200 mb-4">System Status</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="flex items-center gap-3 bg-gray-900/50 p-3 rounded-xl">
            <div className="w-3 h-3 bg-green-500 rounded-full animate-pulse"></div>
            <div>
              <div className="text-xs text-gray-400">Binance API</div>
              <div className="text-sm font-bold text-green-400">Connected</div>
            </div>
          </div>
          <div className="flex items-center gap-3 bg-gray-900/50 p-3 rounded-xl">
            <div className="w-3 h-3 bg-green-500 rounded-full animate-pulse"></div>
            <div>
              <div className="text-xs text-gray-400">Trading Engine</div>
              <div className="text-sm font-bold text-green-400">Operational</div>
            </div>
          </div>
          <div className="flex items-center gap-3 bg-gray-900/50 p-3 rounded-xl">
            <div className="w-3 h-3 bg-green-500 rounded-full animate-pulse"></div>
            <div>
              <div className="text-xs text-gray-400">Market Data</div>
              <div className="text-sm font-bold text-green-400">Live</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
