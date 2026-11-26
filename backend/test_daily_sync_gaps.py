"""Offline regression checks for the daily market-data sync and gap repair.

Covers the client-reported "paper has candles/trades, backtest doesn't" case:
the full-history seed used to write Delta candles as BTCUSDT while the daily
refresh wrote BTCUSD, so the two series diverged; the daily job also started
from the last stored candle and never closed interior holes or backfilled
mark prices on the seeded rows.

Run from the repo root:  python backend/test_daily_sync_gaps.py
No network access: venue adapters are stubbed.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TEST_DB = tempfile.mktemp(suffix='_daily_sync.db')
os.environ['DATABASE_URL'] = f'sqlite:///{TEST_DB}'

from app.database.models import (  # noqa: E402
    Klines, SessionLocal, init_db, migrate_db,
)
from app.services.data_sync import DataSyncService  # noqa: E402

init_db()
PASS, FAIL = [], []


def check(name, ok, extra=''):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not ok else ''))


def add(db, source, symbol, interval, when, close=100.0, marked=False):
    db.add(Klines(source=source, symbol=symbol, interval=interval, event_time=when,
                  open=close, high=close + 2, low=close - 2, close=close + 1, volume=1.0,
                  mark_open=close + 0.5 if marked else None,
                  mark_close=close + 0.5 if marked else None))


print('\n== one canonical symbol per source ==')
check('Delta resolves BTCUSDT to BTCUSD',
      DataSyncService.series_symbol('Delta', 'BTCUSDT') == 'BTCUSD')
check('Binance keeps BTCUSDT',
      DataSyncService.series_symbol('Binance', 'BTCUSDT') == 'BTCUSDT')

# Simulate a database seeded before the fix: full history under BTCUSDT.
db = SessionLocal()
base = datetime.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=8)
for i in (0, 1, 2, 4, 5):
    add(db, 'Delta', 'BTCUSDT', '1h', base + timedelta(hours=i), marked=(i == 0))
db.commit()
db.close()
migrate_db()
db = SessionLocal()
canonical = db.query(Klines).filter_by(source='Delta', symbol='BTCUSD', interval='1h').count()
legacy = db.query(Klines).filter_by(source='Delta', symbol='BTCUSDT', interval='1h').count()
marked = db.query(Klines).filter(
    Klines.source == 'Delta', Klines.symbol == 'BTCUSD', Klines.interval == '1h',
    Klines.mark_close.isnot(None)).count()
db.close()
check('legacy rows copied into the venue series without deleting originals',
      canonical == 5 and legacy == 5, f'canonical={canonical} legacy={legacy}')
check('mark prices survive the merge', marked == 1, f'marked={marked}')
# Idempotent: a second startup must not duplicate anything.
migrate_db()
db = SessionLocal()
again = db.query(Klines).filter_by(source='Delta', symbol='BTCUSD', interval='1h').count()
db.close()
check('migration is idempotent and keeps both series', again == 5 and legacy == 5,
      f'canonical={again} legacy={legacy}')

# A newer canonical candle must never be reverted by the older alias copy.
db = SessionLocal()
row = db.query(Klines).filter_by(source='Delta', symbol='BTCUSD', interval='1h',
                                 event_time=base).first()
row.close = 999.0
db.commit()
db.close()
migrate_db()
db = SessionLocal()
kept_close = db.query(Klines).filter_by(
    source='Delta', symbol='BTCUSD', interval='1h', event_time=base).first().close
legacy_close = db.query(Klines).filter_by(
    source='Delta', symbol='BTCUSDT', interval='1h', event_time=base).first().close
db.close()
check('stale legacy rows never overwrite newer canonical candles',
      kept_close == 999.0 and legacy_close < 999.0, f'canonical={kept_close} legacy={legacy_close}')

print('\n== gap detection ==')
db = SessionLocal()
# The series jumps from base+2h to base+4h: one candle is missing at base+3h.
db.query(Klines).filter(Klines.source == 'Delta', Klines.symbol == 'BTCUSD',
                        Klines.event_time == base + timedelta(hours=3)).delete()
db.commit()
db.close()
gaps = DataSyncService.find_gaps('Delta', 'BTCUSDT', '1h', end=datetime.utcnow())
check('find_gaps sees the missing candle', len(gaps) == 1 and gaps[0][0] == base + timedelta(hours=3),
      str(gaps[:2]))

health = {(row['source'], row['symbol'], row['interval']): row for row in DataSyncService.data_health()}
h = health.get(('Delta', 'BTCUSD', '1h'))
check('data_health exposes the gap', h and h['gap_count'] == 1 and h['largest_gap_bars'] == 1,
      str(h))

print('\n== daily refresh closes the hole on the canonical series ==')
calls = []


def stub_klines(cls, source='Delta', symbol='BTCUSD', interval='1h',
                start_time=None, end_time=None, limit=1000, definition=None):
    calls.append({'symbol': symbol, 'start': start_time, 'end': end_time})
    rows, cursor = [], cls._as_datetime(start_time)
    final = cls._as_datetime(end_time)
    step = timedelta(hours=1)
    while cursor <= final and len(rows) < limit:
        rows.append({'event_time': cursor, 'open': 100.0, 'high': 102.0,
                     'low': 99.0, 'close': 101.0, 'volume': 5.0})
        cursor += step
    return rows


REAL_FETCH = DataSyncService.fetch_klines.__func__
DataSyncService.fetch_klines = classmethod(stub_klines)
try:
    summary = DataSyncService.sync_market_data(
        'Delta', symbol='BTCUSDT', intervals=['1h'], limit=1000)[0]
finally:
    DataSyncService.fetch_klines = classmethod(REAL_FETCH)

check('daily refresh starts at the oldest missing candle',
      bool(calls) and calls[0]['start'] == base + timedelta(hours=3), str(calls[:2]))
check('daily refresh writes to the venue symbol', summary['symbol'] == 'BTCUSD', str(summary))
check('daily refresh reports the gap it found', summary['gaps_found'] >= 1, str(summary))
check('no gap remains after the refresh', summary['gaps_remaining'] == 0, str(summary))
check('all venue requests used the canonical symbol',
      all(call['symbol'] == 'BTCUSD' for call in calls), str(calls[:2]))
db = SessionLocal()
stored = db.query(Klines).filter_by(source='Delta', symbol='BTCUSD', interval='1h').count()
alias = db.query(Klines).filter_by(source='Delta', symbol='BTCUSDT', interval='1h').count()
db.close()
check('backfill writes canonical candles and preserves the legacy copy',
      stored >= 9 and alias == 5, f'stored={stored} alias={alias}')

print('\n== daily mark-price repair ==')
mark_calls = []


def stub_marks(cls, source='Delta', symbol='BTCUSD', interval='1h',
               start_time=None, end_time=None, limit=1000, definition=None):
    mark_calls.append({'symbol': symbol, 'start': start_time, 'end': end_time})
    rows, cursor = [], cls._as_datetime(start_time)
    final = cls._as_datetime(end_time)
    while cursor <= final and len(rows) < limit:
        rows.append({'event_time': cursor, 'open': 210.0, 'high': 211.0,
                     'low': 209.0, 'close': 210.5})
        cursor += timedelta(hours=1)
    return rows


REAL_MARK = DataSyncService.fetch_mark_klines.__func__
DataSyncService.fetch_mark_klines = classmethod(stub_marks)
try:
    marks = DataSyncService.sync_mark_prices('Delta', symbol='BTCUSDT', intervals=['1h'])[0]
finally:
    DataSyncService.fetch_mark_klines = classmethod(REAL_MARK)

db = SessionLocal()
unmarked = db.query(Klines).filter(
    Klines.source == 'Delta', Klines.symbol == 'BTCUSD', Klines.interval == '1h',
    Klines.event_time >= base, Klines.mark_close.is_(None)).count()
db.close()
check('daily mark sync repaired unmarked candles', unmarked == 0 and marks['updated'] > 0,
      f'unmarked={unmarked} summary={marks}')
check('mark fetch used the canonical BTCUSD series',
      bool(mark_calls) and all(call['symbol'] == 'BTCUSD' for call in mark_calls), str(mark_calls[:2]))

print('\n== no needless venue calls when the series is complete ==')
mark_calls.clear()
DataSyncService.fetch_mark_klines = classmethod(stub_marks)
try:
    DataSyncService.sync_mark_prices('Delta', symbol='BTCUSDT', intervals=['1h'])
finally:
    DataSyncService.fetch_mark_klines = classmethod(REAL_MARK)
check('second mark sync makes no requests', not mark_calls, str(mark_calls[:2]))

print(f'\nRESULT: {len(PASS)} passed, {len(FAIL)} failed')
if FAIL:
    print('Failed:', ', '.join(FAIL))
    sys.exit(1)
