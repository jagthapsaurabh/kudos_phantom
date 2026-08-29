"""Several live strategies on one broker account.

A client can start 3-4 live runs at once. Every run keeps its own in-memory
order book, but they all point at the same API key, and a futures account holds
ONE netted position per contract plus ONE order book. Left uncoordinated that
produced three failures, measured against a fake venue before the fix:

1. every instance sent its own order — a long and a short hedged the account to
   net 0.0000 BTC while both books still reported a live 0.006 BTC trade, so
   both computed stops and PnL against a position that did not exist
2. ``_cancel_protection_legs`` called the account-wide ``cancel_all_orders``
   (``DELETE /fapi/v1/allOpenOrders``), so one strategy's exit also pulled the
   stop-loss / take-profit belonging to everyone else on that contract — and
   the client's own orders placed from the Live Terminal
3. the exit was a plain MARKET order with no ``reduce_only``, so when the
   position was already flat the "close" opened a fresh opposite position
   nobody managed

These tests drive the real ``LiveTradeService.tick()`` against a fake venue that
models netting, reduce-only rejection and per-order cancellation, so no network
and no exchange credentials are needed.

Run:  cd backend && ../.venv/bin/python test_multi_instance_live.py
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, '.')

TESTDB = "/tmp/multi_instance_live_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"

from app.core.strategy import PhantomV2Config                          # noqa: E402
from app.services.live_trader import (LiveTradeService, COORDINATOR,   # noqa: E402
                                      AccountCoordinator, account_key,
                                      extract_leg_order_ids,
                                      _is_nothing_to_reduce)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not cond else ""))


def section(title):
    print(f"\n{'=' * 62}\n  {title}\n{'=' * 62}")


# ---------------------------------------------------------------------------
# A venue that behaves like a real futures account: one netted position, one
# order book, and a reduce-only rule that refuses to flip.
# ---------------------------------------------------------------------------
class SharedAccount:
    def __init__(self, label):
        self.label = label
        self.orders = []            # (owner, side, qty, flag)
        self.resting = []           # [{orderId, owner}]
        self.position = 0.0
        self.cancel_all_calls = []
        self.cancelled = []         # (owner, orderId)

    def bind(self, owner):
        self.owner = owner
        return self

    # -- market data / instruments -----------------------------------------
    def perpetual_symbol(self, symbol="BTCUSDT"):
        return symbol

    def get_instrument(self, symbol="BTCUSDT", refresh=False):
        return {"contract_value": 1.0, "step_size": 0.001}

    def get_positions(self, symbol=None):
        if not self.position:
            return []
        return [{"positionAmt": str(self.position), "entryPrice": "60000"}]

    # -- orders -------------------------------------------------------------
    def _oid(self, kind):
        return f"{self.label}-{kind}-{len(self.orders) + 1}"

    def place_order(self, symbol, side, order_type, qty, price=None, stop_price=None,
                    reduce_only=False, size_in_btc=True, **kw):
        signed = float(qty) if str(side).upper() == "BUY" else -float(qty)
        if reduce_only:
            # Real venue rule: a reduce-only order may only SHRINK an existing
            # position. Selling into a long is a valid reduce; selling with
            # nothing long (or while short) is refused.
            if self.position == 0 or signed * self.position > 0:
                return {"error": "ReduceOnly Order is rejected (-2022)"}
        self.orders.append((self.owner, str(side).upper(), float(qty),
                            "reduce_only" if reduce_only else "opening"))
        self.position += signed
        return {"orderId": self._oid("entry"), "status": "FILLED", "avgPrice": "60000"}

    def place_bracket_order(self, symbol, side, qty, price=None, stop_loss_price=None,
                            take_profit_price=None, trigger_method=None,
                            size_in_btc=True, **kw):
        entry = self.place_order(symbol, side, "market", qty)
        if isinstance(entry, dict) and entry.get("error"):
            return entry
        legs = []
        for kind, otype in (("stop_loss", "stop_market"),
                            ("take_profit", "take_profit_market")):
            oid = self._oid(kind)
            self.resting.append({"orderId": oid, "owner": self.owner})
            legs.append({"orderId": oid, "type": otype})
        entry["_bracket"] = True
        entry["legs"] = legs
        return entry

    def cancel_order(self, order_id, symbol=None, client_order_id=None):
        self.cancelled.append((self.owner, order_id))
        before = len(self.resting)
        self.resting = [o for o in self.resting if o["orderId"] != order_id]
        return ({"orderId": order_id} if len(self.resting) != before
                else {"error": "Unknown order sent."})

    def cancel_all_orders(self, symbol=None):
        self.cancel_all_calls.append(self.owner)
        killed = [o["orderId"] for o in self.resting]
        self.resting = []
        return {"cancelled": killed}

    def get_fills(self, symbol=None, limit=10):
        return []


class PersistentSignal:
    """Reports the SAME direction on the last candle of every tick."""

    def __init__(self, direction=1):
        self.direction = direction

    def generate_signals(self, df_1h, df_4h):
        signals = np.zeros(len(df_1h))
        signals[-1] = self.direction
        return signals


BASE = datetime(2024, 1, 1)


def candles(bars=120, last_bar=0, seed=7):
    rng = np.random.RandomState(seed)
    close = 60000 + np.cumsum(rng.randn(bars) * 10)
    idx = pd.date_range(BASE, periods=bars, freq="1h") + timedelta(hours=last_bar)
    return pd.DataFrame({"open": close, "high": close + 30, "low": close - 30,
                         "close": close, "volume": 100.0}, index=idx)


MADE = []


def make(name, direction, broker, api_key, broker_name="Binance", sync=True):
    svc = LiveTradeService(name, [], api_key, "secret", is_custom=True,
                           initial_capital=20000, margin_pct=25,
                           broker_name=broker_name)
    svc.config = PhantomV2Config()
    svc.oms.config = svc.config
    svc.strategy = PersistentSignal(direction)
    svc.use_mark_price = False
    svc.state = {"bar": 0}
    svc.sync_exchange_positions = sync
    svc.broker = broker.bind(name)
    svc.instance_key = f"live_client_{broker_name}_{name}_inst"
    svc._fetch_candles = lambda interval, limit, s=svc: candles(last_bar=s.state["bar"])
    svc._fetch_mark_price = lambda: None
    # Production registers from ``start()``; these tests drive ``tick()``
    # directly, so register here to match the running system.
    COORDINATOR.register(svc)
    MADE.append(svc)
    return svc


def run(svc):
    return asyncio.run(svc.tick())


def holds(svc):
    return "BTCUSDT" in svc.oms.active_trades


# ===========================================================================
section("1. account identity — same key is one account, different keys are not")
# ===========================================================================
same_a = account_key("Binance", "key-abc")
same_b = account_key("Binance", "key-abc")
other = account_key("Binance", "key-xyz")
check("the same API key maps to one account id", same_a == same_b, f"{same_a} vs {same_b}")
check("a different API key is a different account", same_a != other, f"{same_a} vs {other}")
check("a different broker is a different account",
      account_key("Binance", "key-abc") != account_key("Delta", "key-abc"))
check("the account id never embeds the raw secret",
      "key-abc" not in same_a and "secret" not in same_a, same_a)
check("a blank key still yields a stable id",
      account_key("Binance", None) == account_key("Binance", ""))

section("1b. the coordinator tracks members per account")
coord = AccountCoordinator()
alpha = make("Alpha", 1, SharedAccount("x"), "coord-key")
beta = make("Beta", 1, SharedAccount("x"), "coord-key")
gamma = make("Gamma", 1, SharedAccount("x"), "coord-key")
coord.register(alpha)
coord.register(beta)
coord.register(gamma)
check("three instances register on one account",
      len(coord.siblings(alpha)) == 2, len(coord.siblings(alpha)))
check("an instance is never its own sibling",
      alpha not in coord.siblings(alpha))
check("with no position held there is no holder", coord.holder(alpha) is None)
check("first in line is position 1", coord.queue_position(alpha) == 1,
      coord.queue_position(alpha))
alpha.oms.active_trades["BTCUSDT"] = object()      # Alpha now holds a position
check("the holder is the sibling with the open trade",
      coord.holder(beta) is alpha)
check("a waiter is queued at position 2", coord.queue_position(beta) == 2,
      coord.queue_position(beta))
check("the holder itself is not queued behind anyone",
      coord.queue_position(alpha) == 1)
coord.unregister(alpha)
check("unregistering removes the member",
      len(coord.siblings(beta)) == 1, len(coord.siblings(beta)))
check("once the holder unregisters nobody holds the account",
      coord.holder(beta) is None)
alpha.oms.active_trades.clear()
coord.unregister(beta)
coord.unregister(gamma)
check("the account bucket is dropped when it empties",
      coord._members.get(alpha.account_id) is None)
for _s in (alpha, beta, gamma):
    COORDINATOR.unregister(_s)
del alpha, beta, gamma

# ===========================================================================
section("2. three strategies, one API key — they take turns, not collide")
# ===========================================================================
acct = SharedAccount("A1")
svcs = [make(n, d, acct, "shared-key") for n, d in (("Alpha", 1), ("Beta", 1), ("Gamma", 1))]
for s in svcs:
    run(s)

check("only ONE order reached the venue", len(acct.orders) == 1, len(acct.orders))
check("the account holds one netted position", acct.position == 0.006, acct.position)
check("exactly one instance is in a trade", sum(holds(s) for s in svcs) == 1,
      sum(holds(s) for s in svcs))
check("the first instance took the trade", holds(svcs[0]))
check("the second instance waited", not holds(svcs[1]) and svcs[1].skipped_entries == 1,
      svcs[1].skipped_entries)
check("the third instance waited", not holds(svcs[2]) and svcs[2].skipped_entries == 1,
      svcs[2].skipped_entries)
check("a waiter names the instance holding the account",
      "Alpha" in (svcs[1].last_skip_reason or ""), svcs[1].last_skip_reason)
check("a waiter is told how many are ahead of it",
      "queued behind 1 other strateg" in (svcs[1].last_skip_reason or ""),
      svcs[1].last_skip_reason)
check("a waiter is not shown a misleading 'did not open' message",
      "did not open" not in (svcs[1].last_skip_reason or ""), svcs[1].last_skip_reason)
check("all three share one account id",
      len({s.account_id for s in svcs}) == 1, {s.account_id for s in svcs})
for s in svcs:
    COORDINATOR.unregister(s)

# ===========================================================================
section("3. long and short on one account — no hedging to net zero")
# ===========================================================================
hedge = SharedAccount("A2")
opposed = [make("LongOne", 1, hedge, "hedge-key"), make("ShortOne", -1, hedge, "hedge-key")]
for s in opposed:
    run(s)

check("the account is not hedged to net zero", hedge.position != 0.0, hedge.position)
check("only one side is live", sum(holds(s) for s in opposed) == 1,
      sum(holds(s) for s in opposed))
check("the short waited for the long", not holds(opposed[1]))
check("the short's reason names the long",
      "LongOne" in (opposed[1].last_skip_reason or ""), opposed[1].last_skip_reason)
for s in opposed:
    COORDINATOR.unregister(s)

# ===========================================================================
section("4. separate API keys — two strategies trade at the same time")
# ===========================================================================
acct_x, acct_y = SharedAccount("X"), SharedAccount("Y")
solo_x = make("Alpha", 1, acct_x, "key-x")
solo_y = make("Beta", 1, acct_y, "key-y")
for s in (solo_x, solo_y):
    run(s)

check("the two instances have different account ids",
      solo_x.account_id != solo_y.account_id)
check("the first account traded", holds(solo_x))
check("the second account also traded", holds(solo_y), solo_y.last_skip_reason)
check("neither blocked the other",
      solo_x.skipped_entries == 0 and solo_y.skipped_entries == 0,
      (solo_x.skipped_entries, solo_y.skipped_entries))
check("each account holds its own position",
      acct_x.position == 0.006 and acct_y.position == 0.006,
      (acct_x.position, acct_y.position))
for s in (solo_x, solo_y):
    COORDINATOR.unregister(s)

# ===========================================================================
section("5. exiting cancels THIS instance's legs, and nothing else")
# ===========================================================================
own = SharedAccount("A3")
exiter = make("Exiter", 1, own, "exit-key")
run(exiter)
check("the entry recorded its own protection leg ids",
      len(exiter.protection_leg_ids) == 2, exiter.protection_leg_ids)
check("the recorded legs are the ones resting on the venue",
      {o["orderId"] for o in own.resting} == set(exiter.protection_leg_ids),
      (own.resting, exiter.protection_leg_ids))

legs_before = list(exiter.protection_leg_ids)

# The client's own resting orders, placed from the Live Terminal.
own.resting.append({"orderId": "CLIENT-manual-stop", "owner": "client"})
own.resting.append({"orderId": "CLIENT-manual-limit", "owner": "client"})
# A sibling instance's protection, on the same contract.
own.resting.append({"orderId": "SIBLING-stop", "owner": "Sibling"})
before = len(own.resting)

exiter.oms.active_trades["BTCUSDT"].sl = 99999.0     # force the stop next tick
run(exiter)

survivors = {o["orderId"] for o in own.resting}
check("the account-wide cancel was NOT used", own.cancel_all_calls == [],
      own.cancel_all_calls)
check("both of this instance's legs were cancelled individually",
      sorted(oid for _, oid in own.cancelled) == sorted(legs_before),
      (own.cancelled, legs_before))
check("resting count fell by exactly two", len(own.resting) == before - 2,
      (before, len(own.resting)))
check("the client's own orders survived",
      {"CLIENT-manual-stop", "CLIENT-manual-limit"} <= survivors, survivors)
check("a sibling instance's protection survived", "SIBLING-stop" in survivors, survivors)
check("the instance cleared its own leg list after exiting",
      exiter.protection_leg_ids == [], exiter.protection_leg_ids)
check("no stale error was recorded for a clean exit",
      exiter.last_order_error is None, exiter.last_order_error)
COORDINATOR.unregister(exiter)

section("5b. the pre-fix behaviour would have wiped the account")
wiped = SharedAccount("A5")
victim = make("Wiper", 1, wiped, "wipe-key")
run(victim)
wiped.resting += [{"orderId": "CLIENT-manual-stop", "owner": "client"},
                  {"orderId": "SIBLING-stop", "owner": "Sibling"}]
wiped.bind("Wiper")
wiped.cancel_all_orders("BTCUSDT")        # exactly what the old code did
check("account-wide cancel destroys everyone's protection",
      wiped.resting == [], wiped.resting)

# ===========================================================================
section("6. a failed leg cancel is reported, not swallowed")
# ===========================================================================
stuck = SharedAccount("A6")
stuck.cancel_order = lambda order_id, symbol=None, client_order_id=None: {"error": "Rate limit"}
broken = make("Broken", 1, stuck, "broken-key")
run(broken)
broken.oms.active_trades["BTCUSDT"].sl = 99999.0
run(broken)
check("an uncancellable leg raises a visible error",
      broken.last_order_error is not None and "still resting" in (broken.last_order_error or ""),
      broken.last_order_error)
COORDINATOR.unregister(broken)

section("6b. an already-gone leg is not treated as a failure")
gone = SharedAccount("A7")
gone.cancel_order = lambda order_id, symbol=None, client_order_id=None: {"error": "Unknown order sent."}
clean = make("GoneLegs", 1, gone, "gone-key")
run(clean)
clean.oms.active_trades["BTCUSDT"].sl = 99999.0
run(clean)
check("a leg the venue already removed is ignored",
      clean.last_order_error is None, clean.last_order_error)
COORDINATOR.unregister(clean)

# ===========================================================================
section("7. exits are reduce-only, so a close can never open a position")
# ===========================================================================
ro = SharedAccount("A8")
closer = make("Closer", 1, ro, "ro-key")
run(closer)
closer.oms.active_trades["BTCUSDT"].sl = 99999.0
run(closer)
exit_orders = [o for o in ro.orders if o[3] == "reduce_only"]
check("the exit order was sent reduce-only", len(exit_orders) == 1, ro.orders)
check("the entry order was NOT reduce-only",
      [o for o in ro.orders if o[3] == "opening"], ro.orders)
check("the account is flat after the close", ro.position == 0.0, ro.position)
check("the local book is settled", not holds(closer))
COORDINATOR.unregister(closer)

section("7b. already-flat venue settles locally instead of flipping")
flat = SharedAccount("A9")
settler = make("Settler", 1, flat, "flat-key")
run(settler)
flat.position = 0.0                       # something else already flattened it
settler.oms.active_trades["BTCUSDT"].sl = 99999.0
run(settler)
check("no new position was opened by the 'exit'", flat.position == 0.0, flat.position)
check("only the opening order is on the venue", len(flat.orders) == 1, flat.orders)
check("the local book is settled rather than retrying forever", not holds(settler))
check("the rejected reduce-only is not surfaced as a hard error",
      settler.last_order_error is None, settler.last_order_error)
check("protection legs were still cleared", settler.protection_leg_ids == [])
COORDINATOR.unregister(settler)

section("7c. close-&-reverse on an already-flat venue settles instead of wedging")
# Regression: the reverse close is reduce-only too. When the venue had nothing
# to reduce, the worker refused the close, kept the phantom trade in its local
# book, retried the same rejected order on every tick and never sent another
# order again -- permanently wedged with a position it believed but did not own.
rev = SharedAccount("A11")
flipper = make("Flipper", 1, rev, "flip-key")
rev.config = flipper.config
flipper.config.allow_reverse = True
run(flipper)
check("the long entry went out", rev.position == 0.006, rev.position)

rev.position = 0.0                          # venue-side stop got there first
rev.resting = []
orders_before = len(rev.orders)
flipper.strategy = PersistentSignal(-1)     # opposite signal -> close & reverse
for bar in (2, 3, 4):
    flipper.state["bar"] = bar
    run(flipper)

check("the worker was not wedged — the reverse entry went out",
      len(rev.orders) > orders_before, (orders_before, len(rev.orders)))
check("the account now holds the new side", rev.position == -0.006, rev.position)
check("the rejected reduce-only is not left as a hard error",
      flipper.last_order_error is None, flipper.last_order_error)
trade = flipper.oms.active_trades.get("BTCUSDT")
check("the local book agrees with the venue",
      trade is not None and abs(trade.lots * trade.direction - rev.position) < 1e-9,
      (trade.lots if trade else None, rev.position))
check("exactly one reverse entry was sent, not one per tick",
      len([o for o in rev.orders if o[3] == "opening"]) == 2, rev.orders)
check("the settled close cleared its own legs, and the new entry's are tracked",
      flipper.protection_leg_ids == [o["orderId"] for o in rev.resting]
      and "A11-stop_loss-2" not in flipper.protection_leg_ids,
      (flipper.protection_leg_ids, [o["orderId"] for o in rev.resting]))
COORDINATOR.unregister(flipper)

# ===========================================================================
section("8. response parsing helpers")
# ===========================================================================
binance_shape = {"_bracket": True, "entry": {"orderId": 11},
                 "legs": [{"orderId": 12, "type": "STOP_MARKET"},
                          {"orderId": 13, "type": "TAKE_PROFIT_MARKET"}]}
check("Binance-emulated bracket yields both leg ids",
      extract_leg_order_ids(binance_shape) == ["12", "13"],
      extract_leg_order_ids(binance_shape))
check("the entry leg is not treated as protection",
      "11" not in extract_leg_order_ids(binance_shape))
delta_shape = {"entry_order": {"id": 21},
               "stop_loss_order": {"id": 22},
               "take_profit_order": {"id": 23}}
check("Delta-native bracket yields both leg ids",
      sorted(extract_leg_order_ids(delta_shape)) == ["22", "23"],
      extract_leg_order_ids(delta_shape))
check("a plain market order has no legs",
      extract_leg_order_ids({"orderId": 31}) == [])
check("an errored response has no legs",
      extract_leg_order_ids({"error": "rejected"}) == [])
check("a non-dict response has no legs", extract_leg_order_ids(None) == [])
check("a leg with no id at all is skipped",
      extract_leg_order_ids({"_bracket": True, "legs": [{"type": "STOP_MARKET"}]}) == [])

check("binance reduce-only rejection is recognised",
      _is_nothing_to_reduce({"error": "ReduceOnly Order is rejected (-2022)"}))
check("a code-only rejection is recognised", _is_nothing_to_reduce({"error": "-2022"}))
check("a 'no position' rejection is recognised",
      _is_nothing_to_reduce({"error": "No position to reduce"}))
check("a rate limit is NOT mistaken for nothing-to-reduce",
      not _is_nothing_to_reduce({"error": "Too many requests"}))
check("an auth failure is NOT mistaken for nothing-to-reduce",
      not _is_nothing_to_reduce({"error": "Invalid API-key"}))
check("a successful response is not a rejection",
      not _is_nothing_to_reduce({"orderId": 1}))
check("a non-dict response is not a rejection", not _is_nothing_to_reduce("nope"))

# ===========================================================================
section("9. worker lifecycle keeps the coordinator tidy")
# ===========================================================================
life = SharedAccount("A10")
worker = make("Worker", 1, life, "life-key")
COORDINATOR.register(worker)
watcher = make("Watcher", 1, life, "life-key")
COORDINATOR.register(watcher)
check("a running worker sees its sibling", len(COORDINATOR.siblings(watcher)) == 1)
asyncio.run(worker.stop())
check("stopping a worker unregisters it",
      len(COORDINATOR.siblings(watcher)) == 0, COORDINATOR.siblings(watcher))
check("a stopped worker no longer blocks entries",
      COORDINATOR.holder(watcher) is None)
COORDINATOR.unregister(watcher)

# ===========================================================================
section("10. the status payload explains a shared account")
# ===========================================================================
from app.main import _shared_account_status                         # noqa: E402

own_key = SharedAccount("S1")
shared_key = SharedAccount("S2")
lone = make("Lone", 1, own_key, "lone-key")
p1 = make("First", 1, shared_key, "team-key")
p2 = make("Second", 1, shared_key, "team-key")
p3 = make("Third", 1, shared_key, "team-key")

check("a strategy alone on its key reports no shared account",
      _shared_account_status(lone) is None)
idle = _shared_account_status(p1)
check("a shared account reports how many runs use it",
      idle["strategies_on_account"] == 3, idle)
check("with nobody in a trade the queue starts at 1",
      idle["queue_position"] == 1, idle)
check("with nobody in a trade no holder is named",
      idle["position_held_by"] is None, idle)
check("the other runs are listed",
      idle["other_strategies"] == ["Second", "Third"], idle["other_strategies"])
check("the note explains the one-position rule",
      "netted position" in idle["note"], idle["note"])

p1.oms.active_trades["BTCUSDT"] = object()
held = _shared_account_status(p1)
check("the holder is named for itself", held["position_held_by"] == "First", held)
check("the holder is flagged as holding", held["holds_account_position"] is True, held)
check("the holder is at the front of the queue", held["queue_position"] == 1, held)
waiting = _shared_account_status(p2)
check("a waiter is told who holds the position",
      waiting["position_held_by"] == "First", waiting)
check("a waiter is not flagged as holding",
      waiting["holds_account_position"] is False, waiting)
check("a waiter sees its place in line", waiting["queue_position"] == 2, waiting)
check("a waiter sees the full group size",
      waiting["strategies_on_account"] == 3, waiting)
for s in (lone, p1, p2, p3):
    COORDINATOR.unregister(s)

# ===========================================================================
for svc in MADE:
    COORDINATOR.unregister(svc)

print(f"\n{'=' * 62}")
print(f"  PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
if FAIL:
    print("  Failures:")
    for name in FAIL:
        print(f"    - {name}")
print(f"{'=' * 62}")
sys.exit(1 if FAIL else 0)
