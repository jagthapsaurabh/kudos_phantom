"""A rejected Delta API key: detect it, hold trading, stop the quota burn, recover.

The incident this covers is the quiet kind. Every *signed* call answers
``HTTP 401 {"code": "invalid_api_key"}`` while the public endpoints (candles,
tickers, the mark price) keep streaming, so a live instance looks healthy:
``is_running`` is true, the chart moves, and the account panels are simply empty.
Meanwhile the worker polls the dead account once a minute and the Delta deadman
switch acks every 25 seconds, spending a fixed 5-minute weight budget on calls
that cannot succeed — including the budget the *first* order after the fix needs.

Covered here, against a local mock of Delta India (no real venue, no real key):

1. classification — which error strings mean "the key", and which mean "this
   one endpoint is not permitted for this key" (one rejection is a warning, two
   in a row without anything accepted between is the wall)
2. the tally clearing on the next accepted call, and transport failures saying
   nothing about the key either way
3. a held tick: entries stop, no order reaches the exchange, and the signed-call
   count stops climbing tick over tick
4. the deadman switch parking itself gracefully instead of racking up failures,
   and re-arming after a recovery
5. credential reload: the instance re-reads its saved connection (or falls back
   to the login's first usable one), swaps client + account identity + rate
   budget + heartbeat, and resumes without a restart
6. the API surface: ``credentials`` on the status payload, the force-reload
   endpoint, a replaced key reaching running instances on save, ``Check key``
   naming the environment that accepts the key, and whitespace-trimmed pastes

Run:  cd backend && ../.venv/bin/python test_delta_key_recovery.py
"""
import asyncio
import json
import os
import sys
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pandas as pd

sys.path.insert(0, '.')

TESTDB = "/tmp/delta_key_recovery_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"

from app.core.strategy import PhantomV2Config                          # noqa: E402
from app.services.broker_client import (                              # noqa: E402
    AUTH_LATCH_STRIKES, BrokerClient, is_auth_rejection,
)
from app.services import delta_key_probe                              # noqa: E402
from app.services import broker_account                               # noqa: E402
from app.services.heartbeat import DeadmanSwitch                      # noqa: E402
from app.services.live_trader import (COORDINATOR, LiveTradeService,  # noqa: E402
                                      account_key)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not cond else ""))


def section(title):
    print(f"\n{'=' * 66}\n  {title}\n{'=' * 66}")


# ---------------------------------------------------------------------------
# Mock venue: public paths always answer, signed paths accept one key only.
# ---------------------------------------------------------------------------
GOOD_KEY = "GOOD-DELTA-KEY"

SIGNED_PATHS = ("/v2/wallet/balances", "/v2/positions/margined", "/v2/orders",
                "/v2/orders/history", "/v2/fills", "/v2/sub_accounts", "/v2/heartbeat",
                "/v2/orders/bracket")


class Venue:
    """One mock Delta India host plus its own request log."""

    def __init__(self, accepting=GOOD_KEY, name="prod"):
        self.accepting = accepting          # the api-key header this host accepts
        self.name = name
        self.signed_calls = []              # (method, path, api_key)
        self.orders = []                    # POST /v2/orders*
        self.heartbeats = []                # POST /v2/heartbeat*
        self.server = None
        self.thread = None

    # -- routing -----------------------------------------------------------
    def handle(self, method, path, query, headers, body):
        signed = any(path == p or path.startswith(p + "/") or path.startswith(p + "?")
                     for p in SIGNED_PATHS) or method in ("POST", "PUT", "DELETE")
        api_key = headers.get("api-key") or ""
        if signed:
            self.signed_calls.append((method, path, api_key))
            if api_key != self.accepting:
                return 401, {"code": "invalid_api_key",
                             "message": {"error": "invalid_api_key"}}
        if path == "/v2/wallet/balances":
            return 200, {"success": True, "result": [{"asset": {"symbol": "USD"},
                                                      "balance": "500", "available_balance": "400",
                                                      "order_margin": "0", "position_margin": "0"}]}
        if path == "/v2/positions/margined":
            return 200, {"success": True, "result": []}
        if path == "/v2/orders" and method == "POST":
            self.orders.append(json.loads(body or "{}"))
            return 200, {"success": True, "result": {"id": len(self.orders), "status": "filled",
                                                      "avg_price": "60000"}}
        if path.startswith("/v2/orders/bracket"):
            self.orders.append(json.loads(body or "{}"))
            return 200, {"success": True, "result": {"order": {"id": len(self.orders)},
                                                      "stop_loss_order": {"id": 9001},
                                                      "take_profit_order": {"id": 9002}}}
        if path.startswith("/v2/heartbeat"):
            self.heartbeats.append((method, json.loads(body or "{}")))
            return 200, {"success": True, "result": {"process_enabled": "yes",
                                                     "heartbeat_timestamp": 9999999999}}
        if path == "/v2/sub_accounts":
            return 200, {"success": True, "result": [{"id": "5112346", "account_name": "main",
                                                      "margin_mode": "cross", "is_sub_account": False}]}
        if path.endswith("/orders/leverage"):
            return 200, {"success": True, "result": {"leverage": "7", "product_id": 84}}
        if path.startswith("/v2/tickers/"):
            return 200, {"success": True, "result": {"mark_price": "77981.87", "close": "78000"}}
        if path.startswith("/v2/products/"):
            return 200, {"success": True, "result": {"id": 84, "symbol": "BTCUSD",
                                                     "contract_value": 0.001, "tick_size": 0.1,
                                                     "contract_type": "perpetual_futures",
                                                     "quoting_asset": {"symbol": "USD"}}}
        if path == "/v2/products":
            return 200, {"success": True, "result": []}
        return 200, {"success": True, "result": []}

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        venue = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _go(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode() if length else ""
                status, payload = venue.handle(method, self.path.split("?")[0],
                                               self.path, dict(self.headers), body)
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self._go("GET")

            def do_POST(self):
                self._go("POST")

            def do_PUT(self):
                self._go("PUT")

            def do_DELETE(self):
                self._go("DELETE")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.url

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass

    def signed_count(self):
        return len(self.signed_calls)

    def reset(self):
        self.signed_calls.clear()
        self.orders.clear()
        self.heartbeats.clear()


PROD = Venue(GOOD_KEY, "prod")
PROD_URL = PROD.start()

# Nothing in this file may talk to the real exchange: the built-in venue URLs
# are re-pointed at the mock, so even a client built without a definition (a
# reload, a probe fallback) stays local.
BrokerClient.DEFAULTS = {
    "Binance": {"kind": "binance", "market": PROD_URL, "trading": PROD_URL},
    "Delta": {"kind": "delta", "market": PROD_URL, "trading": PROD_URL},
}


class _Definition:
    """The registry row a real instance carries: kind + URLs of the venue."""
    code = "Delta"
    kind = "delta"
    market_data_url = PROD_URL
    trading_api_url = PROD_URL
    rate_limit_per_second = None
    rate_limit_per_minute = None
    quota_per_5min = None
    orders_per_minute = None

# A second host that accepts nothing: the mock's stand-in for the other
# environment, so the probe has two different answers to compare.
DEAD = Venue(accepting="__nobody__", name="testnet")
DEAD_URL = DEAD.start()


def delta_client(api_key, **kw):
    """A real BrokerClient aimed at the mock, using the mock's own transport."""
    client = BrokerClient(api_key, "secret", "Delta", testnet=kw.pop("testnet", False))
    client.trading_url = client.market_url = PROD_URL
    return client


# ===========================================================================
section("1. which failures are about the key, and which are about one endpoint")
# ===========================================================================
check("Delta's invalid_api_key and Binance's -2015 are key failures",
      is_auth_rejection('Delta HTTP 401: {"code": "invalid_api_key"}')
      and is_auth_rejection("Binance HTTP 401: Invalid API-key, IP, or permissions for this endpoint")
      and is_auth_rejection("Delta HTTP 401: invalid api-key"), "markers")
check("a throttled or 5xx endpoint is NOT reported as a key problem",
      not is_auth_rejection("Delta rate limited (HTTP 429)")
      and not is_auth_rejection("Delta server error (HTTP 502)")
      and not is_auth_rejection("product symbol not found"), "markers")

client = delta_client("DEAD-KEY")
check("a fresh client reports ok — nothing has been rejected yet",
      client.credential_health()["state"] == "ok", str(client.credential_health()))

first = client.get_account_balance()
check("the venue's 401 comes back as an error object, not an exception",
      isinstance(first, dict) and "invalid_api_key" in str(first.get("error")), str(first)[:160])
check("one rejection is a warning, not a verdict (sub-account keys 401 on one endpoint)",
      client.credential_health()["state"] == "suspect"
      and client.signed_calls_held()[0] is False, str(client.credential_health()))

second = client.get_positions("BTCUSD")
health = client.credential_health()
check("a second consecutive rejection is the wall",
      health["state"] == "rejected" and health["consecutive_rejections"] == 2
      and health["strikes"] == AUTH_LATCH_STRIKES, str(health))
check("while rejected, callers are told to hold off and for how long",
      client.signed_calls_held()[0] is True and client.signed_calls_held()[1] > 0,
      str(client.signed_calls_held()))
check("the environment the client is pointed at travels with the verdict",
      health["base_url"] == PROD_URL and health["environment"] == "production", str(health))

public = client.fetch_mark_price("BTCUSD")
check("public market data is untouched — this is why the failure is easy to miss",
      public is not None and abs((public.mark_price or 0) - 77981.87) < 1, str(public)[:120])


PROD.reset()
repeats = [client.get_positions("BTCUSD") for _ in range(4)]
check("the client reports, it does not refuse: every caller still gets the venue's own "
      "error (a per-request terminal poll has no budget to protect)",
      PROD.signed_count() == 4 and all("invalid_api_key" in str(r.get("error")) for r in repeats),
      f"{PROD.signed_count()} calls, {str(repeats[0])[:80]}")
check("and it hands the long-lived loops a 'hold off' signal they can honour",
      client.signed_calls_held()[0] is True, str(client.signed_calls_held()))
client.note_signed_call_held("positions read skipped while the key is rejected")
check("skipped calls are counted, so a pause is measurable after it ends",
      client.credential_health()["held_calls"] == 1, str(client.credential_health()))

PROD.accepting = "DEAD-KEY"          # the operator re-enabled the same key venue-side
recovered = client.get_account_balance()
check("the next accepted signed call clears everything",
      not (isinstance(recovered, dict) and recovered.get("error"))
      and client.credential_health()["state"] == "ok"
      and client.credential_health()["consecutive_rejections"] == 0, str(client.credential_health()))
PROD.accepting = GOOD_KEY


# the tally must not be cleared by a network blip, which says nothing about the key
offline = BrokerClient("k", "s", "Delta")
offline.trading_url = offline.market_url = "http://127.0.0.1:1"     # nothing listening
offline._note_signed_result('Delta HTTP 401: {"code": "invalid_api_key"}')
offline._note_signed_result('Delta HTTP 401: {"code": "invalid_api_key"}')
before = offline.credential_health()
offline.get_account_balance()                                       # transport failure
after = offline.credential_health()
check("a transport failure neither adds to nor resets a rejection tally",
      after["state"] == before["state"] and after["consecutive_rejections"] == before["consecutive_rejections"],
      f"{before} -> {after}")

clean = delta_client(GOOD_KEY)
clean.get_account_balance()
clean.get_positions("BTCUSD")
check("a working key stays 'ok' across signed calls",
      clean.credential_health()["state"] == "ok", str(clean.credential_health()))


# ===========================================================================
section("2. public-only failures must not look like a key problem")
# ===========================================================================
class _OnePanelDead:
    """Only /v2/fills is down — the classic 'do not shout key' case."""
    broker_name = "Delta"

    def __init__(self):
        from app.core.rate_limit import RateLimitConfig, RateLimiter
        self.limiter = RateLimiter("one-panel", RateLimitConfig())
        self.rate_limit_config = RateLimitConfig()

    def get_instrument(self, symbol, refresh=False):
        return {"contract_value": 0.001}

    def fetch_mark_price(self, symbol):
        return None

    def get_positions(self, symbol=None):
        return []

    def get_open_orders(self, symbol=None):
        return []

    def get_fills(self, symbol=None, limit=100):
        return {"error": 'Delta HTTP 429: rate limited'}

    def get_order_history(self, symbol=None, limit=100):
        return {"error": 'Delta HTTP 429: rate limited'}

    def get_account_balance(self, asset="USD"):
        return {"balances": []}

    def rate_limit_usage(self):
        return self.limiter.snapshot()


partial = broker_account.account_snapshot(_OnePanelDead(), "BTCUSD")
check("one throttled panel degrades that panel only",
      partial["positions"] == [] and "fills" in partial["errors"]
      and partial.get("auth_error") is None, str(partial["errors"]))


class _AllDead(_OnePanelDead):
    def _dead(self, *a, **k):
        return {"error": 'Delta HTTP 401: {"code": "invalid_api_key"}'}
    get_positions = _dead
    get_open_orders = _dead
    get_fills = _dead
    get_order_history = _dead
    get_account_balance = _dead


snapshot_dead = broker_account.account_snapshot(_AllDead(), "BTCUSD")
verdict = snapshot_dead.get("auth_error") or ""
check("every signed panel 401 collapses into one verdict",
      "rejected this API key" in verdict and "invalid_api_key" in verdict, verdict[:200])
check("the verdict names the fix that exists now: replace the key, then reload",
      "Broker Settings" in verdict and "Replace the key" in verdict
      and "Reload keys" in verdict and "Check key" in verdict, verdict[:320])
check("the verdict no longer tells the operator to restart as the primary fix",
      "no longer needs a restart" in verdict and "Check key" in verdict, verdict[:320])


# ===========================================================================
section("3. the worker holds entries, spends nothing, and says so")
# ===========================================================================
BASE = datetime(2024, 1, 1)


def candles(bars=120, last_bar=0, seed=7):
    rng = np.random.RandomState(seed)
    close = 60000 + np.cumsum(rng.randn(bars) * 10)
    idx = pd.date_range(BASE, periods=bars, freq="1h") + timedelta(hours=last_bar)
    return pd.DataFrame({"open": close, "high": close + 30, "low": close - 30,
                         "close": close, "volume": 100.0}, index=idx)


class PersistentSignal:
    def generate_signals(self, df_1h, df_4h):
        signals = np.zeros(len(df_1h))
        signals[-1] = 1
        return signals


def make_dead_key_service(key="DEAD-KEY", **kw):
    """A live worker on a key the mock rejects, with candles fed offline."""
    config = PhantomV2Config(**kw)
    svc = LiveTradeService("CustomTest", [], key, "secret", is_custom=True,
                           initial_capital=20000, margin_pct=25, broker_name="Delta",
                           user_id=1, instance_key="live_dead_delta_1")
    svc.config = config
    svc.oms.config = config
    svc.strategy = PersistentSignal()
    svc.broker = delta_client(key)
    svc.definition = _Definition()
    svc.use_mark_price = False
    svc.state = {"bar": 0}
    svc._fetch_candles = lambda interval, limit: candles(last_bar=svc.state["bar"])
    svc._fetch_mark_price = lambda: None
    return svc


PROD.reset()
svc = make_dead_key_service()
for _ in range(6):
    asyncio.run(svc.tick())
after_dead = PROD.signed_count()
orders_sent = len(PROD.orders)
check("six ticks of a dead key never send an order",
      orders_sent == 0, f"{orders_sent} orders")
check("entries are held and counted, not silently skipped",
      svc.credentials_state == "rejected" and svc.entries_held_credentials == 4,
      f"state={svc.credentials_state} held={svc.entries_held_credentials}")
check("even the discovery ticks sent no order (the unreadable position read gates the entry)",
      not svc.oms.active_trades and PROD.orders == [] and not svc.broker._instrument_cache.get("skipped"),
      f"{list(svc.oms.active_trades)} / {PROD.orders}")
check("the client records the signed calls the worker skipped",
      svc.broker.credential_health()["held_calls"] >= 4,
      str(svc.broker.credential_health()))
check("the position book is still marked (public candles keep working)",
      svc.last_price is not None and svc.last_checked is not None,
      f"last_price={svc.last_price}")
check("the credential block explains it for the status API",
      svc.credentials_status()["state"] == "rejected"
      and "invalid_api_key" in (svc.credentials_status()["error"] or ""),
      str(svc.credentials_status())[:240])

PROD.reset()
for _ in range(10):
    asyncio.run(svc.tick())
check("and the quota burn stops: 10 more ticks cost 0 signed calls",
      PROD.signed_count() == 0, f"{PROD.signed_count()} calls (was ~5/tick before)")
check("the hold counter keeps climbing so the pause is measurable",
      svc.entries_held_credentials == 14, str(svc.entries_held_credentials))

no_adopt = svc.reload_credentials(force=True)
check("a reload with an unchanged key reports it and keeps waiting",
      no_adopt["reloaded"] is False and no_adopt["verified"] is False
      and ("no Delta connection" in str(no_adopt["reason"]) or "reload" in str(no_adopt["reason"])),
      str(no_adopt)[:240])


# ===========================================================================
section("4. the deadman switch parks itself instead of failing forever")
# ===========================================================================
async def heartbeat_scenario():
    """A switch armed normally, then the key starts failing: try, park, re-arm."""
    sw = DeadmanSwitch(delta_client(GOOD_KEY), "phantom_test_dead",
                       product_symbols=["BTCUSD"], ack_interval=0.05)
    PROD.reset()
    await sw.start()
    await asyncio.sleep(0.2)
    attempted = sw.stats()
    # The key is revoked mid-run: from here the venue answers 401 to everything.
    PROD.accepting = "__nobody__"
    await asyncio.sleep(0.2)
    while_dying = sw.stats()
    PROD.reset()
    await sw.stand_down("Delta rejected the API key — acks could not land")
    calls_after_stand_down = PROD.signed_count()
    PROD.reset()
    await asyncio.sleep(0.25)
    parked = sw.stats()
    # Measure "nothing more is sent" while the switch is still parked, before
    # the resume below starts acking again.
    calls_while_parked = PROD.signed_count()
    failures_at_park = sw.failures
    await asyncio.sleep(0.2)
    parked_again = (PROD.signed_count(), sw.failures)
    # The operator fixes the key: the worker swaps its client and resumes the
    # beat on that new client (the latched one is still inside its backoff).
    PROD.accepting = "DEAD-KEY"
    sw.client = delta_client("DEAD-KEY")
    await sw.resume()
    await asyncio.sleep(0.25)
    resumed = sw.stats()
    PROD.accepting = GOOD_KEY
    await sw.stop()
    return (attempted, calls_after_stand_down, while_dying, parked,
            calls_while_parked, failures_at_park, parked_again, resumed)


PROD.accepting = GOOD_KEY
(attempted, calls_after_stand_down, while_dying, parked, calls_while_parked,
 failures_at_park, parked_again, resumed) = asyncio.run(heartbeat_scenario())
check("a working key arms the switch and keeps acking",
      attempted["created"] is True and attempted["acks"] > 0, str(attempted)[:200])
check("once the key fails, acks are skipped rather than fired into a 401 wall",
      while_dying["skipped_acks"] >= 1, str(while_dying)[:200])
check("stand down is a deliberate pause, not a stale switch",
      parked["stood_down"] is True and "API key" in (parked["stood_down_reason"] or "")
      and parked["enabled"] is False, str(parked)[:240])
check("stand down sends exactly one graceful ttl=0 (a planned pause, not a crash)",
      calls_after_stand_down == 1, f"{calls_after_stand_down} calls")
check("the switch is NOT forgotten: created stays true, so its resting legs still protect",
      parked["created"] is True, str(parked)[:200])
check("parked means parked: no further calls at all",
      calls_while_parked == 0, f"{calls_while_parked} calls after the stand down")
check("and the failure counter stops climbing instead of growing every 25s",
      parked_again == (0, failures_at_park),
      f"calls {parked_again[0]}, failures {failures_at_park} -> {parked_again[1]}")
check("resume re-arms the ack loop on the new credentials",
      resumed["enabled"] is True and resumed["stood_down"] is False
      and resumed["created"] is True and resumed["acks"] > 0, str(resumed)[:240])

PROD.reset()
svc2 = make_dead_key_service()
svc2.heartbeat = DeadmanSwitch(svc2.broker, "phantom_worker_test", product_symbols=["BTCUSD"],
                               ack_interval=0.05)
svc2.heartbeat.created = True
svc2.heartbeat.enabled = True
# Two signed calls rejected is the wall; holding entries then parks the switch.
svc2.broker.get_positions("BTCUSD")
svc2.broker.get_positions("BTCUSD")
asyncio.run(svc2._hold_for_credentials())
check("the worker stands the switch down as part of holding entries",
      svc2.heartbeat.stood_down is True and svc2.heartbeat_stood_down is True,
      str(svc2.heartbeat.stats())[:200])
PROD.reset()
asyncio.run(svc2._hold_for_credentials())
check("and does not re-run the stand-down on every tick",
      svc2.heartbeat.stood_down is True and PROD.signed_count() == 0,
      f"{PROD.signed_count()} calls on the second hold")
svc2.broker.clear_auth_latch()
asyncio.run(svc2.credentials_recovered())
check("recovery resumes the switch",
      svc2.heartbeat.stood_down is False and svc2.credentials_state == "ok",
      str(svc2.heartbeat.stats())[:200])


# ===========================================================================
# ===========================================================================
section("5. credential reload: swap the client, keep the run")
# ===========================================================================
from app.database.models import (SessionLocal, User, BrokerConnection,      # noqa: E402
                                 BrokerDefinition)

db = SessionLocal()
for model in (User, BrokerConnection, BrokerDefinition):
    model.__table__.create(bind=db.get_bind(), checkfirst=True)
db.query(BrokerConnection).delete()
db.query(User).delete()
db.query(BrokerDefinition).delete()
db.commit()
definition = BrokerDefinition(code="Delta", name="Delta Exchange", kind="delta",
                              market_data_url=PROD_URL, trading_api_url=PROD_URL,
                              is_builtin=1, enabled=1)
db.add(definition)
db.commit()
owner = User(username="keyfix", password_hash="x", role="client", is_active=1,
             can_live=1, broker_name="Delta")
db.add(owner)
db.commit()
row = BrokerConnection(user_id=owner.id, broker_code="Delta", label="Delta main",
                       api_key="DEAD-KEY", api_secret="secret", is_active=1)
db.add(row)
db.commit()
db.refresh(row)

svc3 = make_dead_key_service()
svc3.user_id = owner.id
svc3.connection_id = row.id
stale_fingerprint = svc3.broker.key_fingerprint
svc3.broker.get_positions("BTCUSD")          # strike 1
svc3.broker.get_positions("BTCUSD")          # strike 2 → rejected
asyncio.run(svc3._hold_for_credentials())
check("the instance is degraded before the fix", svc3.credentials_state == "rejected",
      str(svc3.credentials_status())[:200])

row.api_key = GOOD_KEY      # what "Replace keys" writes
db.commit()
result = svc3.reload_credentials(force=True)
asyncio.run(svc3.credentials_recovered())
check("reload swaps in the key now saved on the connection",
      result["reloaded"] is True and result["verified"] is True, str(result)[:260])
check("the client is a different key now, and the latch is gone",
      svc3.broker.key_fingerprint != stale_fingerprint
      and svc3.broker.credential_health()["state"] == "ok",
      str(svc3.broker.credential_health()))
check("account identity follows the new key (shared-account queueing stays correct)",
      svc3.account_id == account_key("Delta", GOOD_KEY), str(svc3.account_id))
check("the deadman switch now signs with the new client",
      svc3.heartbeat is None or svc3.heartbeat.client is svc3.broker)
check("the reload is reported for the UI",
      svc3.credential_reloads == 1 and svc3.credentials_status()["reloads"] == 1,
      str(svc3.credentials_status())[:220])
check("the account panels work again on the reloaded client",
      isinstance(svc3.broker.get_account_balance(), list)
      or (isinstance(svc3.broker.get_account_balance(), dict)
          and not svc3.broker.get_account_balance().get("error")))

svc4 = make_dead_key_service()
svc4.user_id = owner.id
svc4.broker = delta_client("DEAD-KEY")
svc4.connection_id = None
adopted = svc4.reload_credentials(force=True)
check("an instance started before the connection existed adopts it (no restart)",
      adopted["reloaded"] is True and adopted["source"] == "connection"
      and svc4.connection_id == row.id, str(adopted)[:240])

row.is_active = 0
db.commit()
svc5 = make_dead_key_service()
svc5.user_id = owner.id
svc5.connection_id = row.id
switched_off = svc5.reload_credentials(force=True)
check("a switched-off connection is refused, with the reason, keeping the old client",
      switched_off["reloaded"] is False
      and "no usable key/secret" in str(switched_off["reason"]).lower()
      or "switched off" in str(switched_off["reason"]).lower()
      or "no complete key/secret" in str(switched_off["reason"]).lower(),
      str(switched_off)[:240])
row.is_active = 1
db.commit()

# Deleted connection: the instance must keep running on what it has.
orphan = make_dead_key_service()
orphan.user_id = owner.id
orphan.connection_id = 999999
gone = orphan.reload_credentials(force=True)
check("a deleted connection reports it instead of trading with nothing",
      gone["reloaded"] is False and "no longer exists" in str(gone["reason"]),
      str(gone)[:200])
check("saved_credentials never raises on a missing row",
      isinstance(broker_account.saved_credentials(owner.id, "Delta", 999999), dict))
check("broker-code spellings resolve (a seeded row may say 'Delta Exchange')",
      broker_account.broker_code_aliases("Delta")[:2] == ["Delta", "Delta Exchange"],
      str(broker_account.broker_code_aliases("Delta")))


# ===========================================================================
section("6. the API: probe, status, force reload, and keys reaching live runs")
# ===========================================================================
import bcrypt                                                          # noqa: E402
from fastapi.testclient import TestClient                              # noqa: E402
import app.main as main_module                                         # noqa: E402

owner.password_hash = bcrypt.hashpw(b"client12345", bcrypt.gensalt()).decode()
db.commit()

main_module.live_trade_instances.clear()
api = TestClient(main_module.app)
token = api.post("/token", data={"username": "keyfix", "password": "client12345"}).json()["access_token"]
H = {"Authorization": f"Bearer {token}"}

status_list = api.get("/live-trade/status", headers=H).json()
check("the status payload is a list (no instances yet)", isinstance(status_list, list))

# Register the degraded instance from section 5 so the endpoints have a target.
live_key = f"live_keyfix_Delta_{owner.id}_inst1"
main_module.live_trade_instances[live_key] = svc3
svc3.instance_key = live_key
svc3.user_id = owner.id
try:
    st = api.get("/live-trade/status", headers=H).json()[0]
    check("status carries the credential block for the terminal",
          "credentials" in st and {"state", "error", "entries_held", "retry_in_seconds",
                                   "connection_id", "reloads"} <= set(st["credentials"]),
          str(st.get("credentials"))[:220])
    check("a healthy instance reports ok, not an error",
          st["credentials"]["state"] == "ok", str(st["credentials"])[:160])
    check("status shows which connection the instance trades on",
          st.get("connection_id") == row.id, str(st.get("connection_id")))
except IndexError:
    check("status carries the credential block for the terminal", False, "instance not listed")
    check("a healthy instance reports ok, not an error", False)
    check("status shows which connection the instance trades on", False)

row.api_key = "DEAD-AGAIN"
db.commit()
svc3.broker = delta_client("DEAD-AGAIN")
svc3.credentials_state = "ok"
PROD.reset()
saved = api.put(f"/broker-connections/{row.id}", headers=H,
                json={"broker_code": "Delta", "label": "Delta main",
                      "api_key": f"  {GOOD_KEY}  ", "api_secret": "  s3cret\n",
                      "passphrase": "", "is_testnet": False, "is_active": True})
check("PUT accepts a key paste with stray whitespace", saved.status_code == 200, saved.text[:200])
body = saved.json()
check("pasted whitespace is trimmed before storing",
      body["api_key"].startswith("GOOD") and body["api_key"].endswith("KEY"), body["api_key"])
check("the save hands the new key to the running instance",
      (body.get("live_instances") or {}).get("verified") == 1
      and (body.get("live_instances") or {}).get("notified") == 1,
      str(body.get("live_instances"))[:240])
check("and the instance is trading again without a restart",
      svc3.credentials_status()["state"] == "ok"
      and svc3.broker.key_fingerprint == broker_account.credential_fingerprint(GOOD_KEY, "s3cret"),
      str(svc3.credentials_status())[:200])

blank = api.put(f"/broker-connections/{row.id}", headers=H,
                json={"broker_code": "Delta", "label": "Renamed", "api_key": "",
                      "api_secret": "", "passphrase": "", "is_testnet": False, "is_active": True})
db.refresh(row)
check("an edit that sends no secret keeps the stored one (the UI cannot read it back)",
      blank.status_code == 200 and row.api_secret == "s3cret" and row.api_key == GOOD_KEY,
      f"{row.api_key}/{row.api_secret}")

reloaded = api.post("/live-trade/reload-credentials", headers=H,
                    json={"instance_key": live_key})
check("the reload endpoint reports what it did",
      reloaded.status_code == 200 and reloaded.json()["verified"] is True
      and reloaded.json()["credentials"]["state"] == "ok", reloaded.text[:240])
other = api.post("/live-trade/reload-credentials",
                 headers={"Authorization": "Bearer nope"},
                 json={"instance_key": live_key})
check("another user's token cannot reload someone's instance", other.status_code == 401,
      str(other.status_code))
missing = api.post("/live-trade/reload-credentials", headers=H,
                   json={"instance_key": "live_keyfix_Delta_nope"})
check("an unknown instance is a 404, not a 500", missing.status_code == 404, str(missing.status_code))

# ---- Check key: which environment accepts this key? ---------------------
delta_key_probe.HOSTS = [{"name": "PRODUCTION", "url": PROD_URL, "testnet": False},
                         {"name": "TESTNET", "url": DEAD_URL, "testnet": True}]
prod_key_check = api.post(f"/broker-connections/{row.id}/probe", headers=H)
check("probe: the environment that accepts the key is named",
      prod_key_check.status_code == 200
      and prod_key_check.json()["environment"] == "production"
      and prod_key_check.json()["accepted_by"] == ["PRODUCTION"]
      and prod_key_check.json()["rejected_by"] == ["TESTNET"], prod_key_check.text[:280])
check("probe: a matching environment flag is confirmed, not questioned",
      prod_key_check.json()["matches_connection"] is True
      and "Reload keys" in prod_key_check.json()["fix"], str(prod_key_check.json()["fix"])[:200])

row.api_key = "TESTNET-ONLY-KEY"
row.api_secret = "sec"
row.is_testnet = 1
db.commit()
DEAD.accepting = "TESTNET-ONLY-KEY"
mismatch = api.post(f"/broker-connections/{row.id}/probe", headers=H).json()
check("probe: a testnet key on a testnet-flagged connection is not called a bad key",
      mismatch["environment"] == "testnet" and mismatch["matches_connection"] is True
      and mismatch["mismatch"] is False, str(mismatch)[:280])

row.is_testnet = 0
db.commit()
flip = api.post(f"/broker-connections/{row.id}/probe", headers=H).json()
check("probe: the toggle mismatch is named as the whole bug, with the fix",
      flip["mismatch"] is True and flip["matches_connection"] is False
      and "Flip the testnet toggle" in flip["fix"], str(flip)[:300])

row.api_key = "NEITHER-HOST-KNOWS-ME"
db.commit()
dead_both = api.post(f"/broker-connections/{row.id}/probe", headers=H).json()
check("probe: rejected everywhere says the key is dead, not the connection",
      dead_both["accepted_by"] == [] and len(dead_both["rejected_by"]) == 2
      and "Create a fresh key" in dead_both["fix"], str(dead_both)[:300])
check("probe: it never pretends a network problem is an auth verdict",
      dead_both["unreachable_from_here"] == [], str(dead_both)[:200])

no_creds = api.post("/broker-connections/999999/probe", headers=H)
check("probe on a connection you do not own is a 404", no_creds.status_code == 404,
      str(no_creds.status_code))

# ---- account settings error is not repeated per endpoint ----------------
settings = delta_client("DEAD-KEY").get_account_settings("BTCUSD")
check("a dead key on every account endpoint says it once, not twice",
      settings["error"].count("invalid_api_key") == 1
      and "every account endpoint answered the same way" in settings["error"],
      str(settings["error"])[:300])
check("the settings verdict still leaves the panels themselves empty, not wrong",
      settings["margin_mode"] is None and settings["accounts"] == [], str(settings)[:200])

PROD.accepting = "__nobody__"
held_snapshot = api.post("/live-account/snapshot", headers=H,
                         json={"broker": "Delta", "symbol": "BTCUSD"})
check("the terminal snapshot survives a dead key with its rate-limit view intact",
      held_snapshot.status_code == 200
      and "credential_health" in held_snapshot.json().get("rate_limits", {}),
      held_snapshot.text[:200])
PROD.accepting = GOOD_KEY

main_module.live_trade_instances.clear()
COORDINATOR._members.clear()
db.close()
PROD.stop()
DEAD.stop()

print(f"\n{'=' * 66}")
print(f"  PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
if FAIL:
    print("  failing: " + "; ".join(FAIL))
print(f"{'=' * 66}")
sys.exit(1 if FAIL else 0)
