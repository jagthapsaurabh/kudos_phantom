"""Delta Exchange India Heartbeat / Deadman Switch.

If the live worker crashes or loses connectivity and stops acknowledging,
the exchange automatically cancels the bot's open orders. That is the
safety layer the client document flags as a must-have — it runs *in
parallel* with the import (candles) and export (orders) flows, not after
them.

Official API (docs.delta.exchange, Heartbeat Management):

* ``POST /v2/heartbeat/create``  — register id + protective action
* ``POST /v2/heartbeat``         — acknowledge; ``ttl`` is milliseconds
* ``GET  /v2/heartbeat``         — list active heartbeats

Set ``ttl=0`` to disable on a graceful shutdown so a planned stop does
not look like a crash. Recommended cadence from the docs: ack every 25 s
with a 30 s TTL, so one missed beat cancels orders (``unhealthy_count: 1``).

This module talks to the venue through :class:`BrokerClient` so every call
is signed, rate-limited and User-Agent'd the same way as orders.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional


# Docs: "Send every 25 seconds (TTL is 30)".
DEFAULT_TTL_MS = 30_000
DEFAULT_ACK_INTERVAL = 25.0
# Action the exchange takes when the heartbeat goes unhealthy.
DEFAULT_ACTION = "cancel_orders"
DEFAULT_UNHEALTHY_COUNT = 1


def _ok(payload) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("error"):
        return False
    if payload.get("success") is False:
        return False
    return True


class DeadmanSwitch:
    """Create + ack loop that keeps a Delta heartbeat alive.

    ``client`` is a :class:`BrokerClient` already pointed at production or
    testnet. Failures never raise into the trading loop: they are recorded
    on :attr:`last_error` and the UI shows **HEARTBEAT FAIL** instead of
    silently leaving orders unprotected.
    """

    def __init__(self, client, heartbeat_id: str,
                 product_symbols: Optional[List[str]] = None,
                 contract_types: Optional[List[str]] = None,
                 ttl_ms: int = DEFAULT_TTL_MS,
                 ack_interval: float = DEFAULT_ACK_INTERVAL,
                 impact: str = "contracts"):
        self.client = client
        self.heartbeat_id = str(heartbeat_id or "phantom_bot")[:64]
        self.product_symbols = list(product_symbols or ["BTCUSD"])
        self.contract_types = list(contract_types or ["perpetual_futures"])
        self.ttl_ms = int(ttl_ms)
        self.ack_interval = float(ack_interval)
        self.impact = impact or "contracts"
        self._stopping = False
        self._task: Optional[asyncio.Task] = None
        self.created = False
        self.enabled = False
        self.acks = 0
        self.failures = 0
        self.last_ack_at: Optional[float] = None
        self.next_expiry: Optional[str] = None
        self.process_enabled: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_create: Optional[Dict[str, Any]] = None
        self.last_ack: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # REST wrappers (sync — the loop runs them in a worker thread)
    # ------------------------------------------------------------------
    def create(self) -> Dict[str, Any]:
        """Register the heartbeat. Idempotent: an already-created id is fine."""
        payload = self.client.create_heartbeat(
            self.heartbeat_id,
            impact=self.impact,
            contract_types=self.contract_types,
            product_symbols=self.product_symbols,
            config=[{"action": DEFAULT_ACTION, "unhealthy_count": DEFAULT_UNHEALTHY_COUNT}],
        )
        self.last_create = payload if isinstance(payload, dict) else {"result": payload}
        if _ok(payload):
            self.created = True
            self.enabled = True
            self.last_error = None
        else:
            message = ""
            if isinstance(payload, dict):
                message = str(payload.get("error") or payload.get("message") or payload)
            else:
                message = str(payload)
            # "already exists" is success for our purposes — just ack.
            lowered = message.lower()
            if "exist" in lowered or "already" in lowered:
                self.created = True
                self.enabled = True
                self.last_error = None
            else:
                self.last_error = message[:300]
        return self.last_create

    def acknowledge(self, ttl_ms: Optional[int] = None) -> Dict[str, Any]:
        """Tell the exchange we are still alive. ``ttl_ms=0`` disables."""
        ttl = self.ttl_ms if ttl_ms is None else int(ttl_ms)
        payload = self.client.send_heartbeat(self.heartbeat_id, ttl=ttl)
        self.last_ack = payload if isinstance(payload, dict) else {"result": payload}
        if _ok(payload):
            self.acks += 1
            self.last_ack_at = time.time()
            self.last_error = None
            result = payload.get("result") if isinstance(payload, dict) else None
            if isinstance(result, dict):
                self.process_enabled = str(result.get("process_enabled"))
                self.next_expiry = str(result.get("heartbeat_timestamp") or "") or None
            self.enabled = ttl > 0
        else:
            self.failures += 1
            message = ""
            if isinstance(payload, dict):
                message = str(payload.get("error") or payload.get("message") or payload)
            else:
                message = str(payload)
            self.last_error = message[:300]
        return self.last_ack

    def disable(self) -> Dict[str, Any]:
        """Graceful shutdown: ttl=0 so a planned stop does not cancel orders."""
        payload = self.acknowledge(ttl_ms=0)
        self.enabled = False
        return payload

    # ------------------------------------------------------------------
    # Async loop
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Create the heartbeat (once) then ack until :meth:`stop`."""
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        self._stopping = True
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        # Always try to disable so a clean stop is not treated as a crash.
        try:
            await asyncio.to_thread(self.disable)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"

    async def _run(self) -> None:
        try:
            await asyncio.to_thread(self.create)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.failures += 1
        while not self._stopping:
            try:
                await asyncio.to_thread(self.acknowledge)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.failures += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
            try:
                await asyncio.sleep(self.ack_interval)
            except asyncio.CancelledError:
                break

    def stats(self) -> Dict[str, Any]:
        age = None
        if self.last_ack_at is not None:
            age = round(time.time() - self.last_ack_at, 2)
        stale = (age is None) or (age > (self.ttl_ms / 1000.0))
        return {
            "heartbeat_id": self.heartbeat_id,
            "enabled": bool(self.enabled),
            "created": bool(self.created),
            "acks": int(self.acks),
            "failures": int(self.failures),
            "ttl_ms": int(self.ttl_ms),
            "ack_interval": float(self.ack_interval),
            "age_seconds": age,
            "stale": bool(stale) if self.created else True,
            "process_enabled": self.process_enabled,
            "next_expiry": self.next_expiry,
            "product_symbols": list(self.product_symbols),
            "action": DEFAULT_ACTION,
            "last_error": self.last_error,
        }
