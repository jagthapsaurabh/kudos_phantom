import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Play, StopCircle, Activity, AlertCircle, TrendingUp, Wallet, Terminal, XCircle, PlusCircle, Target, Trash2, History, Download, RefreshCw, Eye, ChevronDown, ChevronUp, Clock, CalendarClock } from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { API_URL } from '../api';
import TradingWindowsEditor from '../components/TradingWindowsEditor';
import EntryGuardBadges from '../components/EntryGuardBadges';
import { FeedBadge } from './LiveTrade';
import {
  emptySchedule, normalizeSchedule, isScheduleActive, describeSchedule,
} from '../utils/tradingWindows';

// Format an ISO timestamp (already IST-encoded by the backend, or naive UTC)
// explicitly in India Standard Time (UTC+5:30). Guarantees the user always
// sees India time regardless of the browser's own timezone.
const fmtIST = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const ist = new Date(d.getTime() + (5.5 * 60 * 60 * 1000));
  const p = (x) => String(x).padStart(2, '0');
  return `${p(ist.getUTCHours())}:${p(ist.getUTCMinutes())}:${p(ist.getUTCSeconds())} ${p(ist.getUTCDate())}/${p(ist.getUTCMonth() + 1)}/${ist.getUTCFullYear()} IST`;
};

// Saved history timestamps come from the database as naive UTC (no offset),
// so the browser would otherwise read them as local time. Tag them with Z
// first, then reuse the IST renderer above.
const fmtUTC = (iso) => {
  if (!iso) return '—';
  const s = String(iso);
  return fmtIST(/(Z|[+-]\d{2}:?\d{2})$/.test(s) ? s : `${s}Z`);
};

const inr = (v, digits = 0) => (
  v === null || v === undefined || Number.isNaN(Number(v))
    ? '—'
    : `₹${Number(v).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
);

const SESSION_STATUS = {
  running: { label: 'Running', cls: 'text-green-400 border-green-800 bg-green-900/20' },
  stopped: { label: 'Stopped', cls: 'text-gray-300 border-gray-700 bg-gray-900' },
  interrupted: { label: 'Interrupted', cls: 'text-yellow-400 border-yellow-800 bg-yellow-900/20' },
};
const statusMeta = (s) => SESSION_STATUS[s] || { label: s || '—', cls: 'text-gray-400 border-gray-700 bg-gray-900' };

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

// ---------- Trade Card ----------
const StopBar = ({ trade }) => {
  const sl = trade.sl, tp = trade.tp, stopLevel = trade.stop_level, trailActive = !!trade.trail_active;
  const slMoved = trade.sl_entry != null && sl != null && Math.abs(sl - trade.sl_entry) > 0.005;
  return (
    <div className="mt-3">
      <div className="text-[10px] text-gray-500 uppercase font-bold mb-1.5 flex items-center gap-1">
        <Target size={11} /> Stop / Exit Plan
      </div>
      <div className="grid grid-cols-3 gap-2 text-center text-[10px] uppercase font-medium text-gray-400">
        <div className={`p-2 rounded border ${slMoved ? 'border-yellow-500/40 bg-yellow-500/5' : 'bg-gray-800/50'}`}>
          Stop Loss<br />
          <span className="text-xs text-red-300 font-mono">{sl != null ? Number(sl).toFixed(2) : '—'}</span>
          {slMoved && <div className="text-[9px] text-yellow-400 mt-0.5">was {Number(trade.sl_entry).toFixed(2)} · breakeven</div>}
        </div>
        <div className="bg-gray-800/50 p-2 rounded">Take Profit<br /><span className="text-xs text-green-300 font-mono">{tp != null ? Number(tp).toFixed(2) : '—'}</span></div>
        <div className={`p-2 rounded border ${trailActive ? 'border-purple-500/40 bg-purple-500/5' : 'bg-gray-800/50'}`}>
          {trailActive ? 'Trail Stop (active)' : 'Trailing (armed at)'}<br />
          <span className="text-xs text-purple-300 font-mono">{trailActive ? (stopLevel != null ? Number(stopLevel).toFixed(2) : '—') : (trade.trail_activation != null ? Number(trade.trail_activation).toFixed(2) : '—')}</span>
        </div>
        <div className="bg-gray-800/50 p-2 rounded">Active Stop<br /><span className="text-xs text-white font-mono">{stopLevel != null ? Number(stopLevel).toFixed(2) : '—'}</span></div>
        <div className="bg-gray-800/50 p-2 rounded">ATR @ Entry<br /><span className="text-white text-xs font-mono">{trade.atr_at_entry != null ? Number(trade.atr_at_entry).toFixed(2) : '—'}</span></div>
        <div className="bg-gray-800/50 p-2 rounded">Peak Price<br /><span className="text-white text-xs font-mono">{trade.peak_price != null ? Number(trade.peak_price).toFixed(2) : '—'}</span></div>
      </div>
    </div>
  );
};

const TradeCard = ({ trade, onClose }) => (
  <div className="p-4 rounded-xl border border-blue-500/30 bg-blue-500/5 transition hover:scale-[1.01]">
    <div className="flex justify-between items-start mb-3">
      <div>
        <div className="text-xs text-gray-500 uppercase font-bold">{trade.symbol}</div>
        <div className={`text-lg font-bold ${trade.direction === 1 ? 'text-green-400' : 'text-red-400'}`}>
          {trade.direction === 1 ? 'LONG' : 'SHORT'}
        </div>
      </div>
      <div className="text-right">
        <div className="text-xs text-gray-500 uppercase font-bold">Unrealised PnL</div>
        <div className={`text-lg font-mono font-bold ${trade.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
          {trade.pnl >= 0 ? '+' : ''}{Number(trade.pnl).toFixed(2)}
        </div>
      </div>
    </div>
    <div className="grid grid-cols-3 gap-2 text-center text-[10px] uppercase font-medium text-gray-400">
      <div className="bg-gray-800/50 p-2 rounded">Entry<br /><span className="text-white text-xs">{Number(trade.entry).toFixed(2)}</span></div>
      <div className="bg-gray-800/50 p-2 rounded">Current<br /><span className="text-white text-xs">{Number(trade.current).toFixed(2)}</span>
        {trade.mark_price_basis && trade.mark != null && (
          <div className="text-[9px] text-amber-400/90">mark {Number(trade.mark).toFixed(2)}</div>
        )}
      </div>
      <div className="bg-gray-800/50 p-2 rounded">Chg<br /><span className={`text-xs ${trade.chg_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>{trade.chg_pct >= 0 ? '+' : ''}{Number(trade.chg_pct).toFixed(2)}%</span></div>
      <div className="bg-gray-800/50 p-2 rounded">Margin<br /><span className="text-white text-xs">₹{Number(trade.margin).toFixed(0)}</span></div>
      <div className="bg-gray-800/50 p-2 rounded">Leverage<br /><span className="text-white text-xs">{trade.leverage ?? '—'}×</span></div>
      <div className="bg-gray-800/50 p-2 rounded">Notional<br /><span className="text-white text-xs">${Number(trade.notional_usd || 0).toFixed(0)}</span></div>
      <div className="bg-gray-800/50 p-2 rounded">Lots<br /><span className="text-white text-xs">{Number(trade.lots || 0).toFixed(4)} BTC</span></div>
      <div className="bg-gray-800/50 p-2 rounded">Bars Held<br /><span className="text-white text-xs">{trade.bars_held ?? 0}</span></div>
      <div className="bg-gray-800/50 p-2 rounded">Entry (IST)<br /><span className="text-white text-xs">{fmtIST(trade.entry_time)}</span></div>
    </div>
    <StopBar trade={trade} />
    {onClose && (
      <button onClick={onClose} className="mt-3 w-full text-xs text-red-400 hover:text-red-300 flex items-center justify-center gap-1 py-1 rounded border border-red-900/40 hover:bg-red-900/20 transition">
        <XCircle size={12} /> Close Position
      </button>
    )}
  </div>
);

// ---------- Closed Trades Panel ----------
const EXIT_REASONS = {
  TP: { label: 'Take Profit', color: 'text-green-400 border-green-800 bg-green-900/20' },
  TSL: { label: 'Trailing Stop', color: 'text-purple-400 border-purple-800 bg-purple-900/20' },
  SL: { label: 'Stop Loss', color: 'text-red-400 border-red-800 bg-red-900/20' },
  MH: { label: 'Max Hold Time', color: 'text-yellow-400 border-yellow-800 bg-yellow-900/20' },
  REV: { label: 'Reversal', color: 'text-blue-400 border-blue-800 bg-blue-900/20' },
};
const reasonMeta = (r) => EXIT_REASONS[r] || { label: r || '—', color: 'text-gray-400 border-gray-700 bg-gray-900' };

const ClosedTradesPanel = ({ closedTrades }) => {
  if (!closedTrades || closedTrades.length === 0) {
    return (
      <div className="bg-gray-800/50 p-6 rounded-2xl border border-dashed border-gray-700 text-center text-sm text-gray-600">
        No closed trades yet. Closed trades will appear here as the simulator fills & exits positions.
      </div>
    );
  }
  return (
    <div className="bg-gray-800 p-4 rounded-2xl border border-gray-700">
      <h4 className="text-xs font-bold text-gray-400 uppercase mb-3">Trade Reply / Closed Trades ({closedTrades.length})</h4>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="bg-gray-900 text-gray-500 uppercase">
            <tr>
              <th className="p-2">Dir</th>
              <th className="p-2">Entry</th>
              <th className="p-2">Exit</th>
              <th className="p-2" title="Exchange mark price the trade was priced on">Mark (entry → exit)</th>
              <th className="p-2">Stop Loss</th>
              <th className="p-2">Take Profit</th>
              <th className="p-2">Trail Stop</th>
              <th className="p-2">ATR</th>
              <th className="p-2">Exit Condition</th>
              <th className="p-2" title="Gross PnL before fees">PnL</th>
              <th className="p-2" title="Entry + exit fees charged on this trade">Fees</th>
              <th className="p-2" title="Booked = net PnL (gross PnL − fees) credited to equity">Booked</th>
              <th className="p-2">Entry (IST)</th>
              <th className="p-2">Exit (IST)</th>
              <th className="p-2">Held</th>
            </tr>
          </thead>
          <tbody>
            {closedTrades.map((t, i) => {
              const meta = reasonMeta(t.reason);
              const slMoved = t.sl != null && t.sl_final != null && Math.abs(t.sl_final - t.sl) > 0.005;
              return (
                <tr key={i} className="border-b border-gray-700/60 align-top">
                  <td className={`p-2 font-bold ${t.direction === 1 ? 'text-green-400' : 'text-red-400'}`}>{t.direction === 1 ? 'LONG' : 'SHORT'}</td>
                  <td className="p-2 font-mono text-gray-300">{t.entry != null ? Number(t.entry).toFixed(2) : '—'}</td>
                  <td className="p-2 font-mono text-gray-300">{t.exit != null ? Number(t.exit).toFixed(2) : '—'}</td>
                  <td className="p-2 font-mono text-gray-400">
                    {t.entry_mark_price != null || t.exit_mark_price != null
                      ? <span className={t.mark_price_basis ? 'text-amber-300' : 'text-gray-400'}>
                          {t.entry_mark_price != null ? Number(t.entry_mark_price).toFixed(2) : '—'}
                          {' → '}
                          {t.exit_mark_price != null ? Number(t.exit_mark_price).toFixed(2) : '—'}
                        </span>
                      : '—'}
                  </td>
                  <td className="p-2 font-mono text-red-300">{t.sl != null ? Number(t.sl).toFixed(2) : '—'}{slMoved && <span className="text-[9px] text-yellow-400 ml-1">→ {Number(t.sl_final).toFixed(2)} (BE)</span>}</td>
                  <td className="p-2 font-mono text-green-300">{t.tp != null ? Number(t.tp).toFixed(2) : '—'}</td>
                  <td className="p-2 font-mono text-purple-300">{t.trail_stop != null ? Number(t.trail_stop).toFixed(2) : '—'}</td>
                  <td className="p-2 font-mono text-gray-400">{t.atr_at_entry != null ? Number(t.atr_at_entry).toFixed(2) : '—'}</td>
                  <td className="p-2 max-w-[260px]">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold border ${meta.color}`}>{meta.label}</span>
                    {t.exit_detail && <div className="text-[10px] text-gray-500 mt-1 leading-snug">{t.exit_detail}</div>}
                  </td>
                  <td className={`p-2 font-mono font-bold ${(t.gross_pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>{(t.gross_pnl || 0) >= 0 ? '+' : ''}{Number(t.gross_pnl || 0).toFixed(2)}</td>
                  <td className="p-2"><span className="bg-gray-900 px-2 py-0.5 rounded text-[10px] text-gray-400 border border-gray-700">{Number(t.fees || 0).toFixed(2)}</span></td>
                  <td className={`p-2 font-mono font-bold ${(t.pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>{(t.pnl || 0) >= 0 ? '+' : ''}{Number(t.pnl || 0).toFixed(2)}</td>
                  <td className="p-2 text-gray-400 text-[10px]">{fmtIST(t.entry_time)}</td>
                  <td className="p-2 text-gray-400 text-[10px]">{fmtIST(t.exit_time)}</td>
                  <td className="p-2 text-gray-400">{t.bars_held || 0} bars</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

// ---------- Log Panel ----------
const LogPanel = ({ instanceKey }) => {
  const [logs, setLogs] = useState([]);
  const scrollRef = useRef(null);
  const [autoScroll, setAutoScroll] = useState(true);

  useEffect(() => {
    if (!instanceKey) return;
    let alive = true;
    const fetchLogs = async () => {
      try {
        const res = await fetch(`${API_URL}/paper-trade/logs?instance_key=${encodeURIComponent(instanceKey)}`, {
          headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` }
        });
        if (res.ok && alive) {
          const data = await res.json();
          setLogs(data.logs || []);
        }
      } catch (e) {}
    };
    fetchLogs();
    const interval = setInterval(fetchLogs, 2000);
    return () => { alive = false; clearInterval(interval); };
  }, [instanceKey]);

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  const levelColor = (level) => {
    if (level === 'error') return 'text-red-400';
    if (level === 'warn') return 'text-yellow-400';
    if (level === 'trade') return 'text-green-400';
    return 'text-gray-400';
  };

  return (
    <div className="bg-gray-900 rounded-2xl border border-gray-700 flex flex-col h-full">
      <div className="flex items-center justify-between p-3 border-b border-gray-700">
        <div className="flex items-center gap-2 text-sm font-bold text-gray-300">
          <Terminal size={16} className="text-blue-400" /> Live Logs
        </div>
        <label className="flex items-center gap-1 text-[10px] text-gray-500 cursor-pointer">
          <input type="checkbox" checked={autoScroll} onChange={e => setAutoScroll(e.target.checked)} className="accent-blue-500" />
          Auto-scroll
        </label>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 font-mono text-[11px] space-y-0.5 max-h-[600px]">
        {logs.length === 0 && <div className="text-gray-600 italic text-center py-8">Waiting for logs…</div>}
        {logs.map((l, i) => (
          <div key={i} className="flex gap-2">
            <span className="text-gray-600 shrink-0">{l.ts ? fmtIST(l.ts).split(' ')[0] + ' ' + fmtIST(l.ts).split(' ')[1] : ''}</span>
            <span className={`shrink-0 font-bold ${levelColor(l.level)}`}>{l.level.toUpperCase().padEnd(5)}</span>
            <span className="text-gray-300 break-all">{l.msg}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

// ---------- Instance Card ----------
const InstanceCard = ({ inst, position, onStop, onDelete, onSelect, selected }) => {
  const activeTrades = inst.active_trades || [];
  const lastChecked = fmtIST(inst.last_checked);
  const strategyName = inst.strategy_name || (inst.strategy_id === 'PhantomV2' ? 'Kudos V2.5 (Default)' : inst.strategy_id);
  return (
    <div onClick={() => onSelect(inst.instance_key)}
         className={`min-w-0 rounded-xl border p-4 cursor-pointer transition ${selected ? 'border-blue-500 bg-blue-900/20 shadow-lg shadow-blue-950/20' : 'border-gray-700 bg-gray-800 hover:border-gray-600'}`}>
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <div className="mb-1 flex items-center gap-2 text-[9px] font-bold uppercase tracking-wider text-gray-500">
            <span>Session {position}</span>
            <span className={`h-1.5 w-1.5 rounded-full ${inst.is_running ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`}></span>
            <span className={inst.is_running ? 'text-green-400' : 'text-red-400'}>{inst.is_running ? 'Running' : 'Stopped'}</span>
          </div>
          <div className="truncate font-bold text-sm text-gray-100" title={strategyName}>{strategyName}</div>
          <div className="mt-1 truncate text-[10px] text-blue-300">{inst.data_source || 'Binance'} · {inst.taker_fee_bps ?? '—'}/{inst.maker_fee_bps ?? '—'} bps</div>
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <span className={`rounded border px-1.5 py-0.5 text-[9px] font-bold ${
              inst.mark_price_basis ? 'border-amber-700/60 bg-amber-900/20 text-amber-300'
                                    : 'border-gray-700 bg-gray-900 text-gray-400'}`}
                  title={inst.mark_price_basis
                    ? 'Stops, targets and PnL run on the exchange mark price of the BTC perpetual'
                    : 'Priced on the traded price (mark price off)'}>
              {inst.mark_price_basis ? 'MARK' : 'TRADE'}
            </span>
            {inst.entry_paused && (
              <span className="rounded border border-amber-700/60 bg-amber-900/20 px-1.5 py-0.5 text-[9px] font-bold text-amber-300"
                    title="New entries are paused by your trading windows; open positions keep running.">
                ⏸ ENTRIES PAUSED
              </span>
            )}
            {isScheduleActive(inst.trading_windows) && !inst.entry_paused && (
              <span className="max-w-[150px] truncate rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 text-[9px] text-gray-400"
                    title={describeSchedule(inst.trading_windows).join(' · ')}>
                ⏱ {describeSchedule(inst.trading_windows).join(' · ')}
              </span>
            )}
            <EntryGuardBadges
              blocked={inst.blocked_entries || 0}
              held={inst.skipped_entries || 0}
              reason={inst.last_skip_reason}
            />
            <FeedBadge feed={inst.price_feed} />
          </div>
        </div>
        <button onClick={(e) => { e.stopPropagation(); onDelete(inst.instance_key, strategyName); }}
                className="shrink-0 rounded-lg p-1.5 text-gray-500 transition hover:bg-red-900/30 hover:text-red-300"
                title="Delete paper trade session" aria-label={`Delete ${strategyName}`}>
          <Trash2 size={15} />
        </button>
      </div>
      <div className="mb-1 text-xl font-mono text-yellow-400">₹{(inst.equity_inr || 0).toLocaleString(undefined, {minimumFractionDigits: 2})}</div>
      <div className="grid grid-cols-3 gap-2 text-center text-[10px] font-medium text-gray-400 mb-2">
        <div className="bg-gray-900/50 p-1.5 rounded">Current<br /><span className="text-white text-xs">{inst.last_price ? Number(inst.last_price).toLocaleString(undefined, {maximumFractionDigits: 2}) : '—'}</span></div>
        <div className="bg-gray-900/50 p-1.5 rounded">Leverage<br /><span className="text-white text-xs">{inst.leverage ?? '—'}×</span></div>
        <div className="bg-gray-900/50 p-1.5 rounded">Margin<br /><span className="text-white text-xs">{inst.margin_pct ?? '—'}%</span></div>
      </div>
      <div className="mb-2 flex items-center justify-between gap-2 text-[10px] text-gray-500">
        <span>{activeTrades.length} open · {(inst.closed_trades || []).length} closed</span>
        <span className="font-mono text-gray-600" title={inst.instance_key}>{inst.instance_key.split('_').pop()}</span>
      </div>
      <div className="flex flex-wrap justify-between gap-x-3 text-[10px] text-gray-600 font-mono">
        <span>Started: {fmtIST(inst.created_at)}</span>
        <span>Updated: {lastChecked}</span>
      </div>
      {inst.is_running && (
        <button onClick={(e) => { e.stopPropagation(); onStop(inst.instance_key, strategyName); }}
                className="mt-3 w-full rounded-lg border border-red-900/50 bg-red-900/20 px-3 py-2 text-xs font-bold text-red-300 transition hover:bg-red-900/50 flex items-center justify-center gap-1">
          <StopCircle size={14} /> Stop Instance
        </button>
      )}
    </div>
  );
};

// ---------- History Panel (persisted sessions) ----------
// Stopping a paper instance used to discard its trades, logs and equity. Every
// session is now saved server-side, so this panel lists past runs and opens the
// full saved detail (trades, equity curve, logs, parameters).
const HistoryRowDetail = ({ session }) => {
  const curve = (session.equity_curve || []).map((p, i) => ({
    i,
    equity: Number(p.equity),
    ts: fmtIST(p.ts),
  }));
  return (
    <div className="space-y-4 border-t border-gray-700 bg-gray-900/60 p-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        {[
          ['Initial capital', inr(session.initial_capital)],
          ['Final equity', inr(session.final_equity, 2)],
          ['Net PnL', inr(session.net_pnl, 2)],
          ['ROI', session.roi != null ? `${Number(session.roi).toFixed(2)}%` : '—'],
          ['Max drawdown', session.max_drawdown_pct != null ? `${Number(session.max_drawdown_pct).toFixed(2)}%` : '—'],
          ['Win rate', session.win_rate != null ? `${Number(session.win_rate).toFixed(2)}%` : '—'],
          ['Profit factor', session.profit_factor != null ? Number(session.profit_factor).toFixed(2) : '—'],
          ['Fees paid', inr(session.total_fees, 2)],
          ['Closed trades', session.closed_trade_count ?? 0],
          ['Margin %', session.margin_pct != null ? `${Number(session.margin_pct).toFixed(0)}%` : '—'],
          ['Leverage', session.leverage != null ? `${session.leverage}×` : '—'],
          ['Fees (bps)', `${session.taker_fee_bps ?? '—'}/${session.maker_fee_bps ?? '—'}`],
          ['Started', fmtUTC(session.started_at)],
          ['Stopped', fmtUTC(session.stopped_at)],
          ['Last tick', fmtUTC(session.last_checked)],
        ].map(([label, value], i) => (
          <div key={i} className="rounded-lg border border-gray-800 bg-gray-800/60 p-2">
            <div className="text-[9px] font-bold uppercase text-gray-500">{label}</div>
            <div className="truncate text-xs font-mono text-gray-200" title={String(value)}>{value}</div>
          </div>
        ))}
      </div>

      {curve.length > 1 && (
        <div className="rounded-xl border border-gray-700 bg-gray-800 p-3">
          <div className="mb-2 flex items-center gap-2 text-[10px] font-bold uppercase text-gray-400">
            <TrendingUp size={12} className="text-blue-400" /> Saved equity curve ({curve.length} samples)
          </div>
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={curve} margin={{ top: 5, right: 10, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
              <XAxis dataKey="i" stroke="#6b7280" tick={{ fontSize: 9 }} />
              <YAxis stroke="#6b7280" tick={{ fontSize: 9 }} domain={['auto', 'auto']}
                     tickFormatter={v => `₹${Number(v).toLocaleString()}`} width={80} />
              <Tooltip contentStyle={{ backgroundColor: '#111827', borderColor: '#374151', fontSize: 11 }}
                       labelFormatter={(i, payload) => payload?.[0]?.payload?.ts || ''}
                       formatter={v => [inr(v, 2), 'Equity']} />
              <Line type="monotone" dataKey="equity" stroke="#60a5fa" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {(session.open_positions || []).length > 0 && (
        <div className="rounded-xl border border-yellow-900/40 bg-yellow-900/10 p-3 text-[11px] text-yellow-200">
          <b>{session.open_positions.length}</b> position(s) were still open when the session ended —
          their PnL is unrealised and is not included in the closed-trade stats.
        </div>
      )}

      <ClosedTradesPanel closedTrades={session.closed_trades} />

      {(session.logs || []).length > 0 && (
        <div className="rounded-xl border border-gray-700 bg-gray-900 p-3">
          <div className="mb-2 flex items-center gap-2 text-[10px] font-bold uppercase text-gray-400">
            <Terminal size={12} className="text-blue-400" /> Saved logs ({session.logs.length})
          </div>
          <div className="max-h-64 space-y-0.5 overflow-y-auto font-mono text-[10px]">
            {session.logs.slice().reverse().map((l, i) => (
              <div key={i} className="flex gap-2">
                <span className="shrink-0 text-gray-600">{fmtIST(l.ts)}</span>
                <span className={`shrink-0 font-bold ${l.level === 'error' ? 'text-red-400' : l.level === 'warn' ? 'text-yellow-400' : l.level === 'trade' ? 'text-green-400' : 'text-gray-400'}`}>
                  {String(l.level || 'info').toUpperCase().padEnd(5)}
                </span>
                <span className="break-all text-gray-300">{l.msg}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

const exportSessionCSV = (session) => {
  const trades = session.closed_trades || [];
  if (!trades.length) { alert('This session has no closed trades to export.'); return; }
  // [data key, column header] pairs so the export can use clear names while
  // still reading each field from the closed-trade object.
  const cols = [
    ['entry_time', 'Entry Time (IST)'], ['exit_time', 'Exit Time (IST)'],
    ['direction', 'Direction'], ['symbol', 'Symbol'],
    ['entry', 'Entry Price'], ['exit', 'Exit Price'],
    ['entry_trade_price', 'Entry Price (Traded)'], ['exit_trade_price', 'Exit Price (Traded)'],
    ['entry_mark_price', 'Entry Price (Mark)'], ['exit_mark_price', 'Exit Price (Mark)'],
    ['mark_price_basis', 'Priced On Mark'],
    ['lots', 'Lots'], ['margin_inr', 'Margin (INR)'], ['notional_usd', 'Notional (USD)'],
    ['sl', 'Stop Loss'], ['sl_final', 'Stop Loss (Final)'], ['tp', 'Take Profit'],
    ['trail_stop', 'Trail Stop'], ['atr_at_entry', 'ATR @ Entry'], ['peak_price', 'Peak Price'],
    ['bars_held', 'Bars Held'], ['reason', 'Exit Reason'], ['exit_detail', 'Exit Detail'],
    ['gross_pnl', 'PnL (Gross)'], ['fees', 'Fees'], ['pnl', 'Booked PnL (Net)'],
  ];
  const lines = [cols.map(([, label]) => label).join(',')];
  trades.forEach(t => {
    lines.push(cols.map(([key]) => {
      const v = t[key];
      const s = v === null || v === undefined ? '' : String(v);
      return `"${s.replace(/"/g, '""')}"`;
    }).join(','));
  });
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `paper_session_${session.id}_${session.strategy_name || 'kudos'}.csv`.replace(/[^a-z0-9_.-]/gi, '_');
  a.click();
  URL.revokeObjectURL(url);
};

const HistoryPanel = ({ history, loading, onRefresh, onDelete }) => {
  const [openId, setOpenId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const toggle = async (row) => {
    if (openId === row.id) { setOpenId(null); setDetail(null); return; }
    setOpenId(row.id);
    setDetail(null);
    setDetailLoading(true);
    try {
      const res = await fetch(`${API_URL}/paper-trade/history/${row.id}`, {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` },
      });
      if (res.ok) setDetail(await res.json());
    } catch (e) { /* keep the row open with the summary only */ }
    setDetailLoading(false);
  };

  return (
    <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-lg font-bold flex items-center gap-2">
            <History size={20} className="text-blue-400" /> Paper Trade History ({history.length})
          </h3>
          <p className="mt-1 text-[11px] text-gray-500">
            Every session is saved while it runs, so stopping or deleting a live instance never loses its
            trades, equity curve or logs.
          </p>
        </div>
        <button onClick={onRefresh}
                className="flex items-center gap-2 rounded-lg border border-gray-700 px-3 py-1.5 text-xs font-semibold text-gray-300 transition hover:border-blue-500 hover:text-white disabled:opacity-50"
                disabled={loading}>
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
      </div>

      {history.length === 0 ? (
        <div className="rounded-xl border border-dashed border-gray-700 bg-gray-800/50 p-6 text-center text-sm text-gray-600">
          No saved sessions yet. Start an instance above — it appears here automatically and stays after you stop it.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-gray-900 uppercase text-gray-500">
              <tr>
                <th className="p-2">Strategy</th>
                <th className="p-2">Source</th>
                <th className="p-2">Status</th>
                <th className="p-2">Started</th>
                <th className="p-2">Stopped</th>
                <th className="p-2">Capital</th>
                <th className="p-2">Final Equity</th>
                <th className="p-2">Net PnL</th>
                <th className="p-2">ROI</th>
                <th className="p-2">Trades</th>
                <th className="p-2">WR</th>
                <th className="p-2">Max DD</th>
                <th className="p-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {history.map(row => {
                const st = statusMeta(row.status);
                const isOpen = openId === row.id;
                const netPositive = (row.net_pnl || 0) >= 0;
                return (
                  <React.Fragment key={row.id}>
                    <tr className={`border-b border-gray-700/60 align-top transition hover:bg-gray-700/20 ${isOpen ? 'bg-gray-900/40' : ''}`}>
                      <td className="max-w-[180px] p-2">
                        <div className="truncate font-bold text-gray-200" title={row.strategy_name}>{row.strategy_name || row.strategy_id}</div>
                        <div className="text-[9px] text-gray-600">#{String(row.instance_key || '').split('_').pop()}</div>
                      </td>
                      <td className="p-2 text-gray-400">{row.data_source || '—'}</td>
                      <td className="p-2">
                        <span className={`rounded border px-2 py-0.5 text-[10px] font-bold ${st.cls}`}>{st.label}</span>
                      </td>
                      <td className="p-2 font-mono text-[10px] text-gray-400">{fmtUTC(row.started_at)}</td>
                      <td className="p-2 font-mono text-[10px] text-gray-400">{fmtUTC(row.stopped_at)}</td>
                      <td className="p-2 font-mono text-gray-300">{inr(row.initial_capital)}</td>
                      <td className="p-2 font-mono text-gray-200">{inr(row.final_equity, 2)}</td>
                      <td className={`p-2 font-mono font-bold ${netPositive ? 'text-green-400' : 'text-red-400'}`}>{inr(row.net_pnl, 2)}</td>
                      <td className={`p-2 font-mono ${(row.roi || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {row.roi != null ? `${Number(row.roi).toFixed(2)}%` : '—'}
                      </td>
                      <td className="p-2 font-mono text-gray-300">{row.closed_trade_count ?? 0}</td>
                      <td className="p-2 font-mono text-gray-300">{row.win_rate != null ? `${Number(row.win_rate).toFixed(1)}%` : '—'}</td>
                      <td className="p-2 font-mono text-gray-300">{row.max_drawdown_pct != null ? `${Number(row.max_drawdown_pct).toFixed(2)}%` : '—'}</td>
                      <td className="p-2">
                        <div className="flex items-center justify-end gap-1">
                          <button onClick={() => toggle(row)} title={isOpen ? 'Hide result' : 'View saved result'}
                                  className="flex items-center gap-1 rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-300 transition hover:border-blue-500 hover:text-white">
                            {isOpen ? <ChevronUp size={11} /> : <Eye size={11} />} {isOpen ? 'Hide' : 'View'}
                          </button>
                          <button onClick={async () => {
                                    const res = await fetch(`${API_URL}/paper-trade/history/${row.id}`, {
                                      headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } });
                                    if (res.ok) exportSessionCSV(await res.json());
                                  }}
                                  title="Export closed trades to CSV"
                                  className="rounded border border-gray-700 p-1.5 text-gray-400 transition hover:border-blue-500 hover:text-white">
                            <Download size={11} />
                          </button>
                          <button onClick={() => onDelete(row.id, row.strategy_name)} title="Delete this saved session"
                                  className="rounded border border-gray-700 p-1.5 text-gray-500 transition hover:border-red-700 hover:text-red-300">
                            <Trash2 size={11} />
                          </button>
                        </div>
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="border-b border-gray-700/60">
                        <td colSpan={13} className="p-0">
                          {detailLoading && !detail ? (
                            <div className="p-6 text-center text-xs text-gray-500">Loading saved result…</div>
                          ) : detail ? (
                            <HistoryRowDetail session={detail} />
                          ) : (
                            <div className="p-6 text-center text-xs text-gray-500">Saved result unavailable.</div>
                          )}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

// ---------- Main Page ----------
const PaperTrade = () => {
  const [status, setStatus] = useState([]);
  const [selectedStrategy, setSelectedStrategy] = useState('PhantomV2');
  const [loading, setLoading] = useState(false);
  const [strategies, setStrategies] = useState([]);
  const [selectedInstance, setSelectedInstance] = useState(null);
  const [confirm, setConfirm] = useState(null); // { type, key, ... }
  const [capital, setCapital] = useState(20000);
  const [marginPct, setMarginPct] = useState(25);
  // BTC perpetual: risk on the exchange mark price (default on), and the
  // "skip new trades" schedule applied to new instances.
  const [useMarkPrice, setUseMarkPrice] = useState(true);
  const [tradingWindows, setTradingWindows] = useState(() => emptySchedule());
  const [showWindows, setShowWindows] = useState(false);
  // Live ticks for paper exits — same options as Live Trade. Entries still
  // wait for a closed 1h candle either way.
  const [priceFeed, setPriceFeed] = useState('off');
  const [tickInterval, setTickInterval] = useState(5);
  const [dataSource, setDataSource] = useState('Binance');
  const [sources, setSources] = useState([{ code: 'Binance', name: 'Binance Futures' }, { code: 'Delta', name: 'Delta Exchange' }]);
  // Saved sessions (survive stop / server restart) shown in Paper Trade History.
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  const fetchHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const res = await fetch(`${API_URL}/paper-trade/history`, {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` },
      });
      const data = await res.json();
      setHistory(Array.isArray(data) ? data : []);
    } catch (e) { /* history is a read-only extra; never block the live view */ }
    setHistoryLoading(false);
  }, []);

  useEffect(() => {
    fetchHistory();
    const interval = setInterval(fetchHistory, 30000);
    return () => clearInterval(interval);
  }, [fetchHistory]);

  useEffect(() => {
    fetch(`${API_URL}/broker-definitions`, { headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } })
      .then(r => r.ok ? r.json() : []).then(list => {
        if (Array.isArray(list) && list.length) setSources(list.map(x => ({ code: x.code, name: x.name })));
      }).catch(() => {});
    fetch(`${API_URL}/strategies`, { headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } })
      .then(res => res.json())
      .then(data => setStrategies(data));
    // Load the user's (admin-set) default capital & margin for new instances.
    fetch(`${API_URL}/broker-settings`, { headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } })
      .then(r => (r.ok ? r.json() : null))
      .then(data => {
        if (data) {
          if (data.initial_capital) setCapital(data.initial_capital);
          if (data.margin_deployment_pct) setMarginPct(data.margin_deployment_pct);
          if (data.use_mark_price !== undefined && data.use_mark_price !== null) setUseMarkPrice(!!data.use_mark_price);
        }
      })
      .catch(() => {});
    // The account-level schedule is the default for every new instance; an
    // explicit per-instance edit overrides it (and can be saved back).
    fetch(`${API_URL}/trading-windows`, { headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } })
      .then(r => (r.ok ? r.json() : null))
      .then(data => { if (data) setTradingWindows(normalizeSchedule(data)); })
      .catch(() => {});
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/paper-trade/status`, { headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } });
      const data = await res.json();
      setStatus(data);
      // Auto-select the first instance if none selected
      if (!selectedInstance && data.length > 0) {
        setSelectedInstance(data[0].instance_key);
      }
      // Clear selection if instance no longer exists
      if (selectedInstance && !data.find(s => s.instance_key === selectedInstance)) {
        setSelectedInstance(data.length > 0 ? data[0].instance_key : null);
      }
    } catch (e) {}
  }, [selectedInstance]);

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 3000);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  const startTrade = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/paper-trade/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${localStorage.getItem('token')}` },
        body: JSON.stringify({
          strategy_id: selectedStrategy,
          initial_capital: parseFloat(capital),
          margin_pct: parseFloat(marginPct),
          broker_name: dataSource,
          data_source: dataSource,
          use_mark_price: useMarkPrice,
          trading_windows: tradingWindows,
          price_feed: priceFeed, tick_interval: Number(tickInterval),
        })
      });
      if (res.ok) {
        const data = await res.json();
        // Auto-select the new instance after a short delay
        setTimeout(() => {
          fetchStatus();
          setSelectedInstance(data.instance_key);
        }, 500);
        // The new session is saved immediately, so refresh the History list.
        fetchHistory();
      } else {
        const err = await res.json().catch(() => ({}));
        alert(err.detail || 'Could not start paper trade');
      }
    } catch (e) { console.error(e); alert(e.message); }
    setLoading(false);
  };

  const requestStop = (instanceKey, name) => {
    setConfirm({ type: 'stop', key: instanceKey, name });
  };

  const requestDelete = (instanceKey, name) => {
    setConfirm({ type: 'delete', key: instanceKey, name });
  };

  const requestHistoryDelete = (sessionId, name) => {
    setConfirm({ type: 'history', key: sessionId, name });
  };

  const doInstanceAction = async () => {
    if (!confirm) return;
    try {
      const isDelete = confirm.type === 'delete';
      const isHistory = confirm.type === 'history';
      const url = isHistory
        ? `${API_URL}/paper-trade/history/${encodeURIComponent(confirm.key)}`
        : isDelete
          ? `${API_URL}/paper-trade/${encodeURIComponent(confirm.key)}`
          : `${API_URL}/paper-trade/stop?instance_key=${encodeURIComponent(confirm.key)}`;
      const res = await fetch(url, {
        method: (isDelete || isHistory) ? 'DELETE' : 'POST',
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` },
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `Could not ${isHistory ? 'delete the saved session' : isDelete ? 'delete' : 'stop'} paper trade`);
      }
      if (!isHistory) await fetchStatus();
      await fetchHistory();
    } catch (e) {
      console.error(e);
      alert(e.message);
    }
    setConfirm(null);
  };

  // Derived state
  const currentInstance = status.find(s => s.instance_key === selectedInstance);
  const activeTrades = currentInstance?.active_trades || [];
  const totalEquity = status.reduce((s, i) => s + (i.equity_inr || 0), 0);
  const marginUsed = status.flatMap(i => i.active_trades || []).reduce((s, t) => s + t.margin, 0);
  const runningCount = status.filter(s => s.is_running).length;

  return (
    <div className="page-shell">
      <ConfirmModal
        open={!!confirm}
        title={confirm?.type === 'delete'
          ? 'Delete Paper Trade Session?'
          : confirm?.type === 'history'
            ? 'Delete Saved Paper Trade Result?'
            : confirm?.type === 'stop' ? 'Stop Paper Trade Instance?' : 'Confirm'}
        message={confirm?.type === 'delete'
          ? `Delete "${confirm?.name || confirm?.key?.split('_').pop()}"? The live instance stops AND its saved result is removed from History permanently. Use Stop instead if you want to keep the result.`
          : confirm?.type === 'history'
            ? `Permanently delete the saved result of "${confirm?.name || 'this session'}" (trades, equity curve and logs)?`
            : confirm?.type === 'stop'
              ? `Stop "${confirm?.name || confirm?.key?.split('_').pop()}"? Its trades, equity curve and logs are saved — you can review the result in Paper Trade History below.`
              : ''}
        confirmLabel={confirm?.type === 'stop' ? 'Yes, Stop' : 'Yes, Delete'}
        confirmColor="bg-red-600 hover:bg-red-500"
        onCancel={() => setConfirm(null)}
        onConfirm={doInstanceAction}
      />

      {/* Header */}
      <div className="flex flex-col xl:flex-row xl:justify-between xl:items-start gap-4 mb-6">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold flex items-center gap-3 text-blue-400">
            <Activity size={28} /> Paper Trading
          </h1>
          <p className="text-gray-400 text-sm mt-1">Simulating trades with real-time market data</p>
        </div>
        <div className="flex flex-wrap gap-3 items-end">
          <select value={dataSource} onChange={e => setDataSource(e.target.value)}
                  className="bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500">
            {sources.map(s => <option key={s.code} value={s.code}>{s.name}</option>)}
          </select>
          <select value={selectedStrategy} onChange={e => setSelectedStrategy(e.target.value)}
                  className="bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500">
            <option value="PhantomV2">Kudos V2.5 (Default)</option>
            <option value="FastTest">Fast Test Strategy</option>
            {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <div className="flex flex-col">
            <label className="text-[10px] text-gray-500 uppercase font-bold mb-0.5">Exit checks</label>
            <select value={priceFeed} onChange={e => setPriceFeed(e.target.value)}
                    className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
                    title="How often open paper positions are re-checked against the live price. Entries always wait for a closed 1h candle.">
              <option value="off">Every 60s (default)</option>
              <option value="websocket">Live ticks · WebSocket</option>
              <option value="rest">Live ticks · polling</option>
            </select>
          </div>
          {priceFeed !== 'off' && (
            <div className="flex flex-col">
              <label className="text-[10px] text-gray-500 uppercase font-bold mb-0.5">Tick interval (s)</label>
              <input type="number" min="1" max="60" step="1" value={tickInterval}
                     onChange={e => setTickInterval(e.target.value)}
                     className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white w-20 outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          )}
          <div className="flex flex-col">
            <label className="text-[10px] text-gray-500 uppercase font-bold mb-0.5">Pricing &amp; windows</label>
            <button onClick={() => setShowWindows(!showWindows)}
                    className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition ${
                      showWindows || isScheduleActive(tradingWindows) || !useMarkPrice
                        ? 'border-amber-600 bg-amber-900/20 text-amber-300'
                        : 'border-gray-700 bg-gray-800 text-gray-300 hover:border-gray-600'}`}>
              <CalendarClock size={15} />
              {isScheduleActive(tradingWindows) ? 'Windows ON' : 'Windows OFF'}
              <span className="text-[10px] opacity-70">{useMarkPrice ? '· MARK' : '· TRADE'}</span>
            </button>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex flex-col">
              <label className="text-[10px] text-gray-500 uppercase font-bold mb-0.5">Capital (₹)</label>
              <input type="number" min="1000" step="1000" value={capital} onChange={e => setCapital(e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white w-32 outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div className="flex flex-col">
              <label className="text-[10px] text-gray-500 uppercase font-bold mb-0.5">Margin %</label>
              <input type="number" min="1" max="100" step="1" value={marginPct} onChange={e => setMarginPct(e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white w-20 outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <button onClick={startTrade} disabled={loading}
                    className="self-end px-6 py-2 rounded-lg font-bold transition bg-blue-600 hover:bg-blue-500 disabled:opacity-50 flex items-center gap-2">
              <Play size={18} /> {loading ? 'Starting…' : 'Start Instance'}
            </button>
          </div>
        </div>
      </div>

      {/* Pricing basis + "skip new trades" schedule for new instances */}
      {showWindows && (
        <div className="mb-6 grid grid-cols-1 gap-4 xl:grid-cols-3">
          <div className="rounded-xl border border-gray-700 bg-gray-800 p-4">
            <div className="mb-2 text-xs font-bold uppercase tracking-wider text-gray-400">
              BTC perpetual pricing
            </div>
            <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-gray-700 bg-gray-900 p-2.5 text-[11px] text-gray-300">
              <input type="checkbox" checked={useMarkPrice}
                     onChange={e => setUseMarkPrice(e.target.checked)}
                     className="mt-0.5 h-3.5 w-3.5 accent-amber-500" />
              <span>
                <span className="block font-bold text-white">Use mark price</span>
                <span className="mt-0.5 block text-gray-500">
                  Stops, targets, trailing and PnL run on the mark price of the
                  {' '}{String(dataSource).toLowerCase() === 'delta' ? 'BTCUSD' : 'BTCUSDT'} perpetual.
                  The traded price is stored on every trade too.
                </span>
              </span>
            </label>
            <button onClick={async () => {
                try {
                  await fetch(`${API_URL}/trading-windows`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${localStorage.getItem('token')}` },
                    body: JSON.stringify({ ...tradingWindows, enabled: tradingWindows.enabled }),
                  });
                } catch (e) { /* saving the default is a convenience only */ }
              }}
                    className="mt-2 w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-1.5 text-[11px] font-semibold text-gray-300 transition hover:border-blue-500 hover:text-white">
              Save as my account default
            </button>
            <p className="mt-1.5 text-[10px] leading-snug text-gray-600">
              Saved defaults are used by every instance you start until you change them here.
            </p>
          </div>
          <div className="xl:col-span-2">
            <TradingWindowsEditor
              value={tradingWindows}
              onChange={setTradingWindows}
              title="Skip new trades"
              subtitle="New instances started from this page use this schedule. Positions already open keep running inside a window."
            />
          </div>
        </div>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <div className="bg-gray-800 p-4 rounded-xl border border-gray-700">
          <div className="text-xs text-gray-500 uppercase font-bold mb-1">Total Equity</div>
          <div className="text-xl font-mono font-bold text-yellow-400">₹{totalEquity.toLocaleString(undefined, {minimumFractionDigits: 2})}</div>
        </div>
        <div className="bg-gray-800 p-4 rounded-xl border border-gray-700">
          <div className="text-xs text-gray-500 uppercase font-bold mb-1">Margin Used</div>
          <div className="text-xl font-mono font-bold text-blue-400">₹{marginUsed.toLocaleString(undefined, {minimumFractionDigits: 2})}</div>
        </div>
        <div className="bg-gray-800 p-4 rounded-xl border border-gray-700">
          <div className="text-xs text-gray-500 uppercase font-bold mb-1">Running Instances</div>
          <div className="text-xl font-mono font-bold text-green-400">{runningCount}</div>
        </div>
        <div className="bg-gray-800 p-4 rounded-xl border border-gray-700">
          <div className="text-xs text-gray-500 uppercase font-bold mb-1">Open Positions</div>
          <div className="text-xl font-mono font-bold text-purple-400">{status.flatMap(s => s.active_trades || []).length}</div>
        </div>
      </div>

      {/* Main content: left = instances + positions, right = logs */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left 2 cols: Instances + Positions */}
        <div className="lg:col-span-2 space-y-6">
          {/* Instances */}
          <div>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-bold text-gray-400 uppercase flex items-center gap-2">
                <AlertCircle size={16} /> Paper sessions ({status.length})
              </h3>
              {status.length > 1 && <span className="text-[10px] text-gray-600">Select a session to view its positions and logs</span>}
            </div>
            {status.length > 0 ? (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                {status.map((inst, index) => (
                  <InstanceCard key={inst.instance_key} inst={inst} position={index + 1}
                    onStop={requestStop} onDelete={requestDelete} onSelect={setSelectedInstance}
                    selected={inst.instance_key === selectedInstance} />
                ))}
              </div>
            ) : (
              <div className="bg-gray-800 p-8 rounded-xl border border-gray-700 text-center text-gray-500 text-sm">
                No instances running. Start one above.
              </div>
            )}
          </div>

          {/* Active trades for selected instance */}
          <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700 min-h-[300px]">
            <div className="flex justify-between items-center mb-4">
              <h3 className="text-lg font-bold flex items-center gap-2">
                <Activity size={20} className="text-blue-400" /> Simulated Positions
              </h3>
              {currentInstance && (
                <div className="flex min-w-0 items-center gap-3 text-xs">
                  <span className="max-w-[220px] truncate font-semibold text-blue-300" title={currentInstance.strategy_name || currentInstance.strategy_id}>
                    {currentInstance.strategy_name || currentInstance.strategy_id}
                  </span>
                  <span className="text-gray-500">Equity <span className="text-yellow-400 font-mono">₹{(currentInstance.equity_inr || 0).toLocaleString(undefined, {minimumFractionDigits: 2})}</span></span>
                  <span className="text-gray-600 font-mono">#{currentInstance.instance_key.split('_').pop()}</span>
                </div>
              )}
            </div>
            {activeTrades.length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {activeTrades.map((t, i) => <TradeCard key={i} trade={t} />)}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-[200px] text-gray-600">
                <TrendingUp size={40} className="mb-3 opacity-20" />
                <p className="text-sm">{currentInstance ? 'No open positions. Monitoring for signals…' : 'Select an instance to view positions'}</p>
              </div>
            )}
          </div>

          {/* Closed trades / trade reply for selected instance */}
          <ClosedTradesPanel closedTrades={currentInstance?.closed_trades} />
        </div>

        {/* Right col: Live logs */}
        <div className="lg:col-span-1">
          <LogPanel instanceKey={selectedInstance} />
        </div>
      </div>

      {/* Saved results — survives stop, delete of the live card and restarts */}
      <div className="mt-6">
        <HistoryPanel history={history} loading={historyLoading}
                      onRefresh={fetchHistory} onDelete={requestHistoryDelete} />
      </div>
    </div>
  );
};

export default PaperTrade;
// Exported so the saved-result view can be render-tested on its own.
export { HistoryPanel, HistoryRowDetail, exportSessionCSV };
