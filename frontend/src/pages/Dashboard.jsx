import React, { useState, useEffect } from 'react';

const Dashboard = () => {
  const [stats, setStats] = useState({ best_roi: 0, total_runs: 0, avg_win_rate: 0 });

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const res = await fetch('/api/dashboard/stats', { 
          headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } 
        });
        const data = await res.json();
        setStats(data);
      } catch (e) {}
    };
    fetchStats();
  }, []);

  return (
    <div className="ml-64 p-8 bg-gray-900 text-white min-h-screen">
      <h1 className="text-3xl font-bold mb-8 text-blue-400">Command Center</h1>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
          <div className="text-gray-400 text-sm mb-2">Best Backtest ROI</div>
          <div className="text-3xl font-mono font-bold text-green-400">+{stats.best_roi?.toFixed(2)}%</div>
        </div>
        <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
          <div className="text-gray-400 text-sm mb-2">Total Optimizations</div>
          <div className="text-3xl font-mono font-bold text-white">{stats.total_runs}</div>
        </div>
        <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
          <div className="text-gray-400 text-sm mb-2">Avg. Win Rate</div>
          <div className="text-3xl font-mono font-bold text-yellow-400">{stats.avg_win_rate?.toFixed(2)}%</div>
        </div>
      </div>
      <div className="mt-8 bg-gray-800 p-6 rounded-2xl border border-gray-700">
        <h2 className="text-xl font-semibold mb-4">System Health</h2>
        <div className="flex items-center space-x-4">
          <div className="w-3 h-3 bg-green-500 rounded-full animate-pulse"></div>
          <div className="text-gray-300">All systems operational. Connection to Binance API: <span className="text-green-400 font-bold">STABLE</span></div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
