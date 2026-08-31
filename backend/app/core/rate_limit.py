"""Broker rate limiting.

Both venues throttle aggressively and punish abuse with HTTP 429:

* **Delta Exchange** — every endpoint has a weight; the default quota is
  ``10 000`` per *fixed* 5-minute window (it resets to full every 5 minutes,
  it is not a rolling window). Public reads are light, private writes
  (placing an order) are heavy. Exceeding the quota returns HTTP 429 with an
  ``X-RATE-LIMIT-RESET`` header holding the milliseconds to wait.
  There is also a matching-engine limit of **500 operations per second per
  product** (a 50-order batch = 50 operations); cancellations are exempt.
  ``GET /v2/rate_limits/quota`` reports the remaining quota, so the client can
  adapt instead of guessing.

* **Binance Futures (fapi)** — ``REQUEST_WEIGHT`` 2 400/minute and ``ORDERS``
  1 200/minute per IP. Every response carries ``X-MBX-USED-WEIGHT-1M`` with the
  weight already consumed in the current window.

The limiter here is deliberately conservative and *adaptive*:

1. a local sliding-window guard caps requests/second and requests/minute so a
   burst from several workers can never run ahead of the venue;
2. the weight (or order) budget reported by the exchange is tracked and, when
   the venue says we are close to the ceiling, calls are delayed pre-emptively;
3. a 429 response is retried on a bounded schedule that honours
   ``Retry-After`` / ``X-RATE-LIMIT-RESET`` instead of hammering the endpoint.

Limits are stored per broker definition (admin-configurable) so a venue that
raises an account's quota — or a stricter in-house policy — can be dialled in
from the UI without a deploy.
"""

from __future__ import annotations

import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Deque, Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class RateLimitConfig:
    """Throttles applied to one broker connection.

    The defaults are the safe intersection of both venues: 20 requests/second
    and 1 200 requests/minute sits under Binance's 2 400/minute weight budget
    and well under Delta's ~33/second average, while still being far more than
    a single trading worker needs.
    """

    requests_per_second: float = 20.0
    requests_per_minute: float = 1200.0
    # Delta's fixed 5-minute weight quota. ``None`` disables weight tracking
    # (Binance reports its minute budget through response headers instead).
    weight_per_5min: Optional[float] = 10000.0
    # Binance caps order placement twice: 1 200 orders/minute *and* 300 orders
    # per 10 seconds, per account. Only the minute budget would let a 300-order
    # burst through inside ten seconds, so both windows are tracked.
    orders_per_minute: Optional[float] = 1200.0
    orders_per_10s: Optional[float] = 300.0
    # Retry policy for HTTP 429 (and transient 5xx).
    max_retries: int = 4
    backoff_base: float = 0.35
    max_sleep_seconds: float = 30.0
    # Never wait longer than this for a slot; raise instead of blocking a
    # worker forever.
    acquire_timeout: float = 20.0
    # Fraction of the exchange-reported budget at which we start pacing.
    safe_ratio: float = 0.85

    @classmethod
    def coerce(cls, value=None, **overrides) -> "RateLimitConfig":
        """Build a config from a dict/object/None plus keyword overrides."""
        data: Dict[str, object] = {}
        if value is not None:
            if isinstance(value, dict):
                data.update(value)
            else:
                for key in cls.__dataclass_fields__:  # type: ignore[attr-defined]
                    if hasattr(value, key):
                        data[key] = getattr(value, key)
        data.update({k: v for k, v in overrides.items() if v is not None})
        allowed = {k: v for k, v in data.items()
                   if k in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**allowed)

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Usage snapshot (surfaced in the UI and by the API)
# ---------------------------------------------------------------------------
@dataclass
class RateLimitUsage:
    requests_last_second: int = 0
    requests_last_minute: int = 0
    weight_used: float = 0.0
    orders_last_minute: int = 0
    orders_last_10s: int = 0
    throttled_calls: int = 0
    retried_calls: int = 0
    rejected_calls: int = 0          # gave up after max_retries
    last_throttled_at: Optional[float] = None
    exchange_weight: Optional[float] = None       # venue-reported usage
    exchange_quota: Optional[float] = None        # venue-reported remaining
    exchange_reset_ms: Optional[float] = None
    last_error: Optional[str] = None

    def as_dict(self) -> dict:
        return asdict(self)


class RateLimitExceeded(RuntimeError):
    """Raised when a slot could not be acquired before the timeout."""


# ---------------------------------------------------------------------------
# Limiter
# ---------------------------------------------------------------------------
class RateLimiter:
    """Thread-safe sliding-window limiter with exchange feedback.

    One instance is shared by every request made against one broker
    connection, so several trading workers, the data seeder and the account
    poller all draw from the same budget.
    """

    def __init__(self, key: str, config: Optional[RateLimitConfig] = None):
        self.key = key
        self.config = config or RateLimitConfig()
        self._lock = threading.RLock()
        self._second: Deque[float] = deque()
        self._minute: Deque[float] = deque()
        self._weights: Deque[Tuple[float, float]] = deque()   # (ts, weight)
        self._orders: Deque[float] = deque()
        self._orders_10s: Deque[float] = deque()
        self.usage = RateLimitUsage()
        # Exchange-reported state, updated from response headers.
        self._exchange_weight: Optional[float] = None
        self._exchange_quota: Optional[float] = None
        self._exchange_reset_ms: Optional[float] = None

    # -- configuration ---------------------------------------------------
    def configure(self, config: RateLimitConfig) -> None:
        with self._lock:
            self.config = config

    # -- internals -------------------------------------------------------
    def _prune(self, now: float) -> None:
        while self._second and now - self._second[0] >= 1.0:
            self._second.popleft()
        while self._minute and now - self._minute[0] >= 60.0:
            self._minute.popleft()
        while self._weights and now - self._weights[0][0] >= 300.0:
            self._weights.popleft()
        while self._orders and now - self._orders[0] >= 60.0:
            self._orders.popleft()
        while self._orders_10s and now - self._orders_10s[0] >= 10.0:
            self._orders_10s.popleft()

    def _sleep_for(self, windows) -> float:
        """Smallest wait (seconds) until every window has room."""
        now = time.monotonic()
        waits = []
        cfg = self.config
        second, minute, weights, orders, orders_10s = windows
        if cfg.requests_per_second and len(second) >= cfg.requests_per_second:
            waits.append(max(0.0, 1.0 - (now - second[0])) + 0.001)
        if cfg.requests_per_minute and len(minute) >= cfg.requests_per_minute:
            waits.append(max(0.0, 60.0 - (now - minute[0])) + 0.001)
        if cfg.weight_per_5min:
            used = sum(w for _, w in weights)
            if used >= cfg.weight_per_5min and weights:
                waits.append(max(0.0, 300.0 - (now - weights[0][0])) + 0.05)
        if cfg.orders_per_minute and len(orders) >= cfg.orders_per_minute:
            waits.append(max(0.0, 60.0 - (now - orders[0])) + 0.001)
        if cfg.orders_per_10s and len(orders_10s) >= cfg.orders_per_10s:
            waits.append(max(0.0, 10.0 - (now - orders_10s[0])) + 0.001)
        # Exchange-reported budget: slow down before the venue says no.
        if self._exchange_quota is not None and self._exchange_quota <= 0:
            waits.append(min(self._exchange_reset_ms or 1000.0, 60_000.0) / 1000.0)
        elif (self._exchange_weight is not None and cfg.requests_per_minute
              and self._exchange_weight >= cfg.requests_per_minute * cfg.safe_ratio):
            waits.append(0.5)
        return min(max(waits) if waits else 0.0, cfg.max_sleep_seconds)

    # -- public API ------------------------------------------------------
    def acquire(self, weight: float = 1.0, is_order: bool = False,
                timeout: Optional[float] = None) -> float:
        """Block until a request slot is available. Returns the wait in seconds."""
        cfg = self.config
        deadline = time.monotonic() + (cfg.acquire_timeout if timeout is None else timeout)
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                self._prune(now)
                wait = self._sleep_for((self._second, self._minute, self._weights,
                                        self._orders, self._orders_10s))
                if wait <= 0:
                    self._second.append(now)
                    self._minute.append(now)
                    self._weights.append((now, float(weight or 0.0)))
                    if is_order:
                        self._orders.append(now)
                        self._orders_10s.append(now)
                    self.usage.requests_last_second = len(self._second)
                    self.usage.requests_last_minute = len(self._minute)
                    self.usage.weight_used = sum(w for _, w in self._weights)
                    self.usage.orders_last_minute = len(self._orders)
                    self.usage.orders_last_10s = len(self._orders_10s)
                    return waited
                self.usage.throttled_calls += 1
                self.usage.last_throttled_at = time.time()
            if time.monotonic() + wait > deadline:
                raise RateLimitExceeded(
                    f"rate limit slot for {self.key} not available within "
                    f"{cfg.acquire_timeout:.0f}s (local windows full)")
            time.sleep(wait)
            waited += wait

    def note_response(self, headers=None, weight: float = 0.0) -> None:
        """Record what the exchange says about our budget."""
        if not headers:
            return
        def _num(key):
            try:
                value = headers.get(key)
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None
        with self._lock:
            used = _num("X-MBX-USED-WEIGHT-1M")
            if used is None:
                used = _num("x-mbx-used-weight-1m")
            if used is not None:
                self._exchange_weight = used
            reset = _num("X-RATE-LIMIT-RESET")
            if reset is None:
                reset = _num("x-rate-limit-reset")
            if reset is not None:
                self._exchange_reset_ms = reset
            quota = _num("X-RATE-LIMIT-REMAINING")
            if quota is not None:
                self._exchange_quota = quota

    def note_quota(self, current_quota: Optional[float],
                   remaining_ms: Optional[float] = None) -> None:
        """Delta's ``GET /v2/rate_limits/quota`` response."""
        with self._lock:
            if current_quota is not None:
                self._exchange_quota = float(current_quota)
            if remaining_ms is not None:
                self._exchange_reset_ms = float(remaining_ms)

    def note_retry(self) -> None:
        with self._lock:
            self.usage.retried_calls += 1

    def note_rejected(self, message: str = "") -> None:
        with self._lock:
            self.usage.rejected_calls += 1
            self.usage.last_error = str(message)[:300]

    def retry_delay(self, attempt: int, headers=None) -> float:
        """Seconds to wait before retrying a throttled/failed call."""
        reset = None
        if headers:
            for key in ("Retry-After", "retry-after", "X-RATE-LIMIT-RESET",
                        "x-rate-limit-reset"):
                value = headers.get(key)
                if value:
                    try:
                        raw = float(value)
                        # Delta sends milliseconds, Retry-After is seconds.
                        reset = raw / 1000.0 if raw > 120 else raw
                        break
                    except (TypeError, ValueError):
                        continue
        if reset is not None:
            return min(max(reset, 0.05), self.config.max_sleep_seconds)
        backoff = self.config.backoff_base * (2 ** max(0, attempt - 1))
        return min(backoff + random.uniform(0, 0.1), self.config.max_sleep_seconds)

    def snapshot(self) -> dict:
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            data = self.usage.as_dict()
            data.update({
                "key": self.key,
                "config": self.config.as_dict(),
                "requests_last_second": len(self._second),
                "requests_last_minute": len(self._minute),
                "orders_last_minute": len(self._orders),
                "orders_last_10s": len(self._orders_10s),
                "weight_used_5min": round(sum(w for _, w in self._weights), 2),
                "exchange_weight": self._exchange_weight,
                "exchange_quota": self._exchange_quota,
                "exchange_reset_ms": self._exchange_reset_ms,
            })
        return data


# ---------------------------------------------------------------------------
# Registry — one limiter per broker connection, shared by every caller
# ---------------------------------------------------------------------------
_REGISTRY_LOCK = threading.Lock()
_REGISTRY: Dict[str, RateLimiter] = {}


def get_limiter(key: str, config: Optional[RateLimitConfig] = None) -> RateLimiter:
    """Return the shared limiter for ``key`` (broker code, or user@broker)."""
    with _REGISTRY_LOCK:
        limiter = _REGISTRY.get(key)
        if limiter is None:
            limiter = RateLimiter(key, config or RateLimitConfig())
            _REGISTRY[key] = limiter
        elif config is not None:
            limiter.configure(config)
        return limiter


def reset_registry() -> None:
    """Drop every limiter (used by tests)."""
    with _REGISTRY_LOCK:
        _REGISTRY.clear()


def all_snapshots() -> Dict[str, dict]:
    with _REGISTRY_LOCK:
        return {key: limiter.snapshot() for key, limiter in _REGISTRY.items()}


# ---------------------------------------------------------------------------
# Per-venue defaults (documented, overridable from the admin UI)
# ---------------------------------------------------------------------------
VENUE_DEFAULTS: Dict[str, RateLimitConfig] = {
    # 10 000 weight per fixed 5-minute window; ~33/s sustained. Stay well under.
    # Delta publishes no order-specific cap, only the shared weight quota.
    # DeltaGlobal shares the published quota with Delta India.
    "Delta": RateLimitConfig(requests_per_second=20.0, requests_per_minute=1200.0,
                             weight_per_5min=10000.0, orders_per_minute=None,
                             orders_per_10s=None),
    "DeltaGlobal": RateLimitConfig(requests_per_second=20.0, requests_per_minute=1200.0,
                                   weight_per_5min=10000.0, orders_per_minute=None,
                                   orders_per_10s=None),
    # 2 400 weight/minute, 1 200 orders/minute.
    "Binance": RateLimitConfig(requests_per_second=20.0, requests_per_minute=1200.0,
                               weight_per_5min=None, orders_per_minute=1200.0,
                               orders_per_10s=300.0),
}


def default_config_for(broker_code: str, definition=None) -> RateLimitConfig:
    """Limits for a venue, overridden by anything set on the definition.

    The admin UI stores ``rate_limit_per_second`` / ``rate_limit_per_minute`` /
    ``quota_per_5min`` / ``orders_per_minute`` on the broker definition; a
    non-null value there wins over the venue default.
    """
    base = VENUE_DEFAULTS.get(str(broker_code), RateLimitConfig())
    if definition is None:
        return base
    overrides = {}
    mapping = {
        "rate_limit_per_second": "requests_per_second",
        "rate_limit_per_minute": "requests_per_minute",
        "quota_per_5min": "weight_per_5min",
        "orders_per_minute": "orders_per_minute",
    }
    for column, field_name in mapping.items():
        value = getattr(definition, column, None)
        if value is not None:
            try:
                overrides[field_name] = float(value)
            except (TypeError, ValueError):
                continue
    # Both venues keep the 10-second order window at a quarter of the minute
    # budget (Binance: 300 of 1 200), so scale it with an override.
    if "orders_per_minute" in overrides and base.orders_per_10s:
        overrides["orders_per_10s"] = overrides["orders_per_minute"] / 4.0
    return RateLimitConfig.coerce(base.as_dict(), **overrides)
