# Kudos Phantom — Commit & PR History Summary

> **Purpose:** Quick reference for tracking every feature, fix, and rollback point across all merged pull requests.  
> Each section lists the PR number, merge date, all commits (with SHA), what changed, and what files were touched — so you can jump to any SHA to roll back a specific feature.

---

## Table of Contents

| PR | Title | Merged |
|----|-------|--------|
| [PR #1](#pr-1--phantom-v31--mindd-417-4x-trades-full-tradecondition-logging-client-management) | PHANTOM v3.1 — MinDD 4.17%, 4× trades, full trade+condition logging, client management | 2026-08-20 |
| [PR #2](#pr-2--phantom-v33--admin-protection-paper-trade-logs-named-backtests-per-run-delete-confirmations--ui-polish) | PHANTOM v3.3 — Admin protection, paper trade logs, named backtests, per-run delete, confirmations & UI polish | 2026-08-22 |
| [PR #3](#pr-3--fix-market-chart-paper-trading-backtest-ui--client-management) | Fix market chart, paper trading, backtest UI & client management | 2026-08-25 |
| [PR #4](#pr-4--admin-exchange-fees-and-multi-broker-market-data) | Admin exchange fees and multi-broker market data | 2026-08-25 |
| [PR #5](#pr-5--add-direction-specific-longshort-condition-overrides--filter-preview) | Add direction-specific Long/Short condition overrides + filter preview | 2026-08-26 |
| [PR #6](#pr-6--improve-optimizer-ux-compact-sidebar-date-pickers-and-dashboard-stats) | Improve optimizer UX, compact sidebar, date pickers, and dashboard stats | 2026-08-26 |
| [PR #7](#pr-7--fix-chart-fullscreen-error-and-streamline-backtest-results-ux) | Fix chart fullscreen error and streamline backtest results UX | 2026-08-26 |
| [PR #8](#pr-8--delta-exchange-seeding-fix--configurableper-direction-macd--live-paper-trade-metrics--ist-times) | Delta Exchange seeding fix + configurable/per-direction MACD + live paper-trade metrics + IST times | 2026-08-26 |
| [PR #9](#pr-9--admin-ui-reconfiguration--delta-seed-diagnostics--paper-trade-exit-details) | Admin UI reconfiguration + Delta seed diagnostics + paper trade exit details | 2026-08-27 |
| [PR #10](#pr-10--add-resumable-market-data-seeding-and-daily-sync) | Add resumable market data seeding and daily sync | 2026-08-27 |
| [PR #11](#pr-11--per-side-atr-operator-for-longshort-persistent-paper-trade-history-and-trade-log-candlecolour--full-export) | Per-side ATR operator for Long/Short, persistent paper-trade history, and trade-log candle/colour + full export | 2026-08-27 |
| [PR #12](#pr-12--fix-exit-distribution-tooltip-black-on-black-text--add-commit-history-summary-doc) | Fix Exit Distribution tooltip (black-on-black text) + add commit history summary doc | 2026-08-27 |
| [PR #13](#pr-13--show-per-trade-pnl-fees-and-booked-pnl-in-trade-logs--exports) | Show per-trade PnL, fees and booked PnL in trade logs + exports | 2026-08-27 |
| [PR #14](#pr-14--btc-perpetual-mark-pricing-skip-new-trade-windows-and-a-full-live-order-management-terminal) | BTC perpetual mark pricing, skip-new-trade windows, and a full live order-management terminal | 2026-08-28 |
| [PR #15](#pr-15--binancedelta-full-history-seeding-2020today-corrupt-data-repair-unbreakable-long-seeds--navbar-crash-fix) | Binance/Delta full-history seeding (2020→today), corrupt-data repair, unbreakable long seeds + navbar crash fix | 2026-08-29 |

---

## PR #1 — PHANTOM v3.1 — MinDD 4.17%, 4× trades, full trade+condition logging, client management

**Merged:** 2026-08-20  
**Branch:** `arena/01a019df-kudos-phantom`

### Commits

| SHA | Message |
|-----|---------|
| `00cd26a4` | PHANTOM v3: cut drawdown 30.3% → 20.0%, 5.1× more trades, full trade+condition logging |
| `43d191ec` | v3.1: MinDD 4.17% sizing champion, client management, strategy docs, signal overlay |

### What Changed

#### Strategy Engine (`core/strategy.py`, `core/engine.py`, `order_manager.py`)
- Vectorized signal generation (bit-identical to v2.5 loop — parity-tested 569/569 signals)
- New **Momentum Continuation** setup (Setup B): MACD-hist zero-cross + DI confirmation in 4h trend direction, alongside the existing RSI-reversal setup (Setup A)
- Drawdown guard: soft throttle (size cut past 8% DD) + hard circuit breaker (halt at 20% DD, resume at 12%) + breakeven stop at +0.75×ATR
- Enforced `cooldown_bars` (was dormant), fixed open-trade overwrite bug, optional close-&-reverse
- **Champion sizing**: leverage 2, 15% margin, soft throttle 8%

#### Trade + Condition Logging
- Every trade logs **35 fields**: signal/entry/exit candle, setup, candle type, 4h trend, RSI/MACD-hist/ADX/ATR/EMA snapshot, every filter's pass/fail
- Exported to `backend/logs/phantom_v3_trades.csv`
- Persisted in DB (22 new columns, auto-migrated), returned by `GET /backtest/results/{id}`

#### Optimization Tooling
- `optimize_phantom.py` — staged entry-grid sweep → greedy risk tuning with Calmar-style objective on 65/35 train/test split
- `optimize_sizing.py` — leverage × margin × DD-throttle sweep

#### Platform: Roles & Clients
- `users` gains `role`, `is_active`, `can_paper`, `can_live` (auto-migrated)
- Login returns role & permissions; deactivated accounts blocked
- Admin endpoints: client CRUD, permission toggles, password reset, deactivate
- New `GET /phantom/config` + `GET /phantom/signals`

#### Frontend
- **Admin Panel** (`/admin`, admin-only): Client Management tab, Phantom Strategy documentation tab, Paper Control tab
- **Backtest page**: condition columns in trade table, expandable condition chips, CSV export, v3 param groups with champion defaults
- **Market Chart**: toggleable signal-candle overlay (▲/▼ markers with setup/RSI/ADX labels)
- Fixed `/chart` & `/live` routes, role-aware login redirect, Navbar Admin link

#### Performance Results (full dataset 2020-06 → 2026-06, ₹20,000 start)
| Metric | v2.5 Baseline | PHANTOM v3.1 |
|--------|:---:|:---:|
| Max drawdown | 30.34% | **4.17%** (−86%) |
| Total trades | 263 | **1,081** (+311%) |
| Win rate | 51.7% | **59.5%** |
| Profit factor | 1.27 | **1.83** |
| Sharpe (monthly) | 0.74 | **2.40** |

> **Rollback target:** SHA `00cd26a4` restores the original v3 engine before client management. SHA `43d191ec` is the full v3.1 state.

---

## PR #2 — PHANTOM v3.3 — Admin protection, paper trade logs, named backtests, per-run delete, confirmations & UI polish

**Merged:** 2026-08-22  
**Branch:** `arena/01a028f6-kudos-phantom`

### Commits

| SHA | Message |
|-----|---------|
| `0d61c6ea` | feat(v3.3): admin protection, paper trade logs, named backtests, per-run delete, confirmations & UI polish |

### What Changed

#### Admin & Client Protection
- Admin accounts cannot be deactivated — UI toggles locked, backend rejects the request with `Admin accounts cannot be deactivated`

#### Paper Trade Live Logs
- Each paper trade instance captures structured logs (`info/warn/error/trade` levels) in a buffer
- New **Live Logs panel** beside positions, auto-scrolling with 2 s refresh
- New API: `GET /paper-trade/logs?instance_key=...`

#### Paper Trade Summary Cards
- Real-time summary: total equity, margin used, running instances, open positions
- Instance cards show equity, open trade count, running status; auto-selects latest instance

#### Confirmation Dialogs
- All destructive actions now confirm: delete backtest run, clear history, deactivate/reactivate client, stop paper trade, logout
- Consistent modal component (cancel / confirm)

#### Change Password (Admin Panel)
- New **Change Password** tab — verifies current password, min 6 chars for new password
- New API: `POST /auth/change-password`

#### Named Backtests
- Optional **Run Name** field in Strategy Optimizer (defaults to "Phantom Optimization")
- Names shown in history cards

#### Per-Run Backtest Delete
- Hover reveals delete button; deletes run + all associated trade data
- New API: `DELETE /backtest/{run_id}`

#### UI Polish
- Dashboard: gradient stat cards, active sessions, system status panel
- Login: show/hide password toggle, loading state
- Navbar: role badge, logout confirmation
- Paper Trade: instance cards, log panel, summary stats
- Admin Panel: locked toggles for admin rows, purple admin highlight

#### Key Files Changed
- `backend/app/main.py`, `backend/app/services/paper_trader.py`
- `frontend/src/pages/PaperTrade.jsx`, `Backtest.jsx`, `AdminPanel.jsx`, `Dashboard.jsx`, `Login.jsx`
- `frontend/src/components/Navbar.jsx`, `frontend/src/index.js`

> **Rollback target:** SHA `0d61c6ea` — roll back to remove live logs, named backtests, and confirmation dialogs.

---

## PR #3 — Fix market chart, paper trading, backtest UI & client management

**Merged:** 2026-08-25  
**Branch:** `arena/01a034c2-kudos-phantom`

### Commits

| SHA | Message |
|-----|---------|
| `06f88acc` | Fix market chart (lightweight-charts v5), paper trading, backtest UI & client management |
| `b03c33a2` | Never version-control database files |
| `805b0cea` | Document database-untracking step for clean deployments |
| `1e6f8751` | Add Live Trade nav, TradingView-style chart tools & Chartink-like strategy builder |
| `a5ffa20e` | Add configurable trading capital (admin default) for backtests & paper trade |

### What Changed

#### Market Chart (was broken → TradingView-style)
- Migrated to **lightweight-charts v5 API** (`addSeries`/`createSeriesMarkers`). Old v4 API (`addCandlestickSeries` + `setMarkers`) caused the chart to never render
- Added volume histogram pane, crosshair, price/time scales, timeframe tabs, symbol selector, Phantom signal ▲/▼ markers
- `/klines` now returns `volume` + clean integer UTC timestamps
- Added `/symbols` endpoint; chart degrades gracefully when no data is available
- Market Chart link added to sidebar

#### Paper Trading (was showing nothing)
- Anchored SQLite DB to backend directory (`DATABASE_URL` override) — was silently hitting an empty DB when run from `backend/`
- Paper trader reads candles from local DB with Binance API fallback — works offline
- `/paper-trade/status` returns current price, last-checked time, unrealised PnL, closed-trade history
- Paper instances use the tuned champion config

#### Backtest Fixes
- Equity-curve chart now renders (was created but never displayed)
- Added delete-per-run + CSV export in results header
- Run name field clearly optional
- Fixed polling so 0-trade backtests complete instead of hanging

#### Client Management
- Added `full_name`, `mobile`, `email`, `company`, `notes` to `User` model (auto-migrated), create/update API, and Admin Add Client form/table

#### Configurable Trading Capital
- Admin sets a default capital; overridable per backtest/paper trade run

#### Confirmations Added
- Stop live-trade instance, delete strategy, enable/disable client permissions

#### DB / Git
- `.gitignore` updated — database files never version-controlled

> **Rollback target:** SHA `06f88acc` for chart/paper-trade core fixes. `1e6f8751` for Live Trade nav + chart tools. `a5ffa20e` for configurable capital.

---

## PR #4 — Admin exchange fees and multi-broker market data

**Merged:** 2026-08-25  
**Branch:** `arena/01a038b7-kudos-phantom`

### Commits

| SHA | Message |
|-----|---------|
| `4f524cbc` | Add admin exchange fees and multi-broker market data |
| `cc0d65ed` | Handle exchange seed pagination limits |

### What Changed

- Admin can configure **exchange fee schedules** (maker/taker rates per broker)
- Multi-broker market data support — seed candles from different exchanges
- Seed pagination limits handled correctly (no data loss on large historical pulls)

> **Rollback target:** SHA `4f524cbc` — removes exchange fee configuration and multi-broker seeding.

---

## PR #5 — Add direction-specific Long/Short condition overrides + filter preview

**Merged:** 2026-08-26  
**Branch:** `arena/01a03d48-kudos-phantom`

### Commits

| SHA | Message |
|-----|---------|
| `2a6ac652` | Add direction-specific Long/Short condition overrides + filter preview |
| `99055618` | Add optional directional max-ATR cap (atr_regime_max) for high-vol exclusion |

### What Changed

#### Backend (`PhantomV2Config`, `strategy.py`, `engine.py`, `order_manager.py`)
- New `entry_conditions` block in `PhantomV2Config`:
  - `use_direction_conditions` (bool, default `false` → legacy behaviour unchanged)
  - `long` / `short` branches with their own `macd_hist_min`, `stop_loss_atr`, `atr_regime_ratio`, `atr_regime_max`, `rsi_oversold`, `rsi_overbought`, `adx_min`
- Per-direction resolver methods fall back to shared fields when toggle is OFF or value unset — **old saved configs keep working unchanged**
- **MACD HIST MIN is signed**: longs require `hist >= value`; shorts require `hist <= value` (negative = bearish momentum clearly present)
- Optional per-direction `atr_regime_max` cap (`ATR <= value×SMA`) — lets shorts exclude high-volatility regime
- `engine.py` `_condition_snapshot` logs per-side pass/fail masks
- `order_manager.py` applies `stop_loss_atr_for(direction)` for side-aware SL
- New API: `POST /backtest/filter-preview` → per-bucket (LONG/SHORT × REVERSAL/MOMENTUM) count, win rate, PF, avg/net PnL

#### Frontend (`Backtest.jsx`)
- Toggle + LONG / SHORT tabs with every entry field per side (prefilled from shared values)
- **Preview Filters** button showing per-bucket results
- **Save as New Strategy** button

#### Docs
- `README.md`, `PHANTOM_V3_NOTES.md`, `api_docs.md` updated with toggle schema, signed-MACD semantics, max-ATR cap

> **Rollback target:** SHA `2a6ac652` — removes Long/Short condition overrides and filter preview. SHA `99055618` — removes optional max-ATR cap specifically.

---

## PR #6 — Improve optimizer UX, compact sidebar, date pickers, and dashboard stats

**Merged:** 2026-08-26  
**Branch:** `arena/01a03d7b-kudos-phantom`

### Commits

| SHA | Message |
|-----|---------|
| `ac79cdbb` | Improve optimizer UX, compact sidebar, date pickers, and dashboard stats |

### What Changed

#### Strategy Optimizer UX
- Fee schedule shown beside capital; "Managed by admin" text removed
- Step-by-step copy with human-readable parameter hints

#### Date Pickers
- Calendar icon shown on all date fields across the app
- Clicking the input opens the date picker

#### Sidebar
- Narrower, collapsible sidebar; mobile drawer so content has more room
- Pages use a shared responsive shell layout

#### Dashboard Stats
- `/dashboard/stats` no longer crashes on incomplete backtest runs
- UI handles API errors gracefully; pings engine health

> **Rollback target:** SHA `ac79cdbb` — removes UX improvements, collapses sidebar and date picker changes.

---

## PR #7 — Fix chart fullscreen error and streamline backtest results UX

**Merged:** 2026-08-26  
**Branch:** `arena/01a03d92-kudos-phantom`

### Commits

| SHA | Message |
|-----|---------|
| `5cfaacc2` | Fix chart fullscreen errors and improve backtest UX |

### What Changed

#### Chart Fullscreen Fix
- Fixed crash caused by undefined `time` reference in indicator overlay mapping when entering fullscreen
- Chart now resizes (not recreates) on fullscreen toggle — stays stable

#### Backtest UX
- Sidebar tab renamed `Optimizer` → `Backtest`
- Collapsible sections (Setup / Config / History) so users can focus on results
- Auto-collapses non-result sections and scrolls to result area when backtest completes
- Fixed polling flow so completed results appear without page refresh
- Backtest results API returns richer run metadata (no more `undefined` values in UI)

> **Rollback target:** SHA `5cfaacc2` — removes fullscreen fix and collapsible backtest UX.

---

## PR #8 — Delta Exchange seeding fix + configurable/per-direction MACD + live paper-trade metrics + IST times

**Merged:** 2026-08-26  
**Branch:** `arena/01a03dd9-kudos-phantom`

### Commits

| SHA | Message |
|-----|---------|
| `c341c0fa` | Fix Delta Exchange seeding (resolution labels + start/end) and add Strategy Explained tab |
| `0dad45c6` | Clarify MACD in strategy docs: indicator vs histogram threshold |
| `533aa0e8` | Make MACD indicator periods configurable in backtest (macd_fast/slow/signal) |
| `90f4ae28` | Per-direction MACD periods + live paper-trade margin/leverage + IST timestamps |
| `c08e1e5f` | Make PostgreSQL switch work: load .env before DB engine init + add psycopg2 dep |

### What Changed

#### Delta Exchange Seeding Fix
- `/v2/history/candles` requires `resolution` as a **string label** (`1m`, `5m`, `15m`, `1h`, `4h`, `1d`), not numeric seconds — was causing HTTP 400
- Mandatory `start`/`end` params now always sent; symbol `BTCUSDT → BTCUSD` mapping preserved

#### Configurable MACD Periods
- `indicators.py`: `compute_indicators()` accepts `macd_fast/macd_slow/macd_signal` (default 12/26/9)
- `PhantomV2Config` validates `macd_slow > macd_fast`
- `BranchConditions` gains per-side `macd_fast/macd_slow/macd_signal` — when Long/Short toggle is ON, each side computes its own histogram
- `Backtest.jsx`: new MACD Indicator group + per-direction MACD inputs

#### Live Paper/Live-Trade Metrics
- Status endpoints return per-instance `leverage`/`margin_pct` and per-trade `current` (live tick), `chg_pct`, `notional_usd`, `lots`, `leverage`, `sl`, `tp`
- `PaperTrade.jsx`: trade cards show live current price, change %, margin, leverage, notional, lots

#### India Standard Time (IST)
- All paper-trade timestamps emitted in IST (UTC+5:30) from `paper_trader.py` / `main.py`
- `PaperTrade.jsx` formats all times explicitly as IST regardless of browser timezone

#### Strategy Explained Tab (Admin Panel)
- `StrategyExplainedTab.jsx`: plain-language breakdown of capital/margin, trend & regime, entries, risk & exit model, sizing & DD guard, indicator formulas (EMA/SMA/ATR/RSI/MACD/ADX), live sizing calculator

#### PostgreSQL Support
- `.env` loaded before DB engine init; `psycopg2` added as dependency

> **Rollback target:** SHA `c341c0fa` for Delta seeding fix + Strategy Explained tab. `533aa0e8` for configurable MACD. `90f4ae28` for per-direction MACD + live metrics + IST.

---

## PR #9 — Admin UI reconfiguration + Delta seed diagnostics + paper trade exit details

**Merged:** 2026-08-27  
**Branch:** `arena/01a03e2c-kudos-phantom`

### Commits

| SHA | Message |
|-----|---------|
| `0b2aee0e` | Admin UI reconfiguration + Delta seed/paper trade fixes |
| `ad9407d7` | Login page: rebrand title PHANTOM → Kudos, remove tagline |
| `afff50b1` | Rebrand user-facing product name PHANTOM → Kudos |

### What Changed

#### Admin UI Reconfiguration
- New sidebar page: **Phantom Strategy** (`/strategy`) — hosts former admin Strategy Rules + Strategy Explained tabs; available to **all users** as reference
- Admin tab bar: 9 tabs reduced to 5 (`Clients · Paper Control · Fees · Seed Data · Password`) with responsive flex-wrap pill bar
- **Broker tabs merged** — Broker Integrations moved from admin into main sidebar as its own page
- No horizontal scroll in admin tab bar

#### Delta Seed Diagnostics
- Error surfaced as `Seed completed with errors` instead of silent `fetched: 0`
- New `GET /admin/market-data/test` endpoint + **Test connection** button in Seed Data tab

#### Paper Trade Exit Details
- Open trades in `/paper-trade/status` now carry `sl_entry`, `tp`, `trail_stop`, `trail_activation`, `trail_active`, `stop_level`, `breakeven_active`, `atr_at_entry`, `peak_price`
- Closed trades carry full exit fields: `exit_detail`, `sl`, `sl_final`, `tp`, `trail_stop`, `atr_at_entry`, `peak_price`, reason labels (`TP / TSL / SL / MH / REV`)
- Frontend: StopBar on open trade cards; expanded closed-trades table with stop loss (initial → final, BE note), TP, trail stop, ATR, exit condition badge
- Fixed production bug: `paper_trader.py` never imported `BrokerClient` (silent NameError) + NumPy `np.bool_` JSON serialization 500s

#### Rebranding
- Product name **PHANTOM → Kudos** in all user-facing UI (login page, navbar, titles)

#### Tests
- `backend/test_delta_and_paper.py` — 37 checks
- `backend/test_api_e2e.py` — 47 checks

> **Rollback target:** SHA `0b2aee0e` for admin UI + paper trade exit details. `afff50b1` for the PHANTOM → Kudos rebrand.

---

## PR #10 — Add resumable market data seeding and daily sync

**Merged:** 2026-08-27  
**Branch:** `arena/01a0429c-kudos-phantom`

### Commits

| SHA | Message |
|-----|---------|
| `42b05479` | Add directional backtest filters and session management |
| `5ad23d2e` | Add resumable market data seeding and daily sync |

### What Changed

#### Resumable Historical Seeding
- Delta Exchange-aware historical seeding from **2020-01-01 → today** for `15m`, `1h`, `4h`, `1d` intervals
- API-safe bounded windows, pacing, 429 handling, host fallback, diagnostics, explicit `1m`/`5m` exclusions
- Each historical window + next cursor persisted atomically — **interrupted seeds resume without re-fetching completed windows**

#### Daily Sync
- Incremental daily refresh for Binance and all enabled Binance-compatible / Delta-compatible broker definitions
- New manual sync endpoint

#### Admin → Seed Data UI
- Delta preset pre-configured
- Connection testing button
- Progress visibility during seeding
- Daily refresh controls

#### Directional Backtest Filters + Session Management
- Additional per-direction filter parameters available in backtests
- Session management improvements

> **Rollback target:** SHA `5ad23d2e` — removes resumable seeding + daily sync. SHA `42b05479` — removes directional backtest filter additions.

---

## PR #11 — Per-side ATR operator for Long/Short, persistent paper-trade history, and trade-log candle/colour + full export

**Merged:** 2026-08-27  
**Branch:** `arena/01a042d4-kudos-phantom`

### Commits

| SHA | Message |
|-----|---------|
| `687c0540` | Per-side ATR operator + persistent paper-trade history |
| `fea581c3` | Trade log: entry candle + colour, full condition detail, richer export |
| `e8d28432` | Make the symbol guard actually fail without the fix |
| `4a69026b` | Correct the documented export column count and pin it in a test |
| `2b6e04b1` | Distinguish the two trade exports in the README |
| `bc3f91d5` | Export the ATR rule helper under its real name |
| `845c8f67` | Fix candle times shifting by the viewer's timezone offset |
| `7cfd6401` | Document that API timestamps are naive UTC |
| `a31723f0` | Test the DOJI candle colour path |
| `51e92f57` | Make the README test instructions runnable by anyone cloning the repo |

### What Changed

#### 1. Per-Side ATR Comparison Operator
- `entry_conditions.long.atr_regime_op` / `entry_conditions.short.atr_regime_op` — accepts `>=`, `<=`, `>`, `<` (normalises `≥` / `≤` / `=>` / `=<`; rejects unknown operators with HTTP 422)
- `PhantomV2Config.atr_regime_op_for()` / `atr_regime_rule_for()` resolve the rule per side; engine applies a numpy comparator map
- Default is preserved (`>=`) — only read when toggle is ON; toggling on seeds both sides with `>=` so nothing changes until edited
- UI: operator dropdown beside each side's ratio, live rule line, resolved rule echoed by `POST /backtest/filter-preview` in `atr_regime_rules`, rule shown on expanded trade rows
- Strategy docs updated

#### 2. Paper Trade History (Persistent)
- New `paper_sessions` table + `app/services/paper_history.py`
- Snapshot on every fill and every few quiet ticks; **finalised on stop** (including open positions)
- Startup flags rows left `running` as `interrupted` — server restart explains itself instead of losing session data
- `POST /paper-trade/stop` — **keeps result** (`saved_to_history`); `DELETE /paper-trade/{instance_key}` — purges it
- New APIs: `GET /paper-trade/history`, `GET|DELETE /paper-trade/history/{id}`
- **Paper Trade History panel**: one row per session with status badge, equity, ROI, WR, PF, max DD; expands to stats, equity-curve chart, closed-trade table, saved logs, CSV export

#### 3. Trade-Log Candle/Colour + Richer Export
- New per-trade fields: `signal_candle_time` / `signal_candle_type`, `entry_candle_time` / `entry_candle_type`, `exit_candle_type` — colour is `GREEN` / `RED` / `DOJI` (open vs close comparison)
- `entry_conditions_detail`: every condition as `value vs threshold → PASS | FAIL | N/A`
- `exit_detail`: rule that closed the trade (e.g. `Stop loss hit — price rose to 10,260.25 ≥ SL 10,240.23`) + stop plan in force
- Fixed wrong report: `cond_macd_hist_ok` was logged from reversal masks for momentum entries (Setup B) — non-applicable conditions now `null` / `N/A` instead of `FAIL`
- 12 nullable `trades` columns added by `migrate_db()` at startup — existing databases keep working
- **UI**: single Candle column split into **Signal Candle** and **Entry Candle** (UTC + colour chip); Exit cell gets a chip; expanded row shows full breakdown, exit rule, stop plan
- **Excel/CSV Export**: **45 columns** — candle times/colours, one column per entry condition, full breakdown, exit condition + detail, stop plan, PnL — UTF-8 with BOM + CRLF so Excel renders `₹` / `≥` correctly

#### 4. Timezone Bug Fix
- API returns candle timestamps as naive UTC strings (no `Z`/designator); JS was parsing as local time → candles shifted by viewer's UTC offset (e.g. UTC+5:30 viewers saw `13:41 UTC` as `08:11` and midnight candles on the wrong date)
- `fmtCandleTime` now appends `Z` to naive timestamps; test suite now runs under `TZ=Asia/Calcutta` so this regression is caught

#### Bug Fixes
- `test_delta_and_paper.py` was clearing the seeded candles in `backend/trading_system.db` — now points at a temporary SQLite file
- `paper_history._payload()` was not writing `symbol` → now records `service.symbol`

#### Test Coverage
| Suite | Result |
|-------|--------|
| `backend/test_trade_log_detail.py` | 57 passed |
| `frontend npm test` | 76 passed |
| `backend/test_paper_history.py` | 56 passed |
| `backend/test_atr_regime_op.py` | 32 passed |
| `backend/test_delta_and_paper.py` | 37 passed |
| `backend/test_api_e2e.py` | 47 passed |
| **Total** | **305 checks, 0 failures** |

> **Rollback targets:**
> - SHA `687c0540` — per-side ATR operator + paper trade history persistence
> - SHA `fea581c3` — trade log candle/colour fields + 45-column export
> - SHA `845c8f67` — timezone display bug fix

---

## PR #12 — Fix Exit Distribution tooltip (black-on-black text) + add commit history summary doc

**Merged:** 2026-08-27  
**Branch:** `arena/01a0440f-kudos-phantom` · squash commit on `main`: `ad95c37e`

### Commits

| SHA | Message |
|-----|---------|
| `0ed5bb3c` | Fix Exit Distribution tooltip contrast (black-on-black) |
| `f67f255c` | Add commit history summary doc |

### What Changed
- **Backtest UI** (`frontend/src/pages/Backtest.jsx`): the Exit Distribution chart's tooltip rendered black text on the dark background — restyled so values are readable.
- **Docs:** added this `COMMIT_HISTORY_SUMMARY.md` (PR/commit history + rollback guide).

> **Rollback target:** `ad95c37e` (both changes, squash-merged to `main`)

---

## PR #13 — Show per-trade PnL, fees and booked PnL in trade logs + exports

**Merged:** 2026-08-27  
**Branch:** `arena/01a04469-kudos-phantom` · squash commit on `main`: `2ea13d5c`

### Commits

| SHA | Message |
|-----|---------|
| `e772ecbb` | Per-trade PnL, fees and booked PnL in trade logs + CSV exports |

### What Changed
- **Trade logs** (`frontend/src/pages/Backtest.jsx`, `frontend/src/pages/PaperTrade.jsx`): each trade row now shows its own PnL, the fees paid, and the booked (net) PnL, with the same columns in the CSV export.
- **Tests** (`frontend/tests/trade_log_ui.jsx`): assertions for the new columns/exports.

> **Rollback target:** `2ea13d5c`

---

## PR #14 — BTC perpetual mark pricing, skip-new-trade windows, and a full live order-management terminal

**Merged:** 2026-08-28  
**Branch:** `arena/01a0480e-kudos-phantom` · squash commit on `main`: `60f7d374`

### Commits

| SHA | Message |
|-----|---------|
| `b292f7a3` | BTC perpetual resolver + mark-price risk pricing + skip-new-trade windows |
| `8285fa3a` | Live order lifecycle, bracket orders, /terminal page, broker audit trail |
| `deaeb546` | Shared broker rate limiter + docs/order_management_research.md + test suites |

### What Changed

#### BTC perpetual + mark price (`core/mark_price.py`, `core/engine.py`, `services/data_sync.py`)
- Single resolver for the BTC **perpetual** (Binance `BTCUSDT` / Delta `BTCUSD`); dated futures are never substituted. Seeding, engine, paper and live all use it.
- Stops, targets, trailing, breakeven and PnL run on the exchange **mark price**; the traded fill price is stored beside it on every trade and exported in the CSV. Bars without marks fall back to the traded price; runs report `mark_price_basis` / coverage.

#### Skip-new-trade windows (`core/trading_windows.py`, `TradingWindowsEditor.jsx`)
- Scalable schedule (any number of day+time windows, wraps past Sunday, Asia/Kolkata default) honoured by Backtest, Paper and Live. Only **new entries** are refused; open positions keep their stop/target/trail.

#### Live order management (`services/broker_client.py`, `services/broker_account.py`, `services/live_trader.py`, `components/LiveTerminal.jsx`)
- Full order lifecycle: market / limit / stop / stop-limit / take-profit / trailing, edit (Delta), cancel one/all, open orders, order history, fills with fees, margined positions, close/partial close, position margin, leverage and margin mode.
- Bracket orders (entry + SL + TP): native on Delta, emulated with reduce-only legs on Binance; stops trigger on the mark price.
- New **/terminal** page: Positions · Open Orders · Stop Orders · Fills · Order History, Wallet & Margin, Risk, live rate-limit panel, order ticket, leverage/margin controls, cancel-all and close.
- Local audit trail in `broker_orders` / `broker_fills`, tagged with leg, client order id and strategy instance, de-duplicated on the exchange trade id.

#### Broker rate limits (`core/rate_limit.py`)
- Delta 10,000 weight per fixed 5-minute window; Binance 2,400 weight/min plus 1,200 orders/min and 300 orders/10s. One shared limiter per broker connection enforces 20 req/s + 1,200 req/min, tracks venue headers/quota, paces at 85% and retries 429s; limits editable per broker from the UI.

#### Docs & tests
- `docs/order_management_research.md`; backend 472 checks (`test_live_account.py` 144), frontend 216 (`terminal_ui.jsx` 56); `vite build` clean.

> **Rollback targets:**
> - SHA `60f7d374` — whole PR (squash on `main`)
> - `b292f7a3` / `8285fa3a` / `deaeb546` — PR-branch commits (perpetual+windows / terminal / rate-limits+docs)

---

## PR #15 — Binance/Delta full-history seeding (2020→today), corrupt-data repair, unbreakable long seeds + navbar crash fix

**Merged:** 2026-08-29 (squash onto `main`) — https://github.com/jagthapsaurabh/kudos_phantom/pull/15  
**Branch:** `arena/01a04ba9-kudos-phantom`

### Commits

| SHA | Message |
|-----|---------|
| `77d3826` | fix(navbar): import TerminalSquare from lucide-react |
| `eac8743` | Binance full-history seed (2020 → today, incl. 1d) + corrupted-data repair |
| `b7918a7` | Full-history seeds never break: background jobs, request/window retries, paged mark backfill |
| `543f055` | Verify resilient full-history seeding for both Binance and Delta; fix mark-backfill end-date inclusivity |

### What Changed

#### Runtime crash fix (`frontend/src/components/Navbar.jsx`)
- `TerminalSquare` was used in the nav items without being imported → `ReferenceError: TerminalSquare is not defined` crashed the **entire app** for live/admin users (the only ones shown the Terminal item). Runtime-only failure — the bundle built cleanly because the bare identifier is treated as a global.

#### Corrupt Binance data — root cause + repair (`services/data_sync.py`, `scripts/seeder.py`, `scripts/reset_db.py`)
- The legacy seeder preferred local CSVs whose 1h timestamps are **off the candle grid** (e.g. `2020-06-26 11:41:59.523330`) and bulk-inserted without an upsert (duplicates on re-run). Off-grid candles also make mark pricing impossible.
- **"Binance 2020 → today"** preset (Admin → Seed Data) + rewritten `python -m app.scripts.seeder` CLI: clean candles live from the Binance Futures API — `15m, 1h, 4h, 1d` (daily included) — 1 Jan 2020 → today, in 1,500-candle windows with a durable resume cursor. `fetch_all` defaults to 2020-01-01 for **every** source.
- **Repair:** `repair_klines` deletes duplicate + off-grid candles (well-formed rows untouched) — `POST /admin/market-data/repair`, "Repair existing candles" button, and `repair: true` on the seed payload (presets enable it).
- **Visibility:** `/admin/market-data/status` always reports `duplicate_rows`; `?health=1` adds `misaligned_rows` per series; the status table shows both columns.
- **Prevention:** CSV imports are rejected unless timestamps align to the interval grid; `reset_db.py` prints the API-seeder fallback when the legacy CSVs are rejected.
- Cursor fix: resume advances to the next grid boundary (was `window_end + interval`, which skipped the boundary candle when a completed range was extended).

#### Long seeds never break (`services/data_sync.py`, `main.py`, `AdminPanel.jsx`)
- **Request retries:** shared `_get_with_retry` (Binance klines, Binance mark price, Delta OHLC) retries timeouts/resets/429/5xx with growing backoff + `Retry-After`; new `TransientMarketDataError` separates hiccups from permanent failures. Delta host fallback preserved.
- **Window retries:** the full-history loop retries a transient failure at the **same cursor** (default 3 attempts) before marking the range failed; committed windows are never lost, the error says to re-run, and a re-run **resumes** instead of restarting. Knobs: `SEED_REQUEST_RETRIES`, `SEED_REQUEST_BACKOFF_SECONDS`, `SEED_WINDOW_RETRIES`, `SEED_WINDOW_BACKOFF_SECONDS`.
- **Background mode:** `background: true` on the seed payload (set automatically by the full-history presets) runs the job in a server-side worker under a single-job lock (serialized with the daily sync); `GET /admin/market-data/seed-job` exposes live state; the UI polls every 10 s with a running banner — a browser/proxy timeout can no longer truncate an hours-long fetch.
- **Mark price for ALL data:** `sync_mark_prices` pages the mark series across the whole range (same grid-window batching) instead of a single 1,500-candle page; the seed job derives the mark range from the `fetch_all` default.
- **Both venues verified** (`test_seed_repair.py`, 57 checks): adapter classification (incl. mixed-host), pre-listing empty windows, listing-spanning partial pages, `MARK:BTCUSD` paging, background endpoint end-to-end for Binance **and** Delta. Cross-check also fixed: date-only `end_time` in `sync_mark_prices` is now inclusive through that day (previously skipped the final day's marks).

#### Tests & docs
- New `backend/test_seed_repair.py` (57 checks), `frontend/tests/admin_seed_ui.jsx` (14 checks). Full suites green: **backend 529**, **frontend 230**, `vite build` clean. `README.md` / `api_docs.md` document presets, repair, background mode, health columns and retry knobs.
- Sandbox caveat: no outbound access to either exchange (TLS-blocked), so exchange paths run against faithful mock/scripted exchanges — same convention as the existing Delta suite.

> **Rollback targets:**
> - SHA `77d3826` — navbar crash fix
> - SHA `eac8743` — Binance 2020→today seed + repair + health columns
> - SHA `b7918a7` — background jobs + request/window retries + paged mark backfill
> - SHA `543f055` — Delta verification + inclusive mark end date

---

## Quick Rollback Guide

To roll back the codebase to any specific commit SHA:

```bash
# View what a commit changed
git show <SHA>

# Create a rollback branch from any commit
git checkout -b rollback/<feature-name> <SHA>

# Or, soft-revert a specific commit from current branch
git revert <SHA>
```

### Feature → SHA mapping

| Feature | Rollback SHA |
|---------|-------------|
| Initial PHANTOM v3 engine (MaxDD 4.17%) | `00cd26a4` |
| Client management + roles | `43d191ec` |
| Paper trade live logs + named backtests | `0d61c6ea` |
| Market chart fix (lightweight-charts v5) | `06f88acc` |
| Live Trade nav + TradingView chart tools | `1e6f8751` |
| Configurable trading capital | `a5ffa20e` |
| Admin exchange fees + multi-broker data | `4f524cbc` |
| Long/Short direction condition overrides | `2a6ac652` |
| Optional max-ATR cap per direction | `99055618` |
| Compact sidebar + date pickers + UX | `ac79cdbb` |
| Chart fullscreen fix + collapsible backtest | `5cfaacc2` |
| Delta Exchange seeding fix | `c341c0fa` |
| Configurable MACD periods | `533aa0e8` |
| Per-direction MACD + live metrics + IST | `90f4ae28` |
| Admin UI reconfiguration | `0b2aee0e` |
| PHANTOM → Kudos rebrand | `afff50b1` |
| Resumable seeding + daily sync | `5ad23d2e` |
| Per-side ATR operator | `687c0540` |
| Persistent paper-trade history | `687c0540` |
| Trade-log candle/colour + 45-col export | `fea581c3` |
| Candle timezone display fix | `845c8f67` |
| Exit Distribution tooltip contrast | `ad95c37e` |
| Per-trade PnL/fees/booked PnL in logs + exports | `2ea13d5c` |
| BTC perpetual + mark pricing + trading windows + /terminal | `60f7d374` |
| TerminalSquare navbar crash fix | `77d3826` |
| Binance 2020→today seed + corrupt-data repair | `eac8743` |
| Background seeds + retries + paged mark backfill | `b7918a7` |
| Delta-parity verification + inclusive mark end date | `543f055` |

---

## File Ownership Map

> Quick reference: which files are likely to change for a given area.

| Area | Key Files |
|------|-----------|
| Strategy signals | `backend/app/core/strategy.py` |
| Backtest engine + drawdown guard | `backend/app/core/engine.py` |
| Order management (SL/TP/trail) | `backend/app/services/order_manager.py` |
| Paper trading service | `backend/app/services/paper_trader.py` |
| Paper trade history | `backend/app/services/paper_history.py` |
| Market data sync / seeding | `backend/app/services/data_sync.py`, `broker_client.py` |
| API routes | `backend/app/main.py` |
| Database models + migrations | `backend/app/models.py`, `backend/app/database.py` |
| Indicators (EMA/ATR/RSI/MACD) | `backend/app/core/indicators.py` |
| Config schema (PhantomV2Config) | `backend/app/core/strategy.py` or `config.py` |
| Admin panel | `frontend/src/pages/AdminPanel.jsx` |
| Backtest / Optimizer page | `frontend/src/pages/Backtest.jsx` |
| Paper trade page | `frontend/src/pages/PaperTrade.jsx` |
| Market chart | `frontend/src/pages/MarketChart.jsx` |
| Dashboard | `frontend/src/pages/Dashboard.jsx` |
| Login | `frontend/src/pages/Login.jsx` |
| Navbar | `frontend/src/components/Navbar.jsx` |
| API client (frontend) | `frontend/src/api.js` |
| Strategy Explained tab | `frontend/src/components/StrategyExplainedTab.jsx` |

---

*Last updated: 2026-08-29 | Covers PR #1 through PR #15 (all merged to `main`)*
