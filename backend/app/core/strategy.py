from pydantic import BaseModel, Field
from dataclasses import dataclass
import pandas as pd
import numpy as np
import os
from typing import Optional
from dotenv import load_dotenv
from .indicators import compute_indicators, sma

load_dotenv()


class BranchConditions(BaseModel):
    """Per-direction overrides for a single trade side (LONG / SHORT).

    Every field is optional: when ``None`` (or when the master
    ``use_direction_conditions`` toggle is OFF) the value falls back to the
    corresponding shared ``PhantomV2Config`` field, so old saved configs keep
    working unchanged. This is the ``entry_conditions.long.*`` /
    ``entry_conditions.short.*`` section persisted in the run/strategy JSON.
    """
    macd_hist_min: Optional[float] = None   # signed: long hist >= val, short hist <= val
    stop_loss_atr: Optional[float] = None   # SL distance expressed in ATR units
    atr_regime_ratio: Optional[float] = None  # ATR >= ratio * SMA(ATR, 50) (legacy floor)
    atr_regime_max: Optional[float] = None  # optional max-ATR cap (multiple of SMA); None = disabled
    rsi_oversold: Optional[int] = None
    rsi_overbought: Optional[int] = None
    adx_min: Optional[float] = None


class EntryConditions(BaseModel):
    """Long / Short override container (``entry_conditions`` in the run JSON).

    When ``use_direction_conditions`` is False the engine behaves exactly as the
    pre-existing shared-condition engine. When True the LONG and SHORT branches
    each supply their own copy of the directional fields that data shows matter
    for the two sides (MACD hist min, stop-loss ATR, ATR regime ratio, RSI
    bounds, ADX min).
    """
    use_direction_conditions: bool = False
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

    def _pick(self, direction: int, shared_name: str, dir_attr: str):
        branch = self._branch(direction)
        val = getattr(branch, dir_attr, None)
        if val is not None:
            return val
        return getattr(self, shared_name)

    def macd_hist_min_for(self, direction: int) -> float:
        # Directional MACD-hist uses a SIGNED threshold. When the direction
        # hasn't set an override we fall back to the shared magnitude applied
        # to the correct side (long => histogram clearly positive, short =>
        # clearly negative) so the legacy absolute-value filter is preserved.
        ec = self.entry_conditions
        if ec.use_direction_conditions:
            b = ec.long if direction == 1 else ec.short
            if b.macd_hist_min is not None:
                return b.macd_hist_min
        shared = self.macd_hist_min
        return abs(shared) if direction == 1 else -abs(shared)

    def stop_loss_atr_for(self, direction: int) -> float:
        return self._pick(direction, 'stop_loss_atr', 'stop_loss_atr')

    def atr_regime_ratio_for(self, direction: int) -> float:
        return self._pick(direction, 'atr_regime_ratio', 'atr_regime_ratio')

    def atr_regime_max_for(self, direction: int) -> Optional[float]:
        # Optional directional max-ATR cap; None in shared mode or when unset.
        branch = self._branch(direction)
        return getattr(branch, 'atr_regime_max', None)

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
        ind_1h = compute_indicators(df_1h)
        ind_4h = compute_indicators(df_4h)
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
        # `atr_regime_ratio` keeps the legacy lower-bound semantics
        # (atr >= ratio * SMA) in BOTH modes so the shared pre-fill is
        # behaviour-identical when the toggle is first turned on. An
        # additional optional per-direction MAX-ATR cap (`atr_regime_max`,
        # a multiple of SMA, None = disabled) lets shorts exclude the
        # high-volatility regime where the data shows REVERSAL-SHORT
        # underperforms (a lower cap = tighter = excludes more high-vol).
        atr_v = ind_1h['atr14']
        atr_sma = sma(atr_v, 50)
        use_dir = cfg.entry_conditions.use_direction_conditions
        if use_dir:
            reg_ratio_l = cfg.atr_regime_ratio_for(1)
            reg_ratio_s = cfg.atr_regime_ratio_for(-1)
            max_l = cfg.atr_regime_max_for(1)
            max_s = cfg.atr_regime_max_for(-1)
            regime_ok_l = (atr_v >= reg_ratio_l * atr_sma) if max_l is None else ((atr_v >= reg_ratio_l * atr_sma) & (atr_v <= max_l * atr_sma))
            regime_ok_s = (atr_v >= reg_ratio_s * atr_sma) if max_s is None else ((atr_v >= reg_ratio_s * atr_sma) & (atr_v <= max_s * atr_sma))
        else:
            regime_ok_shared = atr_v >= (cfg.atr_regime_ratio * atr_sma)
            regime_ok_l = regime_ok_s = regime_ok_shared

        rsi_v = ind_1h['rsi14']
        hist = ind_1h['macd_hist']
        adx_v = ind_1h['adx']
        pdi, mdi = ind_1h['pdi'], ind_1h['mdi']
        is_green = ind_1h['is_green'].astype(bool)
        is_red = ind_1h['is_red'].astype(bool)

        if use_dir:
            adx_ok_l = adx_v >= cfg.adx_min_for(1)
            adx_ok_s = adx_v >= cfg.adx_min_for(-1)
            # Directional MACD-hist filter uses SIGNED comparisons so shorts
            # can require the histogram to sit clearly below the zero line
            # (negative value) while longs require it to be clearly positive.
            hist_ok_l = hist >= cfg.macd_hist_min_for(1)
            hist_ok_s = hist <= cfg.macd_hist_min_for(-1)
            rsi_oversold_l = cfg.rsi_oversold_for(1)
            rsi_overbought_s = cfg.rsi_overbought_for(-1)
        else:
            adx_ok_shared = adx_v >= cfg.adx_min
            adx_ok_l = adx_ok_s = adx_ok_shared
            hist_ok_shared = np.abs(hist) >= cfg.macd_hist_min
            hist_ok_l = hist_ok_s = hist_ok_shared
            rsi_oversold_l = cfg.rsi_oversold
            rsi_overbought_s = cfg.rsi_overbought

        rsi_prev = np.roll(rsi_v, 1)
        hist_prev = np.roll(hist, 1)
        valid = np.arange(n) >= 1  # baseline loop started at bar 1

        # ---------------- Setup A: RSI reversal (v2.5 baseline) ----------
        long_rsi_A = (rsi_prev < rsi_oversold_l) & is_green
        short_rsi_A = (rsi_prev > rsi_overbought_s) & is_red
        long_macd_A = hist > hist_prev
        short_macd_A = hist < hist_prev

        long_A = valid & (trend_col == 1) & adx_ok_l & hist_ok_l & regime_ok_l & long_rsi_A & long_macd_A
        short_A = valid & (trend_col == -1) & adx_ok_s & hist_ok_s & regime_ok_s & short_rsi_A & short_macd_A

        # ------------- Setup B: momentum continuation (v3, optional) ------
        # Fires when the MACD histogram crosses zero in the trend direction
        # with DI confirmation and RSI agreement.
        cross_up = (hist_prev <= 0) & (hist > 0)
        cross_dn = (hist_prev >= 0) & (hist < 0)
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
        ind_1h = compute_indicators(df_1h)

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
