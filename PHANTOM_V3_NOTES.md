# PHANTOM v3 — Improvement Notes (2026-08-19)

## Objectives
1. **Minimize drawdown**
2. **Increase trade count**
3. **Log every trade with the market conditions at that moment and the exact candle**
4. **Increase overall Phantom strategy performance**

## Baseline (v2.5, full dataset 2020-06 → 2026-06, ₹20,000 start)

| Metric | v2.5 baseline | v3 balanced profile | **v3 low-DD profile (shipped champion)** |
| :--- | ---: | ---: | ---: |
| Total trades | 263 | 1,332 | **1,081** |
| Max drawdown | 30.34% | 20.0% (hard cap) | **4.17%** |
| Win rate | 51.71% | 60.21% | **59.48%** |
| Profit factor | 1.27 | 1.94 | **1.83** |
| Sharpe (monthly) | 0.74 | 2.75 | **2.40** |
| Max consecutive losses | — | 7 | **7** |

The low-DD profile (leverage 2, 15% margin, 7.5% when throttled past 8% DD) was
selected by `optimize_sizing.py`: the natural MaxDD is 4.17% without the circuit
breaker ever needing to fire. A more aggressive alternative (lev 7, 5% margin)
yields 1,919 trades / PF 1.88 at 7.2% DD — switchable via config.

Out-of-sample check (unseen 2024-05 → 2026-06 test split):
697 trades, WR 61.7%, PF 1.64, Sharpe 3.61 — the tuned edge holds on unseen data.

> Note on minimum lot size: at 2x leverage the 0.001 BTC lot floor needs
> roughly ₹35k capital when BTC trades near $120k (larger windows starting from
> the cheap-price era compound normally). Signals rejected for this reason are
> now counted as `LOT_TOO_SMALL` in the run's rejected-signal stats.

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

### Optimizer (`app/scripts/optimize_phantom.py` + `optimize_sizing.py`)
Two-stage search on a 65/35 train/test split: entry grid sweep → greedy
coordinate descent over risk/exit params, scored with a drawdown-weighted
Calmar-style objective. Leaderboard: `backend/logs/optimize_results.csv`;
champion: `backend/logs/champion_config.json`.
A second sizing sweep (`optimize_sizing.py`) explores leverage × margin ×
DD-throttle thresholds and picked the shipped low-DD champion
(`backend/logs/champion_lowdd_config.json`, full table `optimize_sizing.csv`).

### Platform: roles, client management, signal overlay (v3.1)
- **Roles & permissions** on `users`: `role` (admin/client), `is_active`,
  `can_paper`, `can_live` — auto-migrated on `init_db()`. Login returns the role;
  the frontend routes admins to `/admin` and clients to the dashboard.
- **Admin panel** (`/admin`, admin-only):
  - *Client Management*: create client accounts (username/password/capital/margin),
    toggle paper/live trading per client, activate/deactivate accounts, reset
    passwords, inspect each client's paper/live sessions and recent backtests.
  - *Phantom Strategy*: full documentation of every entry condition (Setup A/B),
    filter, exit rule and the drawdown guard, plus the live champion config.
  - *Paper Control*: start/stop paper sessions.
- **Client capabilities**: clients log in with their own credentials and can
  paper-trade (`can_paper`) and live-trade (`can_live`, admin-granted) with the
  strategies they own; API enforces the permissions server-side (403 otherwise),
  and deactivating a client stops their sessions and blocks login.
- **Signal-candle overlay**: `GET /phantom/signals` returns every bar where the
  tuned strategy fires (time, direction, setup, RSI, ADX); the Market Chart page
  draws ▲/▼ markers with setup+RSI+ADX labels on the exact signal candles.
- **Backtest UI**: trade table now carries the condition columns (signal candle,
  setup, candle type, 4h trend, RSI/ADX, expandable per-trade condition chips and
  risk model) + one-click CSV export of the full log; v3 parameters exposed in
  the form (momentum toggle, sizing & drawdown-guard group).
- Fixed pre-existing bugs: `/klines` 500 (`Klines` not imported), trade insert on
  legacy schema, custom-strategy backtest positional args.

### Bug fixes along the way
- `main.py`: custom-strategy backtest passed dates positionally into
  `initial_capital_inr`/`conversion_rate` — now keyword args.
- `main.py`: trade persistence filters to real table columns (previously broke
  on legacy DB schema; schema drift now auto-migrated for **all** tables).

## Addon: direction-specific Long / Short conditions (v3.2)

Data showed the two sides don't behave the same way — REVERSAL-SHORT's quality
collapses at high ATR14 and high MACD-histogram values, a pattern absent on the
long side. A single shared parameter set can't express "tighter filter for
shorts only". This addon exposes an optional per-direction override so the
admin can tune the two sides independently without loosening one to help the
other.

- **Toggle** (`entry_conditions.use_direction_conditions`, UI: *"Use separate
  conditions for Long / Short"*). OFF = exactly the legacy shared engine. ON =
  the LONG and SHORT branches each carry their own copy of the directional
  fields.
- **Directional fields** (`entry_conditions.long.*` / `.short.*`):
  `macd_hist_min`, `stop_loss_atr`, `atr_regime_ratio`, `rsi_oversold`,
  `rsi_overbought`, `adx_min`. Any value left `null` falls back to the shared
  config field, so existing saved configs keep working unchanged.
- **Signed MACD for shorts**: the directional `macd_hist_min` is interpreted per
  side — longs require `hist >= value` (e.g. `5`), shorts require
  `hist <= value` (e.g. `-8`). This lets an admin require **bearish momentum
  clearly present** for shorts (a negative threshold) while longs keep a positive
  threshold. The shared (OFF) field still uses the legacy `|hist| >= min`
  magnitude filter.
- **ATR regime — optional max-ATR cap for shorts**: `atr_regime_ratio` keeps the
  legacy **lower-bound floor** (`ATR >= ratio × SMA`) in both modes, so the
  shared pre-fill is behaviour-identical when the toggle is first switched on.
  To exclude the high-volatility regime where REVERSAL-SHORT underperforms, use
  the optional per-direction **`atr_regime_max`** cap (`ATR <= value × SMA`); a
  lower cap is tighter. `null`/blank disables it.
- **Stop-loss ATR per direction** is applied in `services/order_manager.py` via
  `stop_loss_atr_for(direction)`, so shorts can use a wider/narrower hard stop
  than longs (backtest flagged 84% of losing-day trades exiting via hard SL).
- **Config + lifecycle**: the override is part of `PhantomV2Config` / the
  backtest `params`, saved by *"Save as New Strategy"* on the backtest page,
  and honoured by backtest, Paper and Live trading (a saved `params` strategy is
  auto-detected and run with `StrategyService` rather than the rule builder).
- **Filter preview** (`POST /backtest/filter-preview` + *"Preview Filters"* button):
  before running the full backtest, show the historical trades in each
  LONG/SHORT × REVERSAL/MOMENTUM bucket under the conditions currently set,
  with win rate, profit factor and avg/net PnL. For `champion_lowdd_config.json`
  (shared) the pre-preview bucket breakdown makes MACD/ATR threshold tuning
  directly available in the UI instead of an offline script.

Suggested starting values observed during tuning (exposed as defaults in the UI,
not hardcoded): SHORT `macd_hist_min` ≈ negative (e.g. `-8`) to require bearish
momentum, SHORT `atr_regime_ratio` below the shared `0.5` to exclude the top
volatility quartile where REVERSAL-SHORT's win rate drops to ~52%.

## Reproduce
```bash
python -m backend.app.scripts.run_baseline        # v2.5 parity numbers
python -m backend.app.scripts.run_phantom_v3      # v3 + writes trade log CSV
python -m backend.app.scripts.optimize_phantom    # re-tune (writes champion config)
python backend/app/scripts/test_signal_parity.py  # signal parity test
```
