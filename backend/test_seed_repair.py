"""Offline verification of the Binance full-history seed + corrupt-data repair.

Covers the client-reported "Binance data is corrupt" fix:
  1. the legacy CSVs (off-grid timestamps like 11:41:59.523330) are REJECTED;
  2. repair_klines deletes duplicate + off-grid candles and keeps good rows;
  3. a full-history seed (fetch_all) for Binance defaults to 1 Jan 2020,
     pages through the range, upserts without duplicates and resumes;
  4. data_health reports the corruption counters the admin UI displays.

No real network access: DataSyncService.fetch_klines is monkeypatched with a
grid-aligned generator.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, '.')

TESTDB = "/tmp/seed_repair_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"

from app.database.models import init_db, SessionLocal, Klines
from app.services.data_sync import DataSyncService, MarketDataError

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not cond else ""))

init_db()
DataSyncService.SEED_PAGE_SLEEP_SECONDS = 0.0


def add_kline(db, source, symbol, interval, event_time, close=100.0):
    db.add(Klines(source=source, symbol=symbol, interval=interval, event_time=event_time,
                  open=close, high=close, low=close, close=close, volume=1.0))


# ---------------------------------------------------------------------------
print("\n== CSV import validation ==")
good_csv = os.path.join(tempfile.gettempdir(), "good_1h.csv")
with open(good_csv, "w") as handle:
    handle.write("event_time,open,high,low,close,volume\n")
    handle.write("2020-01-01 00:00:00,100,101,99,100.5,10\n")
    handle.write("2020-01-01 01:00:00,100.5,102,100,101,11\n")
try:
    result = DataSyncService.seed_from_csv(good_csv, "1h", "BTCUSDT", "Binance")
    check("on-grid CSV imports", result["fetched"] == 2 and result["total"] == 2)
except Exception as exc:
    check("on-grid CSV imports", False, str(exc))

corrupt_csv = os.path.join(tempfile.gettempdir(), "corrupt_1h.csv")
with open(corrupt_csv, "w") as handle:
    handle.write("event_time,open,high,low,close,volume\n")
    handle.write("2020-06-26 11:41:59.523330,10058.1,10062.0,10042.3,10050.8,5055030\n")
    handle.write("2020-06-26 12:41:59.523330,10040.0,10058.9,10036.9,10037.9,1324537\n")
try:
    DataSyncService.seed_from_csv(corrupt_csv, "1h", "BTCUSDT", "Binance")
    check("off-grid CSV rejected", False, "import was accepted")
except ValueError as exc:
    check("off-grid CSV rejected", "aligned" in str(exc) and "2 of 2" in str(exc), str(exc)[:120])

db = SessionLocal()
check("off-grid CSV stored nothing", db.query(Klines).filter_by(interval="1h").count() == 2)
db.close()


# ---------------------------------------------------------------------------
print("\n== repair_klines ==")
db = SessionLocal()
add_kline(db, "Binance", "BTCUSDT", "4h", datetime(2020, 1, 1, 0, 0))        # good
add_kline(db, "Binance", "BTCUSDT", "4h", datetime(2020, 1, 1, 4, 0))        # good
add_kline(db, "Binance", "BTCUSDT", "4h", datetime(2020, 1, 1, 4, 0))        # duplicate timestamp
add_kline(db, "Binance", "BTCUSDT", "4h", datetime(2020, 1, 1, 5, 41, 59, 523330))  # off-grid
add_kline(db, "Binance", "BTCUSDT", "4h", datetime(2020, 1, 1, 8, 0))        # good
db.commit()
db.close()

summary = DataSyncService.repair_klines("Binance", "BTCUSDT", ["4h"])
item = summary[0]
check("repair reports totals", item["total"] == 5 and item["removed"] == 2)
check("repair splits defect kinds", item["duplicates_removed"] == 1 and item["misaligned_removed"] == 1)
db = SessionLocal()
remaining = [row.event_time for row in db.query(Klines).filter_by(
    source="Binance", symbol="BTCUSDT", interval="4h").order_by(Klines.event_time).all()]
db.close()
check("repair keeps only aligned unique candles",
      remaining == [datetime(2020, 1, 1), datetime(2020, 1, 1, 4), datetime(2020, 1, 1, 8)], str(remaining))
again = DataSyncService.repair_klines("Binance", "BTCUSDT", ["4h"])[0]
check("repair is idempotent", again["removed"] == 0 and again["kept"] == 3)

try:
    DataSyncService.repair_klines("Binance", "BTCUSDT", ["2h"])
    check("repair rejects unknown intervals", False)
except MarketDataError:
    check("repair rejects unknown intervals", True)


# ---------------------------------------------------------------------------
print("\n== data_health ==")
health = {(row["source"], row["symbol"], row["interval"]): row for row in DataSyncService.data_health()}
h1h = health.get(("Binance", "BTCUSDT", "1h"))
h4h = health.get(("Binance", "BTCUSDT", "4h"))
check("health counts clean 1h series", h1h and h1h["duplicate_rows"] == 0 and h1h["misaligned_rows"] == 0)
check("health counts corrupt series before repair was possible",
      h4h and h4h["count"] == 3 and h4h["duplicate_rows"] == 0 and h4h["misaligned_rows"] == 0)
db = SessionLocal()
add_kline(db, "Binance", "BTCUSDT", "5m", datetime(2020, 1, 1))
add_kline(db, "Binance", "BTCUSDT", "5m", datetime(2020, 1, 1))              # duplicate
add_kline(db, "Binance", "BTCUSDT", "5m", datetime(2020, 1, 2, 11, 41, 59))  # off-grid
db.commit(); db.close()
health = {(row["source"], row["symbol"], row["interval"]): row for row in DataSyncService.data_health()}
h5m = health[("Binance", "BTCUSDT", "5m")]
check("health exposes duplicates", h5m["duplicate_rows"] == 1)
check("health exposes off-grid candles", h5m["misaligned_rows"] == 1 and h5m["scanned"] == 3)


# ---------------------------------------------------------------------------
print("\n== Binance full-history seed defaults to 2020-01-01 ==")
calls = []
REAL_FETCH = DataSyncService.fetch_klines.__func__

def scripted_fetch(cls, source="Binance", symbol="BTCUSDT", interval="1h",
                   start_time=None, end_time=None, limit=1000, definition=None):
    calls.append({"interval": interval, "start": start_time, "end": end_time, "limit": limit})
    step = timedelta(seconds=cls._interval_seconds(interval))
    rows, cursor = [], cls._as_datetime(start_time)
    end = cls._as_datetime(end_time)
    while cursor <= end and len(rows) < int(limit):
        rows.append({"event_time": cursor, "open": 1.0, "high": 2.0, "low": 0.5,
                     "close": 1.5, "volume": 10.0})
        cursor += step
    return rows

DataSyncService.fetch_klines = classmethod(scripted_fetch)
try:
    summary = DataSyncService.seed_market_data(
        source="Binance", symbol="BTCUSDT", intervals=["1d"],
        start_date=None, end_date="2026-08-28", limit=1500, fetch_all=True,
    )
finally:
    DataSyncService.fetch_klines = classmethod(REAL_FETCH)

entry = summary[0]
expected_candles = (datetime(2026, 8, 29) - datetime(2020, 1, 1)).days  # inclusive daily candles
check("fetch_all Binance starts at 2020-01-01",
      entry["requested_start"] == datetime(2020, 1, 1), str(entry.get("requested_start")))
check("seed windows page the whole range", entry["pages"] >= 2 and entry["status"] == "completed",
      f"pages={entry.get('pages')} status={entry.get('status')}")
check("every candle from 2020 to end stored",
      entry["total"] == expected_candles and entry["first"] == datetime(2020, 1, 1),
      f"total={entry.get('total')} expected={expected_candles}")
check("first request begins at the range start",
      calls and calls[0]["start"] == datetime(2020, 1, 1), str(calls[0]["start"] if calls else None))
db = SessionLocal()
stored = db.query(Klines).filter_by(source="Binance", symbol="BTCUSDT", interval="1d").count()
db.close()
check("no duplicate rows stored (upsert)", stored == expected_candles, f"stored={stored}")

# Re-running the identical completed range is a durable no-op...
calls.clear()
DataSyncService.fetch_klines = classmethod(scripted_fetch)
try:
    rerun = DataSyncService.seed_market_data(
        source="Binance", symbol="BTCUSDT", intervals=["1d"],
        start_date="2020-01-01", end_date="2026-08-28", limit=1500, fetch_all=True,
    )[0]
finally:
    DataSyncService.fetch_klines = classmethod(REAL_FETCH)
check("completed range re-run skips fetching", rerun.get("skipped") is True and not calls)

# ...and extends cleanly when the end date moves forward.
DataSyncService.fetch_klines = classmethod(scripted_fetch)
try:
    extended = DataSyncService.seed_market_data(
        source="Binance", symbol="BTCUSDT", intervals=["1d"],
        start_date="2020-01-01", end_date="2026-08-29", limit=1500, fetch_all=True,
    )[0]
finally:
    DataSyncService.fetch_klines = classmethod(REAL_FETCH)
check("later end date resumes instead of restarting",
      extended["total"] == expected_candles + 1 and extended["first"] == datetime(2020, 1, 1),
      f"total={extended.get('total')}")


# ---------------------------------------------------------------------------
print("\n== repair integrates with the seed path ==")
db = SessionLocal()
add_kline(db, "Binance", "BTCUSDT", "1d", datetime(2021, 1, 1, 6, 30))  # off-grid legacy row
db.commit(); db.close()
DataSyncService.fetch_klines = classmethod(scripted_fetch)
try:
    repaired = DataSyncService.repair_klines("Binance", "BTCUSDT", ["1d"])
    reseeded = DataSyncService.seed_market_data(
        source="Binance", symbol="BTCUSDT", intervals=["1d"],
        start_date="2020-01-01", end_date="2026-08-29", limit=1500, fetch_all=True,
    )[0]
finally:
    DataSyncService.fetch_klines = classmethod(REAL_FETCH)
check("repair removes the legacy off-grid row", repaired[0]["misaligned_removed"] == 1)
db = SessionLocal()
off_grid = db.query(Klines).filter_by(source="Binance", symbol="BTCUSDT", interval="1d").filter(
    Klines.event_time == datetime(2021, 1, 1, 6, 30)).count()
total = db.query(Klines).filter_by(source="Binance", symbol="BTCUSDT", interval="1d").count()
db.close()
check("post-repair reseed leaves a clean grid series",
      off_grid == 0 and total == extended["total"], f"total={total}")


# ---------------------------------------------------------------------------
print("\n== transient request retries (adapter level) ==")
import requests as requests_module
from app.services.data_sync import TransientMarketDataError

class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else []
        self.headers = headers or {}
        self.text = text
    def json(self):
        return self._payload

real_get = requests_module.get
DataSyncService.REQUEST_RETRIES = 3
DataSyncService.REQUEST_BACKOFF_SECONDS = 0.0
get_calls = {"n": 0}

def flaky_get(url, params=None, timeout=None, headers=None, **kw):
    get_calls["n"] += 1
    if get_calls["n"] <= 2:
        raise requests_module.exceptions.Timeout("read timed out")
    return FakeResponse(200, payload=[[1577836800000, "1", "2", "0.5", "1.5", "10"]])

requests_module.get = flaky_get
try:
    rows = DataSyncService.fetch_klines("Binance", "BTCUSDT", "1h", limit=10)
    check("timeout then success: retried and returned candles",
          len(rows) == 1 and get_calls["n"] == 3, f"calls={get_calls['n']}")
finally:
    requests_module.get = real_get

get_calls["n"] = 0
def always_429(url, params=None, timeout=None, headers=None, **kw):
    get_calls["n"] += 1
    return FakeResponse(429, payload={}, headers={"Retry-After": "0"}, text="rate limited")
requests_module.get = always_429
try:
    try:
        DataSyncService.fetch_klines("Binance", "BTCUSDT", "1h", limit=10)
        check("persistent 429 raises TransientMarketDataError", False)
    except TransientMarketDataError as exc:
        check("persistent 429 raises TransientMarketDataError", "HTTP 429" in str(exc))
    check("429 retried per REQUEST_RETRIES then gave up",
          get_calls["n"] == DataSyncService.REQUEST_RETRIES + 1, f"calls={get_calls['n']}")
finally:
    requests_module.get = real_get

get_calls["n"] = 0
def bad_request(url, params=None, timeout=None, headers=None, **kw):
    get_calls["n"] += 1
    return FakeResponse(400, payload={"code": -1100}, text="bad symbol")
requests_module.get = bad_request
try:
    try:
        DataSyncService.fetch_klines("Binance", "BTCUSDT", "1h", limit=10)
        check("permanent 400 is not retried", False)
    except MarketDataError as exc:
        check("permanent 400 is not retried", "HTTP 400" in str(exc))
    check("permanent 400 answered after one call", get_calls["n"] == 1)
finally:
    requests_module.get = real_get


# ---------------------------------------------------------------------------
print("\n== window retries carry a long range past exchange hiccups ==")
DataSyncService.WINDOW_RETRIES = 2
DataSyncService.WINDOW_BACKOFF_SECONDS = 0.0
glitch = {"left": 2}

def glitchy_fetch(cls, source="Binance", symbol="BTCUSDT", interval="1h",
                  start_time=None, end_time=None, limit=1000, definition=None):
    if glitch["left"] > 0 and start_time and cls._as_datetime(start_time) >= datetime(2020, 2, 1):
        glitch["left"] -= 1
        raise TransientMarketDataError("request failed after 4 attempts: ReadTimeout")
    return scripted_fetch(cls, source, symbol, interval, start_time, end_time, limit, definition)

DataSyncService.fetch_klines = classmethod(glitchy_fetch)
try:
    summary = DataSyncService.seed_market_data(
        source="Binance", symbol="BTCUSDT", intervals=["1d"],
        start_date="2020-01-01", end_date="2020-03-01", limit=20, fetch_all=True,
    )[0]
finally:
    DataSyncService.fetch_klines = classmethod(scripted_fetch)
expected_leap = (datetime(2020, 3, 2) - datetime(2020, 1, 1)).days  # 61 inclusive daily candles
check("transient mid-range failures retried at the same window",
      summary["status"] == "completed" and summary["total"] == expected_leap,
      f"status={summary.get('status')} total={summary.get('total')}")


# ---------------------------------------------------------------------------
print("\n== exhausted retries keep progress and tell the admin to re-run ==")
persistent = {"starts": []}

def dying_fetch(cls, source="Binance", symbol="BTCUSDT", interval="1h",
                start_time=None, end_time=None, limit=1000, definition=None):
    start = cls._as_datetime(start_time)
    persistent["starts"].append(start)
    if start >= datetime(2021, 1, 20):
        raise TransientMarketDataError("request failed after 4 attempts: ConnectionError")
    return scripted_fetch(cls, source, symbol, interval, start_time, end_time, limit, definition)

# Fresh range (2021) so the durable cursor of the completed 2020 range above
# cannot satisfy this request as "already completed".
DataSyncService.fetch_klines = classmethod(dying_fetch)
try:
    failed = DataSyncService.seed_market_data(
        source="Binance", symbol="BTCUSDT", intervals=["1d"],
        start_date="2021-01-01", end_date="2021-03-01", limit=20, fetch_all=True,
    )[0]
finally:
    DataSyncService.fetch_klines = classmethod(scripted_fetch)
check("exhausted retries mark the range failed", failed["status"] == "failed" and failed.get("error"),
      f"status={failed.get('status')} error={failed.get('error')}")
check("failure message says the range resumes on re-run", "re-run the same seed" in (failed.get("error") or ""))
db = SessionLocal()
kept = db.query(Klines).filter_by(source="Binance", symbol="BTCUSDT", interval="1d").filter(
    Klines.event_time >= datetime(2021, 1, 1), Klines.event_time < datetime(2021, 1, 20)).distinct().count()
db.close()
check("windows committed before the failure are kept", kept >= 19, f"kept={kept}")

# Re-running with the exchange healthy resumes at the cursor and completes.
expected_2021 = (datetime(2021, 3, 2) - datetime(2021, 1, 1)).days  # 60 inclusive daily candles
resumed = DataSyncService.seed_market_data(
    source="Binance", symbol="BTCUSDT", intervals=["1d"],
    start_date="2021-01-01", end_date="2021-03-01", limit=20, fetch_all=True,
)[0]
check("re-run after failure resumes and completes",
      resumed["status"] == "completed" and resumed["total"] == expected_2021,
      f"status={resumed.get('status')} total={resumed.get('total')}")


# ---------------------------------------------------------------------------
print("\n== mark-price backfill pages the whole range ==")
mark_pages = {"n": 0}
REAL_MARK_FETCH = DataSyncService.fetch_mark_klines.__func__

def scripted_mark(cls, source="Binance", symbol="BTCUSDT", interval="1h",
                  start_time=None, end_time=None, limit=1000, definition=None):
    mark_pages["n"] += 1
    step = timedelta(seconds=cls._interval_seconds(interval))
    rows, cursor, stop = [], cls._as_datetime(start_time), cls._as_datetime(end_time)
    while cursor <= stop and len(rows) < int(limit):
        rows.append({"event_time": cursor, "open": 200.0, "high": 201.0,
                     "low": 199.0, "close": 200.5})
        cursor += step
    return rows

DataSyncService.fetch_mark_klines = classmethod(scripted_mark)
try:
    mark_summary = DataSyncService.sync_mark_prices(
        "Binance", "BTCUSDT", ["1d"], start_time="2020-01-01", end_time="2020-01-09", limit=3)
finally:
    DataSyncService.fetch_mark_klines = classmethod(REAL_MARK_FETCH)
mentry = mark_summary[0]
check("mark backfill split the range into pages",
      mark_pages["n"] == 3 and mentry["fetched"] == 9 and not mentry.get("error"),
      f"pages={mark_pages['n']} fetched={mentry.get('fetched')}")
db = SessionLocal()
marked = db.query(Klines).filter(
    Klines.source == "Binance", Klines.symbol == "BTCUSDT", Klines.interval == "1d",
    Klines.event_time >= datetime(2020, 1, 1), Klines.event_time <= datetime(2020, 1, 9),
    Klines.mark_close.isnot(None)).count()
db.close()
check("mark prices written across every candle of the range", marked == 9, f"marked={marked}")


# ---------------------------------------------------------------------------
print("\n== background seed endpoint (long ranges never hold the request) ==")
import asyncio
import bcrypt as _bcrypt
from app.database.models import User
from fastapi.testclient import TestClient
import app.main as main

async def _no_sync():
    await asyncio.sleep(3600)
main.daily_sync_task = _no_sync

db = SessionLocal()
db.query(User).delete()
db.add(User(username="admin", password_hash=_bcrypt.hashpw(b"admin123", _bcrypt.gensalt()).decode(),
            role="admin", is_active=1, can_paper=1, can_live=0,
            initial_capital=20000.0, margin_deployment_pct=25.0, virtual_balance=20000.0))
db.commit()
db.close()

client = TestClient(main.app)
token = client.post("/token", data={"username": "admin", "password": "admin123"}).json()["access_token"]
H = {"Authorization": f"Bearer {token}"}

r = client.post("/admin/market-data/seed", headers=H, json={
    "source": "Binance", "symbol": "BTCUSDT", "intervals": ["1h"], "limit": 50,
    "start_date": "2020-01-01", "end_date": "2020-01-05", "fetch_all": True, "background": True})
d = r.json()
check("background seed accepted immediately",
      r.status_code == 200 and d["background"] is True and "started" in d["status"], str(d)[:200])

import time as _time
deadline = _time.time() + 30
final = None
while _time.time() < deadline:
    state = client.get("/admin/market-data/seed-job", headers=H).json()
    if not state.get("running"):
        final = state.get("last")
        break
    _time.sleep(0.2)
check("background job finished", final is not None and final.get("status") == "Seed completed",
      str(final)[:200])
rows = client.get("/admin/market-data/progress", headers=H).json()
check("background job left durable completed cursors",
      any(row.get("status") == "completed" and row.get("interval") == "1h" for row in rows))
db = SessionLocal()
bg_candles = db.query(Klines).filter_by(source="Binance", symbol="BTCUSDT", interval="1h").filter(
    Klines.event_time >= datetime(2020, 1, 1), Klines.event_time <= datetime(2020, 1, 5, 23)).count()
db.close()
check("background job seeded the requested range", bg_candles == 120, f"candles={bg_candles}")  # 5 days x 24h


# ===========================================================================
print("\n========== DELTA EXCHANGE: same guarantees, Delta paths ==========")

# Earlier sections patched fetch_klines with the Binance script; restore the
# REAL fetch_klines so the Delta sections exercise the genuine Delta adapter
# (fetch_klines → _delta_fetch → _delta_fetch_one).
DataSyncService.fetch_klines = classmethod(REAL_FETCH)

# ---------------------------------------------------------------------------
print("\n== Delta adapter classifies transient vs permanent failures ==")
REAL_DELTA_ONE = DataSyncService._delta_fetch_one.__func__
one_behaviour = {"mode": "transport"}

def scripted_delta_one(cls, host, params):
    mode = one_behaviour["mode"]
    if mode == "transport":
        return [], None, "request failed after 5 attempts: ConnectionError"
    if mode == "429":
        return [], 429, "HTTP 429 Too many requests"
    if mode == "400":
        return [], 400, "HTTP 400 Invalid resolution"
    if mode == "200-empty":
        return [], 200, ""
    return [], 200, ""

DataSyncService._delta_fetch_one = classmethod(scripted_delta_one)
try:
    for mode, expect_transient in [("transport", True), ("429", True), ("400", False), ("200-empty", False)]:
        one_behaviour["mode"] = mode
        try:
            DataSyncService.fetch_klines("Delta", "BTCUSDT", "1h", limit=10)
            check(f"Delta {mode} raises", False, "no exception")
        except TransientMarketDataError as exc:
            check(f"Delta {mode} → transient", expect_transient, str(exc)[:80])
            check(f"Delta {mode} message keeps diagnostics", "Delta Exchange returned 0 candles" in str(exc))
        except MarketDataError as exc:
            check(f"Delta {mode} → permanent", not expect_transient, str(exc)[:80])
            if mode == "200-empty":
                check("Delta 200-empty keeps the pre-listing marker",
                      "HTTP 200 (empty result)" in str(exc), str(exc)[:160])

    # A permanent answer on one host wins even if another host has transport trouble.
    def mixed_delta_one(cls, host, params):
        return ([], 400, "HTTP 400 Illegal characters") if host.endswith("/a") else ([], None, "request failed")
    DataSyncService._delta_fetch_one = classmethod(mixed_delta_one)
    saved_hosts = list(DataSyncService.DELTA_HOSTS)
    DataSyncService.DELTA_HOSTS = ["http://host/a", "http://host/b"]
    try:
        try:
            DataSyncService.fetch_klines("Delta", "BTCUSDT", "1h", limit=10)
            check("Delta mixed 400+transport → permanent", False, "no exception")
        except TransientMarketDataError:
            check("Delta mixed 400+transport → permanent", False, "classified transient")
        except MarketDataError as exc:
            check("Delta mixed 400+transport → permanent", "HTTP 400" in str(exc))
    finally:
        DataSyncService.DELTA_HOSTS = saved_hosts
finally:
    DataSyncService._delta_fetch_one = classmethod(REAL_DELTA_ONE)


# ---------------------------------------------------------------------------
print("\n== Delta full-history: pre-listing empties + mid-range retries ==")
REAL_DELTA_FETCH = DataSyncService._delta_fetch.__func__
# Product lists on 15 Jan: the first window (1–10 Jan) is entirely pre-listing
# and must advance as an empty page; the second window spans the listing and
# returns a partial page, exactly like the real exchange.
LISTING = datetime(2020, 1, 15)
delta_glitch = {"attempts": {}}
delta_mark_symbols = []

def scripted_delta_fetch(cls, symbol, interval, start_time=None, end_time=None, limit=1000, hosts=None):
    is_mark = str(symbol).upper().startswith("MARK:")
    if is_mark:
        delta_mark_symbols.append(str(symbol).upper())
    step = timedelta(seconds=cls._interval_seconds(interval))
    start = cls._as_datetime(start_time)
    stop = cls._as_datetime(end_time)
    if not is_mark:
        if stop < LISTING:
            # Faithful pre-listing window: the real adapter raises exactly this
            # message when every host answers HTTP 200 with zero candles.
            raise MarketDataError(
                f"Delta Exchange returned 0 candles for {symbol} {interval}: "
                f"mock → HTTP 200 (empty result)")
        start = max(start, LISTING)
        key = (str(symbol), start)
        delta_glitch["attempts"][key] = delta_glitch["attempts"].get(key, 0) + 1
        if start == datetime(2020, 1, 21) and delta_glitch["attempts"][key] == 1:
            raise TransientMarketDataError("request failed after 5 attempts: ReadTimeout")
    rows, cursor = [], start
    while cursor <= stop and len(rows) < int(limit):
        rows.append({"event_time": cursor, "open": 300.0, "high": 302.0, "low": 299.0,
                     "close": 301.0, "volume": 5.0})
        cursor += step
    return rows

DataSyncService._delta_fetch = classmethod(scripted_delta_fetch)
try:
    dsummary = DataSyncService.seed_market_data(
        source="Delta", symbol="BTCUSDT", intervals=["1d"],
        start_date="2020-01-01", end_date="2020-02-15", limit=10, fetch_all=True,
    )[0]
finally:
    DataSyncService._delta_fetch = classmethod(REAL_DELTA_FETCH)

expected_delta = (datetime(2020, 2, 16) - LISTING).days  # 32 inclusive daily candles
check("Delta range completes through pre-listing empties and a mid-range hiccup",
      dsummary["status"] == "completed" and dsummary["total"] == expected_delta,
      f"status={dsummary.get('status')} total={dsummary.get('total')} err={dsummary.get('error')}")
check("Delta first stored candle is the listing candle",
      dsummary.get("first") == LISTING, str(dsummary.get("first")))
check("Delta pre-listing window advanced as an empty page",
      (dsummary.get("empty_pages") or 0) >= 1, str(dsummary.get("empty_pages")))
db = SessionLocal()
drows = [r.event_time for r in db.query(Klines).filter_by(
    source="Delta", symbol="BTCUSDT", interval="1d").order_by(Klines.event_time).all()]
db.close()
check("Delta candles form a clean grid with no duplicates", len(drows) == expected_delta
      and len(set(drows)) == len(drows), f"rows={len(drows)}")


# ---------------------------------------------------------------------------
print("\n== Delta mark-price backfill pages the MARK: perpetual series ==")
DataSyncService._delta_fetch = classmethod(scripted_delta_fetch)
try:
    dmark = DataSyncService.sync_mark_prices(
        "Delta", "BTCUSDT", ["1d"], start_time="2020-01-15", end_time="2020-01-24", limit=3)
finally:
    DataSyncService._delta_fetch = classmethod(REAL_DELTA_FETCH)
dmentry = dmark[0]
check("Delta mark backfill paged the range (3-candle pages)",
      dmentry["fetched"] == 10 and dmentry["total"] == 10 and not dmentry.get("error"),
      f"fetched={dmentry.get('fetched')} total={dmentry.get('total')}")
check("Delta mark series requested the MARK: perpetual symbol",
      delta_mark_symbols and all(s.startswith("MARK:") for s in delta_mark_symbols),
      str(delta_mark_symbols[:3]))
db = SessionLocal()
dmarked = db.query(Klines).filter(
    Klines.source == "Delta", Klines.symbol == "BTCUSDT", Klines.interval == "1d",
    Klines.event_time >= datetime(2020, 1, 15), Klines.event_time <= datetime(2020, 1, 24),
    Klines.mark_close.isnot(None)).count()
db.close()
check("Delta mark prices written on every candle of the range", dmarked == 10, f"marked={dmarked}")


# ---------------------------------------------------------------------------
print("\n== Delta background seed endpoint ==")
DataSyncService._delta_fetch = classmethod(scripted_delta_fetch)
try:
    r = client.post("/admin/market-data/seed", headers=H, json={
        "source": "Delta", "symbol": "BTCUSDT", "intervals": ["1d"], "limit": 5,
        "start_date": "2020-03-01", "end_date": "2020-03-15", "fetch_all": True,
        "background": True, "include_mark_price": True})
    d = r.json()
    check("Delta background seed accepted immediately",
          r.status_code == 200 and d["background"] is True and "started" in d["status"], str(d)[:200])
    deadline = _time.time() + 30
    dfinal = None
    while _time.time() < deadline:
        state = client.get("/admin/market-data/seed-job", headers=H).json()
        if not state.get("running"):
            dfinal = state.get("last")
            break
        _time.sleep(0.2)
    check("Delta background job finished", dfinal is not None and dfinal.get("status") == "Seed completed",
          str(dfinal)[:300])
    check("Delta background job included the paged mark series",
          any((m.get("total") or 0) > 0 and not m.get("error")
              for m in (dfinal or {}).get("mark_price", {}).get("summary", [])),
          str((dfinal or {}).get("mark_price"))[:200])
    rows = client.get("/admin/market-data/progress", headers=H).json()
    check("Delta background job left durable completed cursors",
          any(row.get("source") == "Delta" and row.get("status") == "completed"
              and row.get("interval") == "1d" for row in rows))
finally:
    DataSyncService._delta_fetch = classmethod(REAL_DELTA_FETCH)

db = SessionLocal()
dbg = db.query(Klines).filter_by(source="Delta", symbol="BTCUSDT", interval="1d").filter(
    Klines.event_time >= datetime(2020, 3, 1), Klines.event_time <= datetime(2020, 3, 15)).count()
db.close()
check("Delta background job seeded the requested range", dbg == 15, f"candles={dbg}")


print(f"\n{'=' * 60}\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
    sys.exit(1)
