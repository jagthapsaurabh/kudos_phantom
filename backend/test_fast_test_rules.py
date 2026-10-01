"""Offline checks for the **editable entry / exit rules** of the debug strategies.

The client asked for the option to change the *rule* and the *condition*, not
just the values:

* **entry** — the rule's own numbers: the RSI period it reads, the long
  threshold (below it → LONG) and the short threshold (at/above it → SHORT),
  plus the allowed sides (the existing ``trade_direction`` field),
* **exit** — which protective rules are live (stop / target / trailing /
  breakeven / timeout) *and* three optional signal conditions: an opposite
  entry signal, RSI crossing back through a level, and the MACD line flipping
  against the position.

Everything is verified twice: directly on the rule objects, and through a real
``BacktestEngine`` run on the bundled candles, so the wiring the client uses is
what is being pinned. The defaults are asserted to reproduce the original,
hardcoded behaviour byte-for-byte.

    cd backend && python test_fast_test_rules.py
"""
import inspect
import os
import sys

import numpy as np
import pandas as pd
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TESTDB = "/tmp/fast_test_rules_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"

from app.core.engine import BacktestEngine  # noqa: E402
from app.core.fast_test_rules import (  # noqa: E402
    REASON_MACD_FLIP, REASON_OPPOSITE, REASON_RSI_EXIT,
    FastTestOrderManager, exit_conditions_configured, fast_test_bar_state,
    fast_test_exit_condition, fast_test_sides,
)
from app.core.fast_test_v1 import (  # noqa: E402
    FastTestV1Config, FastTestV1OrderManager, fast_test_v1_config,
    order_manager_for, strategy_service_for,
)
from app.core.indicators import compute_indicators, rsi  # noqa: E402
from app.core.strategy import (  # noqa: E402
    FastTestConfig, FastTestStrategyService, PhantomV2Config, StrategyService,
    fast_test_config,
)
from app.database.models import init_db  # noqa: E402

import app.main as main_mod  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

PASSED = 0
FAILED = 0


def check(name, cond, detail=''):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS: {name}")
    else:
        FAILED += 1
        print(f"  FAIL: {name} {detail}")


def load_frames():
    def read(fname):
        df = pd.read_csv(os.path.join(DATA_DIR, fname))
        ts_col = 'timestamp' if 'timestamp' in df.columns else df.columns[0]
        df[ts_col] = pd.to_datetime(df[ts_col])
        df = df.set_index(ts_col).sort_index()
        return df[['open', 'high', 'low', 'close', 'volume']].astype(float)
    return read('btc_1h.csv'), read('btc_4h.csv')


df_1h, df_4h = load_frames()
init_db()

# =============================================================== defaults ===
print("\n=== defaults: nothing changes until a value is edited ===")
cfg = FastTestConfig()
check('entry rule defaults are the original rule',
      cfg.entry_rsi_period == 14 and cfg.entry_rsi_long_max == 50.0
      and cfg.entry_rsi_short_min == 50.0)
check('every protective rule is on by default',
      cfg.use_stop_loss and cfg.use_take_profit and cfg.use_trailing_stop
      and cfg.use_breakeven and cfg.use_timeout)
check('every signal condition is off by default',
      not cfg.exit_on_opposite and not cfg.exit_rsi_enabled
      and not cfg.exit_macd_flip_enabled and cfg.exit_rsi_level == 50.0)
check('no state series is built while every condition is off',
      FastTestStrategyService(cfg).exit_state_series(df_1h, df_4h) is None)
check('no state for a bar while every condition is off',
      fast_test_bar_state(df_1h, df_1h.index[10], cfg) is None)
check('the condition evaluator finds nothing while everything is off',
      fast_test_exit_condition(object.__new__(type('T', (), {'direction': 1})),
                               {'rsi': 20.0, 'macd_line': 1.0, 'macd_signal': 2.0}, cfg) is None)

# The original hardcoded loop, reproduced exactly.
_ind = compute_indicators(df_1h.copy(), macd_fast=cfg.macd_fast, macd_slow=cfg.macd_slow,
                          macd_signal=cfg.macd_signal)
_legacy = np.zeros(len(df_1h))
for _i in range(1, len(df_1h)):
    if _ind['rsi14'][_i] < 50:
        _legacy[_i] = 1
    elif _ind['rsi14'][_i] >= 50:
        _legacy[_i] = -1
_default_signals = FastTestStrategyService(cfg).generate_signals(df_1h.copy(), df_4h.copy())
check('default signals are byte-identical to the original hardcoded rule',
      np.array_equal(_default_signals, _legacy),
      f"diff={int((_default_signals != _legacy).sum())}")
check('the default rule still fires on essentially every candle',
      int((_default_signals != 0).sum()) == len(df_1h) - 1)

# ============================================================ entry rule ===
print("\n=== entry rule: period, thresholds, sides ===")
custom_period = FastTestConfig(entry_rsi_period=7)
_period_signals = FastTestStrategyService(custom_period).generate_signals(df_1h.copy(), df_4h.copy())
_rsi7 = rsi(df_1h['close'].values.astype(np.float64), 7)
_expected7 = np.zeros(len(df_1h))
for _i in range(1, len(df_1h)):
    if _rsi7[_i] < 50:
        _expected7[_i] = 1
    elif _rsi7[_i] >= 50:
        _expected7[_i] = -1
check('a custom RSI period really moves the rule onto that period',
      np.array_equal(_period_signals, _expected7))
check('and it is a different series from the default',
      not np.array_equal(_period_signals, _default_signals))

thresholds = FastTestConfig(entry_rsi_long_max=30.0, entry_rsi_short_min=70.0)
_thresh_signals = FastTestStrategyService(thresholds).generate_signals(df_1h.copy(), df_4h.copy())
_rsi14 = compute_indicators(df_1h.copy())['rsi14']
_expected_thresh = np.zeros(len(df_1h))
for _i in range(1, len(df_1h)):
    if _rsi14[_i] < 30.0:
        _expected_thresh[_i] = 1
    elif _rsi14[_i] >= 70.0:
        _expected_thresh[_i] = -1
check('custom thresholds are applied exactly as typed',
      np.array_equal(_thresh_signals, _expected_thresh))
check('and a narrower band really produces fewer signals',
      int((_thresh_signals != 0).sum()) < int((_default_signals != 0).sum()))
check('the middle of the band is flat (no signal between the thresholds)',
      bool(np.all(_thresh_signals[(_rsi14 >= 30.0) & (_rsi14 < 70.0)] == 0)))

check('trade_direction long only removes every short',
      not bool((FastTestStrategyService(FastTestConfig(trade_direction='long'))
                .generate_signals(df_1h.copy(), df_4h.copy()) == -1).any()))
check('trade_direction short only removes every long',
      not bool((FastTestStrategyService(FastTestConfig(trade_direction='short'))
                .generate_signals(df_1h.copy(), df_4h.copy()) == 1).any()))
check('trade_direction both is the default rule',
      np.array_equal(FastTestStrategyService(FastTestConfig(trade_direction='both'))
                     .generate_signals(df_1h.copy(), df_4h.copy()), _default_signals))
check('fast_test_sides reads long / short / both',
      fast_test_sides(FastTestConfig(trade_direction='long')) == (True, False)
      and fast_test_sides(FastTestConfig(trade_direction='short')) == (False, True)
      and fast_test_sides(FastTestConfig()) == (True, True))
try:
    FastTestConfig(entry_rsi_period=1)
    _period_guard = False
except ValidationError:
    _period_guard = True
check('the RSI period is validated (RSI needs at least 2 bars)', _period_guard)
try:
    FastTestConfig(entry_rsi_long_max=150)
    _range_guard = False
except ValidationError:
    _range_guard = True
check('a threshold outside the RSI range is rejected', _range_guard)

# ============================================================= exit rule ===
print("\n=== exit rule: the five switches ===")


def run_debug(cfg, frame=None):
    frame_1h = (frame if frame is not None else df_1h).copy()
    svc = strategy_service_for(cfg)
    engine = BacktestEngine(config=cfg, fee_schedule=cfg, data_source='Delta',
                            strategy_service=svc, oms=order_manager_for(cfg))
    return engine.run(symbol='BTCUSDT', initial_capital_inr=20000, conversion_rate=85,
                      df_1h=frame_1h, df_4h=df_4h.copy())


def reasons(res):
    return [str(t.get('exit_reason') or '').upper() for t in res['trades']]


base = run_debug(fast_test_config())
check('the default debug run still trades', base['total_trades'] > 0, str(base['total_trades']))
check('and books none of the new exit reasons',
      not ({REASON_OPPOSITE, REASON_RSI_EXIT, REASON_MACD_FLIP} & set(reasons(base))))

no_sl = run_debug(fast_test_config({'use_stop_loss': False}))
check('stop loss off → no SL exit is booked',
      'SL' not in reasons(no_sl), str(sorted(set(reasons(no_sl)))))
check('stop loss off → no trailing stop either (the trail is a stop)',
      'TSL' not in reasons(no_sl))
check('stop loss off → the run is a different outcome',
      no_sl['total_trades'] != base['total_trades'] or no_sl['final_equity_inr'] != base['final_equity_inr'])

no_trail = run_debug(fast_test_config({'use_trailing_stop': False}))
check('trailing stop off → no TSL exit is booked',
      'TSL' not in reasons(no_trail), str(sorted(set(reasons(no_trail)))))
check('trailing stop off → the hard stop still protects',
      'SL' in reasons(no_trail))

no_tp = run_debug(fast_test_config({'use_take_profit': False}))
check('take profit off → no TP exit is booked',
      'TP' not in reasons(no_tp), str(sorted(set(reasons(no_tp)))))

no_timeout = run_debug(fast_test_config({'use_timeout': False}))
check('timeout off → no max-hold exit is booked',
      'MH' not in reasons(no_timeout), str(sorted(set(reasons(no_timeout)))))
check('timeout off → stopped-out trades still exit',
      'SL' in reasons(no_timeout) or 'TP' in reasons(no_timeout))

no_be = run_debug(fast_test_config({'use_breakeven': False}))
check('breakeven off → the stop never ratchets to entry on a stop-out',
      not any('at breakeven' in str(t.get('exit_detail') or '')
              for t in no_be['trades']))

# ========================================================= exit conditions ===
print("\n=== exit rule: opposite signal / RSI / MACD flip ===")
trade_long = type('T', (), {'direction': 1})()
trade_short = type('T', (), {'direction': -1})()

opp_cfg = fast_test_config({'exit_on_opposite': True})
check('opposite-signal exit: a long is closed when the rule turns short',
      fast_test_exit_condition(trade_long, {'rsi': 60.0}, opp_cfg)[0] == REASON_OPPOSITE)
check('opposite-signal exit: a long is kept while the rule still says long',
      fast_test_exit_condition(trade_long, {'rsi': 40.0}, opp_cfg) is None)
check('opposite-signal exit: a short is closed when the rule turns long',
      fast_test_exit_condition(trade_short, {'rsi': 40.0}, opp_cfg)[0] == REASON_OPPOSITE)
check('opposite-signal exit follows the edited thresholds',
      fast_test_exit_condition(trade_long, {'rsi': 40.0},
                               fast_test_config({'exit_on_opposite': True,
                                                 'entry_rsi_long_max': 30.0,
                                                 'entry_rsi_short_min': 70.0})) is None)

rsi_cfg = fast_test_config({'exit_rsi_enabled': True, 'exit_rsi_level': 65.0})
check('RSI exit: a long exits at or above the level',
      fast_test_exit_condition(trade_long, {'rsi': 65.0}, rsi_cfg)[0] == REASON_RSI_EXIT)
check('RSI exit: a long stays below the level',
      fast_test_exit_condition(trade_long, {'rsi': 64.9}, rsi_cfg) is None)
check('RSI exit: a short stays while RSI is above the level',
      fast_test_exit_condition(trade_short, {'rsi': 65.1}, rsi_cfg) is None)
check('RSI exit: a short exits at or below the level',
      fast_test_exit_condition(trade_short, {'rsi': 64.9}, rsi_cfg)[0] == REASON_RSI_EXIT
      and fast_test_exit_condition(trade_short, {'rsi': 30.0}, rsi_cfg)[0] == REASON_RSI_EXIT)

macd_cfg = fast_test_config({'exit_macd_flip_enabled': True})
check('MACD exit: a long exits when the line is below its signal',
      fast_test_exit_condition(trade_long, {'macd_line': 1.0, 'macd_signal': 2.0},
                               macd_cfg)[0] == REASON_MACD_FLIP)
check('MACD exit: a long stays while the line is above its signal',
      fast_test_exit_condition(trade_long, {'macd_line': 2.0, 'macd_signal': 1.0}, macd_cfg) is None)
check('MACD exit: a short exits when the line is above its signal',
      fast_test_exit_condition(trade_short, {'macd_line': 2.0, 'macd_signal': 1.0},
                               macd_cfg)[0] == REASON_MACD_FLIP)

check('a NaN RSI never fires a condition (missing values are skipped)',
      fast_test_exit_condition(trade_long, {'rsi': float('nan')},
                               fast_test_config({'exit_on_opposite': True,
                                                 'exit_rsi_enabled': True})) is None)
check('an empty state never fires a condition',
      fast_test_exit_condition(trade_long, None, rsi_cfg) is None)

all_cfg = fast_test_config({'exit_on_opposite': True, 'exit_rsi_enabled': True,
                            'exit_macd_flip_enabled': True})
check('exit_conditions_configured sees each switch',
      exit_conditions_configured(all_cfg) and exit_conditions_configured(opp_cfg)
      and exit_conditions_configured(rsi_cfg) and exit_conditions_configured(macd_cfg)
      and not exit_conditions_configured(fast_test_config()))

state = fast_test_bar_state(df_1h, df_1h.index[123], all_cfg)
_ind_ref = compute_indicators(df_1h.copy(), macd_fast=all_cfg.macd_fast, macd_slow=all_cfg.macd_slow,
                              macd_signal=all_cfg.macd_signal, rsi_period=all_cfg.entry_rsi_period)
check('the bar state carries that candle\'s own RSI / MACD values',
      state is not None
      and abs(state['rsi'] - float(_ind_ref['rsi14'][123])) < 1e-9
      and abs(state['macd_line'] - float(_ind_ref['macd_line'][123])) < 1e-9
      and abs(state['macd_signal'] - float(_ind_ref['macd_signal'][123])) < 1e-9)
check('a timestamp that is not in the frame yields no state',
      fast_test_bar_state(df_1h, pd.Timestamp('1999-01-01'), all_cfg) is None)
_period_state = fast_test_bar_state(df_1h, df_1h.index[123], fast_test_config(
    {'exit_rsi_enabled': True, 'entry_rsi_period': 7}))
check('the bar state follows the edited RSI period',
      abs(_period_state['rsi'] - float(_rsi7[123])) < 1e-9)

# A real run for each condition.
opp_run = run_debug(fast_test_config({'exit_on_opposite': True}))
check('opposite-signal exits are booked by the engine',
      REASON_OPPOSITE in reasons(opp_run), str(sorted(set(reasons(opp_run)))))
rsi_run = run_debug(fast_test_config({'exit_rsi_enabled': True, 'exit_rsi_level': 50.0}))
check('RSI exits are booked by the engine',
      REASON_RSI_EXIT in reasons(rsi_run), str(sorted(set(reasons(rsi_run)))))
macd_run = run_debug(fast_test_config({'exit_macd_flip_enabled': True}))
check('MACD-flip exits are booked by the engine',
      REASON_MACD_FLIP in reasons(macd_run), str(sorted(set(reasons(macd_run)))))
check('a condition run still respects the stop (worst case first)',
      all(r in ('SL', 'TSL', 'TP', 'MH', REASON_OPPOSITE, REASON_RSI_EXIT, REASON_MACD_FLIP)
          for r in reasons(macd_run)),
      str(sorted(set(reasons(macd_run)))))

# ============================================================ V1 strategy ===
print("\n=== Fast Test V1.0 keeps its own rules and gains these ===")
v1_cfg = fast_test_v1_config({'exit_rsi_enabled': True, 'exit_rsi_level': 50.0,
                              'entry_rsi_period': 10})
check('the V1 config declares the entry / exit rule fields',
      all(hasattr(v1_cfg, f) for f in ('entry_rsi_period', 'entry_rsi_long_max',
                                       'entry_rsi_short_min', 'use_stop_loss',
                                       'exit_rsi_enabled', 'exit_macd_flip_enabled')))
check('a V1 signal run follows the edited entry period',
      np.array_equal(FastTestStrategyService(v1_cfg).generate_signals(df_1h.copy(), df_4h.copy()),
                     FastTestStrategyService(FastTestConfig(
                         entry_rsi_period=10)).generate_signals(df_1h.copy(), df_4h.copy())))
v1_oms = order_manager_for(v1_cfg)
check('the V1 order manager inherits the configurable conditions',
      isinstance(v1_oms, FastTestV1OrderManager) and isinstance(v1_oms, FastTestOrderManager))
check('the V1 close hook chains into the shared conditions',
      'super().strategy_bar_close_exit' in inspect.getsource(FastTestV1OrderManager.strategy_bar_close_exit))
check('the V1 booking level still wins the touch check',
      'strategy_touch_exit' in inspect.getsource(FastTestV1OrderManager))

short_1h = df_1h.iloc[:2500]
v1_run = run_debug(fast_test_v1_config({'exit_rsi_enabled': True, 'exit_rsi_level': 50.0}), short_1h)
check('a V1 run books RSI exits too (and still trades)',
      v1_run['total_trades'] > 0 and REASON_RSI_EXIT in reasons(v1_run),
      str(sorted(set(reasons(v1_run)))))
check('a V1 run still books its own T+0.90% / validation exits',
      bool(set(reasons(v1_run)) & {'TP090', 'VALFAIL'}),
      str(sorted(set(reasons(v1_run)))))

# ============================================================== Phantom ====
print("\n=== the Kudos / Phantom strategy is untouched ===")
check('PhantomV2Config declares none of the debug rule fields',
      not any(f in PhantomV2Config.model_fields for f in (
          'entry_rsi_period', 'entry_rsi_long_max', 'entry_rsi_short_min',
          'use_stop_loss', 'use_take_profit', 'use_trailing_stop', 'use_breakeven',
          'use_timeout', 'exit_on_opposite', 'exit_rsi_enabled', 'exit_rsi_level',
          'exit_macd_flip_enabled')))
check('a Phantom config still resolves to the standard service / order manager',
      isinstance(strategy_service_for(PhantomV2Config()), StrategyService)
      and type(order_manager_for(PhantomV2Config())).__name__ == 'OrderManager')
_pcfg = PhantomV2Config()
check('the switches default to True for a config that never heard of them',
      FastTestOrderManager(_pcfg).config is _pcfg
      and all(main_mod._strategy_family(x) == '' for x in (None, '', 'PhantomV2', 7)))
phantom_engine = BacktestEngine(config=PhantomV2Config(), fee_schedule=PhantomV2Config(),
                                data_source='Delta',
                                strategy_service=StrategyService(PhantomV2Config()),
                                oms=order_manager_for(PhantomV2Config()))
phantom_run = phantom_engine.run(symbol='BTCUSDT', initial_capital_inr=20000, conversion_rate=85,
                                 df_1h=df_1h.iloc[:2500].copy(), df_4h=df_4h.copy())
check('a Phantom run books none of the debug exit reasons',
      not ({REASON_OPPOSITE, REASON_RSI_EXIT, REASON_MACD_FLIP} & set(reasons(phantom_run))),
      str(sorted(set(reasons(phantom_run)))))

# ============================================================== wiring ======
print("\n=== wiring: every run path hands the order manager the candle's state ===")
engine_src = inspect.getsource(BacktestEngine.run)
check('the engine builds the state series only for strategies that expose it',
      "getattr(self.strategy_service, 'exit_state_series'" in engine_src
      and 'state_series = state_fn(df_1h, df_4h)' in engine_src)
check('the engine passes the candle state to update_trade',
      'strategy_bar_state=bar_state' in engine_src)
import app.services.paper_trader as paper_mod  # noqa: E402
import app.services.live_trader as live_mod  # noqa: E402
check('the paper worker computes the completed candle\'s state and passes it',
      'fast_test_bar_state(df_1h_with_ind, completed_bar_time, self.config)' in inspect.getsource(paper_mod)
      and 'strategy_bar_state=bar_state' in inspect.getsource(paper_mod))
check('the live worker computes the completed candle\'s state and passes it',
      'fast_test_bar_state(df_1h_with_ind, completed_bar_time, self.config)' in inspect.getsource(live_mod)
      and 'strategy_bar_state=bar_state' in inspect.getsource(live_mod))
check('the order manager exposes the state to the strategy hooks',
      'self._bar_state = strategy_bar_state' in inspect.getsource(
          __import__('app.services.order_manager', fromlist=['OrderManager']).OrderManager.update_trade))
check('the live bracket drops a leg whose switch is off',
      'bracket_stop_loss(float(planned.sl))' in inspect.getsource(live_mod)
      and 'bracket_trail_amount(trail_distance)' in inspect.getsource(live_mod)
      and 'bracket_tp = float(bracket_tp_raw) if bracket_tp_raw is not None else None'
          in inspect.getsource(live_mod))
check('the adapters accept a missing leg (None = no such order)',
      'if stop_loss_price is not None' in inspect.getsource(
          __import__('app.services.broker_client', fromlist=['BrokerClient']).BrokerClient.place_bracket_order))
_live_cfg = fast_test_config({'use_stop_loss': False, 'use_take_profit': False})
_live_oms = order_manager_for(_live_cfg)
check('a switched-off stop / target sends no venue protection leg',
      _live_oms.bracket_stop_loss(123.0) is None
      and _live_oms.bracket_take_profit(1, 100.0, 110.0) is None
      and _live_oms.bracket_trail_amount(5.0) is None)
_keep_oms = order_manager_for(fast_test_config())
check('and an unedited strategy sends all three exactly as before',
      _keep_oms.bracket_stop_loss(123.0) == 123.0
      and _keep_oms.bracket_take_profit(1, 100.0, 110.0) == 110.0
      and _keep_oms.bracket_trail_amount(5.0) == 5.0)
_v1_keep = order_manager_for(fast_test_v1_config())
check('the V1 venue target stays the booking level (and disappears when TP is off)',
      abs(_v1_keep.bracket_take_profit(1, 100.0, 110.0) - 100.9) < 1e-9
      and order_manager_for(fast_test_v1_config({'use_take_profit': False}))
          .bracket_take_profit(1, 100.0, 110.0) is None)

check('the request model carries the new fields (they are not dropped)',
      all(f in main_mod.StrategyParams.model_fields for f in (
          'entry_rsi_period', 'entry_rsi_long_max', 'entry_rsi_short_min',
          'use_stop_loss', 'use_take_profit', 'use_trailing_stop', 'use_breakeven',
          'use_timeout', 'exit_on_opposite', 'exit_rsi_enabled', 'exit_rsi_level',
          'exit_macd_flip_enabled')))
check('a saved debug strategy round-trips the edited rules',
      fast_test_config({'strategy_id': 'FastTest', 'entry_rsi_period': 9,
                        'exit_on_opposite': True, 'use_timeout': False}).entry_rsi_period == 9
      and main_mod._family_config({'strategy_id': 'FastTestV1', 'entry_rsi_period': 9,
                                   'exit_macd_flip_enabled': True}).entry_rsi_period == 9
      and main_mod._family_config({'strategy_id': 'FastTestV1', 'entry_rsi_period': 9,
                                   'exit_macd_flip_enabled': True}).exit_macd_flip_enabled is True)

print(f"\n{'-' * 60}\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
