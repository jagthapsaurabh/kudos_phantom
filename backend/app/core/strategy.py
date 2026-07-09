from pydantic import BaseModel, Field
from dataclasses import dataclass
import pandas as pd
import numpy as np
import os
from dotenv import load_dotenv
from .indicators import compute_indicators, sma

load_dotenv()

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

class StrategyService:
    def __init__(self, config: PhantomV2Config = PhantomV2Config()):
        self.config = config

    def generate_signals(self, df_1h: pd.DataFrame, df_4h: pd.DataFrame):
        df_1h = df_1h.sort_index()
        df_4h = df_4h.sort_index()
        ind_1h = compute_indicators(df_1h)
        ind_4h = compute_indicators(df_4h)
        
        # 1. MODERATE Trend Alignment (4h close vs EMA50)
        df_1h_with_trend = pd.merge_asof(
            df_1h,
            pd.DataFrame({'ema50_4h': ind_4h['ema50']}, index=df_4h.index), 
            left_index=True, right_index=True, direction='backward'
        )
        trend_col = np.where(df_1h['close'].values > df_1h_with_trend['ema50_4h'].values, 1, -1)
        
        # 2. ATR Regime Filter
        atr_vals = ind_1h['atr14']
        atr_sma50 = sma(atr_vals, 50)
        atr_regime_ok = atr_vals >= (self.config.atr_regime_ratio * atr_sma50)

        signals = np.zeros(len(df_1h))
        for i in range(1, len(df_1h)):
            # Filter 1: ADX minimum
            if ind_1h['adx'][i] < self.config.adx_min: continue
            # Filter 2: MACD histogram magnitude
            if abs(ind_1h['macd_hist'][i]) < self.config.macd_hist_min: continue
            # Filter 3: Volatility Regime
            if not atr_regime_ok[i]: continue
            
            trend = trend_col[i]
            
            # Filter 4: RSI Reversal
            # Long: Previous bar RSI < 30 AND current bar is green
            is_long_rsi = (ind_1h['rsi14'][i-1] < self.config.rsi_oversold) and ind_1h['is_green'][i]
            # Short: Previous bar RSI > 70 AND current bar is red
            is_short_rsi = (ind_1h['rsi14'][i-1] > self.config.rsi_overbought) and ind_1h['is_red'][i]
            
            # Filter 5: MACD Histogram Confirmation
            is_long_macd = ind_1h['macd_hist'][i] > ind_1h['macd_hist'][i-1]
            is_short_macd = ind_1h['macd_hist'][i] < ind_1h['macd_hist'][i-1]
            
            if trend == 1 and is_long_rsi and is_long_macd: 
                signals[i] = 1
            elif trend == -1 and is_short_rsi and is_short_macd: 
                signals[i] = -1
                
        return signals

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
