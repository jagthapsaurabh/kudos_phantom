"""Point saved Delta connections at a named environment — without a key check.

The connection battery (``tools/test_connection.py``) only *detects* the
environment when the venue accepts the stored key. When the operator already
knows the answer — e.g. the key was just created on **Delta India
production** — this tool applies that decision directly: it flips each saved
Delta-family connection's broker code + testnet flag to the named
environment and lets the next signed call prove the key.

Run it **on the trading server** (a whitelisted key only validates from the
box whose egress IP the panel trusts):

    cd backend && ../.venv/bin/python tools/align_delta_env.py --label "NishKudos global"

    # align every Delta / DeltaGlobal connection of every account in one shot:
    cd backend && ../.venv/bin/python tools/align_delta_env.py --all-delta --apply

    # the deployment decision for this system — Delta India production
    # (REST https://api.india.delta.exchange, private WS
    # wss://socket.india.delta.exchange, public WS
    # wss://public-socket.india.delta.exchange):
    cd backend && ../.venv/bin/python tools/align_delta_env.py \
        --environment INDIA_PRODUCTION --all-delta --apply --verify

Without ``--apply`` the tool only reports what would change. ``--verify``
makes one signed ``GET /v2/wallet/balances`` against the target host with the
stored key and reports the answer; it never blocks the apply.

Exit code 0 = applied or nothing to change; 1 = error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.database.models import BrokerConnection, SessionLocal  # noqa: E402
from app.services.broker_client import BrokerClient  # noqa: E402


def delta_connection_rows():
    """Every Delta-family BrokerConnection row, whatever the account."""
    db = SessionLocal()
    rows = db.query(BrokerConnection).all()
    return [r for r in rows if BrokerClient.is_delta_broker(r.broker_code)], db


def current_environment(row) -> dict:
    """The environment a saved connection currently points at, by name."""
    code = "Delta" if str(row.broker_code or "").lower() in ("delta", "delta exchange") \
        else str(row.broker_code or "")
    testnet = bool(row.is_testnet)
    name = ("INDIA-PRODUCTION" if code == "Delta" and not testnet else
            "INDIA-TESTNET" if code == "Delta" else
            "GLOBAL-PRODUCTION" if not testnet else "GLOBAL-TESTNET")
    return {"broker_code": code, "is_testnet": testnet, "name": name}


def describe_row(row, target):
    """Current vs target environment for one saved connection."""
    current = current_environment(row)
    return {
        "connection_id": row.id,
        "label": row.label or row.broker_code,
        "current": current,
        "target": {"broker_code": target["broker_code"],
                   "is_testnet": bool(target["testnet"]),
                   "name": target["name"], "base_url": target["url"]},
        "change": (current["broker_code"] != target["broker_code"]
                   or current["is_testnet"] != bool(target["testnet"])),
    }


def apply_row(db, row, target):
    """Write the target environment onto one row; returns the before/after."""
    before = describe_row(row, target)["current"]
    row.broker_code = target["broker_code"]
    row.is_testnet = int(bool(target["testnet"]))
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"before": before,
            "after": {"broker_code": row.broker_code,
                      "is_testnet": bool(row.is_testnet)}}


def verify_key(row, target):
    """One signed ping on the target host with the stored key (report only)."""
    from app.services.delta_key_probe import probe_host
    if not (row.api_key and row.api_secret):
        return {"state": "no_credentials",
                "detail": "connection stores no key/secret — paste the new key first"}
    result = probe_host(row.api_key, row.api_secret, target["url"],
                        bool(target["testnet"]), target["broker_code"])
    return {"state": result["state"], "detail": result["detail"],
            "base_url": result["base_url"]}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    scope = ap.add_mutually_exclusive_group(required=True)
    scope.add_argument("--label", help="align the connection with this label")
    scope.add_argument("--all-delta", action="store_true",
                       help="align every Delta / DeltaGlobal connection (all accounts)")
    ap.add_argument("--environment", default="INDIA_PRODUCTION",
                    help="canonical Delta environment name (default: INDIA_PRODUCTION)")
    ap.add_argument("--apply", action="store_true",
                    help="write the change (default: report only)")
    ap.add_argument("--verify", action="store_true",
                    help="after applying, probe the target host once with the stored key")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args()

    target = BrokerClient.delta_environment(args.environment)
    if target is None:
        known = ", ".join(h["name"] for h in BrokerClient.delta_hosts())
        raise SystemExit(f"unknown Delta environment {args.environment!r}; use one of: {known}")

    rows, db = delta_connection_rows()
    if args.label:
        label = args.label
        rows = [r for r in rows if (r.label or r.broker_code) == label]
        if not rows:
            names = ", ".join(sorted({(r.label or r.broker_code) for r in
                                      db.query(BrokerConnection).all()})) or "none"
            db.close()
            raise SystemExit(f"no Delta connection labelled {label!r}. Saved: {names}")
    if not rows:
        db.close()
        raise SystemExit("no Delta-family connections found in the database")

    report = {"environment": target["name"], "base_url": target["url"],
              "apply": bool(args.apply), "connections": []}
    changed_any = False
    try:
        for row in rows:
            entry = describe_row(row, target)
            if entry["change"]:
                changed_any = True
                if args.apply:
                    applied = apply_row(db, row, target)
                    entry["applied"] = applied["after"]
                    if args.verify:
                        entry["verify"] = verify_key(row, target)
            else:
                entry["already_aligned"] = True
            report["connections"].append(entry)
    finally:
        db.close()

    report["changed"] = changed_any
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        verb = ("Aligned" if args.apply and changed_any else
                "Would align" if changed_any else "Already aligned")
        print(f"\n{verb} {len(report['connections'])} Delta connection(s) at "
              f"{report['environment']} ({report['base_url']})")
        for entry in report["connections"]:
            c = entry["current"]
            arrow = "->" if entry["change"] else "=="
            print(f"  {arrow} {entry['label']}: {c['broker_code']}"
                  f"{' testnet/demo' if c['is_testnet'] else ' production'} "
                  f"{entry['target']['broker_code']}"
                  f"{' testnet/demo' if entry['target']['is_testnet'] else ' production'}")
            if entry.get("verify"):
                v = entry["verify"]
                print(f"     key check on {v.get('base_url')}: {v['state']} — {v['detail']}")
        if changed_any and not args.apply:
            print("\nDry run — re-run with --apply to write the change.")
        elif changed_any and args.apply:
            print("\nDone. Running live instances re-read the saved connections "
                  "by themselves (or use Live Trade → Reload keys); no restart.")
            if args.verify:
                print("Reminder: a whitelisted key only validates from the trading "
                      "server's egress IP — run this tool there.")
        else:
            print("\nNothing to change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
