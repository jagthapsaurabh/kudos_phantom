"""Parity test: vectorized StrategyService vs the original v2.5 loop implementation."""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
import numpy as np
import pandas as pd
from backend.app.core.indicators import compute_indicators, sma
from backend.app.core.strategy import StrategyService, PhantomV2Config


def legacy_generate_signals(config, df_1h, df_4h):
    """Original v2.5 loop (kept here verbatim from git history for parity testing)."""
    df_1h = df_1h.sort_index()
    df_4h = df_4h.sort_index()
    ind_1h = compute_indicators(df_1h)
    ind_4h = compute_indicators(df_4h)
    df_1h_with_trend = pd.merge_asof(
        df_1h,
        pd.DataFrame({'ema50_4h': ind_4h['ema50']}, index=df_4h.index),
        left_index=True, right_index=True, direction='backward'
    )
    trend_col = np.where(df_1h['close'].values > df_1h_with_trend['ema50_4h'].values, 1, -1)
    atr_vals = ind_1h['atr14']
    atr_sma50 = sma(atr_vals, 50)
    atr_regime_ok = atr_vals >= (config.atr_regime_ratio * atr_sma50)
    signals = np.zeros(len(df_1h))
    for i in range(1, len(df_1h)):
        if ind_1h['adx'][i] < config.adx_min: continue
        if abs(ind_1h['macd_hist'][i]) < config.macd_hist_min: continue
        if not atr_regime_ok[i]: continue
        trend = trend_col[i]
        is_long_rsi = (ind_1h['rsi14'][i-1] < config.rsi_oversold) and ind_1h['is_green'][i]
        is_short_rsi = (ind_1h['rsi14'][i-1] > config.rsi_overbought) and ind_1h['is_red'][i]
        is_long_macd = ind_1h['macd_hist'][i] > ind_1h['macd_hist'][i-1]
        is_short_macd = ind_1h['macd_hist'][i] < ind_1h['macd_hist'][i-1]
        if trend == 1 and is_long_rsi and is_long_macd:
            signals[i] = 1
        elif trend == -1 and is_short_rsi and is_short_macd:
            signals[i] = -1
    return signals


def main():
    df_1h = pd.read_csv('backend/data/btc_1h.csv', index_col=0, parse_dates=True)
    df_4h = pd.read_csv('backend/data/btc_4h.csv', index_col=0, parse_dates=True)
    ok = True
    for cfg in [PhantomV2Config(),
                PhantomV2Config(adx_min=12, macd_hist_min=5, rsi_oversold=35, rsi_overbought=65, atr_regime_ratio=0.3)]:
        svc = StrategyService(cfg)
        new_sig = svc.generate_signals(df_1h.copy(), df_4h.copy())
        old_sig = legacy_generate_signals(cfg, df_1h.copy(), df_4h.copy())
        same = np.array_equal(new_sig, old_sig)
        n_new = int((new_sig != 0).sum())
        n_old = int((old_sig != 0).sum())
        print(f"config adx={cfg.adx_min} hist={cfg.macd_hist_min}: signals match={same} (new={n_new}, old={n_old})")
        if not same:
            diff = np.where(new_sig != old_sig)[0][:10]
            print("  first diff bars:", diff)
            ok = False
    print("PARITY:", "PASS" if ok else "FAIL")


if __name__ == '__main__':
    main()
