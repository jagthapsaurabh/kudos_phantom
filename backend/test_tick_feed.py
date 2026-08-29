"""Live price feeds and the fast exit path.

Before this, the live worker woke every 60 seconds, re-read the candles and
re-checked the stop-loss. A position could run past its stop for up to a full
minute before the worker noticed — and that minute is exactly when a stop
matters. These tests cover the feeds that close that gap, and the narrow fast
tick that consumes them.

Everything runs against a local websocket server and a fake venue, so no real
exchange and no credentials are needed. The real ``WebSocketTickFeed`` code
path is exercised end to end — connect, parse, drop, reconnect — against a
socket on 127.0.0.1.

1. venue message parsing (Binance mark price + book ticker, Delta ticker)
2. messages that carry no usable price are ignored, not guessed at
3. a stale price is never handed to the trader
4. a dropped socket reconnects with backoff instead of killing the worker
5. the REST feed polls and degrades gracefully
6. ``build_tick_feed`` never raises on an unsupported venue
7. ``fast_tick`` re-marks exits without fetching candles or opening entries
8. ``fast_tick`` does not move the candle clock
9. the API rejects a bad feed mode rather than silently downgrading

Run:  cd backend && ../.venv/bin/python test_tick_feed.py
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, '.')

TESTDB = "/tmp/tick_feed_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"

from app.core.mark_price import MarkPriceQuote                       # noqa: E402
from app.core.strategy import PhantomV2Config                        # noqa: E402
from app.services.live_trader import LiveTradeService                # noqa: E402
from app.services.tick_feed import (NullTickFeed, RestTickFeed,      # noqa: E402
                                    WebSocketTickFeed, build_tick_feed,
                                    delta_subscribe, parse_binance, parse_delta)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not cond else ""))


def section(title):
    print(f"\n{'=' * 62}\n  {title}\n{'=' * 62}")


# ===========================================================================
section("1. venue message parsing")
# ===========================================================================
quote = parse_binance({"e": "markPriceUpdate", "s": "BTCUSDT", "p": "60123.40", "i": "60120.00"})
check("binance mark price is read", quote is not None and quote.mark_price == 60123.40, quote)
check("binance index price is read alongside", quote.index_price == 60120.00)
check("the risk basis is the mark price", quote.basis_price == 60123.40)

book = parse_binance({"e": "bookTicker", "s": "BTCUSDT", "b": "60000.00", "a": "60010.00"})
check("binance book ticker is taken at the mid",
      book is not None and book.mark_price == 60005.0, book and book.mark_price)
check("a one-sided book is not guessed at",
      parse_binance({"e": "bookTicker", "s": "BTCUSDT", "b": "60000.00"}) is None)

delta = parse_delta({"type": "ticker", "symbol": "BTCUSD",
                     "mark_price": "60100.50", "last_price": "60101.00"})
check("delta ticker reads mark and last",
      delta is not None and delta.mark_price == 60100.50 and delta.last_price == 60101.00)
check("delta falls back to close when last is absent",
      parse_delta({"type": "ticker", "symbol": "BTCUSD",
                   "close": "59999.0"}).basis_price == 59999.0)
check("a delta price of zero is treated as missing",
      parse_delta({"type": "ticker", "symbol": "BTCUSD", "mark_price": "0"}) is None)

# ===========================================================================
section("2. messages with no usable price are ignored")
# ===========================================================================
check("a delta subscription ack is ignored", parse_delta({"type": "subscriptions"}) is None)
check("a binance depth update is ignored",
      parse_binance({"e": "depthUpdate", "s": "BTCUSDT", "b": [], "a": []}) is None)
check("an execution report is ignored", parse_binance({"e": "ORDER_TRADE_UPDATE"}) is None)
check("a non-dict frame is ignored", parse_binance("nope") is None and parse_delta(None) is None)
check("a list frame is ignored by the parser", parse_binance([1, 2, 3]) is None)
check("a malformed price string is ignored",
      parse_binance({"e": "markPriceUpdate", "s": "BTCUSDT", "p": "n/a"}) is None)

# ===========================================================================
section("3. a stale price is never handed to the trader")
# ===========================================================================
feed = NullTickFeed("Binance", "BTCUSDT", max_age=0.05)
check("a fresh feed has no price yet", feed.quote() is None)
feed.publish(MarkPriceQuote("Binance", "BTCUSDT", mark_price=60000.0))
check("a published price is returned", feed.quote() is not None)
check("the age starts near zero", (feed.age() or 99) < 1.0)
check("stats report a live price", feed.stats()["stale"] is False)
time.sleep(0.08)
check("the same price is refused once stale", feed.quote() is None)
check("stats report it as stale", feed.stats()["stale"] is True)
check("the age keeps counting while stale", (feed.age() or 0) >= 0.05)

empty = NullTickFeed("Binance", "BTCUSDT")
check("publishing nothing usable returns False",
      empty.publish(None) is False and empty.publish(
          MarkPriceQuote("Binance", "BTCUSDT")) is False)
check("an empty feed reports no age", empty.age() is None)
check("an empty feed reports no messages", empty.stats()["messages"] == 0)


# ===========================================================================
section("4. a dropped socket reconnects instead of killing the worker")
# ===========================================================================
def run_socket_case(frames_per_connection, hold, connections_to_serve=2):
    """Serve a stream that closes after ``frames_per_connection`` messages."""
    from websockets.asyncio.server import serve
    served = {"n": 0}

    async def handler(ws):
        served["n"] += 1
        for i in range(frames_per_connection):
            await ws.send(json.dumps({"e": "markPriceUpdate", "s": "BTCUSDT",
                                      "p": str(60000 + served["n"] * 100 + i)}))
            await asyncio.sleep(0.01)
        await asyncio.sleep(hold)
        await ws.close()

    async def main():
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = WebSocketTickFeed(
                f"ws://127.0.0.1:{port}", "Binance", "BTCUSDT", parse_binance,
                max_age=30.0, backoff_cap=0.05)
            await client.start()
            # Wait for the first frames instead of guessing a duration — the
            # server closes shortly after sending them, so a fixed sleep races
            # the reconnect and samples `connected` at an arbitrary moment.
            for _ in range(100):
                if client.messages >= 3:
                    break
                await asyncio.sleep(0.01)
            first = client.quote()
            was_connected = client.connected
            # Let the server drop and the client come back.
            await asyncio.sleep(0.8)
            after = client.quote()
            stats = client.stats()
            await client.stop()
            return first, was_connected, after, stats, client

    return asyncio.run(main())


first, was_connected, after, stats, client = run_socket_case(3, 0.05)
check("the feed connected to the stream", was_connected is True)
check("prices arrived over the socket", stats["messages"] >= 3, stats["messages"])
check("the parsed price is usable", first is not None and first.mark_price > 0)
check("a dropped socket is survived", client.last_error is not None or stats["reconnects"] >= 1,
      stats)
check("the client reconnected and kept receiving",
      stats["reconnects"] >= 1 and stats["messages"] > 3, stats)
check("a price is still available after the drop", after is not None)
check("stats kind is websocket", stats["kind"] == "websocket")
check("stopping leaves the feed disconnected", client.connected is False)


async def _rejecting_socket():
    """A feed pointed at a port nothing listens on must not raise."""
    client = WebSocketTickFeed("ws://127.0.0.1:1/nope", "Binance", "BTCUSDT",
                               parse_binance, backoff_cap=0.05)
    await client.start()
    await asyncio.sleep(0.2)
    stats = client.stats()
    await client.stop()
    return stats


refused = asyncio.run(_rejecting_socket())
check("a refused connection is recorded, not raised",
      refused["last_error"] is not None and refused["messages"] == 0, refused)
check("a refused connection retries", refused["reconnects"] >= 1, refused)
check("a refused connection reports disconnected", refused["connected"] is False)


def _binary_and_batch():
    """Binary frames and batched frames must both parse."""
    from websockets.asyncio.server import serve

    async def handler(ws):
        await ws.send(json.dumps({"e": "markPriceUpdate", "s": "BTCUSDT",
                                  "p": "61000.0"}).encode("utf-8"))
        await ws.send(json.dumps([{"e": "markPriceUpdate", "s": "BTCUSDT", "p": "61100.0"},
                                  {"e": "markPriceUpdate", "s": "BTCUSDT", "p": "61200.0"}]))
        await ws.send("this is not json")
        await asyncio.sleep(0.3)

    async def main():
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = WebSocketTickFeed(f"ws://127.0.0.1:{port}", "Binance", "BTCUSDT",
                                       parse_binance, max_age=30.0)
            await client.start()
            await asyncio.sleep(0.25)
            got = client.quote()
            stats = client.stats()
            await client.stop()
            return got, stats

    return asyncio.run(main())


batched, batch_stats = _binary_and_batch()
check("a binary frame is decoded", batched is not None)
check("a batched frame yields every price in it",
      batch_stats["messages"] == 3, batch_stats["messages"])
check("the newest price in a batch wins",
      batched is not None and batched.mark_price == 61200.0, batched and batched.mark_price)

# ===========================================================================
section("5. the REST feed polls and degrades")
# ===========================================================================
calls = {"n": 0}


def _good_quote():
    calls["n"] += 1
    return MarkPriceQuote("Binance", "BTCUSDT", mark_price=60000.0 + calls["n"])


def _rest_case(fetch, interval=0.05, wait=0.2):
    async def main():
        client = RestTickFeed(fetch, "Binance", "BTCUSDT", interval=interval, max_age=30.0)
        await client.start()
        await asyncio.sleep(wait)
        got = client.quote()
        stats = client.stats()
        await client.stop()
        return got, stats, client

    return asyncio.run(main())


rest_quote, rest_stats, rest_client = _rest_case(_good_quote)
check("the REST feed polled more than once", rest_stats["messages"] >= 2, rest_stats)
check("the REST feed has a price", rest_quote is not None and rest_quote.mark_price > 0)
check("the REST feed reports its kind", rest_stats["kind"] == "rest")
check("the REST feed reports connected while fresh", rest_client.connected is True)


def _exploding_quote():
    raise RuntimeError("venue unreachable")


bad_quote, bad_stats, _ = _rest_case(_exploding_quote)
check("a polling failure is recorded, not raised",
      bad_stats["last_error"] is not None and "venue unreachable" in bad_stats["last_error"],
      bad_stats)
check("a failing REST feed has no price", bad_quote is None)


def _none_quote():
    return None


none_quote, none_stats, _ = _rest_case(_none_quote)
check("a None quote is not published",
      none_stats["messages"] == 0 and none_quote is None, none_stats)

short = RestTickFeed(_good_quote, "Binance", "BTCUSDT", interval=10.0, max_age=1.0)
check("a polled price outlives at least one poll interval",
      short.max_age >= 10.0, short.max_age)

# ===========================================================================
section("6. build_tick_feed never raises on an odd venue")
# ===========================================================================
class FakeClient:
    def fetch_mark_price(self, symbol):
        return MarkPriceQuote("Binance", symbol, mark_price=60000.0)


class BinanceDef:
    kind = "binance"


class DeltaDef:
    kind = "delta"


class GenericDef:
    kind = "generic"


ws_feed = build_tick_feed("websocket", "Binance", "BTCUSDT", BinanceDef(), client=FakeClient())
check("binance websocket is built", ws_feed.kind == "websocket")
check("the binance stream URL is per-symbol lowercase",
      "btcusdt" in ws_feed.url.lower() and ws_feed.url.startswith("wss://"), ws_feed.url)
check("binance needs no subscribe frame", ws_feed.subscribe is None)

delta_feed = build_tick_feed("websocket", "Delta", "BTCUSD", DeltaDef(), client=FakeClient())
check("delta websocket is built", delta_feed.kind == "websocket")
check("delta subscribes to ticker (changelog 17.04.26, not v2/ticker)",
      isinstance(delta_feed.subscribe, dict)
      and delta_feed.subscribe["payload"]["channels"][0]["name"] == "ticker",
      delta_feed.subscribe)
check("delta_subscribe is a well-formed frame",
      delta_subscribe("BTCUSD")["type"] == "subscribe")

rest_built = build_tick_feed("rest", "Binance", "BTCUSDT", BinanceDef(), client=FakeClient())
check("rest mode builds a polling feed", rest_built.kind == "rest")
check("a websocket request with no client falls back to rest",
      build_tick_feed("websocket", "Binance", "BTCUSDT", GenericDef(),
                      client=FakeClient()).kind == "rest")
check("off mode builds nothing", build_tick_feed("off", "Binance", "BTCUSDT",
                                                 BinanceDef(), client=FakeClient()).kind == "none")
check("an unsupported venue with no client degrades to none",
      build_tick_feed("websocket", "Unknown", "X", GenericDef()).kind == "none")
check("a missing definition still resolves a venue",
      build_tick_feed("websocket", "Binance", "BTCUSDT", None,
                      client=FakeClient()).kind == "websocket")
check("the perpetual symbol is used, not the spot symbol",
      build_tick_feed("websocket", "Delta", "BTCUSDT", DeltaDef(),
                      perpetual="BTCUSD", client=FakeClient()).symbol == "BTCUSD")

# ===========================================================================
section("7. fast_tick re-marks exits without touching candles or entries")
# ===========================================================================
class FakeBroker:
    def __init__(self):
        self.orders = []
        self.resting = []
        self.position = 0.0
        self.cancel_all_calls = []
        self.kline_calls = 0

    def bind(self, owner):
        self.owner = owner
        return self

    def perpetual_symbol(self, symbol="BTCUSDT"):
        return symbol

    def get_instrument(self, symbol="BTCUSDT", refresh=False):
        return {"contract_value": 1.0, "step_size": 0.001}

    def get_positions(self, symbol=None):
        if not self.position:
            return []
        return [{"positionAmt": str(self.position), "entryPrice": "60000"}]

    def fetch_klines(self, symbol, interval, limit):
        self.kline_calls += 1
        return []

    def place_order(self, symbol, side, order_type, qty, price=None, stop_price=None,
                    reduce_only=False, size_in_btc=True, **kw):
        signed = float(qty) if str(side).upper() == "BUY" else -float(qty)
        if reduce_only and (self.position == 0 or signed * self.position > 0):
            return {"error": "ReduceOnly Order is rejected (-2022)"}
        self.orders.append((str(side).upper(), float(qty), "reduce_only" if reduce_only else "opening"))
        self.position += signed
        return {"orderId": len(self.orders), "avgPrice": "60000"}

    def place_bracket_order(self, symbol, side, qty, **kw):
        entry = self.place_order(symbol, side, "market", qty)
        if isinstance(entry, dict) and entry.get("error"):
            return entry
        legs = []
        for kind, otype in (("stop_loss", "stop_market"), ("take_profit", "take_profit_market")):
            oid = f"leg-{kind}-{len(self.orders)}"
            self.resting.append({"orderId": oid, "owner": self.owner})
            legs.append({"orderId": oid, "type": otype})
        entry["_bracket"] = True
        entry["legs"] = legs
        return entry

    def cancel_order(self, order_id, symbol=None, client_order_id=None):
        self.resting = [o for o in self.resting if o["orderId"] != order_id]
        return {"orderId": order_id}

    def cancel_all_orders(self, symbol=None):
        self.cancel_all_calls.append(self.owner)
        self.resting = []
        return {"cancelled": True}

    def get_fills(self, symbol=None, limit=10):
        return []


class PersistentSignal:
    def __init__(self, direction=1):
        self.direction = direction

    def generate_signals(self, df_1h, df_4h):
        signals = np.zeros(len(df_1h))
        signals[-1] = self.direction
        return signals


BASE = datetime(2024, 1, 1)


def candles(bars=120, last_bar=0):
    rng = np.random.RandomState(7)
    close = 60000 + np.cumsum(rng.randn(bars) * 10)
    idx = pd.date_range(BASE, periods=bars, freq="1h") + timedelta(hours=last_bar)
    return pd.DataFrame({"open": close, "high": close + 30, "low": close - 30,
                         "close": close, "volume": 100.0}, index=idx)


def make_worker(broker, direction=1):
    svc = LiveTradeService("Ticker", [], "key", "secret", is_custom=True,
                           initial_capital=20000, margin_pct=25, broker_name="Binance")
    svc.config = PhantomV2Config()
    svc.oms.config = svc.config
    svc.strategy = PersistentSignal(direction)
    svc.use_mark_price = False
    svc.state = {"bar": 0}
    svc.broker = broker.bind("Ticker")
    svc.instance_key = "live_client_Binance_Ticker_inst"
    # Count at the layer the worker actually calls. Counting broker.fetch_klines
    # would pass vacuously, because this override never reaches the broker.
    svc.candle_fetches = 0

    def _candles(interval, limit):
        svc.candle_fetches += 1
        return candles()

    svc._fetch_candles = _candles
    svc._fetch_mark_price = lambda: None
    return svc


broker = FakeBroker()
worker = make_worker(broker)
asyncio.run(worker.tick())                      # open a position on the slow tick
check("the slow tick opened a position", "BTCUSDT" in worker.oms.active_trades)
check("the slow tick cached the ATR for fast ticks", worker._last_atr is not None)
check("the slow tick cached the candle time", worker._last_bar_time is not None)
fetches_after_entry = worker.candle_fetches
orders_after_entry = len(broker.orders)
bars_after_entry = worker.oms.active_trades["BTCUSDT"].bars_held

# A feed with no price at all must be a no-op.
worker.tick_feed = NullTickFeed("Binance", "BTCUSDT")
asyncio.run(worker.fast_tick())
check("a fast tick with no price does nothing",
      len(broker.orders) == orders_after_entry and worker.fast_ticks == 0)

# A fresh price above the stop must not close anything either.
calm = NullTickFeed("Binance", "BTCUSDT", max_age=30.0)
calm.publish(MarkPriceQuote("Binance", "BTCUSDT", mark_price=60000.0, last_price=60000.0))
worker.tick_feed = calm
asyncio.run(worker.fast_tick())
check("a fast tick ran", worker.fast_ticks == 1, worker.fast_ticks)
check("a calm price does not close the position",
      "BTCUSDT" in worker.oms.active_trades and len(broker.orders) == orders_after_entry)
check("a fast tick never fetches candles",
      worker.candle_fetches == fetches_after_entry, worker.candle_fetches)
check("a fast tick never opens a new entry",
      len([o for o in broker.orders if o[2] == "opening"]) == 1, broker.orders)

# A stale price must be ignored even though one is present.
stale_feed = NullTickFeed("Binance", "BTCUSDT", max_age=0.01)
stale_feed.publish(MarkPriceQuote("Binance", "BTCUSDT", mark_price=1.0))
time.sleep(0.03)
worker.tick_feed = stale_feed
ticks_before = worker.fast_ticks
asyncio.run(worker.fast_tick())
check("a stale price is not traded on", worker.fast_ticks == ticks_before, worker.fast_ticks)

# Now blow through the stop on the fast tick.
crash = NullTickFeed("Binance", "BTCUSDT", max_age=30.0)
crash.publish(MarkPriceQuote("Binance", "BTCUSDT", mark_price=100.0, last_price=100.0))
worker.tick_feed = crash
asyncio.run(worker.fast_tick())
check("a breached stop closes on the fast tick",
      "BTCUSDT" not in worker.oms.active_trades, list(worker.oms.active_trades))
check("the exit order was reduce-only",
      [o for o in broker.orders if o[2] == "reduce_only"], broker.orders)
check("the account is flat after the fast-tick exit", broker.position == 0.0, broker.position)
check("the exit used no candle fetch", worker.candle_fetches == fetches_after_entry)

# ===========================================================================
section("8. fast_tick does not move the candle clock")
# ===========================================================================
broker2 = FakeBroker()
worker2 = make_worker(broker2)
asyncio.run(worker2.tick())
bars_before = worker2.oms.active_trades["BTCUSDT"].bars_held
live = NullTickFeed("Binance", "BTCUSDT", max_age=30.0)
live.publish(MarkPriceQuote("Binance", "BTCUSDT", mark_price=60000.0))
worker2.tick_feed = live
for _ in range(10):
    asyncio.run(worker2.fast_tick())
bars_after = worker2.oms.active_trades["BTCUSDT"].bars_held
check("ten fast ticks advanced the holding clock zero candles",
      bars_after == bars_before, (bars_before, bars_after))
check("ten fast ticks were all counted", worker2.fast_ticks == 10, worker2.fast_ticks)
# One slow tick fetches the 1h and the 4h frame, so exactly two.
check("the fast ticks did not fetch candles", worker2.candle_fetches == 2, worker2.candle_fetches)

# A worker with no feed configured must not crash on a fast tick.
lonely = make_worker(FakeBroker())
asyncio.run(lonely.fast_tick())
check("a fast tick with no feed at all is a no-op", lonely.fast_ticks == 0)

# ===========================================================================
section("9. the API rejects a bad feed mode")
# ===========================================================================
from app.main import _resolve_price_feed, MAX_TICK_INTERVAL, MIN_TICK_INTERVAL  # noqa: E402


class Payload:
    def __init__(self, mode=None, interval=None):
        self.price_feed = mode
        self.tick_interval = interval


check("omitting price_feed means off",
      _resolve_price_feed(Payload())[0] == "off")
check("off keeps a usable default interval",
      _resolve_price_feed(Payload())[1] == 5.0)
check("websocket is accepted", _resolve_price_feed(Payload("websocket", 2))[0] == "websocket")
check("rest is accepted", _resolve_price_feed(Payload("REST", 10))[0] == "rest")
check("the mode is normalised to lower case",
      _resolve_price_feed(Payload("  WebSocket  ", 3))[0] == "websocket")

for bad_mode in ("carrier-pigeon", "socket"):
    try:
        _resolve_price_feed(Payload(bad_mode, 5))
        check(f"mode {bad_mode!r} is rejected", False, "was accepted")
    except Exception as exc:
        check(f"mode {bad_mode!r} is rejected",
              "price_feed must be one of" in str(getattr(exc, "detail", exc)),
              getattr(exc, "detail", exc))

# An empty string is what a blank <select> sends; it must mean "off", not a 400.
check("an empty mode means off, not an error",
      _resolve_price_feed(Payload("", 5))[0] == "off")

for bad_interval in (0.2, MAX_TICK_INTERVAL + 1, "abc", None if False else -5):
    try:
        _resolve_price_feed(Payload("websocket", bad_interval))
        check(f"interval {bad_interval!r} is rejected", False, "was accepted")
    except Exception as exc:
        check(f"interval {bad_interval!r} is rejected",
              "tick_interval" in str(getattr(exc, "detail", exc)),
              getattr(exc, "detail", exc))

check("the documented bounds are sane",
      1.0 <= MIN_TICK_INTERVAL and MAX_TICK_INTERVAL <= 60.0,
      (MIN_TICK_INTERVAL, MAX_TICK_INTERVAL))
check("a websocket request needs no client to validate",
      _resolve_price_feed(Payload("websocket", 1.0)) == ("websocket", 1.0))

# ===========================================================================
section("10. a worker built for a feed reports it")
# ===========================================================================
configured = LiveTradeService("Cfg", [], "key", "secret", is_custom=True,
                              broker_name="Binance", price_feed="websocket", tick_interval=2.0)
check("the requested mode is stored", configured.price_feed_mode == "websocket")
check("the requested interval is stored", configured.tick_interval == 2.0)
check("no feed is started until the worker runs", configured.tick_feed is None)
check("a worker defaults to off",
      LiveTradeService("D", [], "k", "s", is_custom=True,
                       broker_name="Binance").price_feed_mode == "off")

# ===========================================================================
print(f"\n{'=' * 62}")
print(f"  PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
if FAIL:
    print("  Failures:")
    for name in FAIL:
        print(f"    - {name}")
print(f"{'=' * 62}")
sys.exit(1 if FAIL else 0)
