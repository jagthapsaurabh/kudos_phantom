# Research: live order management, terminal & broker rate limits

How PHANTOM's live-trading layer was designed, what each venue actually allows, and why the code
looks the way it does. Written while building commit `8285fa3` (order lifecycle, `/terminal`, rate
limiting) — every claim below is either quoted from the venue docs (cited) or observed in the code
that shipped.

Scope: **Binance USDS-M Futures** (`fapi`) and **Delta Exchange** (`/v2`), BTC **perpetual** only.

---

## 1. The questions

1. What does a full order lifecycle need — place, edit, cancel, cancel-all, open orders, history,
   fills, positions, margin?
2. Does either venue support **bracket** orders (entry + stop-loss + take-profit in one request)?
3. How is an order **sized** on each venue, and how do we convert a strategy's BTC size safely?
4. Which price **triggers** a stop, and how do we keep that consistent with mark-price pricing?
5. What are the real **rate limits**? (The brief assumed "20 requests per second".)
6. What does a Delta-Exchange-style terminal need to show, and where does each number come from?

---

## 2. Venue capability matrix

| Capability | Binance USDS-M (`/fapi/v1`) | Delta Exchange (`/v2`) |
| :--- | :--- | :--- |
| Place order | `POST /fapi/v1/order` | `POST /v2/orders` |
| Edit order | ❌ none — cancel + replace | ✅ `PUT /v2/orders` ([docs](https://docs.delta.exchange/)) |
| Cancel one | `DELETE /fapi/v1/order` (by `orderId` or `origClientOrderId`) | `DELETE /v2/orders/{id}` or `/v2/orders/client?client_order_id=` |
| Cancel all | `DELETE /fapi/v1/allOpenOrders` | `DELETE /v2/orders/all` (also `cancel_orders_accepted` on a new order) |
| Open orders | `GET /fapi/v1/openOrders` | `GET /v2/orders` |
| Order history | `GET /fapi/v1/allOrders` | `GET /v2/orders/history` |
| Fills | `GET /fapi/v1/userTrades` | `GET /v2/fills` |
| Positions | `GET /fapi/v2/positionRisk` | `GET /v2/positions/margined` |
| Close position | `closePosition=true` (or a sized reduce-only market order) | `POST /v2/positions/close_all` |
| Position margin | `POST /fapi/v1/positionMargin` (`type` 1 = add, 2 = remove) | `POST /v2/positions/change_margin` |
| Wallet | `GET /fapi/v2/account` | `GET /v2/wallet/balances` |
| Leverage | `POST /fapi/v1/leverage` | `POST /v2/orders/leverage` |
| Margin mode | `POST /fapi/v1/marginType` (`ISOLATED`/`CROSSED`) | `POST /v2/positions/margin_mode` |
| **Bracket order** | ❌ no native endpoint — must be emulated | ✅ `POST /v2/orders/bracket`, edited with `PUT /v2/orders/bracket` |
| Rate-limit feedback | `X-MBX-USED-WEIGHT-1M` header | `GET /v2/rate_limits/quota` + `X-RATE-LIMIT-RESET` on 429 |

### Order-type mapping

The terminal speaks one vocabulary and the adapters translate it:

| Terminal | Binance `type` | Delta (`order_type` + `stop_order_type`) |
| :--- | :--- | :--- |
| `market` | `MARKET` | `market_order` |
| `limit` | `LIMIT` (+ `timeInForce`) | `limit_order` + `limit_price` |
| `stop_market` | `STOP_MARKET` | `market_order` + `stop_loss_order` |
| `stop_limit` | `STOP` | `limit_order` + `stop_loss_order` |
| `take_profit_market` | `TAKE_PROFIT_MARKET` | `market_order` + `take_profit_order` |
| `take_profit_limit` | `TAKE_PROFIT` | `limit_order` + `take_profit_order` |
| `trailing_stop` | `TRAILING_STOP_MARKET` (`callbackRate` + `activationPrice`) | `trail_amount` |

Binance's trade endpoint documents the order types and their companion fields — `stopPrice`,
`workingType`, `priceProtect`, `reduceOnly`, `closePosition`, `activationPrice` / `callbackRate` for
trailing stops, and `newClientOrderId` matching `^[\.A-Z\:/a-z0-9_-]{1,36}$`
([Binance New Order](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Order-Test)).

### Stop trigger: the detail that matters most

* Binance: `workingType` is **`MARK_PRICE`** or **`CONTRACT_PRICE`**, default `CONTRACT_PRICE`
  ([same source](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Order-Test)).
  `priceProtect=TRUE` refuses the trigger if mark and contract price diverge too far.
* Delta: `stop_trigger_method` is **`mark_price`**, `last_traded_price` or `spot_price`; bracket
  orders use `bracket_stop_trigger_method` with the same three values
  ([Delta schemas](https://docs.delta.exchange/)).

Because the whole app prices risk on the **mark price** of the BTC perpetual, the adapters force the
mark-price trigger on every protective order, and `MARK` / `CONTRACT` / `LAST_TRADED_PRICE` are
accepted as aliases and mapped to the documented enum (`BrokerClient.BINANCE_WORKING_TYPES`).

### Signing

* Binance: `HMAC-SHA256` over the sorted query string (`timestamp` + `recvWindow` included), sent as
  `signature`, key in `X-MBX-APIKEY`.
* Delta: `HMAC-SHA256` over `method + timestamp + path + query + body`, sent as `signature` with
  `api-key` and `timestamp` headers ([example](https://www.profitaddaweb.com/2025/04/delta-exchange-api-in-python.html)).

---

## 3. Bracket orders

**Delta has a native bracket.** `POST /v2/orders/bracket` takes `product_symbol`, `size`, `side`,
`order_type`, `bracket_stop_trigger_method` and two nested legs — `stop_loss_order` /
`take_profit_order`, each `{order_type, stop_price, trail_amount, limit_price}`. When one leg fills
the other is cancelled automatically, i.e. true OCO behaviour
([schema](https://docs.delta.exchange/), [product doc](https://beta.delta.exchange/support/solutions/articles/80001177901-what-are-bracket-orders-and-how-to-use-them-)).
A plain `POST /v2/orders` can also carry flat `bracket_stop_loss_price` / `bracket_take_profit_price`
fields, which is the fallback if the bracket endpoint is ever unavailable.

**Binance has none.** The emulation chosen: place the entry, then place the two protection legs as
reduce-only `STOP_MARKET` / `TAKE_PROFIT_MARKET` orders. Consequences we had to handle:

1. **Leftover legs.** When the strategy exits early (trailing stop, signal flip), the reduce-only
   legs are still live and would re-close the other side. So closing a position also cancels the
   contract's open orders (`_cancel_protection_legs`).
2. **Partial ordering.** The entry can be rejected; the adapter returns the error and no legs are
   placed.
3. **Response shape differs** — Delta returns `{entry_order, stop_loss_order, take_profit_order}`,
   Binance returns `{entry, legs[]}`. `broker_account.split_order_response()` flattens both into
   `(payload, leg)` pairs so the terminal and the audit table treat them identically.

Real OCO on Binance would need the algo-order endpoints; the reduce-only pair is the documented,
widely used approximation and is good enough for a single-contract strategy.

---

## 4. Sizing: contracts vs BTC

| | Delta BTCUSD perpetual | Binance BTCUSDT perpetual |
| :--- | :--- | :--- |
| Unit | whole **contracts** (integer) | **BTC** (base asset) |
| Contract value | `contract_value` from `/v2/products/{symbol}` — 0.001 BTC per contract | 1.0 |
| Step / minimum | 1 contract | `LOT_SIZE` filter (`stepSize` 0.001, `minQty` 0.001) |

A strategy that thinks in BTC would silently send a 1000× too small order on Delta, so
`get_instrument()` caches the contract spec and `base_to_venue_size()` converts and rounds to the
venue step, returning **0.0 when the result is below the minimum** (the order is refused rather than
rejected by the venue). `venue_to_base_size()` is the inverse for display. The terminal ticket
accepts either unit and shows the notional before the order is sent.

---

## 5. Rate limits — the "20 requests per second" claim

### Delta Exchange

* Every REST endpoint has a **weight**; the default quota is **10 000 per fixed 5-minute window**,
  and the quota **resets to full every 5 minutes** (it is *not* a rolling window — the v2 API
  changelog calls this out explicitly) ([docs](https://docs.delta.exchange/),
  [global docs](https://docs-global.delta.exchange/)).
* Public reads are light, private writes heavy. The published worked example: Get Open Orders = 3,
  Get Balances = 3, Place order = 5, Batch order = 25 (100 + 50 + 200 + 20 calls = 1 950 quota)
  ([docs](https://docs-global.delta.exchange/)).
* 429 responses carry **`X-RATE-LIMIT-RESET`** — milliseconds to wait. Unauthenticated requests are
  throttled per IP, authenticated ones per user ID ([docs](https://docs-global.delta.exchange/)).
* Product/matching-engine limit: **500 operations per second per product**; a 50-order batch counts
  as 50 operations, and **cancellations are exempt**
  ([docs](https://docs-global.delta.exchange/)).
* `GET /v2/rate_limits/quota` returns `{current_quota, remaining_time_in_milliseconds}`, which we
  poll so the UI can show the real remaining budget.
* Batch orders: max 50, same contract. WebSocket: 150 connections / 5 min per IP.

### Binance USDS-M

* **`REQUEST_WEIGHT` 2 400 per minute** per IP, and **`ORDERS` 1 200 per minute *plus* 300 per
  10 seconds**, per account. Both are reported in every response's `rateLimits` array
  ([Binance](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-api-general-info)),
  and confirmed by Binance support: the `ORDERS` budget applies to order-placement endpoints,
  the weight budget to everything ([dev.binance.vision](https://dev.binance.vision/t/how-does-api-rate-limit-classified/8534)).
* Every response carries **`X-MBX-USED-WEIGHT-1M`** with the weight already consumed in the current
  minute.

### Conclusion

The brief's "20 req/s" is a **safe working default, not a venue cap**. Sustained, 20/s = 1 200/min
per IP, which is half of Binance's weight budget and roughly 6 000 of Delta's 10 000 per 5-minute
window — so it is comfortable, but it is *our* policy, not the exchange's rule. The real constraints
are a 5-minute quota (Delta) and a per-minute weight budget plus two order windows (Binance).

### What was implemented

`backend/app/core/rate_limit.py` — one `RateLimiter` **per broker connection**, shared by the live
trader, the data seeder and the terminal poller (a global registry keyed by `broker:key-hash`), so
three workers cannot each spend the same budget.

1. **Local sliding windows**: requests/second, requests/minute, weight/5-min (Delta), orders/minute
   **and orders/10-seconds** (Binance).
2. **Exchange feedback**: `X-MBX-USED-WEIGHT-1M` is tracked and calls are slowed at 85 % of the
   budget (`safe_ratio`); Delta's quota endpoint is read into the limiter too.
3. **429 handling**: retry up to 4 times honouring `Retry-After` (seconds) and
   `X-RATE-LIMIT-RESET` (milliseconds — anything > 120 is treated as ms), then exponential backoff
   (0.35 s base, jitter, 30 s cap). Giving up returns `{"error": …, "rate_limited": true}` — never an
   exception — so a trading loop survives.
4. **Configurable**: limits live on the broker definition (`rate_limit_per_second`,
   `rate_limit_per_minute`, `quota_per_5min`, `orders_per_minute`) and are editable in
   **Broker → Exchange Registry → Limits**; an override of `orders_per_minute` scales the 10-second
   window with it (Binance's own 300/1 200 ratio).
5. **Per-endpoint weights** are attached to every call (`_throttled_request(weight=…)`).

Endpoint weights used (Binance publishes them per endpoint; Delta publishes only the four examples
above, so Delta's are deliberately set at roughly **2× the documented example values** — erring
heavy costs a little throughput, erring light costs a 429 storm):

| Call | Binance | Delta |
| :--- | :--- | :--- |
| candles / mark-price candles | 2 | 3 |
| ticker / mark price / product | 1 | 1 |
| place order | 2 | 10 (doc example: 5) |
| bracket order | — | 20 |
| cancel / cancel-all | 1 | 5 |
| edit order | — | 10 |
| open orders / history / fills | 1 / 5 / 5 | 5 |
| positions / close-all / change margin | 5 / 2 / 2 | 5 / 20 / 10 |
| wallet | 5 | 5 (doc example: 3) |
| leverage / margin mode | 1 | 10 |
| rate-limit quota | — | 1 |

A single strategy worker makes a handful of calls per minute, so in practice the terminal's 10-second
poll is the dominant consumer — which is exactly why the panels are served by **one** snapshot call
rather than one per panel.

---

## 6. Terminal data model (what "like Delta Exchange" means)

`broker_account.account_snapshot()` produces one payload for both venues, and each panel degrades
independently: a dead endpoint fills `errors[<panel>]` instead of blanking the screen.

| Panel | Fields |
| :--- | :--- |
| Contract | symbol, contract type, contract value, tick size, step/min size, size unit, quote asset |
| Mark price | mark (and last/index price) of the perpetual |
| Positions | side, size in BTC **and** venue units, entry, mark, liquidation, bankruptcy, margin, leverage, notional, uPnL, ROE % |
| Open orders | time, contract, type, side, size, price, filled, order id, cancel |
| Stop orders | leg (stop-loss / take-profit), trigger price, limit price, **trigger method**, reduce-only |
| Fills | time, side, size, price, fee, maker/taker, realised PnL, trade id |
| Order history | as open orders plus avg fill and status |
| Wallet & margin | balance, available, used / order / position margin, uPnL, equity |
| Risk | equity, margin utilisation %, effective leverage, long/short/net exposure, position count |
| Rate limits | per-second, per-minute, orders/min, orders/10s, weight/5-min, exchange quota, retries |

Normalisation notes:

* Delta reports `created_at` in **microseconds**, Binance in **milliseconds** — `_millis_to_dt()`
  accepts both.
* Delta answers with `{"success": true, "result": …}`; order-mutation responses are unwrapped so
  callers always see the order object.
* Binance keeps margin at the account level; Delta reports it per position. `portfolio_risk()`
  computes the common numbers (equity = wallet + uPnL, utilisation = used / equity, effective
  leverage = gross notional / equity) from either.

**Local audit trail.** `broker_orders` (leg, parent order, client id, source, instance key, raw
payload) and `broker_fills` (deduplicated on the exchange trade id) mirror everything, because
exchanges drop orders from their history window after a while and because a strategy instance's
orders should be attributable. Matching is done on the exchange's own id first — protection legs
placed without a client id must never collapse into their parent entry.

---

## 7. Decisions, trade-offs and known gaps

| Decision | Why | Cost / gap |
| :--- | :--- | :--- |
| One snapshot call, not one per panel | Rate limits — polling 6 panels separately would 6× the request count | One slow endpoint delays the whole refresh; mitigated by per-panel `errors` |
| Bracket on Binance = reduce-only legs | No native endpoint | Two extra orders per entry; legs must be cancelled on exit (done) |
| Mark-price trigger forced | Risk is priced on the mark price everywhere in the app | A user asking for `last_traded_price` gets ignored unless they pass it explicitly |
| Weights for Delta are estimates | Delta publishes only four example weights | Over-conservative by ~2×; raise them if a real account's quota proves roomier |
| Orders/minute override scales orders/10s | Binance's own ratio is 300 / 1 200 | Assumes the ratio holds for custom limits |
| Return `{"error": …}` instead of raising | A rejected order must not kill a running worker | Callers must check for `error` (the terminal does) |
| Local mirror of orders/fills | Exchange history windows are short; attribution matters | Extra tables; rows can drift if an order is changed outside PHANTOM |

**Known gaps / next steps**

1. **Websockets.** Both venues push fills and order updates over WS (Delta: 150 connections / 5 min
   per IP). Polling every 10 s is fine at our frequency but WS would remove the remaining rate-limit
   pressure and give instant fills.
2. **Hedge mode / `positionSide`** is not modelled — the app is one-way (`BOTH`) only.
3. **Batch orders** (Delta, max 50) are not used; useful if a strategy ever scales out.
4. **Binance algo orders** would give a true OCO; today the reduce-only pair is the approximation.
5. **MMP** (Delta's market-maker protection) and self-trade prevention are not exposed.

---

## 8. Verification

* `backend/test_live_account.py` — 144 checks: limiter windows (including the 10-second order cap),
  order/position/fill adapters for both venues driven against a mock exchange, 429 retry and
  give-up paths, normalized schema, bracket splitting, the audit tables, and every HTTP endpoint.
* `backend/tools/mock_exchange.py` — a fake Binance REST surface to exercise the live endpoints
  end to end without real keys (register it as a `binance`-kind broker pointing at
  `http://127.0.0.1:8099`).
* `frontend/tests/terminal_ui.jsx` — 56 checks: every panel, the order ticket, per-tab tables,
  empty and partial-failure states, and the formatting helpers.

## Sources

* Delta Exchange API docs — rate limits, product limits, `CreateOrderRequest`,
  `CreateBracketOrderRequest`, `EditBracketOrderRequest`: [docs.delta.exchange](https://docs.delta.exchange/) ·
  [docs-global.delta.exchange](https://docs-global.delta.exchange/)
* Delta bracket orders (product behaviour, OCO): [support article](https://beta.delta.exchange/support/solutions/articles/80001177901-what-are-bracket-orders-and-how-to-use-them-)
* Delta REST signing example: [profitaddaweb.com](https://www.profitaddaweb.com/2025/04/delta-exchange-api-in-python.html)
* Binance USDS-M order parameters (`workingType`, `priceProtect`, `reduceOnly`, `closePosition`,
  `newClientOrderId`): [New Order (TRADE)](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Order-Test)
* Binance rate limits (2 400 weight/min, 1 200 orders/min, 300 orders/10 s):
  [WebSocket API General Info](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-api-general-info) ·
  [dev.binance.vision](https://dev.binance.vision/t/how-does-api-rate-limit-classified/8534)
* Binance order-type/trigger semantics (community write-up, matches the official enum):
  [0xhardman](https://0xhardman.xlog.app/binance-futures-api-guide?locale=en)
