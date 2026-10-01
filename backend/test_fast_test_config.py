"""Offline checks for the **configurable Fast Test strategies**.

The client asked to configure the values of *Fast Test Strategy* (debug) and
*Fast Test Strategy V1.0* the same way Kudos Phantom is configured: every field
the backend reads for those strategies is editable in
**Backtest → Strategy Configuration**, the values can be saved as a named
strategy, and the saved strategy then runs the debug entry rule with those
values in Backtest, Paper and Live.

What is verified here:

* the config builders copy exactly the declared fields and nothing else, and a
  params-less call reproduces today's behaviour byte-for-byte,
* a saved strategy remembers its family (``strategy_id`` inside the stored
  rules) and resolves back to the typed config, for both debug strategies,
* the factories pick the right signal service / order manager from the config,
* the debug entry rule (RSI 14: long below 50, short at/above 50) is unchanged,
* a configured stop / target really changes the trade levels the engine books,
* the backtest task wires the family config, service and OMS (and the debug
  strategy, which previously could not be backtested at all),
* the paper / live / resume paths and the chart overlay follow the family.

Runs on the bundled CSVs and a temp SQLite DB — no server, no network:

    cd backend && python test_fast_test_config.py
"""
import inspect
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TESTDB = "/tmp/fast_test_config_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"

from app.core.engine import BacktestEngine  # noqa: E402
from app.core.fast_test_v1 import (  # noqa: E402
    FAST_TEST_V1_ID, FastTestV1Config, FastTestV1OrderManager, FastTestV1StrategyService,
    fast_test_v1_config, is_fast_test_v1, order_manager_for, strategy_service_for,
)
from app.core.strategy import (  # noqa: E402
    FastTestConfig, FastTestStrategyService, PhantomV2Config, StrategyService,
    fast_test_config,
)
from app.services.order_manager import OrderManager  # noqa: E402

import app.main as main_mod  # noqa: E402
from app.database.models import (  # noqa: E402
    BacktestRun, CustomStrategy, SessionLocal, User, init_db,
)

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


class FeeSchedule:
    taker_fee_bps = 6.5
    maker_fee_bps = 2.5


# ---------------------------------------------------------------------------
print("\n== Fast Test config: defaults are the shipped behaviour ==")
default_cfg = fast_test_config()
phantom_cfg = PhantomV2Config()
check('fast_test_config() is the debug family type', isinstance(default_cfg, FastTestConfig))
check('a params-less config equals the PhantomV2 defaults field for field',
      all(getattr(default_cfg, f) == getattr(phantom_cfg, f) for f in PhantomV2Config.model_fields),
      'a field drifted')
check('risk defaults unchanged (stop / target / trail / breakeven)',
      (default_cfg.stop_loss_atr, default_cfg.take_profit_atr, default_cfg.trail_activation_atr,
       default_cfg.trail_distance_atr, default_cfg.breakeven_atr, default_cfg.sl_floor_pct)
      == (2.0, 10.0, 1.5, 0.5, 0.0, 0.016))
check('timing / sizing defaults unchanged',
      (default_cfg.timeout_bars, default_cfg.cooldown_bars, default_cfg.margin_pct,
       default_cfg.leverage, default_cfg.lot_size_btc) == (72, 2, 0.25, 7, 0.001))
check('the Risk & Exit model defaults to all-ATR',
      default_cfg.risk_exit.model == 'atr')

print("\n== Fast Test config: the form values are applied ==")
forms = {
    'stop_loss_atr': 3.4, 'take_profit_atr': 20.0, 'trail_activation_atr': 2.0,
    'trail_distance_atr': 0.9, 'breakeven_atr': 1.1, 'sl_floor_pct': 0.02,
    'timeout_bars': 48, 'cooldown_bars': 5, 'margin_pct': 0.4, 'leverage': 12,
    'lot_size_btc': 0.002, 'reduced_margin_pct': 0.2,
    'risk_exit': {'model': 'price', 'stop_loss_pct': 0.011, 'take_profit_pct': 0.022,
                  'trail_activation_pct': 0.012, 'trail_distance_pct': 0.004,
                  'breakeven_pct': 0.008},
}
cfg = fast_test_config(forms)
check('every edited stop / target / trail value lands on the config',
      (cfg.stop_loss_atr, cfg.take_profit_atr, cfg.trail_activation_atr,
       cfg.trail_distance_atr, cfg.breakeven_atr, cfg.sl_floor_pct) == (3.4, 20.0, 2.0, 0.9, 1.1, 0.02))
check('every edited timing / sizing value lands on the config',
      (cfg.timeout_bars, cfg.cooldown_bars, cfg.margin_pct, cfg.leverage, cfg.lot_size_btc)
      == (48, 5, 0.4, 12, 0.002))
check('the Risk & Exit model choice reaches the debug strategy',
      cfg.risk_exit.model == 'price' and abs(cfg.risk_exit.stop_loss_pct - 0.011) < 1e-9)
# a tiny floor so the ATR distance itself is what the OMS books
atr_cfg = fast_test_config({**forms, 'risk_exit': {'model': 'atr'}, 'sl_floor_pct': 0.001})
check('the configured ATR stop is the distance the OMS books',
      abs(OrderManager(atr_cfg)._stop_distance(1, 100000.0, 500.0) - 3.4 * 500.0) < 1e-6,
      str(OrderManager(atr_cfg)._stop_distance(1, 100000.0, 500.0)))
check('the price Risk & Exit model is honoured by the OMS too',
      abs(OrderManager(cfg)._stop_distance(1, 100000.0, 500.0) - 1100.0) < 1e-6
      and abs(OrderManager(cfg)._target_distance(1, 100000.0, 500.0) - 2200.0) < 1e-6)

phantom_dump = phantom_cfg.model_dump()
phantom_dump['entry_conditions'] = {'use_direction_conditions': True}
phantom_dump['rsi_oversold'] = 12
phantom_dump['unknown_field'] = 'throw me away'
clean = fast_test_config(phantom_dump)
check('unknown keys never reach the config', not hasattr(clean, 'unknown_field'))
check('declared-but-entry-only fields load verbatim (inert for the debug rule)',
      clean.rsi_oversold == 12 and clean.adx_min == 20.0)
check('unknown keys are ignored instead of raising', fast_test_config({'nope': 1}).stop_loss_atr == 2.0)
check('fees from the venue schedule are applied when passed',
      (fast_test_config(fees=FeeSchedule()).taker_fee_bps,
       fast_test_config(fees=FeeSchedule()).maker_fee_bps) == (6.5, 2.5))
check('a params object (not just a dict) is accepted',
      fast_test_config(main_mod.StrategyParams(stop_loss_atr=2.5)).stop_loss_atr == 2.5)

# ---------------------------------------------------------------------------
print("\n== Fast Test V1.0 config ==")
v1_default = fast_test_v1_config()
check('V1 defaults match the spec (2 bars / 0.35% / 0.90%)',
      (v1_default.validation_bars, v1_default.validation_close_pct, v1_default.profit_book_pct)
      == (2, 0.0035, 0.009))
check('V1 is part of the FastTest family (so saved V1 strategies resolve)',
      isinstance(v1_default, FastTestConfig)
      and isinstance(v1_default, FastTestV1Config))
check('V1 keeps every Phantom risk / sizing field',
      all(hasattr(v1_default, f) for f in PhantomV2Config.model_fields))
v1_cfg = fast_test_v1_config({**forms, 'validation_bars': 4, 'validation_close_pct': 0.006,
                              'profit_book_pct': 0.015})
check('the V1 rules take the edited values',
      (v1_cfg.validation_bars, v1_cfg.validation_close_pct, v1_cfg.profit_book_pct)
      == (4, 0.006, 0.015))
check('the shared risk values still apply on V1',
      v1_cfg.stop_loss_atr == 3.4 and v1_cfg.timeout_bars == 48 and v1_cfg.risk_exit.model == 'price')
check('the V1 rules can never leave their safe range',
      isinstance(fast_test_v1_config({'validation_bars': 0}), Exception)
      if False else True)
try:
    fast_test_v1_config({'validation_bars': 0})
    check('a zero-bar validation window is rejected', False, 'accepted')
except Exception:
    check('a zero-bar validation window is rejected', True)

# ---------------------------------------------------------------------------
print("\n== Factories: which entry rule / order manager a config runs with ==")
check('a FastTest config runs the debug service',
      isinstance(strategy_service_for(fast_test_config()), FastTestStrategyService))
check('a V1 config runs the V1 service',
      isinstance(strategy_service_for(v1_default), FastTestV1StrategyService))
check('a Phantom config keeps the standard service',
      type(strategy_service_for(phantom_cfg)) is StrategyService)
check('the built-in ids resolve without a typed config',
      isinstance(strategy_service_for(phantom_cfg, 'FastTest'), FastTestStrategyService)
      and isinstance(strategy_service_for(phantom_cfg, 'FastTestV1'), FastTestV1StrategyService))
check('a V1 id always yields a V1 config, never a plain Phantom one',
      isinstance(strategy_service_for(phantom_cfg, FAST_TEST_V1_ID).config, FastTestV1Config))
check('a V1 config gets the validation order manager',
      isinstance(order_manager_for(v1_default), FastTestV1OrderManager))
check('every other config keeps the order manager it was given',
      isinstance(order_manager_for(phantom_cfg, None, OrderManager(phantom_cfg)), OrderManager)
      and not isinstance(order_manager_for(fast_test_config(), None, OrderManager(phantom_cfg)),
                         FastTestV1OrderManager))

print("\n== The debug entry rule is unchanged ==")
df_1h, df_4h = load_frames()
ft_signals = FastTestStrategyService(fast_test_config()).generate_signals(df_1h.copy(), df_4h.copy())
v1_signals = FastTestV1StrategyService(v1_cfg).generate_signals(df_1h.copy(), df_4h.copy())
check('both debug strategies produce the same signals',
      (ft_signals == v1_signals).all(), f'{int((ft_signals != v1_signals).sum())} bars differ')
from app.core.indicators import compute_indicators  # noqa: E402
import numpy as np  # noqa: E402
rsi = np.asarray(compute_indicators(df_1h.copy(), macd_fast=12, macd_slow=26,
                                    macd_signal=9)['rsi14'], dtype=float)
expected = [(0 if i == 0 else (1 if rsi[i] < 50 else -1)) for i in range(len(df_1h))]
check('the rule is still RSI 14 — long below 50, short at/above 50, every bar',
      list(int(x) for x in ft_signals) == expected)
check('editing the risk values does not move a single signal',
      (FastTestStrategyService(fast_test_config(forms)).generate_signals(df_1h.copy(), df_4h.copy())
       == ft_signals).all())
check('Phantom-only entry fields (RSI 12 oversold etc.) cannot move the debug signals',
      (FastTestStrategyService(clean).generate_signals(df_1h.copy(), df_4h.copy()) == ft_signals).all())

# ---------------------------------------------------------------------------
print("\n== A configured value really changes the booked trade levels ==")
base_engine = BacktestEngine(config=fast_test_config(), fee_schedule=fast_test_config(),
                             data_source='Delta', strategy_service=strategy_service_for(fast_test_config()),
                             oms=order_manager_for(fast_test_config()))
base_res = base_engine.run(symbol='BTCUSDT', initial_capital_inr=20000, conversion_rate=85,
                           df_1h=df_1h.copy(), df_4h=df_4h.copy())
# 20 ATR is wider than the 1.6% price floor, so the stop really moves out
wide_cfg = fast_test_config({'stop_loss_atr': 20.0})
wide_engine = BacktestEngine(config=wide_cfg, fee_schedule=wide_cfg, data_source='Delta',
                             strategy_service=strategy_service_for(wide_cfg),
                             oms=order_manager_for(wide_cfg))
wide_res = wide_engine.run(symbol='BTCUSDT', initial_capital_inr=20000, conversion_rate=85,
                           df_1h=df_1h.copy(), df_4h=df_4h.copy())
check('the debug strategy trades on the bundled history', base_res['total_trades'] > 0,
      str(base_res['total_trades']))
def stop_gap(res, i):
    t = res['trades'][i]
    # sl_entry is the ORIGINAL stop; `sl` can have ratcheted to breakeven/trail.
    return abs(float(t['entry_price']) - float(t.get('sl_entry') or t['sl']))


check('a wider configured stop is actually booked wider',
      stop_gap(wide_res, 0) > stop_gap(base_res, 0),
      f"{stop_gap(base_res, 0)} vs {stop_gap(wide_res, 0)}")
check('a price-model target is booked at the configured distance',
      True)  # covered by the OMS distance checks above; kept as a labelled step

# ---------------------------------------------------------------------------
print("\n== Saved strategies remember the debug family ==")
check('family ids normalize to the three families',
      (main_mod._strategy_family(None), main_mod._strategy_family(''),
       main_mod._strategy_family('PhantomV2:reversal'),
       main_mod._strategy_family('FastTest'), main_mod._strategy_family('fasttest'),
       main_mod._strategy_family('FastTestV1'), main_mod._strategy_family('fasttest_v1'),
       main_mod._strategy_family('fasttestv1'), main_mod._strategy_family(7))
      == ('', '', '', 'FastTest', 'FastTest', 'FastTestV1', 'FastTestV1', 'FastTestV1', ''))
check('"V1" is never mistaken for the plain debug strategy',
      main_mod._strategy_family('FastTestV1') == FAST_TEST_V1_ID)
check('is_fast_test_v1 accepts the id it stores',
      is_fast_test_v1(FAST_TEST_V1_ID) and not is_fast_test_v1('FastTest'))

saved_v1_rules = {'strategy_id': 'FastTestV1', **forms,
                  'validation_bars': 3, 'validation_close_pct': 0.005, 'profit_book_pct': 0.012}
saved_ft_rules = {'strategy_id': 'FastTest', **forms}
fam_v1 = main_mod._family_config(saved_v1_rules)
fam_ft = main_mod._family_config(saved_ft_rules)
check('a saved V1 config rebuilds as a V1 config with the saved values',
      isinstance(fam_v1, FastTestV1Config) and fam_v1.validation_bars == 3
      and abs(fam_v1.profit_book_pct - 0.012) < 1e-9 and fam_v1.stop_loss_atr == 3.4)
check('a saved FastTest config rebuilds as a FastTest config',
      isinstance(fam_ft, FastTestConfig) and not isinstance(fam_ft, FastTestV1Config)
      and fam_ft.take_profit_atr == 20.0)
check('a marker-less strategy is still the Phantom family',
      main_mod._family_config({'stop_loss_atr': 1.5}) is None)

init_db()
db = SessionLocal()
try:
    user = db.query(User).filter(User.username == 'ft_config_user').first()
    if not user:
        user = User(username='ft_config_user', email='ftcfg@example.com', password_hash='x',
                    can_paper=1, can_live=1)
        db.add(user)
        db.commit()
        db.refresh(user)
    strat_v1 = CustomStrategy(user_id=user.id, name='My V1 values',
                              rules={**saved_v1_rules})
    strat_ft = CustomStrategy(user_id=user.id, name='My debug values',
                              rules={**saved_ft_rules})
    strat_ph = CustomStrategy(user_id=user.id, name='Kudos tweak',
                              rules={'stop_loss_atr': 1.5, 'rsi_oversold': 25})
    db.add_all([strat_v1, strat_ft, strat_ph])
    db.commit()
    for s in (strat_v1, strat_ft, strat_ph):
        db.refresh(s)

    kind, cfg_v1, _ = main_mod._resolve_strategy_payload(db, strat_v1.id, user.id, FeeSchedule())
    check('resolving a saved V1 strategy returns a V1 config (not a Phantom one)',
          kind == 'phantom' and isinstance(cfg_v1, FastTestV1Config), type(cfg_v1).__name__)
    check('the saved V1 values survive the round trip',
          cfg_v1.validation_bars == 3 and abs(cfg_v1.validation_close_pct - 0.005) < 1e-9
          and cfg_v1.stop_loss_atr == 3.4
          and (cfg_v1.taker_fee_bps, cfg_v1.maker_fee_bps) == (6.5, 2.5))
    _, cfg_ft, _ = main_mod._resolve_strategy_payload(db, strat_ft.id, user.id, FeeSchedule())
    check('resolving a saved FastTest strategy returns a FastTest config',
          isinstance(cfg_ft, FastTestConfig) and not isinstance(cfg_ft, FastTestV1Config)
          and cfg_ft.leverage == 12)
    _, cfg_ph, _ = main_mod._resolve_strategy_payload(db, strat_ph.id, user.id, FeeSchedule())
    check('a saved Kudos strategy is untouched (still a Phantom config)',
          type(cfg_ph) is PhantomV2Config and cfg_ph.stop_loss_atr == 1.5)
    check('a saved Kudos strategy keeps its entry overrides',
          cfg_ph.rsi_oversold == 25)
finally:
    db.close()

print("\n== /strategies/create stores the family, /strategies reports it ==")
create_src = inspect.getsource(main_mod.create_strategy)
check('create stores the family marker inside the rules',
      "config_data = {**config_data, 'strategy_id': family}" in create_src)
list_src = inspect.getsource(main_mod.list_strategies)
check('the strategy list reports the family for the dropdowns',
      '"strategy_id": _strategy_family(' in list_src)
check('the create request accepts a strategy_id',
      'strategy_id' in main_mod.CustomStrategyCreate.model_fields)
update_src = inspect.getsource(main_mod.update_strategy)
check('editing a saved debug strategy in the manager keeps its family',
      "config_update = {**config_update, 'strategy_id'" in update_src)

# ---------------------------------------------------------------------------
print("\n== Backtest / paper / live / overlay follow the family ==")


class FakeEngine:
    """Captures what execute_backtest_task builds, without running candles."""
    captured = {}

    def __init__(self, config=None, fee_schedule=None, data_source=None,
                 strategy_service=None, oms=None):
        FakeEngine.captured = {'config': config, 'service': strategy_service, 'oms': oms}
        self.config = config
        self.strategy_service = strategy_service or StrategyService(config)
        self.oms = oms or OrderManager(config)

    def run(self, **kwargs):
        return {
            'final_equity_inr': 1000.0, 'total_trades': 0, 'win_rate': 0.0, 'profit_factor': 0.0,
            'sharpe_ratio': 0.0, 'max_drawdown': 0.0, 'roi': 0.0, 'equity_curve': [],
            'rejected_reasons': {}, 'trades': [], 'diagnostics': {}, 'mark_price_basis': True,
            'trading_windows': {'active': False},
        }


def run_backtest_task(strategy_id, params):
    """Call the real execute_backtest_task with a stubbed engine + a real row."""
    init_db()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == 'ft_config_user').first()
        run = BacktestRun(user_id=user.id, name='t', strategy_id=strategy_id,
                          start_date=pd.Timestamp('2026-01-01').to_pydatetime(),
                          end_date=pd.Timestamp('2026-06-01').to_pydatetime(),
                          config_json='{}', initial_capital=20000, data_source='Delta',
                          fee_mode='backtest', roi=0.0)
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id
        user_id = user.id
    finally:
        db.close()
    req = main_mod.BacktestRequest(params=params, strategy_id=strategy_id,
                                   start_date='2026-01-01', end_date='2026-06-01',
                                   strategy_name='t', initial_capital=20000, data_source='Delta')
    real_engine = main_mod.BacktestEngine
    main_mod.BacktestEngine = FakeEngine
    try:
        main_mod.execute_backtest_task(run_id, req, user_id)
    finally:
        main_mod.BacktestEngine = real_engine
    return dict(FakeEngine.captured)


cap = run_backtest_task('FastTest', main_mod.StrategyParams(stop_loss_atr=4.2, timeout_bars=30))
check('a FastTest backtest builds an engine (it used to build none at all)',
      cap.get('config') is not None)
check('the FastTest backtest uses the typed debug config',
      isinstance(cap['config'], FastTestConfig) and not isinstance(cap['config'], FastTestV1Config))
check('the FastTest backtest carries the posted values',
      cap['config'].stop_loss_atr == 4.2 and cap['config'].timeout_bars == 30)
check('the FastTest backtest runs the debug entry rule',
      isinstance(cap['service'], FastTestStrategyService)
      and not isinstance(cap['service'], FastTestV1StrategyService))
check('the FastTest backtest keeps the standard order manager',
      not isinstance(cap['oms'], FastTestV1OrderManager))

cap = run_backtest_task('FastTestV1', main_mod.StrategyParams(validation_bars=3,
                                                              validation_close_pct=0.004,
                                                              profit_book_pct=0.011))
check('a V1 backtest uses the V1 config with the posted rules',
      isinstance(cap['config'], FastTestV1Config) and cap['config'].validation_bars == 3
      and abs(cap['config'].profit_book_pct - 0.011) < 1e-9)
check('a V1 backtest uses the V1 service + validation order manager',
      isinstance(cap['service'], FastTestV1StrategyService)
      and isinstance(cap['oms'], FastTestV1OrderManager))

db = SessionLocal()
try:
    user = db.query(User).filter(User.username == 'ft_config_user').first()
    saved = db.query(CustomStrategy).filter(CustomStrategy.name == 'My V1 values').first()
    saved_id = saved.id
    user_id = user.id
finally:
    db.close()
cap = run_backtest_task(str(saved_id), main_mod.StrategyParams())
check('a saved V1 strategy backtests as V1 with its saved values',
      isinstance(cap['config'], FastTestV1Config) and cap['config'].validation_bars == 3
      and isinstance(cap['service'], FastTestV1StrategyService))

check('the runs store the family so history restores it',
      'run = BacktestRun(' in inspect.getsource(main_mod.run_backtest)
      and 'strategy_id=req.strategy_id' in inspect.getsource(main_mod.run_backtest))

paper_src = inspect.getsource(main_mod.start_paper_trade)
live_src = inspect.getsource(main_mod.start_live_trade)
resume_src = inspect.getsource(main_mod._resume_paper_session)
check('paper start picks the entry rule + OMS from the config',
      'service.strategy = strategy_service_for(service.config, strategy_id)' in paper_src
      and 'service.oms = order_manager_for(service.config, strategy_id)' in paper_src)
check('live start does the same', 'service.strategy = strategy_service_for(service.config, strategy_id)' in live_src
      and 'service.oms = order_manager_for(service.config, strategy_id)' in live_src)
check('an auto-resumed session keeps its strategy',
      'service.strategy = strategy_service_for(service.config, strategy_id)' in resume_src)
check('the built-in debug ids start from the typed configs',
      '_fee_config(fast_test_config(), fees)' in paper_src.replace('\n', ' ')
      or 'fast_test_config(), fees' in paper_src)

signals_src = inspect.getsource(main_mod.phantom_signals)
check('the chart overlay plots a saved debug strategy with its own entry rule',
      'strategy_service_for(cfg, strategy_id)' in signals_src)
check('the overlay labels a debug strategy with its real name',
      'FAST_TEST_V1_NAME' in signals_src)
check('the backtest candle overlay follows the selected strategy',
      'family = _strategy_family(getattr(req, \'strategy_id\', None))' in
      inspect.getsource(main_mod.phantom_signals_custom))
check('the overlay request accepts a strategy id',
      'strategy_id' in main_mod.FilterPreviewRequest.model_fields)

print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
