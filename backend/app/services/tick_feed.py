"""Live price feeds for the live trader.

The live worker used to wake every 60 seconds, re-read the candles and re-check
the stop-loss / take-profit. A position could therefore run past its stop for up
to a full minute before the worker noticed — and that minute is exactly when a
stop matters. These feeds hand the worker the price continuously so exits react
on the tick instead of on the minute.

Three implementations share one interface:

* :class:`WebSocketTickFeed` — subscribes to the venue's own stream. No polling,
  so it costs no rate-limit weight at all.
* :class:`RestTickFeed` — polls the mark-price endpoint. The fallback when a
  socket is unavailable, and the only option on a venue with no stream adapter.
* :class:`NullTickFeed` — returns nothing. The default, which leaves the worker
  behaving exactly as it did before feeds existed.

Every feed reports itself **stale** once its last price is older than
``max_age``. A stale price is never traded on: ``quote()`` returns ``None`` and
the caller keeps using the candle price it already had. Acting on a number
nobody has refreshed would be worse than acting late.

Feeds produce the same :class:`~app.core.mark_price.MarkPriceQuote` the REST
path already returns, so nothing downstream has to know which one it came from.
"""
import asyncio
import json
import time
from typing import Any, Callable, Dict, Optional

from app.core.mark_price import MarkPriceQuote

# How long a price stays usable. Well past a normal stream gap, well short of
# the 60-second poll it replaces.
DEFAULT_MAX_AGE = 15.0


def _f(value) -> Optional[float]:
    """Tolerant float parse: venues send prices as strings, sometimes empty."""
    try:
        if value is None or value == "":
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


# ---------------------------------------------------------------------------
# Venue message parsers
# ---------------------------------------------------------------------------
def parse_binance(payload: Any, source: str = "Binance") -> Optional[MarkPriceQuote]:
    """Binance futures stream message -> quote.

    Handles the two streams worth having:

    * ``btcusdt@markPrice``  — ``{"e":"markPriceUpdate","p":"<mark>","i":"<index>"}``
    * ``btcusdt@bookTicker`` — ``{"b":"<bid>","a":"<ask>"}``, taken at the mid.

    Anything else is ignored rather than guessed at: a partial fill report or a
    book-depth update carries no usable single price.
    """
    if not isinstance(payload, dict):
        return None
    symbol = str(payload.get("s") or "BTCUSDT")
    event = str(payload.get("e") or "")

    if event == "markPriceUpdate" or (payload.get("p") and "b" not in payload):
        mark = _f(payload.get("p"))
        if mark is None:
            return None
        return MarkPriceQuote(source, symbol, mark_price=mark,
                              index_price=_f(payload.get("i")), raw=payload)

    bid, ask = _f(payload.get("b")), _f(payload.get("a"))
    if bid is not None and ask is not None:
        mid = (bid + ask) / 2.0
        return MarkPriceQuote(source, symbol, mark_price=mid, last_price=mid, raw=payload)
    return None


def parse_delta(payload: Any, source: str = "Delta") -> Optional[MarkPriceQuote]:
    """Delta Exchange ``ticker`` message -> quote.

    Changelog 17.04.26 renamed ``v2/ticker`` → ``ticker`` on the public socket.
    Frames still arrive as ``{"type":"ticker","symbol":...}`` with mark + last.
    Subscription acks carry no price and are skipped.
    """
    if not isinstance(payload, dict):
        return None
    kind = str(payload.get("type") or "").lower()
    if kind != "ticker":
        return None
    symbol = str(payload.get("symbol") or "BTCUSD")
    mark = _f(payload.get("mark_price"))
    last = _f(payload.get("last_price")) or _f(payload.get("close"))
    if mark is None and last is None:
        return None
    return MarkPriceQuote(source, symbol, mark_price=mark, last_price=last, raw=payload)


def parse_delta_candlestick(payload: Any) -> Optional[Dict[str, Any]]:
    """Delta ``candlestick_1h`` / ``candlesticks`` frame -> a candle dict.

    Used by the live import flow so indicator warm-up reacts on the 1h close
    instead of polling ``GET /v2/history/candles`` (REST is rate-limited).
    A still-forming bar is returned with ``closed=False``.
    """
    if not isinstance(payload, dict):
        return None
    kind = str(payload.get("type") or payload.get("channel") or "").lower()
    looks_like = (
        kind.startswith("candlestick") or kind in ("candles", "ohlc", "candlesticks")
        or "candle_start_time" in payload
    )
    if not looks_like:
        return None
    if kind in ("ticker", "subscriptions", "heartbeat", "key-auth"):
        return None
    symbol = str(payload.get("symbol") or payload.get("product_symbol") or payload.get("sy") or "")
    resolution = str(payload.get("resolution") or payload.get("interval") or payload.get("res") or "")
    if not resolution and kind.startswith("candlestick_"):
        resolution = kind.split("_", 1)[-1]
    start = payload.get("candle_start_time") or payload.get("start") or payload.get("time") or payload.get("ts")
    try:
        start_ts = float(start) if start is not None else None
    except (TypeError, ValueError):
        start_ts = None
    if start_ts:
        if start_ts > 1e16:       # nanoseconds
            start_ts /= 1e9
        elif start_ts > 1e14:     # microseconds (compact candlestick ``ts``)
            start_ts /= 1e6
        elif start_ts > 1e11:     # milliseconds
            start_ts /= 1e3
    event_time = None
    if start_ts:
        from datetime import datetime, timezone
        try:
            event_time = datetime.fromtimestamp(start_ts, tz=timezone.utc).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            event_time = None
    open_ = _f(payload.get("open") or payload.get("o"))
    high = _f(payload.get("high") or payload.get("h"))
    low = _f(payload.get("low") or payload.get("l"))
    close = _f(payload.get("close") or payload.get("c"))
    volume = _f(payload.get("volume") or payload.get("v")) or 0.0
    if close is None and open_ is None:
        return None
    closed = payload.get("closed")
    if closed is None:
        closed = payload.get("is_closed")
    closed = True if closed is None else bool(closed)
    return {
        "symbol": symbol or "BTCUSD",
        "resolution": resolution or "1h",
        "event_time": event_time,
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
        "closed": closed,
        "raw": payload,
    }


PARSERS = {"binance": parse_binance, "delta": parse_delta}

# Stream endpoints per venue kind. ``{symbol}`` is the venue's perpetual symbol
# (lower-cased for Binance, as its streams require).
STREAM_URLS = {
    "binance": "wss://fstream.binance.com/ws/{symbol_lower}@markPrice@1s",
    "delta": "wss://public-socket.india.delta.exchange",
}
STREAM_URLS_TESTNET = {
    "binance": "wss://stream.binancefuture.com/ws/{symbol_lower}@markPrice@1s",
    "delta": "wss://socket-ind-pub.testnet.deltaex.org",
}

# Delta multiplexes every product on one socket and needs an explicit
# subscription; Binance encodes the stream in the URL and needs nothing.
# The live import flow subscribes to *both* the ticker (mark price for
# exits) and the 1-hour candlestick channel (indicator updates on close)
# so REST candle polling is not needed on every tick.
def delta_subscribe(symbol: str, include_candles: bool = True,
                    resolution: str = "1h") -> Dict[str, Any]:
    # Changelog 17.04.26: ``v2/ticker`` → ``ticker`` on the new public socket.
    # The old name on wss://socket.india.delta.exchange was removed 31 Jul 2026.
    channels = [{"name": "ticker", "symbols": [symbol]}]
    if include_candles:
        # Official candlesticks channel: ``candlestick_${resolution}``
        # (docs.delta.exchange Public Channels). Bare symbol = last-traded OHLC,
        # ``MARK:symbol`` = mark-price OHLC.
        candle_symbols = [symbol]
        if not str(symbol).upper().startswith("MARK:"):
            candle_symbols.append(f"MARK:{symbol}")
        channels.append({"name": f"candlestick_{resolution}", "symbols": candle_symbols})
    return {"type": "subscribe", "payload": {"channels": channels}}


def delta_private_subscribe(symbols: Optional[list] = None) -> Dict[str, Any]:
    """Positions + orders private channels (after key-auth)."""
    wanted = list(symbols or ["all"])
    return {"type": "subscribe",
            "payload": {"channels": [
                {"name": "orders", "symbols": wanted},
                {"name": "positions", "symbols": wanted},
            ]}}


def delta_key_auth(api_key: str, api_secret: str) -> Dict[str, Any]:
    """Socket auth frame: HMAC-SHA256 over ``GET`` + timestamp + ``/live``."""
    import hashlib
    import hmac as hmac_mod
    timestamp = str(int(time.time()))
    signature = hmac_mod.new(api_secret.encode(),
                             f"GET{timestamp}/live".encode(),
                             hashlib.sha256).hexdigest()
    return {"type": "key-auth",
            "payload": {"api-key": api_key, "signature": signature, "timestamp": timestamp}}


async def _default_connect(url: str):
    """Open a real socket. Imported lazily so the module loads without it."""
    from websockets.asyncio.client import connect
    return await connect(url, ping_interval=20, ping_timeout=20, max_queue=64)


class TickFeed:
    """Base feed: keeps the newest price and knows when it has gone stale."""

    def __init__(self, source: str, symbol: str, max_age: float = DEFAULT_MAX_AGE):
        self.source = source
        self.symbol = symbol
        self.max_age = float(max_age)
        self._quote: Optional[MarkPriceQuote] = None
        self._received_at: Optional[float] = None
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self.messages = 0
        self.reconnects = 0
        self.last_error: Optional[str] = None
        # Last closed 1h candle from the candlesticks channel (Delta import flow).
        self.last_candle: Optional[Dict[str, Any]] = None
        self.closed_candles = 0

    @property
    def kind(self) -> str:
        return "none"

    @property
    def connected(self) -> bool:
        return False

    def publish(self, quote: Optional[MarkPriceQuote]) -> bool:
        """Store a parsed quote. Returns False when there was nothing usable."""
        if quote is None or quote.basis_price is None:
            return False
        self._quote = quote
        self._received_at = time.monotonic()
        self.messages += 1
        # Persist every live quote (websocket / REST). A NullTickFeed is the
        # "off" path and must not write synthetic test prices into the table.
        if self.kind != "none":
            try:
                from app.services.tick_store import record_tick
                record_tick(quote, feed_kind=self.kind)
            except Exception:
                pass
        return True

    def quote(self) -> Optional[MarkPriceQuote]:
        """Newest price, or ``None`` when there is none fresh enough to trust."""
        if self._quote is None or self._received_at is None:
            return None
        if time.monotonic() - self._received_at > self.max_age:
            return None
        return self._quote

    def age(self) -> Optional[float]:
        """Seconds since the last usable price; ``None`` when never received."""
        if self._received_at is None:
            return None
        return time.monotonic() - self._received_at

    async def start(self):
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.ensure_future(self._run())

    async def stop(self):
        self._stopping = True
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            from app.services.tick_store import flush_ticks
            flush_ticks()
        except Exception:
            pass

    async def _run(self):
        raise NotImplementedError

    def stats(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "connected": self.connected,
            "messages": self.messages,
            "reconnects": self.reconnects,
            "age_seconds": round(self.age(), 2) if self.age() is not None else None,
            "stale": self.quote() is None,
            "last_error": self.last_error,
            "closed_candles": int(self.closed_candles),
            "last_candle_time": (
                self.last_candle["event_time"].isoformat(timespec="seconds")
                if self.last_candle and self.last_candle.get("event_time") else None
            ),
        }


class NullTickFeed(TickFeed):
    """No feed. The worker falls back to the candle price, as it always did."""

    @property
    def kind(self) -> str:
        return "none"

    async def _run(self):
        while not self._stopping:
            await asyncio.sleep(3600)


class WebSocketTickFeed(TickFeed):
    """Subscribes to the venue's price stream and reconnects when it drops.

    ``connect_fn`` is injectable so the reconnect and parsing logic can be
    exercised against a local socket instead of a real venue.
    """

    def __init__(self, url: str, source: str, symbol: str,
                 parser: Callable[[Any, str], Optional[MarkPriceQuote]],
                 subscribe: Optional[Dict[str, Any]] = None,
                 max_age: float = DEFAULT_MAX_AGE,
                 connect_fn: Optional[Callable] = None,
                 backoff_cap: float = 30.0):
        super().__init__(source, symbol, max_age)
        self.url = url
        self.parser = parser
        self.subscribe = subscribe
        self.connect_fn = connect_fn or _default_connect
        self.backoff_cap = float(backoff_cap)
        self._connected = False

    @property
    def kind(self) -> str:
        return "websocket"

    @property
    def connected(self) -> bool:
        return self._connected

    async def _run(self):
        backoff = 1.0
        while not self._stopping:
            ws = None
            try:
                ws = await self.connect_fn(self.url)
                self._connected = True
                self.last_error = None
                backoff = 1.0
                if self.subscribe is not None:
                    await ws.send(json.dumps(self.subscribe))
                async for raw in ws:
                    if self._stopping:
                        break
                    self._handle(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a dropped socket must never kill the worker
                self.last_error = f"{type(exc).__name__}: {exc}"
            finally:
                self._connected = False
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        pass
            if self._stopping:
                break
            self.reconnects += 1
            # The cap bounds every wait including the first one; applying it
            # only when doubling would make an initial 1s delay ignore a
            # caller that asked for a much tighter retry.
            await asyncio.sleep(min(backoff, self.backoff_cap))
            backoff = min(backoff * 2, self.backoff_cap)

    def _handle(self, raw):
        if isinstance(raw, (bytes, bytearray)):
            try:
                raw = raw.decode("utf-8", "replace")
            except Exception:
                return
        if isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except (ValueError, TypeError):
                return
        else:
            payload = raw
        # Some venues batch several updates into one frame.
        for item in (payload if isinstance(payload, list) else [payload]):
            self.publish(self.parser(item, self.source))
            candle = parse_delta_candlestick(item)
            if candle is not None:
                self.last_candle = candle
                if candle.get("closed"):
                    self.closed_candles += 1


class RestTickFeed(TickFeed):
    """Polls the venue's mark-price endpoint on a fixed interval.

    Costs rate-limit weight, so the interval matters: four instances on one API
    key polling every second is 240 requests a minute against a shared budget.
    """

    def __init__(self, fetch: Callable[[], Any], source: str, symbol: str,
                 interval: float = 5.0, max_age: float = DEFAULT_MAX_AGE):
        # A polled price is only as fresh as the poll, so it must not be
        # discarded before the next one is due.
        super().__init__(source, symbol, max(max_age, interval * 2.5))
        self.fetch = fetch
        self.interval = float(interval)

    @property
    def kind(self) -> str:
        return "rest"

    @property
    def connected(self) -> bool:
        return self._quote is not None and self.quote() is not None

    async def _run(self):
        while not self._stopping:
            try:
                quote = self.fetch()
                if quote is not None:
                    self.last_error = None
                self.publish(quote)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
            try:
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break


def build_tick_feed(kind: str, source: str, symbol: str, definition=None,
                    perpetual: Optional[str] = None,
                    client=None, interval: float = 5.0,
                    max_age: float = DEFAULT_MAX_AGE,
                    connect_fn: Optional[Callable] = None) -> TickFeed:
    """Build the requested feed, degrading to REST and then to nothing.

    ``kind`` is ``websocket``, ``rest`` or ``none``. An unsupported venue never
    raises: the caller still gets a working feed object, it simply has no live
    price and the worker behaves as it did before.
    """
    venue_kind = str(getattr(definition, "kind", "") or "").lower()
    if not venue_kind:
        venue_kind = "binance" if str(source).lower().startswith("binance") else (
            "delta" if "delta" in str(source).lower() else "")
    contract = perpetual or symbol
    parser = PARSERS.get(venue_kind)

    if kind == "websocket" and parser is not None:
        testnet = bool(getattr(client, "testnet", False))
        urls = STREAM_URLS_TESTNET if testnet else STREAM_URLS
        template = urls.get(venue_kind) or STREAM_URLS.get(venue_kind)
        if template:
            url = template.format(symbol_lower=str(contract).lower())
            subscribe = delta_subscribe(contract) if venue_kind == "delta" else None
            return WebSocketTickFeed(url, source, contract, parser,
                                     subscribe=subscribe, max_age=max_age,
                                     connect_fn=connect_fn)

    if kind in ("websocket", "rest") and client is not None:
        return RestTickFeed(lambda: client.fetch_mark_price(symbol), source, contract,
                            interval=interval, max_age=max_age)

    return NullTickFeed(source, contract, max_age=max_age)
