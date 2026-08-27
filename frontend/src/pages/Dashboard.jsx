import React, { useState, useEffect } from 'react';
import { API_URL } from '../api';
import { TrendingUp, BarChart3, Target, Zap, Shield, AlertTriangle } from 'lucide-react';

const num = (v, d = 2) => {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(d) : (0).toFixed(d);
};

const Dashboard = () => {
  const [stats, setStats] = useState({ best_roi: 0, total_runs: 0, avg_win_rate: 0, completed_runs: 0 });
  const [paperStatus, setPaperStatus] = useState([]);
  const [liveStatus, setLiveStatus] = useState([]);
  const [statsError, setStatsError] = useState('');
  const [loading, setLoading] = useState(true);
  const [apiOnline, setApiOnline] = useState(null);
  const username = localStorage.getItem('username') || '—';
  const role = localStorage.getItem('role') || 'client';

  useEffect(() => {
    const headers = { 'Authorization': `Bearer ${localStorage.getItem('token')}` };

    const fetchStats = async () => {
      try {
        const res = await fetch(`${API_URL}/dashboard/stats`, { headers });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || `Stats failed (${res.status})`);
        }
        const data = await res.json();
        setStats({
          best_roi: Number(data.best_roi) || 0,
          avg_roi: Number(data.avg_roi) || 0,
          total_runs: Number(data.total_runs) || 0,
          completed_runs: Number(data.completed_runs) || 0,
          avg_win_rate: Number(data.avg_win_rate) || 0,
          last_run: data.last_run || null,
        });
        setStatsError('');
      } catch (e) {
        setStatsError(e.message || 'Could not load dashboard stats');
      } finally {
        setLoading(false);
      }
    };

    const fetchSessions = async () => {
      try {
        const [paperRes, liveRes] = await Promise.all([
          fetch(`${API_URL}/paper-trade/status`, { headers }),
          fetch(`${API_URL}/live-trade/status`, { headers }),
        ]);
        if (paperRes.ok) {
          const data = await paperRes.json();
          setPaperStatus(Array.isArray(data) ? data : []);
        }
        if (liveRes.ok) {
          const data = await liveRes.json();
          setLiveStatus(Array.isArray(data) ? data : []);
        }
      } catch (e) {}
    };

    const pingHealth = async () => {
      try {
        const res = await fetch(`${API_URL}/`);
        setApiOnline(res.ok);
      } catch (e) {
        setApiOnline(false);
      }
    };

    fetchStats();
    fetchSessions();
    pingHealth();
    const interval = setInterval(() => { fetchSessions(); pingHealth(); }, 10000);
    return () => clearInterval(interval);
  }, []);

  const sessions = [...paperStatus, ...liveStatus];
  const runningInstances = sessions.filter(s => s.is_running).length;
  const openTrades = sessions.flatMap(s => s.active_trades || []).length;
  const bestRoi = Number(stats.best_roi) || 0;

  return (
    <div className="page-shell">
      <div className="mb-6 sm:mb-8 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold text-blue-400">Welcome back, <span className="text-white">{username}</span></h1>
          <p className="text-gray-400 text-sm mt-1">Here's your trading command center overview</p>
        </div>
        <div className="flex items-center gap-2 bg-gray-800 border border-gray-700 px-4 py-2 rounded-lg text-xs w-fit">
          <Shield size={14} className={role === 'admin' ? 'text-purple-400' : 'text-blue-400'} />
          <span className="text-gray-400">Role:</span>
          <span className={`font-bold uppercase ${role === 'admin' ? 'text-purple-400' : 'text-blue-400'}`}>{role}</span>
        </div>
      </div>

      {statsError && (
        <div className="mb-4 flex items-start gap-2 bg-red-900/20 border border-red-800/50 text-red-300 text-sm px-4 py-3 rounded-xl">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <div>
            <div className="font-semibold">Dashboard stats unavailable</div>
            <div className="text-xs text-red-400/80 mt-0.5">{statsError}</div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4 mb-8">
        <div className="bg-gradient-to-br from-green-900/20 to-gray-800 p-4 sm:p-6 rounded-2xl border border-green-900/30 hover:border-green-700/50 transition-all">
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp size={18} className="text-green-400" />
            <span className="text-gray-400 text-[10px] sm:text-xs uppercase font-bold">Best ROI</span>
          </div>
          <div className={`text-2xl sm:text-3xl font-mono font-bold ${bestRoi >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {bestRoi >= 0 ? '+' : ''}{num(bestRoi)}%
          </div>
        </div>
        <div className="bg-gradient-to-br from-blue-900/20 to-gray-800 p-4 sm:p-6 rounded-2xl border border-blue-900/30 hover:border-blue-700/50 transition-all">
          <div className="flex items-center gap-2 mb-3">
            <BarChart3 size={18} className="text-blue-400" />
            <span className="text-gray-400 text-[10px] sm:text-xs uppercase font-bold">Total Runs</span>
          </div>
          <div className="text-2xl sm:text-3xl font-mono font-bold text-white">
            {loading ? '…' : (stats.total_runs || 0)}
            {stats.completed_runs != null && stats.completed_runs !== stats.total_runs && (
              <span className="text-xs text-gray-500 font-normal ml-1">({stats.completed_runs} done)</span>
            )}
          </div>
        </div>
        <div className="bg-gradient-to-br from-yellow-900/20 to-gray-800 p-4 sm:p-6 rounded-2xl border border-yellow-900/30 hover:border-yellow-700/50 transition-all">
          <div className="flex items-center gap-2 mb-3">
            <Target size={18} className="text-yellow-400" />
            <span className="text-gray-400 text-[10px] sm:text-xs uppercase font-bold">Avg Win Rate</span>
          </div>
          <div className="text-2xl sm:text-3xl font-mono font-bold text-yellow-400">{num(stats.avg_win_rate)}%</div>
        </div>
        <div className="bg-gradient-to-br from-purple-900/20 to-gray-800 p-4 sm:p-6 rounded-2xl border border-purple-900/30 hover:border-purple-700/50 transition-all">
          <div className="flex items-center gap-2 mb-3">
            <Zap size={18} className="text-purple-400" />
            <span className="text-gray-400 text-[10px] sm:text-xs uppercase font-bold">Live Sessions</span>
          </div>
          <div className="text-2xl sm:text-3xl font-mono font-bold text-purple-400">
            {runningInstances} <span className="text-sm text-gray-500">({openTrades} trades)</span>
          </div>
        </div>
      </div>

      {paperStatus.length > 0 && (
        <div className="mb-8">
          <h2 className="text-lg font-bold text-gray-200 mb-4 flex items-center gap-2">
            <Zap size={18} className="text-purple-400" /> Active Paper Trading Sessions
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {paperStatus.map(s => (
              <div key={s.instance_key} className="bg-gray-800 p-5 rounded-xl border border-gray-700 hover:border-purple-500/30 transition-all">
                <div className="flex justify-between items-center mb-2">
                  <span className="font-bold text-gray-200 text-sm truncate" title={s.strategy_name || s.strategy_id}>{s.strategy_name || s.strategy_id}</span>
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

      <div className="bg-gray-800 p-4 sm:p-6 rounded-2xl border border-gray-700">
        <h2 className="text-lg font-bold text-gray-200 mb-4">System Status</h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="flex items-center gap-3 bg-gray-900/50 p-3 rounded-xl">
            <div className="w-3 h-3 bg-green-500 rounded-full animate-pulse"></div>
            <div>
              <div className="text-xs text-gray-400">Binance API</div>
              <div className="text-sm font-bold text-green-400">Connected</div>
            </div>
          </div>
          <div className="flex items-center gap-3 bg-gray-900/50 p-3 rounded-xl">
            <div className={`w-3 h-3 rounded-full ${apiOnline ? 'bg-green-500 animate-pulse' : apiOnline === false ? 'bg-red-500' : 'bg-gray-500'}`}></div>
            <div>
              <div className="text-xs text-gray-400">Trading Engine</div>
              <div className={`text-sm font-bold ${apiOnline ? 'text-green-400' : apiOnline === false ? 'text-red-400' : 'text-gray-400'}`}>
                {apiOnline ? 'Operational' : apiOnline === false ? 'Unreachable' : 'Checking…'}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3 bg-gray-900/50 p-3 rounded-xl">
            <div className={`w-3 h-3 rounded-full ${!statsError ? 'bg-green-500 animate-pulse' : 'bg-yellow-500'}`}></div>
            <div>
              <div className="text-xs text-gray-400">Dashboard API</div>
              <div className={`text-sm font-bold ${!statsError ? 'text-green-400' : 'text-yellow-400'}`}>
                {statsError ? 'Stats error' : 'Live'}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
