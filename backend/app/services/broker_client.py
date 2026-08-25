"""Broker adapters used by live trading and market-data fallback."""
import hashlib
import hmac
import json
import time
from typing import Optional
from urllib.parse import urlencode

import requests
import pandas as pd


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

    def __init__(self, api_key: str = "", api_secret: str = "", broker_name: str = "Binance",
                 passphrase: str = "", testnet: bool = False, definition=None):
        self.api_key = api_key or ""
        self.api_secret = api_secret or ""
        self.passphrase = passphrase or ""
        self.broker_name = broker_name or "Binance"
        self.testnet = testnet
        defaults = self.DEFAULTS.get(self.broker_name, self.DEFAULTS["Binance"])
        self.kind = getattr(definition, "kind", None) or defaults["kind"]
        self.market_url = (getattr(definition, "market_data_url", None) or defaults["market"]).rstrip("/")
        self.trading_url = (getattr(definition, "trading_api_url", None) or defaults["trading"]).rstrip("/")
        if self.testnet and self.kind == "binance":
            self.market_url = "https://testnet.binancefuture.com"
            self.trading_url = "https://testnet.binancefuture.com"
        # Backwards-compatible alias used by the original Binance client.
        self.base_url = self.trading_url

    def _generate_signature(self, query_string: str) -> str:
        return hmac.new(self.api_secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def normalize_symbol(symbol: str, broker_name: str) -> str:
        value = (symbol or "BTCUSDT").replace("/", "").replace("-", "").upper()
        if broker_name == "Delta" and value.endswith("USDT"):
            return value[:-4] + "USD"
        return value

    @staticmethod
    def _interval_delta(interval):
        return {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "1D"}.get(interval, interval)

    def fetch_klines(self, symbol="BTCUSDT", interval="1h", limit=500):
        """Return normalized OHLCV dictionaries with UTC-naive datetimes."""
        if self.kind == "binance":
            url = f"{self.market_url}/fapi/v1/klines"
            params = {"symbol": self.normalize_symbol(symbol, "Binance"), "interval": interval, "limit": min(int(limit), 1500)}
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            raw = response.json()
            return [{"event_time": pd.to_datetime(k[0], unit="ms").to_pydatetime(),
                     "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                     "close": float(k[4]), "volume": float(k[5])} for k in raw]
        if self.kind == "delta":
            url = f"{self.market_url}/v2/history/candles"
            params = {"symbol": self.normalize_symbol(symbol, "Delta"), "resolution": self._interval_delta(interval), "limit": min(int(limit), 2000)}
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            payload = response.json()
            raw = payload.get("result", payload) if isinstance(payload, dict) else payload
            rows = []
            for k in raw or []:
                if isinstance(k, dict):
                    ts = k.get("time", k.get("timestamp"))
                    rows.append({"event_time": pd.to_datetime(float(ts), unit="s").to_pydatetime(),
                                 "open": float(k["open"]), "high": float(k["high"]), "low": float(k["low"]),
                                 "close": float(k["close"]), "volume": float(k.get("volume", 0) or 0)})
            return rows
        raise RuntimeError(f"No adapter installed for broker '{self.broker_name}'")

    def _binance_request(self, method, endpoint, params):
        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(sorted(params.items()))
        params["signature"] = hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        headers = {"X-MBX-APIKEY": self.api_key}
        response = requests.request(method, f"{self.trading_url}{endpoint}", params=params, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json()

    def _delta_request(self, method, path, body=None, query=None):
        body_text = json.dumps(body or {}, separators=(",", ":"))
        timestamp = str(int(time.time()))
        query_text = urlencode(sorted((query or {}).items()))
        signature_data = method.upper() + timestamp + path + query_text + body_text
        signature = hmac.new(self.api_secret.encode(), signature_data.encode(), hashlib.sha256).hexdigest()
        headers = {"api-key": self.api_key, "timestamp": timestamp, "signature": signature,
                   "Content-Type": "application/json", "User-Agent": "PHANTOM-Trading-Tool/1.0"}
        response = requests.request(method, f"{self.trading_url}{path}", params=query, data=body_text,
                                    headers=headers, timeout=15)
        response.raise_for_status()
        return response.json()

    def request(self, method: str, endpoint: str, params: dict = None) -> dict:
        try:
            if self.kind == "binance":
                return self._binance_request(method, endpoint, params)
            if self.kind == "delta":
                return self._delta_request(method, endpoint, body=params or {})
            return {"error": f"No authenticated adapter installed for '{self.broker_name}'"}
        except Exception as exc:
            print(f"Broker API Error [{self.broker_name}]: {exc}")
            return {"error": str(exc)}

    def place_order(self, symbol: str, side: str, order_type: str, qty: float, price: Optional[float] = None):
        if self.kind == "binance":
            params = {"symbol": self.normalize_symbol(symbol, "Binance"), "side": side.upper(),
                      "type": order_type.upper(), "quantity": qty}
            if price is not None:
                params["price"] = price
                params["timeInForce"] = "GTC"
            return self.request("POST", "/fapi/v1/order", params)
        if self.kind == "delta":
            # Delta uses product symbols and contract size (size is an integer
            # for most perpetuals; round up from the strategy's BTC quantity).
            body = {"product_symbol": self.normalize_symbol(symbol, "Delta"),
                    "size": max(1, int(round(qty))), "side": side.lower(),
                    "order_type": "market_order" if order_type.upper() == "MARKET" else "limit_order"}
            if price is not None:
                body["limit_price"] = str(price)
            return self.request("POST", "/v2/orders", body)
        return {"error": f"No order adapter installed for '{self.broker_name}'"}

    def get_account_balance(self, asset: str = "USDT"):
        if self.kind == "binance":
            return self.request("GET", "/fapi/v2/account", {})
        if self.kind == "delta":
            return self.request("GET", "/v2/wallet/balances", {})
        return {"error": f"No account adapter installed for '{self.broker_name}'"}
