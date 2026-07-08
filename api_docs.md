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

### 3. Paper Trading
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/paper-trade/start` | `strategy_id` | Starts a new simulation instance |
| `POST` | `/paper-trade/stop` | `instance_key` | Stops a specific simulation instance |
| `GET` | `/paper-trade/status` | None | List all running instances and positions |

### 4. Live Trading
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/live-trade/start` | `strategy_id` | Starts a real-money execution instance |
| `POST` | `/live-trade/stop` | `instance_key` | Stops a live execution instance |
| `GET` | `/live-trade/status` | None | List all live instances and positions |

### 5. Backtesting
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/backtest` | `params`, `strategy_id`, `start_date`, `end_date`, `strategy_name` | Triggers a background backtest |
| `GET` | `/backtest/history` | None | Lists all previous backtest runs |
| `GET` | `/backtest/results/{id}` | None | Get detailed trades and equity curve for a run |

---

## 🛠️ Utility
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/` | None | System health check |
| `GET` | `/klines` | `symbol`, `interval`, `limit` | Fetch raw market data for charts |
| `GET` | `/strategies` | None | List available custom strategies |
| `POST` | `/strategies/create` | `name`, `rules` | Create a new custom strategy |
