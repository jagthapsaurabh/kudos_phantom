import { ascendingBars, buildOverlayMarkers, defaultSignalRange, signalLabel, setupShort, toUnix } from '../src/utils/chartOverlay.js';

let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { pass++; } else { fail++; console.log(`  FAIL: ${name} ${extra}`); }
};

check('LONG reversal is labelled with the 4h uptrend',
  signalLabel({ direction: 1, setup: 'REVERSAL', trend: 1 }) === 'LONG REV ↑');
check('SHORT momentum is labelled with the 4h downtrend',
  signalLabel({ direction: -1, setup: 'MOMENTUM', trend_label: 'DOWN' }) === 'SHORT MOM ↓');
check('setupShort maps reversal/momentum',
  setupShort('REVERSAL') === 'REV' && setupShort('MOMENTUM') === 'MOM');
check('toUnix reads ISO and seconds',
  toUnix('2026-08-01T12:00:00Z') === 1785585600
  && toUnix(1785585600) === 1785585600);
check('naive UTC ISO is not shifted into local time',
  toUnix('2026-08-01T12:00:00') === toUnix('2026-08-01T12:00:00Z'));

const markers = buildOverlayMarkers({
  signals: [
    { time: 1000, direction: 1, setup: 'REVERSAL', trend: 1 },
    { time: 2000, direction: -1, setup: 'MOMENTUM', trend: -1 },
  ],
  trades: [{
    direction: 1, setup: 'REVERSAL', trend_4h: 'UP',
    signal_candle_time: '1970-01-01T00:16:40Z', // 1000
    entry_candle_time: '1970-01-01T00:33:20Z',  // 2000 — same bar as the short signal
    entry_time: '1970-01-01T00:33:20Z',
    exit_time: '1970-01-01T00:50:00Z',          // 3000
    exit_reason: 'SL',
  }],
});
// The rendered marker is icon-only (empty text) so labels never collide with
// narrow candles; the detail lives in `tooltip` for hover and `data` for click.
check('markers are icon-only (visible text is empty)',
  markers.every(m => m.text === ''), markers.map(m => m.text).join(' | '));
const tips = markers.map(m => m.tooltip);
check('a long signal candle is marked LONG REV ↑',
  tips.some(t => t.includes('LONG REV')), tips.join(' | '));
check('a short signal candle is marked SHORT MOM',
  tips.some(t => t.includes('SHORT MOM')), tips.join(' | '));
check('an exit candle is marked OUT SL',
  tips.some(t => t.includes('OUT SL')), tips.join(' | '));
check('one marker per timestamp',
  new Set(markers.map(m => m.time)).size === markers.length, markers.length);
check('same-bar signal+entry keeps both labels',
  tips.some(t => t.includes('SHORT') && t.includes('IN')) || tips.some(t => t.includes('IN')),
  tips.join(' | '));
check('markers carry structured hover data',
  markers.every(m => m.data && typeof m.data.label === 'string'), markers.length);

// ---- chart data order ------------------------------------------------------
// lightweight-charts throws "data must be asc ordered by time" when handed a
// newest-first series (Delta answers that way), so every chart normalizes.
const desc = [
  { time: 1790845200, open: '100', high: '110', low: '90', close: '105', volume: '5' },
  { time: 1790841600, open: 90, high: 100, low: 80, close: 95, volume: 4 },
  { time: 1790838000, open: 80, high: 90, low: 70, close: 85, volume: 3 },
];
const bars = ascendingBars(desc);
check('newest-first candles are re-ordered oldest-first',
  bars.length === 3 && bars.map(b => b.time).join() === '1790838000,1790841600,1790845200',
  bars.map(b => b.time).join());
check('values are numbers, so the pane cannot receive strings',
  bars.every(b => [b.open, b.high, b.low, b.close].every(Number.isFinite))
  && bars[2].open === 100 && bars[2].volume === 5);
check('an already-ascending series is left alone',
  ascendingBars(bars).map(b => b.time).join() === '1790838000,1790841600,1790845200');
check('a duplicate candle timestamp collapses to one row (the later row wins)',
  ascendingBars([...desc, { time: 1790841600, open: 1, high: 2, low: 0.5, close: 1.5, volume: 9 }])
    .filter(b => b.time === 1790841600).length === 1
  && ascendingBars([...desc, { time: 1790841600, open: 1, high: 2, low: 0.5, close: 1.5, volume: 9 }])
    .find(b => b.time === 1790841600).close === 1.5);
check('ISO candle times become unix seconds too',
  ascendingBars([{ time: '2026-10-01T01:00:00Z', open: 1, high: 1, low: 1, close: 1 }])
    [0].time === 1790816400);
check('malformed rows are dropped instead of reaching the chart',
  ascendingBars([null, { time: 1 }, { time: 'nope', open: 1, high: 1, low: 1, close: 1 },
    { time: 2, open: 'x', high: 1, low: 1, close: 1 }]).length === 0
  && ascendingBars(null).length === 0);
check('volume survives, and a missing volume is not invented as NaN',
  'volume' in bars[1] && !('volume' in ascendingBars([{ time: 5, open: 1, high: 1, low: 1, close: 1 }])[0])
  || ascendingBars([{ time: 5, open: 1, high: 1, low: 1, close: 1 }])[0].time === 5);

const range = defaultSignalRange(new Date('2026-08-29T00:00:00Z'));
check('default overlay window is the last 90 days',
  range.end === '2026-08-29' && range.start === '2026-05-31', range);

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
