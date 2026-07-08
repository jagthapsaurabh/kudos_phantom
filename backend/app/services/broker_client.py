import hmac
import hashlib
import time
import requests
import numpy as np
from typing import Optional

class BrokerClient:
    """
    Universal Broker Client for executing LIVE trades.
    Handles signing requests and authentication for Binance and similar brokers.
    """
    def __init__(self, api_key: str, api_secret: str, broker_name: str = "Binance"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.broker_name = broker_name
        self.base_url = "https://fapi.binance.com" if broker_name == "Binance" else "https://api.broker.com"

    def _generate_signature(self, query_string: str) -> str:
        return hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    def request(self, method: str, endpoint: str, params: dict = None) -> dict:
        if params is None: params = {}
        
        # Add timestamp for authentication
        params['timestamp'] = int(time.time() * 1000)
        
        # Generate signature
        query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
        signature = self._generate_signature(query_string)
        params['signature'] = signature
        
        url = f"{self.base_url}{endpoint}"
        
        try:
            if method == "GET":
                response = requests.get(url, params=params, headers={"X-MBX-APIKEY": self.api_key}, timeout=10)
            else:
                response = requests.post(url, params=params, headers={"X-MBX-APIKEY": self.api_key}, timeout=10)
            
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Broker API Error: {e}")
            return {"error": str(e)}

    def place_order(self, symbol: str, side: str, order_type: str, qty: float, price: Optional[float] = None):
        endpoint = "/fapi/v1/order"
        params = {
            "symbol": symbol,
            "side": side, # BUY or SELL
            "type": order_type, # LIMIT or MARKET
            "quantity": qty,
        }
        if price: params["price"] = price
        
        return self.request("POST", endpoint, params)

    def get_account_balance(self, asset: str = "USDT"):
        endpoint = "/fapi/v2/account"
        return self.request("GET", endpoint)
