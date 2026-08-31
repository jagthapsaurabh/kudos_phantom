"""Full broker-connection test: the one place that explains a 401.

``GET/POST`` market data (seeds, candles, tickers) works without credentials;
every *signed* call (balance, positions, orders, fills) goes through the API
key. So "data seed is working but nothing else is" is the signature of a key
problem — and the answer is almost never "the key is dead". Delta Exchange
runs four separate key stores (India production/demo, Global production/demo),
an account/site mismatch answers ``invalid_api_key`` on every signed call, and
a key with only *Read Data* permission can even fail the balance check.

:func:`run_connection_test` runs one battery that separates the causes:

1. **market data**  — public ``/v2/tickers/{symbol}`` on the configured host.
   Failing here is a network/server problem, not a key problem (and it would
   also explain failing seeds, which is NOT the user's symptom).
2. **clock**        — Delta signs with a Unix-seconds timestamp and rejects a
   request more than 5 s off the server clock. A skewed trading box shows up
   as ``SignatureExpired``/``request_expired``, which looks like a bad key.
3. **environment**  — signs ``wallet/balances`` against ALL Delta hosts and
   reports the one that accepts the key.
4. **signed calls** — balance, positions, order history, trading preferences
   on the accepted host. Each is categorized as ``ok`` / ``permission``
   (key is fine, endpoint permission is missing) / ``auth`` / ``unreachable``.
5. **quota**        — the venue's remaining 5-minute weight budget.

Nothing here places, edits or cancels an order: the check is read-only.

The CLI wrapper is ``tools/test_connection.py``; the API wrapper is
``POST /broker-connections/{id}/test`` behind Broker Settings → Test connection.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

from app.services.broker_client import BrokerClient


def _delta_environments() -> List[Dict[str, Any]]:
    """The four Delta environments as (broker_code, testnet, url, name)."""
    return [
        {"broker_code": "Delta", "testnet": False, "url": BrokerClient.DELTA_PRODUCTION,
         "name": "INDIA-PRODUCTION", "label": "Delta India · production"},
        {"broker_code": "Delta", "testnet": True, "url": BrokerClient.DELTA_TESTNET,
         "name": "INDIA-TESTNET", "label": "Delta India · testnet/demo"},
        {"broker_code": "DeltaGlobal", "testnet": False, "url": BrokerClient.DELTA_GLOBAL_PRODUCTION,
         "name": "GLOBAL-PRODUCTION", "label": "Delta Global · production"},
        {"broker_code": "DeltaGlobal", "testnet": True, "url": BrokerClient.DELTA_GLOBAL_TESTNET,
         "name": "GLOBAL-TESTNET", "label": "Delta Global · testnet/demo"},
    ]


def _public_ticker(base_url: str, symbol: str = "BTCUSD") -> Dict[str, Any]:
    """Public, unsigned reachability check + server-clock comparison."""
    import requests
    started = time.time()
    try:
        response = requests.get(f"{base_url.rstrip('/')}/v2/tickers/{symbol}",
                                headers={"User-Agent": BrokerClient.USER_AGENT},
                                timeout=15)
        latency_ms = int((time.time() - started) * 1000)
    except Exception as exc:
        return {"ok": False, "state": "unreachable",
                "detail": f"{exc.__class__.__name__}: {exc}", "latency_ms": None,
                "clock_skew_s": None}
    if response.status_code != 200:
        return {"ok": False, "state": "error",
                "detail": f"HTTP {response.status_code}: {(response.text or '')[:200]}",
                "latency_ms": latency_ms, "clock_skew_s": None}
    clock_skew_s = None
    server_date = response.headers.get("Date")
    if server_date:
        try:
            server = parsedate_to_datetime(server_date)
            if server.tzinfo is None:
                server = server.replace(tzinfo=timezone.utc)
            clock_skew_s = round(
                (datetime.now(timezone.utc) - server).total_seconds(), 2)
        except Exception:
            clock_skew_s = None
    return {"ok": True, "state": "ok",
            "detail": f"public ticker answered in {latency_ms} ms",
            "latency_ms": latency_ms, "clock_skew_s": clock_skew_s}


def _signed(client: BrokerClient, method: str, path: str, weight: float = 1.0) -> Dict[str, Any]:
    """One signed call categorized: ok / permission / auth / unreachable."""
    try:
        response, error = client._delta_request(method, path, weight=weight)
        payload = client._json_body(response, error)
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "state": "unreachable",
                "detail": f"{exc.__class__.__name__}: {exc}"}
    if isinstance(payload, dict) and payload.get("error"):
        detail = str(payload["error"])
        lowered = detail.lower()
        if any(marker in lowered for marker in ("unauthorized", "not authorised",
                                                "not authorized", "forbidden")):
            return {"ok": False, "state": "permission", "detail": detail}
        if any(marker in lowered for marker in ("http 401", "invalid_api_key",
                                                "invalidapikey", "api key not found",
                                                "invalid_api_key")):
            return {"ok": False, "state": "auth", "detail": detail}
        return {"ok": False, "state": "error", "detail": detail}
    if isinstance(payload, dict) and payload.get("success") is False:
        return {"ok": False, "state": "auth", "detail": str(payload)[:300]}
    return {"ok": True, "state": "ok", "detail": "signed call accepted"}


def run_connection_test(api_key: str, api_secret: str, broker_code: str = "Delta",
                        testnet: bool = False, connection_id: Optional[int] = None,
                        label: str = "") -> Dict[str, Any]:
    """Read-only battery; never raises. Returns structured steps + verdict."""
    steps: List[Dict[str, Any]] = []
    code = str(broker_code or "Delta")
    if code.lower() == "deltaglobal":
        code = "DeltaGlobal"

    # 1. Market data on the host the connection points at.
    base_url = BrokerClient.delta_family(code, testnet=bool(testnet))
    tick = _public_ticker(base_url)
    tick.update({"name": "market_data", "title": "Public market data (no key needed)",
                 "broker_code": code, "testnet": bool(testnet), "base_url": base_url})
    steps.append(tick)

    # 2. Clock skew (falls out of the same public call — no extra request).
    skew = tick.get("clock_skew_s")
    clock_step = {
        "name": "clock", "title": "Server clock (Delta signs Unix seconds)",
        "ok": skew is None or abs(skew) <= 5.0, "state": "ok" if skew is None or abs(skew) <= 5.0 else "error",
        "base_url": base_url, "broker_code": code, "testnet": bool(testnet),
        "detail": (f"local clock vs exchange: {skew:+.2f}s"
                   if skew is not None else "no server timestamp returned "
                                            "(clock check skipped)"),
    }
    if skew is not None and abs(skew) > 5.0:
        clock_step["state"] = "error"
        clock_step["detail"] += (" — Delta rejects signatures more than 5 s old "
                                 "(SignatureExpired). Sync the server clock (NTP).")
    steps.append(clock_step)

    # 3. Which environment accepts the key? (Delta only; other venues get 1 host.)
    env_rows: List[Dict[str, Any]] = []
    detected = None
    if BrokerClient.DEFAULTS.get(code, {}).get("kind") == "delta":
        from app.services.delta_key_probe import probe_host
        for env in _delta_environments():
            row = probe_host(api_key, api_secret, env["url"], env["testnet"], env["broker_code"])
            row["name"] = env["name"]
            row["label"] = env["label"]
            row["broker_code"] = env["broker_code"]
            row["testnet"] = env["testnet"]
            env_rows.append(row)
        accepted = next((r for r in env_rows if r.get("state") in ("ok", "permission")), None)
        if accepted:
            detected = {"broker_code": accepted.get("broker_code"),
                        "testnet": bool(accepted.get("testnet")),
                        "name": accepted["name"], "base_url": accepted["base_url"],
                        "permission_gap": accepted.get("state") == "permission",
                        "family": accepted.get("family")}
        env_step = {
            "name": "environment", "title": "Which Delta environment accepts this key?",
            "ok": detected is not None, "state": "ok" if detected else "error",
            "base_url": (detected or {}).get("base_url"),
            "broker_code": (detected or {}).get("broker_code"),
            "testnet": bool((detected or {}).get("testnet")),
            "detail": (f"accepted by {detected['name']} ({detected['base_url']})"
                       if detected else
                       "rejected by every Delta host that answered"),
            "rows": env_rows, "detected": detected,
        }
        steps.append(env_step)
    else:
        probe_client = BrokerClient(api_key, api_secret, code, testnet=testnet)
        single = _signed(probe_client, "GET", "/v2/wallet/balances", weight=5)
        single.update({"name": "environment",
                       "title": f"Signed call on {code} ({'testnet' if testnet else 'production'})",
                       "base_url": probe_client.trading_url,
                       "broker_code": code, "testnet": bool(testnet)})
        steps.append(single)
        if single.get("state") == "ok":
            detected = {"broker_code": code, "testnet": bool(testnet),
                        "name": "PRODUCTION" if not testnet else "TESTNET",
                        "base_url": probe_client.trading_url,
                        "permission_gap": False, "family": None}

    # 4. Signed calls on the environment that accepted the key.
    signed_ok = detected is not None
    if detected:
        client = BrokerClient(api_key, api_secret, detected["broker_code"],
                              testnet=detected["testnet"])
        client.trading_url = client.market_url = detected["base_url"].rstrip("/")
        battery = [
            ("balance", "GET /v2/wallet/balances", 5, "Account balance"),
            ("positions", "GET /v2/positions?product_symbol=BTCUSD", 5, "Open positions"),
            ("orders", "GET /v2/orders?product_symbol=BTCUSD&page_size=1", 5, "Open orders"),
            ("history", "GET /v2/orders/history?product_symbol=BTCUSD&page_size=1", 5, "Order history"),
            ("preferences", "GET /v2/users/trading_preferences", 5, "Trading preferences"),
        ]
        for key, path, weight, title in battery:
            result = _signed(client, "GET", path, weight=weight)
            result.update({"name": key, "title": title,
                           "base_url": detected["base_url"],
                           "broker_code": detected["broker_code"],
                           "testnet": bool(detected["testnet"])})
            steps.append(result)
            if result.get("state") in ("auth", "unreachable"):
                signed_ok = False
    else:
        steps.append({"name": "signed", "title": "Signed account calls",
                      "ok": False, "state": "skipped",
                      "detail": "no host accepted the key — see the environment step"})

    # 5. Rate-limit quota (best effort).
    quota = None
    if detected:
        try:
            client = BrokerClient(api_key, api_secret, detected["broker_code"],
                                  testnet=detected["testnet"])
            client.trading_url = client.market_url = detected["base_url"].rstrip("/")
            payload = client.fetch_rate_limit_quota()
            if isinstance(payload, dict) and not payload.get("error"):
                quota = payload
                steps.append({"name": "quota", "title": "Rate-limit quota",
                              "ok": True, "state": "ok",
                              "detail": f"quota available: {payload.get('current_quota', '?')} weight",
                              "base_url": detected["base_url"],
                              "broker_code": detected["broker_code"],
                              "testnet": bool(detected["testnet"])})
        except Exception:
            quota = None
    if quota is None:
        steps.append({"name": "quota", "title": "Rate-limit quota",
                      "ok": True, "state": "skipped",
                      "detail": "venue quota endpoint not reachable — local limiter still applies"})

    # ---- Verdict ----------------------------------------------------------
    problems: List[str] = []
    fixes: List[str] = []
    if not tick.get("ok"):
        problems.append("The server cannot reach the exchange public API. Seeds and "
                        "mark price will fail too — this is NOT a key problem.")
        fixes.append("Fix DNS/TLS/egress on this machine (run the check from the trading "
                     "server) and re-run.")
    if detected is None:
        problems.append("No Delta environment accepted the key.")
        fixes.append("Re-create the key in the panel of the environment you want to trade, "
                     "or paste it again in full (key AND secret, no stray characters). "
                     "If you already know where the key belongs (e.g. it was just created "
                     "on Delta India production), use 'Align to India production' on the "
                     "connection — it repoints broker + environment without the key having "
                     "to pass first.")
    else:
        family_mismatch = str(code).lower() != str(detected["broker_code"]).lower()
        env_mismatch = bool(testnet) != bool(detected["testnet"])
        if family_mismatch:
            problems.append(f"The connection targets Delta {code} but the key belongs to "
                            f"Delta {detected['broker_code']} — India and Global keep separate "
                            "key stores.")
            fixes.append(f"Repoint the connection to {detected['broker_code']} "
                         f"{'testnet/demo' if detected['testnet'] else 'production'} "
                         "(Test connection → Use this environment, or Edit in Broker Settings).")
        elif env_mismatch:
            problems.append(f"The connection is flagged {'testnet' if testnet else 'production'} "
                            "but the key is for the other side of that host.")
            fixes.append(f"Flip the testnet toggle to {'ON' if detected['testnet'] else 'OFF'} "
                         "on this connection and save — instances re-read it automatically.")
        if detected.get("permission_gap"):
            problems.append("The host accepts the key but the endpoint permission is missing "
                            "(Delta answers UnauthorizedApiAccess).")
            fixes.append("Open API Management for this key and enable Read Data + Trading, "
                         "then Test connection again.")
        if not signed_ok:
            problems.append("Some signed calls failed on the accepted host (see rows above).")

    ok = bool(tick.get("ok")) and detected is not None and signed_ok and not problems
    verdict = {
        "ok": ok,
        "accepted": detected is not None,
        "connection_ok": ok,
        "problems": problems,
        "fixes": fixes,
        "message": (
            f"{label or 'This connection'} is ready — the key works on "
            f"{detected['name']} ({detected['base_url']})."
            if detected is not None and not problems else (
                "Connection test found issues — read the steps above." if problems else
                "The key was rejected by every Delta host that answered.")
        ),
    }
    return {
        "connection_id": connection_id,
        "label": label or code,
        "broker_code": code,
        "is_testnet": bool(testnet),
        "configured_base_url": base_url,
        "steps": steps,
        "detected": detected,
        "verdict": verdict,
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
