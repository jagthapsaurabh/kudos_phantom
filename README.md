# 🌌 PHANTOM v3 Trading Tool

Institutional-grade BTC trading pipeline. **v3** upgrades the classic v2.5 engine with a
higher-frequency dual-setup strategy, active drawdown control and a full
trade + market-condition audit log for every position.

## 🚀 Execution Guide

### 1. Backend (Server A)
Navigate to the backend directory and run from there.

```bash
cd backend
# Install dependencies
pip install fastapi uvicorn sqlalchemy passlib[bcrypt] pandas numpy requests python-dotenv

# 1. Seed Admin User (First time only)
python -m app.scripts.seed_admin

# 2. Seed Market Data (First time only)
python -m app.scripts.seeder

# 3. Start Server
python run.py
```

### 2. Frontend (Server B)
Navigate to the frontend directory.

```bash
cd frontend
npm install
npm run dev
```

---

## ⚡ PHANTOM v3 — What's New

| Area | v2.5 | v3 |
| :--- | :--- | :--- |
| Entries | RSI reversal only | **+ Momentum continuation setup** (MACD-hist zero-cross with DI confirmation, in 4h trend direction) |
| Drawdown control | none | **Portfolio DD throttle**: size cut at soft DD, entry halt at hard DD, auto-resume — measured MaxDD **30.3% → 4.2%** |
| Stops | ATR SL + trailing | **+ breakeven stop** once trade is `breakeven_atr` x ATR in profit |
| Cooldown | configured but unused | **enforced** after every closed trade |
| Overlapping trades | silently overwritten | guarded; optional close-&-reverse (`allow_reverse`) |
| Trade log | 14 fields | **35 fields**: signal candle, entry candle, setup, candle type, 4h trend, RSI/MACD-hist/ADX/ATR/EMA snapshot and every filter's pass/fail |
| Optimizer | none | `optimize_phantom.py` + `optimize_sizing.py`: staged grid + greedy risk tuning with Calmar-style objective and out-of-sample validation |
| Users | single user | **roles:** admin manages client accounts (paper/live permissions); admin panel documents every strategy condition; chart overlays the exact signal candles |

### Baseline vs v3 (full dataset, ₹20,000 start)

| Metric | v2.5 | v3 (low-DD champion) |
| :--- | ---: | ---: |
| Trades | 263 | **1,081** |
| Max drawdown | 30.34% | **4.17%** |
| Win rate | 51.7% | **59.5%** |
| Profit factor | 1.27 | **1.83** |
| Sharpe | 0.74 | **2.40** |

### Run the tuned v3 backtest + full trade log

```bash
# from the repo root
python -m backend.app.scripts.run_phantom_v3        # uses the shipped low-DD champion config
python -m backend.app.scripts.optimize_phantom      # re-tune entry/exit parameters
python -m backend.app.scripts.optimize_sizing       # re-tune leverage/margin/DD-throttle (min DD)
```

Admin panel: sign in with the seeded `admin` account (`python -m app.scripts.seed_admin`
from `backend/`) and open **Admin Panel** in the sidebar to manage clients, read the
full Phantom strategy condition documentation, and control paper sessions. Clients
created there can log in and use Paper/Live trading per the permissions granted.

The trade log is written to `backend/logs/phantom_v3_trades.csv` — one row per trade with the
exact signal candle, execution candle, all indicator values and every entry condition as it
stood at that moment. The same snapshot is also persisted per trade in the `trades` table and
returned by `GET /backtest/results/{run_id}`.

All v3 behaviours are config-driven (`PhantomV2Config`); every new flag defaults to the exact
v2.5 behaviour, so the API, paper trader and live trader remain fully backward compatible.

### v3.2 addon: Direction-specific Long / Short conditions
When the two sides behave differently (REVERSAL-SHORT quality collapses at high ATR/high MACD
histogram), use the **"Use separate conditions for Long / Short"** toggle on the backtest page.
With it ON, the LONG and SHORT branches each carry their own `macd_hist_min` (signed — shorts can
be negative, e.g. `-8`, to require bearish momentum), `stop_loss_atr`, `atr_regime_ratio`,
the optional `atr_regime_max` max-ATR cap (to exclude high-volatility for shorts),
`rsi_oversold`/`rsi_overbought` and `adx_min`. Persisted as `entry_conditions.long.*` /
`entry_conditions.short.*`; any unset value falls back to the shared field. Use the
**Preview Filters** button (or `POST /backtest/filter-preview`) to see per-bucket win rate /
profit factor before running the full backtest, and **Save as New Strategy** to keep a tuned
configuration under a new name for re-running or Paper / Live trading.

---

## 🔌 Exchanges, fees & seeded market data
The admin panel now includes separate **Fees**, **Broker Integrations**, and **Seed Data** tabs:

- Admins manage taker and maker fees in basis points independently for **backtest**, **paper**, and **live** modes and per exchange. New runs snapshot the selected schedule, so changing fees never rewrites historical results. `.env` values remain the first-install fallback only.
- Binance Futures and Delta Exchange are built-in market-data and live-order adapters. Admins can register additional named integrations (a compatible runtime adapter is required before live orders are enabled).
- Seed data is stored as `source + symbol + interval + event_time` and always includes OHLCV volume. The Seed Data tab supports exchange API seeding and CSV import with the required columns `event_time,open,high,low,close,volume`.
- **Delta Exchange seeding:** Delta's `/v2/history/candles` endpoint requires the `resolution` to be a **string label** (`1m`, `5m`, `15m`, `1h`, `4h`, `1d`) and **both** `start` and `end` (Unix seconds) on every request. The adapter (a) maps `BTCUSDT → BTCUSD`, (b) passes the timeframe label through unchanged (numeric/seconds values such as `15`, `60`, `240` return HTTP 400 Bad Request), and (c) derives a `start`/`end` window from the requested `limit` when the caller doesn't supply dates. Seeding from the **Admin → Seed Data** tab (or `POST /admin/market-data/seed` with `"source": "Delta"`) uses this adapter.
- **Delta diagnostics (a seed that fetches 0 candles is no longer silent):** the adapter parses every response shape Delta has used (bare array of dicts, bare array of arrays, `{"result": [...]}`, `{"candles": [...], "result": null}`), falls back from `api.india.delta.exchange` to the CDN host `cdn.india.deltaex.org`, and reports the exact HTTP status / exchange error for each host. Failed intervals appear in the seed response as per-interval `error` entries (status becomes `Seed completed with errors` / `Seed failed`) instead of a bare `fetched: 0`. The **Test connection** button in the Seed Data tab (or `GET /admin/market-data/test?source=Delta&interval=1h`) probes the source with 3 candles and shows what the exchange actually answers.
- **Paper trade details:** every simulated position shows its **Stop / Exit Plan** (current stop loss with the original entry SL and breakeven state, take profit, trailing stop and activation level, active stop, ATR at entry, peak price). Closed trades show the **exit condition** (Take Profit / Trailing Stop / Stop Loss / Max Hold Time) with the exact rule that fired (e.g. "price fell to 67,099 ≤ trail 67,150.00"), exit value, SL (initial → final), TP, trail stop and ATR at entry. The live log prints the same detail on entry and exit.
- Users choose Binance or Delta for each backtest, chart, paper instance, and live instance. Broker Settings supports multiple credential connections, and the existing instance workers allow multiple exchange/strategy sessions to run concurrently.

Useful API endpoints include `GET /broker-definitions`, `GET /broker-connections`, `GET /fee-settings`, `POST /admin/fee-settings`, `POST /admin/market-data/seed`, and `POST /admin/market-data/seed-csv`.

## ⚙️ Configuration
All critical variables are managed in `backend/.env`:
- `DATABASE_URL`: Path to SQLite or Postgres DB.
- `CONVERSION_RATE`: Fixed USD to INR rate.
- `INITIAL_CAPITAL_INR`: Starting balance.
- `SECRET_KEY`: Used for JWT tokens.

## 🛠️ Technical Stack
- **Backend:** FastAPI, SQLAlchemy, Pydantic, NumPy, Pandas.
- **Frontend:** React, Vite, Tailwind CSS, Recharts.
- **Database:** SQLite (Production ready for Postgres).
- **Live Data:** Binance Futures API.
