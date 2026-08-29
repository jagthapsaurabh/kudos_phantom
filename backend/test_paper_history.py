"""End-to-end test: paper-trade sessions survive stop / restart via History.

Uses the same mock Delta exchange + temp SQLite DB pattern as
``test_api_e2e.py`` so it runs offline.
"""
import os
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, '.')

TESTDB = "/tmp/paper_history_test.db"
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

from app.services.data_sync import DataSyncService
from app.services.broker_client import BrokerClient
DataSyncService.DELTA_HOSTS = [BASE]
BrokerClient.DEFAULTS["Delta"]["market"] = BASE

from app.database.models import SessionLocal, User, BrokerDefinition, PaperSession, init_db
import bcrypt

init_db()
db = SessionLocal()
db.query(User).delete()
db.add(User(username="admin", password_hash=bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode(),
            role="admin", is_active=1, can_paper=1, can_live=0,
            initial_capital=20000.0, margin_deployment_pct=25.0, virtual_balance=20000.0))
db.add(User(username="client2", password_hash=bcrypt.hashpw(b"client123", bcrypt.gensalt()).decode(),
            role="client", is_active=1, can_paper=1, can_live=0,
            initial_capital=20000.0, margin_deployment_pct=25.0, virtual_balance=20000.0))
db.query(BrokerDefinition).delete()
db.add(BrokerDefinition(code="Binance", name="Binance Futures", kind="binance", is_builtin=1, enabled=1))
db.add(BrokerDefinition(code="Delta", name="Delta Exchange", kind="delta", is_builtin=1, enabled=1))
db.commit(); db.close()

import asyncio
from fastapi.testclient import TestClient
import app.main as main


async def _no_sync():
    await asyncio.sleep(3600)
main.daily_sync_task = _no_sync

client = TestClient(main.app)

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not cond else ""), flush=True)


r = client.post("/token", data={"username": "admin", "password": "admin123"})
check("login admin", r.status_code == 200, r.text[:200])
H = {"Authorization": f"Bearer {r.json()['access_token']}"}
r = client.post("/token", data={"username": "client2", "password": "client123"})
H2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
MAIN_USER = "admin"   # the H token's login, for privacy checks

print("\n== start a paper instance -> history row created ==", flush=True)
def _start():
    try:
        client.post("/paper-trade/start", headers=H, json={
            "strategy_id": "PhantomV2", "initial_capital": 20000, "margin_pct": 25, "data_source": "Delta"})
    except Exception:
        pass
threading.Thread(target=_start, daemon=True).start()

instance_key = None
for _ in range(100):
    if main.paper_trade_instances:
        instance_key = list(main.paper_trade_instances.keys())[0]
        break
    threading.Event().wait(0.1)
check("instance created", instance_key is not None, str(list(main.paper_trade_instances)))
svc = main.paper_trade_instances.get(instance_key)
svc.strategy.generate_signals = lambda *a, **k: [0] * 200
check("service has instance_key", svc.instance_key == instance_key, str(svc.instance_key))
check("service has session_id", isinstance(svc.session_id, int), str(svc.session_id))

r = client.get("/paper-trade/history", headers=H)
hist = r.json()
check("history lists the running session", r.status_code == 200 and len(hist) == 1, str(hist)[:300])
row = hist[0] if hist else {}
check("history row is 'running'", row.get("status") == "running", str(row)[:200])
check("history row carries strategy + capital",
      row.get("strategy_name") == "Kudos V2.5 (Default)" and row.get("initial_capital") == 20000, str(row)[:200])
check("history row carries the traded symbol",
      row.get("symbol") == "BTCUSDT", str(row.get("symbol")))
# The column has default='BTCUSDT', so the check above would also pass if
# _payload() never mentioned the symbol at all. Point the service at a
# different symbol and re-snapshot: the row must follow the service, not the
# column default.
_real_symbol = svc.symbol
svc.symbol = "ETHUSDT"
from app.services import paper_history as _ph
_ph.persist_snapshot(instance_key, svc)
_row2 = client.get("/paper-trade/history", headers=H).json()[0]
check("history row tracks the service's symbol, not the column default",
      _row2.get("symbol") == "ETHUSDT", str(_row2.get("symbol")))
svc.symbol = _real_symbol
_ph.persist_snapshot(instance_key, svc)
check("symbol restored on the next snapshot",
      client.get("/paper-trade/history", headers=H).json()[0].get("symbol") == _real_symbol)
check("status endpoint exposes session_id",
      client.get("/paper-trade/status", headers=H).json()[0].get("session_id") == svc.session_id)

print("\n== execution overlay: open trade, closed trade, survived session ==", flush=True)
tr = svc.oms.create_order("BTCUSDT", 1, 67000.0, atr_usd=400.0,
                          timestamp=datetime.utcnow(), margin_inr=5000.0, conversion_rate=85.0)
r = client.get("/trade-executions", headers=H)
ex = r.json()
sess_open = next((s for s in ex.get("sessions", []) if s.get("key") == f"paper:{instance_key}"), None)
check("open position appears on /trade-executions while running",
      r.status_code == 200 and sess_open is not None, str(ex)[:250])
open_trade = (sess_open or {}).get("trades", [{}])[0]
check("the open execution carries entry candle + live SL/TP/trail levels",
      open_trade.get("status") == "open" and open_trade.get("entry_time")
      and open_trade.get("sl") is not None and open_trade.get("tp") is not None
      and open_trade.get("trail_stop") is not None,
      str(open_trade)[:250])
check("a running session is not double-listed from its saved row",
      sum(1 for s in ex.get("sessions", []) if s.get("instance_key") == instance_key) == 1,
      str([s.get("key") for s in ex.get("sessions", [])]))
r = client.get("/trade-executions", headers=H2)
check("another user's executions stay private", r.status_code == 200
      and all(f"_{MAIN_USER}_" not in (s.get("instance_key") or "")
              for s in r.json().get("sessions", [])), "")

# Arm the trailing stop above the mock's last 1h close (67099) so the tick exits.
tr.trail_activation = 66000.0
tr.trail_stop = 67150.0
tr.peak_price = 67200.0
asyncio.run(svc.tick())
check("tick closed the trade", not svc.oms.active_trades, str(list(svc.oms.active_trades)))
check("equity curve sampled", len(svc.equity_history) >= 2, str(svc.equity_history[:2]))
r = client.get("/trade-executions", headers=H)
sess_closed = next((s for s in r.json().get("sessions", []) if s.get("key") == f"paper:{instance_key}"), None)
closed_exec = next((t for t in (sess_closed or {}).get("trades", []) if t.get("status") == "closed"), None)
check("closed execution lands on /trade-executions",
      closed_exec is not None, str((sess_closed or {}).get("trades"))[:250])
check("the closed execution carries exit candle, stop plan and PnL",
      closed_exec and closed_exec.get("exit_time") and closed_exec.get("entry_time")
      and closed_exec.get("sl") is not None and closed_exec.get("tp") is not None
      and isinstance(closed_exec.get("pnl"), (int, float)) and closed_exec.get("reason"),
      str(closed_exec)[:250])

row = client.get("/paper-trade/history", headers=H).json()[0]
check("persisted closed_trade_count == 1", row.get("closed_trade_count") == 1, str(row)[:300])
check("persisted net_pnl is a number", isinstance(row.get("net_pnl"), (int, float)), str(row.get("net_pnl")))
check("persisted win_rate == 100", row.get("win_rate") == 100.0, str(row.get("win_rate")))
check("persisted total_fees > 0", (row.get("total_fees") or 0) > 0, str(row.get("total_fees")))
check("final_equity updated", abs((row.get("final_equity") or 0) - svc.equity_inr) < 1e-6,
      f"{row.get('final_equity')} vs {svc.equity_inr}")

print("\n== stop keeps the result in history ==", flush=True)
r = client.post("/paper-trade/stop", params={"instance_key": instance_key}, headers=H)
d = r.json()
check("stop 200", r.status_code == 200, r.text[:200])
check("stop reports saved_to_history", d.get("saved_to_history") is True, str(d))
check("instance gone from the live registry", instance_key not in main.paper_trade_instances)
check("service marked stopped", svc.is_running is False)

r = client.get("/paper-trade/history", headers=H)
hist = r.json()
check("session still in history after stop", len(hist) == 1, str(hist)[:300])
row = hist[0]
check("history status == stopped", row.get("status") == "stopped", str(row)[:200])
r = client.get("/trade-executions", headers=H)
hist_sess = next((s for s in r.json().get("sessions", []) if s.get("key") == f"history:{row['id']}"), None)
check("stopped session's executions still served from history",
      hist_sess is not None and any(t.get("status") == "closed" for t in hist_sess.get("trades", [])),
      str(r.json().get("sessions"))[:250])
check("stopped_at stamped", row.get("stopped_at") is not None, str(row.get("stopped_at")))
check("closed trades survived the stop", row.get("closed_trade_count") == 1, str(row)[:200])
check("roi computed", row.get("roi") is not None, str(row.get("roi")))

session_id = row["id"]
r = client.get(f"/paper-trade/history/{session_id}", headers=H)
detail = r.json()
check("detail 200", r.status_code == 200, r.text[:200])
check("detail has closed_trades", len(detail.get("closed_trades") or []) == 1, str(detail.get("closed_trades"))[:200])
check("detail closed trade keeps exit fields",
      all(k in detail["closed_trades"][0] for k in ("entry", "exit", "reason", "sl", "tp", "pnl", "fees")),
      str(list(detail["closed_trades"][0].keys())))
check("detail has logs", len(detail.get("logs") or []) > 0, str(detail.get("logs"))[:120])
check("detail has equity_curve", len(detail.get("equity_curve") or []) >= 2, str(detail.get("equity_curve"))[:120])
check("detail has the run params", isinstance(detail.get("params"), dict) and "atr_regime_ratio" in detail["params"],
      str(list((detail.get("params") or {}).keys()))[:200])
check("detail open_positions snapshot is a list", isinstance(detail.get("open_positions"), list))

print("\n== access control + delete ==", flush=True)
r = client.get("/paper-trade/history", headers=H2)
check("other user sees an empty history", r.status_code == 200 and r.json() == [], str(r.json())[:200])
r = client.get(f"/paper-trade/history/{session_id}", headers=H2)
check("other user cannot read the session", r.status_code == 404, str(r.status_code))
r = client.delete(f"/paper-trade/history/{session_id}", headers=H2)
check("other user cannot delete the session", r.status_code == 404, str(r.status_code))
r = client.delete(f"/paper-trade/history/{session_id}", headers=H)
check("owner deletes the session", r.status_code == 200, r.text[:200])
check("history empty after delete", client.get("/paper-trade/history", headers=H).json() == [])
r = client.delete(f"/paper-trade/history/{session_id}", headers=H)
check("deleting twice 404s", r.status_code == 404, str(r.status_code))

print("\n== workspace delete purges the saved row ==", flush=True)
def _start2():
    try:
        client.post("/paper-trade/start", headers=H, json={
            "strategy_id": "PhantomV2", "initial_capital": 25000, "margin_pct": 20, "data_source": "Delta"})
    except Exception:
        pass
threading.Thread(target=_start2, daemon=True).start()
key2 = None
for _ in range(100):
    keys = [k for k in main.paper_trade_instances if k != instance_key]
    if keys:
        key2 = keys[0]
        break
    threading.Event().wait(0.1)
check("second instance created", key2 is not None, str(list(main.paper_trade_instances)))
main.paper_trade_instances[key2].strategy.generate_signals = lambda *a, **k: [0] * 200
check("second session in history", len(client.get("/paper-trade/history", headers=H).json()) == 1)
r = client.delete(f"/paper-trade/{key2}", headers=H)
check("workspace delete 200", r.status_code == 200, r.text[:200])
check("workspace delete removed the history row",
      client.get("/paper-trade/history", headers=H).json() == [], str(r.json()))

print("\n== restart marks orphaned sessions interrupted ==", flush=True)
from app.services import paper_history
db = SessionLocal()
db.add(PaperSession(user_id=1, instance_key="paper_admin_Delta_PhantomV2_deadbeef",
                    strategy_id="PhantomV2", strategy_name="Kudos V2.5 (Default)",
                    status="running", initial_capital=20000.0, final_equity=20000.0,
                    created_at=datetime.utcnow(), started_at=datetime.utcnow()))
db.commit(); db.close()
count = paper_history.mark_interrupted_sessions()
check("one row flagged", count == 1, str(count))
row = client.get("/paper-trade/history", headers=H).json()[0]
check("status == interrupted", row.get("status") == "interrupted", str(row)[:200])
check("interrupted row still reviewable", row.get("id") is not None)

print("\n== summarize() roll-up maths ==", flush=True)
closed = [{"pnl": 100.0, "fees": 5.0}, {"pnl": -40.0, "fees": 5.0}, {"pnl": 20.0, "fees": 5.0}]
s = paper_history.summarize(closed, 10000.0, 10080.0,
                            [{"ts": "t1", "equity": 10000.0}, {"ts": "t2", "equity": 10100.0},
                             {"ts": "t3", "equity": 10020.0}, {"ts": "t4", "equity": 10080.0}])
check("closed_trade_count", s["closed_trade_count"] == 3, str(s))
check("win_rate", s["win_rate"] == 66.67, str(s))
check("profit_factor = 120/40", s["profit_factor"] == 3.0, str(s))
check("net_pnl", s["net_pnl"] == 80.0, str(s))
check("total_fees", s["total_fees"] == 15.0, str(s))
check("roi", s["roi"] == 0.8, str(s))
check("peak_equity", s["peak_equity"] == 10100.0, str(s))
check("max_drawdown_pct from the curve", s["max_drawdown_pct"] == round((10100 - 10020) / 10100 * 100, 2), str(s))
empty = paper_history.summarize([], 10000.0, 10000.0, [])
check("empty session -> zeros, no crash",
      empty["closed_trade_count"] == 0 and empty["win_rate"] == 0.0 and empty["profit_factor"] == 0.0
      and empty["roi"] == 0.0, str(empty))

server.shutdown()
print(f"\n{len(PASS)} passed, {len(FAIL)} failed", flush=True)
if FAIL:
    print("FAILED:", FAIL, flush=True)
    sys.exit(1)
