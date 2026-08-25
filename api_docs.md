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
| `GET` | `/paper-trade/status` | None | List all running instances, open positions & closed-trade history |
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
| `GET` | `/backtest/history` | None | Lists all previous backtest runs (incl. `initial_capital`) |
| `GET` | `/backtest/results/{id}` | None | Get detailed trades and equity curve for a run (incl. `initial_capital`) |
| `DELETE` | `/backtest/{id}` | None | Delete a single backtest run and its trades |
| `DELETE` | `/backtest/clear` | None | Delete all of the user's backtest runs |

---

### 7. Admin integrations & seeding
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/admin/fee-settings` | None | List all per-exchange, per-mode schedules |
| `POST` | `/admin/fee-settings` | `broker_code`, `mode`, `taker_fee_bps`, `maker_fee_bps`, `enabled` | Add/update a fee schedule |
| `GET` | `/admin/brokers` | None | List configured integrations |
| `POST` | `/admin/brokers` | `code`, `name`, `kind`, endpoint URLs | Register another broker/data adapter |
| `POST` | `/admin/market-data/seed` | `source`, `symbol`, `intervals`, dates, `limit`, `fetch_all` | Fetch and upsert OHLCV data with volume |
| `POST` | `/admin/market-data/seed-csv` | Multipart CSV + `source`, `symbol`, `interval` | Import `event_time,open,high,low,close,volume` data |
| `GET` | `/admin/market-data/status` | None | Dataset counts, ranges and volume coverage |

## 🛠️ Utility
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/admin/clients` | None | List clients (admin) |
| `POST` | `/admin/clients` | `username`, `password`, `full_name`, `mobile`, `email`, `company`, `notes`, `initial_capital`, `margin_deployment_pct`, `can_paper`, `can_live` | Create a client account (admin) |
| `PUT` | `/admin/clients/{id}` | any of the above | Update client account (admin) |
| `GET` | `/` | None | System health check |
| `GET` | `/klines` | `symbol`, `interval`, `limit` | Fetch raw market data for charts |
| `GET` | `/strategies` | None | List available custom strategies |
| `POST` | `/strategies/create` | `name`, `rules` | Create a new custom strategy |
| `POST` | `/strategies/scan` | `rules`, `symbol`, `interval`, `start_date`, `end_date`, `limit` | Chartink-style scan: returns the latest candles that match an unsaved rule set (for the strategy builder live preview) |
