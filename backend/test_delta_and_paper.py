"""Offline verification of the Delta seed fix + paper-trade exit details.

Runs a local mock 'exchange' (HTTP server) and points DataSyncService at it,
so no real network access is needed.
"""
import json
import os
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, '.')

# Isolate the database BEFORE any app import: this test clears the klines table
# to check the seeder, which used to wipe the developer's real seeded market
# data (backend/trading_system.db) and leave every backtest failing with
# "Insufficient data in DB for the selected date range".
# This suite exercises error classification and host fallback, not backoff;
# disable the (real-sleep) retry layers so dead-host sections stay instant.
os.environ["SEED_REQUEST_RETRIES"] = "0"
os.environ["SEED_WINDOW_RETRIES"] = "0"

TESTDB = "/tmp/delta_paper_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"

from app.services.data_sync import DataSyncService, MarketDataError
from app.services.order_manager import OrderManager

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not cond else ""))


# ---------------------------------------------------------------------------
# Mock exchange server — behaviour selected by path prefix
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        now = int(datetime.now(timezone.utc).timestamp())
        def candles(n=5):
            return [[now - 3600 * (n - i), 100 + i, 101 + i, 99 + i, 100.5 + i, 10.0] for i in range(n)]
        if u.path == "/v2/history/candles":
            mode = q.get("symbol", ["plain_list"])[0]  # test passes the mode as symbol
            if mode == "plain_list_dicts":
                self._send(200, [{"time": now - 3600 * (5 - i), "open": 100, "high": 101, "low": 99,
                                  "close": 100.5, "volume": 10} for i in range(5)])
            elif mode == "plain_list_arrays":
                self._send(200, candles())
            elif mode == "candles_key":
                self._send(200, {"candles": candles(), "error": None, "message": None, "result": None})
            elif mode == "result_key":
                self._send(200, {"result": candles(), "error": None, "message": None})
            elif mode == "empty":
                self._send(200, [])
            elif mode == "empty_result_key":
                self._send(200, {"result": [], "error": None, "message": None})
            elif mode == "error_field":
                self._send(200, {"result": None, "error": "Symbol not found", "message": None})
            elif mode == "http400":
                self._send(400, {"error": "Invalid resolution", "message": None})
            else:
                self._send(500, {"error": "unknown mode"})
        else:
            self._send(404, {"error": "not found"})


server = HTTPServer(("127.0.0.1", 0), Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}"
print(f"mock exchange on {BASE}")

# ---------------------------------------------------------------------------
print("\n== Delta response-shape parsing ==")
# The mock selects its response shape via the `symbol` query param, so we
# patch _delta_symbol to pass the requested 'mode' straight through.
DataSyncService.DELTA_HOSTS = [BASE]
DataSyncService._delta_symbol = classmethod(lambda cls, symbol: symbol)

def fetch_mode(mode, **kw):
    return DataSyncService.fetch_klines("Delta", symbol=mode, interval="1h", limit=10, **kw)

rows = fetch_mode("plain_list_dicts")
check("bare list of dicts", len(rows) == 5 and rows[0]["open"] == 100.0, str(rows[:1]))
rows = fetch_mode("plain_list_arrays")
check("bare list of arrays", len(rows) == 5 and rows[-1]["close"] == 104.5, str(rows[-1]))
rows = fetch_mode("candles_key")
check("candles key with result=null", len(rows) == 5, str(rows[:1]))
rows = fetch_mode("result_key")
check("result key", len(rows) == 5, str(rows[:1]))

print("\n== Error surfacing ==")
try:
    fetch_mode("http400")
    check("HTTP 400 raises with body", False, "no exception")
except MarketDataError as e:
    check("HTTP 400 raises with body", "Invalid resolution" in str(e) and "HTTP 400" in str(e), str(e))

try:
    fetch_mode("error_field")
    check("200 + error field raises", False, "no exception")
except MarketDataError as e:
    check("200 + error field raises", "Symbol not found" in str(e), str(e))

print("\n== Host fallback ==")
dead = "http://127.0.0.1:1"  # nothing listens here
DataSyncService.DELTA_HOSTS = [dead, BASE]
rows = fetch_mode("plain_list_arrays")
check("falls back to 2nd host", len(rows) == 5, str(rows[:1]))

DataSyncService.DELTA_HOSTS = [BASE]
try:
    fetch_mode("empty")
    check("all-empty raises diagnostic", False, "no exception")
except MarketDataError as e:
    check("all-empty raises diagnostic",
          "0 candles" in str(e) and "HTTP 200" in str(e) and "empty result" in str(e), str(e)[:220])

print("\n== test_source ==")
DataSyncService.DELTA_HOSTS = [BASE]
diag = DataSyncService.test_source("Delta", symbol="plain_list_arrays", interval="1h")
check("test_source ok", diag["ok"] is True and diag["rows"] == 5, str(diag))
diag = DataSyncService.test_source("Delta", symbol="http400", interval="1h")
check("test_source failure detail", diag["ok"] is False and "Invalid resolution" in diag["detail"], str(diag))

# ---------------------------------------------------------------------------
print("\n== seed_market_data per-interval error summary ==")
from app.database.models import SessionLocal, Klines, init_db
import app.database.models as models

init_db()
db = SessionLocal()
db.query(Klines).delete()
db.commit(); db.close()

DataSyncService.DELTA_HOSTS = [BASE]
summary = DataSyncService.seed_market_data("Delta", "modeX", ["1h", "4h"], start_date="2026-07-01", end_date="2026-08-01", limit=10, fetch_all=False)
# both intervals hit symbol "modeX" -> unknown mode -> HTTP 500 -> error entries
check("per-interval error entries", all(s.get("error") for s in summary), str(summary))
check("error mentions HTTP", any("HTTP 500" in (s.get("error") or "") for s in summary), str(summary))

# symbol selects a working mode -> rows get seeded
DataSyncService.seed_market_data("Delta", "plain_list_arrays", ["1h"], start_date="2026-07-01", end_date="2026-08-01", limit=10)
db = SessionLocal()
n = db.query(Klines).filter(Klines.source == "Delta", Klines.symbol == "plain_list_arrays").count()
db.close()
check("rows upserted to DB", n == 5, f"n={n}")

# re-seed same data -> updates not duplicates
summary = DataSyncService.seed_market_data("Delta", "plain_list_arrays", ["1h"], start_date="2026-07-01", end_date="2026-08-01", limit=10)
check("upsert idempotent", summary[0]["updated"] == 5 and summary[0]["inserted"] == 0, str(summary[0]))

# ---------------------------------------------------------------------------
print("\n== OrderManager exit details ==")
from app.services.order_manager import OrderManager
from app.core.strategy import PhantomV2Config

oms = OrderManager(PhantomV2Config())
t = oms.create_order("BTCUSDT", 1, 100000, atr_usd=1000, timestamp=datetime(2026, 8, 1), margin_inr=25000, conversion_rate=85.0)
check("sl_entry captured", t.sl_entry == t.sl and t.tp_entry == t.tp, f"sl_entry={t.sl_entry} sl={t.sl}")

# force SL hit
r = oms.update_trade("BTCUSDT", t.sl - 1, t.atr_at_entry, datetime(2026, 8, 2))
check("SL close reason", r is not None and r.exit_reason == "SL", str(r.exit_reason if r else None))
check("SL exit detail", "Stop loss hit" in r.exit_detail and f"{r.sl:,.2f}" in r.exit_detail, r.exit_detail)
check("exit price = stop level", r.exit_price == r.sl, f"{r.exit_price} vs {r.sl}")

# TP hit
t = oms.create_order("BTCUSDT", 1, 100000, atr_usd=1000, timestamp=datetime(2026, 8, 1), margin_inr=25000, conversion_rate=85.0)
r = oms.update_trade("BTCUSDT", t.tp + 1, t.atr_at_entry, datetime(2026, 8, 2))
check("TP close", r is not None and r.exit_reason == "TP" and "Take profit hit" in r.exit_detail, r.exit_detail if r else "")

# trailing stop hit (long): push peak past activation, then fall to trail
t = oms.create_order("BTCUSDT", 1, 100000, atr_usd=1000, timestamp=datetime(2026, 8, 1), margin_inr=25000, conversion_rate=85.0)
oms.update_trade("BTCUSDT", t.trail_activation + 5000, t.atr_at_entry, datetime(2026, 8, 2))
r = oms.update_trade("BTCUSDT", t.trail_stop - 1, t.atr_at_entry, datetime(2026, 8, 3))
check("TSL close", r is not None and r.exit_reason == "TSL" and "Trailing stop hit" in r.exit_detail, r.exit_detail if r else "")

# max holding
from app.core.strategy import PhantomV2Config as P2
cfg = P2(timeout_bars=2)
oms = OrderManager(cfg)
t = oms.create_order("BTCUSDT", 1, 100000, atr_usd=1000, timestamp=datetime(2026, 8, 1), margin_inr=25000, conversion_rate=85.0)
oms.update_trade("BTCUSDT", 100001, 1000, datetime(2026, 8, 2))
r = oms.update_trade("BTCUSDT", 100002, 1000, datetime(2026, 8, 3))
check("MH close", r is not None and r.exit_reason == "MH" and "Max holding time" in r.exit_detail, r.exit_detail if r else "")

# short SL
oms = OrderManager(PhantomV2Config())
t = oms.create_order("BTCUSDT", -1, 100000, atr_usd=1000, timestamp=datetime(2026, 8, 1), margin_inr=25000, conversion_rate=85.0)
r = oms.update_trade("BTCUSDT", t.sl + 1, t.atr_at_entry, datetime(2026, 8, 2))
check("short SL close + detail", r is not None and r.exit_reason == "SL" and "price rose to" in r.exit_detail, r.exit_detail if r else "")

# ---------------------------------------------------------------------------
print("\n== PaperTradeService closed-trade record ==")
from app.services.paper_trader import PaperTradeService

svc = PaperTradeService("PhantomV2", PhantomV2Config(), initial_capital=20000, margin_pct=25, market_source="Binance")
oms2 = svc.oms
trade = oms2.create_order("BTCUSDT", 1, 100000, atr_usd=1000, timestamp=datetime(2026, 8, 1), margin_inr=5000, conversion_rate=85.0)
oms2.update_trade("BTCUSDT", trade.sl - 1, trade.atr_at_entry, datetime(2026, 8, 2))
closed = oms2.active_trades  # should be empty now
result = None
# replay: create + close and call _record_closed with the returned trade
trade = oms2.create_order("BTCUSDT", 1, 100000, atr_usd=1000, timestamp=datetime(2026, 8, 1), margin_inr=5000, conversion_rate=85.0)
result = oms2.update_trade("BTCUSDT", trade.sl - 1, trade.atr_at_entry, datetime(2026, 8, 2))
pnl = (result.exit_price - result.entry_price) * result.direction * result.lots * svc.conversion_rate
svc._record_closed(result, pnl, 1.0, pnl)
rec = svc.closed_trades[-1]
for field in ("sl", "sl_final", "tp", "trail_stop", "trail_activation", "atr_at_entry",
              "peak_price", "margin_inr", "notional_usd", "lots", "exit_detail", "reason", "exit"):
    check(f"closed record has {field}", field in rec, str(rec.keys()))
check("exit detail present", "Stop loss hit" in rec["exit_detail"], rec.get("exit_detail", ""))

# ---------------------------------------------------------------------------
print("\n== main.py imports ==")
try:
    import app.main  # noqa: F401
    check("app.main imports cleanly", True)
except Exception as e:
    check("app.main imports cleanly", False, repr(e))

server.shutdown()
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
