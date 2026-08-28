"""Weekly new-trade skip schedule (India Standard Time).

Covers the Saturday 17:30 -> Sunday 17:30 IST weekend pause the client
described, plus full-day skips and the Pydantic config method used by backtest /
paper / live engines.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.strategy import PhantomV2Config
from app.core.trading_schedule import TradeSkipWindow, is_new_trade_blocked

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not cond else ""), flush=True)


UTC = timezone.utc
def utc(y, m, d, h, mi):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


# 2026-08-29 is Saturday; 2026-08-30 is Sunday; 2026-08-31 is Monday.
DEFAULT_BLOCK = TradeSkipWindow(start_day="Saturday", start_time="17:30",
                                end_day="Sunday", end_time="17:30")

print("\n== default weekend block boundary (IST) ==", flush=True)
check("17:29 IST Saturday not blocked", not is_new_trade_blocked(utc(2026, 8, 29, 11, 59), True, [], [DEFAULT_BLOCK]))
check("17:30 IST Saturday blocked", is_new_trade_blocked(utc(2026, 8, 29, 12, 0), True, [], [DEFAULT_BLOCK]))
check("17:29 IST Sunday blocked", is_new_trade_blocked(utc(2026, 8, 30, 11, 59), True, [], [DEFAULT_BLOCK]))
check("17:30 IST Sunday not blocked", not is_new_trade_blocked(utc(2026, 8, 30, 12, 0), True, [], [DEFAULT_BLOCK]))
check("toggle off never blocks", not is_new_trade_blocked(utc(2026, 8, 29, 12, 0), False, [], [DEFAULT_BLOCK]))

print("\n== full-day skip ==", flush=True)
check("Monday full day blocked", is_new_trade_blocked(utc(2026, 8, 31, 0, 0), True, ["Monday"], []))
check("Monday full day blocked at 18:00 IST", is_new_trade_blocked(utc(2026, 8, 31, 12, 30), True, ["Monday"], []))
check("Tuesday not blocked", not is_new_trade_blocked(utc(2026, 9, 1, 12, 30), True, ["Monday"], []))

print("\n== Pydantic validation and config method ==", flush=True)
try:
    TradeSkipWindow(start_day="Funday", start_time="09:00", end_day="Monday", end_time="10:00")
    check("unknown weekday rejected", False)
except Exception:
    check("unknown weekday rejected", True)
try:
    TradeSkipWindow(start_day="Sunday", start_time="24:00", end_day="Monday", end_time="10:00")
    check("bad time rejected", False)
except Exception:
    check("bad time rejected", True)

cfg = PhantomV2Config(
    skip_new_trades=True,
    skip_days=["Saturday", "Wednesday"],
    skip_blocks=[DEFAULT_BLOCK],
)
check("config blocks inside skip window", cfg.is_new_trade_blocked(utc(2026, 8, 29, 12, 0)))
check("config blocks full Wednesday", cfg.is_new_trade_blocked(utc(2026, 9, 2, 12, 0)))
check("config allows Friday", not cfg.is_new_trade_blocked(utc(2026, 9, 4, 0, 0)))
cfg2 = PhantomV2Config(skip_new_trades=False, skip_blocks=[DEFAULT_BLOCK])
check("config toggle off allows all", not cfg2.is_new_trade_blocked(utc(2026, 8, 29, 12, 0)))

print("\n== cross-week window ==", flush=True)
# Sunday 20:00 -> Monday 06:00 IST crosses midnight.
build = TradeSkipWindow(start_day="Sunday", start_time="20:00",
                        end_day="Monday", end_time="06:00")
check("inside cross-week window", is_new_trade_blocked(utc(2026, 8, 30, 15, 0), True, [], [build]))
check("outside before window", not is_new_trade_blocked(utc(2026, 8, 30, 14, 0), True, [], [build]))
check("outside after window", not is_new_trade_blocked(utc(2026, 8, 31, 1, 0), True, [], [build]))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed", flush=True)
if FAIL:
    print("FAILED:", FAIL, flush=True)
    sys.exit(1)
