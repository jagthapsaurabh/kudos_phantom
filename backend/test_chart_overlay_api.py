"""Contract for plotting strategy results on market candles.

The chart overlay is useless without:
  * /klines honouring start_date/end_date (last-500 from now never contains a
    2020–2024 backtest marker)
  * every candle series being oldest-first — lightweight-charts asserts on the
    order and a venue fallback (Delta) answers newest-first
  * /phantom/signals returning LONG/SHORT, setup, 4h trend and candle colour
"""
import ast
import inspect
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from app.main import get_klines, phantom_signals
from app.services.data_sync import DataSyncService

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

check('/klines fallback hands the chart chronological candles',
      'sorted(by_time)' in src and 'limit=cap' in src)

# --- candle order -----------------------------------------------------------
# Delta serves /v2/history/candles newest-first. The parse step normalizes it,
# so the seed walk, the last-N page and the chart overlay all see one candle per
# timestamp, oldest first.
descending = [
    {'time': 1790845200, 'open': 105, 'high': 110, 'low': 100, 'close': 108, 'volume': 5},
    {'time': 1790841600, 'open': 100, 'high': 106, 'low': 99, 'close': 105, 'volume': 4},
    {'time': 1790838000, 'open': 95, 'high': 101, 'low': 94, 'close': 100, 'volume': 3},
]
parsed = DataSyncService._parse_candle_rows(descending)
times = [row['event_time'] for row in parsed]
check('venue candles are normalized oldest-first', times == sorted(times) and len(times) == 3,
      str(times))
# the parsers hand over naive UTC datetimes (matching the DB column)
check('the oldest venue candle is first',
      parsed[0]['event_time'] == datetime(2026, 10, 1, 7, 0), str(parsed[0]['event_time']))

dupes = DataSyncService._parse_candle_rows(descending + [
    {'time': 1790841600, 'open': 1, 'high': 2, 'low': 0.5, 'close': 1.5, 'volume': 9},
])
check('a duplicate venue timestamp collapses to one candle',
      len(dupes) == 3 and [row['event_time'] for row in dupes] == sorted(times))
check('on a duplicate the later venue row wins',
      dupes[1]['close'] == 1.5, str(dupes[1]))

check('malformed venue rows are skipped instead of aborting the page',
      DataSyncService._parse_candle_rows([None, {'open': 1}, {'time': 'x', 'open': 1}]) == []
      and DataSyncService._parse_candle_rows(None) == [])


class _EmptyQuery:
    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def all(self):
        return []


class _EmptyDB:
    def query(self, *a, **k):
        return _EmptyQuery()


# The DB seed is empty → /klines falls back to the venue. Even if the venue
# adapter ever hands rows over newest-first again, the endpoint must sort them.
original_fetch = DataSyncService.fetch_klines
try:
    DataSyncService.fetch_klines = classmethod(lambda cls, *a, **k: [
        {'event_time': datetime.fromtimestamp(ts, tz=timezone.utc), 'open': 1.0, 'high': 2.0,
         'low': 0.5, 'close': 1.5, 'volume': 7.0}
        for ts in (1790845200, 1790841600, 1790838000)
    ])
    fallback = get_klines(symbol='BTCUSDT', interval='1h', source='Delta', db=_EmptyDB())
finally:
    DataSyncService.fetch_klines = original_fetch
check('the /klines venue fallback returns ascending bars',
      [row['time'] for row in fallback] == [1790838000, 1790841600, 1790845200],
      str([row['time'] for row in fallback]))
check('the /klines venue fallback keeps OHLC + volume',
      fallback[0]['open'] == 1.0 and fallback[0]['volume'] == 7.0)

# Parse-check both functions so a truncated edit cannot ship.
ast.parse(inspect.getsource(get_klines))
ast.parse(inspect.getsource(phantom_signals))
check('get_klines and phantom_signals still parse', True)

print(f'\n{pass_n} passed, {fail_n} failed')
sys.exit(1 if fail_n else 0)
