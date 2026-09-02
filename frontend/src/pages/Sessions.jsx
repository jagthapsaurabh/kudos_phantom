import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity, AlertCircle, ChevronDown, ChevronUp, Clock, Download, FileText,
  Filter, Radio, Search, Terminal, Trash2, TrendingUp, Wallet,
} from 'lucide-react';
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { API_URL } from '../api';

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------
// Timestamps arrive in two shapes: worker-generated strings already carry an
// IST offset, database columns are naive UTC. Tag the naive ones before
// formatting so both render as India time.
const fmtIST = (iso) => {
  if (!iso) return '—';
  const s = String(iso);
  const tagged = /(Z|[+-]\d{2}:?\d{2})$/.test(s) ? s : `${s}Z`;
  const d = new Date(tagged);
  if (Number.isNaN(d.getTime())) return '—';
  const ist = new Date(d.getTime() + 5.5 * 60 * 60 * 1000);
  const p = (x) => String(x).padStart(2, '0');
  return `${p(ist.getUTCDate())}/${p(ist.getUTCMonth() + 1)}/${ist.getUTCFullYear()} `
       + `${p(ist.getUTCHours())}:${p(ist.getUTCMinutes())}:${p(ist.getUTCSeconds())}`;
};

const inr = (v, dp = 2) => (v === null || v === undefined || Number.isNaN(Number(v))
  ? '—'
  : `₹${Number(v).toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp })}`);

const num = (v, dp = 2) => (v === null || v === undefined || Number.isNaN(Number(v))
  ? '—' : Number(v).toFixed(dp));

const pct = (v, dp = 2) => (v === null || v === undefined || Number.isNaN(Number(v))
  ? '—' : `${Number(v).toFixed(dp)}%`);

const pnlClass = (v) => (Number(v) > 0 ? 'text-green-400' : Number(v) < 0 ? 'text-red-400' : 'text-gray-300');

const STATUS = {
  running: { label: 'Running', cls: 'text-green-300 border-green-700 bg-green-900/30' },
  stopped: { label: 'Stopped', cls: 'text-gray-300 border-gray-600 bg-gray-800' },
  interrupted: { label: 'Interrupted', cls: 'text-yellow-300 border-yellow-700 bg-yellow-900/30' },
  failed: { label: 'Failed', cls: 'text-red-300 border-red-700 bg-red-900/30' },
};
const statusMeta = (s) => STATUS[s] || { label: s || '—', cls: 'text-gray-400 border-gray-700 bg-gray-900' };

const EXIT_REASONS = {
  TP: { label: 'Take Profit', cls: 'text-green-400 border-green-800 bg-green-900/20' },
  TSL: { label: 'Trailing Stop', cls: 'text-purple-400 border-purple-800 bg-purple-900/20' },
  SL: { label: 'Stop Loss', cls: 'text-red-400 border-red-800 bg-red-900/20' },
  MH: { label: 'Max Hold', cls: 'text-yellow-400 border-yellow-800 bg-yellow-900/20' },
  REV: { label: 'Reversal', cls: 'text-blue-400 border-blue-800 bg-blue-900/20' },
};
const reasonMeta = (r) => EXIT_REASONS[r] || { label: r || '—', cls: 'text-gray-400 border-gray-700 bg-gray-900' };

// ---------------------------------------------------------------------------
// CSV export — every field, so the client can audit a run outside the tool
// ---------------------------------------------------------------------------
const TRADE_COLS = [
  ['entry_time', 'Entry Time (IST)'], ['exit_time', 'Exit Time (IST)'],
  ['direction', 'Direction'], ['symbol', 'Symbol'],
  ['entry', 'Entry Price'], ['exit', 'Exit Price'],
  ['entry_trade_price', 'Entry (Traded)'], ['exit_trade_price', 'Exit (Traded)'],
  ['entry_mark_price', 'Entry (Mark)'], ['exit_mark_price', 'Exit (Mark)'],
  ['mark_price_basis', 'Priced On Mark'],
  ['lots', 'Lots (BTC)'], ['margin_inr', 'Margin (INR)'], ['notional_usd', 'Notional (USD)'],
  ['sl', 'SL @ Entry'], ['sl_final', 'SL @ Exit'], ['tp', 'Take Profit'],
  ['trail_stop', 'Trail Stop'], ['trail_activation', 'Trail Armed At'],
  ['atr_at_entry', 'ATR @ Entry'], ['peak_price', 'Peak Price'],
  ['bars_held', 'Bars Held'], ['reason', 'Exit Reason'], ['exit_detail', 'Exit Detail'],
  ['gross_pnl', 'PnL (Gross)'], ['fees', 'Fees'], ['pnl', 'PnL (Net)'],
];

const exportCSV = (session) => {
  const trades = session.closed_trades || [];
  if (!trades.length) { alert('This session has no closed trades to export.'); return; }
  const lines = [TRADE_COLS.map(([, l]) => l).join(',')];
  trades.forEach((t) => {
    lines.push(TRADE_COLS.map(([k]) => {
      const v = t[k];
      return `"${(v === null || v === undefined ? '' : String(v)).replace(/"/g, '""')}"`;
    }).join(','));
  });
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `kudos_${session.mode || 'session'}_${session.strategy_name || session.strategy_id}_${session.id}.csv`
    .replace(/[^\w.\-]+/g, '_');
  a.click();
  URL.revokeObjectURL(url);
};

// ---------------------------------------------------------------------------
// Detail blocks
// ---------------------------------------------------------------------------
const Stat = ({ label, value, cls = '' }) => (
  <div className="rounded-lg border border-gray-800 bg-gray-800/60 p-2">
    <div className="text-[9px] font-bold uppercase tracking-wide text-gray-500">{label}</div>
    <div className={`truncate font-mono text-xs ${cls || 'text-gray-200'}`} title={String(value)}>{value}</div>
  </div>
);

// One closed trade, fully expanded: the prices it was filled at, the stop plan
// it started with, the levels in force when it closed, and why it closed.
const TradeRow = ({ trade, index }) => {
  const [open, setOpen] = useState(false);
  const meta = reasonMeta(trade.reason);
  const slMoved = trade.sl != null && trade.sl_final != null
    && Math.abs(Number(trade.sl) - Number(trade.sl_final)) > 0.005;
  return (
    <>
      <tr className={`border-b border-gray-800 transition hover:bg-gray-800/40 ${open ? 'bg-gray-800/30' : ''}`}>
        <td className="p-2 text-gray-500">{index + 1}</td>
        <td className="p-2">
          <span className={`font-bold ${trade.direction === 1 ? 'text-green-400' : 'text-red-400'}`}>
            {trade.direction === 1 ? 'LONG' : 'SHORT'}
          </span>
        </td>
        <td className="p-2 font-mono text-gray-300">{num(trade.entry)}</td>
        <td className="p-2 font-mono text-gray-300">{num(trade.exit)}</td>
        <td className="p-2 font-mono text-gray-400">{num(trade.lots, 4)}</td>
        <td className="p-2">
          <span className={`rounded border px-1.5 py-0.5 text-[10px] font-bold ${meta.cls}`}>{meta.label}</span>
        </td>
        <td className={`p-2 text-right font-mono font-bold ${pnlClass(trade.pnl)}`}>
          {Number(trade.pnl) >= 0 ? '+' : ''}{num(trade.pnl)}
        </td>
        <td className="p-2 font-mono text-[10px] text-gray-500">{fmtIST(trade.entry_time)}</td>
        <td className="p-2 font-mono text-[10px] text-gray-500">{fmtIST(trade.exit_time)}</td>
        <td className="p-2 text-right">
          <button onClick={() => setOpen(!open)}
                  className="rounded border border-gray-700 px-1.5 py-0.5 text-[10px] text-gray-400 transition hover:border-blue-500 hover:text-white">
            {open ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="border-b border-gray-800 bg-gray-900/70">
          <td colSpan={10} className="p-3">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-6">
              <Stat label="SL @ entry" value={num(trade.sl)} cls="text-red-300" />
              <Stat label="SL @ exit" value={num(trade.sl_final)}
                    cls={slMoved ? 'text-yellow-300' : 'text-red-300'} />
              <Stat label="Take profit" value={num(trade.tp)} cls="text-green-300" />
              <Stat label="Trail stop" value={num(trade.trail_stop)} cls="text-purple-300" />
              <Stat label="Trail armed at" value={num(trade.trail_activation)} />
              <Stat label="ATR @ entry" value={num(trade.atr_at_entry)} />
              <Stat label="Peak price" value={num(trade.peak_price)} />
              <Stat label="Bars held" value={trade.bars_held ?? 0} />
              <Stat label="Margin" value={inr(trade.margin_inr)} />
              <Stat label="Notional" value={`$${num(trade.notional_usd, 0)}`} />
              <Stat label="Entry (traded)" value={num(trade.entry_trade_price)} />
              <Stat label="Exit (traded)" value={num(trade.exit_trade_price)} />
              <Stat label="Entry (mark)" value={num(trade.entry_mark_price)} />
              <Stat label="Exit (mark)" value={num(trade.exit_mark_price)} />
              <Stat label="Priced on" value={trade.mark_price_basis ? 'Mark price' : 'Traded price'} />
              <Stat label="Gross PnL" value={num(trade.gross_pnl)} cls={pnlClass(trade.gross_pnl)} />
              <Stat label="Fees" value={trade.fees == null ? 'on exchange' : num(trade.fees)} />
              <Stat label="Net PnL" value={num(trade.pnl)} cls={pnlClass(trade.pnl)} />
            </div>
            {slMoved && (
              <p className="mt-2 text-[10px] text-yellow-400">
                The stop moved during this trade (breakeven / trail) — it exited on {num(trade.sl_final)},
                not the {num(trade.sl)} it started with.
              </p>
            )}
            {trade.exit_detail && (
              <p className="mt-2 rounded border border-gray-700 bg-gray-900 p-2 text-[11px] text-gray-300">
                <b className="text-gray-400">Exit condition:</b> {trade.exit_detail}
              </p>
            )}
          </td>
        </tr>
      )}
    </>
  );
};

const SessionDetail = ({ detail }) => {
  const [tab, setTab] = useState('trades');
  const curve = (detail.equity_curve || []).map((p, i) => ({
    i, equity: Number(p.equity), ts: fmtIST(p.ts),
  }));
  const trades = detail.closed_trades || [];
  const open = detail.open_positions || [];
  const logs = detail.logs || [];
  const params = detail.params || {};

  const tabs = [
    ['trades', `Trades (${trades.length})`, Activity],
    ['open', `Open at end (${open.length})`, Clock],
    ['equity', 'Equity curve', TrendingUp],
    ['logs', `Log (${logs.length})`, Terminal],
    ['params', 'Parameters', FileText],
  ];

  return (
    <div className="space-y-4 border-t border-gray-700 bg-gray-900/70 p-4">
      {/* Headline result */}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <Stat label="Initial capital" value={inr(detail.initial_capital, 0)} />
        <Stat label="Final equity" value={inr(detail.final_equity)} />
        <Stat label="Net PnL" value={inr(detail.net_pnl)} cls={pnlClass(detail.net_pnl)} />
        <Stat label="ROI" value={pct(detail.roi)} cls={pnlClass(detail.roi)} />
        <Stat label="Win rate" value={pct(detail.win_rate, 1)} />
        <Stat label="Profit factor" value={num(detail.profit_factor)} />
        <Stat label="Max drawdown" value={pct(detail.max_drawdown_pct)} cls="text-red-300" />
        <Stat label="Peak equity" value={inr(detail.peak_equity)} />
        <Stat label="Closed trades" value={detail.closed_trade_count ?? trades.length} />
        <Stat label="Fees paid" value={inr(detail.total_fees)} />
        <Stat label="Leverage" value={detail.leverage != null ? `${detail.leverage}×` : '—'} />
        <Stat label="Margin %" value={detail.margin_pct != null ? `${num(detail.margin_pct, 0)}%` : '—'} />
        <Stat label="Fees (bps)" value={`${detail.taker_fee_bps ?? '—'} / ${detail.maker_fee_bps ?? '—'}`} />
        <Stat label="Account" value={detail.account_label || 'Primary'} />
        <Stat label="Price feed" value={detail.price_feed || 'off'} />
        <Stat label="Worker restarts" value={detail.restarts ?? 0} />
        <Stat label="Started" value={fmtIST(detail.started_at)} />
        <Stat label="Stopped" value={fmtIST(detail.stopped_at)} />
      </div>

      {(detail.stop_reason || detail.last_error) && (
        <div className={`rounded-lg border p-3 text-xs ${
          detail.status === 'failed' ? 'border-red-900/50 bg-red-900/20 text-red-200'
                                     : 'border-gray-700 bg-gray-800 text-gray-300'}`}>
          <b>Why it ended:</b> {detail.stop_reason || '—'}
          {detail.last_error && (
            <div className="mt-1 font-mono text-[10px] opacity-80">Last error: {detail.last_error}</div>
          )}
        </div>
      )}

      {/* Tabs */}
      <div className="flex flex-wrap gap-1 border-b border-gray-700">
        {tabs.map(([key, label, Icon]) => (
          <button key={key} onClick={() => setTab(key)}
                  className={`flex items-center gap-1.5 rounded-t-lg px-3 py-2 text-xs font-semibold transition ${
                    tab === key ? 'border-b-2 border-blue-500 bg-gray-800 text-white'
                                : 'text-gray-500 hover:text-gray-300'}`}>
            <Icon size={13} /> {label}
          </button>
        ))}
      </div>

      {tab === 'trades' && (
        trades.length === 0 ? (
          <p className="p-4 text-center text-xs text-gray-500">
            This session closed no trades. Any position still open when it ended is on the
            “Open at end” tab.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-[10px] uppercase text-gray-500">
                <tr className="border-b border-gray-700">
                  <th className="p-2">#</th><th className="p-2">Side</th>
                  <th className="p-2">Entry</th><th className="p-2">Exit</th>
                  <th className="p-2">Lots</th><th className="p-2">Reason</th>
                  <th className="p-2 text-right">Net PnL</th>
                  <th className="p-2">Entered (IST)</th><th className="p-2">Exited (IST)</th>
                  <th className="p-2 text-right">Detail</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => <TradeRow key={i} trade={t} index={i} />)}
              </tbody>
            </table>
          </div>
        )
      )}

      {tab === 'open' && (
        open.length === 0 ? (
          <p className="p-4 text-center text-xs text-gray-500">No positions were open when this session ended.</p>
        ) : (
          <div className="space-y-2">
            <p className="rounded border border-yellow-900/40 bg-yellow-900/10 p-2 text-[11px] text-yellow-200">
              These were still open at the end. Their PnL is unrealised and is <b>not</b> included
              in the closed-trade statistics above.
            </p>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
              {open.map((p, i) => (
                <div key={i} className="rounded-xl border border-yellow-900/40 bg-gray-800 p-3">
                  <div className="mb-2 flex items-center justify-between">
                    <span className={`font-bold ${p.direction === 1 ? 'text-green-400' : 'text-red-400'}`}>
                      {p.direction === 1 ? 'LONG' : 'SHORT'} {p.symbol}
                    </span>
                    <span className={`font-mono text-sm font-bold ${pnlClass(p.pnl ?? p.unrealised_pnl)}`}>
                      {inr(p.pnl ?? p.unrealised_pnl)}
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-1.5">
                    <Stat label="Entry" value={num(p.entry)} />
                    <Stat label="Last" value={num(p.current)} />
                    <Stat label="Lots" value={num(p.lots, 4)} />
                    <Stat label="SL" value={num(p.sl)} cls="text-red-300" />
                    <Stat label="TP" value={num(p.tp)} cls="text-green-300" />
                    <Stat label="Bars" value={p.bars_held ?? 0} />
                  </div>
                  <div className="mt-1.5 text-[10px] text-gray-500">Entered {fmtIST(p.entry_time)}</div>
                </div>
              ))}
            </div>
          </div>
        )
      )}

      {tab === 'equity' && (
        curve.length > 1 ? (
          <div className="rounded-xl border border-gray-700 bg-gray-800 p-3">
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={curve} margin={{ top: 5, right: 12, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis dataKey="i" stroke="#6b7280" tick={{ fontSize: 9 }} />
                <YAxis stroke="#6b7280" tick={{ fontSize: 9 }} domain={['auto', 'auto']} width={84}
                       tickFormatter={(v) => `₹${Number(v).toLocaleString()}`} />
                <Tooltip contentStyle={{ backgroundColor: '#111827', borderColor: '#374151', fontSize: 11 }}
                         labelFormatter={(i, p) => p?.[0]?.payload?.ts || ''}
                         formatter={(v) => [inr(v), 'Equity']} />
                <Line type="monotone" dataKey="equity" stroke="#60a5fa" strokeWidth={1.5} dot={false} />
              </LineChart>
            </ResponsiveContainer>
            <p className="mt-2 text-[10px] text-gray-500">{curve.length} samples</p>
          </div>
        ) : (
          <p className="p-4 text-center text-xs text-gray-500">Not enough equity samples to draw a curve yet.</p>
        )
      )}

      {tab === 'logs' && (
        logs.length === 0 ? (
          <p className="p-4 text-center text-xs text-gray-500">No log lines were saved for this session.</p>
        ) : (
          <div className="max-h-96 space-y-0.5 overflow-y-auto rounded-xl border border-gray-700 bg-gray-950 p-3 font-mono text-[10px]">
            {logs.slice().reverse().map((l, i) => (
              <div key={i} className="flex gap-2">
                <span className="shrink-0 text-gray-600">{fmtIST(l.ts)}</span>
                <span className={`shrink-0 font-bold ${
                  l.level === 'error' ? 'text-red-400' : l.level === 'warn' ? 'text-yellow-400'
                  : l.level === 'trade' ? 'text-green-400' : 'text-gray-500'}`}>
                  {String(l.level || 'info').toUpperCase().padEnd(5)}
                </span>
                <span className="break-all text-gray-300">{l.msg}</span>
              </div>
            ))}
          </div>
        )
      )}

      {tab === 'params' && (
        Object.keys(params).length === 0 ? (
          <p className="p-4 text-center text-xs text-gray-500">No saved parameters for this session.</p>
        ) : (
          <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-5">
            {Object.entries(params)
              .filter(([, v]) => v === null || ['string', 'number', 'boolean'].includes(typeof v))
              .map(([k, v]) => (
                <Stat key={k} label={k.replace(/_/g, ' ')}
                      value={typeof v === 'boolean' ? (v ? 'yes' : 'no') : (v ?? '—')} />
              ))}
          </div>
        )
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
const Sessions = () => {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  // Live and paper histories are kept apart: this page is one tab per mode,
  // never a merged table. Default to live — the real-money record.
  const [tab, setTab] = useState('live');
  const [strategyFilter, setStrategyFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [openId, setOpenId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(null);

  const headers = () => ({ Authorization: `Bearer ${localStorage.getItem('token')}` });

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/sessions`, { headers: headers() });
      const data = await res.json();
      setRows(Array.isArray(data) ? data : []);
    } catch (e) { /* the list is read-only; leave the previous view up */ }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [load]);

  const openDetail = async (row) => {
    if (openId === row.id) { setOpenId(null); setDetail(null); return; }
    setOpenId(row.id); setDetail(null); setDetailLoading(true);
    try {
      const res = await fetch(`${API_URL}/sessions/${row.id}`, { headers: headers() });
      if (res.ok) setDetail(await res.json());
    } catch (e) { /* handled by the empty state below */ }
    setDetailLoading(false);
  };

  const doDelete = async () => {
    if (!confirmDelete) return;
    try {
      await fetch(`${API_URL}/sessions/${confirmDelete.id}`, { method: 'DELETE', headers: headers() });
      if (openId === confirmDelete.id) { setOpenId(null); setDetail(null); }
      await load();
    } catch (e) { alert(e.message); }
    setConfirmDelete(null);
  };

  const strategies = useMemo(
    () => Array.from(new Set(rows.map((r) => r.strategy_name || r.strategy_id).filter(Boolean))).sort(),
    [rows],
  );

  // Counts for the tab buttons — history size per mode at a glance.
  const counts = useMemo(() => rows.reduce((m, r) => {
    const k = (r.mode || 'paper');
    m[k] = (m[k] || 0) + 1;
    return m;
  }, { live: 0, paper: 0 }), [rows]);

  const filtered = useMemo(() => rows.filter((r) => {
    if ((r.mode || 'paper') !== tab) return false;
    if (statusFilter !== 'all' && r.status !== statusFilter) return false;
    if (strategyFilter !== 'all' && (r.strategy_name || r.strategy_id) !== strategyFilter) return false;
    if (query) {
      const hay = `${r.strategy_name} ${r.strategy_id} ${r.account_label} ${r.data_source} ${r.instance_key}`.toLowerCase();
      if (!hay.includes(query.toLowerCase())) return false;
    }
    return true;
  }), [rows, tab, statusFilter, strategyFilter, query]);

  // Roll-up across whatever is currently filtered, so "how has this ONE
  // strategy done overall" is answerable by picking it in the filter.
  const totals = useMemo(() => filtered.reduce((acc, r) => ({
    sessions: acc.sessions + 1,
    trades: acc.trades + (r.closed_trade_count || 0),
    net: acc.net + (r.net_pnl || 0),
    fees: acc.fees + (r.total_fees || 0),
  }), { sessions: 0, trades: 0, net: 0, fees: 0 }), [filtered]);

  return (
    <div className="page-shell">
      {confirmDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
             onClick={() => setConfirmDelete(null)}>
          <div className="mx-4 w-full max-w-md rounded-2xl border border-gray-700 bg-gray-800 p-6 shadow-2xl"
               onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-2 text-lg font-bold text-white">Delete this session?</h3>
            <p className="mb-6 text-sm text-gray-400">
              “{confirmDelete.strategy_name || confirmDelete.strategy_id}” and all of its saved
              trades, equity curve and logs will be permanently removed. This cannot be undone.
            </p>
            <div className="flex justify-end gap-3">
              <button onClick={() => setConfirmDelete(null)}
                      className="rounded-lg bg-gray-700 px-4 py-2 text-sm font-semibold text-gray-300 hover:bg-gray-600 hover:text-white">
                Cancel
              </button>
              <button onClick={doDelete}
                      className="rounded-lg bg-red-600 px-4 py-2 text-sm font-bold text-white hover:bg-red-500">
                Delete permanently
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="mb-6">
        <h1 className="flex items-center gap-3 text-2xl font-bold text-purple-400 sm:text-3xl">
          <FileText size={28} /> Trade History
        </h1>
        <p className="mt-1 text-sm text-gray-400">
          Live trades and paper runs are kept in separate histories — pick a tab. Every run is
          kept after it stops: open one to see its trades, stop plan, equity curve and log in full.
        </p>
      </div>

      {/* Live and paper histories stay separate — one tab per mode. */}
      <div className="mb-5 flex gap-2">
        {[['live', 'Live Trade History', Radio], ['paper', 'Paper Trade History', Activity]].map(([key, label, TabIcon]) => (
          <button key={key} onClick={() => setTab(key)}
                  className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-bold transition ${
                    tab === key
                      ? (key === 'live' ? 'bg-green-600 text-white shadow' : 'bg-blue-600 text-white shadow')
                      : 'border border-gray-700 bg-gray-800 text-gray-400 hover:text-white'}`}>
            <TabIcon size={14} /> {label}
            <span className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${tab === key ? 'bg-black/30' : 'bg-gray-900'}`}>
              {counts[key] || 0}
            </span>
          </button>
        ))}
      </div>

      {/* Filters + roll-up */}
      <div className="mb-5 flex flex-wrap items-end gap-3">
        <div className="flex flex-col">
          <label className="mb-1 text-[10px] font-bold uppercase text-gray-500">Strategy</label>
          <select value={strategyFilter} onChange={(e) => setStrategyFilter(e.target.value)}
                  className="rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm outline-none">
            <option value="all">All strategies</option>
            {strategies.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="flex flex-col">
          <label className="mb-1 text-[10px] font-bold uppercase text-gray-500">Status</label>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
                  className="rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm outline-none">
            <option value="all">Any status</option>
            <option value="running">Running</option>
            <option value="stopped">Stopped</option>
            <option value="interrupted">Interrupted</option>
            <option value="failed">Failed</option>
          </select>
        </div>
        <div className="flex min-w-[180px] flex-1 flex-col">
          <label className="mb-1 text-[10px] font-bold uppercase text-gray-500">Search</label>
          <div className="relative">
            <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
            <input value={query} onChange={(e) => setQuery(e.target.value)}
                   placeholder="strategy, account, broker…"
                   className="w-full rounded-lg border border-gray-700 bg-gray-800 py-2 pl-8 pr-3 text-sm outline-none" />
          </div>
        </div>
      </div>

      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-4">
        <div className="rounded-xl border border-gray-700 bg-gray-800 p-3">
          <div className="text-[10px] font-bold uppercase text-gray-500">Sessions shown</div>
          <div className="font-mono text-xl text-white">{totals.sessions}</div>
        </div>
        <div className="rounded-xl border border-gray-700 bg-gray-800 p-3">
          <div className="text-[10px] font-bold uppercase text-gray-500">Closed trades</div>
          <div className="font-mono text-xl text-white">{totals.trades}</div>
        </div>
        <div className="rounded-xl border border-gray-700 bg-gray-800 p-3">
          <div className="text-[10px] font-bold uppercase text-gray-500">Net PnL</div>
          <div className={`font-mono text-xl ${pnlClass(totals.net)}`}>{inr(totals.net)}</div>
        </div>
        <div className="rounded-xl border border-gray-700 bg-gray-800 p-3">
          <div className="text-[10px] font-bold uppercase text-gray-500">Fees</div>
          <div className="font-mono text-xl text-gray-300">{inr(totals.fees)}</div>
        </div>
      </div>

      <div className="overflow-x-auto rounded-2xl border border-gray-700 bg-gray-800">
        <table className="w-full text-left text-xs">
          <thead className="bg-gray-900/60 text-[10px] uppercase text-gray-500">
            <tr>
              <th className="p-3">Strategy</th>
              <th className="p-3">Account</th>
              <th className="p-3">Status</th>
              <th className="p-3">Started</th>
              <th className="p-3">Stopped</th>
              <th className="p-3">Capital</th>
              <th className="p-3">Final</th>
              <th className="p-3">Net PnL</th>
              <th className="p-3">ROI</th>
              <th className="p-3">Trades</th>
              <th className="p-3">WR</th>
              <th className="p-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={12} className="p-8 text-center text-gray-500">Loading sessions…</td></tr>
            )}
            {!loading && filtered.length === 0 && (
              <tr><td colSpan={12} className="p-8 text-center text-gray-500">
                {tab === 'live'
                  ? 'No live trade sessions match these filters. Start a live instance and its record will stay here after it stops.'
                  : 'No paper sessions match these filters. Start a paper instance and its record will stay here after you stop it.'}
              </td></tr>
            )}
            {filtered.map((row) => {
              const st = statusMeta(row.status);
              const isOpen = openId === row.id;
              return (
                <React.Fragment key={row.id}>
                  <tr className={`border-b border-gray-700/60 align-top transition hover:bg-gray-700/20 ${isOpen ? 'bg-gray-900/40' : ''}`}>
                    <td className="max-w-[180px] p-3">
                      <div className="truncate font-bold text-gray-200" title={row.strategy_name}>
                        {row.strategy_name || row.strategy_id}
                      </div>
                      <div className="text-[9px] text-gray-600">
                        #{String(row.instance_key || '').split('_').pop()} · {row.data_source}
                      </div>
                    </td>
                    <td className="max-w-[130px] truncate p-3 text-gray-400" title={row.account_label}>
                      {row.account_label || 'Primary'}
                    </td>
                    <td className="p-3">
                      <span className={`rounded border px-2 py-0.5 text-[10px] font-bold ${st.cls}`}
                            title={row.stop_reason || row.last_error || ''}>
                        {st.label}
                      </span>
                      {(row.stop_reason || row.last_error) && row.status !== 'running' && row.status !== 'stopped' && (
                        <div className="mt-1 max-w-[190px] text-[9px] leading-snug text-gray-400">
                          {row.stop_reason || row.last_error}
                        </div>
                      )}
                    </td>
                    <td className="p-3 font-mono text-[10px] text-gray-400">{fmtIST(row.started_at)}</td>
                    <td className="p-3 font-mono text-[10px] text-gray-400">{fmtIST(row.stopped_at)}</td>
                    <td className="p-3 font-mono text-gray-300">{inr(row.initial_capital, 0)}</td>
                    <td className="p-3 font-mono text-gray-200">{inr(row.final_equity)}</td>
                    <td className={`p-3 font-mono font-bold ${pnlClass(row.net_pnl)}`}>{inr(row.net_pnl)}</td>
                    <td className={`p-3 font-mono ${pnlClass(row.roi)}`}>{pct(row.roi)}</td>
                    <td className="p-3 font-mono text-gray-300">{row.closed_trade_count ?? 0}</td>
                    <td className="p-3 font-mono text-gray-300">{pct(row.win_rate, 1)}</td>
                    <td className="p-3">
                      <div className="flex items-center justify-end gap-1">
                        <button onClick={() => openDetail(row)}
                                className="flex items-center gap-1 rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-300 transition hover:border-blue-500 hover:text-white">
                          {isOpen ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                          {isOpen ? 'Hide' : 'Details'}
                        </button>
                        <button title="Export trades to CSV"
                                onClick={async () => {
                                  const res = await fetch(`${API_URL}/sessions/${row.id}`, { headers: headers() });
                                  if (res.ok) exportCSV(await res.json());
                                }}
                                className="rounded border border-gray-700 p-1.5 text-gray-400 transition hover:border-blue-500 hover:text-white">
                          <Download size={11} />
                        </button>
                        <button title="Delete this saved session"
                                onClick={() => setConfirmDelete(row)}
                                className="rounded border border-gray-700 p-1.5 text-gray-500 transition hover:border-red-700 hover:text-red-300">
                          <Trash2 size={11} />
                        </button>
                      </div>
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="border-b border-gray-700/60">
                      <td colSpan={12} className="p-0">
                        {detailLoading && !detail ? (
                          <div className="p-6 text-center text-xs text-gray-500">Loading full result…</div>
                        ) : detail ? (
                          <SessionDetail detail={detail} />
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
    </div>
  );
};

export default Sessions;
