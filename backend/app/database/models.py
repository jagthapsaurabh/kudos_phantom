from sqlalchemy import (
    create_engine, Column, Integer, Float, String, DateTime, ForeignKey,
    Index, JSON, Boolean, UniqueConstraint, inspect, text, Text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker, relationship
import datetime
import os
import time
from dotenv import load_dotenv

# Load .env BEFORE reading DATABASE_URL so the engine is built against the
# configured backend (e.g. PostgreSQL). This must happen at import time: it is
# the single shared entry point for the API server (main.py), all scripts and
# the seeder. load_dotenv() does not override already-set real env vars.
load_dotenv()

Base = declarative_base()

# ---------------------------------------------------------------------------
# Database location
# ---------------------------------------------------------------------------
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_DB_PATH = os.path.join(_BACKEND_DIR, 'trading_system.db')
DATABASE_URL = os.getenv('DATABASE_URL', f'sqlite:///{_DEFAULT_DB_PATH}')

# How long a SQLite connection waits for a competing writer before giving up
# with "database is locked". The API server, the paper/live traders and the
# seeder all write to the same file, and a pm2 restart briefly runs two
# processes at once, so the driver must wait rather than fail instantly.
SQLITE_BUSY_TIMEOUT_S = float(os.getenv('SQLITE_BUSY_TIMEOUT', '30'))

_IS_SQLITE = DATABASE_URL.startswith('sqlite')
_engine_kwargs = {}
if _IS_SQLITE:
    _engine_kwargs['connect_args'] = {
        'check_same_thread': False,
        # sqlite3's own busy handler; without it the default is 5s.
        'timeout': SQLITE_BUSY_TIMEOUT_S,
    }
engine = create_engine(DATABASE_URL, **_engine_kwargs)

if _IS_SQLITE:
    from sqlalchemy import event as _sa_event

    @_sa_event.listens_for(engine, 'connect')
    def _set_sqlite_pragmas(dbapi_connection, connection_record):  # pragma: no cover - driver hook
        """Make concurrent access survivable on SQLite.

        WAL lets readers run while a writer holds the lock (the rollback
        journal blocks them, which is what turned a slow migration into
        "database is locked" at startup), and busy_timeout makes any
        remaining contention wait instead of raising immediately.
        """
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute(f'PRAGMA busy_timeout={int(SQLITE_BUSY_TIMEOUT_S * 1000)}')
            cursor.execute('PRAGMA synchronous=NORMAL')
        except Exception:
            # A read-only or exotic filesystem may refuse WAL; the connection
            # is still usable, so never fail startup on a pragma.
            pass
        finally:
            cursor.close()

SessionLocal = sessionmaker(bind=engine)


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    # Legacy single-broker fields are retained for existing installations.
    api_key = Column(String, nullable=True)
    api_secret = Column(String, nullable=True)
    # Delta Exchange (India) is the house broker: the BTCUSD perpetual, the
    # deadman switch and the bracket orders are all built around it.
    broker_name = Column(String, default='Delta')
    initial_capital = Column(Float, default=20000.0)
    margin_deployment_pct = Column(Float, default=25.0)
    virtual_balance = Column(Float, default=20000.0)
    role = Column(String, default='client')
    is_active = Column(Integer, default=1)
    can_paper = Column(Integer, default=1)
    can_live = Column(Integer, default=0)
    # Account-level trading defaults.
    # use_mark_price: risk maths (SL/TP/trail/PnL) runs on the exchange mark
    # price of the BTC perpetual; the traded price is recorded alongside it.
    use_mark_price = Column(Integer, default=1)
    # JSON schedule of "skip new trades" windows shared by Backtest, Paper and
    # Live (see app.core.trading_windows). Each mode can override it per run.
    trading_windows_json = Column(String, nullable=True)
    full_name = Column(String, nullable=True)
    mobile = Column(String, nullable=True)
    email = Column(String, nullable=True)
    company = Column(String, nullable=True)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    broker_connections = relationship('BrokerConnection', back_populates='user', cascade='all, delete-orphan')


class BrokerDefinition(Base):
    """A market-data/execution integration configured by an administrator.

    Binance and Delta are installed as built-ins. Additional definitions let
    an admin register another REST broker without changing client accounts.
    The runtime uses the adapter named by ``kind`` and fails safely when a
    custom definition has no adapter yet.
    """
    __tablename__ = 'broker_definitions'
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    kind = Column(String, default='generic')  # binance | delta | generic
    market_data_url = Column(String, nullable=True)
    trading_api_url = Column(String, nullable=True)
    enabled = Column(Integer, default=1)
    is_builtin = Column(Integer, default=0)
    notes = Column(String, nullable=True)
    # ---- Broker rate-limit policy (see app.core.rate_limit) ------------
    # NULL = use the venue default for this definition. Defaults already sit
    # under both exchanges' documented limits: Delta allows 10 000 weight per
    # fixed 5-minute window, Binance 2 400 weight/minute and 1 200 orders/min.
    rate_limit_per_second = Column(Float, nullable=True)
    rate_limit_per_minute = Column(Float, nullable=True)
    quota_per_5min = Column(Float, nullable=True)
    orders_per_minute = Column(Float, nullable=True)
    # ---- Trading defaults ---------------------------------------------
    default_leverage = Column(Integer, nullable=True)
    margin_mode = Column(String, nullable=True)          # isolated | cross
    # Contract specification override, used when the instrument lookup fails.
    contract_value = Column(Float, nullable=True)        # BTC represented by 1 contract/lot
    tick_size = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class BrokerConnection(Base):
    """Per-user credentials. Multiple connections can be active at once."""
    __tablename__ = 'broker_connections'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    broker_code = Column(String, nullable=False, index=True)
    label = Column(String, nullable=True)
    api_key = Column(String, nullable=True)
    api_secret = Column(String, nullable=True)
    passphrase = Column(String, nullable=True)
    is_testnet = Column(Integer, default=0)
    is_active = Column(Integer, default=1)
    # Account details read from the venue when the connection is saved or
    # refreshed — margin mode, leverage, sub-account list. Per connection,
    # because most users attach several sub-accounts and each has its own
    # settings (e.g. one sub-account in cross, another isolated).
    account_settings = Column(Text, nullable=True)      # JSON from BrokerClient.get_account_settings
    account_settings_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    user = relationship('User', back_populates='broker_connections')
    __table_args__ = (UniqueConstraint('user_id', 'broker_code', 'label', name='uq_user_broker_label'),)


class FeeSetting(Base):
    """Admin-controlled fee schedule, expressed in basis points.

    A schedule is selected by broker/data source and execution mode. This
    keeps backtest/paper/live accounting independent and removes the need to
    edit .env when fees change.
    """
    __tablename__ = 'fee_settings'
    id = Column(Integer, primary_key=True)
    broker_code = Column(String, nullable=False, index=True)
    mode = Column(String, nullable=False, index=True)  # backtest | paper | live
    taker_fee_bps = Column(Float, nullable=False, default=5.9)
    maker_fee_bps = Column(Float, nullable=False, default=2.36)
    enabled = Column(Integer, default=1)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    __table_args__ = (UniqueConstraint('broker_code', 'mode', name='uq_fee_broker_mode'),)


class AppSetting(Base):
    """Tiny key/value store for platform-wide admin settings.

    First user: the USD→INR conversion rate every worker prices margin and
    PnL with. A row here beats the ``USD_INR_RATE`` environment variable,
    which in turn beats the built-in default — so the admin can correct the
    rate from the panel without touching the server.
    """
    __tablename__ = 'app_settings'
    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class Klines(Base):
    __tablename__ = 'klines'
    id = Column(Integer, primary_key=True)
    symbol = Column(String, index=True)
    interval = Column(String, index=True)
    source = Column(String, index=True, default='Delta')
    event_time = Column(DateTime, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)  # base-asset volume; required for seeded candles
    # Mark-price OHLC of the same bar (BTC perpetual). NULL until the mark
    # series is seeded for this source — the engine then falls back to the
    # traded OHLC above and reports mark_price_basis = 0.
    mark_open = Column(Float, nullable=True)
    mark_high = Column(Float, nullable=True)
    mark_low = Column(Float, nullable=True)
    mark_close = Column(Float, nullable=True)
    # UNIQUE, not merely indexed. upsert_rows reads-then-writes, so two
    # concurrent seeds (the daily auto-sync racing a manual admin seed) each
    # saw "no existing row" and both inserted the same candle. The database
    # is the only place that race can be settled; the constraint turns a
    # silent duplicate into an error the upsert can catch and retry as an
    # update.
    __table_args__ = (
        Index('ix_source_symbol_interval_time', 'source', 'symbol', 'interval', 'event_time'),
        UniqueConstraint('source', 'symbol', 'interval', 'event_time',
                         name='uq_klines_series_time'),
    )


class MarketTick(Base):
    """One live quote from a venue stream or REST poll.

    Paper and live workers already consume ticks for exits. Storing every
    quote means the same series can be replayed, resampled into candles, or
    inspected later — the last-N klines table is too coarse for that.
    """
    __tablename__ = 'market_ticks'
    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False, index=True)
    symbol = Column(String, nullable=False, index=True)
    event_time = Column(DateTime, nullable=False, index=True)
    received_at = Column(DateTime, nullable=True)
    mark_price = Column(Float, nullable=True)
    last_price = Column(Float, nullable=True)
    index_price = Column(Float, nullable=True)
    bid = Column(Float, nullable=True)
    ask = Column(Float, nullable=True)
    feed_kind = Column(String, nullable=True)  # websocket | rest
    __table_args__ = (
        Index('ix_market_ticks_source_symbol_time', 'source', 'symbol', 'event_time'),
    )


class MarketDataSeedProgress(Base):
    """Durable cursor for a bounded historical market-data seed.

    A row is one requested source/definition, symbol, interval and date
    range. The cursor is advanced in the same database transaction as the
    candles from the completed window, so a restart repeats at most the
    in-flight request rather than the already committed history.
    """
    __tablename__ = 'market_data_seed_progress'
    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False, index=True)
    definition_key = Column(String, nullable=False, default='', index=True)
    symbol = Column(String, nullable=False, index=True)
    interval = Column(String, nullable=False, index=True)
    requested_start = Column(DateTime, nullable=False)
    requested_end = Column(DateTime, nullable=False)
    next_start = Column(DateTime, nullable=False)
    page_limit = Column(Integer, nullable=False)
    interval_seconds = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default='running', index=True)  # running | failed | completed
    pages = Column(Integer, nullable=False, default=0)
    empty_pages = Column(Integer, nullable=False, default=0)
    fetched = Column(Integer, nullable=False, default=0)
    inserted = Column(Integer, nullable=False, default=0)
    updated = Column(Integer, nullable=False, default=0)
    last_error = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    __table_args__ = (
        UniqueConstraint(
            'source', 'definition_key', 'symbol', 'interval',
            'requested_start', 'requested_end',
            name='uq_market_data_seed_progress_range',
        ),
        Index(
            'ix_market_data_seed_progress_lookup',
            'source', 'definition_key', 'symbol', 'interval',
        ),
    )


class CustomStrategy(Base):
    __tablename__ = 'custom_strategies'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    name = Column(String, nullable=False)
    rules = Column(JSON, nullable=False)
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class BacktestRun(Base):
    __tablename__ = 'backtest_runs'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    name = Column(String)
    # Keep the selected strategy alongside the parameter snapshot so opening a
    # historical run can restore both the form values and the strategy choice.
    strategy_id = Column(String, default='PhantomV2')
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    config_json = Column(String)
    initial_capital = Column(Float, nullable=True)
    data_source = Column(String, default='Delta')
    fee_mode = Column(String, default='backtest')
    taker_fee_bps = Column(Float, nullable=True)
    maker_fee_bps = Column(Float, nullable=True)
    # BTC perpetual pricing: 1 = stops/targets/PnL computed on mark price.
    use_mark_price = Column(Integer, default=1)
    # 1 when the run skipped new entries during configured trading windows.
    trading_windows_enabled = Column(Integer, default=0)
    blocked_entries = Column(Integer, default=0)
    final_equity = Column(Float)
    total_trades = Column(Integer)
    win_rate = Column(Float)
    profit_factor = Column(Float)
    sharpe_ratio = Column(Float)
    max_drawdown = Column(Float)
    roi = Column(Float)
    equity_curve = Column(JSON, nullable=True)
    rejected_reasons = Column(String, nullable=True)
    trades = relationship('Trade', back_populates='run', cascade='all, delete-orphan')


class Trade(Base):
    __tablename__ = 'trades'
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey('backtest_runs.id'))
    entry_time = Column(DateTime)
    exit_time = Column(DateTime)
    direction = Column(Integer)
    entry_price = Column(Float)
    exit_price = Column(Float)
    # BTC perpetual: the fill/traded price and the exchange mark price are both
    # stored. `entry_price`/`exit_price` are the pricing basis actually used for
    # the PnL maths (mark price when `mark_price_basis` = 1), and the other pair
    # keeps the real execution price so a trade can always be reconciled.
    entry_trade_price = Column(Float, nullable=True)
    exit_trade_price = Column(Float, nullable=True)
    entry_mark_price = Column(Float, nullable=True)
    exit_mark_price = Column(Float, nullable=True)
    mark_price_basis = Column(Integer, nullable=True)
    lots = Column(Float)
    margin = Column(Float)
    notional = Column(Float)
    net_pnl = Column(Float)
    fees = Column(Float)
    exit_reason = Column(String)
    equity_after = Column(Float)
    drawdown = Column(Float)
    hold_bars = Column(Integer)
    signal_candle_time = Column(DateTime, nullable=True)
    setup = Column(String, nullable=True)
    candle_type = Column(String, nullable=True)  # legacy alias: signal-candle colour
    # Candle-level detail the client asked for: which candle produced the
    # signal, which candle the entry actually filled on, and the colour of
    # each (GREEN / RED / DOJI). The entry fills on the candle AFTER the
    # signal, so these are deliberately separate columns.
    signal_candle_type = Column(String, nullable=True)
    entry_candle_time = Column(DateTime, nullable=True)
    entry_candle_type = Column(String, nullable=True)
    exit_candle_type = Column(String, nullable=True)
    # Every entry condition spelled out (value vs threshold vs PASS/FAIL),
    # one per line, and the exact rule that closed the trade.
    entry_conditions_detail = Column(String, nullable=True)
    exit_detail = Column(String, nullable=True)
    cond_trend_ok = Column(Integer, nullable=True)
    cond_di_ok = Column(Integer, nullable=True)
    trend_4h = Column(String, nullable=True)
    rsi14 = Column(Float, nullable=True)
    macd_hist = Column(Float, nullable=True)
    adx = Column(Float, nullable=True)
    atr14 = Column(Float, nullable=True)
    ema50_1h = Column(Float, nullable=True)
    ema50_4h = Column(Float, nullable=True)
    cond_adx_ok = Column(Integer, nullable=True)
    cond_macd_hist_ok = Column(Integer, nullable=True)
    cond_atr_regime_ok = Column(Integer, nullable=True)
    cond_rsi_ok = Column(Integer, nullable=True)
    cond_macd_confirm_ok = Column(Integer, nullable=True)
    gross_pnl = Column(Float, nullable=True)
    sl = Column(Float, nullable=True)
    tp = Column(Float, nullable=True)
    # Stop plan at entry vs the levels in force at exit (shows breakeven /
    # trailing moves) — mirrors what the paper-trade closed list already keeps.
    sl_entry = Column(Float, nullable=True)
    trail_stop = Column(Float, nullable=True)
    atr_at_entry = Column(Float, nullable=True)
    peak_price = Column(Float, nullable=True)
    entry_dd_pct = Column(Float, nullable=True)
    margin_pct_used = Column(Float, nullable=True)
    equity_at_entry = Column(Float, nullable=True)
    run = relationship('BacktestRun', back_populates='trades')


class PaperSession(Base):
    """Persistent record of one trading session — paper OR live.

    Workers (``PaperTradeService`` / ``LiveTradeService``) exist only in
    process memory, so stopping an instance — or restarting the server — used
    to throw its result away. Every instance now writes a row here while it
    runs: the parameters it started with, its equity curve, closed trades and
    log buffer. Stopping a session keeps the row so the client can review the
    outcome in depth afterwards; only an explicit delete removes it.

    The table keeps its original name for backwards compatibility, but
    ``mode`` now separates the two kinds of session.

    ``status`` is one of:
      * ``running``     — worker is live in this process
      * ``stopped``     — the user stopped or deleted the live session
      * ``interrupted`` — the server restarted while it was running
      * ``failed``      — the worker crashed repeatedly and gave up
    """
    __tablename__ = 'paper_sessions'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    instance_key = Column(String, unique=True, nullable=False, index=True)
    # 'paper' | 'live' — a live session is recorded exactly like a paper one
    # so stopping a live instance no longer discards its trades and logs.
    mode = Column(String, default='paper', index=True)
    strategy_id = Column(String, nullable=True)
    strategy_name = Column(String, nullable=True)
    symbol = Column(String, default='BTCUSDT')
    data_source = Column(String, default='Delta')
    broker_name = Column(String, nullable=True)
    status = Column(String, default='running', index=True)
    # Run parameters, snapshotted at start so the result can be reproduced.
    config_json = Column(String, nullable=True)
    initial_capital = Column(Float, nullable=True)
    final_equity = Column(Float, nullable=True)
    net_pnl = Column(Float, nullable=True)
    roi = Column(Float, nullable=True)
    peak_equity = Column(Float, nullable=True)
    max_drawdown_pct = Column(Float, nullable=True)
    margin_pct = Column(Float, nullable=True)
    leverage = Column(Integer, nullable=True)
    taker_fee_bps = Column(Float, nullable=True)
    maker_fee_bps = Column(Float, nullable=True)
    conversion_rate = Column(Float, nullable=True)
    # BTC perpetual pricing: 1 = SL/TP/trail/PnL computed on the mark price.
    use_mark_price = Column(Integer, nullable=True)
    # "Skip new trades" schedule in force for this session (JSON).
    trading_windows_json = Column(String, nullable=True)
    blocked_entries = Column(Integer, default=0)
    # Result roll-up over the closed trades.
    closed_trade_count = Column(Integer, default=0)
    win_rate = Column(Float, nullable=True)
    profit_factor = Column(Float, nullable=True)
    total_fees = Column(Float, nullable=True)
    last_price = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    started_at = Column(DateTime, default=datetime.datetime.utcnow)
    stopped_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                       onupdate=datetime.datetime.utcnow)
    last_checked = Column(DateTime, nullable=True)
    # Payloads: [{"ts": ISO-IST, "equity": float}], closed-trade dicts, log
    # lines and the open positions still held when the session ended.
    equity_curve = Column(JSON, nullable=True)
    closed_trades = Column(JSON, nullable=True)
    open_positions = Column(JSON, nullable=True)
    logs = Column(JSON, nullable=True)
    # Why the session is no longer running, plus the last error the loop saw.
    # Without these an ended session could only say "interrupted", which tells
    # the user nothing actionable.
    stop_reason = Column(String, nullable=True)
    last_error = Column(String, nullable=True)
    restarts = Column(Integer, default=0)
    # Enough of the run context to rebuild the worker after a server restart.
    price_feed = Column(String, nullable=True)
    tick_interval = Column(Float, nullable=True)
    testnet = Column(Integer, default=0)
    connection_id = Column(Integer, nullable=True)
    account_label = Column(String, nullable=True)
    # 1 = the user asked for this session to keep running across restarts.
    auto_resume = Column(Integer, default=1)


class BrokerOrder(Base):
    """One order sent to a live broker, mirrored locally.

    The exchange is the source of truth, but keeping a local row means the
    terminal can show an order the moment it is sent, keep a durable audit
    trail after the exchange drops it from its open-order window, and tie a
    fill back to the strategy instance that opened it.
    """

    __tablename__ = 'broker_orders'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    broker_code = Column(String, nullable=False, index=True)
    connection_id = Column(Integer, nullable=True)
    symbol = Column(String, nullable=False, index=True)
    # Our own id, sent to the exchange so the two can always be matched.
    client_order_id = Column(String, nullable=True, index=True)
    broker_order_id = Column(String, nullable=True, index=True)
    # Bracket legs point back at the entry order they protect.
    parent_order_id = Column(String, nullable=True)
    side = Column(String, nullable=False)                 # buy | sell
    order_type = Column(String, nullable=False)           # market | limit | stop_market | …
    leg = Column(String, nullable=True)                   # entry | stop_loss | take_profit
    size = Column(Float, nullable=True)                   # venue units (contracts / BTC lots)
    qty_btc = Column(Float, nullable=True)                # same size expressed in BTC
    price = Column(Float, nullable=True)
    stop_price = Column(Float, nullable=True)
    trigger_method = Column(String, nullable=True)        # mark_price | last_traded_price
    reduce_only = Column(Integer, default=0)
    post_only = Column(Integer, default=0)
    time_in_force = Column(String, nullable=True)
    status = Column(String, default='pending', index=True)  # pending|open|filled|cancelled|rejected
    filled_size = Column(Float, nullable=True)
    avg_fill_price = Column(Float, nullable=True)
    fee = Column(Float, nullable=True)
    realized_pnl = Column(Float, nullable=True)
    # 'manual' (terminal ticket) or 'strategy' (a live-trade instance).
    source = Column(String, default='manual')
    instance_key = Column(String, nullable=True, index=True)
    error = Column(String, nullable=True)
    raw = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)


class BrokerFill(Base):
    """One execution (fill) received from a live broker."""

    __tablename__ = 'broker_fills'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    broker_code = Column(String, nullable=False, index=True)
    symbol = Column(String, nullable=False, index=True)
    client_order_id = Column(String, nullable=True, index=True)
    broker_order_id = Column(String, nullable=True, index=True)
    broker_trade_id = Column(String, nullable=True, index=True)
    side = Column(String, nullable=True)
    size = Column(Float, nullable=True)          # venue units
    qty_btc = Column(Float, nullable=True)
    price = Column(Float, nullable=True)
    fee = Column(Float, nullable=True)
    role = Column(String, nullable=True)         # maker | taker
    realized_pnl = Column(Float, nullable=True)
    source = Column(String, default='manual')
    instance_key = Column(String, nullable=True)
    filled_at = Column(DateTime, nullable=True)
    raw = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    __table_args__ = (
        UniqueConstraint('user_id', 'broker_code', 'broker_trade_id',
                         name='uq_broker_fill_trade'),
    )


def _seed_reference_data():
    """Create built-in providers and .env-compatible initial fee rows once."""
    db = SessionLocal()
    try:
        # Delta first: it is the default venue, and this order drives the
        # broker dropdowns the user sees.
        builtins = [
            ('Delta', 'Delta Exchange', 'delta', 'https://api.india.delta.exchange', 'https://api.india.delta.exchange', 'Built-in Delta Exchange (India) adapter'),
            ('Binance', 'Binance Futures', 'binance', 'https://fapi.binance.com', 'https://fapi.binance.com', 'Built-in Binance Futures adapter'),
            # Delta Exchange Global (www/global.delta.exchange) keeps a SEPARATE
            # key store from India (docs-global.delta.exchange). A key created on
            # the Global site is InvalidApiKey on the India host and vice versa,
            # so it is its own integration, not a URL override of `Delta`.
            ('DeltaGlobal', 'Delta Exchange Global', 'delta', 'https://api.delta.exchange', 'https://api.delta.exchange', 'Built-in Delta Exchange (Global) adapter'),
        ]
        for code, name, kind, market, trading, notes in builtins:
            row = db.query(BrokerDefinition).filter(BrokerDefinition.code == code).first()
            if not row:
                db.add(BrokerDefinition(code=code, name=name, kind=kind, market_data_url=market,
                                        trading_api_url=trading, enabled=1, is_builtin=1, notes=notes))
        taker = float(os.getenv('TAKER_FEE_BPS', '5.9'))
        maker = float(os.getenv('MAKER_FEE_BPS', '2.36'))
        for code in ('Delta', 'Binance', 'DeltaGlobal'):
            for mode in ('backtest', 'paper', 'live'):
                if not db.query(FeeSetting).filter_by(broker_code=code, mode=mode).first():
                    db.add(FeeSetting(broker_code=code, mode=mode, taker_fee_bps=taker,
                                      maker_fee_bps=maker, enabled=1))
        db.commit()
    finally:
        db.close()


def migrate_db():
    """Lightweight additive migration for SQLite and compatible databases."""
    # Everything below runs on ONE connection. Reflecting through
    # ``inspect(engine)`` while this transaction holds a write lock opens a
    # second connection that then waits on the first one — a self-deadlock
    # that surfaced as "database is locked: PRAGMA main.table_info(...)" and
    # killed startup.
    with engine.begin() as conn:
        inspector = inspect(conn)

        def _columns(table_name):
            inspector.clear_cache()
            return {col['name'] for col in inspector.get_columns(table_name)}

        def _has_table(table_name):
            inspector.clear_cache()
            return inspector.has_table(table_name)

        for table in Base.metadata.sorted_tables:
            if not _has_table(table.name):
                continue
            existing = _columns(table.name)
            for col in table.columns:
                if col.name not in existing and col.name != 'id':
                    coltype = col.type.compile(engine.dialect)
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {coltype}'))
            for index in table.indexes:
                try:
                    index.create(bind=conn, checkfirst=True)
                except Exception:
                    pass
        # The klines uniqueness constraint is new. Any duplicate candles an
        # earlier concurrent seed already wrote must go before the unique
        # index can be built, or index creation fails and the app won't
        # start. Keep the highest id per (source, symbol, interval, time) —
        # the most recently written copy.
        if _has_table('klines'):
            try:
                removed = conn.execute(text(
                    'DELETE FROM klines WHERE id NOT IN ('
                    '  SELECT MAX(id) FROM klines'
                    '  GROUP BY source, symbol, interval, event_time)'
                )).rowcount
                if removed:
                    print(f'[migrate] removed {removed} duplicate kline rows')
                # A table-level UniqueConstraint only exists on tables created
                # from the model. SQLite cannot add one with ALTER TABLE, so an
                # upgraded install needs the equivalent UNIQUE INDEX or the
                # duplicate-candle race stays open there.
                conn.execute(text(
                    'CREATE UNIQUE INDEX IF NOT EXISTS uq_klines_series_time '
                    'ON klines (source, symbol, interval, event_time)'
                ))
            except Exception as exc:
                print(f'[migrate] kline dedupe skipped: {exc}')

        # Sessions predate the paper/live split: everything already stored was
        # a paper run, so stamp it rather than leaving the discriminator NULL.
        if _has_table('paper_sessions'):
            session_cols = _columns('paper_sessions')
            if 'mode' in session_cols:
                conn.execute(text("UPDATE paper_sessions SET mode='paper' WHERE mode IS NULL"))

        if _has_table('users'):
            user_cols = _columns('users')
            if 'role' in user_cols:
                conn.execute(text("UPDATE users SET role='client' WHERE role IS NULL"))
                conn.execute(text("UPDATE users SET role='admin' WHERE username='admin'"))
            if 'is_active' in user_cols:
                conn.execute(text('UPDATE users SET is_active=1 WHERE is_active IS NULL'))
            if 'can_paper' in user_cols:
                conn.execute(text('UPDATE users SET can_paper=1 WHERE can_paper IS NULL'))
            if 'can_live' in user_cols:
                conn.execute(text('UPDATE users SET can_live=0 WHERE can_live IS NULL'))
        if _has_table('backtest_runs'):
            run_cols = _columns('backtest_runs')
            if 'strategy_id' in run_cols:
                conn.execute(text("UPDATE backtest_runs SET strategy_id='PhantomV2' WHERE strategy_id IS NULL OR strategy_id=''"))
        if _has_table('klines'):
            kcols = _columns('klines')
            if 'source' in kcols:
                # Legacy unlabelled candles predate multi-venue support and
                # came from Binance. They are stamped Binance for accuracy —
                # Delta is the default for NEW data, but relabelling existing
                # rows would attribute one venue's prices to another.
                conn.execute(text("UPDATE klines SET source='Binance' WHERE source IS NULL OR source=''"))


def init_db(max_attempts: int = 5, retry_delay_s: float = 2.0):
    """Create/upgrade the schema, retrying while another process holds the DB.

    On a pm2 restart the outgoing worker can still be flushing writes when the
    new one boots. Failing startup there put the process into a restart loop;
    waiting a couple of seconds and trying again clears it.
    """
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            Base.metadata.create_all(engine)
            migrate_db()
            _seed_reference_data()
            return
        except OperationalError as exc:
            if 'database is locked' not in str(exc).lower():
                raise
            last_exc = exc
            engine.dispose()
            if attempt < max_attempts:
                print(f'[init_db] database is locked (attempt {attempt}/{max_attempts}); '
                      f'retrying in {retry_delay_s}s')
                time.sleep(retry_delay_s)
    raise last_exc
