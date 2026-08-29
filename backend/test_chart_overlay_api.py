"""Contract for plotting strategy results on market candles.

The chart overlay is useless without:
  * /klines honouring start_date/end_date (last-500 from now never contains a
    2020–2024 backtest marker)
  * /phantom/signals returning LONG/SHORT, setup, 4h trend and candle colour
"""
import ast
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from app.main import get_klines, phantom_signals

pass_n = fail_n = 0

def check(name, cond, extra=''):
    global pass_n, fail_n
    if cond:
        pass_n += 1
    else:
        fail_n += 1
        print(f'  FAIL: {name} {extra}')

sig = inspect.signature(get_klines)
check('/klines accepts start_date', 'start_date' in sig.parameters)
check('/klines accepts end_date', 'end_date' in sig.parameters)

src = inspect.getsource(get_klines)
check('/klines filters event_time when a window is set',
      'Klines.event_time >=' in src and 'timedelta(days=1)' in src)
check('/klines does not cap a windowed query at 500',
      '60000' in src)

src_sig = inspect.getsource(phantom_signals)
for field in ('side', 'trend_label', 'candle_type', 'macd_hist', 'trend'):
    check(f'/phantom/signals returns {field}', f'"{field}"' in src_sig or f"'{field}'" in src_sig)

# Parse-check both functions so a truncated edit cannot ship.
ast.parse(inspect.getsource(get_klines))
ast.parse(inspect.getsource(phantom_signals))
check('get_klines and phantom_signals still parse', True)

print(f'\n{pass_n} passed, {fail_n} failed')
sys.exit(1 if fail_n else 0)
