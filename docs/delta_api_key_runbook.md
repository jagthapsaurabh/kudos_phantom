# Runbook — “API key rejected by the exchange” (Delta India)

**Who this is for:** whoever runs the trading box. You do not need to read the code.

**Symptom:** the live terminal shows a red **API key rejected by the exchange** banner, or a live
instance is `is_running: true`, its chart keeps updating, and every account panel is empty:

```json
"errors": {
  "positions":     "Delta HTTP 401: {\"code\": \"invalid_api_key\"}",
  "open_orders":   "Delta HTTP 401: {\"code\": \"invalid_api_key\"}",
  "balance":       "Delta HTTP 401: {\"code\": \"invalid_api_key\"}"
},
"mark_price": 77981.87,        <- market data still fine
"risk": { "wallet_balance": 0.0, ... }
```

## Why this looks like “nothing is wrong”

Candles, tickers and the mark price are **public** endpoints; positions, orders, fills and balances
are **signed** with the API key. A dead key therefore leaves the chart moving and the worker
`is_running`, and only the account panels know. That is also why the failure is worth catching in
the first minute instead of the first trade.

## What the app now does on its own

| Behaviour | Detail |
| :--- | :--- |
| **Detects it** | Two consecutive signed calls rejected (`invalid_api_key` / HTTP 401 / Binance `-2015`) latches the connection. One rejection alone is *suspect*, not rejected — a sub-account key that is not allowed to list the parent's accounts 401s on exactly that one endpoint and is otherwise tradeable. |
| **Holds entries** | A live instance on a rejected key places no orders. Positions already open keep being marked to market (public data) and the exit logic keeps running. |
| **Stops the quota burn** | Signed calls are skipped while the key is rejected, on a 5s → 10s → 20s … ladder capped at 5 min. Before this, a worker polled the dead account ~40 times a minute against a **fixed 10 000 weight / 5 min** budget — burning the quota the first order after the fix needs. Skipped calls are counted (`held_calls`) so the pause is measurable afterwards. |
| **Parks the deadman switch** | The Delta heartbeat cannot ack with a dead key, so it stands itself down with one graceful `ttl=0` instead of racking up a failure every 25 s. Deliberate: `ttl=0` means “planned pause, not a crash”, so the exchange does **not** cancel the resting stop-loss / take-profit legs — the position stays protected while the bot is blind. It re-arms itself on the first accepted signed call. |
| **Heals without a restart** | On each backoff window the instance re-reads its saved connection from the database and, if the key changed, swaps the client — together with the account it queues on, its rate-limit budget and the heartbeat. Saving a new key on that connection pushes it to every running instance in the same request. |

## What you have to do

1. **Broker Settings → the connection → Check key** (quick) or **Test connection** (full
   read-only battery: market data, clock, all four Delta environments, signed calls, rate quota).
   The key is signed against **all four** Delta environments, and a verdict:
   * *“accepted by INDIA-PRODUCTION”* + *“the connection is flagged testnet”* → flip the toggle.
     That mismatch is the whole bug; the key is fine.
   * *“accepted by GLOBAL-PRODUCTION / GLOBAL-TESTNET”* → the key lives on **Delta Global**;
     India and Global keep separate key stores. Use **Use this environment** (or add a
     connection for **Delta Exchange Global**) — the key is not dead, it is on the other market.
   * *“rejected by every host that answered”* → the key is dead: rotated, deleted, or pasted
     incompletely (Delta keys are long — check the **last** characters). Create a fresh one in the
     panel of the environment you actually trade.
   * *“knows the key, permission missing”* → the key exists and is on the right environment but
     lacks an endpoint permission; enable **Read Data / Trading** in API Management.
   * *“unreachable”* → nothing to conclude yet: DNS/SSL/egress on the box, not the key.
2. **Broker Settings → Replace keys** (or **Use this environment** from the test report, which
   changes broker + environment and hands them to running instances in one click). Paste key
   **and** secret (a blank secret keeps the stored one — the API never returns it). Keys are
   trimmed of whitespace as saved; a newline from a terminal paste is invisible in the UI and is
   a different key to the venue. Running instances are handed the new key by that same save, and
   the response says how many took it.
3. **If you already know the environment — align without a key check.** Detection needs the venue
   to accept the stored key; a key you *just created* proves itself on the next signed call.
   This deployment trades **Delta India production**, so:
   * UI: Broker Settings → **Align to India production** on the connection (or **Align all to
     India production** on the Saved connections header) — sets broker `Delta`, testnet OFF,
     REST `https://api.india.delta.exchange`, private WS `wss://socket.india.delta.exchange`,
     public WS `wss://public-socket.india.delta.exchange`, and hands it to running instances.
   * API: `POST /broker-connections/{id}/align {"environment":"INDIA_PRODUCTION"}` (one row) or
     `POST /broker-connections/align-delta` (every Delta-family row of the login).
   * CLI, on the trading server (a whitelisted key only validates from its egress IP):
     ```bash
     cd backend && ../.venv/bin/python tools/align_delta_env.py --all-delta --apply --verify
     # dry run first:  --all-delta           (prints what would change)
     # one row only:   --label "NishKudos global"
     ```
4. **Live Trade → Reload keys** on the instance, if you would rather not wait for the next retry
   window. It re-reads and probes immediately; a still-bad key just goes back to holding entries.
5. Verify: the connection card shows the margin mode read back from the venue (not an error), and
   `GET /live-account/snapshot` returns a non-empty `balance` with `auth_error: null`.

From the command line, the same battery as step 1 (run it **on the trading server** — a key with an
IP whitelist 401s from any other egress IP, which is indistinguishable from a bad key):

```bash
cd backend && ../.venv/bin/python tools/test_connection.py --label "NishKudos"
# full report, read-only; --apply repoints the saved connection at the detected environment:
cd backend && ../.venv/bin/python tools/test_connection.py --label "NishKudos" --apply --json
# quick four-host key check only:
cd backend && ../.venv/bin/python tools/check_delta_key.py --api-key KEY --api-secret SECRET
```

## Before you rotate a key on an account that is holding a position

Fixing the key is usually routine, but do it with the position **flat** when you can:

* While the key is rejected, the bot cannot manage or close the position — only the resting
  exchange-side bracket legs protect it.
* The deadman switch is stood down (`ttl=0`), so its “cancel orders if the bot dies” safety net is
  off for the duration. Re-enable it by making a signed call work again; the switch re-arms itself.
* Anything placed manually in the venue's own panel during that window is invisible to the
  terminal's local book (the instance reconciles against the venue's position, so it will hold
  entries rather than stack on top of it).

## If a live instance still 401s after a correct key is saved

* **Is it the same connection?** Instances adopt the row they were started with (`connection_id` in
  the status payload). If that row was deleted and re-added, point the instance at the new one with
  `POST /live-trade/reload-credentials {"instance_key": …, "connection_id": …}`.
* **Different account, not different key:** connections are per login. Keys saved while signed in as
  the admin are not visible to a client account, and `saved_credentials` will say so.
* **Shared account:** two instances on one API key take turns holding the position. *Not trading*
  can be the queue, not the key — the instance card shows whose turn it is.
* Still nothing? `GET /live-account/rate-limits` shows `credential_health` for the connection
  (`state`, `error`, `retry_in_seconds`, `held_calls`), and the instance's `credentials` block in
  `GET /live-trade/status` shows `entries_held`, `reloads` and `last_reload`.

## Delta's error table, mapped to what this app does

The same five rejections Delta's support guidance lists are all recognised as **key problems**
(they latch the credential guard, hold entries, and surface the red banner instead of burning
quota); the connection battery (Test connection) separates the causes:

| Delta error | What it means | What the app does |
| :--- | :--- | :--- |
| `ip_not_whitelisted_for_api_key` | Request from a non-whitelisted IP | Recognised as an auth rejection; the banner says to run the check on the trading server — a whitelisted key 401s from any other egress IP exactly like a dead key |
| `invalid_signature` / `signature mismatch` | Wrong secret, or method/path/query/payload changed between signing and sending | Recognised; **Test connection** re-signs the documented `METHOD+timestamp+path+?query+body` string on all four hosts and reports where the key works |
| `request_expired` | Timestamp older than the 5-second window | Recognised; the connection battery compares the server clock to the exchange and tells you to NTP-sync if the skew exceeds 5 s |
| `api_key_not_found` / `invalid_api_key` | Incorrect or deleted key | Recognised; runbook steps 1–3 (re-paste, re-create, or Align to India production) |
| `incomplete_payload` | Missing api-key/timestamp/signature header | Recognised — this one is a client bug, not an operator fix; report it with the full error text |

Signing (implemented in `BrokerClient._delta_request`, verified against the official client):

* `signature_data = METHOD + timestamp + path + query_string + payload`, `timestamp` in Unix
  **seconds** generated right before sending (inside the 5-second window);
* `query_string` is `''` or `?k=v&…` with keys sorted and values `quote_plus`-encoded;
* `payload` is compact JSON (`{"k":"v"}` separators) or `''`;
* `signature = HMAC_SHA256(api_secret, signature_data)` hex;
* headers `api-key`, `timestamp`, `signature`, `Content-Type: application/json` (+ the
  `User-Agent` Delta requires). Secrets live server-side in the database and are never returned
  by the API or shipped to the browser; each bot can use its own connection/key.

## Which environment is which

The official rule, enforced by the app on this box (`DELTA_DEPLOYMENT_FAMILY=india`, the default):

* **Delta India account keys** (www.delta.exchange) → used **only** with the production API
  `https://api.india.delta.exchange`.
* **Demo account keys** (demo.delta.exchange) → used **only** with the testnet API
  `https://cdn-ind.testnet.deltaex.org`.
* **`https://api.delta.exchange` belongs to Delta Global and is not used here.** On an India box
  the app refuses to create/switch/align a connection onto DeltaGlobal with a 400 carrying this
  rule; the read-only key check still signs one call per environment so it can *report* a Global
  key (re-create such a key on India instead).

**This deployment trades Delta India production** — the first row is where every Delta connection
must point:

| | REST host | Keys work on | App broker code |
| :--- | :--- | :--- | :--- |
| Delta India **production** ✅ target | `https://api.india.delta.exchange` | real-money keys from the live panel | `Delta` |
| Delta India **testnet / demo** | `https://cdn-ind.testnet.deltaex.org` | keys from `demo.delta.exchange` only | `Delta` (testnet ON) |
| Delta **Global** production | `https://api.delta.exchange` | keys from `global.delta.exchange` / `www.delta.exchange` | `DeltaGlobal` |
| Delta **Global** testnet / demo | `https://testnet-api.delta.exchange` | keys from `demo-global.delta.exchange` | `DeltaGlobal` (testnet ON) |

The four key stores are separate: a production key on a testnet host, a demo key on production, or
an India key on Global (and the reverse) all answer `invalid_api_key`, and no amount of re-pasting
fixes it — only pointing the connection at the right environment does. That is why the check
signs all four hosts before it is allowed to call a key dead.
