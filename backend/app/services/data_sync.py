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
        return {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "1D"}.get(interval, interval)

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
                response.raise_for_status()
                raw = response.json()
                if not isinstance(raw, list):
                    raise MarketDataError(str(raw))
                return [{"event_time": pd.to_datetime(k[0], unit="ms").to_pydatetime(),
                         "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                         "close": float(k[4]), "volume": float(k[5])} for k in raw]
            except Exception as exc:
                raise MarketDataError(f"Binance data request failed: {exc}") from exc

        if source == "Delta":
            url = "https://api.india.delta.exchange/v2/history/candles"
            params = {"symbol": cls._delta_symbol(symbol), "resolution": cls._delta_resolution(interval), "limit": min(limit, 2000)}
            if start_time:
                params["start"] = int(cls._as_datetime(start_time).replace(tzinfo=timezone.utc).timestamp())
            if end_time:
                params["end"] = int(cls._as_datetime(end_time).replace(tzinfo=timezone.utc).timestamp())
            try:
                response = requests.get(url, params=params, timeout=20)
                response.raise_for_status()
                payload = response.json()
                raw = payload.get("result", payload) if isinstance(payload, dict) else payload
                if not isinstance(raw, list):
                    raise MarketDataError(str(payload))
                rows = []
                for k in raw:
                    if isinstance(k, dict):
                        ts = k.get("time", k.get("timestamp"))
                        rows.append({"event_time": pd.to_datetime(float(ts), unit="s").to_pydatetime(),
                                     "open": float(k["open"]), "high": float(k["high"]),
                                     "low": float(k["low"]), "close": float(k["close"]),
                                     "volume": float(k.get("volume", k.get("vol", 0)) or 0)})
                    else:
                        rows.append({"event_time": pd.to_datetime(float(k[0]), unit="s").to_pydatetime(),
                                     "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                                     "close": float(k[4]), "volume": float(k[5] if len(k) > 5 else 0)})
                return rows
            except Exception as exc:
                raise MarketDataError(f"Delta Exchange data request failed: {exc}") from exc

        raise MarketDataError(f"No market-data adapter is installed for source '{source}'. Add an adapter before live use.")

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
        start = cls._as_datetime(start_date) if start_date else end - timedelta(days=365)
        summary = []
        interval_seconds = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
        for interval in intervals:
            rows = []
            cursor = start
            # The checkbox in the admin UI enables pagination past the
            # exchange's 1,000/1,500 candle API page limit.
            for _ in range(10000 if fetch_all else 1):
                page = cls.fetch_klines(source, symbol, interval, cursor, end, limit)
                if not page:
                    break
                rows.extend(page)
                if not fetch_all or len(page) < limit:
                    break
                last = max(cls._as_datetime(r["event_time"]) for r in page)
                next_cursor = last + timedelta(seconds=interval_seconds.get(interval, 60))
                if next_cursor <= cursor or next_cursor >= end:
                    break
                cursor = next_cursor
            # Deduplicate pages before the single database upsert.
            unique = {cls._as_datetime(r["event_time"]): r for r in rows}
            rows = [unique[t] for t in sorted(unique)]
            result = cls.upsert_rows(rows, source, symbol, interval)
            summary.append({"source": source, "symbol": symbol, "interval": interval,
                            "fetched": len(rows), "fetch_all": fetch_all, **result})
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
