"""Market data adapters and database seeding helpers."""
import os
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import pandas as pd
import requests

from app.database.models import SessionLocal, Klines, init_db


class MarketDataError(RuntimeError):
    pass


class DataSyncService:
    TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]
    SYMBOL = "BTCUSDT"
    SOURCE_ALIASES = {"binance": "Binance", "delta": "Delta", "delta exchange": "Delta"}
    # Delta serves the same public history API from more than one host. If the
    # primary API host is down/geo-blocked the CDN host (the one in Delta's own
    # API guide) still answers, so we fall back through the list in order.
    DELTA_HOSTS = [
        "https://api.india.delta.exchange",
        "https://cdn.india.deltaex.org",
    ]

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
    def _delta_fetch_one(cls, host, params):
        """Try one Delta host. Returns (rows, status_code, note).

        `note` carries the reason a host produced no rows (HTTP status,
        exchange error text, parse failure) so callers can surface *why* a
        seed fetched 0 candles instead of failing silently.
        """
        try:
            response = requests.get(f"{host}/v2/history/candles", params=params, timeout=20)
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
    def _delta_fetch(cls, symbol, interval, start_time=None, end_time=None, limit=1000):
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
        params = {
            "symbol": cls._delta_symbol(symbol),
            "resolution": cls._delta_resolution(interval),
            "limit": min(limit, 2000),
            "start": int(cls._as_datetime(start_time).replace(tzinfo=timezone.utc).timestamp()) if start_time else
                now - cls._interval_seconds(interval) * min(limit, 2000),
            "end": int(cls._as_datetime(end_time).replace(tzinfo=timezone.utc).timestamp()) if end_time else now,
        }
        attempts = []
        for host in cls.DELTA_HOSTS:
            rows, status, note = cls._delta_fetch_one(host, params)
            if rows:
                return rows
            attempts.append(f"{host} → HTTP {status if status is not None else 'n/a'}"
                            + (f" ({note})" if note else " (empty result)"))
        raise MarketDataError(
            f"Delta Exchange returned 0 candles for {params['symbol']} {params['resolution']}: "
            + " | ".join(attempts)
        )

    @classmethod
    def fetch_klines(cls, source="Binance", symbol="BTCUSDT", interval="1h",
                     start_time: Optional[datetime] = None, end_time: Optional[datetime] = None,
                     limit: int = 1000):
        source = cls.normalize_source(source)
        if source == "Binance":
            url = "https://fapi.binance.com/fapi/v1/klines"
            params = {"symbol": cls._binance_symbol(symbol), "interval": interval, "limit": min(limit, 1500)}
            if start_time:
                params["startTime"] = int(cls._as_datetime(start_time).replace(tzinfo=timezone.utc).timestamp() * 1000)
            if end_time:
                params["endTime"] = int(cls._as_datetime(end_time).replace(tzinfo=timezone.utc).timestamp() * 1000)
            try:
                response = requests.get(url, params=params, timeout=20)
                if response.status_code != 200:
                    body = (response.text or "").strip().replace("\n", " ")[:300]
                    raise MarketDataError(f"Binance data request failed: HTTP {response.status_code} {body}".strip())
                raw = response.json()
                if not isinstance(raw, list):
                    raise MarketDataError(f"Binance data request failed: {str(raw)[:300]}")
                return [{"event_time": pd.to_datetime(k[0], unit="ms").to_pydatetime(),
                         "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                         "close": float(k[4]), "volume": float(k[5])} for k in raw]
            except MarketDataError:
                raise
            except Exception as exc:
                raise MarketDataError(f"Binance data request failed: {exc}") from exc

        if source == "Delta":
            return cls._delta_fetch(symbol, interval, start_time, end_time, limit)

        raise MarketDataError(f"No market-data adapter is installed for source '{source}'. Add an adapter before live use.")

    @classmethod
    def test_source(cls, source="Binance", symbol="BTCUSDT", interval="1h", limit=3):
        """Small round-trip used by the admin UI's 'Test connection' button.

        Returns a diagnostic dict describing exactly what the exchange
        answered, so an empty seed can be diagnosed in one click.
        """
        source = cls.normalize_source(source)
        try:
            rows = cls.fetch_klines(source, symbol, interval, limit=limit)
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
    def upsert_rows(cls, rows: Iterable[dict], source="Binance", symbol="BTCUSDT", interval="1h", clear_existing=False):
        source = cls.normalize_source(source)
        init_db()
        db = SessionLocal()
        try:
            if clear_existing:
                db.query(Klines).filter_by(source=source, symbol=symbol, interval=interval).delete()
            existing = {k.event_time: k for k in db.query(Klines).filter_by(source=source, symbol=symbol, interval=interval).all()}
            inserted = updated = 0
            for row in rows:
                event_time = cls._as_datetime(row["event_time"])
                values = {"open": float(row["open"]), "high": float(row["high"]),
                          "low": float(row["low"]), "close": float(row["close"]),
                          "volume": float(row.get("volume", 0) or 0)}
                item = existing.get(event_time)
                if item:
                    for key, value in values.items():
                        setattr(item, key, value)
                    updated += 1
                else:
                    db.add(Klines(source=source, symbol=symbol, interval=interval,
                                   event_time=event_time, **values))
                    inserted += 1
            db.commit()
            return {"inserted": inserted, "updated": updated, "total": inserted + updated}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @classmethod
    def seed_market_data(cls, source="Binance", symbol="BTCUSDT", intervals=None,
                         start_date=None, end_date=None, limit=1000, fetch_all=False):
        source = cls.normalize_source(source)
        intervals = intervals or cls.TIMEFRAMES
        end = cls._as_datetime(end_date) if end_date else datetime.utcnow()
        # Date-only admin inputs are inclusive through the selected day.
        if isinstance(end_date, str) and len(end_date) == 10:
            end = end + timedelta(days=1) - timedelta(microseconds=1)
        summary = []
        interval_seconds = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
        for interval in intervals:
            rows = []
            # Without an explicit start date:
            #  - "Fetch all pages" keeps the 365-day window and paginates it;
            #  - a single page asks for exactly `limit` most recent candles,
            #    because exchanges differ in which end of a wide window they
            #    return and one page of 1,000 candles covers far less than a
            #    year on 1m/5m/15m anyway (this made "0 candles" results
            #    confusing before).
            start = cls._as_datetime(start_date) if start_date else \
                (end - timedelta(days=365) if fetch_all
                 else end - timedelta(seconds=interval_seconds.get(interval, 60) * limit))
            cursor = start
            last_page_end = None
            error = None
            # The checkbox in the admin UI enables pagination past the
            # exchange's 1,000/1,500 candle API page limit.
            for _ in range(10000 if fetch_all else 1):
                try:
                    page = cls.fetch_klines(source, symbol, interval, cursor, end, limit)
                except MarketDataError as exc:
                    error = str(exc)
                    break
                if not page:
                    error = (f"exchange returned 0 candles for "
                             f"{start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} UTC")
                    break
                rows.extend(page)
                page_limit = min(limit, 1500 if source == 'Binance' else 2000)
                if not fetch_all or len(page) < page_limit:
                    break
                last = max(cls._as_datetime(r["event_time"]) for r in page)
                if last_page_end is not None and last <= last_page_end:
                    break  # duplicate page — stop instead of looping forever
                last_page_end = last
                next_cursor = last + timedelta(seconds=interval_seconds.get(interval, 60))
                if next_cursor <= cursor or next_cursor >= end:
                    break
                cursor = next_cursor
            if error or not rows:
                summary.append({"source": source, "symbol": symbol, "interval": interval,
                                "fetched": 0, "fetch_all": fetch_all,
                                "inserted": 0, "updated": 0, "total": 0,
                                "error": error or "no candles fetched"})
                continue
            # Deduplicate pages before the single database upsert.
            unique = {cls._as_datetime(r["event_time"]): r for r in rows}
            rows = [unique[t] for t in sorted(unique)]
            result = cls.upsert_rows(rows, source, symbol, interval)
            entry = {"source": source, "symbol": symbol, "interval": interval,
                     "fetched": len(rows), "fetch_all": fetch_all, **result,
                     "first": min(unique), "last": max(unique)}
            summary.append(entry)
        return summary

    @classmethod
    def sync_market_data(cls, source="Binance", symbol=None, intervals=None):
        source = cls.normalize_source(source)
        symbol = symbol or cls.SYMBOL
        intervals = intervals or cls.TIMEFRAMES
        for interval in intervals:
            try:
                rows = cls.fetch_klines(source, symbol, interval, limit=1000)
                cls.upsert_rows(rows, source, symbol, interval)
                print(f"Synced {len(rows)} {source} {symbol} {interval} candles")
            except MarketDataError as exc:
                print(f"Sync skipped for {source}/{interval}: {exc}")

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
        rows = df.to_dict("records")
        result = cls.upsert_rows(rows, source, symbol, interval, clear_existing)
        return {"source": cls.normalize_source(source), "symbol": symbol, "interval": interval,
                "fetched": len(rows), **result}

    @classmethod
    def update_daily_data(cls, symbol="BTCUSDT", intervals=None, source="Binance"):
        cls.sync_market_data(source, symbol, intervals or ["1h", "4h"])


# Backwards-compatible helpers used by older scripts.
def fetch_binance_klines(symbol, interval, start_time, end_time=None):
    rows = DataSyncService.fetch_klines("Binance", symbol, interval, start_time, end_time, 1500)
    return [[int(pd.Timestamp(r["event_time"]).timestamp() * 1000), str(r["open"]), str(r["high"]), str(r["low"]), str(r["close"]), str(r["volume"])] for r in rows]


def seed_to_db(symbol="BTCUSDT", interval="1h", years=6, source="Binance"):
    end = datetime.utcnow()
    start = end - timedelta(days=years * 365)
    rows = DataSyncService.fetch_klines(source, symbol, interval, start, end, 1500)
    return DataSyncService.upsert_rows(rows, source, symbol, interval)


def seed_from_csv(csv_path, interval, symbol="BTCUSDT", source="Binance"):
    return DataSyncService.seed_from_csv(csv_path, interval, symbol, source)


def update_daily_data(symbol="BTCUSDT", intervals=["1h", "4h"], source="Binance"):
    return DataSyncService.update_daily_data(symbol, intervals, source)
