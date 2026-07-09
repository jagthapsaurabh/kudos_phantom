import React, { useState, useEffect, useMemo } from 'react';
import { Plus, Trash2, Copy, X, ChevronDown, ChevronUp, Lock, Unlock, FolderPlus, FilePlus, Search, Settings, Info } from 'lucide-react';

// --- Constants ---
const FIELDS = [
  { id: 'close', label: 'Close', group: 'Price' },
  { id: 'open', label: 'Open', group: 'Price' },
  { id: 'high', label: 'High', group: 'Price' },
  { id: 'low', label: 'Low', group: 'Price' },
  { id: 'volume', label: 'Volume', group: 'Volume' },
];

const INDICATORS = [
  { id: 'ema', label: 'EMA', group: 'Moving Average', params: { length: 50 } },
  { id: 'sma', label: 'SMA', group: 'Moving Average', params: { length: 50 } },
  { id: 'rsi', label: 'RSI', group: 'Oscillator', params: { length: 14 } },
  { id: 'atr', label: 'ATR', group: 'Volatility', params: { length: 14 } },
  { id: 'adx', label: 'ADX', group: 'Trend', params: { length: 14 } },
  { id: 'pdi', label: 'PDI (+DI)', group: 'Trend', params: { length: 14 } },
  { id: 'mdi', label: 'MDI (-DI)', group: 'Trend', params: { length: 14 } },
  { id: 'macd_line', label: 'MACD Line', group: 'Momentum', params: { fast: 12, slow: 26, signal: 9 } },
  { id: 'macd_signal', label: 'MACD Signal', group: 'Momentum', params: { fast: 12, slow: 26, signal: 9 } },
  { id: 'macd_hist', label: 'MACD Hist', group: 'Momentum', params: { fast: 12, slow: 26, signal: 9 } },
];

const OPERATORS = [
  { label: 'Greater than', value: 'gt', symbol: '>' },
  { label: 'Less than', value: 'lt', symbol: '<' },
  { label: 'Equal to', value: 'eq', symbol: '=' },
  { label: 'Not equal to', value: 'neq', symbol: '!=' },
  { label: 'Greater than or equal', value: 'gte', symbol: '>=' },
  { label: 'Less than or equal', value: 'lte', symbol: '<=' },
  { label: 'Crosses above', value: 'crosses_above', symbol: '↑' },
  { label: 'Crosses below', value: 'crosses_below', symbol: '↓' },
];

const TIMEFRAMES = [
  { id: '1m', label: '1 Minute' },
  { id: '5m', label: '5 Minutes' },
  { id: '15m', label: '15 Minutes' },
  { id: '1h', label: '1 Hour' },
  { id: '4h', label: '4 Hours' },
  { id: '1d', label: '1 Day' },
];

// --- Components ---

const SearchableSelect = ({ value, onChange, options, label, placeholder = "Select..." }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState('');

  const filteredOptions = useMemo(() => 
    options.filter(opt => opt.label.toLowerCase().includes(search.toLowerCase())), 
    [options, search]
  );

  const selectedOption = options.find(opt => opt.id === value);

  return (
    <div className="relative flex flex-col gap-1">
      {label && <label className="text-[10px] text-gray-500 uppercase font-bold">{label}</label>}
      <div className="relative">
        <button 
          onClick={() => setIsOpen(!isOpen)}
          className="w-full text-left bg-gray-900 border border-gray-700 rounded-lg p-2 text-xs flex items-center justify-between min-w-[120px] hover:border-blue-500 transition"
        >
          <span className="truncate">{selectedOption ? selectedOption.label : placeholder}</span>
          <ChevronDown size={14} className="ml-1 shrink-0 text-gray-500" />
        </button>
        
        {isOpen && (
          <div className="absolute z-50 w-full mt-1 bg-gray-800 border border-gray-600 rounded-lg shadow-2xl overflow-hidden">
            <div className="p-2 border-b border-gray-700 flex items-center gap-2 bg-gray-900">
              <Search size={14} className="text-gray-500" />
              <input 
                autoFocus
                className="bg-transparent text-xs outline-none w-full text-white"
                placeholder="Search..."
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
            </div>
            <div className="max-h-60 overflow-y-auto p-1">
              {filteredOptions.length > 0 ? (
                filteredOptions.map(opt => (
                  <div 
                    key={opt.id} 
                    className="px-3 py-2 text-xs hover:bg-blue-600 cursor-pointer text-gray-300 hover:text-white rounded-md transition"
                    onClick={() => {
                      onChange(opt.id);
                      setIsOpen(false);
                      setSearch('');
                    }}
                  >
                    {opt.label}
                  </div>
                ))
              ) : (
                <div className="px-3 py-2 text-xs text-gray-500 text-center">No results found</div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

const ParamEditor = ({ config, onChange }) => {
  if (!config) return null;
  const ind = INDICATORS.find(i => i.id === config.name);
  if (!ind || ind.type === 'field') return null;

  return (
    <div className="flex flex-wrap gap-3 mt-2 p-3 bg-gray-900/80 rounded-xl border border-gray-700 animate-in fade-in slide-in-from-top-1">
      {Object.entries(ind.params).map(([key, defaultValue]) => (
        <div key={key} className="flex items-center gap-2 text-xs">
          <span className="text-gray-500 font-medium capitalize">{key}:</span>
          <input 
            type="number" 
            value={config.params?.[key] ?? defaultValue} 
            onChange={e => onChange({ ...config, params: { ...config.params, [key]: parseInt(e.target.value) } })}
            className="w-16 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-white outline-none focus:border-blue-500"
          />
        </div>
      ))}
    </div>
  );
};

const RuleCondition = ({ rule, updateRule, removeRule, copyRule }) => {
  const [showParamsL, setShowParamsL] = useState(false);
  const [showParamsR, setShowParamsR] = useState(false);

  const safeRule = {
    enabled: rule.enabled ?? true,
    timeframe: rule.timeframe || '1h',
    op: rule.op || 'gt',
    left: rule.left || { name: 'close', type: 'field', offset: 0, params: {} },
    right: rule.right || { type: 'number', value: 0, name: 'close', offset: 0, params: {} },
    ...rule
  };

  const handleLeftChange = (name) => {
    const ind = INDICATORS.find(i => i.id === name) || FIELDS.find(f => f.id === name);
    updateRule({ ...safeRule, left: { ...safeRule.left, name, params: ind?.params || {} } });
  };

  const handleRightChange = (name) => {
    const ind = INDICATORS.find(i => i.id === name) || FIELDS.find(f => f.id === name);
    updateRule({ ...safeRule, right: { ...safeRule.right, name, params: ind?.params || {} } });
  };

  return (
    <div className={`group relative flex flex-col gap-2 p-5 mb-4 rounded-2xl border transition-all ${safeRule.enabled ? 'bg-gray-800 border-gray-600' : 'bg-gray-800/40 border-gray-700 opacity-50'}`}>
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] text-gray-500 uppercase font-bold">Offset</label>
          <input type="number" value={safeRule.left.offset ?? 0} onChange={e => updateRule({...safeRule, left: {...safeRule.left, offset: parseInt(e.target.value)}})} 
                 className="w-16 bg-gray-900 border border-gray-700 rounded-lg p-2 text-xs text-center focus:border-blue-500 outline-none" />
        </div>

        <SearchableSelect 
          label="Indicator" 
          value={safeRule.left.name} 
          options={[...FIELDS, ...INDICATORS]} 
          onChange={handleLeftChange} 
        />
        
        <button onClick={() => setShowParamsL(!showParamsL)} className="mt-4 p-1 text-gray-500 hover:text-white transition">
          {showParamsL ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>

        <div className="flex flex-col gap-1">
          <label className="text-[10px] text-gray-500 uppercase font-bold">Operator</label>
          <select value={safeRule.op} onChange={e => updateRule({...safeRule, op: e.target.value})} 
                  className="bg-gray-900 border border-gray-700 rounded-lg p-2 text-xs min-w-[140px] outline-none focus:border-blue-500">
            {OPERATORS.map(o => <option key={o.value} value={o.value}>{o.label} {o.symbol}</option>)}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-[10px] text-gray-500 uppercase font-bold">Value / Indicator</label>
          <div className="flex items-center gap-2">
            <select value={safeRule.right.type} onChange={e => updateRule({...safeRule, right: {...safeRule.right, type: e.target.value}})} 
                    className="bg-gray-900 border border-gray-700 rounded-lg p-2 text-xs outline-none focus:border-blue-500">
              <option value="number">Number</option>
              <option value="indicator">Indicator</option>
            </select>
            {safeRule.right.type === 'number' ? (
              <input type="number" value={safeRule.right.value ?? 0} onChange={e => updateRule({...safeRule, right: {...safeRule.right, value: parseFloat(e.target.value)}})} 
                     className="w-24 bg-gray-900 border border-gray-700 rounded-lg p-2 text-xs outline-none focus:border-blue-500" />
            ) : (
              <>
                <SearchableSelect 
                  label="" 
                  value={safeRule.right.name} 
                  options={[...FIELDS, ...INDICATORS]} 
                  onChange={handleRightChange} 
                />
                <button onClick={() => setShowParamsR(!showParamsR)} className="mt-4 p-1 text-gray-500 hover:text-white transition">
                  {showParamsR ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </button>
              </>
            )}
          </div>
        </div>

        {safeRule.right.type === 'indicator' && (
          <div className="flex flex-col gap-1">
            <label className="text-[10px] text-gray-500 uppercase font-bold">Offset</label>
            <input type="number" value={safeRule.right.offset ?? 0} onChange={e => updateRule({...safeRule, right: {...safeRule.right, offset: parseInt(e.target.value)}})} 
                   className="w-16 bg-gray-900 border border-gray-700 rounded-lg p-2 text-xs text-center outline-none focus:border-blue-500" />
          </div>
        )}

        <div className="flex flex-col gap-1">
          <label className="text-[10px] text-gray-500 uppercase font-bold">Timeframe</label>
          <select value={safeRule.timeframe} onChange={e => updateRule({...safeRule, timeframe: e.target.value})} 
                  className="bg-gray-900 border border-gray-700 rounded-lg p-2 text-xs outline-none focus:border-blue-500">
            {TIMEFRAMES.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>
        </div>

        <div className="flex items-end gap-2 ml-auto">
          <button onClick={() => updateRule({...safeRule, enabled: !safeRule.enabled})} 
                  className={`p-2 rounded-lg transition ${safeRule.enabled ? 'text-blue-400 hover:bg-blue-900/30' : 'text-gray-500 hover:bg-gray-700'}`}>
            {safeRule.enabled ? <Lock size={18} /> : <Unlock size={18} />}
          </button>
          <button onClick={() => copyRule(safeRule)} className="p-2 text-gray-400 hover:bg-gray-700 rounded-lg transition">
            <Copy size={18} />
          </button>
          <button onClick={() => removeRule()} className="p-2 text-red-400 hover:bg-red-900/30 rounded-lg transition">
            <Trash2 size={18} />
          </button>
        </div>
      </div>

      {showParamsL && <ParamEditor config={safeRule.left} onChange={val => updateRule({...safeRule, left: val})} />}
      {showParamsR && safeRule.right?.type === 'indicator' && <ParamEditor config={safeRule.right} onChange={val => updateRule({...safeRule, right: val})} />}
    </div>
  );
};

const RuleGroup = ({ group, updateGroup, removeGroup, addRule, addGroup }) => {
  if (!group) return null;
  const safeGroup = {
    id: group.id || `group_${Date.now()}`,
    type: 'group',
    operator: group.operator || 'AND',
    children: group.children || [],
    enabled: group.enabled ?? true,
    ...group
  };

  return (
    <div className={`relative p-6 mb-6 rounded-2xl border-2 transition-all ${safeGroup.enabled ? 'border-blue-500/40 bg-blue-500/5' : 'border-gray-700 bg-gray-800/30 opacity-60'}`}>
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-4">
          <button onClick={() => updateGroup({ ...safeGroup, enabled: !safeGroup.enabled })} 
                  className={`p-1.5 rounded-lg transition ${safeGroup.enabled ? 'text-blue-400 bg-blue-500/10' : 'text-gray-500 bg-gray-700'}`}>
            {safeGroup.enabled ? <Lock size={16} /> : <Unlock size={16} />}
          </button>
          <div className="flex items-center gap-2 bg-gray-900 p-1 rounded-full border border-gray-700">
            <button onClick={() => updateGroup({ ...safeGroup, operator: 'AND' })} 
                    className={`px-3 py-1 rounded-full text-[10px] font-bold transition ${safeGroup.operator === 'AND' ? 'bg-blue-600 text-white' : 'text-gray-500 hover:text-gray-300'}`}>
              AND
            </button>
            <button onClick={() => updateGroup({ ...safeGroup, operator: 'OR' })} 
                    className={`px-3 py-1 rounded-full text-[10px] font-bold transition ${safeGroup.operator === 'OR' ? 'bg-blue-600 text-white' : 'text-gray-500 hover:text-gray-300'}`}>
              OR
            </button>
          </div>
          <span className="text-xs text-gray-500 font-semibold uppercase tracking-wider">Logic Group</span>
        </div>
        
        <div className="flex gap-3">
          <button onClick={addRule} className="flex items-center gap-2 text-xs bg-gray-700 hover:bg-gray-600 px-3 py-1.5 rounded-lg text-white transition border border-gray-600">
            <FilePlus size={14} /> Add Rule
          </button>
          <button onClick={addGroup} className="flex items-center gap-2 text-xs bg-gray-700 hover:bg-gray-600 px-3 py-1.5 rounded-lg text-white transition border border-gray-600">
            <FolderPlus size={14} /> Add Group
          </button>
          <button onClick={removeGroup} className="p-1.5 text-red-400 hover:bg-red-900/30 rounded-lg transition">
            <Trash2 size={16} />
          </button>
        </div>
      </div>

      <div className="space-y-3">
        {safeGroup.children.map((child, idx) => (
          child.type === 'group' ? (
            <RuleGroup 
              key={child.id} 
              group={child} 
              updateGroup={val => {
                const newChildren = [...safeGroup.children];
                newChildren[idx] = val;
                updateGroup({ ...safeGroup, children: newChildren });
              }} 
              removeGroup={() => {
                updateGroup({ ...safeGroup, children: safeGroup.children.filter((_, i) => i !== idx) });
              }}
              addRule={(newRule) => {
                const newChildren = [...safeGroup.children];
                newChildren[idx].children.push(newRule);
                updateGroup({ ...safeGroup, children: newChildren });
              }}
              addGroup={(newGroup) => {
                const newChildren = [...safeGroup.children];
                newChildren[idx].children.push(newGroup);
                updateGroup({ ...safeGroup, children: newChildren });
              }}
            />
          ) : (
            <RuleCondition 
              key={child.id} 
              rule={child} 
              updateRule={val => {
                const newChildren = [...safeGroup.children];
                newChildren[idx] = val;
                updateGroup({ ...safeGroup, children: newChildren });
              }} 
              removeRule={() => {
                updateGroup({ ...safeGroup, children: safeGroup.children.filter((_, i) => i !== idx) });
              }}
              copyRule={(r) => {
                const copy = { ...r, id: `rule_${Date.now()}` };
                updateGroup({ ...safeGroup, children: [...safeGroup.children, copy] });
              }}
            />
          )
        ))}
      </div>
    </div>
  );
};

const Strategies = () => {
  const [strategies, setStrategies] = useState([]);
  const [loading, setLoading] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [editingStrat, setEditingStrat] = useState(null);
  const [stratType, setStratType] = useState('params'); 
  const [rootGroup, setRootGroup] = useState({ 
    id: 'root', type: 'group', operator: 'AND', children: [], enabled: true 
  });
  const [form, setForm] = useState({
    name: '',
    params: {
      trend_ema_period: 50, rsi_oversold: 30, rsi_overbought: 70, adx_min: 22,
      macd_hist_min: 25, atr_regime_ratio: 0.5, stop_loss_atr: 2.0, take_profit_atr: 1.2,
      trail_activation_atr: 1.5, trail_distance_atr: 0.5,
    }
  });
  const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  const fetchStrategies = async () => {
    try {
      const res = await fetch(`${API_URL}/strategies`, { 
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } 
      });
      const data = await res.json();
      setStrategies(data);
    } catch (e) { console.error(e); }
  };

  useEffect(() => { fetchStrategies(); }, []);

  const handleSave = async () => {
    setLoading(true);
    const endpoint = editingStrat 
      ? `/api/strategies/update/${editingStrat}` 
      : '/api/strategies/create';
    
    const payload = { name: form.name };
    if (stratType === 'params') {
      payload.params = form.params;
    } else {
      payload.rules = rootGroup;
    }

    try {
      const res = await fetch(`${API_URL}${endpoint}`, {
        method: editingStrat ? 'PUT' : 'POST',
        headers: { 
          'Authorization': `Bearer ${localStorage.getItem('token')}`,
          'Content-Type': 'application/json' 
        },
        body: JSON.stringify(payload),
      });
      
      const data = await res.json();
      if (res.ok) {
        alert(data.status || "Strategy saved successfully!");
        setShowModal(false);
        setEditingStrat(null);
        fetchStrategies();
      } else {
        alert(`Error: ${data.detail || "Something went wrong"}`);
      }
    } catch (e) { 
      alert("Network error"); 
    }
    setLoading(false);
  };

  const openEdit = (strat) => {
    setEditingStrat(strat.id);
    const config = strat.rules;
    if (Array.isArray(config)) {
      setStratType('rules');
      setRootGroup({ id: 'root', type: 'group', operator: 'AND', children: config, enabled: true });
    } else if (config && typeof config === 'object' && config.type === 'group') {
      setStratType('rules');
      setRootGroup(config);
    } else if (typeof config === 'object' && config !== null) {
      setStratType('params');
      setForm({ name: strat.name, params: config });
    } else {
      setStratType('params');
      setForm({ name: strat.name, params: { trend_ema_period: 50 } });
    }
    setForm(prev => ({ ...prev, name: strat.name }));
    setShowModal(true);
  };

  const createNewRule = () => ({
    id: `rule_${Date.now()}`,
    type: 'condition',
    enabled: true,
    timeframe: '4h',
    left: { name: 'close', type: 'field', offset: 0, params: {} },
    op: 'gt',
    right: { type: 'indicator', name: 'ema', offset: 0, params: { length: 50 } },
  });

  const createNewGroup = () => ({
    id: `group_${Date.now()}`,
    type: 'group',
    operator: 'AND',
    children: [],
    enabled: true,
  });

  return (
    <div className="ml-64 p-8 bg-gray-900 text-white min-h-screen">
      <div className="flex justify-between items-center mb-8">
        <h1 className="text-3xl font-bold text-blue-400">Strategies Manager</h1>
        <button onClick={() => { 
          setEditingStrat(null); 
          setForm({name: '', params: { trend_ema_period: 50, rsi_oversold: 30, rsi_overbought: 70, adx_min: 22, macd_hist_min: 25, atr_regime_ratio: 0.5, stop_loss_atr: 2.0, take_profit_atr: 1.2, trail_activation_atr: 1.5, trail_distance_atr: 0.5 }}); 
          setRootGroup({ id: 'root', type: 'group', operator: 'AND', children: [createNewRule()], enabled: true });
          setStratType('params');
          setShowModal(true); 
        }} 
                className="bg-blue-600 px-4 py-2 rounded-lg font-bold hover:bg-blue-500 transition">
          + Add Strategy
        </button>
      </div>
      
      <div className="bg-gray-800 rounded-2xl border border-gray-700 overflow-hidden">
        <table className="w-full text-left">
          <thead className="bg-gray-700 text-gray-300 text-sm">
            <tr>
              <th className="p-4">Strategy Name</th>
              <th className="p-4">Type</th>
              <th className="p-4">Created At</th>
              <th className="p-4">Actions</th>
            </tr>
          </thead>
          <tbody>
            {strategies.map(s => (
              <tr key={s.id} className="border-b border-gray-700 hover:bg-gray-700/50 transition">
                <td className="p-4 font-medium">{s.name}</td>
                <td className="p-4 text-sm text-gray-400">
                  {Array.isArray(s.rules) || (s.rules && s.rules.type === 'group') ? 'Rule-based' : 'Parameter-based'}
                </td>
                <td className="p-4 text-gray-400 text-sm">{new Date(s.created_at).toLocaleDateString()}</td>
                <td className="p-4">
                  <button onClick={() => openEdit(s)} className="text-blue-400 hover:text-blue-300 mr-4">Edit</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showModal && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center p-4 z-50 backdrop-blur-sm">
          <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700 w-full max-w-5xl max-h-[90vh] overflow-y-auto shadow-2xl">
            <div className="flex justify-between items-center mb-6">
              <div className="flex items-center gap-3">
                <h2 className="text-2xl font-bold">{editingStrat ? 'Edit' : 'Create'} Strategy</h2>
                <div className="flex items-center gap-1 px-2 py-1 bg-blue-900/30 text-blue-400 rounded text-[10px] font-bold border border-blue-500/30">
                  <Info size={12} /> Builder v2.5
                </div>
              </div>
              <button onClick={() => setShowModal(false)} className="p-2 text-gray-400 hover:text-white"><X size={24} /></button>
            </div>
            
            <div className="space-y-6">
              <div className="flex flex-col">
                <label className="text-sm text-gray-400 mb-1">Strategy Name</label>
                <input type="text" value={form.name} onChange={e => setForm({...form, name: e.target.value})}
                       className="bg-gray-700 p-3 rounded-lg border border-gray-600 text-white text-lg focus:ring-2 focus:ring-blue-500 outline-none" />
              </div>
              
              <div className="flex gap-4 p-1 bg-gray-900 rounded-xl border border-gray-700 w-fit">
                <button onClick={() => setStratType('params')} className={`px-6 py-2 rounded-lg text-sm font-bold transition ${stratType === 'params' ? 'bg-blue-600 text-white shadow-lg' : 'text-gray-400 hover:text-white'}`}>Parameter-based</button>
                <button onClick={() => setStratType('rules')} className={`px-6 py-2 rounded-lg text-sm font-bold transition ${stratType === 'rules' ? 'bg-blue-600 text-white shadow-lg' : 'text-gray-400 hover:text-white'}`}>Rule-based Builder</button>
              </div>

              {stratType === 'params' ? (
                <div className="grid grid-cols-2 gap-4">
                  {Object.keys(form.params || {}).map(key => (
                    <div key={key} className="flex flex-col">
                      <label className="text-xs text-gray-400 capitalize mb-1">{key.replace('_', ' ')}</label>
                      <input type="number" step="0.01" value={form.params[key]} 
                             onChange={e => setForm({...form, params: {...form.params, [key]: parseFloat(e.target.value)}})}
                             className="bg-gray-700 p-2 rounded border border-gray-600 text-white text-sm" />
                    </div>
                  ))}
                </div>
              ) : (
                <div className="bg-gray-900/50 p-6 rounded-2xl border border-gray-700">
                  <RuleGroup 
                    group={rootGroup} 
                    updateGroup={setRootGroup} 
                    removeGroup={() => {}} 
                    addRule={(r) => setRootGroup({ ...rootGroup, children: [...rootGroup.children, r] })}
                    addGroup={(g) => setRootGroup({ ...rootGroup, children: [...rootGroup.children, g] })}
                  />
                  <div className="flex gap-3 mt-4">
                    <button onClick={() => setRootGroup({ ...rootGroup, children: [...rootGroup.children, createNewRule()] })} 
                            className="flex items-center gap-2 bg-blue-600 px-4 py-2 rounded-lg text-xs font-bold hover:bg-blue-500 transition">
                      <FilePlus size={14} /> Add Root Rule
                    </button>
                    <button onClick={() => setRootGroup({ ...rootGroup, children: [...rootGroup.children, createNewGroup()] })} 
                            className="flex items-center gap-2 bg-gray-700 px-4 py-2 rounded-lg text-xs font-bold hover:bg-gray-600 transition">
                      <FolderPlus size={14} /> Add Root Group
                    </button>
                  </div>
                </div>
              )}
            </div>
            
            <div className="flex gap-3 mt-10">
              <button onClick={() => setShowModal(false)} className="flex-1 bg-gray-700 py-3 rounded-xl font-bold hover:bg-gray-600 transition">Cancel</button>
              <button onClick={handleSave} disabled={loading} className="flex-1 bg-blue-600 py-3 rounded-xl font-bold hover:bg-blue-500 transition disabled:bg-blue-800">
                {loading ? 'Saving...' : 'Save Strategy'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Strategies;
