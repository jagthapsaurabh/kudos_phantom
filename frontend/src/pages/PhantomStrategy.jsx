import React, { useState, useEffect } from 'react';
import { API_URL } from '../api';
import { BookOpen, Calculator } from 'lucide-react';
import StrategyExplainedTab from './StrategyExplainedTab';

const authHeaders = () => ({ 'Authorization': `Bearer ${localStorage.getItem('token')}`, 'Content-Type': 'application/json' });

// ------------------------------------------------------ Strategy docs tab --
const DocSection = ({ title, color, children }) => (
  <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
    <h3 className={`text-sm font-bold uppercase tracking-wider mb-3 ${color}`}>{title}</h3>
    <div className="space-y-2 text-sm text-gray-300">{children}</div>
  </div>
);

const Rule = ({ name, children }) => (
  <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50">
    <span className="font-mono text-blue-300 text-xs font-bold">{name}</span>
    <div className="text-gray-400 text-xs mt-1 leading-relaxed">{children}</div>
  </div>
);

const StrategyRulesTab = ({ profile, champion }) => {
  const cfg = champion?.config || {};
  const fmt = (v) => (typeof v === 'boolean' ? (v ? 'ON' : 'OFF') : v);
  const interesting = [
    'adx_min', 'macd_fast', 'macd_slow', 'macd_signal', 'macd_hist_min',
    'rsi_oversold', 'rsi_overbought', 'atr_regime_ratio',
    'enable_momentum_entry', 'trend_ema_period', 'stop_loss_atr', 'take_profit_atr',
    'trail_activation_atr', 'trail_distance_atr', 'breakeven_atr', 'timeout_bars',
    'cooldown_bars', 'leverage', 'margin_pct', 'reduced_margin_pct',
    'dd_soft_pct', 'dd_halt_pct', 'dd_resume_pct',
  ];
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <DocSection title="📈 Entry Conditions — Setup A: RSI Reversal (LONG)" color="text-green-400">
        <p className="text-xs text-gray-500">All five filters must pass on the same 1h candle; entry executes on the next candle's open.</p>
        <Rule name="1. Trend alignment (4h)">Close(1h) &gt; EMA50(4h) — longs only with the macro uptrend. Shorts require Close &lt; EMA50(4h).</Rule>
        <Rule name="2. ADX filter">ADX(14) ≥ {cfg.adx_min ?? 10} — market must be trending, not chopping.</Rule>
        <Rule name="3. MACD magnitude">|MACD-hist(12,26,9)| ≥ {cfg.macd_hist_min ?? 5} — enough momentum behind the move. The MACD indicator uses periods {cfg.macd_fast ?? 12}/{cfg.macd_slow ?? 26}/{cfg.macd_signal ?? 9}; <b>macd_hist_min is the threshold</b> applied to that histogram, not the indicator itself.</Rule>
        <Rule name="4. ATR volatility regime">ATR(14) ≥ {cfg.atr_regime_ratio ?? 0.5} × SMA50(ATR) — volatility must be alive.</Rule>
        <Rule name="5. Reversal trigger">Prev candle RSI(14) &lt; {cfg.rsi_oversold ?? 40} (long) / &gt; {cfg.rsi_overbought ?? 60} (short) <b>and</b> current candle closes green (long) / red (short).</Rule>
        <Rule name="6. MACD confirmation">MACD-hist rising vs previous bar (long) / falling (short).</Rule>
      </DocSection>

      <DocSection title="⚡ Entry Conditions — Setup B: Momentum Continuation (v3)" color="text-purple-400">
        <p className="text-xs text-gray-500">Adds trend-continuation trades so the strategy trades far more often than reversal-only. Currently <b>{fmt(cfg.enable_momentum_entry)}</b>.</p>
        <Rule name="1. Trend alignment (4h)">Same EMA50(4h) trend filter as Setup A.</Rule>
        <Rule name="2. ADX filter">ADX(14) ≥ {cfg.adx_min ?? 10}.</Rule>
        <Rule name="3. ATR regime">Same volatility regime filter as Setup A.</Rule>
        <Rule name="4. DI confirmation">+DI &gt; −DI (long) / −DI &gt; +DI (short) — directional agreement.</Rule>
        <Rule name="5. MACD zero-cross">MACD-hist crosses above 0 (long) / below 0 (short) on this candle — fresh momentum burst.</Rule>
        <Rule name="6. RSI agreement">RSI(14) ≥ {cfg.momentum_rsi_min ?? 50} (long) / ≤ {cfg.momentum_rsi_min ? (100 - cfg.momentum_rsi_min) : 50} (short).</Rule>
      </DocSection>

      <DocSection title="🛡️ Risk, Exits & Drawdown Guard" color="text-red-400">
        <Rule name="Stop loss">{cfg.stop_loss_atr ?? 1.2}×ATR from entry, with a hard floor of {(cfg.sl_floor_pct ?? 0.016) * 100}% of price.</Rule>
        <Rule name="Take profit">{cfg.take_profit_atr ?? 14}×ATR from entry (maker fee on TP fills).</Rule>
        <Rule name="Trailing stop">Activates after +{cfg.trail_activation_atr ?? 0.8}×ATR; trails the peak at {cfg.trail_distance_atr ?? 0.3}×ATR.</Rule>
        <Rule name="Breakeven stop (v3)">After +{cfg.breakeven_atr ?? 0.75}×ATR in profit, the stop is ratcheted to the entry price — winners can't become losers.</Rule>
        <Rule name="Time stop">Positions older than {cfg.timeout_bars ?? 72} bars are closed at market ("MH").</Rule>
        <Rule name="Cooldown">{cfg.cooldown_bars ?? 0} bar(s) after every close before a new entry is allowed.</Rule>
        <Rule name="Drawdown guard (v3)">Past {cfg.dd_soft_pct ?? 8}% equity drawdown, position size drops to {(cfg.reduced_margin_pct ?? 0.075) * 100}% margin. At {cfg.dd_halt_pct ?? 100}% DD new entries halt entirely and resume below {cfg.dd_resume_pct ?? 100}% DD (100 = guard off).</Rule>
        <Rule name="Position sizing">{(cfg.margin_pct ?? 0.15) * 100}% of equity as margin × {cfg.leverage ?? 2} leverage, quantized to 0.001 BTC lots.</Rule>
        <Rule name="Signal validator">Entries are rejected if price drifts &gt;1% between signal close and next open (gap protection).</Rule>
      </DocSection>

      <DocSection title={`⚙️ Live Champion Config (${profile || 'loading…'})`} color="text-yellow-400">
        <p className="text-xs text-gray-500">This is the exact tuned parameter set currently powering backtests and the signal overlay.</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs">
          {interesting.map(k => {
            const defaults = { macd_fast: 12, macd_slow: 26, macd_signal: 9, trend_ema_period: 50 };
            const val = cfg[k] !== undefined && cfg[k] !== null ? cfg[k] : defaults[k];
            return (
              <div key={k} className="flex justify-between border-b border-gray-700/50 py-1">
                <span className="text-gray-500">{k}</span>
                <span className="text-gray-200 font-bold">{fmt(val)}</span>
              </div>
            );
          })}
        </div>
      </DocSection>
    </div>
  );
};

// ------------------------------------------------------------- Page shell --
const PhantomStrategy = () => {
  const [tab, setTab] = useState('rules');
  const [champion, setChampion] = useState(null);

  useEffect(() => {
    fetch(`${API_URL}/phantom/config`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(setChampion)
      .catch(() => {});
  }, []);

  const tabs = [
    { id: 'rules', label: 'Strategy Rules', icon: <BookOpen size={16} /> },
    { id: 'explained', label: 'Strategy Explained', icon: <Calculator size={16} /> },
  ];

  return (
    <div className="page-shell font-sans">
      <header className="mb-8 flex flex-wrap justify-between items-center gap-3">
        <div>
          <h1 className="text-2xl sm:text-3xl font-extrabold text-blue-400 tracking-tight flex items-center gap-3">
            <BookOpen size={28} /> Kudos Strategy
          </h1>
          <p className="text-gray-500 text-sm mt-1">How Kudos v3 enters, manages and exits positions — plus a formula-by-formula deep dive.</p>
        </div>
      </header>

      <div className="flex flex-wrap gap-2 mb-6 border-b border-gray-800 pb-4">
        {tabs.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition ${tab === t.id ? 'bg-blue-600 text-white shadow-lg shadow-blue-900/30' : 'bg-gray-800 text-gray-400 hover:text-white hover:bg-gray-700'}`}>
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {tab === 'rules' && <StrategyRulesTab profile={champion?.profile} champion={champion} />}
      {tab === 'explained' && <StrategyExplainedTab champion={champion} />}
    </div>
  );
};

export default PhantomStrategy;
