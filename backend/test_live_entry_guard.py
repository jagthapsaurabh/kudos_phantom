"""Live/paper entry gating: ONE real order per signal candle.

The live worker polls every 60 seconds, but an entry condition — a custom rule
set especially — can stay TRUE for many 1h candles. Before these guards every
tick re-read the same signal and sent another order, so a single live run
stacked a new position on the exchange once a minute for as long as the
condition held (5 ticks = 5 bracket orders, all 0.006 BTC, one on top of the
other), and a ``TypeError`` while formatting the "opened" log line aborted the
rest of the tick.

This suite drives the real ``LiveTradeService.tick()`` /
``PaperTradeService.tick()`` against a fake broker and a strategy that reports a
persistent signal, so no network and no exchange credentials are needed:

1. one order per signal candle, not one per tick
2. no second entry while a position is open — even on a NEW candle
3. a position already on the exchange blocks a new entry (restart / manual order)
4. an unreadable exchange position holds entries instead of guessing
5. the holding-time clock runs in candles, not in 60-second ticks
6. after a close, ``cooldown_bars`` candles must pass before the next entry
7. ``allow_reverse`` flattens the open position instead of stacking the other side
8. the paper worker obeys the same three rules

Run:  cd backend && ../.venv/bin/python test_live_entry_guard.py
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, '.')

TESTDB = "/tmp/live_entry_guard_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"

from app.core.strategy import PhantomV2Config                       # noqa: E402
from app.services.live_trader import (LiveTradeService,             # noqa: E402
                                      parse_open_position, price_note)
from app.services.paper_trader import PaperTradeService             # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not cond else ""))


def section(title):
    print(f"\n{'=' * 62}\n  {title}\n{'=' * 62}")


# ---------------------------------------------------------------------------
# Fakes: a broker that records every order, and a strategy that never stops
# reporting the same entry signal.
# ---------------------------------------------------------------------------
class FakeBroker:
    def __init__(self, positions=None, positions_error=False, contract_value=1.0):
        self.orders = []                 # list of (kind, side, qty)
        self.positions = positions or []
        self.positions_error = positions_error
        self.contract_value = contract_value

    # -- market data / instruments -----------------------------------------
    def perpetual_symbol(self, symbol="BTCUSDT"):
        return symbol

    def get_instrument(self, symbol="BTCUSDT", refresh=False):
        return {"contract_value": self.contract_value, "step_size": 0.001}

    def get_positions(self, symbol=None):
        if self.positions_error:
            raise RuntimeError("venue unreachable")
        return self.positions

    # -- orders -------------------------------------------------------------
    def place_order(self, symbol, side, order_type, qty, price=None, stop_price=None,
                    size_in_btc=True, **kw):
        self.orders.append(("MARKET", str(side).upper(), float(qty)))
        return {"orderId": len(self.orders), "status": "FILLED", "avgPrice": "60000"}

    def place_bracket_order(self, symbol, side, qty, price=None, stop_loss_price=None,
                            take_profit_price=None, trigger_method=None,
                            size_in_btc=True, **kw):
        self.orders.append(("BRACKET", str(side).upper(), float(qty)))
        return {"_bracket": True, "entry": {"orderId": len(self.orders), "avgPrice": "60000"}}

    def cancel_all_orders(self, symbol=None):
        return {"cancelled": 2}

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


# Anchor the synthetic candles to the current hour: the workers now refuse to
# OPEN trades on candles hours behind the wall clock (the stale-data gate), so
# a fixture dated in the past would test a state production never trades in.
BASE = pd.Timestamp.utcnow().tz_localize(None).floor("h") - timedelta(hours=119)


def candles(bars=120, last_bar=0, seed=7):
    """A flat-ish random walk whose last candle is ``last_bar`` hours ahead."""
    rng = np.random.RandomState(seed)
    close = 60000 + np.cumsum(rng.randn(bars) * 10)
    idx = pd.date_range(BASE, periods=bars, freq="1h") + timedelta(hours=last_bar)
    return pd.DataFrame({"open": close, "high": close + 30, "low": close - 30,
                         "close": close, "volume": 100.0}, index=idx)


def make_live(direction=1, positions=None, positions_error=False, contract_value=1.0,
              bracket=True, **config_kw):
    """A live worker wired to the fakes; ``tick()`` runs fully offline."""
    config = PhantomV2Config(**config_kw)
    svc = LiveTradeService("CustomTest", [], "key", "secret", is_custom=True,
                           initial_capital=20000, margin_pct=25, broker_name="Binance")
    svc.config = config
    svc.oms.config = config
    svc.strategy = PersistentSignal(direction)
    svc.bracket_orders = bracket
    svc.broker = FakeBroker(positions=positions, positions_error=positions_error,
                            contract_value=contract_value)
    svc.use_mark_price = False            # traded-price basis: no mark endpoint
    svc.state = {"bar": 0}
    svc._fetch_candles = lambda interval, limit: candles(last_bar=svc.state["bar"])
    svc._fetch_mark_price = lambda: None
    return svc


def make_paper(direction=1, **config_kw):
    config = PhantomV2Config(**config_kw)
    svc = PaperTradeService("CustomTest", [], initial_capital=20000, margin_pct=25,
                            is_custom=True, market_source="Binance")
    svc.config = config
    svc.oms.config = config
    svc.strategy = PersistentSignal(direction)
    svc.use_mark_price = False
    svc.state = {"bar": 0}
    svc._fetch_candles = lambda interval, limit: candles(last_bar=svc.state["bar"])
    svc._fetch_mark_price = lambda: None
    return svc


def run(svc, ticks=1, advance_bar=False):
    """Run ``ticks`` worker ticks; ``advance_bar`` rolls to a NEW candle first.

    Without ``advance_bar`` every tick lands inside the SAME 1h candle, which is
    what the real worker sees for most of its 60-second polls.
    """
    for _ in range(ticks):
        if advance_bar:
            svc.state["bar"] += 1
        asyncio.run(svc.tick())


# ===========================================================================
section("1. one order per signal candle (was: one order per 60s tick)")
# ===========================================================================
svc = make_live()
run(svc, ticks=5)
check("5 ticks inside one candle send exactly 1 order",
      len(svc.broker.orders) == 1, str(svc.broker.orders))
check("the single order is the bracketed entry",
      svc.broker.orders and svc.broker.orders[0][0] == "BRACKET"
      and svc.broker.orders[0][1] == "BUY", str(svc.broker.orders))
check("one position is open in the local book",
      list(svc.oms.active_trades) == ["BTCUSDT"], str(svc.oms.active_trades))
check("the held-back ticks are counted for the UI",
      svc.skipped_entries == 4, f"skipped={svc.skipped_entries}")
check("the hold reason is reported", "already traded" in (svc.last_skip_reason or ""),
      str(svc.last_skip_reason))

svc = make_live(bracket=False)
run(svc, ticks=4)
check("plain market entries are gated the same way",
      len(svc.broker.orders) == 1 and svc.broker.orders[0][0] == "MARKET",
      str(svc.broker.orders))

# ===========================================================================
section("2. no second entry while a position is open (even on a new candle)")
# ===========================================================================
svc = make_live(cooldown_bars=0)
run(svc, ticks=1)
opened_at = svc.oms.active_trades["BTCUSDT"].entry_time
run(svc, ticks=3, advance_bar=True)     # three NEW candles, signal still TRUE
check("no extra order on later candles while long",
      len(svc.broker.orders) == 1, str(svc.broker.orders))
check("the still-open position was never replaced",
      svc.oms.active_trades["BTCUSDT"].entry_time == opened_at,
      str(svc.oms.active_trades["BTCUSDT"].entry_time))
check("its holding time advanced by the three new candles",
      svc.oms.active_trades["BTCUSDT"].bars_held == 3,
      f"bars_held={svc.oms.active_trades['BTCUSDT'].bars_held}")
check("reason says a position is already open",
      "position already open" in (svc.last_skip_reason or ""), str(svc.last_skip_reason))

# ===========================================================================
section("3. a position already on the exchange blocks a new entry")
# ===========================================================================
binance_row = [{"symbol": "BTCUSDT", "positionAmt": "0.012", "entryPrice": "59800.5",
                "leverage": "7", "markPrice": "60000"}]
svc = make_live(positions=binance_row)
run(svc, ticks=3)
check("no order is sent while the venue is long 0.012 BTC",
      svc.broker.orders == [], str(svc.broker.orders))
check("the venue position is surfaced for the UI",
      svc.exchange_position and svc.exchange_position["direction"] == 1
      and abs(svc.exchange_position["size_btc"] - 0.012) < 1e-9,
      str(svc.exchange_position))
check("reason names the venue position",
      "already holds a position" in (svc.last_skip_reason or ""), str(svc.last_skip_reason))

delta_rows = [{"product_symbol": "BTCUSD", "size": 12, "entry_price": "59800.5"}]
svc = make_live(positions=delta_rows, contract_value=0.001)   # 12 contracts x 0.001 BTC
run(svc, ticks=2)
check("Delta contracts are converted to BTC before the check",
      svc.exchange_position and abs(svc.exchange_position["size_btc"] - 0.012) < 1e-9,
      str(svc.exchange_position))
check("no order is sent for a Delta position either", svc.broker.orders == [])

svc = make_live(positions=[])
run(svc, ticks=2)
check("a flat venue still allows the entry", len(svc.broker.orders) == 1,
      str(svc.broker.orders))

# ===========================================================================
section("4. an unreadable exchange position holds entries")
# ===========================================================================
svc = make_live(positions_error=True)
run(svc, ticks=2)
check("no order is guessed out when the venue cannot be read",
      svc.broker.orders == [], str(svc.broker.orders))
check("reason explains why entries are held",
      "could not read the open position" in (svc.last_skip_reason or ""),
      str(svc.last_skip_reason))
svc.sync_exchange_positions = False
svc.exchange_position_known = True
run(svc, ticks=1)
check("the check can be switched off by the operator",
      len(svc.broker.orders) == 1, str(svc.broker.orders))

# ===========================================================================
section("5. holding time is counted in candles, not in 60-second ticks")
# ===========================================================================
svc = make_live(timeout_bars=72)
run(svc, ticks=1)
run(svc, ticks=30)                       # 30 minutes inside the same candle
trade = svc.oms.active_trades.get("BTCUSDT")
check("30 ticks in one candle do not advance the holding-time clock",
      trade is not None and trade.bars_held == 0,
      f"bars_held={getattr(trade, 'bars_held', None)}")
check("the position was not force-closed after 72 minutes",
      svc.broker.orders == [("BRACKET", "BUY", 0.006)], str(svc.broker.orders))

svc = make_live(timeout_bars=2, cooldown_bars=0)
run(svc, ticks=1)                        # entry on candle 0
run(svc, ticks=2, advance_bar=True)      # candles 1 and 2 -> MH exit on candle 2
exits = [o for o in svc.broker.orders if o[0] == "MARKET"]
check("two real candles do reach the max-holding-time exit",
      len(svc.oms.active_trades) == 0 and len(exits) == 1, str(svc.broker.orders))
run(svc, ticks=1, advance_bar=True)      # cooldown_bars=0 -> next candle may enter
check("a fresh candle after the exit opens the next position",
      len(svc.broker.orders) == 3, str(svc.broker.orders))

# ===========================================================================
section("6. cooldown_bars is honoured after a close")
# ===========================================================================
svc = make_live(timeout_bars=1, cooldown_bars=2)
run(svc, ticks=1)                        # entry (candle 0)
run(svc, ticks=1, advance_bar=True)      # candle 1 -> MH exit
check("position closed on the next candle", not svc.oms.active_trades,
      str(list(svc.oms.active_trades)))
run(svc, ticks=1, advance_bar=True)      # candle 1 of the cooldown
check("no re-entry during the cooldown", not svc.oms.active_trades,
      str(svc.last_skip_reason))
run(svc, ticks=2, advance_bar=True)      # candles 2 and 3 -> clear
check("entry allowed once the cooldown has passed",
      "BTCUSDT" in svc.oms.active_trades, str(svc.last_skip_reason))

# ===========================================================================
section("7. allow_reverse flattens instead of stacking")
# ===========================================================================
svc = make_live(direction=1, cooldown_bars=0, allow_reverse=True)
run(svc, ticks=1)
check("long opened", svc.broker.orders[-1] == ("BRACKET", "BUY", 0.006), str(svc.broker.orders))
svc.strategy = PersistentSignal(-1)
run(svc, ticks=1, advance_bar=True)
sides = [o[1] for o in svc.broker.orders]
check("opposite signal closes the long before opening the short",
      sides == ["BUY", "SELL", "SELL"], str(svc.broker.orders))
check("only one position is open after the reverse",
      list(svc.oms.active_trades) == ["BTCUSDT"]
      and svc.oms.active_trades["BTCUSDT"].direction == -1,
      str(svc.oms.active_trades))

svc = make_live(direction=1)             # allow_reverse defaults to False
run(svc, ticks=1)
svc.strategy = PersistentSignal(-1)
run(svc, ticks=2, advance_bar=True)
check("without allow_reverse the open long is kept, not stacked",
      len(svc.broker.orders) == 1 and svc.oms.active_trades["BTCUSDT"].direction == 1,
      str(svc.broker.orders))

# ===========================================================================
section("8. paper trading obeys the same rules")
# ===========================================================================
svc = make_paper()
run(svc, ticks=5)
check("paper opens one position, not one per tick",
      list(svc.oms.active_trades) == ["BTCUSDT"] and len(svc.closed_trades) == 0,
      str(list(svc.oms.active_trades)))
check("paper counts the held-back ticks", svc.skipped_entries == 4,
      f"skipped={svc.skipped_entries}")
check("paper logs the hold reason for the UI",
      any("held back" in line["msg"] for line in svc.logs), str(svc.logs[-2:]))
trade = svc.oms.active_trades["BTCUSDT"]
run(svc, ticks=10)
check("paper holding time is not advanced by ticks",
      trade.bars_held == 0, f"bars_held={trade.bars_held}")
check("paper equity is untouched by the held-back signals",
      svc.equity_inr == 20000.0, str(svc.equity_inr))

svc = make_paper(cooldown_bars=0)
run(svc, ticks=1)
first_entry = svc.oms.active_trades["BTCUSDT"].entry_time
run(svc, ticks=3, advance_bar=True)
check("paper does not replace an open position on later candles",
      svc.oms.active_trades["BTCUSDT"].entry_time == first_entry,
      str(svc.oms.active_trades["BTCUSDT"].entry_time))

# ===========================================================================
section("9. helpers")
# ===========================================================================
check("binance position parses to BTC",
      parse_open_position(binance_row) == {"direction": 1, "size_btc": 0.012,
                                           "entry_price": 59800.5},
      str(parse_open_position(binance_row)))
check("delta contracts convert with contract_value",
      parse_open_position(delta_rows, 0.001)["size_btc"] == 0.012,
      str(parse_open_position(delta_rows, 0.001)))
check("short position keeps its direction",
      parse_open_position([{"positionAmt": "-0.004", "entryPrice": "61000"}])["direction"] == -1)
check("flat account parses to None", parse_open_position([{"positionAmt": "0"}]) is None)
check("an error payload parses to None", parse_open_position({"error": "no adapter"}) is None)
check("price_note survives a missing mark price",
      price_note(None, 60000.0, 60000.0, True) == "filled 60,000.00",
      price_note(None, 60000.0, 60000.0, True))
check("price_note still shows mark + fill together",
      price_note(60100.0, 60000.0, 60000.0, True) == "mark 60,100.00 (filled 60,000.00)",
      price_note(60100.0, 60000.0, 60000.0, True))
check("price_note falls back to the traded price",
      price_note(None, None, 60000.0, False) == "60,000.00")

# ===========================================================================
print(f"\n{'=' * 62}")
print(f"  PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
if FAIL:
    print("  Failures:")
    for name in FAIL:
        print(f"    - {name}")
print(f"{'=' * 62}")
sys.exit(1 if FAIL else 0)
