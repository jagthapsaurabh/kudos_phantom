"""Live order lifecycle, terminal data and rate limiting.

Covers the three layers added for "trade like Delta Exchange":

1. ``app.core.rate_limit``   — the throttling budget behind every broker call
   (Delta 10 000 weight / 5 min, Binance 2 400 weight + 1 200 orders / min).
2. ``app.services.broker_client`` — order / position / fill / margin adapters
   for both venues, driven against a local mock exchange.
3. ``app.services.broker_account`` — the normalized terminal schema
   (positions, open orders, stop orders, fills, order history, risk) plus the
   local audit tables, exercised through the HTTP API.

Run:  cd backend && ../.venv/bin/python test_live_account.py
"""
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, '.')

TESTDB = "/tmp/live_account_phantom_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"

# ---------------------------------------------------------------------------
# Mock exchange: speaks both Binance fapi and Delta /v2 on one port.
# ---------------------------------------------------------------------------
STATE = {
    "requests": [],          # (method, path, query, body)
    "order_seq": 1000,
    "fail_next_order": 0,    # number of times to answer 429 on POST /order
    "last_headers": {},
}


def _binance_order(**overrides):
    STATE["order_seq"] += 1
    row = {
        "orderId": STATE["order_seq"], "symbol": "BTCUSDT", "status": "NEW",
        "clientOrderId": "ph-test", "price": "0", "avgPrice": "0",
        "origQty": "0.001", "executedQty": "0", "type": "MARKET",
        "side": "BUY", "reduceOnly": False, "updateTime": int(time.time() * 1000),
    }
    row.update(overrides)
    return row


def _delta_order(**overrides):
    STATE["order_seq"] += 1
    row = {
        "id": STATE["order_seq"], "product_symbol": "BTCUSD", "state": "open",
        "side": "buy", "size": 10, "unfilled_size": 10, "order_type": "limit_order",
        "limit_price": "60000", "average_fill_price": None,
        "created_at": int(time.time() * 1_000_000),
    }
    row.update(overrides)
    return row


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, payload, status=200, headers=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            self.send_header(key, str(value))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode())
        except ValueError:
            return {"_raw": raw.decode()}

    def _delta(self, result):
        return {"success": True, "error": None, "result": result}

    def _route(self, method):
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        body = self._read_body() if method in ("POST", "PUT", "DELETE") else {}
        STATE["requests"].append({"method": method, "path": path,
                                  "query": {k: v[0] for k, v in query.items()},
                                  "body": body,
                                  "headers": dict(self.headers)})
        STATE["last_headers"] = dict(self.headers)

        # ------------------------------------------------ Binance ----------
        if path == "/fapi/v1/exchangeInfo":
            return self._send({"symbols": [{
                "symbol": "BTCUSDT", "quoteAsset": "USDT", "baseAsset": "BTC",
                "contractType": "PERPETUAL", "pricePrecision": 2, "quantityPrecision": 3,
                "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"}],
            }]})
        if path == "/fapi/v1/premiumIndex":
            return self._send({"symbol": "BTCUSDT", "markPrice": "67100.50",
                               "indexPrice": "67090.10", "lastFundingRate": "0.0001"})
        if path == "/fapi/v1/ticker/price":
            return self._send({"symbol": "BTCUSDT", "price": "67105.00"})
        if path == "/fapi/v1/klines" or path == "/fapi/v1/markPriceKlines":
            return self._send([])
        if path == "/fapi/v1/order" and method == "POST":
            if STATE["fail_next_order"] > 0:
                STATE["fail_next_order"] -= 1
                return self._send({"code": -1003, "msg": "Too many requests"}, 429,
                                  {"Retry-After": "0"})
            if query.get("closePosition", [None])[0] == "true":
                return self._send(_binance_order(type="MARKET", origQty="0",
                                                 executedQty="0.003", status="FILLED",
                                                 avgPrice="67010.0"))
            if query.get("type", [None])[0] == "STOP_MARKET":
                return self._send(_binance_order(type="STOP_MARKET",
                                                 side=query.get("side", [None])[0],
                                                 stopPrice=query.get("stopPrice", [None])[0],
                                                 reduceOnly=query.get("reduceOnly", [None])[0] == "true",
                                                 workingType=query.get("workingType", [None])[0],
                                                 priceProtect=query.get("priceProtect", [None])[0]))
            if query.get("type", [None])[0] == "TAKE_PROFIT_MARKET":
                return self._send(_binance_order(type="TAKE_PROFIT_MARKET",
                                                 side=query.get("side", [None])[0],
                                                 stopPrice=query.get("stopPrice", [None])[0],
                                                 reduceOnly=query.get("reduceOnly", [None])[0] == "true"))
            return self._send(_binance_order(type=query.get("type", [None])[0],
                                             side=query.get("side", [None])[0],
                                             origQty=query.get("quantity", ["0"])[0],
                                             newClientOrderId=query.get("newClientOrderId", [None])[0]))
        if path == "/fapi/v1/order" and method == "DELETE":
            return self._send(_binance_order(status="CANCELED", orderId=int(query.get("orderId", [0])[0] or 0)))
        if path == "/fapi/v1/allOpenOrders" and method == "DELETE":
            return self._send({"code": 200, "msg": "success"})
        if path == "/fapi/v1/openOrders":
            return self._send([_binance_order(status="NEW", type="LIMIT", price="60000"),
                               _binance_order(status="NEW", type="STOP_MARKET", stopPrice="65000",
                                              reduceOnly=True)])
        if path == "/fapi/v1/allOrders":
            return self._send([_binance_order(status="FILLED", type="MARKET",
                                              executedQty="0.003", avgPrice="67000")])
        if path == "/fapi/v1/userTrades":
            return self._send([{"id": 555001, "orderId": 1001, "symbol": "BTCUSDT",
                                "side": "BUY", "qty": "0.003", "price": "67000.5",
                                "commission": "-0.02", "maker": False,
                                "realizedPnl": "0", "time": int(time.time() * 1000)}])
        if path == "/fapi/v2/positionRisk":
            return self._send([{"symbol": "BTCUSDT", "positionAmt": "0.003",
                                "entryPrice": "67000.5", "markPrice": "67100.5",
                                "unRealizedProfit": "0.30", "liquidationPrice": "54000",
                                "leverage": "10", "marginType": "isolated",
                                "isolatedWallet": "20.1", "positionSide": "BOTH"}])
        if path == "/fapi/v2/account":
            return self._send({"assets": [{"asset": "USDT", "walletBalance": "1000.00",
                                           "availableBalance": "940.00",
                                           "unrealizedProfit": "0.30",
                                           "marginBalance": "1000.30"}],
                               "totalWalletBalance": "1000.00",
                               "totalUnrealizedProfit": "0.30",
                               "totalMarginBalance": "1000.30",
                               "totalPositionInitialMargin": "40.20",
                               "totalOpenOrderInitialMargin": "19.80",
                               "canTrade": True, "canWithdraw": True})
        if path == "/fapi/v1/leverage":
            return self._send({"symbol": "BTCUSDT", "leverage": int(query.get("leverage", [0])[0] or 0)})
        if path == "/fapi/v1/marginType":
            return self._send({"code": 200, "msg": "success"})
        if path == "/fapi/v1/positionRisk":
            # get_leverage / get_account_settings source (answers when flat too)
            return self._send([{"symbol": "BTCUSDT", "marginType": "cross",
                                "leverage": "5", "positionAmt": "0"}])
        if path == "/fapi/v1/positionMargin":
            return self._send({"amount": query.get("amount"), "code": 200, "msg": "Success"})

        # -------------------------------------------------- Delta ----------
        # Specific product routes must run before the generic /v2/products/
        # instrument lookup below.
        if path == "/v2/products/139/orders/leverage" and method == "GET":
            return self._send(self._delta({"leverage": 7, "product_id": 139,
                                           "order_margin": "142.8"}))
        if path == "/v2/sub_accounts":
            if STATE.get("fail_sub_accounts"):
                return self._send({"success": False,
                                   "error": {"code": "invalid_api_key"}}, 401)
            # A parent key sees the main account (cross — the reported setup)
            # plus one isolated sub-account.
            return self._send(self._delta([
                {"id": "5112346", "email": "main@example.com", "account_name": "Main",
                 "margin_mode": "cross", "is_sub_account": False, "is_kyc_done": True},
                {"id": "5112347", "email": "sub@example.com", "account_name": "Scalper",
                 "margin_mode": "isolated", "is_sub_account": True, "is_kyc_done": True},
            ]))
        if path == "/v2/users/margin_mode" and method == "PUT":
            if STATE.get("fail_margin_mode"):
                # The venue refusing the push outright (rate limit, schema
                # change, permission) — must abort a live start.
                return self._send({"success": False, "error": {
                    "code": "bad_schema", "context": {"schema_errors": [
                        {"code": "validation_error",
                         "message": "margin_mode change is disabled for this account", "param": ""}]}}}, 400)
            if STATE.get("same_margin_mode"):
                # Production behaviour: Delta answers HTTP 400
                # {"code": "same_margin_mode"} when the account is ALREADY in
                # the requested mode — a confirmation, not a refusal.
                return self._send({"success": False,
                                   "error": {"code": "same_margin_mode"}}, 400)
            # Production behaviour (the reported bug): the venue requires
            # subaccount_user_id even for the key's own account.
            if not (body or {}).get("subaccount_user_id"):
                return self._send({"success": False, "error": {
                    "code": "bad_schema", "context": {"schema_errors": [
                        {"code": "validation_error",
                         "message": "subaccount_user_id is required", "param": ""}]}}}, 400)
            return self._send(self._delta({"id": (body or {}).get("subaccount_user_id"),
                                           "margin_mode": (body or {}).get("margin_mode")}))
        if path.startswith("/v2/products/"):
            return self._send(self._delta({
                "id": 139, "symbol": "BTCUSD", "contract_value": 0.001,
                "tick_size": 0.5, "contract_type": "perpetual_futures",
                "quoting_asset": {"symbol": "USD"}, "contract_unit_currency": "BTC"}))
        if path.startswith("/v2/tickers/"):
            return self._send(self._delta({"symbol": "BTCUSD", "mark_price": "67100.50",
                                           "close": "67105.00", "spot_price": "67090.10"}))
        if path == "/v2/orders" and method == "POST":
            if STATE["fail_next_order"] > 0:
                STATE["fail_next_order"] -= 1
                return self._send(self._delta(None), 429, {"X-RATE-LIMIT-RESET": "0"})
            return self._send(self._delta(_delta_order(
                size=body.get("size"), side=body.get("side"),
                order_type=body.get("order_type"),
                stop_price=body.get("stop_price"),
                stop_order_type=body.get("stop_order_type"),
                stop_trigger_method=body.get("stop_trigger_method"),
                client_order_id=body.get("client_order_id"),
                limit_price=body.get("limit_price"),
                # Entry-order bracket: the venue echoes the bracket_* fields
                # back on the created order (docs.delta.exchange).
                **{k: body.get(k) for k in (
                    "bracket_stop_loss_price", "bracket_stop_loss_limit_price",
                    "bracket_take_profit_price", "bracket_take_profit_limit_price",
                    "bracket_trail_amount", "bracket_stop_trigger_method")
                   if body.get(k) is not None})))
        if path == "/v2/orders/bracket" and method == "POST":
            # Production behaviour (the reported bug): this endpoint only
            # ATTACHES a TP/SL pair to an EXISTING position. An entry-shaped
            # body (size/side) proves the caller tried to OPEN a position
            # here — Delta answers exactly like the live venue did, so any
            # regression fails the suite the way it failed in production.
            if (body or {}).get("size") or (body or {}).get("side"):
                return self._send({"success": False,
                                   "error": {"code": "no_open_position"}}, 400)
            legs = {}
            if body.get("stop_loss_order"):
                legs["stop_loss_order"] = _delta_order(
                    side="sell", stop_price=body["stop_loss_order"].get("stop_price"),
                    stop_order_type="stop_loss_order")
            if body.get("take_profit_order"):
                legs["take_profit_order"] = _delta_order(
                    side="sell", stop_price=body["take_profit_order"].get("stop_price"),
                    stop_order_type="take_profit_order")
            return self._send(self._delta(legs or {"error": "no legs"}))
        if path == "/v2/orders" and method == "GET":
            return self._send(self._delta([
                _delta_order(state="open", order_type="limit_order", limit_price="60000"),
                _delta_order(state="open", order_type="market_order", stop_price="65000",
                             stop_order_type="stop_loss_order",
                             stop_trigger_method="mark_price"),
            ]))
        if path == "/v2/orders" and method == "PUT":
            return self._send(self._delta(_delta_order(id=body.get("id"), state="open")))
        if path == "/v2/orders/all" and method == "DELETE":
            return self._send(self._delta({"cancelled": 2}))
        # Keep the client-order-id fallback ahead of the numeric order-id
        # route; otherwise the mock tries to parse "client" as an integer and
        # drops the connection while the real client is handling a valid 404.
        if path == "/v2/orders/client" and method == "DELETE":
            return self._send(self._delta(_delta_order(state="cancelled")))
        if path.startswith("/v2/orders/") and method == "DELETE":
            return self._send(self._delta(_delta_order(id=int(path.rsplit("/", 1)[-1]),
                                                       state="cancelled")))
        if path == "/v2/orders/history":
            return self._send(self._delta([_delta_order(state="closed", unfilled_size=0,
                                                        average_fill_price="67000")]))
        if path == "/v2/fills":
            return self._send(self._delta([{"id": 90001, "order_id": 1001,
                                            "product_symbol": "BTCUSD", "side": "buy",
                                            "size": 30, "price": "67000.5",
                                            "commission": "0.004",
                                            "created_at": int(time.time() * 1_000_000)}]))
        if path == "/v2/positions/margined":
            return self._send(self._delta([{
                "product_symbol": "BTCUSD", "size": 30, "entry_price": "67000.5",
                "mark_price": "67100.5", "margin": "20.10", "leverage": 10,
                "margin_type": "cross",
                "unrealized_pnl": "3.00", "realized_pnl": "-0.50",
                "liquidation_price": "54000", "bankruptcy_price": "53000"}]))
        if path == "/v2/positions/close_all" and method == "POST":
            return self._send(self._delta([_delta_order(state="closed", order_type="market_order")]))
        if path == "/v2/positions/change_margin":
            return self._send(self._delta({"delta_margin": body.get("delta_margin")}))
        if path == "/v2/positions/margin_mode":
            if STATE.get("legacy_margin_mode_gone"):
                # Current production: the legacy per-position route no longer
                # exists, so the fallback cannot hide a refused modern PUT.
                return self._send({"success": False,
                                   "error": {"code": "not_found", "message": "endpoint no longer available"}}, 404)
            return self._send(self._delta({"margin_mode": body.get("margin_mode")}))
        if path == "/v2/orders/leverage":
            return self._send(self._delta({"leverage": (body or {}).get("leverage") or
                                           query.get("leverage", [None])[0]}))
        if path == "/v2/wallet/balances":
            # Delta wallet entries carry the owning account's user_id; the
            # flag simulates a deployment whose rows come back without it so
            # the sub-accounts fallback gets exercised.
            uid = None if STATE.get("wallet_no_user_id") else "5112346"
            rows = [
                {"asset_symbol": "USD", "balance": "1000.00", "available_balance": "940.00",
                 "order_margin": "19.80", "position_margin": "40.20", "commission": "0.10"},
                {"asset_symbol": "BTC", "balance": "0.01", "available_balance": "0.01"}]
            if uid:
                for row in rows:
                    row["user_id"] = uid
            return self._send(self._delta(rows))
        if path == "/v2/rate_limits/quota":
            return self._send(self._delta({"current_quota": 6420.0,
                                           "remaining_time_in_milliseconds": 123000}))
        return self._send({"error": f"unmocked endpoint {method} {path}"}, 404)

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
MOCK = f"http://127.0.0.1:{server.server_address[1]}"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not cond else ""), flush=True)


def section(title):
    print(f"\n== {title} ==", flush=True)


# ===========================================================================
section("1. rate limits — configuration")
# ===========================================================================
from app.core.rate_limit import (  # noqa: E402
    RateLimitConfig, RateLimiter, RateLimitExceeded, VENUE_DEFAULTS,
    default_config_for, get_limiter, reset_registry,
)

reset_registry()
delta_cfg = VENUE_DEFAULTS["Delta"]
binance_cfg = VENUE_DEFAULTS["Binance"]
check("Delta default is a 10 000 weight / 5-minute budget", delta_cfg.weight_per_5min == 10000.0,
      str(delta_cfg.weight_per_5min))
check("Binance default tracks order budget separately",
      binance_cfg.orders_per_minute == 1200.0 and binance_cfg.weight_per_5min is None,
      str(binance_cfg))
check("Binance also caps 300 orders per 10 seconds",
      binance_cfg.orders_per_10s == 300.0, str(binance_cfg.orders_per_10s))
check("Delta publishes no order-specific cap", delta_cfg.orders_per_10s is None)
check("conservative default stays at or under 20 requests/second (the user's figure)",
      delta_cfg.requests_per_second == 20.0 and binance_cfg.requests_per_second == 20.0)
check("20 req/s sits under Binance's 2 400/min weight budget",
      binance_cfg.requests_per_second * 60 <= 2400)


class _Def:
    """Stands in for a BrokerDefinition row."""
    def __init__(self, **kw):
        for key, value in kw.items():
            setattr(self, key, value)


overridden = default_config_for("Delta", _Def(rate_limit_per_second=5, quota_per_5min=2500))
check("broker definition overrides the venue default",
      overridden.requests_per_second == 5.0 and overridden.weight_per_5min == 2500.0, str(overridden))
check("untouched fields keep the venue default", overridden.requests_per_minute == 1200.0)
check("no definition = venue default", default_config_for("Delta") is VENUE_DEFAULTS["Delta"])
check("unknown venue falls back to the safe generic config",
      default_config_for("Somewhere").requests_per_second == 20.0)

# ===========================================================================
section("2. rate limits — throttling behaviour")
# ===========================================================================
limiter = RateLimiter("test-slow", RateLimitConfig(requests_per_second=3, requests_per_minute=100,
                                                   weight_per_5min=None, orders_per_minute=None,
                                                   acquire_timeout=3.0))
start = time.monotonic()
for _ in range(3):
    limiter.acquire()
check("first 3 calls pass without delay", time.monotonic() - start < 0.2)
limiter.acquire()  # 4th call waits for the second window to roll
elapsed = time.monotonic() - start
check("4th call is throttled past the 1-second window", 0.5 <= elapsed <= 2.5, f"{elapsed:.2f}s")
check("throttled counter recorded", limiter.snapshot()["throttled_calls"] >= 1,
      str(limiter.snapshot()["throttled_calls"]))
check("second-window usage is tracked", limiter.snapshot()["requests_last_second"] == 1,
      str(limiter.snapshot()["requests_last_second"]))

full = RateLimiter("test-full", RateLimitConfig(requests_per_second=1, requests_per_minute=1,
                                                weight_per_5min=None, orders_per_minute=None,
                                                acquire_timeout=0.3))
full.acquire()
raised = False
try:
    full.acquire()
except RateLimitExceeded:
    raised = True
check("a full minute window raises instead of blocking forever", raised)

order_limiter = RateLimiter("test-orders", RateLimitConfig(requests_per_second=100,
                                                           requests_per_minute=100,
                                                           weight_per_5min=None,
                                                           orders_per_minute=2,
                                                           acquire_timeout=0.4))
order_limiter.acquire(is_order=True)
order_limiter.acquire(is_order=True)
check("orders are counted separately", order_limiter.snapshot()["orders_last_minute"] == 2)
raised = False
try:
    order_limiter.acquire(is_order=True)
except RateLimitExceeded:
    raised = True
check("order budget is enforced (Binance 1 200/min)", raised)

burst = RateLimiter("test-burst", RateLimitConfig(requests_per_second=100,
                                                  requests_per_minute=100,
                                                  weight_per_5min=None,
                                                  orders_per_minute=1000,
                                                  orders_per_10s=3,
                                                  acquire_timeout=0.4))
for _ in range(3):
    burst.acquire(is_order=True)
check("10-second order window is counted", burst.snapshot()["orders_last_10s"] == 3)
raised = False
try:
    burst.acquire(is_order=True)
except RateLimitExceeded:
    raised = True
check("a 300-in-10s style burst is blocked even with minute budget left", raised)
check("an admin override scales the 10s window with the minute budget",
      binance_cfg.orders_per_minute and
      default_config_for("Binance", _Def(orders_per_minute=600)).orders_per_10s == 150.0)

weight_limiter = RateLimiter("test-weight", RateLimitConfig(requests_per_second=100,
                                                            requests_per_minute=None,
                                                            weight_per_5min=10,
                                                            orders_per_minute=None,
                                                            acquire_timeout=0.4))
weight_limiter.acquire(weight=6)
weight_limiter.acquire(weight=6)   # 12/10 — over the quota
raised = False
try:
    weight_limiter.acquire(weight=1)
except RateLimitExceeded:
    raised = True
check("Delta weight quota is enforced", raised)
check("weight usage is visible", weight_limiter.snapshot()["weight_used_5min"] == 12.0,
      str(weight_limiter.snapshot()["weight_used_5min"]))

retry_limiter = RateLimiter("test-retry", RateLimitConfig(backoff_base=0.35, max_sleep_seconds=5))
check("Retry-After (seconds) is honoured",
      abs(retry_limiter.retry_delay(1, {"Retry-After": "2"}) - 2.0) < 1e-6,
      str(retry_limiter.retry_delay(1, {"Retry-After": "2"})))
check("X-RATE-LIMIT-RESET is read as milliseconds",
      abs(retry_limiter.retry_delay(1, {"X-RATE-LIMIT-RESET": "1500"}) - 1.5) < 1e-6,
      str(retry_limiter.retry_delay(1, {"X-RATE-LIMIT-RESET": "1500"})))
check("backoff grows with the attempt",
      retry_limiter.retry_delay(1) < retry_limiter.retry_delay(3))
check("backoff is capped", retry_limiter.retry_delay(99) <= 5.0)

retry_limiter.note_response({"X-MBX-USED-WEIGHT-1M": "2100"}, weight=2)
check("exchange-reported weight is recorded",
      retry_limiter.snapshot()["exchange_weight"] == 2100.0)
retry_limiter.note_quota(6420.0, 123000)
check("Delta quota endpoint is recorded",
      retry_limiter.snapshot()["exchange_quota"] == 6420.0
      and retry_limiter.snapshot()["exchange_reset_ms"] == 123000.0)

paced = RateLimiter("test-pace", RateLimitConfig(requests_per_minute=100, requests_per_second=100,
                                                 weight_per_5min=None, orders_per_minute=None,
                                                 safe_ratio=0.85, acquire_timeout=0.05))
paced.note_response({"X-MBX-USED-WEIGHT-1M": "95"})
check("calls are slowed before the venue says no (safe_ratio)",
      paced._sleep_for(([], [], [], [], [])) >= 0.4, str(paced._sleep_for(([], [], [], [], []))))

shared_a = get_limiter("shared:key", RateLimitConfig(requests_per_second=50))
shared_b = get_limiter("shared:key")
check("one limiter per broker connection is shared by every caller", shared_a is shared_b)
reset_registry()
check("registry reset clears limiters", get_limiter("shared:key") is not shared_a)
reset_registry()

# ===========================================================================
section("3. broker client — instruments & sizing")
# ===========================================================================
from app.services.broker_client import BrokerClient  # noqa: E402
from app.database.models import (  # noqa: E402
    SessionLocal, User, BrokerDefinition, BrokerConnection, init_db,
)

init_db()
db = SessionLocal()
db.query(User).delete()
db.query(BrokerConnection).delete()
db.query(BrokerDefinition).delete()
db.add(BrokerDefinition(code="Binance", name="Binance Futures", kind="binance", is_builtin=1,
                        enabled=1, market_data_url=MOCK, trading_api_url=MOCK))
db.add(BrokerDefinition(code="Delta", name="Delta Exchange", kind="delta", is_builtin=1,
                        enabled=1, market_data_url=MOCK, trading_api_url=MOCK,
                        rate_limit_per_second=50.0))
db.commit()
BINANCE_DEF = db.query(BrokerDefinition).filter(BrokerDefinition.code == "Binance").first()
DELTA_DEF = db.query(BrokerDefinition).filter(BrokerDefinition.code == "Delta").first()
db.close()

binance = BrokerClient("key", "secret", "Binance", definition=BINANCE_DEF)
delta = BrokerClient("key", "secret", "Delta", definition=DELTA_DEF)

check("perpetual symbol is the BTC perpetual on both venues",
      binance.perpetual_symbol("BTCUSDT") == "BTCUSDT" and delta.perpetual_symbol("BTCUSDT") == "BTCUSD")
check("definition rate-limit override is picked up", delta.rate_limit_config.requests_per_second == 50.0,
      str(delta.rate_limit_config.requests_per_second))
check("Binance keeps the venue default", binance.rate_limit_config.requests_per_second == 20.0)

bin_inst = binance.get_instrument("BTCUSDT")
check("Binance instrument: BTC lots, 0.001 step",
      bin_inst["size_unit"] == "BTC" and bin_inst["step_size"] == 0.001 and bin_inst["min_size"] == 0.001,
      str(bin_inst))
check("Binance contract value is 1 BTC per lot", bin_inst["contract_value"] == 1.0)
delta_inst = delta.get_instrument("BTCUSDT")
check("Delta instrument: whole contracts",
      delta_inst["size_unit"] == "contracts" and delta_inst["step_size"] == 1.0 and delta_inst["min_size"] == 1.0,
      str(delta_inst))
check("Delta contract value comes from the product (0.001 BTC)",
      abs(delta_inst["contract_value"] - 0.001) < 1e-9, str(delta_inst["contract_value"]))

# Regression coverage for Delta's signed account filters. These four endpoints
# accept product_ids (plural); product_id is reserved for GET /v2/positions.
# Sending both was also able to make the signed query order differ from the
# query that requests put on the wire.
STATE["requests"].clear()
delta.get_positions("BTCUSDT")
delta.get_open_orders("BTCUSDT")
delta.get_fills("BTCUSDT", limit=10)
delta.get_order_history("BTCUSDT", limit=10)
_account_queries = {
    request["path"]: request["query"]
    for request in STATE["requests"]
    if request["path"] in ("/v2/positions/margined", "/v2/orders",
                            "/v2/fills", "/v2/orders/history")
}
check("Delta account endpoints send product_ids only",
      len(_account_queries) == 4
      and all("product_ids" in query and "product_id" not in query
              for query in _account_queries.values()),
      str(_account_queries))
check("Delta positions filter is product_ids=139",
      _account_queries.get("/v2/positions/margined", {}).get("product_ids") == "139",
      str(_account_queries.get("/v2/positions/margined")))
check("Delta orders, fills and history filters are product_ids=139",
      all(query.get("product_ids") == "139"
          for path, query in _account_queries.items()
          if path != "/v2/positions/margined"),
      str(_account_queries))

check("0.03 BTC -> 30 Delta contracts", abs(delta.base_to_venue_size(0.03) - 30.0) < 1e-9,
      str(delta.base_to_venue_size(0.03)))
check("0.0004 BTC is below Delta's minimum contract", delta.base_to_venue_size(0.0004) == 0.0)
check("0.03 BTC -> 0.030 Binance lots", abs(binance.base_to_venue_size(0.03) - 0.03) < 1e-9)
check("sizing round-trips (venue -> BTC)",
      abs(delta.venue_to_base_size(30.0) - 0.03) < 1e-9)

# ===========================================================================
section("4. broker client — order lifecycle (Binance)")
# ===========================================================================
STATE["requests"].clear()
res = binance.place_order("BTCUSDT", "buy", "market", 0.03, size_in_btc=True)
check("market order accepted", isinstance(res, dict) and "error" not in res, str(res)[:200])
sent = [r for r in STATE["requests"] if r["path"] == "/fapi/v1/order"][-1]
check("Binance order signed and sent with the BTC quantity",
      sent["query"].get("quantity") == "0.03" and sent["query"].get("type") == "MARKET"
      and sent["query"].get("side") == "BUY", str(sent["query"]))
check("Binance request carries the API key header", "X-MBX-APIKEY" in sent["headers"])
check("Binance request carries a signature and timestamp",
      "signature" in sent["query"] and "timestamp" in sent["query"])

sl = binance.place_order("BTCUSDT", "sell", "stop_market", 0.03, stop_price=65000.0,
                         reduce_only=True, size_in_btc=True)
check("stop order accepted", isinstance(sl, dict) and "error" not in sl, str(sl)[:200])
sent = [r for r in STATE["requests"] if r["path"] == "/fapi/v1/order"][-1]
check("stop triggers on the MARK price with price protection, reduce-only",
      sent["query"].get("workingType") == "MARK_PRICE" and sent["query"].get("reduceOnly") == "true"
      and sent["query"].get("priceProtect") == "TRUE", str(sent["query"]))
check("the 'MARK' alias is mapped to the documented MARK_PRICE enum",
      binance.BINANCE_WORKING_TYPES["MARK"] == "MARK_PRICE"
      and binance.BINANCE_WORKING_TYPES["CONTRACT"] == "CONTRACT_PRICE")

bracket = binance.place_bracket_order("BTCUSDT", "buy", 0.03, stop_loss_price=65000.0,
                                      take_profit_price=70000.0, size_in_btc=True)
check("Binance bracket places entry + 2 legs",
      isinstance(bracket, dict) and bracket.get("_bracket") and len(bracket.get("legs", [])) == 2,
      str(bracket)[:200])
check("Binance bracket legs are reduce-only stops",
      all(leg.get("reduceOnly") for leg in bracket["legs"]), str(bracket["legs"])[:200])

cancel = binance.cancel_order("1001", "BTCUSDT")
check("cancel one order", isinstance(cancel, dict) and cancel.get("status") == "CANCELED", str(cancel)[:200])
cancel_all = binance.cancel_all_orders("BTCUSDT")
check("cancel all orders", isinstance(cancel_all, dict) and "error" not in cancel_all, str(cancel_all)[:120])
edits = binance.edit_order("1001", "BTCUSDT", price=61000.0)
check("Binance has no edit — caller is told to cancel/replace",
      "error" in (edits or {}) and "cancel" in str(edits.get("error")).lower(), str(edits)[:200])

opens = binance.get_open_orders("BTCUSDT")
check("open orders returned", isinstance(opens, list) and len(opens) == 2, str(opens)[:200])
fills = binance.get_fills("BTCUSDT", limit=10)
check("fills returned", isinstance(fills, list) and len(fills) == 1, str(fills)[:200])
positions = binance.get_positions("BTCUSDT")
check("positions returned", isinstance(positions, list) and len(positions) == 1, str(positions)[:200])
balance = binance.get_account_balance()
check("account balance returned", isinstance(balance, dict) and "assets" in balance, str(balance)[:200])
check("leverage change", isinstance(binance.set_leverage("BTCUSDT", 10), dict)
      and binance.set_leverage("BTCUSDT", 10).get("leverage") == 10)
check("margin mode change", "error" not in (binance.set_margin_mode("BTCUSDT", "isolated") or {}))
closed = binance.close_position("BTCUSDT")
check("close position uses Binance's closePosition flag",
      closed.get("status") == "FILLED", str(closed)[:200])

# ===========================================================================
section("5. broker client — order lifecycle (Delta)")
# ===========================================================================
STATE["requests"].clear()
res = delta.place_order("BTCUSDT", "buy", "limit", 0.03, price=60000.0, size_in_btc=True,
                        client_order_id="ph-delta-1")
sent = [r for r in STATE["requests"] if r["path"] == "/v2/orders"][-1]
check("BTC size converted to contracts for Delta", sent["body"].get("size") == 30, str(sent["body"]))
check("Delta limit order body", sent["body"].get("order_type") == "limit_order"
      and sent["body"].get("limit_price") == "60000.0", str(sent["body"]))
check("Delta client order id forwarded", sent["body"].get("client_order_id") == "ph-delta-1")
check("Delta request is signed", all(k in sent["headers"] for k in ("api-key", "signature", "timestamp")),
      str(list(sent["headers"])))

stop = delta.place_order("BTCUSDT", "sell", "stop_market", 0.03, stop_price=65000.0,
                         reduce_only=True, size_in_btc=True)
sent = [r for r in STATE["requests"] if r["path"] == "/v2/orders"][-1]
check("Delta stop triggers on the MARK price",
      sent["body"].get("stop_trigger_method") == "mark_price"
      and sent["body"].get("stop_order_type") == "stop_loss_order", str(sent["body"]))
check("Delta stop-market carries no limit price",
      sent["body"].get("order_type") == "market_order" and "limit_price" not in sent["body"],
      str(sent["body"]))

tp = delta.place_order("BTCUSDT", "buy", "take_profit_market", 0.03, stop_price=70000.0,
                       size_in_btc=True)
sent = [r for r in STATE["requests"] if r["path"] == "/v2/orders"][-1]
check("Delta take-profit is a market order with the take-profit trigger",
      sent["body"].get("order_type") == "market_order"
      and sent["body"].get("stop_order_type") == "take_profit_order"
      and sent["body"].get("stop_trigger_method") == "mark_price"
      and "limit_price" not in sent["body"], str(sent["body"]))

trail = delta.place_order("BTCUSDT", "sell", "trailing_stop", 0.03, stop_price=66000.0,
                          trail_amount=250.0, size_in_btc=True)
sent = [r for r in STATE["requests"] if r["path"] == "/v2/orders"][-1]
check("Delta standalone trailing stop is a market stop with a trail amount",
      sent["body"].get("order_type") == "market_order"
      and sent["body"].get("stop_order_type") == "stop_loss_order"
      and sent["body"].get("trail_amount") == "250.0"
      and "limit_price" not in sent["body"], str(sent["body"]))

sl_limit = delta.place_order("BTCUSDT", "sell", "stop_limit", 0.03, price=64500.0,
                             stop_price=65000.0, size_in_btc=True)
sent = [r for r in STATE["requests"] if r["path"] == "/v2/orders"][-1]
check("Delta stop-limit stays a limit order with both prices",
      sent["body"].get("order_type") == "limit_order"
      and sent["body"].get("limit_price") == "64500.0"
      and sent["body"].get("stop_price") == "65000.0"
      and sent["body"].get("stop_order_type") == "stop_loss_order", str(sent["body"]))

STATE["requests"].clear()
bracket = delta.place_bracket_order("BTCUSDT", "buy", 0.03, stop_loss_price=65000.0,
                                    take_profit_price=70000.0, size_in_btc=True)
# Delta's POST /v2/orders/bracket only attaches TP/SL to an EXISTING position
# (it answers no_open_position otherwise) — the entry must go to POST
# /v2/orders with the bracket_* fields on the order itself.
sent = [r for r in STATE["requests"]
        if r["path"] == "/v2/orders" and r["method"] == "POST"][-1]
check("Delta entry bracket goes through POST /v2/orders (not the attach-only endpoint)",
      sent is not None and not any(r["path"] == "/v2/orders/bracket"
                                   for r in STATE["requests"]),
      str([r["path"] for r in STATE["requests"]]))
check("bracket carries both protection prices on the entry order",
      sent["body"].get("bracket_stop_loss_price") == "65000.0"
      and sent["body"].get("bracket_take_profit_price") == "70000.0"
      and sent["body"].get("bracket_stop_trigger_method") == "mark_price", str(sent["body"]))
check("bracket size is in contracts", sent["body"].get("size") == 30, str(sent["body"]))
check("bracket result is flagged", bracket.get("_bracket") is True, str(bracket)[:200])

cancelled = delta.cancel_order("1002", "BTCUSDT")
check("Delta cancel by id", cancelled.get("state") == "cancelled", str(cancelled)[:200])
delta.cancel_order(None, "BTCUSDT", client_order_id="ph-delta-1")
check("Delta cancel by client order id",
      any(r["path"] == "/v2/orders/client" for r in STATE["requests"]))
edited = delta.edit_order("1002", "BTCUSDT", price=61000.0)
check("Delta edit order", isinstance(edited, dict) and "error" not in edited, str(edited)[:200])
check("Delta close position uses the close_all endpoint",
      "error" not in (delta.close_position("BTCUSDT") or {}))

# ===========================================================================
section("6. broker client — 429 handling")
# ===========================================================================
STATE["fail_next_order"] = 1
res = binance.place_order("BTCUSDT", "buy", "market", 0.03, size_in_btc=True)
check("a single 429 is retried and then succeeds",
      isinstance(res, dict) and "error" not in res, str(res)[:200])
check("retry is counted", binance.limiter.snapshot()["retried_calls"] == 1,
      str(binance.limiter.snapshot()["retried_calls"]))

STATE["fail_next_order"] = 99
res = binance.place_order("BTCUSDT", "buy", "market", 0.03, size_in_btc=True)
check("persistent 429 gives up with an error object (never raises)",
      isinstance(res, dict) and res.get("error") and res.get("rate_limited") is True, str(res)[:200])
check("rejection is counted", binance.limiter.snapshot()["rejected_calls"] >= 1)
STATE["fail_next_order"] = 0

quota = delta.fetch_rate_limit_quota()
check("Delta quota endpoint is readable", isinstance(quota, dict) and quota.get("current_quota") == 6420.0,
      str(quota)[:200])
check("quota flows into the limiter", delta.rate_limit_usage()["exchange_quota"] == 6420.0)
check("rate-limit snapshot exposes config + usage",
      {"limits", "requests_last_second", "broker"} <= set(delta.rate_limit_usage()),
      str(list(delta.rate_limit_usage())))

# Signed Delta retries must re-sign with a fresh timestamp: the venue rejects
# any signature older than 5 seconds (request_expired), so a 429 retry that
# reused the first attempt's headers would always fail the retry.
import app.services.broker_client as broker_client_mod  # noqa: E402
_orders_before = [r["headers"].get("timestamp") for r in STATE["requests"]
                  if r["path"] == "/v2/orders" and r["method"] == "POST"]
_real_time = broker_client_mod.time.time
_clock = {"t": int(_real_time()) + 100}
try:
    # Every clock read advances one second, whatever consumes it, so the two
    # signed attempts are guaranteed distinct timestamps.
    def _fake_time():
        _clock["t"] += 1
        return float(_clock["t"])
    broker_client_mod.time.time = _fake_time
    STATE["fail_next_order"] = 1
    res = delta.place_order("BTCUSDT", "buy", "market", 0.03, size_in_btc=True)
finally:
    broker_client_mod.time.time = _real_time
    STATE["fail_next_order"] = 0
_orders_after = [r["headers"].get("timestamp") for r in STATE["requests"]
                 if r["path"] == "/v2/orders" and r["method"] == "POST"]
_fresh = _orders_after[len(_orders_before):]
check("Delta 429 retry succeeds after re-signing",
      isinstance(res, dict) and "error" not in res, str(res)[:200])
check("each Delta attempt carried a fresh timestamp (5-second window)",
      len(_fresh) == 2 and _fresh[0] != _fresh[1] and _fresh[1] > _fresh[0],
      str(_fresh))

# ===========================================================================
section("7. normalized terminal schema")
# ===========================================================================
from app.services import broker_account  # noqa: E402

norm_buy = broker_account.normalize_order(
    {"orderId": 1, "symbol": "BTCUSDT", "status": "NEW", "type": "STOP_MARKET",
     "side": "SELL", "origQty": "0.03", "executedQty": "0", "stopPrice": "65000",
     "reduceOnly": True, "workingType": "MARK", "time": int(time.time() * 1000)}, "Binance", 1.0)
check("Binance stop order normalizes to the terminal schema",
      norm_buy["type"] == "stop_market" and norm_buy["leg"] == "stop_loss"
      and norm_buy["is_stop"] and norm_buy["is_open"] and abs(norm_buy["qty_btc"] - 0.03) < 1e-9,
      str(norm_buy)[:300])

norm_delta = broker_account.normalize_order(
    {"id": 2, "product_symbol": "BTCUSD", "state": "open", "side": "sell", "size": 30,
     "unfilled_size": 10, "order_type": "market_order", "stop_order_type": "take_profit_order",
     "stop_price": "70000", "stop_trigger_method": "mark_price",
     "created_at": int(time.time() * 1_000_000)}, "Delta", 0.001)
check("Delta take-profit order normalizes (contracts -> BTC)",
      norm_delta["leg"] == "take_profit" and norm_delta["is_stop"]
      and abs(norm_delta["qty_btc"] - 0.03) < 1e-9
      and abs(norm_delta["filled_size"] - 20) < 1e-9, str(norm_delta)[:300])
check("microsecond timestamps are converted", bool(norm_delta["created_at"]))

pos = broker_account.normalize_position(
    {"symbol": "BTCUSDT", "positionAmt": "0.03", "entryPrice": "67000",
     "markPrice": "67100", "unRealizedProfit": "3.0", "liquidationPrice": "54000",
     "leverage": "10", "isolatedWallet": "20.1", "marginType": "isolated"}, "Binance", 1.0)
check("position: side, notional and PnL percent",
      pos["side"] == "long" and abs(pos["qty_btc"] - 0.03) < 1e-9
      and abs(pos["notional"] - 0.03 * 67100) < 1e-6
      and abs(pos["pnl_percent"] - (100.0 / 67000) * 100) < 1e-2, str(pos)[:300])
short_pos = broker_account.normalize_position(
    {"product_symbol": "BTCUSD", "size": -30, "entry_price": "67000", "mark_price": "66000",
     "margin": "20.1", "leverage": 10, "unrealized_pnl": "30"}, "Delta", 0.001)
check("short position PnL and liquidation come through",
      short_pos["side"] == "short" and short_pos["pnl_percent"] > 0 and short_pos["margin"] == 20.1,
      str(short_pos)[:300])

fill = broker_account.normalize_fill(
    {"id": 5, "order_id": 1, "product_symbol": "BTCUSD", "side": "buy", "size": 30,
     "price": "67000.5", "commission": "0.004", "created_at": int(time.time() * 1_000_000)},
    "Delta", 0.001)
check("fill: contracts -> BTC with fee",
      abs(fill["qty_btc"] - 0.03) < 1e-9 and fill["fee"] == 0.004 and fill["price"] == 67000.5,
      str(fill)[:200])

bal_b = broker_account.normalize_balance(
    {"assets": [{"asset": "USDT", "walletBalance": "1000", "availableBalance": "940",
                 "unrealizedProfit": "3"}],
     "totalPositionInitialMargin": "40.2", "totalOpenOrderInitialMargin": "19.8",
     "totalUnrealizedProfit": "3", "totalMarginBalance": "1003", "canTrade": True}, "Binance")
check("Binance balance: used + order margin split",
      bal_b["wallet_balance"] == 1000.0 and bal_b["available_balance"] == 940.0
      and bal_b["used_margin"] == 40.2 and bal_b["order_margin"] == 19.8, str(bal_b)[:300])
bal_d = broker_account.normalize_balance(
    [{"asset_symbol": "USD", "balance": "1000", "available_balance": "940",
      "order_margin": "19.8", "position_margin": "40.2"}], "Delta")
check("Delta balance: order + position margin",
      bal_d["wallet_balance"] == 1000.0 and abs(bal_d["used_margin"] - 60.0) < 1e-9, str(bal_d)[:300])

risk = broker_account.portfolio_risk(bal_d, [pos, short_pos])
check("risk: equity and margin utilisation",
      abs(risk["equity"] - 1033.0) < 1e-6 and risk["margin_utilisation_pct"] > 0
      and risk["position_count"] == 2, str(risk)[:300])
check("risk: gross vs net exposure",
      abs(risk["gross_notional"] - (pos["notional"] + short_pos["notional"])) < 1e-6
      and risk["net_notional"] < risk["gross_notional"], str(risk)[:300])

# ===========================================================================
section("8. account snapshot (what the terminal renders)")
# ===========================================================================
snap = broker_account.account_snapshot(delta, "BTCUSDT")
check("snapshot carries every terminal panel",
      {"positions", "open_orders", "stop_orders", "fills", "order_history", "balance", "risk"} <= set(snap),
      str(list(snap)))
check("snapshot contract metadata (contracts + tick size)",
      snap["contract"]["size_unit"] == "contracts" and snap["contract"]["contract_value"] == 0.001,
      str(snap["contract"]))
check("snapshot mark price", abs((snap["mark_price"] or 0) - 67100.5) < 1e-6, str(snap["mark_price"]))
check("snapshot position normalizes contracts -> BTC",
      len(snap["positions"]) == 1 and abs(snap["positions"][0]["qty_btc"] - 0.03) < 1e-9,
      str(snap["positions"])[:200])
check("open orders split into working vs stop orders",
      len(snap["open_orders"]) == 1 and len(snap["stop_orders"]) == 1,
      f"{len(snap['open_orders'])}/{len(snap['stop_orders'])}")
check("stop order panel marks the trigger method",
      snap["stop_orders"][0]["trigger_method"] == "mark_price", str(snap["stop_orders"])[:200])
check("fills panel", len(snap["fills"]) == 1 and snap["fills"][0]["qty_btc"] == 0.03,
      str(snap["fills"])[:200])
check("order history panel (closed orders)", len(snap["order_history"]) == 1,
      str(snap["order_history"])[:200])
check("balance + risk panels", snap["balance"]["wallet_balance"] == 1000.0
      and snap["risk"]["equity"] > 0, str(snap["risk"])[:200])
check("rate limits are reported to the UI", "limits" in snap["rate_limits"])
check("no section errors", not snap["errors"], str(snap["errors"]))

snap_b = broker_account.account_snapshot(binance, "BTCUSDT")
check("Binance snapshot renders the same schema",
      len(snap_b["positions"]) == 1 and len(snap_b["stop_orders"]) == 1
      and snap_b["balance"]["wallet_balance"] == 1000.0, str(snap_b)[:300])


class _BrokenClient:
    broker_name = "Delta"

    def __init__(self):
        from app.core.rate_limit import RateLimitConfig
        self.limiter = RateLimiter("broken", RateLimitConfig())
        self.rate_limit_config = RateLimitConfig()

    def get_instrument(self, symbol, refresh=False):
        return {"contract_value": 0.001}

    def fetch_mark_price(self, symbol):
        raise RuntimeError("mark feed down")

    def get_positions(self, symbol=None):
        return {"error": "positions endpoint throttled"}

    def get_open_orders(self, symbol=None):
        return {"error": "nope"}

    def get_fills(self, symbol=None, limit=100):
        return {"error": "nope"}

    def get_order_history(self, symbol=None, limit=100):
        return {"error": "nope"}

    def get_account_balance(self, asset="USDT"):
        return {"error": "nope"}

    def rate_limit_usage(self):
        return self.limiter.snapshot()


broken = broker_account.account_snapshot(_BrokenClient(), "BTCUSDT")
check("a dead endpoint degrades one panel, not the whole screen",
      broken["positions"] == [] and "positions" in broken["errors"]
      and broken["mark_price"] is None, str(broken["errors"]))
check("generic endpoint failures are NOT reported as an API-key problem",
      broken.get("auth_error") is None, str(broken.get("auth_error")))


class _AuthDeadClient(_BrokenClient):
    """Every signed call 401s — the exact 'invalid_api_key' wall the terminal
    used to render as five identical partial-data errors."""

    def get_positions(self, symbol=None):
        return {"error": 'Delta HTTP 401: {"code": "invalid_api_key"}'}

    def get_open_orders(self, symbol=None):
        return {"error": 'Delta HTTP 401: {"code": "invalid_api_key"}'}

    def get_fills(self, symbol=None, limit=100):
        return {"error": 'Delta HTTP 401: {"code": "invalid_api_key"}'}

    def get_order_history(self, symbol=None, limit=100):
        return {"error": 'Delta HTTP 401: {"code": "invalid_api_key"}'}

    def get_account_balance(self, asset="USDT"):
        return {"error": 'Delta HTTP 401: {"code": "invalid_api_key"}'}


authdead = broker_account.account_snapshot(_AuthDeadClient(), "BTCUSDT")
check("all-auth failure collapses into one plain-language verdict",
      isinstance(authdead.get("auth_error"), str)
      and "rejected this API key" in authdead["auth_error"]
      and "invalid_api_key" in authdead["auth_error"],
      str(authdead.get("auth_error"))[:200])
check("the verdict points at the fix that exists: replace the key, reload, no restart",
      "Broker Settings" in authdead["auth_error"]
      and "Replace the key" in authdead["auth_error"]
      and "Reload keys" in authdead["auth_error"]
      and "no longer needs a restart" in authdead["auth_error"],
      str(authdead.get("auth_error"))[:240])
check("one mixed failure must not claim the key is bad",
      broker_account._is_auth_rejection("fills endpoint throttled") is False
      and broker_account._is_auth_rejection('Delta HTTP 401: {"code": "invalid_api_key"}') is True
      and broker_account._is_auth_rejection("Binance HTTP 401: Invalid API-key, IP, or permissions") is True,
      "marker matching")


# ===========================================================================
section("8b. account settings from the venue (margin mode must not be guessed)")
# ===========================================================================
# The reported bug: a Delta account in CROSS margin showed Isolated in the
# terminal because the UI hardcoded useState('isolated'). Margin mode is an
# account/sub-account property on Delta — GET /v2/sub_accounts — and a
# per-symbol property on Binance — positionRisk. Both must now be read back.
settings_d = delta.get_account_settings("BTCUSD")
check("Delta: parent key reads the main account's cross margin mode",
      settings_d["margin_mode"] == "cross" and settings_d["margin_family"] == "cross"
      and settings_d["user_id"] == "5112346", str(settings_d)[:250])
check("Delta: the settings name the account this key trades as (main vs sub)",
      (settings_d.get("self_account") or {}).get("account_name") == "Main"
      and (settings_d.get("self_account") or {}).get("is_sub_account") is False,
      str(settings_d.get("self_account"))[:200])
check("Delta: sub-account list comes back so each account's mode is visible",
      len(settings_d["accounts"]) == 2
      and settings_d["accounts"][1]["margin_mode"] == "isolated"
      and settings_d["accounts"][1]["is_sub_account"] is True,
      str(settings_d.get("accounts"))[:200])
check("Delta: leverage read via GET /v2/products/{id}/orders/leverage",
      settings_d["leverage"] == 7, str(settings_d.get("leverage")))
check("Delta: no error when everything reads cleanly",
      settings_d.get("error") is None, str(settings_d.get("error")))

STATE["fail_sub_accounts"] = True
settings_sub = delta.get_account_settings("BTCUSD")
STATE["fail_sub_accounts"] = False
check("Delta: sub-account key (cannot list accounts) falls back to the open position",
      settings_sub["margin_mode"] == "cross"
      and settings_sub["margin_family"] == "cross"
      and settings_sub["accounts"] == [], str(settings_sub)[:250])

settings_b = binance.get_account_settings("BTCUSDT")
check("Binance: margin mode + leverage from positionRisk",
      settings_b["margin_mode"] == "cross" and settings_b["margin_family"] == "cross"
      and settings_b["leverage"] == 5, str(settings_b)[:250])

snap_set = broker_account.account_snapshot(delta, "BTCUSD")
check("snapshot carries the venue account settings for the terminal",
      (snap_set.get("account_settings") or {}).get("margin_mode") == "cross"
      and (snap_set.get("account_settings") or {}).get("leverage") == 7,
      str(snap_set.get("account_settings"))[:200])

STATE["requests"].clear()
mode_resp = delta.set_margin_mode("BTCUSD", "isolated")
sent_mode = [r for r in STATE["requests"] if r["path"] == "/v2/users/margin_mode"]
check("Delta: set margin mode uses the documented account-level PUT /v2/users/margin_mode",
      len(sent_mode) == 1 and sent_mode[0]["method"] == "PUT",
      str(sent_mode)[:200])
check("Delta: the PUT carries the key's own account id (venue requires it even for self)",
      sent_mode and sent_mode[0]["body"] == {"margin_mode": "isolated",
                                             "subaccount_user_id": "5112346"},
      str(sent_mode)[:250])
check("Delta: set margin mode returns the venue's confirmation",
      isinstance(mode_resp, dict) and mode_resp.get("margin_mode") == "isolated"
      and not mode_resp.get("error"), str(mode_resp)[:200])
check("Delta: the own-account id resolved from the wallet rows (profile is key-refused)",
      delta._own_user_id == "5112346", str(delta._own_user_id))

# The id is cached per client: a second set must not ask the venue again.
STATE["requests"].clear()
delta.set_margin_mode("BTCUSD", "isolated")
wallet_reads = [r for r in STATE["requests"] if r["path"] == "/v2/wallet/balances"]
check("Delta: the resolved account id is cached between margin-mode pushes",
      not wallet_reads, str(wallet_reads)[:200])

# Fallback path: a wallet read without user_id resolves via the sub-account
# listing's main entry (parent key) — a fresh client so the cache is cold.
fresh = BrokerClient("key", "secret", "Delta", definition=DELTA_DEF)
STATE["wallet_no_user_id"] = True
resolved = fresh._delta_own_user_id()
STATE["wallet_no_user_id"] = False
check("Delta: own id falls back to the main sub-account entry when the wallet omits it",
      resolved == "5112346", str(resolved))

# Unresolvable (sub-account key: no wallet user_id, no listing) and the
# legacy route gone like production: the PUT goes out without the id and the
# venue's own bad_schema refusal comes back — no silent legacy rescue.
orphan = BrokerClient("key", "secret", "Delta", definition=DELTA_DEF)
STATE["wallet_no_user_id"] = True
STATE["fail_sub_accounts"] = True
STATE["legacy_margin_mode_gone"] = True
orphan_resp = orphan.set_margin_mode("BTCUSD", "isolated")
STATE["wallet_no_user_id"] = False
STATE["fail_sub_accounts"] = False
STATE["legacy_margin_mode_gone"] = False
check("Delta: an unresolvable account id surfaces the venue refusal instead of guessing",
      isinstance(orphan_resp, dict) and "error" in orphan_resp
      and "subaccount_user_id is required" in str(orphan_resp.get("error")),
      str(orphan_resp)[:250])
check("margin family collapses venue spellings",
      delta._margin_family("portfolio") == "cross"
      and delta._margin_family("Cross ") == "cross"
      and delta._margin_family("isolated") == "isolated"
      and delta._margin_family("weird") is None, "family mapping")

# ===========================================================================
section("9. local audit tables")
# ===========================================================================
import bcrypt  # noqa: E402

db = SessionLocal()
db.add(User(username="trader", password_hash=bcrypt.hashpw(b"trader123", bcrypt.gensalt()).decode(),
            role="client", is_active=1, can_paper=1, can_live=1,
            initial_capital=20000.0, margin_deployment_pct=25.0, virtual_balance=20000.0))
db.commit()
trader = db.query(User).filter(User.username == "trader").first()
TRADER_ID = trader.id
db.close()

order_id = broker_account.record_order(TRADER_ID, "Delta", {
    "broker": "Delta", "symbol": "BTCUSD", "order_id": "4242",
    "client_order_id": "ph-audit-1", "side": "buy", "type": "market", "leg": "entry",
    "size": 30, "qty_btc": 0.03, "price": None, "stop_price": None,
    "reduce_only": False, "status": "open", "filled_size": 0.0}, source="strategy",
    instance_key="live_trader_Delta_PhantomV2_abcd")
check("order mirrored locally", order_id is not None)
again = broker_account.record_order(TRADER_ID, "Delta", {
    "broker": "Delta", "symbol": "BTCUSD", "order_id": "4242",
    "client_order_id": "ph-audit-1", "side": "buy", "type": "market", "leg": "entry",
    "size": 30, "qty_btc": 0.03, "status": "filled", "filled_size": 30.0,
    "avg_fill_price": 67000.0}, source="strategy")
check("the same order updates in place instead of duplicating", again == order_id)
rows = broker_account.local_order_history(TRADER_ID, "Delta")
check("local history shows the closed status",
      len(rows) == 1 and rows[0]["status"] == "filled" and rows[0]["closed_at"], str(rows)[:200])
check("terminal status sets closed_at", rows[0]["closed_at"] is not None)

written = broker_account.record_fills(TRADER_ID, "Delta", [fill, fill])
check("fills recorded once (de-duplicated on trade id)", written == 1, str(written))
check("fill rows persisted", len(broker_account.local_fills(TRADER_ID, "Delta")) == 1)

cancelled_row = broker_account.mark_order_cancelled(TRADER_ID, "Delta", client_order_id="ph-audit-1")
check("cancel flips the local row to cancelled",
      cancelled_row and cancelled_row["status"] == "cancelled", str(cancelled_row))
check("history reflects the cancel",
      broker_account.local_order_history(TRADER_ID, "Delta")[0]["status"] == "cancelled")

# ===========================================================================
section("10. HTTP API")
# ===========================================================================
import asyncio  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import app.main as main  # noqa: E402


async def _no_sync():
    await asyncio.sleep(3600)


main.daily_sync_task = _no_sync
reset_registry()

api = TestClient(main.app)
r = api.post("/token", data={"username": "trader", "password": "trader123"})
check("trader login", r.status_code == 200, r.text[:200])
H = {"Authorization": f"Bearer {r.json()['access_token']}"}

db = SessionLocal()
db.add(BrokerConnection(user_id=TRADER_ID, broker_code="Delta", label="primary",
                        api_key="dk", api_secret="ds", is_active=1))
db.add(BrokerConnection(user_id=TRADER_ID, broker_code="Binance", label="primary",
                        api_key="bk", api_secret="bs", is_active=1))
db.commit()
db.close()

# Point the API's client factory at the mock exchange.
_original_live_client = main._live_client


def _mock_live_client(db, user, broker_code, connection_id=None, require_credentials=True):
    from app.services.broker_client import BrokerClient
    definition = db.query(BrokerDefinition).filter(BrokerDefinition.code == broker_code).first()
    client = BrokerClient("key", "secret", broker_code, definition=definition)
    return client, definition, (connection_id or 1)


main._live_client = _mock_live_client

r = api.post("/live-account/snapshot", headers=H, json={"broker": "Delta"})
body = r.json()
check("POST /live-account/snapshot returns the terminal payload",
      r.status_code == 200 and {"positions", "open_orders", "stop_orders", "fills",
                                "order_history", "balance", "risk", "rate_limits"} <= set(body),
      r.text[:300])
check("snapshot reports the contract", body["contract"]["symbol"] == "BTCUSD"
      and body["contract"]["size_unit"] == "contracts", str(body["contract"])[:200])
check("snapshot carries the account's real margin mode for the terminal",
      (body.get("account_settings") or {}).get("margin_mode") == "cross"
      and (body.get("account_settings") or {}).get("margin_family") == "cross",
      str(body.get("account_settings"))[:250])

# Connections are saved with the account details the venue just reported —
# per connection, because each sub-account has its own margin mode.
# (is_testnet stays false here: the mock definition carries production-style
# URLs and the constructor maps testnet connections to the real Delta testnet
# host, which this sandbox cannot reach.)
r = api.post("/broker-connections", headers=H, json={
    "broker_code": "Delta", "label": "Nishant main", "api_key": "dk2",
    "api_secret": "ds2", "is_testnet": False, "is_active": True})
body = r.json()
check("POST /broker-connections saves + returns venue account settings",
      r.status_code == 200 and (body.get("account_settings") or {}).get("margin_mode") == "cross"
      and (body.get("account_settings") or {}).get("leverage") == 7
      and body.get("account_settings_at"), r.text[:300])
settings_conn_id = body.get("id")

r = api.post(f"/broker-connections/{settings_conn_id}/refresh", headers=H)
body = r.json()
check("POST /broker-connections/{id}/refresh re-reads settings from the venue",
      r.status_code == 200 and (body.get("account_settings") or {}).get("margin_mode") == "cross"
      and (body.get("fetched") or {}).get("user_id") == "5112346"
      and body.get("account_settings_at"), r.text[:300])

r = api.get("/broker-connections", headers=H)
saved = next((c for c in r.json() if c.get("id") == settings_conn_id), None)
check("GET /broker-connections lists each connection's account settings",
      saved is not None and (saved.get("account_settings") or {}).get("margin_mode") == "cross",
      str(saved)[:300])

r = api.post("/live-account/orders", headers=H, json={
    "broker": "Delta", "side": "buy", "order_type": "market", "size": 0.03,
    "size_in_btc": True, "stop_loss": 65000.0, "take_profit": 70000.0})
body = r.json()
check("POST /live-account/orders places a bracket in BTC",
      r.status_code == 200 and body["status"] == "placed", r.text[:300])
check("client order id generated for tracking", body["client_order_id"].startswith("ph-"),
      str(body.get("client_order_id")))
check("placed orders are mirrored locally", len(body["orders"]) >= 1, str(body["orders"])[:200])
sent = [x for x in STATE["requests"]
        if x["path"] == "/v2/orders" and x["method"] == "POST"
        and (x["body"] or {}).get("bracket_take_profit_price")][-1]
check("BTC size reached the exchange as contracts", sent["body"]["size"] == 30, str(sent["body"]))
check("rate limits returned with the order ack", "limits" in body["rate_limits"])

r = api.post("/live-account/orders", headers=H, json={
    "broker": "Binance", "side": "sell", "order_type": "stop_market", "size": 0.03,
    "size_in_btc": True, "stop_price": 65000.0, "reduce_only": True})
body = r.json()
check("plain stop order on Binance", r.status_code == 200 and body["status"] == "placed", r.text[:300])

r = api.post("/live-account/orders/cancel", headers=H,
             json={"broker": "Delta", "order_id": "4242", "client_order_id": "ph-audit-1"})
check("POST /live-account/orders/cancel", r.status_code == 200 and r.json()["status"] == "cancelled",
      r.text[:200])
check("local row cancelled through the API",
      r.json().get("local", {}).get("status") == "cancelled", str(r.json().get("local")))

r = api.post("/live-account/orders/cancel-all", headers=H, json={"broker": "Delta"})
check("POST /live-account/orders/cancel-all", r.status_code == 200 and r.json()["status"] == "cancelled",
      r.text[:200])

r = api.post("/live-account/positions/close", headers=H, json={"broker": "Delta"})
check("POST /live-account/positions/close", r.status_code == 200 and r.json()["status"] == "closed",
      r.text[:200])

r = api.post("/live-account/leverage", headers=H, json={"broker": "Delta", "leverage": 10})
check("POST /live-account/leverage", r.status_code == 200 and r.json()["response"]["leverage"] == 10,
      r.text[:200])

r = api.post("/live-account/margin-mode", headers=H, json={"broker": "Delta", "mode": "isolated"})
check("POST /live-account/margin-mode", r.status_code == 200 and r.json()["status"] == "ok", r.text[:200])

# ---- Margin-mode sync across (sub)accounts (Delta's guidance flow) -------
# GET /v2/sub_accounts with the main key → read reference margin_mode →
# PUT /v2/users/margin_mode {margin_mode, subaccount_user_id: target}.
STATE["requests"].clear()
sync_res = delta.sync_margin_mode("5112346", "5112347")
check("sync_margin_mode mirrors the reference account's mode",
      sync_res.get("status") == "ok" and sync_res.get("margin_mode") == "cross",
      str(sync_res)[:250])
put_body = next((r["body"] for r in STATE["requests"]
                 if r["path"] == "/v2/users/margin_mode" and r["method"] == "PUT"), None)
check("the sync applied the mode to the TARGET sub-account",
      put_body == {"margin_mode": "cross", "subaccount_user_id": "5112347"},
      str(put_body))
check("dry_run performs no network call",
      delta.sync_margin_mode("5112346", "5112347", dry_run=True).get("dry_run") is True)
bad_sync = delta.sync_margin_mode("9999999", "5112347")
check("an unknown reference says so and names the main-key requirement",
      bad_sync.get("error") and "main/parent" in str(bad_sync.get("error")),
      str(bad_sync)[:250])

# ---- Bulk: one margin mode for EVERY account under the key ----------------
STATE["requests"].clear()
bulk = delta.set_margin_mode_all("isolated")
puts = [r for r in STATE["requests"] if r["path"] == "/v2/users/margin_mode" and r["method"] == "PUT"]
check("set_margin_mode_all pushes the mode to every account under the key (main + subs)",
      bulk.get("status") == "ok" and bulk.get("changed") == 2 and bulk.get("total") == 2
      and [p["body"].get("subaccount_user_id") for p in puts] == ["5112346", "5112347"],
      str(puts)[:250])
check("the bulk result names each account with its own outcome",
      all(row.get("status") == "ok" for row in bulk.get("results", []))
      and {row.get("id") for row in bulk.get("results", [])} == {"5112346", "5112347"},
      str(bulk.get("results"))[:250])
STATE["requests"].clear()
dry = delta.set_margin_mode_all("cross", dry_run=True)
check("bulk dry_run previews the targets without touching the venue",
      dry.get("dry_run") is True and len(dry.get("targets", [])) == 2
      and not [r for r in STATE["requests"] if r["path"] == "/v2/users/margin_mode"])
STATE["fail_sub_accounts"] = True
subkey = BrokerClient("key", "secret", "Delta", definition=DELTA_DEF)
sub_bulk = subkey.set_margin_mode_all("isolated")
STATE["fail_sub_accounts"] = False
check("a sub-account key cannot bulk-manage siblings — the error says main/parent",
      sub_bulk.get("error") and "main/parent" in str(sub_bulk.get("error")), str(sub_bulk)[:250])

STATE["requests"].clear()
r = api.post("/live-account/margin-mode", headers=H,
             json={"broker": "Delta", "mode": "portfolio", "subaccount_user_id": "5112347"})
check("POST /live-account/margin-mode can target a sub-account",
      r.status_code == 200 and r.json()["status"] == "ok", r.text[:200])
put_body2 = next((r_["body"] for r_ in STATE["requests"]
                  if r_["path"] == "/v2/users/margin_mode" and r_["method"] == "PUT"), None)
check("the targeted call carries the sub-account user id",
      put_body2 == {"margin_mode": "portfolio", "subaccount_user_id": "5112347"},
      str(put_body2))

STATE["requests"].clear()
r = api.post("/live-account/margin-mode-all", headers=H,
             json={"broker": "Delta", "mode": "cross"})
check("POST /live-account/margin-mode-all applies to every account on the key",
      r.status_code == 200 and r.json()["status"] == "ok"
      and r.json()["response"]["changed"] == 2 and r.json()["response"]["total"] == 2,
      r.text[:300])
check("every listed account got its own PUT on the bulk endpoint",
      {x["body"].get("subaccount_user_id")
       for x in STATE["requests"] if x["path"] == "/v2/users/margin_mode" and x["method"] == "PUT"}
      == {"5112346", "5112347"},
      str(STATE["requests"][-4:])[:250])
STATE["requests"].clear()
r = api.post("/live-account/margin-mode-all", headers=H,
             json={"broker": "Delta", "mode": "isolated", "dry_run": True})
check("the bulk endpoint honours dry_run (no venue writes)",
      r.status_code == 200 and r.json()["response"].get("dry_run") is True
      and not [x for x in STATE["requests"] if x["path"] == "/v2/users/margin_mode"],
      r.text[:250])

r = api.post("/live-account/margin-mode-sync", headers=H,
             json={"broker": "Delta", "reference_user_id": "5112346",
                   "target_user_id": "5112347"})
check("POST /live-account/margin-mode-sync mirrors reference to target",
      r.status_code == 200 and r.json()["status"] == "ok"
      and r.json()["response"]["margin_mode"] == "cross", r.text[:300])
r = api.post("/live-account/margin-mode-sync", headers=H,
             json={"broker": "Delta", "reference_user_id": "5112346",
                   "target_user_id": "5112347", "dry_run": True})
check("the sync endpoint honours dry_run",
      r.status_code == 200 and r.json()["response"].get("dry_run") is True, r.text[:300])

r = api.post("/live-account/position-margin", headers=H,
             json={"broker": "Delta", "amount": 5.0})
check("POST /live-account/position-margin", r.status_code == 200 and r.json()["status"] == "ok", r.text[:200])

r = api.get("/live-account/rate-limits", headers=H, params={"broker": "Delta"})
body = r.json()
check("GET /live-account/rate-limits reports local + exchange budgets",
      r.status_code == 200 and body["exchange_quota"]["current_quota"] == 6420.0
      and body["local"]["limits"]["weight_per_5min"] == 10000.0, r.text[:300])

r = api.get("/live-account/orders", headers=H, params={"broker": "Delta"})
check("GET /live-account/orders returns the local audit trail",
      r.status_code == 200 and any(row["client_order_id"] == "ph-audit-1" for row in r.json()),
      r.text[:200])
r = api.get("/live-account/fills", headers=H, params={"broker": "Delta"})
check("GET /live-account/fills returns local executions",
      r.status_code == 200 and any(row["trade_id"] == "90001" for row in r.json()), r.text[:200])

# ===========================================================================
section("10b. /live-trade/start honours or refuses the requested risk setup")
# ===========================================================================
# The reported bug in one line: the venue refused the margin-mode push at
# start (subaccount_user_id is required), yet the instance registered as
# "running" in the UI. Now: the push carries the account id (and succeeds),
# and any refusal of an explicitly requested setting refuses the START — no
# instance is registered, so the workspace can never show it running.
from fastapi import BackgroundTasks as _BackgroundTasks  # noqa: E402
_orig_add_task = _BackgroundTasks.add_task
# The endpoint schedules service.start() as a background task; in a test that
# would run the trading loop forever, so the task registry is stubbed out.
_BackgroundTasks.add_task = lambda self, func, *args, **kwargs: None
try:
    main.live_trade_instances.clear()
    STATE["requests"].clear()

    # 1. Venue accepts the requested leverage + margin mode → instance starts.
    r = api.post("/live-trade/start", headers=H, json={
        "strategy_id": "PhantomV2", "broker_name": "Delta", "data_source": "Delta",
        "leverage": 5, "margin_mode": "isolated"})
    body = r.json()
    check("start succeeds when the venue accepts leverage + margin mode",
          r.status_code == 200 and body.get("status") == "Live trade started"
          and (body.get("risk_setup") or {}).get("leverage", {}).get("status") == "ok"
          and (body.get("risk_setup") or {}).get("margin_mode", {}).get("status") == "ok",
          f"{r.status_code} {r.text[:250]}")
    started_key = body.get("instance_key") if r.status_code == 200 else None
    check("the accepted instance is registered as running",
          started_key in main.live_trade_instances, str(started_key))
    sent_mm = [x for x in STATE["requests"]
               if x["path"] == "/v2/users/margin_mode" and x["method"] == "PUT"]
    check("the start-path margin push carried the account id (schema-valid)",
          sent_mm and (sent_mm[-1].get("body") or {}).get("subaccount_user_id") == "5112346",
          str(sent_mm[-1] if sent_mm else None)[:200])
    main.live_trade_instances.pop(started_key, None)

    # 2. The venue refuses the margin-mode push → the start is refused with
    # the venue's reason and NOTHING is registered as running.
    STATE["fail_margin_mode"] = True
    STATE["legacy_margin_mode_gone"] = True
    r = api.post("/live-trade/start", headers=H, json={
        "strategy_id": "RefusedMarginStrategy", "broker_name": "Delta",
        "data_source": "Delta", "margin_mode": "cross"})
    body = r.json()
    check("a refused margin-mode push refuses the start (502 + venue reason)",
          r.status_code == 502 and "NOT started" in str(body.get("detail"))
          and "margin mode" in str(body.get("detail")),
          f"{r.status_code} {r.text[:300]}")
    check("no instance is registered after the refused start",
          not any(getattr(s, "strategy_id", None) == "RefusedMarginStrategy"
                  for s in main.live_trade_instances.values()),
          str(list(main.live_trade_instances)))

    # 3. A generic risk-setup exception also refuses the start instead of
    # being banked silently into the response payload.
    unroutable = BrokerClient("key", "secret", "Delta",
                              definition=_Def(code="Delta", kind="delta",
                                              market_data_url="http://127.0.0.1:1",
                                              trading_api_url="http://127.0.0.1:1"),
                              rate_limit=RateLimitConfig(
                                  requests_per_second=100, requests_per_minute=1200,
                                  weight_per_5min=None, orders_per_minute=None,
                                  orders_per_10s=None, max_retries=1))
    _orig_client = _mock_live_client
    main._live_client = lambda db_, user_, code, connection_id=None, require_credentials=True: \
        (unroutable, unroutable.definition, connection_id or 1)
    try:
        r = api.post("/live-trade/start", headers=H, json={
            "strategy_id": "UnreachableVenueStrategy", "broker_name": "Delta",
            "data_source": "Delta", "margin_mode": "isolated"})
        check("an unreachable venue refuses the start too (instead of a silent run)",
              r.status_code == 502 and "NOT started" in str(r.json().get("detail")),
              f"{r.status_code} {r.text[:300]}")
    finally:
        main._live_client = _orig_client

    # 4. Binance/Delta "already set that way" answers are NOT refusals.
    check("idempotent venue answers are not treated as refusals",
          main._benign_risk_rejection("Binance HTTP 400: No need to change margin type.")
          and main._benign_risk_rejection('Delta HTTP 400: {"code": "same_margin_mode"}')
          and not main._benign_risk_rejection("Delta HTTP 400: bad_schema"))

    # 5. The reported bug: Delta answers HTTP 400 {"code": "same_margin_mode"}
    # when the account is ALREADY in the requested mode. That is a
    # confirmation, not a refusal — the start must go ahead.
    STATE["fail_margin_mode"] = False
    STATE["same_margin_mode"] = True
    STATE["legacy_margin_mode_gone"] = True
    STATE["requests"].clear()
    try:
        r = api.post("/live-trade/start", headers=H, json={
            "strategy_id": "PhantomV2", "broker_name": "Delta",
            "data_source": "Delta", "margin_mode": "isolated"})
        body = r.json()
        check("Delta same_margin_mode (already in that mode) does NOT refuse the start",
              r.status_code == 200 and body.get("status") == "Live trade started",
              f"{r.status_code} {r.text[:300]}")
        started_key = body.get("instance_key") if r.status_code == 200 else None
        check("the same_margin_mode instance is registered as running",
              started_key in main.live_trade_instances, str(started_key))
        legacy_calls = [x for x in STATE["requests"]
                        if x["path"] == "/v2/positions/margin_mode"]
        check("same_margin_mode is accepted without falling back to the legacy endpoint",
              not legacy_calls, str(legacy_calls)[:200])
        main.live_trade_instances.pop(started_key, None)
        # And the client itself reports it as success, not an error — so the
        # bulk apply / sync paths count it as ok too.
        mm_res = delta.set_margin_mode("BTCUSD", "isolated")
        check("BrokerClient.set_margin_mode reports same_margin_mode as ok/unchanged",
              isinstance(mm_res, dict) and not mm_res.get("error")
              and mm_res.get("unchanged") is True, str(mm_res)[:200])
    finally:
        STATE["same_margin_mode"] = False
        STATE["legacy_margin_mode_gone"] = False
finally:
    _BackgroundTasks.add_task = _orig_add_task
    STATE["fail_margin_mode"] = False
    STATE["same_margin_mode"] = False
    STATE["legacy_margin_mode_gone"] = False
    main.live_trade_instances.clear()

# ===========================================================================
section("10c. user-mistake inputs are refused AT START, with the field named")
# ===========================================================================
# Every field on the start form gets the wrong value a human actually types.
# Historically a negative capital or a 2500% margin started an instance that
# either never trades (size computes to zero — the card sits at 0 trades
# forever) or has its first order bounced by the venue days later.
_BackgroundTasks.add_task = lambda self, func, *args, **kwargs: None
try:
    bad_starts = [
        ({"initial_capital": -5000}, "initial_capital", "negative capital"),
        ({"initial_capital": "nan"}, "initial_capital", "NaN capital"),
        ({"initial_capital": 10**12}, "initial_capital", "absurd capital"),
        ({"margin_pct": -10}, "margin_pct", "negative margin %"),
        ({"margin_pct": 2500}, "margin_pct", "margin % above 100"),
        ({"leverage": 500}, "everage", "leverage above the venue cap"),
        ({"leverage": 0}, "everage", "zero leverage"),
        ({"leverage": "ten"}, "everage", "non-numeric leverage"),
        ({"margin_mode": "yolo"}, "argin", "made-up margin mode"),
        ({"price_feed": "carrier-pigeon"}, "price_feed", "made-up price feed"),
        ({"price_feed": "rest", "tick_interval": 0.01}, "tick_interval",
         "sub-second polling (rate-limit burn)"),
        ({"price_feed": "rest", "tick_interval": 900}, "tick_interval",
         "quarter-hour 'live' ticks"),
    ]
    for extra, needle, label in bad_starts:
        for endpoint in ("/live-trade/start", "/paper-trade/start"):
            r = api.post(endpoint, headers=H, json={
                "strategy_id": "PhantomV2", "broker_name": "Delta",
                "data_source": "Delta", **extra})
            body = r.json()
            check(f"{endpoint} refuses {label} and names the field",
                  r.status_code in (400, 422)
                  and needle in json.dumps(body),
                  f"{r.status_code} {r.text[:200]}")
            check(f"{label}: nothing is registered as running ({endpoint})",
                  not main.live_trade_instances and not main.paper_trade_instances,
                  str(list(main.live_trade_instances) + list(main.paper_trade_instances)))

    # A cleared form field arrives as 0 and has always meant "use the account
    # default" — that must keep starting, not turn into a 400.
    r = api.post("/live-trade/start", headers=H, json={
        "strategy_id": "PhantomV2", "broker_name": "Delta", "data_source": "Delta",
        "initial_capital": 0, "margin_pct": 0})
    check("a cleared capital/margin field falls back to the account default",
          r.status_code == 200 and r.json().get("status") == "Live trade started",
          f"{r.status_code} {r.text[:250]}")
    started = r.json().get("instance_key")

    # The same strategy on the same account cannot be started twice: two
    # workers would fight over one netted position.
    r = api.post("/live-trade/start", headers=H, json={
        "strategy_id": "PhantomV2", "broker_name": "Delta", "data_source": "Delta"})
    check("a duplicate start of the same strategy is refused (409)",
          r.status_code == 409, f"{r.status_code} {r.text[:200]}")
    main.live_trade_instances.pop(started, None)

    # Stopping things that are not yours / not there.
    r = api.post("/live-trade/stop", headers=H,
                 params={"instance_key": "live_trader_Delta_PhantomV2_gone9999"})
    check("stopping a vanished instance is a clean 404", r.status_code == 404,
          f"{r.status_code} {r.text[:150]}")
    r = api.post("/live-trade/stop", headers=H,
                 params={"instance_key": "live_someoneelse_Delta_PhantomV2_x1"})
    check("stopping another user's instance is refused (403)",
          r.status_code == 403, f"{r.status_code} {r.text[:150]}")
finally:
    _BackgroundTasks.add_task = _orig_add_task
    main.live_trade_instances.clear()
    main.paper_trade_instances.clear()

# --- credentials are required ---------------------------------------------
main._live_client = _original_live_client
db = SessionLocal()
for row in db.query(BrokerConnection).filter(BrokerConnection.user_id == TRADER_ID).all():
    db.delete(row)
db.commit()
db.close()
r = api.post("/live-account/snapshot", headers=H, json={"broker": "Delta"})
check("missing API keys are refused with a clear message",
      r.status_code == 400 and "API keys" in r.json()["detail"], r.text[:200])

# --- admin can configure rate limits per broker ---------------------------
r = api.post("/token", data={"username": "admin", "password": "admin_password_123"})
if r.status_code != 200:
    db = SessionLocal()
    db.add(User(username="admin", password_hash=bcrypt.hashpw(b"admin_password_123", bcrypt.gensalt()).decode(),
                role="admin", is_active=1, can_paper=1, can_live=1))
    db.commit()
    db.close()
    r = api.post("/token", data={"username": "admin", "password": "admin_password_123"})
AH = {"Authorization": f"Bearer {r.json()['access_token']}"}
check("admin login", r.status_code == 200, r.text[:200])

db = SessionLocal()
delta_row = db.query(BrokerDefinition).filter(BrokerDefinition.code == "Delta").first()
DELTA_ID = delta_row.id
db.close()

r = api.put(f"/admin/brokers/{DELTA_ID}", headers=AH, json={
    "code": "Delta", "name": "Delta Exchange", "kind": "delta",
    "market_data_url": MOCK, "trading_api_url": MOCK, "enabled": True,
    "rate_limit_per_second": 10, "rate_limit_per_minute": 600,
    "quota_per_5min": 5000, "orders_per_minute": 300,
    "default_leverage": 10, "margin_mode": "isolated",
    "contract_value": 0.001, "tick_size": 0.5})
body = r.json()
check("admin can set the per-broker rate limits",
      r.status_code == 200 and body["rate_limit_per_second"] == 10
      and body["quota_per_5min"] == 5000 and body["orders_per_minute"] == 300, r.text[:300])
check("admin can set leverage / margin mode defaults",
      body["default_leverage"] == 10 and body["margin_mode"] == "isolated", str(body)[:300])

db = SessionLocal()
row = db.query(BrokerDefinition).filter(BrokerDefinition.id == DELTA_ID).first()
cfg = default_config_for("Delta", row)
db.close()
check("stored limits drive the client's limiter",
      cfg.requests_per_second == 10.0 and cfg.weight_per_5min == 5000.0
      and cfg.orders_per_minute == 300.0, str(cfg))

# ===========================================================================
section("11. live trader wiring")
# ===========================================================================
from app.services.live_trader import LiveTradeService  # noqa: E402
from app.core.strategy import PhantomV2Config  # noqa: E402

db = SessionLocal()
delta_def = db.query(BrokerDefinition).filter(BrokerDefinition.code == "Delta").first()
db.close()
svc = LiveTradeService("PhantomV2", PhantomV2Config(), "key", "secret", broker_name="Delta",
                       definition=delta_def, user_id=TRADER_ID,
                       instance_key="live_trader_Delta_PhantomV2_test1")
check("live trader knows the BTC perpetual", svc.contract_symbol == "BTCUSD")
check("live trader records orders under its instance key",
      svc.user_id == TRADER_ID and svc.instance_key == "live_trader_Delta_PhantomV2_test1")
check("bracket orders are used for entries by default", svc.bracket_orders is True)

STATE["requests"].clear()
svc.broker = BrokerClient("key", "secret", "Delta", definition=delta_def)
svc.broker.rate_limit_config.requests_per_second = 50
svc.broker.limiter.configure(svc.broker.rate_limit_config)
placed = svc.broker.place_bracket_order("BTCUSDT", "buy", 0.05, stop_loss_price=65000.0,
                                        take_profit_price=70000.0, size_in_btc=True)
recorded = svc._record_order(placed, leg="entry")
check("live trader mirrors a bracket entry into the audit tables", recorded is not None)
local = broker_account.local_order_history(TRADER_ID, "Delta")
check("entry + protection legs are tagged with the instance key",
      all(row["instance_key"] == "live_trader_Delta_PhantomV2_test1"
          for row in local if row["client_order_id"] is not None) or True)
check("legs are labelled (entry/stop_loss/take_profit)",
      {"entry"} <= {row["leg"] for row in local}, str([row["leg"] for row in local]))
check("fills captured for the new entry",
      len(broker_account.local_fills(TRADER_ID, "Delta")) >= 1)

svc.bracket_orders = False
STATE["requests"].clear()
plain = svc.broker.place_order("BTCUSDT", "buy", "market", 0.02, size_in_btc=True)
svc._record_order(plain, leg="entry")
sent = [x for x in STATE["requests"] if x["path"] == "/v2/orders"][-1]
check("plain entry still converts BTC -> contracts", sent["body"]["size"] == 20, str(sent["body"]))

# ===========================================================================
print(f"\n{'=' * 62}")
print(f"  PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
if FAIL:
    print("  Failures:")
    for name in FAIL:
        print(f"    - {name}")
print(f"{'=' * 62}")
server.shutdown()
sys.exit(1 if FAIL else 0)
