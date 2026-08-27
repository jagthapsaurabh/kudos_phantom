from pydantic import BaseModel, Field, field_validator, model_validator
from dataclasses import dataclass
import pandas as pd
import numpy as np
import os
from typing import Optional
from dotenv import load_dotenv
from .indicators import compute_indicators, sma, macd as _macd

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
    atr_regime_ratio: Optional[float] = None  # ATR compared with ratio * SMA(ATR, 50)
    # Comparison used for the rule above. None = default '>=' (legacy floor).
    atr_regime_op: Optional[str] = None
    atr_regime_max: Optional[float] = None  # optional max-ATR cap (multiple of SMA); None = disabled
    rsi_oversold: Optional[int] = None
    rsi_overbought: Optional[int] = None
    adx_min: Optional[float] = None

    @field_validator('atr_regime_op')
    @classmethod
    def _validate_atr_regime_op(cls, value):
        """Reject an unknown operator instead of silently trading with '>='."""
        if value is None:
            return None
        return normalize_atr_regime_op(value)


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
    long: BranchConditions = Field(default_factory=BranchConditions)
    short: BranchConditions = Field(default_factory=BranchConditions)


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
    # Direction-specific condition overrides (default OFF = shared engine).
    # See EntryConditions / BranchConditions above.
    # ------------------------------------------------------------------
    entry_conditions: EntryConditions = Field(default_factory=EntryConditions)

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
        if use_dir:
            l_f, l_s, l_sig = cfg.macd_periods_for(1)
            s_f, s_s, s_sig = cfg.macd_periods_for(-1)
            hist_long = _macd(close, fast=l_f, slow=l_s, signal_period=l_sig)[2]
            hist_short = _macd(close, fast=s_f, slow=s_s, signal_period=s_sig)[2]
        else:
            hist_long = hist
            hist_short = hist

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
        if not cfg.enable_momentum_entry:
            long_B[:] = False
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
        }
        return signals, meta

    def generate_signals(self, df_1h: pd.DataFrame, df_4h: pd.DataFrame):
        """Backward-compatible entry point used by API / paper / live traders."""
        signals, _ = self._compute(df_1h, df_4h)
        return signals

    def generate_signals_with_metadata(self, df_1h: pd.DataFrame, df_4h: pd.DataFrame):
        """Signals plus the per-bar condition snapshot used for trade logging."""
        return self._compute(df_1h, df_4h)

class FastTestStrategyService:
    """Simple strategy to generate very frequent signals for testing Paper/Live trading."""
    def __init__(self, config: PhantomV2Config = PhantomV2Config()):
        self.config = config

    def generate_signals(self, df_1h: pd.DataFrame, df_4h: pd.DataFrame):
        df_1h = df_1h.sort_index()
        ind_1h = compute_indicators(df_1h, macd_fast=self.config.macd_fast, macd_slow=self.config.macd_slow, macd_signal=self.config.macd_signal)

        signals = np.zeros(len(df_1h))
        rsi = ind_1h['rsi14']

        for i in range(1, len(df_1h)):
            # For testing purposes, we use very loose bounds so signals happen almost every bar
            # Long if RSI is below 55, Short if RSI is above 45.
            # To avoid flickering, we'll just use a simple split:
            if rsi[i] < 50:
                signals[i] = 1
            elif rsi[i] >= 50:
                signals[i] = -1
        return signals

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
