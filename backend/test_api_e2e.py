"""End-to-end API test: admin seed + test-connection + paper-trade status,
with a mock Delta exchange and a temp SQLite DB."""
import os
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, '.')

TESTDB = "/tmp/e2e_phantom_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        import json
        now = int(datetime.now(timezone.utc).timestamp())
        candles = [[now - 3600 * (50 - i), 67000 + i, 67100 + i, 66900 + i, 67050 + i, 12.0] for i in range(50)]
        body = json.dumps({"candles": candles, "error": None, "message": None, "result": None}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = HTTPServer(("127.0.0.1", 0), Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}"

# Keep the dead-host sections fast: no adapter-level retries in this suite.
os.environ["SEED_REQUEST_RETRIES"] = "0"
os.environ["SEED_WINDOW_RETRIES"] = "0"

from app.services.data_sync import DataSyncService
from app.services.broker_client import BrokerClient
DataSyncService.DELTA_HOSTS = [BASE]
BrokerClient.DEFAULTS["Delta"]["market"] = BASE

from app.database.models import SessionLocal, User, BrokerDefinition, init_db
import bcrypt

init_db()
db = SessionLocal()
db.query(User).delete()
db.add(User(username="admin", password_hash=bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode(),
            role="admin", is_active=1, can_paper=1, can_live=0,
            initial_capital=20000.0, margin_deployment_pct=25.0, virtual_balance=20000.0))
db.query(BrokerDefinition).delete()
db.add(BrokerDefinition(code="Binance", name="Binance Futures", kind="binance", is_builtin=1, enabled=1))
db.add(BrokerDefinition(code="Delta", name="Delta Exchange", kind="delta", is_builtin=1, enabled=1))
db.commit(); db.close()

import asyncio
from fastapi.testclient import TestClient
import app.main as main

# Keep the 24h background market sync out of the test.
async def _no_sync():
    await asyncio.sleep(3600)
main.daily_sync_task = _no_sync

client = TestClient(main.app)

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not cond else ""), flush=True)

r = client.post("/token", data={"username": "admin", "password": "admin123"})
check("login", r.status_code == 200, r.text[:200])
H = {"Authorization": f"Bearer {r.json()['access_token']}"}

print("\n== GET /admin/market-data/test (Delta) ==", flush=True)
r = client.get("/admin/market-data/test", params={"source": "Delta", "symbol": "BTCUSDT", "interval": "1h"}, headers=H)
d = r.json()
# mock ignores `limit`, so just verify connectivity + shape
check("test ok", r.status_code == 200 and d["ok"] is True and d["rows"] >= 3 and len(d["sample"]) == 3, str(d)[:300])

print("\n== POST /admin/market-data/seed (Delta 1h, no dates) ==", flush=True)
r = client.post("/admin/market-data/seed", headers=H, json={
    "source": "Delta", "symbol": "BTCUSDT", "intervals": ["1h"], "limit": 1000, "fetch_all": False})
d = r.json()
s = d["summary"][0]
check("seed 200 + status", r.status_code == 200 and d["status"] == "Seed completed", str(d)[:300])
check("seed fetched 50", s.get("fetched") == 50, str(s))
check("seed first/last in summary", "first" in s and "last" in s, str(s))

print("\n== seed failure surfaces per-interval error (all hosts dead) ==", flush=True)
DataSyncService.DELTA_HOSTS = ["http://127.0.0.1:1", "http://127.0.0.1:2"]
r = client.post("/admin/market-data/seed", headers=H, json={
    "source": "Delta", "symbol": "BTCUSDT", "intervals": ["1h", "4h"], "limit": 1000})
d = r.json()
check("still 200 with status", r.status_code == 200 and d["status"] in ("Seed completed with errors", "Seed failed"), str(d)[:200])
check("per-interval error present", all("error" in x for x in d["summary"]), str(d)[:300])
check("error names the request failure", any("request failed" in (x.get("error") or "") for x in d["summary"]), str(d)[:300])
DataSyncService.DELTA_HOSTS = [BASE]

print("\n== paper trade: start, status with open trade details ==", flush=True)
# TestClient blocks until the instance's background loop ends, so fire the
# start request in a thread (production uvicorn returns the response at once).
start_result = {}
def _start():
    try:
        start_result["r"] = client.post("/paper-trade/start", headers=H, json={
            "strategy_id": "PhantomV2", "initial_capital": 20000, "margin_pct": 25, "data_source": "Delta"})
    except Exception as e:
        start_result["e"] = e
t = threading.Thread(target=_start, daemon=True)
t.start()

instance_key = None
for _ in range(100):
    if len(main.paper_trade_instances) >= 1:
        instance_key = list(main.paper_trade_instances.keys())[0]
        break
    threading.Event().wait(0.1)
check("instance created", instance_key is not None, str(list(main.paper_trade_instances)))
svc = main.paper_trade_instances.get(instance_key)
# Keep the 60s background tick from opening its own position on mock data.
svc.strategy.generate_signals = lambda *a, **k: [0] * 200

# 1h is seeded in the DB, 4h must come from the (mock) exchange fallback —
# both must resolve for the simulator to run.
d1 = svc._fetch_candles("1h", 100)
d4 = svc._fetch_candles("4h", 100)
check("paper service resolves 1h from DB", d1 is not None and len(d1) == 50, str(None if d1 is None else d1.shape))
check("paper service resolves 4h from exchange fallback", d4 is not None and len(d4) == 50, str(None if d4 is None else d4.shape))

# Force an open position so the status endpoint must serialize the stop fields.
tr = svc.oms.create_order("BTCUSDT", 1, 67000.0, atr_usd=400.0,
                          timestamp=datetime.utcnow(), margin_inr=5000.0, conversion_rate=85.0)
svc.last_price = 67200.0
r = client.get("/paper-trade/status", headers=H)
trades = r.json()[0]["active_trades"]
check("one active trade", len(trades) == 1, str(r.json())[:300])
at = trades[0]
for field in ("sl", "sl_entry", "tp", "trail_stop", "trail_activation", "trail_active",
              "stop_level", "breakeven_active", "atr_at_entry", "peak_price"):
    check(f"active trade has {field}", field in at, str(at.keys()))
check("stop_level == sl when trail inactive", at["stop_level"] == at["sl"], f"{at['stop_level']} vs {at['sl']}")

# Push price up past trail activation, then re-read status.
tr.peak_price = tr.trail_activation + 3000
tr.trail_stop = tr.peak_price - 0.5 * 400.0
r = client.get("/paper-trade/status", headers=H)
at = r.json()[0]["active_trades"][0]
check("trail active + stop_level is trail", at["trail_active"] is True and at["stop_level"] == at["trail_stop"], str(at))

# Regression: ATR from pandas is np.float64 -> comparisons yield np.bool_,
# which FastAPI cannot serialize. Create a numpy-flavoured trade and make
# sure the status endpoint still answers 200.
import numpy as _np
tr_np = svc.oms.create_order("BTCUSDT", -1, 67000.0, atr_usd=_np.float64(400.0),
                             timestamp=datetime.utcnow(), margin_inr=5000.0, conversion_rate=85.0)
tr_np.trail_activation = _np.float64(66000.0)  # ensure numpy-typed comparison
r = client.get("/paper-trade/status", headers=H)
check("status 200 with numpy-typed trade fields", r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}")
ats = r.json()[0]["active_trades"]
np_tr = [a for a in ats if a["direction"] == -1]
check("numpy trade serialized", len(np_tr) == 1 and isinstance(np_tr[0]["trail_active"], bool)
      and np_tr[0]["sl"] == np_tr[0]["sl"], str(ats)[:300])
svc.oms.active_trades.pop("BTCUSDT", None)  # drop the extra short (one per symbol)
# re-add the long we were analysing (symbol slot was overwritten by the short)
tr = svc.oms.create_order("BTCUSDT", 1, 67000.0, atr_usd=400.0,
                          timestamp=datetime.utcnow(), margin_inr=5000.0, conversion_rate=85.0)

# Arm the trailing stop just above the mock's last 1h close (67099) so a real
# tick() exits via TSL and exercises the full record+log path.
tr.trail_activation = 66000.0
tr.trail_stop = 67150.0
tr.peak_price = 67200.0
import asyncio as _aio
_aio.run(svc.tick())
check("real tick closed trade via TSL", not svc.oms.active_trades, str(list(svc.oms.active_trades)))

r = client.get("/paper-trade/status", headers=H)
closed = r.json()[0]["closed_trades"]
check("closed trade visible", len(closed) == 1, str(closed)[:200])
ct = closed[0]
for field in ("sl", "sl_final", "tp", "trail_stop", "trail_activation", "atr_at_entry",
              "peak_price", "margin_inr", "notional_usd", "lots", "exit_detail", "exit"):
    check(f"closed trade has {field}", field in ct, str(ct.keys()))
check("closed exit detail mentions trailing", "Trailing stop hit" in ct["exit_detail"], ct.get("exit_detail", ""))

r = client.get("/paper-trade/logs", params={"instance_key": instance_key}, headers=H)
logs = r.json()["logs"]
check("log has close detail line", any("Exit condition:" in l["msg"] for l in logs), str(logs[-3:]))

print("\n== BrokerClient runtime Delta fetch (candles-key payload) ==", flush=True)
rows = BrokerClient(broker_name="Delta").fetch_klines("BTCUSDT", "1h", 10)
# mock ignores limit and always returns 50 candles
check("runtime fetch parses candles key", len(rows) >= 10 and rows[0]["open"] > 0, str(rows[:1]))

print("\n== stop instance ==", flush=True)
r = client.post("/paper-trade/stop", params={"instance_key": instance_key}, headers=H)
check("stop 200", r.status_code == 200, r.text[:200])
check("instance removed from registry", instance_key not in main.paper_trade_instances,
      str(list(main.paper_trade_instances)))
# NOTE: the background start request (TestClient blocks until the instance's
# 60s loop exits) is a TestClient artifact — uvicorn returns the start
# response to the browser immediately. We only verify the loop stops.
check("service marked stopped", svc.is_running is False, str(svc.is_running))

print("\n== market-data status lists Delta rows ==", flush=True)
DataSyncService.DELTA_HOSTS = ["https://api.india.delta.exchange", "https://cdn.india.deltaex.org"]
r = client.get("/admin/market-data/status", headers=H)
check("status lists Delta rows", r.status_code == 200 and any(x["source"] == "Delta" for x in r.json()), str(r.json())[:200])

server.shutdown()
print(f"\n{len(PASS)} passed, {len(FAIL)} failed", flush=True)
if FAIL:
    print("FAILED:", FAIL, flush=True)
    sys.exit(1)
