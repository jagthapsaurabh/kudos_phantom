from backend.app.core.engine import BacktestEngine
from backend.app.core.strategy import StrategyService, PhantomV2Config
from backend.app.core.indicators import compute_indicators
from backend.app.database.models import SessionLocal, Klines
import pandas as pd
import numpy as np
from datetime import datetime

def debug_signals():
    config = PhantomV2Config()
    strategy_service = StrategyService(config)
    
    db = SessionLocal()
    df_1h = pd.DataFrame([
        {'event_time': k.event_time, 'open': k.open, 'high': k.high, 'low': k.low, 'close': k.close, 'volume': k.volume}
        for k in db.query(Klines).filter(Klines.symbol == "BTCUSDT", Klines.interval == "1h").order_by(Klines.event_time.asc()).all()
    ])
    df_4h = pd.DataFrame([
        {'event_time': k.event_time, 'open': k.open, 'high': k.high, 'low': k.low, 'close': k.close, 'volume': k.volume}
        for k in db.query(Klines).filter(Klines.symbol == "BTCUSDT", Klines.interval == "4h").order_by(Klines.event_time.asc()).all()
    ])
    db.close()
    
    df_1h.set_index('event_time', inplace=True)
    df_4h.set_index('event_time', inplace=True)
    
    ind_1h = compute_indicators(df_1h)
    ind_4h = compute_indicators(df_4h)
    
    df_1h_with_trend = pd.merge_asof(
        df_1h,
        pd.DataFrame({'ema50_4h': ind_4h['ema50']}, index=df_4h.index), 
        left_index=True, right_index=True, direction='backward'
    )
    trend_col = np.where(df_1h['close'].values > df_1h_with_trend['ema50_4h'].values, 1, -1)
    
    atr_vals = ind_1h['atr14']
    from backend.app.core.indicators import sma
    atr_sma50 = sma(atr_vals, 50)
    atr_regime_ok = atr_vals >= (config.atr_regime_ratio * atr_sma50)

    counts = {
        "total": len(df_1h),
        "adx_ok": 0,
        "macd_mag_ok": 0,
        "atr_regime_ok": 0,
        "rsi_ok": 0,
        "macd_conf_ok": 0,
        "trend_ok": 0,
        "final_signal": 0
    }

    for i in range(1, len(df_1h)):
        # Filter 1: ADX minimum
        if ind_1h['adx'][i] >= config.adx_min:
            counts["adx_ok"] += 1
            # Filter 2: MACD histogram magnitude
            if abs(ind_1h['macd_hist'][i]) >= config.macd_hist_min:
                counts["macd_mag_ok"] += 1
                # Filter 3: Volatility Regime
                if atr_regime_ok[i]:
                    counts["atr_regime_ok"] += 1
                    
                    trend = trend_col[i]
                    # Filter 4: RSI Reversal
                    is_long_rsi = (ind_1h['rsi14'][i-1] < config.rsi_oversold) and ind_1h['is_green'][i]
                    is_short_rsi = (ind_1h['rsi14'][i-1] > config.rsi_overbought) and ind_1h['is_red'][i]
                    if is_long_rsi or is_short_rsi:
                        counts["rsi_ok"] += 1
                        
                        # Filter 5: MACD Histogram Confirmation
                        is_long_macd = ind_1h['macd_hist'][i] > ind_1h['macd_hist'][i-1]
                        is_short_macd = ind_1h['macd_hist'][i] < ind_1h['macd_hist'][i-1]
                        if (trend == 1 and is_long_rsi and is_long_macd) or (trend == -1 and is_short_rsi and is_short_macd):
                            counts["final_signal"] += 1

    print(counts)

if __name__ == "__main__":
    debug_signals()
