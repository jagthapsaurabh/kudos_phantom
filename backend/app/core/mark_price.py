"""Mark-price plumbing shared by the backtest engine, paper and live traders.

The tool trades the **BTC coin-margined / USDT perpetual** on Binance and
Delta. For those contracts the exchange publishes a *mark price* — a
manipulation-resistant price built from the index and the funding basis — and
it is the price liquidations are computed on. Every risk decision here (stop,
target, trail, breakeven, PnL) is therefore made on the mark price, while the
price the order actually fills at (the traded/last price) is recorded
alongside it so the two can always be reconciled.

Conventions used everywhere:

``price``        – the pricing basis used for the maths (mark when available)
``trade_price``  – the price the order could actually be filled at
``mark_price``   – the exchange mark price at that instant

When mark data is unavailable (older seeded candles, an exchange without a
mark feed) the engine falls back to the traded price and says so through
``mark_price_basis``, so a run never silently changes its accounting rules.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd

# ---------------------------------------------------------------------------
# Perpetual contracts
# ---------------------------------------------------------------------------
# Both venues quote BTC as a perpetual: Binance lists the USDT-margined
# BTCUSDT perpetual, Delta lists the BTCUSD perpetual. Dated futures
# (BTCUSD_27Sep24, BTCUSDT_241227 …) are deliberately NOT used — this map is
# the single place where "the BTC perpetual" is resolved for either venue.
PERPETUAL_SYMBOLS = {
    "Binance": "BTCUSDT",
    "Delta": "BTCUSD",
    "DeltaGlobal": "BTCUSD",  # Global lists the same BTCUSD perpetual
}
# Symbols the app may carry internally, mapped to each venue's perpetual.
_PERPETUAL_ALIASES = {
    "BTCUSDT": {"Binance": "BTCUSDT", "Delta": "BTCUSD", "DeltaGlobal": "BTCUSD"},
    "BTCUSD": {"Binance": "BTCUSDT", "Delta": "BTCUSD", "DeltaGlobal": "BTCUSD"},
    "BTC": {"Binance": "BTCUSDT", "Delta": "BTCUSD", "DeltaGlobal": "BTCUSD"},
}
DELTA_MARK_PREFIX = "MARK:"


def normalize_source_name(source: Optional[str]) -> str:
    text = str(source or "Binance").strip()
    return {"binance": "Binance", "delta": "Delta",
            "delta exchange": "Delta"}.get(text.lower(), text)


def perpetual_symbol(source: Optional[str], symbol: str = "BTCUSDT") -> str:
    """Resolve the **perpetual** contract symbol for a venue.

    ``BTCUSDT`` / ``BTCUSD`` / ``BTC`` all resolve to the venue's perpetual;
    a dated future is *not* rewritten (an explicit contract name wins) so an
    operator who really wants one can still ask for it.
    """
    venue = normalize_source_name(source)
    raw = str(symbol or "BTCUSDT").replace("/", "").replace("-", "").upper()
    default = PERPETUAL_SYMBOLS.get(venue, raw)
    if raw in _PERPETUAL_ALIASES:
        return _PERPETUAL_ALIASES[raw].get(venue, default)
    return default or raw


def mark_symbol(source: Optional[str], symbol: str = "BTCUSDT") -> str:
    """Symbol used by the venue's *mark price* history endpoint."""
    venue = normalize_source_name(source)
    perp = perpetual_symbol(venue, symbol)
    if venue in ("Delta", "DeltaGlobal"):
        return f"{DELTA_MARK_PREFIX}{perp}"
    return perp


def contract_label(source: Optional[str], symbol: str = "BTCUSDT") -> str:
    venue = normalize_source_name(source)
    return f"{perpetual_symbol(venue, symbol)} perpetual ({venue})"


# ---------------------------------------------------------------------------
# Live mark price
# ---------------------------------------------------------------------------
class MarkPriceQuote:
    """Current mark / last price pair for one contract."""

    def __init__(self, source: str, symbol: str, mark_price: Optional[float] = None,
                 last_price: Optional[float] = None, index_price: Optional[float] = None,
                 fetched_at: Optional[datetime] = None, raw: Optional[Dict[str, Any]] = None):
        self.source = source
        self.symbol = symbol
        self.mark_price = _f(mark_price)
        self.last_price = _f(last_price) if last_price is not None else self.mark_price
        self.index_price = _f(index_price)
        self.fetched_at = fetched_at or datetime.now(timezone.utc).replace(tzinfo=None)
        self.raw = raw or {}

    @property
    def basis_price(self) -> Optional[float]:
        """Price used for risk maths: mark when available, else last traded."""
        return self.mark_price if self.mark_price else self.last_price

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "symbol": self.symbol,
            "perpetual_symbol": perpetual_symbol(self.source, self.symbol),
            "mark_price": self.mark_price,
            "last_price": self.last_price,
            "index_price": self.index_price,
            "mark_price_basis": self.mark_price is not None,
            "fetched_at": self.fetched_at.isoformat(timespec="seconds") if self.fetched_at else None,
        }


def _f(value) -> Optional[float]:
    try:
        if value is None:
            return None
        number = float(value)
        if number != number:  # NaN
            return None
        return number
    except (TypeError, ValueError):
        return None


class MarkPriceService:
    """Fetch the current mark price (and mark-price candles) for a venue."""

    TIMEOUT = 15

    @classmethod
    def _binance(cls, base: str, perp: str) -> Optional[MarkPriceQuote]:
        import requests
        quote = MarkPriceQuote("Binance", perp)
        response = requests.get(f"{base}/fapi/v1/premiumIndex",
                                params={"symbol": perp}, timeout=cls.TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        quote.mark_price = _f(payload.get("markPrice"))
        quote.index_price = _f(payload.get("indexPrice"))
        quote.raw = payload
        try:
            ticker = requests.get(f"{base}/fapi/v1/ticker/price",
                                  params={"symbol": perp}, timeout=cls.TIMEOUT)
            if ticker.status_code == 200:
                quote.last_price = _f(ticker.json().get("price"))
        except Exception:
            pass
        if quote.last_price is None:
            quote.last_price = quote.mark_price
        return quote if quote.mark_price or quote.last_price else None

    @classmethod
    def _delta(cls, base: str, perp: str) -> Optional[MarkPriceQuote]:
        import requests
        quote = MarkPriceQuote("Delta", perp)
        response = requests.get(f"{base}/v2/tickers/{perp}", timeout=cls.TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        row = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(row, list):
            row = row[0] if row else None
        if not isinstance(row, dict):
            row = payload if isinstance(payload, dict) else {}
        # Delta has used both `mark_price` and `mark_price_` prefixes over
        # time; read whichever is present so a rename cannot break the feed.
        quote.mark_price = _f(row.get("mark_price") if "mark_price" in row else row.get("mark price"))
        quote.last_price = _f(row.get("close") or row.get("last_price") or row.get("spot_price"))
        quote.index_price = _f(row.get("index_price") or row.get("spot_price"))
        quote.raw = row
        if quote.last_price is None:
            quote.last_price = quote.mark_price
        return quote if quote.mark_price or quote.last_price else None

    @classmethod
    def current(cls, source: str = "Binance", symbol: str = "BTCUSDT",
                definition=None, client=None) -> Optional[MarkPriceQuote]:
        """Current mark + last price. Returns ``None`` when the venue is unreachable."""
        venue = normalize_source_name(source)
        perp = perpetual_symbol(venue, symbol)
        try:
            if client is not None and hasattr(client, "fetch_mark_price"):
                quote = client.fetch_mark_price(perp)
                if quote:
                    return quote
        except Exception:
            pass
        base = None
        kind = None
        if definition is not None:
            kind = str(getattr(definition, "kind", "") or "").lower()
            base = (getattr(definition, "market_data_url", None) or "").rstrip("/") or None
        if not base:
            base = "https://fapi.binance.com" if venue == "Binance" else "https://api.india.delta.exchange"
            kind = "binance" if venue == "Binance" else "delta"
        try:
            if kind == "binance":
                return cls._binance(base, perp)
            if kind == "delta":
                return cls._delta(base, perp)
        except Exception as exc:  # never let a quote failure kill a tick
            print(f"[mark-price] {venue} mark price unavailable: {exc}")
        return None

    @classmethod
    def mark_klines(cls, source: str = "Binance", symbol: str = "BTCUSDT",
                    interval: str = "1h", start_time=None, end_time=None,
                    limit: int = 1000) -> List[Dict[str, Any]]:
        """Historical mark-price candles, normalized like regular OHLC rows."""
        venue = normalize_source_name(source)
        perp = perpetual_symbol(venue, symbol)
        if venue == "Binance":
            import requests
            params = {"symbol": perp, "interval": interval,
                      "limit": int(min(max(1, limit), 1500))}
            if start_time is not None:
                params["startTime"] = int(pd.Timestamp(start_time).timestamp() * 1000)
            if end_time is not None:
                params["endTime"] = int(pd.Timestamp(end_time).timestamp() * 1000)
            response = requests.get("https://fapi.binance.com/fapi/v1/markPriceKlines",
                                    params=params, timeout=20)
            response.raise_for_status()
            rows = []
            for k in response.json() or []:
                rows.append({"event_time": pd.to_datetime(k[0], unit="ms").to_pydatetime(),
                             "open": float(k[1]), "high": float(k[2]),
                             "low": float(k[3]), "close": float(k[4])})
            return rows
        if venue == "Delta":
            from app.services.data_sync import DataSyncService
            return DataSyncService.fetch_mark_klines(venue, perp, interval,
                                                     start_time, end_time, limit)
        return []


# ---------------------------------------------------------------------------
# Series helpers (backtest)
# ---------------------------------------------------------------------------
MARK_COLUMNS = ("mark_open", "mark_high", "mark_low", "mark_close")


def mark_columns_present(df: Optional[pd.DataFrame]) -> bool:
    if df is None or not hasattr(df, "columns"):
        return False
    return all(col in df.columns for col in MARK_COLUMNS)


def mark_series(df: pd.DataFrame, column: str = "mark_close") -> Optional[pd.Series]:
    """Numeric mark-price series for a candle frame (NaN where not seeded)."""
    if column not in df.columns:
        return None
    series = pd.to_numeric(df[column], errors="coerce")
    if series.notna().sum() == 0:
        return None
    return series


def decision_series(df: pd.DataFrame, use_mark_price: bool = True,
                    fallback_column: str = "close", mark_column: str = "mark_close"):
    """Return (series, coverage, basis) for the price used by the engine.

    ``coverage`` is the share of bars that actually carry a mark price, so a
    run can disclose how much of it was priced on mark.
    """
    fallback = pd.to_numeric(df[fallback_column], errors="coerce")
    if not use_mark_price:
        return fallback, 0.0, "trade"
    marks = mark_series(df, mark_column)
    if marks is None:
        return fallback, 0.0, "trade"
    coverage = float(marks.notna().sum()) / float(max(1, len(marks)))
    return marks.fillna(fallback), coverage, ("mark" if coverage > 0 else "trade")


def _as_datetime(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    return pd.to_datetime(value, utc=True).to_pydatetime().replace(tzinfo=None)


def upsert_mark_rows(db, rows: Iterable[Dict[str, Any]], source: str, symbol: str,
                     interval: str) -> Dict[str, int]:
    """Store mark OHLC on existing candles (matched by source/symbol/time).

    Mark prices belong to the same candle as the traded OHLCV, so they are
    written onto the ``klines`` row instead of a parallel table: a backtest
    then reads one row per bar and always has the two prices aligned.
    """
    from app.database.models import Klines

    prepared: Dict[datetime, Dict[str, float]] = {}
    for row in rows or []:
        event_time = _as_datetime(row.get("event_time"))
        if event_time is None:
            continue
        prepared[event_time] = {
            "mark_open": _f(row.get("open")),
            "mark_high": _f(row.get("high")) if row.get("high") is not None else _f(row.get("open")),
            "mark_low": _f(row.get("low")) if row.get("low") is not None else _f(row.get("open")),
            "mark_close": _f(row.get("close")),
        }
    if not prepared:
        return {"inserted": 0, "updated": 0, "total": 0}
    source = normalize_source_name(source)
    updated = 0
    for event_time, values in prepared.items():
        row = db.query(Klines).filter(
            Klines.source == source, Klines.symbol == symbol,
            Klines.interval == interval, Klines.event_time == event_time,
        ).first()
        if row is None:
            continue
        for key, value in values.items():
            setattr(row, key, value)
        updated += 1
    return {"inserted": 0, "updated": updated, "total": updated}
