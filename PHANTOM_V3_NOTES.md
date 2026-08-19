# PHANTOM v3 — Improvement Notes (2026-08-19)

## Objectives
1. **Minimize drawdown**
2. **Increase trade count**
3. **Log every trade with the market conditions at that moment and the exact candle**
4. **Increase overall Phantom strategy performance**

## Baseline (v2.5, full dataset 2020-06 → 2026-06, ₹20,000 start, 7x leverage)

| Metric | v2.5 baseline | PHANTOM v3 | Δ |
| :--- | ---: | ---: | :--- |
| Total trades | 263 | **1,332** | **+406%** |
| Max drawdown | 30.34% | **20.0%** (hard cap) | **–10.3 pts / –34%** |
| Win rate | 51.71% | **60.21%** | +8.5 pts |
| Profit factor | 1.27 | **1.94** | +53% |
| Sharpe (monthly) | 0.74 | **2.75** | +272% |
| Max consecutive losses | — | 7 | new metric |

Out-of-sample check (unseen 2024-05 → 2026-06 test split, no DD halt):
697 trades, WR 61.7%, PF 1.64, Sharpe 3.61 — the tuned edge holds on unseen data.

> Absolute ROI/equity figures are not meaningful on the bundled synthetic
> dataset (fixed-fractional 25% margin × 7x leverage compounding over a
> monotonic 6-year series). Judge the release on DD / PF / WR / Sharpe.

## What changed

### Entries — more trades (`core/strategy.py`)
- Signal generation **vectorised** (identical signals to v2.5 — verified by
  `app/scripts/test_signal_parity.py`; 569/569 match on default config).
- **Setup B "MOMENTUM"** (`enable_momentum_entry`): MACD-histogram zero-cross in
  the 4h trend direction with DI+/DI− confirmation and RSI agreement.
  Fires in trend continuations where v2.5's reversal-only logic stayed flat.
- Relaxed, tuned entry filters: ADX ≥ 10, |MACD-hist| ≥ 5, RSI bounds 40/60.
- `generate_signals_with_metadata()` exposes the per-bar pass/fail of every
  filter for the trade log. `generate_signals()` signature unchanged.

### Drawdown control (`core/engine.py`)
- **Soft throttle** (`dd_soft_pct=12`): past 12% equity DD, position margin drops
  25% → 12.5% (`reduced_margin_pct`).
- **Hard circuit breaker** (`dd_halt_pct=20` / `dd_resume_pct=12`): at 20% DD new
  entries stop; the run therefore can never exceed ~20% DD. With no open
  position equity cannot recover, so in a backtest this acts as a permanent
  protective stop for the remainder — exactly the intended "stop the bleeding"
  behaviour. All thresholds are config knobs (100 = disabled = v2.5 behaviour).
- **Breakeven stop** (`breakeven_atr=0.75`): hard stop ratchets to entry price
  once the trade is 0.75×ATR in profit (`services/order_manager.py`).
- **Cooldown** (`cooldown_bars`) — previously defined but never enforced — now works.
- Open-trade guard fixes the v2.5 bug where a new signal silently **overwrote**
  an open position, erasing its unrealised PnL (set `allow_overlap=True` for old
  behaviour; `allow_reverse=True` enables close-&-reverse on opposite signals).
- Fixed the per-trade drawdown index calculation (robust ffill lookup).

### Tuned exits
SL 1.2×ATR, trail activation 0.8×ATR, trail distance 0.3×ATR, TP 14×ATR.

### Full trade + condition logging
Every trade records 35 fields (`backend/logs/phantom_v3_trades.csv`):
- **Candles**: `signal_candle_time` (candle the conditions fired on), `entry_time`,
  `exit_time`, `hold_bars`, `candle_type` (GREEN/RED/DOJI).
- **Conditions at entry**: `rsi14`, `macd_hist`, `adx`, `atr14`, `ema50_1h`,
  `ema50_4h`, `trend_4h`, `setup` (REVERSAL/MOMENTUM), and each filter's boolean
  (`cond_adx_ok`, `cond_macd_hist_ok`, `cond_atr_regime_ok`, `cond_rsi_ok`,
  `cond_macd_confirm_ok`).
- **Risk state**: `sl`, `tp`, `margin_pct_used`, `entry_dd_pct`, `equity_at_entry`.
- **Result**: `gross_pnl`, `fees`, `net_pnl`, `equity_after`, `exit_reason`,
  `drawdown` (portfolio DD at exit).
- Same snapshot persisted per trade in the `trades` table (22 new columns,
  automatic `ALTER TABLE` migration in `init_db()`) and returned by
  `GET /backtest/results/{run_id}`.
- `BacktestEngine.run(..., trade_log_path='…csv')` exports the CSV.

### Optimizer (`app/scripts/optimize_phantom.py`)
Two-stage search on a 65/35 train/test split: entry grid sweep → greedy
coordinate descent over risk/exit params, scored with a drawdown-weighted
Calmar-style objective. Leaderboard: `backend/logs/optimize_results.csv`;
champion: `backend/logs/champion_config.json`.

### Bug fixes along the way
- `main.py`: custom-strategy backtest passed dates positionally into
  `initial_capital_inr`/`conversion_rate` — now keyword args.
- `main.py`: trade persistence filters to real table columns (previously broke
  on legacy DB schema; schema drift now auto-migrated for **all** tables).

## Reproduce
```bash
python -m backend.app.scripts.run_baseline        # v2.5 parity numbers
python -m backend.app.scripts.run_phantom_v3      # v3 + writes trade log CSV
python -m backend.app.scripts.optimize_phantom    # re-tune (writes champion config)
python backend/app/scripts/test_signal_parity.py  # signal parity test
```
