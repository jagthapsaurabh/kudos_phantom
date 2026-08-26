import React, { useState, useEffect, useRef } from 'react';
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts';
import { createChart, AreaSeries } from 'lightweight-charts';
import { API_URL } from '../api';
import DateInput from '../components/DateInput';
import { Activity, TrendingUp, RotateCcw, Trash2, Tag, Download, Timer, HelpCircle, Play, SlidersHorizontal, CalendarRange, Wallet, ChevronDown, ChevronUp } from 'lucide-react';

const PARAM_META = {
  trend_ema_period: { label: 'Trend EMA', hint: 'How far back the 4h trend looks. Higher = slower, fewer trades.' },
  rsi_oversold: { label: 'RSI oversold', hint: 'Long reversal fires after RSI was below this level.' },
  rsi_overbought: { label: 'RSI overbought', hint: 'Short reversal fires after RSI was above this level.' },
  adx_min: { label: 'Min ADX', hint: 'Skip choppy markets. Higher = only strong trends.' },
  macd_fast: { label: 'MACD fast (EMA)', hint: 'Short MACD period. MACD line = EMA(fast) − EMA(slow).' },
  macd_slow: { label: 'MACD slow (EMA)', hint: 'Long MACD period. Must be greater than fast.' },
  macd_signal: { label: 'MACD signal', hint: 'Signal-line period: Signal = EMA(MACD_line, signal). Histogram = MACD_line − Signal.' },
  macd_hist_min: { label: 'MACD hist min', hint: 'Minimum momentum size. Longs: hist ≥ this. Shorts: hist ≤ this (use a negative).' },
  atr_regime_ratio: { label: 'Min ATR floor', hint: 'Require ATR ≥ this × its 50-bar average. Lower = more trades.' },
  atr_regime_max: { label: 'Max ATR cap', hint: 'Optional: skip high-volatility. Blank = off.' },
  enable_momentum_entry: { label: 'Momentum entries', hint: 'Also take trend-continuation trades, not just reversals.' },
  cooldown_bars: { label: 'Cooldown bars', hint: 'Wait this many candles after a close before a new entry.' },
  stop_loss_atr: { label: 'Stop loss (ATR)', hint: 'Distance of the stop from entry, in ATRs. Higher = wider stop.' },
  take_profit_atr: { label: 'Take profit (ATR)', hint: 'Distance of the profit target from entry, in ATRs.' },
  trail_activation_atr: { label: 'Trail activation', hint: 'Start trailing the stop after this much profit (ATR).' },
  trail_distance_atr: { label: 'Trail distance', hint: 'How tightly the trail follows price, in ATRs.' },
  breakeven_atr: { label: 'Breakeven after', hint: 'Move stop to entry once profit reaches this many ATRs.' },
  leverage: { label: 'Leverage', hint: 'Position notional = margin × leverage.' },
  margin_pct: { label: 'Margin % of equity', hint: 'Share of equity used as margin per trade (0.15 = 15%).' },
  dd_soft_pct: { label: 'Soft drawdown %', hint: 'Past this equity drawdown, position size is reduced.' },
  dd_halt_pct: { label: 'Halt drawdown %', hint: 'Past this, new entries stop. 100 = guard off.' },
  dd_resume_pct: { label: 'Resume drawdown %', hint: 'Start entries again once drawdown falls below this.' },
};

const isBacktestComplete = (runDetails) => Boolean(
  runDetails
  && runDetails.total_trades !== null
  && runDetails.total_trades !== undefined
  && runDetails.final_equity !== null
  && runDetails.final_equity !== undefined
  && Array.isArray(runDetails.equity_curve)
);

const normalizeBacktestResults = (runDetails, trades = []) => {
  if (!runDetails) return null;
  return {
    ...runDetails,
    final_equity_inr: runDetails.final_equity ?? runDetails.final_equity_inr ?? null,
    trades: Array.isArray(trades) ? trades : [],
    rejected_reasons: runDetails.rejected_reasons && typeof runDetails.rejected_reasons === 'object'
      ? runDetails.rejected_reasons
      : {},
  };
};

const formatCurrencyValue = (value, digits = 0) => (
  value === null || value === undefined || Number.isNaN(Number(value))
    ? '—'
    : `₹${Number(value).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
);

const formatPercentValue = (value, digits = 2) => (
  value === null || value === undefined || Number.isNaN(Number(value))
    ? '—'
    : `${Number(value).toFixed(digits)}%`
);

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

const SectionCard = ({ title, subtitle, icon: Icon, collapsed = false, onToggle, actions, className = '', children }) => (
  <div className={`bg-gray-800 rounded-2xl border border-gray-700 shadow-xl ${className}`}>
    <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between sm:p-6">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          {Icon ? <Icon size={16} className="shrink-0 text-blue-400" /> : null}
          <h2 className="text-sm font-bold uppercase tracking-wider text-gray-200">{title}</h2>
        </div>
        {subtitle ? <p className="mt-1 text-xs text-gray-500">{subtitle}</p> : null}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {actions}
        {onToggle && (
          <button
            onClick={onToggle}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-700 bg-gray-900 px-3 py-1.5 text-xs font-semibold text-gray-300 transition hover:border-blue-500 hover:text-white"
          >
            {collapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
            {collapsed ? 'View' : 'Hide'}
          </button>
        )}
      </div>
    </div>
    {!collapsed && <div className="px-4 pb-4 sm:px-6 sm:pb-6">{children}</div>}
  </div>
);

const Backtest = () => {
  const DEFAULT_PARAMS = {
    trend_ema_period: 50,
    macd_fast: 12, macd_slow: 26, macd_signal: 9,
    rsi_oversold: 40, rsi_overbought: 60, adx_min: 10, macd_hist_min: 5,
    atr_regime_ratio: 0.5, enable_momentum_entry: true, cooldown_bars: 0,
    stop_loss_atr: 1.2, take_profit_atr: 14.0, trail_activation_atr: 0.8,
    trail_distance_atr: 0.3, breakeven_atr: 0.75,
    leverage: 2, margin_pct: 0.15,
    dd_soft_pct: 8.0, dd_halt_pct: 100.0, dd_resume_pct: 100.0,
    entry_conditions: {
      use_direction_conditions: false,
      long: { macd_hist_min: 5, stop_loss_atr: 1.2, atr_regime_ratio: 0.5, atr_regime_max: null, rsi_oversold: 40, rsi_overbought: 60, adx_min: 10 },
      short: { macd_hist_min: -5, stop_loss_atr: 1.2, atr_regime_ratio: 0.5, atr_regime_max: null, rsi_oversold: 40, rsi_overbought: 60, adx_min: 10 },
    },
  };
  const [selectedStrategyId, setSelectedStrategyId] = useState('PhantomV2');
  const [strategies, setStrategies] = useState([]);
  const [params, setParams] = useState({ ...DEFAULT_PARAMS });
  const [activeDirTab, setActiveDirTab] = useState('long');
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [dates, setDates] = useState({ start: '2020-07-04', end: '2026-07-04' });
  const [history, setHistory] = useState([]);
  const [showHistory, setShowHistory] = useState(false);
  const [expandedTrade, setExpandedTrade] = useState(null);
  const [runName, setRunName] = useState('');
  const [confirm, setConfirm] = useState(null); // { type, runId, ... }
  const [capital, setCapital] = useState(20000); // starting capital for the run (default = admin set)
  const [dataSource, setDataSource] = useState('Binance');
  const [sources, setSources] = useState([{ code: 'Binance', name: 'Binance Futures' }, { code: 'Delta', name: 'Delta Exchange' }]);
  const [fees, setFees] = useState({ taker_fee_bps: 5.9, maker_fee_bps: 2.36 });
  const defaultSectionVisibility = {
    history: true,
    setup: true,
    config: true,
    preview: true,
    summary: true,
    equity: true,
    breakdown: true,
    trades: true,
  };
  const completedRunSectionVisibility = {
    history: false,
    setup: false,
    config: false,
    preview: false,
    summary: true,
    equity: true,
    breakdown: true,
    trades: false,
  };
  const [sectionVisibility, setSectionVisibility] = useState(() => {
    if (typeof window === 'undefined') return defaultSectionVisibility;
    try {
      const saved = JSON.parse(localStorage.getItem('backtest_section_visibility') || '{}');
      return { ...defaultSectionVisibility, ...saved };
    } catch {
      return defaultSectionVisibility;
    }
  });

  // Chart Refs
  const chartContainerRef = useRef();
  const chartRef = useRef();
  const resultsRef = useRef(null);
  const pollIntervalRef = useRef(null);
  const resultsSectionRef = useRef(null);

  // Fields that the backtest shows live per-direction with the toggle ON.
  const directionalFields = [
    'macd_hist_min', 'stop_loss_atr', 'atr_regime_ratio', 'atr_regime_max',
    'rsi_oversold', 'rsi_overbought', 'adx_min',
  ];
  // Shared groups shown always (they stay single regardless of the toggle).
  const sharedGroups = {
    "Trend & Regime": ["trend_ema_period", "cooldown_bars"],
    "MACD Indicator": ["macd_fast", "macd_slow", "macd_signal"],
    "Entries (v3)": ["enable_momentum_entry"],
    "Risk & Exit Model": ["take_profit_atr", "trail_activation_atr", "trail_distance_atr", "breakeven_atr"],
    "Sizing & Drawdown Guard": ["leverage", "margin_pct", "dd_soft_pct", "dd_halt_pct", "dd_resume_pct"],
  };
  // Param groups when the toggle is OFF (all fields shared, legacy layout).
  const sharedParamGroups = {
    "Trend & Regime": ["trend_ema_period", "atr_regime_ratio", "cooldown_bars"],
    "MACD Indicator": ["macd_fast", "macd_slow", "macd_signal"],
    "Entries (v3)": ["rsi_oversold", "rsi_overbought", "adx_min", "macd_hist_min", "enable_momentum_entry"],
    "Risk & Exit Model": ["stop_loss_atr", "take_profit_atr", "trail_activation_atr", "trail_distance_atr", "breakeven_atr"],
    "Sizing & Drawdown Guard": ["leverage", "margin_pct", "dd_soft_pct", "dd_halt_pct", "dd_resume_pct"],
  };
  const useDirection = !!(params.entry_conditions && params.entry_conditions.use_direction_conditions);

  const toggleSection = (key) => setSectionVisibility(prev => ({ ...prev, [key]: !prev[key] }));
  const setSharedField = (field, value) => setParams(prev => ({ ...prev, [field]: value }));

  const setDirField = (side, field, value) => setParams(prev => ({
    ...prev,
    entry_conditions: {
      ...prev.entry_conditions,
      [side]: { ...prev.entry_conditions[side], [field]: value },
    },
  }));

  const setUseDirection = (val) => setParams(prev => {
    const p = { ...prev };
    const ec = prev.entry_conditions || DEFAULT_PARAMS.entry_conditions;
    // Pre-fill long/short from the current shared values so nothing breaks
    // for existing configs; SHORT MACD defaults to the negative of the shared
    // magnitude so bearish momentum is required (suggested starting value).
    const long = { ...ec.long };
    const short = { ...ec.short };
    long.macd_hist_min = prev.macd_hist_min ?? long.macd_hist_min ?? 5;
    long.stop_loss_atr = prev.stop_loss_atr ?? long.stop_loss_atr ?? 1.2;
    long.atr_regime_ratio = prev.atr_regime_ratio ?? long.atr_regime_ratio ?? 0.5;
    long.atr_regime_max = (long.atr_regime_max === undefined ? null : long.atr_regime_max);
    long.rsi_oversold = prev.rsi_oversold ?? long.rsi_oversold ?? 40;
    long.rsi_overbought = prev.rsi_overbought ?? long.rsi_overbought ?? 60;
    long.adx_min = prev.adx_min ?? long.adx_min ?? 10;
    short.macd_hist_min = -(prev.macd_hist_min ?? Math.abs(short.macd_hist_min ?? 5));
    short.stop_loss_atr = prev.stop_loss_atr ?? short.stop_loss_atr ?? 1.2;
    short.atr_regime_ratio = prev.atr_regime_ratio ?? short.atr_regime_ratio ?? 0.5;
    short.atr_regime_max = (short.atr_regime_max === undefined ? null : short.atr_regime_max);
    short.rsi_oversold = prev.rsi_oversold ?? short.rsi_oversold ?? 40;
    short.rsi_overbought = prev.rsi_overbought ?? short.rsi_overbought ?? 60;
    short.adx_min = prev.adx_min ?? short.adx_min ?? 10;
    p.entry_conditions = { use_direction_conditions: val, long, short };
    return p;
  });

  // The parameter form applies to PhantomV2 and to saved Phantom-style
  // strategies (params stored as an object, not Chartink rule arrays).
  const showParamForm = selectedStrategyId === 'PhantomV2' ||
    strategies.some(s => String(s.id) === String(selectedStrategyId) &&
      s.rules && typeof s.rules === 'object' && !Array.isArray(s.rules) &&
      ('entry_conditions' in s.rules || 'rsi_oversold' in s.rules));

  const renderNumberInput = (field, value, onChange) => {
    const meta = PARAM_META[field] || { label: field.replace(/_/g, ' '), hint: '' };
    return (
      <div className="flex flex-col">
        <label className="text-[10px] text-gray-400 font-semibold mb-1 flex items-center gap-1">
          {meta.label}
          {meta.hint && <span title={meta.hint} className="text-gray-600 hover:text-blue-400 cursor-help"><HelpCircle size={11} /></span>}
        </label>
        <input type="number" step="0.01" value={value ?? ''}
          onChange={onChange}
          className="bg-gray-900 p-2 rounded-lg border border-gray-700 text-white text-xs outline-none focus:border-blue-500 transition w-full" />
        {meta.hint && <span className="text-[10px] text-gray-600 mt-0.5 leading-snug hidden xl:block">{meta.hint}</span>}
      </div>
    );
  };

  const renderCheckInput = (field, checked, onChange) => {
    const meta = PARAM_META[field] || { label: field.replace(/_/g, ' '), hint: '' };
    return (
      <div className="flex flex-col">
        <label className="text-[10px] text-gray-400 font-semibold mb-1 flex items-center gap-1">
          {meta.label}
          {meta.hint && <span title={meta.hint} className="text-gray-600 hover:text-blue-400 cursor-help"><HelpCircle size={11} /></span>}
        </label>
        <label className="flex items-center gap-2 bg-gray-900 p-2 rounded-lg border border-gray-700 text-xs text-gray-300 cursor-pointer">
          <input type="checkbox" checked={checked} onChange={onChange} className="accent-blue-500" />
          Allow momentum continuation trades
        </label>
      </div>
    );
  };

  const authHeaders = () => ({ 'Authorization': `Bearer ${localStorage.getItem('token')}` });

  const fetchStrategies = async () => {
    try {
      const res = await fetch(`${API_URL}/strategies`, { headers: authHeaders() });
      const data = await res.json();
      setStrategies(Array.isArray(data) ? data : []);
    } catch (e) { }
  };

  const fetchHistory = async (options = {}) => {
    const { autoOpen = false } = options;
    try {
      const res = await fetch(`${API_URL}/backtest/history`, { headers: authHeaders() });
      const data = await res.json();
      if (Array.isArray(data)) {
        setHistory(data);
        if (autoOpen && data.length > 0) setShowHistory(true);
      }
    } catch (e) { }
  };

  // Load the user's (admin-set) default capital to prefill the form.
  useEffect(() => {
    fetch(`${API_URL}/broker-settings`, { headers: authHeaders() })
      .then(r => (r.ok ? r.json() : null))
      .then(data => { if (data && data.initial_capital) setCapital(data.initial_capital); })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fetch(`${API_URL}/fee-settings?broker_code=${encodeURIComponent(dataSource)}&mode=backtest`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null).then(v => v && setFees(v)).catch(() => {});
  }, [dataSource]);

  useEffect(() => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('backtest_section_visibility', JSON.stringify(sectionVisibility));
    }
  }, [sectionVisibility]);

  useEffect(() => {
    if (preview) {
      setSectionVisibility(prev => ({ ...prev, preview: true }));
    }
  }, [preview]);

  useEffect(() => {
    if (results) {
      setExpandedTrade(null);
      setShowHistory(false);
      setPreview(null);
      setSectionVisibility(prev => ({
        ...prev,
        ...completedRunSectionVisibility,
      }));
      window.requestAnimationFrame(() => {
        resultsSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    }
  }, [results]);

  useEffect(() => {
    if (results && results.equity_curve && sectionVisibility.equity) {
      resultsRef.current = results;
      const timer = setTimeout(() => { initEquityChart(results.equity_curve); }, 100);
      return () => clearTimeout(timer);
    }
  }, [results, sectionVisibility.equity]);

  useEffect(() => {
    if (!sectionVisibility.equity && chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
    }
  }, [sectionVisibility.equity]);

  useEffect(() => () => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
    }
  }, []);

  const resetParams = () => setParams(JSON.parse(JSON.stringify(DEFAULT_PARAMS)));

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
          initial_capital: parseFloat(capital),
          data_source: dataSource,
          fee_mode: 'backtest',
        }),
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || "Backtest failed");
      }

      const data = await response.json();
      const runId = data.run_id;

      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }

      const pollForResults = async () => {
        try {
          const res = await fetch(`${API_URL}/backtest/results/${runId}`, { headers: authHeaders() });
          if (!res.ok) return false;
          const resultData = await res.json();
          if (!isBacktestComplete(resultData.run_details)) return false;

          setResults(normalizeBacktestResults({ id: runId, ...resultData.run_details }, resultData.trades));
          setLoading(false);
          if (pollIntervalRef.current) {
            clearInterval(pollIntervalRef.current);
            pollIntervalRef.current = null;
          }
          fetchHistory();
          setRunName('');
          return true;
        } catch (e) {
          console.error('Polling error:', e);
          return false;
        }
      };

      const completed = await pollForResults();
      if (!completed) {
        pollIntervalRef.current = setInterval(pollForResults, 2000);
      }
    } catch (error) {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
      alert(error.message);
      setLoading(false);
    }
  };

  // Save the current parameter set as a new named strategy so it can be
  // re-run, paper traded or live traded later.
  const saveAsNewStrategy = async () => {
    const name = (runName.trim() || `Phantom ${new Date().toLocaleString()}`).slice(0, 60);
    setSaving(true);
    try {
      const res = await fetch(`${API_URL}/strategies/create`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, params }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to save strategy");
      alert(`Strategy "${name}" saved successfully. You can now run it from the strategy dropdown, or Paper / Live trade it from the Trading page.`);
      fetchStrategies();
      setRunName('');
    } catch (e) {
      alert(`Error saving strategy: ${e.message}`);
    }
    setSaving(false);
  };

  // Lightweight per-bucket check of the currently-set conditions.
  const runFilterPreview = async () => {
    setPreviewLoading(true);
    setPreview(null);
    try {
      const res = await fetch(`${API_URL}/backtest/filter-preview`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          params,
          start_date: dates.start,
          end_date: dates.end,
          data_source: dataSource,
          fee_mode: 'backtest',
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Preview failed");
      setPreview(data);
    } catch (e) {
      alert(`Error running filter preview: ${e.message}`);
    }
    setPreviewLoading(false);
  };

  const handleStrategySelect = (sid) => {
    setSelectedStrategyId(sid);
    // When a saved Phantom-style strategy is chosen, load its params into the
    // form so the admin can tweak it before re-running.
    const found = strategies.find(s => String(s.id) === String(sid));
    if (found && found.rules && typeof found.rules === 'object' && !Array.isArray(found.rules) &&
        (found.rules.entry_conditions || 'rsi_oversold' in found.rules)) {
      const merged = { ...DEFAULT_PARAMS, ...found.rules };
      setParams(JSON.parse(JSON.stringify(merged)));
      setRunName(found.name || '');
    }
  };

  const initEquityChart = (equityData) => {
    if (!chartContainerRef.current) return;
    if (chartRef.current) chartRef.current.remove();
    const chart = createChart(chartContainerRef.current, {
      layout: { background: { color: '#111827' }, textColor: '#9ca3af' },
      grid: { vertLines: { color: '#1f2937' }, horzLines: { color: '#1f2937' } },
      width: chartContainerRef.current.clientWidth,
      height: 380,
      timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 4 },
    });
    const areaSeries = chart.addSeries(AreaSeries, {
      lineColor: '#3b82f6',
      topColor: 'rgba(59, 130, 246, 0.4)',
      bottomColor: 'rgba(59, 130, 246, 0)',
      lineWidth: 2,
      priceLineVisible: true,
      lastValueVisible: true,
    });
    const start = resultsRef.current?.start_date ? new Date(resultsRef.current.start_date).getTime() / 1000 : 0;
    areaSeries.setData(equityData.map((val, idx) => ({ time: Math.floor(start + idx * 3600), value: val })));
    chart.timeScale().fitContent();
    chartRef.current = chart;
  };

  const loadRun = async (runId) => {
    try {
      const res = await fetch(`${API_URL}/backtest/results/${runId}`, { headers: authHeaders() });
      if (!res.ok) throw new Error((await res.json()).detail || "Run not found");
      const data = await res.json();
      if (!data.run_details) throw new Error("Run details missing");
      setResults(normalizeBacktestResults({ id: runId, ...data.run_details }, data.trades));
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
    totalTrades: results.total_trades ?? 0,
    initialCapital: results.initial_capital ?? 20000,
    finalEquity: results.final_equity_inr ?? results.final_equity ?? null,
    netProfit: (results.final_equity_inr ?? results.final_equity) != null
      ? (results.final_equity_inr ?? results.final_equity) - (results.initial_capital ?? 20000)
      : null,
    roi: results.roi,
    winRate: results.win_rate,
    profitFactor: results.profit_factor,
    sharpe: results.sharpe_ratio,
    maxDD: results.max_drawdown,
    exitDist: (results.trades || []).reduce((acc, t) => { acc[t.exit_reason] = (acc[t.exit_reason] || 0) + 1; return acc; }, {}),
    directionDist: (results.trades || []).reduce((acc, t) => { const dir = t.direction === 1 ? 'Long' : 'Short'; acc[dir] = (acc[dir] || 0) + 1; return acc; }, {}),
    rejections: results.rejected_reasons || {}
  } : null;

  const pieData = stats?.exitDist ? Object.entries(stats.exitDist).map(([name, value]) => ({ name, value })) : [];
  const COLORS = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6'];

  useEffect(() => {
    fetchStrategies(); fetchHistory({ autoOpen: true });
    fetch(`${API_URL}/broker-definitions`, { headers: authHeaders() }).then(r => r.ok ? r.json() : []).then(list => {
      if (Array.isArray(list) && list.length) setSources(list.map(x => ({ code: x.code, name: x.name })));
    }).catch(() => {});
  }, []);

  return (
    <div className="page-shell font-sans">
      <ConfirmModal
        open={!!confirm}
        title={confirm?.type === 'deleteRun' ? 'Delete Backtest Run?' : confirm?.type === 'clearAll' ? 'Clear All Backtest History?' : 'Confirm'}
        message={confirm?.type === 'deleteRun' ? `This will permanently delete "${confirm?.name}" and all its trade data.` : confirm?.type === 'clearAll' ? 'This will permanently delete ALL backtest runs and their trade data. This cannot be undone.' : ''}
        confirmLabel={confirm?.type === 'clearAll' ? 'Yes, Clear All' : 'Yes, Delete'}
        confirmColor="bg-red-600 hover:bg-red-500"
        onCancel={() => setConfirm(null)}
        onConfirm={doConfirm}
      />

      <div className="mb-8 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight text-blue-400">Backtest</h1>
          <p className="text-sm text-gray-500">Strategy optimizer with collapsible setup, configuration and results sections.</p>
        </div>
        <div className="flex flex-wrap gap-3">
          <button onClick={requestClearAll} className="flex items-center gap-2 rounded-lg border border-red-900/50 bg-red-900/20 px-4 py-2 text-sm font-semibold text-red-400 transition hover:bg-red-900/40">
            <Trash2 size={14} /> Clear History
          </button>
          <button onClick={() => setShowHistory(!showHistory)} className="rounded-lg border border-gray-700 bg-gray-800 px-4 py-2 text-sm font-semibold transition hover:bg-gray-700">
            {showHistory ? 'Hide History' : 'View History'}
          </button>
        </div>
      </div>

      {showHistory && (
        <SectionCard
          title={`Backtest History (${history.length} runs)`}
          subtitle="Open a previous run or remove it from saved history."
          icon={Timer}
          collapsed={!sectionVisibility.history}
          onToggle={() => toggleSection('history')}
          actions={
            <button onClick={() => setShowHistory(false)} className="text-xs text-gray-500 transition hover:text-white">
              Close
            </button>
          }
          className="mb-8"
        >
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {history.map(run => (
              <div key={run.id} className="group relative rounded-xl border border-gray-700 bg-gray-900 p-4 transition hover:border-blue-500">
                <div className="cursor-pointer" onClick={() => loadRun(run.id)}>
                  <div className="font-bold text-gray-200 transition group-hover:text-blue-400">{run.name || 'Unnamed Run'}</div>
                  <div className="text-xs text-gray-500">{run.start_date?.split('T')[0]} → {run.end_date?.split('T')[0]} · {run.data_source || 'Binance'}</div>
                  <div className={`mt-2 text-sm font-mono ${(run.roi || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>ROI: {(run.roi || 0).toFixed(2)}%</div>
                </div>
                <button onClick={(e) => { e.stopPropagation(); requestDeleteRun(run.id, run.name); }}
                        className="absolute right-3 top-3 rounded-lg p-1.5 text-gray-500 transition hover:bg-red-900/20 hover:text-red-400"
                        title="Delete this run">
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
          {history.length === 0 && <p className="py-4 text-center text-gray-500">No backtest runs yet.</p>}
        </SectionCard>
      )}

      <SectionCard
        title="Run Setup"
        subtitle="Choose the date range, exchange, strategy and starting capital for the next run."
        icon={CalendarRange}
        collapsed={!sectionVisibility.setup}
        onToggle={() => toggleSection('setup')}
        className="mb-8"
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <div className="flex flex-col">
            <label className="mb-1 text-[10px] font-bold uppercase text-gray-500">Start date</label>
            <DateInput value={dates.start} onChange={e => setDates({ ...dates, start: e.target.value })} />
          </div>
          <div className="flex flex-col">
            <label className="mb-1 text-[10px] font-bold uppercase text-gray-500">End date</label>
            <DateInput value={dates.end} onChange={e => setDates({ ...dates, end: e.target.value })} />
          </div>
          <div className="flex flex-col">
            <label className="mb-1 text-[10px] font-bold uppercase text-gray-500">Market data / exchange</label>
            <select value={dataSource} onChange={e => setDataSource(e.target.value)}
              className="rounded-lg border border-gray-700 bg-gray-900 p-2 text-sm text-white outline-none transition focus:ring-2 focus:ring-blue-500">
              {sources.map(s => <option key={s.code} value={s.code}>{s.name}</option>)}
            </select>
          </div>
          <div className="flex flex-col">
            <label className="mb-1 text-[10px] font-bold uppercase text-gray-500">Strategy to test</label>
            <select value={selectedStrategyId} onChange={e => handleStrategySelect(e.target.value)}
              className="rounded-lg border border-gray-700 bg-gray-900 p-2 text-sm text-white outline-none transition focus:ring-2 focus:ring-blue-500">
              <option value="PhantomV2">Phantom V2.5 (Default)</option>
              {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
          <div className="flex flex-col">
            <label className="mb-1 flex items-center gap-1 text-[10px] font-bold uppercase text-gray-500"><Tag size={10} /> Run name (optional)</label>
            <input type="text" placeholder="e.g. Aggressive RSI Test" value={runName} onChange={e => setRunName(e.target.value)}
              className="rounded-lg border border-gray-700 bg-gray-900 p-2 text-sm text-white outline-none transition focus:ring-2 focus:ring-blue-500" maxLength={60} />
          </div>
          <div className="flex flex-col">
            <label className="mb-1 flex items-center gap-1 text-[10px] font-bold uppercase text-gray-500"><Wallet size={10} /> Starting capital (₹)</label>
            <input type="number" min="1000" step="1000" value={capital} onChange={e => setCapital(e.target.value)}
              className="rounded-lg border border-gray-700 bg-gray-900 p-2 text-sm text-white outline-none transition focus:ring-2 focus:ring-blue-500" />
          </div>
          <div className="flex flex-col sm:col-span-2">
            <label className="mb-1 text-[10px] font-bold uppercase text-gray-500">{dataSource} fee schedule</label>
            <div className="flex min-h-[38px] flex-wrap items-center gap-x-5 gap-y-1 rounded-lg border border-gray-700 bg-gray-900 px-3 py-2">
              <span className="text-[11px] text-gray-400">Taker <b className="ml-1 font-mono text-white">{Number(fees.taker_fee_bps || 0).toFixed(2)} bps</b></span>
              <span className="text-[11px] text-gray-400">Maker <b className="ml-1 font-mono text-white">{Number(fees.maker_fee_bps || 0).toFixed(2)} bps</b></span>
              <span className="text-[10px] text-gray-600">Applied to every fill in this run</span>
            </div>
          </div>
        </div>
      </SectionCard>

      {showParamForm && (
        <SectionCard
          title="Strategy Configuration"
          subtitle="Tune PHANTOM parameters, then hide this section when you want more room for results."
          icon={SlidersHorizontal}
          collapsed={!sectionVisibility.config}
          onToggle={() => toggleSection('config')}
          className="mb-8"
        >
          <div className="mb-6">
            <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-gray-700 bg-gray-900 p-3 text-sm text-gray-300">
              <input type="checkbox" checked={useDirection}
                onChange={e => setUseDirection(e.target.checked)}
                className="mt-0.5 h-4 w-4 accent-blue-500" />
              <div>
                <div className="font-bold text-white">Use separate conditions for Long / Short</div>
                <div className="mt-0.5 text-[11px] text-gray-500">
                  When ON, tune MACD-hist min, stop-loss ATR, ATR regime floor + max-ATR cap and RSI/ADX independently for LONG and SHORT.
                  Off = shared conditions (current v2.5 behaviour).
                </div>
              </div>
            </label>
          </div>

          {!useDirection && (
            <div className="grid grid-cols-1 gap-6 border-t border-gray-700 pt-6 sm:grid-cols-2 xl:grid-cols-4">
              {Object.entries(sharedParamGroups).map(([groupName, fields]) => (
                <div key={groupName} className="space-y-3">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-blue-400">{groupName}</h3>
                  <div className="space-y-3">
                    {fields.map(field => field === 'enable_momentum_entry'
                      ? renderCheckInput(field, !!params[field], e => setSharedField(field, e.target.checked))
                      : renderNumberInput(field, params[field], e => setSharedField(field, parseFloat(e.target.value))))}
                  </div>
                </div>
              ))}
            </div>
          )}

          {useDirection && (
            <>
              <div className="mb-6 grid grid-cols-1 gap-6 sm:grid-cols-2 xl:grid-cols-4">
                {Object.entries(sharedGroups).map(([groupName, fields]) => (
                  <div key={groupName} className="space-y-3">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-blue-400">{groupName}</h3>
                    <div className="space-y-3">
                      {fields.map(field => field === 'enable_momentum_entry'
                        ? renderCheckInput(field, !!params[field], e => setSharedField(field, e.target.checked))
                        : renderNumberInput(field, params[field], e => setSharedField(field, parseFloat(e.target.value))))}
                    </div>
                  </div>
                ))}
              </div>

              <div className="border-t border-gray-700 pt-5">
                <div className="mb-4 flex gap-2">
                  {['long', 'short'].map(side => (
                    <button key={side} onClick={() => setActiveDirTab(side)}
                      className={`rounded-lg px-5 py-2 text-xs font-bold uppercase tracking-wider transition ${
                        activeDirTab === side
                          ? side === 'long'
                            ? 'bg-green-600 text-white shadow-lg shadow-green-900/20'
                            : 'bg-red-600 text-white shadow-lg shadow-red-900/20'
                          : 'border border-gray-700 bg-gray-900 text-gray-400 hover:text-white'}`}>
                      {side === 'long' ? 'Long' : 'Short'}
                      <span className="ml-1 opacity-80">{side === 'long' ? '▲' : '▼'}</span>
                    </button>
                  ))}
                  <span className="ml-auto self-center text-[10px] text-gray-500">
                    Editing: <b className={activeDirTab === 'long' ? 'text-green-400' : 'text-red-400'}>{activeDirTab}</b> conditions
                  </span>
                </div>
                <div className="grid grid-cols-1 gap-6 sm:grid-cols-3">
                  {directionalFields.map(field => (
                    <div key={field} className="flex flex-col">
                      <label className="mb-1 flex items-center gap-1 text-[10px] font-semibold text-gray-400">
                        {(PARAM_META[field]?.label) || field.replace(/_/g, ' ')}
                        <span className={activeDirTab === 'long' ? 'text-green-500' : 'text-red-500'}>({activeDirTab})</span>
                        {PARAM_META[field]?.hint && <span title={PARAM_META[field].hint} className="cursor-help text-gray-600"><HelpCircle size={11} /></span>}
                      </label>
                      <input type="number" step="0.01"
                        value={(params.entry_conditions && params.entry_conditions[activeDirTab] && params.entry_conditions[activeDirTab][field]) ?? (field === 'atr_regime_max' ? '' : 0) }
                        onChange={e => {
                          const val = e.target.value;
                          setDirField(activeDirTab, field, val === '' ? null : parseFloat(val));
                        }}
                        className="w-full rounded-lg border border-gray-700 bg-gray-900 p-2 text-xs text-white outline-none transition focus:border-blue-500" />
                      {field === 'atr_regime_max' && (
                        <span className="mt-0.5 text-[9px] text-gray-600">max-ATR cap (multiples of SMA; blank = off)</span>
                      )}
                      {field === 'atr_regime_ratio' && (
                        <span className="mt-0.5 text-[9px] text-gray-600">min-ATR floor (lower = more trades)</span>
                      )}
                      {field === 'macd_hist_min' && (
                        <span className="mt-0.5 text-[9px] text-gray-600">{activeDirTab === 'long' ? 'require hist ≥ this' : 'require hist ≤ this (use negative for bearish)'}</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </SectionCard>
      )}

      <SectionCard
        title="Run Controls"
        subtitle="Preview filters, save the current settings, or run the full backtest."
        icon={Play}
        className="mb-8"
      >
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <p className="max-w-md text-[11px] text-gray-500">
            <SlidersHorizontal size={12} className="mr-1 inline text-gray-600" />
            Preview Filters is a fast quality check. Run Backtest builds the full equity curve and trade log.
          </p>
          <div className="flex flex-col flex-wrap gap-2 sm:flex-row sm:items-center">
            <button onClick={resetParams} className="flex items-center justify-center gap-2 px-4 py-2 text-xs text-gray-500 transition hover:text-white">
              <RotateCcw size={14} /> Reset defaults
            </button>
            <button onClick={runFilterPreview} disabled={previewLoading || loading}
              className="flex items-center justify-center gap-2 rounded-xl border border-blue-800/50 px-4 py-2 text-xs font-semibold text-blue-300 transition hover:bg-blue-900/20 disabled:opacity-50">
              {previewLoading ? <div className="h-3 w-3 rounded-full border-2 border-blue-400 border-t-transparent animate-spin"></div> : 'Preview Filters'}
            </button>
            <button onClick={saveAsNewStrategy} disabled={saving}
              className="flex items-center justify-center gap-2 rounded-xl bg-green-700 px-4 py-2 text-xs font-bold text-white transition hover:bg-green-600 disabled:opacity-50">
              <Download size={14} /> {saving ? 'Saving...' : 'Save as strategy'}
            </button>
            <button onClick={runBacktest} disabled={loading} className="flex items-center justify-center gap-2 rounded-xl bg-blue-600 px-8 py-3 font-bold shadow-lg shadow-blue-900/20 transition hover:bg-blue-500 disabled:opacity-50">
              {loading ? <div className="h-4 w-4 rounded-full border-2 border-white border-t-transparent animate-spin"></div> : <><Play size={16} /> Run Backtest</>}
            </button>
          </div>
        </div>
      </SectionCard>

      {preview && (
        <SectionCard
          title="Filter Preview — per bucket"
          subtitle={`${preview.total_trades} trades · WR ${preview.total_win_rate}% · PF ${preview.total_profit_factor}`}
          icon={Activity}
          collapsed={!sectionVisibility.preview}
          onToggle={() => toggleSection('preview')}
          actions={
            <>
              {preview.use_direction_conditions && <span className="rounded border border-purple-800/40 bg-purple-900/40 px-2 py-0.5 text-[10px] text-purple-300">direction-specific ON</span>}
              <button onClick={() => setPreview(null)} className="text-xs text-gray-500 transition hover:text-white">Close</button>
            </>
          }
          className="mb-8 border-blue-800/40"
        >
          <p className="mb-4 text-xs text-gray-500">
            Buckets reflect the conditions currently set in the configuration form.
          </p>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
            {Object.entries(preview.buckets || {}).map(([key, b]) => {
              const side = key.split('_')[0];
              const isLong = side === 'LONG';
              return (
                <div key={key} className={`rounded-xl border p-4 ${isLong ? 'border-green-800/40 bg-green-900/10' : 'border-red-800/40 bg-red-900/10'}`}>
                  <div className="mb-2 flex items-center justify-between">
                    <span className={`text-xs font-bold ${isLong ? 'text-green-400' : 'text-red-400'}`}>{key}</span>
                    <span className="rounded bg-gray-900 px-2 py-0.5 text-[10px] text-gray-400">{b.count} trades</span>
                  </div>
                  <div className="space-y-1 font-mono text-[11px]">
                    <div className="flex justify-between"><span className="text-gray-500">Win rate</span><span className="text-white">{b.win_rate}%</span></div>
                    <div className="flex justify-between"><span className="text-gray-500">Profit factor</span><span className="text-white">{b.profit_factor}</span></div>
                    <div className="flex justify-between"><span className="text-gray-500">Avg PnL</span><span className={b.avg_pnl >= 0 ? 'text-green-400' : 'text-red-400'}>₹{b.avg_pnl}</span></div>
                    <div className="flex justify-between"><span className="text-gray-500">Net PnL</span><span className={b.net_pnl >= 0 ? 'text-green-400' : 'text-red-400'}>₹{b.net_pnl}</span></div>
                  </div>
                </div>
              );
            })}
          </div>
        </SectionCard>
      )}

      {results ? (
        <div ref={resultsSectionRef} className="animate-in space-y-8 fade-in duration-500">
          <SectionCard
            title="Backtest Summary"
            subtitle={`${results.name || 'Unnamed Run'} · ${results.start_date ? new Date(results.start_date).toLocaleDateString() : '—'} → ${results.end_date ? new Date(results.end_date).toLocaleDateString() : '—'} · ${results.data_source || 'Binance'} · ${results.total_trades ?? 0} trades · fees ${results.taker_fee_bps != null ? Number(results.taker_fee_bps).toFixed(2) : '—'}/${results.maker_fee_bps != null ? Number(results.maker_fee_bps).toFixed(2) : '—'} bps`}
            icon={Timer}
            collapsed={!sectionVisibility.summary}
            onToggle={() => toggleSection('summary')}
            actions={
              <>
                <button onClick={exportTradesCSV} className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-xs font-bold transition hover:bg-blue-500">
                  <Download size={14} /> CSV Export
                </button>
                <button onClick={() => requestDeleteRun(results.id, results.name)} className="flex items-center gap-2 rounded-lg bg-red-900/30 px-4 py-2 text-xs font-bold text-red-300 transition hover:bg-red-900/50">
                  <Trash2 size={14} /> Delete
                </button>
              </>
            }
          >
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5">
              <StatCard label="Initial Capital" value={formatCurrencyValue(stats?.initialCapital)} color="text-yellow-400" />
              <StatCard label="Final Equity" value={formatCurrencyValue(stats?.finalEquity)} color={stats?.finalEquity >= (stats?.initialCapital || 0) ? 'text-green-400' : 'text-red-400'} />
              <StatCard label="Net Profit" value={formatCurrencyValue(stats?.netProfit)} color={stats?.netProfit >= 0 ? 'text-green-400' : 'text-red-400'} />
              <StatCard label="ROI" value={formatPercentValue(stats?.roi)} color={stats?.roi >= 0 ? 'text-green-400' : 'text-red-400'} />
              <StatCard label="Win Rate" value={formatPercentValue(stats?.winRate)} color="text-purple-400" />
            </div>
          </SectionCard>

          <SectionCard
            title="Equity Curve"
            subtitle="Expand this section to inspect the run's equity growth over time."
            icon={TrendingUp}
            collapsed={!sectionVisibility.equity}
            onToggle={() => toggleSection('equity')}
          >
            <div ref={chartContainerRef} className="w-full" />
          </SectionCard>

          <SectionCard
            title="Performance Breakdown"
            subtitle="Exit distribution, core metrics and rejected signal counts."
            icon={Activity}
            collapsed={!sectionVisibility.breakdown}
            onToggle={() => toggleSection('breakdown')}
          >
            <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
              <div className="flex flex-col rounded-2xl border border-gray-700 bg-gray-800 p-6 shadow-xl">
                <h3 className="mb-4 text-sm font-semibold text-gray-400">Exit Distribution</h3>
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

              <div className="rounded-2xl border border-gray-700 bg-gray-800 p-6 shadow-xl">
                <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-400">
                  <Activity size={16} /> Core Metrics
                </h3>
                <div className="space-y-4">
                  <MetricRow label="Total Trades" value={stats?.totalTrades ?? 0} />
                  <MetricRow label="Profit Factor" value={stats?.profitFactor != null ? Number(stats.profitFactor).toFixed(2) : '—'} />
                  <MetricRow label="Sharpe Ratio" value={stats?.sharpe != null ? Number(stats.sharpe).toFixed(2) : '—'} />
                  <MetricRow label="Max Drawdown" value={formatPercentValue(stats?.maxDD)} color="text-red-400" />
                  <MetricRow label="Longs" value={`${stats?.directionDist?.Long || 0} (${stats?.totalTrades ? ((stats?.directionDist?.Long || 0) / stats?.totalTrades * 100).toFixed(1) : 0}%)`} />
                  <MetricRow label="Shorts" value={`${stats?.directionDist?.Short || 0} (${stats?.totalTrades ? ((stats?.directionDist?.Short || 0) / stats?.totalTrades * 100).toFixed(1) : 0}%)`} />

                  <div className="mt-6 border-t border-gray-700 pt-6">
                    <div className="mb-3 flex items-center justify-between">
                      <span className="text-xs font-bold uppercase text-gray-500">Rejected Signals</span>
                      <span className="rounded border border-red-900/50 bg-red-900/30 px-2 py-0.5 text-xs font-bold text-red-400">
                        {Object.values(stats?.rejections || {}).reduce((a, b) => a + b, 0)}
                      </span>
                    </div>
                    <div className="space-y-2">
                      {Object.entries(stats?.rejections || {}).map(([reason, count]) => (
                        <div key={reason} className="flex items-center justify-between rounded-lg border border-gray-700/50 bg-gray-900 p-2">
                          <span className="text-[10px] italic text-gray-500">{reason}</span>
                          <span className="text-xs font-mono font-bold text-gray-300">{count}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </SectionCard>

          <SectionCard
            title={`Detailed Trade Logs (${results.trades?.length || 0})`}
            subtitle="Entry conditions per candle. Expand a row for deeper inspection."
            icon={Activity}
            collapsed={!sectionVisibility.trades}
            onToggle={() => toggleSection('trades')}
            actions={
              <button onClick={exportTradesCSV} className="rounded bg-blue-600 px-3 py-1 text-[10px] font-bold transition hover:bg-blue-500">
                ⬇ CSV Export
              </button>
            }
            className="overflow-hidden"
          >
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="bg-gray-900 uppercase text-gray-500">
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
                      <tr className="cursor-pointer border-b border-gray-700 transition hover:bg-gray-700/30"
                          onClick={() => setExpandedTrade(expandedTrade === i ? null : i)}>
                        <td className="p-3 font-mono text-gray-400">{t.signal_candle_time ? new Date(t.signal_candle_time).toLocaleString() : (t.entry_time ? new Date(t.entry_time).toLocaleString() : 'N/A')}</td>
                        <td className="p-3">{(t.entry_price || 0).toFixed(2)}</td>
                        <td className="p-3">{(t.exit_price || 0).toFixed(2)}</td>
                        <td className={`p-3 font-bold ${t.direction === 1 ? 'text-green-400' : 'text-red-400'}`}>{t.direction === 1 ? 'L' : 'S'}</td>
                        <td className="p-3"><span className={`rounded px-2 py-0.5 text-[10px] font-bold ${t.setup === 'MOMENTUM' ? 'bg-purple-900/40 text-purple-300' : 'bg-blue-900/40 text-blue-300'}`}>{t.setup || '—'}</span></td>
                        <td className={`p-3 ${t.candle_type === 'GREEN' ? 'text-green-400' : t.candle_type === 'RED' ? 'text-red-400' : 'text-gray-400'}`}>{t.candle_type || '—'}</td>
                        <td className={`p-3 ${t.trend_4h === 'UP' ? 'text-green-400' : 'text-red-400'}`}>{t.trend_4h || '—'}</td>
                        <td className="p-3">{t.rsi14 != null ? t.rsi14.toFixed(1) : '—'}</td>
                        <td className="p-3">{t.adx != null ? t.adx.toFixed(1) : '—'}</td>
                        <td className={`p-3 font-bold ${t.net_pnl > 0 ? 'text-green-400' : 'text-red-400'}`}>₹{(t.net_pnl || 0).toFixed(2)}</td>
                        <td className="p-3"><span className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-[10px] text-gray-400">{t.exit_reason || 'N/A'}</span></td>
                        <td className="p-3 text-gray-500">{expandedTrade === i ? '▼' : '▶'}</td>
                      </tr>
                      {expandedTrade === i && (
                        <tr className="border-b border-gray-700 bg-gray-900/60">
                          <td colSpan={12} className="p-4">
                            <div className="grid grid-cols-2 gap-3 text-[11px] md:grid-cols-4">
                              <div>
                                <div className="mb-1 text-[9px] font-bold uppercase text-gray-500">Entry Conditions</div>
                                <div className="flex flex-wrap gap-1">
                                  <CondChip ok={t.conditions?.adx_ok} label={`ADX≥min (${t.adx?.toFixed(1) ?? '?'})`} />
                                  <CondChip ok={t.conditions?.macd_hist_ok} label="MACD-hist mag" />
                                  <CondChip ok={t.conditions?.atr_regime_ok} label="ATR regime" />
                                  <CondChip ok={t.conditions?.rsi_ok} label="RSI trigger" />
                                  <CondChip ok={t.conditions?.macd_confirm_ok} label="MACD confirm" />
                                </div>
                              </div>
                              <div>
                                <div className="mb-1 text-[9px] font-bold uppercase text-gray-500">Indicators @ Signal</div>
                                <div className="space-y-0.5 font-mono text-gray-300">
                                  <div>MACD-hist: {t.macd_hist?.toFixed(2) ?? '—'}</div>
                                  <div>ATR14: {t.atr14?.toFixed(2) ?? '—'}</div>
                                  <div>EMA50 1h: {t.ema50_1h?.toFixed(2) ?? '—'}</div>
                                  <div>EMA50 4h: {t.ema50_4h?.toFixed(2) ?? '—'}</div>
                                </div>
                              </div>
                              <div>
                                <div className="mb-1 text-[9px] font-bold uppercase text-gray-500">Risk Model</div>
                                <div className="space-y-0.5 font-mono text-gray-300">
                                  <div>SL: {t.sl?.toFixed(2) ?? '—'}</div>
                                  <div>TP: {t.tp?.toFixed(2) ?? '—'}</div>
                                  <div>Margin: ₹{(t.margin || 0).toFixed(0)} ({((t.margin_pct_used || 0) * 100).toFixed(1)}%)</div>
                                  <div>Lots: {(t.lots || 0).toFixed(4)} • DD@entry: {(t.entry_dd_pct || 0).toFixed(1)}%</div>
                                </div>
                              </div>
                              <div>
                                <div className="mb-1 text-[9px] font-bold uppercase text-gray-500">Result</div>
                                <div className="space-y-0.5 font-mono text-gray-300">
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
          </SectionCard>
        </div>
      ) : (
        <div className="rounded-2xl border border-gray-700 bg-gray-800 p-8 text-center shadow-inner sm:p-16">
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-gray-900 text-gray-600">
            <TrendingUp size={32} />
          </div>
          <h3 className="text-xl font-bold text-gray-400">No Backtest Data</h3>
          <p className="mx-auto mt-2 max-w-md text-sm text-gray-600">Configure your strategy parameters and date range, then hit "Run Backtest" to analyze the equity curve.</p>
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
