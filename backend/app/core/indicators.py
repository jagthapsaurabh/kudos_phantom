import numpy as np
import pandas as pd

def ema(data: np.ndarray, period: int) -> np.ndarray:
    out = np.empty_like(data, dtype=np.float64)
    out[0] = data[0]
    alpha = 2.0 / (period + 1)
    for i in range(1, len(data)):
        out[i] = alpha * data[i] + (1 - alpha) * out[i - 1]
    return out

def sma(data: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(data).rolling(period, min_periods=1).mean().values.astype(np.float64)

def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    true_range = np.maximum(
        high - low,
        np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)),
    )
    # Client code uses EMA for ATR
    return ema(true_range, period)

def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(close, prepend=close[0])
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    # Client code uses EMA for RSI average gains/losses
    avg_gain = ema(gains, period)
    avg_loss = ema(losses, period)
    avg_loss = np.where(avg_loss < 1e-10, 1e-10, avg_loss)
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

def adx_di(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14):
    n = len(close)
    plus_dm, minus_dm, tr = np.zeros(n), np.zeros(n), np.zeros(n)
    for i in range(1, n):
        up, down = high[i] - high[i-1], low[i-1] - low[i]
        plus_dm[i] = max(up, 0) if up > down else 0
        minus_dm[i] = max(down, 0) if down > up else 0
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    # Client code uses EMA for ADX smoothing
    atr_v = ema(tr, period)
    safe = np.where(atr_v > 1e-10, atr_v, 1e-10)
    pdi, mdi = 100 * ema(plus_dm, period) / safe, 100 * ema(minus_dm, period) / safe
    dx = 100 * np.abs(pdi - mdi) / np.where(pdi + mdi > 0, pdi + mdi, 1)
    return ema(dx, period), pdi, mdi

def macd(close: np.ndarray, fast=12, slow=26, signal_period=9):
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal_period)
    return macd_line, signal_line, macd_line - signal_line

def compute_indicators(df: pd.DataFrame, macd_fast: int = 12, macd_slow: int = 26, macd_signal: int = 9) -> dict[str, np.ndarray]:
    # Delta/Binance BTC perpetual mark-price candles are stored as mark_open /
    # mark_high / mark_low / mark_close. When present they are used for every
    # calculation (RSI/ADX/ATR/MACD/EMA and candle colour) and old seeded data
    # falls back to the trade-price OHLCV below.
    def _pick(prefix, fallback):
        return df[f"{prefix}close"].values.astype(np.float64) if f"{prefix}close" in df.columns \
            else df[fallback].values.astype(np.float64)

    o = _pick("mark_open", "open")
    h = _pick("mark_high", "high")
    l = _pick("mark_low", "low")
    c = _pick("mark_close", "close")
    v = df["volume"].values.astype(np.float64)
    ind = {"o": o, "h": h, "l": l, "c": c, "v": v, "n": len(c)}
    ind["atr14"] = atr(h, l, c, 14)
    ind["rsi14"] = rsi(c, 14)
    ind["ema50"] = ema(c, 50)
    ind["adx"], ind["pdi"], ind["mdi"] = adx_di(h, l, c, 14)
    # MACD periods are user-configurable (config.macd_fast/slow/signal); pass
    # them through so the backtest/paper/live runs honour the chosen values.
    ind["macd_line"], ind["macd_signal"], ind["macd_hist"] = macd(c, fast=macd_fast, slow=macd_slow, signal_period=macd_signal)
    ind["is_green"], ind["is_red"] = c > o, c < o
    return ind
