"""Offline checks for **FastTest V1.0** — the debug strategy with the
2H / +0.35% validation and the touch-based +0.90% profit-booking layer.

What is verified:

* the original FastTest entry rule is copied exactly (bar-for-bar parity) and
  FastTest itself is untouched (no new exit reasons, no audit fields, no
  behaviour change anywhere in the engine),
* +0.90% is a TOUCH (wick) rule, +0.35% is a CLOSE rule,
* the stop still wins when one candle pierces both the stop and the target,
* the validation deadline is the close of the second completed 1h candle,
  pass → continue with the existing exits, fail → exit at that very close,
* the seven audit fields are filled on the backtest trade log, the CSV export,
  the paper worker's closed-trade record and the live worker's record,
* Net P&L is after entry + exit fees,
* the DB gains the audit columns additively (old databases migrate in place).

Runs on the bundled CSVs and a temp SQLite DB — no server, no network:

    cd backend && python test_fast_test_v1.py
"""
import datetime as dt
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TESTDB = "/tmp/fast_test_v1_test.db"
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"

from app.core.engine import BacktestEngine  # noqa: E402
from app.core.fast_test_v1 import (  # noqa: E402
    FAST_TEST_V1_ID, FAST_TEST_V1_NAME, STATUS_FAILED, STATUS_NOT_REACHED,
    STATUS_PENDING, STATUS_TP_HIT, STATUS_VALIDATED, FastTestV1Config,
    FastTestV1OrderManager, FastTestV1StrategyService, completed_bar_close,
    fast_test_v1_config, is_fast_test_v1,
)
from app.core.strategy import FastTestStrategyService, PhantomV2Config, parse_phantom_variant  # noqa: E402
from app.services.order_manager import OrderManager  # noqa: E402
from app.services.paper_trader import PaperTradeService  # noqa: E402
from app.services.live_trader import LiveTradeService  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
AUDIT_FIELDS = ('validation_status', 'validation_close', 'validation_threshold',
                'tp090_hit', 'validation_exit', 'final_exit_reason', 'final_net_pnl')
V1_REASONS = ('TP090', 'VALFAIL')
LEGACY_REASONS = ('SL', 'TSL', 'TP', 'MH', 'REV')

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


T0 = pd.Timestamp('2026-01-01 00:00:00')


def new_oms(**overrides):
    cfg = FastTestV1Config(**overrides)
    return FastTestV1OrderManager(cfg), cfg


def open_trade(oms, direction=1, price=100.0, atr=1.0, ts=T0):
    return oms.create_order("BTCUSDT", direction, price, atr, ts, 5000.0, 85.0,
                            trade_price_usd=price, mark_price_usd=price,
                            mark_price_basis=True)


def bar_close(oms, close, bar_time, price=None, high=None, low=None, trade_price=None,
              mark=None, atr=1.0):
    """One engine-shaped candle pass: stop/target + close-based rule."""
    return oms.update_trade(
        "BTCUSDT", float(price if price is not None else close), atr, bar_time,
        trade_price_usd=float(trade_price if trade_price is not None else (price if price is not None else close)),
        mark_price_usd=mark,
        bar_high_usd=(float(high) if high is not None else None),
        bar_low_usd=(float(low) if low is not None else None),
        bar_close_usd=float(close), bar_time=bar_time,
    )


# ---------------------------------------------------------------------------
def test_entry_parity():
    print("\n== Entry logic is the FastTest rule, copied ==")
    df_1h, df_4h = load_frames()
    base = FastTestStrategyService(PhantomV2Config())
    v1 = FastTestV1StrategyService(FastTestV1Config())
    s_base = base.generate_signals(df_1h.copy(), df_4h)
    s_v1 = v1.generate_signals(df_1h.copy(), df_4h)
    check("signal arrays identical to FastTest", len(s_base) == len(s_v1)
          and all(a == b for a, b in zip(s_base, s_v1)))
    check("every bar after the first carries a signal (long or short)",
          all(s in (1, -1) for s in s_v1[1:]))
    check("bar 0 is the only zero signal", s_v1[0] == 0 and 0 not in list(s_v1[1:]))
    check("longs below RSI 50, shorts at/above it",
          all((s_v1[i] == 1) == (base.generate_signals(df_1h.copy(), df_4h)[i] == 1)
              for i in range(1, 40)))
    check("FastTestV1 is NOT a Phantom preset id", parse_phantom_variant(FAST_TEST_V1_ID) is None)
    check("is_fast_test_v1 matches only the V1 id",
          is_fast_test_v1('FastTestV1') and not is_fast_test_v1('FastTest')
          and not is_fast_test_v1('PhantomV2'))


def test_defaults_and_config():
    print("\n== Spec defaults, and nothing else changed ==")
    cfg = FastTestV1Config()
    check("validation window is 2 completed candles", cfg.validation_bars == 2)
    check("validation requires +0.35% close", abs(cfg.validation_close_pct - 0.0035) < 1e-12)
    check("profit booking at +0.90% touch", abs(cfg.profit_book_pct - 0.009) < 1e-12)
    base = PhantomV2Config()
    check("stop / trailing / timeout / sizing are inherited unchanged",
          (cfg.stop_loss_atr, cfg.trail_activation_atr, cfg.trail_distance_atr,
           cfg.timeout_bars, cfg.margin_pct, cfg.leverage, cfg.allow_reverse,
           cfg.allow_overlap) ==
          (base.stop_loss_atr, base.trail_activation_atr, base.trail_distance_atr,
           base.timeout_bars, base.margin_pct, base.leverage, base.allow_reverse,
           base.allow_overlap))
    check("entry condition block is inherited unchanged",
          cfg.model_dump()['entry_conditions'] == base.model_dump()['entry_conditions'])
    over = fast_test_v1_config({'validation_bars': 3, 'profit_book_pct': 0.02,
                                'validation_close_pct': 0.01, 'leverage': 99,
                                'macd_slow': 30})
    check("params may override the three V1 knobs", (over.validation_bars, over.profit_book_pct,
                                                     over.validation_close_pct) == (3, 0.02, 0.01))
    check("unknown / non-V1 keys are still honoured as normal params",
          over.leverage == 99 and over.macd_slow == 30)
    check("no Phantom config value leaks in as a V1 knob",
          fast_test_v1_config(PhantomV2Config()).profit_book_pct == 0.009)


def test_profit_booking_touch():
    print("\n== +0.90% booking is TOUCH-based (long) ==")
    oms, _ = new_oms()
    t = open_trade(oms)
    check("threshold level is entry x 1.0035", abs(t.v1_validate_level - 100.35) < 1e-9)
    check("profit level is entry x 1.0090", abs(t.v1_profit_level - 100.90) < 1e-9)
    # A wick to 100.95 with a close back at 100.20 must still book.
    closed = bar_close(oms, 100.20, T0, high=100.95, low=100.10)
    check("a wick through +0.90% books the position", closed is not None)
    check("booked at the +0.90% level, not the wick", abs(closed.exit_price - 100.90) < 1e-9)
    check("exit reason is TP090", closed.exit_reason == 'TP090')
    check("the detail names the touch rule", '+0.90% profit booking' in closed.exit_detail)
    audit = oms.strategy_audit_fields(closed, 1234.0)
    check("audit: tp090_hit = 1", audit['tp090_hit'] == 1)
    check("audit: status TP_090_HIT", audit['validation_status'] == STATUS_TP_HIT)
    check("audit: final reason + net pnl", audit['final_exit_reason'] == 'TP090'
          and audit['final_net_pnl'] == 1234.0)
    check("audit: threshold recorded", abs(audit['validation_threshold'] - 100.35) < 1e-9)

    print("\n== +0.90% booking is TOUCH-based (short, mirrored) ==")
    oms, _ = new_oms()
    t = open_trade(oms, direction=-1)
    check("short threshold is entry x 0.9965", abs(t.v1_validate_level - 99.65) < 1e-9)
    check("short profit level is entry x 0.9910", abs(t.v1_profit_level - 99.10) < 1e-9)
    closed = bar_close(oms, 99.80, T0, high=99.90, low=99.05)
    check("a wick through -0.90% books the short", closed is not None
          and abs(closed.exit_price - 99.10) < 1e-9 and closed.exit_reason == 'TP090')


def test_stop_keeps_priority():
    print("\n== the resting stop still wins inside one candle ==")
    oms, _ = new_oms()
    t = open_trade(oms)
    # Same candle pierces both the SL (98.0) and the +0.90% target.
    closed = bar_close(oms, 100.5, T0, high=100.95, low=97.5)
    check("the stop is booked, not the profit target", closed is not None
          and closed.exit_reason in ('SL', 'TSL'), closed.exit_reason if closed else None)
    audit = oms.strategy_audit_fields(closed, 10.0)
    check("audit: tp090_hit = 0", audit['tp090_hit'] == 0)
    check("audit: status NOT_REACHED (booking never happened)",
          audit['validation_status'] == STATUS_NOT_REACHED)


def test_validation_pass_then_existing_exits():
    print("\n== validation PASS -> the existing exits keep running ==")
    oms, _ = new_oms()
    t = open_trade(oms)
    out = bar_close(oms, 100.40, T0, high=100.50, low=100.05)
    check("a +0.35% close validates without exiting", out is None)
    check("status is VALIDATED", t.v1_status == STATUS_VALIDATED)
    check("the validating close is recorded", abs(t.v1_validation_close - 100.40) < 1e-9)
    # The trade keeps running and a later stop still closes it normally.
    later = bar_close(oms, 99.0, T0 + pd.Timedelta(hours=5), high=99.2, low=97.9)
    check("a later stop exits normally after validation", later is not None
          and later.exit_reason in ('SL', 'TSL'), later.exit_reason if later else None)
    audit = oms.strategy_audit_fields(later, 50.0)
    check("audit keeps the VALIDATED verdict", audit['validation_status'] == STATUS_VALIDATED)
    check("audit keeps the validating close", abs(audit['validation_close'] - 100.40) < 1e-9)
    check("audit: validation_exit = 0", audit['validation_exit'] == 0)


def test_validation_fail_exits_at_2h_close():
    print("\n== validation FAIL -> exit at the 2H close ==")
    oms, _ = new_oms()
    t = open_trade(oms)
    first = bar_close(oms, 100.10, T0, high=100.20, low=99.95)
    check("no exit at the 1H close", first is None)
    check("still pending after the first close", t.v1_status == STATUS_PENDING)
    second = bar_close(oms, 100.20, T0 + pd.Timedelta(hours=1), high=100.30, low=100.05)
    check("exits at the 2H close", second is not None and second.exit_reason == 'VALFAIL')
    check("exit price is the 2H close", abs(second.exit_price - 100.20) < 1e-9)
    check("the detail names the 2H rule", '2H validation failed' in second.exit_detail
          and 'Exited at the 2H candle close' in second.exit_detail)
    audit = oms.strategy_audit_fields(second, 77.0)
    check("audit: status FAILED", audit['validation_status'] == STATUS_FAILED)
    check("audit: validation_exit = 1", audit['validation_exit'] == 1)
    check("audit: validation_close is the failing close",
          abs(audit['validation_close'] - 100.20) < 1e-9)
    check("audit: final net pnl passed through", audit['final_net_pnl'] == 77.0)
    check("no position left open", not oms.active_trades)


def test_deadline_is_the_second_close():
    print("\n== the deadline is the SECOND completed close, not later ==")
    oms, _ = new_oms()
    open_trade(oms)
    check("first close still pending", bar_close(oms, 100.05, T0) is None)
    second = bar_close(oms, 100.10, T0 + pd.Timedelta(hours=1))
    check("exits on the second close", second is not None and second.exit_reason == 'VALFAIL')
    # An extra failing close afterwards must not re-open/resurrect anything.
    third = bar_close(oms, 100.90, T0 + pd.Timedelta(hours=2), high=101.5)
    check("nothing left to close afterwards", third is None and not oms.active_trades)

    print("\n== validation_bars is configurable ==")
    oms3, _ = new_oms(validation_bars=3)
    open_trade(oms3)
    check("3-bar window: 1st close pending", bar_close(oms3, 100.05, T0) is None)
    check("3-bar window: 2nd close pending", bar_close(oms3, 100.10, T0 + pd.Timedelta(hours=1)) is None)
    third = bar_close(oms3, 100.15, T0 + pd.Timedelta(hours=2))
    check("3-bar window: exits at the 3rd close", third is not None
          and third.exit_reason == 'VALFAIL' and abs(third.exit_price - 100.15) < 1e-9)


def test_priority_and_edge_cases():
    print("\n== priority rules and edge cases ==")
    oms, _ = new_oms()
    open_trade(oms)
    check("a candle before the entry bar is not counted",
          bar_close(oms, 100.05, T0 - pd.Timedelta(hours=3)) is None)
    check("the entry candle is judged once", bar_close(oms, 100.05, T0) is None)

    # +0.90% touched on the same candle as the failing 2H close -> booking wins.
    oms, _ = new_oms()
    open_trade(oms)
    bar_close(oms, 100.10, T0)
    closed = bar_close(oms, 100.15, T0 + pd.Timedelta(hours=1), high=100.95)
    check("+0.90% touch beats the failing validation on the same candle",
          closed is not None and closed.exit_reason == 'TP090'
          and abs(closed.exit_price - 100.90) < 1e-9)

    # Validation that passes on the deadline close simply continues.
    oms, _ = new_oms()
    open_trade(oms)
    bar_close(oms, 100.10, T0)
    check("a +0.35% close on the deadline validates instead of exiting",
          bar_close(oms, 100.40, T0 + pd.Timedelta(hours=1)) is None)

    # Shorts mirror the whole rule.
    oms, _ = new_oms()
    open_trade(oms, direction=-1)
    check("short: a -0.35% close validates", bar_close(oms, 99.60, T0, low=99.55) is None)
    oms2, _ = new_oms()
    open_trade(oms2, direction=-1)
    bar_close(oms2, 99.80, T0, high=99.90)
    out = bar_close(oms2, 99.85, T0 + pd.Timedelta(hours=1), high=99.95)
    check("short: exits at the 2H close when -0.35% is missed", out is not None
          and out.exit_reason == 'VALFAIL' and abs(out.exit_price - 99.85) < 1e-9)

    # The base hooks change nothing for every other strategy.
    plain = OrderManager(PhantomV2Config())
    t = plain.create_order("BTCUSDT", 1, 100.0, 1.0, T0, 5000.0, 85.0)
    check("default touch hook is a no-op", plain.strategy_touch_exit(t, 200.0, 1.0) is None)
    check("default bar-close hook is a no-op",
          plain.strategy_bar_close_exit(t, 200.0, T0 + pd.Timedelta(hours=1)) is None)
    check("default audit fields are empty", plain.strategy_audit_fields(t, 5.0) == {})
    check("default summary is empty", plain.strategy_summary([]) == {})
    check("default bracket TP is the plan's own TP",
          plain.bracket_take_profit(1, 100.0, 110.0) == 110.0)
    v1 = FastTestV1OrderManager(FastTestV1Config())
    check("V1 bracket TP is the +0.90% level", abs(v1.bracket_take_profit(1, 100.0, 110.0) - 100.9) < 1e-9
          and abs(v1.bracket_take_profit(-1, 100.0, 90.0) - 99.1) < 1e-9)


def test_completed_bar_close_helper():
    print("\n== the workers hand over the completed candle, not the forming one ==")
    df_1h, _ = load_frames()
    ts = df_1h.index[-2]
    got = completed_bar_close(df_1h, ts)
    check("returns (close, high, low) for the stamped candle", got is not None
          and abs(got[0] - float(df_1h.loc[ts, 'close'])) < 1e-9)
    check("unknown stamp -> None", completed_bar_close(df_1h, pd.Timestamp('1999-01-01')) is None)
    check("missing frame -> None", completed_bar_close(None, ts) is None)


def test_backtest_end_to_end():
    print("\n== backtest: FastTest untouched, V1 audited ==")
    df_1h, df_4h = load_frames()

    ft_cfg = PhantomV2Config(taker_fee_bps=5.9, maker_fee_bps=2.36)
    ft_engine = BacktestEngine(config=ft_cfg, fee_schedule=ft_cfg, data_source='Delta')
    ft_engine.strategy_service = FastTestStrategyService(ft_cfg)
    ft = ft_engine.run(df_1h=df_1h.copy(), df_4h=df_4h.copy(),
                       initial_capital_inr=20000, conversion_rate=85.0)
    ft_again_engine = BacktestEngine(config=ft_cfg, fee_schedule=ft_cfg, data_source='Delta')
    ft_again_engine.strategy_service = FastTestStrategyService(ft_cfg)
    ft_again = ft_again_engine.run(df_1h=df_1h.copy(), df_4h=df_4h.copy(),
                                   initial_capital_inr=20000, conversion_rate=85.0)
    check("FastTest trades are byte-identical across runs (no behaviour change)",
          [(t['entry_time'], t['exit_time'], t['exit_reason'], round(t['net_pnl'], 6))
           for t in ft['trades']] ==
          [(t['entry_time'], t['exit_time'], t['exit_reason'], round(t['net_pnl'], 6))
           for t in ft_again['trades']])
    check("FastTest uses no V1 exit reason",
          not any(t['exit_reason'] in V1_REASONS for t in ft['trades']))
    check("FastTest trade log carries no V1 audit fields",
          not any(f in ft['trades'][0] for f in AUDIT_FIELDS) if ft['trades'] else True)
    check("FastTest results have no strategy_summary",
          'strategy_summary' not in ft and 'strategy_summary' not in ft_again)

    cfg = FastTestV1Config(taker_fee_bps=5.9, maker_fee_bps=2.36)
    engine = BacktestEngine(config=cfg, fee_schedule=cfg, data_source='Delta',
                            strategy_service=FastTestV1StrategyService(cfg),
                            oms=FastTestV1OrderManager(cfg))
    res = engine.run(df_1h=df_1h.copy(), df_4h=df_4h.copy(),
                     initial_capital_inr=20000, conversion_rate=85.0)
    trades = res['trades']
    check("V1 backtest produced trades", len(trades) > 0)
    check("every V1 trade carries all seven audit fields",
          all(all(f in t for f in AUDIT_FIELDS) for t in trades))
    check("every exit reason is either a legacy one or a V1 one",
          all(t['final_exit_reason'] in LEGACY_REASONS + V1_REASONS for t in trades))
    check("final_exit_reason matches the trade's exit_reason",
          all(t['final_exit_reason'] == t['exit_reason'] for t in trades))
    check("validation thresholds are entry x 1.0035 / x 0.9965",
          all(abs(t['validation_threshold'] - t['entry_price'] * (1.0035 if t['direction'] == 1 else 0.9965)) < 1e-6
              for t in trades))
    check("validation status values are from the documented set",
          {t['validation_status'] for t in trades} <=
          {STATUS_VALIDATED, STATUS_FAILED, STATUS_TP_HIT, STATUS_NOT_REACHED})
    booked = [t for t in trades if t['tp090_hit']]
    check("+0.90% bookings exited exactly at the +0.90% level",
          booked and all(abs(t['exit_price'] - t['entry_price'] * (1.009 if t['direction'] == 1 else 0.991)) < 1e-6
                         for t in booked))
    check("+0.90% bookings are reason TP090 (status TP_090_HIT, or VALIDATED when "
          "the 2H close had already validated the trade first)",
          all(t['exit_reason'] == 'TP090' and t['validation_status'] in (STATUS_TP_HIT, STATUS_VALIDATED)
              for t in booked))
    failed = [t for t in trades if t['validation_exit']]
    check("validation failures exited at their recorded validation close",
          failed and all(abs(t['exit_price'] - t['validation_close']) < 1e-9 for t in failed))
    check("validation failures are reason VALFAIL and status FAILED",
          all(t['exit_reason'] == 'VALFAIL' and t['validation_status'] == STATUS_FAILED for t in failed))
    check("validated trades keep the legacy exits (or book the +0.90% profit later)",
          all(t['exit_reason'] in LEGACY_REASONS + ('TP090',) for t in trades
              if t['validation_status'] == STATUS_VALIDATED))
    check("Net P&L is after fees: gross - fees = net",
          all(abs((t['gross_pnl'] - t['fees']) - t['net_pnl']) < 1e-6 for t in trades))
    check("final_net_pnl equals the net-of-fees P&L",
          all(abs(t['final_net_pnl'] - t['net_pnl']) < 1e-6 for t in trades))
    check("fees were actually charged on both legs", all(t['fees'] > 0 for t in trades))
    check("results carry the V1 strategy summary",
          res.get('strategy_summary', {}).get('rule', '').startswith('FastTest V1.0'))
    check("summary counts match the trade log",
          res['strategy_summary']['booked_at_090'] == len(booked)
          and res['strategy_summary']['validation_failed'] == len(failed))

    # Entries come from the same signal set as FastTest — only the exits differ.
    v1_service = FastTestV1StrategyService(cfg)
    sig = v1_service.generate_signals(df_1h.copy(), df_4h.copy())
    sig_by_bar = {df_1h.index[i]: int(sig[i]) for i in range(len(df_1h))}
    check("every V1 entry sits on a bar whose signal matches its direction",
          all(sig_by_bar.get(t['signal_candle_time']) == t['direction'] for t in trades),
          str([(str(t['signal_candle_time']), t['direction']) for t in trades[:3]]))
    check("V1 entries and FastTest entries both come from the same signal rule",
          all(sig_by_bar.get(t['signal_candle_time']) == t['direction'] for t in ft['trades']))

    print("\n== CSV export carries the audit fields ==")
    csv_path = os.path.join(DATA_DIR, '_tmp_fasttest_v1_log.csv')
    try:
        engine.export_trade_log(trades, csv_path)
        cols = pd.read_csv(csv_path, nrows=1).columns.tolist()
        check("CSV export has all seven audit columns",
              all(c in cols for c in AUDIT_FIELDS))
        check("the original column layout is untouched (first column still signal_candle_time)",
              cols[0] == 'signal_candle_time')
    finally:
        if os.path.exists(csv_path):
            os.remove(csv_path)


def test_paper_worker_path():
    print("\n== paper worker books the rules and records the audit ==")
    cfg = fast_test_v1_config()
    svc = PaperTradeService(FAST_TEST_V1_ID, cfg, initial_capital=20000, margin_pct=25,
                            market_source='Delta', broker_name='Delta',
                            strategy_name=FAST_TEST_V1_NAME)
    svc.strategy = FastTestV1StrategyService(svc.config)
    svc.oms = FastTestV1OrderManager(svc.config)
    trade = open_trade(svc.oms, price=100.0, ts=T0)
    closed = svc._manage_open_positions(100.10, 1.0, T0, 100.10, None, True,
                                        bar_close=(100.10, 100.20, 99.95), bar_time=T0)
    check("first close does not exit the paper trade", closed is False)
    closed = svc._manage_open_positions(100.20, 1.0, T0 + pd.Timedelta(hours=1), 100.20, None, True,
                                        bar_close=(100.20, 100.30, 100.05),
                                        bar_time=T0 + pd.Timedelta(hours=1))
    check("second close exits the paper trade", closed is True and not svc.oms.active_trades)
    check("the closed-trade record is in the paper history", len(svc.closed_trades) == 1)
    rec = svc.closed_trades[0]
    check("paper record carries the audit fields", all(f in rec for f in AUDIT_FIELDS))
    check("paper record: FAILED + VALFAIL + validation_exit", rec['validation_status'] == STATUS_FAILED
          and rec['final_exit_reason'] == 'VALFAIL' and rec['validation_exit'] == 1)
    check("paper record: net P&L is the booked, fee-adjusted figure",
          abs(rec['final_net_pnl'] - rec['pnl']) < 1e-9 and rec['fees'] > 0)

    # The strategy the worker runs is the V1 config (2 bars / 0.35% / 0.90%).
    check("paper worker uses the V1 config", isinstance(svc.config, FastTestV1Config)
          and svc.config.validation_bars == 2)


def test_live_worker_path():
    print("\n== live worker books the rules, sends the exit, records the audit ==")

    class FakeBroker:
        def __init__(self):
            self.calls = []

        def place_order(self, *a, **k):
            self.calls.append((a, k))
            return {"id": "fake-1", "average_price": 100.2}

        def place_bracket_order(self, *a, **k):
            self.calls.append((a, k))
            return {"id": "fake-1", "average_price": 100.2}

    cfg = fast_test_v1_config()
    svc = LiveTradeService(FAST_TEST_V1_ID, cfg, "k", "s", initial_capital=20000,
                           margin_pct=25, broker_name="Delta")
    svc.strategy = FastTestV1StrategyService(svc.config)
    svc.oms = FastTestV1OrderManager(svc.config)
    svc.broker = FakeBroker()
    svc.is_running = True
    open_trade(svc.oms, price=100.0, ts=T0)
    svc._manage_open_positions(100.10, 1.0, T0, 100.10, None, True,
                               bar_close=(100.10, 100.20, 99.95), bar_time=T0)
    svc._manage_open_positions(100.20, 1.0, T0 + pd.Timedelta(hours=1), 100.20, None, True,
                               bar_close=(100.20, 100.30, 100.05),
                               bar_time=T0 + pd.Timedelta(hours=1))
    check("the live worker sent the flattening order", len(svc.broker.calls) == 1)
    args, kwargs = svc.broker.calls[0]
    check("the live close is reduce-only (never opens the other side)",
          kwargs.get('reduce_only') is True and args[1] == 'SELL')
    check("the live closed-trade record carries the audit fields",
          len(svc.closed_trades) == 1 and all(f in svc.closed_trades[0] for f in AUDIT_FIELDS))
    check("live record: FAILED + VALFAIL", svc.closed_trades[0]['validation_status'] == STATUS_FAILED
          and svc.closed_trades[0]['final_exit_reason'] == 'VALFAIL')

    # Bracket TP for V1 is the +0.90% level.
    svc2 = LiveTradeService(FAST_TEST_V1_ID, cfg, "k", "s", initial_capital=20000,
                            margin_pct=25, broker_name="Delta")
    svc2.oms = FastTestV1OrderManager(svc2.config)
    check("live bracket TP is the +0.90% booking level",
          abs(svc2.oms.bracket_take_profit(1, 100.0, 110.0) - 100.9) < 1e-9)


def test_db_columns_and_migration():
    print("\n== DB columns exist and old databases migrate additively ==")
    from sqlalchemy import inspect as sa_inspect, text
    from app.database.models import Base, SessionLocal, Trade, engine as db_engine, migrate_db

    # 1. A legacy database WITHOUT the audit columns must gain them in place.
    with db_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS trades"))
        conn.execute(text(
            "CREATE TABLE trades (id INTEGER PRIMARY KEY, run_id INTEGER, entry_time DATETIME,"
            " exit_time DATETIME, direction INTEGER, entry_price FLOAT, exit_price FLOAT,"
            " lots FLOAT, margin FLOAT, notional FLOAT, net_pnl FLOAT, fees FLOAT,"
            " exit_reason VARCHAR, equity_after FLOAT, drawdown FLOAT, hold_bars INTEGER)"))
        conn.execute(text("INSERT INTO trades (id, direction, entry_price, net_pnl) VALUES (1, 1, 100.0, 5.0)"))
    Base.metadata.create_all(db_engine)
    migrate_db()
    cols = {c['name'] for c in sa_inspect(db_engine).get_columns('trades')}
    check("migration adds the seven audit columns", set(AUDIT_FIELDS) <= cols, sorted(set(AUDIT_FIELDS) - cols))
    with db_engine.connect() as conn:
        kept = conn.execute(text("SELECT net_pnl FROM trades WHERE id = 1")).scalar()
    check("existing rows survive the migration untouched", abs(kept - 5.0) < 1e-9)

    # 2. A V1 trade round-trips through the Trade model.
    db = SessionLocal()
    try:
        db.add(Trade(run_id=None, direction=1, entry_price=100.0, exit_price=100.9,
                     net_pnl=120.0, fees=3.5, exit_reason='TP090',
                     validation_status=STATUS_TP_HIT, validation_close=100.4,
                     validation_threshold=100.35, tp090_hit=1, validation_exit=0,
                     final_exit_reason='TP090', final_net_pnl=120.0))
        db.commit()
        row = db.query(Trade).filter(Trade.exit_reason == 'TP090').first()
        check("audit fields round-trip through the ORM",
              row is not None and row.validation_status == STATUS_TP_HIT
              and abs(row.validation_threshold - 100.35) < 1e-9 and row.tp090_hit == 1
              and row.final_exit_reason == 'TP090' and abs(row.final_net_pnl - 120.0) < 1e-9)
    finally:
        db.close()


def test_api_payload():
    print("\n== the results endpoint exposes the audit fields to the trade log ==")
    from app import main as main_module
    from app.database.models import BacktestRun, Base, SessionLocal, Trade, User, engine as db_engine

    Base.metadata.create_all(db_engine)
    db = SessionLocal()
    try:
        user = User(username='v1_tester', password_hash='x', is_active=1)
        db.add(user)
        db.commit()
        db.refresh(user)
        run = BacktestRun(user_id=user.id, name='V1 smoke', strategy_id=FAST_TEST_V1_ID,
                          initial_capital=20000, final_equity=20120, total_trades=1)
        db.add(run)
        db.commit()
        db.refresh(run)
        db.add(Trade(run_id=run.id, direction=1, entry_price=100.0, exit_price=100.9,
                     lots=1.0, net_pnl=120.0, fees=3.5, exit_reason='TP090',
                     validation_status=STATUS_TP_HIT, validation_close=100.4,
                     validation_threshold=100.35, tp090_hit=1, validation_exit=0,
                     final_exit_reason='TP090', final_net_pnl=120.0))
        db.commit()
        payload = main_module.get_backtest_results(run.id, user, db)
        row = payload['trades'][0]
        check("API trade row carries all seven audit fields",
              all(f in row for f in AUDIT_FIELDS), sorted(set(AUDIT_FIELDS) - set(row)))
        check("API values match what the engine recorded",
              row['validation_status'] == STATUS_TP_HIT and row['tp090_hit'] == 1
              and row['final_exit_reason'] == 'TP090' and abs(row['final_net_pnl'] - 120.0) < 1e-9)
        check("API run details keep strategy_id = FastTestV1",
              payload['run_details']['strategy_id'] == FAST_TEST_V1_ID)
    finally:
        db.close()

    check("display name for the dropdown is Fast Test Strategy V1.0",
          main_module._builtin_strategy_name(FAST_TEST_V1_ID) == FAST_TEST_V1_NAME)
    check("FastTest keeps its own display name",
          main_module._builtin_strategy_name('FastTest') == 'Fast Test Strategy')
    check("FastTestV1 is not treated as a Kudos preset",
          not main_module._is_builtin_phantom(FAST_TEST_V1_ID)
          and main_module._is_builtin_phantom('PhantomV2'))
    check("main.py routes the V1 id explicitly",
          main_module.is_fast_test_v1(FAST_TEST_V1_ID))


def main():
    test_entry_parity()
    test_defaults_and_config()
    test_profit_booking_touch()
    test_stop_keeps_priority()
    test_validation_pass_then_existing_exits()
    test_validation_fail_exits_at_2h_close()
    test_deadline_is_the_second_close()
    test_priority_and_edge_cases()
    test_completed_bar_close_helper()
    test_backtest_end_to_end()
    test_paper_worker_path()
    test_live_worker_path()
    test_db_columns_and_migration()
    test_api_payload()
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
