"""
Centralized error handling for PHANTOM v2.5.

Covers:
- Custom exception hierarchy (domain errors)
- Global FastAPI exception handlers
- Consistent error response envelope
- Validation helpers for trading inputs
- DB error mapping
- Broker error classification
- Logging with request context
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("phantom")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------
def error_response(
    status_code: int,
    message: str,
    *,
    code: str = "error",
    details: Optional[Any] = None,
    hint: Optional[str] = None,
) -> JSONResponse:
    """Consistent error envelope for all API errors.

    Includes both `error` (new envelope) and `detail` (legacy FastAPI shape)
    so existing clients/tests that read `detail` continue to work while new
    clients use the richer `error`/`code`/`hint` fields.
    """
    body: Dict[str, Any] = {
        "error": message,
        "detail": message,  # backward compat for legacy clients/tests
        "code": code,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    if details is not None:
        body["details"] = details
    if hint:
        body["hint"] = hint
    return JSONResponse(status_code=status_code, content=body)

# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------
class PhantomError(Exception):
    """Base for all domain errors that map to HTTP responses."""
    def __init__(self, message: str, *, status_code: int = 400, code: str = "phantom_error", details: Any = None, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.details = details
        self.hint = hint

class ValidationError(PhantomError):
    def __init__(self, message: str, *, details: Any = None, hint: str = ""):
        super().__init__(message, status_code=400, code="validation_error", details=details, hint=hint)

class AuthenticationError(PhantomError):
    def __init__(self, message: str = "Invalid credentials", *, details: Any = None):
        super().__init__(message, status_code=401, code="auth_error", details=details,
                         hint="Check your API key, secret and environment (production vs testnet).")

class AuthorizationError(PhantomError):
    def __init__(self, message: str = "Not authorized"):
        super().__init__(message, status_code=403, code="forbidden")

class NotFoundError(PhantomError):
    def __init__(self, message: str = "Not found"):
        super().__init__(message, status_code=404, code="not_found")

class ConflictError(PhantomError):
    def __init__(self, message: str, *, details: Any = None):
        super().__init__(message, status_code=409, code="conflict", details=details)

class RateLimitError(PhantomError):
    def __init__(self, message: str = "Rate limit exceeded", *, retry_after: Optional[float] = None):
        super().__init__(message, status_code=429, code="rate_limited",
                         details={"retry_after": retry_after} if retry_after else None,
                         hint="The request was throttled locally or by the exchange. Retry after the suggested delay.")

class BrokerError(PhantomError):
    def __init__(self, message: str, *, broker: str = "", raw: Any = None, is_auth: bool = False):
        code = "broker_auth_error" if is_auth else "broker_error"
        status = 401 if is_auth else 502
        super().__init__(message, status_code=status, code=code, details={"broker": broker, "raw": raw},
                         hint="Check broker connection in Broker Settings or use the 'Check key' probe." if is_auth else "")

class MarketDataError(PhantomError):
    def __init__(self, message: str, *, details: Any = None):
        super().__init__(message, status_code=502, code="market_data_error", details=details)

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def validate_leverage(value: Any) -> int:
    try:
        lev = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"Leverage must be an integer, got {value!r}")
    if not 1 <= lev <= 125:
        raise ValidationError(f"Leverage must be between 1 and 125, got {lev}")
    return lev

def validate_size(value: Any, *, min_size: float = 0.0001, max_size: float = 1000.0) -> float:
    try:
        sz = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"Size must be a number, got {value!r}")
    if not (min_size <= sz <= max_size):
        raise ValidationError(f"Size must be between {min_size} and {max_size}, got {sz}")
    if sz <= 0:
        raise ValidationError("Size must be positive (Delta changelog 15.04.26: limit_price/size <=0 rejected)")
    return sz

def validate_price(value: Any, *, allow_none: bool = True) -> Optional[float]:
    if value is None and allow_none:
        return None
    try:
        p = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"Price must be a number, got {value!r}")
    if p <= 0:
        raise ValidationError(f"Price must be positive (Delta changelog 15.04.26), got {p}")
    return p

def validate_symbol(value: Any) -> str:
    if not value or not isinstance(value, str):
        raise ValidationError("Symbol is required")
    s = value.strip().upper()
    if len(s) < 3 or len(s) > 20:
        raise ValidationError(f"Invalid symbol length: {s!r}")
    return s

def validate_broker_code(value: Any) -> str:
    if not value or not isinstance(value, str):
        raise ValidationError("Broker code is required")
    code = value.strip()
    if not code:
        raise ValidationError("Broker code cannot be empty")
    # Normalize via known aliases
    normalized = {"binance": "Binance", "delta": "Delta", "delta exchange": "Delta"}.get(code.lower(), code)
    return normalized

def validate_margin_mode(value: Any) -> str:
    allowed = {"isolated", "cross", "portfolio"}
    mode = str(value or "").strip().lower()
    if mode not in allowed:
        raise ValidationError(f"Margin mode must be one of {', '.join(allowed)}, got {value!r}")
    return mode

def validate_order_type(value: Any) -> str:
    allowed = {"market", "limit", "stop_market", "stop_limit", "take_profit_market", "take_profit_limit", "trailing_stop"}
    ot = str(value or "").strip().lower()
    if ot not in allowed:
        # Also accept venue native names
        native_map = {"market_order": "market", "limit_order": "limit", "stop_loss_order": "stop_market",
                      "take_profit_order": "take_profit_market"}
        if ot in native_map:
            return native_map[ot]
        raise ValidationError(f"Order type must be one of {', '.join(allowed)}, got {value!r}")
    return ot

def validate_side(value: Any) -> str:
    side = str(value or "").strip().lower()
    if side not in ("buy", "sell", "long", "short"):
        raise ValidationError(f"Side must be buy/sell, got {value!r}")
    # Normalize long->buy, short->sell
    return {"long": "buy", "short": "sell"}.get(side, side)

# ---------------------------------------------------------------------------
# DB error mapper
# ---------------------------------------------------------------------------
def map_db_error(exc: Exception) -> PhantomError:
    """Map SQLAlchemy errors to domain errors with user-friendly messages."""
    if isinstance(exc, IntegrityError):
        msg = str(exc.orig) if hasattr(exc, 'orig') else str(exc)
        if "UNIQUE constraint failed" in msg or "unique" in msg.lower() or "duplicate" in msg.lower():
            # Extract constraint context
            if "uq_user_broker_label" in msg or "broker_connections" in msg:
                return ConflictError("A connection with this label already exists for this broker.",
                                     details={"constraint": "unique_broker_label"})
            if "users" in msg and "username" in msg:
                return ConflictError("Username already exists.", details={"field": "username"})
            if "broker_definitions" in msg:
                return ConflictError("Broker code already exists.", details={"field": "code"})
            if "fee_settings" in msg:
                return ConflictError("Fee setting for this broker/mode already exists.")
            return ConflictError("Duplicate entry violates unique constraint.", details={"db_message": msg[:300]})
        if "FOREIGN KEY" in msg or "foreign key" in msg.lower():
            return ValidationError("Referenced record does not exist (foreign key violation).")
        if "NOT NULL" in msg:
            return ValidationError("Required field missing (NOT NULL violation).", details={"db_message": msg[:300]})
        return PhantomError(f"Database integrity error: {msg[:300]}", status_code=400, code="db_integrity_error")
    if isinstance(exc, SQLAlchemyError):
        logger.error(f"Database error: {exc}\n{traceback.format_exc()}")
        return PhantomError("Database operation failed. Please retry.", status_code=500, code="db_error",
                            hint="If this persists, check DATABASE_URL and disk space.")
    return PhantomError(str(exc), status_code=500, code="internal_error")

# ---------------------------------------------------------------------------
# Broker error classifier
# ---------------------------------------------------------------------------
AUTH_MARKERS = (
    "invalid_api_key", "invalidapikey", "invalid api-key", "api-key format",
    "-2015", "unauthorized", "signatureexpired", "signature expired",
    "unauthorizedapiaccess", "ip_not_whitelisted", "signature mismatch", "http 401",
)

def classify_broker_error(message: str, *, broker: str = "") -> Dict[str, Any]:
    """Classify a broker error message for UI guidance."""
    lower = str(message or "").lower()
    is_auth = any(m in lower for m in AUTH_MARKERS)
    is_rate_limited = "rate limit" in lower or "429" in lower or "too many requests" in lower
    is_insufficient_margin = "insufficient" in lower and "margin" in lower
    is_size_too_small = "too small" in lower or "minimum" in lower or "lot size" in lower
    is_price_invalid = "price" in lower and ("invalid" in lower or "<= 0" in lower or "positive" in lower)
    
    category = "auth" if is_auth else "rate_limit" if is_rate_limited else "margin" if is_insufficient_margin else "size" if is_size_too_small else "price" if is_price_invalid else "order" if "order" in lower else "unknown"
    
    hints = {
        "auth": "Your API key was rejected. Check Broker Settings: re-enter key/secret, verify production vs testnet, and use 'Check key' probe.",
        "rate_limit": "You hit the exchange rate limit. The local throttler will back off automatically. Wait a few seconds and retry.",
        "margin": "Insufficient margin. Reduce position size or add funds to the account.",
        "size": "Order size below minimum. Increase size or check contract_value for this product.",
        "price": "Price must be positive (Delta changelog 15.04.26). Check limit_price and stop_price.",
        "order": "Order rejected by exchange. Check symbol, side, and order type.",
    }
    
    return {
        "category": category,
        "is_auth": is_auth,
        "is_rate_limited": is_rate_limited,
        "hint": hints.get(category, ""),
        "broker": broker,
    }

# ---------------------------------------------------------------------------
# FastAPI exception handlers
# ---------------------------------------------------------------------------
async def phantom_error_handler(request: Request, exc: PhantomError):
    logger.warning(f"PhantomError [{exc.code}] on {request.method} {request.url.path}: {exc.message}")
    return error_response(exc.status_code, exc.message, code=exc.code, details=exc.details, hint=exc.hint)

async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Pydantic validation errors
    details = exc.errors() if hasattr(exc, 'errors') else str(exc)
    logger.warning(f"Validation error on {request.method} {request.url.path}: {details}")
    return error_response(422, "Validation failed", code="validation_error", details=details,
                          hint="Check request body fields and types.")

async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # Map FastAPI HTTPException to our envelope
    logger.warning(f"HTTP {exc.status_code} on {request.method} {request.url.path}: {exc.detail}")
    return error_response(exc.status_code, str(exc.detail), code=f"http_{exc.status_code}")

async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.method} {request.url.path}: {exc}\n{traceback.format_exc()}")
    return error_response(500, "Internal server error", code="internal_error",
                          details={"type": exc.__class__.__name__} if logger.level <= logging.DEBUG else None,
                          hint="Check server logs. If this persists, restart the service.")

def register_exception_handlers(app):
    """Register all global exception handlers on the FastAPI app."""
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    app.add_exception_handler(PhantomError, phantom_error_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
    logger.info("Global exception handlers registered")
