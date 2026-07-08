from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, ForeignKey, Index, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import datetime

Base = declarative_base()

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
    final_equity = Column(Float)
    total_trades = Column(Integer)
    win_rate = Column(Float)
    profit_factor = Column(Float)
    sharpe_ratio = Column(Float)
    max_drawdown = Column(Float)
    roi = Column(Float)
    equity_curve = Column(JSON, nullable=True)
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
    run = relationship("BacktestRun", back_populates="trades")

engine = create_engine('sqlite:///trading_system.db', connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

def init_db():
    Base.metadata.create_all(engine)
