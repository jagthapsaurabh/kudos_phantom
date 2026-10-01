import React from 'react';
import { buildTradesCSV } from '../pages/Backtest';

// ---------------------------------------------------------------------------
// Paper / Live trade analysis: the same entry-condition and exit-condition
// detail the Backtest page shows, for closed paper and live trades.
//
// The backend records the identical fields on every paper/live closed trade
// that a backtest trade row carries (signal candle + colour, the per-condition
// PASS/FAIL breakdown, the exit rule and the candle it landed in), so this
// module renders that data with the same wording and exports it through the
// Backtest page's own CSV builder — one column layout for all three pages.
// ---------------------------------------------------------------------------

// The entry-condition block: every filter with the value it measured, the
// threshold applied to that side and PASS/FAIL — plus which candle signalled
// and which candle the fill happened on.
export const EntryConditionBlock = ({ trade, testId = 'trade-entry-conditions' }) => {
  if (!trade) return null;
  const detail = trade.entry_conditions_detail;
  const anyCandle = trade.signal_candle_type || trade.entry_candle_type || trade.candle_type;
  if (!detail && !anyCandle) return null;
  return (
    <div className="mb-3 rounded-lg border border-gray-700 bg-gray-900 p-3" data-testid={testId}>
      <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[9px] font-bold uppercase text-gray-500">
        <span>
          Entry Conditions — {trade.setup || 'setup'} on the {trade.signal_candle_type || trade.candle_type || '—'} signal candle,
          filled on the {trade.entry_candle_type || '—'} entry candle
        </span>
        <CandleChips trade={trade} />
      </div>
      {detail ? (
        <div className="space-y-0.5 font-mono text-[10px]">
          {String(detail).split('\n').map((line, li) => (
            <div key={li}
                 className={line.endsWith('PASS') ? 'text-green-400'
                   : line.endsWith('FAIL') ? 'text-red-400'
                   : 'text-gray-400'}>
              {line}
            </div>
          ))}
        </div>
      ) : (
        <div className="text-[10px] text-gray-500">
          This strategy does not publish per-condition metadata, so the entry rule only is recorded.
        </div>
      )}
      {(trade.signal_candle_time || trade.entry_candle_time) && (
        <div className="mt-2 space-y-0.5 font-mono text-[9px] text-gray-500">
          {trade.signal_candle_time && <div>Signal candle: {fmtClock(trade.signal_candle_time)} UTC</div>}
          {trade.entry_candle_time && <div>Entry candle: {fmtClock(trade.entry_candle_time)} UTC</div>}
        </div>
      )}
    </div>
  );
};

// The exit-condition block: the reason code, the exact rule that fired and the
// colour of the candle the exit landed in.
export const ExitConditionBlock = ({ trade, testId = 'trade-exit-conditions' }) => {
  if (!trade || (!trade.exit_detail && !trade.reason && !trade.exit_reason)) return null;
  const reason = trade.reason || trade.exit_reason || '—';
  return (
    <div className="mb-3 rounded-lg border border-gray-700 bg-gray-900 p-3" data-testid={testId}>
      <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[9px] font-bold uppercase text-gray-500">
        <span>Exit Condition — {reason} on the {trade.exit_candle_type || '—'} candle</span>
        <CandleChips trade={trade} />
      </div>
      <div className="font-mono text-[10px] text-yellow-300">
        {trade.exit_detail || 'No exit detail was recorded for this trade (older record).'}
      </div>
      {trade.exit_time && (
        <div className="mt-2 font-mono text-[9px] text-gray-500">Exit candle: {fmtClock(trade.exit_time)} UTC</div>
      )}
    </div>
  );
};

// FastTest V1.0 audit block — rendered only when the seven fields exist.
export const FastTestV1AuditBlock = ({ trade, testId = 'trade-v1-audit' }) => {
  if (!trade || !trade.validation_status) return null;
  const money = (v) => (v == null ? '—' : `₹${Number(v).toFixed(2)}`);
  return (
    <div className="mb-3 rounded-lg border border-gray-700 bg-gray-900 p-3" data-testid={testId}>
      <div className="mb-1.5 text-[9px] font-bold uppercase text-gray-500">
        FastTest V1.0 — 2H validation &amp; +0.90% booking
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-[10px] text-gray-300 md:grid-cols-4">
        <div>Validation Status: <b>{trade.validation_status}</b></div>
        <div>Validation Close: <b>{trade.validation_close != null ? Number(trade.validation_close).toFixed(2) : '—'}</b></div>
        <div>Validation Threshold: <b>{trade.validation_threshold != null ? Number(trade.validation_threshold).toFixed(2) : '—'}</b></div>
        <div>TP 0.90% Hit: <b>{trade.tp090_hit ? 'YES' : 'NO'}</b></div>
        <div>Validation Exit: <b>{trade.validation_exit ? 'YES' : 'NO'}</b></div>
        <div>Final Exit Reason: <b>{trade.final_exit_reason || trade.reason || trade.exit_reason || '—'}</b></div>
        <div>Final Net P&amp;L: <b className={(trade.final_net_pnl ?? trade.pnl ?? 0) > 0 ? 'text-green-400' : 'text-red-400'}>
          {money(trade.final_net_pnl ?? trade.pnl)}
        </b></div>
      </div>
    </div>
  );
};

// Everything a reviewer needs for one closed paper / live trade.
export const TradeConditionDetail = ({ trade }) => {
  if (!trade) return null;
  const hasAnything = trade.entry_conditions_detail || trade.exit_detail
    || trade.signal_candle_type || trade.entry_candle_type || trade.exit_candle_type
    || trade.validation_status;
  if (!hasAnything) {
    return (
      <div className="p-3 text-[10px] text-gray-500" data-testid="trade-conditions-empty">
        No condition detail was recorded for this trade. Trades opened before this feature was
        enabled — and strategies that publish no condition metadata — have none.
      </div>
    );
  }
  return (
    <div data-testid="trade-conditions-detail">
      <EntryConditionBlock trade={trade} />
      <ExitConditionBlock trade={trade} />
      <FastTestV1AuditBlock trade={trade} />
    </div>
  );
};

const CandleChips = ({ trade }) => (
  <span className="flex flex-wrap gap-1">
    <CandleChip color={trade.signal_candle_type || trade.candle_type} label="signal" />
    <CandleChip color={trade.entry_candle_type} label="entry" />
    <CandleChip color={trade.exit_candle_type} label="exit" />
  </span>
);

const CandleChip = ({ color, label }) => {
  if (!color) return null;
  const cls = color === 'GREEN'
    ? 'bg-green-900/30 text-green-400 border-green-800/40'
    : color === 'RED'
      ? 'bg-red-900/30 text-red-400 border-red-800/40'
      : 'bg-gray-800 text-gray-500 border-gray-700';
  return (
    <span className={`rounded border px-1.5 py-0.5 text-[9px] font-bold ${cls}`}
          title={`${label} candle colour: ${color}`}>
      {color === 'GREEN' ? '▲' : color === 'RED' ? '▼' : '●'} {color}
    </span>
  );
};

// Candle timestamps are UTC; render them as UTC HH:MM so the analysis points
// at the same candle regardless of the viewer's timezone.
const fmtClock = (v) => {
  if (!v) return '';
  const s = String(v);
  const d = new Date(/(Z|[+-]\d{2}:?\d{2})$/.test(s) ? s : `${s}Z`);
  if (Number.isNaN(d.getTime())) return String(v);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} `
    + `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
};

// ---------------------------------------------------------------------------
// Export — the Backtest trade-log spreadsheet itself.
// ---------------------------------------------------------------------------

// Paper / live closed-trade records use their own key names (entry, exit, pnl,
// margin_inr …). Map one onto the backtest trade-log row shape so the same
// `buildTradesCSV` produces byte-for-byte the same columns as the Backtest
// export, including the entry-condition detail and the V1 audit fields.
export const tradeLogRow = (t) => ({
  direction: t.direction,
  setup: t.setup || '',
  signal_candle_time: t.signal_candle_time || t.entry_time,
  signal_candle_type: t.signal_candle_type || t.candle_type || '',
  entry_candle_time: t.entry_candle_time || t.entry_time,
  entry_candle_type: t.entry_candle_type || '',
  entry_price: t.entry,
  entry_mark_price: t.entry_mark_price,
  entry_trade_price: t.entry_trade_price,
  exit_time: t.exit_time,
  exit_candle_type: t.exit_candle_type || '',
  exit_price: t.exit,
  exit_mark_price: t.exit_mark_price,
  exit_trade_price: t.exit_trade_price,
  mark_price_basis: t.mark_price_basis,
  trend_4h: t.trend_4h,
  rsi14: t.rsi14, macd_hist: t.macd_hist, adx: t.adx, atr14: t.atr14,
  ema50_1h: t.ema50_1h, ema50_4h: t.ema50_4h,
  conditions: {
    trend_ok: t.cond_trend_ok,
    adx_ok: t.cond_adx_ok,
    macd_hist_ok: t.cond_macd_hist_ok,
    atr_regime_ok: t.cond_atr_regime_ok,
    rsi_ok: t.cond_rsi_ok,
    macd_confirm_ok: t.cond_macd_confirm_ok,
    di_ok: t.cond_di_ok,
    macd_line_ok: t.cond_macd_line_ok,
  },
  entry_conditions_detail: t.entry_conditions_detail,
  exit_reason: t.reason || t.exit_reason,
  exit_detail: t.exit_detail,
  sl_entry: t.sl != null ? t.sl : t.sl_entry,
  sl: t.sl_final != null ? t.sl_final : t.sl,
  tp: t.tp,
  trail_stop: t.trail_stop,
  atr_at_entry: t.atr_at_entry,
  peak_price: t.peak_price,
  lots: t.lots,
  margin: t.margin_inr != null ? t.margin_inr : t.margin,
  notional: t.notional_usd != null ? t.notional_usd : t.notional,
  margin_pct_used: t.margin_pct_used,
  entry_dd_pct: t.entry_dd_pct,
  gross_pnl: t.gross_pnl,
  fees: t.fees,
  net_pnl: t.pnl != null ? t.pnl : t.net_pnl,
  equity_after: t.equity_after,
  drawdown: t.drawdown,
  hold_bars: t.bars_held != null ? t.bars_held : t.hold_bars,
  macd_line: t.macd_line, macd_signal: t.macd_signal,
  validation_status: t.validation_status,
  validation_close: t.validation_close,
  validation_threshold: t.validation_threshold,
  tp090_hit: t.tp090_hit,
  validation_exit: t.validation_exit,
  final_exit_reason: t.final_exit_reason,
  final_net_pnl: t.final_net_pnl,
});

// The spreadsheet for a set of paper / live closed trades — same columns as the
// Backtest export, with the UTF-8 BOM so ₹ and ≥ survive in Excel.
export const tradesToCSV = (trades) => `\uFEFF${buildTradesCSV((trades || []).map(tradeLogRow))}`;

export const downloadTradesCSV = (trades, filename) => {
  if (!trades || !trades.length) {
    alert('This session has no closed trades to export.');
    return false;
  }
  const blob = new Blob([tradesToCSV(trades)], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
  return true;
};
