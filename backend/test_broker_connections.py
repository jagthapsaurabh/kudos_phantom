"""Broker credentials: which saved connection a live call actually uses.

"API keys not configured for Binance. Add them in Broker Settings." used to
cover five different situations that look identical from the browser:

1. no connection saved on this login at all
2. a connection saved under the *display name* (``Binance Futures``) instead of
   the registry code (``Binance``) — hand-edited rows and seeded rows do this
3. a connection inserted straight into the database, so ``is_active`` is NULL
   (the column default only applies to rows written through SQLAlchemy)
4. a connection that was switched off
5. keys saved while signed in as a different account (connections are per-user)

Only 1, 4 and 5 are real problems, and each now says which one it is. This
suite drives the real endpoints and the real ``_live_client`` resolution.

Run:  cd backend && ../.venv/bin/python test_broker_connections.py
"""
import os
import sys

sys.path.insert(0, '.')

TESTDB = "/tmp/broker_connections_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"

import bcrypt                                                        # noqa: E402
from fastapi.testclient import TestClient                            # noqa: E402
from sqlalchemy import text                                          # noqa: E402

from app.main import app, _live_client                               # noqa: E402
from app.database.models import (SessionLocal, User, BrokerDefinition,  # noqa: E402
                                 BrokerConnection)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not cond else ""))


def section(title):
    print(f"\n{'=' * 62}\n  {title}\n{'=' * 62}")


db = SessionLocal()
BrokerDefinition.__table__.create(bind=db.get_bind(), checkfirst=True)
User.__table__.create(bind=db.get_bind(), checkfirst=True)
BrokerConnection.__table__.create(bind=db.get_bind(), checkfirst=True)
db.query(BrokerConnection).delete()
db.query(User).delete()
db.query(BrokerDefinition).delete()
db.add(BrokerDefinition(code="Binance", name="Binance Futures", kind="binance",
                        is_builtin=1, enabled=1))
db.add(BrokerDefinition(code="Delta", name="Delta Exchange", kind="delta",
                        is_builtin=1, enabled=1))


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
    response = client.post("/token", data={"username": username, "password": password})
    assert response.status_code == 200, response.text[:200]
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


client = TestClient(app)

# ===========================================================================
section("1. a saved connection is picked up however its code is spelled")
# ===========================================================================
canonical = make_user("canonical")
db.add(BrokerConnection(user_id=canonical.id, broker_code="Binance", label="primary",
                        api_key="KEY-CANON", api_secret="SECRET", is_active=1))
display_name = make_user("display_name")
db.add(BrokerConnection(user_id=display_name.id, broker_code="Binance Futures", label="primary",
                        api_key="KEY-NAME", api_secret="SECRET", is_active=1))
lowercase = make_user("lowercase")
db.add(BrokerConnection(user_id=lowercase.id, broker_code="binance", label="primary",
                        api_key="KEY-LOWER", api_secret="SECRET", is_active=1))
db.commit()

for user, expected_key in [(canonical, "KEY-CANON"), (display_name, "KEY-NAME"),
                           (lowercase, "KEY-LOWER")]:
    built, definition, connection_id = _live_client(db, user, "Binance")
    check(f"'{user.username}' resolves to a client", built is not None and built.api_key == expected_key,
          f"api_key={getattr(built, 'api_key', None)}")
    check(f"'{user.username}' keeps its connection id", connection_id is not None)
    check(f"'{user.username}' uses the Binance adapter", definition.code == "Binance")

# ===========================================================================
section("2. is_active NULL (hand-inserted row) is treated as ON")
# ===========================================================================
null_active = make_user("null_active")
db.execute(text("INSERT INTO broker_connections "
                "(user_id, broker_code, label, api_key, api_secret, is_active) "
                "VALUES (:u, 'Binance', 'primary', 'KEY-NULL', 'SECRET', NULL)"),
           {"u": null_active.id})
db.commit()
built, _, connection_id = _live_client(db, null_active, "Binance")
check("a NULL is_active row still supplies credentials",
      built is not None and built.api_key == "KEY-NULL", f"api_key={getattr(built, 'api_key', None)}")

switched_off = make_user("switched_off")
db.add(BrokerConnection(user_id=switched_off.id, broker_code="Binance", label="primary",
                        api_key="KEY-OFF", api_secret="SECRET", is_active=0))
db.commit()
try:
    _live_client(db, switched_off, "Binance")
    check("a switched-off connection is refused", False, "no error raised")
except Exception as exc:
    detail = getattr(exc, "detail", str(exc))
    check("a switched-off connection is refused", "switched off" in detail, detail)
    check("the refusal names the account and the fix",
          "switched_off" in detail and "Broker Settings" in detail, detail)

# ===========================================================================
section("3. the error says which cause applies")
# ===========================================================================
no_keys = make_user("no_keys")
try:
    _live_client(db, no_keys, "Binance")
    check("missing keys raise", False, "no error raised")
except Exception as exc:
    detail = getattr(exc, "detail", str(exc))
    check("missing keys name the account", "no_keys" in detail, detail)
    check("missing keys explain the Exchange Registry holds no credentials",
          "Exchange Registry" in detail and "Add broker connection" in detail, detail)

other_account = make_user("other_account")
try:
    _live_client(db, other_account, "Binance")
    check("another login's keys are not shared", False, "no error raised")
except Exception as exc:
    detail = getattr(exc, "detail", str(exc))
    check("another login's keys are not shared", "No API keys" in detail, detail)

# A specific connection id that belongs to another broker / does not exist.
try:
    _live_client(db, canonical, "Binance", connection_id=999999)
    check("an unknown connection id is refused", False, "no error raised")
except Exception as exc:
    detail = getattr(exc, "detail", str(exc))
    check("an unknown connection id is refused", "999999" in detail, detail)

# ===========================================================================
section("4. GET /broker-connections/diagnose")
# ===========================================================================
headers = login("display_name")
r = client.get("/broker-connections/diagnose", params={"broker": "Binance"}, headers=headers)
check("diagnose answers 200", r.status_code == 200, r.text[:200])
body = r.json()
check("diagnose names the account", body["account"] == "display_name", str(body.get("account")))
check("diagnose reports the registry entry", body["definition"]["code"] == "Binance"
      and body["definition"]["name"] == "Binance Futures", str(body.get("definition")))
check("diagnose shows the stored vs resolved code",
      body["connections"][0]["stored_code"] == "Binance Futures"
      and body["connections"][0]["resolved_code"] == "Binance", str(body["connections"]))
check("diagnose says the broker is ready to trade", body["ready"] is True, str(body.get("problems")))
check("the secret is never returned in full",
      body["connections"][0]["has_secret"] is True and "SECRET" not in r.text)

headers = login("no_keys")
r = client.get("/broker-connections/diagnose", params={"broker": "Binance"}, headers=headers)
body = r.json()
check("diagnose reports not ready when no keys exist", body["ready"] is False)
check("diagnose lists the problem", len(body["problems"]) >= 1, str(body.get("problems")))

headers = login("switched_off")
body = client.get("/broker-connections/diagnose",
                  params={"broker": "Binance"}, headers=headers).json()
check("diagnose reports a switched-off connection", body["ready"] is False
      and any("switched off" in p for p in body["problems"]), str(body.get("problems")))

headers = login("no_keys")
body = client.get("/broker-connections/diagnose", params={"broker": "Delta"}, headers=headers).json()
check("diagnose works for a second broker", body["broker"] == "Delta" and body["ready"] is False)

# ===========================================================================
section("5. connection CRUD still round-trips")
# ===========================================================================
headers = login("no_keys")
r = client.post("/broker-connections", headers=headers, json={
    "broker_code": "Binance Futures", "label": "main", "api_key": "KEY-NEW",
    "api_secret": "SECRET-NEW", "is_testnet": False})
check("POST accepts the display name", r.status_code == 200, r.text[:200])
created = r.json()
check("POST stores the canonical code", created["broker_code"] == "Binance", str(created))
check("POST masks the key", "KEY-NEW" not in r.text and created["has_secret"] is True)

body = client.get("/broker-connections/diagnose",
                  params={"broker": "Binance"}, headers=headers).json()
check("the new connection makes the broker ready", body["ready"] is True, str(body.get("problems")))

r = client.put(f"/broker-connections/{created['id']}", headers=headers, json={
    "broker_code": "Binance", "label": "main", "api_key": "", "api_secret": "",
    "is_testnet": True})
check("PUT with blank secrets keeps the stored ones", r.status_code == 200
      and r.json()["has_secret"] is True, r.text[:200])
check("PUT records testnet", r.json()["is_testnet"] is True)

r = client.delete(f"/broker-connections/{created['id']}", headers=headers)
check("DELETE removes the connection", r.status_code == 200, r.text[:200])
body = client.get("/broker-connections/diagnose",
                  params={"broker": "Binance"}, headers=headers).json()
check("the broker is not ready again after delete", body["ready"] is False)

r = client.post("/broker-connections", headers=headers,
                json={"broker_code": "Binance", "label": "x", "api_key": "K"})
check("a secret is still required", r.status_code == 400, r.text[:200])

# ===========================================================================
section("6. the terminal endpoint carries the precise message through")
# ===========================================================================
r = client.post("/live-account/snapshot", headers=login("no_keys"),
                json={"broker": "Binance", "include_history": False})
check("snapshot refuses with 400", r.status_code == 400, r.text[:200])
detail = r.json().get("detail", "")
check("the 400 explains the Registry/connection split",
      "Exchange Registry" in detail and "Add broker connection" in detail, detail)

# ===========================================================================
print(f"\n{'=' * 62}")
print(f"  PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
if FAIL:
    print("  Failures:")
    for name in FAIL:
        print(f"    - {name}")
print(f"{'=' * 62}")
db.close()
sys.exit(1 if FAIL else 0)
