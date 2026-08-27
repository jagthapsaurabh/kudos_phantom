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
| Trade log | 14 fields | **45 exported columns**: which candle signalled, which candle the entry filled on and its colour, every entry condition (value vs threshold, pass/fail), the exit rule that fired and the stop plan |
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

### v3.2 addon: Direction-specific Long / Short thresholds
When the two sides behave differently, the Backtest page keeps the shared values as the default and
places two independent switches below them: **Use separate Long / Short MACD hist** and
**Use separate Long / Short Min ATR floor**. With the MACD switch on, LONG uses `hist >=` its value
and SHORT uses `hist <=` its signed value (for example `-8` requires bearish momentum). With the ATR
switch on, each side picks **both** the comparison and the value: an operator dropdown
(`>=`, `<=`, `>`, `<`) next to its ratio, so LONG can require `ATR ≥ 0.5 × SMA(ATR, 50)` while SHORT
only trades when volatility is calm (`ATR < 1.2 × SMA(ATR, 50)`). Both sides start on the original
`>=` rule, so switching the toggle on never changes behaviour until the client edits it; an unset
side falls back to the shared field. Values are persisted as
`entry_conditions.long.*` / `entry_conditions.short.*` (`atr_regime_ratio`, `atr_regime_op`,
`atr_regime_max`). The legacy `use_direction_conditions` master switch remains supported for existing
configurations.
Use **Preview Filters** (or `POST /backtest/filter-preview`) to see the per-bucket trade-off before
running the full backtest — the response echoes the exact rule per side in `atr_regime_rules`, and each
trade's expanded log row shows the ATR test that filtered it. **Save as strategy** keeps a tuned
configuration under a name for re-running or Paper / Live trading.

Opening any saved Backtest history card now restores its saved dates, exchange, strategy, capital,
and complete parameter snapshot before showing the result.

### Trade log: which candle, which colour, and the full export
The trade log answers three questions for every trade — **which candle raised the signal**, **which
candle the entry actually filled on**, and **what colour each was**. The strategy fires on candle *i*
and fills at the open of candle *i+1*, so the log keeps both: a **Signal Candle** column and an
**Entry Candle** column, each showing a UTC timestamp plus a colour chip (`▲ GREEN`, `▼ RED`,
`● DOJI`). The **Exit** cell does the same for the candle the position closed on. Expanding a row
spells out all seven entry conditions with the measured value against the threshold applied
(`2. ADX: 13.3 >= min 10.0 -> PASS`), the exit rule that fired, and the stop plan in force
(`SL: `, `Trail stop: `, `ATR@entry: `, `Peak: `).

Conditions that the firing setup never applies show **`N/A`**, not `FAIL` — a momentum entry (Setup B)
enters on the MACD zero-cross, so the MACD-histogram magnitude test is not used for it. That keeps a
trade that legitimately fired from looking like it broke its own rules.

**Excel / CSV Export** writes one row per trade with 45 columns: signal/entry/exit candle times (UTC,
to the second) and colours, one column per entry condition (`PASS` / `FAIL` / `N/A`), the full
condition breakdown, the exit condition and its detail, the stop plan and the PnL fields. The file is
UTF-8 with a BOM and CRLF line endings, so Excel opens it without turning `₹` and `≥` into mojibake.

That button is separate from the raw engine dump described above
(`backend/logs/phantom_v3_trades.csv`, 47 machine-readable columns written by
`BacktestEngine.export_trade_log`). The engine file keeps the raw values — `cond_*_ok` as
`True`/`False`/blank, snake_case headers — for scripting; the UI export is the human-readable sheet,
rendering those same flags as `PASS` / `FAIL` / `N/A` and adding the candle colours and times.

Paper sessions export the same per-trade fields (`entry_time, exit_time, direction, symbol, entry,
exit, lots, margin_inr, notional_usd, sl, sl_final, tp, trail_stop, atr_at_entry, peak_price,
bars_held, reason, exit_detail, gross_pnl, fees, pnl`) from the History panel.

### Running the tests
```bash
# backend (offline; no exchange or DB seed required)
cd backend && ../.venv/bin/python test_trade_log_detail.py   # 50 checks: candles, colours, conditions, export columns
cd backend && ../.venv/bin/python test_atr_regime_op.py      # 32 checks: per-side ATR operator
cd backend && ../.venv/bin/python test_paper_history.py      # 56 checks: paper history persistence

# frontend (renders the real components with react-dom/server)
cd frontend && npm test                                      # 76 checks: trade-log table + CSV export
```
`test_delta_and_paper.py` now points `DATABASE_URL` at a temporary SQLite file before importing the
app, so running the suite no longer clears the seeded candles in `backend/trading_system.db`.

---

## 🔌 Exchanges, fees & seeded market data
The admin panel now includes separate **Fees**, **Broker Integrations**, and **Seed Data** tabs:

- Admins manage taker and maker fees in basis points independently for **backtest**, **paper**, and **live** modes and per exchange. New runs snapshot the selected schedule, so changing fees never rewrites historical results. `.env` values remain the first-install fallback only.
- Binance Futures and Delta Exchange are built-in market-data and live-order adapters. Admins can register additional named integrations (a compatible runtime adapter is required before live orders are enabled).
- Seed data is stored as `source + symbol + interval + event_time` and always includes OHLCV volume. The Seed Data tab supports exchange API seeding and CSV import with the required columns `event_time,open,high,low,close,volume`.
- **Delta Exchange seeding:** Delta API `/v2/history/candles` requires the `resolution` to be a **string label** (`1m`, `5m`, `15m`, `1h`, `4h`, `1d`) and both `start` and `end` Unix timestamps. Delta returns at most **2,000 candles per request** and charges three rate-limit units for OHLC requests. The Seed Data tab **Delta 2020 → today** preset therefore requests only `15m`, `1h`, `4h` and `1d`, splits 1 Jan 2020 → today into safe windows, paces requests and retries HTTP 429 responses. `1m` and `5m` are intentionally excluded from this full-history plan. Each committed window stores a durable cursor in `market_data_seed_progress`, so an interrupted range resumes without re-fetching committed windows; repeating a completed range is a no-op.
- **Delta diagnostics (a seed that fetches 0 candles is no longer silent):** the adapter maps `BTCUSDT → BTCUSD`, parses every response shape Delta has used (bare array of dicts, bare array of arrays, `{"result": [...]}`, `{"candles": [...], "result": null}`), falls back from `api.india.delta.exchange` to the CDN host `cdn.india.deltaex.org`, and reports the exact HTTP status / exchange error for each host. Failed intervals appear in the seed response as per-interval `error` entries. The **Test connection** button (or `GET /admin/market-data/test?source=Delta&interval=1h`) probes the source with a safe request before a long seed.
- **Daily refresh:** after startup and every 24 hours, the API incrementally refreshes all supported candle intervals for Binance and every enabled Binance-compatible or Delta-compatible broker integration. Delta daily refresh also omits `1m`/`5m`; generic integrations are reported as skipped until a compatible adapter is configured. **Run daily refresh now** in the Seed Data tab runs the same cycle immediately.
- **Paper trade details:** every simulated position shows its **Stop / Exit Plan** (current stop loss with the original entry SL and breakeven state, take profit, trailing stop and activation level, active stop, ATR at entry, peak price). Closed trades show the **exit condition** (Take Profit / Trailing Stop / Stop Loss / Max Hold Time) with the exact rule that fired (e.g. "price fell to 67,099 ≤ trail 67,150.00"), exit value, SL (initial → final), TP, trail stop and ATR at entry. The live log prints the same detail on entry and exit.
- Users choose Binance or Delta for each backtest, chart, paper instance, and live instance. Broker Settings supports multiple credential connections, and the existing instance workers allow multiple exchange/strategy sessions to run concurrently.
- **Paper trade history (results survive a stop):** every paper instance is mirrored into the `paper_sessions` table while it runs — equity curve, closed trades, log buffer, fees, sizing and the parameter snapshot. **Stopping** an instance keeps its result; the new **Paper Trade History** panel lists every session with status, final equity, net PnL, ROI, win rate, profit factor, max drawdown and trade count, and expands to the full saved result (stats, equity-curve chart, closed-trade table, saved logs and any positions still open at stop) with CSV export. Status is `running`, `stopped` or `interrupted` (the server restarted mid-session, so the row explains itself instead of vanishing). Only an explicit delete — the workspace delete on a live card, or History → Delete — removes a saved result.

Useful API endpoints include `GET /broker-definitions`, `GET /broker-connections`, `GET /fee-settings`, `POST /admin/fee-settings`, `POST /admin/market-data/seed`, `GET /admin/market-data/progress`, `POST /admin/market-data/sync-now`, `POST /admin/market-data/seed-csv`, `GET /paper-trade/history`, `GET /paper-trade/history/{session_id}`, and `DELETE /paper-trade/history/{session_id}`.

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
