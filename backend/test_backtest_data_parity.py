"""Offline regression tests for Backtest/Paper market-data parity.

A paper worker asks the venue for BTCUSD candles on Delta, and the seeder
stores them under that venue symbol. The backtest used to ask the DB for
"BTCUSDT" instead, found nothing, and either failed or silently backtested a
short live-API page — which is why an identical strategy showed paper trades
but no backtest trades for the same two months.

Run from the repo root:  python backend/test_backtest_data_parity.py
No network access: every venue call is stubbed.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TEST_DB = tempfile.mktemp(suffix='_backtest_parity.db')
os.environ['DATABASE_URL'] = f'sqlite:///{TEST_DB}'

from app.database.models import init_db, SessionLocal, Klines  # noqa: E402
from app.core.engine import BacktestEngine  # noqa: E402
from app.core.strategy import PhantomV2Config  # noqa: E402
from app.services.data_sync import DataSyncService  # noqa: E402

init_db()

PASS, FAIL = [], []


def check(name, ok, extra=''):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not ok else ''))


def add_candles(source, symbol, interval, start, count, step_hours=1, close=100.0):
    db = SessionLocal()
    for i in range(count):
        when = start + timedelta(hours=i * step_hours)
        db.add(Klines(source=source, symbol=symbol, interval=interval, event_time=when,
                      open=close, high=close + 5, low=close - 5, close=close + 1, volume=1.0))
    db.commit()
    db.close()


def no_api(*args, **kwargs):
    """Stand-in for an unreachable venue: no silent fallback data."""
    return []


DataSyncService.fetch_klines = staticmethod(no_api)
DataSyncService.fetch_mark_klines = staticmethod(no_api)

print('\n== venue symbol resolution ==')
start = datetime(2026, 9, 1)
add_candles('Delta', 'BTCUSD', '1h', start, 72)          # what the seeder writes
add_candles('Binance', 'BTCUSDT', '1h', start, 72)
add_candles('legacycsv', 'BTCUSDT', '1h', start, 72)     # legacy CSV import

engine = BacktestEngine(PhantomV2Config(), data_source='Delta')
df = engine._get_data_from_db('BTCUSDT', '1h', '2026-09-01', '2026-09-02', 'Delta')
check('Delta BTCUSDT request reads seeded BTCUSD candles', len(df) == 48, f'rows={len(df)}')
check('seeded frame remembers the venue symbol', df.attrs.get('symbol') == 'BTCUSD', str(df.attrs))
check('result is flagged as seeded, not venue fallback', df.attrs.get('origin') == 'seeded')

df = engine._get_data_from_db('BTCUSDT', '1h', '2026-09-01', '2026-09-02', 'Binance')
check('Binance BTCUSDT still resolves', len(df) == 48 and df.attrs.get('symbol') == 'BTCUSDT')

# A legacy CSV series kept the canonical symbol and a differently-cased source.
legacy = BacktestEngine(PhantomV2Config(), data_source='LEGACYCSV')
df = legacy._get_data_from_db('BTCUSDT', '1h', '2026-09-01', '2026-09-02', 'legacycsv')
check('source spelling and legacy symbol are tolerated',
      len(df) == 48 and df.attrs.get('symbol') == 'BTCUSDT', f'rows={len(df)}')

# A date-only end is inclusive through that day (not just up to midnight).
add_candles('DateTest', 'BTCUSDT', '1h', start, 1)
db = SessionLocal()
for day in (2, 3):
    db.add(Klines(source='DateTest', symbol='BTCUSDT', interval='1h',
                  event_time=datetime(2026, 9, day, 12), open=1, high=2, low=0, close=1, volume=1))
db.commit()
db.close()
df = engine._get_data_from_db('BTCUSDT', '1h', '2026-09-01', '2026-09-02', 'DateTest')
check('date-only end_date includes the final day', len(df) == 2, f'rows={len(df)}')
df = engine._get_data_from_db('BTCUSDT', '1h', '2026-09-01', '2026-09-03', 'DateTest')
check('the day after end_date is excluded', len(df) == 3, f'rows={len(df)}')

print('\n== end-to-end backtest on seeded Delta candles ==')
import pandas as pd  # noqa: E402

data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
df_1h_csv = pd.read_csv(os.path.join(data_dir, 'btc_1h.csv'), index_col=0, parse_dates=True)
df_4h_csv = pd.read_csv(os.path.join(data_dir, 'btc_4h.csv'), index_col=0, parse_dates=True)
window_start, window_end = '2023-04-01', '2023-06-01'
db = SessionLocal()
for frame, interval in ((df_1h_csv, '1h'), (df_4h_csv, '4h')):
    for ts, row in frame.loc[window_start:window_end].iterrows():
        db.add(Klines(source='Delta', symbol='BTCUSD', interval=interval,
                      event_time=ts.to_pydatetime(), open=float(row.open), high=float(row.high),
                      low=float(row.low), close=float(row.close), volume=float(row.volume)))
db.commit()
db.close()

engine = BacktestEngine(PhantomV2Config(), data_source='Delta')
results = engine.run(symbol='BTCUSDT', initial_capital_inr=50000, conversion_rate=85.0,
                     start_date=window_start, end_date=window_end)
check('seeded two-month Delta backtest produces trades', results['total_trades'] > 0,
      f"trades={results['total_trades']} rejects={results['rejected_reasons']}")
info = results['data_info']
check('run reports the candles it actually used',
      info['1h']['origin'] == 'seeded' and info['1h']['symbol'] == 'BTCUSD'
      and info['1h']['rows'] > 1000 and info['4h']['rows'] > 300, json.dumps(info, default=str))

print('\n== venue fallback covers the whole window ==')
calls = []


def paged_klines(source, symbol, interval, start_time=None, end_time=None, limit=500):
    calls.append((source, symbol, interval, start_time, end_time, limit))
    step = timedelta(hours=1)
    cursor = start_time
    rows = []
    while cursor <= end_time and len(rows) < min(limit, 400):
        rows.append({'event_time': cursor, 'open': 100.0, 'high': 101.0,
                     'low': 99.0, 'close': 100.5, 'volume': 1.0})
        cursor += step
    return rows


DataSyncService.fetch_klines = staticmethod(paged_klines)
real_page_limit = DataSyncService._page_limit.__func__
DataSyncService._page_limit = classmethod(lambda cls, source, requested, definition=None: 100)
fallback = BacktestEngine(PhantomV2Config(use_mark_price=False), data_source='Delta')
df = fallback._get_data_from_db('BTCUSDT', '1h', '2026-11-01', '2026-11-10', 'Delta')
check('fallback pages past one exchange page', len(df) == 240 and len(calls) >= 3,
      f'rows={len(df)} pages={len(calls)}')
check('fallback asks the venue for BTCUSD',
      bool(calls) and all(call[1] == 'BTCUSD' for call in calls), str(calls[:2]))
DataSyncService.fetch_klines = staticmethod(no_api)
DataSyncService._page_limit = real_page_limit

print('\n== empty data fails loudly ==')
empty = BacktestEngine(PhantomV2Config(), data_source='Delta')
try:
    empty.run(symbol='BTCUSDT', start_date='2026-11-01', end_date='2026-11-02')
    check('unseeded backtest explains the missing candles', False, 'no exception raised')
except ValueError as exc:
    message = str(exc)
    check('unseeded backtest explains the missing candles',
          'BTCUSD' in message and 'Seed both intervals' in message, message[:160])

print(f'\nRESULT: {len(PASS)} passed, {len(FAIL)} failed')
if FAIL:
    print('Failed:', ', '.join(FAIL))
    sys.exit(1)
