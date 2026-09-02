"""Normalized live-account view: orders, positions, fills, margin and risk.

Binance and Delta report the same concepts with completely different field
names and units — Delta sizes in whole contracts and reports margin per
position, Binance sizes in BTC and reports margin at the account level. This
module maps both onto one schema so the terminal (and the live trader) can
render a Delta-style screen for either venue:

* :func:`normalize_order`    — one order (entry, stop or take-profit leg)
* :func:`normalize_position` — one open position with margin + liquidation
* :func:`normalize_fill`     — one execution
* :func:`normalize_balance`  — wallet / margin balances
* :func:`account_snapshot`   — everything above plus portfolio risk metrics
* :func:`record_order` / :func:`record_fills` — persist into the database

Numbers are converted to the units the UI expects: ``qty_btc`` for size,
USD prices, INR only where the app already uses INR (nowhere here — the
broker account is always native currency).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.mark_price import perpetual_symbol
from app.database.models import BrokerFill, BrokerOrder, SessionLocal
from app.services.broker_client import is_auth_rejection

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
TERMINAL_STATES = {
    "filled", "closed", "cancelled", "canceled", "rejected", "expired",
    "fully_filled", "partially_filled_and_cancelled",
}


def _f(value, default=None):
    try:
        if value is None or value == "":
            return default
        number = float(value)
        return default if number != number else number
    except (TypeError, ValueError):
        return default


def _i(value, default=None):
    number = _f(value)
    return default if number is None else int(number)


def _millis_to_dt(value):
    """Delta returns microseconds; Binance milliseconds. Both → naive UTC."""
    number = _f(value)
    if number is None:
        return None
    if number > 1e14:          # microseconds
        number /= 1_000_000.0
    elif number > 1e11:        # milliseconds
        number /= 1000.0
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def _iso(value):
    when = value if isinstance(value, datetime) else _millis_to_dt(value)
    return when.isoformat(timespec="seconds") if when else None


def _unwrap(row: Any) -> Any:
    """Delta answers with ``{"success": true, "result": ...}``; some call
    sites pass the envelope straight through, so unwrap it here."""
    if isinstance(row, dict) and "success" in row and "result" in row:
        inner = row.get("result")
        if isinstance(inner, dict):
            return inner
    return row


def _is_error(payload) -> bool:
    return isinstance(payload, dict) and bool(payload.get("error"))


def _error_text(payload) -> str:
    if isinstance(payload, dict):
        return str(payload.get("error") or "")
    return ""


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
def normalize_order(row: dict, source: str, contract_value: float = 1.0) -> dict:
    """Map one broker order onto the terminal schema."""
    row = _unwrap(row)
    if _is_error(row):
        return {"error": _error_text(row)}
    source = str(source)
    size = _f(row.get("size") if source == "Delta" else row.get("origQty"), 0.0) or 0.0
    filled = _f(row.get("unfilled_size"), None)
    if source == "Delta":
        filled_size = (size - filled) if filled is not None else _f(row.get("filled_size"), 0.0)
        status_raw = str(row.get("state") or "").lower()
        price = _f(row.get("limit_price"))
        stop_price = _f(row.get("stop_price"))
        order_id = str(row.get("id") or "")
        client_id = row.get("client_order_id")
        order_type = str(row.get("order_type") or "")
        stop_type = row.get("stop_order_type")
        symbol = row.get("product_symbol")
        created = _millis_to_dt(row.get("created_at"))
        avg_price = _f(row.get("average_fill_price"))
        fee = _f(row.get("paid_commission") or row.get("commission"))
        reduce_only = bool(row.get("reduce_only"))
        trail = _f(row.get("trail_amount"))
        trigger = row.get("stop_trigger_method")
        if order_type == "market_order":
            kind = "market"
        elif order_type == "limit_order":
            kind = "limit"
        else:
            kind = order_type
        if stop_type == "stop_loss_order":
            kind = "stop_market" if order_type == "market_order" else "stop_limit"
            leg = "stop_loss"
        elif stop_type == "take_profit_order":
            kind = "take_profit_market" if order_type == "market_order" else "take_profit_limit"
            leg = "take_profit"
        else:
            leg = "entry"
        status = {"open": "open", "pending": "pending", "closed": "filled",
                  "cancelled": "cancelled", "filled": "filled",
                  "partially_filled": "partially_filled"}.get(status_raw, status_raw or "open")
    else:  # Binance
        filled_size = _f(row.get("executedQty"), 0.0) or 0.0
        status_raw = str(row.get("status") or "").lower()
        price = _f(row.get("price")) or None
        if price in (0.0, 0):
            price = None
        stop_price = _f(row.get("stopPrice"))
        order_id = str(row.get("orderId") or row.get("orderID") or "")
        client_id = row.get("clientOrderId") or row.get("origClientOrderId")
        symbol = row.get("symbol")
        created = _millis_to_dt(row.get("time") or row.get("updateTime"))
        avg_price = _f(row.get("avgPrice"))
        fee = None
        reduce_only = bool(row.get("reduceOnly"))
        trail = None
        trigger = row.get("workingType")
        native = str(row.get("type") or "").upper()
        kind = {"MARKET": "market", "LIMIT": "limit", "STOP_MARKET": "stop_market",
                "STOP": "stop_limit", "TAKE_PROFIT_MARKET": "take_profit_market",
                "TAKE_PROFIT": "take_profit_limit",
                "TRAILING_STOP_MARKET": "trailing_stop"}.get(native, native.lower())
        leg = {"STOP_MARKET": "stop_loss", "STOP": "stop_loss",
               "TAKE_PROFIT_MARKET": "take_profit",
               "TAKE_PROFIT": "take_profit"}.get(native, "entry")
        status = {"NEW": "open", "PARTIALLY_FILLED": "partially_filled", "FILLED": "filled",
                  "CANCELED": "cancelled", "REJECTED": "rejected",
                  "EXPIRED": "expired"}.get(str(row.get("status") or "").upper(), status_raw or "open")

    filled_size = filled_size or 0.0
    return {
        "broker": source,
        "symbol": symbol or perpetual_symbol(source),
        "order_id": order_id,
        "client_order_id": client_id,
        "side": str(row.get("side") or "").lower(),
        "type": kind,
        "leg": leg,
        "size": float(size),
        "qty_btc": float(size) * float(contract_value or 1.0),
        "price": price,
        "stop_price": stop_price,
        "trail_amount": trail,
        "trigger_method": (str(trigger).lower() if trigger else None),
        "reduce_only": reduce_only,
        "status": status,
        "is_open": status in ("open", "pending", "partially_filled"),
        "is_stop": leg in ("stop_loss", "take_profit") or kind.startswith(("stop", "take_profit")),
        "filled_size": float(filled_size),
        "unfilled_size": float(size) - float(filled_size),
        "avg_fill_price": avg_price,
        "fee": fee,
        "created_at": _iso(created),
        "raw": row,
    }


def split_order_response(response: Any, source: str = "") -> List[tuple]:
    """Flatten a place-order response into ``[(payload, leg_name), ...]``.

    A bracket order comes back in a different shape per venue:

    * Delta (native) — ``{entry_order, stop_loss_order, take_profit_order}``
    * Binance (emulated) — ``{entry, legs: [...]}``
    * Delta may also answer with a plain list of the created orders.

    Everything else is a single order and comes back as one ``('entry', …)``
    pair. Rows that carry an ``error`` are dropped.
    """
    response = _unwrap(response)
    if not isinstance(response, dict) or response.get("error"):
        return []
    rows: List[tuple] = []
    if response.get("_bracket") or "entry_order" in response or "entry" in response:
        entry = response.get("entry") or response.get("entry_order")
        if isinstance(entry, dict) and not entry.get("error"):
            rows.append((entry, "entry"))
        for raw_leg in (response.get("legs") or []):
            if not isinstance(raw_leg, dict) or raw_leg.get("error"):
                continue
            kind = str(raw_leg.get("type") or "").upper()
            rows.append((raw_leg, "take_profit" if "TAKE_PROFIT" in kind else "stop_loss"))
        for key, leg in (("stop_loss_order", "stop_loss"), ("take_profit_order", "take_profit")):
            leg_row = response.get(key)
            if isinstance(leg_row, dict) and not leg_row.get("error"):
                rows.append((leg_row, leg))
        if rows:
            return rows
    if isinstance(response.get("result"), list):
        return [(row, "entry") for row in response["result"]
                if isinstance(row, dict) and not row.get("error")]
    return [(response, "entry")]


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------
def normalize_position(row: dict, source: str, contract_value: float = 1.0,
                       mark_price: Optional[float] = None) -> dict:
    """Map one open position onto the terminal schema."""
    row = _unwrap(row)
    if _is_error(row):
        return {"error": _error_text(row)}
    source = str(source)
    if source == "Delta":
        size = _f(row.get("size"), 0.0) or 0.0
        side = "long" if size > 0 else ("short" if size < 0 else "flat")
        entry = _f(row.get("entry_price"))
        mark = mark_price or _f(row.get("mark_price"))
        liq = _f(row.get("liquidation_price"))
        margin = _f(row.get("margin"))
        realized = _f(row.get("realized_pnl"))
        unrealized = _f(row.get("unrealized_pnl"))
        symbol = row.get("product_symbol")
        leverage = _i(row.get("leverage"))
        bankruptcy = _f(row.get("bankruptcy_price"))
        adl = row.get("adl_level")
    else:  # Binance positionRisk
        size = _f(row.get("positionAmt"), 0.0) or 0.0
        side = "long" if size > 0 else ("short" if size < 0 else "flat")
        entry = _f(row.get("entryPrice"))
        mark = mark_price or _f(row.get("markPrice"))
        liq = _f(row.get("liquidationPrice"))
        margin = _f(row.get("isolatedWallet")) or None
        realized = None
        unrealized = _f(row.get("unRealizedProfit"))
        symbol = row.get("symbol")
        leverage = _i(row.get("leverage"))
        bankruptcy = None
        adl = None

    qty_btc = abs(size) * float(contract_value or 1.0)
    entry = entry or 0.0
    mark = mark or entry
    if size:
        pnl_percent = ((mark - entry) / entry * 100.0) if side == "long" else ((entry - mark) / entry * 100.0)
    else:
        pnl_percent = 0.0
    notional = qty_btc * mark
    return {
        "broker": source,
        "symbol": symbol or perpetual_symbol(source),
        "side": side,
        "size": abs(float(size)),
        "qty_btc": qty_btc,
        "entry_price": entry,
        "mark_price": mark,
        "liquidation_price": liq,
        "bankruptcy_price": bankruptcy,
        "margin": margin,
        "leverage": leverage,
        "notional": notional,
        "unrealized_pnl": unrealized,
        "realized_pnl": realized,
        "pnl_percent": pnl_percent,
        "adl_level": adl,
        "raw": row,
    }


# ---------------------------------------------------------------------------
# Fills
# ---------------------------------------------------------------------------
def normalize_fill(row: dict, source: str, contract_value: float = 1.0) -> dict:
    """Map one execution onto the terminal schema."""
    row = _unwrap(row)
    if _is_error(row):
        return {"error": _error_text(row)}
    source = str(source)
    if source == "Delta":
        size = _f(row.get("size"), 0.0) or 0.0
        price = _f(row.get("price"))
        fee = _f(row.get("commission") or row.get("paid_commission"))
        trade_id = str(row.get("id") or "")
        order_id = str(row.get("order_id") or "")
        client_id = row.get("client_order_id")
        side = str(row.get("side") or "").lower()
        when = _millis_to_dt(row.get("created_at"))
        symbol = row.get("product_symbol")
        role = row.get("role") or row.get("meta_data", {}).get("role") if isinstance(row.get("meta_data"), dict) else row.get("role")
        realized = _f(row.get("realized_pnl"))
    else:  # Binance userTrades
        size = _f(row.get("qty"), 0.0) or 0.0
        price = _f(row.get("price"))
        fee = _f(row.get("commission"))
        trade_id = str(row.get("id") or "")
        order_id = str(row.get("orderId") or "")
        client_id = row.get("clientOrderId")
        side = str(row.get("side") or "").lower()
        when = _millis_to_dt(row.get("time"))
        symbol = row.get("symbol")
        role = "maker" if row.get("maker") else "taker"
        realized = _f(row.get("realizedPnl"))
    return {
        "broker": source,
        "symbol": symbol or perpetual_symbol(source),
        "trade_id": trade_id,
        "order_id": order_id,
        "client_order_id": client_id,
        "side": side,
        "size": abs(float(size)),
        "qty_btc": abs(float(size)) * float(contract_value or 1.0),
        "price": price,
        "fee": fee,
        "role": (str(role).lower() if role else None),
        "realized_pnl": realized,
        "filled_at": _iso(when),
        "raw": row,
    }


# ---------------------------------------------------------------------------
# Balances / margin
# ---------------------------------------------------------------------------
def normalize_balance(payload: Any, source: str, asset: str = None) -> dict:
    """Wallet balances and margin breakdown for one venue."""
    if _is_error(payload):
        return {"error": _error_text(payload)}
    source = str(source)
    out: Dict[str, Any] = {
        "broker": source,
        "asset": asset or ("USD" if source == "Delta" else "USDT"),
        "wallet_balance": None,
        "available_balance": None,
        "used_margin": None,
        "order_margin": None,
        "position_margin": None,
        "unrealized_pnl": None,
        "total": None,
        "balances": [],
    }
    if source == "Delta":
        rows = payload if isinstance(payload, list) else (payload or {}).get("result", [])
        rows = rows if isinstance(rows, list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            out["balances"].append({
                "asset": row.get("asset_symbol"),
                "balance": _f(row.get("balance")),
                "available": _f(row.get("available_balance")),
                "order_margin": _f(row.get("order_margin")),
                "position_margin": _f(row.get("position_margin")),
                "commission": _f(row.get("commission")),
            })
        primary = None
        for row in out["balances"]:
            if (row.get("asset") or "").upper() == (asset or "USD").upper():
                primary = row
                break
        primary = primary or (out["balances"][0] if out["balances"] else {})
        out["wallet_balance"] = _f(primary.get("balance"))
        out["available_balance"] = _f(primary.get("available"))
        out["order_margin"] = _f(primary.get("order_margin"))
        out["position_margin"] = _f(primary.get("position_margin"))
        out["commission"] = _f(primary.get("commission"))
        # Delta's available_balance accounts for commission (fees reserved for
        # open positions or pending). Include it in used_margin so the UI shows
        # where the money is actually locked. Without this, the balance panel
        # shows "Used margin $0" while available is less than wallet — a gap
        # that looks like a bug but is just unreported commission.
        out["used_margin"] = (
            (_f(primary.get("order_margin"), 0.0) or 0.0) +
            (_f(primary.get("position_margin"), 0.0) or 0.0) +
            (_f(primary.get("commission"), 0.0) or 0.0)
        )
    else:
        payload = payload if isinstance(payload, dict) else {}
        out["asset"] = asset or "USDT"
        for asset_row in payload.get("assets") or []:
            out["balances"].append({
                "asset": asset_row.get("asset"),
                "balance": _f(asset_row.get("walletBalance")),
                "available": _f(asset_row.get("availableBalance")),
                "unrealized_pnl": _f(asset_row.get("unrealizedProfit")),
                "margin_balance": _f(asset_row.get("marginBalance")),
            })
        for asset_row in out["balances"]:
            if (asset_row.get("asset") or "").upper() == out["asset"].upper():
                out["wallet_balance"] = asset_row["balance"]
                out["available_balance"] = asset_row["available"]
                out["unrealized_pnl"] = asset_row.get("unrealized_pnl")
        out["used_margin"] = _f(payload.get("totalPositionInitialMargin"), 0.0) or 0.0
        out["order_margin"] = _f(payload.get("totalOpenOrderInitialMargin"), 0.0) or 0.0
        out["position_margin"] = out["used_margin"]
        out["unrealized_pnl"] = out["unrealized_pnl"] if out["unrealized_pnl"] is not None \
            else _f(payload.get("totalUnrealizedProfit"))
        out["total"] = _f(payload.get("totalMarginBalance"))
        out["can_trade"] = bool(payload.get("canTrade"))
        out["can_withdraw"] = bool(payload.get("canWithdraw"))
        out["multi_asset_margin"] = bool(payload.get("multiAssetsMargin", False))
    return out


# ---------------------------------------------------------------------------
# Portfolio risk
# ---------------------------------------------------------------------------
def portfolio_risk(balance: dict, positions: List[dict]) -> dict:
    """Account-level risk numbers shown in the terminal's risk panel."""
    wallet = _f(balance.get("wallet_balance")) or 0.0
    available = _f(balance.get("available_balance")) or 0.0
    used = _f(balance.get("used_margin")) or 0.0
    unrealized = sum(_f(p.get("unrealized_pnl")) or 0.0 for p in positions if not p.get("error"))
    long_notional = sum(p.get("notional") or 0.0 for p in positions
                        if not p.get("error") and p.get("side") == "long")
    short_notional = sum(p.get("notional") or 0.0 for p in positions
                         if not p.get("error") and p.get("side") == "short")
    notional = long_notional + short_notional
    net_notional = long_notional - short_notional
    equity = wallet + unrealized
    return {
        "wallet_balance": wallet,
        "equity": equity,
        "available_margin": available,
        "used_margin": used,
        "order_margin": _f(balance.get("order_margin")) or 0.0,
        "unrealized_pnl": unrealized,
        "gross_notional": notional,
        "net_notional": net_notional,
        "long_exposure": long_notional,
        "short_exposure": short_notional,
        "margin_utilisation_pct": (used / equity * 100.0) if equity else 0.0,
        "effective_leverage": (notional / equity) if equity else 0.0,
        "free_margin_pct": (available / equity * 100.0) if equity else 0.0,
        "position_count": len([p for p in positions if not p.get("error")]),
    }


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------
# Auth-level rejections (Delta: invalid_api_key / HTTP 401; Binance: -2015,
# "Invalid API-key"). A key rejected at the auth layer fails every signed
# endpoint identically, so the snapshot collapses the wall of errors into one
# verdict the terminal can render as a fix-it banner.
# The marker list lives with the client (it is what latches the credential
# backoff); re-exported here so the snapshot verdict and the worker agree on
# what "the key is bad" means.
_is_auth_rejection = is_auth_rejection


def _auth_error_verdict(source: str, errors: Dict[str, str],
                        attempted: list) -> Optional[str]:
    """One plain-language message when *every* signed section was auth-rejected.

    ``attempted`` lists the signed sections actually called (fills and
    order_history are skipped when ``include_history`` is False). Returns None
    unless all of them failed with an auth-level error — a single dead panel
    (e.g. fills throttled) stays a partial-data case, not a key problem.
    """
    if not attempted:
        return None
    texts = [errors.get(section) for section in attempted]
    if not all(texts):
        return None
    if not all(_is_auth_rejection(t) for t in texts):
        return None
    return (
        f"{source} rejected this API key on every authenticated call "
        f"({texts[0]}). The key does not work on the environment this "
        "connection points at: it was regenerated or revoked, pasted "
        "incompletely, it is a production key on a testnet connection "
        "(or the reverse), or it belongs to the other Delta market (India "
        "vs Global keep separate key stores). Replace the key in Broker "
        "Settings, or run 'Check key' (quick) / 'Test connection' (full "
        "battery) on the connection there — they say which environment "
        "accepts the key and offer 'Use this environment'; if you already "
        "know the environment (e.g. the key was just created on Delta "
        "India production), use 'Align to India production' — and running "
        "live instances re-read the saved credentials by themselves (or "
        "use 'Reload keys' on the instance), so a key fix no longer needs "
        "a restart."
    )


def account_snapshot(client, symbol: str = "BTCUSDT", include_history: bool = True,
                     history_limit: int = 50) -> dict:
    """Everything the terminal needs, sourced from one broker client.

    Failures never raise: each section reports its own ``error`` so a partial
    outage (e.g. the fills endpoint throttled) degrades one panel instead of
    blanking the screen.
    """
    source = str(client.broker_name)
    symbol = symbol or "BTCUSDT"
    instrument = {}
    try:
        instrument = client.get_instrument(symbol) or {}
    except Exception as exc:
        instrument = {"error": str(exc)}
    contract_value = _f(instrument.get("contract_value"), 1.0) or 1.0

    mark_price = None
    try:
        quote = client.fetch_mark_price(symbol)
        if quote is not None:
            mark_price = quote.mark_price or quote.last_price
    except Exception:
        mark_price = None

    def _collect(call, mapper):
        try:
            payload = call()
        except Exception as exc:
            return [], f"{exc.__class__.__name__}: {exc}"
        if _is_error(payload):
            return [], _error_text(payload)
        rows = payload if isinstance(payload, list) else ([payload] if payload else [])
        return [mapper(row) for row in rows], None

    positions, positions_error = _collect(
        lambda: client.get_positions(symbol),
        lambda row: normalize_position(row, source, contract_value, mark_price))
    open_orders, open_error = _collect(
        lambda: client.get_open_orders(symbol),
        lambda row: normalize_order(row, source, contract_value))
    fills, fills_error = ([], None)
    history, history_error = ([], None)
    if include_history:
        fills, fills_error = _collect(
            lambda: client.get_fills(symbol, limit=history_limit),
            lambda row: normalize_fill(row, source, contract_value))
        history, history_error = _collect(
            lambda: client.get_order_history(symbol, limit=history_limit),
            lambda row: normalize_order(row, source, contract_value))

    balance: Dict[str, Any] = {}
    balance_error = None
    try:
        balance = normalize_balance(client.get_account_balance(), source) or {}
        if _is_error(balance):
            balance_error = _error_text(balance)
            balance = {}
    except Exception as exc:
        balance_error = f"{exc.__class__.__name__}: {exc}"

    open_orders = [o for o in open_orders if not o.get("error")]
    stop_orders = [o for o in open_orders if o.get("is_stop")]
    working_orders = [o for o in open_orders if not o.get("is_stop")]
    risk = portfolio_risk(balance, positions)

    # Account-level settings (margin mode, leverage, sub-account list) come
    # from the venue so the terminal renders what the exchange will actually
    # do — a cross-margin sub-account must never display as isolated.
    account_settings: Dict[str, Any] = {}
    if hasattr(client, "get_account_settings"):
        try:
            account_settings = client.get_account_settings(symbol) or {}
        except Exception as exc:
            account_settings = {"error": f"{exc.__class__.__name__}: {exc}"}

    return {
        "broker": source,
        "symbol": perpetual_symbol(source, symbol),
        "contract": {
            "symbol": perpetual_symbol(source, symbol),
            "contract_type": instrument.get("contract_type") or "perpetual",
            "contract_value": contract_value,
            "tick_size": instrument.get("tick_size"),
            "step_size": instrument.get("step_size"),
            "min_size": instrument.get("min_size"),
            "size_unit": instrument.get("size_unit"),
            "quote_asset": instrument.get("quote_asset"),
            "product_id": instrument.get("product_id"),
            "error": instrument.get("error"),
        },
        "mark_price": mark_price,
        "balance": balance,
        "risk": risk,
        "account_settings": account_settings,
        "positions": [p for p in positions if not p.get("error")],
        "open_orders": working_orders,
        "stop_orders": stop_orders,
        "fills": [f for f in fills if not f.get("error")],
        "order_history": [o for o in history if not o.get("error")],
        "errors": {k: v for k, v in {
            "positions": positions_error, "open_orders": open_error,
            "fills": fills_error, "order_history": history_error,
            "balance": balance_error, "instrument": instrument.get("error"),
        }.items() if v},
        # All-auth-failure verdict (see _auth_error_verdict). None when the
        # key is fine and only individual panels degraded.
        "auth_error": _auth_error_verdict(source, {
            "positions": positions_error, "open_orders": open_error,
            "fills": fills_error, "order_history": history_error,
            "balance": balance_error,
        }, ["positions", "open_orders", "balance"] +
           (["fills", "order_history"] if include_history else [])),
        "rate_limits": client.rate_limit_usage(),
        "fetched_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Credential reload
# ---------------------------------------------------------------------------
def _connection_is_active(row) -> bool:
    """``NULL`` counts as active: rows inserted outside SQLAlchemy never got
    the column default, and treating them as switched off would hide a key the
    operator can clearly see in the UI."""
    return bool(getattr(row, "is_active", None) in (None, 1, True))


def broker_code_aliases(broker_code: Any) -> List[str]:
    """Every spelling of this venue that a saved row may carry.

    Connections are stored under the canonical registry code, but hand-edited
    and seeded rows have used the display name (``Delta Exchange``) — a reload
    that only matched the exact code would silently keep trading on stale keys.
    """
    code = str(broker_code or "").strip()
    full = {"delta": "Delta Exchange", "binance": "Binance Futures"}
    bare = {"delta": "Delta", "binance": "Binance"}
    lowered = code.lower()
    out = [code]
    for name in (bare.get(lowered), full.get(lowered), code.title()):
        if name and name not in out:
            out.append(name)
    return out


def credential_fingerprint(api_key: str, api_secret: str) -> str:
    """Stable, non-reversible fingerprint of one key/secret pair.

    A reload has to distinguish "the operator saved a different key" from "same
    key, still rejected" — which decides whether to swap the client or just keep
    probing — without the secret itself ever being compared, logged or returned.
    """
    return hashlib.sha256(f"{api_key or ''}|{api_secret or ''}".encode()).hexdigest()[:8]


def saved_credentials(user_id: Optional[int], broker_code: str,
                      connection_id: Optional[int] = None) -> Dict[str, Any]:
    """The key material a running instance *should* be using right now.

    An instance builds its ``BrokerClient`` once, at start, so a key replaced in
    Broker Settings is invisible to it until somebody re-reads this. Resolution
    mirrors the API's own: the saved connection (``connection_id`` wins when the
    instance was started against a specific one), else the legacy per-account
    keys. Never raises — "nothing to reload" comes back as an ``error`` so the
    worker keeps the client it has instead of trading with no credentials.
    """
    out = {"api_key": None, "api_secret": None, "passphrase": None,
           "is_testnet": False, "connection_id": None, "label": None,
           "source": "none", "error": "no database session"}
    try:
        from app.database.models import BrokerConnection, User
        db = SessionLocal()
        try:
            row = None
            if connection_id is not None:
                row = db.query(BrokerConnection).filter(
                    BrokerConnection.id == int(connection_id)).first()
                if row is None:
                    out["error"] = (f"broker connection #{connection_id} no longer exists — "
                                    "this instance keeps the credentials it started with")
                    return out
                if not _connection_is_active(row):
                    # Switching a connection off must not silently re-point a
                    # running strategy at it; keep the status quo and say so.
                    out["error"] = (f"the connection '{row.label or row.broker_code}' is switched "
                                    "off — this instance keeps the credentials it started with")
                    return out
            if row is None and user_id is not None:
                rows = db.query(BrokerConnection).filter(
                    BrokerConnection.user_id == int(user_id),
                    BrokerConnection.broker_code.in_(broker_code_aliases(broker_code))
                ).order_by(BrokerConnection.created_at).all()
                row = next((r for r in rows if r.api_key and r.api_secret
                            and _connection_is_active(r)), None)
                if row is None and rows:
                    out["error"] = ("the saved connection has no usable key/secret or is "
                                    "switched off — nothing to reload")
                    return out
            if row is not None:
                from app.core.secrets import decrypt_secret, SecretDecryptionError
                try:
                    secret = decrypt_secret(row.api_secret)
                except SecretDecryptionError as exc:
                    # Fail secure: keep trading on the credentials the instance
                    # started with instead of adopting an undecryptable secret.
                    out["error"] = str(exc)
                    return out
                return {
                    "api_key": row.api_key, "api_secret": secret,
                    "passphrase": getattr(row, "passphrase", None) or None,
                    "is_testnet": bool(getattr(row, "is_testnet", 0)),
                    "connection_id": row.id,
                    "label": getattr(row, "label", None) or row.broker_code,
                    "source": "connection", "error": None,
                }
            if user_id is not None:
                user = db.query(User).filter(User.id == int(user_id)).first()
                if user is not None and (user.broker_name or "Delta") == broker_code \
                        and user.api_key and user.api_secret:
                    from app.core.secrets import decrypt_secret, SecretDecryptionError
                    try:
                        secret = decrypt_secret(user.api_secret)
                    except SecretDecryptionError as exc:
                        out["error"] = str(exc)
                        return out
                    return {
                        "api_key": user.api_key, "api_secret": secret,
                        "passphrase": None,
                        "is_testnet": None,   # legacy keys carry no environment flag
                        "connection_id": None,
                        "label": "Legacy account", "source": "legacy_user", "error": None,
                    }
            out["error"] = (f"no {broker_code} connection saved on this login to reload — "
                             "add or replace the key in Broker Settings")
            return out
        finally:
            db.close()
    except Exception as exc:
        out["error"] = f"credential reload failed: {exc.__class__.__name__}: {exc}"
        return out


# ---------------------------------------------------------------------------
# Local persistence
# ---------------------------------------------------------------------------
def record_order(user_id: int, broker_code: str, order: dict, source: str = "manual",
                 instance_key: str = None, connection_id: int = None,
                 parent_order_id: str = None, leg: str = None, raw: Any = None) -> Optional[int]:
    """Insert/update the local mirror of one order. Returns the row id."""
    if _is_error(order):
        return None
    db = SessionLocal()
    try:
        client_id = order.get("client_order_id")
        broker_id = str(order.get("order_id") or "")
        query = db.query(BrokerOrder).filter(
            BrokerOrder.user_id == user_id, BrokerOrder.broker_code == broker_code)
        row = None
        # The exchange's own id is the only truly unique key: protection legs
        # placed without a client id must not collapse into their parent entry
        # just because the venue echoed the same (or an empty) client id.
        if broker_id:
            row = query.filter(BrokerOrder.broker_order_id == broker_id).first()
        if row is None and client_id:
            candidate = query.filter(BrokerOrder.client_order_id == client_id).first()
            if candidate is not None and (not candidate.broker_order_id
                                          or candidate.broker_order_id == broker_id
                                          or not broker_id):
                row = candidate
        if row is None:
            row = BrokerOrder(user_id=user_id, broker_code=broker_code,
                              connection_id=connection_id,
                              symbol=order.get("symbol") or perpetual_symbol(broker_code),
                              created_at=datetime.utcnow())
            db.add(row)
        row.client_order_id = client_id
        row.broker_order_id = broker_id or row.broker_order_id
        row.parent_order_id = parent_order_id or row.parent_order_id
        row.side = order.get("side") or row.side
        row.order_type = order.get("type") or row.order_type
        row.leg = leg or order.get("leg") or row.leg
        row.size = order.get("size")
        row.qty_btc = order.get("qty_btc")
        row.price = order.get("price")
        row.stop_price = order.get("stop_price")
        row.trigger_method = order.get("trigger_method")
        row.reduce_only = int(bool(order.get("reduce_only")))
        row.status = order.get("status") or row.status
        row.filled_size = order.get("filled_size")
        row.avg_fill_price = order.get("avg_fill_price")
        row.fee = order.get("fee")
        row.source = source or row.source or "manual"
        row.instance_key = instance_key or row.instance_key
        if raw is not None:
            try:
                row.raw = json.loads(json.dumps(raw, default=str))
            except Exception:
                row.raw = None
        if row.status in TERMINAL_STATES and row.closed_at is None:
            row.closed_at = datetime.utcnow()
        row.updated_at = datetime.utcnow()
        db.commit()
        return row.id
    except Exception as exc:
        db.rollback()
        print(f"[broker-account] failed to record order: {exc}")
        return None
    finally:
        db.close()


def record_fills(user_id: int, broker_code: str, fills: List[dict],
                 source: str = "manual", instance_key: str = None) -> int:
    """Persist executions, de-duplicated on the exchange trade id."""
    if _is_error(fills):
        return 0
    db = SessionLocal()
    written = 0
    try:
        for fill in fills or []:
            if not isinstance(fill, dict) or fill.get("error"):
                continue
            trade_id = str(fill.get("trade_id") or "")
            query = db.query(BrokerFill).filter(
                BrokerFill.user_id == user_id, BrokerFill.broker_code == broker_code)
            if trade_id:
                row = query.filter(BrokerFill.broker_trade_id == trade_id).first()
            else:
                row = query.filter(BrokerFill.client_order_id == (fill.get("client_order_id") or None),
                                   BrokerFill.price == fill.get("price"),
                                   BrokerFill.size == fill.get("size")).first()
            if row is not None:
                continue
            db.add(BrokerFill(
                user_id=user_id, broker_code=broker_code,
                symbol=fill.get("symbol") or perpetual_symbol(broker_code),
                client_order_id=fill.get("client_order_id"),
                broker_order_id=str(fill.get("order_id") or ""),
                broker_trade_id=trade_id or None,
                side=fill.get("side"), size=fill.get("size"), qty_btc=fill.get("qty_btc"),
                price=fill.get("price"), fee=fill.get("fee"), role=fill.get("role"),
                realized_pnl=fill.get("realized_pnl"), source=source or "manual",
                instance_key=instance_key,
                filled_at=fill.get("filled_at") and datetime.fromisoformat(fill["filled_at"]),
                raw=None,
            ))
            written += 1
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[broker-account] failed to record fills: {exc}")
    finally:
        db.close()
    return written


def mark_order_cancelled(user_id: int, broker_code: str, order_id: str = None,
                         client_order_id: str = None, status: str = "cancelled") -> Optional[dict]:
    """Flip a locally mirrored order to cancelled/rejected after a cancel call."""
    db = SessionLocal()
    try:
        query = db.query(BrokerOrder).filter(
            BrokerOrder.user_id == user_id, BrokerOrder.broker_code == broker_code)
        row = None
        if client_order_id:
            row = query.filter(BrokerOrder.client_order_id == client_order_id).first()
        if row is None and order_id:
            row = query.filter(BrokerOrder.broker_order_id == str(order_id)).first()
        if row is None:
            return None
        row.status = status
        row.updated_at = datetime.utcnow()
        if row.closed_at is None:
            row.closed_at = datetime.utcnow()
        db.commit()
        return {"id": row.id, "client_order_id": row.client_order_id,
                "order_id": row.broker_order_id, "status": row.status}
    except Exception as exc:
        db.rollback()
        print(f"[broker-account] failed to mark order cancelled: {exc}")
        return None
    finally:
        db.close()


def local_order_history(user_id: int, broker_code: str = None, limit: int = 200) -> List[dict]:
    """Orders this user has sent, from the local audit table."""
    db = SessionLocal()
    try:
        query = db.query(BrokerOrder).filter(BrokerOrder.user_id == user_id)
        if broker_code:
            query = query.filter(BrokerOrder.broker_code == broker_code)
        rows = query.order_by(BrokerOrder.created_at.desc()).limit(limit).all()
        return [{
            "id": r.id, "broker": r.broker_code, "symbol": r.symbol,
            "order_id": r.broker_order_id, "client_order_id": r.client_order_id,
            "side": r.side, "type": r.order_type, "leg": r.leg,
            "size": r.size, "qty_btc": r.qty_btc, "price": r.price,
            "stop_price": r.stop_price, "reduce_only": bool(r.reduce_only),
            "status": r.status, "filled_size": r.filled_size,
            "avg_fill_price": r.avg_fill_price, "fee": r.fee,
            "source": r.source, "instance_key": r.instance_key, "error": r.error,
            "created_at": _iso(r.created_at), "updated_at": _iso(r.updated_at),
            "closed_at": _iso(r.closed_at),
        } for r in rows]
    finally:
        db.close()


def local_fills(user_id: int, broker_code: str = None, limit: int = 200) -> List[dict]:
    """Executions recorded locally (survive the exchange's history window)."""
    db = SessionLocal()
    try:
        query = db.query(BrokerFill).filter(BrokerFill.user_id == user_id)
        if broker_code:
            query = query.filter(BrokerFill.broker_code == broker_code)
        rows = query.order_by(BrokerFill.id.desc()).limit(limit).all()
        return [{
            "id": r.id, "broker": r.broker_code, "symbol": r.symbol,
            "trade_id": r.broker_trade_id, "order_id": r.broker_order_id,
            "client_order_id": r.client_order_id, "side": r.side,
            "size": r.size, "qty_btc": r.qty_btc, "price": r.price,
            "fee": r.fee, "role": r.role, "realized_pnl": r.realized_pnl,
            "source": r.source, "instance_key": r.instance_key,
            "filled_at": _iso(r.filled_at),
        } for r in rows]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Fills CSV export (Kudos / backtest-style trade log)
# ---------------------------------------------------------------------------
KUDOS_FILL_COLUMNS = (
    "filled_at", "symbol", "side", "direction", "size", "qty_btc",
    "price", "fee", "role", "realized_pnl", "trade_id", "order_id",
    "client_order_id", "broker",
)
# Closest mapping to the paper/backtest trade-log headers so the same
# spreadsheet the client already analyses can ingest live fills.
KUDOS_TRADE_COLUMNS = (
    "entry_time", "exit_time", "direction", "symbol", "entry", "exit",
    "lots", "fees", "pnl", "side_open", "side_close", "trade_id_open",
    "trade_id_close", "broker",
)


def _csv_text(header, rows) -> str:
    """UTF-8 BOM + CRLF, matching the backtest Excel/CSV export."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return "\ufeff" + buf.getvalue()


def fills_to_csv(fills: List[dict], broker: str = "Delta") -> str:
    """One row per execution, Kudos-style columns."""
    rows = []
    for fill in fills or []:
        if not isinstance(fill, dict) or fill.get("error"):
            continue
        side = str(fill.get("side") or "").lower()
        direction = 1 if side == "buy" else (-1 if side == "sell" else "")
        rows.append([
            fill.get("filled_at") or "",
            fill.get("symbol") or "",
            side,
            direction,
            fill.get("size") if fill.get("size") is not None else "",
            fill.get("qty_btc") if fill.get("qty_btc") is not None else "",
            fill.get("price") if fill.get("price") is not None else "",
            fill.get("fee") if fill.get("fee") is not None else "",
            fill.get("role") or "",
            fill.get("realized_pnl") if fill.get("realized_pnl") is not None else "",
            fill.get("trade_id") or "",
            fill.get("order_id") or "",
            fill.get("client_order_id") or "",
            fill.get("broker") or broker,
        ])
    return _csv_text(KUDOS_FILL_COLUMNS, rows)


def fills_to_kudos_trades_csv(fills: List[dict], broker: str = "Delta") -> str:
    """FIFO-pair opening and closing fills into round-trip trade rows.

    Live fills are executions, not finished trades. Pairing buy↔sell in
    time order produces the closest thing to the backtest trade log the
    client already analyses (entry/exit/direction/lots/fees/pnl).
    Unpaired (still-open) fills are emitted with a blank exit.
    """
    ordered = []
    for fill in fills or []:
        if not isinstance(fill, dict) or fill.get("error"):
            continue
        ordered.append(fill)
    ordered.sort(key=lambda f: str(f.get("filled_at") or ""))
    open_long, open_short = [], []
    trades = []

    def _lots(fill):
        return abs(_f(fill.get("qty_btc")) or _f(fill.get("size")) or 0.0)

    def _close(opened, closer, direction):
        lots = min(_lots(opened), _lots(closer)) or _lots(opened)
        entry = _f(opened.get("price")) or 0.0
        exit_px = _f(closer.get("price")) or 0.0
        fees = (abs(_f(opened.get("fee")) or 0.0) + abs(_f(closer.get("fee")) or 0.0))
        pnl = (exit_px - entry) * direction * lots
        realized = _f(closer.get("realized_pnl"))
        if realized is not None:
            pnl = realized
        trades.append([
            opened.get("filled_at") or "",
            closer.get("filled_at") or "",
            direction,
            opened.get("symbol") or closer.get("symbol") or "",
            entry, exit_px, lots, fees, pnl,
            opened.get("side") or "", closer.get("side") or "",
            opened.get("trade_id") or "", closer.get("trade_id") or "",
            opened.get("broker") or closer.get("broker") or broker,
        ])

    for fill in ordered:
        side = str(fill.get("side") or "").lower()
        if side == "buy":
            if open_short:
                _close(open_short.pop(0), fill, -1)
            else:
                open_long.append(fill)
        elif side == "sell":
            if open_long:
                _close(open_long.pop(0), fill, 1)
            else:
                open_short.append(fill)
    for leftover in open_long:
        trades.append([
            leftover.get("filled_at") or "", "", 1,
            leftover.get("symbol") or "", _f(leftover.get("price")) or "",
            "", _lots(leftover), abs(_f(leftover.get("fee")) or 0.0), "",
            leftover.get("side") or "", "", leftover.get("trade_id") or "",
            "", leftover.get("broker") or broker,
        ])
    for leftover in open_short:
        trades.append([
            leftover.get("filled_at") or "", "", -1,
            leftover.get("symbol") or "", _f(leftover.get("price")) or "",
            "", _lots(leftover), abs(_f(leftover.get("fee")) or 0.0), "",
            leftover.get("side") or "", "", leftover.get("trade_id") or "",
            "", leftover.get("broker") or broker,
        ])
    return _csv_text(KUDOS_TRADE_COLUMNS, trades)
