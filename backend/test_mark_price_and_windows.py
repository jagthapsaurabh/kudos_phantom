"""BTC perpetual mark pricing + "skip new trades" windows.

Offline suite (no exchange, no seeded database required):

* ``TradingWindow`` geometry — weekend wrap, all-day blocks, timezone.
* ``TradingWindowGuard`` — entries blocked inside a window, exits never.
* ``BacktestEngine`` — mark-price basis, both prices stored per trade, and
  new entries refused inside a configured window while open trades keep
  running.
* ``OrderManager`` / ``PaperTradeService`` — mark fields on a closed trade.
* API — ``/trading-windows``, ``/market/mark-price``, ``/market/contract``.

Run from the backend directory:

    python test_mark_price_and_windows.py
"""
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, '.')

TESTDB = "/tmp/mark_windows_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"

import numpy as np
import pandas as pd

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not cond else ""), flush=True)


# ---------------------------------------------------------------------------
print("\n== 1. perpetual contract resolution ==")
from app.core.mark_price import (
    perpetual_symbol, mark_symbol, contract_label, decision_series, MarkPriceQuote,
)

check("Binance perpetual is BTCUSDT", perpetual_symbol("Binance", "BTCUSDT") == "BTCUSDT")
check("Binance resolves BTCUSD -> BTCUSDT", perpetual_symbol("Binance", "BTCUSD") == "BTCUSDT")
check("Delta perpetual is BTCUSD", perpetual_symbol("Delta", "BTCUSDT") == "BTCUSD")
check("Delta keeps BTCUSD", perpetual_symbol("Delta", "BTCUSD") == "BTCUSD")
check("Delta mark symbol is MARK:BTCUSD", mark_symbol("Delta", "BTCUSDT") == "MARK:BTCUSD")
check("Binance mark symbol is BTCUSDT", mark_symbol("Binance") == "BTCUSDT")
check("contract label mentions perpetual", "perpetual" in contract_label("Delta", "BTCUSDT"),
      contract_label("Delta", "BTCUSDT"))

# ---------------------------------------------------------------------------
print("\n== 2. trading-window geometry ==")
from app.core.trading_windows import (
    TradingWindow, TradingWindowConfig, TradingWindowGuard, all_day_window,
    weekend_window, normalize_weekday, parse_hhmm, BLOCK_REASON,
)

check("weekday names", [normalize_weekday(n) for n in ("Mon", "tuesday", "SAT", "Sun")] == [0, 1, 5, 6])
check("weekday ints", normalize_weekday(0) == 0 and normalize_weekday(6) == 6)
check("hh:mm parse", parse_hhmm("18:30") == 18 * 60 + 30 and parse_hhmm("00:00") == 0)

IST = timezone(timedelta(hours=5, minutes=30))
weekend = weekend_window("18:30", "01:00", 5, 0)
check("weekend window wraps the week", weekend.wraps is True)

guard_ist = TradingWindowGuard(TradingWindowConfig(enabled=True, timezone="Asia/Kolkata",
                                                   windows=[weekend]))
# Local IST datetimes -> aware UTC equivalents.
def ist(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=IST)

# 2024-01-06 is a Saturday, 2024-01-07 Sunday, 2024-01-08 Monday.
cases = [
    ("Sat 12:00 IST", ist(2024, 1, 6, 12, 0), False),
    ("Sat 18:29 IST", ist(2024, 1, 6, 18, 29), False),
    ("Sat 18:30 IST (start)", ist(2024, 1, 6, 18, 30), True),
    ("Sat 23:59 IST", ist(2024, 1, 6, 23, 59), True),
    ("Sun 00:30 IST", ist(2024, 1, 7, 0, 30), True),
    ("Sun 18:29 IST", ist(2024, 1, 7, 18, 29), True),
    ("Mon 00:30 IST", ist(2024, 1, 8, 0, 30), True),
    ("Mon 00:59 IST", ist(2024, 1, 8, 0, 59), True),
    ("Mon 01:00 IST (end)", ist(2024, 1, 8, 1, 0), False),
    ("Mon 12:00 IST", ist(2024, 1, 8, 12, 0), False),
    ("Wed 03:00 IST", ist(2024, 1, 10, 3, 0), False),
]
for label, when, blocked in cases:
    check(f"{label} -> {'blocked' if blocked else 'allowed'}",
          guard_ist.is_blocked(when) is blocked,
          f"got blocked={guard_ist.is_blocked(when)}")

# The same schedule evaluated through a UTC naive timestamp (what the DB holds).
sat_2330_ist = datetime(2024, 1, 6, 18, 0)  # 23:30 IST = 18:00 UTC
check("naive-UTC input is converted to IST", guard_ist.is_blocked(sat_2330_ist) is True)
mon_0130_ist = datetime(2024, 1, 8, 2, 0)   # 07:30 IST Monday
check("naive-UTC Monday after the window is allowed", guard_ist.is_blocked(mon_0130_ist) is False)

# All-day blocks: Sunday, and Tuesday (Mon=0, so Tuesday is 1).
sunday = all_day_window("sunday", label="Sunday")
tuesday = all_day_window("tuesday")
guard_days = TradingWindowGuard(TradingWindowConfig(
    enabled=True, timezone="Asia/Kolkata", windows=[sunday, tuesday]))
check("Sunday 00:00 blocked", guard_days.is_blocked(ist(2024, 1, 7, 0, 0)) is True)
check("Sunday 23:59 blocked", guard_days.is_blocked(ist(2024, 1, 7, 23, 59)) is True)
check("Monday 00:00 open again", guard_days.is_blocked(ist(2024, 1, 8, 0, 0)) is False)
check("Tuesday blocked", guard_days.is_blocked(ist(2024, 1, 9, 12, 0)) is True)
check("Wednesday open", guard_days.is_blocked(ist(2024, 1, 10, 12, 0)) is False)

# Master switch off -> nothing is blocked even with windows configured.
off = TradingWindowGuard(TradingWindowConfig(enabled=False, windows=[sunday]))
check("disabled schedule blocks nothing", off.is_blocked(ist(2024, 1, 7, 12, 0)) is False)
check("disabled schedule reports inactive", off.enabled is False)
check("an individual window can be switched off",
      TradingWindowGuard(TradingWindowConfig(enabled=True, windows=[sunday.model_copy(update={'enabled': False})])).enabled is False)

# Exits keep running by default (the requested behaviour).
check("exits are allowed inside a window", guard_ist.allows_exit(ist(2024, 1, 7, 12, 0)) is True)
check("entries are refused inside a window", guard_ist.allows_new_entry(ist(2024, 1, 7, 12, 0)) is False)
block_exits = TradingWindowGuard(TradingWindowConfig(enabled=True, block_exits=True, windows=[sunday]))
check("block_exits opt-in works", block_exits.allows_exit(ist(2024, 1, 7, 12, 0)) is False)

check("blocked reason names the window",
      (guard_ist.blocked_reason(ist(2024, 1, 7, 12, 0)) or "").startswith(BLOCK_REASON),
      str(guard_ist.blocked_reason(ist(2024, 1, 7, 12, 0))))

nxt = guard_ist.next_open_from(ist(2024, 1, 7, 12, 0))
check("next open is Monday 01:00 IST",
      nxt is not None and nxt.weekday() == 0 and (nxt.hour, nxt.minute) == (1, 0), str(nxt))
check("no next-open when the schedule is off", off.next_open_from(ist(2024, 1, 7, 12, 0)) is None)

check("description is human readable",
      "Sat" in weekend.describe() and "Mon" in weekend.describe(), weekend.describe())
check("summary lists windows", len(guard_ist.summary()["windows"]) == 1)

# ---------------------------------------------------------------------------
print("\n== 3. mark-price series selection ==")
frame = pd.DataFrame({
    "open": [100.0, 101.0, 102.0],
    "close": [101.0, 102.0, 103.0],
    "mark_open": [100.5, 101.5, np.nan],
    "mark_close": [101.5, 102.5, np.nan],
})
series, coverage, basis = decision_series(frame, True)
check("mark basis chosen", basis == "mark")
check("missing marks fall back to the traded price", abs(series.iloc[2] - 103.0) < 1e-9, str(series.iloc[2]))
check("coverage counts only real marks", abs(coverage - 2 / 3) < 1e-9, str(coverage))
series2, cov2, basis2 = decision_series(frame, False)
check("mark pricing can be switched off", basis2 == "trade" and abs(series2.iloc[0] - 101.0) < 1e-9)
no_mark = frame.drop(columns=["mark_open", "mark_close"])
_, _, basis3 = decision_series(no_mark, True)
check("no mark column -> traded price basis", basis3 == "trade")

quote = MarkPriceQuote("Delta", "BTCUSD", mark_price=67010.5, last_price=67005.0)
check("basis price prefers the mark", quote.basis_price == 67010.5)
check("traded price is kept", quote.last_price == 67005.0)

# ---------------------------------------------------------------------------
print("\n== 4. engine: mark-price basis and stored prices ==")
from app.core.engine import BacktestEngine
from app.core.strategy import PhantomV2Config


class ScriptedStrategy:
    """Emits a signal on the bars we choose; used instead of a real setup."""

    def __init__(self, bars):
        self.bars = bars

    def generate_signals(self, df_1h, df_4h):
        sig = np.zeros(len(df_1h))
        for i, direction in self.bars.items():
            if i < len(sig):
                sig[i] = direction
        return sig


def make_frames(n=200, mark_offset=25.0, start="2024-01-01"):
    """Hourly bars and 4h bars; the mark price runs above the traded price."""
    idx = pd.date_range(start, periods=n, freq="h")
    close = np.linspace(60000, 64000, n) + np.sin(np.arange(n) / 3.0) * 120
    open_ = close - 15.0
    high = np.maximum(open_, close) + 40
    low = np.minimum(open_, close) - 40
    df_1h = pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": np.full(n, 12.0),
        "mark_open": open_ + mark_offset, "mark_high": high + mark_offset,
        "mark_low": low + mark_offset, "mark_close": close + mark_offset,
    }, index=idx)
    idx4 = pd.date_range(start, periods=max(2, n // 4), freq="4h")
    c4 = np.linspace(60000, 64000, len(idx4))
    df_4h = pd.DataFrame({
        "open": c4 - 60, "high": c4 + 120, "low": c4 - 120, "close": c4,
        "volume": np.full(len(idx4), 100.0),
    }, index=idx4)
    return df_1h, df_4h


def run_engine(signals=None, **kwargs):
    df_1h, df_4h = make_frames()
    cfg = kwargs.pop("config", None) or PhantomV2Config(
        stop_loss_atr=50.0, take_profit_atr=500.0, timeout_bars=6, cooldown_bars=0,
        margin_pct=0.25, leverage=2, lot_size_btc=0.001)
    engine = BacktestEngine(config=cfg, data_source="Binance")
    engine.strategy_service = ScriptedStrategy(signals or {10: 1, 40: 1, 70: -1, 100: 1})
    return engine, engine.run(symbol="BTCUSDT", initial_capital_inr=200000.0,
                              df_1h=df_1h, df_4h=df_4h, **kwargs)


engine, results = run_engine()
check("engine reports mark basis", results.get("mark_price_basis") is True,
      str(results.get("mark_price_basis")))
check("mark coverage is 100%", float(results.get("mark_price_coverage", 0)) == 100.0)
trades = results["trades"]
check("engine produced trades", len(trades) >= 4, str(len(trades)))
first = trades[0]
check("entry price is the mark price",
      abs(first["entry_price"] - first["entry_mark_price"]) < 1e-6, str(first))
check("traded entry price differs from the mark by the offset",
      abs(first["entry_trade_price"] - first["entry_mark_price"] + 25.0) < 1e-6,
      f"{first.get('entry_trade_price')} vs {first.get('entry_mark_price')}")
check("exit mark price stored", first.get("exit_mark_price") is not None)
check("exit traded price stored", first.get("exit_trade_price") is not None)
check("mark_price_basis flag on the trade", bool(first.get("mark_price_basis")) is True)
expected_gross = (first["exit_price"] - first["entry_price"]) * first["direction"] \
    * first["lots"] * 85.0
check("PnL is computed on the mark price",
      abs(first["gross_pnl"] - expected_gross) < 1e-6,
      f"{first['gross_pnl']} vs {expected_gross}")

# Same run with mark pricing switched off -> traded price basis.
cfg_off = PhantomV2Config(stop_loss_atr=50.0, take_profit_atr=500.0, timeout_bars=6,
                          cooldown_bars=0, margin_pct=0.25, leverage=2,
                          use_mark_price=False)
_, results_off = run_engine(config=cfg_off)
check("mark pricing can be turned off", results_off.get("mark_price_basis") is False)
off_trade = results_off["trades"][0]
check("traded basis equals the candle price",
      abs(off_trade["entry_price"] - off_trade["entry_trade_price"]) < 1e-6)
check("mark price is still recorded when available",
      off_trade.get("entry_mark_price") is not None)

# No mark columns at all -> falls back cleanly instead of crashing.
df_1h, df_4h = make_frames()
df_1h = df_1h.drop(columns=["mark_open", "mark_high", "mark_low", "mark_close"])
cfg_mark = PhantomV2Config(stop_loss_atr=50.0, take_profit_atr=500.0, timeout_bars=6,
                           cooldown_bars=0, margin_pct=0.25, leverage=2)
engine_nb = BacktestEngine(config=cfg_mark, data_source="Binance")
engine_nb.strategy_service = ScriptedStrategy({10: 1, 70: -1})
res_nb = engine_nb.run(symbol="BTCUSDT", initial_capital_inr=200000.0, df_1h=df_1h, df_4h=df_4h)
check("missing mark data does not break the run", len(res_nb["trades"]) >= 2)
check("missing mark data reports traded basis", res_nb["mark_price_basis"] is False)

# ---------------------------------------------------------------------------
print("\n== 5. engine: skip-new-trade windows ==")
# 2024-01-07 is a Sunday. Signal on bar 166 -> entry on bar 167 (Sunday 23:00).
# Signal on bar 10 -> entry on bar 11 (Monday 11:00) -> allowed.
cfg_win = PhantomV2Config(
    stop_loss_atr=50.0, take_profit_atr=500.0, timeout_bars=4, cooldown_bars=0,
    margin_pct=0.25, leverage=2,
    trading_windows=TradingWindowConfig(
        enabled=True, timezone="UTC",
        windows=[TradingWindow(start_day=6, end_day=6, all_day=True, label="Sunday")]),
)
# Bars 143/150/160 fire just before and inside Sunday; bar 10 is a Monday.
window_signals = {10: 1, 143: 1, 150: -1, 160: 1}
engine_w, results_w = run_engine(signals=window_signals, config=cfg_win)
blocked = results_w["diagnostics"]["blocked_entries"]
check("entries inside the window were refused", blocked == 3, str(blocked))
check("blocked entries are reported as a rejection reason",
      results_w["rejected_reasons"].get(BLOCK_REASON, 0) >= 1, str(results_w["rejected_reasons"]))
check("the run records the schedule it applied",
      results_w["trading_windows"]["active"] is True and len(results_w["trading_windows"]["windows"]) == 1)

# Same schedule disabled -> the same signals all become trades.
cfg_disabled = cfg_win.model_copy(update={
    "trading_windows": TradingWindowConfig(enabled=False, windows=[
        TradingWindow(start_day=6, end_day=6, all_day=True)])})
_, results_d = run_engine(signals=window_signals, config=cfg_disabled)
check("disabled schedule blocks nothing", results_d["diagnostics"]["blocked_entries"] == 0)
check("the same signals all trade when the schedule is off",
      len(results_d["trades"]) == 4 and len(results_w["trades"]) == 1,
      f"off={len(results_d['trades'])} on={len(results_w['trades'])}")

# A signal fired before the window, with the trade still open inside it, must
# NOT be closed by the window (only new entries are skipped).
cfg_open = PhantomV2Config(
    stop_loss_atr=500.0, take_profit_atr=5000.0, timeout_bars=200, cooldown_bars=0,
    margin_pct=0.25, leverage=2,
    trading_windows=TradingWindowConfig(
        enabled=True, timezone="UTC",
        windows=[TradingWindow(start_day=6, end_day=6, all_day=True, label="Sunday")]),
)
df_1h, df_4h = make_frames(n=200)
engine_o = BacktestEngine(config=cfg_open, data_source="Binance")
engine_o.strategy_service = ScriptedStrategy({140: 1})   # entry at bar 141 (Fri 21:00 UTC)
res_o = engine_o.run(symbol="BTCUSDT", initial_capital_inr=200000.0, df_1h=df_1h, df_4h=df_4h)
check("a position opened before the window keeps running", len(res_o["trades"]) == 1)
if res_o["trades"]:
    exit_time = pd.Timestamp(res_o["trades"][0]["exit_time"])
    check("that position was not force-closed at the window edge",
          exit_time.weekday() != 6 or exit_time.hour > 0, str(exit_time))

# ---------------------------------------------------------------------------
print("\n== 6. order manager keeps both prices ==")
from app.services.order_manager import OrderManager

oms = OrderManager(PhantomV2Config(use_mark_price=True, stop_loss_atr=2.0,
                                   take_profit_atr=10.0, leverage=2))
trade = oms.create_order("BTCUSDT", 1, 67010.0, 300.0, datetime(2024, 1, 2, 10), 50000.0,
                         trade_price_usd=67005.0, mark_price_usd=67010.0,
                         mark_price_basis=True)
check("entry mark stored", trade.entry_mark_price == 67010.0)
check("entry traded price stored", trade.entry_trade_price == 67005.0)
check("mark basis flagged", trade.mark_price_basis is True)
closed = oms.close_trade("BTCUSDT", 67500.0, datetime(2024, 1, 2, 12), "TP",
                         detail="target hit", trade_price_usd=67495.0,
                         mark_price_usd=67500.0)
check("exit mark stored", closed.exit_mark_price == 67500.0)
check("exit traded price stored", closed.exit_trade_price == 67495.0)

# ---------------------------------------------------------------------------
print("\n== 7. paper trader: mark price + window ==")
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        import json
        if "tickers" in self.path:
            body = json.dumps({"success": True, "result": {
                "symbol": "BTCUSD", "mark_price": "67100.5", "close": "67095.0",
                "index_price": "67090.0"}}).encode()
        else:
            body = json.dumps({"candles": [], "result": None}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"

from types import SimpleNamespace
from app.services.broker_client import BrokerClient
BrokerClient.DEFAULTS["Delta"]["market"] = BASE
DELTA_DEFINITION = SimpleNamespace(code="Delta", kind="delta", market_data_url=BASE,
                                   trading_api_url=BASE, is_builtin=1, enabled=1)
from app.database.models import init_db
init_db()

from app.services.paper_trader import PaperTradeService
from app.core.mark_price import MarkPriceService

quote = MarkPriceService.current("Delta", "BTCUSDT", definition=DELTA_DEFINITION)
check("Delta mark price parsed", quote is not None and abs(quote.mark_price - 67100.5) < 1e-6, str(quote))
check("Delta traded price parsed", quote is not None and abs(quote.last_price - 67095.0) < 1e-6)
check("quote exposes the perpetual symbol", quote is not None and quote.symbol == "BTCUSD", str(quote.symbol))

svc = PaperTradeService(
    "PhantomV2", PhantomV2Config(use_mark_price=True), initial_capital=200000.0,
    margin_pct=25.0, market_source="Delta", broker_name="Delta",
    trading_windows=TradingWindowConfig(enabled=True, timezone="UTC",
                                        windows=[all_day_window("sunday")]),
    use_mark_price=True)
check("paper service exposes the contract", svc.symbol is not None)
check("paper service holds the schedule", svc.window_guard.enabled is True)
sunday = datetime(2024, 1, 7, 12, 0)
monday = datetime(2024, 1, 8, 12, 0)
check("paper guard blocks Sunday entries", svc.window_guard.allows_new_entry(sunday) is False)
check("paper guard allows Monday entries", svc.window_guard.allows_new_entry(monday) is True)
check("paper guard keeps managing exits", svc.window_guard.allows_exit(sunday) is True)

oms2 = svc.oms
t2 = oms2.create_order("BTCUSDT", 1, 67100.5, 250.0, monday, 50000.0,
                       trade_price_usd=67095.0, mark_price_usd=67100.5,
                       mark_price_basis=True)
closed2 = svc.oms.close_trade("BTCUSDT", 67200.0, monday + timedelta(hours=1), "TP",
                              trade_price_usd=67195.0, mark_price_usd=67200.0)
svc._record_closed(closed2, 1234.5, 12.0, 1246.5)
saved = svc.closed_trades[-1]
check("closed paper trade stores the mark entry", saved.get("entry_mark_price") == 67100.5)
check("closed paper trade stores the traded entry", saved.get("entry_trade_price") == 67095.0)
check("closed paper trade stores the mark exit", saved.get("exit_mark_price") == 67200.0)
check("closed paper trade flags mark basis", saved.get("mark_price_basis") is True)

# ---------------------------------------------------------------------------
print("\n== 8. API endpoints ==")
from app.database.models import SessionLocal, User, BrokerDefinition
import bcrypt
import asyncio
import json as _json

db = SessionLocal()
db.query(User).delete()
db.add(User(username="admin", password_hash=bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode(),
            role="admin", is_active=1, can_paper=1, can_live=0,
            initial_capital=20000.0, margin_deployment_pct=25.0, virtual_balance=20000.0))
db.query(BrokerDefinition).delete()
db.add(BrokerDefinition(code="Binance", name="Binance Futures", kind="binance", is_builtin=1, enabled=1))
db.add(BrokerDefinition(code="Delta", name="Delta Exchange", kind="delta", is_builtin=1,
                        enabled=1, market_data_url=BASE, trading_api_url=BASE))
db.commit()
db.close()

import app.main as main
from app.services.data_sync import DataSyncService
DataSyncService.DELTA_HOSTS = [BASE]
async def _no_sync():
    await asyncio.sleep(3600)
main.daily_sync_task = _no_sync

from fastapi.testclient import TestClient
client = TestClient(main.app)

r = client.post("/token", data={"username": "admin", "password": "admin123"})
check("login", r.status_code == 200, r.text[:200])
H = {"Authorization": f"Bearer {r.json()['access_token']}"}

r = client.get("/market/contract", params={"source": "Delta"}, headers=H)
d = r.json()
check("contract endpoint", r.status_code == 200 and d["perpetual_symbol"] == "BTCUSD"
      and d["contract_type"] == "perpetual", str(d))

r = client.get("/market/mark-price", params={"source": "Delta"}, headers=H)
d = r.json()
check("mark price endpoint", r.status_code == 200 and abs(d["mark_price"] - 67100.5) < 1e-6, str(d))
check("mark price endpoint returns the traded price too", abs(d["last_price"] - 67095.0) < 1e-6, str(d))

r = client.get("/trading-windows", headers=H)
d = r.json()
check("trading-windows GET defaults to off", r.status_code == 200 and d["enabled"] is False, str(d))

r = client.put("/trading-windows", headers=H, json={
    "enabled": True, "timezone": "Asia/Kolkata", "windows": [
        {"start_day": "sat", "start_time": "18:30", "end_day": "mon", "end_time": "01:00",
         "all_day": False, "enabled": True, "label": "Weekend"}]})
d = r.json()
check("trading-windows PUT saves", r.status_code == 200 and d["enabled"] is True and len(d["windows"]) == 1, str(d))
check("saved window keeps its label", d["windows"][0]["label"] == "Weekend", str(d["windows"]))

r = client.get("/broker-settings", headers=H)
d = r.json()
check("broker-settings echoes the schedule", d.get("trading_windows", {}).get("enabled") is True, str(d.get("trading_windows")))
check("broker-settings exposes use_mark_price", "use_mark_price" in d)

r = client.put("/trading-windows", headers=H, json={"enabled": True, "quick_days": ["sunday", 2]})
d = r.json()
check("quick_days replaces the schedule", r.status_code == 200 and len(d["windows"]) == 2, str(d))
check("quick_days windows are all-day", all(w["all_day"] for w in d["windows"]), str(d["windows"]))

r = client.put("/trading-windows", headers=H, json={"enabled": True, "windows": [{"start_day": "nope"}]})
check("invalid weekday is rejected", r.status_code == 400, r.text[:200])

r = client.post("/broker-settings", headers=H, json={
    "initial_capital": 20000, "margin_pct": 25, "broker_name": "Delta",
    "use_mark_price": False})
check("use_mark_price can be saved on the account", r.status_code == 200 and r.json().get("use_mark_price") is False, r.text[:200])
r = client.get("/trading-windows", headers=H)
check("saved preference is returned", r.json().get("use_mark_price") is False, str(r.json()))

# ---------------------------------------------------------------------------
print(f"\n{'=' * 60}")
print(f"PASSED {len(PASS)}   FAILED {len(FAIL)}")
if FAIL:
    print("Failures:")
    for name in FAIL:
        print(f"  - {name}")
    sys.exit(1)
print("All mark-price / trading-window checks passed.")
