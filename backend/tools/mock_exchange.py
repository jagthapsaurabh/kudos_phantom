"""Throwaway mock of the Binance Futures REST surface.

Used to smoke-test the live-order endpoints without touching a real exchange:

    ../.venv/bin/python tools/mock_exchange.py --port 8099

It answers just enough of /fapi/v1/* for the terminal: exchangeInfo, mark
price, open orders, order history, fills, positionRisk, account and the
order/cancel endpoints. Nothing is persisted beyond the process.
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

SEQ = {"order": 5000}


def order(**overrides):
    SEQ["order"] += 1
    row = {
        "orderId": SEQ["order"], "symbol": "BTCUSDT", "status": "NEW",
        "clientOrderId": f"mock-{SEQ['order']}", "price": "0", "avgPrice": "0",
        "origQty": "0.001", "executedQty": "0", "type": "MARKET",
        "side": "BUY", "reduceOnly": False, "updateTime": int(time.time() * 1000),
    }
    row.update(overrides)
    return row


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self, method):
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        now_ms = int(time.time() * 1000)

        if path == "/fapi/v1/exchangeInfo":
            return self._send({"symbols": [{
                "symbol": "BTCUSDT", "quoteAsset": "USDT", "baseAsset": "BTC",
                "contractType": "PERPETUAL", "pricePrecision": 2, "quantityPrecision": 3,
                "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"}],
            }]})
        if path == "/fapi/v1/premiumIndex":
            return self._send({"symbol": "BTCUSDT", "markPrice": "67100.50", "indexPrice": "67090.10"})
        if path == "/fapi/v1/ticker/price":
            return self._send({"symbol": "BTCUSDT", "price": "67105.00"})
        if path == "/fapi/v1/order" and method == "POST":
            kind = query.get("type", ["MARKET"])[0]
            if kind == "STOP_MARKET":
                return self._send(order(type="STOP_MARKET", side=query.get("side", ["SELL"])[0],
                                        origQty=query.get("quantity", ["0"])[0],
                                        stopPrice=query.get("stopPrice", ["0"])[0],
                                        reduceOnly=query.get("reduceOnly", [None])[0] == "true",
                                        workingType=query.get("workingType", [None])[0]))
            if kind == "TAKE_PROFIT_MARKET":
                return self._send(order(type="TAKE_PROFIT_MARKET", side=query.get("side", ["SELL"])[0],
                                        origQty=query.get("quantity", ["0"])[0],
                                        stopPrice=query.get("stopPrice", ["0"])[0],
                                        reduceOnly=query.get("reduceOnly", [None])[0] == "true"))
            return self._send(order(type=kind, side=query.get("side", ["BUY"])[0],
                                    origQty=query.get("quantity", ["0"])[0],
                                    newClientOrderId=query.get("newClientOrderId", [None])[0],
                                    closePosition=query.get("closePosition", [None])[0]))
        if path == "/fapi/v1/order" and method == "DELETE":
            return self._send(order(status="CANCELED", orderId=int(query.get("orderId", [0])[0] or 0)))
        if path == "/fapi/v1/allOpenOrders" and method == "DELETE":
            return self._send({"code": 200, "msg": "success"})
        if path == "/fapi/v1/openOrders":
            return self._send([order(status="NEW", type="LIMIT", price="60000", origQty="0.030"),
                               order(status="NEW", type="STOP_MARKET", stopPrice="65000",
                                     origQty="0.030", reduceOnly=True, side="SELL")])
        if path == "/fapi/v1/allOrders":
            return self._send([order(status="FILLED", type="MARKET", executedQty="0.030",
                                     avgPrice="67000.50", updateTime=now_ms - 3600_000)])
        if path == "/fapi/v1/userTrades":
            return self._send([{"id": 70001, "orderId": 5001, "symbol": "BTCUSDT", "side": "BUY",
                                "qty": "0.030", "price": "67000.50", "commission": "-0.0201",
                                "maker": False, "realizedPnl": "0", "time": now_ms - 3600_000}])
        if path == "/fapi/v2/positionRisk":
            return self._send([{"symbol": "BTCUSDT", "positionAmt": "0.030",
                                "entryPrice": "67000.50", "markPrice": "67100.50",
                                "unRealizedProfit": "3.00", "liquidationPrice": "54000.00",
                                "leverage": "10", "marginType": "isolated", "isolatedWallet": "20.10"}])
        if path == "/fapi/v2/account":
            return self._send({"assets": [{"asset": "USDT", "walletBalance": "1000.00",
                                           "availableBalance": "940.00", "unrealizedProfit": "3.00"}],
                               "totalWalletBalance": "1000.00", "totalUnrealizedProfit": "3.00",
                               "totalMarginBalance": "1003.00",
                               "totalPositionInitialMargin": "40.20",
                               "totalOpenOrderInitialMargin": "19.80",
                               "canTrade": True, "canWithdraw": True})
        if path == "/fapi/v1/leverage":
            return self._send({"symbol": "BTCUSDT", "leverage": int(query.get("leverage", [0])[0] or 0)})
        if path in ("/fapi/v1/marginType", "/fapi/v1/positionMargin"):
            return self._send({"code": 200, "msg": "success"})
        return self._send({"code": -1121, "msg": f"unmocked endpoint {method} {path}"}, 404)

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    server = HTTPServer((args.host, args.port), Handler)
    print(f"mock exchange listening on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
