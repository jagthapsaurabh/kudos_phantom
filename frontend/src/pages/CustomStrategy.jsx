import React, { useState } from 'react';
import { Plus, Trash2, Play } from 'lucide-react';
import { API_URL } from '../api';

const RuleBuilder = ({ rules, setRules }) => {
  const fields = ['close', 'open', 'high', 'low', 'volume', 'ema50', 'ema9', 'rsi14', 'adx', 'macd_hist'];
  const operators = [
    { label: 'Greater Than (>)', value: 'gt' },
    { label: 'Less Than (<)', value: 'lt' },
    { label: 'Equals (==)', value: 'eq' },
    { label: 'Crosses Above', value: 'crosses_above' },
    { label: 'Crosses Below', value: 'crosses_below' },
  ];

  const addRule = () => {
    setRules([...rules, { field: 'close', op: 'gt', value: 'ema50', timeframe: '4h' }]);
  };

  const removeRule = (index) => {
    const newRules = rules.filter((_, i) => i !== index);
    setRules(newRules);
  };

  const updateRule = (index, key, val) => {
    const newRules = [...rules];
    newRules[index][key] = val;
    setRules(newRules);
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-lg font-semibold">Entry Conditions (AND)</h3>
        <button onClick={addRule} className="bg-blue-600 p-2 rounded-full hover:bg-blue-500 transition">
          <Plus size={20} />
        </button>
      </div>
      
      {rules.map((rule, idx) => (
        <div key={idx} className="flex gap-3 items-end bg-gray-700 p-4 rounded-xl border border-gray-600 animate-in fade-in slide-in-from-top-2">
          <div className="flex-1">
            <label className="text-xs text-gray-400">Field</label>
            <select value={rule.field} onChange={e => updateRule(idx, 'field', e.target.value)} 
                    className="w-full bg-gray-800 p-2 rounded border border-gray-600 text-sm text-white">
              {fields.map(f => <option key={f} value={f}>{f}</option>)}
            </select>
          </div>
          <div className="w-40">
            <label className="text-xs text-gray-400">Operator</label>
            <select value={rule.op} onChange={e => updateRule(idx, 'op', e.target.value)} 
                    className="w-full bg-gray-800 p-2 rounded border border-gray-600 text-sm text-white">
              {operators.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          <div className="flex-1">
            <label className="text-xs text-gray-400">Value / Indicator</label>
            <input type="text" value={rule.value} onChange={e => updateRule(idx, 'value', e.target.value)}
                   className="w-full bg-gray-800 p-2 rounded border border-gray-600 text-sm text-white" placeholder="e.g. 30 or ema50" />
          </div>
          <div className="w-24">
            <label className="text-xs text-gray-400">Timeframe</label>
            <select value={rule.timeframe} onChange={e => updateRule(idx, 'timeframe', e.target.value)} 
                    className="w-full bg-gray-800 p-2 rounded border border-gray-600 text-sm text-white">
              <option value="1h">1h</option>
              <option value="4h">4h</option>
            </select>
          </div>
          <button onClick={() => removeRule(idx)} className="p-2 text-red-400 hover:bg-red-900/30 rounded-lg transition">
            <Trash2 size={20} />
          </button>
        </div>
      ))}
    </div>
  );
};

const CustomStrategyBuilder = () => {
  const [rules, setRules] = useState([{ field: 'close', op: 'gt', value: 'ema50', timeframe: '4h' }]);
  const [stratName, setStratName] = useState('My Custom Strategy');
  const [loading, setLoading] = useState(false);

  const saveStrategy = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/strategies/create`, {
        method: 'POST',
        headers: { 
          'Authorization': `Bearer ${localStorage.getItem('token')}`,
          'Content-Type': 'application/json' 
        },
        body: JSON.stringify({ 
          name: stratName, 
          rules: rules // Sent as rules for the dynamic builder
        })
      });
      const data = await res.json();
      if (res.ok) {
        alert(`Custom Strategy "${stratName}" Saved successfully!`);
      } else {
        alert(`Error: ${data.detail || "Failed to save strategy"}`);
      }
    } catch (e) { 
      alert("Network error while saving strategy"); 
    }
    setLoading(false);
  };

  return (
    <div className="page-shell">
      <div className="flex flex-col lg:flex-row lg:justify-between lg:items-center gap-4 mb-8">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold text-blue-400">Custom Strategy Builder</h1>
          <p className="text-gray-400">Create complex entry conditions using Chartink-style logic</p>
        </div>
        <div className="bg-blue-900/30 border border-blue-500/50 p-3 rounded-lg text-xs text-blue-300 max-w-xs">
          💡 Tip: Use <b>ema50</b>, <b>rsi14</b>, or <b>adx</b> as values for dynamic comparisons.
        </div>
      </div>

      <div className="bg-gray-800 p-8 rounded-2xl border border-gray-700 max-w-5xl shadow-2xl">
        <div className="mb-8">
          <label className="block text-sm text-gray-400 mb-2">Strategy Name</label>
          <input type="text" value={stratName} onChange={e => setStratName(e.target.value)}
                 className="bg-gray-700 p-3 rounded-lg border border-gray-600 w-full max-w-md text-xl font-bold text-white focus:ring-2 focus:ring-blue-500 outline-none transition" />
        </div>
        
        <RuleBuilder rules={rules} setRules={setRules} />
        
        <div className="mt-10 flex justify-end">
          <button onClick={saveStrategy} disabled={loading} className="bg-blue-600 px-8 py-3 rounded-lg font-bold hover:bg-blue-500 flex items-center gap-2 transition disabled:opacity-50 shadow-lg shadow-blue-900/20">
            {loading ? 'Saving...' : <><Play size={20} /> Save & Deploy Strategy</>}
          </button>
        </div>
      </div>
    </div>
  );
};

export default CustomStrategyBuilder;
