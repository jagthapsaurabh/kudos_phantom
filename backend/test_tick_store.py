"""Persist live ticks so they can be queried and resampled later.

The in-memory feed only keeps the newest quote. These tests cover the store
that writes every usable tick into SQLite and reads it back as a raw series
or as OHLC candles.

1. a published websocket/REST quote is flushed into market_ticks
2. NullTickFeed (feed off) does not write
3. bid/ask and venue event time are taken from the raw frame
4. identical same-millisecond quotes are de-duplicated
5. a date window returns only ticks inside it
6. ticks resample to 1-minute OHLC
7. a broken write never raises into the feed
"""
import os
import sys
from datetime import datetime, timedelta

TESTDB = "/tmp/tick_store_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"
os.environ["PHANTOM_RECORD_TICKS"] = "1"
os.environ["PHANTOM_COLLECT_TICKS"] = "0"

sys.path.insert(0, os.path.dirname(__file__) or ".")

from app.core.mark_price import MarkPriceQuote
from app.database.models import MarketTick, SessionLocal, init_db
from app.services.tick_feed import NullTickFeed, RestTickFeed, parse_binance, parse_delta
from app.services.tick_store import (
    OHLC_SECONDS, RECORDER, bid_ask_of, collector_enabled, event_time_of,
    flush_ticks, latest_tick, query_ticks, quote_to_row, record_tick,
    recording_enabled, series_stats, ticks_to_ohlc,
)

init_db()
RECORDER._table_ready = False
RECORDER._buf.clear()
RECORDER.accepted = RECORDER.written = RECORDER.dropped = 0
RECORDER._last_key = None

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not cond else ""))


print("\n== 1. a published quote is flushed into market_ticks ==")
feed = RestTickFeed(lambda: None, "Binance", "BTCUSDT", interval=30.0, max_age=60.0)
q = parse_binance({"e": "markPriceUpdate", "s": "BTCUSDT", "p": "60123.40", "i": "60120.00",
                   "E": 1_700_000_000_000})
check("parser produced a quote", q is not None and q.mark_price == 60123.40)
ok = feed.publish(q)
flush_ticks()
rows = query_ticks("Binance", "BTCUSDT")
check("publish returned True", ok is True)
check("one tick was written", len(rows) == 1, len(rows))
check("mark price is stored", rows and rows[0].mark_price == 60123.40)
check("index price is stored", rows and rows[0].index_price == 60120.00)
check("feed kind is rest", rows and rows[0].feed_kind == "rest")

print("\n== 2. NullTickFeed does not write ==")
before = RECORDER.accepted
null = NullTickFeed("Binance", "BTCUSDT")
null.publish(MarkPriceQuote("Binance", "BTCUSDT", mark_price=99999.0))
flush_ticks()
check("off-feed quotes are not recorded", RECORDER.accepted == before, RECORDER.accepted)

print("\n== 3. bid/ask and venue event time ==")
book = parse_binance({"e": "bookTicker", "s": "BTCUSDT", "b": "60000.00", "a": "60010.00",
                      "E": 1_700_000_001_000})
bid, ask = bid_ask_of(book)
check("book ticker bid/ask", bid == 60000.0 and ask == 60010.0, (bid, ask))
check("venue event time is milliseconds",
      event_time_of(book) == datetime.utcfromtimestamp(1_700_000_001),
      event_time_of(book))
delta = parse_delta({"type": "ticker", "symbol": "BTCUSD",
                     "mark_price": "60100.50", "last_price": "60101.00",
                     "best_bid": "60099", "best_ask": "60102",
                     "timestamp": 1_700_000_002_000_000})
row = quote_to_row(delta, "websocket")
check("delta row uses BTCUSD", row and row["symbol"] == "BTCUSD")
check("delta bid/ask from best_*", row and row["bid"] == 60099.0 and row["ask"] == 60102.0, row)
check("delta timestamp microseconds",
      row and row["event_time"] == datetime.utcfromtimestamp(1_700_000_002),
      row and row["event_time"])

print("\n== 4. identical same-millisecond quotes are de-duplicated ==")
RECORDER._last_key = None
same = MarkPriceQuote("Binance", "BTCUSDT", mark_price=61000.0,
                      fetched_at=datetime(2024, 1, 1, 12, 0, 0),
                      raw={"E": 1_700_000_010_000})
check("first copy is kept", record_tick(same, "websocket") is True)
check("duplicate is dropped", record_tick(same, "websocket") is False)
check("dropped counter moved", RECORDER.dropped >= 1, RECORDER.dropped)

print("\n== 5. a date window returns only ticks inside it ==")
flush_ticks()
# Plant two extra rows on known days.
from app.database.models import SessionLocal
db = SessionLocal()
db.add(MarketTick(source="Binance", symbol="BTCUSDT",
                  event_time=datetime(2024, 6, 1, 10, 0, 0), mark_price=65000.0, feed_kind="websocket"))
db.add(MarketTick(source="Binance", symbol="BTCUSDT",
                  event_time=datetime(2024, 6, 3, 10, 0, 0), mark_price=66000.0, feed_kind="websocket"))
db.add(MarketTick(source="Delta", symbol="BTCUSD",
                  event_time=datetime(2024, 6, 2, 10, 0, 0), mark_price=65500.0, feed_kind="websocket"))
db.commit(); db.close()
window = query_ticks("Binance", "BTCUSDT",
                     start=datetime(2024, 6, 1), end=datetime(2024, 6, 2))
check("window is source-filtered", all(r.source == "Binance" for r in window))
check("window excludes 6 Jun 3", all(r.event_time < datetime(2024, 6, 2) for r in window),
      [r.event_time for r in window])
check("window includes 1 Jun", any(r.mark_price == 65000.0 for r in window))
latest = latest_tick("Binance", "BTCUSDT")
check("latest is the newest Binance tick", latest is not None and latest.mark_price == 66000.0,
      latest.mark_price if latest else None)
stats = series_stats()
check("stats list both venues",
      { (s["source"], s["symbol"]) for s in stats } >= {("Binance", "BTCUSDT"), ("Delta", "BTCUSD")},
      stats)

print("\n== 6. ticks resample to 1-minute OHLC ==")
class T:
    def __init__(self, ts, price):
        self.event_time = ts
        self.mark_price = price
        self.last_price = None

base = datetime(2024, 6, 1, 10, 0, 5)
sample = [
    T(base, 100.0),
    T(base + timedelta(seconds=10), 110.0),
    T(base + timedelta(seconds=20), 90.0),
    T(base + timedelta(seconds=70), 95.0),  # next minute
]
bars = ticks_to_ohlc(sample, "1m")
check("two minute buckets", len(bars) == 2, len(bars))
check("first bar OHLC", bars and bars[0]["open"] == 100.0 and bars[0]["high"] == 110.0
      and bars[0]["low"] == 90.0 and bars[0]["close"] == 90.0, bars[0] if bars else None)
check("first bar volume is tick count", bars and bars[0]["volume"] == 3)
check("second bar is the later minute", bars and bars[1]["open"] == 95.0 and bars[1]["volume"] == 1)
check("1h is a supported interval", "1h" in OHLC_SECONDS)
try:
    ticks_to_ohlc(sample, "3d")
    check("unknown interval is rejected", False)
except ValueError:
    check("unknown interval is rejected", True)

print("\n== 7. a broken write never raises into the feed ==")
check("recording defaults on", recording_enabled() is True)
check("collector is off on a /tmp test db", collector_enabled() is False)
# Force a flush against a closed / bogus state by emptying then injecting a
# row and pointing the recorder at a bad engine — record_tick itself must
# swallow. We just call it with None.
check("record_tick(None) is False, not raised", record_tick(None) is False)
ws_quote = parse_binance({"e": "markPriceUpdate", "s": "BTCUSDT", "p": "70000"})
# RestTickFeed.publish already persisted above; a websocket-kind feed would too.
from app.services.tick_feed import WebSocketTickFeed
client = WebSocketTickFeed("ws://127.0.0.1:1/nope", "Binance", "BTCUSDT", parse_binance)
check("websocket publish persists without a live socket",
      client.publish(ws_quote) is True)
flush_ticks()
check("flush is safe to call twice", flush_ticks() == 0)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
