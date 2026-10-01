from pydantic import BaseModel, Field, field_validator, model_validator
from dataclasses import dataclass
import pandas as pd
import numpy as np
import os
from typing import Optional
from dotenv import load_dotenv
from .indicators import compute_indicators, sma, macd as _macd
from .trading_windows import TradingWindowConfig

load_dotenv()

# ---------------------------------------------------------------------------
# ATR volatility-regime comparison
# ---------------------------------------------------------------------------
# The client can choose how each side compares ATR with its 50-bar average:
# greater than (">"), less than ("<"), greater-or-equal (">=") or
# less-or-equal ("<="). ">=" is the original Phantom behaviour and stays the
# default whenever no operator is configured, so existing runs and saved
# strategies keep producing exactly the same signals.
ATR_REGIME_OPS = ('>=', '<=', '>', '<')
DEFAULT_ATR_REGIME_OP = '>='
_ATR_OP_FUNCS = {
    '>=': np.greater_equal,
    '<=': np.less_equal,
    '>': np.greater,
    '<': np.less,
}
# Friendly labels used by the UI / preview so the rule reads like maths.
ATR_REGIME_OP_LABELS = {'>=': '≥', '<=': '≤', '>': '>', '<': '<'}


def normalize_atr_regime_op(value):
    """Validate/normalise an ATR comparison operator.

    ``None``/blank means "not configured" and resolves to the default ">=".
    Accepts the unicode forms (≥ / ≤) too, because those are what the UI shows.
    """
    if value is None:
        return DEFAULT_ATR_REGIME_OP
    op = str(value).strip()
    if not op:
        return DEFAULT_ATR_REGIME_OP
    op = op.replace('=>', '>=').replace('=<', '<=').replace('≥', '>=').replace('≤', '<=')
    if op not in ATR_REGIME_OPS:
        raise ValueError(
            f"atr_regime_op must be one of {', '.join(ATR_REGIME_OPS)} (got '{value}')")
    return op


# ---------------------------------------------------------------------------
# v3.5 — setup / direction separation
# ---------------------------------------------------------------------------
# ``setup_mode`` chooses WHICH entry setups may fire and ``trade_direction``
# chooses WHICH sides may fire. Both default to ``'both'``, which is exactly
# the behaviour every existing run / saved strategy / paper / live instance
# already has, so nothing changes until a client picks another value.
#
#   setup_mode       both      -> Setup A always, Setup B when
#                                 ``enable_momentum_entry`` is ON (legacy)
#                    reversal  -> Setup A (RSI reversal) only
#                    momentum  -> Setup B (momentum continuation) only — the
#                                 momentum checkbox is treated as ON so a
#                                 "momentum only" strategy can never be empty
#   trade_direction  both / long / short
SETUP_MODES = ('both', 'reversal', 'momentum')
DEFAULT_SETUP_MODE = 'both'
_SETUP_MODE_ALIASES = {
    'both': 'both', 'all': 'both', 'any': 'both', 'a+b': 'both', 'ab': 'both',
    'combined': 'both', 'reversal+momentum': 'both', 'momentum+reversal': 'both',
    'reversal': 'reversal', 'reversal_only': 'reversal', 'reversal-only': 'reversal',
    'rev': 'reversal', 'a': 'reversal', 'setup_a': 'reversal', 'setup-a': 'reversal',
    'setupa': 'reversal', 'rsi': 'reversal', 'rsi_reversal': 'reversal',
    'momentum': 'momentum', 'momentum_only': 'momentum', 'momentum-only': 'momentum',
    'mom': 'momentum', 'b': 'momentum', 'setup_b': 'momentum', 'setup-b': 'momentum',
    'setupb': 'momentum', 'continuation': 'momentum', 'trend': 'momentum',
}
SETUP_MODE_LABELS = {'both': 'Reversal + Momentum', 'reversal': 'Reversal only',
                     'momentum': 'Momentum only'}

TRADE_DIRECTIONS = ('both', 'long', 'short')
DEFAULT_TRADE_DIRECTION = 'both'
_TRADE_DIRECTION_ALIASES = {
    'both': 'both', 'all': 'both', 'any': 'both', 'long_short': 'both',
    'long+short': 'both', 'long-short': 'both', 'longshort': 'both', 'two_way': 'both',
    'two-way': 'both', 'bidirectional': 'both', '0': 'both',
    'long': 'long', 'long_only': 'long', 'long-only': 'long', 'longs': 'long',
    'longonly': 'long', 'buy': 'long', 'bull': 'long', '1': 'long', '+1': 'long',
    'short': 'short', 'short_only': 'short', 'short-only': 'short', 'shorts': 'short',
    'shortonly': 'short', 'sell': 'short', 'bear': 'short', '-1': 'short',
}
TRADE_DIRECTION_LABELS = {'both': 'Long + Short', 'long': 'Long only', 'short': 'Short only'}


def _normalize_choice(value, aliases, allowed, default, field_name):
    """Shared normaliser: blank/None -> default, aliases -> canonical, else raise."""
    if value is None:
        return default
    if isinstance(value, bool):
        # ``True``/``False`` mean nothing here; refuse rather than guess.
        raise ValueError(f"{field_name} must be one of {', '.join(allowed)} (got {value!r})")
    key = str(value).strip().lower().replace(' ', '_')
    if not key:
        return default
    out = aliases.get(key)
    if out is None:
        raise ValueError(f"{field_name} must be one of {', '.join(allowed)} (got '{value}')")
    return out


def normalize_setup_mode(value):
    """'both' | 'reversal' | 'momentum' (accepts A/B, rev/mom, blank = both)."""
    return _normalize_choice(value, _SETUP_MODE_ALIASES, SETUP_MODES, DEFAULT_SETUP_MODE, 'setup_mode')


def normalize_trade_direction(value):
    """'both' | 'long' | 'short' (accepts long_only, buy/sell, 1/-1, blank = both)."""
    return _normalize_choice(value, _TRADE_DIRECTION_ALIASES, TRADE_DIRECTIONS,
                             DEFAULT_TRADE_DIRECTION, 'trade_direction')


# ---------------------------------------------------------------------------
# v3.5 — MACD line / signal line rules
# ---------------------------------------------------------------------------
# The strategy has always traded on the MACD *histogram* only. These optional
# rules add the MACD *line* and the *signal line* as entry conditions. Every
# rule is ``'off'`` by default; the whole block is also behind a master switch
# (``macd_line_rules.enabled``) so an existing configuration is untouched.
#
#   above_below  LONG needs the bullish side (line > signal / line > 0 /
#                signal > 0), SHORT the bearish side.
#   cross        the same, but it must have happened ON the signal candle
#                (previous bar on the other side) — a fresh crossover.
MACD_LINE_RULES = ('off', 'above_below', 'cross')
DEFAULT_MACD_LINE_RULE = 'off'
_MACD_LINE_RULE_ALIASES = {
    'off': 'off', 'none': 'off', 'no': 'off', 'false': 'off', '0': 'off', 'disabled': 'off',
    'above_below': 'above_below', 'above/below': 'above_below', 'above-below': 'above_below',
    'abovebelow': 'above_below', 'on': 'above_below', 'true': 'above_below', '1': 'above_below',
    'trend': 'above_below', 'aligned': 'above_below', 'side': 'above_below',
    'position': 'above_below', 'above': 'above_below', 'below': 'above_below',
    'cross': 'cross', 'crossover': 'cross', 'crossed': 'cross', 'crossing': 'cross',
    'cross_over': 'cross', 'x': 'cross',
}
MACD_LINE_RULE_LABELS = {'off': 'off', 'above_below': 'above / below', 'cross': 'cross'}
# The three comparisons a rule can be applied to.
MACD_LINE_RULE_KEYS = ('line_vs_signal', 'line_vs_zero', 'signal_vs_zero')


def normalize_macd_line_rule(value):
    """'off' | 'above_below' | 'cross' (blank = off)."""
    return _normalize_choice(value, _MACD_LINE_RULE_ALIASES, MACD_LINE_RULES,
                             DEFAULT_MACD_LINE_RULE, 'macd line rule')


class MacdLineConditions(BaseModel):
    """Optional MACD line / signal line entry conditions (``macd_line_rules``).

    OFF by default (``enabled=False`` and every rule ``'off'``), so existing
    runs keep trading on the histogram alone. When ON, each active rule is an
    extra gate on BOTH setups for the side it applies to:

    * ``line_vs_signal`` — LONG: MACD line > signal line, SHORT: line < signal
      (``'cross'``: the line crossed the signal on this candle).
    * ``line_vs_zero``   — LONG: MACD line > 0, SHORT: MACD line < 0
      (``'cross'``: the line crossed zero on this candle).
    * ``signal_vs_zero`` — LONG: signal line > 0, SHORT: signal line < 0.
    * ``line_min``       — minimum distance: LONG line >= value, SHORT
      line <= -value (``None`` = off). Per-side overrides are signed.
    * ``signal_min``     — same for the signal line.
    """
    enabled: bool = False
    line_vs_signal: str = DEFAULT_MACD_LINE_RULE
    line_vs_zero: str = DEFAULT_MACD_LINE_RULE
    signal_vs_zero: str = DEFAULT_MACD_LINE_RULE
    line_min: Optional[float] = None
    signal_min: Optional[float] = None

    @field_validator('line_vs_signal', 'line_vs_zero', 'signal_vs_zero', mode='before')
    @classmethod
    def _validate_rules(cls, value):
        return normalize_macd_line_rule(value)


class BranchConditions(BaseModel):
    """Per-direction overrides for a single trade side (LONG / SHORT).

    Every field is optional. When ``None`` (or when the switch governing that
    field is OFF) the value falls back to the corresponding shared
    ``PhantomV2Config`` field, so old saved configs keep working unchanged.
    This is the ``entry_conditions.long.*`` / ``entry_conditions.short.*``
    section persisted in the run/strategy JSON.
    """
    macd_fast: Optional[int] = None         # per-direction MACD EMA fast period
    macd_slow: Optional[int] = None         # per-direction MACD EMA slow period
    macd_signal: Optional[int] = None       # per-direction MACD signal period
    macd_hist_min: Optional[float] = None   # signed: long hist >= val, short hist <= val
    stop_loss_atr: Optional[float] = None   # SL distance expressed in ATR units
    # v3.6 — per-side stop when the stop level uses the price model (fraction of
    # the entry price, e.g. 0.016 = 1.6%). Mirrors ``stop_loss_atr`` exactly:
    # consulted only when the direction-condition master switch is ON.
    stop_loss_pct: Optional[float] = None
    atr_regime_ratio: Optional[float] = None  # ATR compared with ratio * SMA(ATR, 50)
    # Comparison used for the rule above. None = default '>=' (legacy floor).
    atr_regime_op: Optional[str] = None
    atr_regime_max: Optional[float] = None  # optional max-ATR cap (multiple of SMA); None = disabled
    rsi_oversold: Optional[int] = None
    rsi_overbought: Optional[int] = None
    adx_min: Optional[float] = None
    # v3.5 — per-side MACD line / signal line rules. Only consulted when
    # ``macd_line_rules.enabled`` AND ``use_direction_macd_line`` are ON; a
    # ``None`` falls back to the shared ``macd_line_rules`` value. The two
    # thresholds are SIGNED per side (long: line >= v, short: line <= v).
    macd_line_vs_signal: Optional[str] = None
    macd_line_vs_zero: Optional[str] = None
    macd_signal_vs_zero: Optional[str] = None
    macd_line_min: Optional[float] = None
    macd_signal_min: Optional[float] = None

    @field_validator('atr_regime_op')
    @classmethod
    def _validate_atr_regime_op(cls, value):
        """Reject an unknown operator instead of silently trading with '>='."""
        if value is None:
            return None
        return normalize_atr_regime_op(value)

    @field_validator('macd_line_vs_signal', 'macd_line_vs_zero', 'macd_signal_vs_zero', mode='before')
    @classmethod
    def _validate_macd_line_rules(cls, value):
        """``None`` keeps the shared rule; anything else must be a known rule."""
        if value is None:
            return None
        return normalize_macd_line_rule(value)


class EntryConditions(BaseModel):
    """Long / Short override container (``entry_conditions`` in the run JSON).

    ``use_direction_conditions`` is retained for backwards compatibility with
    the original v3.2 configuration, where every directional filter could be
    overridden at once. New configurations can opt into the two client-facing
    controls independently: ``use_direction_macd_hist`` and
    ``use_direction_atr_floor``. This keeps MACD histogram and ATR-floor
    tuning side-specific without unexpectedly changing RSI, ADX, MACD periods,
    or stop-loss behaviour.
    """
    # Legacy master switch. Existing saved configs with this set to true keep
    # their full long/short overrides and continue to work unchanged.
    use_direction_conditions: bool = False
    # Independent switches used by the Backtest form.
    use_direction_macd_hist: bool = False
    use_direction_atr_floor: bool = False
    # v3.5: Long / Short get their own MACD line / signal line rules.
    use_direction_macd_line: bool = False
    long: BranchConditions = Field(default_factory=BranchConditions)
    short: BranchConditions = Field(default_factory=BranchConditions)


# ---------------------------------------------------------------------------
# v3.6 — Risk & Exit model.
#
# Every protective level (stop loss, take profit, trailing stop, breakeven) is
# measured either in ATR units — the original Phantom behaviour, and still the
# default — or as a percentage of price. The choice is PER LEVEL, so a client
# can run an ATR stop with a price-based target, or any other mix.
#
#   'atr'   → all levels in ATR units (default: nothing changes)
#   'price' → all levels in % of price
#   'both'  → per-level mix (each level keeps its own selector value)
#
# The per-level selector values (`*_mode`) are what the engine reads; `model`
# is the convenience selector the form offers, and when it names a single
# model it forces every level to that model during validation so a config can
# never be stored inconsistent.
# ---------------------------------------------------------------------------
RISK_EXIT_ATR = 'atr'
RISK_EXIT_PRICE = 'price'
RISK_EXIT_MODELS = (RISK_EXIT_ATR, RISK_EXIT_PRICE, 'both')
RISK_EXIT_LEVELS = ('stop', 'target', 'trail', 'breakeven')
RISK_EXIT_MODE_LABELS = {RISK_EXIT_ATR: 'ATR-based', RISK_EXIT_PRICE: 'Price-based (%)'}
RISK_EXIT_MODEL_LABELS = {
    RISK_EXIT_ATR: 'ATR-based (default)',
    RISK_EXIT_PRICE: 'Price-based (%)',
    'both': 'Both — per level',
}


def normalize_risk_exit_model(value) -> str:
    """'atr' / 'price' / 'both' (aliases normalised); anything else raises."""
    v = str(value or '').strip().lower()
    aliases = {
        'atr': RISK_EXIT_ATR, 'atr-based': RISK_EXIT_ATR, 'atr_based': RISK_EXIT_ATR,
        'price': RISK_EXIT_PRICE, 'price-based': RISK_EXIT_PRICE, 'price_based': RISK_EXIT_PRICE,
        'pct': RISK_EXIT_PRICE, 'percent': RISK_EXIT_PRICE, 'percentage': RISK_EXIT_PRICE, '%': RISK_EXIT_PRICE,
        'both': 'both', 'mixed': 'both', 'mix': 'both', 'custom': 'both', 'per-level': 'both',
    }
    if v not in aliases:
        raise ValueError(f"Unknown Risk & Exit model {value!r}; use one of {RISK_EXIT_MODELS}")
    return aliases[v]


def normalize_risk_exit_mode(value) -> str:
    """'atr' or 'price' for one level; anything else raises."""
    v = str(value or '').strip().lower()
    aliases = {
        'atr': RISK_EXIT_ATR, 'atr-based': RISK_EXIT_ATR, 'atr_based': RISK_EXIT_ATR, 'atrs': RISK_EXIT_ATR,
        'price': RISK_EXIT_PRICE, 'price-based': RISK_EXIT_PRICE, 'price_based': RISK_EXIT_PRICE,
        'pct': RISK_EXIT_PRICE, 'percent': RISK_EXIT_PRICE, 'percentage': RISK_EXIT_PRICE, '%': RISK_EXIT_PRICE,
        'usd': RISK_EXIT_PRICE,
    }
    if v not in aliases:
        raise ValueError(f"Unknown Risk & Exit mode {value!r}; use 'atr' or 'price'")
    return aliases[v]


class RiskExitModel(BaseModel):
    """ATR-based (default) or price-based (%) risk & exit levels.

    ``*_pct`` values are fractions of the entry (pricing-basis) price, exactly
    like ``sl_floor_pct`` / ``margin_pct`` elsewhere in the config, so
    ``stop_loss_pct = 0.016`` means a stop 1.6% away from entry. They are only
    read for a level whose mode is 'price'; the ATR values
    (``stop_loss_atr`` and friends) are only read for a level whose mode is
    'atr'. Both sets are always kept, so switching a level back and forth in
    the form never loses the other value.

    The default is every level on ATR — byte-for-byte the original behaviour.
    """
    model: str = RISK_EXIT_ATR
    stop_mode: str = RISK_EXIT_ATR
    target_mode: str = RISK_EXIT_ATR
    trail_mode: str = RISK_EXIT_ATR
    breakeven_mode: str = RISK_EXIT_ATR
    # Defaults chosen for the price model: the stop matches the 1.6% price
    # floor the ATR model already enforces, the trail arms at +1.5% and
    # follows 0.5% behind the peak, breakeven at +1%.
    stop_loss_pct: float = Field(default=0.016, gt=0.0, le=0.5)
    take_profit_pct: float = Field(default=0.03, gt=0.0, le=1.0)
    trail_activation_pct: float = Field(default=0.015, ge=0.0, le=0.5)
    trail_distance_pct: float = Field(default=0.005, gt=0.0, le=0.5)
    breakeven_pct: float = Field(default=0.01, ge=0.0, le=0.5)

    @field_validator('model', mode='before')
    @classmethod
    def _validate_model(cls, value):
        return normalize_risk_exit_model(value)

    @field_validator('stop_mode', 'target_mode', 'trail_mode', 'breakeven_mode', mode='before')
    @classmethod
    def _validate_mode(cls, value):
        return normalize_risk_exit_mode(value)

    @model_validator(mode='after')
    def _single_model_selects_every_level(self):
        """'atr' / 'price' mean "that model on every level".

        'both' leaves the per-level selectors untouched — that is the mix.
        """
        if self.model in (RISK_EXIT_ATR, RISK_EXIT_PRICE):
            self.stop_mode = self.target_mode = self.trail_mode = self.breakeven_mode = self.model
        return self

    def mode_for(self, level: str) -> str:
        return str(getattr(self, f'{level}_mode', RISK_EXIT_ATR) or RISK_EXIT_ATR)

    def pct_for(self, level: str) -> float:
        return float(getattr(self, self.pct_field(level), 0.0) or 0.0)

    @staticmethod
    def pct_field(level: str) -> str:
        return {
            'stop': 'stop_loss_pct',
            'target': 'take_profit_pct',
            'trail': 'trail_activation_pct',
            'trail_distance': 'trail_distance_pct',
            'breakeven': 'breakeven_pct',
        }.get(level, f'{level}_pct')


class PhantomV2Config(BaseModel):
    entry_interval: str = "1h"
    trend_interval: str = "4h"
    trend_ema_period: int = Field(default=int(os.getenv("TREND_EMA_PERIOD", 50)), ge=5)
    rsi_period: int = Field(default=14, ge=2)
    rsi_oversold: int = Field(default=30, ge=5, le=45)
    rsi_overbought: int = Field(default=70, ge=55, le=95)
    macd_fast: int = Field(default=12, ge=2)
    macd_slow: int = Field(default=26, ge=5)
    macd_signal: int = Field(default=9, ge=2)
    adx_period: int = Field(default=14, ge=2)
    adx_min: float = Field(default=float(os.getenv("ADX_MIN", 20.0)), ge=0.0)
    macd_hist_min: float = Field(default=float(os.getenv("MACD_HIST_MIN", 20.0)), ge=0.0)
    atr_regime_ratio: float = Field(default=0.50, ge=0.0, le=1.0)
    atr_period: int = Field(default=14, ge=2)
    stop_loss_atr: float = Field(default=2.0, gt=0.0)
    take_profit_atr: float = Field(default=10.0, gt=0.0)
    sl_floor_pct: float = Field(default=0.016, ge=0.0)
    trail_activation_atr: float = Field(default=1.5, ge=0.0)
    trail_distance_atr: float = Field(default=0.5, gt=0.0)
    timeout_bars: int = Field(default=72, ge=1)
    cooldown_bars: int = Field(default=2, ge=0)
    margin_pct: float = Field(default=0.25, gt=0.0, le=1.0)
    leverage: int = Field(default=7, ge=1, le=125)
    lot_size_btc: float = Field(default=0.001, gt=0.0)
    max_notional_mult: int = Field(default=10, ge=1)
    taker_fee_bps: float = Field(default=float(os.getenv("TAKER_FEE_BPS", 5.9)), ge=0.0)
    maker_fee_bps: float = Field(default=float(os.getenv("MAKER_FEE_BPS", 2.36)), ge=0.0)
    liquidation_buffer: float = Field(default=0.005, ge=0.0)
    # ------------------------------------------------------------------
    # PHANTOM v3 additions (defaults preserve the v2.5 baseline behaviour)
    # ------------------------------------------------------------------
    # Setup B: momentum continuation entries (MACD-hist zero-cross with DI
    # confirmation, trading in the direction of the 4h trend). Increases
    # trade frequency in trending regimes where Setup A rarely fires.
    enable_momentum_entry: bool = Field(default=False)
    momentum_rsi_min: float = Field(default=50.0, ge=0.0, le=100.0)
    # Breakeven stop: once price moves `breakeven_atr` x ATR in favour the
    # hard stop is moved to the entry price. 0.0 disables the feature.
    breakeven_atr: float = Field(default=0.0, ge=0.0)
    # Portfolio-level drawdown throttle (all values in % of peak equity).
    #  - dd_soft_pct  : past this DD, position size is cut to reduced_margin_pct
    #  - dd_halt_pct  : past this DD, new entries stop entirely
    #  - dd_resume_pct: entries resume once DD recovers below this level
    # 100.0 means "never triggers" -> v2.5 behaviour.
    dd_soft_pct: float = Field(default=100.0, ge=0.0, le=100.0)
    dd_halt_pct: float = Field(default=100.0, ge=0.0, le=100.0)
    dd_resume_pct: float = Field(default=100.0, ge=0.0, le=100.0)
    reduced_margin_pct: float = Field(default=0.125, gt=0.0, le=1.0)
    # Engine trade-management switches
    allow_reverse: bool = Field(default=False)   # close & reverse on opposite signal
    allow_overlap: bool = Field(default=False)   # v2.5 behaviour: overwrite open trade
    # ------------------------------------------------------------------
    # v3.5 — strategy separation. Both default to 'both' = today's engine.
    #   setup_mode:      'both' | 'reversal' (Setup A only) | 'momentum' (Setup B only)
    #   trade_direction: 'both' | 'long' (longs only)       | 'short' (shorts only)
    # ------------------------------------------------------------------
    setup_mode: str = Field(default=DEFAULT_SETUP_MODE)
    trade_direction: str = Field(default=DEFAULT_TRADE_DIRECTION)
    # v3.5 — optional MACD line / signal line entry rules (OFF by default).
    macd_line_rules: MacdLineConditions = Field(default_factory=MacdLineConditions)
    # ------------------------------------------------------------------
    # v3.6 — Risk & Exit model. Every protective level (stop / target /
    # trail / breakeven) is measured in ATR units (default — unchanged)
    # or as a % of price, chosen per level. See ``RiskExitModel``.
    # ------------------------------------------------------------------
    risk_exit: RiskExitModel = Field(default_factory=RiskExitModel)
    # ------------------------------------------------------------------
    # Direction-specific condition overrides (default OFF = shared engine).
    # See EntryConditions / BranchConditions above.
    # ------------------------------------------------------------------
    entry_conditions: EntryConditions = Field(default_factory=EntryConditions)
    # ------------------------------------------------------------------
    # BTC perpetual pricing (Binance BTCUSDT / Delta BTCUSD perpetual)
    # ------------------------------------------------------------------
    # True  → stops, targets, trailing, breakeven and PnL are computed on the
    #         exchange MARK price; the traded/fill price is stored alongside.
    # False → legacy behaviour: everything runs on the traded price.
    use_mark_price: bool = Field(default=True)
    # ------------------------------------------------------------------
    # "Skip new trades" schedule (weekend / holiday blackout windows).
    # Open positions keep being managed; only new entries are blocked.
    # ------------------------------------------------------------------
    trading_windows: TradingWindowConfig = Field(default_factory=TradingWindowConfig)

    @field_validator('setup_mode', mode='before')
    @classmethod
    def _validate_setup_mode(cls, value):
        """Canonical 'both' / 'reversal' / 'momentum'; unknown values raise."""
        return normalize_setup_mode(value)

    @field_validator('trade_direction', mode='before')
    @classmethod
    def _validate_trade_direction(cls, value):
        """Canonical 'both' / 'long' / 'short'; unknown values raise."""
        return normalize_trade_direction(value)

    @model_validator(mode="after")
    def _validate_macd_periods(self):
        # MACD requires a valid slow > fast relationship or the indicator is
        # meaningless (EMA(fast) − EMA(slow) flips sign). Raise so bad values
        # never silently produce garbage signals.
        if self.macd_slow <= self.macd_fast:
            raise ValueError(f"macd_slow ({self.macd_slow}) must be greater than macd_fast ({self.macd_fast})")
        # Same check for per-direction overrides.
        if self.entry_conditions.use_direction_conditions:
            for side in ("long", "short"):
                b = getattr(self.entry_conditions, side)
                fast = b.macd_fast if b.macd_fast is not None else self.macd_fast
                slow = b.macd_slow if b.macd_slow is not None else self.macd_slow
                if slow <= fast:
                    raise ValueError(
                        f"{side} macd_slow ({slow}) must be greater than {side} macd_fast ({fast})")
        return self

    # ------------------------------------------------------------------
    # Resolvers: return the per-direction value when the toggle is ON and a
    # value is set, else the legacy shared field. `direction` is +1 (long)
    # or -1 (short).
    # ------------------------------------------------------------------
    def _branch(self, direction: int) -> BranchConditions:
        ec = self.entry_conditions
        if ec.use_direction_conditions:
            return ec.long if direction == 1 else ec.short
        return BranchConditions()

    def uses_direction_macd_hist(self) -> bool:
        """Whether MACD histogram thresholds are selected per trade side."""
        ec = self.entry_conditions
        return bool(ec.use_direction_conditions or ec.use_direction_macd_hist)

    def uses_direction_atr_floor(self) -> bool:
        """Whether the minimum ATR floor is selected per trade side."""
        ec = self.entry_conditions
        return bool(ec.use_direction_conditions or ec.use_direction_atr_floor)

    def _pick(self, direction: int, shared_name: str, dir_attr: str):
        branch = self._branch(direction)
        val = getattr(branch, dir_attr, None)
        if val is not None:
            return val
        return getattr(self, shared_name)

    def macd_periods_for(self, direction: int) -> tuple:
        """Return (fast, slow, signal) MACD periods for the trade side.

        When the direction-condition toggle is ON and that side supplies its
        own periods, they are used; otherwise fall back to the shared periods.
        """
        ec = self.entry_conditions
        if ec.use_direction_conditions:
            b = ec.long if direction == 1 else ec.short
            fast = b.macd_fast if b.macd_fast is not None else self.macd_fast
            slow = b.macd_slow if b.macd_slow is not None else self.macd_slow
            signal = b.macd_signal if b.macd_signal is not None else self.macd_signal
            return fast, slow, signal
        return self.macd_fast, self.macd_slow, self.macd_signal

    def macd_hist_min_for(self, direction: int) -> float:
        # Directional MACD-hist uses a SIGNED threshold. Longs compare with >=
        # and shorts with <=, so a negative short value requires bearish
        # momentum. When no directional override is active, preserve the old
        # absolute-magnitude filter by applying the shared value to the proper
        # side of zero.
        ec = self.entry_conditions
        if self.uses_direction_macd_hist():
            b = ec.long if direction == 1 else ec.short
            if b.macd_hist_min is not None:
                return b.macd_hist_min
        shared = self.macd_hist_min
        return abs(shared) if direction == 1 else -abs(shared)

    def stop_loss_atr_for(self, direction: int) -> float:
        return self._pick(direction, 'stop_loss_atr', 'stop_loss_atr')

    # ------------------------------------------------------------------
    # v3.6 — Risk & Exit model resolution.
    #
    # These are the single place the engine (OrderManager), the paper / live
    # workers and the docs summary ask "how far is the stop / target / trail
    # on this trade?". Each level answers from its own mode: ATR units
    # (original behaviour) or a % of price. Defaults are all-ATR, so an
    # untouched config produces the same numbers as before.
    # ------------------------------------------------------------------
    def risk_exit_config(self) -> RiskExitModel:
        """The Risk & Exit model, or all-ATR defaults if a caller's config
        object predates it (keeps third-party / test configs working)."""
        cfg = getattr(self, 'risk_exit', None)
        return cfg if isinstance(cfg, RiskExitModel) else RiskExitModel()

    def risk_exit_mode_for(self, level: str) -> str:
        return self.risk_exit_config().mode_for(level)

    def _risk_pct_for(self, level: str, direction: Optional[int] = None) -> float:
        """The % value for a level, honouring a per-side stop override.

        Mirrors ``stop_loss_atr_for``: the LONG / SHORT branch value is used
        when the direction-condition master switch is ON and the side sets one
        (an empty branch is returned otherwise, so old configs are unaffected).
        """
        if direction is not None:
            branch = self._branch(direction)
            override = getattr(branch, RiskExitModel.pct_field(level), None)
            if override is not None:
                return float(override)
        return self.risk_exit_config().pct_for(level)

    def stop_loss_distance_for(self, direction: int, price_usd: float,
                               atr_usd: float) -> float:
        """Distance from entry to the hard stop (always positive)."""
        if self.risk_exit_mode_for('stop') == RISK_EXIT_PRICE:
            return float(price_usd) * self._risk_pct_for('stop', direction)
        # ATR model: max(stop_loss_atr × ATR, sl_floor_pct × price), unchanged.
        dist = self.stop_loss_atr_for(direction) * float(atr_usd)
        floor = float(getattr(self, 'sl_floor_pct', 0.0) or 0.0) * float(price_usd)
        return max(dist, floor)

    def take_profit_distance_for(self, direction: int, price_usd: float,
                                 atr_usd: float) -> float:
        """Distance from entry to the profit target (always positive)."""
        if self.risk_exit_mode_for('target') == RISK_EXIT_PRICE:
            return float(price_usd) * self._risk_pct_for('target')
        return self.take_profit_atr * float(atr_usd)

    def trail_activation_distance_for(self, direction: int, price_usd: float,
                                      atr_usd: float) -> float:
        """Favourable distance after which the trail starts following price."""
        if self.risk_exit_mode_for('trail') == RISK_EXIT_PRICE:
            return float(price_usd) * self._risk_pct_for('trail')
        return self.trail_activation_atr * float(atr_usd)

    def trail_distance_for(self, reference_price_usd: float,
                           current_atr_usd: float) -> float:
        """How far the trail sits behind the running peak / low.

        Price model: the chosen % of the reference price (the peak being
        trailed), so the stop follows it by that percentage. ATR model:
        ``trail_distance_atr × current ATR`` — unchanged.
        """
        if self.risk_exit_mode_for('trail') == RISK_EXIT_PRICE:
            pct = self._risk_pct_for('trail_distance', None)
            return float(reference_price_usd) * pct
        return self.trail_distance_atr * float(current_atr_usd)

    def breakeven_trigger_for(self, direction: int, entry_price: float,
                              atr_at_entry: float) -> Optional[float]:
        """Price at which the stop ratchets to entry, or ``None`` when off."""
        if self.risk_exit_mode_for('breakeven') == RISK_EXIT_PRICE:
            pct = self._risk_pct_for('breakeven')
            if pct <= 0:
                return None
            sign = 1.0 if direction == 1 else -1.0
            return float(entry_price) * (1.0 + sign * pct)
        be = float(getattr(self, 'breakeven_atr', 0.0) or 0.0)
        if be <= 0:
            return None
        sign = 1.0 if direction == 1 else -1.0
        return float(entry_price) + sign * be * float(atr_at_entry)

    def risk_exit_level_text(self, level: str) -> str:
        """One level as the client reads it, e.g. ``1.2×ATR`` or ``1.6% price``."""
        rx = self.risk_exit_config()
        if rx.mode_for(level) == RISK_EXIT_PRICE:
            return f"{rx.pct_for(level) * 100:g}% price"
        atr_field = {'stop': 'stop_loss_atr', 'target': 'take_profit_atr',
                     'trail': 'trail_activation_atr', 'breakeven': 'breakeven_atr'}[level]
        return f"{float(getattr(self, atr_field, 0.0) or 0.0):g}×ATR"

    def risk_exit_text(self) -> str:
        """The whole model on one line, for the docs pages and summaries."""
        rx = self.risk_exit_config()
        parts = [f"Stop {self.risk_exit_level_text('stop')}",
                 f"TP {self.risk_exit_level_text('target')}"]
        if rx.mode_for('trail') == RISK_EXIT_PRICE:
            parts.append(f"Trail {rx.pct_for('trail') * 100:g}% → "
                         f"{rx.pct_for('trail_distance') * 100:g}%")
        else:
            parts.append(f"Trail {float(self.trail_activation_atr):g}×ATR → "
                         f"{float(self.trail_distance_atr):g}×ATR")
        be = self.risk_exit_level_text('breakeven')
        parts.append(f"BE {be}" if be != '0×ATR' else "BE off")
        return " · ".join(parts)

    def risk_exit_summary(self) -> dict:
        """Structured view of the active Risk & Exit model (API / docs)."""
        rx = self.risk_exit_config()
        levels = {}
        for level in RISK_EXIT_LEVELS:
            levels[level] = {
                'mode': rx.mode_for(level),
                'mode_label': RISK_EXIT_MODE_LABELS.get(rx.mode_for(level), rx.mode_for(level)),
                'atr': float(getattr(self, {
                    'stop': 'stop_loss_atr', 'target': 'take_profit_atr',
                    'trail': 'trail_activation_atr', 'breakeven': 'breakeven_atr',
                }[level], 0.0) or 0.0),
                'pct': rx.pct_for(level),
                'text': self.risk_exit_level_text(level),
            }
        levels['trail']['distance_atr'] = float(self.trail_distance_atr)
        levels['trail']['distance_pct'] = rx.pct_for('trail_distance')
        return {
            'model': rx.model,
            'model_label': RISK_EXIT_MODEL_LABELS.get(rx.model, rx.model),
            'levels': levels,
            'text': self.risk_exit_text(),
        }

    def atr_regime_ratio_for(self, direction: int) -> float:
        if self.uses_direction_atr_floor():
            branch = self.entry_conditions.long if direction == 1 else self.entry_conditions.short
            if branch.atr_regime_ratio is not None:
                return branch.atr_regime_ratio
        return self.atr_regime_ratio

    def atr_regime_max_for(self, direction: int) -> Optional[float]:
        # Optional directional max-ATR cap; None in shared mode or when unset.
        if not self.uses_direction_atr_floor():
            return None
        branch = self.entry_conditions.long if direction == 1 else self.entry_conditions.short
        return getattr(branch, 'atr_regime_max', None)

    def atr_regime_op_for(self, direction: int) -> str:
        """Comparison operator this side uses for its ATR regime rule.

        Only the per-direction ATR toggle can change it; with the toggle OFF
        (or no operator chosen) the legacy ``'>='`` floor is used, which keeps
        every existing run/strategy bit-for-bit identical.
        """
        if self.uses_direction_atr_floor():
            branch = self.entry_conditions.long if direction == 1 else self.entry_conditions.short
            op = getattr(branch, 'atr_regime_op', None)
            if op:
                return normalize_atr_regime_op(op)
        return DEFAULT_ATR_REGIME_OP

    def atr_regime_rule_for(self, direction: int) -> str:
        """Human-readable rule, e.g. ``ATR < 1.20 x SMA50(ATR)``.

        Used by the filter preview and the trade log so the client can see the
        exact test each side was filtered with.
        """
        op = self.atr_regime_op_for(direction)
        label = ATR_REGIME_OP_LABELS.get(op, op)
        rule = f"ATR {label} {self.atr_regime_ratio_for(direction):g} x SMA50(ATR)"
        cap = self.atr_regime_max_for(direction)
        if cap is not None:
            rule += f" and ATR <= {cap:g} x SMA50(ATR)"
        return rule

    def adx_min_for(self, direction: int) -> float:
        return self._pick(direction, 'adx_min', 'adx_min')

    def rsi_oversold_for(self, direction: int) -> float:
        return self._pick(direction, 'rsi_oversold', 'rsi_oversold')

    def rsi_overbought_for(self, direction: int) -> float:
        return self._pick(direction, 'rsi_overbought', 'rsi_overbought')

    # ------------------------------------------------------------------
    # v3.5 — setup / direction separation
    # ------------------------------------------------------------------
    def reversal_enabled(self) -> bool:
        """Whether Setup A (RSI reversal) may fire."""
        return self.setup_mode in ('both', 'reversal')

    def momentum_enabled(self) -> bool:
        """Whether Setup B (momentum continuation) may fire.

        ``'momentum'`` forces it ON (a momentum-only strategy with the
        momentum checkbox off would trade nothing); ``'reversal'`` forces it
        OFF; ``'both'`` keeps the legacy ``enable_momentum_entry`` switch.
        """
        if self.setup_mode == 'momentum':
            return True
        if self.setup_mode == 'reversal':
            return False
        return bool(self.enable_momentum_entry)

    def allows_direction(self, direction: int) -> bool:
        """Whether trades on this side (+1 long / -1 short) may be opened."""
        if self.trade_direction == 'long':
            return direction == 1
        if self.trade_direction == 'short':
            return direction == -1
        return True

    def setup_label(self) -> str:
        if self.setup_mode == 'both' and not self.enable_momentum_entry:
            # Legacy way of running reversal-only: the momentum box unticked.
            return 'Reversal only (momentum entries off)'
        return SETUP_MODE_LABELS.get(self.setup_mode, self.setup_mode)

    def direction_label(self) -> str:
        return TRADE_DIRECTION_LABELS.get(self.trade_direction, self.trade_direction)

    # ------------------------------------------------------------------
    # v3.5 — MACD line / signal line rules
    # ------------------------------------------------------------------
    def macd_line_block_enabled(self) -> bool:
        """The raw ``macd_line_rules.enabled`` switch (rules may still all be off)."""
        return bool(self.macd_line_rules.enabled)

    def uses_macd_line_rules(self) -> bool:
        """Master switch: the block is ON *and* at least one rule / threshold
        is active on either side. An enabled block with every rule ``'off'``
        is treated exactly like a disabled one (no gate, no log line)."""
        if not self.macd_line_block_enabled():
            return False
        return bool(self.macd_line_rule_parts_for(1) or self.macd_line_rule_parts_for(-1))

    def uses_direction_macd_line(self) -> bool:
        """Whether Long and Short carry their own MACD line / signal rules."""
        return bool(self.macd_line_block_enabled() and self.entry_conditions.use_direction_macd_line)

    def macd_line_rule_for(self, direction: int, which: str) -> str:
        """Rule ('off' / 'above_below' / 'cross') for one comparison and side.

        ``which`` is one of ``line_vs_signal`` / ``line_vs_zero`` /
        ``signal_vs_zero``. Always ``'off'`` while the block is disabled.
        """
        if which not in MACD_LINE_RULE_KEYS:
            raise ValueError(f"unknown MACD rule '{which}'")
        if not self.macd_line_block_enabled():
            return 'off'
        if self.uses_direction_macd_line():
            branch = self.entry_conditions.long if direction == 1 else self.entry_conditions.short
            override = getattr(branch, f'macd_{which}', None)
            if override:
                return normalize_macd_line_rule(override)
        return getattr(self.macd_line_rules, which)

    def _macd_level_for(self, direction: int, branch_attr: str, shared_attr: str) -> Optional[float]:
        if not self.macd_line_block_enabled():
            return None
        if self.uses_direction_macd_line():
            branch = self.entry_conditions.long if direction == 1 else self.entry_conditions.short
            override = getattr(branch, branch_attr, None)
            if override is not None:
                return float(override)   # signed per side, like macd_hist_min
        shared = getattr(self.macd_line_rules, shared_attr)
        if shared is None:
            return None
        # Shared value is a magnitude: longs need >= |v|, shorts need <= -|v|.
        return abs(float(shared)) if direction == 1 else -abs(float(shared))

    def macd_line_min_for(self, direction: int) -> Optional[float]:
        """Signed MACD-line threshold for the side (None = no threshold)."""
        return self._macd_level_for(direction, 'macd_line_min', 'line_min')

    def macd_signal_min_for(self, direction: int) -> Optional[float]:
        """Signed signal-line threshold for the side (None = no threshold)."""
        return self._macd_level_for(direction, 'macd_signal_min', 'signal_min')

    def macd_line_rule_parts_for(self, direction: int) -> list:
        """Human-readable list of the active MACD line / signal rules for a side."""
        is_long = direction == 1
        parts = []
        names = {'line_vs_signal': ('MACD line', 'signal'),
                 'line_vs_zero': ('MACD line', '0'),
                 'signal_vs_zero': ('signal line', '0')}
        for key in MACD_LINE_RULE_KEYS:
            rule = self.macd_line_rule_for(direction, key)
            if rule == 'off':
                continue
            left, right = names[key]
            op = '>' if is_long else '<'
            if rule == 'cross':
                parts.append(f"{left} crosses {'above' if is_long else 'below'} {right}")
            else:
                parts.append(f"{left} {op} {right}")
        thr = self.macd_line_min_for(direction)
        if thr is not None:
            parts.append(f"MACD line {'>=' if is_long else '<='} {thr:g}")
        thr = self.macd_signal_min_for(direction)
        if thr is not None:
            parts.append(f"signal line {'>=' if is_long else '<='} {thr:g}")
        return parts

    def macd_line_rule_text_for(self, direction: int) -> str:
        """e.g. ``MACD line > signal; MACD line > 0`` — ``off`` when nothing is active."""
        parts = self.macd_line_rule_parts_for(direction)
        return '; '.join(parts) if parts else 'off'


# ---------------------------------------------------------------------------
# v3.5 — built-in Phantom presets ("strategy separation" in every dropdown)
# ---------------------------------------------------------------------------
# A preset id is the champion strategy id followed by one or two variant
# tokens: ``PhantomV2:reversal``, ``PhantomV2:long``, ``PhantomV2:momentum:short``.
# The tokens map onto ``setup_mode`` / ``trade_direction``; everything else
# (thresholds, risk, sizing) is the tuned champion config. ``PhantomV2`` on
# its own is untouched and keeps meaning the full strategy.
BUILTIN_PHANTOM_ID = 'PhantomV2'
_PRESET_SEPARATORS = (':', '-', '.', '/')


def parse_phantom_variant(strategy_id) -> Optional[dict]:
    """``{'setup_mode': ..., 'trade_direction': ...}`` for a built-in id, else None.

    ``'PhantomV2'`` -> both/both. Unknown tokens (or a saved-strategy id) return
    ``None`` so the caller falls through to the custom-strategy lookup.
    """
    if strategy_id is None:
        return None
    sid = str(strategy_id).strip()
    if not sid:
        return None
    if sid == BUILTIN_PHANTOM_ID:
        return {'setup_mode': DEFAULT_SETUP_MODE, 'trade_direction': DEFAULT_TRADE_DIRECTION}
    if not sid.lower().startswith(BUILTIN_PHANTOM_ID.lower()):
        return None
    rest = sid[len(BUILTIN_PHANTOM_ID):]
    if not rest or rest[0] not in _PRESET_SEPARATORS:
        return None
    for sep in _PRESET_SEPARATORS:
        rest = rest.replace(sep, ':')
    tokens = [t for t in rest.split(':') if t]
    if not tokens or len(tokens) > 2:
        return None
    setup_mode, trade_direction = DEFAULT_SETUP_MODE, DEFAULT_TRADE_DIRECTION
    seen_setup = seen_dir = False
    for token in tokens:
        key = token.strip().lower()
        if not key or key[0].isdigit() or key[0] in '+-':
            # Numeric aliases ('1' / '-1') are for the config field only; an
            # id such as 'PhantomV2-1' must never resolve to a preset.
            return None
        if key in _SETUP_MODE_ALIASES and _SETUP_MODE_ALIASES[key] != 'both' and not seen_setup:
            setup_mode = _SETUP_MODE_ALIASES[key]
            seen_setup = True
        elif key in _TRADE_DIRECTION_ALIASES and _TRADE_DIRECTION_ALIASES[key] != 'both' and not seen_dir:
            trade_direction = _TRADE_DIRECTION_ALIASES[key]
            seen_dir = True
        else:
            return None
    return {'setup_mode': setup_mode, 'trade_direction': trade_direction}


def phantom_preset_id(setup_mode: str = 'both', trade_direction: str = 'both') -> str:
    """Canonical built-in id for a setup / direction pair."""
    setup_mode = normalize_setup_mode(setup_mode)
    trade_direction = normalize_trade_direction(trade_direction)
    parts = [BUILTIN_PHANTOM_ID]
    if setup_mode != 'both':
        parts.append(setup_mode)
    if trade_direction != 'both':
        parts.append(trade_direction)
    return ':'.join(parts)


def phantom_preset_name(strategy_id) -> Optional[str]:
    """Display name for a built-in id ("Kudos — Reversal · Long only"), else None."""
    variant = parse_phantom_variant(strategy_id)
    if variant is None:
        return None
    if variant['setup_mode'] == 'both' and variant['trade_direction'] == 'both':
        return 'Kudos V2.5 (Default)'
    bits = []
    if variant['setup_mode'] != 'both':
        bits.append(SETUP_MODE_LABELS[variant['setup_mode']].replace(' only', ''))
    if variant['trade_direction'] != 'both':
        bits.append(TRADE_DIRECTION_LABELS[variant['trade_direction']])
    if len(bits) == 1 and variant['setup_mode'] != 'both':
        bits[0] = SETUP_MODE_LABELS[variant['setup_mode']]
    return 'Kudos — ' + ' · '.join(bits)


def apply_phantom_variant(config: 'PhantomV2Config', strategy_id) -> 'PhantomV2Config':
    """Return ``config`` with the preset's setup / direction applied.

    A plain ``PhantomV2`` (or a non-built-in id) returns the config unchanged,
    so existing call sites keep their exact behaviour.
    """
    variant = parse_phantom_variant(strategy_id)
    if not variant:
        return config
    if variant['setup_mode'] == 'both' and variant['trade_direction'] == 'both':
        return config
    return config.model_copy(update=dict(variant))


def _build_presets():
    presets = []
    for setup_mode in SETUP_MODES:
        for trade_direction in TRADE_DIRECTIONS:
            if setup_mode == 'both' and trade_direction == 'both':
                continue   # that is the default strategy itself
            sid = phantom_preset_id(setup_mode, trade_direction)
            presets.append({
                'id': sid,
                'name': phantom_preset_name(sid),
                'setup_mode': setup_mode,
                'trade_direction': trade_direction,
                'setup_label': SETUP_MODE_LABELS[setup_mode],
                'direction_label': TRADE_DIRECTION_LABELS[trade_direction],
            })
    return presets


# Every separated variant of the tuned strategy, in dropdown order.
PHANTOM_PRESETS = _build_presets()


class StrategyService:
    def __init__(self, config: PhantomV2Config = PhantomV2Config()):
        self.config = config

    # ------------------------------------------------------------------
    # Vectorised core: returns (signals, metadata). Metadata carries the
    # full indicator snapshot + pass/fail of every filter for each bar so
    # every trade can be logged together with the market conditions that
    # produced it (and the exact candle it fired on).
    # ------------------------------------------------------------------
    def _compute(self, df_1h: pd.DataFrame, df_4h: pd.DataFrame):
        cfg = self.config
        df_1h = df_1h.sort_index()
        df_4h = df_4h.sort_index()
        ind_1h = compute_indicators(df_1h, macd_fast=cfg.macd_fast, macd_slow=cfg.macd_slow, macd_signal=cfg.macd_signal)
        ind_4h = compute_indicators(df_4h, macd_fast=cfg.macd_fast, macd_slow=cfg.macd_slow, macd_signal=cfg.macd_signal)
        n = len(df_1h)

        # 1. MODERATE Trend Alignment (4h close vs EMA50, asof-mapped to 1h)
        ema50_4h_map = pd.merge_asof(
            df_1h,
            pd.DataFrame({'ema50_4h': ind_4h['ema50']}, index=df_4h.index),
            left_index=True, right_index=True, direction='backward'
        )['ema50_4h'].values.astype(np.float64)
        close = df_1h['close'].values.astype(np.float64)
        trend_col = np.where(close > ema50_4h_map, 1, -1)

        # 2. ATR Regime Filter (optionally per-direction).
        # Each side compares ATR against `ratio x SMA(ATR, 50)` using its own
        # operator: '>=' (default, the legacy floor), '<=', '>' or '<'. The
        # independent ATR toggle changes the ratio AND the comparison used by
        # LONG and SHORT without touching any other filter.
        # The legacy master switch and optional max-ATR cap remain supported for
        # old saved configurations.
        atr_v = ind_1h['atr14']
        atr_sma = sma(atr_v, 50)
        use_dir = cfg.entry_conditions.use_direction_conditions
        use_dir_atr = cfg.uses_direction_atr_floor()
        if use_dir_atr:
            reg_ratio_l = cfg.atr_regime_ratio_for(1)
            reg_ratio_s = cfg.atr_regime_ratio_for(-1)
            op_l = _ATR_OP_FUNCS[cfg.atr_regime_op_for(1)]
            op_s = _ATR_OP_FUNCS[cfg.atr_regime_op_for(-1)]
            max_l = cfg.atr_regime_max_for(1)
            max_s = cfg.atr_regime_max_for(-1)
            floor_l = op_l(atr_v, reg_ratio_l * atr_sma)
            floor_s = op_s(atr_v, reg_ratio_s * atr_sma)
            regime_ok_l = floor_l if max_l is None else (floor_l & (atr_v <= max_l * atr_sma))
            regime_ok_s = floor_s if max_s is None else (floor_s & (atr_v <= max_s * atr_sma))
        else:
            regime_ok_shared = atr_v >= (cfg.atr_regime_ratio * atr_sma)
            regime_ok_l = regime_ok_s = regime_ok_shared

        rsi_v = ind_1h['rsi14']
        hist = ind_1h['macd_hist']
        adx_v = ind_1h['adx']
        pdi, mdi = ind_1h['pdi'], ind_1h['mdi']
        is_green = ind_1h['is_green'].astype(bool)
        is_red = ind_1h['is_red'].astype(bool)

        # Per-direction MACD periods are part of the legacy master switch.
        # The new MACD-hist-only switch keeps the shared indicator periods and
        # only changes the signed threshold for each side.
        # The MACD line and signal line are kept next to the histogram (same
        # call, same periods) for the v3.5 line / signal rules and the trade log.
        line_v = ind_1h['macd_line']
        sig_v = ind_1h['macd_signal']
        if use_dir:
            l_f, l_s, l_sig = cfg.macd_periods_for(1)
            s_f, s_s, s_sig = cfg.macd_periods_for(-1)
            line_long, sig_long, hist_long = _macd(close, fast=l_f, slow=l_s, signal_period=l_sig)
            line_short, sig_short, hist_short = _macd(close, fast=s_f, slow=s_s, signal_period=s_sig)
        else:
            hist_long = hist
            hist_short = hist
            line_long = line_short = line_v
            sig_long = sig_short = sig_v

        # v3.5 MACD line / signal line gates — all-True (no-op) unless the
        # client switched the block on.
        macd_line_rules_on = cfg.uses_macd_line_rules()
        if macd_line_rules_on:
            macd_line_ok_l = self._macd_line_mask(cfg, 1, line_long, sig_long)
            macd_line_ok_s = self._macd_line_mask(cfg, -1, line_short, sig_short)
        else:
            macd_line_ok_l = np.ones(n, dtype=bool)
            macd_line_ok_s = np.ones(n, dtype=bool)

        use_dir_hist = cfg.uses_direction_macd_hist()
        if use_dir:
            adx_ok_l = adx_v >= cfg.adx_min_for(1)
            adx_ok_s = adx_v >= cfg.adx_min_for(-1)
            rsi_oversold_l = cfg.rsi_oversold_for(1)
            rsi_overbought_s = cfg.rsi_overbought_for(-1)
        else:
            adx_ok_shared = adx_v >= cfg.adx_min
            adx_ok_l = adx_ok_s = adx_ok_shared
            rsi_oversold_l = cfg.rsi_oversold
            rsi_overbought_s = cfg.rsi_overbought

        if use_dir_hist:
            # Directional MACD-hist is signed: LONG uses >= and SHORT uses <=.
            hist_ok_l = hist_long >= cfg.macd_hist_min_for(1)
            hist_ok_s = hist_short <= cfg.macd_hist_min_for(-1)
        else:
            # Legacy shared mode retains its absolute-magnitude comparison.
            hist_ok_shared = np.abs(hist) >= cfg.macd_hist_min
            hist_ok_l = hist_ok_s = hist_ok_shared

        rsi_prev = np.roll(rsi_v, 1)
        hist_prev = np.roll(hist, 1)
        hist_long_prev = np.roll(hist_long, 1)
        hist_short_prev = np.roll(hist_short, 1)
        valid = np.arange(n) >= 1  # baseline loop started at bar 1

        # ---------------- Setup A: RSI reversal (v2.5 baseline) ----------
        long_rsi_A = (rsi_prev < rsi_oversold_l) & is_green
        short_rsi_A = (rsi_prev > rsi_overbought_s) & is_red
        long_macd_A = hist_long > hist_long_prev
        short_macd_A = hist_short < hist_short_prev

        long_A = valid & (trend_col == 1) & adx_ok_l & hist_ok_l & regime_ok_l & long_rsi_A & long_macd_A
        short_A = valid & (trend_col == -1) & adx_ok_s & hist_ok_s & regime_ok_s & short_rsi_A & short_macd_A

        # ------------- Setup B: momentum continuation (v3, optional) ------
        # Fires when the MACD histogram crosses zero in the trend direction
        # with DI confirmation and RSI agreement.
        cross_up = (hist_long_prev <= 0) & (hist_long > 0)
        cross_dn = (hist_short_prev >= 0) & (hist_short < 0)
        long_B = valid & (trend_col == 1) & adx_ok_l & regime_ok_l & (pdi > mdi) & cross_up & (rsi_v >= cfg.momentum_rsi_min)
        short_B = valid & (trend_col == -1) & adx_ok_s & regime_ok_s & (mdi > pdi) & cross_dn & (rsi_v <= 100.0 - cfg.momentum_rsi_min)

        # ---------------- v3.5 gates (all no-ops on a default config) -----
        # MACD line / signal line rules apply to both setups on their side.
        if macd_line_rules_on:
            long_A = long_A & macd_line_ok_l
            long_B = long_B & macd_line_ok_l
            short_A = short_A & macd_line_ok_s
            short_B = short_B & macd_line_ok_s
        # Setup separation: momentum_enabled() is exactly the legacy
        # enable_momentum_entry switch while setup_mode is 'both'.
        if not cfg.momentum_enabled():
            long_B[:] = False
            short_B[:] = False
        if not cfg.reversal_enabled():
            long_A[:] = False
            short_A[:] = False
        # Direction separation: long-only / short-only strategies.
        if not cfg.allows_direction(1):
            long_A[:] = False
            long_B[:] = False
        if not cfg.allows_direction(-1):
            short_A[:] = False
            short_B[:] = False

        signals = np.zeros(n)
        signals[long_A | long_B] = 1
        signals[short_A | short_B] = -1

        setup = np.full(n, '', dtype=object)
        setup[long_B | short_B] = 'MOMENTUM'
        setup[long_A | short_A] = 'REVERSAL'

        meta = {
            'rsi14': rsi_v, 'macd_hist': hist, 'adx': adx_v, 'atr14': atr_v,
            'ema50_1h': ind_1h['ema50'], 'ema50_4h': ema50_4h_map,
            'pdi': pdi, 'mdi': mdi,
            'trend': trend_col, 'is_green': is_green, 'is_red': is_red,
            # Reference values the trade log needs to spell out every entry
            # condition (actual value vs the threshold that was applied).
            'atr_sma50': atr_sma,
            'rsi_prev': rsi_prev,
            'macd_hist_long': hist_long, 'macd_hist_short': hist_short,
            'macd_hist_long_prev': hist_long_prev, 'macd_hist_short_prev': hist_short_prev,
            'close': close,
            # Setup B (momentum) masks, kept separate from the reversal masks so
            # a MOMENTUM trade's log shows the filters that actually fired.
            'cond_di_long': pdi > mdi, 'cond_di_short': mdi > pdi,
            'cond_mom_cross_long': cross_up, 'cond_mom_cross_short': cross_dn,
            'cond_mom_rsi_long': rsi_v >= cfg.momentum_rsi_min,
            'cond_mom_rsi_short': rsi_v <= 100.0 - cfg.momentum_rsi_min,
            # Per-direction condition masks (used by the engine snapshot to
            # log which filter passed for the side a trade actually fired on).
            'cond_adx_ok_long': adx_ok_l, 'cond_adx_ok_short': adx_ok_s,
            'cond_macd_hist_ok_long': hist_ok_l, 'cond_macd_hist_ok_short': hist_ok_s,
            'cond_atr_regime_ok_long': regime_ok_l, 'cond_atr_regime_ok_short': regime_ok_s,
            # Backward-compatible shared keys (identical to the long masks when
            # the direction toggle is OFF).
            'cond_adx_ok': adx_ok_l, 'cond_macd_hist_ok': hist_ok_l,
            'cond_atr_regime_ok': regime_ok_l,
            'cond_long_rsi': long_rsi_A, 'cond_short_rsi': short_rsi_A,
            'cond_long_macd': long_macd_A, 'cond_short_macd': short_macd_A,
            # Human-readable ATR rule actually applied to each side (scalars,
            # not per-bar arrays) — surfaced in the trade log and preview.
            'atr_regime_rule_long': cfg.atr_regime_rule_for(1),
            'atr_regime_rule_short': cfg.atr_regime_rule_for(-1),
            'setup': setup,
            'long_A': long_A, 'short_A': short_A, 'long_B': long_B, 'short_B': short_B,
            # v3.5 — MACD line / signal line values (shared and per side), the
            # pass/fail of the optional line rules, and the separation settings
            # this run was generated with.
            'macd_line': line_v, 'macd_signal': sig_v,
            'macd_line_long': line_long, 'macd_line_short': line_short,
            'macd_signal_long': sig_long, 'macd_signal_short': sig_short,
            'macd_line_long_prev': np.roll(line_long, 1), 'macd_line_short_prev': np.roll(line_short, 1),
            'macd_signal_long_prev': np.roll(sig_long, 1), 'macd_signal_short_prev': np.roll(sig_short, 1),
            'macd_line_rules_enabled': bool(macd_line_rules_on),
            'cond_macd_line_ok_long': macd_line_ok_l, 'cond_macd_line_ok_short': macd_line_ok_s,
            'macd_line_rule_long': cfg.macd_line_rule_text_for(1),
            'macd_line_rule_short': cfg.macd_line_rule_text_for(-1),
            'setup_mode': cfg.setup_mode, 'trade_direction': cfg.trade_direction,
        }
        return signals, meta

    @staticmethod
    def _macd_line_mask(cfg: PhantomV2Config, direction: int, line: np.ndarray, signal: np.ndarray) -> np.ndarray:
        """Per-bar pass mask of the v3.5 MACD line / signal line rules for one side.

        LONG needs the bullish side of each active comparison, SHORT the
        bearish side; ``'cross'`` additionally requires the previous bar to
        have been on the other side (a crossover ON the signal candle). Bar 0
        has no previous bar; the caller's ``valid`` mask already excludes it.
        """
        is_long = direction == 1
        ok = np.ones(len(line), dtype=bool)
        line_prev = np.roll(line, 1)
        sig_prev = np.roll(signal, 1)

        def _apply(rule, now_bull, now_bear, prev_bull, prev_bear):
            nonlocal ok
            if rule == 'above_below':
                ok &= now_bull if is_long else now_bear
            elif rule == 'cross':
                # Previous bar on/below the level, this bar above it (long) —
                # mirrored for short.
                ok &= (~prev_bull & now_bull) if is_long else (~prev_bear & now_bear)

        _apply(cfg.macd_line_rule_for(direction, 'line_vs_signal'),
               line > signal, line < signal, line_prev > sig_prev, line_prev < sig_prev)
        _apply(cfg.macd_line_rule_for(direction, 'line_vs_zero'),
               line > 0, line < 0, line_prev > 0, line_prev < 0)
        _apply(cfg.macd_line_rule_for(direction, 'signal_vs_zero'),
               signal > 0, signal < 0, sig_prev > 0, sig_prev < 0)

        thr = cfg.macd_line_min_for(direction)
        if thr is not None:
            ok &= (line >= thr) if is_long else (line <= thr)
        thr = cfg.macd_signal_min_for(direction)
        if thr is not None:
            ok &= (signal >= thr) if is_long else (signal <= thr)
        return ok

    def generate_signals(self, df_1h: pd.DataFrame, df_4h: pd.DataFrame):
        """Backward-compatible entry point used by API / paper / live traders."""
        signals, _ = self._compute(df_1h, df_4h)
        return signals

    def generate_signals_with_metadata(self, df_1h: pd.DataFrame, df_4h: pd.DataFrame):
        """Signals plus the per-bar condition snapshot used for trade logging."""
        return self._compute(df_1h, df_4h)

class FastTestConfig(PhantomV2Config):
    """Config of the **Fast Test (debug)** strategy.

    Every PhantomV2 field is inherited — the debug strategy shares the same
    risk / sizing / exit plan. On top of that it declares the two things this
    family lets the client change (all defaults are the shipped behaviour, so an
    unedited strategy is byte-for-byte the original):

    * the **entry rule** — ``entry_rsi_period`` / ``entry_rsi_long_max`` /
      ``entry_rsi_short_min`` (RSI 14, long below 50, short at/above 50) and the
      side filter the config already had (``trade_direction``),
    * the **exit rule** — ``use_*`` switches for the stop, target, trailing
      stop, breakeven and timeout, plus three optional signal conditions
      (``exit_on_opposite``, ``exit_rsi_enabled`` + ``exit_rsi_level``,
      ``exit_macd_flip_enabled``), all OFF by default.

    The *type* is what marks the strategy family: a saved strategy stores the
    family id next to its parameters (see ``/strategies/create``), so the API
    rebuilds this class — or
    :class:`~app.core.fast_test_v1.FastTestV1Config`, which extends it — and the
    backtest / paper / live workers then run the configured entry rule with the
    client's own stop, target, sizing and timing values.
    """

    # ---- Entry rule (the debug rule, parameterised) -------------------
    #: RSI period the debug entry rule reads on the 1h candles.
    entry_rsi_period: int = Field(default=14, ge=2, le=200)
    #: LONG while RSI is below this value (the original rule's 50).
    entry_rsi_long_max: float = Field(default=50.0, ge=0.0, le=100.0)
    #: SHORT while RSI is at or above this value (the original rule's 50).
    entry_rsi_short_min: float = Field(default=50.0, ge=0.0, le=100.0)

    # ---- Exit rule: which protective rules the order manager applies ---
    use_stop_loss: bool = True
    use_take_profit: bool = True
    use_trailing_stop: bool = True
    use_breakeven: bool = True
    use_timeout: bool = True

    # ---- Exit rule: optional signal conditions (all off by default) ----
    exit_on_opposite: bool = False
    exit_rsi_enabled: bool = False
    exit_rsi_level: float = Field(default=50.0, ge=0.0, le=100.0)
    exit_macd_flip_enabled: bool = False


def _is_fast_test_config(config) -> bool:
    """True for the debug-strategy config family (FastTest and V1.0)."""
    try:
        from .fast_test_v1 import FastTestV1Config  # local import: avoids a cycle
        if isinstance(config, FastTestV1Config):
            return True
    except Exception:
        pass
    return isinstance(config, FastTestConfig)


def fast_test_config(params=None, fees=None) -> 'FastTestConfig':
    """Build the FastTest config from a request / saved-strategy params block.

    Only fields the config declares are copied, so a Phantom params block (or a
    future form field) can never inject a value the debug strategy does not
    have. Anything missing keeps the shipped default, so a params-less call
    reproduces the current FastTest behaviour exactly.
    """
    payload = {}
    if params is not None:
        try:
            dump = params.model_dump() if hasattr(params, "model_dump") else dict(params)
        except Exception:
            dump = {}
        allowed = set(FastTestConfig.model_fields)
        payload = {k: v for k, v in (dump or {}).items() if k in allowed}
    cfg = FastTestConfig(**payload)
    if fees is not None:
        taker = float(getattr(fees, "taker_fee_bps", 0.0) or 0.0)
        maker = float(getattr(fees, "maker_fee_bps", 0.0) or 0.0)
        cfg = cfg.model_copy(update={"taker_fee_bps": taker, "maker_fee_bps": maker})
    return cfg


class FastTestStrategyService:
    """Simple strategy to generate very frequent signals for testing Paper/Live trading.

    The entry rule (RSI 14 on the 1h candles: long below 50, short at/above 50)
    is unchanged; the config now carries the risk / sizing / timing values the
    client edits in the Strategy Configuration panel when this strategy — or a
    saved copy of it — is selected.
    """
    label = 'FASTTEST'

    def __init__(self, config: PhantomV2Config = None):
        self.config = config or FastTestConfig()

    def generate_signals(self, df_1h: pd.DataFrame, df_4h: pd.DataFrame):
        """The debug entry rule, with the client's own thresholds.

        Shipped defaults: RSI(14) on the 1h candles, LONG below 50 / SHORT at or
        above 50 — byte-for-byte the original loop. The period, the two
        thresholds and the allowed sides (``trade_direction``) are editable per
        saved strategy; the shape of the rule (one side per candle, decided on
        that candle's RSI) never changes.
        """
        from .fast_test_rules import entry_rsi_long_max, entry_rsi_period, entry_rsi_short_min, fast_test_sides

        df_1h = df_1h.sort_index()
        ind_1h = compute_indicators(df_1h, macd_fast=self.config.macd_fast, macd_slow=self.config.macd_slow,
                                    macd_signal=self.config.macd_signal,
                                    rsi_period=entry_rsi_period(self.config))

        signals = np.zeros(len(df_1h))
        rsi = ind_1h['rsi14']
        long_max = entry_rsi_long_max(self.config)
        short_min = entry_rsi_short_min(self.config)
        allow_long, allow_short = fast_test_sides(self.config)

        for i in range(1, len(df_1h)):
            # For testing purposes, we use very loose bounds so signals happen almost every bar.
            # Long below the long threshold, short at or above the short threshold.
            if allow_long and rsi[i] < long_max:
                signals[i] = 1
            elif allow_short and rsi[i] >= short_min:
                signals[i] = -1
        return signals

    def exit_state_series(self, df_1h: pd.DataFrame, df_4h: pd.DataFrame = None):
        """Per-bar values the configured exit conditions judge (``None`` when off).

        The engine computes the series once and hands the order manager the
        candle's scalars; the paper / live workers use
        :func:`~app.core.fast_test_rules.fast_test_bar_state` instead. Nothing
        is computed when no condition is switched on, so an unedited strategy
        stays exactly as cheap as it was.
        """
        from .fast_test_rules import entry_rsi_period, exit_conditions_configured

        if not exit_conditions_configured(self.config):
            return None
        try:
            ind_1h = compute_indicators(
                df_1h.sort_index(),
                macd_fast=self.config.macd_fast, macd_slow=self.config.macd_slow,
                macd_signal=self.config.macd_signal,
                rsi_period=entry_rsi_period(self.config))
            return {
                'rsi': ind_1h['rsi14'],
                'macd_line': ind_1h['macd_line'],
                'macd_signal': ind_1h['macd_signal'],
            }
        except Exception:
            return None

@dataclass
class ValidationResult:
    passed: bool
    reason: str
    price_drift_pct: float

class ValidatorService:
    def validate_signal(self, signal_dir, ref_price, current_price, ind_1h_slice):
        # Increased drift tolerance from 0.005 to 0.01 (1%)
        # This prevents the validator from killing too many trades due to minor price gaps
        drift = abs(current_price - ref_price) / ref_price
        if drift > 0.01: return ValidationResult(False, "PRICE_DRIFT", drift)
        return ValidationResult(True, "PASSED", drift)
