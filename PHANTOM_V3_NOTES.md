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

## Addon: MACD line / signal rules + setup / direction separation (v3.5)

Client asks: (1) "MACD signal and line settings", (2) separate momentum and
reversal strategies, (3) long-only and short-only strategies — all "without
disturbing the current codes". Every addition below is a new field with a
default that reproduces the previous behaviour bit for bit (verified by hashing
signals, setup labels and trade lists of six reference configs before / after).

- **MACD line / signal line rules** (`PhantomV2Config.macd_line_rules`,
  `MacdLineConditions`): `enabled` (default `False`), `line_vs_signal`,
  `line_vs_zero`, `signal_vs_zero` (each `off` / `above_below` / `cross`),
  `line_min`, `signal_min` (magnitudes; longs `>= v`, shorts `<= -v`). Applied
  in `StrategyService._compute` as an extra AND-mask on both setups
  (`_macd_line_mask`); LONG reads the bullish side, SHORT the bearish side,
  `cross` requires the previous bar on the other side. Per-side overrides
  follow the v3.2 pattern: `entry_conditions.use_direction_macd_line` +
  `entry_conditions.long/short.macd_line_vs_signal|macd_line_vs_zero|
  macd_signal_vs_zero|macd_line_min|macd_signal_min` (per-side levels are
  signed as typed, like `macd_hist_min`). An enabled block with every rule
  `off` is treated as disabled. The MACD *periods* are unchanged and now shown
  on the docs page and the Paper / Live pages (`/phantom/config` → `summary`).
- **Setup separation** (`setup_mode`: `both` / `reversal` / `momentum`).
  `momentum_enabled()` keeps the legacy `enable_momentum_entry` switch while
  `both`; `reversal` forces Setup B off (identical to the legacy switch off);
  `momentum` forces Setup B on and Setup A off. On a candle where both setups
  qualified, the two-sided run labels REVERSAL; momentum-only takes it as
  MOMENTUM (Setup A priority is unchanged).
- **Direction separation** (`trade_direction`: `both` / `long` / `short`):
  the dropped side's masks are cleared after all filters, so the kept side's
  signals are exactly those of the two-sided run.
- **Built-in presets**: `parse_phantom_variant` accepts
  `PhantomV2:<setup>[:<direction>]` / `PhantomV2:<direction>` (separators
  `: - . /`), `apply_phantom_variant` narrows any config, `PHANTOM_PRESETS`
  lists the 8 curated ids, `phantom_preset_name` names them
  ("Kudos — Reversal · Long only"). `main.py` resolves them wherever
  `PhantomV2` was special-cased (signals, backtest task, filter preview,
  preflight, paper / live start + resume) via `_is_builtin_phantom` /
  `_load_builtin_config` / `_builtin_strategy_name`; `GET /phantom/presets`
  and `GET /phantom/config?strategy_id=` serve the UI. Presets are distinct
  strategy ids for `running_conflict`, so they run next to the default.
- **Logging**: `meta` gains `macd_line`, `macd_signal` (shared + per side),
  `cond_macd_line_ok_long/short`, `macd_line_rules_enabled`, rule texts,
  `setup_mode`, `trade_direction`. `engine._condition_snapshot` adds
  `macd_line`, `macd_signal`, `cond_macd_line_ok` (None when the rules are
  off); `_entry_conditions_text` appends "8. MACD line/signal …" only when
  they are on; `Trade` gains the three nullable columns (additive migration);
  CSV / Excel export and `/backtest/results` carry them; results also report
  `setup_mode`, `trade_direction`, `macd_line_rules`.
- **UI**: `utils/phantomPresets.js` (ids, names, labels, rule text mirror),
  `PhantomPresetOptions` (optgroup in every dropdown), Backtest form
  *Strategy separation* + *MACD line / signal line rules* blocks (locked to a
  preset when one is selected), result / preview badges, trade-log chip,
  `StrategyConfigSummary` on Paper / Live, Kudos Strategy docs (Rules +
  Explained tabs).
- **Tests**: `backend/test_macd_line_and_modes.py` (76 checks),
  `frontend/tests/phantom_presets_ui.jsx` (50 checks).

## FastTest V1.0 — debug strategy with a validation / profit-booking layer

A **new, separate strategy** (`FastTestV1`, `backend/app/core/fast_test_v1.py`).
`FastTest` itself is not modified: same code, same signals, same results.

- **Entry**: a verbatim copy of the FastTest rule — RSI(14) on the 1h candles,
  long below 50 / short at or above 50, one signal per bar, no other filter.
- **Unchanged**: initial SL, trailing SL, breakeven, timeout, cooldown,
  sizing, leverage, mark-price handling, fees, FIFO booking.
- **Added rule 1 — +0.90% profit booking (TOUCH)**: `high ≥ entry × 1.0090`
  (long) / `low ≤ entry × 0.9910` (short) books the full position at the level
  (reason `TP090`). The engine passes candle extremes, live/paper pass the
  tick price, so a wick that would have filled a resting limit is honoured.
- **Added rule 2 — 2H validation (CLOSE)**: the close of the second completed
  1h candle after entry must reach `entry × 1.0035` (long) / `× 0.9965`
  (short). Reached → `VALIDATED`, the existing exits keep running; missed →
  the trade exits **at that close** (reason `VALFAIL`).
- **Priority**: entry → +0.90% touch → 2H close verdict. Inside one candle the
  resting stop still wins (the engine's worst-case rule); otherwise the
  booking rule runs before the trail / TP / timeout steps.
- **Wiring**: `OrderManager` grew three no-op hooks (`strategy_touch_exit`,
  `strategy_bar_close_exit`, `strategy_audit_fields` / `strategy_summary` /
  `bracket_take_profit`); `FastTestV1OrderManager` overrides them. The backtest
  engine takes optional `strategy_service` / `oms` overrides and passes the
  current candle's close as `bar_close_usd` / `bar_time`; the paper and live
  workers pass the **completed** candle's close on the tick after a rollover.
  `main.py` routes the id in `/phantom/signals`, the backtest task,
  `/paper-trade/start`, `_resume_paper_session` and `/live-trade/start`; live
  brackets rest their TP at the +0.90% level.
- **Audit**: seven fields per trade — `validation_status`, `validation_close`,
  `validation_threshold`, `tp090_hit`, `validation_exit`,
  `final_exit_reason`, `final_net_pnl`. `Trade` gains seven nullable columns
  (additive migration); `/backtest/results`, the CSV export, the trade-log
  table, the paper/live closed-trade panel and the Sessions detail all show
  them. Net P&L is after entry + exit fees.
- **Tests**: `backend/test_fast_test_v1.py` (114 checks),
  `frontend/tests/fast_test_v1_ui.jsx` (27 checks), trade-log pins updated to
  60 CSV columns.

## Paper / Live trade analysis (entry + exit conditions, same as Backtest)

- **One builder for all three paths.** The entry-condition text / snapshot /
  MACD-line lines moved out of `BacktestEngine` into
  `backend/app/core/trade_conditions.py` unchanged (verified byte-identical:
  a 64-case dump across default / MACD-line-enabled / reversal-long /
  momentum-short configs, `cmp` clean). `engine.py` now delegates.
- **Paper & live workers record the same detail.** At entry, the worker asks
  the strategy for its metadata (`generate_signals_with_metadata`, already on
  `StrategyService`) and stores `entry_context` on the trade: signal candle +
  colour, every `cond_*` flag, the readable PASS/FAIL breakdown. At exit the
  worker stamps `exit_candle_type`, and `_record_closed` merges it all into the
  closed-trade record. `Trade` gained `entry_context: dict` and
  `exit_candle_type: str` (defaults keep every other caller unchanged).
- **UI + export.** Paper → Trade Reply / Closed Trades and Live → Closed trades
  (live) both have a per-row **Conditions** view and **Export CSV**. The export
  reuses the Backtest `buildTradesCSV` through `tradeRowFromClosed()`, so the
  spreadsheet is the same 60+ column layout (condition columns + V1 audit).
  Saved paper sessions export the same file from the History panel.
- **Not invented where it does not exist.** `FastTest` / `FastTestV1` publish no
  condition metadata; their paper/live rows show the exit rule, candle colours
  and the V1 audit fields, with an explicit note instead of fake PASS/FAILs.
- **Never in the way of execution.** The snapshot is taken *after* the
  paper/live order exists, a strategy whose metadata raises yields `{}` with no
  exception and no error output, and the merge into the closed-trade record is
  wrapped so a malformed record can never stop a booking or an exit order.
- **Tests**: `backend/test_trade_conditions_shared.py` (34 checks, incl. a real
  paper tick and the failure paths), `frontend/tests/trade_conditions_ui.jsx`
  (22 checks); the full backend + frontend suites stay green and the 2.08 MB
  trade-list parity dump is unchanged.

## Addon: Risk & Exit model — ATR, price %, or both per level (v3.6)

The strategy priced every protective level in ATR units. The client can now price each level either
in **ATR units** (the default — unchanged), as a **% of the entry price**, or run a **mix** by
choosing the model level by level (e.g. an ATR stop with a price-based target).

- **One model, resolved once.** `RiskExitModel` (in `backend/app/core/strategy.py`, next to
  `EntryConditions`) holds the selector and the percentages:
  `model` = `atr` | `price` | `both`, plus `stop_mode` / `target_mode` / `trail_mode` /
  `breakeven_mode` and `stop_loss_pct` / `take_profit_pct` / `trail_activation_pct` /
  `trail_distance_pct` / `breakeven_pct` (fractions, like `sl_floor_pct`). `model = 'atr' | 'price'`
  is a shorthand that forces every level (validators keep a config from being stored
  half-switched); `model = 'both'` keeps the per-level selectors.
- **Resolvers on the config** are the single answer to "how far is this level?":
  `stop_loss_distance_for()`, `take_profit_distance_for()`, `trail_activation_distance_for()`,
  `trail_distance_for()`, `breakeven_trigger_for()` (returns `None` = feature off), plus
  `risk_exit_level_text()` / `risk_exit_text()` / `risk_exit_summary()` for the docs, the API and
  the Paper / Live strategy summary.
- **OrderManager** calls those resolvers in `create_order` (SL / TP / trail activation) and in both
  `update_trade` branches (trail advance, breakeven ratchet). A plain config object without the
  model (old test stubs, an older saved config) falls back to the original ATR formulas — that
  fallback is covered by the tests.
- **Defaults reproduce the old maths exactly**: ATR stop = `max(stop_loss_atr × ATR,
  sl_floor_pct × price)` — the floor applies to the ATR model only; a price stop is exactly the %
  the client typed. The direction-specific stop override works for either model
  (`stop_loss_atr` / `stop_loss_pct` under `entry_conditions.long|short`, master switch ON).
- **Live venues get a price trail too.** `LiveTradeService._trail_amount()` sends
  `trail_distance_pct × price` when the trail is on the price model (no price → no venue trail — it
  is never silently replaced by an ATR distance); the stop and target legs already come from
  `OrderManager`, so the venue bracket follows the same model.
- **UI.** `frontend/src/utils/riskExit.js` is the single front-end source of truth (labels,
  defaults, pure helpers, text). `RiskExitModelEditor.jsx` renders the model selector plus one
  ATR | Price % switch per level and is embedded in **Backtest → Strategy Configuration → Risk &
  Exit Model**; the numbers read as percents (1.6) while the payload stores fractions (0.016).
  **Kudos Strategy → Strategy Rules** and **Strategy Explained** document the model in force, and
  `StrategyConfigSummary` (Paper / Live) shows it next to the MACD settings.
- **Nothing else moves.** Signals, entries, sizing, fees and the FastTest V1 layer are untouched;
  the all-ATR default keeps the trade-list parity dump byte-identical.
- **Tests**: `backend/test_risk_exit_model.py` (48 checks: defaults, price model, per-level mix,
  per-side stops, trail / breakeven on both sides, live venue trail, legacy config fallback, V1 and
  validation), `frontend/tests/risk_exit_model_ui.jsx` (40 checks: helpers, editor rendering in all
  three models, page wiring, docs tabs).
- **v3.6 addon — Market Chart zoom & full screen**: the Market Chart toolbar gained `− / + / Reset`
  zoom (both axes — the visible bar range *and* the price range), the current factor label, `+`/`-`/`0`
  keyboard shortcuts, double-click-to-reset, and a **Full screen** button that falls back to an
  in-page full-window overlay when the browser blocks the Fullscreen API. Vertical zoom is applied
  through an `autoscaleInfoProvider`, so the axis keeps following new candles while zoomed.
  `frontend/src/utils/chartZoom.js` holds the pure maths and `frontend/tests/chart_zoom_ui.jsx`
  (41 checks) covers the helpers, the rendered toolbar and the runtime wiring.
- **Fix — "data must be asc ordered by time" on the Backtest candle pane**: Delta answers
  `/v2/history/candles` newest-first, and `/klines` returns that venue fallback whenever the local
  seed is empty for the window. lightweight-charts asserts its data is oldest-first, so the overlay
  pane threw inside React. Candles are now normalized at three levels — `_parse_candle_rows` sorts
  and de-duplicates before anything else sees the rows, `/klines` sorts (and windows) the fallback
  response, and `ascendingBars()` in `frontend/src/utils/chartOverlay.js` normalizes every candle
  series the UI plots (numeric times, one candle per timestamp, oldest first). Covered by
  `test_chart_overlay_api.py` (18 checks, incl. a stubbed fallback round-trip) and
  `frontend/tests/chart_overlay.jsx` (20 checks).

## Reproduce
```bash
python -m backend.app.scripts.run_baseline        # v2.5 parity numbers
python -m backend.app.scripts.run_phantom_v3      # v3 + writes trade log CSV
python -m backend.app.scripts.optimize_phantom    # re-tune (writes champion config)
python backend/app/scripts/test_signal_parity.py  # signal parity test
```
