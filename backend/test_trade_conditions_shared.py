"""Offline checks for the **shared trade-condition detail**.

The Backtest page has always shown every entry condition (measured value,
threshold on that side, PASS/FAIL), the candle colours and the exact exit rule.
The client asked for the same detail — and the same export — on Paper and Live
trades, so the builders now live in ``app/core/trade_conditions.py`` and are
used by all three paths.

What is verified here:

* ``BacktestEngine``'s methods are thin delegations to the shared builders, so
  the backtest trade log reads exactly as it did before,
* the snapshot / readable breakdown / MACD-line lines are produced for both
  setups and both directions, and a metadata-less strategy yields ``{}``,
* a paper worker records the entry-condition record on the trade and merges it
  (plus the exit candle colour) into its closed-trade history,
* the live worker's closed-trade record carries the same keys,
* legacy records and old sessions are untouched (no key is required to exist).

Runs offline on the bundled CSVs and a temp SQLite DB:

    cd backend && python test_trade_conditions_shared.py
"""
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TESTDB = os.path.join(tempfile.gettempdir(), "trade_conditions_shared_test.db")
if os.path.exists(TESTDB):
    os.unlink(TESTDB)
os.environ["DATABASE_URL"] = f"sqlite:///{TESTDB}"

from app.core import trade_conditions as tc  # noqa: E402
from app.core.engine import BacktestEngine  # noqa: E402
from app.core.fast_test_v1 import FastTestV1StrategyService, fast_test_v1_config  # noqa: E402
from app.core.strategy import PhantomV2Config, StrategyService  # noqa: E402
from app.services.order_manager import OrderManager  # noqa: E402
from app.services.paper_trader import PaperTradeService  # noqa: E402
from app.services.live_trader import LiveTradeService  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

PASSED = 0
FAILED = 0


def check(name, cond, detail=''):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name} {detail}")


def read(fname):
    df = pd.read_csv(os.path.join(DATA_DIR, fname))
    df['event_time'] = pd.to_datetime(df['event_time'])
    return df.set_index('event_time').sort_index()[['open', 'high', 'low', 'close', 'volume']].astype(float)


DF_1H = read('btc_1h.csv')
DF_4H = read('btc_4h.csv')


def first_signal_bar(meta, signals, direction):
    """The first bar whose signal matches ``direction`` (so the snapshot is real)."""
    for i in range(1, len(signals)):
        if int(signals[i]) == direction:
            return i
    return len(signals) - 1


# --------------------------------------------------------------------------
print("\n== the engine's builders ARE the shared builders (no text drift) ==")
CFG = PhantomV2Config()
SVC = StrategyService(CFG)
signals, meta = SVC.generate_signals_with_metadata(DF_1H.copy(), DF_4H.copy())
ENGINE = BacktestEngine(config=CFG)
ENGINE.strategy_service = SVC

for direction in (1, -1):
    i = first_signal_bar(meta, signals, direction)
    check(f"snapshot delegation (dir {direction})",
          BacktestEngine._condition_snapshot(meta, i, direction) == tc.condition_snapshot(meta, i, direction))
    check(f"entry-conditions text delegation (dir {direction})",
          ENGINE._entry_conditions_text(meta, i, direction) == tc.entry_conditions_text(CFG, meta, i, direction))
    check(f"macd-line lines delegation (dir {direction})",
          ENGINE._macd_line_conditions_text(meta, i, direction, 8)
          == tc.macd_line_conditions_text(CFG, meta, i, direction, 8))

i = first_signal_bar(meta, signals, 1)
snap = tc.condition_snapshot(meta, i, 1)
text = tc.entry_conditions_text(CFG, meta, i, 1)
check("the snapshot carries every condition flag the trade log shows",
      all(k in snap for k in ('signal_candle_type', 'trend_4h', 'setup', 'rsi14', 'macd_hist', 'adx',
                              'atr14', 'ema50_1h', 'ema50_4h', 'cond_trend_ok', 'cond_adx_ok',
                              'cond_macd_hist_ok', 'cond_atr_regime_ok', 'cond_rsi_ok',
                              'cond_macd_confirm_ok', 'cond_di_ok', 'macd_line', 'macd_signal')))
check("the readable breakdown numbers every condition and ends in PASS/FAIL",
      text.startswith('Side: ') and text.count('\n') >= 5
      and all(line.rstrip().endswith(('PASS', 'FAIL', 'N/A')) for line in text.split('\n')[1:]))
check("candle colours come from the shared helper",
      tc.candle_color(meta, i) in ('GREEN', 'RED', 'DOJI')
      and tc.candle_color(None, i) is None)

print("\n== entry_context = snapshot + readable breakdown + candle stamps ==")
ctx = tc.entry_context(CFG, meta, i, 1, signal_candle_time=DF_1H.index[i],
                       entry_candle_time=DF_1H.index[i], entry_candle_type='GREEN')
check("one call returns everything a trade-log row needs",
      ctx['entry_conditions_detail'] == text and ctx['signal_candle_time'] == DF_1H.index[i]
      and ctx['entry_candle_time'] == DF_1H.index[i] and ctx['entry_candle_type'] == 'GREEN'
      and ctx['cond_trend_ok'] == snap['cond_trend_ok'])
check("entry_context is empty for a metadata-less run (never invents conditions)",
      tc.entry_context(CFG, None, 1, 1) == {})

print("\n== frame_candle_color (the exit-candle stamp) ==")
frame = pd.DataFrame({'is_green': [0, 1, 0], 'is_red': [1, 0, 0]})
check("GREEN / RED / DOJI are read off the indicator frame",
      tc.frame_candle_color(frame, 0) == 'RED' and tc.frame_candle_color(frame, 1) == 'GREEN'
      and tc.frame_candle_color(pd.DataFrame({'is_green': [0], 'is_red': [0]}), 0) == 'DOJI')
check("a frame without candle flags (or no frame) yields None, not a guess",
      tc.frame_candle_color(pd.DataFrame({'close': [1.0]}), 0) is None
      and tc.frame_candle_color(None) is None and tc.frame_candle_color(pd.DataFrame(), 0) is None)


# --------------------------------------------------------------------------
class StubMetaStrategy:
    """A strategy that publishes condition metadata (like PhantomV2 does)."""
    label = 'STUB'

    def __init__(self, meta):
        self.config = PhantomV2Config()
        self.meta = meta

    def generate_signals(self, df_1h, df_4h):
        return [0] * len(self.meta)

    def generate_signals_with_metadata(self, df_1h, df_4h):
        return [0] * len(self.meta), self.meta


print("\n== paper worker: conditions recorded at entry, exported at exit ==")
paper = PaperTradeService('PhantomV2', PhantomV2Config(), initial_capital=20000, margin_pct=25)
paper.strategy = StubMetaStrategy(meta)
# The workers describe the newest bar — the one whose signal they act on.
last_i = len(meta) - 1
last_time = DF_1H.index[last_i]
last_text = tc.entry_conditions_text(paper.strategy.config, meta, last_i, 1)
last_snap = tc.condition_snapshot(meta, last_i, 1)
entry_ctx = paper._entry_condition_context(1, DF_1H, DF_4H, last_time)
check("the paper entry records the shared condition detail",
      entry_ctx.get('entry_conditions_detail') == last_text
      and entry_ctx.get('signal_candle_time') == last_time
      and entry_ctx.get('cond_rsi_ok') == last_snap['cond_rsi_ok'])

trade = paper.oms.create_order('BTCUSDT', 1, 100.0, 1.0, DF_1H.index[i], 5000.0, 85.0)
trade.entry_context = entry_ctx
trade.exit_candle_type = 'RED'
trade.exit_detail = 'Take profit hit — price rose to 100.90 ≥ TP 100.90'
trade.exit_reason = 'TP'
paper._record_closed(trade, 120.5, 9.5, 130.0)
rec = paper.closed_trades[-1]
check("the closed-trade record carries the entry conditions",
      rec.get('entry_conditions_detail') == last_text and rec.get('trend_4h') == last_snap['trend_4h']
      and rec.get('cond_trend_ok') == last_snap['cond_trend_ok'])
check("the record carries the candle stamps as IST strings",
      isinstance(rec.get('signal_candle_time'), str) and rec['signal_candle_time'].endswith('+05:30')
      and rec.get('entry_candle_type') == 'GREEN' and rec.get('exit_candle_type') == 'RED')
check("the record still carries the exit rule and the PnL the UI already used",
      rec['exit_detail'].startswith('Take profit hit') and rec['reason'] == 'TP'
      and abs(rec['pnl'] - 120.5) < 1e-9)

no_meta = PaperTradeService('FastTestV1', fast_test_v1_config(), initial_capital=20000, margin_pct=25)
no_meta.strategy = FastTestV1StrategyService(no_meta.config)
check("a metadata-less strategy records nothing extra (and never raises)",
      no_meta._entry_condition_context(1, DF_1H, DF_4H, DF_1H.index[i]) == {})

legacy = paper.oms.create_order('BTCUSDT', -1, 100.0, 1.0, DF_1H.index[i], 5000.0, 85.0)
legacy.exit_reason = 'SL'
paper._record_closed(legacy, -50.0, 5.0, -45.0)
legacy_rec = paper.closed_trades[-1]
check("a trade with no context still records cleanly (old sessions keep working)",
      'entry_conditions_detail' not in legacy_rec and 'exit_candle_type' not in legacy_rec
      and legacy_rec['reason'] == 'SL')

print("\n== live worker: the same record shape ==")


class FakeBroker:
    def __init__(self):
        self.calls = []

    def place_order(self, *a, **k):
        self.calls.append((a, k))
        return {'id': 'x', 'average_price': 100.9}


live = LiveTradeService('PhantomV2', PhantomV2Config(), 'k', 's', initial_capital=20000,
                        margin_pct=25, broker_name='Delta')
live.strategy = StubMetaStrategy(meta)
live.broker = FakeBroker()
live_ctx = live._entry_condition_context(1, DF_1H, DF_4H, last_time)
live_trade = live.oms.create_order('BTCUSDT', 1, 100.0, 1.0, DF_1H.index[i], 5000.0, 85.0)
live_trade.entry_context = live_ctx
live_trade.exit_candle_type = 'GREEN'
live._record_closed(live_trade)
live_rec = live.closed_trades[-1]
check("the live closed-trade record carries the entry conditions",
      live_rec.get('entry_conditions_detail') == last_text
      and live_rec.get('cond_atr_regime_ok') == last_snap['cond_atr_regime_ok'])
check("the live record carries the exit candle colour",
      live_rec.get('exit_candle_type') == 'GREEN' and live_rec.get('signal_candle_time', '').endswith('+05:30'))

live_no_meta = LiveTradeService('FastTestV1', fast_test_v1_config(), 'k', 's')
live_no_meta.strategy = FastTestV1StrategyService(live_no_meta.config)
check("the live worker skips the snapshot for a metadata-less strategy",
      live_no_meta._entry_condition_context(-1, DF_1H, DF_4H, DF_1H.index[i]) == {})

print("\n== end-to-end: a live paper tick stores the conditions on the entry ==")
import asyncio  # noqa: E402
import datetime as _dt  # noqa: E402
import numpy as np  # noqa: E402
from types import SimpleNamespace  # noqa: E402


def fresh_frames():
    """100 hourly candles ending now, plus the matching 4h frame."""
    end = pd.Timestamp.utcnow().floor('h')
    idx = pd.date_range(end=end, periods=100, freq='h')
    base = 60000 + 500 * np.sin(np.arange(100) / 7.0)
    df = pd.DataFrame({
        'open': base, 'high': base + 120, 'low': base - 120, 'close': base + 20,
        'volume': 1.0,
    }, index=idx)
    idx4 = pd.date_range(end=end, periods=100, freq='4h')
    base4 = 60000 + 500 * np.sin(np.arange(100) / 4.0)
    df4 = pd.DataFrame({
        'open': base4, 'high': base4 + 200, 'low': base4 - 200, 'close': base4 + 10,
        'volume': 1.0,
    }, index=idx4)
    return df, df4


class AlwaysLong:
    """Signals long on every bar, and publishes real Phantom metadata."""
    label = 'TEST'

    def __init__(self, cfg, meta):
        self.config = cfg
        self.meta = meta

    def _signals(self, n):
        return np.ones(n)

    def generate_signals(self, df_1h, df_4h):
        return self._signals(len(df_1h))

    def generate_signals_with_metadata(self, df_1h, df_4h):
        return self._signals(len(df_1h)), self.meta


async def run_paper_entry():
    df_1h, df_4h = fresh_frames()
    cfg = PhantomV2Config()
    _, meta = StrategyService(cfg).generate_signals_with_metadata(df_1h.copy(), df_4h.copy())
    svc = PaperTradeService('PhantomV2', cfg, initial_capital=200000, margin_pct=50,
                            market_source='Binance', broker_name='Binance')
    svc.strategy = AlwaysLong(cfg, meta)
    price = float(df_1h['close'].iloc[-1])
    svc.use_mark_price = True
    svc._fetch_candles = lambda interval, limit: (df_1h if interval == '1h' else df_4h)
    svc._fetch_mark_price = lambda: SimpleNamespace(mark_price=price, last_price=price)
    await svc.tick()
    trade = next(iter(svc.oms.active_trades.values()), None)
    # Bring the plan's TP inside the current price so the next tick exits.
    if trade is not None:
        trade.tp = price * 0.999
    await svc.tick()
    return svc, trade, meta, df_1h


svc, opened, meta, frame = asyncio.run(run_paper_entry())
check("a real paper tick opened a trade", opened is not None)
check("the entry carries the condition record the client exports",
      bool(getattr(opened, 'entry_context', None))
      and opened.entry_context.get('entry_conditions_detail', '').startswith('Side: LONG'),
      str(getattr(opened, 'entry_context', None))[:200])
check("the entry record names the signal bar and its colour",
      opened.entry_context.get('signal_candle_time') == frame.index[-1]
      and opened.entry_context.get('signal_candle_type') in ('GREEN', 'RED', 'DOJI'))
closed = svc.closed_trades[-1] if svc.closed_trades else {}
check("the closed paper trade exports the entry conditions",
      closed.get('entry_conditions_detail', '').startswith('Side: LONG')
      and 'setup' in closed and closed.get('trend_4h') in ('UP', 'DOWN'),
      str(closed)[:200])
check("the closed paper trade exports the exit condition and its candle",
      closed.get('reason') and closed.get('exit_candle_type') in ('GREEN', 'RED', 'DOJI')
      and bool(closed.get('exit_detail')),
      str({k: closed.get(k) for k in ('reason', 'exit_candle_type', 'exit_detail')}))
check("the paper record keeps every number the panel already showed",
      closed.get('pnl') is not None and closed.get('bars_held') is not None
      and closed.get('entry') is not None and closed.get('entry_time'))

print(f"\nPASSED: {PASSED}  FAILED: {FAILED}")
sys.exit(1 if FAILED else 0)
