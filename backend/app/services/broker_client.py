"""Broker adapters used by live trading, market-data fallback and the terminal.

Two venues are installed: **Binance Futures (USDS-M)** and **Delta Exchange**.
Both are wired to the venue's BTC *perpetual* (see ``app.core.mark_price``).

The client covers the whole order lifecycle the trading terminal needs:

* market data           — candles, mark-price candles, tickers, order book
* instruments           — contract size / tick size / step size, so a strategy
                          sized in BTC is converted into the venue's own units
* orders                — place (market / limit / stop / stop-limit), bracket
                          orders (entry + stop-loss + take-profit), edit,
                          cancel one, cancel all, open orders, order history
* positions             — margined positions, add/remove margin, close one/all
* fills & trades        — user fills (executions) with fees
* wallet / risk         — balances, available margin, used margin, unrealised PnL
* account config        — leverage, margin mode
* rate limits           — local sliding-window limiter + exchange feedback

Every call goes through :class:`RateLimiter`, honours an HTTP 429
``Retry-After`` / ``X-RATE-LIMIT-RESET`` before retrying, and records what the
venue reports about the remaining budget so the UI can show it.

Failures are returned — not raised — as ``{"error": "..."}`` so a trading loop
keeps running after a rejected order.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from app.core.mark_price import MarkPriceQuote, perpetual_symbol
from app.core.rate_limit import (
    RateLimitConfig, RateLimitExceeded, default_config_for, get_limiter,
)


class BrokerClient:
    """Small common client for Binance Futures and Delta Exchange.

    Public market data works without credentials. Trading endpoints deliberately
    return an error object instead of raising so the existing worker can keep
    scanning after a rejected order.
    """

    DEFAULTS = {
        "Binance": {"kind": "binance", "market": "https://fapi.binance.com", "trading": "https://fapi.binance.com"},
        "Delta": {"kind": "delta", "market": "https://api.india.delta.exchange", "trading": "https://api.india.delta.exchange"},
    }

    # Accepted aliases for Binance's `workingType` (stop-trigger source).
    BINANCE_WORKING_TYPES = {
        "MARK_PRICE": "MARK_PRICE", "MARK": "MARK_PRICE",
        "CONTRACT_PRICE": "CONTRACT_PRICE", "CONTRACT": "CONTRACT_PRICE",
        "LAST_TRADED_PRICE": "CONTRACT_PRICE", "LAST": "CONTRACT_PRICE",
    }

    # Binance order types the terminal can send.
    BINANCE_ORDER_TYPES = {
        "market": "MARKET", "limit": "LIMIT",
        "stop_market": "STOP_MARKET", "stop_limit": "STOP",
        "take_profit_market": "TAKE_PROFIT_MARKET", "take_profit_limit": "TAKE_PROFIT",
        "trailing_stop": "TRAILING_STOP_MARKET",
    }

    def __init__(self, api_key: str = "", api_secret: str = "", broker_name: str = "Binance",
                 passphrase: str = "", testnet: bool = False, definition=None,
                 rate_limit: Optional[RateLimitConfig] = None, limiter_key: str = ""):
        self.api_key = api_key or ""
        self.api_secret = api_secret or ""
        self.passphrase = passphrase or ""
        self.broker_name = broker_name or "Binance"
        self.testnet = testnet
        self.definition = definition
        defaults = self.DEFAULTS.get(self.broker_name, self.DEFAULTS["Binance"])
        self.kind = getattr(definition, "kind", None) or defaults["kind"]
        self.market_url = (getattr(definition, "market_data_url", None) or defaults["market"]).rstrip("/")
        self.trading_url = (getattr(definition, "trading_api_url", None) or defaults["trading"]).rstrip("/")
        if self.testnet and self.kind == "binance":
            self.market_url = "https://testnet.binancefuture.com"
            self.trading_url = "https://testnet.binancefuture.com"
        # Backwards-compatible alias used by the original Binance client.
        self.base_url = self.trading_url
        # One limiter per broker (+ credentials) shared by every caller, so the
        # trader, the seeder and the terminal poller share a single budget.
        self.rate_limit_config = rate_limit or default_config_for(self.broker_name, definition)
        self.limiter_key = limiter_key or f"{self.broker_name}:{hashlib.sha1(self.api_key.encode()).hexdigest()[:8]}"
        self.limiter = get_limiter(self.limiter_key, self.rate_limit_config)
        self._instrument_cache: Dict[str, Dict[str, Any]] = {}
        self._last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Symbols & instruments
    # ------------------------------------------------------------------
    @staticmethod
    def normalize_symbol(symbol: str, broker_name: str) -> str:
        value = (symbol or "BTCUSDT").replace("/", "").replace("-", "").upper()
        if broker_name == "Delta" and value.endswith("USDT"):
            return value[:-4] + "USD"
        return value

    # The BTC *perpetual* is the only contract this tool trades; dated futures
    # are never substituted. ``perpetual_symbol`` is the single resolver.
    PERPETUAL_SYMBOLS = {"Binance": "BTCUSDT", "Delta": "BTCUSD"}

    def perpetual_symbol(self, symbol: str = "BTCUSDT") -> str:
        return perpetual_symbol(self.broker_name, symbol)

    @staticmethod
    def _f(value, default=None):
        try:
            if value is None or value == "":
                return default
            number = float(value)
            return default if number != number else number
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------
    # Intervals
    # ------------------------------------------------------------------
    @staticmethod
    def _interval_delta(interval):
        # Delta Exchange rejects numeric/seconds resolution values ("15","60",
        # "240","1D") with HTTP 400. It requires the string label ("15m","1h",
        # "4h","1d"), which matches the app's own interval names.
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

    # ==================================================================
    # Low-level transport (rate limited + 429 aware)
    # ==================================================================
    def _throttled_request(self, method, url, params=None, data=None, headers=None,
                           weight: float = 1.0, is_order: bool = False):
        """One HTTP call inside the rate-limit budget, retrying 429/5xx."""
        cfg = self.rate_limit_config
        last_error = None
        for attempt in range(1, int(max(1, cfg.max_retries)) + 1):
            try:
                self.limiter.acquire(weight=weight, is_order=is_order)
            except RateLimitExceeded as exc:
                self.limiter.note_rejected(str(exc))
                return None, {"error": str(exc)}
            try:
                response = requests.request(method, url, params=params, data=data,
                                            headers=headers, timeout=20)
            except requests.RequestException as exc:
                last_error = f"{self.broker_name} request failed: {exc.__class__.__name__}: {exc}"
                self.limiter.note_rejected(last_error)
                if attempt >= cfg.max_retries:
                    return None, {"error": last_error}
                time.sleep(self.limiter.retry_delay(attempt))
                continue

            self.limiter.note_response(getattr(response, "headers", None), weight)
            if response.status_code == 429:
                self.limiter.note_retry()
                last_error = (f"{self.broker_name} rate limited (HTTP 429)"
                              + (f" — retry after {response.headers.get('Retry-After') or response.headers.get('X-RATE-LIMIT-RESET')}"
                                 if response.headers.get("Retry-After") or response.headers.get("X-RATE-LIMIT-RESET") else ""))
                if attempt >= cfg.max_retries:
                    self.limiter.note_rejected(last_error)
                    return None, {"error": last_error, "rate_limited": True}
                time.sleep(self.limiter.retry_delay(attempt, response.headers))
                continue
            if 500 <= response.status_code < 600:
                last_error = f"{self.broker_name} server error (HTTP {response.status_code})"
                if attempt >= cfg.max_retries:
                    self.limiter.note_rejected(last_error)
                    return None, {"error": last_error}
                time.sleep(self.limiter.retry_delay(attempt, response.headers))
                continue
            return response, None
        return None, {"error": last_error or f"{self.broker_name} request failed"}

    def _json_body(self, response, error):
        """Parse a response body, mapping any failure to an ``error`` dict."""
        if error:
            return error
        try:
            payload = response.json()
        except ValueError:
            body = (response.text or "").strip().replace("\n", " ")[:300]
            return {"error": f"{self.broker_name} returned a non-JSON body (HTTP {response.status_code}): {body}"}
        if response.status_code not in (200, 201):
            message = ""
            if isinstance(payload, dict):
                message = (payload.get("msg") or payload.get("message")
                           or payload.get("error") or "")
                if isinstance(message, dict):
                    message = json.dumps(message)
            return {"error": f"{self.broker_name} HTTP {response.status_code}: {message or str(payload)[:200]}"}
        if isinstance(payload, dict) and payload.get("error"):
            return {"error": f"{self.broker_name}: {payload['error']}"}
        return payload

    # ------------------------------------------------------------------
    # Binance signing
    # ------------------------------------------------------------------
    def _binance_request(self, method, endpoint, params, weight: float = 1.0,
                         is_order: bool = False):
        params = {k: v for k, v in dict(params or {}).items() if v is not None}
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = int(params.get("recvWindow", 5000))
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        signature = hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        headers = {"X-MBX-APIKEY": self.api_key, "Content-Type": "application/x-www-form-urlencoded"}
        url = f"{self.trading_url}{endpoint}"
        if method.upper() == "GET":
            response, error = self._throttled_request("GET", url, params={**params, "signature": signature},
                                                      headers=headers, weight=weight, is_order=is_order)
        else:
            response, error = self._throttled_request(method, url, params={**params, "signature": signature},
                                                      headers=headers, weight=weight, is_order=is_order)
        return response, error

    # ------------------------------------------------------------------
    # Delta signing
    # ------------------------------------------------------------------
    def _delta_request(self, method, path, body=None, query=None, weight: float = 1.0,
                       is_order: bool = False):
        body_text = json.dumps(body or {}, separators=(",", ":")) if body is not None else ""
        timestamp = str(int(time.time()))
        query_text = "&".join(f"{k}={v}" for k, v in sorted((query or {}).items()))
        signature_data = method.upper() + timestamp + path + query_text + body_text
        signature = hmac.new(self.api_secret.encode(), signature_data.encode(), hashlib.sha256).hexdigest()
        headers = {"api-key": self.api_key, "timestamp": timestamp, "signature": signature,
                   "Content-Type": "application/json", "User-Agent": "PHANTOM-Trading-Tool/1.0"}
        url = f"{self.trading_url}{path}"
        response, error = self._throttled_request(method, url, params=query or None,
                                                  data=body_text or None, headers=headers,
                                                  weight=weight, is_order=is_order)
        return response, error

    def _delta_result(self, payload):
        """Unwrap Delta's ``{"success": true, "result": ...}`` envelope."""
        if not isinstance(payload, dict):
            return payload
        if payload.get("error"):
            return {"error": str(payload["error"])}
        return payload.get("result", payload)

    # ==================================================================
    # Public market data
    # ==================================================================
    def fetch_klines(self, symbol="BTCUSDT", interval="1h", limit=500):
        """Return normalized OHLCV dictionaries with UTC-naive datetimes."""
        if self.kind == "binance":
            url = f"{self.market_url}/fapi/v1/klines"
            params = {"symbol": self.normalize_symbol(symbol, "Binance"), "interval": interval, "limit": min(int(limit), 1500)}
            response, error = self._throttled_request("GET", url, params=params, weight=2)
            payload = self._json_body(response, error)
            if isinstance(payload, dict) and "error" in payload:
                raise RuntimeError(payload["error"])
            return [{"event_time": pd.to_datetime(k[0], unit="ms").to_pydatetime(),
                     "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                     "close": float(k[4]), "volume": float(k[5])} for k in payload]
        if self.kind == "delta":
            url = f"{self.market_url}/v2/history/candles"
            params = {"symbol": self.normalize_symbol(symbol, "Delta"), "resolution": self._interval_delta(interval), "limit": min(int(limit), 2000)}
            # `start` and `end` are mandatory for Delta; derive a window from
            # `limit` when the caller didn't pass one (avoids HTTP 400).
            now = int(time.time())
            params.setdefault("start", now - int(self._interval_seconds(interval)) * min(int(limit), 2000))
            params.setdefault("end", now)
            try:
                response, error = self._throttled_request("GET", url, params=params, weight=3)
                payload = self._json_body(response, error)
            except requests.RequestException as exc:
                raise RuntimeError(f"Delta data request failed: {exc}") from exc
            if isinstance(payload, dict) and payload.get("error"):
                raise RuntimeError(f"Delta data request failed: {payload['error']}")
            if response is not None and response.status_code != 200:
                body = (response.text or "").strip().replace("\n", " ")[:300]
                raise RuntimeError(f"Delta data request failed: HTTP {response.status_code} {body}".strip())
            # Delta has answered with several wrappers over the years
            # (bare list, {"result": [...]}, {"candles": [...], "result": null});
            # use the shared parser so this stays in sync with the seeder.
            from app.services.data_sync import DataSyncService
            array, parse_error = DataSyncService._extract_delta_array(payload)
            if parse_error:
                raise RuntimeError(f"Delta data request failed: {parse_error}")
            return DataSyncService._parse_candle_rows(array)
        raise RuntimeError(f"No adapter installed for broker '{self.broker_name}'")

    def fetch_mark_price(self, symbol: str = "BTCUSDT"):
        """Current mark price for the perpetual. ``None`` when unavailable.

        Binance: /fapi/v1/premiumIndex · Delta: /v2/tickers/{symbol}
        The traded (last) price is read alongside it so the fill price and the
        pricing basis are both known at entry.
        """
        perp = self.perpetual_symbol(symbol)
        quote = MarkPriceQuote(self.broker_name, perp)
        try:
            if self.kind == "binance":
                response, error = self._throttled_request(
                    "GET", f"{self.market_url}/fapi/v1/premiumIndex",
                    params={"symbol": perp}, weight=1)
                payload = self._json_body(response, error)
                if isinstance(payload, list):
                    payload = payload[0] if payload else {}
                if isinstance(payload, dict) and "error" not in payload:
                    quote.mark_price = self._f(payload.get("markPrice"))
                    quote.index_price = self._f(payload.get("indexPrice"))
                    quote.raw = payload
                    response2, error2 = self._throttled_request(
                        "GET", f"{self.market_url}/fapi/v1/ticker/price",
                        params={"symbol": perp}, weight=1)
                    ticker = self._json_body(response2, error2)
                    if isinstance(ticker, dict):
                        quote.last_price = self._f(ticker.get("price"))
            elif self.kind == "delta":
                response, error = self._throttled_request(
                    "GET", f"{self.market_url}/v2/tickers/{perp}", weight=1)
                payload = self._json_body(response, error)
                row = self._delta_result(payload)
                if not isinstance(row, dict):
                    return None
                quote.mark_price = self._f(row.get("mark_price"))
                quote.last_price = self._f(row.get("close") or row.get("last_price") or row.get("spot_price"))
                quote.index_price = self._f(row.get("index_price") or row.get("spot_price"))
                quote.raw = row
            else:
                return None
        except Exception as exc:
            print(f"[{self.broker_name}] mark price unavailable: {exc}")
            return None
        if quote.last_price is None:
            quote.last_price = quote.mark_price
        return quote if (quote.mark_price or quote.last_price) else None

    def fetch_mark_price_klines(self, symbol="BTCUSDT", interval="1h", limit=500,
                                start_time=None, end_time=None):
        """Historical mark-price candles (used to seed mark prices)."""
        perp = self.perpetual_symbol(symbol)
        if self.kind == "binance":
            params = {"symbol": perp, "interval": interval, "limit": min(max(1, int(limit)), 1500)}
            if start_time is not None:
                params["startTime"] = int(pd.Timestamp(start_time).timestamp() * 1000)
            if end_time is not None:
                params["endTime"] = int(pd.Timestamp(end_time).timestamp() * 1000)
            response, error = self._throttled_request(
                "GET", f"{self.market_url}/fapi/v1/markPriceKlines", params=params, weight=2)
            payload = self._json_body(response, error)
            if isinstance(payload, dict) and "error" in payload:
                raise RuntimeError(payload["error"])
            return [{"event_time": pd.to_datetime(k[0], unit="ms").to_pydatetime(),
                     "open": float(k[1]), "high": float(k[2]),
                     "low": float(k[3]), "close": float(k[4])} for k in payload]
        if self.kind == "delta":
            from app.services.data_sync import DataSyncService
            return DataSyncService.fetch_mark_klines(self.broker_name, perp, interval,
                                                     start_time, end_time, limit)
        return []

    # ==================================================================
    # Instruments (contract size / tick size) — sizing in the venue's units
    # ==================================================================
    def get_instrument(self, symbol: str = "BTCUSDT", refresh: bool = False):
        """Normalized contract specification for the perpetual.

        Delta sizes orders in **contracts** (an integer count), Binance in base
        asset (BTC) with a step size. ``contract_value`` is how much BTC one
        contract/one lot represents, so a strategy size expressed in BTC can be
        converted for either venue.
        """
        key = self.perpetual_symbol(symbol)
        if not refresh and key in self._instrument_cache:
            return self._instrument_cache[key]
        instrument: Dict[str, Any] = {
            "symbol": key, "source": self.broker_name, "contract_type": "perpetual",
            "contract_value": 1.0, "tick_size": 0.01, "step_size": 0.001,
            "min_size": None, "quote_asset": "USD", "size_unit": "contracts",
            "product_id": None, "price_precision": 2, "quantity_precision": 3,
        }
        try:
            if self.kind == "binance":
                response, error = self._throttled_request(
                    "GET", f"{self.market_url}/fapi/v1/exchangeInfo", weight=1)
                payload = self._json_body(response, error)
                for item in (payload or {}).get("symbols", []) if isinstance(payload, dict) else []:
                    if item.get("symbol") != key:
                        continue
                    instrument["quote_asset"] = item.get("quoteAsset", "USDT")
                    instrument["contract_type"] = (item.get("contractType") or "PERPETUAL").lower()
                    for filt in item.get("filters", []):
                        if filt.get("filterType") == "PRICE_FILTER":
                            instrument["tick_size"] = self._f(filt.get("tickSize"), 0.01)
                        if filt.get("filterType") == "LOT_SIZE":
                            instrument["step_size"] = self._f(filt.get("stepSize"), 0.001)
                            instrument["min_size"] = self._f(filt.get("minQty"), instrument["step_size"])
                    instrument["price_precision"] = int(item.get("pricePrecision", 2) or 2)
                    instrument["quantity_precision"] = int(item.get("quantityPrecision", 3) or 3)
                    instrument["contract_value"] = 1.0
                    instrument["size_unit"] = item.get("baseAsset", "BTC")
                    break
            elif self.kind == "delta":
                response, error = self._throttled_request(
                    "GET", f"{self.market_url}/v2/products/{key}", weight=1)
                payload = self._json_body(response, error)
                row = self._delta_result(payload)
                if isinstance(row, dict):
                    instrument.update({
                        "product_id": row.get("id"),
                        "contract_value": self._f(row.get("contract_value"), 1.0),
                        "tick_size": self._f(row.get("tick_size"), 0.01),
                        "step_size": 1.0,            # Delta sizes in whole contracts
                        "min_size": 1.0,
                        "quote_asset": row.get("quoting_asset", {}).get("symbol", "USD")
                        if isinstance(row.get("quoting_asset"), dict) else "USD",
                        "size_unit": "contracts",
                        "contract_type": str(row.get("contract_type", "perpetual_futures")),
                        "notional_type": row.get("contract_unit_currency"),
                    })
        except Exception as exc:
            print(f"[{self.broker_name}] instrument lookup failed for {key}: {exc}")
        self._instrument_cache[key] = instrument
        return instrument

    def base_to_venue_size(self, quantity_btc: float, symbol: str = "BTCUSDT") -> float:
        """Convert a BTC quantity into the venue's order size (contracts/lots)."""
        instrument = self.get_instrument(symbol)
        raw = float(quantity_btc) / float(instrument.get("contract_value") or 1.0)
        step = float(instrument.get("step_size") or 1.0)
        size = round(raw / step) * step
        minimum = instrument.get("min_size")
        if minimum and size < float(minimum):
            return 0.0
        precision = int(instrument.get("quantity_precision", 3) or 3)
        if instrument.get("size_unit") == "contracts":
            return float(int(round(size)))
        return float(round(size, precision))

    def venue_to_base_size(self, size: float, symbol: str = "BTCUSDT") -> float:
        """Inverse of :meth:`base_to_venue_size` (venue size → BTC)."""
        instrument = self.get_instrument(symbol)
        return float(size) * float(instrument.get("contract_value") or 1.0)

    # ==================================================================
    # Orders
    # ==================================================================
    def place_order(self, symbol: str, side: str, order_type: str, qty: float,
                    price: Optional[float] = None, stop_price: Optional[float] = None,
                    reduce_only: bool = False, client_order_id: Optional[str] = None,
                    time_in_force: str = "GTC", post_only: bool = False,
                    working_type: str = "MARK_PRICE", stop_side: Optional[str] = None,
                    trail_amount: Optional[float] = None, size_in_btc: bool = False):
        """Place an order. ``qty`` is in the venue's own units unless
        ``size_in_btc`` is set, in which case it is converted for you.

        ``order_type`` accepts the terminal's names: ``market``, ``limit``,
        ``stop_market``, ``stop_limit``, ``take_profit_market``,
        ``take_profit_limit``, ``trailing_stop`` — or the venue's native names
        (``MARKET``, ``LIMIT``, ``STOP_MARKET``, ``market_order`` …).
        """
        perp = self.perpetual_symbol(symbol)
        size = self.base_to_venue_size(qty, symbol) if size_in_btc else float(qty)
        if size <= 0:
            return {"error": f"Order size too small: {qty} BTC is below {self.get_instrument(symbol).get('min_size')} "
                             f"{self.get_instrument(symbol).get('size_unit')}"}
        if self.kind == "binance":
            return self._binance_place_order(perp, side, order_type, size, price, stop_price,
                                             reduce_only, client_order_id, time_in_force,
                                             post_only, working_type, trail_amount)
        if self.kind == "delta":
            return self._delta_place_order(perp, side, order_type, size, price, stop_price,
                                           reduce_only, client_order_id, time_in_force,
                                           post_only, stop_side, trail_amount)
        return {"error": f"No order adapter installed for '{self.broker_name}'"}

    def _binance_place_order(self, symbol, side, order_type, size, price, stop_price,
                             reduce_only, client_order_id, time_in_force, post_only,
                             working_type, trail_amount):
        native = self.BINANCE_ORDER_TYPES.get(str(order_type).lower(), str(order_type).upper())
        params = {"symbol": symbol, "side": str(side).upper(), "type": native, "quantity": size}
        if native in ("LIMIT", "STOP", "TAKE_PROFIT"):
            params["price"] = price
            params["timeInForce"] = "GTC" if (post_only or time_in_force == "GTX") else time_in_force
            if post_only:
                params["timeInForce"] = "GTX"
        if native in ("STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET"):
            if stop_price is None:
                return {"error": f"{native} requires a stop_price"}
            params["stopPrice"] = stop_price
            # Documented enum is MARK_PRICE / CONTRACT_PRICE; "MARK" and
            # "CONTRACT" are accepted by callers and mapped here.
            params["workingType"] = self.BINANCE_WORKING_TYPES.get(
                str(working_type or "MARK_PRICE").upper(), "MARK_PRICE")
            params["priceProtect"] = "TRUE"
        if native == "TRAILING_STOP_MARKET":
            params["callbackRate"] = trail_amount
            params["activationPrice"] = stop_price
        if reduce_only:
            params["reduceOnly"] = "true"
        if client_order_id:
            params["newClientOrderId"] = str(client_order_id)[:36]
        response, error = self._binance_request("POST", "/fapi/v1/order", params,
                                                weight=2, is_order=True)
        return self._json_body(response, error)

    def _delta_place_order(self, symbol, side, order_type, size, price, stop_price,
                           reduce_only, client_order_id, time_in_force, post_only,
                           stop_side, trail_amount, size_in_btc: bool = False):
        size = self.base_to_venue_size(size, symbol) if size_in_btc else float(size)
        body: Dict[str, Any] = {
            "product_symbol": symbol,
            "size": int(round(size)),
            "side": str(side).lower(),
        }
        lowered = str(order_type).lower()
        is_stop = "stop" in lowered or trail_amount is not None
        if lowered in ("market", "market_order"):
            body["order_type"] = "market_order"
            if is_stop:
                body["order_type"] = "market_order"
                body["stop_order_type"] = "stop_loss_order" if stop_side != "take_profit" else "take_profit_order"
        else:
            body["order_type"] = "limit_order"
            body["limit_price"] = str(price)
            body["time_in_force"] = "gtc" if str(time_in_force).upper() in ("GTC", "GTX") else "ioc"
            if is_stop:
                body["stop_order_type"] = "stop_loss_order" if stop_side != "take_profit" else "take_profit_order"
        if is_stop:
            if stop_price is None and trail_amount is None:
                return {"error": "stop orders require a stop_price (or trail_amount)"}
            if stop_price is not None:
                body["stop_price"] = str(stop_price)
            if trail_amount is not None:
                body["trail_amount"] = str(trail_amount)
            # Price risk is managed on the mark price (see app.core.mark_price).
            body["stop_trigger_method"] = "mark_price"
        if reduce_only:
            body["reduce_only"] = "true"
        if post_only:
            body["post_only"] = "true"
        if client_order_id:
            body["client_order_id"] = str(client_order_id)[:32]
        response, error = self._delta_request("POST", "/v2/orders", body=body,
                                              weight=10, is_order=True)
        return self._delta_result(self._json_body(response, error))

    def place_bracket_order(self, symbol: str, side: str, qty: float,
                            price: Optional[float] = None,
                            stop_loss_price: Optional[float] = None,
                            take_profit_price: Optional[float] = None,
                            client_order_id: Optional[str] = None,
                            trigger_method: str = "mark_price", size_in_btc: bool = True):
        """Entry order with an attached stop-loss and take-profit.

        Delta supports this natively (``POST /v2/orders/bracket``) and cancels
        the unused leg when the other fills. Binance has no bracket endpoint,
        so the entry is sent first and the two protection legs are placed as
        reduce-only STOP_MARKET / TAKE_PROFIT_MARKET orders afterwards.
        """
        perp = self.perpetual_symbol(symbol)
        size = self.base_to_venue_size(qty, symbol) if size_in_btc else float(qty)
        if size <= 0:
            return {"error": "Order size too small for the contract's minimum"}
        close_side = "sell" if str(side).lower() == "buy" else "buy"
        if self.kind == "delta":
            body: Dict[str, Any] = {
                "product_symbol": perp,
                "size": int(round(size)),
                "side": str(side).lower(),
                "order_type": "market_order" if price is None else "limit_order",
                "bracket_stop_trigger_method": trigger_method or "mark_price",
            }
            if price is not None:
                body["limit_price"] = str(price)
            if stop_loss_price is not None:
                body["stop_loss_order"] = {"order_type": "market_order",
                                           "stop_price": str(stop_loss_price)}
            if take_profit_price is not None:
                body["take_profit_order"] = {"order_type": "market_order",
                                             "stop_price": str(take_profit_price)}
            if client_order_id:
                body["client_order_id"] = str(client_order_id)[:32]
            response, error = self._delta_request("POST", "/v2/orders/bracket", body=body,
                                                  weight=20, is_order=True)
            payload = self._json_body(response, error)
            if isinstance(payload, dict) and not payload.get("error"):
                payload["_bracket"] = True
            return payload
        if self.kind == "binance":
            entry = self.place_order(symbol, side, "market" if price is None else "limit",
                                     size, price=price, client_order_id=client_order_id)
            if isinstance(entry, dict) and entry.get("error"):
                return entry
            legs: List[Dict[str, Any]] = []
            if stop_loss_price is not None:
                legs.append(self.place_order(symbol, close_side, "stop_market", size,
                                             stop_price=stop_loss_price, reduce_only=True,
                                             working_type="MARK_PRICE"))
            if take_profit_price is not None:
                legs.append(self.place_order(symbol, close_side, "take_profit_market", size,
                                             stop_price=take_profit_price, reduce_only=True,
                                             working_type="MARK_PRICE"))
            return {"entry": entry, "legs": legs, "_bracket": True,
                    "note": "Binance has no native bracket order; protection legs placed as reduce-only stops."}
        return {"error": f"No bracket-order adapter installed for '{self.broker_name}'"}

    def cancel_order(self, order_id, symbol: Optional[str] = None,
                     client_order_id: Optional[str] = None):
        perp = self.perpetual_symbol(symbol) if symbol else None
        if self.kind == "binance":
            params = {"symbol": perp or ""}
            if client_order_id:
                params["origClientOrderId"] = client_order_id
            else:
                params["orderId"] = order_id
            response, error = self._binance_request("DELETE", "/fapi/v1/order", params,
                                                    weight=1, is_order=True)
            return self._json_body(response, error)
        if self.kind == "delta":
            if client_order_id and not order_id:
                response, error = self._delta_request("DELETE", "/v2/orders/client",
                                                      query={"client_order_id": client_order_id},
                                                      weight=5, is_order=True)
            else:
                response, error = self._delta_request("DELETE", f"/v2/orders/{order_id}",
                                                      weight=5, is_order=True)
            return self._delta_result(self._json_body(response, error))
        return {"error": f"No cancel adapter installed for '{self.broker_name}'"}

    def cancel_all_orders(self, symbol: Optional[str] = None):
        perp = self.perpetual_symbol(symbol) if symbol else None
        if self.kind == "binance":
            params = {"symbol": perp} if perp else {}
            response, error = self._binance_request("DELETE", "/fapi/v1/allOpenOrders", params,
                                                    weight=1, is_order=True)
            return self._json_body(response, error)
        if self.kind == "delta":
            body = {"product_symbol": perp} if perp else {}
            response, error = self._delta_request("DELETE", "/v2/orders/all", body=body,
                                                  weight=5, is_order=True)
            return self._delta_result(self._json_body(response, error))
        return {"error": f"No cancel adapter installed for '{self.broker_name}'"}

    def edit_order(self, order_id, symbol: Optional[str] = None, price: Optional[float] = None,
                   size: Optional[float] = None, stop_price: Optional[float] = None,
                   client_order_id: Optional[str] = None):
        perp = self.perpetual_symbol(symbol) if symbol else None
        if self.kind == "delta":
            body: Dict[str, Any] = {"id": int(order_id)}
            if perp:
                body["product_symbol"] = perp
            if price is not None:
                body["limit_price"] = str(price)
            if size is not None:
                body["size"] = int(round(size))
            if stop_price is not None:
                body["stop_price"] = str(stop_price)
            response, error = self._delta_request("PUT", "/v2/orders", body=body,
                                                  weight=10, is_order=True)
            return self._delta_result(self._json_body(response, error))
        return {"error": "Editing an order is only supported on Delta Exchange; cancel and replace instead."}

    def get_open_orders(self, symbol: Optional[str] = None):
        perp = self.perpetual_symbol(symbol) if symbol else None
        if self.kind == "binance":
            params = {"symbol": perp} if perp else {}
            response, error = self._binance_request("GET", "/fapi/v1/openOrders", params, weight=1)
            payload = self._json_body(response, error)
            return payload if isinstance(payload, list) else ([payload] if payload.get("error") is None else payload)
        if self.kind == "delta":
            query = {"product_symbol": perp} if perp else {}
            response, error = self._delta_request("GET", "/v2/orders", query=query, weight=5)
            payload = self._json_body(response, error)
            result = self._delta_result(payload)
            return result if isinstance(result, list) else ([] if isinstance(result, dict) and result.get("error") is None else result)
        return {"error": f"No order adapter installed for '{self.broker_name}'"}

    def get_order_history(self, symbol: Optional[str] = None, limit: int = 100,
                          start_time=None, end_time=None):
        perp = self.perpetual_symbol(symbol) if symbol else None
        if self.kind == "binance":
            params = {"limit": min(max(1, int(limit)), 1000)}
            if perp:
                params["symbol"] = perp
            if start_time is not None:
                params["startTime"] = int(pd.Timestamp(start_time).timestamp() * 1000)
            if end_time is not None:
                params["endTime"] = int(pd.Timestamp(end_time).timestamp() * 1000)
            response, error = self._binance_request("GET", "/fapi/v1/allOrders", params, weight=5)
            payload = self._json_body(response, error)
            return payload if isinstance(payload, list) else payload
        if self.kind == "delta":
            query: Dict[str, Any] = {"page_size": min(max(1, int(limit)), 100)}
            if perp:
                query["product_symbol"] = perp
            if start_time is not None:
                query["start_time"] = int(pd.Timestamp(start_time).timestamp())
            if end_time is not None:
                query["end_time"] = int(pd.Timestamp(end_time).timestamp())
            response, error = self._delta_request("GET", "/v2/orders/history", query=query, weight=5)
            payload = self._json_body(response, error)
            return self._delta_result(payload)
        return {"error": f"No order adapter installed for '{self.broker_name}'"}

    def get_order(self, order_id=None, client_order_id: Optional[str] = None):
        if self.kind == "binance":
            params = {}
            if client_order_id:
                params["origClientOrderId"] = client_order_id
            else:
                params["orderId"] = order_id
            response, error = self._binance_request("GET", "/fapi/v1/order", params, weight=1)
            return self._json_body(response, error)
        if self.kind == "delta":
            if client_order_id:
                response, error = self._delta_request("GET", "/v2/orders/client",
                                                      query={"client_order_id": client_order_id}, weight=5)
            else:
                response, error = self._delta_request("GET", f"/v2/orders/{order_id}", weight=5)
            payload = self._json_body(response, error)
            return self._delta_result(payload)
        return {"error": f"No order adapter installed for '{self.broker_name}'"}

    # ==================================================================
    # Fills / executions
    # ==================================================================
    def get_fills(self, symbol: Optional[str] = None, limit: int = 100,
                  start_time=None, end_time=None):
        perp = self.perpetual_symbol(symbol) if symbol else None
        if self.kind == "binance":
            params = {"limit": min(max(1, int(limit)), 1000)}
            if perp:
                params["symbol"] = perp
            if start_time is not None:
                params["startTime"] = int(pd.Timestamp(start_time).timestamp() * 1000)
            if end_time is not None:
                params["endTime"] = int(pd.Timestamp(end_time).timestamp() * 1000)
            response, error = self._binance_request("GET", "/fapi/v1/userTrades", params, weight=5)
            payload = self._json_body(response, error)
            return payload if isinstance(payload, list) else payload
        if self.kind == "delta":
            query: Dict[str, Any] = {"page_size": min(max(1, int(limit)), 100)}
            if perp:
                query["product_symbol"] = perp
            if start_time is not None:
                query["start_time"] = int(pd.Timestamp(start_time).timestamp())
            if end_time is not None:
                query["end_time"] = int(pd.Timestamp(end_time).timestamp())
            response, error = self._delta_request("GET", "/v2/fills", query=query, weight=5)
            payload = self._json_body(response, error)
            return self._delta_result(payload)
        return {"error": f"No fills adapter installed for '{self.broker_name}'"}

    # ==================================================================
    # Positions & margin
    # ==================================================================
    def get_positions(self, symbol: Optional[str] = None):
        perp = self.perpetual_symbol(symbol) if symbol else None
        if self.kind == "binance":
            params = {"symbol": perp} if perp else {}
            response, error = self._binance_request("GET", "/fapi/v2/positionRisk", params, weight=5)
            payload = self._json_body(response, error)
            if isinstance(payload, list):
                return [row for row in payload if self._f(row.get("positionAmt"), 0.0)]
            return payload
        if self.kind == "delta":
            query = {"product_symbol": perp} if perp else {}
            response, error = self._delta_request("GET", "/v2/positions/margined", query=query, weight=5)
            payload = self._json_body(response, error)
            return self._delta_result(payload)
        return {"error": f"No position adapter installed for '{self.broker_name}'"}

    def close_position(self, symbol: str = "BTCUSDT", size: Optional[float] = None,
                       size_in_btc: bool = True):
        """Market-close the open position for a contract (reduce-only).

        With ``size`` the close is partial (a reduce-only market order); without
        it the whole position is flattened through the venue's own close-all
        endpoint.
        """
        perp = self.perpetual_symbol(symbol)
        if self.kind == "binance":
            positions = self.get_positions(symbol)
            amount = 0.0
            if isinstance(positions, list) and positions:
                amount = self._f(positions[0].get("positionAmt"), 0.0) or 0.0
            side = "SELL" if amount > 0 else "BUY"
            if size is None:
                params = {"symbol": perp, "side": side, "type": "MARKET",
                          "closePosition": "true"}
            else:
                quantity = self.base_to_venue_size(size, symbol) if size_in_btc else float(size)
                if quantity <= 0:
                    return {"error": "Close size is below the contract minimum"}
                params = {"symbol": perp, "side": side, "type": "MARKET",
                          "quantity": quantity, "reduceOnly": "true"}
            response, error = self._binance_request("POST", "/fapi/v1/order", params,
                                                    weight=2, is_order=True)
            return self._json_body(response, error)
        if self.kind == "delta":
            if size is None:
                body = {"product_symbol": perp, "close_all": "true"}
                response, error = self._delta_request("POST", "/v2/positions/close_all", body=body,
                                                      weight=20, is_order=True)
                return self._delta_result(self._json_body(response, error))
            return self._delta_place_order(perp, "sell", "market", size, None, None, True,
                                           None, "gtc", False, None, None,
                                           size_in_btc=size_in_btc)
        return {"error": f"No position adapter installed for '{self.broker_name}'"}

    def change_position_margin(self, symbol: str, amount: float, symbol_arg=None):
        """Add (positive) or remove (negative) isolated-margin from a position."""
        perp = self.perpetual_symbol(symbol)
        if self.kind == "binance":
            params = {"symbol": perp, "amount": float(amount), "type": 1 if amount > 0 else 2}
            response, error = self._binance_request("POST", "/fapi/v1/positionMargin", params,
                                                    weight=2, is_order=True)
            return self._json_body(response, error)
        if self.kind == "delta":
            body = {"product_symbol": perp, "delta_margin": str(float(amount))}
            response, error = self._delta_request("POST", "/v2/positions/change_margin", body=body,
                                                  weight=10, is_order=True)
            return self._delta_result(self._json_body(response, error))
        return {"error": f"No position adapter installed for '{self.broker_name}'"}

    # ==================================================================
    # Wallet / account / risk
    # ==================================================================
    def get_account_balance(self, asset: str = "USDT"):
        if self.kind == "binance":
            response, error = self._binance_request("GET", "/fapi/v2/account", {}, weight=5)
            return self._json_body(response, error)
        if self.kind == "delta":
            response, error = self._delta_request("GET", "/v2/wallet/balances", weight=5)
            payload = self._json_body(response, error)
            return self._delta_result(payload)
        return {"error": f"No account adapter installed for '{self.broker_name}'"}

    def set_leverage(self, symbol: str, leverage: int):
        perp = self.perpetual_symbol(symbol)
        if self.kind == "binance":
            response, error = self._binance_request("POST", "/fapi/v1/leverage",
                                                    {"symbol": perp, "leverage": int(leverage)},
                                                    weight=1, is_order=True)
            return self._json_body(response, error)
        if self.kind == "delta":
            body = {"product_symbol": perp, "leverage": int(leverage)}
            response, error = self._delta_request("POST", "/v2/orders/leverage", body=body,
                                                  weight=10, is_order=True)
            return self._delta_result(self._json_body(response, error))
        return {"error": f"No leverage adapter installed for '{self.broker_name}'"}

    def get_leverage(self, symbol: str):
        perp = self.perpetual_symbol(symbol)
        if self.kind == "binance":
            response, error = self._binance_request("GET", "/fapi/v1/positionRisk",
                                                    {"symbol": perp}, weight=5)
            payload = self._json_body(response, error)
            if isinstance(payload, list) and payload:
                return {"leverage": self._f(payload[0].get("leverage")),
                        "margin_type": payload[0].get("marginType")}
            return payload
        if self.kind == "delta":
            response, error = self._delta_request("GET", "/v2/orders/leverage",
                                                  query={"product_symbol": perp}, weight=5)
            payload = self._json_body(response, error)
            return self._delta_result(payload)
        return {"error": f"No leverage adapter installed for '{self.broker_name}'"}

    def set_margin_mode(self, symbol: str, mode: str = "isolated"):
        perp = self.perpetual_symbol(symbol)
        if self.kind == "binance":
            response, error = self._binance_request("POST", "/fapi/v1/marginType",
                                                    {"symbol": perp, "marginType": str(mode).upper()},
                                                    weight=1, is_order=True)
            return self._json_body(response, error)
        if self.kind == "delta":
            body = {"product_symbol": perp, "margin_mode": str(mode).lower()}
            response, error = self._delta_request("POST", "/v2/positions/margin_mode", body=body,
                                                  weight=10, is_order=True)
            return self._delta_result(self._json_body(response, error))
        return {"error": f"No margin-mode adapter installed for '{self.broker_name}'"}

    # ==================================================================
    # Rate limits
    # ==================================================================
    def rate_limit_usage(self) -> dict:
        """Local usage plus whatever the venue has told us about the budget."""
        snapshot = self.limiter.snapshot()
        snapshot.update({
            "broker": self.broker_name,
            "limits": self.rate_limit_config.as_dict(),
        })
        return snapshot

    def fetch_rate_limit_quota(self):
        """Delta's remaining quota for the current 5-minute window."""
        if self.kind == "delta":
            response, error = self._throttled_request(
                "GET", f"{self.market_url}/v2/rate_limits/quota", weight=1)
            payload = self._json_body(response, error)
            result = self._delta_result(payload)
            if isinstance(result, dict):
                self.limiter.note_quota(self._f(result.get("current_quota")),
                                        self._f(result.get("remaining_time_in_milliseconds")))
            return result
        return {"note": "Binance reports usage through the X-MBX-USED-WEIGHT-1M response header",
                "used_weight_1m": self.limiter.snapshot().get("exchange_weight")}

    # ==================================================================
    # Backwards-compatible entry point used by older code paths
    # ==================================================================
    def request(self, method: str, endpoint: str, params: dict = None) -> dict:
        try:
            if self.kind == "binance":
                response, error = self._binance_request(method, endpoint, params)
                return self._json_body(response, error)
            if self.kind == "delta":
                response, error = self._delta_request(method, endpoint, body=params or {})
                return self._json_body(response, error)
            return {"error": f"No authenticated adapter installed for '{self.broker_name}'"}
        except Exception as exc:
            print(f"Broker API Error [{self.broker_name}]: {exc}")
            return {"error": str(exc)}
