# Delta BTCUSD guardrails — audit of the external review against this repo

Audited 2026-09-01. Two sources of truth were used:

* **the live venue** — public `GET https://api.india.delta.exchange/v2/products/BTCUSD`
  (fetched during this audit; every product number quoted below is from that response), and
* **this codebase** — every row cites `file:line`, so a claim can be checked rather than argued.

The review under audit is the "comprehensive set of precautions for strategy-based BTCUSD
trading on Delta Exchange" (10 general sections + a 3-tier backtest/paper/live addendum).

## Bottom line

**21 of its ~40 precautions are already enforced here, 8 are partial, 6 are genuinely missing,
and 4 are wrong or misleading for this stack.** Its contract facts are all correct — the live
product spec confirms every number in its table.

The two findings worth engineering time now, in order:

| # | Finding | Why it costs money |
| :-- | :--- | :--- |
| **P0** | Automated orders are sent **without an idempotency key, and POSTs are retried on 5xx** | A gateway 502 after the matching engine accepted the order = a second real entry/bracket on the account |
| **P1** | Prices are **never aligned to `tick_size` (0.5)** before sending | Every limit entry and every bracket SL/TP trigger carries an arbitrary float from ATR maths; a non-multiple is rejected by the venue, and the app's own classifier blames `symbol, side, and order type` |

Everything else is accuracy (fees, funding, venue caps) or hardening (IPv4, shared WS feed,
`system_status`), useful but not bleeding.

## Fact-check of the review's claims

Confirmed against the live spec: `contract_value 0.001`, `tick_size 0.5`, `default_leverage 200`,
`initial_margin 0.5`, `maintenance_margin 0.25`, `taker_commission_rate 0.0005`,
`maker_commission_rate 0.0002`, `position_size_limit 125000`, `price_band 5`,
`product_specs.expiry_interval 28800`, `product_specs.only_reduce_only_orders_allowed false`,
`trading_status "operational"`. **All correct, including the two fields it invented nothing for** —
`only_reduce_only_orders_allowed` and the `disrupted_*` trading statuses do exist on this product.

Four corrections, because acting on them as written would be wrong:

1. **"leverage is not configurable via API … must be set via the leverage endpoint/UI beforehand"** —
   self-contradictory. Delta's leverage endpoint *is* an API call,
   `POST /v2/products/{product_id}/orders/leverage`, and this repo already implements it
   (`broker_client.py:2100-2140`, with the legacy `POST /v2/orders/leverage` fallback). What is true:
   leverage is not a field on the order payload. The real gap in this repo is the opposite one —
   nothing *calls* it at start (see §2).
2. **"10,000 weight per 5-minute rolling window"** — Delta's window is **fixed**, not rolling
   ("quota resets to full every 5 mins"). This matters for backoff design: with a fixed window you can
   burn 10k in 30 seconds and be dark for the remaining 4.5 minutes, so a retry schedule must be
   bounded by `X-RATE-LIMIT-RESET` (it is: `rate_limit.py` + `_throttled_request`), not by a sliding budget.
3. **"margin mode comes from `trading_preferences`"** (its earlier note, and the reason it predicted
   `margin mode unknown` would self-heal) — for Delta India the margin mode lives on the
   **(sub)account**: `GET /v2/sub_accounts` → `margin_mode`, which is what `get_account_settings`
   reads (`broker_client.py:2217-2240`) and what `sync_margin_mode` writes
   (`PUT /v2/users/margin_mode` in `set_margin_mode`, `broker_client.py:2271-2300`).
4. **"confirm paper-trade market-data pulls don't share the live key's quota"** — they don't, already:
   the limiter is keyed by broker + api-key fingerprint (`broker_client.py:318`) and the paper worker
   builds its market-data client **without credentials** (`paper_trader.py:247`, `:567`). Their wider
   point stands for *authenticated* public reads, which this app deliberately leaves unsigned too, so
   those calls are IP-throttled venue-side while still spending local weight (conservative by design).

## Audit table

Legend: ✅ enforced in code · 🟡 partial · ❌ absent · ⚠️ review claim wrong/misleading.

### 1. Contract specifics

| Precaution | Status | Evidence |
| :--- | :--- | :--- |
| Never hardcode; fetch `GET /v2/products/{symbol}` | ✅ | `get_instrument` (`broker_client.py:847-905`), cached per client, refreshed at worker warm-up (`live_trader.py:744`) and on demand (`refresh=True`) |
| Use `contract_value` for BTC ↔ lots | ✅ | `base_to_venue_size` / `venue_to_base_size` (`broker_client.py:1052-1071`) + `normalize_*` in `broker_account.py:101/238/304`; admin override columns exist (`models.py:94-95`) |
| Sizes in whole contracts / min lot | ✅ | `step_size = 1.0` for Delta, `int(round(size))` on the body (`:1178`, `:1283`), sub-minimum entries refused as `LOT_TOO_SMALL` (`engine.py:532`) |
| Round prices to `tick_size` | ❌ | No rounding helper anywhere; `_delta_limit_price` is `str(price)` (`:458`), bracket legs `str(stop_loss_price)` (`:1300`, `:1306`) — **P1** |
| Use the venue's real fee rates | 🟡 | A per-broker/per-mode `FeeSetting` table exists, but all three brokers are **seeded from `.env` Binance numbers** (`models.py:500-506`: 5.9/2.36 bps vs the venue's 5.0/2.0 bps) and `taker/maker_commission_rate` from the product spec — already fetched — is never read |
| Respect `position_size_limit` / notional caps | ❌ | `validate_size` allows 1,000 BTC (`error_handling.py:131`) and 10,000 from the terminal (`main.py:3538`); the venue caps one position at 125,000 lots = 125 BTC (`position_notional_limit` 5,000,000 USD, `max_leverage_notional` 100,000 — the review omitted those two) |
| Respect `price_band` (5%) | ❌ | Not pre-checked; rejections land in the generic `order` category with a misleading hint (`error_handling.py:245-253`) |

### 2. Leverage & margin

| Precaution | Status | Evidence |
| :--- | :--- | :--- |
| Never rely on the venue's default leverage | 🟡 | The worker never *sets* or *verifies* leverage. Entry sizing is `margin × config.leverage` (`live_trader.py:890`) and `config.leverage` is bounded 1-125 (`strategy.py:126`) — so a local 7x sizing model can sit on an account the venue has at 200x (the default for BTCUSD, and what this user's connection reports). `POST /live-account/leverage` exists but is manual (`main.py:3638`) |
| Enforce isolated vs portfolio deliberately | 🟡 | Read at connection level and re-read on save/rotate (`_fetch_connection_settings`, `main.py:859-879`), syncable between sub-accounts (`sync_margin_mode`), but not compared to the venue when an instance starts |
| Watch `liquidation_price` with a buffer | 🟡 | Normalized into the account snapshot (`broker_account.py:250/288`) and displayed (`LiveTerminal.jsx:547`); no threshold, no alert, no entry gate |
| Track `blocked_margin` / `available_balance` | 🟡 | `normalize_balance` + `portfolio_risk` (`broker_account.py:355-460`) feed the terminal; an entry is not refused when available margin is short — the venue's rejection is the check |

### 3. Order execution safety

| Precaution | Status | Evidence |
| :--- | :--- | :--- |
| `reduce_only` on every close | ✅ | Live exits (`live_trader.py:1019-1021`, `1127-1129`), Binance protection legs (`broker_client.py:1332/1336`), plus a `_is_nothing_to_reduce` path that settles the local book instead of retrying |
| Market vs limit trade-off handled explicitly | ✅ | Entries/exits are market; the bracket keeps risk exchange-side by default (`bracket_orders=True`, `live_trader.py:240`) |
| `unfilled_size` / partial fills | 🟡 | Normalized for display (`broker_account.py:108/190`) but the worker's book assumes a full fill (`extract_fill_price`, `live_trader.py:922`). Harmless while entries are market orders; wrong the day a limit entry is added |
| Validate price/size client-side | 🟡 | `validate_price` only rejects ≤ 0 (`error_handling.py:142-151`, matching Delta changelog 15.04.26); no tick, band or cap validation |
| Check `trading_status` / `only_reduce_only_orders_allowed` before placing | ❌ | Neither field is read anywhere, though both are on the live spec |
| Correct classification of venue rejections | 🟡 | `classify_broker_error` covers auth/rate_limit/margin/size/price/order (`error_handling.py:236-262`), but `ERROR_HANDLING.md:60-68` documents keys (`code`, `retryable`) and category names (`insufficient_margin`, `invalid_size`, `invalid_price`) that the function does not return — doc drift, and no `tick`/`band` markers |

### 4. Deadman switch — the review's "critical" item

✅ **Implemented, and stricter than the recommendation**:
`POST /v2/heartbeat/create` + ack loop with **25 s cadence / 30 s TTL / `unhealthy_count: 1` /
`cancel_orders`** (`heartbeat.py:30-33`), `ttl=0` on graceful stop so a planned restart is not a crash
(`disable_heartbeat`), failures surfaced as `HEARTBEAT FAIL` instead of silently leaving orders
unprotected, and **defaulted ON for every Delta live instance** (`main.py:2617-2619`).
Combined with `bracket_orders=True`, a worker dying mid-trade leaves the position protected
exchange-side. Residual gap: an operator turning `bracket_orders` off disables the only protection
that outlives the process — worth a loud warning in that combination, not a code change.

### 5. Rate limits

| Precaution | Status | Evidence |
| :--- | :--- | :--- |
| Local budget + 429 backoff + `X-RATE-LIMIT-RESET` | ✅ | `RateLimitConfig` (rps, rpm, `weight_per_5min=10000`, orders/min, orders/10s, `max_retries=4`, `backoff_base`, `acquire_timeout`, `safe_ratio`) in `rate_limit.py:44-70`; `Retry-After`/`X-RATE-LIMIT-RESET` honoured in `_throttled_request:544-560` |
| Read `/v2/rate_limits/quota` | ✅ | `fetch_rate_limit_quota` → `limiter.note_quota` (`broker_client.py:2622-2634`), exposed via `GET /live-account/rate-limits` |
| Per-endpoint weights | 🟡 | Conservative where it counts (open orders/balances 5 vs the docs' 3, place 10 vs 5, bracket 20, batch 25) and slightly light for history/fills (5 vs 10) — inert at ~1 call/min/instance |
| 500 operations/s per product | 🟡 | Per-key order budgets exist (Binance-shaped 1200/min + 300/10s); no per-product op counter. Only reachable through the batch endpoints |
| Prefer WS over polling | 🟡 | Public `ticker` + `candlestick_1h`, private `orders`/`positions`/`margins` (`tick_feed.py:182-213`); fills are still REST-polled by design |
| Share one WS connection across strategies | ❌ | `build_tick_feed` gives each instance its own feed (`tick_feed.py:469+`). Bounded reconnect (exp backoff capped at 30 s, `:372-404`) keeps this far from 150 conns/IP/5min at 3-5 instances; a shared per-(venue,symbol) registry is the right move if instance count grows |

### 6. Funding — ❌ absent

`get_funding_history` / `GET /v2/history/funding` exist (`broker_client.py:991`) and
`mark_price.py` documents that the mark price already *embeds* the funding basis, but **no tier
charges funding**: `grep funding` finds nothing in `engine.py`, `paper_trader.py`, `live_trader.py`.
With `annualized_funding: 10.95` and `product_specs.expiry_interval: 28800` on BTCUSD, a
multi-hour-holding strategy at 7x is paying roughly a fifth of its margin per year that the backtest
never sees. Accuracy issue for the paper/backtest tiers, not a safety one — but it is the single
biggest remaining source of "backtest ≠ live".

### 7. Connectivity & signing

| Precaution | Status | Evidence |
| :--- | :--- | :--- |
| Timestamp generated immediately before sending | ✅ | Better: a **fresh** timestamp+signature per attempt, so a 429 retry can't go stale (`_delta_headers:648-660` + `refresh_headers` in `_throttled_request:499-536`) |
| Signed string == sent request, byte for byte | ✅ | One deterministic query serialization is both signed and passed to `requests` (`_delta_request:663-700`, with the comment warning about the failure mode) |
| `product_ids` vs `product_id` discipline | ✅ | Explicitly encoded in the callers with comments (`get_open_orders:1670-1692`, `get_order_history:1698-1731`, `get_positions:1967-1993`) |
| Never blindly retry order placement without idempotency | ❌ | **P0.** `_throttled_request` retries every method on 429 **and 5xx** (`:544-560`). Terminal orders get `client_order_id = f"ph-{uuid4}"` **once per request** (`main.py:3554`), so a retry replays the same body and the venue's unique-id rule refuses it — protection by accident, not design. `LiveTradeService` passes **no** `client_order_id` on its entry/bracket/exit orders (`live_trader.py:905`, `:1019`, `:1127`) → a 5xx after the engine accepted the order means a duplicate live order |
| Force IPv4 (or whitelist both families) | ❌ | No socket-family pinning anywhere in `backend/`. With an IP-whitelisted key on a dual-stack egress, AAAA-first resolution intermittently 401s (`ip_not_whitelisted_for_api_key`) in a way that looks like a dead key — the exact class of confusing failure the connection battery exists to end |

### 8. System-level risk controls

| Precaution | Status | Evidence |
| :--- | :--- | :--- |
| Own circuit breakers (daily loss, size, open orders) | 🟡 | `dd_soft_pct` / `dd_halt_pct` / `dd_resume_pct` are configured on the **shared** config (`strategy.py:148-150`) and enforced **only in the backtest engine** (`engine.py:461-470`) — the live and paper workers never read them, so the tier holding real money has the loosest rules. No daily-loss cap at all. Position stacking *is* prevented: one-order-per-signal-candle, one-position-per-instance, venue-position-aware, credential-held (`live_trader.py:1051-1108`, covered by `test_live_entry_guard.py`) |
| React to `system_status` / degraded mode | ❌ | Channel not subscribed. Nearest equivalents: configurable blackout windows enforced in paper *and* live (`core/trading_windows.py`; `live_trader.py:835-841`) and the auth latch that holds entries while the key is rejected |
| Log every order / fill / cancel | ✅ | `record_order`, `record_fills`, `mark_order_cancelled`, local audit trail + fills CSV incl. the kudos export (`broker_account.py:752-1011`) |
| Testnet rehearsal before live | 🟡 | Per-connection `is_testnet` with family-correct hosts (`broker_client.py:124-135`) and the probe/battery telling you when the two are mismatched — but nothing *requires* a paper or testnet run before a live instance starts |

### 9. Sub-accounts / multi-client

| Precaution | Status | Evidence |
| :--- | :--- | :--- |
| Verify margin mode per sub-account before each run | 🟡 | `get_account_settings` + `sync_margin_mode` (parent-key-only listing enforced with an explicit error, `broker_client.py:2310-2360`); not part of instance start |
| Per-sub-account IP whitelisting | ❌ (unknowable in code) | Best available: the probe reports `ip_not_whitelisted_for_api_key` per host and says "run from the trading server" (`AUTH_REJECTION_MARKERS`, `delta_key_probe.verdict`) |
| Segregate live / paper / backtest credentials | ✅ | Already the review's "hard architectural guarantee": paper constructs its market-data client with **no credentials**, so a paper bug cannot sign an order request (`paper_trader.py:247`, `:567`); mode is a separate service class and endpoint, never inferred; `FeeSetting` is keyed by `(broker, mode)` so the three tiers price independently. Live start is a deliberate, separate action — nothing promotes a paper run — and the DB audit rows carry their source |

### 10. Compliance / operational

Not code, and correctly raised: **2FA on the account that mints a Trading-permission key**, and an
explicit risk statement for whoever's money this trades (200x default on a USD-margined BTC perp).
`docs/delta_api_key_runbook.md` is the right home for both; it currently documents keys, environments
and failure triage, not leverage or fee reality.

## Addendum: the three-tier (backtest / paper / live) recommendations

| Recommendation | Status | Evidence / note |
| :--- | :--- | :--- |
| Simulate fills against real book depth (`ob_l2`) | ❌ | Paper fills off `basis_price` — mark price when available, else last (`mark_price.py:104-107`, `paper_trader.py:297-313`). No spread, no depth, no queue position. The critique is fair; the fix is a research project. Cheaper and honest: label the paper/backtest fills in the UI as *mark-price-simulated* |
| Model fees explicitly | ✅ | Taker/maker bps on the shared config, applied in engine maths (`strategy.py:129-130`, `engine.py:88-95`, `_fee_config` in `main.py:258-266`) — but see the Delta fee-number mismatch in §1 |
| No slippage model | ❌ | `grep -rn slippage backend/app` → nothing. Every simulated fill is at the basis price |
| Partial fills in paper | ❌ | Paper is all-or-nothing at the trigger price |
| Same tick/lot rounding in paper as live | 🟡 | Lot rounding is shared (both go through the same `oms.create_order` sizing); tick rounding exists in **neither** tier |
| Apply funding to simulated positions | ❌ | §6 |
| Backtest data ≡ live data shape | ✅ | One candle frame (`time, open, high, low, close, volume`) from `fetch_klines` for seeds and live alike; mark-price candles share the same normalizer (`mark_price.py`, `data_sync.py:1223` column check) |
| Latency difference flagged | 🟡 | Backtest fills at the next candle open (no latency, no queue); live/paper act on a closed-candle tick every 60 s with `fast_tick` exits. The difference is real but documented in prose only — no UI caveat |
| Seed completeness / gaps | ✅ | `repair_klines` re-fetches missing intervals and repairs duplicates (`data_sync.py:596+`, tested in `test_seed_repair.py`) |
| Enforce live risk rules in paper too | 🟡 | Windows, cooldown, one-position and reverse-then-enter are shared; drawdown throttle and venue caps are not |
| Mode as a first-class parameter, logged per order | ✅ | `source`/`mode` on every order row and `_record_order(..., leg=…)`; audit trail queryable per broker |
| `client_order_id` prefix per mode | 🟡 | `ph-` prefix exists for terminal orders (`main.py:3554`) but is not mode-tagged and not used by the live worker at all — see P0 |
| Paper needs only Read Data | ✅ (and better) | Paper needs nothing: it is unauthenticated |
| Session health for paper, urgency tiering for live | ✅ | Live: heartbeat state, credential state, `entry_paused`, `last_order_error`; paper: `_log` + persisted history + stale-quote guard (`max_age`, `tick_feed.py:472`) |

## What I would change, in order

**P0 — make every automated order idempotent, and stop blind POST retries.**
1. Default `client_order_id` inside the order builders (`place_order`, `place_bracket_order`,
   `place_position_bracket`, `batch_create_orders`) when the caller omits it, derived from *intent*
   rather than from a fresh random uuid per attempt: `f"{strategy}-{sha1(symbol|side|type|size|signal_bar_iso)[:18]}"`,
   truncated to the venue's 32 chars. The body is built once per logical order today, so a retry
   replays the same key and Delta refuses the duplicate.
2. Do not retry non-idempotent methods on **5xx** when no key is present (429 retries stay safe: the
   engine never saw that request). One guard in `_throttled_request` covers both venues.
3. Test: a 500-then-200 sequence must produce one accepted order; two different signal bars must
   produce two different ids.

**P1 — align prices, and read the product spec you already fetched.**
Extend `get_instrument` to keep `price_band`, `taker_commission_rate`, `maker_commission_rate`,
`position_size_limit`, `position_notional_limit`, `initial_margin`, `maintenance_margin`,
`trading_status`, `product_specs.*`; add one `align_price(price, tick, side, band)` used by both
venues' order builders (round *against* the trader: down for buys, up for sells — and clamp bracket
stops inside `price_band`); pre-flight refuse when `trading_status != "operational"` or when
`only_reduce_only_orders_allowed` and the order is not reduce-only; extend the `price` category
markers with `tick`, `multiple`, `band`, and reconcile `ERROR_HANDLING.md` with what
`classify_broker_error` actually returns.

**P2 — make the three tiers share one risk layer, and let the venue set its own fees.**
Lift the dd-soft/dd-halt/dd-resume state machine out of `BacktestEngine` into a small `RiskThrottle`
used by paper and live (both already record equity points), add `max_daily_loss_pct`, and seed each
broker's `FeeSetting` from the venue's published rates instead of a Binance `.env` default — for
Delta that is 5.0/2.0 bps vs today's 5.9/2.36, i.e. every Delta backtest over-charges taker fees by
18% before the strategy has even traded.

**P2 — funding and liquidation.**
Charge funding on the 8 h boundary in paper (live feed already available) and from history in
backtest; turn `liquidation_price` from a displayed number into a rule — refuse an entry whose
distance to liquidation is inside a multiple of the strategy's own stop distance.

**P3 — hardening.**
`DELTA_FORCE_IPV4` (or an explicit dual-stack whitelist requirement in the runbook); a shared
per-(venue, symbol) WS registry before instance count grows; a per-product operations/s counter if the
batch endpoints ever get wired into a loop; a loud warning when heartbeat is on and `bracket_orders`
is off.

## Deliberate non-changes

* **"Use limit entries to avoid slippage."** Entries fill on the next candle open by design; a limit
  that doesn't fill is a *different strategy*, not a safer one.
* **"Simulate against `ob_l2` depth with queue modelling."** Not a guardrail, a market-microstructure
  simulator. Until it exists, the tiers are labelled by their fill basis (mark vs traded price) in the
  overlay and in `_record_closed`, which is where the user's expectations are actually set.
* **Credential-less paper trading** is already stronger than the review's "Read Data only" advice —
  don't add keys to the paper path to "improve parity".
* **`bracket_orders` defaulting to on** plus the deadman switch is the answer to its §4; no separate
  position-closing heartbeat action should be invented beyond what the venue documents.
