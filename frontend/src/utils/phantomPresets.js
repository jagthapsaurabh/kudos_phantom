// Built-in Kudos (PhantomV2) presets and the v3.5 separation / MACD-line
// settings — one source of truth for every strategy dropdown and form.
//
// The backend accepts any `PhantomV2:<setup>[:<direction>]` id (see
// backend/app/core/strategy.py `parse_phantom_variant`); this file lists the
// curated subset shown to the client and the labels used everywhere.

export const PHANTOM_DEFAULT_ID = 'PhantomV2';
export const PHANTOM_DEFAULT_NAME = 'Kudos V2.5 (Default)';
// Named strategy: the tuned champion with condition 3 (MACD hist) required on
// momentum entries too. Plain PhantomV2 keeps the original behaviour.
export const KUDOS_OPTIMIZE_COMPIL_ID = 'PhantomV2:optimize-compulsory';
export const KUDOS_OPTIMIZE_COMPIL_NAME = 'Kudos Optimize Compulsory';
export const FAST_TEST_ID = 'FastTest';
export const FAST_TEST_NAME = 'Fast Test Strategy';
// FastTest V1.0 — the same debug signals, plus the 2H +0.35% validation and
// the touch-based +0.90% profit booking. A separate id: FastTest itself is
// untouched.
export const FAST_TEST_V1_ID = 'FastTestV1';
export const FAST_TEST_V1_NAME = 'Fast Test Strategy V1.0';

export const SETUP_MODES = [
  { value: 'both', label: 'Reversal + Momentum', short: 'Both setups',
    hint: 'Original behaviour: take mean-reversion (Setup A) and trend-continuation (Setup B) entries.' },
  { value: 'reversal', label: 'Reversal only', short: 'Reversal',
    hint: 'Only Setup A — RSI reversal entries in the 4h trend direction. Momentum entries never fire.' },
  { value: 'momentum', label: 'Momentum only', short: 'Momentum',
    hint: 'Only Setup B — MACD zero-cross continuation entries. Reversal entries never fire.' },
];

export const TRADE_DIRECTIONS = [
  { value: 'both', label: 'Long + Short', short: 'Both sides',
    hint: 'Original behaviour: trade both directions.' },
  { value: 'long', label: 'Long only', short: 'Long',
    hint: 'Never open a short. Long entries are exactly the ones the two-sided strategy takes.' },
  { value: 'short', label: 'Short only', short: 'Short',
    hint: 'Never open a long. Short entries are exactly the ones the two-sided strategy takes.' },
];

// MACD line / signal line comparisons (v3.5). 'off' is the default for every
// rule so an existing strategy keeps trading exactly as before.
export const MACD_LINE_RULES = [
  { value: 'off', label: 'Off (not checked)' },
  { value: 'above_below', label: 'Long: above · Short: below' },
  { value: 'cross', label: 'Crossover on the signal candle' },
];

export const MACD_LINE_RULE_KEYS = [
  { key: 'line_vs_signal', label: 'MACD line vs signal line', left: 'MACD line', right: 'signal' },
  { key: 'line_vs_zero', label: 'MACD line vs zero', left: 'MACD line', right: '0' },
  { key: 'signal_vs_zero', label: 'Signal line vs zero', left: 'signal line', right: '0' },
];

// The seven documented entry conditions. Selecting one in Backtest makes it
// an extra gate on every entry (Setup B included). The list is empty by
// default, so an untouched run is exactly the original strategy.
export const COMPULSORY_CONDITIONS = [
  { value: 'trend', label: '1. 4h trend' },
  { value: 'adx', label: '2. ADX' },
  { value: 'macd_hist', label: '3. MACD hist' },
  { value: 'atr_regime', label: '4. ATR regime' },
  { value: 'rsi_trigger', label: '5. RSI trigger' },
  { value: 'candle_color', label: '6. Candle colour' },
  { value: 'macd_confirm', label: '7. MACD confirmation' },
];
export const COMPULSORY_CONDITION_LABELS = Object.fromEntries(
  COMPULSORY_CONDITIONS.map(c => [c.value, c.label]));

export const DEFAULT_MACD_LINE_RULES = {
  enabled: false,
  line_vs_signal: 'off',
  line_vs_zero: 'off',
  signal_vs_zero: 'off',
  line_min: null,
  signal_min: null,
};

const setupLabel = (mode) => (SETUP_MODES.find(m => m.value === mode) || SETUP_MODES[0]);
const directionLabel = (dir) => (TRADE_DIRECTIONS.find(d => d.value === dir) || TRADE_DIRECTIONS[0]);

export const setupModeLabel = (mode) => setupLabel(mode).label;
export const tradeDirectionLabel = (dir) => directionLabel(dir).label;

// Curated presets shown under "Kudos presets" in every strategy dropdown.
// Order: the two most-asked-for splits first, then the finer combinations.
const PRESET_SPECS = [
  ['reversal', 'both'], ['momentum', 'both'],
  ['both', 'long'], ['both', 'short'],
  ['reversal', 'long'], ['reversal', 'short'],
  ['momentum', 'long'], ['momentum', 'short'],
];

export const phantomPresetId = (setup = 'both', direction = 'both') => {
  const parts = [PHANTOM_DEFAULT_ID];
  if (setup && setup !== 'both') parts.push(setup);
  if (direction && direction !== 'both') parts.push(direction);
  return parts.join(':');
};

export const phantomPresetName = (setup = 'both', direction = 'both') => {
  if ((setup || 'both') === 'both' && (direction || 'both') === 'both') return PHANTOM_DEFAULT_NAME;
  const bits = [];
  if (setup !== 'both') bits.push(setupLabel(setup).short);
  if (direction !== 'both') bits.push(`${directionLabel(direction).short} only`);
  if (bits.length === 1 && setup !== 'both') bits[0] = `${bits[0]} only`;
  return `Kudos — ${bits.join(' · ')}`;
};

export const PHANTOM_PRESETS = [
  ...PRESET_SPECS.map(([setup, direction]) => ({
    id: phantomPresetId(setup, direction),
    name: phantomPresetName(setup, direction),
    setup_mode: setup,
    trade_direction: direction,
    setup_label: setupLabel(setup).label,
    direction_label: directionLabel(direction).label,
  })),
  {
    id: KUDOS_OPTIMIZE_COMPIL_ID,
    name: KUDOS_OPTIMIZE_COMPIL_NAME,
    setup_mode: 'both',
    trade_direction: 'both',
    setup_label: setupLabel('both').label,
    direction_label: directionLabel('both').label,
    compulsory_conditions: ['macd_hist'],
    compulsory_label: COMPULSORY_CONDITION_LABELS.macd_hist,
  },
];

// `PhantomV2:reversal:long` -> { setup_mode, trade_direction } | null.
// Mirrors backend parse_phantom_variant (separators : - . /, 1–2 tokens).
export const parsePhantomVariant = (strategyId) => {
  if (strategyId === null || strategyId === undefined) return null;
  const text = String(strategyId).trim();
  const lower = text.toLowerCase();
  if (lower === KUDOS_OPTIMIZE_COMPIL_ID.toLowerCase() || lower === 'kudosoptimizecompulsory') {
    return { setup_mode: 'both', trade_direction: 'both', compulsory_conditions: ['macd_hist'] };
  }
  if (!lower.startsWith(PHANTOM_DEFAULT_ID.toLowerCase())) return null;
  const rest = text.slice(PHANTOM_DEFAULT_ID.length);
  if (!rest) return { setup_mode: 'both', trade_direction: 'both' };
  if (!/^[:\-./]/.test(rest)) return null;
  const tokens = rest.slice(1).split(/[:\-./]/).map(t => t.trim().toLowerCase()).filter(Boolean);
  if (tokens.length === 0 || tokens.length > 2) return null;
  let setup = 'both';
  let direction = 'both';
  for (const tok of tokens) {
    if (tok === 'reversal' || tok === 'momentum') {
      if (setup !== 'both') return null;
      setup = tok;
    } else if (tok === 'long' || tok === 'short') {
      if (direction !== 'both') return null;
      direction = tok;
    } else {
      return null;
    }
  }
  return { setup_mode: setup, trade_direction: direction };
};

// True for the champion AND its presets (all run the built-in engine config).
export const isPhantomBuiltin = (strategyId) => parsePhantomVariant(strategyId) !== null;
// True only for the narrowed presets (never for plain `PhantomV2`).
export const isPhantomPreset = (strategyId) => {
  const v = parsePhantomVariant(strategyId);
  return !!v && (v.setup_mode !== 'both' || v.trade_direction !== 'both'
    || (v.compulsory_conditions && v.compulsory_conditions.length > 0));
};

// True for the FastTestV1 debug strategy (and nothing else). Mirrors the
// backend's `is_fast_test_v1`.
export const isFastTestV1 = (strategyId) =>
  String(strategyId ?? '').trim().toLowerCase() === FAST_TEST_V1_ID.toLowerCase();

// Display name for a built-in id; null for saved / custom strategies so the
// caller can fall back to its own lookup.
export const builtinStrategyName = (strategyId) => {
  if (String(strategyId) === FAST_TEST_ID) return FAST_TEST_NAME;
  if (isFastTestV1(strategyId)) return FAST_TEST_V1_NAME;
  const text = String(strategyId ?? '').trim().toLowerCase();
  if (text === KUDOS_OPTIMIZE_COMPIL_ID.toLowerCase() || text === 'kudosoptimizecompulsory') {
    return KUDOS_OPTIMIZE_COMPIL_NAME;
  }
  const v = parsePhantomVariant(strategyId);
  return v ? phantomPresetName(v.setup_mode, v.trade_direction) : null;
};

// Name resolution used by the pages: saved strategy name -> built-in name -> id.
export const strategyDisplayName = (strategyId, strategies = []) => {
  const found = (strategies || []).find(s => String(s.id) === String(strategyId));
  if (found && found.name) return found.name;
  return builtinStrategyName(strategyId) || String(strategyId ?? '');
};

// Text of the active MACD line / signal rules for one side, mirroring
// PhantomV2Config.macd_line_rule_text_for on the backend ('off' when none).
export const macdLineRuleText = (params, direction) => {
  const rules = params?.macd_line_rules || {};
  if (!rules.enabled) return 'off';
  const isLong = direction === 1;
  const side = isLong ? 'long' : 'short';
  const ec = params?.entry_conditions || {};
  const branch = ec.use_direction_macd_line ? (ec[side] || {}) : {};
  const parts = [];
  for (const spec of MACD_LINE_RULE_KEYS) {
    const rule = branch[`macd_${spec.key}`] || rules[spec.key] || 'off';
    if (rule === 'off') continue;
    if (rule === 'cross') parts.push(`${spec.left} crosses ${isLong ? 'above' : 'below'} ${spec.right}`);
    else parts.push(`${spec.left} ${isLong ? '>' : '<'} ${spec.right}`);
  }
  const level = (branchKey, sharedKey) => {
    const own = branch[branchKey];
    if (own !== null && own !== undefined && own !== '') return Number(own);
    const shared = rules[sharedKey];
    if (shared === null || shared === undefined || shared === '') return null;
    const mag = Math.abs(Number(shared));
    return isLong ? mag : -mag;
  };
  const lineMin = level('macd_line_min', 'line_min');
  if (lineMin !== null && !Number.isNaN(lineMin)) parts.push(`MACD line ${isLong ? '≥' : '≤'} ${lineMin}`);
  const sigMin = level('macd_signal_min', 'signal_min');
  if (sigMin !== null && !Number.isNaN(sigMin)) parts.push(`signal line ${isLong ? '≥' : '≤'} ${sigMin}`);
  return parts.length ? parts.join('; ') : 'off';
};

export const macdLineRulesActive = (params) =>
  macdLineRuleText(params, 1) !== 'off' || macdLineRuleText(params, -1) !== 'off';
