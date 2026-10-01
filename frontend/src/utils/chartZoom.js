// v3.6 — Market Chart zoom helpers.
//
// Pure functions, so the zoom maths is testable without a chart instance or a
// DOM. The page keeps two independent zooms:
//
//   * horizontal — the visible logical range of bars (what the mouse wheel
//     already changes; the +/- buttons use the same range),
//   * vertical   — a factor applied to the automatic price fit (1 = auto fit,
//     < 1 tighter around the middle, > 1 wider).
//
// Both buttons and the keyboard shortcuts call these helpers, so the tooltips,
// the buttons and the shortcuts can never drift apart.

export const ZOOM_IN_FACTOR = 0.65;               // fewer bars / tighter price range
export const ZOOM_OUT_FACTOR = 1 / ZOOM_IN_FACTOR; // exactly undoes one zoom-in
export const MIN_VISIBLE_BARS = 5;                 // never zoom past a handful of candles
export const MAX_VISIBLE_BARS = 3000;              // "Reset" (fit content) is the way to see all
export const PRICE_ZOOM_MIN = 0.3;                 // tightest vertical zoom-in
export const PRICE_ZOOM_MAX = 8;                   // widest vertical zoom-out

// Keyboard shortcuts, matching the button tooltips.
export const ZOOM_KEYS = {
  in: ['+', '=', 'Add'],
  out: ['-', '_', 'Subtract'],
  reset: ['0', 'Home'],
};

export const clampPriceZoom = (value, lo = PRICE_ZOOM_MIN, hi = PRICE_ZOOM_MAX) => {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return 1;
  return Math.min(hi, Math.max(lo, n));
};

// Scale a visible logical range (bar indices, possibly fractional) around its
// centre. `factor` < 1 zooms in, > 1 zooms out, 1 is a no-op.
export const zoomLogicalRange = (range, factor, minBars = MIN_VISIBLE_BARS, maxBars = MAX_VISIBLE_BARS) => {
  if (!range) return null;
  const from = Number(range.from);
  const to = Number(range.to);
  const f = Number(factor);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return null;
  if (!Number.isFinite(f) || f <= 0) return null;
  const span = to - from;
  if (!(span > 0)) return null;
  const centre = (from + to) / 2;
  const next = Math.min(maxBars, Math.max(minBars, span * f));
  return { from: centre - next / 2, to: centre + next / 2 };
};

// Scale a price range around its middle. Accepts the {from,to} shape the price
// scale returns and the {minValue,maxValue} shape the autoscale provider uses.
export const zoomPriceRange = (range, factor) => {
  if (!range) return null;
  const from = Number(range.from !== undefined ? range.from : range.minValue);
  const to = Number(range.to !== undefined ? range.to : range.maxValue);
  const f = Number(factor);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return null;
  if (!Number.isFinite(f) || f <= 0) return null;
  const mid = (from + to) / 2;
  const half = Math.max(((to - from) / 2) * f, 1e-9);
  return { from: mid - half, to: mid + half };
};

export const stepPriceZoom = (current, factor) => clampPriceZoom(clampPriceZoom(current) * Number(factor || 1));

// Human label for the current vertical zoom, shown next to the buttons.
export const zoomLabel = (factor) => {
  const z = clampPriceZoom(factor);
  if (Math.abs(z - 1) < 1e-9) return 'auto fit';
  return z < 1 ? `${(1 / z).toFixed(2)}× in` : `${z.toFixed(2)}× out`;
};

// Which zoom action a keydown means (null = not a zoom shortcut).
export const zoomActionForKey = (key) => {
  if (ZOOM_KEYS.in.includes(key)) return 'in';
  if (ZOOM_KEYS.out.includes(key)) return 'out';
  if (ZOOM_KEYS.reset.includes(key)) return 'reset';
  return null;
};

// True when the key event came from a field the user is typing in, so the
// shortcuts stay out of the way.
export const isTypingTarget = (el) => (
  !!el && (el.isContentEditable === true
    || ['INPUT', 'SELECT', 'TEXTAREA'].includes(String(el.tagName || '').toUpperCase()))
);
