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


print(f"\n{'=' * 60}\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
    sys.exit(1)
