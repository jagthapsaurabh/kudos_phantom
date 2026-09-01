"""Test a broker connection end-to-end, read-only, without touching orders.

The one command an operator runs when the terminal says:

    Delta HTTP 401: {"code": "invalid_api_key"}

while market data / seeds keep working. That combination is *always* a
credential-or-environment problem, and this battery separates the causes:

1. public market data on the configured host (network, not key);
2. clock skew (Delta rejects signatures more than 5 s off server time);
3. which of the four Delta environments accepts the key;
4. signed calls (balance, positions, orders, history, preferences);
5. rate-limit quota.

Nothing here places, edits or cancels an order — the check is read-only.

Run on the trading server (a whitelisted key 401s from any other egress IP):

    cd backend && ../.venv/bin/python tools/test_connection.py \\
        --broker Delta --api-key YOUR_KEY --api-secret YOUR_SECRET

or against a saved connection (needs the app database):

    cd backend && ../.venv/bin/python tools/test_connection.py --label "NishKudos"

When the test identifies the environment the key belongs to, apply it in one
step and let running instances re-read it without a restart:

    cd backend && ../.venv/bin/python tools/test_connection.py \\
        --label "NishKudos" --apply --json

Exit code 0 = connection ready; 1 = problems found.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.services.connection_test import run_connection_test  # noqa: E402
from app.services.broker_client import BrokerClient  # noqa: E402


def creds_from_db(label: str):
    """Look the credentials up in the app database by connection label."""
    from app.database.models import BrokerConnection, SessionLocal
    db = SessionLocal()
    try:
        rows = db.query(BrokerConnection).filter(
            BrokerConnection.broker_code.in_(["Delta", "DeltaGlobal"])).all()
        row = next((r for r in rows if (r.label or "") == label), None)
        if row is None:
            names = ", ".join(sorted({r.label or r.broker_code for r in rows})) or "none"
            raise SystemExit(f"no Delta connection labelled {label!r}. Saved: {names}")
        return row
    finally:
        db.close()


def apply_environment(row, detected) -> dict:
    """Point the saved connection at the detected environment and verify."""
    from app.database.models import SessionLocal
    db = SessionLocal()
    try:
        row.broker_code = detected["broker_code"]
        row.is_testnet = int(bool(detected["testnet"]))
        db.add(row)
        db.commit()
        db.refresh(row)
        return {"applied": True, "broker_code": row.broker_code,
                "is_testnet": bool(row.is_testnet),
                "label": row.label or row.broker_code,
                "base_url": detected.get("base_url", "")}
    finally:
        db.close()


def print_human(result: dict) -> None:
    verdict = result.get("verdict", {})
    detected = result.get("detected")
    print(f"\nConnection: {result.get('label')} · broker {result.get('broker_code')} "
          f"· {'testnet/demo' if result.get('is_testnet') else 'production'} "
          f"· {result.get('configured_base_url')}")
    print(f"Result:     {'READY' if verdict.get('ok') else 'ISSUES FOUND'}")
    for step in result.get("steps", []):
        mark = {"ok": "  OK ", "permission": "  PERMISSION", "auth": "  REJECTED",
                "error": "  FAIL", "skipped": "  SKIP", "unreachable": "  NET "}.get(
            step.get("state"), f"  {str(step.get('state'))[:8].upper():8s}")
        print(f"{mark} {step.get('title', step.get('name', ''))}")
        if step.get("state") not in (None, "ok", "skipped") and step.get("endpoint"):
            print(f"       tried: {step.get('endpoint')}")
        if step.get("detail"):
            print(f"       {step.get('detail')}")
        for row in step.get("rows") or []:
            state = row.get("state")
            print(f"       - {row.get('name'):16s} {row.get('base_url', '')} "
                  f"-> {'accepts' if state == 'ok' else 'knows key, permission missing' if state == 'permission' else 'rejects' if state == 'auth' else 'unreachable'}")
    for problem in verdict.get("problems", []):
        print(f"\n  PROBLEM: {problem}")
    for fix in verdict.get("fixes", []):
        print(f"  FIX:     {fix}")
    if detected:
        print(f"\nDetected environment: {detected.get('name')} · broker "
              f"{detected.get('broker_code')} · {'testnet/demo' if detected.get('testnet') else 'production'}"
              f" · {detected.get('base_url')}")
        same = (str(result.get("broker_code")) == str(detected.get("broker_code", ""))
                and bool(result.get("is_testnet")) == bool(detected.get("testnet")))
        if not same:
            print("  Tip: re-run with --apply to point this saved connection at that "
                  "environment (running instances re-read it by themselves).")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--api-key", help="API key to test (pair with --api-secret)")
    src.add_argument("--label", help="BrokerConnection label to load from the app DB")
    ap.add_argument("--api-secret", default="", help="API secret (or set DELTA_API_SECRET)")
    ap.add_argument("--broker", default="Delta",
                    help="broker code: Delta (India) or DeltaGlobal")
    ap.add_argument("--testnet", action="store_true",
                    help="connection currently points at testnet/demo (default: production)")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    ap.add_argument("--apply", action="store_true",
                    help="with --label: point the saved connection at the detected "
                         "environment (updates broker code + testnet flag)")
    args = ap.parse_args()

    if args.label:
        row = creds_from_db(args.label)
        from app.core.secrets import decrypt_secret, SecretDecryptionError
        api_key = row.api_key or ""
        try:
            api_secret = decrypt_secret(row.api_secret) or ""
        except SecretDecryptionError as exc:
            raise SystemExit(str(exc))
        broker_code = str(row.broker_code or args.broker)
        testnet = bool(row.is_testnet)
        label = row.label or broker_code
    else:
        api_key = args.api_key or ""
        api_secret = args.api_secret or os.environ.get("DELTA_API_SECRET", "")
        if not api_key or not api_secret:
            raise SystemExit("--api-key and --api-secret are both required (use --label "
                             "to load them from a saved connection)")
        broker_code = "DeltaGlobal" if args.broker.lower() == "deltaglobal" else args.broker
        testnet = bool(args.testnet)
        label = args.broker

    result = run_connection_test(api_key, api_secret, broker_code=broker_code,
                                 testnet=testnet, label=label)

    if args.apply and args.label:
        detected = result.get("detected")
        if detected:
            applied = apply_environment(row, detected)
            result["applied"] = applied
            result["message_after_apply"] = (
                f"Saved: connection '{applied['label']}' now points at broker "
                f"{applied['broker_code']} ({'testnet/demo' if applied['is_testnet'] else 'production'}). "
                "Running instances re-read the saved credentials by themselves (or use "
                "Live Trade → Reload keys); no restart needed.")
        else:
            result["applied"] = {"applied": False,
                                 "reason": "no environment accepted the key — nothing changed"}

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print_human(result)
        if result.get("message_after_apply"):
            print(f"\n{result['message_after_apply']}")
    return 0 if result.get("verdict", {}).get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
