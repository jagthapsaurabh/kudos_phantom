from sqlalchemy import (
    create_engine, Column, Integer, Float, String, DateTime, ForeignKey,
    Index, JSON, Boolean, UniqueConstraint, inspect, text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import datetime
import os
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
_engine_kwargs = {}
if DATABASE_URL.startswith('sqlite'):
    _engine_kwargs['connect_args'] = {'check_same_thread': False}
engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine)


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    # Legacy single-broker fields are retained for existing installations.
    api_key = Column(String, nullable=True)
    api_secret = Column(String, nullable=True)
    broker_name = Column(String, default='Binance')
    initial_capital = Column(Float, default=20000.0)
    margin_deployment_pct = Column(Float, default=25.0)
    virtual_balance = Column(Float, default=20000.0)
    role = Column(String, default='client')
    is_active = Column(Integer, default=1)
    can_paper = Column(Integer, default=1)
    can_live = Column(Integer, default=0)
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


class Klines(Base):
    __tablename__ = 'klines'
    id = Column(Integer, primary_key=True)
    symbol = Column(String, index=True)
    interval = Column(String, index=True)
    source = Column(String, index=True, default='Binance')
    event_time = Column(DateTime, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)  # base-asset volume; required for seeded candles
    __table_args__ = (Index('ix_source_symbol_interval_time', 'source', 'symbol', 'interval', 'event_time'),)


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
    data_source = Column(String, default='Binance')
    fee_mode = Column(String, default='backtest')
    taker_fee_bps = Column(Float, nullable=True)
    maker_fee_bps = Column(Float, nullable=True)
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
    candle_type = Column(String, nullable=True)
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
    entry_dd_pct = Column(Float, nullable=True)
    margin_pct_used = Column(Float, nullable=True)
    equity_at_entry = Column(Float, nullable=True)
    run = relationship('BacktestRun', back_populates='trades')


def _seed_reference_data():
    """Create built-in providers and .env-compatible initial fee rows once."""
    db = SessionLocal()
    try:
        builtins = [
            ('Binance', 'Binance Futures', 'binance', 'https://fapi.binance.com', 'https://fapi.binance.com', 'Built-in Binance Futures adapter'),
            ('Delta', 'Delta Exchange', 'delta', 'https://api.india.delta.exchange', 'https://api.india.delta.exchange', 'Built-in Delta Exchange adapter'),
        ]
        for code, name, kind, market, trading, notes in builtins:
            row = db.query(BrokerDefinition).filter(BrokerDefinition.code == code).first()
            if not row:
                db.add(BrokerDefinition(code=code, name=name, kind=kind, market_data_url=market,
                                        trading_api_url=trading, enabled=1, is_builtin=1, notes=notes))
        taker = float(os.getenv('TAKER_FEE_BPS', '5.9'))
        maker = float(os.getenv('MAKER_FEE_BPS', '2.36'))
        for code in ('Binance', 'Delta'):
            for mode in ('backtest', 'paper', 'live'):
                if not db.query(FeeSetting).filter_by(broker_code=code, mode=mode).first():
                    db.add(FeeSetting(broker_code=code, mode=mode, taker_fee_bps=taker,
                                      maker_fee_bps=maker, enabled=1))
        db.commit()
    finally:
        db.close()


def migrate_db():
    """Lightweight additive migration for SQLite and compatible databases."""
    with engine.begin() as conn:
        inspector = inspect(engine)
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {col['name'] for col in inspect(engine).get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing and col.name != 'id':
                    coltype = col.type.compile(engine.dialect)
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {coltype}'))
            for index in table.indexes:
                try:
                    index.create(bind=conn, checkfirst=True)
                except Exception:
                    pass
        if inspector.has_table('users'):
            user_cols = {col['name'] for col in inspect(engine).get_columns('users')}
            if 'role' in user_cols:
                conn.execute(text("UPDATE users SET role='client' WHERE role IS NULL"))
                conn.execute(text("UPDATE users SET role='admin' WHERE username='admin'"))
            if 'is_active' in user_cols:
                conn.execute(text('UPDATE users SET is_active=1 WHERE is_active IS NULL'))
            if 'can_paper' in user_cols:
                conn.execute(text('UPDATE users SET can_paper=1 WHERE can_paper IS NULL'))
            if 'can_live' in user_cols:
                conn.execute(text('UPDATE users SET can_live=0 WHERE can_live IS NULL'))
        if inspector.has_table('backtest_runs'):
            run_cols = {col['name'] for col in inspect(engine).get_columns('backtest_runs')}
            if 'strategy_id' in run_cols:
                conn.execute(text("UPDATE backtest_runs SET strategy_id='PhantomV2' WHERE strategy_id IS NULL OR strategy_id=''"))
        if inspector.has_table('klines'):
            kcols = {col['name'] for col in inspect(engine).get_columns('klines')}
            if 'source' in kcols:
                conn.execute(text("UPDATE klines SET source='Binance' WHERE source IS NULL OR source=''"))


def init_db():
    Base.metadata.create_all(engine)
    migrate_db()
    _seed_reference_data()
