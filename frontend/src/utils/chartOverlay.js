/** Markers that plot strategy results on a market candlestick chart.

Every marker carries the full strategy detail (side, setup, 4h trend, candle
type, RSI, in/out leg) so a chart can render TradingView-style: the candle
shows only the icon (arrow / circle / square) and the detail is exposed on
hover / click. lightweight-charts keeps one marker per timestamp, so later
kinds on the same bar (exit > entry > signal) replace the shape while the
label is merged so nothing is silently dropped.

* LONG / SHORT — which side the strategy took
* REV / MOM    — reversal vs momentum setup
* ↑ / ↓        — 4h trend that licensed the trade
* IN / OUT     — backtest fill vs exit (and the exit reason)
*/

export const toUnix = (value) => {
  if (value == null || value === '') return null;
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value > 1e12 ? Math.floor(value / 1000) : Math.floor(value);
  }
  const s = String(value);
  // API datetimes are naive UTC. JS would otherwise parse them as local time
  // and every marker would land several hours off the candle.
  const iso = /(Z|[+-]\d{2}:?\d{2})$/.test(s) ? s : (s.includes('T') ? `${s}Z` : s);
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return Math.floor(d.getTime() / 1000);
};

export const fmtUnixUtc = (value) => {
  const t = toUnix(value);
  if (t == null) return '';
  const d = new Date(t * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} `
    + `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
};

export const setupShort = (setup) => {
  const s = String(setup || '').toUpperCase();
  if (!s) return 'SIG';
  if (s.startsWith('REV')) return 'REV';
  if (s.startsWith('MOM')) return 'MOM';
  return s.slice(0, 4);
};

export const trendArrow = (signal) => {
  if (!signal) return '';
  if (signal.trend === 1 || signal.trend_label === 'UP') return '↑';
  if (signal.trend === -1 || signal.trend_label === 'DOWN') return '↓';
  return '';
};

export const signalLabel = (signal) => {
  const side = Number(signal?.direction) === 1 ? 'LONG' : 'SHORT';
  const setup = setupShort(signal?.setup);
  const trend = trendArrow(signal);
  return `${side} ${setup}${trend ? ` ${trend}` : ''}`.trim();
};

const RANK = { signal: 1, entry: 2, exit: 3 };

// Merge two markers that landed on the same bar. The higher-ranked kind keeps
// its shape/position/colour (so exit > entry > signal wins visually), while the
// label and the hover detail are joined so nothing is silently dropped.
const mergeMarker = (prev, next) => {
  if (!prev) return next;
  const keepShape = (RANK[next.kind] || 0) >= (RANK[prev.kind] || 0) ? next : prev;
  const text = prev.text === next.text ? prev.text : `${prev.text} · ${next.text}`;
  const tooltip = prev.tooltip === next.tooltip ? prev.tooltip : `${prev.tooltip} · ${next.tooltip}`;
  const data = { ...(prev.data || {}), ...(next.data || {}) };
  return { ...keepShape, text, tooltip, data };
};

// The visible label is deliberately empty: the chart shows the icon alone
// (TradingView-style) so narrow candles and marker labels never collide. The
// detail lives in `tooltip` (hover) and `data` (click / side panel).
const markerBase = () => ({
  text: '',
  tooltip: '',
  data: {},
});

export const buildOverlayMarkers = ({ signals = [], trades = [] } = {}) => {
  const byTime = new Map();
  const put = (time, marker) => {
    const t = toUnix(time);
    if (t == null) return;
    const next = { ...markerBase(), ...marker, time: t };
    byTime.set(t, mergeMarker(byTime.get(t), next));
  };

  for (const s of signals || []) {
    const long = Number(s.direction) === 1;
    const side = long ? 'LONG' : 'SHORT';
    const setup = setupShort(s.setup);
    const trend = trendArrow(s);
    const label = `${side} ${setup}${trend ? ` ${trend}` : ''}`.trim();
    put(s.time, {
      kind: 'signal',
      position: long ? 'belowBar' : 'aboveBar',
      color: long ? '#22c55e' : '#ef4444',
      shape: long ? 'arrowUp' : 'arrowDown',
      tooltip: label,
      data: {
        side,
        setup,
        trend: (s.trend_label === 'UP' || s.trend === 1) ? 'UP'
          : (s.trend_label === 'DOWN' || s.trend === -1) ? 'DOWN' : '',
        candle: s.candle_type || '',
        rsi: s.rsi14 ?? null,
        price: s.price ?? null,
        kind: 'signal',
        label,
      },
    });
  }

  for (const trade of trades || []) {
    const long = Number(trade.direction) === 1;
    const setup = setupShort(trade.setup);
    const side = long ? 'LONG' : 'SHORT';
    const sigTime = trade.signal_candle_time || trade.entry_time;
    const entTime = trade.entry_candle_time || trade.entry_time;
    const exTime = trade.exit_time;
    const trend = trade.trend_4h === 'UP' || trade.trend === 1 ? '↑'
      : trade.trend_4h === 'DOWN' || trade.trend === -1 ? '↓' : '';
    const label = `${side} ${setup}${trend ? ` ${trend}` : ''}`;
    put(sigTime, {
      kind: 'signal',
      position: long ? 'belowBar' : 'aboveBar',
      color: long ? '#22c55e' : '#ef4444',
      shape: long ? 'arrowUp' : 'arrowDown',
      tooltip: label,
      data: {
        side,
        setup,
        trend: trade.trend_4h === 'UP' || trade.trend === 1 ? 'UP'
          : trade.trend_4h === 'DOWN' || trade.trend === -1 ? 'DOWN' : '',
        candle: trade.signal_candle_type || trade.candle_type || '',
        rsi: trade.rsi14 ?? null,
        price: trade.entry_price ?? null,
        kind: 'signal',
        label,
      },
    });
    const entUnix = toUnix(entTime);
    const sigUnix = toUnix(sigTime);
    if (entUnix && entUnix !== sigUnix) {
      put(entTime, {
        kind: 'entry',
        position: long ? 'belowBar' : 'aboveBar',
        color: '#38bdf8',
        shape: 'circle',
        tooltip: 'IN · entry fill',
        data: { side, kind: 'entry', label: 'IN · entry fill', price: trade.entry_price ?? null },
      });
    }
    if (exTime) {
      const reason = trade.exit_reason || '';
      put(exTime, {
        kind: 'exit',
        position: long ? 'aboveBar' : 'belowBar',
        color: '#f59e0b',
        shape: 'square',
        tooltip: `OUT${reason ? ` ${reason}` : ''}`.trim(),
        data: {
          side,
          kind: 'exit',
          label: `OUT${reason ? ` ${reason}` : ''}`.trim(),
          price: trade.exit_price ?? null,
          reason,
        },
      });
    }
  }

  return [...byTime.values()]
    .sort((a, b) => a.time - b.time)
    .map(({ kind, ...marker }) => marker);
};

export const defaultSignalRange = (now = new Date()) => {
  const end = new Date(now);
  const start = new Date(end.getTime() - 90 * 24 * 60 * 60 * 1000);
  const iso = (d) => d.toISOString().slice(0, 10);
  return { start: iso(start), end: iso(end) };
};
