"""Which Delta environment an API key actually belongs to.

The symptom is always the same and never self-explanatory: every signed call
answers ``HTTP 401 {"code": "invalid_api_key"}`` while candles, tickers and the
mark price keep working, because the public endpoints are unsigned. That single
error string covers six different situations —

1. the key was regenerated or deleted in the exchange panel;
2. it was pasted incompletely (Delta keys are long, and a truncated paste is
   still syntactically plausible);
3. it is a **production** key on a **testnet** connection (same family);
4. it is a **testnet** key on a **production** connection (same family);
5. it is an **India** key but the connection targets **Delta Global**;
6. it is a **Global** key but the connection targets **Delta India**.

3–6 are indistinguishable from 1–2 by looking at the key, and they are the ones
the operator can fix without creating anything. Delta's two markets (India and
Global) keep **separate key stores**, and each has its own production and demo
store, so the probe signs ``GET /v2/wallet/balances`` against **all four hosts**;
whichever one accepts the key is the environment the connection has to point at.
``/v2/profile`` is deliberately not used as the ping — from the 19.08.26
changelog it is no longer reachable with API keys — so the probe signs the call
the docs leave for a key check.

Two callers share this: the ``tools/check_delta_key.py`` script (run it on the
trading box, since a whitelisted key 401s from any other egress IP) and the
``POST /broker-connections/{id}/probe`` endpoint behind Broker Settings → **Check
key**, so the answer is in the same screen as the form that fixes it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.broker_client import BrokerClient

# All four Delta REST environments, in the order they are probed.
HOSTS: List[Dict[str, Any]] = BrokerClient.delta_hosts()

# Transport-level failures say nothing about the key — they say the machine
# could not reach the host, which is its own bug (DNS, SSL, egress IP, proxy).
_TRANSPORT_TOKENS = ("SSLError", "ConnectionError", "Timeout", "Max retries",
                     "Failed to resolve", "request failed", "non-JSON body")

# The venue *knows* the key but refuses the endpoint: key permissions
# (Read Data / Trading). That is proof the key exists on this host — exactly
# what an environment check needs — so it must not read as "rejected".
_PERMISSION_TOKENS = ("unauthorizedapiaccess", "unauthorized_api_access",
                      "not authorised", "not authorized", "forbidden",
                      "permission", "api key not authorised")


def _state_for(detail: str) -> str:
    """``"unreachable"`` / ``"auth"`` / ``"permission"`` for one host's answer."""
    if any(token in detail for token in _TRANSPORT_TOKENS):
        return "unreachable"
    if any(token in detail for token in _PERMISSION_TOKENS):
        return "permission"
    return "auth"


def probe_host(api_key: str, api_secret: str, base_url: str,
               testnet: bool = False, broker_code: str = "Delta") -> Dict[str, Any]:
    """Sign one ``GET /v2/wallet/balances`` against one host.

    Returns ``{"state", "detail", "base_url", "environment"}`` — never raises,
    because a probe that breaks the request it was called from is worse than a
    probe that reports the failure. ``state`` is one of:

    * ``ok``         — signed call accepted (key + permissions good);
    * ``permission`` — host accepted the key but the endpoint needs a
                       permission the key does not have (still identifies the
                       environment; the operator must enable Trading / Read);
    * ``auth``       — host answered and rejected the key;
    * ``unreachable``— never got an HTTP answer (network — no verdict).
    """
    environment = ("testnet" if testnet else "production")
    family = "global" if str(broker_code).lower().startswith("deltaglobal") else "india"
    if not (api_key and api_secret):
        return {"state": "no_credentials", "detail": "no API key/secret to test",
                "base_url": base_url, "environment": environment, "family": family}
    try:
        client = BrokerClient(api_key, api_secret, broker_code, testnet=testnet)
        # Belt and braces: probe exactly the host we were asked about, whatever
        # the broker definition's URLs say.
        client.trading_url = client.market_url = base_url.rstrip("/")
        payload = client.get_account_balance()
    except Exception as exc:  # pragma: no cover - defensive
        return {"state": "unreachable", "detail": f"{exc.__class__.__name__}: {exc}",
                "base_url": base_url, "environment": environment, "family": family}
    if isinstance(payload, dict) and payload.get("success") is False:
        detail = str(payload)[:300]
        return {"state": _state_for(detail), "detail": detail, "base_url": base_url,
                "environment": environment, "family": family}
    detail = str(payload.get("error") or "") if isinstance(payload, dict) else ""
    if detail:
        return {"state": _state_for(detail), "detail": detail[:300],
                "base_url": base_url, "environment": environment, "family": family}
    return {"state": "ok", "detail": "wallet balances OK (signed call accepted)",
            "base_url": base_url, "environment": environment, "family": family}


def probe_key(api_key: str, api_secret: str,
              hosts: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Probe every host. At most four signed calls, once per click."""
    rows = []
    for host in (hosts or HOSTS):
        row = probe_host(api_key, api_secret, host["url"], host.get("testnet", False),
                         host.get("broker_code", "Delta"))
        row["name"] = host["name"]
        row["broker_code"] = host.get("broker_code", "Delta")
        row["testnet"] = bool(host.get("testnet", False))
        row["site"] = host.get("site", "")
        rows.append(row)
    return rows


def verdict(rows: List[Dict[str, Any]], flagged_testnet: Optional[bool] = None,
            flagged_broker: str = "Delta", label: str = "") -> Dict[str, Any]:
    """Plain-language reading of a :func:`probe_key` result.

    ``flagged_testnet`` + ``flagged_broker`` are what the *connection* says;
    when they disagree with what the key says, that mismatch is the whole bug
    and the fix is one edit, not a new key.
    """
    # A host that answered "permission" still proves the key exists there.
    accepted = next((row for row in rows if row.get("state") in ("ok", "permission")), None)
    answered = [row for row in rows if row.get("state") in ("ok", "permission", "auth")]
    unreachable = [row for row in rows if row.get("state") == "unreachable"]
    permission_only = [row for row in rows if row.get("state") == "permission"]
    out: Dict[str, Any] = {
        "environment": (accepted or {}).get("environment"),
        "family": (accepted or {}).get("family"),
        "base_url": (accepted or {}).get("base_url"),
        "accepted_by": [row["name"] for row in rows if row.get("state") in ("ok", "permission")],
        "rejected_by": [row["name"] for row in rows if row.get("state") == "auth"],
        "unreachable_from_here": [row["name"] for row in unreachable],
        "permission_gap": bool(permission_only),
        "matches_connection": None,
        "mismatch": False,
        "summary": "",
        "fix": "",
        "rows": rows,
        # What the operator should point the connection at, when known.
        "detected": ({"broker_code": accepted["broker_code"], "testnet": bool(accepted["testnet"]),
                      "name": accepted["name"], "base_url": accepted["base_url"]}
                     if accepted is not None else None),
    }
    if accepted is not None:
        name = accepted["name"]
        family_name = "Delta Global" if accepted.get("family") == "global" else "Delta India"
        needs_permission = accepted.get("state") == "permission"
        out["summary"] = (f"{label or 'This key'} is accepted by {name} "
                          f"({accepted['base_url']}) — it is a {family_name} key."
                          + (" The key is missing an endpoint permission."
                             if needs_permission else ""))
        matches = (str(flagged_broker).lower() == str(accepted.get("broker_code", "")).lower()
                   and bool(flagged_testnet) == bool(accepted["testnet"]))
        out["matches_connection"] = bool(matches)
        if matches:
            out["fix"] = ("The environment flag matches the key. If a live instance "
                          "still 401s it was started with older credentials — Reload "
                          "keys on the instance (no restart needed)."
                          + (" Enable the missing permission on this key in API "
                             "Management (Read Data / Trading)." if needs_permission else ""))
        else:
            out["mismatch"] = True
            # India/Global mismatch is the one that surprised people the most:
            # an India probe that rejects it used to be reported as "dead key".
            same_family = str(flagged_broker).lower() == str(accepted.get("broker_code", "")).lower()
            if not same_family:
                out["summary"] += (f" (India and Global keep separate key stores, so this "
                                   f"key never works on {flagged_broker}.)")
                out["fix"] = (f"The connection uses {flagged_broker} but this key only works on "
                              f"{name}. Use 'Test connection' → 'Use this environment' to point "
                              f"the saved connection at {accepted['broker_code']} — running "
                              "instances re-read the credentials by themselves, no restart.")
            else:
                out["fix"] = ("Flip the testnet toggle on this connection to "
                              f"{'ON' if accepted['testnet'] else 'OFF'} and save — running "
                              "instances re-read the saved credentials by themselves, so no "
                              "restart is needed.")
        return out
    if answered:
        out["summary"] = (f"{label or 'This key'} is rejected by every Delta host that "
                          f"answered ({', '.join(r['name'] for r in answered)}).")
        out["fix"] = ("The key is dead: deleted, rotated, or pasted incompletely (check "
                      "the last characters). Create a fresh key in the panel of the "
                      "environment you want to trade, paste key AND secret, and save. "
                      "If the key is IP-whitelisted, run the probe on the trading server "
                      "— a different egress IP 401s the same way a bad key does.")
        return out
    out["summary"] = ("No Delta host answered from this machine, so this says "
                      "nothing about the key.")
    out["fix"] = ("Fix connectivity/DNS/SSL on the trading server first "
                  "(the probe runs from the box, not the browser).")
    return out


def probe_single_host(broker_name: str, api_key: str, api_secret: str,
                      testnet: bool = False) -> Dict[str, Any]:
    """One signed ping on the venue's own host, shaped like :func:`probe_host`.

    Used for every non-Delta broker: their keys are not shared between
    environments the way Delta's are, so there is a host to check and no
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
            "environment": "testnet" if testnet else "production",
            "broker_code": broker_name, "site": ""}


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
            "matches_connection": None, "mismatch": False, "permission_gap": False,
            "detected": ({"broker_code": broker_name,
                          "testnet": bool(flagged_testnet),
                          "name": row["name"], "base_url": row["base_url"]} if accepted else None),
        }
    rows = probe_key(api_key, api_secret)
    out = verdict(rows, flagged_testnet=flagged_testnet,
                  flagged_broker=broker_name or "Delta", label=label)
    out["broker"] = broker_name or "Delta"
    out["environment_checked"] = True
    out["accepted"] = bool(out["accepted_by"])
    return out
