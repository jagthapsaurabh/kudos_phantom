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
4. **signed calls** — balance, positions, open orders, order history, trading
   preferences on the accepted host, plus the sub-account listing the margin-mode
   panel reads (reported, never decisive). Each is categorized as ``ok`` /
   ``permission`` (key is fine, endpoint permission is missing) / ``auth`` /
   ``unreachable`` (no HTTP answer came back — this machine, not the key) /
   ``error``. Any of the last three on a decisive call means the connection is
   NOT ready, even when the host accepted the key one step earlier.
5. **quota**        — the venue's remaining 5-minute weight budget.

Nothing here places, edits or cancels an order: the check is read-only.

The CLI wrapper is ``tools/test_connection.py``; the API wrapper is
``POST /broker-connections/{id}/test`` behind Broker Settings → Test connection.
"""
from __future__ import annotations

import time
import urllib.parse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

from app.core.urls import normalize_base_url
from app.services.broker_client import BrokerClient
from app.services.delta_key_probe import never_reached_the_venue


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
        response = requests.get(f"{normalize_base_url(base_url)}/v2/tickers/{symbol}",
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


def _endpoint_label(method: str, path: str, query: Optional[Dict[str, Any]] = None) -> str:
    """``"GET /v2/orders?product_symbol=BTCUSD"`` — for *humans* only.

    This string is what the report and the terminal display. It must never be
    fed back into a request builder: that is exactly how "check the balance"
    turned into a DNS lookup for ``api.india.delta.exchangeget%20`` (see
    :func:`_signed`).
    """
    text = f"{str(method).upper()} {path}"
    params = {k: v for k, v in dict(query or {}).items() if v is not None}
    if params:
        text += "?" + urllib.parse.urlencode(sorted(params.items()))
    return text


def _classify(payload: Any, label: str) -> Dict[str, Any]:
    """One venue answer → a step state, in the report's vocabulary.

    Order matters and is shared with the environment probe
    (:func:`app.services.delta_key_probe.never_reached_the_venue`): "no HTTP answer
    came back" is checked first, because that is a statement about this
    machine — DNS, TLS, egress, or a URL we built wrong — and it must never be
    filed as an opinion about the key.
    """
    if isinstance(payload, dict) and payload.get("error"):
        detail = str(payload["error"])
        if never_reached_the_venue(detail):
            return {"ok": False, "state": "unreachable", "endpoint": label, "detail": detail}
        lowered = detail.lower()
        if any(marker in lowered for marker in ("unauthorized", "not authorised",
                                                "not authorized", "forbidden")):
            return {"ok": False, "state": "permission", "endpoint": label, "detail": detail}
        if any(marker in lowered for marker in ("http 401", "invalid_api_key",
                                                "invalidapikey", "api key not found",
                                                "invalid_signature", "request_expired",
                                                "incomplete_payload", "signature mismatch")):
            return {"ok": False, "state": "auth", "endpoint": label, "detail": detail}
        return {"ok": False, "state": "error", "endpoint": label, "detail": detail}
    if isinstance(payload, dict) and payload.get("success") is False:
        return {"ok": False, "state": "auth", "endpoint": label, "detail": str(payload)[:300]}
    return {"ok": True, "state": "ok", "endpoint": label, "detail": "signed call accepted"}


def _signed(client: BrokerClient, method: str, path: str,
            query: Optional[Dict[str, Any]] = None, weight: float = 1.0) -> Dict[str, Any]:
    """One signed call categorized: ok / permission / auth / unreachable / error.

    ``path`` is the **bare** endpoint and ``query`` its params. Passing the
    display label instead (``"GET /v2/wallet/balances"``, or a path with its
    query glued on) used to make every account panel of this report fail with
    ``Failed to resolve 'api.india.delta.exchangeget '``, because the label was
    concatenated onto the host and the space ended the authority. The transport
    now refuses such a path outright (:meth:`BrokerClient._path_error`) and the
    label is built here, separately, for display.
    """
    label = _endpoint_label(method, path, query)
    try:
        response, error = client._delta_request(method, path, query=query, weight=weight)
        payload = client._json_body(response, error)
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "state": "unreachable", "endpoint": label,
                "detail": f"{exc.__class__.__name__}: {exc}"}
    return _classify(payload, label)


def _call(client: BrokerClient, call, label: str) -> Dict[str, Any]:
    """One read-only high-level client call, categorized like :func:`_signed`.

    Used where the venue's own adapter has to build the request (a non-Delta
    key has a different signing scheme, so hand-picking a ``/v2/...`` path
    would sign a Binance call the Delta way).
    """
    try:
        payload = call()
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "state": "unreachable", "endpoint": label,
                "detail": f"{exc.__class__.__name__}: {exc}"}
    return _classify(payload, label)


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
        single = _call(probe_client, probe_client.get_account_balance,
                       f"{code} signed account call")
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
    #
    # Every row carries its own bare endpoint and its own display label is
    # derived from that — never the other way round. See the comment on the
    # Delta table below for what happens when a label is used as a path.
    signed_ok = detected is not None
    unreachable_steps: List[str] = []
    if detected:
        client = BrokerClient(api_key, api_secret, detected["broker_code"],
                              testnet=detected["testnet"])
        client.trading_url = client.market_url = normalize_base_url(detected["base_url"])
        if client.kind == "delta":
            # Rows are (name, title, method, path, query) — the *bare* endpoint
            # and its params, which is what gets signed and sent. The
            # human-readable version is derived from them in :func:`_signed`,
            # never supplied alongside: this battery used to carry
            # ``("balance", "GET /v2/wallet/balances", …)`` and pass that label
            # as the path, so every step looked up
            # ``api.india.delta.exchangeget%20`` and the report blamed the
            # exchange for a string built three lines away.
            rows = [
                ("balance", "Account balance", "GET", "/v2/wallet/balances", None),
                # /v2/positions/margined, not /v2/positions: the margined
                # endpoint is what get_positions() trades against, and the
                # singular /v2/positions needs a product_id this battery does
                # not resolve.
                ("positions", "Open positions", "GET", "/v2/positions/margined",
                 {"product_symbol": "BTCUSD"}),
                ("orders", "Open orders", "GET", "/v2/orders",
                 {"product_symbol": "BTCUSD", "page_size": 1}),
                ("history", "Order history", "GET", "/v2/orders/history",
                 {"product_symbol": "BTCUSD", "page_size": 1}),
                ("preferences", "Trading preferences", "GET",
                 "/v2/users/trading_preferences", None),
                # Read-only, and reported but never decisive: the terminal's
                # "margin mode" comes from this endpoint (Delta keeps margin
                # mode on the (sub)account, not in trading_preferences), and a
                # key that belongs to a sub-account is *not allowed* to list
                # the parent's accounts. Both facts have to be visible without
                # letting either one call the key dead.
                ("accounts", "Margin mode source (sub-accounts)", "GET",
                 "/v2/sub_accounts", None),
            ]
            battery = [(name, title,
                        (lambda m=method, p=path, q=query:
                         _signed(client, m, p, query=q, weight=5)))
                       for (name, title, method, path, query) in rows]
        else:
            # Another venue signs differently, so nothing here may be spelled
            # ``/v2/…``: the account adapters already know their own endpoints,
            # and calling them is the more honest check anyway — it is the code
            # the trader will actually run. Endpoints a venue does not have are
            # reported as skipped, never as failures.
            rows = [
                ("balance", "Account balance", client.get_account_balance,
                 "signed wallet balance"),
                ("positions", "Open positions",
                 lambda: client.get_positions("BTCUSDT"), "signed open positions"),
                ("orders", "Open orders",
                 lambda: client.get_open_orders("BTCUSDT"), "signed open orders"),
                ("history", "Order history",
                 lambda: client.get_order_history("BTCUSDT", limit=1),
                 "signed order history"),
                ("preferences", "Trading preferences", None, None),
                ("accounts", "Margin mode source (sub-accounts)", None, None),
            ]
            battery = []
            for (name, title, fn, label) in rows:
                if fn is None:
                    battery.append((name, title, lambda: {
                        "ok": True, "state": "skipped",
                        "endpoint": f"{detected['broker_code']} has no such endpoint",
                        "detail": "Delta-only account setting — not checked"}))
                else:
                    battery.append((name, title,
                                    (lambda f=fn, l=label: _call(client, f, l))))
        for key, title, run in battery:
            result = run()
            result.update({"name": key, "title": title,
                           "base_url": detected["base_url"],
                           "broker_code": detected["broker_code"],
                           "testnet": bool(detected["testnet"])})
            if key == "accounts":
                result["decisive"] = False
                if result.get("state") == "auth":
                    result["detail"] = (
                        f"{result['detail']} — this key may be a sub-account key, "
                        f"which cannot list its parent's accounts; the margin-mode "
                        f"panel falls back to the open position's margin type")
            steps.append(result)
            # Anything but "the venue answered and accepted the key" (or
            # accepted it but the endpoint needs a permission the key lacks —
            # still proof the credential works) means the account side does not
            # work, *including* a call that never got a reply at all.
            if result.get("state") not in ("ok", "permission", "skipped"):
                if key == "accounts":
                    continue
                signed_ok = False
                if result.get("state") == "unreachable":
                    unreachable_steps.append(str(result.get("endpoint") or title))
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
            client.trading_url = client.market_url = normalize_base_url(detected["base_url"])
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
        if unreachable_steps:
            # A signed call that never got an HTTP answer while the same host
            # accepted the key two steps earlier cannot be about the key, and
            # must not be allowed to end this report with "ready".
            problems.append(
                "Some signed calls never reached "
                f"{detected['name']} ({', '.join(unreachable_steps)}) — that is a "
                "network or URL-construction problem on this server, NOT a key "
                "problem: the same host accepted the key in the step above.")
            fixes.append(
                f"Read the host named in the error against the one under test "
                f"({detected['base_url']}). If they differ, a value was appended to "
                "the base URL by the caller — the transport refuses such a request "
                "before opening a socket now, and the step detail says so. If the "
                "hosts match, this is DNS/TLS/egress on the trading server.")
        elif not signed_ok:
            failed = [str(s.get("endpoint") or s.get("title")) for s in steps
                      if s.get("name") in ("balance", "positions", "orders",
                                          "history", "preferences")
                      and s.get("state") not in ("ok", "permission", "skipped")]
            problems.append("Some signed calls failed on the accepted host "
                            f"({', '.join(failed) or 'see steps above'}).")
            fixes.append("Check the state of each step: 'rejected' is the key or the "
                         "environment, 'failed' is the endpoint itself (a Delta error "
                         "code follows it), 'unreachable' never reached the venue.")

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
