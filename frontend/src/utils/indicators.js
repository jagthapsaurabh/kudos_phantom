// Lightweight technical-indicator helpers for the market chart.
// These mirror the backend `indicators.py` so on-screen values line up with
// the strategy engine.

export const ema = (values, period) => {
  const out = new Array(values.length);
  const alpha = 2 / (period + 1);
  out[0] = values[0];
  for (let i = 1; i < values.length; i++) {
    out[i] = alpha * values[i] + (1 - alpha) * out[i - 1];
  }
  return out;
};

export const sma = (values, period) => {
  const out = new Array(values.length).fill(0);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    out[i] = i >= period - 1 ? sum / period : null;
  }
  return out;
};

export const rsi = (closes, period = 14) => {
  const out = new Array(closes.length).fill(null);
  if (closes.length < period + 1) return out;
  const gains = [];
  const losses = [];
  for (let i = 1; i < closes.length; i++) {
    const delta = closes[i] - closes[i - 1];
    gains.push(delta > 0 ? delta : 0);
    losses.push(delta < 0 ? -delta : 0);
  }
  const avgGain = ema(gains, period);
  const avgLoss = ema(losses, period);
  // out[0] is null (no previous bar), bars line up with closes index
  for (let i = 0; i < closes.length; i++) {
    if (i === 0) { out[i] = null; continue; }
    const g = avgGain[i - 1];
    const l = avgLoss[i - 1];
    const denom = l < 1e-10 ? 1e-10 : l;
    out[i] = 100 - 100 / (1 + g / denom);
  }
  return out;
};

export const macd = (closes, fast = 12, slow = 26, signalPeriod = 9) => {
  const emaFast = ema(closes, fast);
  const emaSlow = ema(closes, slow);
  const macdLine = closes.map((_, i) => emaFast[i] - emaSlow[i]);
  const signalLine = ema(macdLine, signalPeriod);
  const hist = macdLine.map((v, i) => v - signalLine[i]);
  return { macdLine, signalLine, histogram: hist };
};

/**
 * Build all indicator series (indexed like closes) using the cached candle
 * data. Returns { ema20, ema50, sma50, rsi14, macd: {...} }.
 */
export const computeAll = (closes, opts = {}) => {
  const ema20 = opts.ema20 ? ema(closes, 20) : null;
  const ema50 = opts.ema50 ? ema(closes, 50) : null;
  const sma50 = opts.sma50 ? sma(closes, 50) : null;
  const rsi14 = opts.rsi ? rsi(closes, 14) : null;
  const macdData = opts.macd ? macd(closes, 12, 26, 9) : null;
  return { ema20, ema50, sma50, rsi14, macd: macdData };
};
