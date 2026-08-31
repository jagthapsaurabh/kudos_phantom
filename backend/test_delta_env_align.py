"""Align saved Delta connections at a named environment — no key check needed.

The detection flow (``/test?apply=true``) needs the venue to accept the stored
key before it can name the environment. This suite covers the one-shot path
for when the operator already knows the answer: **the deployment trades Delta
India production** (REST https://api.india.delta.exchange), so the saved
Delta / DeltaGlobal connections are repointed at INDIA-PRODUCTION by name —
broker code + testnet flag — and the next signed call proves the key.

Covers:
* ``BrokerClient.delta_environment`` / ``is_delta_broker`` name registry;
* ``POST /broker-connections/{id}/align`` (single row, validation, scoping);
* ``POST /broker-connections/align-delta`` (bulk, non-Delta rows untouched);
* ``tools/align_delta_env.py`` (dry run, apply, verify, label lookup).

Runs offline: the account-details fetch is stubbed, network is never hit.

Run: cd backend && ../.venv/bin/python test_delta_env_align.py
"""
import importlib.util
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

TESTDB = "/tmp/delta_env_align_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"

import bcrypt                                                        # noqa: E402
from fastapi.testclient import TestClient                            # noqa: E402

import app.main as main_mod                                          # noqa: E402
from app.main import app                                             # noqa: E402
from app.database.models import (SessionLocal, User, BrokerDefinition,  # noqa: E402
                                 BrokerConnection)
from app.services.broker_client import BrokerClient                  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not cond else ""))


def section(title):
    print(f"\n{'=' * 62}\n  {title}\n{'=' * 62}")


# ---------------------------------------------------------------------------
# Fixtures: temp DB + stubbed account-details fetch (no network, ever).
# ---------------------------------------------------------------------------
db = SessionLocal()
BrokerDefinition.__table__.create(bind=db.get_bind(), checkfirst=True)
User.__table__.create(bind=db.get_bind(), checkfirst=True)
BrokerConnection.__table__.create(bind=db.get_bind(), checkfirst=True)
db.query(BrokerConnection).delete()
db.query(User).delete()
db.query(BrokerDefinition).delete()
for code, name, kind in (("Delta", "Delta Exchange", "delta"),
                         ("DeltaGlobal", "Delta Exchange Global", "delta"),
                         ("Binance", "Binance Futures", "binance")):
    db.add(BrokerDefinition(code=code, name=name, kind=kind, is_builtin=1, enabled=1))
db.commit()

_fetch_calls = []


def fake_fetch_connection_settings(d, row):
    """Store a canned venue answer instead of calling the exchange."""
    _fetch_calls.append((row.id, row.broker_code, bool(row.is_testnet)))
    row.account_settings = json.dumps({"margin_mode": "cross", "leverage": 7})
    row.account_settings_at = datetime.utcnow()
    d.commit()


main_mod._fetch_connection_settings = fake_fetch_connection_settings

client = TestClient(app)


def make_user(username, password="client12345"):
    user = User(username=username,
                password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
                role="client", is_active=1, can_paper=1, can_live=1,
                initial_capital=20000.0, margin_deployment_pct=25.0)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def login(username, password="client12345"):
    r = client.post("/token", data={"username": username, "password": password})
    assert r.status_code == 200, r.text[:200]
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def add_connection(user_id, broker_code, label, testnet=0, key="KEY", secret="SECRET"):
    row = BrokerConnection(user_id=user_id, broker_code=broker_code, label=label,
                           api_key=key, api_secret=secret,
                           is_testnet=int(testnet), is_active=1)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ===========================================================================
section("1. the Delta environment name registry resolves and refuses cleanly")
# ===========================================================================
env = BrokerClient.delta_environment("INDIA-PRODUCTION")
check("INDIA-PRODUCTION resolves to Delta India production",
      env and env["broker_code"] == "Delta" and env["testnet"] is False
      and env["url"] == "https://api.india.delta.exchange", str(env))
for alias in ("INDIA_PRODUCTION", "india production", "IndiaProduction", "INDIA"):
    check(f"alias {alias!r} maps to the same environment",
          BrokerClient.delta_environment(alias) == env,
          str(BrokerClient.delta_environment(alias)))
env_t = BrokerClient.delta_environment("GLOBAL-TESTNET")
check("GLOBAL-TESTNET resolves to DeltaGlobal testnet",
      env_t and env_t["broker_code"] == "DeltaGlobal" and env_t["testnet"] is True
      and env_t["url"] == "https://testnet-api.delta.exchange", str(env_t))
check("unknown name resolves to None",
      BrokerClient.delta_environment("MARS-PRODUCTION") is None
      and BrokerClient.delta_environment(None) is None)
check("is_delta_broker accepts every Delta spelling and rejects Binance",
      all(BrokerClient.is_delta_broker(x) for x in
          ("Delta", "DeltaGlobal", "Delta Exchange", "delta exchange"))
      and not BrokerClient.is_delta_broker("Binance")
      and not BrokerClient.is_delta_broker(""))

# ===========================================================================
section("2. POST /broker-connections/{id}/align repoints one connection")
# ===========================================================================
op = make_user("op_delta")
g = add_connection(op.id, "DeltaGlobal", "NishKudos global", testnet=1)
d = add_connection(op.id, "Delta", "NishKudos delta exchange", testnet=1)
b = add_connection(op.id, "Binance", "binance row", testnet=0)
headers = login("op_delta")

r = client.post(f"/broker-connections/{g.id}/align", headers=headers,
                json={"environment": "INDIA_PRODUCTION"})
body = r.json()
check("align accepts and returns 200", r.status_code == 200, r.text[:200])
check("DeltaGlobal + testnet -> Delta + production",
      body.get("applied") is True
      and body.get("broker_code") == "Delta" and body.get("is_testnet") is False
      and body.get("environment") == "INDIA-PRODUCTION"
      and body.get("base_url") == "https://api.india.delta.exchange", str(body))
db.refresh(g)
check("the row itself now points at India production",
      g.broker_code == "Delta" and g.is_testnet == 0, str(g.broker_code))
check("account details were re-read after the align",
      body.get("account_settings", {}).get("margin_mode") == "cross", str(body))

r = client.post(f"/broker-connections/{d.id}/align", headers=headers,
                json={"environment": "INDIA"})
check("the alias spelling is accepted and flips the testnet flag",
      r.status_code == 200 and r.json()["is_testnet"] is False, r.text[:200])

r = client.post(f"/broker-connections/{d.id}/align", headers=headers,
                json={"environment": "MARS-PRODUCTION"})
check("an unknown environment is a 400 naming the real ones",
      r.status_code == 400 and "INDIA-PRODUCTION" in r.json().get("detail", ""),
      r.text[:200])

r = client.post(f"/broker-connections/{b.id}/align", headers=headers,
                json={"environment": "INDIA_PRODUCTION"})
check("a non-Delta connection is refused",
      r.status_code == 400 and "only Delta" in r.json().get("detail", ""),
      r.text[:200])

other = make_user("other_delta")
o = add_connection(other.id, "DeltaGlobal", "foreign row", testnet=1)
r = client.post(f"/broker-connections/{o.id}/align", headers=headers,
                json={"environment": "INDIA_PRODUCTION"})
check("another account's connection is 404, not 400",
      r.status_code == 404, r.text[:200])
r = client.post("/broker-connections/99999/align", headers=headers,
                json={"environment": "INDIA_PRODUCTION"})
check("a missing connection is 404", r.status_code == 404, r.text[:200])

# ===========================================================================
section("3. POST /broker-connections/align-delta repoints every Delta row")
# ===========================================================================
bulk_user = make_user("bulk_delta")
bg = add_connection(bulk_user.id, "DeltaGlobal", "bulk global", testnet=0)
bd = add_connection(bulk_user.id, "Delta", "bulk india demo", testnet=1)
bb = add_connection(bulk_user.id, "Binance", "bulk binance", testnet=1)
bheaders = login("bulk_delta")

r = client.post("/broker-connections/align-delta", headers=bheaders,
                json={"environment": "INDIA_PRODUCTION"})
body = r.json()
check("bulk align returns 200", r.status_code == 200, r.text[:200])
check("both Delta rows were repointed, Binance untouched",
      body.get("delta_connections") == 2 and len(body.get("changed", [])) == 2,
      str(body))
db.refresh(bg); db.refresh(bd); db.refresh(bb)
check("rows landed on Delta · production",
      bg.broker_code == "Delta" and bg.is_testnet == 0
      and bd.broker_code == "Delta" and bd.is_testnet == 0, str((bg.broker_code, bd.broker_code)))
check("the Binance row keeps its own environment",
      bb.broker_code == "Binance" and bb.is_testnet == 1, str((bb.broker_code, bb.is_testnet)))

r = client.post("/broker-connections/align-delta", headers=bheaders,
                json={"environment": "INDIA_PRODUCTION"})
body = r.json()
check("second bulk align is idempotent (nothing changed)",
      r.status_code == 200 and body.get("delta_connections") == 2
      and len(body.get("changed", [])) == 0 and len(body.get("unchanged", [])) == 2,
      str(body))

# ===========================================================================
section("4. tools/align_delta_env.py — the trading-server one-liner")
# ===========================================================================
spec = importlib.util.spec_from_file_location(
    "align_delta_env", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "tools", "align_delta_env.py"))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

saved_argv = sys.argv[:]
try:
    sys.argv = ["align_delta_env.py", "--label", "NishKudos global",
                "--environment", "INDIA_PRODUCTION", "--json"]
    rc = tool.main()
    check("dry run exits 0", rc == 0, rc)
    db.refresh(g)
    check("dry run changed nothing on disk",
          g.broker_code == "Delta" and g.is_testnet == 0, str((g.broker_code, g.is_testnet)))

    sys.argv = ["align_delta_env.py", "--label", "NishKudos global",
                "--environment", "INDIA_TESTNET", "--apply", "--json"]
    rc = tool.main()
    db.refresh(g)
    check("--apply writes the named environment onto the row",
          rc == 0 and g.broker_code == "Delta" and g.is_testnet == 1,
          str((g.broker_code, g.is_testnet)))

    sys.argv = ["align_delta_env.py", "--all-delta",
                "--environment", "INDIA_PRODUCTION", "--apply"]
    rc = tool.main()
    db.refresh(g); db.refresh(d)
    check("--all-delta repoints every Delta-family row (both test users)",
          rc == 0 and g.broker_code == "Delta" and g.is_testnet == 0
          and d.broker_code == "Delta" and d.is_testnet == 0,
          str((g.broker_code, g.is_testnet, d.broker_code, d.is_testnet)))

    sys.argv = ["align_delta_env.py", "--all-delta", "--environment", "NOPE"]
    try:
        tool.main()
        check("unknown environment exits nonzero", False, "no SystemExit")
    except SystemExit as exc:
        check("unknown environment exits nonzero", exc.code != 0, str(exc.code))

    sys.argv = ["align_delta_env.py", "--label", "does not exist"]
    try:
        tool.main()
        check("missing label exits nonzero", False, "no SystemExit")
    except SystemExit as exc:
        check("missing label exits nonzero", exc.code != 0, str(exc.code))

    sys.argv = ["align_delta_env.py", "--all-delta", "--environment", "GLOBAL_TESTNET"]
    try:
        tool.main()
        check("the CLI refuses the Global family on an India box", False, "no SystemExit")
    except SystemExit as exc:
        check("the CLI refuses the Global family on an India box",
              exc.code != 0 and "Global" in str(exc), str(exc))

    # --verify makes one signed probe on the target host (faked here).
    from app.services import delta_key_probe as probe_mod
    probe_calls = []
    original_probe_host = probe_mod.probe_host

    def fake_probe(api_key, api_secret, base_url, testnet=False, broker_code="Delta"):
        probe_calls.append((base_url, testnet, broker_code))
        return {"state": "ok", "detail": "wallet balances OK (signed call accepted)",
                "base_url": base_url, "environment": "production", "family": "india"}

    probe_mod.probe_host = fake_probe
    try:
        sys.argv = ["align_delta_env.py", "--label", "NishKudos global",
                    "--environment", "INDIA_TESTNET", "--apply",
                    "--verify", "--json"]
        rc = tool.main()
        check("--verify probes the target host once after applying",
              rc == 0 and probe_calls == [("https://cdn-ind.testnet.deltaex.org", True, "Delta")],
              str(probe_calls))
    finally:
        probe_mod.probe_host = original_probe_host
finally:
    sys.argv = saved_argv

# ===========================================================================
section("5. deployment rail: India box refuses the Delta Global family")
# ===========================================================================
# Default DELTA_DEPLOYMENT_FAMILY is india. The official rule (India keys →
# production API only; api.delta.exchange = Global) is enforced at the door.
check("deployment family defaults to india",
      BrokerClient.delta_deployment_family() == "india",
      BrokerClient.delta_deployment_family())
check("the rule text names both hosts and the Global exclusion",
      "api.delta.exchange" in BrokerClient.DELTA_FAMILY_RULE
      and "api.india.delta.exchange" in BrokerClient.DELTA_FAMILY_RULE
      and "cdn-ind.testnet.deltaex.org" in BrokerClient.DELTA_FAMILY_RULE
      and "not used by this deployment" in BrokerClient.DELTA_FAMILY_RULE,
      BrokerClient.DELTA_FAMILY_RULE)

rail_user = make_user("rail_delta")
rheaders = login("rail_delta")

r = client.post("/broker-connections", headers=rheaders, json={
    "broker_code": "DeltaGlobal", "label": "would be global",
    "api_key": "K", "api_secret": "S"})
check("creating a DeltaGlobal connection is refused with the rule",
      r.status_code == 400 and "api.delta.exchange" in r.json().get("detail", ""),
      r.text[:300])

r = client.post("/broker-connections", headers=rheaders, json={
    "broker_code": "Delta", "label": "india row", "api_key": "K", "api_secret": "S"})
india_row = r.json()
check("creating a Delta (India) connection still works",
      r.status_code == 200 and india_row.get("broker_code") == "Delta", r.text[:300])

rail_global = add_connection(rail_user.id, "DeltaGlobal", "legacy global row", testnet=1)
r = client.put(f"/broker-connections/{india_row['id']}", headers=rheaders, json={
    "broker_code": "DeltaGlobal", "label": "india row", "api_key": "", "api_secret": "",
    "passphrase": None, "is_testnet": False, "is_active": True})
check("switching a row ONTO DeltaGlobal is refused",
      r.status_code == 400 and "api.delta.exchange" in r.json().get("detail", ""),
      r.text[:300])
db.refresh(rail_global)
r = client.put(f"/broker-connections/{rail_global.id}", headers=rheaders, json={
    "broker_code": "DeltaGlobal", "label": "legacy global row", "api_key": "",
    "api_secret": "", "passphrase": None, "is_testnet": False, "is_active": True})
check("an existing DeltaGlobal row stays editable (align/delete paths open)",
      r.status_code == 200, r.text[:300])

r = client.post(f"/broker-connections/{india_row['id']}/align", headers=rheaders,
                json={"environment": "GLOBAL_PRODUCTION"})
check("aligning a row ONTO Global is refused with the rule",
      r.status_code == 400 and "api.delta.exchange" in r.json().get("detail", ""),
      r.text[:300])
r = client.post("/broker-connections/align-delta", headers=rheaders,
                json={"environment": "GLOBAL_TESTNET"})
check("bulk-aligning ONTO Global is refused too",
      r.status_code == 400, r.text[:300])

# A Global-market box opts out of the rail with DELTA_DEPLOYMENT_FAMILY=global.
os.environ["DELTA_DEPLOYMENT_FAMILY"] = "global"
try:
    check("the env flag flips the deployment family",
          BrokerClient.delta_deployment_family() == "global")
    r = client.post("/broker-connections", headers=rheaders, json={
        "broker_code": "DeltaGlobal", "label": "global box row",
        "api_key": "K", "api_secret": "S"})
    check("on a global box the Global adapter is accepted",
          r.status_code == 200 and r.json().get("broker_code") == "DeltaGlobal",
          r.text[:300])
    r = client.post(f"/broker-connections/{india_row['id']}/align", headers=rheaders,
                    json={"environment": "GLOBAL_PRODUCTION"})
    check("on a global box aligning onto Global is accepted",
          r.status_code == 200, r.text[:300])
finally:
    os.environ["DELTA_DEPLOYMENT_FAMILY"] = "india"

# ===========================================================================
print(f"\n{'=' * 62}\n  {len(PASS)} PASS / {len(FAIL)} FAIL\n{'=' * 62}")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
