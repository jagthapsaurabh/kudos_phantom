"""Persist live ticks so they can be queried and resampled later.

The paper/live workers already hold the newest quote in memory for exits.
That number is gone the moment the next one arrives. This module writes
every usable quote into ``market_ticks`` (batched, never on the caller's
critical path) and exposes helpers the API uses to read them back as a
raw series or as OHLC candles.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.mark_price import MarkPriceQuote, normalize_source_name, perpetual_symbol
from app.database.models import MarketTick, SessionLocal, engine

FLUSH_SIZE = 100
# Two feeds on the same contract (collector + a paper session) can publish
# the same quote in the same millisecond — keep one.
_DEDUP_WINDOW = 0.001

COLLECTOR_STREAMS = (
    ("Binance", "BTCUSDT"),
    ("Delta", "BTCUSD"),
)

OHLC_SECONDS = {
    "1s": 1, "5s": 5, "15s": 15,
    "1m": 60, "5m": 300, "15m": 900,
    "1h": 3600, "4h": 14400,
}


def recording_enabled() -> bool:
    raw = os.getenv("PHANTOM_RECORD_TICKS")
    if raw is None:
        return True
    return str(raw).strip().lower() not in ("0", "false", "off", "no")


def collector_enabled() -> bool:
    """Always-on websocket collector. Off under tmp/test DBs unless forced."""
    raw = os.getenv("PHANTOM_COLLECT_TICKS")
    if raw is not None:
        return str(raw).strip().lower() not in ("0", "false", "off", "no", "")
    url = os.getenv("DATABASE_URL") or ""
    if "/tmp/" in url or ":memory:" in url:
        return False
    return True


def _f(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0:
        return None
    return number


def _parse_ts(value) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1e16:       # nanoseconds
        number /= 1e9
    elif number > 1e14:     # microseconds
        number /= 1e6
    elif number > 1e11:     # milliseconds
        number /= 1e3
    try:
        return datetime.utcfromtimestamp(number)
    except (OverflowError, OSError, ValueError):
        return None


def event_time_of(quote: MarkPriceQuote) -> datetime:
    """Venue event time when the frame carries one, else the local receive time."""
    raw = quote.raw if isinstance(getattr(quote, "raw", None), dict) else {}
    for key in ("E", "T", "timestamp", "time", "ts"):
        parsed = _parse_ts(raw.get(key)) if raw else None
        if parsed is not None:
            return parsed
    fetched = getattr(quote, "fetched_at", None)
    if isinstance(fetched, datetime):
        return fetched.replace(tzinfo=None) if fetched.tzinfo else fetched
    return datetime.utcnow()


def bid_ask_of(quote: MarkPriceQuote) -> Tuple[Optional[float], Optional[float]]:
    raw = quote.raw if isinstance(getattr(quote, "raw", None), dict) else {}
    bid = _f(raw.get("b") or raw.get("best_bid") or raw.get("bid") or raw.get("buy"))
    ask = _f(raw.get("a") or raw.get("best_ask") or raw.get("ask") or raw.get("sell"))
    quotes = raw.get("quotes")
    if isinstance(quotes, dict):
        bid = bid or _f(quotes.get("best_bid") or quotes.get("bid"))
        ask = ask or _f(quotes.get("best_ask") or quotes.get("ask"))
    return bid, ask


def quote_to_row(quote: MarkPriceQuote, feed_kind: str = "websocket") -> Optional[Dict[str, Any]]:
    if quote is None or getattr(quote, "basis_price", None) is None:
        return None
    source = normalize_source_name(getattr(quote, "source", None) or "Binance")
    symbol = str(getattr(quote, "symbol", None) or perpetual_symbol(source, "BTCUSDT"))
    event_time = event_time_of(quote)
    received = getattr(quote, "fetched_at", None)
    if isinstance(received, datetime):
        received = received.replace(tzinfo=None) if received.tzinfo else received
    else:
        received = datetime.utcnow()
    bid, ask = bid_ask_of(quote)
    return {
        "source": source,
        "symbol": symbol,
        "event_time": event_time,
        "received_at": received,
        "mark_price": _f(getattr(quote, "mark_price", None)),
        "last_price": _f(getattr(quote, "last_price", None)),
        "index_price": _f(getattr(quote, "index_price", None)),
        "bid": bid,
        "ask": ask,
        "feed_kind": str(feed_kind or "websocket"),
    }


class TickRecorder:
    """In-memory buffer that bulk-inserts into ``market_ticks``."""

    def __init__(self, flush_size: int = FLUSH_SIZE):
        self.flush_size = int(flush_size)
        self._buf: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._last_key: Optional[Tuple] = None
        self.accepted = 0
        self.written = 0
        self.dropped = 0
        self.last_error: Optional[str] = None
        self._table_ready = False

    def record(self, quote: MarkPriceQuote, feed_kind: str = "websocket") -> bool:
        if not recording_enabled():
            return False
        row = quote_to_row(quote, feed_kind)
        if row is None:
            return False
        key = (row["source"], row["symbol"], row["event_time"],
               row["mark_price"], row["last_price"])
        with self._lock:
            if key == self._last_key:
                self.dropped += 1
                return False
            self._last_key = key
            self._buf.append(row)
            self.accepted += 1
            should_flush = len(self._buf) >= self.flush_size
        if should_flush:
            self.flush()
        return True

    def _ensure_table(self):
        if self._table_ready:
            return
        MarketTick.__table__.create(bind=engine, checkfirst=True)
        self._table_ready = True

    def flush(self) -> int:
        with self._lock:
            batch, self._buf = self._buf, []
        if not batch:
            return 0
        try:
            self._ensure_table()
            db = SessionLocal()
            try:
                db.bulk_insert_mappings(MarketTick, batch)
                db.commit()
            finally:
                db.close()
            self.written += len(batch)
            self.last_error = None
            return len(batch)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            # Put the rows back so a later flush can retry, but cap the
            # buffer so a broken DB cannot grow without bound.
            with self._lock:
                combined = batch + self._buf
                self._buf = combined[-self.flush_size * 4:]
            return 0

    def pending(self) -> int:
        with self._lock:
            return len(self._buf)

    def stats(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "written": self.written,
            "dropped": self.dropped,
            "pending": self.pending(),
            "last_error": self.last_error,
            "enabled": recording_enabled(),
        }


RECORDER = TickRecorder()


def record_tick(quote: MarkPriceQuote, feed_kind: str = "websocket") -> bool:
    try:
        return RECORDER.record(quote, feed_kind)
    except Exception:
        return False


def flush_ticks() -> int:
    try:
        return RECORDER.flush()
    except Exception:
        return 0


def query_ticks(source: str = "Binance", symbol: str = "BTCUSDT",
                start: Optional[datetime] = None, end: Optional[datetime] = None,
                limit: int = 5000, db=None) -> List[MarketTick]:
    own = db is None
    if own:
        db = SessionLocal()
    try:
        source = normalize_source_name(source)
        cap = max(1, min(int(limit or 5000), 60000))
        q = db.query(MarketTick).filter(
            MarketTick.source == source, MarketTick.symbol == symbol)
        if start is not None:
            q = q.filter(MarketTick.event_time >= start)
        if end is not None:
            q = q.filter(MarketTick.event_time < end)
        windowed = start is not None or end is not None
        if windowed:
            rows = q.order_by(MarketTick.event_time.asc()).limit(cap).all()
        else:
            rows = q.order_by(MarketTick.event_time.desc()).limit(cap).all()
            rows.reverse()
        return rows
    finally:
        if own:
            db.close()


def latest_tick(source: str = "Binance", symbol: str = "BTCUSDT", db=None) -> Optional[MarketTick]:
    own = db is None
    if own:
        db = SessionLocal()
    try:
        source = normalize_source_name(source)
        return (db.query(MarketTick)
                .filter(MarketTick.source == source, MarketTick.symbol == symbol)
                .order_by(MarketTick.event_time.desc())
                .first())
    finally:
        if own:
            db.close()


def series_stats(db=None) -> List[Dict[str, Any]]:
    from sqlalchemy import func
    own = db is None
    if own:
        db = SessionLocal()
    try:
        rows = db.query(
            MarketTick.source, MarketTick.symbol,
            func.count(MarketTick.id),
            func.min(MarketTick.event_time),
            func.max(MarketTick.event_time),
        ).group_by(MarketTick.source, MarketTick.symbol).all()
        return [{
            "source": source, "symbol": symbol, "count": int(count or 0),
            "first": first, "last": last,
        } for source, symbol, count, first, last in rows]
    except Exception:
        return []
    finally:
        if own:
            db.close()


def _tick_price(row) -> Optional[float]:
    return _f(getattr(row, "mark_price", None)) or _f(getattr(row, "last_price", None))


def ticks_to_ohlc(rows: Sequence, interval: str = "1m") -> List[Dict[str, Any]]:
    """Resample stored ticks into OHLC candles (volume = ticks in the bucket)."""
    seconds = OHLC_SECONDS.get(str(interval).lower())
    if not seconds:
        raise ValueError(f"unsupported tick OHLC interval '{interval}'")
    buckets: Dict[datetime, Dict[str, Any]] = {}
    order: List[datetime] = []
    for row in rows:
        price = _tick_price(row)
        event = getattr(row, "event_time", None)
        if price is None or not isinstance(event, datetime):
            continue
        naive = event.replace(tzinfo=None) if event.tzinfo else event
        epoch = int((naive.replace(tzinfo=timezone.utc)).timestamp())
        bucket_epoch = epoch - (epoch % seconds)
        bucket = datetime.utcfromtimestamp(bucket_epoch)
        bar = buckets.get(bucket)
        if bar is None:
            bar = {"time": bucket, "open": price, "high": price, "low": price,
                   "close": price, "volume": 0}
            buckets[bucket] = bar
            order.append(bucket)
        else:
            if price > bar["high"]:
                bar["high"] = price
            if price < bar["low"]:
                bar["low"] = price
            bar["close"] = price
        bar["volume"] += 1
    return [buckets[key] for key in order]


_collector_feeds: List[Any] = []
_collector_running = False


def collector_stats() -> Dict[str, Any]:
    feeds = []
    for feed in list(_collector_feeds):
        try:
            feeds.append({
                "source": getattr(feed, "source", None),
                "symbol": getattr(feed, "symbol", None),
                **(feed.stats() if hasattr(feed, "stats") else {}),
            })
        except Exception:
            pass
    return {
        "running": _collector_running,
        "enabled": collector_enabled(),
        "streams": list(COLLECTOR_STREAMS),
        "feeds": feeds,
        "recorder": RECORDER.stats(),
    }


async def run_collector():
    """Subscribe to the BTC perpetual on each venue and persist every tick.

    Runs for the life of the API process so ticks accumulate even when no
    paper/live session is open. Failures never raise into FastAPI.
    """
    global _collector_running
    if not collector_enabled():
        return
    from app.services.tick_feed import build_tick_feed
    import asyncio
    _collector_running = True
    try:
        for source, symbol in COLLECTOR_STREAMS:
            try:
                feed = build_tick_feed(
                    "websocket", source, symbol,
                    perpetual=perpetual_symbol(source, symbol))
                await feed.start()
                _collector_feeds.append(feed)
            except Exception as exc:
                print(f"[ticks] collector failed to start {source} {symbol}: {exc}")
        print(f"[ticks] collector listening on {len(_collector_feeds)} stream(s)")
        while True:
            await asyncio.sleep(1.0)
            flush_ticks()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"[ticks] collector stopped: {exc}")
    finally:
        _collector_running = False
        for feed in list(_collector_feeds):
            try:
                await feed.stop()
            except Exception:
                pass
        _collector_feeds.clear()
        flush_ticks()
