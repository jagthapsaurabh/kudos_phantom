"""Application-level encryption for exchange API secrets at rest.

Delta's own integration guidance: the API secret must not sit in the database
in plain text — encrypt it at rest (AES-256) and keep the key out of the DB
and out of the codebase. This module implements exactly that with AES-256-GCM:

* ``SECRETS_ENCRYPTION_KEY`` — base64-encoded 32-byte key, supplied by the
  environment (secrets manager / systemd env / .env), never committed and
  never stored in the database. Generate one with
  ``python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"``.
* New and updated secrets are stored as ``enc:v1:<base64(nonce|ciphertext|tag)>``.
* Rows written before encryption was enabled (plain text) decrypt to
  themselves, so upgrades are seamless; they are re-encrypted the next time
  the operator saves that secret.
* The secret is decrypted only in memory at the moment a request is signed —
  never logged, never returned by the API, never shipped to the browser.

Without the environment key the module is a transparent pass-through, which
keeps developer/test databases working exactly as before. With encrypted rows
present and the key missing, decryption raises :class:`SecretDecryptionError`
so the failure is loud instead of silently trading with a corrupt secret.
"""
from __future__ import annotations

import base64
import os
from functools import lru_cache
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_PREFIX = "enc:v1:"


class SecretDecryptionError(RuntimeError):
    """An encrypted secret could not be decrypted (missing/wrong key)."""


@lru_cache(maxsize=1)
def _encryption_key() -> Optional[bytes]:
    """The 32-byte AES key from the environment, or ``None`` when unset."""
    raw = os.getenv("SECRETS_ENCRYPTION_KEY", "").strip()
    if not raw:
        return None
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception:
        raise SecretDecryptionError(
            "SECRETS_ENCRYPTION_KEY is set but is not valid base64 — generate it "
            "with: python -c \"import base64,os; print(base64.b64encode(os.urandom(32)).decode())\"")
    if len(key) != 32:
        raise SecretDecryptionError(
            f"SECRETS_ENCRYPTION_KEY must decode to exactly 32 bytes "
            f"(AES-256); got {len(key)}")
    return key


def encryption_enabled() -> bool:
    """True when new/updated secrets will be encrypted at rest."""
    return _encryption_key() is not None


def encrypt_secret(plain: Optional[str]) -> str:
    """Encrypt one secret for storage; plain text passes through when the
    key is not configured (developer mode). Never raises for empty values."""
    value = (plain or "").strip()
    if not value:
        return ""
    if value.startswith(_PREFIX):          # already stored encrypted
        return value
    key = _encryption_key()
    if key is None:
        return value
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, value.encode(), None)
    return _PREFIX + base64.b64encode(nonce + ciphertext).decode()


def decrypt_secret(stored: Optional[str]) -> str:
    """Decrypt one stored secret; legacy plain-text rows pass through."""
    value = (stored or "").strip()
    if not value:
        return ""
    if not value.startswith(_PREFIX):
        return value                       # written before encryption existed
    key = _encryption_key()
    if key is None:
        raise SecretDecryptionError(
            "the stored API secret is encrypted but SECRETS_ENCRYPTION_KEY is "
            "not set — export the same key used when the secret was saved")
    try:
        blob = base64.b64decode(value[len(_PREFIX):], validate=True)
        nonce, ciphertext = blob[:12], blob[12:]
        return AESGCM(key).decrypt(nonce, ciphertext, None).decode()
    except SecretDecryptionError:
        raise
    except Exception as exc:
        raise SecretDecryptionError(
            f"could not decrypt the stored API secret: {exc.__class__.__name__} "
            f"— the key was rotated or the value is corrupt; re-enter the secret "
            f"in Broker Settings (Replace keys)") from exc


def decrypt_or_error(stored: Optional[str]) -> str:
    """Decrypt, mapping failures to a plain error string (never raises)."""
    try:
        return decrypt_secret(stored)
    except SecretDecryptionError as exc:
        return f"<secret unavailable: {exc}>"
