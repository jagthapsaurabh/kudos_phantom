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
| `GET` | `/broker-connections` | None | Current user's masked, multi-broker connections, each with the `account_settings` last read from the venue (margin mode, leverage, sub-account list) |
| `POST` | `/broker-connections` | `broker_code`, `label`, `api_key`, `api_secret`, `passphrase`, `is_testnet` | Add a credential connection; multiple may be active. On save the server reads the account's real settings from the exchange (Delta: `GET /v2/sub_accounts` + product leverage; Binance: `positionRisk`) and returns them as `account_settings` — a bad key is visible immediately |
| `PUT` | `/broker-connections/{id}` | same as POST | **Replace the keys** on a connection. Blank `api_secret` keeps the stored one (secrets are never returned); pasted whitespace is trimmed before storing, because a trailing newline from a terminal paste is a different key to the venue. Account settings are re-read from the venue and the new credentials are handed to every live instance trading on this connection, so a rotated key takes effect without a restart — the response reports it as `live_instances: {notified, reloaded, verified, instances: [{instance_key, reloaded, verified, state}]}` |
| `POST` | `/broker-connections/{id}/refresh` | None | Re-read margin mode / leverage / sub-accounts from the venue for one connection (use after rotating a key or changing margin mode on the exchange). Auth failures come back as `account_settings.error`, so this doubles as a key check |
| `POST` | `/broker-connections/{id}/probe` | None | **Check key.** Signs one `GET /v2/wallet/balances` against each Delta India environment and says which one accepts this key: `{environment, accepted_by, rejected_by, unreachable_from_here, matches_connection, mismatch, summary, fix, rows}`. A production key on a testnet-flagged connection (or the reverse) is reported as a toggle to flip, not a key to re-create; only when every answering host rejects it is the key itself declared dead. Runs from the server, which is the machine a whitelisted key trusts. When the probe succeeds the connection's cached `account_settings` refresh and running instances re-read it |
| `GET` | `/broker-connections/diagnose` | `broker`, `connection_id` | Whether THIS login can trade that broker: the registry entry, every saved connection (stored vs resolved code, masked key, secret present, on/off, testnet, account settings), `ready` and a plain-language `problems` list |
| `DELETE` | `/broker-connections/{id}` | None | Remove a user's connection |
| `GET` | `/broker-settings` | None | Get capital defaults and masked legacy settings |
| `POST` | `/broker-settings` | `api_key`, `api_secret`, `initial_capital`, `margin_pct`, `broker_name` | Update legacy/primary broker keys and capital |
| `GET` | `/fee-settings` | `broker_code`, `mode` | Read the admin schedule used by a new run |
| `GET` | `/admin/brokers` | None | Every broker definition **including its rate-limit and trading defaults** |
| `PUT` | `/admin/brokers/{id}` | `code`, `name`, `kind`, `market_data_url`, `trading_api_url`, `enabled`, `notes`, `rate_limit_per_second`, `rate_limit_per_minute`, `quota_per_5min`, `orders_per_minute`, `default_leverage`, `margin_mode`, `contract_value`, `tick_size` | Edit a broker (admin). Admin-only; blanks fall back to the venue default |

Fees are managed by admins in basis points using `POST /admin/fee-settings` with `broker_code`, `mode` (`backtest`, `paper`, or `live`), `taker_fee_bps`, and `maker_fee_bps`. Schedules are snapshotted on backtest runs; `.env` is only the first-install fallback.

### 3. Charts & Market Data
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/klines` | `symbol`, `interval`, `limit`, `source` | Fetch source-specific OHLCV candle data (including volume) for charts |
| `GET` | `/symbols` | `source` | List symbols available in the selected local market-data source |
| `GET` | `/market/contract` | `source` | The BTC **perpetual** contract traded on that venue (`BTCUSDT` on Binance, `BTCUSD` on Delta) |
| `GET` | `/market/mark-price` | `source`, `symbol` | Live mark price, traded (last) price and index price of the perpetual |
| `GET` | `/trading-windows` | None | The account's default "skip new trades" schedule + whether entries are paused now |
| `PUT` | `/trading-windows` | `enabled`, `timezone`, `block_exits`, `windows[]` (or `quick_days[]`) | Save the account default used when a start request omits a schedule |
| `GET` | `/phantom/signals` | `symbol`, `source`, `start_date`, `end_date`, `strategy_id` | Signal candles for the chart overlay. `strategy_id` may be `PhantomV2` (default, tuned champion), `FastTest`, or a custom strategy id created in the Strategies manager. |

### 4. Paper Trading
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/paper-trade/start` | `strategy_id`, `broker_name`/`data_source`, `connection_id`, `initial_capital` (optional), `margin_pct` (optional), `use_mark_price` (optional), `trading_windows` (optional) | Starts a source-specific simulation instance using the admin's paper fee schedule. Multiple exchange instances can run concurrently. Returns the `instance_key`, the `session_id` of its saved history row, and the resolved `contract` / `trading_windows` summary. |
| `POST` | `/paper-trade/stop` | `instance_key` | Stops a specific simulation instance. The result is **saved** to History (`saved_to_history`, `session_id` in the response). |
| `DELETE` | `/paper-trade/{instance_key}` | None | Stops a session and permanently removes it, including its saved history row (`history_removed`) |
| `GET` | `/paper-trade/status` | None | List all running instances, open positions & closed-trade history, including the saved strategy name, `session_id`, the live equity curve, the BTC-perpetual mark/traded prices (`last_mark_price`, `last_trade_price`, `mark_price_basis`), the instance's `trading_windows` schedule, whether `entry_paused` is true right now and how many entries it has `blocked_entries`, plus the entry-gate counters `skipped_entries` / `last_skip_reason` (signals refused because a position is already open, the candle was already traded, or the post-exit cooldown is running) |
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
| `POST` | `/live-trade/start` | `strategy_id`, `broker_name`/`data_source`, `connection_id`, `initial_capital`, `margin_pct`, `use_mark_price` (optional), `trading_windows` (optional), `price_feed` (optional), `tick_interval` (optional) | Starts a real-money execution instance on the selected broker using the live fee schedule. Orders go to the venue's BTC perpetual and risk is managed on the mark price when `use_mark_price` is true. `connection_id` picks which saved connection (sub-account / API key) this instance trades with; omitted uses the account's primary connection. `price_feed` is `off` (default, exits re-checked every 60s), `websocket` (venue price stream) or `rest` (polled mark price); `tick_interval` (1–60s, default 5) sets how often open positions are re-marked. Only exits speed up — entries still wait for a closed 1h candle. A bad mode or interval is rejected with 400 rather than silently downgraded. |
| `POST` | `/live-trade/reload-credentials` | `instance_key`, optional `connection_id` | Re-read a running instance's saved connection and swap it in — credentials, the account it queues on, its rate-limit budget and its deadman switch — then probe once. Use it after fixing a rejected key instead of restarting (which would lose the instance's local book); `connection_id` also re-points an instance whose original connection was deleted and re-added. Returns `{reloaded, verified, reason, source, fingerprint, credentials}` |
| `POST` | `/live-trade/stop` | `instance_key` | Stops a live execution instance |
| `GET` | `/trade-executions` | None | Executed trades for the market-chart overlay: which candle each entry/exit landed on, with the stop plan (SL at entry vs in force at exit, TP, trailing stop), PnL, exit reason and bars held. Combines running live instances (open position + trades closed since start), running paper instances, and saved paper sessions from History (a still-running session is reported once, from the worker). Times are IST-offset ISO strings; backtest runs keep their `/chart?run=<id>` deep link |
| `GET` | `/live-trade/status` | None | List all live instances and positions, plus the perpetual `contract`, mark/traded prices, the `trading_windows` schedule in force, `entry_paused` and `blocked_entries`, plus the entry-gate counters `skipped_entries` / `last_skip_reason`, any `exchange_position` the venue already holds (new entries are held so it is never doubled up), `last_order_error`, `price_feed` (`mode`, `kind`, `connected`, `stale`, `age_seconds`, `messages`, `reconnects`, `fast_ticks`, `last_error` — a stale feed means exit checks have fallen back to the 60-second cadence), and `shared_account` when other live runs point at the same API key (`strategies_on_account`, `queue_position`, `position_held_by`, `holds_account_position`, `other_strategies`) — one account carries one netted position per contract, so those runs take turns, `connection_id`, and `credentials` — the instance's own credential state (`state: ok | suspect | rejected`, the venue's `error`, `environment`, `key` fingerprint, `retry_in_seconds`, `entries_held`, `reloads`, `last_reload`, `heartbeat_stood_down`); a `rejected` state means entries are being held and the worker is re-reading the saved key on that backoff |

Every order a live instance sends is mirrored into `broker_orders` (with its `leg`, `client_order_id`
and `instance_key`) and every execution into `broker_fills`, so the terminal keeps an audit trail
after the exchange drops the order from its own history window.

### 5b. Live Account — orders, positions, fills, margin (the terminal)

| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/live-account/snapshot` | `broker`, `connection_id`, `symbol`, `include_history`, `history_limit` | Everything the terminal renders in one call: `contract`, `mark_price`, `balance`, `risk`, `positions`, `open_orders`, `stop_orders`, `fills`, `order_history`, `errors`, `rate_limits`, plus `account_settings` — the margin mode / leverage / sub-account list as the *venue* holds them for the selected connection (the terminal's margin-mode select and leverage input start from these, never a hardcoded default) — and `auth_error`, a single plain-language verdict when every signed section was auth-rejected (invalid/regenerated key, or a production key on a testnet connection) — it names **Replace keys** and **Reload keys**, because a running instance now re-reads its connection instead of needing a restart. `rate_limits.credential_health` carries the same state (`state`, `error`, `consecutive_rejections`, `strikes`, `retry_in_seconds`, `held_calls`, `key`, `environment`) |
| `POST` | `/live-account/orders` | `broker`, `connection_id`, `symbol`, `side`, `order_type`, `size`, `size_in_btc`, `price`, `stop_price`, `trail_amount`, `reduce_only`, `post_only`, `time_in_force`, `working_type`, `client_order_id`, `stop_loss`, `take_profit`, `stop_trigger`, `source`, `instance_key` | Place an order. With `stop_loss` / `take_profit` it becomes a **bracket** (native on Delta, emulated with reduce-only legs on Binance). Sizes may be given in BTC (`size_in_btc: true`) and are converted into the venue's own units |
| `POST` | `/live-account/orders/cancel` | `broker`, `connection_id`, `order_id` or `client_order_id`, `symbol` | Cancel one order and mark the local row cancelled |
| `POST` | `/live-account/orders/cancel-all` | `broker`, `connection_id`, `symbol` | Cancel every open order on the contract |
| `POST` | `/live-account/positions/close` | `broker`, `connection_id`, `symbol`, `size`, `size_in_btc` | Flatten (or partially reduce) the position with a reduce-only market order |
| `POST` | `/live-account/leverage` | `broker`, `connection_id`, `symbol`, `leverage` | Set contract leverage |
| `POST` | `/live-account/margin-mode` | `broker`, `connection_id`, `symbol`, `mode` (`isolated`\|`cross`) | Set the margin mode |
| `POST` | `/live-account/position-margin` | `broker`, `connection_id`, `symbol`, `amount` | Add (positive) or remove (negative) isolated margin |
| `GET` | `/live-account/rate-limits` | `broker` (query), `connection_id` | Local throttling windows **and** the venue's own remaining quota (Delta), plus `credential_health` — whether the key on this connection is being accepted (`state`, `error`, `consecutive_rejections`, `retry_in_seconds`, `held_calls`) |
| `GET` | `/live-account/orders` | `broker`, `limit` | Orders sent through PHANTOM, from the local audit table |
| `GET` | `/live-account/fills` | `broker`, `limit` | Executions recorded locally (kept after the exchange history window) |

All of them require API keys; a missing credential returns `400 API keys not configured for <broker>`.
Broker failures come back as `{"error": "..."}` (never a 500) so a trading loop survives a rejected
order, and `{"error": ..., "rate_limited": true}` marks a 429 that exhausted its retries.

Example — place a bracket order for 0.05 BTC with a stop and a target:

```json
POST /live-account/orders
{
  "broker": "Delta",
  "side": "buy",
  "order_type": "market",
  "size": 0.05,
  "size_in_btc": true,
  "stop_loss": 65000,
  "take_profit": 70000,
  "stop_trigger": "mark_price"
}
```

```json
{
  "status": "placed",
  "broker": "Delta",
  "symbol": "BTCUSD",
  "contract_value": 0.001,
  "client_order_id": "ph-9f2c…",
  "orders": [
    { "leg": "entry",        "type": "market",              "qty_btc": 0.05 },
    { "leg": "stop_loss",    "type": "stop_market",         "qty_btc": 0.05, "stop_price": 65000 },
    { "leg": "take_profit",  "type": "take_profit_market",  "qty_btc": 0.05, "stop_price": 70000 }
  ],
  "rate_limits": { "requests_last_second": 3, "orders_last_minute": 3, "limits": { "requests_per_second": 20 } }
}
```

### 5c. Broker rate limits

The venue research behind the table below (published limits, weights and the reasoning for the
conservative defaults) is written up in `docs/order_management_research.md`.


Every broker call — market data, orders, the terminal poller — goes through one shared
`RateLimiter` per broker connection (`app/core/rate_limit.py`), so several workers can never
outrun the venue together.

| Venue | Documented limit | What the client enforces |
| :--- | :--- | :--- |
| **Delta Exchange** | 10 000 weight per **fixed** 5-minute window; 500 matching-engine ops/second/product; 429 carries `X-RATE-LIMIT-RESET` (ms) | 20 req/s and 1 200 req/min plus the weight budget; `GET /v2/rate_limits/quota` is polled and the remaining quota paces further calls |
| **Binance Futures** | 2 400 request-weight/minute and 1 200 **orders**/minute per IP; every response carries `X-MBX-USED-WEIGHT-1M` | 20 req/s, 1 200 req/min, order slots counted separately; usage headers tracked and calls slowed at 85 % of the budget |

A 429 is retried up to 4 times honouring `Retry-After` (seconds) / `X-RATE-LIMIT-RESET`
(milliseconds) before falling back to exponential back-off (0.35 s base, 30 s cap); if it still
fails the call returns a `rate_limited` error object instead of raising. The safe default of
**20 requests/second** is deliberately well inside both venues' budgets and can be dialled per
broker from **Broker → Exchange Registry → Limits**.

### 6. Backtesting
| Method | Endpoint | Request Body | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/backtest` | `params`, `strategy_id`, `start_date`, `end_date`, `strategy_name`, `initial_capital` (optional), `data_source`, `fee_mode`, `use_mark_price` (optional), `trading_windows` (optional) | Triggers a source-specific background backtest using the admin fee schedule. Defaults to the user's (admin-set) initial capital. |
| `POST` | `/backtest/filter-preview` | `params`, `start_date`, `end_date`, `symbol`, `data_source`, `fee_mode` | Synchronous per-bucket peek at the current conditions (`LONG/SHORT x REVERSAL/MOMENTUM`) with win rate, profit factor and avg/net PnL — handy while tuning the direction-specific thresholds before a full run. |
| `GET` | `/backtest/history` | None | Lists all previous backtest runs (incl. `strategy_id` and `initial_capital`) |
| `GET` | `/backtest/results/{id}` | None | Get detailed trades, equity curve and the exact saved `params` snapshot for a run (incl. `strategy_id`, `initial_capital`, `use_mark_price`, `trading_windows_enabled`, `blocked_entries` and `contract`) |
| `DELETE` | `/backtest/{id}` | None | Delete a single backtest run and its trades |
| `DELETE` | `/backtest/clear` | None | Delete all of the user's backtest runs |

**BTC perpetual, mark price and "skip new trades" windows.** Every run — backtest, paper and live —
is executed on the venue's BTC **perpetual** (`BTCUSDT` on Binance, `BTCUSD` on Delta; dated futures
are never substituted) and priced on the exchange **mark price** by default:

* `params.use_mark_price` (default `true`) — stops, targets, trailing, breakeven and PnL are computed
  on the mark price. The traded price is recorded on every trade next to it, so both are always
  stored: `entry_price`/`exit_price` hold the pricing basis, `entry_trade_price`/`exit_trade_price`
  the traded fill, and `entry_mark_price`/`exit_mark_price` the mark price at that instant
  (`mark_price_basis` = 1 when the maths ran on mark).
* `params.trading_windows` (or the top-level `trading_windows` field) — a schedule of periods in
  which **new** entries are refused:

```json
{
  "enabled": true,
  "timezone": "Asia/Kolkata",
  "block_exits": false,
  "windows": [
    { "label": "Weekend gap", "start_day": "sat", "start_time": "18:30",
      "end_day": "mon", "end_time": "01:00", "all_day": false, "enabled": true },
    { "label": "Skip Tuesday", "start_day": 1, "end_day": 1, "all_day": true, "enabled": true }
  ]
}
```

  Days are `0=Mon … 6=Sun` (names are accepted too). An `all_day` window covers every minute from
  the start day through the end day. A timed window whose end precedes its start wraps past Sunday,
  which is how "Saturday 18:30 → Monday 01:00" is expressed. Any number of windows can be combined.
  `block_exits` is off by default: **positions that are already open keep their stop, target,
  breakeven and trailing rules** — only new entries are skipped. Refused entries are counted in
  `diagnostics.blocked_entries` and reported as the rejection reason `TRADING_WINDOW`.
* The account default lives in `GET`/`PUT /trading-windows` and on `/broker-settings`
  (`use_mark_price`, `trading_windows`); a start request may override it per run/instance.
* Historical mark prices are seeded onto the existing candles (`klines.mark_open/high/low/close`)
  by passing `include_mark_price: true` to `POST /admin/market-data/seed`, and are refreshed by the
  daily sync. Where no mark series exists the engine falls back to the traded price and reports
  `mark_price_basis: false` (and `mark_price_coverage` as a percentage) instead of failing.

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
| `POST` | `/admin/market-data/seed` | `source`, `symbol`, `intervals`, dates, `limit`, `fetch_all`, `repair`, `background` | Fetch and upsert OHLCV data with volume. Full-history (`fetch_all`) requests default to 1 Jan 2020 for every source, are split into API-safe windows (Binance 1,500 / Delta 2,000 candles) and include the daily `1d` candles. `repair: true` deletes duplicate and off-grid candles first. `background: true` runs the seed in a server-side worker so a long range can never be killed by a request timeout, and returns immediately. Delta full history excludes `1m`/`5m`. |
| `GET` | `/admin/market-data/seed-job` | None | Live state of the background seed worker: running flag, request and last result |
| `POST` | `/admin/market-data/repair` | `source`, `symbol`, `intervals` (query) | Remove corrupted candles without fetching: duplicate timestamps (legacy batch inserts) and timestamps off the interval grid (legacy CSV imports, e.g. `11:41:59.523330` on a 1h series). Well-formed rows are never touched; re-seed afterwards to refill. |
| `POST` | `/admin/market-data/sync-now` | None | Run the same incremental multi-source refresh used by the daily scheduler |
| `POST` | `/admin/market-data/seed-csv` | Multipart CSV + `source`, `symbol`, `interval` | Import `event_time,open,high,low,close,volume` data. Rejected when timestamps are not aligned to the interval grid — this is what stopped the corrupted legacy CSVs from being imported again. |
| `GET` | `/admin/market-data/status` | `?health=1` | Dataset counts, ranges, volume coverage and `duplicate_rows`; with `health=1` also `misaligned_rows` (off-grid candles) per series |
| `GET` | `/admin/market-data/progress` | None | Durable full-history seed cursors, status, counts and last error |

For the requested Delta backfill, send `source: "Delta"`, `intervals: ["15m", "1h", "4h", "1d"]`, `start_date: "2020-01-01"`, `end_date` as today, `limit: 2000`, and `fetch_all: true`. The server makes one bounded request per window because Delta returns at most 2,000 OHLC candles and charges three rate-limit units per request. `1m` and `5m` are rejected for this Delta seed plan. Every completed window and its next cursor are committed atomically with the candles in `market_data_seed_progress`; a restart resumes the saved cursor and an already-completed request is not fetched again. `GET /admin/market-data/progress` exposes this state. The daily scheduler refreshes all supported intervals for Binance and enabled Binance-compatible / Delta-compatible broker definitions; Delta daily refresh also excludes `1m` and `5m`. Generic broker definitions are reported as skipped until an adapter is configured.

Long ranges are engineered never to break mid-fetch: every exchange request retries transient failures (timeouts, connection resets, HTTP 429/5xx) with growing backoff and `Retry-After` honouring, and each window is additionally retried at the same cursor before the range is marked failed — at which point the durable cursor keeps every committed window, the error says to re-run, and a re-run resumes instead of restarting. `background: true` (set automatically by the admin UI's full-history presets) moves the whole job into a server-side worker guarded by a single-job lock, so browser/proxy timeouts cannot truncate a hours-long walk; poll `GET /admin/market-data/progress` and `GET /admin/market-data/seed-job`. Retry counts are tunable via `SEED_REQUEST_RETRIES`, `SEED_REQUEST_BACKOFF_SECONDS`, `SEED_WINDOW_RETRIES` and `SEED_WINDOW_BACKOFF_SECONDS`. Mark prices follow the same batching: a full-history seed pages the mark series across the whole 2020 → today range instead of a single request.

The Binance backfill is identical in shape — `source: "Binance"`, `intervals: ["15m", "1h", "4h", "1d"]`, `limit: 1500`, `fetch_all: true` — and is the replacement for corrupted CSV history: candles come straight from the Binance Futures API and every candle is upserted, never duplicated. Omitting `start_date` in full-history mode now defaults to 1 Jan 2020 for Binance too. Send `repair: true` on the first run (the admin UI's "Binance 2020 → today" preset does this automatically) to delete the duplicate and off-grid candles left behind by the legacy seeder/CSV imports; the response reports how many corrupt candles were removed per interval, and `GET /admin/market-data/status?health=1` shows `duplicate_rows` / `misaligned_rows` so a corrupt series is visible at a glance.

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
