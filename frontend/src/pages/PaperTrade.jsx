import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Play, StopCircle, Activity, AlertCircle, TrendingUp, Wallet, Terminal, XCircle, PlusCircle, Target, Trash2 } from 'lucide-react';
import { API_URL } from '../api';

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
      <div className="bg-gray-800/50 p-2 rounded">Current<br /><span className="text-white text-xs">{Number(trade.current).toFixed(2)}</span></div>
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
              <th className="p-2">Stop Loss</th>
              <th className="p-2">Take Profit</th>
              <th className="p-2">Trail Stop</th>
              <th className="p-2">ATR</th>
              <th className="p-2">Exit Condition</th>
              <th className="p-2">Net PnL</th>
              <th className="p-2">Fees</th>
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
                  <td className="p-2 font-mono text-red-300">{t.sl != null ? Number(t.sl).toFixed(2) : '—'}{slMoved && <span className="text-[9px] text-yellow-400 ml-1">→ {Number(t.sl_final).toFixed(2)} (BE)</span>}</td>
                  <td className="p-2 font-mono text-green-300">{t.tp != null ? Number(t.tp).toFixed(2) : '—'}</td>
                  <td className="p-2 font-mono text-purple-300">{t.trail_stop != null ? Number(t.trail_stop).toFixed(2) : '—'}</td>
                  <td className="p-2 font-mono text-gray-400">{t.atr_at_entry != null ? Number(t.atr_at_entry).toFixed(2) : '—'}</td>
                  <td className="p-2 max-w-[260px]">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold border ${meta.color}`}>{meta.label}</span>
                    {t.exit_detail && <div className="text-[10px] text-gray-500 mt-1 leading-snug">{t.exit_detail}</div>}
                  </td>
                  <td className={`p-2 font-mono font-bold ${t.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>{t.pnl >= 0 ? '+' : ''}{Number(t.pnl).toFixed(2)}</td>
                  <td className="p-2"><span className="bg-gray-900 px-2 py-0.5 rounded text-[10px] text-gray-400 border border-gray-700">{Number(t.fees || 0).toFixed(2)}</span></td>
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
  const [dataSource, setDataSource] = useState('Binance');
  const [sources, setSources] = useState([{ code: 'Binance', name: 'Binance Futures' }, { code: 'Delta', name: 'Delta Exchange' }]);

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
        }
      })
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
        })
      });
      if (res.ok) {
        const data = await res.json();
        // Auto-select the new instance after a short delay
        setTimeout(() => {
          fetchStatus();
          setSelectedInstance(data.instance_key);
        }, 500);
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

  const doInstanceAction = async () => {
    if (!confirm) return;
    try {
      const isDelete = confirm.type === 'delete';
      const url = isDelete
        ? `${API_URL}/paper-trade/${encodeURIComponent(confirm.key)}`
        : `${API_URL}/paper-trade/stop?instance_key=${encodeURIComponent(confirm.key)}`;
      const res = await fetch(url, {
        method: isDelete ? 'DELETE' : 'POST',
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` },
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `Could not ${isDelete ? 'delete' : 'stop'} paper trade`);
      }
      await fetchStatus();
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
        title={confirm?.type === 'delete' ? 'Delete Paper Trade Session?' : confirm?.type === 'stop' ? 'Stop Paper Trade Instance?' : 'Confirm'}
        message={confirm?.type === 'delete'
          ? `Delete "${confirm?.name || confirm?.key?.split('_').pop()}"? Its session, logs and in-memory trade history will be removed.`
          : confirm?.type === 'stop'
            ? `Stop "${confirm?.name || confirm?.key?.split('_').pop()}" and close this monitoring session? You can start a new instance later.`
            : ''}
        confirmLabel={confirm?.type === 'delete' ? 'Yes, Delete' : 'Yes, Stop'}
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
    </div>
  );
};

export default PaperTrade;
