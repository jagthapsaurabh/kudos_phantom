import React, { useState, useEffect } from 'react';
import { API_URL } from '../api';

const BrokerSettings = () => {
  const [settings, setSettings] = useState({ 
    apiKey: '', 
    apiSecret: '', 
    broker: 'Binance', 
    initialCapital: 20000, 
    marginPct: 25 
  });

  useEffect(() => {
    const loadSettings = async () => {
      try {
        const res = await fetch(`${API_URL}/broker-settings`, {
          headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` }
        });
        const data = await res.json();
        setSettings({
          apiKey: data.api_key || '',
          apiSecret: data.api_secret || '',
          broker: data.broker_name || 'Binance',
          initialCapital: data.initial_capital || 20000,
          marginPct: data.margin_deployment_pct || 25
        });
      } catch (e) { console.error("Error loading settings", e); }
    };
    loadSettings();
  }, []);

  const saveSettings = async () => {
    try {
      const res = await fetch(`${API_URL}/broker-settings`, {
        method: 'POST',
        headers: { 
          'Authorization': `Bearer ${localStorage.getItem('token')}`,
          'Content-Type': 'application/json' 
        },
        body: JSON.stringify({
          api_key: settings.apiKey,
          api_secret: settings.apiSecret,
          broker_name: settings.broker,
          initial_capital: parseFloat(settings.initialCapital),
          margin_pct: parseFloat(settings.marginPct),
        }),
      });
      if (res.ok) alert("Settings saved successfully!");
      else alert("Error saving settings");
    } catch (e) { alert("Error saving keys"); }
  };

  return (
    <div className="ml-64 p-8 bg-gray-900 text-white min-h-screen">
      <h1 className="text-3xl font-bold mb-8 text-blue-400">Broker & Capital Settings</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-8 max-w-5xl">
        <div className="bg-gray-800 p-8 rounded-2xl border border-gray-700">
          <h2 className="text-xl font-semibold mb-6 text-gray-300">API Credentials</h2>
          <div className="space-y-4">
            <div>
              <label className="text-sm text-gray-400">Broker</label>
              <select className="w-full bg-gray-700 p-3 rounded-lg border border-gray-600" 
                      value={settings.broker} onChange={e => setSettings({...settings, broker: e.target.value})}>
                <option value="Binance">Binance</option>
                <option value="Delta">Delta Exchange</option>
                <option value="ByBit">ByBit</option>
              </select>
            </div>
            <div>
              <label className="text-sm text-gray-400">API Key</label>
              <input type="password" className="w-full bg-gray-700 p-3 rounded-lg border border-gray-600" 
                     value={settings.apiKey} onChange={e => setSettings({...settings, apiKey: e.target.value})} />
            </div>
            <div>
              <label className="text-sm text-gray-400">Secret Key</label>
              <input type="password" className="w-full bg-gray-700 p-3 rounded-lg border border-gray-600" 
                     value={settings.apiSecret} onChange={e => setSettings({...settings, apiSecret: e.target.value})} />
            </div>
          </div>
        </div>
        <div className="bg-gray-800 p-8 rounded-2xl border border-gray-700">
          <h2 className="text-xl font-semibold mb-6 text-gray-300">Capital Management</h2>
          <div className="space-y-4">
            <div>
              <label className="text-sm text-gray-400">Initial Capital (₹)</label>
              <input type="number" className="w-full bg-gray-700 p-3 rounded-lg border border-gray-600" 
                     value={settings.initialCapital} onChange={e => setSettings({...settings, initialCapital: e.target.value})} />
            </div>
            <div>
              <label className="text-sm text-gray-400">Margin Deployment (%)</label>
              <input type="number" className="w-full bg-gray-700 p-3 rounded-lg border border-gray-600" 
                     value={settings.marginPct} onChange={e => setSettings({...settings, marginPct: e.target.value})} />
            </div>
            <div className="p-4 bg-gray-900 rounded-lg border border-gray-700">
              <p className="text-sm text-gray-400">Estimated Margin per Trade:</p>
              <p className="text-2xl font-bold text-blue-400">₹{(settings.initialCapital * (settings.marginPct / 100)).toLocaleString()}</p>
            </div>
          </div>
        </div>
      </div>
      <div className="mt-8 max-w-5xl">
        <button onClick={saveSettings} className="bg-blue-600 px-12 py-4 rounded-xl font-bold hover:bg-blue-500 transition shadow-lg shadow-blue-900/20">
          Save All Settings
        </button>
      </div>
    </div>
  );
};

export default BrokerSettings;
