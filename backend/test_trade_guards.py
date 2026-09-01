"""Tests for the paper/live fixes: crash-proof paper loop, resume, one
strategy per account, Delta as the default broker, and the Delta bracket
stop-loss leg that Delta was rejecting.

Runs fully offline against a temp SQLite DB — no network, no exchange.
"""
import asyncio
import os
import sys

sys.path.insert(0, '.')

TESTDB = "/tmp/trade_guards_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"

PASS, FAIL = 0, []


def check(label, ok, detail=""):
    global PASS
    if ok:
        PASS += 1
        print(f"  PASS: {label}")
    else:
        FAIL.append(label)
        print(f"  FAIL: {label} {detail}")


# ---------------------------------------------------------------------------
print("\n== paper_trader imports the modules its loop uses ==", flush=True)
# The original bug: `time.monotonic()` in the live-tick loop with no
# `import time`, so every feed-enabled paper session died on its first
# iteration and later surfaced as "Interrupted".
import app.services.paper_trader as pt

check("paper_trader has `time` bound", hasattr(pt, "time"))
check("paper_trader has `traceback` bound", hasattr(pt, "traceback"))
src = open("app/services/paper_trader.py").read()
for name in ("time.monotonic", "traceback.format_exc"):
    mod = name.split(".")[0]
    check(f"{name} is backed by `import {mod}`",
          (name not in src) or (f"import {mod}" in src))

# ---------------------------------------------------------------------------
print("\n== the paper loop survives a crashing tick ==", flush=True)
from app.core.strategy import PhantomV2Config

svc = pt.PaperTradeService("PhantomV2", PhantomV2Config(), initial_capital=20000.0)
svc.MAX_RESTARTS = 2
calls = {"n": 0}


async def boom():
    calls["n"] += 1
    raise RuntimeError("synthetic tick failure")


async def drive():
    svc.tick = boom
    # A loop body that raises outside the per-tick guard must be supervised,
    # not fatal. Patch _run_loop to raise straight away.
    async def exploding_loop():
        raise RuntimeError("synthetic loop failure")
    svc._run_loop = exploding_loop
    await asyncio.wait_for(svc.start(), timeout=30)


asyncio.run(drive())
check("supervisor stopped after MAX_RESTARTS", svc.is_running is False)
check("restarts were counted", svc.restarts >= 2, str(svc.restarts))
check("the failure reason is recorded", "synthetic loop failure" in str(svc.last_error),
      str(svc.last_error))
check("stop_reason explains the end", "consecutive failures" in str(svc.stop_reason),
      str(svc.stop_reason))

# ---------------------------------------------------------------------------
print("\n== a normal stop is not a failure ==", flush=True)
svc2 = pt.PaperTradeService("PhantomV2", PhantomV2Config(), initial_capital=20000.0)


async def stop_cleanly():
    async def quiet_loop():
        while svc2.is_running:
            await asyncio.sleep(0.01)
    svc2._run_loop = quiet_loop
    task = asyncio.create_task(svc2.start())
    await asyncio.sleep(0.05)
    await svc2.stop()
    await asyncio.wait_for(task, timeout=10)


asyncio.run(stop_cleanly())
check("clean stop leaves no error", svc2.last_error is None, str(svc2.last_error))
check("clean stop records the user reason", svc2.stop_reason == "stopped by user",
      str(svc2.stop_reason))
check("clean stop did not count a restart", svc2.restarts == 0, str(svc2.restarts))

# ---------------------------------------------------------------------------
print("\n== a dead price feed degrades instead of ending the session ==", flush=True)
svc3 = pt.PaperTradeService("PhantomV2", PhantomV2Config(), initial_capital=20000.0,
                            price_feed="websocket", tick_interval=1.0)
ticks = {"n": 0}


async def feed_fallback():
    import app.services.tick_feed as tf

    def broken(*a, **k):
        raise RuntimeError("socket refused")
    original = tf.build_tick_feed
    tf.build_tick_feed = broken

    async def counting_tick():
        ticks["n"] += 1
        svc3.is_running = False
    svc3.tick = counting_tick
    svc3.is_running = True   # start() normally sets this
    try:
        await asyncio.wait_for(svc3._run_with_feed(), timeout=15)
    finally:
        tf.build_tick_feed = original


asyncio.run(feed_fallback())
check("fell back to the candle cadence", svc3.price_feed_mode == "off", svc3.price_feed_mode)
check("still traded after the feed failed", ticks["n"] >= 1, str(ticks["n"]))
check("the feed failure is reported", "price feed unavailable" in str(svc3.last_error),
      str(svc3.last_error))

# ---------------------------------------------------------------------------
print("\n== Delta bracket stop-loss: stop_price XOR trail_amount ==", flush=True)
# Delta rejects a bracket SL leg carrying both:
#   "Only stop_price or trail_amount should be specified for bracket stop
#    loss order"  -> every entry on a trailing strategy failed.
from app.services.broker_client import BrokerClient

client = BrokerClient("k", "s", "Delta")
client._instrument_cache["BTCUSD"] = {"product_id": 27, "contract_value": 0.001}

with_trail = client.place_bracket_order("BTCUSD", "buy", 10, stop_loss_price=100.0,
                                        take_profit_price=200.0, trail_amount=15.0,
                                        size_in_btc=False, dry_run=True)
sl = with_trail["body"]["stop_loss_order"]
check("trailing leg sends trail_amount", sl.get("trail_amount") == "15.0", str(sl))
check("trailing leg omits stop_price", "stop_price" not in sl, str(sl))
check("take-profit leg is untouched",
      with_trail["body"]["take_profit_order"].get("stop_price") == "200.0", str(with_trail["body"]))

no_trail = client.place_bracket_order("BTCUSD", "buy", 10, stop_loss_price=100.0,
                                      take_profit_price=200.0, size_in_btc=False,
                                      dry_run=True)
sl2 = no_trail["body"]["stop_loss_order"]
check("without a trail the fixed stop is sent", sl2.get("stop_price") == "100.0", str(sl2))
check("without a trail there is no trail_amount", "trail_amount" not in sl2, str(sl2))

# ---------------------------------------------------------------------------
print("\n== Delta is the default broker ==", flush=True)
import app.main as main

check("DEFAULT_BROKER is Delta", main.DEFAULT_BROKER == "Delta")
check("no source resolves to Delta", main.normalize_source(None) == "Delta")
check("empty source resolves to Delta", main.normalize_source("") == "Delta")
check("an explicit broker still wins", main.normalize_source("binance") == "Binance")
check("TradeStartRequest defaults to Delta",
      main.TradeStartRequest(strategy_id="PhantomV2").broker_name == "Delta")

# ---------------------------------------------------------------------------
print("\n== one strategy per (strategy + account) ==", flush=True)


class FakeUser:
    id = 1
    username = "admin"


class FakeConn:
    def __init__(self, cid, label):
        self.id, self.label = cid, label


user = FakeUser()
conn_a, conn_b = FakeConn(2, "NishKudos delta exchange"), FakeConn(3, "Saurabh Test")


class FakeService:
    def __init__(self, strategy_id, connection_id, running=True):
        self.strategy_id = strategy_id
        self.strategy_name = f"Strategy {strategy_id}"
        self.connection_id = connection_id
        self.broker_name = "Delta"
        self.market_source = "Delta"
        self.is_running = running
        self.user_id = 1


main.live_trade_instances.clear()
main.live_trade_instances["live_admin_Delta_9_fc5f3cdd"] = FakeService("9", 2)

hit = main.running_conflict("live", user, "9", "Delta", conn_a)
check("same strategy on the SAME account is blocked", hit is not None)
check("the blocking instance is named", hit and hit[0] == "live_admin_Delta_9_fc5f3cdd")
detail = main._conflict_detail("live", hit[0], hit[1], conn_a)
check("the message names the account", "NishKudos delta exchange" in detail, detail)
check("the message says what to do", "stop the existing instance" in detail.lower(), detail)

check("same strategy on a DIFFERENT account is allowed",
      main.running_conflict("live", user, "9", "Delta", conn_b) is None)
check("a different strategy on the same account is allowed",
      main.running_conflict("live", user, "FastTest", "Delta", conn_a) is None)
check("paper and live are tracked separately",
      main.running_conflict("paper", user, "9", "Delta", conn_a) is None)

main.live_trade_instances["live_admin_Delta_9_fc5f3cdd"].is_running = False
check("a stopped instance no longer blocks",
      main.running_conflict("live", user, "9", "Delta", conn_a) is None)

# The exact duplicate from the reported status dump: strategy 9 twice on
# connection 2. The second one must never have been allowed to start.
main.live_trade_instances.clear()
main.live_trade_instances["live_admin_Delta_9_fc5f3cdd"] = FakeService("9", 2)
check("the reported duplicate (strategy 9 twice on connection 2) is refused",
      main.running_conflict("live", user, "9", "Delta", conn_a) is not None)
main.live_trade_instances.clear()

# ---------------------------------------------------------------------------
print("\n== paper sessions carry account + resume metadata ==", flush=True)
from app.database.models import init_db, SessionLocal, PaperSession
from app.services import paper_history

init_db()
svc4 = pt.PaperTradeService("9", PhantomV2Config(), initial_capital=20000.0,
                            market_source="Delta", broker_name="Delta",
                            connection_id=2, account_label="NishKudos delta exchange",
                            leverage=7, price_feed="websocket", tick_interval=5.0)
check("leverage override lands on the config", svc4.config.leverage == 7,
      str(svc4.config.leverage))
check("the account is recorded", svc4.account_label == "NishKudos delta exchange")
check("the connection is recorded", svc4.connection_id == 2)

svc4.instance_key = "paper_admin_Delta_9_test1234"
svc4.user_id = 1
svc4.stop_reason = "stopped by user"
svc4.last_error = "something went wrong earlier"
sid = paper_history.start_session(1, svc4.instance_key, svc4)
check("session row created", sid is not None)

db = SessionLocal()
row = db.query(PaperSession).filter(PaperSession.id == sid).first()
check("account_label persisted", row.account_label == "NishKudos delta exchange")
check("connection_id persisted", row.connection_id == 2)
check("price_feed persisted", row.price_feed == "websocket")
check("stop_reason persisted", row.stop_reason == "stopped by user")
check("last_error persisted", row.last_error == "something went wrong earlier")
check("auto_resume defaults on", bool(row.auto_resume))
db.close()

spec = next((r for r in paper_history.resumable_sessions()
             if r["instance_key"] == svc4.instance_key), None)
# It is still 'running', so it is not resumable until a restart flags it.
check("a running session is not in the resume list", spec is None)
paper_history.mark_interrupted_sessions()
spec = next((r for r in paper_history.resumable_sessions()
             if r["instance_key"] == svc4.instance_key), None)
check("after a restart it becomes resumable", spec is not None)
check("the spec carries the broker", spec and spec["data_source"] == "Delta", str(spec)[:200])
check("the spec carries the connection", spec and spec["connection_id"] == 2)
check("the spec carries the feed", spec and spec["price_feed"] == "websocket")

# ---------------------------------------------------------------------------
print("\n" + "=" * 62)
print(f"  PASSED: {PASS}   FAILED: {len(FAIL)}")
if FAIL:
    for f in FAIL:
        print(f"    - {f}")
print("=" * 62)
sys.exit(1 if FAIL else 0)
