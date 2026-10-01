"""Offline checks for the **Risk & Exit model** (v3.6).

The strategy has always measured its protective levels in ATR units. The
client asked to be able to run those levels on price instead — or both, chosen
level by level (e.g. an ATR stop with a price-based target).

What is verified here:

* the defaults are all-ATR and reproduce the original formulas **exactly**
  (including the SL floor ``max(stop_loss_atr × ATR, sl_floor_pct × price)``),
* ``model='price'`` moves every level to a % of the entry price,
* ``model='both'`` keeps a per-level mix, and each level resolves its own mode,
* the LONG / SHORT per-side stop override works for whichever model the stop
  uses (``stop_loss_atr`` or ``stop_loss_pct``),
* the trailing stop and breakeven stop follow the same rules in both models
  (OrderManager, long and short),
* the live worker's venue trail distance follows the model too,
* a plain config object without the model (test stubs / old configs) keeps the
  original ATR behaviour,
* invalid modes are rejected instead of silently trading a different plan.

Runs offline on a temp SQLite DB:

    cd backend && python test_risk_exit_model.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TESTDB = os.path.join(tempfile.gettempdir(), "risk_exit_model_test.db")
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"

from pydantic import ValidationError  # noqa: E402

from app.core.fast_test_v1 import FastTestV1Config, FastTestV1OrderManager  # noqa: E402
from app.core.strategy import (  # noqa: E402
    PhantomV2Config, RiskExitModel, RISK_EXIT_ATR, RISK_EXIT_PRICE,
)
from app.services.order_manager import OrderManager  # noqa: E402
from app.services.live_trader import LiveTradeService  # noqa: E402

PASSED = 0
FAILED = 0


def close_to(a, b, tol=1e-6):
    return abs(float(a) - float(b)) < tol


def check(name, cond, detail=''):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name} {detail}")


PRICE = 100000.0
ATR = 500.0
TS = "2024-01-01T00:00:00Z"


def open_trade(cfg, direction=1, price=PRICE, atr=ATR, margin_inr=10000.0):
    om = OrderManager(cfg)
    trade = om.create_order("BTCUSDT", direction, price, atr, TS, margin_inr, 85.0)
    return om, trade


class LegacyConfig:
    """A plain object like the old test stubs: no Risk & Exit model at all."""
    stop_loss_atr = 2.0
    take_profit_atr = 10.0
    trail_activation_atr = 1.5
    trail_distance_atr = 0.5
    breakeven_atr = 0.0
    sl_floor_pct = 0.016
    leverage = 7
    lot_size_btc = 0.001


# ---------------------------------------------------------------------------
# 1. Defaults — all-ATR, exact original formulas
# ---------------------------------------------------------------------------
print("\n-- defaults (all-ATR) --")
cfg = PhantomV2Config()
check("default model is ATR", cfg.risk_exit.model == RISK_EXIT_ATR)
check("default levels are all ATR",
      all(cfg.risk_exit_mode_for(l) == RISK_EXIT_ATR
          for l in ('stop', 'target', 'trail', 'breakeven')))
check("default stop = max(stop_loss_atr × ATR, sl_floor_pct × price)",
      close_to(cfg.stop_loss_distance_for(1, PRICE, ATR), max(2.0 * ATR, 0.016 * PRICE)))
check("default stop: the ATR term wins when it is larger",
      close_to(PhantomV2Config(stop_loss_atr=5.0).stop_loss_distance_for(1, PRICE, ATR), 5.0 * ATR))
check("default target = take_profit_atr × ATR",
      close_to(cfg.take_profit_distance_for(1, PRICE, ATR), 10.0 * ATR))
check("default trail activation = trail_activation_atr × ATR",
      close_to(cfg.trail_activation_distance_for(1, PRICE, ATR), 1.5 * ATR))
check("default trail distance = trail_distance_atr × current ATR",
      close_to(cfg.trail_distance_for(123456.0, ATR), 0.5 * ATR))
check("default breakeven is off (breakeven_atr = 0)",
      cfg.breakeven_trigger_for(1, PRICE, ATR) is None)

om, trade = open_trade(PhantomV2Config())
check("create_order: default levels match the original code",
      close_to(trade.sl, PRICE - max(2.0 * ATR, 0.016 * PRICE))
      and close_to(trade.tp, PRICE + 10.0 * ATR)
      and close_to(trade.trail_activation, PRICE + 1.5 * ATR),
      f"sl={trade.sl} tp={trade.tp} act={trade.trail_activation}")


# ---------------------------------------------------------------------------
# 2. model='price' — every level on a % of the entry price
# ---------------------------------------------------------------------------
print("\n-- price model --")
cfg_p = PhantomV2Config(risk_exit={'model': 'price'})
check("model='price' selects price on every level",
      all(cfg_p.risk_exit_mode_for(l) == RISK_EXIT_PRICE
          for l in ('stop', 'target', 'trail', 'breakeven')))
check("price stop = stop_loss_pct × price",
      close_to(cfg_p.stop_loss_distance_for(1, PRICE, ATR), 0.016 * PRICE))
check("price target = take_profit_pct × price",
      close_to(cfg_p.take_profit_distance_for(1, PRICE, ATR), 0.03 * PRICE))
check("price trail activation = trail_activation_pct × price",
      close_to(cfg_p.trail_activation_distance_for(1, PRICE, ATR), 0.015 * PRICE))
check("price trail distance follows the peak by the chosen %",
      close_to(cfg_p.trail_distance_for(102000.0, ATR), 0.005 * 102000.0))
check("price breakeven fires at entry × (1 + breakeven_pct)",
      close_to(cfg_p.breakeven_trigger_for(1, PRICE, ATR), PRICE * 1.01))
check("price breakeven mirrors for shorts",
      close_to(cfg_p.breakeven_trigger_for(-1, PRICE, ATR), PRICE * 0.99))

om, trade = open_trade(cfg_p)
check("create_order: price model levels",
      close_to(trade.sl, PRICE * 0.984) and close_to(trade.tp, PRICE * 1.03)
      and close_to(trade.trail_activation, PRICE * 1.015)
      and close_to(trade.trail_stop, trade.sl),
      f"sl={trade.sl} tp={trade.tp} act={trade.trail_activation} trail={trade.trail_stop}")

om_s, trade_s = open_trade(cfg_p, direction=-1)
check("create_order: price model mirrors for shorts",
      close_to(trade_s.sl, PRICE * 1.016) and close_to(trade_s.tp, PRICE * 0.97)
      and close_to(trade_s.trail_activation, PRICE * 0.985),
      f"sl={trade_s.sl} tp={trade_s.tp} act={trade_s.trail_activation}")


# ---------------------------------------------------------------------------
# 3. model='both' — per-level mix
# ---------------------------------------------------------------------------
print("\n-- per-level mix ('both') --")
cfg_mix = PhantomV2Config(risk_exit={
    'model': 'both', 'stop_mode': 'price', 'stop_loss_pct': 0.02,
})
check("mixed: stop uses price", cfg_mix.risk_exit_mode_for('stop') == RISK_EXIT_PRICE)
check("mixed: target stays ATR", cfg_mix.risk_exit_mode_for('target') == RISK_EXIT_ATR)
check("mixed: stop distance uses the price %",
      close_to(cfg_mix.stop_loss_distance_for(1, PRICE, ATR), 0.02 * PRICE))
check("mixed: target distance stays in ATR",
      close_to(cfg_mix.take_profit_distance_for(1, PRICE, ATR), 10.0 * ATR))
om, trade = open_trade(cfg_mix)
check("mixed create_order: price stop with an ATR target",
      close_to(trade.sl, PRICE * 0.98) and close_to(trade.tp, PRICE + 10.0 * ATR),
      f"sl={trade.sl} tp={trade.tp}")

# 'atr' / 'price' are shorthands: whichever is named wins over stale per-level keys.
cfg_force = PhantomV2Config(risk_exit={'model': 'atr', 'stop_mode': 'price'})
check("model='atr' forces every level back to ATR",
      close_to(cfg_force.stop_loss_distance_for(1, PRICE, ATR), max(2.0 * ATR, 0.016 * PRICE)))


# ---------------------------------------------------------------------------
# 4. Per-side stop override, both models
# ---------------------------------------------------------------------------
print("\n-- per-side (LONG / SHORT) stop --")
cfg_side = PhantomV2Config(risk_exit={'model': 'price', 'stop_loss_pct': 0.01},
                           entry_conditions={'use_direction_conditions': True,
                                             'long': {'stop_loss_pct': 0.02},
                                             'short': {'stop_loss_pct': 0.03}})
check("side override: LONG stop % used",
      close_to(cfg_side.stop_loss_distance_for(1, PRICE, ATR), 0.02 * PRICE))
check("side override: SHORT stop % used",
      close_to(cfg_side.stop_loss_distance_for(-1, PRICE, ATR), 0.03 * PRICE))
cfg_side_atr = PhantomV2Config(entry_conditions={'use_direction_conditions': True,
                                                 'long': {'stop_loss_atr': 4.0}})
check("side override: ATR stop still honoured (long 4×ATR)",
      close_to(cfg_side_atr.stop_loss_distance_for(1, PRICE, ATR), 4.0 * ATR))
check("side override: ATR stop falls back to shared for the other side",
      close_to(cfg_side_atr.stop_loss_distance_for(-1, PRICE, ATR), max(2.0 * ATR, 0.016 * PRICE)))


# ---------------------------------------------------------------------------
# 5. Trailing stop + breakeven, through update_trade (long and short)
# ---------------------------------------------------------------------------
print("\n-- trailing / breakeven in update_trade --")
cfg_trail = PhantomV2Config(risk_exit={'model': 'both', 'trail_mode': 'price'})
om, trade = open_trade(cfg_trail)
om.update_trade("BTCUSDT", 101900.0, ATR, TS, bar_high_usd=102000.0, bar_low_usd=100000.0)
check("price trail: stop follows the peak by the chosen %",
      abs(trade.trail_stop - (102000.0 - 0.005 * 102000.0)) < 1e-9, f"trail={trade.trail_stop}")

cfg_trail_atr = PhantomV2Config()
om, trade = open_trade(cfg_trail_atr)
om.update_trade("BTCUSDT", 101900.0, ATR, TS, bar_high_usd=102000.0, bar_low_usd=100000.0)
check("ATR trail unchanged",
      abs(trade.trail_stop - (102000.0 - 0.5 * ATR)) < 1e-9, f"trail={trade.trail_stop}")

cfg_be = PhantomV2Config(risk_exit={'model': 'both', 'breakeven_mode': 'price'})
om, trade = open_trade(cfg_be)
om.update_trade("BTCUSDT", 101400.0, ATR, TS, bar_high_usd=101500.0, bar_low_usd=100000.0)
check("price breakeven ratchets the stop to entry",
      close_to(trade.sl, PRICE), f"sl={trade.sl}")

om_be_off, trade_be_off = open_trade(PhantomV2Config())
om_be_off.update_trade("BTCUSDT", 101400.0, ATR, TS, bar_high_usd=101500.0, bar_low_usd=100000.0)
check("ATR breakeven stays off by default (stop untouched)",
      close_to(trade_be_off.sl, PRICE - max(2.0 * ATR, 0.016 * PRICE)), f"sl={trade_be_off.sl}")

cfg_be_s = PhantomV2Config(risk_exit={'model': 'both', 'breakeven_mode': 'price'})
om, trade = open_trade(cfg_be_s, direction=-1)
om.update_trade("BTCUSDT", 98600.0, ATR, TS, bar_high_usd=100000.0, bar_low_usd=98500.0)
check("price breakeven mirrors for shorts", close_to(trade.sl, PRICE), f"sl={trade.sl}")


# ---------------------------------------------------------------------------
# 6. Live worker: the venue bracket trail follows the model
# ---------------------------------------------------------------------------
print("\n-- live venue trail --")
live = LiveTradeService.__new__(LiveTradeService)
live.config = PhantomV2Config()
check("live ATR trail = trail_distance_atr × ATR",
      close_to(live._trail_amount(ATR, PRICE), 0.5 * ATR))
live.config = PhantomV2Config(risk_exit={'model': 'price'})
check("live price trail = trail_distance_pct × price",
      close_to(live._trail_amount(ATR, PRICE), 0.005 * PRICE))
check("live price trail without a price sends no venue trail",
      live._trail_amount(ATR, None) is None)
live.config = LegacyConfig()
live.config.trail_distance_atr = 0.0   # legacy configs can carry 0 = off
check("live ATR trail of 0 sends no venue trail", live._trail_amount(ATR, PRICE) is None)


# ---------------------------------------------------------------------------
# 7. Legacy configs, FastTest V1 and validation
# ---------------------------------------------------------------------------
print("\n-- legacy / V1 / validation --")

om_legacy, trade_legacy = open_trade(LegacyConfig())
check("legacy config object: original ATR levels",
      close_to(trade_legacy.sl, PRICE - max(2.0 * ATR, 0.016 * PRICE))
      and close_to(trade_legacy.tp, PRICE + 10.0 * ATR), f"sl={trade_legacy.sl} tp={trade_legacy.tp}")

v1_cfg = FastTestV1Config()
check("FastTest V1 inherits the all-ATR default", v1_cfg.risk_exit.model == RISK_EXIT_ATR)
v1_om = FastTestV1OrderManager(v1_cfg)
v1_trade = v1_om.create_order("BTCUSDT", 1, PRICE, ATR, TS, 10000.0, 85.0)
base_trade = OrderManager(FastTestV1Config()).create_order("BTCUSDT", 1, PRICE, ATR, TS, 10000.0, 85.0)
check("FastTest V1 levels are identical to the base engine",
      close_to(v1_trade.sl, base_trade.sl) and close_to(v1_trade.tp, base_trade.tp)
      and close_to(v1_trade.trail_activation, base_trade.trail_activation))
check("FastTest V1 bracket TP is still its +0.90% level",
      close_to(v1_om.bracket_take_profit(1, PRICE, base_trade.tp), PRICE * 1.009))

for bad in ({'model': 'nope'}, {'stop_mode': 'nope'}, {'model': 'both', 'target_mode': 'ATR!'}):
    try:
        RiskExitModel(**bad)
        ok = False
    except ValidationError:
        ok = True
    check(f"invalid risk exit rejected: {bad}", ok)

check("percentages round-trip through model_dump",
      close_to(PhantomV2Config(risk_exit={'model': 'price', 'stop_loss_pct': 0.02})
               .model_dump()['risk_exit']['stop_loss_pct'], 0.02))
check("config without the model still resolves ATR",
      PhantomV2Config().risk_exit_config().model == RISK_EXIT_ATR)

summary = PhantomV2Config(risk_exit={'model': 'price'}).risk_exit_summary()
check("summary: model + labels + per-level text",
      summary['model'] == RISK_EXIT_PRICE and summary['model_label'].startswith('Price')
      and summary['levels']['stop']['mode'] == RISK_EXIT_PRICE
      and summary['levels']['target']['text'].endswith('% price')
      and 'distance_pct' in summary['levels']['trail'],
      str(summary)[:200])
check("summary text reads as the client sees it",
      PhantomV2Config().risk_exit_text().startswith('Stop ')
      and '×ATR' in PhantomV2Config().risk_exit_text())

print(f"\nPASSED: {PASSED}  FAILED: {FAILED}")
sys.exit(1 if FAILED else 0)
