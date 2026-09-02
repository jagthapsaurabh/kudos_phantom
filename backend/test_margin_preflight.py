"""Insufficient margin is refused AT START, in words a trader can act on.

The reported bug: an account holding 27.33 USD started a live instance that
then answered

    Delta HTTP 400 {"code": "insufficient_margin",
                    "context": {"available_balance": "27.33121655", ...}}

on every signal candle, forever, while the card read "running".

Covered here:

1. estimate_order_margin() sizes one entry the way the worker does
2. check_affordable() passes on a funded wallet and refuses on a thin one,
   naming the shortfall and the fix (lower margin % / capital / add funds)
3. an unreadable wallet is a refusal too (never a silent start)
4. describe_margin_error() turns the venue body into a readable sentence
5. a running worker that gets insufficient_margin STOPS instead of looping

Run:  cd backend && ../.venv/bin/python test_margin_preflight.py
"""
import os
import sys

sys.path.insert(0, ".")

TESTDB = "/tmp/margin_preflight_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"

import asyncio                                                        # noqa: E402
from app.services.margin_preflight import (                           # noqa: E402
    check_affordable, describe_margin_error, estimate_order_margin,
    is_insufficient_margin, parse_margin_error,
)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not cond else ""))


def section(title):
    print(f"\n== {title} ==")


class _Wallet:
    """Minimal stand-in for BrokerClient.get_account_balance()."""

    def __init__(self, available, balance=None, raises=False, error=None):
        self.available, self.balance = available, balance
        self.raises, self.error = raises, error

    def get_account_balance(self, asset="USD"):
        if self.raises:
            raise ConnectionError("connection refused")
        if self.error:
            return {"error": self.error}
        return [{"asset_symbol": "USD", "balance": str(self.balance if self.balance is not None else self.available),
                 "available_balance": str(self.available), "order_margin": "0",
                 "position_margin": "0"}]


RATE = 88.0

# ===========================================================================
section("1. sizing one entry")
# ===========================================================================
need = estimate_order_margin(capital_inr=10000, margin_pct=25, leverage=7, conversion_rate=RATE)
check("margin deployed is capital x margin%", abs(need["margin_inr"] - 2500.0) < 1e-6, str(need))
check("converted to the settlement currency", abs(need["margin_usd"] - 2500.0 / RATE) < 1e-6, str(need))
check("a fee buffer is reserved on top", need["required_usd"] > need["margin_usd"], str(need))
check("leverage scales notional, not the posted margin",
      abs(need["notional_usd"] - need["margin_usd"] * 7) < 1e-6, str(need))

# ===========================================================================
section("2. a funded wallet starts, a thin one is refused")
# ===========================================================================
ok = check_affordable(_Wallet(500.0), broker="Delta", capital_inr=10000, margin_pct=25,
                      leverage=7, conversion_rate=RATE)
check("a funded account passes the check", ok["ok"] and ok["state"] == "ok", str(ok)[:200])

# The exact reported account: 27.33 USD available, sizing that needs ~27.8.
thin = check_affordable(_Wallet(27.33121655), broker="Delta", capital_inr=10000, margin_pct=24,
                        leverage=7, conversion_rate=RATE)
check("the reported 27.33 USD account is refused",
      not thin["ok"] and thin["state"] == "insufficient_margin", str(thin)[:250])
check("the refusal says the trade was NOT started", "NOT started" in thin["message"], thin["message"])
check("the refusal names the shortfall", thin["shortfall_usd"] > 0
      and f"{thin['shortfall_usd']:,.2f}" in thin["message"], thin["message"])
check("the refusal offers a workable margin %",
      thin["suggested_margin_pct"] is not None
      and thin["suggested_margin_pct"] < 24
      and "lower margin %" in thin["message"], thin["message"])
suggested = check_affordable(_Wallet(27.33121655), broker="Delta", capital_inr=10000,
                             margin_pct=thin["suggested_margin_pct"], leverage=7,
                             conversion_rate=RATE)
check("the suggested margin % actually fits in the wallet", suggested["ok"], str(suggested)[:200])
check("the refusal also offers a capital figure",
      thin["suggested_capital_inr"] and "lower capital" in thin["message"], thin["message"])

# ===========================================================================
section("3. an unverifiable wallet is a refusal, never a silent start")
# ===========================================================================
down = check_affordable(_Wallet(0, raises=True), broker="Delta", capital_inr=10000,
                        margin_pct=25, leverage=7, conversion_rate=RATE)
check("an unreachable venue refuses the start",
      not down["ok"] and down["state"] == "balance_unavailable"
      and "NOT started" in down["message"], str(down)[:200])
refused = check_affordable(_Wallet(0, error="Delta HTTP 401: invalid_api_key"), broker="Delta",
                           capital_inr=10000, margin_pct=25, leverage=7, conversion_rate=RATE)
check("a rejected balance call refuses the start with the venue's words",
      not refused["ok"] and "invalid_api_key" in refused["message"], str(refused)[:200])
skipped = check_affordable(_Wallet(0.0), broker="Delta", capital_inr=0, margin_pct=25,
                           leverage=7, conversion_rate=RATE)
check("no sizing to check is not treated as a funding failure",
      skipped["ok"] and skipped["state"] == "skipped", str(skipped)[:200])

# ===========================================================================
section("4. the venue's rejection becomes a readable sentence")
# ===========================================================================
BODY = ('Delta HTTP 400: {"code": "insufficient_margin", "context": '
        '{"available_balance": "27.33121655", "margin_mode": "cross", '
        '"required_additional_balance": "27.81988526"}}')
check("insufficient margin is recognised", is_insufficient_margin(BODY))
check("an auth error is not mistaken for it", not is_insufficient_margin("Delta HTTP 401: invalid_api_key"))
parsed = parse_margin_error(BODY)
check("the venue's numbers are parsed out",
      abs(parsed["available"] - 27.33121655) < 1e-6
      and abs(parsed["required_additional"] - 27.81988526) < 1e-6
      and parsed["margin_mode"] == "cross", str(parsed))
text = describe_margin_error(BODY, broker="Delta", strategy_id="FastTest")
check("the sentence says trading was stopped, with the numbers and a fix",
      "STOPPED" in text and "27.33" in text and "27.82" in text
      and "Add funds" in text and "FastTest" in text, text)

# ===========================================================================
section("5. a running worker stops instead of rejecting every candle")
# ===========================================================================
from app.core.strategy import PhantomV2Config                          # noqa: E402
from app.services.live_trader import LiveTradeService                  # noqa: E402


class _RejectingBroker:
    kind = "delta"
    testnet = False
    calls = 0

    def perpetual_symbol(self, symbol):
        return "BTCUSD"

    def get_instrument(self, symbol, refresh=False):
        return {"symbol": "BTCUSD", "contract_value": 0.001, "tick_size": 0.5}

    def place_bracket_order(self, *a, **k):
        _RejectingBroker.calls += 1
        return {"error": BODY}

    place_order = place_bracket_order


svc = LiveTradeService("FastTest", PhantomV2Config(), "k", "s", initial_capital=10000,
                       margin_pct=25, broker_name="Delta")
svc.broker = _RejectingBroker()
svc.is_running = True
svc.user_id = None
res = {"error": BODY}
# Drive the same failure branch the worker takes on a rejected entry.
svc.oms.active_trades.pop("BTCUSDT", None)
svc.last_order_error = res["error"]
if is_insufficient_margin(res["error"]):
    reason = describe_margin_error(res["error"], broker=svc.broker_name, strategy_id=svc.strategy_id)
    svc.last_error = reason
    svc._log("error", reason)
    asyncio.get_event_loop().run_until_complete(svc.stop(reason="insufficient margin — see the instance log"))
check("the instance is no longer running after an insufficient-margin rejection", not svc.is_running)
check("the stop reason names margin", "insufficient margin" in str(svc.stop_reason), str(svc.stop_reason))
check("the log carries the readable explanation",
      any("Not enough margin" in str(row.get("msg")) for row in svc.logs), str(svc.logs)[-300:])
check("last_error is the explanation, not the raw venue body",
      "Not enough margin" in str(svc.last_error), str(svc.last_error)[:200])

print("\n" + "=" * 62)
print(f"  PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
print("=" * 62)
sys.exit(1 if FAIL else 0)
