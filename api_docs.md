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

### 2. Broker & Capital Settings
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/broker-settings` | None | Get current API keys and capital |
| `POST` | `/broker-settings` | `api_key`, `api_secret`, `initial_capital`, `margin_pct`, `broker_name` | Update broker keys and capital |

### 3. Charts & Market Data
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/klines` | `symbol`, `interval`, `limit` | Fetch candle data (with volume) for charts |
| `GET` | `/symbols` | None | List symbols available in the local market-data store |
| `GET` | `/phantom/signals` | `symbol`, `start_date`, `end_date`, `strategy_id` | Signal candles for the chart overlay. `strategy_id` may be `PhantomV2` (default, tuned champion), `FastTest`, or a custom strategy id created in the Strategies manager. |

### 4. Paper Trading
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/paper-trade/start` | `strategy_id`, `initial_capital` (optional), `margin_pct` (optional) | Starts a new simulation instance. `initial_capital`/`margin_pct` default to the user's (admin-set) values. |
| `POST` | `/paper-trade/stop` | `instance_key` | Stops a specific simulation instance |
| `GET` | `/paper-trade/status` | None | List all running instances, open positions & closed-trade history |
| `GET` | `/paper-trade/logs` | `instance_key` | Live log buffer for an instance |

### 5. Live Trading
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/live-trade/start` | `strategy_id` | Starts a real-money execution instance |
| `POST` | `/live-trade/stop` | `instance_key` | Stops a live execution instance |
| `GET` | `/live-trade/status` | None | List all live instances and positions |

### 6. Backtesting
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/backtest` | `params`, `strategy_id`, `start_date`, `end_date`, `strategy_name`, `initial_capital` (optional) | Triggers a background backtest with a custom starting capital. Defaults to the user's (admin-set) initial capital. |
| `GET` | `/backtest/history` | None | Lists all previous backtest runs (incl. `initial_capital`) |
| `GET` | `/backtest/results/{id}` | None | Get detailed trades and equity curve for a run (incl. `initial_capital`) |
| `DELETE` | `/backtest/{id}` | None | Delete a single backtest run and its trades |
| `DELETE` | `/backtest/clear` | None | Delete all of the user's backtest runs |

---

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
