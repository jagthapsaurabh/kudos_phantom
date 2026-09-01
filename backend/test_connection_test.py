"""Tests for the four-environment Delta probe + full connection-test battery.

Covers exactly what can go wrong in production without network access from a
dev box: environment identification (India vs Global, production vs demo),
permission-gap detection, and the wiring of the read-only battery
(``app/services/connection_test.py`` + ``tools/test_connection.py`` edge
behaviour). Runs offline: network-facing pieces are faked, not hit.

Run: cd backend && python test_connection_test.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.services import delta_key_probe, connection_test
from app.services.broker_client import BrokerClient

pass_count = 0
fail_count = 0


def check(name, cond, extra=""):
    global pass_count, fail_count
    if cond:
        pass_count += 1
    else:
        fail_count += 1
        print(f"  FAIL: {name} {extra}")


print("== DeltaGlobal is a first-class Delta broker ==")
check("DEFAULTS has DeltaGlobal (kind delta, global host)",
      BrokerClient.DEFAULTS["DeltaGlobal"]["kind"] == "delta"
      and BrokerClient.DEFAULTS["DeltaGlobal"]["market"] == "https://api.delta.exchange")
client = BrokerClient("k", "s", "DeltaGlobal", testnet=True)
check("DeltaGlobal + testnet → Global testnet host (not India demo)",
      client.trading_url == BrokerClient.DELTA_GLOBAL_TESTNET, client.trading_url)
client = BrokerClient("k", "s", "Delta", testnet=True)
check("Delta + testnet → India testnet host still",
      client.trading_url == BrokerClient.DELTA_TESTNET, client.trading_url)
check("all four hosts are listed, each with its own broker code",
      len(BrokerClient.delta_hosts()) == 4
      and {h["name"] for h in BrokerClient.delta_hosts()} == {
          "INDIA-PRODUCTION", "INDIA-TESTNET", "GLOBAL-PRODUCTION", "GLOBAL-TESTNET"}
      and {h["broker_code"] for h in BrokerClient.delta_hosts()} == {"Delta", "DeltaGlobal"})

print("== verdict: India/Global mismatch is named, not 'dead key' ==")
rows = [
    {"name": "INDIA-PRODUCTION", "broker_code": "Delta", "testnet": False,
     "state": "auth", "detail": "invalid_api_key", "base_url": "https://api.india.delta.exchange",
     "environment": "production", "family": "india"},
    {"name": "INDIA-TESTNET", "broker_code": "Delta", "testnet": True,
     "state": "auth", "detail": "invalid_api_key", "base_url": "https://cdn-ind.testnet.deltaex.org",
     "environment": "testnet", "family": "india"},
    {"name": "GLOBAL-PRODUCTION", "broker_code": "DeltaGlobal", "testnet": False,
     "state": "ok", "detail": "wallet balances OK", "base_url": "https://api.delta.exchange",
     "environment": "production", "family": "global"},
    {"name": "GLOBAL-TESTNET", "broker_code": "DeltaGlobal", "testnet": True,
     "state": "auth", "detail": "invalid_api_key", "base_url": "https://testnet-api.delta.exchange",
     "environment": "testnet", "family": "global"},
]
v = delta_key_probe.verdict(rows, flagged_testnet=True, flagged_broker="Delta", label="NishKudos")
check("a Global key is accepted, not called dead",
      v["accepted_by"] == ["GLOBAL-PRODUCTION"])
check("...and accepted_by names the working host", v["accepted_by"] == ["GLOBAL-PRODUCTION"])
check("the connection mismatch is flagged and explained",
      v["mismatch"] is True and "separate key store" in v["summary"]
      and "DeltaGlobal" in v["fix"] and "Test connection" in v["fix"], str(v["fix"]))
check("detected carries what the UI needs to repoint the connection",
      v["detected"] == {"broker_code": "DeltaGlobal", "testnet": False,
                        "name": "GLOBAL-PRODUCTION", "base_url": "https://api.delta.exchange"})

rows2 = [dict(r, state="auth") for r in rows]
rows2[2]["state"] = "permission"
rows2[2]["detail"] = "Delta: UnauthorizedApiAccess"
v2 = delta_key_probe.verdict(rows2, flagged_testnet=False, flagged_broker="DeltaGlobal")
check("a permission gap still identifies the environment",
      v2["accepted_by"] == ["GLOBAL-PRODUCTION"] and v2["permission_gap"] is True)
check("...and the fix says enable the permission", "permission" in v2["fix"].lower())

rows3 = [dict(r, state="auth") for r in rows]
v3 = delta_key_probe.verdict(rows3, flagged_testnet=True, flagged_broker="Delta")
check("rejected everywhere is still the dead-key verdict",
      v3["accepted_by"] == [] and len(v3["rejected_by"]) == 4
      and "Create a fresh key" in v3["fix"])

print("== run_connection_test: read-only battery wiring (no network) ==")
orig_tick = connection_test._public_ticker
orig_probe = delta_key_probe.probe_host
orig_signed = connection_test._signed
orig_quota = BrokerClient.fetch_rate_limit_quota
try:
    connection_test._public_ticker = lambda base_url, symbol="BTCUSD": {
        "ok": True, "state": "ok", "detail": "public ticker answered in 12 ms",
        "latency_ms": 12, "clock_skew_s": 0.4}
    delta_key_probe.probe_host = lambda key, secret, url, testnet=False, broker_code="Delta": {
        "state": "ok" if url == "https://api.delta.exchange" else "auth",
        "detail": "wallet balances OK", "base_url": url,
        "environment": "testnet" if testnet else "production",
        "family": "global" if broker_code.lower() == "deltaglobal" else "india"}
    connection_test._signed = lambda client, method, path, query=None, weight=1.0: {
        "ok": True, "state": "ok", "detail": "signed call accepted",
        "endpoint": f"{method} {path}"}
    BrokerClient.fetch_rate_limit_quota = lambda self: {"current_quota": 9000}

    result = connection_test.run_connection_test("K", "S", broker_code="Delta",
                                                 testnet=True, label="NishKudos")
    names = [s.get("name") for s in result["steps"]]
    check("battery has market data, clock, environment, signed + quota steps",
          {"market_data", "clock", "environment", "balance", "positions", "orders",
           "history", "preferences", "quota"} <= set(names), str(names))
    check("detected points at DeltaGlobal production",
          result["detected"]["broker_code"] == "DeltaGlobal"
          and result["detected"]["testnet"] is False, str(result["detected"]))
    check("the verdict names the India/Global mismatch",
          any("separate key store" in p for p in result["verdict"]["problems"])
          and any("DeltaGlobal" in f for f in result["verdict"]["fixes"]),
          str(result["verdict"]))
    check("a ready connection reports ok, and a mismatched one is not 'ok'",
          result["verdict"]["ok"] is False)
    check("no step ever tries to write an order (read-only shape)",
          all(s.get("name") != "place_order" for s in result["steps"]))

    result2 = connection_test.run_connection_test("K", "S", broker_code="DeltaGlobal",
                                                  testnet=False, label="Global key")
    check("connection already on the right family/environment is OK",
          result2["verdict"]["ok"] is True and result2["detected"]["name"] == "GLOBAL-PRODUCTION",
          str(result2["verdict"])[:200])
finally:
    connection_test._public_ticker = orig_tick
    delta_key_probe.probe_host = orig_probe
    connection_test._signed = orig_signed
    BrokerClient.fetch_rate_limit_quota = orig_quota

print("== battery at the wire: the URL that actually leaves the process ==")
# Everything above stubs ``_signed``, which is exactly how the shipped bug
# survived: the battery used to pass its *display* label ("GET
# /v2/wallet/balances") as the request path, so ``base_url + path`` became
# ``https://api.india.delta.exchangeGET /v2/wallet/balances``, the space ended
# the authority, and all five account steps died in DNS ("Failed to resolve
# api.india.delta.exchangeget%20") with a key that was perfectly fine. These
# checks fake the transport one level lower and assert on the request.
import requests as _requests
from app.services import broker_client as _bc

NAMES = ("balance", "positions", "orders", "history", "preferences", "accounts")
EXPECTED_URLS = {
    "https://api.india.delta.exchange/v2/wallet/balances": {},
    "https://api.india.delta.exchange/v2/positions/margined": {"product_symbol": "BTCUSD"},
    "https://api.india.delta.exchange/v2/orders": {"product_symbol": "BTCUSD", "page_size": 1},
    "https://api.india.delta.exchange/v2/orders/history": {"product_symbol": "BTCUSD", "page_size": 1},
    "https://api.india.delta.exchange/v2/users/trading_preferences": {},
    "https://api.india.delta.exchange/v2/sub_accounts": {},
}
EXPECTED_LABELS = {
    "balance": "GET /v2/wallet/balances",
    "positions": "GET /v2/positions/margined?product_symbol=BTCUSD",
    "orders": "GET /v2/orders?page_size=1&product_symbol=BTCUSD",
    "history": "GET /v2/orders/history?page_size=1&product_symbol=BTCUSD",
    "preferences": "GET /v2/users/trading_preferences",
    "accounts": "GET /v2/sub_accounts",
}
sent: list = []


class _Resp:
    status_code = 200
    headers = {"X-RATE-LIMIT-REMAINING": "475", "Content-Type": "application/json"}
    text = '{"success": true, "result": []}'

    def json(self):
        return {"success": True, "result": []}


def _fake_transport(fail_for=()):
    def fake_request(method, url, params=None, data=None, headers=None, timeout=None, **kw):
        sent.append((method, url, dict(params or {})))
        if any(token in url for token in fail_for):
            raise _requests.ConnectionError(
                f"HTTPSConnectionPool(host={url.split('//', 1)[1]!r}, port=443): "
                "Max retries exceeded with url: / (Failed to resolve)")
        return _Resp()
    return fake_request


orig_request, orig_sleep = _requests.request, _bc.time.sleep
orig_tick, orig_probe, orig_quota = (connection_test._public_ticker,
                                     delta_key_probe.probe_host,
                                     BrokerClient.fetch_rate_limit_quota)
try:
    _bc.time.sleep = lambda *_a, **_k: None          # retry loops, no wall clock
    _requests.request = _fake_transport()
    connection_test._public_ticker = lambda base_url, symbol="BTCUSD": {
        "ok": True, "state": "ok", "detail": "public ticker answered in 33 ms",
        "latency_ms": 33, "clock_skew_s": 3.38}
    delta_key_probe.probe_host = lambda key, secret, url, testnet=False, broker_code="Delta": {
        "state": "ok" if url == "https://api.india.delta.exchange" else "auth",
        "detail": "wallet balances OK", "base_url": url, "name": "",
        "environment": "testnet" if testnet else "production",
        "family": "global" if str(broker_code).lower() == "deltaglobal" else "india",
        "broker_code": broker_code, "testnet": bool(testnet)}
    BrokerClient.fetch_rate_limit_quota = lambda self: {"current_quota": 475}

    sent.clear()
    wire = connection_test.run_connection_test("K", "S", broker_code="Delta",
                                              testnet=False, label="Wired")
    steps = {s.get("name"): s for s in wire["steps"] if s.get("name") in NAMES}
    check("the battery reports one step per signed endpoint",
          all(n in steps for n in NAMES), str(sorted(steps))[:200])
    check("all six signed battery steps succeed against a 200-answering venue",
          all(steps.get(n, {}).get("state") == "ok" for n in NAMES),
          str([(n, steps.get(n, {}).get("detail")) for n in NAMES])[:300])
    check("every battery step is requested at its own endpoint, nothing else",
          {u.split("?")[0]: p for _, u, p in sent} == EXPECTED_URLS, str(sent)[:300])
    check("no query string is ever glued onto a path",
          all("?" not in u for _m, u, _p in sent), str(sent)[:200])
    check("...and no method name is",
          all(" " not in u and "GET" not in u.split("//", 1)[1].split("/")[0]
              for _m, u, _p in sent), str(sent)[:200])
    check("the host asked for is the host the key was accepted on",
          {u.split("//", 1)[1].split("/")[0] for _m, u, _p in sent} == {"api.india.delta.exchange"},
          str(sent)[:200])
    check("each step shows the operator the endpoint it actually called",
          all((steps.get(n) or {}).get("endpoint") == EXPECTED_LABELS[n] for n in NAMES),
          str([(n, (steps.get(n) or {}).get("endpoint")) for n in NAMES])[:300])
    check("a battery that passes says READY", wire["verdict"]["ok"] is True,
          str(wire["verdict"])[:250])
    check("the sub-account step is reported but never decisive",
          (steps.get("accounts") or {}).get("decisive") is False)

    # A non-Delta connection must be asked in its own venue's language: this
    # battery used to sign GET /v2/wallet/balances against Binance, which
    # answers HTML, and "unreachable" was the best the report could do.
    sent.clear()
    binance = connection_test.run_connection_test("K", "S", broker_code="Binance",
                                                   testnet=False, label="Binance")
    from urllib.parse import urlsplit
    binance_paths = sorted({urlsplit(u).path for _m, u, _p in sent})
    check("a Binance connection is never probed with a Delta /v2 endpoint",
          all(not p.startswith("/v2/") for p in binance_paths), str(binance_paths)[:250])
    check("...and its account steps go through the Binance adapters",
          {"/fapi/v2/account", "/fapi/v2/positionRisk", "/fapi/v1/openOrders",
           "/fapi/v1/allOrders"} <= set(binance_paths), str(binance_paths)[:250])
    b_steps = {s.get("name"): s for s in binance["steps"]}
    check("Delta-only account settings are skipped for other venues, not failed",
          b_steps.get("preferences", {}).get("state") == "skipped"
          and b_steps.get("accounts", {}).get("state") == "skipped"
          and b_steps.get("balance", {}).get("state") == "ok"
          and binance["verdict"]["ok"] is True,
          str([(k, v.get("state")) for k, v in b_steps.items()])[:250])

    # A signed call that gets no HTTP answer must not be able to hide behind a
    # key verdict, nor let the report end with "is ready".
    sent.clear()
    _requests.request = _fake_transport(fail_for=["/v2/positions"])
    half = connection_test.run_connection_test("K", "S", broker_code="Delta",
                                               testnet=False, label="Half wired")
    pos = next((s for s in half["steps"] if s.get("name") == "positions"), {})
    check("a call that never reached the venue is 'unreachable', not an auth result",
          pos.get("state") == "unreachable", str(pos)[:250])
    check("...and the connection is then NOT reported ready",
          half["verdict"]["ok"] is False and "found issues" in half["verdict"]["message"],
          str(half["verdict"])[:250])
    check("...and the problem says which endpoints never got an answer",
          any("never reached" in p for p in half["verdict"]["problems"]),
          str(half["verdict"]["problems"])[:300])
    check("...while the step that did answer still proves the key",
          half["detected"] is not None and half["verdict"]["accepted"] is True)
finally:
    _requests.request = orig_request
    _bc.time.sleep = orig_sleep
    connection_test._public_ticker = orig_tick
    delta_key_probe.probe_host = orig_probe
    BrokerClient.fetch_rate_limit_quota = orig_quota

print("== base URLs are normalized where they are read ==")
from app.core.urls import normalize_base_url, path_problem, url_problem

check("a pasted trailing space or slash is not a different host",
      normalize_base_url("  https://api.india.delta.exchange/ \n") == "https://api.india.delta.exchange",
      repr(normalize_base_url("  https://api.india.delta.exchange/ \n")))
check("an absent definition URL falls back to the built-in host",
      normalize_base_url(None, BrokerClient.DELTA_PRODUCTION) == BrokerClient.DELTA_PRODUCTION)
# Trimming is deliberately minimal. Stripping *inner* whitespace too would turn
# a broken value into a plausible-looking host, and a plausible host is the one
# thing a signed request must never be aimed at, so the space stays in the
# string and the transport guard keeps failing loudly.
check("only unambiguous whitespace is trimmed, so a bad host stays a bad host",
      url_problem(normalize_base_url("https://api.india.delta.exchange GET")) is not None
      and normalize_base_url("https://api.india.delta.exchangeGET ")
      == "https://api.india.delta.exchangeGET")
check("a bare, query-free path is always acceptable", path_problem("/v2/orders") is None)
sloppy = BrokerClient("k", "s", "Delta", definition=types.SimpleNamespace(
    code="Delta", kind="delta", is_builtin=0,
    market_data_url=" https://api.india.delta.exchange/ ",
    trading_api_url="https://api.india.delta.exchange\n"))
check("a broker definition typed with stray whitespace still trades India production",
      sloppy.trading_url == "https://api.india.delta.exchange"
      and sloppy.market_url == "https://api.india.delta.exchange", sloppy.trading_url)

print("== transport guards: a display label can never become a URL ==")
_requests.request = _fake_transport()
sent.clear()
try:
    guard = BrokerClient("k", "s", "Delta")
    bad_path = guard._json_body(*guard._delta_request("GET", "GET /v2/wallet/balances"))
    check("a method-prefixed path is refused, not signed and not sent",
          "starts with an HTTP method" in str(bad_path) and not sent, str(bad_path)[:200])
    glued = guard._json_body(*guard._delta_request("GET", "/v2/orders?product_symbol=BTCUSD"))
    check("a query glued onto the path is refused too (it must be signed once)",
          "query string" in str(glued) and not sent, str(glued)[:200])
    good = guard._json_body(*guard._delta_request("GET", "/v2/wallet/balances"))
    check("a bare path still goes out, with the same bytes as before",
          len(sent) == 1 and not good.get("error"), f"{sent} {str(good)[:120]}")
    corrupt = guard._json_body(*guard._throttled_request(
        "GET", "https://api.india.delta.exchangeGET /v2/wallet/balances"))
    check("a corrupted base URL is caught before DNS, in words",
          "not a hostname" in str(corrupt) and len(sent) == 1, str(corrupt)[:200])
    refusal = str(bad_path["error"])
    check("the guard's own refusal is filed as 'never reached the venue', "
          "not as a key verdict",
          delta_key_probe.is_local_refusal(refusal)
          and delta_key_probe.never_reached_the_venue(refusal)
          and delta_key_probe._state_for(refusal) == "unreachable"
          and connection_test._classify(bad_path, "GET /x")["state"] == "unreachable",
          refusal[:200])
    check("a real 401 is still an auth answer for both classifiers",
          not delta_key_probe.never_reached_the_venue(
              'Delta HTTP 401: {"code": "invalid_api_key"}')
          and delta_key_probe._state_for('Delta HTTP 401: {"code": "invalid_api_key"}') == "auth")
    check("and a dropped connection still is",
          delta_key_probe.is_transport_failure(
              "Delta request failed: ConnectionError: Failed to resolve"))
finally:
    _requests.request = orig_request

print("== CLI tool imports and arg parsing ==")
import importlib.util
cli_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "test_connection.py")
spec = importlib.util.spec_from_file_location("test_connection_cli", cli_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
check("tools/test_connection.py imports cleanly", callable(mod.main))

print(f"\n{pass_count} passed, {fail_count} failed")
sys.exit(1 if fail_count else 0)
