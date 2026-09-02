"""Can this account actually afford the first order?

The reported bug in one line: a live instance started happily on an account
holding 27.33 USD, then every signal candle produced

    ❌ [FastTest] LIVE order failed: Delta HTTP 400:
       {"code": "insufficient_margin",
        "context": {"available_balance": "27.33121655",
                    "margin_mode": "cross",
                    "required_additional_balance": "27.81988526"}}

forever. The instance said "running", the log said nothing a trader can act
on, and no order would EVER go through because the sizing (capital × margin %
÷ leverage) simply does not fit in the wallet.

This module answers the question BEFORE the instance is registered:

* :func:`estimate_order_margin` — what one entry of this size will cost on the
  venue (initial margin + a fee/slippage buffer), in the wallet's currency.
* :func:`check_affordable`      — read the live wallet and compare, returning a
  verdict with a message written for a human ("Add 0.49 USD ... or lower
  margin % to 24%"), not a raw venue payload.
* :func:`describe_margin_error` — turn a venue's insufficient-margin rejection
  into the same kind of plain message, for the running-worker path.

Both the start endpoint and the pre-flight endpoint use it, so the Start
button can refuse with a reason instead of producing an instance that can
never trade.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

# Entry + exit taker fees plus a little price drift between sizing and fill.
# Delta rejects when the wallet cannot cover margin **and** the fees reserved
# with it, which is exactly why 27.33 available failed a 27.3-ish margin.
FEE_BUFFER_PCT = 2.0


def _f(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def estimate_order_margin(capital_inr: float, margin_pct: float, leverage: float,
                          conversion_rate: float, fee_buffer_pct: float = FEE_BUFFER_PCT) -> Dict[str, Any]:
    """Initial margin one entry needs, in the venue's settlement currency (USD).

    Mirrors the worker's own sizing (``live_trader.tick``): the rupee margin
    deployed per trade is ``capital × margin_pct``; leverage buys notional on
    top of it but does NOT change what has to be posted. So the cash the venue
    locks is the rupee margin converted to USD, plus fees.
    """
    capital_inr = _f(capital_inr, 0.0) or 0.0
    margin_pct = _f(margin_pct, 0.0) or 0.0
    rate = _f(conversion_rate, 0.0) or 0.0
    margin_inr = capital_inr * (margin_pct / 100.0)
    margin_usd = (margin_inr / rate) if rate else 0.0
    buffer_usd = margin_usd * (float(fee_buffer_pct) / 100.0)
    return {
        "margin_inr": margin_inr,
        "margin_usd": margin_usd,
        "fee_buffer_usd": buffer_usd,
        "required_usd": margin_usd + buffer_usd,
        "notional_usd": margin_usd * (_f(leverage, 1.0) or 1.0),
        "leverage": _f(leverage, 1.0) or 1.0,
        "conversion_rate": rate,
        "margin_pct": margin_pct,
        "capital_inr": capital_inr,
    }


def _affordable_margin_pct(available_usd: float, capital_inr: float,
                           conversion_rate: float, fee_buffer_pct: float = FEE_BUFFER_PCT):
    """Largest margin % whose order would still fit in the wallet (0 if none)."""
    capital_inr = _f(capital_inr, 0.0) or 0.0
    rate = _f(conversion_rate, 0.0) or 0.0
    if capital_inr <= 0 or rate <= 0:
        return None
    usable = (_f(available_usd, 0.0) or 0.0) / (1.0 + float(fee_buffer_pct) / 100.0)
    pct = usable * rate / capital_inr * 100.0
    # Round DOWN to a whole percent: suggesting the exact boundary would be
    # rejected again by the next tick of price movement.
    return max(0.0, float(int(pct)))


def _affordable_capital_inr(available_usd: float, margin_pct: float,
                            conversion_rate: float, fee_buffer_pct: float = FEE_BUFFER_PCT):
    margin_pct = _f(margin_pct, 0.0) or 0.0
    rate = _f(conversion_rate, 0.0) or 0.0
    if margin_pct <= 0 or rate <= 0:
        return None
    usable = (_f(available_usd, 0.0) or 0.0) / (1.0 + float(fee_buffer_pct) / 100.0)
    return max(0.0, usable * rate / (margin_pct / 100.0))


def check_affordable(client, *, broker: str, capital_inr: float, margin_pct: float,
                     leverage: float, conversion_rate: float,
                     asset: Optional[str] = None) -> Dict[str, Any]:
    """Read the wallet and decide whether the first order can be placed.

    Returns a dict that is always safe to render::

        {"ok": bool, "state": "ok"|"insufficient_margin"|"balance_unavailable",
         "message": "...", "available_usd": .., "required_usd": .., ...}

    ``ok`` False is a refusal: the caller must NOT start the instance.
    """
    need = estimate_order_margin(capital_inr, margin_pct, leverage, conversion_rate)
    out: Dict[str, Any] = {"ok": False, "state": "balance_unavailable", "broker": broker,
                           "message": "", **need}
    if need["required_usd"] <= 0:
        # Nothing to check (no capital / no rate): let the existing sizing
        # validation own that error rather than inventing a second one.
        out.update({"ok": True, "state": "skipped",
                    "message": "Margin pre-check skipped (no sizing to verify)."})
        return out

    from app.services.broker_account import normalize_balance
    try:
        payload = client.get_account_balance()
    except Exception as exc:                                   # network / client error
        out["error"] = f"{exc.__class__.__name__}: {exc}"
        out["message"] = (f"Could not read your {broker} wallet balance, so there is no way "
                          f"to tell whether this order size can be funded — the live trade "
                          f"was NOT started. {out['error']}")
        return out
    balance = normalize_balance(payload, broker, asset,
                                meta=getattr(client, "last_balance_meta", None))
    if isinstance(balance, dict) and balance.get("error"):
        out["error"] = str(balance["error"])
        out["message"] = (f"{broker} refused the wallet-balance request, so the order size "
                          f"could not be checked against your funds — the live trade was NOT "
                          f"started. {out['error']}")
        return out

    available = _f(balance.get("available_balance"))
    wallet = _f(balance.get("wallet_balance"))
    asset_code = balance.get("asset") or asset or "USD"
    out.update({"available_usd": available, "wallet_usd": wallet, "asset": asset_code})
    # Margin the venue is ALREADY holding back, and the mode holding it. On a
    # cross-margin account the isolated fields read zero, so this is the only
    # thing that explains a wallet bigger than what can actually be traded with
    # — and freeing it is a fix the operator can apply without depositing.
    blocked = _f(balance.get("blocked_margin"))
    mode = balance.get("margin_mode")
    if blocked:
        out["blocked_margin"] = blocked
        out["margin_mode"] = mode
    if available is None:
        out["error"] = "wallet balance did not include an available balance"
        out["message"] = (f"{broker} did not report an available balance for {asset_code}, so "
                          f"the order size could not be verified — the live trade was NOT "
                          f"started. Check the connection in Broker Settings.")
        return out

    if available >= need["required_usd"]:
        out.update({"ok": True, "state": "ok",
                    "message": (f"Funding check passed: {available:,.2f} {asset_code} available, "
                                f"about {need['required_usd']:,.2f} {asset_code} needed per trade "
                                f"({need['margin_pct']:g}% of ₹{need['capital_inr']:,.0f}).")})
        return out

    shortfall = need["required_usd"] - available
    suggest_pct = _affordable_margin_pct(available, capital_inr, conversion_rate)
    suggest_capital = _affordable_capital_inr(available, margin_pct, conversion_rate)
    fixes = [f"add about {shortfall:,.2f} {asset_code} to the account"]
    if blocked and blocked > 0.01:
        fixes.append(f"free the {blocked:,.2f} {asset_code} the venue is already blocking"
                     + (f" in {mode} margin" if mode else "")
                     + " (cancel open orders, close open positions)")
    if suggest_pct and suggest_pct > 0:
        fixes.append(f"lower margin % from {need['margin_pct']:g}% to {suggest_pct:g}% or less")
    if suggest_capital and suggest_capital > 0:
        fixes.append(f"lower capital from ₹{need['capital_inr']:,.0f} to about "
                     f"₹{suggest_capital:,.0f}")
    out.update({
        "ok": False, "state": "insufficient_margin",
        "shortfall_usd": shortfall,
        "suggested_margin_pct": suggest_pct,
        "suggested_capital_inr": suggest_capital,
        "message": (
            f"Not enough margin on your {broker} account, so the live trade was NOT started. "
            f"Each entry needs about {need['required_usd']:,.2f} {asset_code} "
            f"({need['margin_pct']:g}% of ₹{need['capital_inr']:,.0f} at "
            f"₹{need['conversion_rate']:,.2f}/USD, incl. ~{FEE_BUFFER_PCT:g}% fee buffer) "
            f"but only {available:,.2f} {asset_code} is available — short by "
            f"{shortfall:,.2f} {asset_code}. Fix it by one of: " + "; ".join(fixes) + "."),
    })
    return out


# ---------------------------------------------------------------------------
# Venue rejections (running worker)
# ---------------------------------------------------------------------------
def is_insufficient_margin(error: Any) -> bool:
    text = str(error or "").lower()
    return "insufficient_margin" in text or ("insufficient" in text and "margin" in text)


def parse_margin_error(error: Any) -> Dict[str, Any]:
    """Pull ``available_balance`` / ``required_additional_balance`` out of Delta's body."""
    text = str(error or "")
    out: Dict[str, Any] = {}
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            body = json.loads(match.group(0))
            ctx = body.get("context") if isinstance(body, dict) else None
            if isinstance(ctx, dict):
                out["available"] = _f(ctx.get("available_balance"))
                out["required_additional"] = _f(ctx.get("required_additional_balance"))
                out["margin_mode"] = ctx.get("margin_mode")
        except Exception:
            pass
    return out


def describe_margin_error(error: Any, *, broker: str = "the exchange",
                          strategy_id: str = "") -> str:
    """A trader-readable sentence for an insufficient-margin rejection."""
    parts = parse_margin_error(error)
    available = parts.get("available")
    required = parts.get("required_additional")
    who = f"[{strategy_id}] " if strategy_id else ""
    detail = ""
    if available is not None and required is not None:
        detail = (f" The account has {available:,.2f} available and the order needs about "
                  f"{required:,.2f} more"
                  + (f" in {parts['margin_mode']} margin mode" if parts.get("margin_mode") else "")
                  + ".")
    return (f"{who}Not enough margin on {broker} to place this trade, so trading has been "
            f"STOPPED instead of retrying the same rejected order every candle.{detail} "
            f"Add funds, or restart with a lower margin % / capital, then start the instance again.")
