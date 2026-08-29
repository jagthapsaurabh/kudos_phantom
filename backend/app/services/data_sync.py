"""Market data adapters and database seeding helpers."""
import os
import threading
import time
import calendar
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import pandas as pd
import requests
from sqlalchemy import func

from app.database.models import (
    BrokerDefinition, MarketDataSeedProgress, SessionLocal, Klines, init_db,
)


class MarketDataError(RuntimeError):
    pass


class TransientMarketDataError(MarketDataError):
    """A transport/exchange hiccup worth retrying (timeout, connection reset,
    HTTP 429/5xx) rather than aborting a long seed."""



class DataSyncService:
    TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]
    # The Delta public OHLC endpoint is capped at 2,000 candles per request.
    # A full BTC history is therefore practical for the coarser app timeframes
    # only; 1m/5m are intentionally excluded from the full-history preset and
    # from the daily Delta refresh.
    DELTA_HISTORY_INTERVALS = ["15m", "1h", "4h", "1d"]
    DELTA_EXCLUDED_INTERVALS = {"1m", "5m"}
    DAILY_INTERVALS = TIMEFRAMES
    SYMBOL = "BTCUSDT"
    SOURCE_ALIASES = {"binance": "Binance", "delta": "Delta", "delta exchange": "Delta"}
    DELTA_MAX_CANDLES = 2000
    BINANCE_MAX_CANDLES = 1500
    SEED_PAGE_SLEEP_SECONDS = float(os.getenv("SEED_PAGE_SLEEP_SECONDS", "0.10"))
    # A 2020 → today walk is thousands of requests, and any single one can
    # time out or get rate-limited mid-flight. Two layers keep the range from
    # "breaking": every HTTP request retries transient failures with growing
    # backoff, and the window loop retries a whole window (same cursor) before
    # giving up. Giving up never loses progress — the durable cursor means a
    # re-run continues where the range stopped.
    REQUEST_RETRIES = int(os.getenv("SEED_REQUEST_RETRIES", "4"))
    REQUEST_BACKOFF_SECONDS = float(os.getenv("SEED_REQUEST_BACKOFF_SECONDS", "1.0"))
    WINDOW_RETRIES = int(os.getenv("SEED_WINDOW_RETRIES", "3"))
    WINDOW_BACKOFF_SECONDS = float(os.getenv("SEED_WINDOW_BACKOFF_SECONDS", "5.0"))
    TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}
    RETRY_SLEEP_CAP_SECONDS = 60.0
    # Full-history seeds default to 1 Jan 2020 for every adapter. Binance's
    # BTCUSDT perpetual lists in 2019, so a 2020 start always has candles;
    # Delta's pre-listing windows simply come back empty. An explicit
    # start_date always wins.
    FULL_HISTORY_START = datetime(2020, 1, 1)
    # Data-health scans walk stored timestamps in Python (exact candle-grid
    # math that works identically on SQLite and PostgreSQL). The walk is
    # capped per series so a giant 1m history cannot pin the admin request.
    HEALTH_SCAN_ROW_LIMIT = 300_000
    # Delta serves the same public history API from more than one host. If the
    # primary API host is down/geo-blocked the CDN host (the one in Delta's own
    # API guide) still answers, so we fall back through the list in order.
    DELTA_HOSTS = [
        "https://api.india.delta.exchange",
        "https://cdn.india.deltaex.org",
    ]
    # A manual admin refresh and the startup/24-hour worker must not overlap
    # and double the exchange request rate.
    DAILY_SYNC_LOCK = threading.Lock()

    @classmethod
    def normalize_source(cls, source: str | None) -> str:
        value = (source or "Binance").strip()
        return cls.SOURCE_ALIASES.get(value.lower(), value)

    @staticmethod
    def _as_datetime(value):
        if isinstance(value, datetime):
            return value.replace(tzinfo=None) if value.tzinfo else value
        return pd.to_datetime(value, utc=True).to_pydatetime().replace(tzinfo=None)

    @classmethod
    def _binance_symbol(cls, symbol):
        return symbol.replace("-", "").replace("/", "").upper()

    @classmethod
    def _delta_symbol(cls, symbol):
        s = symbol.replace("/", "").replace("-", "").upper()
        return s[:-4] + "USD" if s.endswith("USDT") else s

    @classmethod
    def _delta_resolution(cls, interval):
        # Delta Exchange expects the *string label* of the timeframe, not a
        # seconds value (e.g. "15m", "1h", "4h", "1d"). Sending "15"/"60"/"240"
        # returns HTTP 400 Bad Request because the resolution enum is rejected.
        # The app's own interval names (1m,5m,15m,1h,4h,1d) are already the
        # labels Delta uses, so we pass them through.
        label = str(interval).lower()
        alias = {"60m": "1h", "240m": "4h", "2h": "2h", "6h": "6h"}
        if label in ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d", "1w"):
            return label
        return alias.get(label, label)

    @staticmethod
    def _interval_seconds(interval):
        return {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
                "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
                "1d": 86400, "1w": 604800}.get(interval, 3600)

    @classmethod
    def _adapter_kind(cls, source, definition=None):
        """Return the market-data adapter kind for a configured source."""
        configured = getattr(definition, "kind", None) if definition is not None else None
        if configured:
            return str(configured).lower().strip()
        normalized = cls.normalize_source(source)
        if normalized == "Binance":
            return "binance"
        if normalized == "Delta":
            return "delta"
        return None

    @staticmethod
    def _base_url(definition, fallback):
        value = getattr(definition, "market_data_url", None) if definition is not None else None
        return (value or fallback).rstrip("/")

    @classmethod
    def _page_limit(cls, source, requested, definition=None):
        kind = cls._adapter_kind(source, definition)
        maximum = cls.BINANCE_MAX_CANDLES if kind == "binance" else cls.DELTA_MAX_CANDLES
        return min(max(1, int(requested)), maximum)

    @staticmethod
    def _parse_candle_rows(raw):
        """Normalize raw Delta candles into OHLCV dicts.

        Delta has served this endpoint in both shapes (and per market):
          - a bare JSON array of dicts:  [{"time": 1690000000, "open": ..., "high": ..., "low": ..., "close": ..., "volume": ...}]
          - a bare JSON array of arrays: [[time, open, high, low, close, volume], ...]
        Malformed rows are skipped instead of aborting the whole page.
        """
        rows = []
        for k in raw or []:
            try:
                if isinstance(k, dict):
                    ts = k.get("time", k.get("timestamp"))
                    if ts is None:
                        continue
                    rows.append({
                        "event_time": pd.to_datetime(float(ts), unit="s").to_pydatetime(),
                        "open": float(k["open"]), "high": float(k["high"]),
                        "low": float(k["low"]), "close": float(k["close"]),
                        "volume": float(k.get("volume", k.get("vol", 0)) or 0),
                    })
                elif isinstance(k, (list, tuple)) and len(k) >= 5:
                    rows.append({
                        "event_time": pd.to_datetime(float(k[0]), unit="s").to_pydatetime(),
                        "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                        "close": float(k[4]), "volume": float(k[5] if len(k) > 5 else 0),
                    })
            except (TypeError, ValueError, KeyError):
                continue
        return rows

    @staticmethod
    def _extract_delta_array(payload):
        """Find the candle array in a Delta response payload.

        Handles every wrapper shape the API has used:
          - top-level list
          - {"result": [...]}
          - {"candles": [...], "result": null, ...}
        Returns (array_or_None, error_text_or_None).
        """
        if isinstance(payload, list):
            return payload, None
        if isinstance(payload, dict):
            error = payload.get("error")
            if error:
                return None, f"exchange error: {error}"
            for key in ("result", "candles"):
                value = payload.get(key)
                if isinstance(value, list):
                    return value, None
            return None, f"no candle array in response: {str(payload)[:200]}"
        return None, f"unexpected payload type: {str(payload)[:200]}"

    @classmethod
    def _get_with_retry(cls, url, params, timeout=20, headers=None):
        """GET that retries transient failures instead of failing a long seed.

        Retries connection errors, timeouts and HTTP 429/5xx with growing
        backoff (honouring Retry-After when the exchange sends it). Once the
        attempts are exhausted the caller gets a TransientMarketDataError so
        the window loop can retry the whole window, and a permanent failure
        stays a plain MarketDataError.
        """
        attempt = 0
        while True:
            try:
                response = requests.get(url, params=params, headers=headers, timeout=timeout)
            except requests.RequestException as exc:
                if attempt < cls.REQUEST_RETRIES:
                    time.sleep(min(cls.REQUEST_BACKOFF_SECONDS * (2 ** attempt), cls.RETRY_SLEEP_CAP_SECONDS))
                    attempt += 1
                    continue
                raise TransientMarketDataError(
                    f"request failed after {attempt + 1} attempts: {exc.__class__.__name__}: {exc}") from exc
            if response.status_code in cls.TRANSIENT_HTTP_STATUSES and attempt < cls.REQUEST_RETRIES:
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = max(float(retry_after), 0.5)
                except (TypeError, ValueError):
                    delay = cls.REQUEST_BACKOFF_SECONDS * (2 ** attempt)
                time.sleep(min(delay, cls.RETRY_SLEEP_CAP_SECONDS))
                attempt += 1
                continue
            return response

    @classmethod
    def _delta_fetch_one(cls, host, params):
        """Try one Delta host. Returns (rows, status_code, note).

        Delta assigns the OHLC endpoint a weight of three and can return at
        most 2,000 candles. A short retry on HTTP 429 honours the exchange's
        reset header; all other diagnostics are returned to the caller so a
        seed never reports a misleading silent zero.
        """
        base = host.rstrip("/")
        url = f"{base}/history/candles" if base.endswith("/v2") else f"{base}/v2/history/candles"
        headers = {"Accept": "application/json", "User-Agent": "PHANTOM-Trading-Tool/1.0"}
        try:
            # Retries (timeouts, resets, 429/5xx with Retry-After) live in the
            # shared _get_with_retry helper, so a long Delta history walk
            # survives exchange hiccups instead of aborting the range. A
            # transport failure is returned as a note so the remaining hosts
            # are still tried.
            try:
                response = cls._get_with_retry(url, params, timeout=20, headers=headers)
            except TransientMarketDataError as exc:
                return [], None, str(exc)
            if response.status_code != 200:
                body = (response.text or "").strip().replace("\n", " ")[:300]
                return [], response.status_code, f"HTTP {response.status_code} {body}".strip()
            try:
                payload = response.json()
            except ValueError:
                body = (response.text or "").strip().replace("\n", " ")[:200]
                return [], response.status_code, f"non-JSON body: {body}"
            array, error = cls._extract_delta_array(payload)
            if error:
                return [], response.status_code, error
            return cls._parse_candle_rows(array), response.status_code, ""
        except requests.RequestException as exc:
            return [], None, f"request failed: {exc.__class__.__name__}: {exc}"

    @classmethod
    def _delta_fetch(cls, symbol, interval, start_time=None, end_time=None, limit=1000, hosts=None):
        """Fetch Delta candles, falling back through known hosts.

        Raises MarketDataError listing every host attempt so an admin can see
        exactly what the exchange answered (this is what made the old
        "0 candles" result impossible to diagnose).
        """
        # Delta requires BOTH `start` and `end` on every request (they are
        # mandatory query params). When the caller does not supply them we
        # derive a sane window (now .. now - limit*interval) instead of
        # omitting them, which would trigger a 400 Bad Request.
        now = int(datetime.now(timezone.utc).timestamp())
        page_limit = min(max(1, int(limit)), cls.DELTA_MAX_CANDLES)
        params = {
            "symbol": cls._delta_symbol(symbol),
            "resolution": cls._delta_resolution(interval),
            "limit": page_limit,
            "start": int(cls._as_datetime(start_time).replace(tzinfo=timezone.utc).timestamp()) if start_time else
                now - cls._interval_seconds(interval) * page_limit,
            "end": int(cls._as_datetime(end_time).replace(tzinfo=timezone.utc).timestamp()) if end_time else now,
        }
        attempts = []
        statuses = []
        for host in (hosts or cls.DELTA_HOSTS):
            rows, status, note = cls._delta_fetch_one(host, params)
            if rows:
                return rows
            statuses.append(status)
            attempts.append(f"{host} → HTTP {status if status is not None else 'n/a'}"
                            + (f" ({note})" if note else " (empty result)"))
        detail = (f"Delta Exchange returned 0 candles for {params['symbol']} {params['resolution']}: "
                  + " | ".join(attempts))
        # Every host failing with a transport error or a rate-limit/server
        # status is a transient condition — let the window loop retry it.
        if statuses and all(status is None or status in cls.TRANSIENT_HTTP_STATUSES for status in statuses):
            raise TransientMarketDataError(detail)
        raise MarketDataError(detail)

    @classmethod
    def fetch_klines(cls, source="Binance", symbol="BTCUSDT", interval="1h",
                     start_time: Optional[datetime] = None, end_time: Optional[datetime] = None,
                     limit: int = 1000, definition=None):
        source = cls.normalize_source(source)
        kind = cls._adapter_kind(source, definition)
        if kind == "binance":
            base = cls._base_url(definition, "https://fapi.binance.com")
            url = f"{base}/fapi/v1/klines"
            params = {"symbol": cls._binance_symbol(symbol), "interval": interval,
                      "limit": min(max(1, int(limit)), cls.BINANCE_MAX_CANDLES)}
            if start_time:
                params["startTime"] = int(cls._as_datetime(start_time).replace(tzinfo=timezone.utc).timestamp() * 1000)
            if end_time:
                params["endTime"] = int(cls._as_datetime(end_time).replace(tzinfo=timezone.utc).timestamp() * 1000)
            try:
                response = cls._get_with_retry(url, params, timeout=20)
                if response.status_code != 200:
                    body = (response.text or "").strip().replace("\n", " ")[:300]
                    message = f"Binance-compatible data request failed: HTTP {response.status_code} {body}".strip()
                    if response.status_code in cls.TRANSIENT_HTTP_STATUSES:
                        raise TransientMarketDataError(message)
                    raise MarketDataError(message)
                raw = response.json()
                if not isinstance(raw, list):
                    raise MarketDataError(f"Binance-compatible data request failed: {str(raw)[:300]}")
                return [{"event_time": pd.to_datetime(k[0], unit="ms").to_pydatetime(),
                         "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                         "close": float(k[4]), "volume": float(k[5])} for k in raw]
            except MarketDataError:
                raise
            except Exception as exc:
                raise MarketDataError(f"Binance-compatible data request failed: {exc}") from exc

        if kind == "delta":
            # Built-in Delta keeps the primary + CDN fallback. A custom
            # Delta-compatible definition is restricted to its configured host.
            custom_hosts = None
            if definition is not None and not getattr(definition, "is_builtin", False):
                configured_url = getattr(definition, "market_data_url", None)
                if configured_url:
                    custom_hosts = [configured_url.rstrip("/")]
            return cls._delta_fetch(symbol, interval, start_time, end_time, limit, hosts=custom_hosts)

        raise MarketDataError(f"No market-data adapter is installed for source '{source}'. Configure a Binance-compatible or Delta-compatible adapter first.")

    # ------------------------------------------------------------------
    # Mark price history (BTC perpetual)
    # ------------------------------------------------------------------
    # Both venues publish a mark-price series for the perpetual that is
    # separate from the traded OHLCV:
    #   Binance → /fapi/v1/markPriceKlines?symbol=BTCUSDT
    #   Delta   → /v2/history/candles?symbol=MARK:BTCUSD
    # The rows are stored on the matching klines row (mark_open/high/low/close)
    # so a backtest always has the traded price and the mark price aligned.
    @classmethod
    def fetch_mark_klines(cls, source="Binance", symbol="BTCUSDT", interval="1h",
                          start_time=None, end_time=None, limit=1000, definition=None):
        from app.core.mark_price import mark_symbol, perpetual_symbol

        source = cls.normalize_source(source)
        kind = cls._adapter_kind(source, definition)
        perp = perpetual_symbol(source, symbol)
        if kind == "binance":
            base = cls._base_url(definition, "https://fapi.binance.com")
            params = {"symbol": perp, "interval": interval,
                      "limit": min(max(1, int(limit)), cls.BINANCE_MAX_CANDLES)}
            if start_time:
                params["startTime"] = int(cls._as_datetime(start_time).replace(tzinfo=timezone.utc).timestamp() * 1000)
            if end_time:
                params["endTime"] = int(cls._as_datetime(end_time).replace(tzinfo=timezone.utc).timestamp() * 1000)
            response = cls._get_with_retry(f"{base}/fapi/v1/markPriceKlines", params, timeout=20)
            if response.status_code != 200:
                body = (response.text or "").strip().replace("\n", " ")[:300]
                message = f"Binance mark-price request failed: HTTP {response.status_code} {body}".strip()
                if response.status_code in cls.TRANSIENT_HTTP_STATUSES:
                    raise TransientMarketDataError(message)
                raise MarketDataError(message)
            return [{"event_time": pd.to_datetime(k[0], unit="ms").to_pydatetime(),
                     "open": float(k[1]), "high": float(k[2]),
                     "low": float(k[3]), "close": float(k[4])} for k in response.json()]
        if kind == "delta":
            custom_hosts = None
            if definition is not None and not getattr(definition, "is_builtin", False):
                configured_url = getattr(definition, "market_data_url", None)
                if configured_url:
                    custom_hosts = [configured_url.rstrip("/")]
            return cls._delta_fetch(mark_symbol(source, perp), interval, start_time,
                                    end_time, limit, hosts=custom_hosts)
        raise MarketDataError(
            f"No mark-price adapter is installed for source '{source}'. "
            f"Only Binance-compatible and Delta-compatible sources expose a mark price."
        )

    @classmethod
    def sync_mark_prices(cls, source="Binance", symbol="BTCUSDT", intervals=None,
                         start_time=None, end_time=None, limit=1000, definition=None):
        """Fetch mark-price candles and write them onto the seeded klines.

        Rows whose candle has not been seeded yet are skipped (the traded
        OHLCV seed always runs first), so this is safe to call after any seed
        or daily refresh. Returns a per-interval summary.
        """
        from app.core.mark_price import upsert_mark_rows

        source = cls.normalize_source(source)
        kind = cls._adapter_kind(source, definition)
        intervals = list(intervals or (cls.DELTA_HISTORY_INTERVALS if kind == "delta" else cls.TIMEFRAMES))
        init_db()
        summary = []
        db = SessionLocal()
        try:
            for interval in intervals:
                entry = {"source": source, "symbol": symbol, "interval": interval}
                try:
                    interval_seconds = cls._interval_seconds(interval)
                    page_limit = cls._page_limit(source, limit, definition)
                    if start_time:
                        # Full-history mark backfill: page the whole range like
                        # the traded-OHLCV seed instead of fetching a single
                        # page (one page covers only the first `limit` candles
                        # of a 2020 → today range). Mark rows whose candle is
                        # not seeded yet are skipped, so this runs after the
                        # OHLCV seed of the same range.
                        cursor = cls._as_datetime(start_time)
                        range_end = cls._as_datetime(end_time) if end_time else datetime.utcnow()
                        # A date-only end string (YYYY-MM-DD) is inclusive
                        # through that day — the same rule as the traded-OHLCV
                        # seed — so the mark series covers the final day too.
                        if isinstance(end_time, str) and len(end_time) == 10:
                            range_end += timedelta(days=1) - timedelta(microseconds=1)
                        window_span = timedelta(seconds=interval_seconds * max(0, page_limit - 1))
                        inserted = updated = fetched = 0
                        first = last = None
                        window_attempts = 0
                        while cursor < range_end:
                            window_end = min(range_end, cursor + window_span)
                            try:
                                rows = cls.fetch_mark_klines(source, symbol, interval, cursor,
                                                             window_end, page_limit, definition=definition)
                                window_attempts = 0
                            except TransientMarketDataError:
                                window_attempts += 1
                                if window_attempts <= cls.WINDOW_RETRIES:
                                    time.sleep(min(cls.WINDOW_BACKOFF_SECONDS * (2 ** (window_attempts - 1)),
                                                   cls.RETRY_SLEEP_CAP_SECONDS))
                                    continue
                                raise
                            if rows:
                                result = upsert_mark_rows(db, rows, source, symbol, interval)
                                db.commit()
                                inserted += result["inserted"]
                                updated += result["updated"]
                                fetched += len(rows)
                                first = first or rows[0]["event_time"]
                                last = rows[-1]["event_time"]
                            cursor = cls._next_grid_time(window_end, interval_seconds)
                            if cls.SEED_PAGE_SLEEP_SECONDS > 0:
                                time.sleep(cls.SEED_PAGE_SLEEP_SECONDS)
                        entry.update(inserted=inserted, updated=updated,
                                     total=inserted + updated, fetched=fetched,
                                     first=first, last=last)
                    else:
                        rows = cls.fetch_mark_klines(source, symbol, interval, start_time,
                                                     end_time, limit, definition=definition)
                        result = upsert_mark_rows(db, rows, source, symbol, interval) if rows else {
                            "inserted": 0, "updated": 0, "total": 0}
                        db.commit()
                        entry.update(result, fetched=len(rows))
                except MarketDataError as exc:
                    db.rollback()
                    entry.update(inserted=0, updated=0, total=0, fetched=0, error=str(exc))
                summary.append(entry)
        finally:
            db.close()
        return summary

    @classmethod
    def test_source(cls, source="Binance", symbol="BTCUSDT", interval="1h", limit=3, definition=None):
        """Small round-trip used by the admin UI's 'Test connection' button.

        Returns a diagnostic dict describing exactly what the exchange
        answered, so an empty seed can be diagnosed in one click.
        """
        source = cls.normalize_source(source)
        try:
            rows = cls.fetch_klines(source, symbol, interval, limit=limit, definition=definition)
            first = rows[0]["event_time"] if rows else None
            last = rows[-1]["event_time"] if rows else None
            return {"ok": bool(rows), "source": source, "symbol": symbol, "interval": interval,
                    "rows": len(rows), "first": first, "last": last,
                    "sample": rows[:3] if rows else [],
                    "detail": f"{source} reachable — {len(rows)} candles returned" if rows
                              else f"{source} answered but returned 0 candles for {symbol} {interval}"}
        except MarketDataError as exc:
            return {"ok": False, "source": source, "symbol": symbol, "interval": interval,
                    "rows": 0, "first": None, "last": None, "sample": [], "detail": str(exc)}

    @classmethod
    def _upsert_rows_in_session(cls, db, rows: Iterable[dict], source, symbol, interval,
                                clear_existing=False):
        """Upsert rows without committing; callers can include other state."""
        if clear_existing:
            db.query(Klines).filter_by(source=source, symbol=symbol, interval=interval).delete()
        prepared = {}
        for row in rows:
            event_time = cls._as_datetime(row["event_time"])
            prepared[event_time] = {
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": float(row.get("volume", 0) or 0),
            }
        # Historical windows are committed one at a time. Restrict the lookup
        # to this page's timestamps instead of loading an entire multi-year
        # table into ORM objects for every window.
        existing = {}
        if prepared:
            existing = {
                k.event_time: k for k in db.query(Klines).filter(
                    Klines.source == source, Klines.symbol == symbol,
                    Klines.interval == interval, Klines.event_time.in_(prepared),
                ).all()
            }
        inserted = updated = 0
        for event_time, values in prepared.items():
            item = existing.get(event_time)
            if item:
                for key, value in values.items():
                    setattr(item, key, value)
                updated += 1
            else:
                db.add(Klines(source=source, symbol=symbol, interval=interval,
                               event_time=event_time, **values))
                inserted += 1
        return {"inserted": inserted, "updated": updated, "total": inserted + updated}

    @classmethod
    def upsert_rows(cls, rows: Iterable[dict], source="Binance", symbol="BTCUSDT", interval="1h", clear_existing=False):
        source = cls.normalize_source(source)
        init_db()
        db = SessionLocal()
        try:
            result = cls._upsert_rows_in_session(
                db, rows, source, symbol, interval, clear_existing=clear_existing,
            )
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @classmethod
    def _aligned_to_grid(cls, event_time, interval_seconds):
        """True when a candle timestamp sits exactly on the interval grid.

        Binance opens 1h candles at whole hours, Delta at whole hours too; a
        stored timestamp like 2020-06-26 11:41:59.523330 for a 1h series is
        corrupt — it can never line up with the exchange grid and can never
        receive a mark price.
        """
        return not (event_time.microsecond or event_time.second
                    or calendar.timegm(event_time.timetuple()) % interval_seconds)

    @staticmethod
    def _next_grid_time(value, interval_seconds):
        """First candle-grid instant strictly after `value`.

        A window can end mid-candle (a date-string end becomes 23:59:59.999999),
        so advancing by a whole interval would skip that candle's timestamp
        when the range is later extended — the resumed cursor must land exactly
        on the next boundary.
        """
        epoch = calendar.timegm(value.timetuple())
        return datetime(1970, 1, 1) + timedelta(
            seconds=epoch - (epoch % interval_seconds) + interval_seconds)

    @classmethod
    def repair_klines(cls, source="Binance", symbol="BTCUSDT", intervals=None, definition=None):
        """Remove corrupted candles from a stored series.

        Two defects have shipped from historical seeding paths and both corrupt
        charts, backtests and mark pricing:
          - duplicate candles — the legacy seeder inserted whole batches with
            no upsert, so every re-run stored each candle again;
          - off-grid candles — the bundled CSV history carries timestamps that
            are not on the interval grid (e.g. 11:41:59.523330 for a 1h
            series), which never match exchange data or the mark-price series.

        Per interval this keeps the newest row of each duplicate timestamp
        (the row the upsert path would update) and deletes every row whose
        timestamp is not a whole multiple of the interval. Well-formed rows
        are never touched. Re-seed afterwards to refill what was removed.
        """
        source = cls.normalize_source(source)
        kind = cls._adapter_kind(source, definition)
        if intervals is None:
            intervals = cls.DELTA_HISTORY_INTERVALS if kind == "delta" else cls.TIMEFRAMES
        intervals = [str(interval).lower() for interval in intervals]
        unknown = sorted(set(intervals) - set(cls.TIMEFRAMES))
        if unknown:
            raise MarketDataError(f"Unsupported repair interval(s): {', '.join(unknown)}")

        init_db()
        summary = []
        db = SessionLocal()
        try:
            for interval in intervals:
                interval_seconds = cls._interval_seconds(interval)
                # id-ascending so the last id per timestamp is the newest write
                rows = db.query(Klines.id, Klines.event_time).filter(
                    Klines.source == source, Klines.symbol == symbol,
                    Klines.interval == interval,
                ).order_by(Klines.event_time.asc(), Klines.id.asc()).all()

                aligned_ids_by_time = {}
                misaligned_ids = set()
                for row_id, event_time in rows:
                    if cls._aligned_to_grid(event_time, interval_seconds):
                        aligned_ids_by_time.setdefault(event_time, []).append(row_id)
                    else:
                        misaligned_ids.add(row_id)
                duplicate_ids = set()
                for ids in aligned_ids_by_time.values():
                    duplicate_ids.update(ids[:-1])  # keep the newest (max id)

                remove_ids = sorted(misaligned_ids | duplicate_ids)
                removed = 0
                for chunk_start in range(0, len(remove_ids), 500):
                    chunk = remove_ids[chunk_start:chunk_start + 500]
                    removed += db.query(Klines).filter(
                        Klines.id.in_(chunk)).delete(synchronize_session=False)
                db.commit()
                summary.append({
                    "source": source, "symbol": symbol, "interval": interval,
                    "total": len(rows), "removed": len(remove_ids),
                    "duplicates_removed": len(duplicate_ids),
                    "misaligned_removed": len(misaligned_ids),
                    "kept": len(rows) - len(remove_ids),
                })
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return summary

    @classmethod
    def data_health(cls, scan_limit=None):
        """Per-series corruption counters: duplicate and off-grid candles.

        ``duplicate_rows`` is a cheap SQL count/distinct and always exact.
        ``misaligned_rows`` uses the exact grid check in Python and is capped
        per series by ``scan_limit`` (a full 1m history can be millions of
        rows); ``scanned`` reports how many rows were checked and
        ``misaligned_rows`` is None when the scan was truncated before any
        corruption was found.
        """
        scan_limit = cls.HEALTH_SCAN_ROW_LIMIT if scan_limit is None else int(scan_limit)
        init_db()
        db = SessionLocal()
        try:
            report = {}
            for source, symbol, interval, total, distinct in db.query(
                Klines.source, Klines.symbol, Klines.interval,
                func.count(Klines.id), func.count(func.distinct(Klines.event_time)),
            ).group_by(Klines.source, Klines.symbol, Klines.interval).all():
                key = (source or "Binance", symbol, interval)
                report[key] = {
                    "source": key[0], "symbol": symbol, "interval": interval,
                    "count": int(total),
                    "duplicate_rows": int(total) - int(distinct),
                    "misaligned_rows": None, "scanned": 0,
                }
            for (source, symbol, interval), entry in report.items():
                interval_seconds = cls._interval_seconds(interval)
                rows = db.query(Klines.event_time).filter(
                    Klines.source == source, Klines.symbol == symbol,
                    Klines.interval == interval,
                ).order_by(Klines.event_time.desc()).limit(scan_limit + 1).all()
                truncated = len(rows) > scan_limit
                misaligned = 0
                for index, (event_time,) in enumerate(rows[:scan_limit]):
                    if not cls._aligned_to_grid(event_time, interval_seconds):
                        misaligned += 1
                entry["scanned"] = min(len(rows), scan_limit)
                entry["misaligned_rows"] = misaligned if (misaligned or not truncated) else None
            return [report[key] for key in sorted(report)]
        finally:
            db.close()

    @classmethod
    def _seed_definition_key(cls, source, definition=None):
        """Stable progress namespace for a built-in or configured definition."""
        code = getattr(definition, "code", None) if definition is not None else None
        return str(code or source)

    @classmethod
    def _prepare_seed_progress(cls, source, definition, symbol, interval,
                               requested_start, requested_end, page_limit,
                               interval_seconds):
        """Create or reopen the durable cursor for one full-history interval."""
        definition_key = cls._seed_definition_key(source, definition)
        db = SessionLocal()
        try:
            progress = db.query(MarketDataSeedProgress).filter_by(
                source=source, definition_key=definition_key, symbol=symbol,
                interval=interval, requested_start=requested_start,
                requested_end=requested_end,
            ).first()
            if progress is None:
                # If an interrupted/completed run was made with an older
                # "through today" end date, extend that same cursor instead
                # of starting the 2020 history over. The range key is updated
                # before any new window is fetched.
                progress = db.query(MarketDataSeedProgress).filter(
                    MarketDataSeedProgress.source == source,
                    MarketDataSeedProgress.definition_key == definition_key,
                    MarketDataSeedProgress.symbol == symbol,
                    MarketDataSeedProgress.interval == interval,
                    MarketDataSeedProgress.requested_start == requested_start,
                    MarketDataSeedProgress.requested_end < requested_end,
                ).order_by(MarketDataSeedProgress.requested_end.desc()).first()
                if progress is not None:
                    progress.requested_end = requested_end
                    progress.status = 'running'
                    progress.last_error = None
                    progress.completed_at = None
                    db.commit()
            if progress is None:
                progress = MarketDataSeedProgress(
                    source=source, definition_key=definition_key, symbol=symbol,
                    interval=interval, requested_start=requested_start,
                    requested_end=requested_end, next_start=requested_start,
                    page_limit=page_limit, interval_seconds=interval_seconds,
                    status='running', pages=0, empty_pages=0, fetched=0,
                    inserted=0, updated=0,
                )
                db.add(progress)
                db.commit()
            else:
                # A completed range is intentionally a no-op on repeat. A
                # failed/interrupted range resumes at its last committed
                # next_start, retaining the original safe window size.
                if progress.status != 'completed':
                    if progress.next_start >= requested_end:
                        progress.status = 'completed'
                        progress.completed_at = progress.completed_at or datetime.utcnow()
                    else:
                        progress.status = 'running'
                        progress.last_error = None
                    db.commit()
            return {
                'id': progress.id, 'definition_key': definition_key,
                'next_start': progress.next_start, 'page_limit': progress.page_limit,
                'interval_seconds': progress.interval_seconds, 'status': progress.status,
                'pages': progress.pages, 'empty_pages': progress.empty_pages,
                'inserted': progress.inserted, 'updated': progress.updated,
            }
        finally:
            db.close()

    @classmethod
    def _persist_seed_window(cls, source, definition_key, symbol, interval,
                             requested_start, requested_end, page_rows,
                             next_start, pages, empty_pages, is_complete):
        """Atomically commit a page of candles and its next cursor."""
        db = SessionLocal()
        try:
            progress = db.query(MarketDataSeedProgress).filter_by(
                source=source, definition_key=definition_key, symbol=symbol,
                interval=interval, requested_start=requested_start,
                requested_end=requested_end,
            ).first()
            if progress is None:
                raise MarketDataError('Seed progress row disappeared before window commit')
            result = cls._upsert_rows_in_session(
                db, page_rows, source, symbol, interval,
            )
            progress.next_start = next_start
            progress.pages = pages
            progress.empty_pages = empty_pages
            progress.inserted = (progress.inserted or 0) + result['inserted']
            progress.updated = (progress.updated or 0) + result['updated']
            progress.fetched = (progress.fetched or 0) + len(page_rows)
            progress.status = 'completed' if is_complete else 'running'
            progress.last_error = None
            progress.completed_at = datetime.utcnow() if is_complete else None
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @classmethod
    def _mark_seed_progress_failed(cls, source, definition_key, symbol, interval,
                                   requested_start, requested_end, detail):
        """Record a fetch/persistence failure without changing the cursor."""
        db = SessionLocal()
        try:
            progress = db.query(MarketDataSeedProgress).filter_by(
                source=source, definition_key=definition_key, symbol=symbol,
                interval=interval, requested_start=requested_start,
                requested_end=requested_end,
            ).first()
            if progress is not None:
                progress.status = 'failed'
                progress.last_error = str(detail)[:2000]
                db.commit()
        finally:
            db.close()

    @classmethod
    def _seed_progress_snapshot(cls, source, definition_key, symbol, interval,
                                requested_start, requested_end):
        """Return durable counters plus the actual unique candle range."""
        db = SessionLocal()
        try:
            progress = db.query(MarketDataSeedProgress).filter_by(
                source=source, definition_key=definition_key, symbol=symbol,
                interval=interval, requested_start=requested_start,
                requested_end=requested_end,
            ).first()
            query = db.query(Klines).filter(
                Klines.source == source, Klines.symbol == symbol,
                Klines.interval == interval, Klines.event_time >= requested_start,
                Klines.event_time <= requested_end,
            )
            first_row = query.with_entities(Klines.event_time).order_by(Klines.event_time.asc()).first()
            last_row = query.with_entities(Klines.event_time).order_by(Klines.event_time.desc()).first()
            return {
                'progress': progress,
                'fetched': query.count(),
                'first': first_row[0] if first_row else None,
                'last': last_row[0] if last_row else None,
            }
        finally:
            db.close()

    @classmethod
    def seed_market_data(cls, source="Binance", symbol="BTCUSDT", intervals=None,
                         start_date=None, end_date=None, limit=1000, fetch_all=False,
                         definition=None):
        """Fetch and upsert a date range, splitting it into API-sized windows.

        Delta does not expose a cursor for OHLC history; it only accepts a
        start/end window and returns at most 2,000 candles. Sending the entire
        2020-to-now range in one request therefore returns only a partial page.
        Full-history seeding advances fixed, non-overlapping windows instead of
        trying to advance from whichever end the exchange happened to return.
        """
        source = cls.normalize_source(source)
        kind = cls._adapter_kind(source, definition)
        if intervals is None:
            intervals = cls.DELTA_HISTORY_INTERVALS if kind == "delta" else cls.TIMEFRAMES
        intervals = [str(interval).lower() for interval in intervals]
        unknown = sorted(set(intervals) - set(cls.TIMEFRAMES))
        if unknown:
            raise MarketDataError(f"Unsupported seed interval(s): {', '.join(unknown)}")
        if kind == "delta":
            excluded = sorted(set(intervals) & cls.DELTA_EXCLUDED_INTERVALS)
            if excluded:
                raise MarketDataError(
                    f"Delta full-history seeding excludes {', '.join(excluded)}; "
                    f"use {', '.join(cls.DELTA_HISTORY_INTERVALS)}"
                )
        if not intervals:
            raise MarketDataError("Select at least one interval")

        end = cls._as_datetime(end_date) if end_date else datetime.utcnow()
        # Date-only admin inputs are inclusive through the selected day.
        if isinstance(end_date, str) and len(end_date) == 10:
            end = end + timedelta(days=1) - timedelta(microseconds=1)
        if start_date:
            requested_start = cls._as_datetime(start_date)
        elif fetch_all:
            # Full-history mode defaults to 1 Jan 2020 for every adapter
            # (Binance BTCUSDT futures list in 2019-09, so a 2020 start always
            # has candles; Delta's pre-listing windows come back empty and the
            # seed keeps advancing). An explicit start_date still wins.
            requested_start = cls.FULL_HISTORY_START
        else:
            requested_start = end - timedelta(
                seconds=cls._interval_seconds(intervals[0]) * max(1, int(limit))
            )
        if requested_start >= end:
            raise MarketDataError("Seed start date must be before the end date")

        init_db()
        summary = []
        for interval in intervals:
            interval_seconds = cls._interval_seconds(interval)
            page_limit = cls._page_limit(source, limit, definition)
            requested_range_start = requested_start if start_date or fetch_all else end - timedelta(
                seconds=interval_seconds * page_limit
            )
            error = None

            if not fetch_all:
                # Preserve the original one-page compatibility path for
                # callers/imports that do not request a full backfill.
                page = []
                try:
                    page = cls.fetch_klines(
                        source, symbol, interval, requested_range_start, end, page_limit,
                        definition=definition,
                    )
                except MarketDataError as exc:
                    error = str(exc)
                unique = {cls._as_datetime(row["event_time"]): row for row in (page or [])}
                normalized_rows = [unique[event_time] for event_time in sorted(unique)]
                entry = {
                    "source": source, "symbol": symbol, "interval": interval,
                    "fetched": len(normalized_rows), "fetch_all": False,
                    "pages": 1, "empty_pages": 1 if not page else 0,
                    "requested_start": requested_range_start,
                    "requested_end": end, "page_limit": page_limit,
                }
                if normalized_rows:
                    result = cls.upsert_rows(normalized_rows, source, symbol, interval)
                    entry.update(result, first=min(unique), last=max(unique))
                else:
                    entry.update(inserted=0, updated=0, total=0)
                if error:
                    entry["error"] = error
                elif not normalized_rows:
                    entry["error"] = (
                        f"exchange returned 0 candles for "
                        f"{requested_range_start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} UTC"
                    )
                summary.append(entry)
                continue

            # Full-history mode has a durable cursor. Each window commits its
            # candles and next_start together, so a process restart resumes at
            # the first uncommitted window and never needs to retain the whole
            # range in memory.
            interval_seconds = cls._interval_seconds(interval)
            progress_state = cls._prepare_seed_progress(
                source, definition, symbol, interval, requested_start, end,
                page_limit, interval_seconds,
            )
            definition_key = progress_state['definition_key']
            resumed = bool(progress_state['pages'] or progress_state['next_start'] != requested_start)
            if progress_state['status'] == 'completed':
                snapshot = cls._seed_progress_snapshot(
                    source, definition_key, symbol, interval, requested_start, end,
                )
                progress = snapshot['progress']
                summary.append({
                    "source": source, "symbol": symbol, "interval": interval,
                    "fetched": snapshot['fetched'], "fetch_all": True,
                    "pages": progress.pages if progress else progress_state['pages'],
                    "empty_pages": progress.empty_pages if progress else progress_state['empty_pages'],
                    "requested_start": requested_start, "requested_end": end,
                    "page_limit": progress.page_limit if progress else page_limit,
                    "inserted": progress.inserted if progress else 0,
                    "updated": progress.updated if progress else 0,
                    "total": snapshot['fetched'], "status": "completed",
                    "resumed": resumed, "skipped": True,
                    "first": snapshot['first'], "last": snapshot['last'],
                })
                continue

            page_limit = progress_state['page_limit']
            window_span = timedelta(seconds=interval_seconds * max(0, page_limit - 1))
            cursor = progress_state['next_start']
            pages = progress_state['pages']
            empty_pages = progress_state['empty_pages']
            max_pages = 10000
            window_attempts = 0
            while pages < max_pages and cursor < end:
                window_end = min(end, cursor + window_span)
                attempted_page = []
                try:
                    attempted_page = cls.fetch_klines(
                        source, symbol, interval, cursor, window_end, page_limit,
                        definition=definition,
                    )
                    window_attempts = 0
                except TransientMarketDataError as exc:
                    # Timeouts / resets / 429 / 5xx are retried at the same
                    # cursor so an hours-long walk shrugs off exchange hiccups
                    # instead of dying mid-range. The adapter has already
                    # retried the single request; this retries the window.
                    window_attempts += 1
                    if window_attempts <= cls.WINDOW_RETRIES:
                        time.sleep(min(cls.WINDOW_BACKOFF_SECONDS * (2 ** (window_attempts - 1)),
                                       cls.RETRY_SLEEP_CAP_SECONDS))
                        continue
                    error = (f"{exc} — {cls.WINDOW_RETRIES + 1} window attempts failed; progress is "
                             f"saved, re-run the same seed to resume from {cursor:%Y-%m-%d %H:%M} UTC")
                    cls._mark_seed_progress_failed(
                        source, definition_key, symbol, interval,
                        requested_start, end, error,
                    )
                    break
                except MarketDataError as exc:
                    detail = str(exc)
                    # A Delta product can have no candles before its listing
                    # date. That valid HTTP-200 empty window advances just like
                    # any other page; transport, HTTP and schema errors stop
                    # here and are recorded durably at the current cursor.
                    if kind == 'delta' and 'HTTP 200 (empty result)' in detail:
                        attempted_page = []
                    else:
                        error = f"{detail} — progress is saved; re-run the same seed to resume"
                        cls._mark_seed_progress_failed(
                            source, definition_key, symbol, interval,
                            requested_start, end, error,
                        )
                        break
                except Exception as exc:
                    error = f"{exc.__class__.__name__}: {exc}"
                    cls._mark_seed_progress_failed(
                        source, definition_key, symbol, interval,
                        requested_start, end, error,
                    )
                    break

                page_rows_by_time = {}
                for row in attempted_page or []:
                    event_time = cls._as_datetime(row.get("event_time"))
                    if cursor <= event_time <= window_end and requested_start <= event_time <= end:
                        page_rows_by_time[event_time] = row
                page_rows = [page_rows_by_time[event_time] for event_time in sorted(page_rows_by_time)]
                pages += 1
                if not page_rows:
                    empty_pages += 1
                # Delta treats the requested time range as inclusive. Advance
                # to the next candle boundary so committed windows do not
                # overlap or repeat historical API work, and a cursor saved at
                # a mid-candle window end (23:59:59.999999) still lands on the
                # boundary candle when the range is extended later. A final
                # window is complete once that cursor reaches the requested
                # end, even when the end date has a time-of-day remainder.
                next_cursor = cls._next_grid_time(window_end, interval_seconds)
                is_complete = next_cursor >= end
                try:
                    cls._persist_seed_window(
                        source, definition_key, symbol, interval,
                        requested_start, end, page_rows, next_cursor,
                        pages, empty_pages, is_complete,
                    )
                except Exception as exc:
                    error = f"window commit failed: {exc}"
                    cls._mark_seed_progress_failed(
                        source, definition_key, symbol, interval,
                        requested_start, end, error,
                    )
                    break
                cursor = next_cursor
                if is_complete:
                    break
                if cls.SEED_PAGE_SLEEP_SECONDS > 0:
                    time.sleep(cls.SEED_PAGE_SLEEP_SECONDS)

            if error is None and cursor < end:
                error = f"seed stopped after {max_pages} windows before reaching the requested end"
                cls._mark_seed_progress_failed(
                    source, definition_key, symbol, interval,
                    requested_start, end, error,
                )

            snapshot = cls._seed_progress_snapshot(
                source, definition_key, symbol, interval, requested_start, end,
            )
            progress = snapshot['progress']
            entry = {
                "source": source, "symbol": symbol, "interval": interval,
                "fetched": snapshot['fetched'], "fetch_all": True,
                "pages": progress.pages if progress else pages,
                "empty_pages": progress.empty_pages if progress else empty_pages,
                "requested_start": requested_start, "requested_end": end,
                "page_limit": progress.page_limit if progress else page_limit,
                "inserted": progress.inserted if progress else 0,
                "updated": progress.updated if progress else 0,
                "total": snapshot['fetched'], "status": progress.status if progress else 'failed',
                "resumed": resumed, "first": snapshot['first'], "last": snapshot['last'],
            }
            if error:
                entry["error"] = error
            elif progress and progress.status == 'failed' and progress.last_error:
                entry["error"] = progress.last_error
            elif snapshot['fetched'] == 0:
                entry["error"] = (
                    f"exchange returned 0 candles for "
                    f"{requested_start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} UTC"
                )
            summary.append(entry)
        return summary

    @classmethod
    def sync_market_data(cls, source="Binance", symbol=None, intervals=None,
                          definition=None, limit=1000):
        """Incrementally refresh one configured source for the daily job."""
        source = cls.normalize_source(source)
        symbol = symbol or cls.SYMBOL
        kind = cls._adapter_kind(source, definition)
        intervals = intervals or (cls.DELTA_HISTORY_INTERVALS if kind == "delta" else cls.TIMEFRAMES)
        init_db()
        db = SessionLocal()
        try:
            latest_by_interval = {
                interval: db.query(Klines.event_time).filter_by(
                    source=source, symbol=symbol, interval=interval
                ).order_by(Klines.event_time.desc()).first()
                for interval in intervals
            }
        finally:
            db.close()

        summary = []
        end = datetime.utcnow()
        for interval in intervals:
            interval_seconds = cls._interval_seconds(interval)
            latest = latest_by_interval.get(interval)
            last_time = latest[0] if latest else None
            start = cls._as_datetime(last_time) - timedelta(seconds=interval_seconds) if last_time else \
                end - timedelta(seconds=interval_seconds * cls._page_limit(source, limit, definition))
            try:
                rows = cls.fetch_klines(
                    source, symbol, interval, start, end, limit, definition=definition
                )
                result = cls.upsert_rows(rows, source, symbol, interval) if rows else {
                    "inserted": 0, "updated": 0, "total": 0
                }
                item = {"source": source, "symbol": symbol, "interval": interval,
                        "fetched": len(rows), "first": rows[0]["event_time"] if rows else None,
                        "last": rows[-1]["event_time"] if rows else None, **result}
                print(f"Synced {len(rows)} {source} {symbol} {interval} candles")
            except MarketDataError as exc:
                item = {"source": source, "symbol": symbol, "interval": interval,
                        "fetched": 0, "inserted": 0, "updated": 0, "total": 0,
                        "error": str(exc)}
                print(f"Sync skipped for {source}/{interval}: {exc}")
            summary.append(item)
        return summary

    @classmethod
    def sync_all_configured_sources_daily(cls, symbol=None, intervals=None):
        """Serialize manual and scheduled multi-source refreshes."""
        with cls.DAILY_SYNC_LOCK:
            return cls._sync_all_configured_sources_daily(symbol, intervals)

    @classmethod
    def _sync_all_configured_sources_daily(cls, symbol=None, intervals=None):
        """Refresh Binance and every enabled compatible broker definition.

        Added integrations opt into the existing ``binance`` or ``delta``
        adapter kinds. Generic definitions remain visible in the result as a
        clear skip, rather than being sent an invalid, guessed request.
        """
        symbol = symbol or cls.SYMBOL
        init_db()
        db = SessionLocal()
        try:
            definitions = db.query(BrokerDefinition).filter(BrokerDefinition.enabled == 1).all()
        finally:
            db.close()

        configured_codes = {cls.normalize_source(row.code) for row in definitions}
        sources = [(row.code, row) for row in definitions]
        for builtin in ("Binance", "Delta"):
            if builtin not in configured_codes:
                sources.append((builtin, None))

        summary = []
        for source, definition in sources:
            kind = cls._adapter_kind(source, definition)
            if kind not in ("binance", "delta"):
                item = {"source": cls.normalize_source(source), "symbol": symbol,
                        "error": "daily refresh skipped: no compatible market-data adapter configured"}
                summary.append(item)
                print(f"Sync skipped for {source}: no compatible market-data adapter configured")
                continue
            wanted = list(intervals) if intervals else (
                cls.DELTA_HISTORY_INTERVALS if kind == "delta" else cls.DAILY_INTERVALS
            )
            if kind == "delta":
                wanted = [i for i in wanted if i not in cls.DELTA_EXCLUDED_INTERVALS]
            summary.extend(cls.sync_market_data(
                source, symbol, wanted, definition=definition, limit=1000
            ))
            # Keep the mark-price series of the BTC perpetual current too, so
            # paper/live risk (and the next backtest) can be priced on mark.
            # A mark-price failure never fails the candle refresh.
            try:
                mark_summary = cls.sync_mark_prices(
                    source, symbol, wanted, definition=definition, limit=1000)
                for item in mark_summary:
                    item["series"] = "mark_price"
                summary.extend(mark_summary)
            except Exception as exc:
                print(f"Mark-price refresh skipped for {source}: {exc}")
        return summary

    @classmethod
    def seed_from_csv(cls, csv_path, interval, symbol="BTCUSDT", source="Binance", clear_existing=False):
        if not os.path.exists(csv_path):
            raise FileNotFoundError(os.path.abspath(csv_path))
        df = pd.read_csv(csv_path)
        required = {"event_time", "open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing))}")
        numeric = ['open', 'high', 'low', 'close', 'volume']
        for column in numeric:
            df[column] = pd.to_numeric(df[column], errors='coerce')
        if df[numeric].isna().any().any():
            raise ValueError('CSV contains blank or non-numeric OHLCV values; volume is required for every candle')
        # Candle timestamps must sit on the interval grid. The bundled legacy
        # CSVs carry timestamps like 2020-06-26 11:41:59.523330 for a "1h"
        # series — importing them corrupts charts, backtests and mark pricing,
        # so the import is rejected with the exact offending rows instead.
        times = pd.to_datetime(df['event_time'], errors='coerce')
        interval_seconds = cls._interval_seconds(str(interval).lower())
        # Unit-safe epoch seconds: pandas 2 stores parsed columns in varying
        # resolutions (s/us/ns), so subtracting the epoch beats raw int64 casts.
        epochs = (times - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)
        bad = times.isna() | (epochs % interval_seconds != 0)
        if bool(bad.any()):
            samples = ', '.join(str(value) for value in times[bad].head(3))
            raise ValueError(
                f"{int(bad.sum())} of {len(df)} rows are not aligned to the {interval} candle grid "
                f"(e.g. {samples}). Misaligned candles corrupt charts, backtests and mark pricing, "
                f"so the import was rejected. Fetch clean candles from the exchange instead "
                f"(Admin → Seed Data → 'Binance 2020 → today', or python -m app.scripts.seeder)."
            )
        rows = df.to_dict("records")
        result = cls.upsert_rows(rows, source, symbol, interval, clear_existing)
        return {"source": cls.normalize_source(source), "symbol": symbol, "interval": interval,
                "fetched": len(rows), **result}

    @classmethod
    def update_daily_data(cls, symbol="BTCUSDT", intervals=None, source="Binance"):
        """Backward-compatible daily entry point for one source."""
        return cls.sync_market_data(source, symbol, intervals, limit=1000)


# Backwards-compatible helpers used by older scripts.
def fetch_binance_klines(symbol, interval, start_time, end_time=None):
    rows = DataSyncService.fetch_klines("Binance", symbol, interval, start_time, end_time, 1500)
    return [[int(pd.Timestamp(r["event_time"]).timestamp() * 1000), str(r["open"]), str(r["high"]), str(r["low"]), str(r["close"]), str(r["volume"])] for r in rows]


def seed_to_db(symbol="BTCUSDT", interval="1h", years=6, source="Binance"):
    """Back-compat full-history seed (the old helper fetched a single page and
    inserted it without an upsert, leaving partial data and duplicates)."""
    end = datetime.utcnow()
    start = end - timedelta(days=years * 365)
    return DataSyncService.seed_market_data(
        source, symbol, [interval], start_date=start.strftime("%Y-%m-%d"),
        end_date=None, limit=1500, fetch_all=True,
    )


def seed_from_csv(csv_path, interval, symbol="BTCUSDT", source="Binance"):
    return DataSyncService.seed_from_csv(csv_path, interval, symbol, source)


def update_daily_data(symbol="BTCUSDT", intervals=None, source="Binance"):
    return DataSyncService.update_daily_data(symbol, intervals, source)
