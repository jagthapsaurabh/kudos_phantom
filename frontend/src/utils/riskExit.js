// v3.6 — Risk & Exit model: every protective level is measured either in ATR
// units (the original Phantom behaviour, and still the default) or as a
// percentage of price. The choice is PER LEVEL, so a strategy can run an ATR
// stop with a price-based target; ``model`` is the convenience selector:
//
//   'atr'   → every level on ATR (default — nothing changes)
//   'price' → every level on % of price
//   'both'  → per-level mix
//
// This file is the single front-end source of truth: the Backtest form, the
// Kudos Strategy docs tabs and the Paper / Live strategy summary all read the
// labels, the defaults and the text builder from here. It mirrors
// ``RiskExitModel`` in backend/app/core/strategy.py.

export const RISK_EXIT_ATR = 'atr';
export const RISK_EXIT_PRICE = 'price';

export const RISK_EXIT_MODELS = [
  {
    value: RISK_EXIT_ATR,
    label: 'ATR-based (default)',
    hint: 'Every level in ATR units — exactly the behaviour that shipped before. Stop adapts to volatility.',
  },
  {
    value: RISK_EXIT_PRICE,
    label: 'Price-based (%)',
    hint: 'Every level as a percentage of the entry price. Independent of volatility.',
  },
  {
    value: 'both',
    label: 'Both — per level',
    hint: 'Pick ATR or Price for each level separately — e.g. an ATR stop with a price-based target.',
  },
];

// One row per protective level. `atrField` / `pctField` are the keys the
// backend reads (top level of the params for the ATR values, inside
// `risk_exit` for the percentages). The trailing stop has two numbers: the
// activation level and the distance that follows the peak.
export const RISK_EXIT_LEVELS = [
  {
    key: 'stop', label: 'Stop loss', pin: 'risk-mode-stop',
    atrField: 'stop_loss_atr', atrLabel: 'Stop loss (ATR)', pctField: 'stop_loss_pct',
    distance: null,
  },
  {
    key: 'target', label: 'Take profit', pin: 'risk-mode-target',
    atrField: 'take_profit_atr', atrLabel: 'Take profit (ATR)', pctField: 'take_profit_pct',
    distance: null,
  },
  {
    key: 'trail', label: 'Trailing stop', pin: 'risk-mode-trail',
    atrField: 'trail_activation_atr', atrLabel: 'Trail activation (ATR)', pctField: 'trail_activation_pct',
    distance: {
      atrField: 'trail_distance_atr', atrLabel: 'Trail distance (ATR)', pctField: 'trail_distance_pct',
    },
  },
  {
    key: 'breakeven', label: 'Breakeven stop', pin: 'risk-mode-breakeven',
    atrField: 'breakeven_atr', atrLabel: 'Breakeven after (ATR)', pctField: 'breakeven_pct',
    distance: null,
  },
];

// Percentages are stored as fractions (0.016 = 1.6%), exactly like
// sl_floor_pct / margin_pct on the backend. The form shows percent numbers.
export const DEFAULT_RISK_EXIT = {
  model: RISK_EXIT_ATR,
  stop_mode: RISK_EXIT_ATR,
  target_mode: RISK_EXIT_ATR,
  trail_mode: RISK_EXIT_ATR,
  breakeven_mode: RISK_EXIT_ATR,
  stop_loss_pct: 0.016,
  take_profit_pct: 0.03,
  trail_activation_pct: 0.015,
  trail_distance_pct: 0.005,
  breakeven_pct: 0.01,
};

// Labels / hints consumed by the Backtest form's parameter meta map.
export const RISK_EXIT_META = {
  stop_loss_pct: { label: 'Stop loss (%)', hint: 'Distance of the stop from entry, in % of the entry price (1.6 = 1.6%).' },
  take_profit_pct: { label: 'Take profit (%)', hint: 'Distance of the profit target from entry, in % of the entry price.' },
  trail_activation_pct: { label: 'Trail activation (%)', hint: 'Start trailing the stop after this much profit (% of entry).' },
  trail_distance_pct: { label: 'Trail distance (%)', hint: 'The trail follows the peak by this percentage.' },
  breakeven_pct: { label: 'Breakeven after (%)', hint: 'Move the stop to entry once profit reaches this % of entry. 0 = off.' },
};

export const normalizeRiskExit = (value) => ({
  ...DEFAULT_RISK_EXIT,
  ...(value && typeof value === 'object' && !Array.isArray(value) ? value : {}),
});

const LEVEL_KEYS = ['stop', 'target', 'trail', 'breakeven'];
const modeKey = (level) => `${level}_mode`;

/** The selector value for one level ('atr' | 'price'). */
export const riskExitModeFor = (params, level) => {
  const rx = normalizeRiskExit(params && params.risk_exit);
  const mode = rx[modeKey(level)];
  return mode === RISK_EXIT_PRICE ? RISK_EXIT_PRICE : RISK_EXIT_ATR;
};

/** The model selector value ('atr' | 'price' | 'both'). */
export const riskExitModelFor = (params) => normalizeRiskExit(params && params.risk_exit).model;

/** Pure helper: switch the whole model, returning the new ``risk_exit`` block. */
export const applyRiskExitModel = (riskExit, model) => {
  const next = normalizeRiskExit(riskExit);
  next.model = RISK_EXIT_MODELS.some((m) => m.value === model) ? model : RISK_EXIT_ATR;
  if (next.model !== 'both') {
    LEVEL_KEYS.forEach((level) => { next[modeKey(level)] = next.model; });
  }
  return next;
};

/**
 * Pure helper: switch ONE level between ATR and price.
 *
 * ``model`` is recomputed from the four selectors, so it always reports the
 * truth: all-ATR, all-price, or the per-level mix ('both').
 */
export const setRiskExitLevelMode = (riskExit, level, mode) => {
  const next = normalizeRiskExit(riskExit);
  next[modeKey(level)] = mode === RISK_EXIT_PRICE ? RISK_EXIT_PRICE : RISK_EXIT_ATR;
  const modes = LEVEL_KEYS.map((k) => next[modeKey(k)]);
  next.model = modes.every((m) => m === RISK_EXIT_ATR) ? RISK_EXIT_ATR
    : modes.every((m) => m === RISK_EXIT_PRICE) ? RISK_EXIT_PRICE : 'both';
  return next;
};

export const riskExitModelLabel = (model) =>
  (RISK_EXIT_MODELS.find((m) => m.value === model) || RISK_EXIT_MODELS[0]).label;

const fmt = (v) => `${Math.round(Number(v) * 1000) / 1000}`;

/** One level as the client reads it, e.g. ``1.2×ATR`` or ``1.6% price``. */
export const riskExitLevelText = (params, level) => {
  const def = RISK_EXIT_LEVELS.find((l) => l.key === level);
  const p = (params && typeof params === 'object') ? params : {};
  const rx = normalizeRiskExit(p.risk_exit);
  if (def && riskExitModeFor(p, level) === RISK_EXIT_PRICE) {
    return `${fmt(rx[def.pctField] * 100)}% price`;
  }
  return `${fmt(p[def ? def.atrField : `${level}_atr`] ?? 0)}×ATR`;
};

/** The whole model on one line, for the docs page and the strategy summary. */
export const riskExitText = (params) => {
  const p = (params && typeof params === 'object') ? params : {};
  const rx = normalizeRiskExit(p.risk_exit);
  const parts = [
    `Stop ${riskExitLevelText(p, 'stop')}`,
    `TP ${riskExitLevelText(p, 'target')}`,
  ];
  if (riskExitModeFor(p, 'trail') === RISK_EXIT_PRICE) {
    parts.push(`Trail ${fmt(rx.trail_activation_pct * 100)}% → ${fmt(rx.trail_distance_pct * 100)}%`);
  } else {
    parts.push(`Trail ${fmt(p.trail_activation_atr ?? 0)}×ATR → ${fmt(p.trail_distance_atr ?? 0)}×ATR`);
  }
  const be = riskExitLevelText(p, 'breakeven');
  parts.push(be === '0×ATR' ? 'BE off' : `BE ${be}`);
  return parts.join(' · ');
};
