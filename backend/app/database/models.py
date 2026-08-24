from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, ForeignKey, Index, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import datetime
import os

Base = declarative_base()

# ---------------------------------------------------------------------------
# Database location
# ---------------------------------------------------------------------------
# Anchor the SQLite file to the backend directory so the app uses the SAME
# database regardless of the process working directory. Override with the
# DATABASE_URL environment variable (e.g. postgres://... or sqlite:///...).
# __file__ = backend/app/database/models.py -> go up 3 levels to backend/
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_DB_PATH = os.path.join(_BACKEND_DIR, 'trading_system.db')
DATABASE_URL = os.getenv('DATABASE_URL', f'sqlite:///{_DEFAULT_DB_PATH}')

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    api_key = Column(String, nullable=True)
    api_secret = Column(String, nullable=True)
    broker_name = Column(String, default="Binance")
    initial_capital = Column(Float, default=20000.0)
    margin_deployment_pct = Column(Float, default=25.0)
    virtual_balance = Column(Float, default=20000.0)
    # PHANTOM v3: roles & permissions (admin manages clients)
    role = Column(String, default="client")          # 'admin' | 'client'
    is_active = Column(Integer, default=1)           # 0 = login blocked
    can_paper = Column(Integer, default=1)           # paper-trading allowed
    can_live = Column(Integer, default=0)            # live trading requires admin grant
    # ---- Client profile / basic info (v3.2) ----
    full_name = Column(String, nullable=True)        # e.g. "Rahul Sharma"
    mobile = Column(String, nullable=True)           # contact mobile number
    email = Column(String, nullable=True)            # contact email
    company = Column(String, nullable=True)          # firm / organisation
    notes = Column(String, nullable=True)            # admin notes
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class Klines(Base):
    __tablename__ = 'klines'
    id = Column(Integer, primary_key=True)
    symbol = Column(String, index=True)
    interval = Column(String, index=True)
    event_time = Column(DateTime, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    __table_args__ = (Index('ix_symbol_interval_time', 'symbol', 'interval', 'event_time'),)

class CustomStrategy(Base):
    __tablename__ = 'custom_strategies'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    name = Column(String, nullable=False)
    # rules stores a list of rules: [{"field": "close", "op": "gt", "value": "ema50", "timeframe": "4h"}, ...]
    rules = Column(JSON, nullable=False) 
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class BacktestRun(Base):
    __tablename__ = 'backtest_runs'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    name = Column(String)
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    config_json = Column(String)
    initial_capital = Column(Float, nullable=True)   # capital the run started with (default = user's/admin-set)
    final_equity = Column(Float)
    total_trades = Column(Integer)
    win_rate = Column(Float)
    profit_factor = Column(Float)
    sharpe_ratio = Column(Float)
    max_drawdown = Column(Float)
    roi = Column(Float)
    equity_curve = Column(JSON, nullable=True)
    rejected_reasons = Column(String, nullable=True)  # JSON: signal rejection counts
    trades = relationship("Trade", back_populates="run")

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
    # ---- PHANTOM v3: full entry-condition snapshot (trade + condition log) ----
    signal_candle_time = Column(DateTime, nullable=True)   # candle the conditions fired on
    setup = Column(String, nullable=True)                  # REVERSAL | MOMENTUM
    candle_type = Column(String, nullable=True)            # GREEN | RED | DOJI
    trend_4h = Column(String, nullable=True)               # UP | DOWN
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
    run = relationship("BacktestRun", back_populates="trades")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

def migrate_db():
    """Lightweight SQLite migration: add any ORM-mapped column that is missing
    from the live database (covers legacy schema drift and the PHANTOM v3
    trade-condition log). Never drops or alters existing columns."""
    from sqlalchemy import text
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table.name})"))}
            if not existing:
                continue  # table will be created by create_all
            for col in table.columns:
                if col.name not in existing and col.name != 'id':
                    coltype = col.type.compile(engine.dialect)
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {coltype}"))
        # Backfill role/permission columns on pre-existing user rows
        user_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(users)"))}
        if 'role' in user_cols:
            conn.execute(text("UPDATE users SET role='client' WHERE role IS NULL"))
            conn.execute(text("UPDATE users SET role='admin' WHERE username='admin'"))
        if 'is_active' in user_cols:
            conn.execute(text("UPDATE users SET is_active=1 WHERE is_active IS NULL"))
        if 'can_paper' in user_cols:
            conn.execute(text("UPDATE users SET can_paper=1 WHERE can_paper IS NULL"))
        if 'can_live' in user_cols:
            conn.execute(text("UPDATE users SET can_live=0 WHERE can_live IS NULL"))


def init_db():
    Base.metadata.create_all(engine)
    migrate_db()
