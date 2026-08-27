"""Trade-log detail: entry candle + colour, every entry condition, exit rule.

Offline — uses the bundled BTC candles and runs the real BacktestEngine, then
checks what a reviewer would see in the trade log and the Excel/CSV export.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from app.core.engine import BacktestEngine
from app.core.indicators import compute_indicators
from app.core.strategy import PhantomV2Config

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not cond else ""), flush=True)


DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
df_1h = pd.read_csv(os.path.join(DATA, 'btc_1h.csv'), index_col=0, parse_dates=True).iloc[:3000]
df_4h = pd.read_csv(os.path.join(DATA, 'btc_4h.csv'), index_col=0, parse_dates=True).iloc[:800]

BASE = dict(adx_min=10, macd_hist_min=5, rsi_oversold=40, rsi_overbought=60,
            atr_regime_ratio=0.5, enable_momentum_entry=True, stop_loss_atr=1.2,
            take_profit_atr=14.0, trail_activation_atr=0.8, trail_distance_atr=0.3,
            breakeven_atr=0.75, leverage=2, margin_pct=0.15)

res = BacktestEngine(config=PhantomV2Config(**BASE)).run(
    df_1h=df_1h.copy(), df_4h=df_4h.copy(), initial_capital_inr=20000)
trades = res['trades']
mom = [t for t in trades if t['setup'] == 'MOMENTUM']
rev = [t for t in trades if t['setup'] == 'REVERSAL']

print("\n== which candle got the entry, and its colour ==", flush=True)
check("run produced trades", len(trades) > 0, str(len(trades)))
check("both setups present", len(mom) > 0 and len(rev) > 0, f"mom={len(mom)} rev={len(rev)}")
for label, t in (('first trade', trades[0]),):
    check(f"{label}: signal candle time recorded", t.get('signal_candle_time') is not None)
    check(f"{label}: entry candle time recorded", t.get('entry_candle_time') is not None)
    check(f"{label}: entry candle is the candle AFTER the signal",
          t.get('entry_candle_time') is not None and t.get('signal_candle_time') is not None
          and t['entry_candle_time'] > t['signal_candle_time'],
          f"{t.get('signal_candle_time')} -> {t.get('entry_candle_time')}")
    check(f"{label}: entry candle time == entry fill time",
          t.get('entry_candle_time') == t.get('entry_time'),
          f"{t.get('entry_candle_time')} vs {t.get('entry_time')}")
    check(f"{label}: signal candle colour recorded",
          t.get('signal_candle_type') in ('GREEN', 'RED', 'DOJI'), str(t.get('signal_candle_type')))
    check(f"{label}: entry candle colour recorded",
          t.get('entry_candle_type') in ('GREEN', 'RED', 'DOJI'), str(t.get('entry_candle_type')))
    check(f"{label}: exit candle colour recorded",
          t.get('exit_candle_type') in ('GREEN', 'RED', 'DOJI'), str(t.get('exit_candle_type')))

colours = {t.get('entry_candle_type') for t in trades}
check("entry colours include green and red across the run",
      {'GREEN', 'RED'}.issubset(colours), str(colours))

# The seeded candles never close exactly on their open, so DOJI is documented
# and rendered by the UI but never actually produced by this run. Exercise the
# classification directly so the third colour cannot silently rot.
print("\n== candle colour classification (incl. DOJI, which this data never hits) ==", flush=True)
_syn = {'is_green': np.array([True, False, False]),
        'is_red':   np.array([False, True,  False])}
for _i, _exp in ((0, 'GREEN'), (1, 'RED'), (2, 'DOJI')):
    _got = BacktestEngine._candle_color(_syn, _i)
    check(f"close {'>' if _i == 0 else '<' if _i == 1 else '=='} open -> {_exp}",
          _got == _exp, str(_got))
check("colour is None when metadata is missing", BacktestEngine._candle_color(None, 0) is None)
check("colour is None when the bar index is out of range",
      BacktestEngine._candle_color(_syn, 99) is None)
check("colour is None when the metadata lacks the keys",
      BacktestEngine._candle_color({'other': np.array([True])}, 0) is None)
# The documented rule is close vs open, computed in indicators.py as
# is_green = c > o, is_red = c < o — confirm that is what feeds the colour.
_ind = compute_indicators(pd.DataFrame({
    'open':   [100.0, 100.0, 100.0],
    'high':   [101.0, 101.0, 101.0],
    'low':    [99.0,  99.0,  99.0],
    'close':  [105.0, 95.0, 100.0],   # green, red, doji
    'volume': [1.0,   1.0,  1.0],
}, index=pd.date_range('2020-01-01', periods=3, freq='h')))
_real_meta = {'is_green': _ind['is_green'], 'is_red': _ind['is_red']}
check("indicators drive the colour end to end",
      [BacktestEngine._candle_color(_real_meta, i) for i in range(3)] == ['GREEN', 'RED', 'DOJI'],
      str([BacktestEngine._candle_color(_real_meta, i) for i in range(3)]))
differ = [t for t in trades if t.get('signal_candle_type') != t.get('entry_candle_type')]
check("signal and entry candles can differ in colour", len(differ) > 0, str(len(differ)))
check("legacy candle_type still present (signal candle)",
      all(t.get('candle_type') in ('GREEN', 'RED', 'DOJI') for t in trades))

print("\n== every entry condition is spelled out ==", flush=True)
for label, group in (('MOMENTUM', mom), ('REVERSAL', rev)):
    t = group[0]
    detail = t.get('entry_conditions_detail') or ''
    check(f"{label}: entry_conditions_detail present", len(detail) > 0)
    check(f"{label}: names the side and setup",
          detail.splitlines()[0].startswith('Side: ') and label in detail.splitlines()[0],
          detail.splitlines()[0] if detail else '')
    check(f"{label}: lists 4h trend condition", '4h trend' in detail)
    check(f"{label}: lists ADX condition", 'ADX' in detail)
    check(f"{label}: lists ATR regime with the operator applied",
          'ATR regime' in detail and 'x SMA50(ATR)' in detail)
    check(f"{label}: every line carries PASS/FAIL/N-A",
          all(l.endswith(('PASS', 'FAIL', 'N/A')) for l in detail.splitlines()[1:]),
          detail)

print("\n== no fired trade fails a gate its own setup applies ==", flush=True)
unexpected = []
for t in trades:
    for line in (t.get('entry_conditions_detail') or '').splitlines()[1:]:
        if line.endswith('FAIL'):
            unexpected.append((t['setup'], line))
check("zero unexpected FAILs across all trades", not unexpected, str(unexpected[:3]))

print("\n== setup-specific gates are reported truthfully ==", flush=True)
check("MOMENTUM does not claim a MACD-magnitude PASS/FAIL",
      all(t.get('cond_macd_hist_ok') is None for t in mom),
      str({t.get('cond_macd_hist_ok') for t in mom}))
check("MOMENTUM reports the DI gate",
      all(t.get('cond_di_ok') is True for t in mom), str({t.get('cond_di_ok') for t in mom}))
check("MOMENTUM text says the magnitude filter is not applied",
      all('not applied' in (t.get('entry_conditions_detail') or '') for t in mom))
check("REVERSAL reports the MACD-magnitude gate",
      all(t.get('cond_macd_hist_ok') is True for t in rev),
      str({t.get('cond_macd_hist_ok') for t in rev}))
check("REVERSAL does not claim a DI PASS/FAIL",
      all(t.get('cond_di_ok') is None for t in rev), str({t.get('cond_di_ok') for t in rev}))
check("REVERSAL text names the candle colour it needed",
      all('Candle colour' in (t.get('entry_conditions_detail') or '') for t in rev))
check("trend gate reported and true for every trade",
      all(t.get('cond_trend_ok') is True for t in trades))

print("\n== exit condition is recorded ==", flush=True)
check("exit_detail present on every trade",
      all((t.get('exit_detail') or '') != '' for t in trades))
check("exit_detail names the rule that fired",
      all(any(k in t['exit_detail'] for k in ('Stop loss hit', 'Take profit hit',
              'Trailing stop hit', 'Max holding time')) for t in trades),
      str({t['exit_detail'][:22] for t in trades}))
check("exit_reason present on every trade", all(t.get('exit_reason') for t in trades))
check("stop plan at entry vs exit recorded",
      all(t.get('sl_entry') is not None and t.get('trail_stop') is not None for t in trades))
check("ATR at entry and peak price recorded",
      all(t.get('atr_at_entry') is not None and t.get('peak_price') is not None for t in trades))

print("\n== per-side ATR operator shows up in the condition text ==", flush=True)
split_cfg = PhantomV2Config(**BASE, entry_conditions={
    'use_direction_atr_floor': True,
    'long': {'atr_regime_ratio': 0.5, 'atr_regime_op': '>='},
    'short': {'atr_regime_ratio': 1.2, 'atr_regime_op': '<'},
})
res2 = BacktestEngine(config=split_cfg).run(df_1h=df_1h.copy(), df_4h=df_4h.copy(),
                                            initial_capital_inr=20000)
longs = [t for t in res2['trades'] if t['direction'] == 1]
shorts = [t for t in res2['trades'] if t['direction'] == -1]
check("long trades show the >= rule",
      longs and all('ATR regime: ATR' in t['entry_conditions_detail']
                    and ' >= 0.50 x SMA50(ATR)' in t['entry_conditions_detail'] for t in longs))
check("short trades show the < rule",
      shorts and all(' < 1.20 x SMA50(ATR)' in t['entry_conditions_detail'] for t in shorts),
      (shorts[0]['entry_conditions_detail'] if shorts else 'no shorts'))

print("\n== engine CSV export carries the new columns ==", flush=True)
out = '/tmp/kudos_trade_log_test.csv'
BacktestEngine.export_trade_log(trades, out)
head = open(out, encoding='utf-8').readline().strip()
for col in ('signal_candle_time', 'signal_candle_type', 'entry_candle_time',
            'entry_candle_type', 'exit_candle_type', 'cond_trend_ok', 'cond_di_ok',
            'entry_conditions_detail', 'exit_detail', 'sl_entry', 'trail_stop'):
    check(f"CSV column {col}", col in head.split(','), head[:200])

print(f"\n{len(PASS)} passed, {len(FAIL)} failed", flush=True)
if FAIL:
    print("FAILED:", FAIL, flush=True)
    sys.exit(1)
