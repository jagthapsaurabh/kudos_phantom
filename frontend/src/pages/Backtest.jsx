import React, { useState, useEffect, useRef } from 'react';
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts';
import { createChart, AreaSeries } from 'lightweight-charts';
import { API_URL } from '../api';
import DateInput from '../components/DateInput';
import TradingWindowsEditor from '../components/TradingWindowsEditor';
import { emptySchedule, normalizeSchedule, isScheduleActive, describeSchedule } from '../utils/tradingWindows';
import { Activity, TrendingUp, RotateCcw, Trash2, Tag, Download, Timer, HelpCircle, Play, SlidersHorizontal, CalendarRange, Wallet, ChevronDown, ChevronUp, Target, PauseCircle, LineChart } from 'lucide-react';
import MarketOverlayChart from '../components/MarketOverlayChart';
import PhantomPresetOptions from '../components/PhantomPresetOptions';
import RiskExitModelEditor from '../components/RiskExitModelEditor';
import { DEFAULT_RISK_EXIT, RISK_EXIT_META, riskExitText } from '../utils/riskExit';
import {
  SETUP_MODES, TRADE_DIRECTIONS, MACD_LINE_RULES, MACD_LINE_RULE_KEYS, DEFAULT_MACD_LINE_RULES,
  parsePhantomVariant, isPhantomBuiltin, builtinStrategyName, macdLineRuleText,
  FAST_TEST_ID, FAST_TEST_NAME, FAST_TEST_V1_ID, FAST_TEST_V1_NAME, isFastTestV1,
} from '../utils/phantomPresets';

const PARAM_META = {
  trend_ema_period: { label: 'Trend EMA', hint: 'How far back the 4h trend looks. Higher = slower, fewer trades.' },
  rsi_oversold: { label: 'RSI oversold', hint: 'Long reversal fires after RSI was below this level.' },
  rsi_overbought: { label: 'RSI overbought', hint: 'Short reversal fires after RSI was above this level.' },
  adx_min: { label: 'Min ADX', hint: 'Skip choppy markets. Higher = only strong trends.' },
  macd_fast: { label: 'MACD fast (EMA)', hint: 'Short MACD period. MACD line = EMA(fast) − EMA(slow).' },
  macd_slow: { label: 'MACD slow (EMA)', hint: 'Long MACD period. Must be greater than fast.' },
  macd_signal: { label: 'MACD signal', hint: 'Signal-line period: Signal = EMA(MACD_line, signal). Histogram = MACD_line − Signal.' },
  macd_hist_min: { label: 'MACD hist min', hint: 'Minimum momentum size. Longs: hist ≥ this. Shorts: hist ≤ this (use a negative).' },
  atr_regime_ratio: { label: 'Min ATR floor', hint: 'Require ATR ≥ this × its 50-bar average. Lower = more trades. Turn on the switch below to pick the comparison (>, <, ≥, ≤) and value separately for Long and Short.' },
  atr_regime_max: { label: 'Max ATR cap', hint: 'Optional: skip high-volatility. Blank = off.' },
  enable_momentum_entry: { label: 'Momentum entries', hint: 'Also take trend-continuation trades, not just reversals.' },
  cooldown_bars: { label: 'Cooldown bars', hint: 'Wait this many candles after a close before a new entry.' },
  stop_loss_atr: { label: 'Stop loss (ATR)', hint: 'Distance of the stop from entry, in ATRs. Higher = wider stop.' },
  take_profit_atr: { label: 'Take profit (ATR)', hint: 'Distance of the profit target from entry, in ATRs.' },
  trail_activation_atr: { label: 'Trail activation', hint: 'Start trailing the stop after this much profit (ATR).' },
  trail_distance_atr: { label: 'Trail distance', hint: 'How tightly the trail follows price, in ATRs.' },
  breakeven_atr: { label: 'Breakeven after', hint: 'Move stop to entry once profit reaches this many ATRs.' },
  // v3.6 — price-model percentages (fractions in the payload, % in the form).
  ...RISK_EXIT_META,
  leverage: { label: 'Leverage', hint: 'Position notional = margin × leverage.' },
  margin_pct: { label: 'Margin % of equity', hint: 'Share of equity used as margin per trade (0.15 = 15%).' },
  dd_soft_pct: { label: 'Soft drawdown %', hint: 'Past this equity drawdown, position size is reduced.' },
  dd_halt_pct: { label: 'Halt drawdown %', hint: 'Past this, new entries stop. 100 = guard off.' },
  dd_resume_pct: { label: 'Resume drawdown %', hint: 'Start entries again once drawdown falls below this.' },
  // v3.5 — strategy separation + MACD line / signal line rules.
  setup_mode: { label: 'Setup', hint: 'Which entry setup may fire: both (original), Reversal only (Setup A) or Momentum only (Setup B).' },
  trade_direction: { label: 'Direction', hint: 'Which side may be opened: both (original), Long only or Short only.' },
  macd_line_min: { label: 'MACD line level', hint: 'Optional. Longs need MACD line ≥ this, shorts ≤ minus this. Blank = off.' },
  macd_signal_min: { label: 'Signal line level', hint: 'Optional. Longs need signal line ≥ this, shorts ≤ minus this. Blank = off.' },
  // ---- Fast Test (debug) + Fast Test V1.0 -----------------------------------
  // The two debug strategies share the Phantom risk / sizing / timing plan, so
  // every value here is a field the backend already reads for them. `percent`
  // fields are stored as fractions (0.016) and shown as 1.6.
  timeout_bars: { label: 'Timeout (bars)', hint: 'Close the trade after this many candles with no other exit.' },
  lot_size_btc: { label: 'Lot size (BTC)', hint: 'Venue contract step — the position is rounded down to it.' },
  reduced_margin_pct: { label: 'Reduced margin %', hint: 'Margin per trade once the soft drawdown limit is passed (12.5 = 12.5%).', percent: true },
  sl_floor_pct: { label: 'Stop floor %', hint: 'The ATR stop is never tighter than this share of price (1.6 = 1.6%). ATR model only.', percent: true },
  validation_bars: { label: 'Validation window (bars)', hint: 'Completed 1H candles watched before the 2H validation verdict. 2 = 2 hours.' },
  validation_close_pct: { label: 'Validation close %', hint: 'Favourable close the window must reach (0.35 = +0.35%).', percent: true },
  profit_book_pct: { label: 'Profit booking %', hint: 'A touch of entry ± this books the whole position (0.90 = +0.90%).', percent: true },
  // ---- Fast Test (debug) + Fast Test V1.0 — the editable rules --------------
  // The entry rule's own numbers and the exit rule's RSI level. Defaults are
  // the original hardcoded rule: RSI(14), long below 50, short at/above 50.
  entry_rsi_period: { label: 'RSI period', hint: 'Period of the RSI the debug entry rule reads (14 = the original).' },
  entry_rsi_long_max: { label: 'Long below RSI', hint: 'LONG while RSI is below this value (50 = the original rule).' },
  entry_rsi_short_min: { label: 'Short at/above RSI', hint: 'SHORT while RSI is at or above this value (50 = the original rule).' },
  exit_rsi_level: { label: 'RSI exit level', hint: 'Close a long at/above this RSI, a short at/below it.' },
};

// Fast Test (debug) + Fast Test V1.0 — the protective rules the client can
// switch off, and the optional signal conditions. Everything defaults to the
// shipped behaviour, so an unedited strategy is unchanged.
const DEBUG_EXIT_SWITCHES = [
  ['use_stop_loss', 'Stop loss', 'The hard stop (and the venue-side stop leg). Off = a losing position is not stopped out.'],
  ['use_take_profit', 'Take profit', 'The fixed target (and the venue-side target leg).'],
  ['use_trailing_stop', 'Trailing stop', 'The trail that follows price once it is in profit.'],
  ['use_breakeven', 'Breakeven', 'Ratchet the stop to entry once the trade is in profit.'],
  ['use_timeout', 'Timeout', 'Close after the configured number of candles.'],
];
const DEBUG_EXIT_CONDITIONS = [
  ['exit_on_opposite', 'Exit on opposite signal', 'Close when the entry rule above points the other way.'],
  ['exit_macd_flip_enabled', 'Exit on MACD flip', 'Close when the MACD line crosses its signal against the position.'],
];

// The tool trades the BTC *perpetual* on every venue: Binance lists it as
// BTCUSDT, Delta as BTCUSD. Dated futures are never substituted.
const perpetualFor = (source) => (String(source || '').toLowerCase() === 'delta' ? 'BTCUSD' : 'BTCUSDT');

// Comparison operators the client can choose for the per-side ATR regime rule.
// '>=' (ATR ≥ ratio × 50-bar average) is the original Phantom behaviour and is
// what each side starts from when the Long/Short switch is turned on, so the
// default condition never changes unless the client edits it.
const ATR_REGIME_OPS = [
  { value: '>=', label: '≥ (greater than or equal)', short: 'ATR ≥' },
  { value: '<=', label: '≤ (less than or equal)', short: 'ATR ≤' },
  { value: '>', label: '> (greater than)', short: 'ATR >' },
  { value: '<', label: '< (less than)', short: 'ATR <' },
];
const DEFAULT_ATR_OP = '>=';
const atrOpShort = (op) => (ATR_REGIME_OPS.find(o => o.value === op) || ATR_REGIME_OPS[0]).short;

// Mirrors the backend resolver (PhantomV2Config.atr_regime_rule_for) so a
// restored run can show the exact ATR test each side was filtered with.
const atrRegimeRuleFor = (params, direction) => {
  const side = direction === 1 ? 'long' : 'short';
  const ec = params?.entry_conditions || {};
  const perSide = !!(ec.use_direction_conditions || ec.use_direction_atr_floor);
  const branch = perSide ? (ec[side] || {}) : {};
  const op = branch.atr_regime_op || DEFAULT_ATR_OP;
  const ratio = branch.atr_regime_ratio ?? params?.atr_regime_ratio ?? 0;
  const symbol = { '>=': '≥', '<=': '≤', '>': '>', '<': '<' }[op] || op;
  return `ATR ${symbol} ${ratio} × SMA50(ATR)`;
};

// PASS / FAIL / N/A for one entry condition. N/A means the setup that fired
// never applies that filter (e.g. MACD-hist magnitude on a momentum entry),
// which is different from the trade failing it.
const condLabel = (v) => (v === undefined || v === null ? 'N/A' : (v ? 'PASS' : 'FAIL'));

// Candle timestamps are UTC. Render them as UTC 24h so the log always points at
// the same candle regardless of the viewer's timezone (and matches the export).
const fmtCandleTime = (v, opts = {}) => {
  if (!v) return '';
  // The API returns naive UTC strings ("2020-06-26T13:41:59.523330"). JS parses
  // a datetime with no timezone designator as LOCAL time, so without the "Z" a
  // viewer in Asia/Calcutta would see every candle time shifted by -05:30.
  const s = String(v);
  const d = new Date(/(Z|[+-]\d{2}:?\d{2})$/.test(s) ? s : `${s}Z`);
  if (Number.isNaN(d.getTime())) return '';
  const p = (n) => String(n).padStart(2, '0');
  const base = `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} `
    + `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
  return opts.seconds ? `${base}:${p(d.getUTCSeconds())}` : base;
};

// Build the trade-log spreadsheet: which candle signalled, which candle the
// entry filled on and the colour of each, every entry condition (individual
// columns plus the full readable breakdown), and the exit condition.
// Kept pure (trades in, CSV text out) so it can be asserted in tests.
const buildTradesCSV = (trades) => {
  const header = [
    'Trade #', 'Direction', 'Setup',
    'Signal Candle Time', 'Signal Candle Colour',
    'Entry Candle Time', 'Entry Candle Colour',
    'Entry Price (Basis)', 'Entry Price (Mark)', 'Entry Price (Traded)',
    'Exit Time', 'Exit Candle Colour',
    'Exit Price (Basis)', 'Exit Price (Mark)', 'Exit Price (Traded)',
    'Priced On Mark',
    '4H Trend',
    'RSI14', 'MACD Hist', 'ADX', 'ATR14', 'EMA50 1h', 'EMA50 4h',
    // Every entry condition, one column each, so the sheet can be filtered.
    'Entry Cond 1 - 4H Trend', 'Entry Cond 2 - ADX', 'Entry Cond 3 - MACD Hist',
    'Entry Cond 4 - ATR Regime', 'Entry Cond 5 - RSI Trigger',
    'Entry Cond 6 - MACD Confirm', 'Entry Cond 7 - DI Confirm',
    'All Entry Conditions (detail)',
    // Exit condition: the reason code plus the exact rule that fired.
    'Exit Condition', 'Exit Condition Detail',
    'SL at Entry', 'SL at Exit', 'Take Profit', 'Trail Stop', 'ATR at Entry', 'Peak Price',
    'Lots', 'Margin', 'Notional', 'Margin % Used', 'Drawdown at Entry %',
    'PnL (Gross)', 'Fees', 'Booked PnL (Net)', 'Equity After', 'Drawdown %', 'Bars Held',
    // v3.5 — appended after the original layout so existing sheets keep their
    // column positions: MACD line / signal at the signal candle and the
    // optional MACD line-rule result (N/A for runs that did not use it).
    'MACD Line', 'MACD Signal', 'Entry Cond 8 - MACD Line/Signal',
    // FastTest V1.0 audit — appended last, blank on every other strategy.
    'Validation Status', 'Validation Close', 'Validation Threshold',
    'TP 0.90% Hit', 'Validation Exit', 'Final Exit Reason', 'Final Net P&L',
  ];
  // UTC, to the second, so a row in the sheet matches the on-screen log exactly.
  const fmtTime = (v) => fmtCandleTime(v, { seconds: true });
  const num = (v, d = 2) => (v === null || v === undefined ? '' : Number(v).toFixed(d));
  const rows = (trades || []).map((t, i) => {
    const c = t.conditions || {};
    return [
      i + 1,
      t.direction === 1 ? 'LONG' : 'SHORT',
      t.setup || '',
      fmtTime(t.signal_candle_time || t.entry_time),
      t.signal_candle_type || t.candle_type || '',
      fmtTime(t.entry_candle_time || t.entry_time),
      t.entry_candle_type || '',
      num(t.entry_price), num(t.entry_mark_price), num(t.entry_trade_price),
      fmtTime(t.exit_time),
      t.exit_candle_type || '',
      num(t.exit_price), num(t.exit_mark_price), num(t.exit_trade_price),
      t.mark_price_basis ? 'YES' : 'NO',
      t.trend_4h || '',
      num(t.rsi14, 1), num(t.macd_hist), num(t.adx, 1), num(t.atr14),
      num(t.ema50_1h), num(t.ema50_4h),
      condLabel(c.trend_ok), condLabel(c.adx_ok), condLabel(c.macd_hist_ok),
      condLabel(c.atr_regime_ok), condLabel(c.rsi_ok), condLabel(c.macd_confirm_ok),
      condLabel(c.di_ok),
      (t.entry_conditions_detail || '').replace(/\r?\n/g, ' | '),
      t.exit_reason || '',
      t.exit_detail || '',
      num(t.sl_entry), num(t.sl), num(t.tp), num(t.trail_stop),
      num(t.atr_at_entry), num(t.peak_price),
      num(t.lots, 4), num(t.margin, 0), num(t.notional, 2),
      t.margin_pct_used != null ? `${(t.margin_pct_used * 100).toFixed(1)}%` : '',
      num(t.entry_dd_pct),
      num(t.gross_pnl), num(t.fees), num(t.net_pnl), num(t.equity_after),
      num(t.drawdown), t.hold_bars ?? '',
      num(t.macd_line), num(t.macd_signal), condLabel(c.macd_line_ok),
      t.validation_status || '',
      num(t.validation_close), num(t.validation_threshold),
      t.tp090_hit == null ? '' : (t.tp090_hit ? 'YES' : 'NO'),
      t.validation_exit == null ? '' : (t.validation_exit ? 'YES' : 'NO'),
      t.final_exit_reason || t.exit_reason || '',
      num(t.final_net_pnl ?? t.net_pnl),
    ];
  });
  // CRLF so Excel keeps one row per line; the caller prepends the UTF-8 BOM so
  // the ₹ / ≥ characters survive.
  const esc = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  return [header, ...rows].map(r => r.map(esc).join(',')).join('\r\n');
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

// Trade-log table: which candle signalled, which candle the entry filled on and
// its colour, plus a per-trade breakdown of every entry condition and the exit rule.
// Extracted as a component so the new columns can be rendered and asserted in tests.
const TradeLogTable = ({ trades, params, expandedTrade, onToggleRow }) => (
<div className="overflow-x-auto">
    <table className="w-full text-left text-xs">
      <thead className="bg-gray-900 uppercase text-gray-500">
        <tr>
          <th className="p-3 font-semibold">Signal Candle</th>
          <th className="p-3 font-semibold">Entry Candle</th>
          <th className="p-3 font-semibold">Entry</th>
          <th className="p-3 font-semibold">Exit</th>
          <th className="p-3 font-semibold">Dir</th>
          <th className="p-3 font-semibold">Setup</th>
          <th className="p-3 font-semibold">4H Trend</th>
          <th className="p-3 font-semibold">RSI</th>
          <th className="p-3 font-semibold">ADX</th>
          <th className="p-3 font-semibold" title="Gross PnL before fees">PnL</th>
          <th className="p-3 font-semibold" title="Entry + exit fees charged on this trade">Fees</th>
          <th className="p-3 font-semibold" title="Booked = net PnL (gross PnL − fees) added to equity">Booked</th>
          <th className="p-3 font-semibold">Reason</th>
          <th className="p-3 font-semibold">Cond.</th>
        </tr>
      </thead>
      <tbody>
        {trades?.map((t, i) => (
          <React.Fragment key={i}>
            <tr className="cursor-pointer border-b border-gray-700 transition hover:bg-gray-700/30"
                onClick={() => onToggleRow(expandedTrade === i ? null : i)}>
              <td className="p-3">
                <div className="font-mono text-gray-400">{fmtCandleTime(t.signal_candle_time || t.entry_time) || 'N/A'}</div>
                <CandleChip color={t.signal_candle_type || t.candle_type} label="signal" />
              </td>
              <td className="p-3">
                <div className="font-mono text-gray-400">{fmtCandleTime(t.entry_candle_time || t.entry_time) || 'N/A'}</div>
                <CandleChip color={t.entry_candle_type} label="entry" />
              </td>
              <td className="p-3">{(t.entry_price || 0).toFixed(2)}</td>
              <td className="p-3">
                <div>{(t.exit_price || 0).toFixed(2)}</div>
                <CandleChip color={t.exit_candle_type} label="exit" />
              </td>
              <td className={`p-3 font-bold ${t.direction === 1 ? 'text-green-400' : 'text-red-400'}`}>{t.direction === 1 ? 'L' : 'S'}</td>
              <td className="p-3"><span className={`rounded px-2 py-0.5 text-[10px] font-bold ${t.setup === 'MOMENTUM' ? 'bg-purple-900/40 text-purple-300' : 'bg-blue-900/40 text-blue-300'}`}>{t.setup || '—'}</span></td>
              <td className={`p-3 ${t.trend_4h === 'UP' ? 'text-green-400' : 'text-red-400'}`}>{t.trend_4h || '—'}</td>
              <td className="p-3">{t.rsi14 != null ? t.rsi14.toFixed(1) : '—'}</td>
              <td className="p-3">{t.adx != null ? t.adx.toFixed(1) : '—'}</td>
              <td className={`p-3 font-mono font-bold ${(t.gross_pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>₹{(t.gross_pnl || 0).toFixed(2)}</td>
              <td className="p-3 font-mono text-gray-400">₹{(t.fees || 0).toFixed(2)}</td>
              <td className={`p-3 font-mono font-bold ${(t.net_pnl || 0) > 0 ? 'text-green-400' : 'text-red-400'}`}>₹{(t.net_pnl || 0).toFixed(2)}</td>
              <td className="p-3">
                <span className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-[10px] text-gray-400">{t.exit_reason || 'N/A'}</span>
                {/* FastTest V1.0: the validation verdict for this trade. */}
                {t.validation_status && (
                  <span className={`ml-1 rounded px-1.5 py-0.5 text-[9px] font-bold ${validationChipClass(t.validation_status)}`}
                        title={`Validation ${t.validation_status}${t.validation_close != null ? ` — close ${Number(t.validation_close).toFixed(2)} vs ${Number(t.validation_threshold).toFixed(2)}` : ''}`}>
                    {t.tp090_hit ? '+0.90% HIT' : t.validation_status}
                  </span>
                )}
              </td>
              <td className="p-3 text-gray-500">{expandedTrade === i ? '▼' : '▶'}</td>
            </tr>
            {expandedTrade === i && (
              <tr className="border-b border-gray-700 bg-gray-900/60">
                <td colSpan={14} className="p-4">
                  {/* Every entry condition spelled out: measured value vs the
                      threshold applied, and PASS/FAIL. Built by the engine so it
                      always matches what was actually evaluated. */}
                  {t.entry_conditions_detail && (
                    <div className="mb-3 rounded-lg border border-gray-700 bg-gray-900 p-3">
                      <div className="mb-1.5 text-[9px] font-bold uppercase text-gray-500">
                        Entry Conditions — {t.setup || 'setup'} on the {t.signal_candle_type || t.candle_type || '—'} signal candle,
                        filled on the {t.entry_candle_type || '—'} entry candle
                      </div>
                      <div className="space-y-0.5 font-mono text-[10px]">
                        {String(t.entry_conditions_detail).split('\n').map((line, li) => (
                          <div key={li}
                               className={line.endsWith('PASS') ? 'text-green-400'
                                 : line.endsWith('FAIL') ? 'text-red-400'
                                 : 'text-gray-400'}>
                            {line}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {t.exit_detail && (
                    <div className="mb-3 rounded-lg border border-gray-700 bg-gray-900 p-3">
                      <div className="mb-1.5 text-[9px] font-bold uppercase text-gray-500">
                        Exit Condition — {t.exit_reason || '—'} on the {t.exit_candle_type || '—'} candle
                      </div>
                      <div className="font-mono text-[10px] text-yellow-300">{t.exit_detail}</div>
                    </div>
                  )}
                  {t.validation_status && (
                    <div className="mb-3 rounded-lg border border-gray-700 bg-gray-900 p-3"
                         data-testid="v1-validation-audit">
                      <div className="mb-1.5 text-[9px] font-bold uppercase text-gray-500">
                        FastTest V1.0 — 2H validation &amp; +0.90% booking
                      </div>
                      <div className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-[10px] text-gray-300 md:grid-cols-4">
                        <div>Validation Status: <b>{t.validation_status}</b></div>
                        <div>Validation Close: <b>{t.validation_close != null ? Number(t.validation_close).toFixed(2) : '—'}</b></div>
                        <div>Validation Threshold: <b>{t.validation_threshold != null ? Number(t.validation_threshold).toFixed(2) : '—'}</b></div>
                        <div>TP 0.90% Hit: <b>{t.tp090_hit ? 'YES' : 'NO'}</b></div>
                        <div>Validation Exit: <b>{t.validation_exit ? 'YES' : 'NO'}</b></div>
                        <div>Final Exit Reason: <b>{t.final_exit_reason || t.exit_reason || '—'}</b></div>
                        <div>Final Net P&amp;L: <b className={(t.final_net_pnl ?? t.net_pnl ?? 0) > 0 ? 'text-green-400' : 'text-red-400'}>₹{Number(t.final_net_pnl ?? t.net_pnl ?? 0).toFixed(2)}</b></div>
                      </div>
                    </div>
                  )}
                  <div className="grid grid-cols-2 gap-3 text-[11px] md:grid-cols-4">
                    <div>
                      <div className="mb-1 text-[9px] font-bold uppercase text-gray-500">Condition Flags</div>
                      <div className="flex flex-wrap gap-1">
                        <CondChip ok={t.conditions?.trend_ok} label={`4h trend (${t.trend_4h || '?'})`} />
                        <CondChip ok={t.conditions?.adx_ok} label={`ADX≥min (${t.adx?.toFixed(1) ?? '?'})`} />
                        <CondChip ok={t.conditions?.macd_hist_ok} label="MACD-hist mag" />
                        <CondChip ok={t.conditions?.atr_regime_ok} label="ATR regime" />
                        <CondChip ok={t.conditions?.rsi_ok} label={t.setup === 'MOMENTUM' ? 'RSI agreement' : 'RSI trigger'} />
                        <CondChip ok={t.conditions?.macd_confirm_ok} label={t.setup === 'MOMENTUM' ? 'MACD zero-cross' : 'MACD confirm'} />
                        <CondChip ok={t.conditions?.di_ok} label="DI confirm" />
                        {t.conditions?.macd_line_ok !== undefined && t.conditions?.macd_line_ok !== null && (
                          <CondChip ok={t.conditions?.macd_line_ok} label="MACD line/signal" />
                        )}
                      </div>
                      <div className="mt-1.5 text-[9px] text-gray-500">
                        ATR test: <span className="font-mono text-gray-300">{atrRegimeRuleFor(params, t.direction)}</span>
                        <span className="ml-2 text-gray-600">·  '·' means the setup that fired never applies that filter</span>
                      </div>
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        <CandleChip color={t.signal_candle_type || t.candle_type} label="signal" />
                        <CandleChip color={t.entry_candle_type} label="entry" />
                        <CandleChip color={t.exit_candle_type} label="exit" />
                      </div>
                    </div>
                    <div>
                      <div className="mb-1 text-[9px] font-bold uppercase text-gray-500">Indicators @ Signal</div>
                      <div className="space-y-0.5 font-mono text-gray-300">
                        <div>MACD-hist: {t.macd_hist?.toFixed(2) ?? '—'}</div>
                        {(t.macd_line != null || t.macd_signal != null) && (
                          <div>MACD line / signal: {t.macd_line?.toFixed(2) ?? '—'} / {t.macd_signal?.toFixed(2) ?? '—'}</div>
                        )}
                        <div>ATR14: {t.atr14?.toFixed(2) ?? '—'}</div>
                        <div>EMA50 1h: {t.ema50_1h?.toFixed(2) ?? '—'}</div>
                        <div>EMA50 4h: {t.ema50_4h?.toFixed(2) ?? '—'}</div>
                      </div>
                    </div>
                    <div>
                      <div className="mb-1 text-[9px] font-bold uppercase text-gray-500">Risk Model</div>
                      <div className="space-y-0.5 font-mono text-gray-300">
                        <div>SL: {t.sl?.toFixed(2) ?? '—'}{t.sl_entry != null && Math.abs(t.sl - t.sl_entry) > 0.005 ? ` (entry ${t.sl_entry.toFixed(2)})` : ''}</div>
                        <div>TP: {t.tp?.toFixed(2) ?? '—'}</div>
                        <div>Trail stop: {t.trail_stop?.toFixed(2) ?? '—'} • ATR@entry: {t.atr_at_entry?.toFixed(2) ?? '—'}</div>
                        <div>Margin: ₹{(t.margin || 0).toFixed(0)} ({((t.margin_pct_used || 0) * 100).toFixed(1)}%)</div>
                        <div>Lots: {(t.lots || 0).toFixed(4)} • DD@entry: {(t.entry_dd_pct || 0).toFixed(1)}%</div>
                      </div>
                    </div>
                    <div>
                      <div className="mb-1 text-[9px] font-bold uppercase text-gray-500">Result</div>
                      <div className="space-y-0.5 font-mono text-gray-300">
                        {t.mark_price_basis ? (
                          <div className="text-[10px] text-amber-300">
                            Entry mark {t.entry_mark_price?.toFixed(2) ?? '—'} (traded {t.entry_trade_price?.toFixed(2) ?? '—'}) · Exit mark {t.exit_mark_price?.toFixed(2) ?? '—'} (traded {t.exit_trade_price?.toFixed(2) ?? '—'})
                          </div>
                        ) : (
                          <div className="text-[10px] text-gray-500">Priced on the traded price (mark price off)</div>
                        )}
                        <div>PnL (Gross): ₹{(t.gross_pnl || 0).toFixed(2)} • Fees: ₹{(t.fees || 0).toFixed(2)}</div>
                        <div className={t.net_pnl > 0 ? 'text-green-400' : 'text-red-400'}>Booked (Net): ₹{(t.net_pnl || 0).toFixed(2)}</div>
                        <div>Exit: {fmtCandleTime(t.exit_time) || '—'} ({t.hold_bars || 0} bars)</div>
                        <div>Peak: {t.peak_price?.toFixed(2) ?? '—'}</div>
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
);

// `initialStrategyId` lets a caller (the page-shell tests) open the form on a
// specific strategy — including the two debug families, whose panels are only
// rendered for that selection. The routed page passes nothing.
const Backtest = ({ initialStrategyId = 'PhantomV2' } = {}) => {
  const DEFAULT_PARAMS = {
    trend_ema_period: 50,
    macd_fast: 12, macd_slow: 26, macd_signal: 9,
    rsi_oversold: 40, rsi_overbought: 60, adx_min: 10, macd_hist_min: 5,
    atr_regime_ratio: 0.5, enable_momentum_entry: true, cooldown_bars: 0,
    stop_loss_atr: 1.2, take_profit_atr: 14.0, trail_activation_atr: 0.8,
    trail_distance_atr: 0.3, breakeven_atr: 0.75,
    // v3.6 — ATR units (default: unchanged), price %, or a per-level mix.
    risk_exit: { ...DEFAULT_RISK_EXIT },
    // Tradable defaults: at 100k BTC, 50k INR * 0.25 * 7 /85 = 1029 USD = 0.01 BTC > 0.001 min lot.
    // Previous 20k*0.15*2 gave 0.0007 BTC -> LOT_TOO_SMALL -> 0 trades.
    leverage: 7, margin_pct: 0.25,
    dd_soft_pct: 8.0, dd_halt_pct: 100.0, dd_resume_pct: 100.0,
    // Shared by Phantom and both debug strategies — the shipped defaults, so
    // sending them never changes an existing run.
    timeout_bars: 72, lot_size_btc: 0.001, reduced_margin_pct: 0.125, sl_floor_pct: 0.016,
    // Fast Test V1.0 rules (2H window / +0.35% validation / +0.90% booking).
    // Only the V1 strategy reads them; every other strategy ignores them.
    validation_bars: 2, validation_close_pct: 0.0035, profit_book_pct: 0.009,
    // Fast Test (debug) + V1.0 — the editable ENTRY rule (RSI 14: long below
    // 50, short at/above 50) and EXIT rule (every protective rule on, no
    // signal conditions). Defaults = the original hardcoded behaviour.
    entry_rsi_period: 14, entry_rsi_long_max: 50, entry_rsi_short_min: 50,
    use_stop_loss: true, use_take_profit: true, use_trailing_stop: true,
    use_breakeven: true, use_timeout: true,
    exit_on_opposite: false, exit_rsi_enabled: false, exit_rsi_level: 50,
    exit_macd_flip_enabled: false,
    // BTC perpetual: risk is managed on the exchange MARK price; the traded
    // price is recorded next to it (both are stored for every trade).
    use_mark_price: true,
    // "Skip new trades" schedule (weekend / holiday blackout windows).
    trading_windows: emptySchedule(),
    // v3.5 strategy separation — 'both' / 'both' is the original strategy.
    setup_mode: 'both',
    trade_direction: 'both',
    // v3.5 MACD line / signal line rules — OFF unless the client enables them.
    macd_line_rules: { ...DEFAULT_MACD_LINE_RULES },
    entry_conditions: {
      // The two direction toggles are intentionally independent. The legacy
      // use_direction_conditions flag is still accepted for older saved runs.
      use_direction_conditions: false,
      use_direction_macd_hist: false,
      use_direction_atr_floor: false,
      use_direction_macd_line: false,
      long: { macd_fast: null, macd_slow: null, macd_signal: null, macd_hist_min: null, stop_loss_atr: null, atr_regime_ratio: null, atr_regime_op: null, atr_regime_max: null, rsi_oversold: null, rsi_overbought: null, adx_min: null,
              macd_line_vs_signal: null, macd_line_vs_zero: null, macd_signal_vs_zero: null, macd_line_min: null, macd_signal_min: null },
      short: { macd_fast: null, macd_slow: null, macd_signal: null, macd_hist_min: null, stop_loss_atr: null, atr_regime_ratio: null, atr_regime_op: null, atr_regime_max: null, rsi_oversold: null, rsi_overbought: null, adx_min: null,
               macd_line_vs_signal: null, macd_line_vs_zero: null, macd_signal_vs_zero: null, macd_line_min: null, macd_signal_min: null },
    },
  };
  const [selectedStrategyId, setSelectedStrategyId] = useState(initialStrategyId);
  const [strategies, setStrategies] = useState([]);
  const [params, setParams] = useState({ ...DEFAULT_PARAMS });
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [results, setResults] = useState(null);
  const [restoredRun, setRestoredRun] = useState(false);
  const [loading, setLoading] = useState(false);
  const [dates, setDates] = useState({ start: '2020-07-04', end: '2026-07-04' });
  const [history, setHistory] = useState([]);
  const [showHistory, setShowHistory] = useState(false);
  const [expandedTrade, setExpandedTrade] = useState(null);
  const [runName, setRunName] = useState('');
  const [confirm, setConfirm] = useState(null); // { type, runId, ... }
  const [capital, setCapital] = useState(50000); // tradable default; previous 20k was untradable at 100k BTC with low lev
  const [overlayCandles, setOverlayCandles] = useState([]);
  const [overlaySignals, setOverlaySignals] = useState([]);
  const [overlayLoading, setOverlayLoading] = useState(false);
  const [dataSource, setDataSource] = useState('Delta');
  const [sources, setSources] = useState([{ code: 'Delta', name: 'Delta Exchange' }, { code: 'Binance', name: 'Binance Futures' }]);
  const [fees, setFees] = useState({ taker_fee_bps: 5.9, maker_fee_bps: 2.36 });
  const defaultSectionVisibility = {
    history: true,
    setup: true,
    config: true,
    preview: true,
    summary: true,
    equity: true,
    candles: true,
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
    candles: true,
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

  // All fields are shared by default. The two threshold switches below add
  // only the Long / Short values the client needs to tune independently.
  const sharedParamGroups = {
    "Trend & Regime": ["trend_ema_period", "atr_regime_ratio", "cooldown_bars"],
    "MACD Indicator": ["macd_fast", "macd_slow", "macd_signal"],
    "Entries (v3)": ["rsi_oversold", "rsi_overbought", "adx_min", "macd_hist_min", "enable_momentum_entry"],
    "Risk & Exit Model": ["stop_loss_atr", "take_profit_atr", "trail_activation_atr", "trail_distance_atr", "breakeven_atr"],
    "Sizing & Drawdown Guard": ["leverage", "margin_pct", "dd_soft_pct", "dd_halt_pct", "dd_resume_pct"],
  };
  const useDirection = !!(params.entry_conditions && params.entry_conditions.use_direction_conditions);
  const useDirMacdHist = useDirection || !!(params.entry_conditions && params.entry_conditions.use_direction_macd_hist);
  const useDirAtrFloor = useDirection || !!(params.entry_conditions && params.entry_conditions.use_direction_atr_floor);
  // v3.5 — MACD line / signal rules and the setup / direction selectors.
  const macdLineRules = params.macd_line_rules || DEFAULT_MACD_LINE_RULES;
  const useDirMacdLine = !!(macdLineRules.enabled && params.entry_conditions && params.entry_conditions.use_direction_macd_line);
  // A built-in preset (e.g. PhantomV2:reversal:long) fixes setup / direction;
  // the selectors then mirror the preset and are locked.
  const presetVariant = parsePhantomVariant(selectedStrategyId);
  const presetLocked = !!presetVariant && (presetVariant.setup_mode !== 'both' || presetVariant.trade_direction !== 'both');

  const toggleSection = (key) => setSectionVisibility(prev => ({ ...prev, [key]: !prev[key] }));
  const setSharedField = (field, value) => setParams(prev => ({ ...prev, [field]: value }));
  const setMacdLineRule = (field, value) => setParams(prev => ({
    ...prev,
    macd_line_rules: { ...DEFAULT_MACD_LINE_RULES, ...(prev.macd_line_rules || {}), [field]: value },
  }));
  const setMacdLinePerSide = (value) => setParams(prev => {
    const ec = prev.entry_conditions || {};
    const rules = { ...DEFAULT_MACD_LINE_RULES, ...(prev.macd_line_rules || {}) };
    const seed = (side) => {
      const b = { ...(ec[side] || {}) };
      if (value) {
        // Start each side from the shared rule so switching the toggle on
        // never changes the strategy until the client edits a side.
        for (const spec of MACD_LINE_RULE_KEYS) {
          const k = `macd_${spec.key}`;
          if (!b[k]) b[k] = rules[spec.key] || 'off';
        }
      }
      return b;
    };
    return { ...prev, entry_conditions: { ...ec, use_direction_macd_line: value, long: seed('long'), short: seed('short') } };
  });

  // The client-facing switches are deliberately independent. Enabling one
  // copies the current shared value into each side only when that side does
  // not already have a saved override.
  const setDirectionalToggle = (field, value) => setParams(prev => {
    const ec = prev.entry_conditions || {};
    const long = { ...(ec.long || {}) };
    const short = { ...(ec.short || {}) };
    if (value && field === 'use_direction_macd_hist') {
      long.macd_hist_min = long.macd_hist_min ?? prev.macd_hist_min ?? 0;
      short.macd_hist_min = short.macd_hist_min ?? -(Math.abs(prev.macd_hist_min ?? 0));
    }
    if (value && field === 'use_direction_atr_floor') {
      long.atr_regime_ratio = long.atr_regime_ratio ?? prev.atr_regime_ratio ?? 0;
      short.atr_regime_ratio = short.atr_regime_ratio ?? prev.atr_regime_ratio ?? 0;
      // Seed both sides with the engine's default comparison so switching the
      // toggle on never changes the rule until the client edits it.
      long.atr_regime_op = long.atr_regime_op ?? DEFAULT_ATR_OP;
      short.atr_regime_op = short.atr_regime_op ?? DEFAULT_ATR_OP;
    }
    return {
      ...prev,
      entry_conditions: {
        ...ec,
        [field]: value,
        long,
        short,
      },
    };
  });

  const setTradingWindows = (next) => setParams(prev => ({ ...prev, trading_windows: next }));
  const setUseMarkPrice = (next) => setParams(prev => ({ ...prev, use_mark_price: !!next }));

  const setDirectionalValue = (side, field, value) => setParams(prev => ({
    ...prev,
    entry_conditions: {
      ...(prev.entry_conditions || {}),
      [side]: {
        ...((prev.entry_conditions || {})[side] || {}),
        [field]: value,
      },
    },
  }));

  // Which strategy *family* the form is editing. 'FastTest' / 'FastTestV1' make
  // the panel show the debug strategy's own fields; a saved strategy remembers
  // its family in `rules.strategy_id`, so re-opening it edits the same fields.
  const familyOf = (sid) => {
    if (isFastTestV1(sid)) return FAST_TEST_V1_ID;
    if (String(sid) === FAST_TEST_ID) return FAST_TEST_ID;
    const saved = strategies.find(s => String(s.id) === String(sid));
    const stored = saved && saved.rules && typeof saved.rules === 'object' ? saved.rules.strategy_id : '';
    if (isFastTestV1(stored)) return FAST_TEST_V1_ID;
    if (String(stored) === FAST_TEST_ID) return FAST_TEST_ID;
    return '';
  };
  const strategyFamily = familyOf(selectedStrategyId);
  const fastTestFamily = !!strategyFamily;

  // The parameter form applies to PhantomV2, to both debug strategies (their
  // built-in ids or a saved copy of them) and to saved Kudos-style strategies
  // (params stored as an object, not Chartink rule arrays).
  const showParamForm = fastTestFamily || isPhantomBuiltin(selectedStrategyId) ||
    strategies.some(s => String(s.id) === String(selectedStrategyId) &&
      s.rules && typeof s.rules === 'object' && !Array.isArray(s.rules) &&
      ('entry_conditions' in s.rules || 'rsi_oversold' in s.rules));

  // Percent fields are stored as fractions (0.016) and shown as percents (1.6),
  // exactly like the Risk & Exit model editor.
  const asPercent = (v) => (v === undefined || v === null || v === '' ? '' : Math.round(Number(v) * 1e6) / 1e4);
  const toFraction = (raw) => (raw === '' ? 0 : Number(raw) / 100);

  const renderNumberInput = (field, value, onChange) => {
    const meta = PARAM_META[field] || { label: field.replace(/_/g, ' '), hint: '' };
    return (
      <div className="flex flex-col">
        <label className="text-[10px] text-gray-400 font-semibold mb-1 flex items-center gap-1">
          {meta.label}
          {meta.hint && <span title={meta.hint} className="text-gray-600 hover:text-blue-400 cursor-help"><HelpCircle size={11} /></span>}
        </label>
        <input type="number" step="0.01" value={meta.percent ? asPercent(value) : (value ?? '')}
          onChange={e => (meta.percent
            ? onChange({ target: { value: toFraction(e.target.value) } })
            : onChange(e))}
          data-testid={`param-${field}`}
          className="bg-gray-900 p-2 rounded-lg border border-gray-700 text-white text-xs outline-none focus:border-blue-500 transition w-full" />
        {meta.hint && <span className="text-[10px] text-gray-600 mt-0.5 leading-snug hidden xl:block">{meta.hint}</span>}
      </div>
    );
  };
  const setFamilyField = (field, value) => setParams(prev => ({ ...prev, [field]: value }));

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
        ...(restoredRun ? { setup: true, config: true } : {}),
      }));
      window.requestAnimationFrame(() => {
        resultsSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    }
  }, [results, restoredRun]);

  useEffect(() => {
    if (results && results.equity_curve && sectionVisibility.equity) {
      resultsRef.current = results;
      const timer = setTimeout(() => { initEquityChart(results.equity_curve); }, 100);
      return () => clearTimeout(timer);
    }
  }, [results, sectionVisibility.equity]);

  useEffect(() => {
    if (!results) { setOverlayCandles([]); setOverlaySignals([]); return undefined; }
    const start = results.start_date ? String(results.start_date).slice(0, 10) : null;
    const end = results.end_date ? String(results.end_date).slice(0, 10) : null;
    const source = results.data_source || 'Binance';
    const symbol = perpetualFor(source);
    const q = new URLSearchParams({ symbol, interval: '1h', limit: '50000', source });
    if (start) q.set('start_date', start);
    if (end) q.set('end_date', end);
    setOverlayLoading(true);
    const klinesPromise = fetch(`${API_URL}/klines?${q.toString()}`)
      .then(r => (r.ok ? r.json() : [])).then(d => Array.isArray(d) ? d : []).catch(() => []);
    // Fetch signals for the same params that produced the backtest so chart vs backtest parity is visible.
    const runParams = results.params || params;
    const signalsPromise = fetch(`${API_URL}/phantom/signals/custom`, {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        params: runParams,
        strategy_id: results.strategy_id || selectedStrategyId,
        start_date: start,
        end_date: end,
        symbol,
        data_source: source,
        fee_mode: 'backtest',
        initial_capital: results.initial_capital || capital,
        use_mark_price: !!runParams.use_mark_price,
        trading_windows: runParams.trading_windows,
      }),
    }).then(r => (r.ok ? r.json() : [])).then(d => Array.isArray(d) ? d : []).catch(() => []);
    Promise.all([klinesPromise, signalsPromise]).then(([candles, signals]) => {
      setOverlayCandles(candles);
      setOverlaySignals(signals);
    }).finally(() => setOverlayLoading(false));
    return undefined;
  }, [results]);

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
    const csv = buildTradesCSV(results.trades);
    const blob = new Blob(['\uFEFF', csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `kudos_trades_run_${results.name || 'export'}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const runBacktest = async () => {
    setLoading(true);
    try {
      const strategyName = runName.trim() || (selectedStrategyId === 'PhantomV2' ? 'Kudos Optimization'
        : (builtinStrategyName(selectedStrategyId) || `Custom Run ${selectedStrategyId}`));
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
          // Sent both inside `params` (saved with the run) and top level so the
          // API can apply them even if a strategy payload is rebuilt.
          use_mark_price: !!params.use_mark_price,
          trading_windows: params.trading_windows,
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

          setRestoredRun(false);
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
    const name = (runName.trim() || `Kudos ${new Date().toLocaleString()}`).slice(0, 60);
    setSaving(true);
    try {
      const res = await fetch(`${API_URL}/strategies/create`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, params, strategy_id: strategyFamily || undefined }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to save strategy");
      alert(`Strategy "${name}" saved successfully${fastTestFamily ? ` as a ${strategyFamily === FAST_TEST_V1_ID ? FAST_TEST_V1_NAME : FAST_TEST_NAME} configuration` : ''}. `
        + 'You can now run it from the strategy dropdown, or Paper / Live trade it from the Trading page.');
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
          initial_capital: parseFloat(capital),
          use_mark_price: !!params.use_mark_price,
          trading_windows: params.trading_windows,
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

  const mergeSavedParams = (savedParams) => {
    const saved = savedParams && typeof savedParams === 'object' ? savedParams : {};
    const base = JSON.parse(JSON.stringify(DEFAULT_PARAMS));
    const savedConditions = saved.entry_conditions && typeof saved.entry_conditions === 'object'
      ? saved.entry_conditions : {};
    const merged = {
      ...base,
      ...saved,
      // Restored from the saved run when present, otherwise the form default.
      trading_windows: normalizeSchedule(saved.trading_windows ?? base.trading_windows),
      // v3.5 fields: runs saved before them simply keep the inert defaults.
      setup_mode: saved.setup_mode || base.setup_mode,
      trade_direction: saved.trade_direction || base.trade_direction,
      macd_line_rules: { ...base.macd_line_rules, ...((saved.macd_line_rules && typeof saved.macd_line_rules === 'object') ? saved.macd_line_rules : {}) },
      // v3.6: runs saved before the Risk & Exit model stay all-ATR.
      risk_exit: { ...base.risk_exit, ...((saved.risk_exit && typeof saved.risk_exit === 'object') ? saved.risk_exit : {}) },
      entry_conditions: {
        ...base.entry_conditions,
        ...savedConditions,
        long: { ...base.entry_conditions.long, ...(savedConditions.long || {}) },
        short: { ...base.entry_conditions.short, ...(savedConditions.short || {}) },
      },
    };
    // `strategy_id` is the saved strategy's *family* marker, not a parameter.
    delete merged.strategy_id;
    // Runs saved before the independent switches were introduced used the
    // legacy master switch. Surface that state in the new controls as well.
    if (savedConditions.use_direction_conditions) {
      if (savedConditions.use_direction_macd_hist === undefined) merged.entry_conditions.use_direction_macd_hist = true;
      if (savedConditions.use_direction_atr_floor === undefined) merged.entry_conditions.use_direction_atr_floor = true;
    }
    return merged;
  };

  const dateInputValue = value => value ? String(value).slice(0, 10) : null;

  const applyRunParameters = (runDetails) => {
    const savedParams = runDetails?.params && typeof runDetails.params === 'object'
      ? runDetails.params : {};
    if (Object.keys(savedParams).length > 0) setParams(mergeSavedParams(savedParams));
    if (runDetails?.strategy_id) setSelectedStrategyId(String(runDetails.strategy_id));
    const start = dateInputValue(runDetails?.start_date);
    const end = dateInputValue(runDetails?.end_date);
    if (start && end) setDates({ start, end });
    if (runDetails?.initial_capital != null) setCapital(runDetails.initial_capital);
    if (runDetails?.data_source) setDataSource(runDetails.data_source);
    if (runDetails?.name) setRunName(runDetails.name);
  };

  const handleStrategySelect = (sid) => {
    setSelectedStrategyId(sid);
    // Built-in preset (Kudos — Reversal only, Long only, …): mirror its
    // setup / direction in the form. The plain default resets both to 'both'
    // so switching back never leaves a preset's restriction behind.
    const variant = parsePhantomVariant(sid);
    if (variant) {
      setParams(prev => ({ ...prev, setup_mode: variant.setup_mode, trade_direction: variant.trade_direction }));
      return;
    }
    // When a saved Kudos-style strategy is chosen, load its params into the
    // form so the admin can tweak it before re-running.
    const found = strategies.find(s => String(s.id) === String(sid));
    if (found && found.rules && typeof found.rules === 'object' && !Array.isArray(found.rules) &&
        (found.rules.entry_conditions || 'rsi_oversold' in found.rules)) {
      setParams(mergeSavedParams(found.rules));
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
      // A historical run is both a result and a reusable configuration. Load
      // its exact parameter snapshot before showing the result so rerunning it
      // cannot accidentally use values from a different run.
      applyRunParameters(data.run_details);
      setRestoredRun(true);
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
    rejections: results.rejected_reasons || {},
    // BTC perpetual + skip-window facts this run was executed with.
    useMarkPrice: results.use_mark_price !== 0 && results.use_mark_price !== false,
    contract: results.contract,
    blockedEntries: Number(results.blocked_entries || 0),
    windows: results.params?.trading_windows || null,
  } : null;

  const pieData = stats?.exitDist ? Object.entries(stats.exitDist).map(([name, value]) => ({ name, value })) : [];
  const COLORS = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6'];

  // Custom tooltip for the Exit Distribution pie chart — Recharts default
  // renders item text in black which is invisible on a dark background.
  const PieTooltip = ({ active, payload }) => {
    if (!active || !payload || !payload.length) return null;
    const { name, value } = payload[0].payload;
    const color = payload[0].payload.fill || payload[0].color || '#fff';
    const total = pieData.reduce((s, d) => s + d.value, 0);
    const pct = total ? ((value / total) * 100).toFixed(1) : '0.0';
    return (
      <div style={{
        backgroundColor: '#1f2937',
        border: '1px solid #374151',
        borderRadius: 8,
        padding: '8px 12px',
        boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
        minWidth: 140,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
          <span style={{ width: 10, height: 10, borderRadius: '50%', backgroundColor: color, display: 'inline-block', flexShrink: 0 }} />
          <span style={{ color: '#e5e7eb', fontWeight: 700, fontSize: 13 }}>{name}</span>
        </div>
        <div style={{ color: '#ffffff', fontSize: 13, paddingLeft: 16 }}>
          <span style={{ fontWeight: 700 }}>{value}</span>
          <span style={{ color: '#9ca3af', marginLeft: 4 }}>trades</span>
          <span style={{ color: '#6b7280', marginLeft: 6 }}>({pct}%)</span>
        </div>
      </div>
    );
  };

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
          <p className="text-sm text-gray-500">Strategy optimizer — finished runs plot LONG/SHORT on market candles.</p>
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
              <div key={run.id} onClick={() => loadRun(run.id)} className="group relative cursor-pointer rounded-xl border border-gray-700 bg-gray-900 p-4 transition hover:border-blue-500">
                <div>
                  <div className="font-bold text-gray-200 transition group-hover:text-blue-400">{run.name || 'Unnamed Run'}</div>
                  <div className="text-xs text-gray-500">{run.start_date?.split('T')[0]} → {run.end_date?.split('T')[0]} · {run.data_source || 'Delta'}</div>
                  <div className={`mt-2 text-sm font-mono ${(run.roi || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>ROI: {(run.roi || 0).toFixed(2)}%</div>
                  <a href={`/chart?run=${run.id}`} onClick={(e) => e.stopPropagation()}
                     className="mt-2 inline-flex items-center gap-1 text-[11px] font-semibold text-sky-400 hover:text-sky-300">
                    <LineChart size={12} /> View on market chart
                  </a>
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
              <option value="PhantomV2">Kudos V2.5 (Default)</option>
              <PhantomPresetOptions />
              <option value={FAST_TEST_ID}>{FAST_TEST_NAME} (debug — configurable)</option>
              <option value={FAST_TEST_V1_ID}>{FAST_TEST_V1_NAME} (Validation + 0.90% TP)</option>
              {strategies.length > 0 && (
                <optgroup label="Saved strategies">
                  {strategies.map(s => (
                    <option key={s.id} value={s.id}>
                      {s.name}{isFastTestV1(s.strategy_id) ? ` · ${FAST_TEST_V1_NAME}`
                        : String(s.strategy_id) === FAST_TEST_ID ? ` · ${FAST_TEST_NAME}` : ''}
                    </option>
                  ))}
                </optgroup>
              )}
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

        {/* BTC perpetual pricing + "skip new trades" schedule for this run. */}
        <div className="mt-4 grid grid-cols-1 gap-4 border-t border-gray-700 pt-4 xl:grid-cols-3">
          <div className="flex flex-col">
            <label className="mb-1 flex items-center gap-1 text-[10px] font-bold uppercase text-gray-500">
              <Target size={10} /> Contract
            </label>
            <div className="flex min-h-[38px] items-center rounded-lg border border-gray-700 bg-gray-900 px-3 py-2">
              <span className="font-mono text-xs text-white">{perpetualFor(dataSource)}</span>
              <span className="ml-2 text-[10px] text-gray-500">perpetual</span>
            </div>
            <label className="mt-2 flex cursor-pointer items-start gap-2 rounded-lg border border-gray-700 bg-gray-900/80 p-2 text-[10px] text-gray-300">
              <input type="checkbox" checked={!!params.use_mark_price}
                     onChange={e => setUseMarkPrice(e.target.checked)}
                     className="mt-0.5 h-3.5 w-3.5 accent-blue-500" />
              <span>
                <span className="block font-bold text-white">Use mark price</span>
                <span className="mt-0.5 block text-gray-500">
                  Stops, targets, trailing and PnL run on the exchange mark price.
                  The traded price is stored on every trade as well.
                </span>
              </span>
            </label>
          </div>
          <div className="xl:col-span-2">
            <TradingWindowsEditor
              value={params.trading_windows}
              onChange={setTradingWindows}
              title="Trading windows — skip new trades"
              subtitle={`Only NEW entries are blocked during these windows (${dataSource} candle time). Trades already open keep running.`}
            />
          </div>
        </div>
      </SectionCard>

      {showParamForm && (
        <SectionCard
          title={fastTestFamily ? `${strategyFamily === FAST_TEST_V1_ID ? FAST_TEST_V1_NAME : FAST_TEST_NAME} — Configuration` : 'Strategy Configuration'}
          subtitle={fastTestFamily
            ? 'Every value the backend uses for this strategy. Save the form as a strategy to reuse it in Paper / Live.'
            : 'Tune Kudos parameters, then hide this section when you want more room for results.'}
          icon={SlidersHorizontal}
          collapsed={!sectionVisibility.config}
          onToggle={() => toggleSection('config')}
          className="mb-8"
        >
          {fastTestFamily ? (
            <div className="mb-5 rounded-xl border border-amber-900/40 bg-amber-900/10 p-3 text-xs text-gray-400" data-testid="fast-test-config-note">
              <b className="text-white">Entry rule</b> and <b className="text-white">Exit rule</b> below are yours to change —
              the shipped values are the original rule ({strategyFamily === FAST_TEST_V1_ID ? `${FAST_TEST_V1_NAME}: RSI 14 — long below 50, short at/above 50, plus the 2H validation and +0.90% booking` : `${FAST_TEST_NAME}: RSI 14 — long below 50, short at/above 50`}),
              so an unedited strategy behaves exactly as before.
              Everything else is the config the backend reads for this strategy — the same stop / target / trailing / sizing plan as Kudos, with your own values.
              Press <b className="text-white">Save strategy</b> to reuse them in Backtest, Paper and Live.
            </div>
          ) : (
          <div className="mb-5 rounded-xl border border-blue-900/40 bg-blue-900/10 p-3 text-xs text-gray-400">
            Set the shared strategy values below. Use the switches under <b className="text-white">MACD hist min</b> or
            <b className="text-white"> Min ATR floor</b> only when Long and Short need different thresholds — the ATR switch
            also lets each side pick its own comparison (<b className="text-white">&gt;, &lt;, ≥, ≤</b>) against the 50-bar ATR average.
          </div>
          )}

          {/* v3.5 — strategy separation: which setup and which side may trade. */}
          {!fastTestFamily && (
          <div className="mb-6 rounded-xl border border-gray-700 bg-gray-900/60 p-4" data-testid="strategy-separation">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-xs font-bold uppercase tracking-wider text-blue-400">Strategy separation</h3>
              {presetLocked ? (
                <span className="rounded border border-amber-800/60 bg-amber-900/20 px-2 py-0.5 text-[10px] text-amber-300">
                  Fixed by the selected preset ({builtinStrategyName(selectedStrategyId)}). Pick <b>Kudos V2.5 (Default)</b> to change.
                </span>
              ) : (
                <span className="text-[10px] text-gray-500">Both = the original strategy. Save the form as a strategy to reuse a split in Paper / Live.</span>
              )}
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {[['setup_mode', SETUP_MODES], ['trade_direction', TRADE_DIRECTIONS]].map(([field, options]) => {
                const meta = PARAM_META[field];
                const current = options.find(o => o.value === (params[field] || 'both')) || options[0];
                return (
                  <div key={field} className="flex flex-col">
                    <label className="mb-1 flex items-center gap-1 text-[10px] font-semibold text-gray-400">
                      {meta.label}
                      <span title={meta.hint} className="cursor-help text-gray-600 hover:text-blue-400"><HelpCircle size={11} /></span>
                    </label>
                    <select value={params[field] || 'both'} disabled={presetLocked}
                      onChange={e => setSharedField(field, e.target.value)}
                      className="w-full rounded-lg border border-gray-700 bg-gray-900 p-2 text-xs text-white outline-none transition focus:border-blue-500 disabled:opacity-60">
                      {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                    <span className="mt-1 text-[10px] leading-snug text-gray-500">{current.hint}</span>
                  </div>
                );
              })}
            </div>
            {(params.setup_mode === 'momentum' && params.enable_momentum_entry === false) && (
              <p className="mt-2 text-[10px] text-amber-300">Momentum only overrides the unticked "Momentum entries" box — Setup B fires regardless.</p>
            )}
          </div>
          )}

          {fastTestFamily ? (
            <div className="space-y-6" data-testid="fast-test-config">
              <div className="grid grid-cols-1 gap-6 border-t border-gray-700 pt-6 sm:grid-cols-2 xl:grid-cols-3">
                <div className="space-y-3">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-blue-400">Risk &amp; Exit Model</h3>
                  <RiskExitModelEditor params={params} setParams={setParams} />
                  <div className="pt-1">
                    {renderNumberInput('sl_floor_pct', params.sl_floor_pct,
                      e => setFamilyField('sl_floor_pct', parseFloat(e.target.value)))}
                  </div>
                </div>
                <div className="space-y-3">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-blue-400">Exits &amp; Timing</h3>
                  <div className="space-y-3">
                    {['timeout_bars', 'cooldown_bars'].map(field => <React.Fragment key={field}>
                      {renderNumberInput(field, params[field], e => setFamilyField(field, parseFloat(e.target.value)))}
                    </React.Fragment>)}
                  </div>
                </div>
                <div className="space-y-3">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-blue-400">Sizing &amp; Drawdown Guard</h3>
                  <div className="space-y-3">
                    {['leverage', 'margin_pct', 'lot_size_btc', 'reduced_margin_pct', 'dd_soft_pct', 'dd_halt_pct', 'dd_resume_pct']
                      .map(field => <React.Fragment key={field}>
                        {renderNumberInput(field, params[field], e => setFamilyField(field, parseFloat(e.target.value)))}
                      </React.Fragment>)}
                  </div>
                </div>
              </div>
              <div className="rounded-xl border border-blue-900/40 bg-blue-900/10 p-4" data-testid="fast-test-entry-rule">
                <h3 className="text-xs font-bold uppercase tracking-wider text-blue-300">Entry rule</h3>
                <p className="mt-1 text-[10px] leading-snug text-gray-400">
                  One side per 1H candle, decided on that candle's RSI. Change the period, either threshold or the allowed
                  direction and the same rule runs with your numbers.
                  <span className="ml-1 font-mono text-gray-300">
                    Now: RSI({params.entry_rsi_period}) → long below {params.entry_rsi_long_max}, short at/above {params.entry_rsi_short_min}
                    {params.trade_direction === 'long' ? ' · long only' : params.trade_direction === 'short' ? ' · short only' : ''}.
                  </span>
                </p>
                <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  {['entry_rsi_period', 'entry_rsi_long_max', 'entry_rsi_short_min'].map(field => (
                    <React.Fragment key={field}>
                      {renderNumberInput(field, params[field], e => setFamilyField(field, parseFloat(e.target.value)))}
                    </React.Fragment>
                  ))}
                  <div className="flex flex-col">
                    <label className="text-[10px] text-gray-400 font-semibold mb-1">Direction</label>
                    <select value={params.trade_direction || 'both'} data-testid="param-trade_direction"
                      onChange={e => setFamilyField('trade_direction', e.target.value)}
                      className="bg-gray-900 p-2 rounded-lg border border-gray-700 text-white text-xs outline-none focus:border-blue-500 transition w-full">
                      <option value="both">Both — long &amp; short</option>
                      <option value="long">Long only</option>
                      <option value="short">Short only</option>
                    </select>
                  </div>
                </div>
              </div>

              <div className="rounded-xl border border-gray-700 bg-gray-900/60 p-4" data-testid="fast-test-exit-rule">
                <h3 className="text-xs font-bold uppercase tracking-wider text-blue-400">Exit rule</h3>
                <p className="mt-1 text-[10px] leading-snug text-gray-400">
                  Which protective rules this strategy runs, and the optional conditions that close a position on a
                  completed candle. Everything is on / off exactly as the original strategy behaved — a switch only
                  changes a run once you save this strategy. The stop still keeps priority over a condition, and a
                  condition over the timeout.
                </p>
                <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {DEBUG_EXIT_SWITCHES.map(([field, label, hint]) => (
                    <label key={field} className="flex cursor-pointer items-start gap-2 rounded-lg border border-gray-700 bg-gray-900/80 p-2 text-[10px] text-gray-300">
                      <input type="checkbox" checked={params[field] !== false} data-testid={`toggle-${field}`}
                        onChange={e => setFamilyField(field, e.target.checked)}
                        className="mt-0.5 h-3.5 w-3.5 accent-blue-500" />
                      <span>
                        <span className="block font-bold text-white">{label}</span>
                        <span className="mt-0.5 block leading-snug text-gray-500">{hint}</span>
                      </span>
                    </label>
                  ))}
                </div>
                <h4 className="mt-4 text-[10px] font-bold uppercase tracking-wider text-gray-400">Exit conditions (on a completed candle)</h4>
                <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {DEBUG_EXIT_CONDITIONS.map(([field, label, hint]) => (
                    <label key={field} className="flex cursor-pointer items-start gap-2 rounded-lg border border-gray-700 bg-gray-900/80 p-2 text-[10px] text-gray-300">
                      <input type="checkbox" checked={!!params[field]} data-testid={`toggle-${field}`}
                        onChange={e => setFamilyField(field, e.target.checked)}
                        className="mt-0.5 h-3.5 w-3.5 accent-blue-500" />
                      <span>
                        <span className="block font-bold text-white">{label}</span>
                        <span className="mt-0.5 block leading-snug text-gray-500">{hint}</span>
                      </span>
                    </label>
                  ))}
                  <div className="rounded-lg border border-gray-700 bg-gray-900/80 p-2">
                    <label className="flex cursor-pointer items-start gap-2 text-[10px] text-gray-300">
                      <input type="checkbox" checked={!!params.exit_rsi_enabled} data-testid="toggle-exit_rsi_enabled"
                        onChange={e => setFamilyField('exit_rsi_enabled', e.target.checked)}
                        className="mt-0.5 h-3.5 w-3.5 accent-blue-500" />
                      <span>
                        <span className="block font-bold text-white">Exit on RSI level</span>
                        <span className="mt-0.5 block leading-snug text-gray-500">Close a long at/above the level, a short at/below it.</span>
                      </span>
                    </label>
                    {params.exit_rsi_enabled && (
                      <div className="mt-2">
                        {renderNumberInput('exit_rsi_level', params.exit_rsi_level, e => setFamilyField('exit_rsi_level', parseFloat(e.target.value)))}
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {strategyFamily === FAST_TEST_V1_ID && (
                <div className="rounded-xl border border-emerald-900/40 bg-emerald-900/10 p-4" data-testid="fast-test-v1-rules">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-emerald-300">V1.0 — validation &amp; profit booking</h3>
                  <p className="mt-1 text-[10px] leading-snug text-gray-400">
                    The window is measured in completed 1H candles. A favourable close inside the window validates the trade and
                    the normal exits continue; a close that never gets there exits at the window's last close.
                    The booking percentage is checked on a price <b className="text-white">touch</b> and books the whole position first.
                  </p>
                  <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
                    {['validation_bars', 'validation_close_pct', 'profit_book_pct'].map(field => <React.Fragment key={field}>
                      {renderNumberInput(field, params[field], e => setFamilyField(field, parseFloat(e.target.value)))}
                    </React.Fragment>)}
                  </div>
                </div>
              )}
            </div>
          ) : (
          <div className="grid grid-cols-1 gap-6 border-t border-gray-700 pt-6 sm:grid-cols-2 xl:grid-cols-4">
            {Object.entries(sharedParamGroups).map(([groupName, fields]) => (
              <div key={groupName} className="space-y-3">
                <h3 className="text-xs font-bold uppercase tracking-wider text-blue-400">{groupName}</h3>
                <div className="space-y-3">
                  {groupName === 'Risk & Exit Model' ? (
                    /* The five ATR / % values are edited by their own block:
                       a model selector plus an ATR | Price % switch per level. */
                    <RiskExitModelEditor params={params} setParams={setParams} />
                  ) : fields.map(field => {
                    if (field === 'enable_momentum_entry') {
                      return <React.Fragment key={field}>
                        {renderCheckInput(field, !!params[field], e => setSharedField(field, e.target.checked))}
                      </React.Fragment>;
                    }
                    if (field === 'macd_hist_min' || field === 'atr_regime_ratio') {
                      const isHist = field === 'macd_hist_min';
                      const enabled = isHist ? useDirMacdHist : useDirAtrFloor;
                      const toggleKey = isHist ? 'use_direction_macd_hist' : 'use_direction_atr_floor';
                      const label = isHist ? 'Use separate Long / Short MACD hist' : 'Use separate Long / Short Min ATR floor';
                      return <div key={field} className="space-y-2">
                        {renderNumberInput(field, params[field], e => setSharedField(field, parseFloat(e.target.value)))}
                        <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-gray-700 bg-gray-900/80 p-2 text-[10px] text-gray-300">
                          <input type="checkbox" checked={enabled}
                            onChange={e => setDirectionalToggle(toggleKey, e.target.checked)}
                            className="mt-0.5 h-3.5 w-3.5 accent-blue-500" />
                          <span>
                            <span className="block font-bold text-white">{label}</span>
                            <span className="mt-0.5 block text-gray-500">
                              {isHist
                                ? 'Long uses hist ≥ value; Short uses hist ≤ value.'
                                : 'Pick the comparison (>, <, ≥, ≤) and value for each side. Both start on the default ATR ≥ rule.'}
                            </span>
                          </span>
                        </label>
                        {enabled && (
                          <div className="space-y-2 rounded-lg border border-gray-700 bg-gray-900 p-2">
                            <div className="grid grid-cols-2 gap-2">
                              {['long', 'short'].map(side => {
                                const sideValue = params.entry_conditions?.[side]?.[field];
                                const sideOp = params.entry_conditions?.[side]?.atr_regime_op ?? DEFAULT_ATR_OP;
                                return <div key={side}>
                                  <label className={`mb-1 block text-[9px] font-bold uppercase ${side === 'long' ? 'text-green-400' : 'text-red-400'}`}>
                                    {side} · {isHist ? (side === 'long' ? 'hist ≥' : 'hist ≤') : atrOpShort(sideOp)}
                                  </label>
                                  {isHist ? (
                                    <input type="number" step="0.01" value={sideValue ?? ''}
                                      onChange={e => setDirectionalValue(side, field, e.target.value === '' ? null : parseFloat(e.target.value))}
                                      className="w-full rounded border border-gray-700 bg-gray-800 p-1.5 text-xs text-white outline-none focus:border-blue-500" />
                                  ) : (
                                    <div className="flex gap-1">
                                      <select value={sideOp}
                                        onChange={e => setDirectionalValue(side, 'atr_regime_op', e.target.value)}
                                        title="How ATR is compared with its 50-bar average"
                                        className="w-16 shrink-0 rounded border border-gray-700 bg-gray-800 p-1.5 text-xs font-mono text-white outline-none focus:border-blue-500">
                                        {ATR_REGIME_OPS.map(o => <option key={o.value} value={o.value}>{o.value}</option>)}
                                      </select>
                                      <input type="number" step="0.01" value={sideValue ?? ''}
                                        onChange={e => setDirectionalValue(side, field, e.target.value === '' ? null : parseFloat(e.target.value))}
                                        className="w-full rounded border border-gray-700 bg-gray-800 p-1.5 text-xs text-white outline-none focus:border-blue-500" />
                                    </div>
                                  )}
                                </div>;
                              })}
                            </div>
                            {!isHist && (
                              <p className="text-[9px] leading-snug text-gray-500">
                                Applied as <span className="font-mono text-gray-300">ATR {atrOpShort(params.entry_conditions?.long?.atr_regime_op ?? DEFAULT_ATR_OP).replace('ATR ', '')} {params.entry_conditions?.long?.atr_regime_ratio ?? params[field] ?? 0} × SMA50(ATR)</span> for
                                longs and <span className="font-mono text-gray-300">ATR {atrOpShort(params.entry_conditions?.short?.atr_regime_op ?? DEFAULT_ATR_OP).replace('ATR ', '')} {params.entry_conditions?.short?.atr_regime_ratio ?? params[field] ?? 0} × SMA50(ATR)</span> for
                                shorts. Use <span className="font-mono text-gray-300">&lt;</span> to trade only when volatility is calm.
                              </p>
                            )}
                          </div>
                        )}
                      </div>;
                    }
                    return <React.Fragment key={field}>
                      {renderNumberInput(field, params[field], e => setSharedField(field, parseFloat(e.target.value)))}
                    </React.Fragment>;
                  })}
                </div>
              </div>
            ))}
          </div>
          )}

          {/* v3.5 — optional MACD line / signal line entry rules. */}
          {!fastTestFamily && (
          <div className="mt-6 rounded-xl border border-gray-700 bg-gray-900/60 p-4" data-testid="macd-line-rules">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="text-xs font-bold uppercase tracking-wider text-blue-400">MACD line / signal line rules</h3>
                <p className="mt-1 max-w-2xl text-[10px] leading-snug text-gray-500">
                  Extra entry filters on the MACD <b className="text-gray-300">line</b> and its <b className="text-gray-300">signal line</b>
                  (periods {params.macd_fast}/{params.macd_slow}/{params.macd_signal} from the MACD Indicator group). Off by default — the
                  histogram threshold and the existing MACD confirmation / zero-cross checks are unchanged. Every rule is read on the
                  bullish side for longs and the bearish side for shorts, and applies to both Reversal and Momentum entries.
                </p>
              </div>
              <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-xs text-gray-200">
                <input type="checkbox" checked={!!macdLineRules.enabled}
                  onChange={e => setMacdLineRule('enabled', e.target.checked)} className="h-3.5 w-3.5 accent-blue-500" />
                Enable MACD line rules
              </label>
            </div>
            {macdLineRules.enabled && (
              <div className="mt-4 space-y-4">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                  {MACD_LINE_RULE_KEYS.map(spec => (
                    <div key={spec.key} className="flex flex-col">
                      <label className="mb-1 text-[10px] font-semibold text-gray-400">{spec.label}</label>
                      <select value={macdLineRules[spec.key] || 'off'}
                        onChange={e => setMacdLineRule(spec.key, e.target.value)}
                        className="w-full rounded-lg border border-gray-700 bg-gray-900 p-2 text-xs text-white outline-none focus:border-blue-500">
                        {MACD_LINE_RULES.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
                      </select>
                    </div>
                  ))}
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  {renderNumberInput('macd_line_min', macdLineRules.line_min,
                    e => setMacdLineRule('line_min', e.target.value === '' ? null : parseFloat(e.target.value)))}
                  {renderNumberInput('macd_signal_min', macdLineRules.signal_min,
                    e => setMacdLineRule('signal_min', e.target.value === '' ? null : parseFloat(e.target.value)))}
                </div>
                <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-gray-700 bg-gray-900/80 p-2 text-[10px] text-gray-300">
                  <input type="checkbox" checked={useDirMacdLine}
                    onChange={e => setMacdLinePerSide(e.target.checked)} className="mt-0.5 h-3.5 w-3.5 accent-blue-500" />
                  <span>
                    <span className="block font-bold text-white">Use separate Long / Short MACD line rules</span>
                    <span className="mt-0.5 block text-gray-500">Each side starts from the shared rules above; levels entered per side are used signed as typed (short levels are normally negative).</span>
                  </span>
                </label>
                {useDirMacdLine && (
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                    {['long', 'short'].map(side => {
                      const branch = params.entry_conditions?.[side] || {};
                      return (
                        <div key={side} className="space-y-2 rounded-lg border border-gray-700 bg-gray-900 p-3">
                          <div className={`text-[9px] font-bold uppercase ${side === 'long' ? 'text-green-400' : 'text-red-400'}`}>{side}</div>
                          {MACD_LINE_RULE_KEYS.map(spec => (
                            <div key={spec.key} className="flex items-center justify-between gap-2">
                              <span className="text-[10px] text-gray-400">{spec.label}</span>
                              <select value={branch[`macd_${spec.key}`] || macdLineRules[spec.key] || 'off'}
                                onChange={e => setDirectionalValue(side, `macd_${spec.key}`, e.target.value)}
                                className="w-44 rounded border border-gray-700 bg-gray-800 p-1.5 text-xs text-white outline-none focus:border-blue-500">
                                {MACD_LINE_RULES.map(r => <option key={r.value} value={r.value}>{r.value === 'above_below' ? (side === 'long' ? 'Above' : 'Below') : r.label}</option>)}
                              </select>
                            </div>
                          ))}
                          <div className="grid grid-cols-2 gap-2">
                            {[['macd_line_min', side === 'long' ? 'MACD line ≥' : 'MACD line ≤'], ['macd_signal_min', side === 'long' ? 'Signal line ≥' : 'Signal line ≤']].map(([field, label]) => (
                              <div key={field}>
                                <label className="mb-1 block text-[9px] font-bold uppercase text-gray-500">{label}</label>
                                <input type="number" step="0.01" value={branch[field] ?? ''} placeholder="off"
                                  onChange={e => setDirectionalValue(side, field, e.target.value === '' ? null : parseFloat(e.target.value))}
                                  className="w-full rounded border border-gray-700 bg-gray-800 p-1.5 text-xs text-white outline-none focus:border-blue-500" />
                              </div>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
                <p className="text-[10px] leading-snug text-gray-500">
                  Applied as <span className="font-mono text-green-300">Long: {macdLineRuleText(params, 1)}</span>
                  <span className="mx-2 text-gray-600">|</span>
                  <span className="font-mono text-red-300">Short: {macdLineRuleText(params, -1)}</span>
                </p>
              </div>
            )}
          </div>
          )}

          {useDirection && !fastTestFamily && (
            <div className="mt-5 rounded-lg border border-yellow-900/50 bg-yellow-900/10 p-3 text-[10px] text-yellow-300">
              This run contains the legacy full Long / Short override switch. Its additional RSI, ADX, MACD-period,
              stop-loss and max-ATR overrides are still honoured by the engine; the two switches above control the
              editable MACD histogram and minimum ATR floor values.
            </div>
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
            {fastTestFamily
              ? `${strategyFamily === FAST_TEST_V1_ID ? FAST_TEST_V1_NAME : FAST_TEST_NAME} runs the entry / exit rules above — Run Backtest builds the full equity curve and trade log.`
              : 'Preview Filters is a fast quality check. Run Backtest builds the full equity curve and trade log.'}
          </p>
          <div className="flex flex-col flex-wrap gap-2 sm:flex-row sm:items-center">
            <button onClick={resetParams} className="flex items-center justify-center gap-2 px-4 py-2 text-xs text-gray-500 transition hover:text-white">
              <RotateCcw size={14} /> Reset defaults
            </button>
            {!fastTestFamily && (
              <button onClick={runFilterPreview} disabled={previewLoading || loading}
                className="flex items-center justify-center gap-2 rounded-xl border border-blue-800/50 px-4 py-2 text-xs font-semibold text-blue-300 transition hover:bg-blue-900/20 disabled:opacity-50">
                {previewLoading ? <div className="h-3 w-3 rounded-full border-2 border-blue-400 border-t-transparent animate-spin"></div> : 'Preview Filters'}
              </button>
            )}
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
          subtitle={`${preview.total_trades} trades · WR ${preview.total_win_rate}% · PF ${preview.total_profit_factor} · cap ₹${preview.initial_capital_used || capital}`}
          icon={Activity}
          collapsed={!sectionVisibility.preview}
          onToggle={() => toggleSection('preview')}
          actions={
            <>
              {(preview.use_direction_conditions || preview.use_direction_macd_hist || preview.use_direction_atr_floor) && (
                <span className="rounded border border-purple-800/40 bg-purple-900/40 px-2 py-0.5 text-[10px] text-purple-300">
                  {preview.use_direction_conditions ? 'direction-specific ON' : 'side thresholds ON'}
                </span>
              )}
              {preview.setup_mode && preview.setup_mode !== 'both' && (
                <span className="rounded border border-blue-800/40 bg-blue-900/40 px-2 py-0.5 text-[10px] text-blue-300">{preview.setup_label || preview.setup_mode}</span>
              )}
              {preview.trade_direction && preview.trade_direction !== 'both' && (
                <span className="rounded border border-blue-800/40 bg-blue-900/40 px-2 py-0.5 text-[10px] text-blue-300">{preview.direction_label || preview.trade_direction}</span>
              )}
              {preview.risk_exit?.model && preview.risk_exit.model !== 'atr' && (
                <span className="rounded border border-amber-800/40 bg-amber-900/40 px-2 py-0.5 text-[10px] text-amber-300"
                      title={riskExitText(params)}>
                  {preview.risk_exit.model_label || 'Price-based risk'}
                </span>
              )}
              {preview.macd_line_rules?.enabled && (
                <span className="rounded border border-emerald-800/40 bg-emerald-900/40 px-2 py-0.5 text-[10px] text-emerald-300"
                      title={`Long: ${preview.macd_line_rules.long} | Short: ${preview.macd_line_rules.short}`}>
                  MACD line rules ON
                </span>
              )}
              <button onClick={() => setPreview(null)} className="text-xs text-gray-500 transition hover:text-white">Close</button>
            </>
          }
          className="mb-8 border-blue-800/40"
        >
          <p className="mb-4 text-xs text-gray-500">
            Buckets reflect the conditions currently set in the configuration form. Uses same data source as chart (DB → remote fallback).
          </p>
          {preview.rejected_reasons && Object.keys(preview.rejected_reasons).length > 0 && (
            <div className="mb-4 rounded-xl border border-red-900/50 bg-red-900/20 p-3">
              <div className="mb-2 text-[10px] font-bold uppercase text-red-300">Rejected / Filtered — why trades = 0?</div>
              <div className="flex flex-wrap gap-2">
                {Object.entries(preview.rejected_reasons).map(([reason, count]) => (
                  <span key={reason} className={`rounded border px-2 py-1 font-mono text-[10px] ${reason==='LOT_TOO_SMALL' ? 'border-red-700 bg-red-900/40 text-red-200' : 'border-gray-700 bg-gray-900 text-gray-300'}`}>
                    {reason}: {count}
                  </span>
                ))}
              </div>
              {preview.rejected_reasons.LOT_TOO_SMALL > 0 && (
                <div className="mt-2 text-[11px] text-red-200">
                  LOT_TOO_SMALL means notional below 0.001 BTC min lot. Fix: increase capital to 50000-100000, leverage to 7, margin to 0.25. At 100k BTC price, 20k*0.15*2/85=70 USD =0.0007 BTC &lt;0.001.
                </div>
              )}
              {preview.blocked_entries > 0 && <div className="mt-1 text-[10px] text-amber-300">Blocked by trading windows: {preview.blocked_entries}</div>}
            </div>
          )}
          {preview.diagnostics && (
            <div className="mb-4 rounded border border-gray-700 bg-gray-900 p-2 text-[10px] font-mono text-gray-400">
              signals {preview.diagnostics.total_signals} → {preview.diagnostics.signals_in_range} in range · cooldown {preview.diagnostics.cooldown_skipped} · window {preview.diagnostics.window_blocked} · trades {preview.diagnostics.trades_entered}
            </div>
          )}
          {preview.atr_regime_rules && (
            <div className="mb-4 flex flex-wrap gap-2 text-[10px]">
              {['long', 'short'].map(side => (
                <span key={side}
                  className={`rounded border px-2 py-1 font-mono ${side === 'long' ? 'border-green-800/40 bg-green-900/10 text-green-300' : 'border-red-800/40 bg-red-900/10 text-red-300'}`}>
                  {side.toUpperCase()}: {preview.atr_regime_rules[side]}
                </span>
              ))}
            </div>
          )}
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
                <a href={`/chart?run=${results.id}`}
                   className="flex items-center gap-2 rounded-lg border border-sky-800/60 bg-sky-900/30 px-4 py-2 text-xs font-bold text-sky-300 transition hover:bg-sky-900/50">
                  <LineChart size={14} /> View on market chart
                </a>
                <button onClick={exportTradesCSV} title="Opens directly in Excel — includes every entry condition, the exit condition and the candle colours"
                        className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-xs font-bold transition hover:bg-blue-500">
                  <Download size={14} /> Excel / CSV Export
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
            {/* What the run was priced on, and how many entries the schedule refused. */}
            <div className="mt-4 flex flex-wrap items-center gap-2 rounded-xl border border-gray-700 bg-gray-900 px-3 py-2 text-[11px]">
              <span className="rounded border border-gray-700 bg-gray-800 px-2 py-0.5 font-mono text-gray-200">
                {stats?.contract || `${perpetualFor(results?.data_source)} perpetual`}
              </span>
              <span className={`rounded border px-2 py-0.5 font-semibold ${
                stats?.useMarkPrice ? 'border-amber-700/60 bg-amber-900/20 text-amber-300'
                                    : 'border-gray-700 bg-gray-800 text-gray-400'}`}>
                {stats?.useMarkPrice ? 'Priced on MARK price' : 'Priced on traded price'}
              </span>
              {isScheduleActive(stats?.windows) ? (
                <span className="flex items-center gap-1 rounded border border-amber-700/60 bg-amber-900/20 px-2 py-0.5 text-amber-300">
                  <PauseCircle size={11} />
                  Skip-windows ON: {describeSchedule(stats?.windows).join(' · ')}
                </span>
              ) : (
                <span className="rounded border border-gray-700 bg-gray-800 px-2 py-0.5 text-gray-400">
                  No trading windows
                </span>
              )}
              {stats?.blockedEntries > 0 && (
                <span className="rounded border border-red-900/60 bg-red-900/20 px-2 py-0.5 font-semibold text-red-300">
                  {stats.blockedEntries} new trades skipped
                </span>
              )}
              {/* v3.5 — which setups / sides this run traded and the MACD line rules it applied. */}
              {(() => {
                const runParams = results?.params && typeof results.params === 'object' ? results.params : {};
                const variant = parsePhantomVariant(results?.strategy_id);
                const setup = variant && variant.setup_mode !== 'both' ? variant.setup_mode : (runParams.setup_mode || 'both');
                const dir = variant && variant.trade_direction !== 'both' ? variant.trade_direction : (runParams.trade_direction || 'both');
                const setupLabel = (SETUP_MODES.find(m => m.value === setup) || SETUP_MODES[0]).label;
                const dirLabel = (TRADE_DIRECTIONS.find(d => d.value === dir) || TRADE_DIRECTIONS[0]).label;
                const lineOn = !!runParams.macd_line_rules?.enabled && (macdLineRuleText(runParams, 1) !== 'off' || macdLineRuleText(runParams, -1) !== 'off');
                return (
                  <>
                    {(setup !== 'both' || dir !== 'both') && (
                      <span className="rounded border border-blue-800/60 bg-blue-900/20 px-2 py-0.5 font-semibold text-blue-300">
                        {setupLabel} · {dirLabel}
                      </span>
                    )}
                    {lineOn && (
                      <span className="rounded border border-emerald-800/60 bg-emerald-900/20 px-2 py-0.5 font-semibold text-emerald-300"
                            title={`Long: ${macdLineRuleText(runParams, 1)} | Short: ${macdLineRuleText(runParams, -1)}`}>
                        MACD line rules ON
                      </span>
                    )}
                  </>
                );
              })()}
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
            title="Market candles"
            subtitle="Every LONG / SHORT on the 1h candle that signalled it, plus IN fills and OUT exits. Open the Market Chart for a full-size view."
            icon={LineChart}
            collapsed={!sectionVisibility.candles}
            onToggle={() => toggleSection('candles')}
            actions={
              <a href={`/chart?run=${results.id}`}
                 className="inline-flex items-center gap-1 rounded-lg border border-sky-800/60 bg-sky-900/20 px-3 py-1.5 text-[11px] font-semibold text-sky-300 hover:text-white">
                Open on Market Chart
              </a>
            }
          >
            <div className="mb-2 flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-gray-400">
              <span className="flex items-center gap-1"><span className="text-green-500">▲</span> LONG</span>
              <span className="flex items-center gap-1"><span className="text-red-500">▼</span> SHORT</span>
              <span>REV = reversal · MOM = momentum</span>
              <span className="flex items-center gap-1"><span className="text-sky-400">●</span> IN fill</span>
              <span className="flex items-center gap-1"><span className="text-amber-400">■</span> OUT exit</span>
              {overlayLoading && <span className="animate-pulse text-gray-500">Loading candles…</span>}
              {!overlayLoading && <span className="text-gray-600">{overlayCandles.length} candles · {overlaySignals.length} raw signals · {results.trades?.length||0} trades</span>}
            </div>
            {overlayLoading && !overlayCandles.length ? (
              <div className="flex h-48 items-center justify-center text-sm text-gray-500">Loading market candles…</div>
            ) : (
              <MarketOverlayChart candles={overlayCandles} trades={results.trades || []} signals={overlaySignals} height={420} />
            )}
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
                        {pieData.map((entry, index) => {
                          const fill = COLORS[index % COLORS.length];
                          entry.fill = fill;   // expose fill to PieTooltip payload
                          return <Cell key={`cell-${index}`} fill={fill} />;
                        })}
                      </Pie>
                      <Tooltip content={<PieTooltip />} />
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
            subtitle="Which candle signalled, which candle the entry filled on and its colour. Expand a row for every entry condition and the exact exit rule."
            icon={Activity}
            collapsed={!sectionVisibility.trades}
            onToggle={() => toggleSection('trades')}
            actions={
              <button onClick={exportTradesCSV} title="Opens directly in Excel — includes every entry condition, the exit condition and the candle colours"
                      className="rounded bg-blue-600 px-3 py-1 text-[10px] font-bold transition hover:bg-blue-500">
                ⬇ Excel / CSV Export
              </button>
            }
            className="overflow-hidden"
          >
            {/* FastTest V1.0 runs: how the validation layer actually behaved. */}
            {fastTestV1Summary(results.trades) && (
              <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-[10px]"
                   data-testid="fasttest-v1-summary">
                <span className="font-bold uppercase tracking-wide text-gray-400">FastTest V1.0</span>
                <span className="rounded bg-gray-800 px-1.5 py-0.5 text-gray-300">trades {fastTestV1Summary(results.trades).trades}</span>
                <span className="rounded bg-green-900/40 px-1.5 py-0.5 text-green-300">validated {fastTestV1Summary(results.trades).validated}</span>
                <span className="rounded bg-emerald-900/40 px-1.5 py-0.5 text-emerald-200">+0.90% booked {fastTestV1Summary(results.trades).booked}</span>
                <span className="rounded bg-red-900/40 px-1.5 py-0.5 text-red-300">validation exits {fastTestV1Summary(results.trades).failed}</span>
                <span className="rounded bg-gray-800 px-1.5 py-0.5 text-gray-400">closed before 2H {fastTestV1Summary(results.trades).notReached}</span>
              </div>
            )}
            <TradeLogTable trades={results.trades} params={results.params}
                expandedTrade={expandedTrade} onToggleRow={setExpandedTrade} />
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

// Colour of a single candle (GREEN / RED / DOJI). The `label` says which
// candle it belongs to — signal, entry or exit — because the entry fills on the
// candle after the signal and the two colours are often different.
const CandleChip = ({ color, label }) => {
  if (!color) return null;
  const cls = color === 'GREEN'
    ? 'bg-green-900/30 text-green-400 border-green-800/40'
    : color === 'RED'
      ? 'bg-red-900/30 text-red-400 border-red-800/40'
      : 'bg-gray-800 text-gray-500 border-gray-700';
  return (
    <span className={`mt-1 inline-block rounded border px-1.5 py-0.5 text-[9px] font-bold uppercase ${cls}`}
          title={`${label} candle colour: ${color}`}>
      {color === 'GREEN' ? '▲' : color === 'RED' ? '▼' : '●'} {color}
    </span>
  );
};

const CondChip = ({ ok, label }) => (
  <span className={`px-2 py-0.5 rounded text-[10px] font-semibold border ${
    ok ? 'bg-green-900/30 text-green-400 border-green-800/40'
       : ok === false || ok === 0
         ? 'bg-red-900/30 text-red-400 border-red-800/40'
         : 'bg-gray-800 text-gray-500 border-gray-700'}`}>
    {ok ? '✓' : (ok === false || ok === 0 ? '✗' : '·')} {label}
  </span>
);

// Colour of the FastTest V1.0 validation chip on the trade log.
const validationChipClass = (status) => {
  switch (String(status || '').toUpperCase()) {
    case 'VALIDATED': return 'bg-green-900/50 text-green-300 border border-green-800';
    case 'FAILED': return 'bg-red-900/50 text-red-300 border border-red-800';
    case 'TP_090_HIT': return 'bg-emerald-900/50 text-emerald-200 border border-emerald-800';
    case 'NOT_REACHED': return 'bg-gray-800 text-gray-400 border border-gray-700';
    default: return 'bg-gray-800 text-gray-400 border border-gray-700';
  }
};

// Run-level V1 counters for the header strip (0/undefined -> not a V1 run).
const fastTestV1Summary = (trades) => {
  const rows = (trades || []).filter(t => t && t.validation_status);
  if (!rows.length) return null;
  return {
    trades: rows.length,
    validated: rows.filter(t => t.validation_status === 'VALIDATED').length,
    failed: rows.filter(t => t.validation_exit).length,
    booked: rows.filter(t => t.tp090_hit).length,
    notReached: rows.filter(t => t.validation_status === 'NOT_REACHED').length,
  };
};

// Exported for tests: the pure helpers and the trade-log table behind the
// Backtest page's trade log and Excel/CSV export.
export { buildTradesCSV, condLabel, fmtCandleTime, atrRegimeRuleFor,
         TradeLogTable, CandleChip, CondChip, validationChipClass, fastTestV1Summary };

export default Backtest;
