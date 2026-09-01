# Delta Exchange Alignment — Phantom V3 BTC Perpetual Live

This doc maps **official REST** (docs.delta.exchange/#ApiSection) and **MCP use-cases** (mcp.delta.exchange/docs/use-cases) to *our software's actual flows*, not generic parity.

## Deployment Target (this system): DELTA INDIA PRODUCTION

The deployment decision for this system — every Delta connection, key and live
instance belongs on **Delta India production** (`INDIA-PRODUCTION`):

| | Endpoint |
| :--- | :--- |
| REST | `https://api.india.delta.exchange` |
| Private WebSocket | `wss://socket.india.delta.exchange` |
| Public WebSocket | `wss://public-socket.india.delta.exchange` |
| App broker code | `Delta` · testnet **OFF** |

The official key/API rule (as quoted by the operator) is enforced by the app:

* API keys created on the **Delta India** account (www.delta.exchange) → used
  **only** with the production API `https://api.india.delta.exchange`.
* API keys created on the **Demo** account (demo.delta.exchange) → used
  **only** with the testnet API `https://cdn-ind.testnet.deltaex.org`.
* `https://api.delta.exchange` belongs to **Delta Global** and is **not used
  here**.

Enforcement: `DELTA_DEPLOYMENT_FAMILY` defaults to `india` (set it to `global`
only on a box that trades the Global market). On an India box the app refuses
to *create* a DeltaGlobal connection, refuses to *switch* a connection onto
DeltaGlobal, and refuses to *align* onto the Global family — each 400 carries
the rule text (`BrokerClient.DELTA_FAMILY_RULE`). The read-only four-host key
probe still signs one call per environment, so it can *tell you* a key is a
Global key (and then you re-create it on India); it never places orders.

`INDIA_PRODUCTION` (also `INDIA-PRODUCTION`) is a first-class environment name
in the app: `BrokerClient.delta_environment()` resolves it, and the one-shot
align actions below repoint saved connections at it **without needing the
stored key to pass a probe first** (the probe-detection flow needs the venue to
accept the key; a freshly created key proves itself on the next signed call).

### Secrets at rest (per Delta's architecture guidance)

* Signing happens **only** in the Python backend; the React frontend never sees
  the API secret in any form — it talks to `/broker-connections`, `/live-*` and
  the terminal endpoints with its own JWT, and every secret-holding response
  returns `has_secret` / a masked key instead of secret material.
* `app/core/secrets.py` encrypts the `api_secret` at rest with **AES-256-GCM**
  (`enc:v1:` envelope) using `SECRETS_ENCRYPTION_KEY` from the environment
  (32-byte base64). Rows written before encryption pass through and are
  re-encrypted on the next save; decryption happens only in memory at signing
  time (`_live_client`, connection probe/test, `saved_credentials`, the CLI
  tools). A missing/rotated key fails loud (`SecretDecryptionError`) and live
  instances fail secure — they keep the credentials they started with.
* IP whitelisting is **per API key** (main and sub-account keys separately);
  the server's static egress IP must be on every key in use, and the probe
  runs from the trading box for exactly that reason.

* UI: Broker Settings → the connection → **Align to India production** (or
  **Align all to India production** on the Saved connections header).
* API: `POST /broker-connections/{id}/align {"environment":"INDIA_PRODUCTION"}`
  (one row) / `POST /broker-connections/align-delta` (every Delta-family row of
  the login). Both re-read account details and hand the change to running live
  instances — no restart.
* CLI (on the trading server, because a whitelisted key only validates from its
  egress IP): `backend/tools/align_delta_env.py --all-delta --apply --verify`.

## Software Need (live_trader.py + broker_account.py + tick_feed + data_sync + main.py)

- **Instrument warmup**: `get_instrument` → tick_size, contract_value, product_id. REST: GET /v2/products/{symbol}. Used once at startup + daily refresh.
- **Credential probe**: `get_account_balance` → GET /v2/wallet/balances (official key ping since GET /v2/profile blocked per changelog 19.08.26). Also trading_preferences as fallback.
- **Positions**: `get_positions` → GET /v2/positions/margined?product_ids= (or product_symbol when the product id is unavailable) + `get_position` → GET /v2/positions?product_id= (real-time). `parse_open_position` avoids stacking. `get_margined_positions` CSV max10 + short-option PnL patch (GH#9) kept for dashboard parity but software uses single BTC product.
- **Bracket entry**: `place_bracket_order` → POST /v2/orders/bracket with size/side/order_type/limit_price/bracket_stop_trigger_method/stop_loss_order/take_profit_order/client_order_id. Entry side BUY/SELL, qty in BTC converted via `base_to_venue_size`, price None=MARKET, SL/TP from strategy, trigger_method mark_price (risk on mark) / last_traded_price, trail_amount = ATR * trail_distance_atr (string). Also supports `place_order` with bracket_* top-level fields (REST entry-bracket via POST /v2/orders) — both forms valid per docs: CreateOrderRequest bracket_* and CreateBracketOrderRequest legs.
- **Position bracket**: `place_position_bracket` → POST /v2/orders/bracket without size/side (MCP use-case "Bracket an open position"): {product_id/product_symbol, stop_loss_order, take_profit_order, bracket_stop_trigger_method}. Size not needed as it closes entire position.
- **Reduce-only exits**: `place_order` reduce_only MARKET → POST /v2/orders {reduce_only:true}. Used for normal exits and close&reverse.
- **Protection-leg cancel**: `cancel_order` scoped to `protection_leg_ids` extracted via `split_order_response`. DELETE /v2/orders body {id, product_id} official, fallback path /v2/orders/{id} for mocks. Client-order-id via body {client_order_id, product_id} + fallback /v2/orders/client?client_order_id=.
- **Fills audit**: `get_fills` → GET /v2/fills?product_ids/product_symbol&page_size. Local audit via broker_fills.
- **DeadmanSwitch**: `create_heartbeat` POST /v2/heartbeat/create {heartbeat_id, impact, contract_types, product_symbols, underlying_assets, config:[{action:cancel_orders,unhealthy_count:1}]}, `send_heartbeat` POST /v2/heartbeat {heartbeat_id, ttl}, `get_heartbeats` GET /v2/heartbeat. TTL 0 disables.
- **Wallet**: `get_account_balance` balances for UI + risk. REST Wallet Balances.
- **Leverage/margin**: `set_leverage` POST /v2/products/{id}/orders/leverage {leverage} + fallback POST /v2/orders/leverage, `get_leverage` GET same, `change_position_margin` POST /v2/positions/change_margin {product_id, delta_margin}, `set_margin_mode` PUT /v2/users/margin_mode {margin_mode, subaccount_user_id} (account-level per docs) + legacy fallback, `get_account_settings` GET /v2/sub_accounts (lists each sub-account margin_mode) + leverage, `set_auto_topup` PUT /v2/positions/auto_topup.
- **MMP**: `update_mmp_config` PUT /v2/users/update_mmp, `reset_mmp` PUT /v2/users/reset_mmp, `get_mmp_config` via trading_preferences mmp_config.
- **Rate limits**: `rate_limit_usage` local limiter snapshot + exchange quota via GET /v2/rate_limits/quota (Delta 10k weight/5min). 429 Retry-After/X-RATE-LIMIT-RESET honoured.
- **Market data (public, no key)**: `fetch_klines` GET /v2/history/candles?symbol&resolution&start&end (start/end mandatory), `fetch_mark_price` GET /v2/tickers/{symbol} (mark+last+index), `fetch_mark_price_klines` via DataSyncService, plus MCP-aligned: `get_ticker`, `list_tickers`, `get_product`, `list_products`, `get_orderbook` L2, `get_recent_trades`, `get_candles`, `get_mark_price_history`, `get_oi_history`, `get_funding_history`, `get_reference_data` assets, `get_products`. Options chain `get_options_chain`, `get_indices`, `get_settlement_prices` kept for dashboard parity but NOT required for BTC perp live — pruned conceptually.

## REST ApiSection Groups Mapped to Software

- **Products**: get/list/tickers — instrument warmup + mark price.
- **Orders**: Place, Cancel, Edit, Active, Bracket, Edit Bracket, Cancel All, Batch Create/Edit/Delete, Get by id/client oid, Change/Get leverage — all mapped; batch not needed for live loop but useful for terminal & MCP parity, with dry_run.
- **Positions**: margined, position, Auto Topup, Add/Remove margin, Close All — mapped.
- **TradeHistory**: fills — audit.
- **Orderbook**: L2 — pre-trade research (optional dashboard).
- **Trades**: public — pre-trade.
- **Wallet**: Balances/Transactions/Download — balance needed, transactions optional ledger.
- **Stats**: volume — optional.
- **MMP**: Update/Reset — risk protection.
- **Account**: preferences/subaccounts/margin mode/rate limit quota — margin mode + leverage.
- **Heartbeat**: Create/Send/Get — deadman.
- **Settlement**: Prices — optional, not needed for perp.
- **Candles**: history — data_sync.

## MCP Use-Cases Mapped to Software

- **Daily check-in**: get_positions/get_margined_positions/get_ticker → LiveTerminal snapshot (risk + PnL + exposure).
- **Funding & basis monitor**: get_ticker/get_funding_history/get_indices → optional dashboard; not blocking live.
- **Pre-trade research**: get_orderbook/get_oi_history/get_recent_trades/get_candles → terminal + data_sync; candles already via fetch_klines.
- **Risk & liquidation scan**: get_margined_positions/get_positions/get_product_leverage → risk panel + leverage.
- **PnL & tax**: get_fills/get_order_history/bulk_fills_export with 90-day notice — fills needed for audit; bulk export writes CSV inside cwd/home with notice.
- **Wallet ledger**: get_wallet_transactions/get_wallet_balances with 90-day notice — balance needed.
- **Trading opt-in**: place_order/place_bracket_order/cancel_all_orders/set_leverage/change_position_margin/close_all_positions with dry_run first — MCP pattern implemented via dry_run flag that echoes method/path/body without sending.

## Signing (per REST doc + delta-rest-client PyPI 1.0.14)

- `signature_data = METHOD + timestamp + path + query_string + body_string`
- `query_string` = '' or '?k=v&...' sorted, quote_plus-encoded, leading '?' included (previous bug missed '?' causing Signature Mismatch on any call with query).
- `body_string` = '' when body None else compact JSON separators (',', ':').
- Headers: api-key, timestamp, signature, Content-Type: application/json, User-Agent: PHANTOM-Trading-Tool/1.0 (required on every Delta request).

## Bracket Payload — Both Forms Supported

- **Entry bracket via POST /v2/orders** (CreateOrderRequest): {product_id/product_symbol, size, side, order_type, limit_price?, bracket_stop_loss_price, bracket_take_profit_price, bracket_trail_amount, bracket_stop_trigger_method, ...}
- **Entry bracket via POST /v2/orders/bracket** (our live_trader): {product_id/product_symbol, size, side, order_type, limit_price?, bracket_stop_trigger_method, stop_loss_order:{order_type, stop_price, trail_amount}, take_profit_order:{order_type, stop_price}, client_order_id}
- **Position bracket via POST /v2/orders/bracket** (MCP): {product_id/product_symbol, stop_loss_order, take_profit_order, bracket_stop_trigger_method} — no size/side, closes entire position.

## Error Handling (added 2026-08-31)

### Server-Side
- **Central module** `app/core/error_handling.py`: `PhantomError` hierarchy, `error_response` envelope `{error, detail (legacy compat), code, timestamp, details, hint}`, validators (`validate_leverage 1-125`, `validate_size >0 per Delta 15.04.26`, `validate_price >0`, `validate_symbol`, `validate_broker_code`, `validate_margin_mode`, `validate_order_type`, `validate_side`), `map_db_error` (UNIQUE → 409 Conflict, FK → 400, NOT NULL → 400), `classify_broker_error` (auth/rate_limit/margin/size/price with hints), global FastAPI handlers + request logging middleware.
- **Live endpoints**: `POST /live-account/orders` validates broker/symbol/side/order_type/size/price/stop_loss/take_profit, `POST /live-account/leverage` validates leverage/symbol/broker, `POST /live-account/margin-mode` validates margin_mode/symbol/broker, logs broker error category on failure.
- **Broker connections**: `POST /broker-connections` validates broker_code, requires key/secret, maps `IntegrityError` via `map_db_error` to 409 envelope, `SQLAlchemyError` to 500.
- **Broker client** `broker_client.py`: `AUTH_REJECTION_MARKERS`, `AUTH_LATCH_STRIKES=2`, backoff 5s→300s, `_throttled_request` with RateLimiter + 429 Retry-After, `_json_body` returns `{error}`, `credential_health`, `signed_calls_held`.

### Client-Side
- **api.js**: request interceptor adds Bearer + `X-Request-ID`, response interceptor handles 401 (dispatch `auth:expired`, clear storage, redirect after 800ms), 429 (dispatch `api:rate-limited` with Retry-After), 403 (dispatch `api:forbidden`), 5xx logs to console.
- **Toast system**: `hooks/useToast.js` (`addToast`, `toastFromError` parsing new envelope + legacy `detail` + network), `components/ToastContainer.jsx` (bottom-right, code/hint/details), `main.jsx` GlobalToastListener for interceptor events.
- **ErrorBoundary**: `components/ErrorBoundary.jsx` class component, `getDerivedStateFromError`, `componentDidCatch`, red card UI with Try Again + Reload, wraps entire app and TradingPage.
- **TradingPage**: removed `mockTrades`, wired real `GET /paper-trade/status` and `/live-trade/status` + `/strategies` + `/paper-trade/history`, aggregated trades, account summary, `useToast` + `ToastContainer`, loading states, empty states, no mock data.

### DB-Level
- **Constraints**: `uq_user_broker_label`, `uq_fee_broker_mode`, `uq_market_data_seed_progress_range`, `uq_broker_fill_trade`, `username UNIQUE`, `code UNIQUE` (case-insensitive check in API).
- **Indexes**: composite `ix_source_symbol_interval_time` for klines, `ix_market_ticks_source_symbol_time`, per-table `user_id`, `broker_code`, `symbol`.
- **Orphans**: FK checks via `scripts/db_integrity_check.py` (connections, strategies, runs, trades, sessions, orders, fills), misconfigured active connections with no key.
- **Seed durability**: `MarketDataSeedProgress` cursor advanced in same transaction as candles, resumable, stuck >2h flagged, zero progress >30min flagged.
- **Klines repair**: `repair_klines` removes duplicate timestamps (legacy batch) + off-grid timestamps (legacy CSV), `data_health()` reports `duplicate_rows` + `misaligned_rows`, admin endpoints `POST /admin/market-data/repair` and `seed?repair=true`.
- **Integrity script**: `backend/scripts/db_integrity_check.py` — checks unique, orphans, indexes, seed progress, klines health, NOT NULL; `--fix` auto-fixes stuck seeds + deactivates bad connections; exit 0 clean / 1 issues / 2 error.

## Test

- Backend 16 suites, 1022 PASS 0 FAIL (verified 2026-08-31): `test_live_account.py` 166, `test_broker_connections.py` 40, `test_api_e2e.py` 47, etc. Includes 429 retry, auth latch, rate limits, normalized schema, account snapshot, credential probe.
- Frontend 7 suites, 345 PASS 0 FAIL (verified 2026-08-31): `broker_keys_ui`, `terminal_ui`, `pages_smoke`, `trade_log_ui`, `trading_windows_ui`, `admin_seed_ui`, `chart_overlay` — all components resolve imports, no mock trades.
- `ERROR_HANDLING.md` documents server/client/DB error handling, testing checklist, future improvements.

## Key Provided

- The original report's API key is deliberately **not** stored in this repo — per Delta's own
  security guidance (never hardcode API secrets in shared code, keep them out of chat/repos). The
  sandbox egress blocks the live probe (SSL_ERROR_SYSCALL/EOF), but local mock tests pass and
  signing matches the official client; a key that appeared in git history should be treated as
  exposed and **rotated** in API Management.
- **Delta runs four separate key stores** (India production/testnet, Global production/testnet); the
  live report showed the connection on India testnet with `invalid_api_key`, which is the *same*
  answer for: an India production key on testnet, a Global key on India, a half-pasted key, or a
  rotated key. `delta_key_probe` now signs all four hosts, `connection_test.run_connection_test` +
  `tools/test_connection.py --apply` repoint the saved connection at the environment that accepts
  it (no restart), and **DeltaGlobal** is a built-in broker with its own hosts so a Global key has a
  first-class adapter. Run the check **on the trading server** (a whitelisted key 401s from any
  other egress IP), then apply the verdict there.
- **Deterministic alignment (2026-08-31).** Detection needs a working key; alignment does not.
  `BrokerClient.delta_environment()` resolves the four canonical environment names,
  `POST /broker-connections/{id}/align` + `/align-delta` apply a named environment (broker code +
  testnet flag) with no probe, `tools/align_delta_env.py` does the same from the shell with
  `--apply --verify`, and Broker Settings exposes **Align to India production** on every Delta-family
  connection that is not there yet.
- **Delta integration scripts conformance (2026-08-31).** Audited against Delta's four reference
  scripts + endpoint/WS tables. Already conforming: order strings `market_order`/`limit_order`,
  `DELETE /v2/orders` `{id, product_id}`, `DELETE /v2/orders/all` with product filter, public
  candles chunked at 2000, public WS on `wss://public-socket.india.delta.exchange` with new channel
  names, private-WS key-auth `HMAC("GET"+ts+"/live")`. Closed gaps: `BrokerClient.sync_margin_mode`
  (reference→target mirror via `GET /v2/sub_accounts` + `PUT /v2/users/margin_mode`),
  `POST /live-account/margin-mode-sync` + `subaccount_user_id` on `POST /live-account/margin-mode`,
  and `delta_private_subscribe` now covers orders/positions/margins (fills stay on REST
  `GET /v2/fills`, deliberately not duplicated).
- **Deployment rail (2026-08-31).** `DELTA_DEPLOYMENT_FAMILY` (default `india`) enforces the
  official rule — India keys → `api.india.delta.exchange` only, Demo keys →
  `cdn-ind.testnet.deltaex.org` only, `api.delta.exchange` = Global, not used here. On an India box:
  creating/switch-aligning onto DeltaGlobal is a 400 carrying the rule
  (`BrokerClient.DELTA_FAMILY_RULE`), the CLI refuses Global targets, and Broker Settings shows the
  rule on Global/testnet rows + the Add-connection form. The read-only four-host probe still
  *reports* Global keys (so the operator re-creates the key on India) without ever trading there.
- **Secrets encrypted at rest (2026-08-31).** AES-256-GCM via `app/core/secrets.py` +
  `SECRETS_ENCRYPTION_KEY`; `GET /broker-settings` no longer returns even the masked secret
  (`has_secret` instead); decrypt-only-at-signing across `_live_client`, probe/test endpoints,
  `saved_credentials` and the CLI tools; `cryptography` added to requirements. Tests:
  `test_secret_encryption.py` (22 checks, offline) + full backend/frontend sweep green.
  Tests: `test_delta_env_align.py` (41 checks, offline) + `broker_keys_ui` align/rule checks; the
  auth verdict in `broker_account.account_snapshot` names Check key / Test connection / Align as
  the three fix paths.
