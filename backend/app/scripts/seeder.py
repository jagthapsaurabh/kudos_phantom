"""Market-data seeder CLI.

Default behaviour (no arguments) is exactly what a corrupted Binance seed
needs: fetch clean candles straight from the exchange — 1 Jan 2020 → today
for 15m, 1h, 4h and 1d (daily candles included) — after removing the
duplicate and off-grid candles the legacy CSV path left behind, then refresh
the mark-price series and run the daily incremental sync.

The legacy default preferred local CSV files whose 1h timestamps look like
2020-06-26 11:41:59.523330 — off the candle grid, so charts, backtests and
mark pricing all saw corrupted data. CSVs are now a validated opt-in via
--csv and are rejected when their timestamps are not aligned to the interval.

Examples:
  python -m app.scripts.seeder                        # Binance 2020 → today (15m,1h,4h,1d)
  python -m app.scripts.seeder --source Delta         # Delta 2020 → today (15m,1h,4h,1d)
  python -m app.scripts.seeder --intervals 1d         # daily candles only
  python -m app.scripts.seeder --start 2022-01-01     # custom range
  python -m app.scripts.seeder --no-repair --no-mark-price --no-daily-refresh
  python -m app.scripts.seeder --csv data/btc_4h.csv --intervals 4h   # validated import
"""
import argparse
import os
from datetime import datetime, timedelta

from app.database.models import init_db
from app.services.data_sync import DataSyncService, MarketDataError

DEFAULT_INTERVALS = "15m,1h,4h,1d"  # 1d = daily candles; matches the Delta preset
DEFAULT_START = "2020-01-01"


def seed_full_history(source="Binance", symbol="BTCUSDT", intervals=None,
                      start=DEFAULT_START, end=None, limit=1500,
                      repair=True, mark_price=True, daily_refresh=True):
    """Repair + fetch + upsert one source's full history (default 2020 → today)."""
    init_db()
    intervals = list(intervals or DEFAULT_INTERVALS.split(","))
    end = end or datetime.utcnow().strftime("%Y-%m-%d")

    print(f"Seeding {source} {symbol} {','.join(intervals)} from {start} → {end} ...")
    if repair:
        for item in DataSyncService.repair_klines(source, symbol, intervals):
            print(f"  repair {item['interval']}: removed {item['removed']} corrupt candles "
                  f"({item['duplicates_removed']} duplicate, {item['misaligned_removed']} off-grid), "
                  f"kept {item['kept']}")

    summary = DataSyncService.seed_market_data(
        source=source, symbol=symbol, intervals=intervals,
        start_date=start, end_date=end, limit=limit, fetch_all=True,
    )
    for item in summary:
        line = (f"  {item['interval']}: {item.get('total', 0)} candles stored "
                f"({item.get('first')} → {item.get('last')}) in {item.get('pages', 0)} windows"
                + (f" — {item['error']}" if item.get('error') else ""))
        print(line)

    if mark_price:
        for item in DataSyncService.sync_mark_prices(source, symbol, intervals, limit=limit):
            if item.get('error'):
                print(f"  mark price {item['interval']}: {item['error']}")
        print("  mark-price series refreshed")

    if daily_refresh:
        DataSyncService.sync_all_configured_sources_daily()
        print("Daily incremental refresh complete.")
    return summary


def seed_from_csv(csv_path, interval, symbol="BTCUSDT", source="Binance", clear_existing=False):
    """Validated CSV import. Returns True on success; False with a message.

    Timestamps must already sit on the interval grid — the historical CSVs
    shipped with the project are off-grid and are rejected on purpose.
    """
    if not os.path.exists(csv_path):
        print(f"CSV file {csv_path} not found at {os.path.abspath(csv_path)}.")
        return False
    try:
        result = DataSyncService.seed_from_csv(csv_path, interval, symbol, source, clear_existing)
    except Exception as exc:
        print(f"⚠️ CSV import rejected: {exc}")
        return False
    print(f"✅ Imported {result['fetched']} {source} {symbol} {interval} candles from {csv_path}.")
    return True


def update_daily_data(symbol="BTCUSDT", intervals=None):
    """Run the same configured multi-source daily refresh as the API."""
    return DataSyncService.sync_all_configured_sources_daily(symbol, intervals)


def seed_to_db(symbol="BTCUSDT", interval="1h", years=6, source="Binance"):
    """Back-compat helper: full-history fetch for one interval."""
    start = (datetime.utcnow() - timedelta(days=years * 365)).strftime("%Y-%m-%d")
    return seed_full_history(source, symbol, [interval], start=start,
                             repair=False, mark_price=False, daily_refresh=False)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Seed market data from a live exchange API (Binance by default) or a validated CSV.",
        epilog="Full-history requests are split into exchange-safe windows, resume from a durable "
               "cursor after an interruption, and upsert every candle (no duplicates).")
    parser.add_argument('--source', default='Binance', help='Binance (default) or Delta')
    parser.add_argument('--symbol', default='BTCUSDT')
    parser.add_argument('--intervals', default=DEFAULT_INTERVALS,
                        help=f"comma-separated intervals (default {DEFAULT_INTERVALS}; 1d = daily candles)")
    parser.add_argument('--start', default=DEFAULT_START, help='start date (default 2020-01-01)')
    parser.add_argument('--end', default=None, help='end date (default: today)')
    parser.add_argument('--limit', type=int, default=1500,
                        help='candles per API request (Binance max 1500, Delta max 2000; default 1500)')
    parser.add_argument('--repair', action=argparse.BooleanOptionalAction, default=True,
                        help='remove duplicate + off-grid candles before fetching (default: on)')
    parser.add_argument('--mark-price', action=argparse.BooleanOptionalAction, default=True,
                        help='also refresh the BTC perpetual mark-price series (default: on)')
    parser.add_argument('--daily-refresh', action=argparse.BooleanOptionalAction, default=True,
                        help='run the multi-source daily incremental sync afterwards (default: on)')
    parser.add_argument('--csv', default=None,
                        help='import this validated CSV instead of fetching from the exchange API')
    args = parser.parse_args(argv)

    symbol = args.symbol.upper()
    intervals = [interval.strip().lower() for interval in args.intervals.split(',') if interval.strip()]
    if not intervals:
        parser.error('--intervals cannot be empty')

    if args.csv:
        if len(intervals) > 1:
            print(f"⚠️ A CSV holds one interval; using {intervals[0]} for {args.csv}.")
        ok = seed_from_csv(args.csv, intervals[0], symbol, args.source)
        raise SystemExit(0 if ok else 1)

    try:
        seed_full_history(args.source, symbol, intervals, args.start, args.end,
                          max(1, args.limit), args.repair, args.mark_price, args.daily_refresh)
    except MarketDataError as exc:
        print(f"⚠️ Seed failed: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
