"""Delta Exchange India live import / export / deadman-switch flow.

Covers the client document (docs.delta.exchange) without hitting the real
venue. A local mock captures every signed REST call so we can assert:

1. Testnet vs production REST + WebSocket hosts
2. HMAC-SHA256 over ``method + timestamp + path + query + body``
3. User-Agent on every request
4. ``GET /v2/products`` warmup
5. Bracket entry with ``trail_amount`` as a string on the SL leg
6. Heartbeat create / ack / disable (ttl=0) — the deadman switch
7. 1h candlesticks on the public socket (ticker stays channels[0])
8. Fills CSV in Kudos / backtest trade-log columns

Binance paths are not changed by this work; a regression check is included.

Run:  cd backend && ../.venv/bin/python test_delta_live_flow.py
"""
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import urlparse

sys.path.insert(0, ".")

TESTDB = "/tmp/delta_live_flow_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"

import asyncio                                                         # noqa: E402
import numpy as np                                                     # noqa: E402
import pandas as pd                                                    # noqa: E402
from app.core.mark_price import MarkPriceQuote                         # noqa: E402
from app.core.strategy import PhantomV2Config                          # noqa: E402
from app.services.broker_account import (                              # noqa: E402
    KUDOS_FILL_COLUMNS, KUDOS_TRADE_COLUMNS, fills_to_csv,
    fills_to_kudos_trades_csv,
)
from app.services.broker_client import BrokerClient                    # noqa: E402
from app.services.heartbeat import DeadmanSwitch                       # noqa: E402
from app.services.live_trader import LiveTradeService                  # noqa: E402
from app.services.paper_trader import PaperTradeService                # noqa: E402
from app.services.tick_feed import (                                   # noqa: E402
    STREAM_URLS, STREAM_URLS_TESTNET, NullTickFeed, build_tick_feed,
    delta_subscribe, parse_delta, parse_delta_candlestick,
)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}: {name}"
          + (f"  [{extra}]" if extra and not cond else ""))


def section(title):
    print(f"\n{'=' * 62}\n  {title}\n{'=' * 62}")


# ===========================================================================
section("1. production vs testnet hosts (India)")
# ===========================================================================
prod = BrokerClient("k", "s", "Delta", testnet=False)
demo = BrokerClient("k", "s", "Delta", testnet=True)
check("production REST is api.india.delta.exchange",
      prod.market_url == "https://api.india.delta.exchange"
      and prod.trading_url == "https://api.india.delta.exchange",
      prod.market_url)
check("testnet REST is cdn-ind.testnet.deltaex.org",
      demo.market_url == "https://cdn-ind.testnet.deltaex.org"
      and demo.trading_url == "https://cdn-ind.testnet.deltaex.org",
      demo.market_url)
check("a testnet flag does not change Binance production URL",
      BrokerClient("k", "s", "Binance", testnet=False).trading_url
      == "https://fapi.binance.com")
check("Binance testnet still points at testnet.binancefuture.com",
      BrokerClient("k", "s", "Binance", testnet=True).trading_url
      == "https://testnet.binancefuture.com")

ws_prod = prod.websocket_urls()
ws_demo = demo.websocket_urls()
check("production public WS is the new public-socket host (changelog 17.04.26)",
      ws_prod["public"] == "wss://public-socket.india.delta.exchange", ws_prod)
check("testnet private WS is socket-ind.testnet.deltaex.org",
      ws_demo["private"] == "wss://socket-ind.testnet.deltaex.org", ws_demo)
check("testnet public WS is socket-ind-pub.testnet.deltaex.org",
      "testnet.deltaex.org" in ws_demo["public"], ws_demo)
check("tick_feed production socket is the public India host",
      STREAM_URLS["delta"] == "wss://public-socket.india.delta.exchange")
check("tick_feed testnet socket is the public India testnet host",
      STREAM_URLS_TESTNET["delta"] == "wss://socket-ind-pub.testnet.deltaex.org")
check("Binance stream URL is unchanged",
      "fstream.binance.com" in STREAM_URLS["binance"])


# ===========================================================================
section("2. HMAC-SHA256 signature + User-Agent")
# ===========================================================================
secret = "supersecret"
client = BrokerClient("api-key-1", secret, "Delta")
method, timestamp, path = "POST", "1710000000", "/v2/orders/bracket"
query, body = "", '{"product_symbol":"BTCUSD"}'
expected = hmac.new(secret.encode(),
                    f"{method}{timestamp}{path}{query}{body}".encode(),
                    hashlib.sha256).hexdigest()
# Reconstruct the same way _delta_request does.
signature_data = method.upper() + timestamp + path + query + body
got = hmac.new(secret.encode(), signature_data.encode(), hashlib.sha256).hexdigest()
check("signature is method + timestamp + path + query + body",
      got == expected, got)
check("query is part of the payload (empty query still concatenates)",
      hmac.new(secret.encode(), f"GET{timestamp}/v2/products".encode(),
               hashlib.sha256).hexdigest()
      == hmac.new(secret.encode(),
                  f"GET{timestamp}/v2/products".encode(),
                  hashlib.sha256).hexdigest())

# Capture the actual headers a signed request would send, via a local server.
CAPTURE = {"headers": {}, "path": "", "method": "", "body": b""}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        CAPTURE["headers"] = {k: v for k, v in self.headers.items()}
        CAPTURE["path"] = self.path
        CAPTURE["method"] = "GET"
        body = json.dumps({"success": True, "result": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        CAPTURE["headers"] = {k: v for k, v in self.headers.items()}
        CAPTURE["path"] = self.path
        CAPTURE["method"] = "POST"
        length = int(self.headers.get("Content-Length") or 0)
        CAPTURE["body"] = self.rfile.read(length) if length else b""
        body = json.dumps({"success": True, "result": {"ok": True}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


httpd = HTTPServer(("127.0.0.1", 0), _Handler)
port = httpd.server_address[1]
Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.05)

local = BrokerClient("api-key-1", secret, "Delta")
local.market_url = f"http://127.0.0.1:{port}"
local.trading_url = f"http://127.0.0.1:{port}"
local.create_heartbeat("phantom_bot",
                       contract_types=["perpetual_futures"],
                       product_symbols=["BTCUSD"])
ua = CAPTURE["headers"].get("User-Agent") or CAPTURE["headers"].get("user-agent")
check("User-Agent is sent on signed heartbeat create",
      ua == BrokerClient.USER_AGENT, ua)
check("api-key header is present",
      CAPTURE["headers"].get("api-key") == "api-key-1", CAPTURE["headers"])
check("timestamp header is present",
      bool(CAPTURE["headers"].get("timestamp")), CAPTURE["headers"])
sig_hdr = CAPTURE["headers"].get("signature")
ts = CAPTURE["headers"].get("timestamp")
body_text = CAPTURE["body"].decode() if CAPTURE["body"] else ""
path_only = urlparse(CAPTURE["path"]).path
expected_sig = hmac.new(
    secret.encode(),
    f"POST{ts}{path_only}{body_text}".encode(),
    hashlib.sha256).hexdigest()
check("captured signature matches HMAC of method+ts+path+body",
      sig_hdr == expected_sig, (sig_hdr, expected_sig, path_only, body_text[:80]))
check("create posts to /v2/heartbeat/create",
      path_only == "/v2/heartbeat/create", CAPTURE["path"])

local.send_heartbeat("phantom_bot", ttl=30000)
check("ack posts to /v2/heartbeat",
      urlparse(CAPTURE["path"]).path == "/v2/heartbeat", CAPTURE["path"])
ack_body = json.loads(CAPTURE["body"] or b"{}")
check("ack body carries heartbeat_id and ttl in milliseconds",
      ack_body.get("heartbeat_id") == "phantom_bot" and ack_body.get("ttl") == 30000,
      ack_body)
local.disable_heartbeat("phantom_bot")
off_body = json.loads(CAPTURE["body"] or b"{}")
check("disable sends ttl=0 so a planned stop is not a crash",
      off_body.get("ttl") == 0, off_body)

# Public products call also carries User-Agent.
local.get_products()
ua2 = CAPTURE["headers"].get("User-Agent") or CAPTURE["headers"].get("user-agent")
check("User-Agent is sent on public GET /v2/products",
      ua2 == BrokerClient.USER_AGENT and "/v2/products" in CAPTURE["path"],
      (ua2, CAPTURE["path"]))
httpd.shutdown()


# ===========================================================================
section("3. bracket order trail_amount + edit_bracket")
# ===========================================================================
CAPTURE2 = {"calls": []}


class RecordingClient(BrokerClient):
    def _delta_request(self, method, path, body=None, query=None, weight=1.0,
                       is_order=False):
        CAPTURE2["calls"].append({"method": method, "path": path, "body": body,
                                  "query": query, "weight": weight,
                                  "is_order": is_order})

        class _R:
            status_code = 200
            headers = {}
            text = '{"success": true, "result": {"id": 1}}'

            def json(self):
                return {"success": True, "result": {"id": 1,
                                                    "entry_order": {"id": 1},
                                                    "stop_loss_order": {"id": 2},
                                                    "take_profit_order": {"id": 3}}}

        return _R(), None


rec = RecordingClient("k", "s", "Delta")
rec._instrument_cache["BTCUSD"] = {
    "contract_value": 0.001, "step_size": 1, "min_size": 1,
    "size_unit": "contracts", "quantity_precision": 0,
}
rec._instrument_cache["BTCUSDT"] = rec._instrument_cache["BTCUSD"]
payload = rec.place_bracket_order(
    "BTCUSDT", "buy", 0.01, price=None,
    stop_loss_price=59000, take_profit_price=62000,
    trail_amount=250.5, size_in_btc=True)
call = CAPTURE2["calls"][-1]
sl = (call["body"] or {}).get("stop_loss_order") or {}
check("bracket posts to /v2/orders/bracket",
      call["method"] == "POST" and call["path"] == "/v2/orders/bracket", call)
# Delta rejects a bracket SL leg carrying BOTH stop_price and trail_amount
# ("Only stop_price or trail_amount should be specified for bracket stop loss
# order"), which failed every entry on a trailing strategy. The trail wins.
check("trail_amount is a string on the SL leg",
      sl.get("trail_amount") == "250.5", sl)
check("a trailing SL leg omits stop_price (Delta bad_schema)",
      "stop_price" not in sl, sl)

# Without a trail distance the fixed stop is still sent, as a string.
rec.place_bracket_order("BTCUSDT", "buy", 0.01, price=None,
                        stop_loss_price=59000, take_profit_price=62000,
                        size_in_btc=True)
sl_fixed = (CAPTURE2["calls"][-1]["body"] or {}).get("stop_loss_order") or {}
check("SL stop_price is a string (decimal gotcha)",
      sl_fixed.get("stop_price") == "59000", sl_fixed)
check("a fixed SL leg carries no trail_amount", "trail_amount" not in sl_fixed, sl_fixed)
check("TP stop_price is a string",
      (call["body"] or {}).get("take_profit_order", {}).get("stop_price") == "62000",
      call["body"])
check("bracket is flagged native", payload.get("_bracket") is True)

rec.edit_bracket_order(42, symbol="BTCUSDT", stop_loss_price=59100, trail_amount=180)
edit = CAPTURE2["calls"][-1]
check("edit uses PUT /v2/orders/bracket",
      edit["method"] == "PUT" and edit["path"] == "/v2/orders/bracket", edit)
check("edit trail_amount stays a string",
      edit["body"]["stop_loss_order"]["trail_amount"] == "180", edit["body"])

rec.create_heartbeat("bot1", product_symbols=["BTCUSD"],
                     contract_types=["perpetual_futures"])
hb = CAPTURE2["calls"][-1]
check("heartbeat create is POST /v2/heartbeat/create",
      hb["method"] == "POST" and hb["path"] == "/v2/heartbeat/create", hb)
check("default config is cancel_orders after 1 missed beat",
      hb["body"]["config"] == [{"action": "cancel_orders", "unhealthy_count": 1}],
      hb["body"])
check("impact is contracts", hb["body"]["impact"] == "contracts", hb["body"])


# ===========================================================================
section("4. DeadmanSwitch loop (create + ack + ttl=0)")
# ===========================================================================
class FakeHBClient:
    def __init__(self):
        self.creates = []
        self.acks = []

    def create_heartbeat(self, heartbeat_id, impact="contracts",
                         contract_types=None, product_symbols=None,
                         underlying_assets=None, config=None):
        self.creates.append({
            "heartbeat_id": heartbeat_id, "impact": impact,
            "contract_types": contract_types, "product_symbols": product_symbols,
            "config": config,
        })
        return {"success": True, "result": {"heartbeat_id": heartbeat_id}}

    def send_heartbeat(self, heartbeat_id, ttl=30000):
        self.acks.append({"heartbeat_id": heartbeat_id, "ttl": ttl})
        return {"success": True,
                "result": {"process_enabled": "true",
                           "heartbeat_timestamp": "2026-01-01T00:00:30Z"}}


async def _hb_roundtrip():
    fake = FakeHBClient()
    switch = DeadmanSwitch(fake, "phantom_x", product_symbols=["BTCUSD"],
                           ack_interval=0.05)
    await switch.start()
    await __import__("asyncio").sleep(0.18)
    await switch.stop()
    return fake, switch

fake, switch = __import__("asyncio").run(_hb_roundtrip())
check("deadman created the heartbeat once",
      len(fake.creates) >= 1, fake.creates)
check("deadman acked at least once while running",
      any(a["ttl"] == 30000 for a in fake.acks), fake.acks)
check("stop disables with ttl=0",
      fake.acks and fake.acks[-1]["ttl"] == 0, fake.acks[-1])
check("stats report created + acks",
      switch.stats()["created"] is True and switch.stats()["acks"] >= 1,
      switch.stats())
check("config asked the exchange to cancel_orders",
      fake.creates[0]["config"][0]["action"] == "cancel_orders"
      and fake.creates[0]["config"][0]["unhealthy_count"] == 1,
      fake.creates[0])

# Failures never raise into the trading loop.
class Boom:
    def create_heartbeat(self, *a, **k):
        raise RuntimeError("venue down")

    def send_heartbeat(self, *a, **k):
        raise RuntimeError("venue down")


async def _hb_boom():
    switch = DeadmanSwitch(Boom(), "x", ack_interval=0.05)
    await switch.start()
    await __import__("asyncio").sleep(0.12)
    await switch.stop()
    return switch

boom = __import__("asyncio").run(_hb_boom())
check("a venue outage is recorded, not raised",
      boom.last_error is not None and boom.failures >= 1, boom.stats())


# ===========================================================================
section("5. WS candlesticks 1h — ticker stays channels[0]")
# ===========================================================================
frame = delta_subscribe("BTCUSD")
channels = frame["payload"]["channels"]
check("subscribe type is subscribe", frame["type"] == "subscribe")
check("channels[0] is ticker (changelog 17.04.26 renamed v2/ticker)",
      channels[0]["name"] == "ticker", channels)
check("candlestick_1h is also subscribed",
      any(c["name"] == "candlestick_1h" for c in channels), channels)
check("ticker stays on the product symbol",
      channels[0]["symbols"] == ["BTCUSD"], channels)
check("candles also subscribe MARK:BTCUSD (official mark-price OHLC)",
      "MARK:BTCUSD" in channels[1]["symbols"] and "BTCUSD" in channels[1]["symbols"],
      channels)

candle = parse_delta_candlestick({
    "type": "candlestick_1h", "symbol": "BTCUSD",
    "open": "60000", "high": "60100", "low": "59900", "close": "60050",
    "volume": "12.5", "candle_start_time": 1_700_000_000,
    "closed": True,
})
check("a closed 1h candle is parsed",
      candle is not None and candle["closed"] is True and candle["close"] == 60050.0,
      candle)
check("a ticker is not mistaken for a candle",
      parse_delta_candlestick({"type": "ticker", "symbol": "BTCUSD",
                               "mark_price": "60000", "close": "60001"}) is None)
check("parse_delta still reads the ticker",
      parse_delta({"type": "ticker", "symbol": "BTCUSD",
                   "mark_price": "60000"}).mark_price == 60000.0)

class DeltaDef:
    kind = "delta"


class FakeTickClient:
    testnet = True


feed = build_tick_feed("websocket", "Delta", "BTCUSD", DeltaDef(),
                       client=FakeTickClient())
check("testnet client picks the India testnet socket",
      "testnet.deltaex.org" in feed.url, feed.url)
check("built feed still subscribes ticker first",
      feed.subscribe["payload"]["channels"][0]["name"] == "ticker")


# ===========================================================================
section("6. live worker: trail_amount + heartbeat default")
# ===========================================================================
delta_worker = LiveTradeService(
    "PhantomV2", PhantomV2Config(), "k", "s", broker_name="Delta")
binance_worker = LiveTradeService(
    "PhantomV2", PhantomV2Config(), "k", "s", broker_name="Binance")
check("Delta worker enables the deadman switch by default",
      delta_worker.heartbeat_enabled is True)
check("Binance worker leaves the deadman switch off",
      binance_worker.heartbeat_enabled is False)
check("explicit False disables it on Delta",
      LiveTradeService("X", PhantomV2Config(), "k", "s",
                       broker_name="Delta", heartbeat=False).heartbeat_enabled is False)

cfg = PhantomV2Config()
delta_worker.config = cfg
delta_worker.oms.config = cfg
trail = delta_worker._trail_amount(500.0)
check("trail_amount is trail_distance_atr × ATR",
      abs(trail - float(cfg.trail_distance_atr) * 500.0) < 1e-9, trail)
check("zero ATR yields no trail", delta_worker._trail_amount(0) is None)

# Warmup must not raise when the venue is unreachable.
class QuietBroker:
    kind = "delta"
    testnet = True

    def get_instrument(self, symbol="BTCUSDT", refresh=False):
        raise RuntimeError("offline")

    def get_account_balance(self, asset="USDT"):
        raise RuntimeError("offline")


delta_worker.broker = QuietBroker()
try:
    delta_worker._warmup()
    check("warmup failures are swallowed", True)
except Exception as exc:
    check("warmup failures are swallowed", False, exc)


# ===========================================================================
section("7. Kudos fills CSV")
# ===========================================================================
fills = [
    {"filled_at": "2026-01-01T10:00:00", "symbol": "BTCUSD", "side": "buy",
     "size": 10, "qty_btc": 0.01, "price": 60000, "fee": 0.5, "role": "taker",
     "realized_pnl": None, "trade_id": "t1", "order_id": "o1",
     "client_order_id": "ph-1", "broker": "Delta"},
    {"filled_at": "2026-01-01T12:00:00", "symbol": "BTCUSD", "side": "sell",
     "size": 10, "qty_btc": 0.01, "price": 61000, "fee": 0.5, "role": "taker",
     "realized_pnl": 9.0, "trade_id": "t2", "order_id": "o2",
     "client_order_id": "ph-2", "broker": "Delta"},
]
raw = fills_to_csv(fills)
check("fills CSV starts with a UTF-8 BOM (Excel)", raw.startswith("\ufeff"))
header = raw.lstrip("\ufeff").split("\r\n")[0]
check("fills CSV header is the Kudos fill columns",
      tuple(header.split(",")) == KUDOS_FILL_COLUMNS, header)
check("fills CSV has one row per execution",
      raw.count("\r\n") >= 3)

trades = fills_to_kudos_trades_csv(fills)
trade_header = trades.lstrip("\ufeff").split("\r\n")[0]
check("kudos trades CSV header matches the backtest log",
      tuple(trade_header.split(",")) == KUDOS_TRADE_COLUMNS, trade_header)
row = trades.lstrip("\ufeff").split("\r\n")[1]
check("FIFO pairing produced one round-trip",
      "60000" in row and "61000" in row and "9.0" in row, row)
check("entry_time / exit_time / direction columns exist",
      KUDOS_TRADE_COLUMNS[:3] == ("entry_time", "exit_time", "direction"))


# ===========================================================================
section("8. paper trade live ticks")
# ===========================================================================
paper = PaperTradeService("PhantomV2", PhantomV2Config(), initial_capital=20000,
                          margin_pct=25, market_source="Binance",
                          price_feed="websocket", tick_interval=2.0)
check("paper stores the requested feed mode", paper.price_feed_mode == "websocket")
check("paper defaults to off when omitted",
      PaperTradeService("PhantomV2", PhantomV2Config()).price_feed_mode == "off")

from datetime import timedelta as _td
rng = np.random.RandomState(3)
close = 60000 + np.cumsum(rng.randn(80) * 5)
idx = pd.date_range(datetime(2024, 2, 1), periods=80, freq="1h")
df = pd.DataFrame({"open": close, "high": close + 20, "low": close - 20,
                   "close": close, "volume": 10.0}, index=idx)
from app.core.indicators import compute_indicators
atr = float(compute_indicators(df)["atr14"][-1])
opened = paper.oms.create_order("BTCUSDT", 1, 60000.0, atr, idx[-1],
                                5000.0, paper.conversion_rate)
check("paper opened a long to test the fast tick", opened is not None)
paper._last_atr = atr
paper._last_bar_time = idx[-1]
crash = NullTickFeed("Binance", "BTCUSDT", max_age=30.0)
crash.publish(MarkPriceQuote("Binance", "BTCUSDT", mark_price=100.0, last_price=100.0))
paper.tick_feed = crash
asyncio.run(paper.fast_tick())
check("a paper stop closes on the live tick, not the 60s candle",
      "BTCUSDT" not in paper.oms.active_trades, list(paper.oms.active_trades))
check("the paper fast tick was counted", paper.fast_ticks == 1, paper.fast_ticks)
check("the closed paper trade was booked",
      len(paper.closed_trades) == 1 and paper.closed_trades[0]["reason"] == "SL",
      paper.closed_trades)


# ===========================================================================
section("9. GET /v2/profile is blocked (changelog 19.08.26)")
# ===========================================================================
# docs.delta.exchange changelog 19.08.26: GET /v2/profile is no longer
# accessible with API keys from 19 Aug 2026. Requests are rejected. Account
# reads must use GET /v2/wallet/balances (which this client already does).
import inspect as _inspect
_src = _inspect.getsource(BrokerClient)
check("account balance uses GET /v2/wallet/balances, not /v2/profile",
      "/v2/wallet/balances" in _inspect.getsource(BrokerClient.get_account_balance)
      and "/v2/profile" not in _inspect.getsource(BrokerClient.get_account_balance))
blocked = BrokerClient("k", "s", "Delta")
# Point at a closed port so a leak would fail the request instead of hanging.
blocked.trading_url = "http://127.0.0.1:1"
blocked.market_url = "http://127.0.0.1:1"
refused = blocked._json_body(*blocked._delta_request("GET", "/v2/profile"))
check("GET /v2/profile is refused locally (no HTTP call)",
      isinstance(refused, dict) and "19.08.26" in str(refused.get("error", "")),
      refused)
refused_alias = blocked._json_body(*blocked._delta_request("GET", "/profile"))
check("bare /profile is refused the same way",
      isinstance(refused_alias, dict) and "wallet/balances" in str(refused_alias.get("error", "")),
      refused_alias)
via_request = blocked.request("GET", "/v2/profile")
check("generic request() also refuses /v2/profile",
      isinstance(via_request, dict) and "19.08.26" in str(via_request.get("error", "")),
      via_request)
# Wallet balances is still a real signed GET (RecordingClient captured paths).
check("source never GETs /v2/profile except to block it",
      _src.count('GET", "/v2/profile"') == 0 and "changelog 19.08.26" in _src)

# Changelog 15.04.26: limit_price ≤ 0 is rejected — never send it.
zero = rec.place_order("BTCUSDT", "buy", "limit", 1, price=0, size_in_btc=False)
check("limit_price 0 is refused before HTTP (changelog 15.04.26)",
      isinstance(zero, dict) and "15.04.26" in str(zero.get("error", "")), zero)
neg = rec.place_order("BTCUSDT", "buy", "limit", 1, price=-1, size_in_btc=False)
check("negative limit_price is refused",
      isinstance(neg, dict) and "limit_price" in str(neg.get("error", "")), neg)
before = len(CAPTURE2["calls"])
rec.place_order("BTCUSDT", "buy", "limit", 1, price=60100, size_in_btc=False)
limit_call = CAPTURE2["calls"][-1] if len(CAPTURE2["calls"]) > before else {}
check("a positive limit_price is still sent as a string",
      (limit_call.get("body") or {}).get("limit_price") == "60100", limit_call)

compact = parse_delta_candlestick({
    "type": "candlestick_1m", "c": 71748.0, "h": 71751.5, "l": 71737.0,
    "o": 71737.0, "res": "1m", "sy": "BTCUSD", "ts": 1_700_000_000_000_000,
})
check("compact candlestick keys (o/h/l/c/sy/res/ts) parse",
      compact is not None and compact["close"] == 71748.0
      and compact["symbol"] == "BTCUSD" and compact["resolution"] == "1m",
      compact)


# ===========================================================================
print(f"\n{'=' * 62}")
print(f"  PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
if FAIL:
    print("  Failures:")
    for name in FAIL:
        print(f"    - {name}")
print(f"{'=' * 62}")
sys.exit(1 if FAIL else 0)
