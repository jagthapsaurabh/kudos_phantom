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
body = with_trail["body"]
check("entry bracket goes to POST /v2/orders (bracket_* on the entry order)",
      with_trail.get("path") == "/v2/orders", str(with_trail)[:200])
# Delta signs the trail against the CLOSING leg: a buy entry's stop-loss is a
# sell below the market, so the distance must travel as a negative number or
# the venue answers HTTP 400 bad_schema "bracket_trail_amount should be
# negative for buy orders" — the bug that blocked every trailing live entry.
check("trailing bracket sends bracket_trail_amount",
      body.get("bracket_trail_amount") == "-15.0", str(body))
check("trailing bracket omits bracket_stop_loss_price",
      "bracket_stop_loss_price" not in body, str(body))
check("take-profit is untouched",
      body.get("bracket_take_profit_price") == "200.0", str(body))

short_trail = client.place_bracket_order("BTCUSD", "sell", 10, stop_loss_price=300.0,
                                         take_profit_price=200.0, trail_amount=15.0,
                                         size_in_btc=False, dry_run=True)
check("a short entry's bracket trail is positive (stop sits above the market)",
      short_trail["body"].get("bracket_trail_amount") == "15.0", str(short_trail["body"]))
check("an already-signed trail distance is not double-negated",
      client.place_bracket_order("BTCUSD", "buy", 10, stop_loss_price=100.0,
                                 take_profit_price=200.0, trail_amount=-15.0,
                                 size_in_btc=False, dry_run=True)["body"].get("bracket_trail_amount")
      == "-15.0", "idempotent sign")
check("a zero trail falls back to the fixed stop instead of dropping protection",
      client.place_bracket_order("BTCUSD", "buy", 10, stop_loss_price=100.0,
                                 take_profit_price=200.0, trail_amount=0,
                                 size_in_btc=False, dry_run=True)["body"].get("bracket_stop_loss_price")
      == "100.0", "zero trail")

no_trail = client.place_bracket_order("BTCUSD", "buy", 10, stop_loss_price=100.0,
                                      take_profit_price=200.0, size_in_btc=False,
                                      dry_run=True)
body2 = no_trail["body"]
check("without a trail the fixed stop is sent",
      body2.get("bracket_stop_loss_price") == "100.0", str(body2))
check("without a trail there is no trail_amount",
      "bracket_trail_amount" not in body2, str(body2))

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
print("\n== honesty: intra-candle stops trigger; both-hit books the STOP ==", flush=True)
# A stop pierced INSIDE a candle used to be survived when the close recovered
# — profits a venue-side stop order would never have allowed. The engine now
# hands the candle's high/low to the OMS, and when both the stop and the
# target sit inside one bar the STOP fills (worst case, never the best).
from datetime import datetime as _dt
from app.services.order_manager import OrderManager
from app.core.strategy import PhantomV2Config as _Cfg

_oms = OrderManager(_Cfg())
_t = _oms.create_order("BTCUSDT", 1, 100000, atr_usd=1000, timestamp=_dt(2026, 8, 1),
                       margin_inr=25000, conversion_rate=85.0)
_sl, _tp = _t.sl, _t.tp
# Close recovers above the stop, but the LOW pierced it.
_r = _oms.update_trade("BTCUSDT", _sl + 500, _t.atr_at_entry, _dt(2026, 8, 2),
                       bar_high_usd=_sl + 600, bar_low_usd=_sl - 1)
check("a stop pierced inside the candle closes the trade",
      _r is not None and _r.exit_reason == "SL", getattr(_r, "exit_reason", None))
check("the stop fills at the stop level, not the friendlier close",
      _r is not None and abs(_r.exit_price - _sl) < 1e-9,
      f"{getattr(_r, 'exit_price', None)} vs {_sl}")

_oms2 = OrderManager(_Cfg())
_t2 = _oms2.create_order("BTCUSDT", 1, 100000, atr_usd=1000, timestamp=_dt(2026, 8, 1),
                         margin_inr=25000, conversion_rate=85.0)
# One violent candle spans BOTH the stop and the target.
_r2 = _oms2.update_trade("BTCUSDT", 100000, _t2.atr_at_entry, _dt(2026, 8, 2),
                         bar_high_usd=_t2.tp + 1, bar_low_usd=_t2.sl - 1)
check("when one candle spans SL and TP, the STOP is booked (never the best case)",
      _r2 is not None and _r2.exit_reason == "SL", getattr(_r2, "exit_reason", None))

_oms3 = OrderManager(_Cfg())
_t3 = _oms3.create_order("BTCUSDT", -1, 100000, atr_usd=1000, timestamp=_dt(2026, 8, 1),
                         margin_inr=25000, conversion_rate=85.0)
_r3 = _oms3.update_trade("BTCUSDT", _t3.sl - 500, _t3.atr_at_entry, _dt(2026, 8, 2),
                         bar_high_usd=_t3.sl + 1, bar_low_usd=_t3.sl - 600)
check("a short's stop pierced by the candle HIGH closes too",
      _r3 is not None and _r3.exit_reason == "SL", getattr(_r3, "exit_reason", None))

_oms4 = OrderManager(_Cfg())
_t4 = _oms4.create_order("BTCUSDT", 1, 100000, atr_usd=1000, timestamp=_dt(2026, 8, 1),
                         margin_inr=25000, conversion_rate=85.0)
_r4 = _oms4.update_trade("BTCUSDT", 100000, _t4.atr_at_entry, _dt(2026, 8, 2))
check("live/paper ticks without candle extremes behave exactly as before",
      _r4 is None and "BTCUSDT" in _oms4.active_trades)

# ---------------------------------------------------------------------------
print("\n== honesty: no NEW trades on stale candles (live and paper) ==", flush=True)
# The candle fetch falls back to seeded/stored data when the venue feed is
# down. A worker that keeps opening trades on that data manufactures results
# real trading can never see — live it would place REAL orders sized from a
# market that no longer exists.
import numpy as _np
import pandas as _pd
from datetime import timedelta as _tdelta
from app.services.live_trader import LiveTradeService
from app.services.paper_trader import PaperTradeService


class _AlwaysLong:
    def generate_signals(self, df_1h, df_4h):
        return _np.ones(len(df_1h), dtype=int)


def _frames(hours_old):
    end = _pd.Timestamp.utcnow().tz_localize(None).floor("h") - _tdelta(hours=hours_old)
    idx = _pd.date_range(end - _tdelta(hours=119), end, freq="1h")
    close = _np.full(len(idx), 60000.0)
    return _pd.DataFrame({"open": close, "high": close + 30, "low": close - 30,
                          "close": close, "volume": 10.0}, index=idx)


async def _held(self):
    # Test stand-in for the credentials gate: always hold, so a "fresh" live
    # tick proves the STALE gate let it through without touching a venue.
    return True


def _gated_worker(cls, hours_old):
    if cls is LiveTradeService:
        svc = cls("StaleTest", _Cfg(), "test-key", "test-secret",
                  initial_capital=20000, margin_pct=25, broker_name="Binance",
                  bracket_orders=False)
        svc._hold_for_credentials = _held.__get__(svc)
    else:
        svc = cls("StaleTest", _Cfg(), initial_capital=20000, margin_pct=25,
                  market_source="Binance")
    svc.strategy = _AlwaysLong()
    # A fresh mark price is available, so the stale gate exercises its deeper
    # path: manage open positions on the live mark, but still hold entries.
    from types import SimpleNamespace as _NS
    svc.use_mark_price = True
    svc._fetch_candles = lambda interval, limit: _frames(hours_old)
    svc._fetch_mark_price = lambda: _NS(mark_price=60000.0, last_price=60000.0)
    return svc


for cls, name in ((LiveTradeService, "live"), (PaperTradeService, "paper")):
    stale = _gated_worker(cls, hours_old=30)
    asyncio.run(stale.tick())
    check(f"{name}: 30h-old candles open NOTHING",
          not stale.oms.active_trades, str(list(stale.oms.active_trades)))
    check(f"{name}: the hold is visible, not silent",
          bool(stale.candles_stale) and "old" in str(stale.last_skip_reason or ""),
          str(stale.last_skip_reason))
    check(f"{name}: the skipped-entry counter moved",
          int(getattr(stale, "skipped_entries", 0)) >= 1,
          str(getattr(stale, "skipped_entries", None)))
    fresh = _gated_worker(cls, hours_old=0)
    asyncio.run(fresh.tick())
    check(f"{name}: fresh candles are not blocked by the gate",
          not fresh.candles_stale, str(fresh.last_skip_reason))

check("the stale gate names its threshold in the reason",
      "limit 3" in str(_gated_worker(LiveTradeService, 30)._candles_stale(
          _pd.Timestamp.utcnow().tz_localize(None) - _tdelta(hours=30))))

# ---------------------------------------------------------------------------
print("\n== honesty: paper candles come from the venue, never the DB ==", flush=True)
# The stored-candle fallback is gone entirely: a paper session either sees
# the venue's live feed or sees nothing and says so. Replaying seeded
# history while presenting itself as live is exactly the 'simulation'
# this platform must not do.
svc_nofb = PaperTradeService("NoFallback", _Cfg(), initial_capital=20000,
                             margin_pct=25, market_source="Binance")
check("the DB-candle reader is gone from the paper worker",
      not hasattr(svc_nofb, "_get_data_from_db"))


class _DeadVenue:
    def __init__(self, *a, **k):
        pass

    def fetch_klines(self, *a, **k):
        raise ConnectionError("venue unreachable")


_real_broker_client = pt.BrokerClient
pt.BrokerClient = _DeadVenue
try:
    got = svc_nofb._fetch_candles("1h", 50)
finally:
    pt.BrokerClient = _real_broker_client
check("a dead venue feed yields NO candles instead of stored history", got is None)

# ---------------------------------------------------------------------------
print("\n== admin-controlled USD→INR rate ==", flush=True)
from app.database.models import init_db
init_db()
import app.services.app_settings as app_settings
from app.services.app_settings import get_usd_inr_rate, set_usd_inr_rate, usd_inr_setting

_saved_env = os.environ.pop("USD_INR_RATE", None)
try:
    r, src, _ = usd_inr_setting()
    check("no admin value, no env -> built-in default", r == 85.0 and src == "default", f"{r} {src}")

    os.environ["USD_INR_RATE"] = "90.5"
    r, src, _ = usd_inr_setting()
    check("env var beats the default", r == 90.5 and src == "env", f"{r} {src}")

    set_usd_inr_rate(88.25)
    r, src, _ = usd_inr_setting()
    check("admin-saved value beats the env var", r == 88.25 and src == "admin", f"{r} {src}")

    for bad in (8.5, 8500, 0, -85, "abc"):
        try:
            set_usd_inr_rate(bad)
            check(f"insane rate {bad!r} rejected", False, "no error raised")
        except ValueError:
            check(f"insane rate {bad!r} rejected", True)

    svc_rate = PaperTradeService("RateTest", _Cfg(), initial_capital=20000,
                                 margin_pct=25, market_source="Binance")
    check("a new paper worker starts with the admin rate",
          svc_rate.conversion_rate == 88.25, str(svc_rate.conversion_rate))

    import app.main as main_mod
    main_mod.paper_trade_instances["_rate_test"] = svc_rate
    try:
        out = main_mod.update_usd_inr(main_mod.UsdInrPayload(rate=86.0), admin=None)
        check("admin endpoint saves and reports the rate", out["rate"] == 86.0, str(out))
        check("admin endpoint updates RUNNING sessions",
              svc_rate.conversion_rate == 86.0 and out["applied_to_running"] >= 1,
              f"{svc_rate.conversion_rate} / {out.get('applied_to_running')}")
        got = main_mod.get_usd_inr(admin=None)
        check("admin endpoint reads back the saved rate",
              got["rate"] == 86.0 and got["source"] == "admin", str(got))
    finally:
        main_mod.paper_trade_instances.pop("_rate_test", None)
finally:
    if _saved_env is not None:
        os.environ["USD_INR_RATE"] = _saved_env
    else:
        os.environ.pop("USD_INR_RATE", None)

# ---------------------------------------------------------------------------
print("\n" + "=" * 62)
print(f"  PASSED: {PASS}   FAILED: {len(FAIL)}")
if FAIL:
    for f in FAIL:
        print(f"    - {f}")
print("=" * 62)
sys.exit(1 if FAIL else 0)
