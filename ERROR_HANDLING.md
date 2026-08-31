# Error Handling — Phantom V3 BTC Perpetual Live Trader

This document describes **server-side and client-side** error handling, DB-level checks, and how the system behaves under failure modes specific to Delta Exchange India + Binance Futures live trading.

---

## 1. Server-Side Error Handling

### 1.1 Centralized Module: `backend/app/core/error_handling.py`

**Philosophy:** All errors return a consistent envelope so frontend, logs, and API clients parse the same shape.

#### Error Envelope
```json
{
  "error": "Human readable message",
  "code": "VALIDATION_ERROR | AUTH_ERROR | RATE_LIMIT | BROKER_AUTH | ...",
  "timestamp": "2026-08-31T08:00:00Z",
  "details": { "field": "...", "hint": "..." } | null,
  "hint": "Actionable next step for user"
}
```

#### Exception Hierarchy
- `PhantomError` (base) → `status_code`, `code`, `details`, `hint`
  - `ValidationError` (400) — bad input: leverage, size, price, symbol, broker, margin mode, order type, side
  - `AuthenticationError` (401) — invalid credentials, expired token
  - `AuthorizationError` (403) — account deactivated, admin-only
  - `NotFoundError` (404) — strategy, connection, run not found
  - `ConflictError` (409) — duplicate username, duplicate broker label
  - `RateLimitError` (429) — throttled, includes `Retry-After`
  - `BrokerError` (502) — venue rejected request, includes `broker` field
  - `MarketDataError` (502) — klines/mark price unavailable

#### Validation Helpers
| Function | Rules | Error Code |
|----------|-------|------------|
| `validate_leverage(v)` | 1-125 int, Binance 125, Delta 100 max but allow 125 for cross-venue | `INVALID_LEVERAGE` |
| `validate_size(v)` | >0, <10000, finite | `INVALID_SIZE` |
| `validate_price(v)` | >0, finite, <1e9 | `INVALID_PRICE` |
| `validate_symbol(v)` | 1-20 chars alphanumeric + _/- | `INVALID_SYMBOL` |
| `validate_broker_code(v)` | normalized via `normalize_source`, must be known | `INVALID_BROKER` |
| `validate_margin_mode(v)` | isolated/cross only | `INVALID_MARGIN_MODE` |
| `validate_order_type(v)` | market/limit/stop/take_profit etc mapped to native | `INVALID_ORDER_TYPE` |
| `validate_side(v)` | long→buy, short→sell, buy/sell passthrough | `INVALID_SIDE` |

All validators raise `PhantomValidationError` with field context.

#### DB Error Mapper: `map_db_error(exc)`
- `IntegrityError` → parses SQLite/Postgres message:
  - `UNIQUE constraint uq_user_broker_label` → `ConflictError(409)` "Broker connection label already exists for this user"
  - `UNIQUE users.username` → `ConflictError(409)` "Username already exists"
  - `UNIQUE broker_definitions.code` → `ConflictError(409)` "Broker code already exists"
  - `UNIQUE fee_settings uq_fee_broker_mode` → `ConflictError(409)` "Fee schedule already exists"
  - `FOREIGN KEY` → `ValidationError(400)` "Referenced record does not exist"
  - `NOT NULL` → `ValidationError(400)` "Required field missing: <field>"
- `SQLAlchemyError` → generic `PhantomError(500)` with rollback hint

#### Broker Error Classifier: `classify_broker_error(message, broker)`
Detects category from venue response text:
- **auth** — keywords: `invalid_api_key`, `invalid_signature`, `authentication`, `unauthorized`, `api key`, `signature mismatch`, `AUTH_REJECTION_MARKERS` (latch after 2 strikes → 5s→300s backoff)
- **rate_limit** — `rate limit`, `too many requests`, `429`, `quota exceeded` → hint: "Retry after X seconds"
- **insufficient_margin** — `insufficient margin`, `margin insufficient`, `not enough balance`
- **invalid_size** — `invalid size`, `lot size`, `min notional`, `precision`
- **invalid_price** — `invalid price`, `price filter`, `tick size`
- **unknown** — fallback, logs full response

Returns: `{ category, code, hint, retryable: bool }`

#### Global Exception Handlers: `register_exception_handlers(app)`
Registered at startup in `main.py`:
```python
@app.exception_handler(PhantomError) → JSON envelope with status_code
@app.exception_handler(HTTPException) → wrapped into envelope (preserves legacy raises)
@app.exception_handler(RequestValidationError) → 422 with field details
@app.exception_handler(Exception) → 500 with generic message, logs traceback, never leaks internals
```

#### Request Logging Middleware
```python
@app.middleware("http")
async def log_requests(request, call_next):
    # Logs 400+ as warning with method+path, unhandled as error with full exc
    # Adds X-Request-ID header tracing (frontend sends fe-<ts>-<rand>)
```

### 1.2 Broker Client Robustness: `backend/app/services/broker_client.py`

Already robust — documented here for completeness:

- **AUTH_REJECTION_MARKERS** tuple: detects auth failure from any Delta/Binance error shape
- **AUTH_LATCH_STRIKES=2**: after 2 auth failures, latches credential as rejected, holds new signed calls
- **Backoff**: 5s → 300s exponential, `credential_health()` reports state
- **Throttling**: `_throttled_request` uses `RateLimiter` per venue:
  - Delta: 10,000 weight / 5 min fixed window
  - Binance: 2,400 weight/min + 1,200 orders/min
  - 429 handling: reads `Retry-After` / `X-RATE-LIMIT-RESET`, sleeps, retries once
- **JSON safety**: `_json_body` returns `{"error": "..."}` instead of raising on non-JSON
- **Signing**: `METHOD + timestamp + path + ?query + body` with compact JSON, `User-Agent: PHANTOM-Trading-Tool/1.0`

### 1.3 Live-Account Endpoints: Validation Wiring

Patched in `main.py`:

- `POST /live-account/orders`:
  - Validates broker, symbol, side, order_type, size, price/stop_price/stop_loss/take_profit via helpers
  - Raises 400 with `VALIDATION_ERROR` envelope on bad input
  - Catches setup exceptions → 500 "Failed to prepare order"
  - Records fills even if order placement partially fails

- `POST /live-account/leverage`:
  - `validate_leverage`, `validate_symbol`, `validate_broker_code`
  - Logs broker error category on failure: `set_leverage failed [auth]: ...`

- `POST /live-account/margin-mode`:
  - `validate_margin_mode`, `validate_symbol`, `validate_broker_code`
  - Same logging pattern

- `POST /broker-connections`:
  - `validate_broker_code`, min length checks for key/secret (8 chars)
  - `IntegrityError` → `map_db_error` → 409 envelope
  - `SQLAlchemyError` → 500 "Database error while saving connection"
  - Whitespace stripping: trailing newline from paste is different key

### 1.4 DB-Level Safety

- **UniqueConstraints**: `uq_user_broker_label`, `uq_fee_broker_mode`, `uq_market_data_seed_progress_range`, `uq_broker_fill_trade`
- **ForeignKeys**: `BrokerConnection.user_id → users.id`, `CustomStrategy.user_id`, `BacktestRun.user_id`, `Trade.run_id`, `PaperSession.user_id`, etc.
- **Indexes**: composite `ix_source_symbol_interval_time` for klines, `ix_market_ticks_source_symbol_time`, per-table `user_id`, `broker_code`, `symbol`
- **Migrations**: `migrate_db()` additive — adds columns, creates indexes with `checkfirst=True`, never drops
- **Seed durability**: `MarketDataSeedProgress` cursor advanced in same transaction as candles, resumable on restart

### 1.5 Integrity Check Script: `backend/scripts/db_integrity_check.py`

```
python -m scripts.db_integrity_check          # human report, exit 0 clean / 1 issues / 2 error
python -m scripts.db_integrity_check --json   # JSON report
python -m scripts.db_integrity_check --fix    # auto-fix: stuck seeds → failed, active no-key connections → deactivated
```

Checks:
- **unique_constraints**: duplicate username, case-insensitive broker code, uq_user_broker_label, uq_fee_broker_mode, duplicate klines timestamp, uq_broker_fill_trade, instance_key unique
- **orphaned_data**: FK violations (connections, strategies, runs, trades, sessions, orders, fills), misconfigured active connections with no key
- **indexes**: critical composite indexes existence
- **seed_progress**: stuck running >2h, failed, zero progress >30min
- **klines_health**: empty series, sparse series (<50% expected), misaligned off-grid timestamps, duplicate rows via `DataSyncService.data_health()`
- **not_null**: username, password_hash, broker code, ohlc nulls

---

## 2. Client-Side Error Handling

### 2.1 API Layer: `frontend/src/api.js`

**Request interceptor:**
- Attaches `Authorization: Bearer <token>` from localStorage
- Adds `X-Request-ID: fe-<timestamp>-<rand>` for backend tracing

**Response interceptor:**
- **401**: token expired/invalid
  - Ignores if request is `/token` (login) or already on `/login` page
  - Dispatches `auth:expired` custom event → `GlobalToastListener` shows warning toast
  - Clears `token`, `role` from localStorage, redirects to `/login` after 800ms (allows toast visible)
- **429**: rate limited
  - Reads `Retry-After` header or `details.retry_after` from envelope
  - Dispatches `api:rate-limited` event → toast with retry hint
- **403**: forbidden
  - Dispatches `api:forbidden` event → error toast
- **5xx**: logs to console `console.error('[API] Server error ...')`
- Always rejects so calling code can `try/catch`

### 2.2 Global Toast System

**`frontend/src/hooks/useToast.js`:**
- `addToast(message, { type, code, details, hint, duration })`
- `toastFromError(error, fallback)` — parses:
  - New envelope: `{ error, code, timestamp, details, hint }` → type based on status (429→warning, 5xx→error)
  - Legacy: `{ detail: string }`
  - Network: `error.request` → "Network error — server unreachable"
  - Generic: `error.message`
- Auto-dismiss after `duration` (default 5s, 8s for 5xx/network)
- `removeToast(id)`, `clearToasts()`

**`frontend/src/components/ToastContainer.jsx`:**
- Fixed bottom-right, max-w-sm, backdrop blur, border color by type
- Shows: message (bold), code (mono, 10px), hint (italic), details (truncated 200 chars)
- Dismiss button per toast

**`frontend/src/main.jsx` GlobalToastListener:**
- Listens to `auth:expired`, `api:rate-limited`, `api:forbidden` custom events from api.js interceptor
- Maintains local toast array, auto-removes after duration

### 2.3 ErrorBoundary: `frontend/src/components/ErrorBoundary.jsx`

- Class component: `getDerivedStateFromError`, `componentDidCatch`
- Logs to console `[ErrorBoundary] Uncaught error`
- UI: red border card, error message, "Try Again" (resets state) + "Reload Page"
- Wraps entire app in `main.jsx`, and `TradingPage` individually
- `fallbackMessage` prop for context-specific messaging

### 2.4 TradingPage Real API Wiring (No Mocks)

**Before:** hardcoded `mockTrades` array, `fetch` with manual headers, no error UI, simplified stop logic.

**After (`frontend/src/pages/TradingPage.jsx`):**
- Uses `api` (axios) with interceptors, not raw `fetch`
- `fetchStatus`: `GET /paper-trade/status` or `/live-trade/status` → aggregates `active_trades` from all instances, computes `accountSummary` (equity, margin, PnL)
- `fetchStrategies`: `GET /strategies` → populates dropdown
- `fetchHistory`: `GET /paper-trade/history` → recent sessions list
- `toggleTrade`:
  - Start: `POST /live-trade/start` or `/paper-trade/start` with `strategy_id` → success toast, refresh after 1s
  - Stop: iterates running instances, `POST /.../stop?instance_key=...` per instance, error toast per failed stop
- `useToast` + `ToastContainer` for user feedback
- Loading states: `loading` (start/stop button), `statusLoading` (refresh spinner)
- Empty states: "Engine idle" vs "No active positions. Engine scanning..."
- Error handling: `toastFromError` on all catch blocks, 401 ignored (handled by interceptor)
- No mock data — all trades from real API response shape: `entry`, `current`, `pnl`, `margin`, `symbol`, `direction`, `entry_time`

### 2.5 Other Frontend Pages

- **LiveTrade.jsx / PaperTrade.jsx**: already use `API_URL` fetch with error alerts; now benefit from global interceptor + ErrorBoundary
- **BrokerSettings.jsx**: `auth()` helper, message state, now benefits from envelope parsing via `toastFromError` pattern (future improvement: migrate to useToast)
- **LiveTerminal.jsx**: `call()` with `detail/error` fallback, now 401 redirect via interceptor instead of manual check

---

## 3. DB-Level Error Handling

### 3.1 Constraints & Indexes (models.py)

| Table | Constraint | Purpose | Error Handling |
|-------|------------|---------|----------------|
| `users` | `username UNIQUE NOT NULL` | Prevent duplicate accounts | 409 ConflictError via map_db_error |
| `broker_definitions` | `code UNIQUE NOT NULL` | One row per venue | Case-insensitive check in API + 409 |
| `broker_connections` | `uq_user_broker_label` | No duplicate labels per user/broker | 409, UI shows "label exists" |
| `fee_settings` | `uq_fee_broker_mode` | One schedule per broker/mode | 409 |
| `klines` | `ix_source_symbol_interval_time` | Fast range queries, dedup check | Duplicate rows detected by integrity script |
| `broker_fills` | `uq_broker_fill_trade` | No double-count fills | Integrity check flags duplicates |
| `paper_sessions` | `instance_key UNIQUE` | One row per instance | 409 if race |
| `market_data_seed_progress` | `uq_market_data_seed_progress_range` | Durable cursor, no overlapping seeds | Seed job checks before insert |

### 3.2 Orphan Handling

- **FKs defined**: SQLAlchemy relationships with `ForeignKey`, but SQLite FK enforcement is off by default — integrity script checks orphans explicitly
- **Cascade**: `User.broker_connections` has `cascade='all, delete-orphan'` — deleting user removes connections
- **Orphan checks**: integrity script queries `~id.in_(parent)` for all FK relationships
- **Misconfigured**: active connection with empty `api_key` → flagged as misconfigured, auto-fix deactivates

### 3.3 Seed Progress Durability

- **Cursor**: `next_start` advanced in same transaction as inserted klines
- **Stuck detection**: `status=running` but `updated_at < now-2h` → flagged, auto-fix marks `failed`
- **Resume**: `DataSyncService` skips completed ranges, resumes at `next_start` cursor, so re-running never refetches finished windows
- **Repair**: `repair_klines` removes duplicate timestamps + off-grid candles (legacy CSV imports)

### 3.4 Klines Duplicate/Misaligned Repair

- **Duplicate**: legacy batch inserts without upsert → same `event_time` multiple times → `repair_klines` keeps first, deletes rest
- **Misaligned**: legacy CSV with timestamps off grid (e.g., 11:41:59 on 1h) → `data_health()` scans exact timestamps vs interval grid, capped scan
- **Integrity report**: `duplicate_rows = count - distinct(event_time)`, `misaligned_rows` from health scan
- **Admin endpoints**: `POST /admin/market-data/repair` (no fetch, just cleanup), `POST /admin/market-data/seed?repair=true` (repair + fetch)

---

## 4. Testing Error Handling

### Backend Tests (16 suites, 922+ PASS)

- `test_live_account.py`: mocks Binance + Delta, covers 429 retry, auth-error verdict, rate limits, normalized schema
- `test_broker_client.py`: AUTH markers, latch, backoff, throttling
- `test_error_handling.py` (new, should be added): validates `validate_*` helpers, `map_db_error`, `classify_broker_error`, envelope shape

### Frontend Tests (7 suites, 345 PASS)

- `api.test.js`: request interceptor attaches token, response interceptor handles 401/429
- `TradingPage.test.js`: no mocks, real API wiring, error toast on failure

### Manual Test Checklist

- [ ] Invalid leverage (0, 200) → 400 `INVALID_LEVERAGE` envelope, toast shows hint
- [ ] Duplicate broker label → 409 `CONFLICT`, toast "label already exists"
- [ ] Expired token → 401, toast "Session expired", redirect to /login after 800ms
- [ ] Rate limited (429) → warning toast with retry-after
- [ ] Broker auth failure → 502 `BROKER_AUTH`, hint "Check API key/secret, IP whitelist"
- [ ] DB integrity script → `python -m scripts.db_integrity_check` → 0 issues on clean DB
- [ ] Stuck seed → insert running row with old updated_at, run script with --fix → marked failed

---

## 5. Future Improvements

- Migrate all frontend pages from raw `fetch` to `api` axios instance for consistent error handling
- Add `test_error_handling.py` backend suite covering all validators + mappers
- Add Sentry or backend `/logs` endpoint for ErrorBoundary reports
- Enable SQLite FK enforcement: `PRAGMA foreign_keys=ON` in engine connect args
- Add `ON CONFLICT DO NOTHING` upsert for klines to prevent duplicates at DB level
- Frontend: replace `alert()` with `useToast` in PaperTrade.jsx, LiveTrade.jsx, BrokerSettings.jsx
