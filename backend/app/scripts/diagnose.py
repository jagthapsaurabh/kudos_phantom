import pandas as pd
import numpy as np
from backend.core.indicators import compute_indicators

def diagnose_strategy():
    print("Diagnosing Strategy Filters...")
    df_1h = pd.read_csv("backend/data/btc_1h.csv", index_col=0, parse_dates=True)
    df_4h = pd.read_csv("backend/data/btc_4h.csv", index_col=0, parse_dates=True)
    
    # Force numeric
    for df in [df_1h, df_4h]:
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    
    print("Data Sample:\n", df_1h[['close', 'volume']].head())
    
    ind_1h = compute_indicators(df_1h)
    ind_4h = compute_indicators(df_4h)
    
    # Drop NAs for the diagnosis stats
    adx = ind_1h['adx'].dropna()
    macd_hist = ind_1h['macd_hist'].dropna()
    rsi = ind_1h['rsi14'].dropna()
    
    print(f"ADX: Mean={adx.mean():.2f}, Max={adx.max():.2f}, Min={adx.min():.2f}, % > 22 = {(adx >= 22).mean()*100:.2f}%")
    print(f"MACD Hist: Mean={macd_hist.mean():.2f}, Max={macd_hist.max():.2f}, Min={macd_hist.min():.2f}, % abs > 25 = {(macd_hist.abs() >= 25).mean()*100:.2f}%")
    print(f"RSI: Mean={rsi.mean():.2f}, Max={rsi.max():.2f}, Min={rsi.min():.2f}, % < 30 = {(rsi < 30).mean()*100:.2f}%, % > 70 = {(rsi > 70).mean()*100:.2f}%")
    
    atr = ind_1h['atr14'].dropna()
    atr_sma = atr.rolling(50).mean().dropna()
    # Need to align for regime check
    common = atr.index.intersection(atr_sma.index)
    regime = atr[common] >= (0.5 * atr_sma[common])
    print(f"ATR Regime: % Pass = {regime.mean()*100:.2f}%")
    
    is_green = (df_1h['close'] > df_1h['open'])
    rsi_rev = (ind_1h['rsi14'].shift(1) < 30) & is_green
    print(f"RSI Reversal Long: % Pass = {rsi_rev.mean()*100:.2f}%")

if __name__ == "__main__":
    diagnose_strategy()
