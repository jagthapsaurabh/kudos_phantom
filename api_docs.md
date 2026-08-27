# 📖 PHANTOM v2.5 API Documentation

## 🛡️ Authentication
All protected endpoints require a Bearer Token in the header:
`Authorization: Bearer <username>`

### 1. Auth Endpoints
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/auth/register` | `username`, `password` | Creates a new user account |
| `POST` | `/token` | `username`, `password` (Form Data) | Returns access token |

---

## 📈 Trading Core

### 2. Broker, exchange & fee settings
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/broker-definitions` | None | Enabled exchange/broker data sources (Binance and Delta are built in) |
| `GET` | `/broker-connections` | None | Current user's masked, multi-broker connections |
| `POST` | `/broker-connections` | `broker_code`, `label`, `api_key`, `api_secret`, `passphrase`, `is_testnet` | Add a credential connection; multiple may be active |
| `DELETE` | `/broker-connections/{id}` | None | Remove a user's connection |
| `GET` | `/broker-settings` | None | Get capital defaults and masked legacy settings |
| `POST` | `/broker-settings` | `api_key`, `api_secret`, `initial_capital`, `margin_pct`, `broker_name` | Update legacy/primary broker keys and capital |
| `GET` | `/fee-settings` | `broker_code`, `mode` | Read the admin schedule used by a new run |

Fees are managed by admins in basis points using `POST /admin/fee-settings` with `broker_code`, `mode` (`backtest`, `paper`, or `live`), `taker_fee_bps`, and `maker_fee_bps`. Schedules are snapshotted on backtest runs; `.env` is only the first-install fallback.

### 3. Charts & Market Data
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/klines` | `symbol`, `interval`, `limit`, `source` | Fetch source-specific OHLCV candle data (including volume) for charts |
| `GET` | `/symbols` | `source` | List symbols available in the selected local market-data source |
| `GET` | `/phantom/signals` | `symbol`, `source`, `start_date`, `end_date`, `strategy_id` | Signal candles for the chart overlay. `strategy_id` may be `PhantomV2` (default, tuned champion), `FastTest`, or a custom strategy id created in the Strategies manager. |

### 4. Paper Trading
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/paper-trade/start` | `strategy_id`, `broker_name`/`data_source`, `connection_id`, `initial_capital` (optional), `margin_pct` (optional) | Starts a source-specific simulation instance using the admin's paper fee schedule. Multiple exchange instances can run concurrently. Returns the `instance_key` and the `session_id` of its saved history row. |
| `POST` | `/paper-trade/stop` | `instance_key` | Stops a specific simulation instance. The result is **saved** to History (`saved_to_history`, `session_id` in the response). |
| `DELETE` | `/paper-trade/{instance_key}` | None | Stops a session and permanently removes it, including its saved history row (`history_removed`) |
| `GET` | `/paper-trade/status` | None | List all running instances, open positions & closed-trade history, including the saved strategy name, `session_id` and the live equity curve |
| `GET` | `/paper-trade/logs` | `instance_key` | Live log buffer for an instance |
| `GET` | `/paper-trade/history` | None | **Every** paper session the user has run (newest first) with status, equity, ROI, win rate, profit factor, max drawdown and trade count. Survives stop and server restart. |
| `GET` | `/paper-trade/history/{session_id}` | None | Full saved result of one session: closed trades, equity curve, saved logs, positions still open at stop and the parameter snapshot |
| `DELETE` | `/paper-trade/history/{session_id}` | None | Delete one saved session from History |

Paper sessions are mirrored into the `paper_sessions` table while they run (on every fill and every
few quiet ticks) and finalised when they stop, so stopping an instance no longer loses its trades,
equity curve or logs. Status is `running`, `stopped`, or `interrupted` (the server restarted while it
was live). Only an explicit delete — the workspace delete on a live card or History → Delete — removes
a saved result.

### 5. Live Trading
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/live-trade/start` | `strategy_id`, `broker_name`/`data_source`, `connection_id`, `initial_capital`, `margin_pct` | Starts a real-money execution instance on the selected broker using the live fee schedule |
| `POST` | `/live-trade/stop` | `instance_key` | Stops a live execution instance |
| `GET` | `/live-trade/status` | None | List all live instances and positions |

### 6. Backtesting
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/backtest` | `params`, `strategy_id`, `start_date`, `end_date`, `strategy_name`, `initial_capital` (optional), `data_source`, `fee_mode` | Triggers a source-specific background backtest using the admin fee schedule. Defaults to the user's (admin-set) initial capital. |
| `POST` | `/backtest/filter-preview` | `params`, `start_date`, `end_date`, `symbol`, `data_source`, `fee_mode` | Synchronous per-bucket peek at the current conditions (`LONG/SHORT x REVERSAL/MOMENTUM`) with win rate, profit factor and avg/net PnL — handy while tuning the direction-specific thresholds before a full run. |
| `GET` | `/backtest/history` | None | Lists all previous backtest runs (incl. `strategy_id` and `initial_capital`) |
| `GET` | `/backtest/results/{id}` | None | Get detailed trades, equity curve and the exact saved `params` snapshot for a run (incl. `strategy_id` and `initial_capital`) |
| `DELETE` | `/backtest/{id}` | None | Delete a single backtest run and its trades |
| `DELETE` | `/backtest/clear` | None | Delete all of the user's backtest runs |

**Direction-specific conditions (`params.entry_conditions`).** Every `params` object may optionally
carry an `entry_conditions` block. The new Backtest controls are independent:
`use_direction_macd_hist` enables separate Long/Short `macd_hist_min` values and
`use_direction_atr_floor` enables separate Long/Short `atr_regime_ratio` values. When either is
false, that field uses the shared value. The legacy `use_direction_conditions` master switch remains
supported for older configs and enables all of the original directional overrides
(`macd_hist_min`, `stop_loss_atr`, `atr_regime_ratio`, RSI bounds and `adx_min`). Any field left
`null` falls back to the shared value. The two client-facing fields are interpreted **per side**:

- `macd_hist_min` is **signed** — longs require `hist >= value` (e.g. `5`), shorts require
  `hist <= value` (e.g. `-8`), so a negative short threshold means "bearish momentum clearly present".
- `atr_regime_ratio` is compared with `SMA(ATR, 50)` using the side's `atr_regime_op`.
- `atr_regime_op` (optional, per side) chooses the comparison: `">="`, `"<="`, `">"` or `"<"`.
  It defaults to `">="` — the original lower-bound floor — and is only read when
  `use_direction_atr_floor` (or the legacy master switch) is on, so existing runs and saved
  strategies are unchanged. Unknown operators are rejected with a validation error; the unicode
  forms `≥` / `≤` and `=>` / `=<` are accepted and normalised.
- `atr_regime_max` (optional on each side) adds a **max-ATR cap**
  (`ATR <= value × SMA(ATR, 50)`); when blank/`null` it is disabled. It is ANDed with the
  operator test above. A lower cap is tighter and excludes the high-volatility regimes where
  shorts underperform.

`POST /backtest/filter-preview` echoes the resolved rule per side in `atr_regime_rules`
(e.g. `{"long": "ATR ≥ 0.5 x SMA50(ATR)", "short": "ATR < 1.2 x SMA50(ATR)", "operators": {...}}`),
and each trade's expanded log row shows the test that filtered it.

```json
{
  "macd_hist_min": 5.0,
  "atr_regime_ratio": 0.5,
  "entry_conditions": {
    "use_direction_macd_hist": true,
    "use_direction_atr_floor": true,
    "long":  { "macd_hist_min": 5.0,  "atr_regime_ratio": 0.5, "atr_regime_op": ">=" },
    "short": { "macd_hist_min": -8.0, "atr_regime_ratio": 1.2, "atr_regime_op": "<" }
  }
}
```

**Trade-log detail (`GET /backtest/results/{id}` → `trades[]`).** Each trade now identifies the
candles involved and explains why it entered and exited. The strategy fires on the **signal**
candle *i* and fills at the open of candle *i+1*, so both are recorded:

| Field | Meaning |
| :--- | :--- |
| `signal_candle_time`, `signal_candle_type` | The candle that raised the signal, and its colour: `GREEN` (`close > open`), `RED` (`close < open`) or `DOJI` (`close == open`) |
| `entry_candle_time`, `entry_candle_type` | The candle the entry actually filled on (one bar after the signal) and its colour — "which colour got the entry" |
| `exit_candle_type` | Colour of the candle the position closed on |
| `entry_conditions_detail` | Multi-line text: every entry condition with the measured value, the threshold applied and `PASS` / `FAIL` / `N/A`. `N/A` means the setup that fired never applies that filter (Setup B momentum enters on the MACD zero-cross, so the MACD-histogram magnitude test is not used) |
| `exit_detail` | The exact rule that closed the trade, e.g. `Stop loss hit — price rose to 10,260.25 ≥ SL 10,240.23 (initial SL 10,240.23)` |
| `conditions` | Machine-readable flags: `trend_ok`, `adx_ok`, `macd_hist_ok`, `atr_regime_ok`, `rsi_ok`, `macd_confirm_ok`, `di_ok`. `null` = not applied by that setup |
| `sl_entry`, `trail_stop`, `atr_at_entry`, `peak_price` | Stop plan actually in force: the initial stop, the final trailing stop, ATR at entry and the best price reached |

`candle_type` is still returned for older saved runs and equals the signal candle colour. All the
new columns are nullable and are added to existing `trades` tables by `migrate_db()` at startup, so
older runs simply return `null` for them.

**Timestamps are naive UTC.** Every `*_time` field is serialised without a timezone designator —
`2020-06-26T13:41:59.523330`, not `...Z`. They are UTC. Parsers that assume local time for an
undesignated datetime will shift them: JavaScript's `new Date("2020-06-26T13:41:59.523330")` reads it
as *local* time, so a viewer at UTC+05:30 gets `08:11Z`. Append `Z` before parsing, or parse with an
explicit UTC assumption. The Backtest UI does this in `fmtCandleTime`, which is why the trade log and
the export agree regardless of the viewer's timezone.

**Excel / CSV export.** The Backtest page's *Excel / CSV Export* button writes one row per trade
with 45 columns — signal/entry/exit candle times (UTC) and colours, one column per entry condition
(`PASS` / `FAIL` / `N/A`), the full condition breakdown, the exit condition and its detail, the stop
plan and the PnL fields. It is UTF-8 with a BOM and CRLF line endings so Excel opens it cleanly.

---

### 7. Admin integrations & seeding
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/admin/fee-settings` | None | List all per-exchange, per-mode schedules |
| `POST` | `/admin/fee-settings` | `broker_code`, `mode`, `taker_fee_bps`, `maker_fee_bps`, `enabled` | Add/update a fee schedule |
| `GET` | `/admin/brokers` | None | List configured integrations |
| `POST` | `/admin/brokers` | `code`, `name`, `kind`, endpoint URLs | Register another broker/data adapter |
| `POST` | `/admin/market-data/seed` | `source`, `symbol`, `intervals`, dates, `limit`, `fetch_all` | Fetch and upsert OHLCV data with volume. Delta full-history requests are split into API-safe windows and exclude `1m`/`5m`. |
| `POST` | `/admin/market-data/sync-now` | None | Run the same incremental multi-source refresh used by the daily scheduler |
| `POST` | `/admin/market-data/seed-csv` | Multipart CSV + `source`, `symbol`, `interval` | Import `event_time,open,high,low,close,volume` data |
| `GET` | `/admin/market-data/status` | None | Dataset counts, ranges and volume coverage |
| `GET` | `/admin/market-data/progress` | None | Durable full-history seed cursors, status, counts and last error |

For the requested Delta backfill, send `source: "Delta"`, `intervals: ["15m", "1h", "4h", "1d"]`, `start_date: "2020-01-01"`, `end_date` as today, `limit: 2000`, and `fetch_all: true`. The server makes one bounded request per window because Delta returns at most 2,000 OHLC candles and charges three rate-limit units per request. `1m` and `5m` are rejected for this Delta seed plan. Every completed window and its next cursor are committed atomically with the candles in `market_data_seed_progress`; a restart resumes the saved cursor and an already-completed request is not fetched again. `GET /admin/market-data/progress` exposes this state. The daily scheduler refreshes all supported intervals for Binance and enabled Binance-compatible / Delta-compatible broker definitions; Delta daily refresh also excludes `1m` and `5m`. Generic broker definitions are reported as skipped until an adapter is configured.

## 🛠️ Utility
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/dashboard/stats` | None | Per-user backtest aggregates (`best_roi`, `avg_roi`, `total_runs`, `completed_runs`, `avg_win_rate`, `best_win_rate`, `last_run`). Incomplete runs with null ROI/win-rate are skipped. |
| `GET` | `/admin/clients` | None | List clients (admin) |
| `POST` | `/admin/clients` | `username`, `password`, `full_name`, `mobile`, `email`, `company`, `notes`, `initial_capital`, `margin_deployment_pct`, `can_paper`, `can_live` | Create a client account (admin) |
| `PUT` | `/admin/clients/{id}` | any of the above | Update client account (admin) |
| `GET` | `/` | None | System health check |
| `GET` | `/klines` | `symbol`, `interval`, `limit` | Fetch raw market data for charts |
| `GET` | `/strategies` | None | List available custom strategies |
| `POST` | `/strategies/create` | `name`, `rules` or `params` | Create a new custom strategy. `rules` stores a Chartink-style rule list (dynamic builder), `params` stores a Phantom `PhantomV2Config` dict (can include `entry_conditions`). A saved `params` strategy can be re-run or Paper / Live traded directly. |
| `POST` | `/strategies/scan` | `rules`, `symbol`, `interval`, `start_date`, `end_date`, `limit` | Chartink-style scan: returns the latest candles that match an unsaved rule set (for the strategy builder live preview) |
