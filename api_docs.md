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
| `POST` | `/paper-trade/start` | `strategy_id`, `broker_name`/`data_source`, `connection_id`, `initial_capital` (optional), `margin_pct` (optional) | Starts a source-specific simulation instance using the admin's paper fee schedule. Multiple exchange instances can run concurrently. |
| `POST` | `/paper-trade/stop` | `instance_key` | Stops a specific simulation instance |
| `DELETE` | `/paper-trade/{instance_key}` | None | Stops and removes a paper-trade session from the user's workspace |
| `GET` | `/paper-trade/status` | None | List all running instances, open positions & closed-trade history, including the saved strategy name |
| `GET` | `/paper-trade/logs` | `instance_key` | Live log buffer for an instance |

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
- `atr_regime_ratio` keeps the legacy **lower-bound floor** semantics
  (`ATR >= ratio × SMA`) in both modes.
- `atr_regime_max` (optional on each side) adds a **max-ATR cap**
  (`ATR <= value × SMA(ATR, 50)`); when blank/`null` it is disabled. A lower
  cap is tighter and excludes the high-volatility regimes where shorts
  underperform. This is the field to use to exclude the top volatility quartile
  for shorts.

```json
{
  "macd_hist_min": 5.0,
  "entry_conditions": {
    "use_direction_macd_hist": true,
    "use_direction_atr_floor": true,
    "long":  { "macd_hist_min": 5.0, "atr_regime_ratio": 0.5 },
    "short": { "macd_hist_min": -8.0, "atr_regime_ratio": 0.3 }
  }
}
```

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
