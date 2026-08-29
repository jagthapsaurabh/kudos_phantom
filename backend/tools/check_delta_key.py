"""Which Delta India environment does an API key actually belong to?

Symptom this answers: a live instance reports

    heartbeat.last_error: 'Delta HTTP 401: {"code": "invalid_api_key"}'
    heartbeat.created: false, acks: 0, failures rising

while is_running stays true and market data keeps updating (public
endpoints are unsigned — only *signed* calls 401). broker_client.py
documents the classic cause: production keys on the testnet host (or the
reverse) return InvalidApiKey.

The probe signs ``GET /v2/wallet/balances`` — the key ping recommended
after the 19.08.26 changelog locked /v2/profile out of API-key access —
against BOTH Delta India hosts using the same BrokerClient the live
trader uses, then prints a verdict.

Run on the trading server (not from a laptop with a different egress IP):

    cd backend && ../.venv/bin/python tools/check_delta_key.py \
        --api-key YOUR_KEY --api-secret YOUR_SECRET

or read the credentials from the running app's database:

    cd backend && ../.venv/bin/python tools/check_delta_key.py \
        --label "Delta Nishant sir"

Exit code 0 = the key works on at least one host (verdict printed),
1 = rejected by both.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.services.broker_client import BrokerClient  # noqa: E402

PRODUCTION = BrokerClient.DELTA_PRODUCTION
TESTNET = BrokerClient.DELTA_TESTNET


def probe(api_key: str, api_secret: str, base_url: str, testnet: bool) -> tuple:
    """Return (state, detail) for a signed wallet-balance ping on one host.

    state is one of "ok" (key accepted), "auth" (host answered and rejected
    the key), "unreachable" (never got an HTTP answer — local network issue,
    says nothing about the key).
    """
    client = BrokerClient(api_key, api_secret, "Delta", testnet=testnet)
    # Belt and braces: force the host we were asked about.
    client.trading_url = client.market_url = base_url
    payload = client.get_account_balance()
    if isinstance(payload, dict) and payload.get("error"):
        detail = str(payload["error"])
        if "request failed" in detail or "non-JSON body" in detail:
            # Transport-level failure (SSL/timeout/DNS) — not an auth verdict.
            if any(tok in detail for tok in ("SSLError", "ConnectionError",
                                             "Timeout", "Max retries",
                                             "Failed to resolve")):
                return "unreachable", detail
        if "HTTP 401" in detail or "invalid_api_key" in detail:
            return "auth", detail
        return "auth", detail
    if isinstance(payload, dict) and payload.get("success") is False:
        return "auth", str(payload)
    return "ok", "wallet balances OK (signed call accepted)"


def creds_from_db(label: str):
    """Look the credentials up in the app database by connection label."""
    try:
        from app.database.models import SessionLocal, BrokerConnection
    except Exception as exc:  # pragma: no cover - import guard
        raise SystemExit(f"cannot import app models: {exc}")
    db = SessionLocal()
    try:
        rows = db.query(BrokerConnection).filter(
            BrokerConnection.broker_code == "Delta").all()
        row = next((r for r in rows if (r.label or "") == label), None)
        if row is None:
            names = ", ".join(sorted({r.label or r.broker_code for r in rows})) or "none"
            raise SystemExit(f"no Delta connection labelled {label!r}. Saved: {names}")
        return row.api_key, row.api_secret, bool(row.is_testnet), row
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--api-key", help="API key to test (pair with --api-secret)")
    src.add_argument("--label", help="BrokerConnection label to load from the app DB")
    ap.add_argument("--api-secret", default="", help="API secret (or set DELTA_API_SECRET)")
    ap.add_argument("--heartbeat-id", default="",
                    help="also POST /v2/heartbeat/create with this id on the working host")
    args = ap.parse_args()

    if args.label:
        api_key, api_secret, flagged_testnet, row = creds_from_db(args.label)
        print(f"connection {args.label!r}: api_key={api_key[:6]}...{api_key[-4:] if len(api_key) > 10 else ''} "
              f"is_testnet={int(flagged_testnet)}")
    else:
        api_key = args.api_key
        api_secret = args.api_secret or os.environ.get("DELTA_API_SECRET", "")
        if not api_secret:
            raise SystemExit("--api-secret (or DELTA_API_SECRET) is required with --api-key")
        flagged_testnet = None

    print()
    verdict = None
    saw_auth_rejection = False
    for name, url, testnet in (("PRODUCTION", PRODUCTION, False),
                               ("TESTNET", TESTNET, True)):
        state, detail = probe(api_key, api_secret, url, testnet)
        mark = {"ok": "ACCEPTS the key",
                "auth": "rejects the key",
                "unreachable": "UNREACHABLE (network — no verdict)"}[state]
        print(f"  {name:10s} {url}")
        print(f"             -> {mark}: {detail}")
        if state == "ok":
            verdict = (name, url, testnet)
        elif state == "auth":
            saw_auth_rejection = True

    print()
    if verdict is None:
        if not saw_auth_rejection:
            print("VERDICT: no host answered — fix network/DNS from this machine first;\n"
                  "       this run says nothing about the key itself.")
        else:
            print("VERDICT: rejected by the host(s) that answered — the key is dead\n"
                  "       (deleted, regenerated, copy/paste damage, or it belongs to Delta\n"
                  "       global rather than Delta India). Create a fresh key in the panel\n"
                  "       of the environment you want to trade.")
        return 1

    name, url, testnet = verdict
    print(f"VERDICT: this is a {name} key ({url}).")
    if flagged_testnet is None:
        return 0
    if flagged_testnet != testnet:
        print(f"  MISMATCH: the connection is flagged is_testnet={int(flagged_testnet)} "
              f"but the key only works on {name}.")
        print("  Fix: edit the connection in Broker Settings and set the Testnet toggle "
              f"to {'ON' if testnet else 'OFF'}, then stop and restart the live instance.")
    else:
        print("  The connection's testnet flag matches the key. If the live instance still "
              "401s, it was started with older credentials — stop it and start it again "
              "(credentials are read once, at instance start).")

    if args.heartbeat_id:
        client = BrokerClient(api_key, api_secret, "Delta", testnet=testnet)
        payload = client.create_heartbeat(args.heartbeat_id,
                                          product_symbols=["BTCUSD"],
                                          config=[{"action": "cancel_orders",
                                                   "unhealthy_count": 1}])
        ok = isinstance(payload, dict) and not payload.get("error")
        print(f"\n  heartbeat/create {args.heartbeat_id!r}: "
              f"{'OK' if ok else payload.get('error')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
