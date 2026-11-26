# Why a paper session can show trades that the backtest does not

Both modes run the same `StrategyService` entry logic, but they used to read
**different candles**. This is the first thing to check when a two-month paper
run has trades and a two-month backtest of the same saved strategy has none.

## Root cause that was fixed

* The seeder writes the BTC perpetual under the **venue** symbol: `BTCUSD` on
  Delta, `BTCUSDT` on Binance (`DataSyncService.venue_symbol`). The paper and
  live workers already resolve that symbol. The backtest engine did not: it
  asked the database for `BTCUSDT` regardless of source, found no Delta rows,
  and silently fell back to a single public-API page (1,000 hourly candles ≈
  41 days) or to no data at all.
* `BacktestEngine._get_data_from_db()` now resolves the venue symbol, accepts
  legacy `BTCUSDT` rows, matches source spellings case-insensitively, and
  pages the venue fallback over the **whole** date range.
* A date-only `end_date` is inclusive through that day in the backtest, as it
  already was in the seeder and `GET /klines`.
* `GET /klines` (chart overlay) resolves the venue symbol too, so the chart,
  backtest and paper session can be compared on the same candles.

## Daily seeder / gap audit

The daily job (`sync_all_configured_sources_daily`, started at app boot and
every 24 hours) also had data-identity problems, now fixed:

* Full-history seeds wrote Delta candles as `BTCUSDT`, the daily updater as
  `BTCUSD` — two series that never met. Every seed/mark/repair path now writes
  one canonical venue symbol, and existing installs are copied at startup
  (legacy rows are kept as a backup — nothing is deleted — and mark prices are
  preserved).
* The job measured "last candle" on the raw ticker, so on Delta it always
  started from a fixed recent window rather than its own last stored candle.
  It now starts from the **oldest missing candle** in a 90-day lookback and
  pages to now, closing interior holes as well as the tail.
* Mark prices were fetched but joined on the raw ticker, so they never landed
  on the daily series. The job now resumes from the oldest candle without a
  mark and stops asking once the recent window is complete.
* `GET /admin/market-data/status?health=1` (Admin → data health) reports
  `gap_count` and `largest_gap_bars` next to duplicates/off-grid rows, so a
  failed window is visible without opening the database.

A failed exchange window is recorded in the sync summary and **stops** that
interval; the next daily run starts from the hole again. This is deliberate —
continuing would move "latest" past the gap and hide it forever.

## How to verify a run

Every backtest result now carries `data_info`:

```jsonc
"data_info": {
  "requested_symbol": "BTCUSDT",
  "source": "Delta",
  "1h": {"origin": "seeded", "symbol": "BTCUSD", "rows": 1440,
         "first": "2026-09-01T00:00:00", "last": "2026-10-31T23:00:00"},
  "4h": { "origin": "seeded", "symbol": "BTCUSD", "rows": 360, ... }
}
```

The same object is stored on the run and shown next to the Backtest Summary.
Check that:

1. `origin` is `seeded` (not `venue-api`) — a venue fallback means the seed
   did not cover the range;
2. `symbol` is the venue's symbol and `rows` roughly matches the number of
   candles in the window (24 per day for 1h, 6 for 4h);
3. **both** 1h and 4h intervals are seeded for the selected source;
4. mark prices were included in the seed when "Priced on MARK price" is on.

If the candles are missing, `POST /admin/market-data/seed` with the same
source and date range, then rerun the backtest. An empty backtest now raises a
message naming the missing interval, symbol and source instead of reporting
zero trades silently.

## What still differs by design

Even with identical candles, a paper trade and a backtest trade are not
byte-for-byte the same event:

| | Paper Trade | Backtest |
| :--- | :--- | :--- |
| Signal candle | newest (possibly still forming) 1h candle | closed candles in the range |
| Fill | same candle, current mark/traded price | **next candle's open** |
| Stop/target check | every 60 s tick or live-price tick (fast exits) | candle high/low plus close |
| Price basis | live mark price when available | seeded mark OHLC, traded fallback per bar |
| Drawdown throttle | not applied | applied by the engine |

So compare **signal candles and conditions**, not exact fill timestamps. A run
with `origin: seeded`, both intervals populated and still no trades is a
strategy/parameter question (thresholds, trading windows, validator price
drift) rather than a market-data problem.
