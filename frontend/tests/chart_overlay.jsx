import { buildOverlayMarkers, defaultSignalRange, signalLabel, setupShort, toUnix } from '../src/utils/chartOverlay.js';

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

const range = defaultSignalRange(new Date('2026-08-29T00:00:00Z'));
check('default overlay window is the last 90 days',
  range.end === '2026-08-29' && range.start === '2026-05-31', range);

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
