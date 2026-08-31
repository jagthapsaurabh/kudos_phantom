"""API secrets encrypted at rest (AES-256-GCM) — and never returned.

Delta's integration guidance: the api_secret must not sit in the database in
plain text, decryption happens only in memory at signing time, and the React
side must never see the secret in any form. This suite locks that in:

* ``encrypt_secret`` / ``decrypt_secret`` round-trip (``enc:v1:`` envelope);
* legacy plain-text rows decrypt to themselves (seamless upgrade);
* missing/wrong ``SECRETS_ENCRYPTION_KEY`` fails loud, not silently;
* create/update endpoints store ciphertext, never plaintext;
* ``_live_client`` / ``saved_credentials`` hand the *decrypted* secret to the
  broker client (and fail secure when it cannot be decrypted);
* ``GET /broker-settings`` returns ``has_secret`` instead of secret material.

Runs offline. Run: cd backend && ../.venv/bin/python test_secret_encryption.py
"""
import base64
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

TESTDB = "/tmp/secret_encryption_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"
TEST_KEY = base64.b64encode(os.urandom(32)).decode()
os.environ["SECRETS_ENCRYPTION_KEY"] = TEST_KEY

import bcrypt                                                        # noqa: E402
from fastapi.testclient import TestClient                            # noqa: E402

import app.main as main_mod                                          # noqa: E402
from app.main import app                                             # noqa: E402
from app.core import secrets as secrets_mod                          # noqa: E402
from app.database.models import (SessionLocal, User, BrokerDefinition,  # noqa: E402
                                 BrokerConnection)
from app.services import broker_account                              # noqa: E402

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
db.add(BrokerDefinition(code="Delta", name="Delta Exchange", kind="delta",
                        is_builtin=1, enabled=1))
db.commit()


def fake_fetch_connection_settings(d, row):
    row.account_settings = json.dumps({"margin_mode": "cross"})
    row.account_settings_at = datetime.utcnow()
    d.commit()


main_mod._fetch_connection_settings = fake_fetch_connection_settings
client = TestClient(app)


def make_user(username):
    user = User(username=username,
                password_hash=bcrypt.hashpw(b"client12345", bcrypt.gensalt()).decode(),
                role="client", is_active=1, can_paper=1, can_live=1,
                initial_capital=20000.0, margin_deployment_pct=25.0)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def login(username):
    r = client.post("/token", data={"username": username, "password": "client12345"})
    assert r.status_code == 200, r.text[:200]
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ===========================================================================
section("1. AES-256-GCM round-trip + upgrade passthrough")
# ===========================================================================
check("encryption is enabled when the key is configured",
      secrets_mod.encryption_enabled() is True)
plain = "SuP3rS3cret-Delta-Key"
cipher = secrets_mod.encrypt_secret(plain)
check("the ciphertext carries the version envelope and hides the plaintext",
      cipher.startswith("enc:v1:") and plain not in cipher, cipher[:60])
check("decrypt restores the exact secret",
      secrets_mod.decrypt_secret(cipher) == plain)
check("re-encrypting an already-encrypted value is a no-op",
      secrets_mod.encrypt_secret(cipher) == cipher)
check("a legacy plain-text row decrypts to itself (seamless upgrade)",
      secrets_mod.decrypt_secret("legacy-plain-secret") == "legacy-plain-secret"
      and secrets_mod.decrypt_secret("") == ""
      and secrets_mod.decrypt_secret(None) == "")
secrets_mod._encryption_key.cache_clear()
os.environ["SECRETS_ENCRYPTION_KEY"] = base64.b64encode(b"X" * 32).decode()
try:
    try:
        secrets_mod.decrypt_secret(cipher)
        check("a wrong key fails loud", False, "no error raised")
    except secrets_mod.SecretDecryptionError:
        check("a wrong key fails loud", True)
finally:
    secrets_mod._encryption_key.cache_clear()
    os.environ["SECRETS_ENCRYPTION_KEY"] = TEST_KEY
secrets_mod._encryption_key.cache_clear()
os.environ.pop("SECRETS_ENCRYPTION_KEY")
try:
    try:
        secrets_mod.decrypt_secret(cipher)
        check("a missing key fails loud on an encrypted row", False, "no error raised")
    except secrets_mod.SecretDecryptionError:
        check("a missing key fails loud on an encrypted row", True)
    check("without a key new values store as before (developer mode)",
          secrets_mod.encrypt_secret(plain) == plain)
finally:
    secrets_mod._encryption_key.cache_clear()
    os.environ["SECRETS_ENCRYPTION_KEY"] = TEST_KEY

# ===========================================================================
section("2. endpoints store ciphertext, never plaintext")
# ===========================================================================
op = make_user("sec_op")
headers = login("sec_op")
r = client.post("/broker-connections", headers=headers, json={
    "broker_code": "Delta", "label": "enc row", "api_key": "KEY-ABC",
    "api_secret": plain})
check("creating a connection still succeeds with encryption on",
      r.status_code == 200 and r.json().get("has_secret") is True, r.text[:300])
row = db.query(BrokerConnection).filter_by(label="enc row").first()
check("the DB row stores the encrypted envelope, not the secret",
      row is not None and str(row.api_secret).startswith("enc:v1:")
      and plain not in str(row.api_secret), str(row.api_secret)[:60])
check("the API response contains no secret material",
      plain not in r.text, r.text[:200])

r = client.put(f"/broker-connections/{row.id}", headers=headers, json={
    "broker_code": "Delta", "label": "enc row", "api_key": "",
    "api_secret": "rotated-secret", "passphrase": None,
    "is_testnet": False, "is_active": True})
check("replacing a key re-encrypts the new secret",
      r.status_code == 200, r.text[:200])
db.refresh(row)
check("the rotated secret is stored encrypted",
      str(row.api_secret).startswith("enc:v1:") and "rotated-secret" not in str(row.api_secret),
      str(row.api_secret)[:60])
check("...and decrypts back to the rotated value",
      secrets_mod.decrypt_secret(row.api_secret) == "rotated-secret")

# ===========================================================================
section("3. signing paths receive the decrypted secret")
# ===========================================================================
live_client, definition, cid = main_mod._live_client(db, op, "Delta", row.id)
check("_live_client hands the broker client the decrypted secret",
      live_client.api_secret == "rotated-secret", repr(live_client.api_secret)[:40])
check("...and the same key material the connection carries",
      live_client.api_key == "KEY-ABC")

saved = broker_account.saved_credentials(op.id, "Delta", row.id)
check("saved_credentials (live-instance reload) returns the decrypted secret",
      saved.get("api_secret") == "rotated-secret"
      and saved.get("source") == "connection", str(saved)[:200])

# Fail secure: with the key gone, reloads must NOT adopt a garbage secret.
secrets_mod._encryption_key.cache_clear()
os.environ.pop("SECRETS_ENCRYPTION_KEY")
try:
    saved_no_key = broker_account.saved_credentials(op.id, "Delta", row.id)
    check("a reload without the key fails secure with an explanation",
          saved_no_key.get("api_secret") is None and saved_no_key.get("error")
          and "SECRETS_ENCRYPTION_KEY" in saved_no_key["error"], str(saved_no_key)[:200])
    r = client.get("/broker-settings", headers=headers)
    check("GET /broker-settings still works (no decryption needed there)",
          r.status_code == 200, r.text[:200])
finally:
    secrets_mod._encryption_key.cache_clear()
    os.environ["SECRETS_ENCRYPTION_KEY"] = TEST_KEY

# ===========================================================================
section("4. the browser never receives the secret in any form")
# ===========================================================================
r = client.get("/broker-settings", headers=headers)
body = r.json()
check("settings returns has_secret instead of secret material",
      "has_secret" in body and "api_secret" not in body
      and plain not in r.text and "rotated-secret" not in r.text, str(body)[:200])
r = client.get("/broker-connections", headers=headers)
check("connection list has has_secret but no secret text",
      "has_secret" in r.text and "rotated-secret" not in r.text
      and plain not in r.text, r.text[:300])
r = client.get("/broker-connections/diagnose?broker=Delta", headers=headers)
check("diagnose reports the masked key and no secret",
      "KEY-ABC" not in r.text and "rotated-secret" not in r.text, r.text[:300])

# ===========================================================================
print(f"\n{'=' * 62}\n  {len(PASS)} PASS / {len(FAIL)} FAIL\n{'=' * 62}")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
