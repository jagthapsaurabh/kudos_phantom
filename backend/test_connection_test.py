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
    connection_test._signed = lambda client, method, path, weight=1.0: {
        "ok": True, "state": "ok", "detail": "signed call accepted"}
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

print("== CLI tool imports and arg parsing ==")
import importlib.util
cli_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "test_connection.py")
spec = importlib.util.spec_from_file_location("test_connection_cli", cli_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
check("tools/test_connection.py imports cleanly", callable(mod.main))

print(f"\n{pass_count} passed, {fail_count} failed")
sys.exit(1 if fail_count else 0)
