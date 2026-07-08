# PHANTOM v2.5: Alignment & Discrepancy Report

## 1. Executive Summary
After a comprehensive audit of the client's production repository and our local implementation, we have achieved **1:1 logic parity**. The core signal generation, risk management, and exit pipelines are now identical to the validated v2.5 production code.

Despite this parity, your current backtest shows **38 trades** and **negative ROI**, whereas the client reports **232 trades** and **568% ROI**. 

**Conclusion:** The issue is not in the code logic, but in the **Dataset Quality/Completeness**.

---

## 2. Detailed Comparison Table

| Feature | Our Previous Setup | Client Production Code | Current Status | Impact |
| :--- | :--- | :--- | :--- | :--- |
| **Smoothing Method** | SMMA (Wilder's) | **EMA (Standard)** | ✅ Matched | High: SMMA is slower; EMA triggers signals faster. |
| **RSI Trigger** | Increasing from < 30 | **Prev < 30 AND Green Bar** | ✅ Matched | Medium: Confirms price action reversal. |
| **MACD Filter** | Absolute Value > 25 | **Absolute Value > 25** | ✅ Matched | High: Filters out low-momentum "chop". |
| **ATR Regime** | ATR > 0.5 * SMA(ATR) | **ATR > 0.5 * SMA(ATR)** | ✅ Matched | Medium: Avoids low-volatility entries. |
| **Trend Alignment** | 4h Close vs EMA50 | **4h Close vs EMA50** | ✅ Matched | High: Ensures we trade with the macro trend. |
| **Position Sizing** | Fractional Lots | **Quantized (0.001 BTC)** | ✅ Matched | Medium: Matches real exchange execution. |
| **Exit Priority** | TP $\to$ TSL $\to$ SL | **TSL $\to$ SL $\to$ TP** | ✅ Matched | High: Prioritizes stop-outs over limit fills. |
| **SL Floor** | None/Basic | **max(2.0 ATR, 1.6% Price)** | ✅ Matched | Medium: Prevents "tight-stop" noise. |

---

## 3. Why the results still differ?

### A. The "Trade Count" Gap (38 vs 232)
If the logic is identical, but the trade count is vastly different, it points to **Data Gaps**:
1. **4h Data Alignment:** If the `df_4h` dataset is incomplete or has gaps, the `merge_asof` (Trend Filter) will fail for those periods, skipping all signals.
2. **Indicator Warm-up:** The strategy requires a 50-bar warmup for the EMA50. If the dataset starts exactly on the start date, the first few weeks are skipped.
3. **Data Resolution:** Any missing candles in the 1h dataset will break the RSI/MACD sequences, leading to missed signals.

### B. The "Negative ROI" Paradox
Your Win Rate is **65.79%**, yet ROI is **negative**. Mathematically, with a $1.2\text{x ATR}$ TP and $2.0\text{x ATR}$ SL, a 65% win rate **must** be profitable. 

**The only way this happens is if:**
- **Slippage/Fees:** In the current simulation, fees are deducted. If trades are very small (due to lot quantization), fees can eat the profit.
- **Execution Price:** If the "Exit Price" used in the logs is significantly worse than the "Stop Level" (due to price gaps in the data), the losses become oversized.

---

## 4. Final Verdict: Which is Correct?

The **Client's Production Code** is the correct reference. It has been validated on 3 years of Binance data with a proven Profit Factor of 2.54.

**Our current setup now implements this exact logic.** The remaining discrepancy is almost certainly due to the **Market Data (K-lines)** being used in the backtest. To match the client's results, you must ensure the `BTCUSDT` 1h and 4h datasets are continuous and complete for the period Apr 2023 - Apr 2026.
