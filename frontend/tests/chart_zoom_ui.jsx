// v3.6 — Market Chart zoom + full screen.
//
// Two halves: the zoom maths is pure (src/utils/chartZoom.js) and is checked
// directly; the toolbar the user actually sees is rendered with react-dom/server
// (the Chart page already renders headless in pages_smoke.jsx), and the wiring
// that only exists at runtime (chart API calls, keyboard, fullscreen fallback)
// is pinned by reading the page source.
import React from 'react';
import { renderToString } from 'react-dom/server';
import { readFileSync } from 'node:fs';
import ChartPage from '../src/pages/Chart.jsx';
import {
  ZOOM_IN_FACTOR, ZOOM_OUT_FACTOR, MIN_VISIBLE_BARS, MAX_VISIBLE_BARS,
  PRICE_ZOOM_MIN, PRICE_ZOOM_MAX, ZOOM_KEYS,
  clampPriceZoom, stepPriceZoom, zoomLabel, zoomLogicalRange, zoomPriceRange,
  zoomActionForKey, isTypingTarget,
} from '../src/utils/chartZoom.js';

let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { pass++; } else { fail++; console.log(`  FAIL: ${name} ${extra}`); }
};

// --- horizontal (time) zoom ------------------------------------------------
const base = { from: 100, to: 200 };           // 100 bars visible
const zin = zoomLogicalRange(base, ZOOM_IN_FACTOR);
const zout = zoomLogicalRange(base, ZOOM_OUT_FACTOR);
check('zoom in keeps the centre and shrinks the span',
  Math.abs((zin.from + zin.to) / 2 - 150) < 1e-9 && Math.abs((zin.to - zin.from) - 65) < 1e-9,
  JSON.stringify(zin));
check('zoom out keeps the centre and grows the span',
  Math.abs((zout.from + zout.to) / 2 - 150) < 1e-9 && Math.abs((zout.to - zout.from) - 100 / ZOOM_IN_FACTOR) < 1e-9,
  JSON.stringify(zout));
check('zoom out exactly undoes zoom in',
  Math.abs((ZOOM_OUT_FACTOR * ZOOM_IN_FACTOR) - 1) < 1e-12);
check('zooming in never goes below the minimum bar count',
  zoomLogicalRange({ from: 0, to: 6 }, 0.65).to - zoomLogicalRange({ from: 0, to: 6 }, 0.65).from === MIN_VISIBLE_BARS,
  JSON.stringify(zoomLogicalRange({ from: 0, to: 6 }, 0.65)));
check('zooming out is capped so Reset (fit content) is the full view',
  Math.abs((zoomLogicalRange({ from: 0, to: 2000 }, 4).to - zoomLogicalRange({ from: 0, to: 2000 }, 4).from) - MAX_VISIBLE_BARS) < 1e-9);
check('fractional bar indices survive a zoom',
  Math.abs((zoomLogicalRange({ from: 10.5, to: 17.5 }, 0.5).from + zoomLogicalRange({ from: 10.5, to: 17.5 }, 0.5).to) / 2 - 14) < 1e-9
  && zoomLogicalRange({ from: 10.5, to: 17.5 }, 0.5).from % 1 !== 0);
check('no range (chart not ready) zooms to null, never throws',
  zoomLogicalRange(null, 0.65) === null && zoomLogicalRange(undefined, 0.65) === null);
check('a degenerate or invalid range is refused',
  zoomLogicalRange({ from: 5, to: 5 }, 0.65) === null
  && zoomLogicalRange({ from: 0, to: 10 }, 0) === null
  && zoomLogicalRange({ from: 0, to: 10 }, NaN) === null);

// --- vertical (price) zoom -------------------------------------------------
const px = { from: 98000, to: 102000 };
const pin = zoomPriceRange(px, 0.5);
check('price zoom in halves the range around the middle',
  Math.abs((pin.from + pin.to) / 2 - 100000) < 1e-6
  && Math.abs((pin.to - pin.from) - 2000) < 1e-6, JSON.stringify(pin));
check('price zoom out widens it around the middle',
  Math.abs((zoomPriceRange(px, 2).to - zoomPriceRange(px, 2).from) - 8000) < 1e-6);
check('factor 1 leaves the auto range alone',
  zoomPriceRange(px, 1).from === px.from && zoomPriceRange(px, 1).to === px.to);
check('the autoscale minValue/maxValue shape is accepted too',
  Math.abs(zoomPriceRange({ minValue: 10, maxValue: 20 }, 0.5).to - 17.5) < 1e-9);
check('a missing price range zooms to null',
  zoomPriceRange(null, 0.5) === null && zoomPriceRange({ from: 'x', to: 1 }, 0.5) === null);
check('the price range never collapses to zero height',
  (zoomPriceRange({ from: 100, to: 100 }, 0.5).to - zoomPriceRange({ from: 100, to: 100 }, 0.5).from) > 0);

// --- accumulated zoom / label / shortcuts ----------------------------------
check('clampPriceZoom honours the limits',
  clampPriceZoom(0.0001) === PRICE_ZOOM_MIN && clampPriceZoom(999) === PRICE_ZOOM_MAX);
check('an unusable stored zoom falls back to auto fit',
  clampPriceZoom(undefined) === 1 && clampPriceZoom(0) === 1 && clampPriceZoom('x') === 1);
check('repeated zoom-ins stop at the tightest limit',
  stepPriceZoom(stepPriceZoom(stepPriceZoom(0.5, ZOOM_IN_FACTOR), ZOOM_IN_FACTOR), ZOOM_IN_FACTOR) >= PRICE_ZOOM_MIN);
let z = 1;
for (let i = 0; i < 40; i++) z = stepPriceZoom(z, ZOOM_IN_FACTOR);
check('forty zoom-ins cannot go past the minimum', z === PRICE_ZOOM_MIN, String(z));
check('zoomLabel names auto fit and both directions',
  zoomLabel(1) === 'auto fit' && zoomLabel(0.5) === '2.00× in' && zoomLabel(2) === '2.00× out',
  `${zoomLabel(1)} / ${zoomLabel(0.5)} / ${zoomLabel(2)}`);
check('shortcut keys map to zoom actions',
  zoomActionForKey('+') === 'in' && zoomActionForKey('=') === 'in'
  && zoomActionForKey('-') === 'out' && zoomActionForKey('_') === 'out'
  && zoomActionForKey('0') === 'reset' && zoomActionForKey('a') === null);
check('the advertised keys are the keys that work',
  ZOOM_KEYS.in.includes('+') && ZOOM_KEYS.out.includes('-') && ZOOM_KEYS.reset.includes('0'));
check('shortcuts are ignored while typing in a field',
  isTypingTarget({ tagName: 'INPUT' }) && isTypingTarget({ tagName: 'select' })
  && isTypingTarget({ tagName: 'TEXTAREA' }) && isTypingTarget({ isContentEditable: true }));
check('shortcuts still fire over the chart and buttons',
  !isTypingTarget({ tagName: 'DIV' }) && !isTypingTarget({ tagName: 'BUTTON' }) && !isTypingTarget(null));

// --- the toolbar the user sees --------------------------------------------
let html = '';
try {
  html = renderToString(React.createElement(ChartPage));
} catch (e) {
  check('Chart page renders headless', false, String(e && e.message));
}
check('Market Chart renders', html.includes('Market Chart'));
check('the toolbar has a zoom control group',
  html.includes('chart-zoom-group') && html.includes('chart-zoom-in')
  && html.includes('chart-zoom-out') && html.includes('chart-zoom-reset'));
check('the zoom group starts on the automatic price fit',
  html.includes('chart-zoom-label') && html.includes('auto fit'));
check('the buttons advertise the +/-/0 shortcuts',
  html.includes('Zoom in (+)') && html.includes('Zoom out (-)') && html.includes('Reset zoom (0)'));
check('the full screen button is present and labelled',
  html.includes('chart-fullscreen') && html.includes('Full screen'));
check('the chart explains wheel / drag / double-click',
  html.includes('wheel to zoom') && html.includes('drag to pan') && html.includes('double-click resets'));

// --- wiring that only exists at runtime -----------------------------------
// esbuild bundles this file to CJS, so the source path is resolved from the
// runner's working directory — frontend/ under npm test.
const readSource = (rel) => {
  for (const base of ['', 'frontend/']) {
    try { return readFileSync(`${base}${rel}`, 'utf8'); } catch { /* next */ }
  }
  return '';
};
const src = readSource('src/pages/Chart.jsx');
check('the page uses the shared zoom helpers', src.includes("from '../utils/chartZoom'"));
check('horizontal zoom scales the visible bar range (same range as the wheel)',
  src.includes('getVisibleLogicalRange()') && src.includes('setVisibleLogicalRange(next)'));
check('vertical zoom rides on the automatic price fit (autoscale provider)',
  src.includes('autoscaleInfoProvider: makePriceZoomProvider()')
  && src.includes('zoomPriceRange(res.priceRange, factor)')
  && src.includes('applyOptions({ autoscaleInfoProvider: makePriceZoomProvider() })'));
check('the buttons always win over a manually dragged price axis',
  src.includes("priceScale('right').setAutoScale(true)"));
check('Reset also refits the price axis (factor 1 skips the provider)',
  src.includes('if (!res || !res.priceRange || factor === 1) return res'));
check('Reset refits all candles', src.includes('timeScale().fitContent()'));
check('double-click resets the zoom', src.includes('subscribeDblClick') && src.includes('unsubscribeDblClick'));
check('keyboard shortcuts are registered and guarded',
  src.includes("window.addEventListener('keydown', onKey)") && src.includes('isTypingTarget(e.target)'));
check('fullscreen falls back to the overlay when the API is blocked',
  src.includes('requestFullscreen') && src.includes('.catch(() => setFullscreen(true))'));
check('Escape leaves the overlay fullscreen', src.includes("e.key === 'Escape'") && src.includes('!document.fullscreenElement'));
check('the page behind the overlay cannot scroll',
  src.includes("document.body.style.overflow = 'hidden'"));
check('leaving fullscreen restores the real label',
  html.includes('Full screen') && src.includes("'Exit full'"));

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
