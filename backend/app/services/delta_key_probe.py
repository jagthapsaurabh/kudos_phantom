"""Which Delta India environment an API key actually belongs to.

The symptom is always the same and never self-explanatory: every signed call
answers ``HTTP 401 {"code": "invalid_api_key"}`` while candles, tickers and the
mark price keep working, because the public endpoints are unsigned. That single
error string covers four different situations —

1. the key was regenerated or deleted in the exchange panel;
2. it was pasted incompletely (Delta keys are long, and a truncated paste is
   still syntactically plausible);
3. it is a **production** key on a **testnet** connection;
4. it is a **testnet** key on a **production** connection.

3 and 4 are indistinguishable from 1 and 2 by looking at the key, and they are
the only ones the operator can fix without creating anything. Delta India's two
environments have separate key stores, so the same key is checked against both
hosts: whichever one signs it is the environment the connection has to point at.
``/v2/profile`` is deliberately not used as the ping — from the 19.08.26
changelog it is no longer reachable with API keys — so the probe signs
``GET /v2/wallet/balances``, the call the docs leave for a key check.

Two callers share this: the ``tools/check_delta_key.py`` script (run it on the
trading box, since a whitelisted key 401s from any other egress IP) and the
``POST /broker-connections/{id}/probe`` endpoint behind Broker Settings → **Check
key**, so the answer is in the same screen as the form that fixes it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.broker_client import BrokerClient

# The two Delta India REST environments, in the order they are probed.
HOSTS: List[Dict[str, Any]] = [
    {"name": "PRODUCTION", "url": BrokerClient.DELTA_PRODUCTION, "testnet": False},
    {"name": "TESTNET", "url": BrokerClient.DELTA_TESTNET, "testnet": True},
]

# Transport-level failures say nothing about the key — they say the machine
# could not reach the host, which is its own bug (DNS, SSL, egress IP, proxy).
_TRANSPORT_TOKENS = ("SSLError", "ConnectionError", "Timeout", "Max retries",
                     "Failed to resolve", "request failed", "non-JSON body")


def _state_for(detail: str) -> str:
    """``"unreachable"`` for a network failure, else ``"auth"`` for a rejection."""
    if any(token in detail for token in _TRANSPORT_TOKENS):
        return "unreachable"
    return "auth"


def probe_host(api_key: str, api_secret: str, base_url: str,
               testnet: bool = False) -> Dict[str, Any]:
    """Sign one ``GET /v2/wallet/balances`` against one host.

    Returns ``{"state", "detail", "base_url", "environment"}`` — never raises,
    because a probe that breaks the request it was called from is worse than a
    probe that reports the failure.
    """
    environment = "testnet" if testnet else "production"
    if not (api_key and api_secret):
        return {"state": "no_credentials", "detail": "no API key/secret to test",
                "base_url": base_url, "environment": environment}
    try:
        client = BrokerClient(api_key, api_secret, "Delta", testnet=testnet)
        # Belt and braces: probe exactly the host we were asked about, whatever
        # the broker definition's URLs say.
        client.trading_url = client.market_url = base_url.rstrip("/")
        payload = client.get_account_balance()
    except Exception as exc:  # pragma: no cover - defensive
        return {"state": "unreachable", "detail": f"{exc.__class__.__name__}: {exc}",
                "base_url": base_url, "environment": environment}
    if isinstance(payload, dict) and payload.get("success") is False:
        detail = str(payload)[:300]
        return {"state": "auth", "detail": detail, "base_url": base_url,
                "environment": environment}
    detail = str(payload.get("error") or "") if isinstance(payload, dict) else ""
    if detail:
        return {"state": _state_for(detail), "detail": detail[:300],
                "base_url": base_url, "environment": environment}
    return {"state": "ok", "detail": "wallet balances OK (signed call accepted)",
            "base_url": base_url, "environment": environment}


def probe_key(api_key: str, api_secret: str,
              hosts: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Probe every host, production first. Two signed calls, once per click."""
    rows = []
    for host in (hosts or HOSTS):
        row = probe_host(api_key, api_secret, host["url"], host.get("testnet", False))
        row["name"] = host["name"]
        rows.append(row)
    return rows


def verdict(rows: List[Dict[str, Any]], flagged_testnet: Optional[bool] = None,
            label: str = "") -> Dict[str, Any]:
    """Plain-language reading of a :func:`probe_key` result.

    ``flagged_testnet`` is what the *connection* says the environment is; when
    it disagrees with what the key says, that mismatch is the whole bug and the
    fix is one toggle, not a new key.
    """
    accepted = next((row for row in rows if row.get("state") == "ok"), None)
    answered = [row for row in rows if row.get("state") in ("ok", "auth")]
    unreachable = [row for row in rows if row.get("state") == "unreachable"]
    out: Dict[str, Any] = {
        "environment": (accepted or {}).get("environment"),
        "base_url": (accepted or {}).get("base_url"),
        "accepted_by": [row["name"] for row in rows if row.get("state") == "ok"],
        "rejected_by": [row["name"] for row in rows if row.get("state") == "auth"],
        "unreachable_from_here": [row["name"] for row in unreachable],
        "matches_connection": None,
        "mismatch": False,
        "summary": "",
        "fix": "",
        "rows": rows,
    }
    if accepted is not None:
        name = accepted["name"]
        out["summary"] = (f"{label or 'This key'} is accepted by {name} "
                          f"({accepted['base_url']}).")
        if flagged_testnet is None:
            out["fix"] = f"Point the connection at {name} and save."
        else:
            wants = "testnet" if flagged_testnet else "production"
            out["matches_connection"] = bool(flagged_testnet) == (name == "TESTNET")
            if out["matches_connection"]:
                out["fix"] = ("The environment flag matches the key. If a live instance "
                              "still 401s it was started with older credentials — Reload "
                              "keys on the instance (no restart needed).")
            else:
                out["mismatch"] = True
                out["fix"] = (f"The connection is flagged {wants} but the key only works "
                              f"on {name}. Flip the testnet toggle on this connection, "
                              "save, and running instances re-read it by themselves.")
        return out
    if answered:
        out["summary"] = (f"{label or 'This key'} is rejected by every Delta India host "
                          f"that answered ({', '.join(r['name'] for r in answered)}).")
        out["fix"] = ("The key is dead: deleted, rotated, or pasted incompletely (check "
                      "the last characters). Create a fresh key in the panel of the "
                      "environment you want to trade, paste key AND secret, and save. "
                      "If the key is IP-whitelisted, run the probe on the trading server "
                      "— a different egress IP 401s the same way a bad key does.")
        return out
    out["summary"] = ("Neither Delta India host answered from this machine, so this says "
                      "nothing about the key.")
    out["fix"] = ("Fix connectivity/DNS/SSL on the trading server first "
                  "(the probe runs from the box, not the browser).")
    return out


def probe_single_host(broker_name: str, api_key: str, api_secret: str,
                      testnet: bool = False) -> Dict[str, Any]:
    """One signed ping on the venue's own host, shaped like :func:`probe_host`.

    Used for every non-Delta broker: their keys are not shared between
    environments the way Delta India's are, so there is a host to check and no
    second one to compare it against.
    """
    client = BrokerClient(api_key, api_secret, broker_name, testnet=testnet)
    base_url = client.trading_url
    try:
        payload = client.get_account_balance()
        detail = str(payload.get("error") or "") if isinstance(payload, dict) else ""
    except Exception as exc:
        detail = f"{exc.__class__.__name__}: {exc}"
    state = "ok" if not detail else _state_for(detail)
    return {"name": ("TESTNET" if testnet else "PRODUCTION"), "state": state,
            "detail": detail[:300] or "signed call accepted",
            "base_url": base_url,
            "environment": "testnet" if testnet else "production"}


def probe_connection(api_key: str, api_secret: str, flagged_testnet: Optional[bool],
                     label: str = "", broker_kind: str = "delta",
                     broker_name: str = "Delta") -> Dict[str, Any]:
    """One call for the API: probe + verdict, with a non-Delta fallback.

    A non-Delta venue has one environment per key, so there is nothing to
    disambiguate — say so with the venue's own answer instead of pretending.
    """
    if str(broker_kind or "").lower() != "delta":
        row = probe_single_host(broker_name, api_key, api_secret,
                                testnet=bool(flagged_testnet))
        accepted = row["state"] == "ok"
        return {
            "broker": broker_name, "environment_checked": False,
            "accepted": accepted, "rows": [row],
            "summary": (f"{label or 'This key'} was accepted by {broker_name} at {row['base_url']}."
                        if accepted else
                        f"{label or 'This key'} was rejected by {broker_name}: {row['detail']}"),
            "fix": "" if accepted else "Re-enter the key and secret for this venue.",
            "environment": row["environment"], "base_url": row["base_url"],
            "accepted_by": [row["name"]] if accepted else [],
            "rejected_by": [] if accepted else [row["name"]],
            "unreachable_from_here": [row["name"]] if row["state"] == "unreachable" else [],
            "matches_connection": None, "mismatch": False,
        }
    rows = probe_key(api_key, api_secret)
    out = verdict(rows, flagged_testnet=flagged_testnet, label=label)
    out["broker"] = broker_name or "Delta"
    out["environment_checked"] = True
    out["accepted"] = bool(out["accepted_by"])
    return out
