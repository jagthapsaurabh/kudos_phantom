import React, { useState, useEffect } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

const LoginPage = ({ onLogin }) => {
  const [form, setForm] = useState({ username: '', password: '' });
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    const formData = new FormData();
    formData.append('username', form.username);
    formData.append('password', form.password);

    try {
      const res = await fetch('/api/token', { method: 'POST', body: formData });
      if (!res.ok) throw new Error('Invalid credentials');
      const data = await res.json();
      localStorage.setItem('token', data.access_token);
      onLogin(data.access_token);
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <div className="min-h-screen bg-gray-900 flex items-center justify-center p-4">
      <div className="bg-gray-800 p-8 rounded-2xl shadow-2xl border border-gray-700 w-full max-w-md">
        <h1 className="text-3xl font-bold text-blue-400 text-center mb-6">PHANTOM v2.5</h1>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="text-sm text-gray-400">Username</label>
            <input type="text" className="w-full bg-gray-700 p-3 rounded-lg border border-gray-600 text-white" 
                   value={form.username} onChange={e => setForm({...form, username: e.target.value})} />
          </div>
          <div>
            <label className="text-sm text-gray-400">Password</label>
            <input type="password" className="w-full bg-gray-700 p-3 rounded-lg border border-gray-600 text-white" 
                   value={form.password} onChange={e => setForm({...form, password: e.target.value})} />
          </div>
          {error && <p className="text-red-400 text-sm">{error}</p>}
          <button type="submit" className="w-full bg-blue-600 hover:bg-blue-500 py-3 rounded-lg font-bold transition">Login</button>
        </form>
      </div>
    </div>
  );
};

const AdminPanel = ({ token, onLogout }) => {
  const [params, setParams] = useState({
    trend_ema_period: 50, rsi_oversold: 30, rsi_overbought: 70, adx_min: 22,
    macd_hist_min: 25, atr_regime_ratio: 0.5, stop_loss_atr: 2.0, take_profit_atr: 1.2,
    trail_activation_atr: 1.5, trail_distance_atr: 0.5,
  });
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [paperStatus, setPaperStatus] = useState({ status: 'stopped', equity: 20000 });

  useEffect(() => {
    const interval = setInterval(fetchPaperStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  const authHeader = { 'Authorization': `Bearer ${token}` };

  const fetchPaperStatus = async () => {
    try {
      const res = await fetch('/api/paper/status', { headers: authHeader });
      const data = await res.json();
      setPaperStatus(data);
    } catch (e) {}
  };

  const runBacktest = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/backtest', {
        method: 'POST',
        headers: { ...authHeader, 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      });
      const data = await response.json();
      setResults(data);
    } catch (error) {
      console.error("Backtest failed", error);
    }
    setLoading(false);
  };

  const updateConfig = async () => {
    await fetch('/api/config/update', {
      method: 'POST',
      headers: { ...authHeader, 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    });
    alert("Configuration updated!");
  };

  const togglePaperTrade = async () => {
    const endpoint = paperStatus.status === 'running' ? '/api/paper/stop' : '/api/paper/start';
    await fetch(endpoint, { method: 'POST', headers: authHeader });
    fetchPaperStatus();
  };

  return (
    <div className="p-8 bg-gray-900 text-white min-h-screen font-sans">
      <header className="mb-8 border-b border-gray-700 pb-4 flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold text-blue-400">PHANTOM v2.5 Admin</h1>
          <p className="text-gray-400">Strategy Optimizer & Paper Trade Control</p>
        </div>
        <div className="flex gap-4 items-center">
          <div className={`px-4 py-2 rounded-full text-sm font-bold ${paperStatus.status === 'running' ? 'bg-green-900 text-green-400' : 'bg-red-900 text-red-400'}`}>
            Paper Trade: {paperStatus.status.toUpperCase()}
          </div>
          <div className="text-xl font-mono text-yellow-400">Equity: ₹{paperStatus.equity.toLocaleString()}</div>
          <button onClick={togglePaperTrade} className="bg-blue-600 px-4 py-2 rounded-lg hover:bg-blue-500 font-bold transition">
            {paperStatus.status === 'running' ? 'Stop' : 'Start'} Paper
          </button>
          <button onClick={onLogout} className="bg-gray-700 px-4 py-2 rounded-lg hover:bg-gray-600 text-sm">Logout</button>
        </div>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div className="bg-gray-800 p-6 rounded-xl shadow-lg border border-gray-700">
          <h2 className="text-xl font-semibold mb-4">Strategy Parameters</h2>
          <div className="space-y-4 max-h-[60vh] overflow-y-auto pr-2">
            {Object.keys(params).map(key => (
              <div key={key} className="flex flex-col">
                <label className="text-sm text-gray-400 capitalize">{key.replace('_', ' ')}</label>
                <input type="number" step="0.01" value={params[key]} 
                       onChange={e => setParams({...params, [key]: parseFloat(e.target.value)})}
                       className="bg-gray-700 text-white p-2 rounded border border-gray-600 focus:border-blue-500 outline-none" />
              </div>
            ))}
          </div>
          <div className="mt-6 flex gap-2">
            <button onClick={runBacktest} disabled={loading} className="flex-1 bg-blue-600 hover:bg-blue-500 py-3 rounded-lg font-bold disabled:opacity-50">
              {loading ? '...' : '🚀 Backtest'}
            </button>
            <button onClick={updateConfig} className="flex-1 bg-gray-600 hover:bg-gray-500 py-3 rounded-lg font-bold">
              💾 Save Config
            </button>
          </div>
        </div>

        <div className="lg:col-span-2 space-y-8">
          {results ? (
            <>
              <div className="grid grid-cols-3 gap-4">
                <div className="bg-gray-800 p-4 rounded-xl border border-gray-700 text-center">
                  <div className="text-gray-400 text-sm">Final Equity</div>
                  <div className="text-2xl font-bold text-green-400">₹{results.final_equity_inr.toLocaleString()}</div>
                </div>
                <div className="bg-gray-800 p-4 rounded-xl border border-gray-700 text-center">
                  <div className="text-gray-400 text-sm">Max Drawdown</div>
                  <div className="text-2xl font-bold text-red-400">{results.max_drawdown.toFixed(2)}%</div>
                </div>
                <div className="bg-gray-800 p-4 rounded-xl border border-gray-700 text-center">
                  <div className="text-gray-400 text-sm">Win Rate</div>
                  <div className="text-2xl font-bold text-yellow-400">{results.win_rate.toFixed(2)}%</div>
                </div>
              </div>
              <div className="bg-gray-800 p-6 rounded-xl border border-gray-700 h-96">
                <h3 className="text-lg font-semibold mb-4">Equity Curve (INR)</h3>
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={results.equity_curve.map((val, i) => ({ name: i, value: val }))}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                    <XAxis dataKey="name" hide />
                    <YAxis stroke="#9CA3AF" domain={['auto', 'auto']} />
                    <Tooltip contentStyle={{backgroundColor: '#1F2937', border: 'none'}} />
                    <Line type="monotone" dataKey="value" stroke="#60A5FA" strokeWidth={2} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </>
          ) : (
            <div className="bg-gray-800 p-12 rounded-xl border border-gray-700 text-center text-gray-500">
              No results. Adjust parameters and run backtest.
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

const App = () => {
  const [token, setToken] = useState(localStorage.getItem('token'));
  if (!token) return <LoginPage onLogin={setToken} />;
  return <AdminPanel token={token} onLogout={() => { localStorage.removeItem('token'); setToken(null); }} />;
};

export default App;
