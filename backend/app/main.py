from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, status, Body, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
import pandas as pd
import json
import logging
from typing import Any, Optional, List, Dict, Union
import os
from dotenv import load_dotenv
from .core.engine import BacktestEngine
from .core.strategy import PhantomV2Config, StrategyService
from .core.mark_price import MarkPriceService, perpetual_symbol, contract_label
from .core.trading_windows import (
    TradingWindowConfig, TradingWindowGuard, default_config as default_window_config,
)
from .core.error_handling import (
    PhantomError, ValidationError as PhantomValidationError, AuthenticationError, AuthorizationError,
    NotFoundError, ConflictError, RateLimitError, BrokerError,
    MarketDataError as PhantomMarketDataError,
    error_response, classify_broker_error, map_db_error,
    validate_leverage, validate_size, validate_price, validate_symbol,
    validate_broker_code, validate_margin_mode, validate_order_type, validate_side,
    register_exception_handlers, logger as phantom_logger,
)
from .core.secrets import encrypt_secret, decrypt_secret, SecretDecryptionError
from .services.paper_trader import PaperTradeService, _to_ist
from .services import paper_history
from .services.live_trader import LiveTradeService, COORDINATOR
from .services.data_sync import DataSyncService
from .services.broker_client import BrokerClient
from .database.models import (
    init_db, SessionLocal, User, CustomStrategy, BacktestRun, Trade, Klines,
    MarketTick, MarketDataSeedProgress, BrokerDefinition, BrokerConnection, FeeSetting,
    PaperSession,
)
import bcrypt
from passlib.context import CryptContext
import asyncio
import threading
from datetime import datetime, timezone, timedelta
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

# Load environment variables
load_dotenv()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

app = FastAPI(
    title="PHANTOM v2.5 Trading Tool",
    description="BTC perpetual live trader: Delta Exchange India + Binance Futures. Phantom V3 strategy with bracket orders, heartbeat, and mark-price risk.",
    version="2.5.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register global exception handlers for consistent error envelope
register_exception_handlers(app)

# Request logging middleware for error context
@app.middleware("http")
async def log_requests(request: Request, call_next):
    try:
        response = await call_next(request)
        if response.status_code >= 400:
            phantom_logger.warning(f"{request.method} {request.url.path} -> {response.status_code}")
        return response
    except Exception as exc:
        phantom_logger.error(f"Unhandled in {request.method} {request.url.path}: {exc}")
        raise

import uuid

# Global state
paper_trade_instances: Dict[str, PaperTradeService] = {}
live_trade_instances: Dict[str, LiveTradeService] = {}


def _utc_ts(dt):
    """Return a UTC UNIX timestamp (seconds) from a datetime, regardless of
    whether the datetime is timezone-aware or naive (assumed UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()

class StrategyParams(PhantomV2Config):
    pass

class StrategyCreate(BaseModel):
    name: str
    params: StrategyParams

class CustomStrategyCreate(BaseModel):
    name: str
    rules: Optional[List[Dict]] = None
    params: Optional[Dict] = None


class BacktestRequest(BaseModel):
    params: StrategyParams
    strategy_id: str = "PhantomV2"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    strategy_name: str = "Default Run"
    # Capital to run the backtest with. If omitted, the user's (admin-set)
    # initial_capital is used.
    initial_capital: Optional[float] = None
    data_source: str = 'Delta'
    fee_mode: str = 'backtest'
    # Optional top-level overrides. The UI sends both inside `params` (so they
    # are saved with the run and restored later); these exist so a scripted run
    # can switch them without rebuilding the whole parameter block.
    use_mark_price: Optional[bool] = None
    trading_windows: Optional[TradingWindowConfig] = None

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def get_current_user(token: str = Depends(oauth2_scheme), db=Depends(get_db)):
    user = db.query(User).filter(User.username == token).first()
    if not user: raise HTTPException(status_code=401, detail="Invalid credentials")
    if user.is_active == 0: raise HTTPException(status_code=403, detail="Account deactivated. Contact admin.")
    return user

def require_admin(user=Depends(get_current_user)):
    if getattr(user, 'role', 'client') != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


#: The house broker. Delta Exchange India is the venue this tool is built
#: around (BTCUSD perpetual, deadman switch, bracket orders), so it is what
#: every "no broker specified" path resolves to.
DEFAULT_BROKER = 'Delta'


def normalize_source(source: Optional[str]) -> str:
    value = (source or DEFAULT_BROKER).strip()
    return {'binance': 'Binance', 'delta': 'Delta', 'delta exchange': 'Delta'}.get(value.lower(), value)


def _fee_dict(row, broker_code=None, mode=None):
    return {
        'id': getattr(row, 'id', None), 'broker_code': getattr(row, 'broker_code', broker_code),
        'mode': getattr(row, 'mode', mode),
        'taker_fee_bps': float(getattr(row, 'taker_fee_bps', 0.0)),
        'maker_fee_bps': float(getattr(row, 'maker_fee_bps', 0.0)),
        'enabled': bool(getattr(row, 'enabled', 1)), 'updated_at': getattr(row, 'updated_at', None),
    }


def _apply_run_overrides(req):
    """Fold the optional top-level mark-price / window switches into params.

    Both switches live inside ``params`` so they are persisted with the run and
    restored when the run is reopened; a top-level value (when present) wins,
    which keeps scripted runs short.
    """
    if req.use_mark_price is None and req.trading_windows is None:
        return req
    try:
        updates = {}
        if req.use_mark_price is not None:
            updates['use_mark_price'] = bool(req.use_mark_price)
        if req.trading_windows is not None:
            updates['trading_windows'] = req.trading_windows
        req.params = req.params.model_copy(update=updates)
    except Exception:
        pass
    return req


def _user_window_config(user) -> TradingWindowConfig:
    """The account's saved schedule (empty/disabled when never configured)."""
    raw = getattr(user, 'trading_windows_json', None)
    if raw:
        try:
            return TradingWindowConfig(**json.loads(raw))
        except Exception:
            pass
    return TradingWindowConfig()


def resolve_window_config(payload, user) -> TradingWindowConfig:
    """Schedule for a paper/live instance: request value, else account default."""
    explicit = getattr(payload, 'trading_windows', None)
    if explicit is not None:
        if isinstance(explicit, TradingWindowConfig):
            return explicit
        if isinstance(explicit, dict):
            try:
                return TradingWindowConfig(**explicit)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid trading_windows: {exc}")
    return _user_window_config(user)


def resolve_use_mark_price(payload, user) -> bool:
    """Mark-price switch: request value, else the account default."""
    explicit = getattr(payload, 'use_mark_price', None)
    if explicit is not None:
        return bool(explicit)
    value = getattr(user, 'use_mark_price', None)
    return True if value is None else bool(value)


PRICE_FEED_MODES = ("auto", "off", "websocket", "rest")
# A sub-second interval buys nothing — entries wait for a closed 1h candle —
# and on the REST feed it would burn the shared rate-limit budget for no gain.
MIN_TICK_INTERVAL = 1.0
MAX_TICK_INTERVAL = 60.0


def _resolve_sizing(payload, user):
    """Capital and margin % resolved with the mistakes a human makes refused.

    Both fields come straight from a form. Historically they were cast and
    trusted, so ``initial_capital: -5000`` or ``margin_pct: 2500`` started an
    instance that either never trades (order size computes to zero — the card
    just sits at 0 trades forever) or has its very first order bounced by the
    venue for insufficient margin. A wrong number must be refused AT START,
    with a message naming the field, not discovered days later.
    """
    import math

    def _field(value, name, upper, unit):
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail=f"{name} must be a number, got {value!r}")
        # A cleared form field arrives as 0 and has always meant "use the
        # account default" — keep that. Everything else wrong is refused.
        if number == 0:
            return None
        if not math.isfinite(number) or number < 0:
            raise HTTPException(status_code=400,
                                detail=f"{name} must be a positive number, got {value}")
        if number > upper:
            raise HTTPException(status_code=400,
                                detail=f"{name} must be at most {upper:g}{unit}, got {number:g}")
        return number

    capital = _field(payload.initial_capital, "initial_capital", 1e9, " INR")
    margin_pct = _field(payload.margin_pct, "margin_pct", 100.0, "%")
    if capital is None:
        capital = float(user.initial_capital or 20000.0)
    if margin_pct is None:
        margin_pct = float(user.margin_deployment_pct or 25.0)
    return capital, margin_pct


def _resolve_price_feed(payload):
    """Validate the live-price-feed request into ``(mode, interval)``.

    Omitted means ``auto`` — the operator is a trader, not a network engineer:
    the worker takes the venue websocket and fails over to REST polling by
    itself, so nobody has to know what either word means. The explicit modes
    stay accepted for power users and old clients. A *bad* mode is rejected
    rather than silently downgraded: a client asking for websocket exits and
    quietly getting the 60-second cadence would believe a stop is being
    watched continuously when it is not.
    """
    mode = str(getattr(payload, "price_feed", None) or "auto").strip().lower()
    if mode not in PRICE_FEED_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"price_feed must be one of {', '.join(PRICE_FEED_MODES)} (got '{mode}')")
    try:
        interval = float(getattr(payload, "tick_interval", None) or 5.0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="tick_interval must be a number of seconds")
    if not (MIN_TICK_INTERVAL <= interval <= MAX_TICK_INTERVAL):
        raise HTTPException(
            status_code=400,
            detail=f"tick_interval must be between {MIN_TICK_INTERVAL:g} and "
                   f"{MAX_TICK_INTERVAL:g} seconds (got {interval})")
    return mode, interval


def resolve_fees(db, broker_code: str, mode: str, fallback=None):
    """Resolve the admin schedule; fall back to the strategy/.env defaults."""
    broker_code = normalize_source(broker_code)
    mode = (mode or 'backtest').lower()
    row = db.query(FeeSetting).filter_by(broker_code=broker_code, mode=mode, enabled=1).first()
    if row:
        return row
    return fallback or type('FeeSchedule', (), {
        'taker_fee_bps': float(os.getenv('TAKER_FEE_BPS', '5.9')),
        'maker_fee_bps': float(os.getenv('MAKER_FEE_BPS', '2.36')),
    })()


def _fee_config(config, fees):
    """Make a fee-adjusted copy without changing the request model."""
    try:
        return config.model_copy(update={'taker_fee_bps': float(fees.taker_fee_bps),
                                         'maker_fee_bps': float(fees.maker_fee_bps)})
    except AttributeError:
        return config.copy(update={'taker_fee_bps': float(fees.taker_fee_bps),
                                   'maker_fee_bps': float(fees.maker_fee_bps)})


_PHANTOM_PARAM_KEYS = (
    'entry_conditions', 'use_direction_conditions', 'rsi_oversold',
    'rsi_overbought', 'stop_loss_atr', 'macd_hist_min', 'atr_regime_ratio',
    'adx_min', 'trend_ema_period', 'trading_windows', 'use_mark_price',
)


def _parse_run_params(config_json):
    """Decode a historical run's parameter snapshot safely.

    Older databases may contain an empty or malformed snapshot. Returning an
    empty object keeps the results endpoint usable for those runs while newer
    runs still restore the exact nested ``entry_conditions`` block.
    """
    if not config_json:
        return {}
    try:
        value = json.loads(config_json) if isinstance(config_json, str) else config_json
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _resolve_strategy_payload(db, strategy_id, user_id, fees):
    """Resolve a saved custom strategy into a runnable payload.

    A strategy created by the Phantom parameter form (or the backtest page's
    "Save as new strategy") stores a :class:`PhantomV2Config` params dict under
    the `rules` JSON column. A strategy created by the Chartink-style rule
    builder stores a list of rule nodes. This helper detects which and returns
    ``(kind, payload, strat)`` where kind is ``'phantom'`` or ``'dynamic'``.

    Returns ``None`` when the strategy does not exist / is not owned by user.
    """
    try:
        strategy_id = int(strategy_id)
    except (ValueError, TypeError):
        return None
    strat = db.query(CustomStrategy).filter(
        CustomStrategy.id == strategy_id, CustomStrategy.user_id == user_id).first()
    if not strat:
        return None
    data = strat.rules
    if isinstance(data, dict) and any(k in data for k in _PHANTOM_PARAM_KEYS):
        cfg = PhantomV2Config(**{k: v for k, v in data.items() if k in PhantomV2Config.model_fields})
        if fees is not None:
            cfg = _fee_config(cfg, fees)
        return ('phantom', cfg, strat)
    return ('dynamic', data, strat)


async def daily_sync_task():
    """Refresh Binance and every enabled compatible broker once per day.

    The synchronous exchange adapters run in a worker thread so a long or
    rate-limited seed/refresh never blocks FastAPI's event loop. Delta uses its
    supported 15m/1h/4h/1d set here; the 1m/5m history is deliberately omitted.
    """
    while True:
        try:
            await asyncio.to_thread(DataSyncService.sync_all_configured_sources_daily)
        except Exception as exc:
            # A single unavailable exchange must not kill the 24-hour loop.
            print(f"Daily market-data sync failed: {exc}")
        # Sleep for 24 hours (86400 seconds).
        await asyncio.sleep(86400)

def _resume_paper_session(spec):
    """Rebuild and restart one interrupted paper worker from its saved row.

    A paper session used to die permanently on every deploy/restart, which is
    what the user saw as "Interrupted". The saved row carries everything needed
    to stand the worker back up — strategy, broker, capital, margin, schedule,
    feed — so the session continues instead of ending. The closed trades and
    equity curve already banked are carried over, so History stays continuous.
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == spec["user_id"]).first()
        if not user:
            return None
        source = normalize_source(spec.get("data_source"))
        fees = resolve_fees(db, source, 'paper')
        definition = db.query(BrokerDefinition).filter(
            BrokerDefinition.code == source, BrokerDefinition.enabled == 1).first()
        if not definition:
            return None
        strategy_id = str(spec.get("strategy_id") or "")
        capital = float(spec.get("initial_capital") or user.initial_capital or 20000.0)
        margin_pct = float(spec.get("margin_pct") or user.margin_deployment_pct or 25.0)
        common = dict(initial_capital=capital, margin_pct=margin_pct, market_source=source,
                      broker_name=source, fee_schedule=fees, broker_definition=definition,
                      strategy_name=spec.get("strategy_name"),
                      trading_windows=spec.get("trading_windows"),
                      use_mark_price=(None if spec.get("use_mark_price") is None
                                      else bool(spec["use_mark_price"])),
                      price_feed=spec.get("price_feed") or 'off',
                      tick_interval=float(spec.get("tick_interval") or 5.0),
                      testnet=bool(spec.get("testnet")),
                      connection_id=spec.get("connection_id"),
                      account_label=spec.get("account_label"))
        if strategy_id == "FastTest":
            from .core.strategy import FastTestStrategyService
            config = _fee_config(PhantomV2Config(), fees)
            service = PaperTradeService(strategy_id, config, **common)
            service.strategy = FastTestStrategyService(service.config)
        elif strategy_id == "PhantomV2":
            service = PaperTradeService(strategy_id, _fee_config(_load_champion_config(), fees), **common)
        else:
            resolved = _resolve_strategy_payload(db, strategy_id, user.id, fees)
            if not resolved:
                return None
            kind, strategy_payload, strat = resolved
            common["strategy_name"] = strat.name
            service = PaperTradeService(strategy_id, strategy_payload,
                                        is_custom=(kind != 'phantom'), **common)
        # Carry the banked result forward so the resumed session is continuous.
        service.instance_key = spec["instance_key"]
        service.user_id = spec["user_id"]
        service.session_id = spec["id"]
        service.resumed_from_session = spec["id"]
        service.closed_trades = list(spec.get("closed_trades") or [])
        curve = list(spec.get("equity_curve") or [])
        if curve:
            service.equity_history = curve
            try:
                service.equity_inr = float(curve[-1].get("equity", capital))
            except (TypeError, ValueError, AttributeError):
                pass
        service.logs = list(spec.get("logs") or [])[-service.MAX_LOG_LINES:]
        service._log("info", "♻️ Session resumed automatically after a server restart — "
                             "trades, equity curve and logs carried over.")
        paper_history.persist_snapshot(spec["instance_key"], service)
        return service
    except Exception as exc:
        print(f"[paper-history] resume failed for {spec.get('instance_key')}: {exc}")
        return None
    finally:
        db.close()


@app.on_event("startup")
def startup():
    init_db()
    # Sessions saved as 'running' belong to a process that no longer exists
    # (the server restarted). Flag them, then stand the resumable ones back up
    # so a deploy no longer silently ends every client's paper run.
    interrupted = paper_history.mark_interrupted_sessions()
    if interrupted:
        print(f"[paper-history] marked {len(interrupted)} paper session(s) as interrupted")
    resumed = 0
    for spec in interrupted:
        if not spec.get("auto_resume"):
            continue
        service = _resume_paper_session(spec)
        if service is None:
            continue
        service.history_status = paper_history.STATUS_RUNNING
        paper_trade_instances[spec["instance_key"]] = service
        asyncio.create_task(service.start())
        resumed += 1
    if resumed:
        print(f"[paper-history] auto-resumed {resumed} paper session(s)")
    # Start the background sync task
    asyncio.create_task(daily_sync_task())
    # Persist every live tick (Binance + Delta BTC perpetual) so the series
    # can be replayed / resampled later, even with no paper/live session open.
    try:
        from .services.tick_store import collector_enabled, run_collector
        if collector_enabled():
            asyncio.create_task(run_collector())
    except Exception as exc:
        print(f"[ticks] collector not started: {exc}")

@app.post("/token")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db=Depends(get_db)):
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user:
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    if user.is_active == 0:
        raise HTTPException(status_code=403, detail="Account deactivated. Contact admin.")

    try:
        # Use bcrypt directly to avoid passlib version incompatibility issues
        import bcrypt
        password_bytes = form_data.password.encode('utf-8')
        hashed_bytes = user.password_hash.encode('utf-8')
        if bcrypt.checkpw(password_bytes, hashed_bytes):
            return {
                "access_token": user.username, "token_type": "bearer",
                "username": user.username, "role": user.role or "client",
                "can_paper": user.can_paper if user.can_paper is not None else 1,
                "can_live": user.can_live if user.can_live is not None else 0,
            }
    except Exception as e:
        print(f"Login Verification Error: {e}")
        raise HTTPException(status_code=500, detail="Authentication system error")

    raise HTTPException(status_code=400, detail="Incorrect username or password")

@app.post("/auth/register")
def register(username: str, password: str, db=Depends(get_db)):
    import bcrypt
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
    user = User(username=username, password_hash=hashed)
    db.add(user)
    db.commit()
    return {"status": "User registered"}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@app.post("/auth/change-password")
def change_password(payload: ChangePasswordRequest, user=Depends(get_current_user), db=Depends(get_db)):
    """Change the logged-in user's password."""
    try:
        import bcrypt
        # Verify current password
        if not bcrypt.checkpw(payload.current_password.encode('utf-8'), user.password_hash.encode('utf-8')):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        if len(payload.new_password) < 6:
            raise HTTPException(status_code=400, detail="New password must be at least 6 characters")
        # Hash and save
        user.password_hash = bcrypt.hashpw(payload.new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        db.commit()
        return {"status": "Password changed successfully"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to change password: {str(e)}")

# --- HEALTH CHECK ---
@app.get("/")
def health_check():
    return {
        "status": "online",
        "system": "PHANTOM v2.5",
        "timestamp": datetime.utcnow(),
        "message": "Trading engine is operational"
    }

# --- STRATEGY MANAGEMENT ---
@app.get("/strategies")
def list_strategies(user=Depends(get_current_user), db=Depends(get_db)):
    try:
        strategies = db.query(CustomStrategy).filter(CustomStrategy.user_id == user.id).all()
        return [{"id": s.id, "name": s.name, "rules": s.rules, "is_active": s.is_active, "created_at": s.created_at} for s in strategies]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch strategies: {str(e)}")

@app.post("/strategies/create")
def create_strategy(strategy: CustomStrategyCreate, user=Depends(get_current_user), db=Depends(get_db)):
    try:
        config_data = strategy.rules if strategy.rules is not None else strategy.params
        if config_data is None:
            raise HTTPException(status_code=400, detail="Either 'rules' or 'params' must be provided")
        new_strat = CustomStrategy(user_id=user.id, name=strategy.name, rules=config_data)
        db.add(new_strat)
        db.commit()
        return {"status": "Strategy created successfully", "id": new_strat.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error creating strategy: {str(e)}")

@app.put("/strategies/update/{strat_id}")
def update_strategy(strat_id: int, strategy_data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    try:
        strat = db.query(CustomStrategy).filter(CustomStrategy.id == strat_id, CustomStrategy.user_id == user.id).first()
        if not strat: raise HTTPException(status_code=404, detail="Strategy not found")
        if "name" in strategy_data: strat.name = strategy_data["name"]
        config_update = strategy_data.get("rules") or strategy_data.get("params")
        if config_update is not None: strat.rules = config_update
        db.commit()
        return {"status": "Strategy updated successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/strategies/{strat_id}")
def delete_strategy(strat_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    try:
        strat = db.query(CustomStrategy).filter(CustomStrategy.id == strat_id, CustomStrategy.user_id == user.id).first()
        if not strat: raise HTTPException(status_code=404, detail="Strategy not found")
        db.delete(strat)
        db.commit()
        return {"status": "Strategy deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- STRATEGY SCANNER (Chartink-style live preview) ----------------------
class ScanRequest(BaseModel):
    rules: Union[List[Dict], Dict]
    symbol: str = "BTCUSDT"
    interval: str = "1h"
    source: str = "Delta"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    limit: int = 25

@app.post("/strategies/scan")
def scan_strategy(payload: ScanRequest, user=Depends(get_current_user)):
    """Scan the market data against an unsaved rule set (Chartink-style).

    Returns the most recent candles that satisfy the rule group so the
    strategy builder can show a live 'conditions met' preview.
    """
    try:
        from .core.dynamic_strategy import DynamicStrategyService
        engine = BacktestEngine(PhantomV2Config())
        df_1h = engine._get_data_from_db(payload.symbol, payload.interval, payload.start_date, payload.end_date, normalize_source(payload.source))
        df_4h = engine._get_data_from_db(payload.symbol, "4h", payload.start_date, payload.end_date, normalize_source(payload.source))
        if df_1h.empty or df_4h.empty:
            return []

        svc = DynamicStrategyService(payload.rules)
        signals = svc.generate_signals(df_1h, df_4h)

        out = []
        closes = df_1h['close'].values
        opens = df_1h['open'].values
        highs = df_1h['high'].values
        lows = df_1h['low'].values
        for i in range(1, len(df_1h)):
            if signals[i] != 0:
                out.append({
                    "time": int(_utc_ts(df_1h.index[i])),
                    "direction": int(signals[i]),
                    "open": float(opens[i]), "high": float(highs[i]),
                    "low": float(lows[i]), "close": float(closes[i]),
                })
        # Return the most recent matches
        return out[-payload.limit:]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scan error: {str(e)}")

# --- ADMIN: CLIENT MANAGEMENT ---
class ClientCreate(BaseModel):
    username: str
    password: str
    initial_capital: float = 20000.0
    margin_deployment_pct: float = 25.0
    can_paper: bool = True
    can_live: bool = False
    full_name: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    notes: Optional[str] = None

class ClientUpdate(BaseModel):
    password: Optional[str] = None
    initial_capital: Optional[float] = None
    margin_deployment_pct: Optional[float] = None
    can_paper: Optional[bool] = None
    can_live: Optional[bool] = None
    is_active: Optional[bool] = None
    full_name: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    notes: Optional[str] = None

def _client_dict(u: User):
    return {
        "id": u.id, "username": u.username, "role": u.role or "client",
        "is_active": u.is_active if u.is_active is not None else 1,
        "can_paper": u.can_paper if u.can_paper is not None else 1,
        "can_live": u.can_live if u.can_live is not None else 0,
        "initial_capital": u.initial_capital, "margin_deployment_pct": u.margin_deployment_pct,
        "virtual_balance": u.virtual_balance, "broker_name": u.broker_name,
        "full_name": u.full_name, "mobile": u.mobile, "email": u.email,
        "company": u.company, "notes": u.notes,
        "has_api_keys": bool(u.api_key and u.api_secret), "created_at": u.created_at,
    }

@app.get("/admin/clients")
def list_clients(admin=Depends(require_admin), db=Depends(get_db)):
    users = db.query(User).order_by(User.created_at.asc()).all()
    return [_client_dict(u) for u in users]

@app.post("/admin/clients")
def create_client(payload: ClientCreate, admin=Depends(require_admin), db=Depends(get_db)):
    try:
        if db.query(User).filter(User.username == payload.username).first():
            raise HTTPException(status_code=400, detail="Username already exists")
        import bcrypt
        hashed = bcrypt.hashpw(payload.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        user = User(username=payload.username, password_hash=hashed, role='client',
                    initial_capital=payload.initial_capital,
                    margin_deployment_pct=payload.margin_deployment_pct,
                    virtual_balance=payload.initial_capital,
                    can_paper=int(payload.can_paper), can_live=int(payload.can_live),
                    is_active=1,
                    full_name=payload.full_name, mobile=payload.mobile,
                    email=payload.email, company=payload.company, notes=payload.notes)
        db.add(user)
        db.commit()
        db.refresh(user)
        return {"status": "Client created", "client": _client_dict(user)}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error creating client: {str(e)}")

@app.put("/admin/clients/{client_id}")
def update_client(client_id: int, payload: ClientUpdate, admin=Depends(require_admin), db=Depends(get_db)):
    try:
        user = db.query(User).filter(User.id == client_id).first()
        if not user: raise HTTPException(status_code=404, detail="Client not found")
        if payload.password:
            import bcrypt
            user.password_hash = bcrypt.hashpw(payload.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        if payload.initial_capital is not None: user.initial_capital = payload.initial_capital
        if payload.margin_deployment_pct is not None: user.margin_deployment_pct = payload.margin_deployment_pct
        if payload.can_paper is not None: user.can_paper = int(payload.can_paper)
        if payload.can_live is not None: user.can_live = int(payload.can_live)
        if payload.full_name is not None: user.full_name = payload.full_name
        if payload.mobile is not None: user.mobile = payload.mobile
        if payload.email is not None: user.email = payload.email
        if payload.company is not None: user.company = payload.company
        if payload.notes is not None: user.notes = payload.notes
        if payload.is_active is not None:
            # Never allow deactivation of admin users
            if user.role == 'admin' and not payload.is_active:
                raise HTTPException(status_code=400, detail="Admin accounts cannot be deactivated")
            user.is_active = int(payload.is_active)
            if not payload.is_active:
                # Stop the client's running sessions. The saved history rows
                # are finalised (not deleted) so the results stay reviewable.
                for key in list(paper_trade_instances.keys()):
                    if f"_{user.username}_" in key:
                        svc = paper_trade_instances[key]
                        svc.is_running = False
                        paper_history.finalize_session(key, svc)
                        del paper_trade_instances[key]
                for key in list(live_trade_instances.keys()):
                    if f"_{user.username}_" in key:
                        live_trade_instances[key].is_running = False
                        del live_trade_instances[key]
        db.commit()
        db.refresh(user)
        return {"status": "Client updated", "client": _client_dict(user)}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error updating client: {str(e)}")

@app.delete("/admin/clients/{client_id}")
def deactivate_client(client_id: int, admin=Depends(require_admin), db=Depends(get_db)):
    user = db.query(User).filter(User.id == client_id).first()
    if not user: raise HTTPException(status_code=404, detail="Client not found")
    if user.username == admin.username:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own admin account")
    user.is_active = 0
    db.commit()
    return {"status": f"Client '{user.username}' deactivated"}

@app.get("/admin/clients/{client_id}/activity")
def client_activity(client_id: int, admin=Depends(require_admin), db=Depends(get_db)):
    user = db.query(User).filter(User.id == client_id).first()
    if not user: raise HTTPException(status_code=404, detail="Client not found")
    paper = [{"instance_key": k, "strategy_id": s.strategy_id,
              "strategy_name": getattr(s, 'strategy_name', s.strategy_id), "equity_inr": s.equity_inr,
              "is_running": s.is_running, "open_trades": len(s.oms.active_trades)}
             for k, s in paper_trade_instances.items() if f"_{user.username}_" in k]
    live = [{"instance_key": k, "strategy_id": s.strategy_id,
             "strategy_name": getattr(s, 'strategy_name', s.strategy_id), "equity_inr": s.equity_inr,
             "is_running": s.is_running, "open_trades": len(s.oms.active_trades)}
            for k, s in live_trade_instances.items() if f"_{user.username}_" in k]
    runs = db.query(BacktestRun).filter(BacktestRun.user_id == user.id)\
               .order_by(BacktestRun.timestamp.desc()).limit(10).all()
    return {
        "client": _client_dict(user),
        "paper_sessions": paper, "live_sessions": live,
        "recent_backtests": [{"id": r.id, "name": r.name, "roi": r.roi,
                              "total_trades": r.total_trades, "timestamp": r.timestamp} for r in runs],
    }

# --- BROKERS, FEE SCHEDULES & MARKET DATA -------------------------------
class FeeSettingPayload(BaseModel):
    broker_code: str
    mode: str
    taker_fee_bps: float
    maker_fee_bps: float
    enabled: bool = True


class BrokerDefinitionPayload(BaseModel):
    code: str
    name: str
    kind: str = 'generic'
    market_data_url: Optional[str] = None
    trading_api_url: Optional[str] = None
    enabled: bool = True
    notes: Optional[str] = None
    # Rate-limit policy — NULL falls back to the venue default
    # (Delta 10 000 weight / 5 min, Binance 2 400 weight + 1 200 orders / min).
    rate_limit_per_second: Optional[float] = None
    rate_limit_per_minute: Optional[float] = None
    quota_per_5min: Optional[float] = None
    orders_per_minute: Optional[float] = None
    # Trading defaults for the live terminal.
    default_leverage: Optional[int] = None
    margin_mode: Optional[str] = None
    contract_value: Optional[float] = None
    tick_size: Optional[float] = None


def _broker_dict(row):
    return {'id': row.id, 'code': row.code, 'name': row.name, 'kind': row.kind,
            'market_data_url': row.market_data_url, 'trading_api_url': row.trading_api_url,
            'enabled': bool(row.enabled), 'is_builtin': bool(row.is_builtin), 'notes': row.notes,
            'rate_limit_per_second': row.rate_limit_per_second,
            'rate_limit_per_minute': row.rate_limit_per_minute,
            'quota_per_5min': row.quota_per_5min,
            'orders_per_minute': row.orders_per_minute,
            'default_leverage': row.default_leverage, 'margin_mode': row.margin_mode,
            'contract_value': row.contract_value, 'tick_size': row.tick_size}


def _apply_broker_payload(row, payload):
    """Copy the editable broker fields (incl. rate limits) onto a row."""
    row.name = payload.name
    row.kind = payload.kind.lower()
    row.market_data_url = payload.market_data_url
    row.trading_api_url = payload.trading_api_url
    row.enabled = int(payload.enabled)
    row.notes = payload.notes
    row.rate_limit_per_second = payload.rate_limit_per_second
    row.rate_limit_per_minute = payload.rate_limit_per_minute
    row.quota_per_5min = payload.quota_per_5min
    row.orders_per_minute = payload.orders_per_minute
    row.default_leverage = payload.default_leverage
    row.margin_mode = (payload.margin_mode or None)
    row.contract_value = payload.contract_value
    row.tick_size = payload.tick_size


# Delta is the default venue, so it leads every broker dropdown. Ordering by
# name alone would put "Binance Futures" first purely alphabetically.
DEFAULT_BROKER_CODE = 'Delta'


def _broker_sort_key(row):
    return (0 if (row.code or '') == DEFAULT_BROKER_CODE else 1, row.name or '')


@app.get('/broker-definitions')
def broker_definitions(user=Depends(get_current_user), db=Depends(get_db)):
    rows = db.query(BrokerDefinition).filter(BrokerDefinition.enabled == 1).all()
    return [_broker_dict(b) for b in sorted(rows, key=_broker_sort_key)]


@app.get('/admin/brokers')
def admin_brokers(admin=Depends(require_admin), db=Depends(get_db)):
    rows = db.query(BrokerDefinition).all()
    return [_broker_dict(b) for b in sorted(rows, key=_broker_sort_key)]


@app.post('/admin/brokers')
def create_broker(payload: BrokerDefinitionPayload, admin=Depends(require_admin), db=Depends(get_db)):
    code = normalize_source(payload.code)
    # Case-insensitive: connections and instance settings resolve codes with
    # func.lower(), so "bybit" and "Bybit" would be two rows fighting over one
    # identity. One spelling per venue, enforced at the door.
    if db.query(BrokerDefinition).filter(func.lower(BrokerDefinition.code) == code.lower()).first():
        raise HTTPException(status_code=400, detail='Broker code already exists')
    row = BrokerDefinition(code=code, is_builtin=0)
    _apply_broker_payload(row, payload)
    db.add(row)
    db.flush()
    default_taker = float(os.getenv('TAKER_FEE_BPS', '5.9'))
    default_maker = float(os.getenv('MAKER_FEE_BPS', '2.36'))
    for mode in ('backtest', 'paper', 'live'):
        db.add(FeeSetting(broker_code=code, mode=mode, taker_fee_bps=default_taker,
                          maker_fee_bps=default_maker, enabled=1))
    db.commit(); db.refresh(row)
    return _broker_dict(row)


@app.put('/admin/brokers/{broker_id}')
def update_broker(broker_id: int, payload: BrokerDefinitionPayload, admin=Depends(require_admin), db=Depends(get_db)):
    row = db.query(BrokerDefinition).filter(BrokerDefinition.id == broker_id).first()
    if not row: raise HTTPException(status_code=404, detail='Broker integration not found')
    _apply_broker_payload(row, payload)
    db.commit(); db.refresh(row)
    return _broker_dict(row)


@app.delete('/admin/brokers/{broker_id}')
def delete_broker(broker_id: int, admin=Depends(require_admin), db=Depends(get_db)):
    row = db.query(BrokerDefinition).filter(BrokerDefinition.id == broker_id).first()
    if not row: raise HTTPException(status_code=404, detail='Broker integration not found')
    if row.is_builtin: raise HTTPException(status_code=400, detail='Built-in integrations cannot be deleted; disable them instead')
    row.enabled = 0; db.commit()
    return {'status': 'Broker integration disabled'}


@app.get('/admin/fee-settings')
def admin_fee_settings(admin=Depends(require_admin), db=Depends(get_db)):
    return [_fee_dict(f) for f in db.query(FeeSetting).order_by(FeeSetting.broker_code, FeeSetting.mode).all()]


@app.get('/fee-settings')
def fee_settings(broker_code: str = 'Delta', mode: str = 'backtest', user=Depends(get_current_user), db=Depends(get_db)):
    return _fee_dict(resolve_fees(db, broker_code, mode), normalize_source(broker_code), mode)


@app.post('/admin/fee-settings')
def save_fee_setting(payload: FeeSettingPayload, admin=Depends(require_admin), db=Depends(get_db)):
    broker = normalize_source(payload.broker_code)
    mode = payload.mode.lower().strip()
    if mode not in ('backtest', 'paper', 'live'):
        raise HTTPException(status_code=400, detail='mode must be backtest, paper, or live')
    if payload.taker_fee_bps < 0 or payload.maker_fee_bps < 0:
        raise HTTPException(status_code=400, detail='Fees cannot be negative')
    row = db.query(FeeSetting).filter_by(broker_code=broker, mode=mode).first()
    if not row:
        row = FeeSetting(broker_code=broker, mode=mode)
        db.add(row)
    row.taker_fee_bps = payload.taker_fee_bps; row.maker_fee_bps = payload.maker_fee_bps
    row.enabled = int(payload.enabled); row.updated_at = datetime.utcnow()
    db.commit(); db.refresh(row)
    return _fee_dict(row)


@app.put('/admin/fee-settings/{fee_id}')
def update_fee_setting(fee_id: int, payload: FeeSettingPayload, admin=Depends(require_admin), db=Depends(get_db)):
    row = db.query(FeeSetting).filter(FeeSetting.id == fee_id).first()
    if not row: raise HTTPException(status_code=404, detail='Fee schedule not found')
    row.broker_code = normalize_source(payload.broker_code); row.mode = payload.mode.lower()
    row.taker_fee_bps = payload.taker_fee_bps; row.maker_fee_bps = payload.maker_fee_bps
    row.enabled = int(payload.enabled); row.updated_at = datetime.utcnow()
    db.commit(); db.refresh(row)
    return _fee_dict(row)


# ------------------------------------------------------------------
# Platform settings: the USD→INR conversion rate
# ------------------------------------------------------------------
class UsdInrPayload(BaseModel):
    rate: float


def _usd_inr_dict():
    from .services.app_settings import usd_inr_setting, USD_INR_MIN, USD_INR_MAX
    rate, source, updated_at = usd_inr_setting()
    return {
        "rate": rate,
        # 'admin' when saved from the panel, 'env' for USD_INR_RATE, else
        # 'default' — so the UI can say whether anyone actually chose this.
        "source": source,
        "updated_at": updated_at.isoformat() if updated_at else None,
        "min": USD_INR_MIN,
        "max": USD_INR_MAX,
    }


@app.get('/admin/settings/usd-inr')
def get_usd_inr(admin=Depends(require_admin)):
    return _usd_inr_dict()


@app.put('/admin/settings/usd-inr')
def update_usd_inr(payload: UsdInrPayload, admin=Depends(require_admin)):
    from .services.app_settings import set_usd_inr_rate
    try:
        rate = set_usd_inr_rate(payload.rate)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Running workers pick the new rate up immediately: it is the CURRENT
    # market fact used to convert margin and PnL from here on. Trades already
    # closed keep the numbers they were booked with.
    applied = 0
    for svc in list(paper_trade_instances.values()) + list(live_trade_instances.values()):
        try:
            svc.conversion_rate = rate
            applied += 1
        except Exception:
            pass
    out = _usd_inr_dict()
    out["applied_to_running"] = applied
    return out


class BrokerConnectionPayload(BaseModel):
    broker_code: str
    label: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    passphrase: Optional[str] = None
    is_testnet: bool = False
    is_active: bool = True


def _mask(value):
    if not value: return ''
    return value[:4] + ('•' * max(0, len(value) - 8)) + value[-4:] if len(value) > 8 else '••••••••'


def _decrypt_secret(value):
    """Decrypt a stored API secret, or fail loud with a fixable message.

    API secrets are encrypted at rest (``app.core.secrets``); every read point
    goes through here so the plaintext exists only in memory while signing.
    """
    try:
        return decrypt_secret(value)
    except SecretDecryptionError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


def _connection_dict(row):
    # Account details as last read from the venue (margin mode / leverage /
    # sub-accounts). Parsed JSON so the UI never has to guess what mode a
    # sub-account is in; None until the first successful read.
    settings = None
    if getattr(row, 'account_settings', None):
        try:
            settings = json.loads(row.account_settings)
        except (TypeError, ValueError):
            settings = None
    return {'id': row.id, 'broker_code': row.broker_code, 'label': row.label or row.broker_code,
            'api_key': _mask(row.api_key), 'has_secret': bool(row.api_secret),
            'is_testnet': bool(row.is_testnet), 'is_active': bool(row.is_active),
            'account_settings': settings,
            'account_settings_at': row.account_settings_at.isoformat() if row.account_settings_at else None,
            'created_at': row.created_at}


def _fetch_connection_settings(db, connection):
    """Read margin mode / leverage / sub-accounts from the venue, per connection.

    Best-effort by design: an invalid key stores an error (surfaced in
    Broker Settings next to the connection) instead of failing the save, and
    a venue hiccup never breaks the request. Runs synchronously so the save
    response already says what the account actually is.
    """
    from .services.broker_client import BrokerClient
    settings = None
    try:
        definition = db.query(BrokerDefinition).filter(
            BrokerDefinition.code == _canonical_broker_code(db, connection.broker_code)).first()
        client = BrokerClient(connection.api_key, _decrypt_secret(connection.api_secret),
                              connection.broker_code, connection.passphrase or '',
                              bool(connection.is_testnet), definition)
        settings = client.get_account_settings('BTCUSDT')
    except Exception as exc:
        settings = {'error': f'{exc.__class__.__name__}: {exc}'}
    try:
        connection.account_settings = json.dumps(settings) if settings is not None else None
        connection.account_settings_at = datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()
    return settings


def _live_instances_on_connection(connection, user_id=None) -> list:
    """Running live instances that trade on this saved connection.

    Matched by ``connection_id`` because two connections can carry keys for the
    same venue — and the same sub-account — and only the id says which one an
    instance was started with. An instance with *no* connection id (started on
    the legacy per-account keys) is included too: a connection saved for that
    venue and login is exactly what its credential reload picks up.
    """
    if connection is None:
        return []
    code = normalize_source(getattr(connection, "broker_code", "") or "")
    own_id = getattr(connection, "id", None)
    out = []
    for key, service in list(live_trade_instances.items()):
        service_connection = getattr(service, "connection_id", None)
        if service_connection == own_id:
            out.append((key, service))
            continue
        if service_connection is None and normalize_source(getattr(service, "broker_name", "")) == code \
                and (user_id is None or getattr(service, "user_id", None) == user_id):
            out.append((key, service))
    return out


def _adopt_credentials_sync(service) -> dict:
    """Re-read the saved connection into one running instance (blocking half)."""
    try:
        result = service.reload_credentials(force=True)
    except Exception as exc:
        result = {"reloaded": False, "verified": False,
                  "reason": f"credential reload failed: {exc.__class__.__name__}: {exc}"}
    creds = result.get("credentials") or {}
    return {"reloaded": bool(result.get("reloaded")),
            "verified": bool(result.get("verified")),
            "reason": result.get("reason"),
            "state": creds.get("state"),
            "error": result.get("error") or creds.get("error")}


async def _adopt_saved_credentials(connection, user_id=None) -> dict:
    """Hand newly saved credentials to the instances already trading on them.

    This is why "restart the live instance" is no longer part of fixing a key:
    the instance re-reads its own connection row and swaps the client — with it
    the account it queues on, the rate-limit budget and the deadman switch — so
    the next tick resumes by itself. Blocking in the request thread would stall
    the loop, and the heartbeat has to be re-armed *on* that loop, hence the
    split between to_thread and the awaited resume.
    """
    instances = _live_instances_on_connection(connection)
    out = {"notified": len(instances), "reloaded": 0, "verified": 0, "instances": []}
    for key, service in instances:
        entry = await asyncio.to_thread(_adopt_credentials_sync, service)
        if entry.get("verified"):
            try:
                await service.credentials_recovered()
            except Exception as exc:
                entry["resume_error"] = f"{exc.__class__.__name__}: {exc}"
        entry["instance_key"] = key
        out["instances"].append(entry)
        out["reloaded"] += int(bool(entry.get("reloaded")))
        out["verified"] += int(bool(entry.get("verified")))
    return out


@app.get('/broker-connections')
def list_broker_connections(user=Depends(get_current_user), db=Depends(get_db)):
    rows = db.query(BrokerConnection).filter(BrokerConnection.user_id == user.id).order_by(BrokerConnection.created_at).all()
    # A legacy account still gets a selectable connection without a migration step.
    result = [_connection_dict(r) for r in rows]
    if not result and user.api_key:
        result.append({'id': None, 'broker_code': user.broker_name or DEFAULT_BROKER, 'label': 'Legacy account',
                       'api_key': _mask(user.api_key), 'has_secret': bool(user.api_secret), 'is_testnet': False,
                       'is_active': True, 'legacy': True})
    return result


@app.get('/broker-connections/diagnose')
def diagnose_broker_connection(broker: str = 'Delta', connection_id: Optional[int] = None,
                               user=Depends(get_current_user), db=Depends(get_db)):
    """Explain, for THIS login, whether a broker is ready to trade.

    "API keys not configured" used to cover five different situations that look
    identical from the browser. This reports what the server actually found —
    the registry entry, every saved connection (with the code it resolves to),
    the legacy per-account keys, and a plain-language list of what is missing —
    so the difference between the Exchange Registry and a broker connection is
    visible instead of guessed at.
    """
    code = normalize_source(broker)
    definition = db.query(BrokerDefinition).filter(BrokerDefinition.code == code).first()
    rows = _user_connections(db, user, code)
    connections = [{
        'id': r.id, 'label': r.label or r.broker_code,
        'stored_code': r.broker_code,
        'resolved_code': _canonical_broker_code(db, r.broker_code),
        'api_key': _mask(r.api_key), 'has_secret': bool(r.api_secret),
        'is_active': _connection_is_active(r), 'is_testnet': bool(r.is_testnet),
        'account_settings': _connection_dict(r).get('account_settings'),
        'created_at': r.created_at,
    } for r in rows]
    legacy_keys = bool(user.api_key and user.api_secret and
                       normalize_source(user.broker_name) == code)
    usable = [c for c in connections
              if c['is_active'] and c['api_key'] and c['has_secret']
              and (connection_id is None or c['id'] == connection_id)]

    problems = []
    if not definition:
        problems.append(f"'{code}' is not in the Exchange Registry, so no adapter can trade it.")
    elif not definition.enabled:
        problems.append(f"'{code}' is disabled in the Exchange Registry — enable it to use it.")
    if not connections:
        problems.append(f"No broker connection saved on the account '{user.username}'. The Exchange "
                        f"Registry only registers the integration; credentials belong to a login.")
    else:
        if not any(c['is_active'] for c in connections):
            problems.append("Every saved connection is switched off.")
        if not any(c['has_secret'] for c in connections):
            problems.append("No saved connection has an API secret. Secrets are never returned, so "
                            "re-enter it when editing the connection.")
    if not usable and not legacy_keys:
        problems.append(_credentials_problem(db, user, code, connection_id))

    return {
        'broker': code,
        'account': user.username,
        'definition': ({'code': definition.code, 'name': definition.name, 'kind': definition.kind,
                        'enabled': bool(definition.enabled), 'is_builtin': bool(definition.is_builtin)}
                       if definition else None),
        'connections': connections,
        'legacy_account_keys': legacy_keys,
        'ready': bool(usable or legacy_keys) and bool(definition and definition.enabled),
        'problems': problems,
    }


@app.post('/broker-connections')
async def create_broker_connection(payload: BrokerConnectionPayload, user=Depends(get_current_user), db=Depends(get_db)):
    try:
        broker_code = validate_broker_code(payload.broker_code)
    except PhantomValidationError as ve:
        raise HTTPException(status_code=ve.status_code, detail=ve.message)
    # Accept any spelling the UI or a script may send (code, case, or the
    # display name) and store the canonical registry code, so a saved row always
    # matches what the live call looks up.
    code = _canonical_broker_code(db, broker_code)
    # Deployment rail: this box trades Delta India, so the Global adapter
    # (https://api.delta.exchange) is refused at the door with the official
    # key/API rule — an India key would be rejected there anyway.
    if not BrokerClient.delta_family_allowed(code):
        raise HTTPException(status_code=400, detail=BrokerClient.DELTA_FAMILY_RULE)
    if not db.query(BrokerDefinition).filter_by(code=code, enabled=1).first():
        raise HTTPException(status_code=400, detail='Unknown or disabled broker integration')
    if not payload.api_key or not payload.api_secret:
        raise HTTPException(status_code=400, detail='API key and secret are required')
    # Short keys are allowed in tests; real keys are longer, but a paste error
    # with a single char is caught by broker's own auth rejection, not here.
    # Keys are stored as pasted apart from surrounding whitespace: a trailing
    # newline from a terminal paste is invisible in the UI and is a different
    # key as far as the venue is concerned.
    try:
        row = BrokerConnection(user_id=user.id, broker_code=code, label=payload.label,
                               api_key=payload.api_key.strip(),
                               api_secret=encrypt_secret(payload.api_secret.strip()),
                               passphrase=(payload.passphrase or '').strip() or None,
                               is_testnet=int(payload.is_testnet), is_active=int(payload.is_active))
        db.add(row); db.commit(); db.refresh(row)
    except IntegrityError as ie:
        db.rollback()
        err = map_db_error(ie)
        raise HTTPException(status_code=err.status_code, detail=err.message)
    except SQLAlchemyError as se:
        db.rollback()
        phantom_logger.error(f"DB error create_broker_connection: {se}")
        raise HTTPException(status_code=500, detail="Database error while saving connection")
    # Read margin mode / leverage / sub-accounts from the venue right away so
    # the connection card shows what the account actually is (and a bad key
    # is visible immediately instead of surfacing later as a 401 wall).
    await asyncio.to_thread(_fetch_connection_settings, db, row)
    db.refresh(row)
    out = _connection_dict(row)
    # An instance started on the legacy per-account keys has no connection of
    # its own: this row is exactly what its reload picks up, so hand it over
    # now instead of waiting for that instance to time out on a dead key.
    out['live_instances'] = await _adopt_saved_credentials(row, user.id)
    return out


@app.put('/broker-connections/{connection_id}')
async def update_broker_connection(connection_id: int, payload: BrokerConnectionPayload, user=Depends(get_current_user), db=Depends(get_db)):
    row = db.query(BrokerConnection).filter(BrokerConnection.id == connection_id, BrokerConnection.user_id == user.id).first()
    if not row: raise HTTPException(status_code=404, detail='Broker connection not found')
    code = _canonical_broker_code(db, payload.broker_code)
    # Deployment rail: a row may stay on DeltaGlobal (the operator can still
    # align or delete it), but it may not be *switched onto* the Global
    # adapter on an India-only box.
    if code == "DeltaGlobal" and BrokerClient.delta_deployment_family() == "india" \
            and str(row.broker_code or "").lower() not in ("deltaglobal",):
        raise HTTPException(status_code=400, detail=BrokerClient.DELTA_FAMILY_RULE)
    row.broker_code = code; row.label = payload.label
    # Empty secrets mean "keep the existing secret" in the edit form — the API
    # never returns one, so an edit form cannot round-trip it.
    if payload.api_key: row.api_key = payload.api_key.strip()
    if payload.api_secret: row.api_secret = encrypt_secret(payload.api_secret.strip())
    if payload.passphrase is not None: row.passphrase = (payload.passphrase or '').strip() or None
    row.is_testnet = int(payload.is_testnet); row.is_active = int(payload.is_active)
    db.commit(); db.refresh(row)
    # Credentials or environment may have changed: re-read the account details.
    await asyncio.to_thread(_fetch_connection_settings, db, row)
    db.refresh(row)
    out = _connection_dict(row)
    # The fix for a rejected key is now this request, not a process restart:
    # every instance trading on this connection swaps its client in place.
    out['live_instances'] = await _adopt_saved_credentials(row, user.id)
    return out


@app.post('/broker-connections/{connection_id}/probe')
async def probe_broker_connection(connection_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Ask each Delta India environment whether it accepts this connection's key.

    'invalid_api_key' on every signed call is the same answer to four different
    questions, and the only one that cannot be read off the key is which
    environment it belongs to — production keys and demo/testnet keys live in
    separate stores. So sign one ``GET /v2/wallet/balances`` per host and report
    what came back, which is also the difference between 'create a new key' and
    'flip one toggle'.

    Note this probes from the **server**, which is the machine whose IP a
    whitelisted key trusts; a probe from anywhere else can 401 a good key.
    """
    row = db.query(BrokerConnection).filter(BrokerConnection.id == connection_id,
                                            BrokerConnection.user_id == user.id).first()
    if not row: raise HTTPException(status_code=404, detail='Broker connection not found')
    if not (row.api_key and row.api_secret):
        raise HTTPException(status_code=400, detail='This connection has no key/secret pair to probe')
    code = _canonical_broker_code(db, row.broker_code)
    definition = db.query(BrokerDefinition).filter(BrokerDefinition.code == code).first()
    from .services.delta_key_probe import probe_connection
    result = await asyncio.to_thread(
        probe_connection, row.api_key, _decrypt_secret(row.api_secret), bool(row.is_testnet),
        row.label or code, (definition.kind if definition else 'generic'), code)
    out = {'connection_id': row.id, 'label': row.label or code, 'broker': code,
           'is_testnet': bool(row.is_testnet), **result}
    # A probe that lands is also a working key: refresh the cached account
    # details and let any instance holding entries off it right away.
    if result.get('accepted'):
        await asyncio.to_thread(_fetch_connection_settings, db, row)
        out['live_instances'] = await _adopt_saved_credentials(row, user.id)
    return out


@app.post('/broker-connections/{connection_id}/test')
async def test_broker_connection(connection_id: int, request: Request,
                                 user=Depends(get_current_user), db=Depends(get_db)):
    """Read-only full connection battery: the answer to "why is everything
    signed rejected while seeds work?".

    Public market data (seeds, candles, tickers) needs no API key, so a venue
    can look half-alive while every account/order call 401s. This endpoint runs
    the same battery as ``tools/test_connection.py`` and returns a step-by-step
    report + the environment the key actually belongs to. It never places,
    edits or cancels an order.

    ``?apply=true`` additionally repoints the saved connection at the detected
    environment (broker code + testnet flag) — the fix the report describes —
    and hands the new credentials to any running instances, no restart needed.
    """
    from .services.connection_test import run_connection_test
    row = db.query(BrokerConnection).filter(BrokerConnection.id == connection_id,
                                            BrokerConnection.user_id == user.id).first()
    if not row: raise HTTPException(status_code=404, detail='Broker connection not found')
    if not (row.api_key and row.api_secret):
        raise HTTPException(status_code=400, detail='This connection has no key/secret pair to test')
    code = _canonical_broker_code(db, row.broker_code)
    result = await asyncio.to_thread(
        run_connection_test, row.api_key, _decrypt_secret(row.api_secret), code,
        bool(row.is_testnet), row.id, row.label or code)
    detected = result.get('detected')
    if detected and request.query_params.get('apply') == 'true':
        row.broker_code = detected['broker_code']
        row.is_testnet = int(bool(detected['testnet']))
        db.commit()
        db.refresh(row)
        await asyncio.to_thread(_fetch_connection_settings, db, row)
        result['applied'] = {'applied': True, 'broker_code': row.broker_code,
                             'is_testnet': bool(row.is_testnet),
                             'label': row.label or row.broker_code,
                             'base_url': detected.get('base_url', '')}
        result['live_instances'] = await _adopt_saved_credentials(row, user.id)
    return result


class DeltaEnvironmentAlignPayload(BaseModel):
    environment: str


def _resolve_delta_environment(environment: str) -> Dict[str, Any]:
    """Canonical Delta environment dict for an align request, or a 400.

    The names are the ones the connection battery prints (INDIA-PRODUCTION,
    INDIA-TESTNET, GLOBAL-PRODUCTION, GLOBAL-TESTNET), with the same spelling
    tolerance as :meth:`BrokerClient.delta_environment`.
    """
    env = BrokerClient.delta_environment(environment)
    if env is None:
        known = ", ".join(h["name"] for h in BrokerClient.delta_hosts())
        raise HTTPException(
            status_code=400,
            detail=f"Unknown Delta environment {environment!r}. Use one of: {known}")
    return env


def _align_connection_to_environment(db, row: BrokerConnection,
                                     environment: str) -> Dict[str, Any]:
    """Point one saved Delta connection at a named environment (no key needed).

    The detection flow (``/test?apply=true``) needs the venue to accept the
    stored key before it can name the environment; when the operator already
    knows where the key belongs (e.g. it was just created on India
    production), this applies that decision directly — broker code + testnet
    flag — and lets the next signed call prove it. Returns the applied block.
    """
    env = _resolve_delta_environment(environment)
    code = _canonical_broker_code(db, row.broker_code)
    if not BrokerClient.is_delta_broker(code):
        raise HTTPException(status_code=400,
                            detail=f"Connection '{row.label or code}' is a {code} "
                                   "connection — only Delta (India) and DeltaGlobal "
                                   "connections have named environments to align.")
    # Deployment rail: aligning *onto* the Global family is refused on an
    # India-only box; aligning off it (the fix) stays allowed.
    if env["broker_code"] == "DeltaGlobal" \
            and not BrokerClient.delta_family_allowed("DeltaGlobal"):
        raise HTTPException(status_code=400, detail=BrokerClient.DELTA_FAMILY_RULE)
    row.broker_code = env["broker_code"]
    row.is_testnet = int(bool(env["testnet"]))
    db.commit()
    db.refresh(row)
    return {"applied": True, "environment": env["name"],
            "broker_code": row.broker_code, "is_testnet": bool(row.is_testnet),
            "label": row.label or row.broker_code, "base_url": env["url"]}


@app.post('/broker-connections/align-delta')
async def align_all_delta_connections(payload: DeltaEnvironmentAlignPayload,
                                      user=Depends(get_current_user),
                                      db=Depends(get_db)):
    """Point EVERY saved Delta-family connection of this login at one environment.

    The one-shot alignment for a deployment decision like "we trade Delta
    India production": every Delta / DeltaGlobal connection row is repointed
    (broker code + testnet flag) without needing the stored key to be accepted
    first — a key that was created on the target environment proves itself on
    the next signed call. Non-Delta connections are left alone. Running live
    instances on the repointed rows are handed the change immediately.
    """
    target = _resolve_delta_environment(payload.environment)
    if target["broker_code"] == "DeltaGlobal" \
            and not BrokerClient.delta_family_allowed("DeltaGlobal"):
        raise HTTPException(status_code=400, detail=BrokerClient.DELTA_FAMILY_RULE)
    rows = db.query(BrokerConnection).filter(
        BrokerConnection.user_id == user.id).all()
    delta_rows = [r for r in rows if BrokerClient.is_delta_broker(
        _canonical_broker_code(db, r.broker_code))]
    changed = []
    unchanged = []
    for row in delta_rows:
        before = (_canonical_broker_code(db, row.broker_code), bool(row.is_testnet))
        entry = {"connection_id": row.id, "label": row.label or row.broker_code,
                 "before": {"broker_code": before[0], "is_testnet": before[1]}}
        if before == (target["broker_code"], bool(target["testnet"])):
            unchanged.append(entry)
            continue
        row.broker_code = target["broker_code"]
        row.is_testnet = int(bool(target["testnet"]))
        db.commit()
        db.refresh(row)
        await asyncio.to_thread(_fetch_connection_settings, db, row)
        live = await _adopt_saved_credentials(row, user.id)
        entry.update({"broker_code": row.broker_code, "is_testnet": bool(row.is_testnet),
                      "account_settings": _connection_dict(row).get("account_settings"),
                      "live_instances": live})
        changed.append(entry)
    return {"environment": target["name"], "base_url": target["url"],
            "delta_connections": len(delta_rows), "changed": changed,
            "unchanged": unchanged}


@app.post('/broker-connections/{connection_id}/align')
async def align_broker_connection(connection_id: int,
                                  payload: DeltaEnvironmentAlignPayload,
                                  user=Depends(get_current_user),
                                  db=Depends(get_db)):
    """Point ONE saved connection at a named Delta environment, no key needed.

    The counterpart of ``?apply=true`` on the connection test for when the
    answer is already known: e.g. the key was just created on **Delta India
    production**, so align this connection to INDIA-PRODUCTION (REST
    ``https://api.india.delta.exchange``) and let the next signed call prove
    the key. Re-reads account details and hands the change to running
    instances, same as saving the connection would.
    """
    row = db.query(BrokerConnection).filter(BrokerConnection.id == connection_id,
                                            BrokerConnection.user_id == user.id).first()
    if not row: raise HTTPException(status_code=404, detail='Broker connection not found')
    applied = _align_connection_to_environment(db, row, payload.environment)
    await asyncio.to_thread(_fetch_connection_settings, db, row)
    db.refresh(row)
    out = {"connection_id": row.id, **applied,
           "account_settings": _connection_dict(row).get("account_settings")}
    out["live_instances"] = await _adopt_saved_credentials(row, user.id)
    return out


@app.post('/broker-connections/{connection_id}/refresh')
def refresh_broker_connection(connection_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Re-read account details (margin mode, leverage, sub-accounts) from the venue.

    Use after rotating a key, changing margin mode on the exchange, or moving
    funds between sub-accounts — the saved values are a cache, this re-pulls
    them. Doubles as a key check: an auth failure comes back as
    ``account_settings.error`` on the connection.
    """
    row = db.query(BrokerConnection).filter(BrokerConnection.id == connection_id,
                                            BrokerConnection.user_id == user.id).first()
    if not row: raise HTTPException(status_code=404, detail='Broker connection not found')
    settings = _fetch_connection_settings(db, row)
    db.refresh(row)
    out = _connection_dict(row)
    out['fetched'] = settings
    return out


@app.delete('/broker-connections/{connection_id}')
def delete_broker_connection(connection_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    row = db.query(BrokerConnection).filter(BrokerConnection.id == connection_id, BrokerConnection.user_id == user.id).first()
    if not row: raise HTTPException(status_code=404, detail='Broker connection not found')
    db.delete(row); db.commit(); return {'status': 'Broker connection removed'}


class MarketSeedPayload(BaseModel):
    source: str = 'Delta'
    symbol: str = 'BTCUSDT'
    # None lets the adapter choose its safe default. Delta defaults to the
    # full-history-compatible 15m/1h/4h/1d set; Binance keeps all app candles.
    intervals: Optional[List[str]] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    limit: int = 1000
    fetch_all: bool = False
    # Run the seed in a background worker instead of the request thread. Long
    # full-history walks then cannot be killed by a browser/proxy request
    # timeout; the client polls /admin/market-data/progress instead.
    background: bool = False
    # Remove duplicate and off-grid candles from the selected series before
    # fetching. This is the repair path for a corrupted seed (legacy CSV
    # imports carried timestamps off the candle grid and the legacy seeder
    # inserted batches without an upsert, duplicating every candle).
    repair: bool = False
    # Also seed the mark-price series for the BTC perpetual. Risk maths runs on
    # the mark price, so without it a backtest silently falls back to the
    # traded price (and says so through mark_price_basis).
    include_mark_price: bool = False


def _market_definition(db, source):
    """Resolve an enabled broker definition for API seeding/syncing."""
    normalized = normalize_source(source)
    row = db.query(BrokerDefinition).filter(BrokerDefinition.code == normalized).first()
    if row is not None and not row.enabled:
        raise HTTPException(status_code=400, detail=f"Market-data source '{normalized}' is disabled")
    if row is None and normalized not in ('Binance', 'Delta'):
        raise HTTPException(status_code=404, detail=f"Broker integration '{normalized}' not found")
    return normalized, row


# One background seed at a time; state is polled by the admin UI.
_seed_job_state = {'running': False, 'started_at': None, 'source': None, 'symbol': None,
                   'intervals': None, 'fetch_all': None, 'last': None}
_seed_job_state_lock = threading.Lock()


def _seed_job_snapshot():
    return {key: _seed_job_state.get(key) for key in
            ('running', 'started_at', 'source', 'symbol', 'intervals', 'fetch_all')}


def _validated_seed_request(db, source, symbol, intervals):
    """Shared payload validation for the sync and background seed paths."""
    source, definition = _market_definition(db, source)
    kind = DataSyncService._adapter_kind(source, definition)
    if intervals is not None:
        intervals = [str(interval).lower() for interval in intervals
                     if str(interval).lower() in DataSyncService.TIMEFRAMES]
        if not intervals:
            raise HTTPException(status_code=400, detail='Select at least one valid interval')
    if kind == 'delta' and intervals:
        excluded = sorted(set(intervals) & DataSyncService.DELTA_EXCLUDED_INTERVALS)
        if excluded:
            raise HTTPException(
                status_code=400,
                detail=(f"Delta Exchange full-history seeding excludes {', '.join(excluded)}. "
                        f"Select only {', '.join(DataSyncService.DELTA_HISTORY_INTERVALS)}."),
            )
    return source, definition, kind, intervals


def _run_seed_job(source, symbol, intervals, start_date, end_date, limit,
                  fetch_all, repair, include_mark_price):
    """Repair + fetch + mark-price seed for one request.

    Runs in the request thread (synchronous seed) or in the background worker
    thread; opens its own DB session so ORM objects never cross threads.
    """
    db = SessionLocal()
    try:
        source, definition, kind, intervals = _validated_seed_request(db, source, symbol, intervals)
        # Optional repair pass runs first so the seed below upserts into a
        # clean series: it removes duplicate timestamps (legacy batch inserts)
        # and candles whose timestamps are off the interval grid (legacy CSV
        # imports). A failure here never blocks the fetch itself.
        repair_summary = []
        if repair:
            effective_intervals = intervals if intervals is not None else (
                DataSyncService.DELTA_HISTORY_INTERVALS if kind == 'delta'
                else DataSyncService.TIMEFRAMES)
            try:
                repair_summary = DataSyncService.repair_klines(
                    source, symbol, effective_intervals, definition=definition)
            except Exception as exc:
                repair_summary = [{'error': str(exc)}]
        summary = DataSyncService.seed_market_data(
            source=source, symbol=symbol, intervals=intervals,
            start_date=start_date, end_date=end_date,
            limit=max(1, min(limit, DataSyncService.DELTA_MAX_CANDLES)),
            fetch_all=fetch_all, definition=definition,
        )
        # Per-interval failures are reported in the summary instead of 502 so
        # the admin can see exactly which interval failed and why.
        ok = sum(1 for item in summary if not item.get('error'))
        status = 'Seed completed' if ok == len(summary) else ('Seed failed' if ok == 0 else 'Seed completed with errors')
        mark_summary = []
        if include_mark_price and ok:
            # Traded candles exist now, so the mark series can be attached to
            # them — paged across the whole seeded range, not just one page.
            # A failure here never fails the seed itself.
            try:
                mark_start = start_date or (
                    DataSyncService.FULL_HISTORY_START.strftime('%Y-%m-%d') if fetch_all else None)
                mark_summary = DataSyncService.sync_mark_prices(
                    source=source, symbol=symbol, intervals=intervals,
                    start_time=mark_start, end_time=end_date,
                    limit=max(1, min(limit, DataSyncService.DELTA_MAX_CANDLES)),
                    definition=definition,
                )
            except Exception as exc:
                mark_summary = [{'error': str(exc)}]
        return {'status': status, 'source': source, 'symbol': symbol,
                'fetch_all': fetch_all, 'summary': summary,
                'repair': {'requested': bool(repair),
                           'removed': sum(int(item.get('removed', 0)) for item in repair_summary
                                          if not item.get('error')),
                           'summary': repair_summary},
                'mark_price': {'requested': bool(include_mark_price),
                               'perpetual_symbol': perpetual_symbol(source, symbol),
                               'summary': mark_summary}}
    finally:
        db.close()


def _seed_job_worker(source, symbol, intervals, start_date, end_date, limit,
                     fetch_all, repair, include_mark_price):
    """Background seed entry point; stores the result for /seed-job polling."""
    try:
        # One seed at a time, and never on top of the daily refresh cycle.
        with DataSyncService.DAILY_SYNC_LOCK:
            result = _run_seed_job(source, symbol, intervals, start_date, end_date,
                                   limit, fetch_all, repair, include_mark_price)
    except Exception as exc:
        result = {'status': 'Seed failed', 'error': str(exc), 'summary': []}
    with _seed_job_state_lock:
        _seed_job_state['running'] = False
        _seed_job_state['last'] = result


@app.get('/admin/market-data/status')
def market_data_status(health: bool = False, admin=Depends(require_admin), db=Depends(get_db)):
    """Per-series inventory. `duplicate_rows` is always reported (SQL
    count/distinct). With ?health=1 the response also carries `misaligned_rows`
    — candles whose timestamp is off the interval grid, the signature of the
    corrupted CSV imports — via an exact (capped) scan."""
    rows = db.query(
        Klines.source, Klines.symbol, Klines.interval,
        func.count(Klines.id).label('count'), func.count(Klines.volume).label('volume_rows'),
        func.count(func.distinct(Klines.event_time)).label('distinct_times'),
        func.min(Klines.event_time).label('first'), func.max(Klines.event_time).label('last'),
    ).group_by(Klines.source, Klines.symbol, Klines.interval).order_by(
        Klines.source, Klines.symbol, Klines.interval).all()
    health_by_key = {}
    if health:
        for item in DataSyncService.data_health():
            health_by_key[(item['source'], item['symbol'], item['interval'])] = item
    result = []
    for source, symbol, interval, count, volume_rows, distinct_times, first, last in rows:
        source = source or 'Delta'
        entry = {'source': source, 'symbol': symbol, 'interval': interval,
                 'count': int(count), 'volume_rows': int(volume_rows),
                 'duplicate_rows': int(count) - int(distinct_times),
                 'first': first, 'last': last}
        if health:
            item = health_by_key.get((source, symbol, interval), {})
            entry['misaligned_rows'] = item.get('misaligned_rows')
            entry['scanned_rows'] = item.get('scanned', 0)
        result.append(entry)
    return result


@app.post('/admin/market-data/seed')
def seed_market_data(payload: MarketSeedPayload, admin=Depends(require_admin), db=Depends(get_db)):
    # Validate in the request thread so bad payloads fail fast with a 4xx.
    _validated_seed_request(db, payload.source, payload.symbol.upper(), payload.intervals)
    if payload.background:
        # A 2020 → today walk over several intervals is thousands of bounded
        # requests and can legitimately run for a long time. Running it in the
        # request thread ties it to the browser/proxy request timeout — the
        # classic "long seed breaks" failure. The durable cursor makes the job
        # resumable, so it runs in a worker thread and the client polls
        # /admin/market-data/progress instead of holding a request open.
        with _seed_job_state_lock:
            if _seed_job_state['running']:
                return {'status': 'Seed already running in background', 'background': True,
                        'job': _seed_job_snapshot(), 'summary': []}
            _seed_job_state.update(
                running=True, started_at=datetime.utcnow(), last=None,
                source=payload.source, symbol=payload.symbol.upper(),
                intervals=payload.intervals, fetch_all=payload.fetch_all)
        threading.Thread(
            target=_seed_job_worker, daemon=True,
            args=(payload.source, payload.symbol.upper(), payload.intervals, payload.start_date,
                  payload.end_date, payload.limit, payload.fetch_all, payload.repair,
                  payload.include_mark_price),
        ).start()
        return {'status': 'Seed started in background', 'background': True,
                'job': _seed_job_snapshot(),
                'note': ('The seed runs server-side and survives this request; watch '
                         'GET /admin/market-data/progress or the Historical seed progress table. '
                         'Completed ranges are skipped and an interrupted range resumes at its '
                         'committed cursor, so re-running never refetches finished windows.'),
                'summary': []}
    try:
        return _run_seed_job(payload.source, payload.symbol.upper(), payload.intervals,
                             payload.start_date, payload.end_date, payload.limit,
                             payload.fetch_all, payload.repair, payload.include_mark_price)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get('/admin/market-data/seed-job')
def market_data_seed_job(admin=Depends(require_admin)):
    """Live state of the background seed worker (running flag, request and
    last result) so the UI can poll without holding the seed request open."""
    with _seed_job_state_lock:
        return dict(_seed_job_state)


@app.post('/admin/market-data/repair')
def repair_market_data(source: str = 'Delta', symbol: str = 'BTCUSD', intervals: Optional[str] = None,
                       admin=Depends(require_admin), db=Depends(get_db)):
    """Remove corrupted candles without fetching: duplicate timestamps (legacy
    batch inserts stored each candle again) and off-grid timestamps (legacy
    CSV imports, e.g. 11:41:59.523330 on a 1h series). Well-formed rows are
    never touched. Follow up with a seed to refill whatever was removed."""
    try:
        source, definition = _market_definition(db, source)
        wanted = [interval.strip().lower() for interval in (intervals or '').split(',') if interval.strip()] or None
        summary = DataSyncService.repair_klines(source, symbol.upper(), wanted, definition=definition)
        removed = sum(int(item.get('removed', 0)) for item in summary)
        status = 'Repair completed' if not any(item.get('error') for item in summary) else 'Repair completed with errors'
        return {'status': f'{status} — {removed} corrupt candles removed', 'summary': summary}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get('/admin/market-data/progress')
def market_data_seed_progress(admin=Depends(require_admin), db=Depends(get_db)):
    """Show durable historical-seed cursors for restart/resume diagnostics."""
    rows = db.query(MarketDataSeedProgress).order_by(
        MarketDataSeedProgress.updated_at.desc(), MarketDataSeedProgress.id.desc(),
    ).limit(100).all()
    return [{
        'source': row.source, 'definition': row.definition_key,
        'symbol': row.symbol, 'interval': row.interval,
        'requested_start': row.requested_start, 'requested_end': row.requested_end,
        'next_start': row.next_start, 'status': row.status,
        'pages': row.pages, 'empty_pages': row.empty_pages,
        'fetched': row.fetched, 'inserted': row.inserted, 'updated': row.updated,
        'last_error': row.last_error, 'updated_at': row.updated_at,
        'completed_at': row.completed_at,
    } for row in rows]


@app.get('/admin/market-data/test')
def test_market_data_source(source: str = 'Delta', symbol: str = 'BTCUSD', interval: str = '1h',
                            admin=Depends(require_admin), db=Depends(get_db)):
    """Round-trip probe: shows exactly what the exchange answers for a tiny
    request, so an empty seed (0 candles) can be diagnosed from the UI."""
    source, definition = _market_definition(db, source)
    interval = str(interval).lower()
    if interval not in DataSyncService.TIMEFRAMES:
        raise HTTPException(status_code=400, detail='Invalid interval')
    if DataSyncService._adapter_kind(source, definition) == 'delta' and interval in DataSyncService.DELTA_EXCLUDED_INTERVALS:
        raise HTTPException(status_code=400, detail='Delta connection tests exclude 1m and 5m; use 15m, 1h, 4h, or 1d')
    return DataSyncService.test_source(source, symbol.upper(), interval, definition=definition)


@app.post('/admin/market-data/sync-now')
def sync_market_data_now(admin=Depends(require_admin)):
    """Run the same incremental multi-source cycle used by the daily task."""
    try:
        summary = DataSyncService.sync_all_configured_sources_daily()
        ok = sum(1 for item in summary if not item.get('error'))
        status = 'Daily refresh completed' if ok == len(summary) else ('Daily refresh failed' if ok == 0 else 'Daily refresh completed with errors')
        return {'status': status, 'summary': summary}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post('/admin/market-data/seed-csv')
async def seed_market_data_csv(source: str = 'Delta', symbol: str = 'BTCUSD', interval: str = '1h',
                         clear_existing: bool = False, file: UploadFile = File(...), admin=Depends(require_admin)):
    try:
        import tempfile
        from .services.data_sync import DataSyncService
        content = await file.read() if hasattr(file, 'read') else file.file.read()
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as handle:
            handle.write(content); path = handle.name
        try:
            result = DataSyncService.seed_from_csv(path, interval, symbol.upper(), normalize_source(source), clear_existing)
        finally:
            os.unlink(path)
        return {'status': 'CSV imported with OHLCV volume', 'summary': result}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# --- BTC PERPETUAL: contract, mark price and "skip new trades" schedule ---
@app.get("/market/contract")
def market_contract(source: str = "Delta", user=Depends(get_current_user), db=Depends(get_db)):
    """Which contract the tool trades on a venue.

    Both venues are wired to the BTC **perpetual** (BTCUSDT on Binance, BTCUSD
    on Delta). Dated futures are never substituted.
    """
    code = normalize_source(source)
    definition = db.query(BrokerDefinition).filter(BrokerDefinition.code == code).first()
    return {
        "source": code,
        "symbol": "BTCUSDT",
        "perpetual_symbol": perpetual_symbol(code, "BTCUSDT"),
        "contract": contract_label(code, "BTCUSDT"),
        "contract_type": "perpetual",
        "enabled": bool(getattr(definition, "enabled", 1)) if definition else False,
    }


@app.get("/market/mark-price")
def market_mark_price(source: str = "Delta", symbol: str = "BTCUSD",
                      user=Depends(get_current_user), db=Depends(get_db)):
    """Live mark price (and traded price) of the BTC perpetual.

    Used by the UI to show exactly which price risk is being managed on.
    """
    code = normalize_source(source)
    definition = db.query(BrokerDefinition).filter(BrokerDefinition.code == code).first()
    quote = MarkPriceService.current(code, symbol, definition=definition)
    if quote is None:
        raise HTTPException(status_code=502,
                            detail=f"Mark price unavailable for {code} {perpetual_symbol(code, symbol)}. "
                                   f"The public market-data endpoint did not answer.")
    payload = quote.as_dict()
    payload.update({
        "contract_type": "perpetual",
        "use_mark_price": bool(True if getattr(user, 'use_mark_price', None) is None else bool(user.use_mark_price)),
    })
    return payload


class TradingWindowsPayload(BaseModel):
    """Account-level schedule used by Backtest / Paper / Live by default."""
    enabled: Optional[bool] = None
    timezone: Optional[str] = None
    utc_offset_minutes: Optional[int] = None
    block_exits: Optional[bool] = None
    windows: Optional[List[Dict]] = None
    # Convenience: replace the whole schedule with these weekday blocks.
    quick_days: Optional[List[Any]] = None


@app.get("/trading-windows")
def get_trading_windows(user=Depends(get_current_user)):
    """The signed-in account's default "skip new trades" schedule."""
    config = _user_window_config(user)
    guard = TradingWindowGuard(config)
    return {
        **guard.summary(),
        "use_mark_price": bool(True if getattr(user, 'use_mark_price', None) is None else bool(user.use_mark_price)),
        "now_local": guard.local_now().isoformat(timespec="minutes"),
        "entry_blocked_now": guard.is_blocked(datetime.utcnow()),
        "next_open": (guard.next_open_from(datetime.utcnow()).isoformat(timespec="minutes")
                      if guard.next_open_from(datetime.utcnow()) else None),
    }


@app.put("/trading-windows")
def save_trading_windows(payload: TradingWindowsPayload, user=Depends(get_current_user), db=Depends(get_db)):
    """Persist the account default used when a start request omits a schedule."""
    try:
        if payload.quick_days is not None:
            # Shortcut used by the UI's day chips: replace the schedule with
            # one all-day block per selected weekday.
            from .core.trading_windows import all_day_window
            windows = [all_day_window(day) for day in payload.quick_days]
            config = TradingWindowConfig(
                enabled=bool(payload.enabled) if payload.enabled is not None else True,
                timezone=payload.timezone or "Asia/Kolkata",
                block_exits=bool(payload.block_exits or False),
                windows=windows,
            )
        else:
            updates = {k: v for k, v in payload.model_dump(exclude_none=True).items()
                       if k != "quick_days"}
            merged = {**_user_window_config(user).model_dump(), **updates}
            config = TradingWindowConfig(**merged)
        user.trading_windows_json = json.dumps(config.model_dump())
        db.commit()
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Invalid trading windows: {exc}")
    guard = TradingWindowGuard(config)
    return {"status": "Trading windows saved", **guard.summary()}


# --- PHANTOM v3: config + signal overlay for charts ---
_LOGS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'logs'))

def _champion_config_path() -> Optional[str]:
    # Prefer high-leverage champion (lev 7 / margin 0.25) so 20k INR can trade at 100k BTC.
    # Low-DD profile (lev2/margin0.15) needs ~32k+ at that price and yields LOT_TOO_SMALL.
    for name in ('champion_config.json', 'champion_lowdd_config.json'):
        path = os.path.join(_LOGS_DIR, name)
        if os.path.exists(path):
            return path
    return None

def _load_champion_config() -> PhantomV2Config:
    """Best-known tuned config with tradable sizing guard."""
    path = _champion_config_path()
    if path:
        try:
            with open(path) as f:
                kw = json.load(f)
            cfg = PhantomV2Config(**{k: v for k, v in kw.items() if k in PhantomV2Config.model_fields})
            if cfg.leverage < 5:
                cfg = cfg.model_copy(update={'leverage': 5})
            if cfg.margin_pct < 0.20:
                cfg = cfg.model_copy(update={'margin_pct': 0.20})
            return cfg
        except Exception:
            pass
    return PhantomV2Config()

@app.get("/phantom/config")
def phantom_config(user=Depends(get_current_user)):
    cfg = _load_champion_config()
    path = _champion_config_path()
    return {"profile": os.path.basename(path) if path else 'v2.5-defaults',
            "config": cfg.model_dump()}

@app.get("/phantom/signals")
def phantom_signals(start_date: Optional[str] = None, end_date: Optional[str] = None,
                    symbol: str = "BTCUSDT", strategy_id: Optional[str] = "PhantomV2",
                    source: str = "Delta", user=Depends(get_current_user), db=Depends(get_db)):
    """Signal candles for the chart overlay.

    By default it overlays the tuned Phantom champion config. Pass
    `strategy_id` to overlay the signals of a specific strategy instead
    (e.g. a custom strategy created in the Strategies manager, or "FastTest").
    """
    if strategy_id == "PhantomV2":
        cfg = _load_champion_config()
        engine = BacktestEngine(cfg)
        strategy_service = engine.strategy_service
        wants_metadata = True
        label = None
    elif strategy_id == "FastTest":
        cfg = PhantomV2Config()
        engine = BacktestEngine(cfg)
        from .core.strategy import FastTestStrategyService
        strategy_service = FastTestStrategyService(cfg)
        wants_metadata = False
        label = "FastTest"
    else:
        try:
            strat = db.query(CustomStrategy).filter(
                CustomStrategy.id == int(strategy_id),
                CustomStrategy.user_id == user.id,
            ).first()
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid strategy id")
        if not strat:
            raise HTTPException(status_code=404, detail="Custom strategy not found")
        resolved = _resolve_strategy_payload(db, strategy_id, user.id, None)
        if resolved and resolved[0] == 'phantom':
            cfg = resolved[1]
            engine = BacktestEngine(cfg)
            strategy_service = engine.strategy_service
            wants_metadata = True
            label = strat.name
        else:
            from .core.dynamic_strategy import DynamicStrategyService
            cfg = PhantomV2Config()
            engine = BacktestEngine(cfg)
            strategy_service = DynamicStrategyService(strat.rules)
            wants_metadata = False
            label = strat.name

    df_1h = engine._get_data_from_db(symbol, "1h", start_date, end_date, normalize_source(source))
    df_4h = engine._get_data_from_db(symbol, "4h", start_date, end_date, normalize_source(source))
    if df_1h.empty or df_4h.empty:
        return []

    if wants_metadata:
        signals, meta = strategy_service.generate_signals_with_metadata(df_1h, df_4h)
    else:
        signals = strategy_service.generate_signals(df_1h, df_4h)
        meta = None

    out = []
    closes = df_1h['close'].values
    for i in range(1, len(df_1h)):
        s = signals[i]
        if s == 0:
            continue
        direction = int(s)
        item = {
            "time": int(_utc_ts(df_1h.index[i])),
            "direction": direction,
            "side": "LONG" if direction == 1 else "SHORT",
            "price": float(closes[i]),
            "setup": label or "CUSTOM",
            "rsi14": None,
            "adx": None,
            "macd_hist": None,
            "trend": None,
            "trend_label": None,
            "candle_type": None,
        }
        if meta is not None:
            item["setup"] = str(meta['setup'][i])
            try:
                item["rsi14"] = round(float(meta['rsi14'][i]), 2)
            except Exception:
                pass
            try:
                item["adx"] = round(float(meta['adx'][i]), 3)
            except Exception:
                pass
            try:
                item["macd_hist"] = round(float(meta['macd_hist'][i]), 4)
            except Exception:
                pass
            try:
                trend = int(meta['trend'][i])
                item["trend"] = trend
                item["trend_label"] = "UP" if trend == 1 else ("DOWN" if trend == -1 else "FLAT")
            except Exception:
                pass
            try:
                if bool(meta['is_green'][i]):
                    item["candle_type"] = "GREEN"
                elif bool(meta['is_red'][i]):
                    item["candle_type"] = "RED"
                else:
                    item["candle_type"] = "DOJI"
            except Exception:
                pass
        out.append(item)
        if len(out) >= 2000:
            break
    return out

# --- BACKTESTING ---
def execute_backtest_task(run_id: int, req: BacktestRequest, user_id: int):
    db = SessionLocal()
    try:
        start_date_str = req.start_date or "2020-07-04"
        end_date_str = req.end_date or "2026-07-04"

        # Resolve starting capital: run record (already stored by run_backtest),
        # else explicit request value, else the user's (admin-set default)
        # initial capital, else the engine default.
        run0 = db.query(BacktestRun).filter(BacktestRun.id == run_id).first()
        user = db.query(User).filter(User.id == user_id).first()
        default_capital = (user.initial_capital if user and user.initial_capital else 20000.0)
        capital = float(run0.initial_capital) if (run0 and run0.initial_capital) else (float(req.initial_capital) if req.initial_capital else float(default_capital))

        source = normalize_source(req.data_source)
        fees = resolve_fees(db, source, req.fee_mode, req.params)
        config = _fee_config(req.params, fees)
        if req.strategy_id == "PhantomV2":
            engine = BacktestEngine(config=config, fee_schedule=fees, data_source=source)
        else:
            resolved = _resolve_strategy_payload(db, req.strategy_id, user_id, fees)
            if not resolved: return
            kind, payload, strat = resolved
            if kind == 'phantom':
                # A saved Phantom params config (may include entry_conditions).
                engine = BacktestEngine(config=payload, fee_schedule=fees, data_source=source)
            else:
                from .core.dynamic_strategy import DynamicStrategyService
                class DynamicBacktestEngine:
                    def __init__(self, rules):
                        self.config = _fee_config(PhantomV2Config(), fees)
                        self.strategy_service = DynamicStrategyService(rules)
                        from .core.strategy import ValidatorService
                        self.validator_service = ValidatorService()
                        from .services.order_manager import OrderManager
                        self.oms = OrderManager(self.config)
                    def run(self, symbol="BTCUSDT", initial_capital_inr=20000, start_date=None, end_date=None, conversion_rate=None):
                        original_engine = BacktestEngine(self.config, fee_schedule=fees, data_source=source)
                        original_engine.strategy_service = self.strategy_service
                        from .services.app_settings import get_usd_inr_rate
                        rate = float(conversion_rate) if conversion_rate else get_usd_inr_rate()
                        # NOTE: pass by keyword - the engine signature is
                        # run(symbol, initial_capital_inr, conversion_rate, start_date, end_date, ...)
                        return original_engine.run(symbol=symbol, initial_capital_inr=initial_capital_inr, conversion_rate=rate, start_date=start_date, end_date=end_date)
                engine = DynamicBacktestEngine(payload)

        # The same admin-controlled USD->INR rate the workers use, so a
        # backtest's rupee figures line up with what paper/live would report.
        from .services.app_settings import get_usd_inr_rate
        results = engine.run(symbol="BTCUSDT", initial_capital_inr=capital, conversion_rate=get_usd_inr_rate(),
                             start_date=start_date_str, end_date=end_date_str)
        
        run = db.query(BacktestRun).filter(BacktestRun.id == run_id).first()
        if run:
            run.final_equity = float(results['final_equity_inr'])
            run.total_trades = int(results['total_trades'])
            run.win_rate = float(results['win_rate'])
            run.profit_factor = float(results['profit_factor'])
            run.sharpe_ratio = float(results['sharpe_ratio'])
            run.max_drawdown = float(results['max_drawdown'])
            run.roi = float(results['roi'])
            run.equity_curve = results['equity_curve']
            run.rejected_reasons = json.dumps(results.get('rejected_reasons', {}))
            run.data_source = source
            run.fee_mode = req.fee_mode
            run.taker_fee_bps = float(fees.taker_fee_bps)
            run.maker_fee_bps = float(fees.maker_fee_bps)
            # What the run actually priced on (mark vs traded) and how many
            # entries the "skip new trades" schedule refused.
            run.use_mark_price = int(bool(results.get('mark_price_basis', True)))
            run.trading_windows_enabled = int(bool(
                results.get('trading_windows', {}).get('active', False)))
            run.blocked_entries = int(results.get('diagnostics', {}).get('blocked_entries', 0) or 0)
            
            trade_cols = {c.name for c in Trade.__table__.columns}
            for t in results['trades']:
                row = {k: v for k, v in t.items() if k in trade_cols}
                # SQLite stores bools as ints; normalise numpy/pandas scalars
                for k, v in row.items():
                    if hasattr(v, 'item'):
                        try: row[k] = v.item()
                        except Exception: pass
                    if isinstance(row.get(k), bool):
                        row[k] = int(row[k])
                db.add(Trade(run_id=run.id, **row))
            db.commit()
    except Exception as e:
        print(f"Backtest Task Error: {e}")
        db.rollback()
    finally:
        db.close()

@app.post("/backtest")
def run_backtest(req: BacktestRequest, user=Depends(get_current_user), db=Depends(get_db), background_tasks: BackgroundTasks = BackgroundTasks()):
    try:
        # Fold the optional top-level mark-price / trading-window switches into
        # the parameter snapshot so they are saved with the run.
        req = _apply_run_overrides(req)
        start_date_str = req.start_date or "2020-07-04"
        end_date_str = req.end_date or "2026-07-04"
        start_date_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date_dt = datetime.strptime(end_date_str, "%Y-%m-%d")

        # Resolve the starting capital once so it is stored on the run and
        # reused by the background task: explicit value, else the user's default.
        capital = float(req.initial_capital) if req.initial_capital else float(user.initial_capital or 20000.0)

        # Create a placeholder run record
        run = BacktestRun(
            user_id=user.id,
            name=req.strategy_name,
            strategy_id=req.strategy_id,
            start_date=start_date_dt,
            end_date=end_date_dt,
            config_json=json.dumps(req.params.model_dump()),
            initial_capital=capital,
            data_source=normalize_source(req.data_source),
            fee_mode=req.fee_mode,
            # BTC perpetual pricing + "skip new trades" schedule for this run.
            use_mark_price=int(bool(getattr(req.params, 'use_mark_price', True))),
            trading_windows_enabled=int(bool(getattr(
                getattr(req.params, 'trading_windows', None), 'enabled', False))),
            roi=0.0 # Placeholder
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        background_tasks.add_task(execute_backtest_task, run.id, req, user.id)
        return {"run_id": run.id, "status": "Started in background"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/backtest/history")
def get_backtest_history(user=Depends(get_current_user), db=Depends(get_db)):
    runs = db.query(BacktestRun).filter(BacktestRun.user_id == user.id).order_by(BacktestRun.timestamp.desc()).all()
    return [{"id": r.id, "name": r.name, "strategy_id": r.strategy_id or 'PhantomV2',
             "start_date": r.start_date, "end_date": r.end_date,
             "roi": r.roi, "initial_capital": r.initial_capital or 20000,
             "data_source": r.data_source or 'Delta', "taker_fee_bps": r.taker_fee_bps,
             "maker_fee_bps": r.maker_fee_bps, "timestamp": r.timestamp,
             "use_mark_price": int(r.use_mark_price) if r.use_mark_price is not None else 1,
             "blocked_entries": int(r.blocked_entries or 0)} for r in runs]

@app.get("/backtest/results/{run_id}")
def get_backtest_results(run_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    run = db.query(BacktestRun).filter(BacktestRun.id == run_id, BacktestRun.user_id == user.id).first()
    if not run: raise HTTPException(status_code=404, detail="Run not found")
    trades = db.query(Trade).filter(Trade.run_id == run_id).all()
    trade_list = [{ "entry_time": t.entry_time, "exit_time": t.exit_time, "direction": t.direction, "entry_price": t.entry_price, "exit_price": t.exit_price, "lots": t.lots, "margin": t.margin, "notional": t.notional, "net_pnl": t.net_pnl, "fees": t.fees, "exit_reason": t.exit_reason, "equity_after": t.equity_after, "drawdown": t.drawdown, "hold_bars": t.hold_bars,
                    # PHANTOM v3: entry-condition snapshot (candle, setup, indicators)
                    "signal_candle_time": t.signal_candle_time, "setup": t.setup,
                    "candle_type": t.candle_type, "trend_4h": t.trend_4h,
                    # Which candle signalled vs which candle the entry filled on,
                    # and the colour of each — plus the colour of the exit candle.
                    "signal_candle_type": t.signal_candle_type or t.candle_type,
                    "entry_candle_time": t.entry_candle_time,
                    "entry_candle_type": t.entry_candle_type,
                    "exit_candle_type": t.exit_candle_type,
                    # Full readable entry/exit condition breakdown for the
                    # trade log and the Excel/CSV export.
                    "entry_conditions_detail": t.entry_conditions_detail,
                    "exit_detail": t.exit_detail,
                    "rsi14": t.rsi14, "macd_hist": t.macd_hist, "adx": t.adx,
                    "atr14": t.atr14, "ema50_1h": t.ema50_1h, "ema50_4h": t.ema50_4h,
                    "conditions": {
                        "trend_ok": t.cond_trend_ok,
                        "adx_ok": t.cond_adx_ok, "macd_hist_ok": t.cond_macd_hist_ok,
                        "atr_regime_ok": t.cond_atr_regime_ok, "rsi_ok": t.cond_rsi_ok,
                        "macd_confirm_ok": t.cond_macd_confirm_ok,
                        "di_ok": t.cond_di_ok,
                    },
                    # BTC perpetual: the traded price and the exchange mark
                    # price are both persisted; entry/exit_price are the basis
                    # the PnL was actually computed on.
                    "entry_trade_price": t.entry_trade_price,
                    "exit_trade_price": t.exit_trade_price,
                    "entry_mark_price": t.entry_mark_price,
                    "exit_mark_price": t.exit_mark_price,
                    "mark_price_basis": t.mark_price_basis,
                    "gross_pnl": t.gross_pnl, "sl": t.sl, "tp": t.tp,
                    "sl_entry": t.sl_entry, "trail_stop": t.trail_stop,
                    "atr_at_entry": t.atr_at_entry, "peak_price": t.peak_price,
                    "entry_dd_pct": t.entry_dd_pct, "margin_pct_used": t.margin_pct_used,
                    "equity_at_entry": t.equity_at_entry } for t in trades]
    return {
        "run_details": {
            "id": run.id,
            "name": run.name,
            "strategy_id": run.strategy_id or 'PhantomV2',
            # The exact parameter snapshot is returned with a run so the UI can
            # restore the form when a historical card is opened.
            "params": _parse_run_params(run.config_json),
            "start_date": run.start_date,
            "end_date": run.end_date,
            "initial_capital": run.initial_capital or 20000,
            "final_equity": run.final_equity,
            "total_trades": run.total_trades,
            "win_rate": run.win_rate,
            "profit_factor": run.profit_factor,
            "sharpe_ratio": run.sharpe_ratio,
            "max_drawdown": run.max_drawdown,
            "roi": run.roi,
            "equity_curve": run.equity_curve,
            "data_source": run.data_source or 'Delta',
            "fee_mode": run.fee_mode or 'backtest',
            "taker_fee_bps": run.taker_fee_bps,
            "maker_fee_bps": run.maker_fee_bps,
            "timestamp": run.timestamp,
            "rejected_reasons": json.loads(run.rejected_reasons) if run.rejected_reasons else {},
            # BTC perpetual pricing + the blackout schedule that shaped the run.
            "use_mark_price": int(run.use_mark_price) if run.use_mark_price is not None else 1,
            "trading_windows_enabled": int(run.trading_windows_enabled or 0),
            "blocked_entries": int(run.blocked_entries or 0),
            "contract": contract_label(run.data_source or 'Delta', "BTCUSDT"),
        },
        "trades": trade_list
    }

@app.delete("/backtest/clear")
def clear_backtest_history(user=Depends(get_current_user), db=Depends(get_db)):
    try:
        # Delete trades associated with the user's runs first
        run_ids = [r.id for r in db.query(BacktestRun).filter(BacktestRun.user_id == user.id).all()]
        if run_ids:
            db.query(Trade).filter(Trade.run_id.in_(run_ids)).delete()
            db.query(BacktestRun).filter(BacktestRun.user_id == user.id).delete()
        db.commit()
        return {"status": "Backtest history cleared successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error clearing history: {str(e)}")


@app.delete("/backtest/{run_id}")
def delete_backtest_run(run_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Delete a single backtest run and its associated trades."""
    try:
        run = db.query(BacktestRun).filter(BacktestRun.id == run_id, BacktestRun.user_id == user.id).first()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        run_name = run.name
        # Delete associated trades first
        db.query(Trade).filter(Trade.run_id == run_id).delete()
        db.delete(run)
        db.commit()
        return {"status": f"Backtest run '{run_name}' (#{run_id}) deleted"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error deleting run: {str(e)}")

# --- FILTER PREVIEW (per bucket, before the full run) --------------------
class FilterPreviewRequest(BaseModel):
    params: StrategyParams
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    symbol: str = "BTCUSD"
    data_source: str = 'Delta'
    fee_mode: str = 'backtest'
    use_mark_price: Optional[bool] = None
    trading_windows: Optional[TradingWindowConfig] = None
    initial_capital: Optional[float] = None

@app.post("/backtest/filter-preview")
def filter_preview(req: FilterPreviewRequest, user=Depends(get_current_user), db=Depends(get_db)):
    """Quick per-bucket peek at the conditions currently in the form.

    Runs a lightweight backtest with the given params and buckets trades by
    LONG/SHORT x REVERSAL/MOMENTUM. Now also returns rejected_reasons and
    diagnostics so LOT_TOO_SMALL is visible instead of silent 0 trades.
    """
    try:
        req = _apply_run_overrides(req)
        source = normalize_source(req.data_source)
        fees = resolve_fees(db, source, req.fee_mode, req.params)
        config = _fee_config(req.params, fees)
        engine = BacktestEngine(config=config, fee_schedule=fees, data_source=source)
        start = req.start_date or "2020-07-04"
        end = req.end_date or "2026-07-04"
        # Use explicit capital or user's default or 50000 (tradable at 100k BTC)
        cap = float(req.initial_capital) if req.initial_capital else float(user.initial_capital or 50000.0)
        if cap < 20000:
            cap = 50000.0
        from .services.app_settings import get_usd_inr_rate
        results = engine.run(symbol=req.symbol, initial_capital_inr=cap,
                             conversion_rate=get_usd_inr_rate(),
                             start_date=start, end_date=end)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Filter preview error: {str(e)}")

    buckets = {}
    for t in results['trades']:
        side = 'LONG' if t['direction'] == 1 else 'SHORT'
        setup = (t.get('setup') or 'UNKNOWN').upper()
        key = f"{side}_{setup}"
        b = buckets.setdefault(key, {
            'count': 0, 'wins': 0, 'net': 0.0,
            'wins_sum': 0.0, 'loss_sum': 0.0,
        })
        pnl = float(t['net_pnl'])
        b['count'] += 1
        b['net'] += pnl
        if pnl > 0:
            b['wins'] += 1
            b['wins_sum'] += pnl
        else:
            b['loss_sum'] += abs(pnl)

    out = {}
    for key, b in buckets.items():
        pf = (b['wins_sum'] / b['loss_sum']) if b['loss_sum'] > 0 else (99.0 if b['wins_sum'] > 0 else 0.0)
        out[key] = {
            'count': b['count'],
            'win_rate': round(b['wins'] / b['count'] * 100, 2) if b['count'] else 0.0,
            'profit_factor': round(pf, 2),
            'avg_pnl': round(b['net'] / b['count'], 2) if b['count'] else 0.0,
            'net_pnl': round(b['net'], 2),
        }
    sides = {}
    for key, b in buckets.items():
        side = key.split('_')[0]
        s = sides.setdefault(side, {'count': 0, 'wins': 0, 'net': 0.0})
        s['count'] += b['count']
        s['wins'] += b['wins']
        s['net'] += b['net']
    for side, s in sides.items():
        s['win_rate'] = round(s['wins'] / s['count'] * 100, 2) if s['count'] else 0.0
        s['avg_pnl'] = round(s['net'] / s['count'], 2) if s['count'] else 0.0

    return {
        'total_trades': len(results['trades']),
        'total_win_rate': round(results['win_rate'], 2),
        'total_profit_factor': round(results['profit_factor'], 2),
        'mark_price_basis': bool(results.get('mark_price_basis', False)),
        'mark_price_coverage': results.get('mark_price_coverage', 0.0),
        'trading_windows': results.get('trading_windows', {}),
        'blocked_entries': int(results.get('diagnostics', {}).get('blocked_entries', 0) or 0),
        'rejected_reasons': results.get('rejected_reasons', {}),
        'diagnostics': results.get('diagnostics', {}),
        'initial_capital_used': cap,
        'use_direction_conditions': req.params.entry_conditions.use_direction_conditions,
        'use_direction_macd_hist': req.params.entry_conditions.use_direction_macd_hist,
        'use_direction_atr_floor': req.params.entry_conditions.use_direction_atr_floor,
        'atr_regime_rules': {
            'long': config.atr_regime_rule_for(1),
            'short': config.atr_regime_rule_for(-1),
            'operators': {
                'long': config.atr_regime_op_for(1),
                'short': config.atr_regime_op_for(-1),
            },
        },
        'buckets': out,
        'by_side': {k: v for k, v in sides.items()},
        'setup_dist': results.get('setup_dist', {}),
    }


@app.post("/phantom/signals/custom")
def phantom_signals_custom(req: FilterPreviewRequest, user=Depends(get_current_user), db=Depends(get_db)):
    """Signals for chart-backtest parity: uses the same params the backtest form sends."""
    try:
        req = _apply_run_overrides(req)
        source = normalize_source(req.data_source)
        fees = resolve_fees(db, source, req.fee_mode, req.params)
        config = _fee_config(req.params, fees)
        engine = BacktestEngine(config=config, fee_schedule=fees, data_source=source)
        strategy_service = engine.strategy_service
        df_1h = engine._get_data_from_db(req.symbol, "1h", req.start_date, req.end_date, source)
        df_4h = engine._get_data_from_db(req.symbol, "4h", req.start_date, req.end_date, source)
        if df_1h.empty or df_4h.empty:
            return []
        if hasattr(strategy_service, 'generate_signals_with_metadata'):
            signals, meta = strategy_service.generate_signals_with_metadata(df_1h, df_4h)
        else:
            signals = strategy_service.generate_signals(df_1h, df_4h)
            meta = None
        out = []
        closes = df_1h['close'].values
        for i in range(1, len(df_1h)):
            s = signals[i]
            if s == 0:
                continue
            direction = int(s)
            item = {
                "time": int(_utc_ts(df_1h.index[i])),
                "direction": direction,
                "side": "LONG" if direction == 1 else "SHORT",
                "price": float(closes[i]),
                "setup": "CUSTOM",
                "rsi14": None, "adx": None, "macd_hist": None,
                "trend": None, "trend_label": None, "candle_type": None,
            }
            if meta is not None:
                item["setup"] = str(meta['setup'][i])
                try: item["rsi14"] = round(float(meta['rsi14'][i]), 2)
                except Exception: pass
                try: item["adx"] = round(float(meta['adx'][i]), 3)
                except Exception: pass
                try: item["macd_hist"] = round(float(meta['macd_hist'][i]), 4)
                except Exception: pass
                try:
                    trend = int(meta['trend'][i])
                    item["trend"] = trend
                    item["trend_label"] = "UP" if trend == 1 else "DOWN"
                except Exception: pass
                try:
                    if bool(meta['is_green'][i]): item["candle_type"] = "GREEN"
                    elif bool(meta['is_red'][i]): item["candle_type"] = "RED"
                    else: item["candle_type"] = "DOJI"
                except Exception: pass
            out.append(item)
            if len(out) >= 2000:
                break
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Custom signals error: {str(e)}")

# --- PAPER TRADING ---
class TradeStartRequest(BaseModel):
    strategy_id: str
    # Optional starting capital. If omitted, the user's (admin-set default)
    # initial capital is used.
    initial_capital: Optional[float] = None
    margin_pct: Optional[float] = None
    broker_name: str = 'Delta'
    data_source: Optional[str] = None
    connection_id: Optional[int] = None
    testnet: bool = False
    # BTC perpetual pricing: risk the trade on the exchange MARK price.
    # Omitted → the account default (saved from the UI) is used.
    use_mark_price: Optional[bool] = None
    # "Skip new trades" schedule. Omitted → the account default is used.
    # Open positions keep running; only new entries are refused.
    trading_windows: Optional[TradingWindowConfig] = None
    # Live price feed for exit checks. "auto" (default) takes the venue
    # websocket and fails over to REST polling by itself — no transport
    # decision for the operator. "off" keeps the original 60-second cadence;
    # "websocket" / "rest" force one transport (power users / old clients).
    # Only exits speed up — entries still wait for a closed 1h candle.
    price_feed: str = 'auto'
    tick_interval: float = 5.0
    # Deadman switch (Delta Exchange India). None = ON for Delta, OFF otherwise.
    heartbeat: Optional[bool] = None
    # ---- Risk controls -------------------------------------------------
    # Leverage the instance sizes with. Omitted -> the strategy config default
    # (7x). On live, it is also pushed to the venue before the first order so
    # the exchange and the local sizing agree.
    leverage: Optional[int] = None
    # isolated | cross | portfolio. Omitted -> whatever the account already is.
    # Applied to the venue on live starts only; paper has no margin engine.
    margin_mode: Optional[str] = None
    # Keep a paper session alive across a server restart (auto-resume).
    auto_resume: Optional[bool] = True

def resolve_broker_context(payload, user, db, require_credentials=False):
    code = normalize_source(payload.data_source or payload.broker_name)
    definition = db.query(BrokerDefinition).filter(BrokerDefinition.code == code, BrokerDefinition.enabled == 1).first()
    if not definition:
        raise HTTPException(status_code=400, detail=f"Broker/data source '{code}' is not configured or enabled")
    connection = None
    if payload.connection_id is not None:
        rows = _user_connections(db, user, code)
        connection = next((r for r in rows if r.id == payload.connection_id), None)
        if not connection:
            raise HTTPException(status_code=404, detail="Selected broker connection not found or disabled")
        if not _connection_is_active(connection):
            raise HTTPException(status_code=400,
                                detail=f"The connection '{connection.label or connection.broker_code}' "
                                       f"is switched off. Enable it in Broker Settings.")
    else:
        connection, _ = _pick_connection(db, user, code)
    api_key = connection.api_key if connection else (user.api_key if (user.broker_name or DEFAULT_BROKER) == code else '')
    api_secret = (_decrypt_secret(connection.api_secret) if connection
                  else (_decrypt_secret(user.api_secret) if (user.broker_name or DEFAULT_BROKER) == code else ''))
    passphrase = connection.passphrase if connection else ''
    testnet = bool(connection.is_testnet) if connection else bool(payload.testnet)
    if require_credentials and (not api_key or not api_secret):
        raise HTTPException(status_code=400,
                            detail=_credentials_problem(db, user, code, payload.connection_id))
    return code, definition, api_key or '', api_secret or '', passphrase or '', testnet, connection


# ---------------------------------------------------------------------------
# One strategy per (strategy + broker account) at a time
# ---------------------------------------------------------------------------
# A futures account holds ONE netted position per contract. Two runs of the
# same strategy on the same API key therefore cannot both carry their own
# trade: they stack, hedge to flat, or fight over the same stop legs. This is
# the guard that stops it happening, and the pre-flight endpoint below lets the
# UI tell the user *before* they press Start rather than after.

def _account_identity(source, connection, user):
    """Stable identity of the venue account an instance would trade on."""
    if connection is not None:
        return f"{source}:conn:{connection.id}"
    return f"{source}:legacy:{user.id}"


def _instance_account_identity(service, fallback_user_id=None):
    conn_id = getattr(service, "connection_id", None)
    source = normalize_source(getattr(service, "broker_name", None)
                              or getattr(service, "market_source", None))
    if conn_id is not None:
        return f"{source}:conn:{conn_id}"
    return f"{source}:legacy:{getattr(service, 'user_id', fallback_user_id)}"


def running_conflict(mode, user, strategy_id, source, connection):
    """The already-running instance that blocks this start, or ``None``.

    Matching is on (strategy, venue account) — the same strategy on a *different*
    connection is fine, and a different strategy on the same connection is
    allowed but queues (reported separately by ``_shared_account_status``).
    """
    pool = live_trade_instances if mode == 'live' else paper_trade_instances
    wanted = _account_identity(source, connection, user)
    for key, service in pool.items():
        if f"_{user.username}_" not in key:
            continue
        if str(getattr(service, "strategy_id", "")) != str(strategy_id):
            continue
        if _instance_account_identity(service, user.id) != wanted:
            continue
        # `is_running` is flipped by the background task, which runs AFTER the
        # start response is sent — a double-clicked Start button would pass
        # this check twice and put two workers on one netted position. An
        # instance that is registered but not yet started (`pending_start`)
        # blocks a duplicate exactly like a running one.
        if not (getattr(service, "is_running", False)
                or getattr(service, "pending_start", False)):
            continue
        return key, service
    return None


def _conflict_detail(mode, key, service, connection):
    account = (getattr(connection, "label", None) or "the primary account")
    return (
        f"'{getattr(service, 'strategy_name', None) or service.strategy_id}' is already "
        f"running in {mode} on {account}. A futures account holds one netted position "
        f"per contract, so the same strategy cannot run twice on the same account — "
        f"stop the existing instance ({key.split('_')[-1]}) first, or pick a different "
        f"broker connection."
    )


class PreflightRequest(BaseModel):
    mode: str = 'paper'              # paper | live
    strategy_id: str
    broker_name: str = DEFAULT_BROKER
    data_source: Optional[str] = None
    connection_id: Optional[int] = None
    # resolve_broker_context() reads this off the payload when no saved
    # connection is selected; it must exist here too.
    testnet: bool = False


@app.post("/trade/preflight")
def trade_preflight(payload: PreflightRequest, user=Depends(get_current_user), db=Depends(get_db)):
    """Can this strategy start right now — and what will it share?

    Called by the Start button before it posts, so the user is told about a
    duplicate (blocking) or a shared account (queueing) up front instead of
    getting a 409 after the fact.
    """
    mode = 'live' if str(payload.mode).lower() == 'live' else 'paper'
    try:
        source, definition, api_key, api_secret, _p, testnet, connection = \
            resolve_broker_context(payload, user, db, require_credentials=(mode == 'live'))
    except HTTPException as exc:
        return {"can_start": False, "blocking": True, "reason": exc.detail,
                "kind": "broker", "mode": mode}

    conflict = running_conflict(mode, user, payload.strategy_id, source, connection)
    if conflict:
        key, service = conflict
        return {"can_start": False, "blocking": True, "kind": "duplicate", "mode": mode,
                "instance_key": key, "reason": _conflict_detail(mode, key, service, connection)}

    # Not blocking, but worth saying: another strategy already holds this
    # account, so this one will start and then wait its turn.
    pool = live_trade_instances if mode == 'live' else paper_trade_instances
    wanted = _account_identity(source, connection, user)
    sharing = [str(getattr(s, "strategy_name", None) or s.strategy_id)
               for k, s in pool.items()
               if f"_{user.username}_" in k and getattr(s, "is_running", False)
               and _instance_account_identity(s, user.id) == wanted]
    return {
        "can_start": True, "blocking": False, "mode": mode,
        "kind": "shared" if sharing else "ok",
        "account_label": (getattr(connection, "label", None) or "Primary"),
        "connection_id": (connection.id if connection else None),
        "broker": source, "testnet": bool(testnet),
        "sharing_with": sharing,
        "reason": (f"{len(sharing)} other strategy/strategies already run on this account "
                   f"({', '.join(sharing)}). Only one of them can hold a position at a "
                   f"time — the rest wait their turn.") if sharing else None,
    }


@app.post("/paper-trade/start")
def start_paper_trade(
    payload: TradeStartRequest,
    user=Depends(get_current_user),
    db=Depends(get_db),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    if not (user.can_paper if user.can_paper is not None else 1):
        raise HTTPException(status_code=403, detail="Paper trading is disabled for this account. Contact admin.")
    source, definition, api_key, api_secret, passphrase, testnet, connection = resolve_broker_context(payload, user, db)
    # One strategy per venue account: refuse a duplicate up front rather than
    # letting two workers fight over the same netted position.
    conflict = running_conflict('paper', user, payload.strategy_id, source, connection)
    if conflict:
        raise HTTPException(status_code=409,
                            detail=_conflict_detail('paper', conflict[0], conflict[1], connection))
    fees = resolve_fees(db, source, 'paper')
    config = _fee_config(_load_champion_config() if payload.strategy_id == 'PhantomV2' else PhantomV2Config(), fees)
    capital, margin_pct = _resolve_sizing(payload, user)
    # Paper is the rehearsal for live: a leverage or margin mode the venue
    # would refuse must be refused here too, not silently simulated so the
    # mistake only surfaces on the first real order.
    try:
        if payload.leverage is not None:
            payload.leverage = int(validate_leverage(payload.leverage))
        if payload.margin_mode is not None:
            payload.margin_mode = validate_margin_mode(payload.margin_mode)
    except PhantomValidationError as ve:
        raise HTTPException(status_code=ve.status_code, detail=ve.message)
    strategy_id = str(payload.strategy_id)
    strategy_name = 'Kudos V2.5 (Default)' if strategy_id == 'PhantomV2' else 'Fast Test Strategy' if strategy_id == 'FastTest' else None
    # BTC perpetual pricing + "skip new trades" schedule for this instance.
    window_config = resolve_window_config(payload, user)
    use_mark = resolve_use_mark_price(payload, user)
    feed_mode, feed_interval = _resolve_price_feed(payload)
    # Which sub-account this paper run is modelled on, so its card matches the
    # live card for the same strategy/account pair.
    account_label = (getattr(connection, "label", None) or
                     getattr(connection, "broker_code", None) or "Primary") if connection else "Primary"
    instance_id = str(uuid.uuid4())[:8]
    instance_key = f"paper_{user.username}_{source}_{strategy_id}_{instance_id}"

    if strategy_id == "FastTest":
        from .core.strategy import FastTestStrategyService
        service = PaperTradeService(strategy_id, config, initial_capital=capital, margin_pct=margin_pct,
                                    market_source=source, broker_name=source, fee_schedule=fees,
                                    broker_definition=definition, strategy_name=strategy_name,
                                    trading_windows=window_config, use_mark_price=use_mark,
                                    price_feed=feed_mode, tick_interval=feed_interval, testnet=testnet,
                                    connection_id=(connection.id if connection else None),
                                    account_label=account_label, leverage=payload.leverage)
        service.strategy = FastTestStrategyService(service.config)
    elif strategy_id != "PhantomV2":
        resolved = _resolve_strategy_payload(db, strategy_id, user.id, fees)
        if not resolved:
            raise HTTPException(status_code=404, detail="Custom strategy not found")
        kind, strategy_payload, strat = resolved
        strategy_name = strat.name
        if kind == 'phantom':
            service = PaperTradeService(strategy_id, strategy_payload, initial_capital=capital, margin_pct=margin_pct,
                                        market_source=source, broker_name=source, fee_schedule=fees,
                                        is_custom=False, broker_definition=definition, strategy_name=strategy_name,
                                        trading_windows=window_config, use_mark_price=use_mark,
                                    price_feed=feed_mode, tick_interval=feed_interval, testnet=testnet,
                                    connection_id=(connection.id if connection else None),
                                    account_label=account_label, leverage=payload.leverage)
        else:
            service = PaperTradeService(strategy_id, strategy_payload, initial_capital=capital, margin_pct=margin_pct,
                                        market_source=source, broker_name=source, fee_schedule=fees,
                                        is_custom=True, broker_definition=definition, strategy_name=strategy_name,
                                       trading_windows=window_config, use_mark_price=use_mark,
                                    price_feed=feed_mode, tick_interval=feed_interval, testnet=testnet,
                                    connection_id=(connection.id if connection else None),
                                    account_label=account_label, leverage=payload.leverage)
    else:
        service = PaperTradeService(strategy_id, config, initial_capital=capital, margin_pct=margin_pct,
                                    market_source=source, broker_name=source, fee_schedule=fees,
                                    broker_definition=definition, strategy_name=strategy_name,
                                    trading_windows=window_config, use_mark_price=use_mark,
                                    price_feed=feed_mode, tick_interval=feed_interval, testnet=testnet,
                                    connection_id=(connection.id if connection else None),
                                    account_label=account_label, leverage=payload.leverage)
    # Every instance is mirrored into paper_sessions so stopping it (or a
    # server restart) no longer throws the result away.
    service.instance_key = instance_key
    service.user_id = user.id
    service.auto_resume = bool(payload.auto_resume if payload.auto_resume is not None else True)
    session_id = paper_history.start_session(user.id, instance_key, service)
    # Blocks a double-clicked Start until the background task flips is_running.
    service.pending_start = True
    paper_trade_instances[instance_key] = service
    background_tasks.add_task(service.start)
    return {"status": "Paper trade started", "instance_key": instance_key, "strategy_id": strategy_id,
            "strategy_name": service.strategy_name, "data_source": source,
            "session_id": session_id,
            "account_label": account_label,
            "connection_id": (connection.id if connection else None),
            "leverage": getattr(service.config, 'leverage', None),
            "margin_pct": margin_pct,
            "contract": contract_label(source, "BTCUSDT"),
            "perpetual_symbol": perpetual_symbol(source, "BTCUSDT"),
            "use_mark_price": bool(getattr(service, 'use_mark_price', True)),
            "trading_windows": service.window_guard.summary(),
            "price_feed": feed_mode, "tick_interval": feed_interval,
            "taker_fee_bps": fees.taker_fee_bps, "maker_fee_bps": fees.maker_fee_bps}

@app.post("/paper-trade/stop")
async def stop_paper_trade(instance_key: str, user=Depends(get_current_user)):
    if f"_{user.username}_" not in instance_key:
        raise HTTPException(status_code=403, detail="Not your instance")
    if instance_key in paper_trade_instances:
        service = paper_trade_instances[instance_key]
        await service.stop()
        # Keep the saved row: the client reviews the result in History.
        session_id = paper_history.finalize_session(instance_key, service)
        del paper_trade_instances[instance_key]
        return {"status": "Paper trade stopped", "saved_to_history": session_id is not None,
                "session_id": session_id}
    raise HTTPException(status_code=404, detail="Instance not found")

@app.delete("/paper-trade/{instance_key}")
async def delete_paper_trade(instance_key: str, user=Depends(get_current_user)):
    """Stop a session and permanently remove it, including its saved history.

    Stopping keeps the result in History; this is the destructive action, so
    the UI asks for confirmation before calling it.
    """
    if f"_{user.username}_" not in instance_key:
        raise HTTPException(status_code=403, detail="Not your instance")
    service = paper_trade_instances.get(instance_key)
    if not service:
        raise HTTPException(status_code=404, detail="Instance not found")
    await service.stop()
    del paper_trade_instances[instance_key]
    purged = paper_history.delete_record(instance_key)
    return {"status": "Paper trade deleted", "instance_key": instance_key,
            "history_removed": purged}


# --- PAPER TRADE HISTORY (persisted, survives stop / restart) ------------
@app.get("/paper-trade/history")
def get_paper_history(user=Depends(get_current_user), db=Depends(get_db)):
    """Every paper session this user has run, newest first.

    Rows are written while the instance runs and finalised when it stops, so
    the client can review trades, equity curve and logs of a stopped session.
    """
    try:
        return paper_history.list_sessions(user.id, db)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load paper history: {str(exc)}")


@app.get("/paper-trade/history/{session_id}")
def get_paper_history_detail(session_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Full saved detail for one session: trades, equity curve, logs, params."""
    detail = paper_history.get_session(session_id, user.id, db)
    if not detail:
        raise HTTPException(status_code=404, detail="Paper session not found")
    return detail


@app.delete("/paper-trade/history/{session_id}")
def delete_paper_history(session_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Delete one saved paper session from History."""
    found, instance_key = paper_history.delete_session(session_id, user.id, db)
    if not found:
        raise HTTPException(status_code=404, detail="Paper session not found")
    # If the worker is somehow still alive, drop it from the workspace too.
    if instance_key and instance_key in paper_trade_instances:
        paper_trade_instances.pop(instance_key, None)
    return {"status": "Paper session deleted", "id": session_id}


# --- UNIFIED SESSION HISTORY (paper AND live) ----------------------------
# One store, one shape, one reviewer. A stopped live instance is now just as
# reviewable as a paper one: same trades, equity curve, logs and parameters.

@app.get("/sessions")
def list_all_sessions(mode: Optional[str] = None, strategy_id: Optional[str] = None,
                      user=Depends(get_current_user), db=Depends(get_db)):
    """Every paper AND live session this user has run, newest first.

    ``mode`` filters to 'paper' or 'live'; ``strategy_id`` narrows to one
    strategy so a client can look at a single strategy's whole track record.
    """
    try:
        rows = paper_history.list_sessions(user.id, db, mode=mode)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load sessions: {exc}")
    if strategy_id is not None:
        rows = [r for r in rows if str(r.get('strategy_id')) == str(strategy_id)]
    # Mark which sessions still have a worker alive in this process, so the UI
    # can offer Stop on those and review-only on the rest.
    live_keys = set(live_trade_instances) | set(paper_trade_instances)
    for row in rows:
        row['is_running'] = row.get('instance_key') in live_keys
    return rows


@app.get("/sessions/{session_id}")
def session_detail(session_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Full saved detail for ONE session: trades, curve, logs, parameters.

    When the worker is still alive its in-memory state is layered on top, so a
    running session shows live numbers and a stopped one shows exactly what it
    finished with.
    """
    detail = paper_history.get_session(session_id, user.id, db)
    if not detail:
        raise HTTPException(status_code=404, detail="Session not found")
    key = detail.get('instance_key')
    service = live_trade_instances.get(key) or paper_trade_instances.get(key)
    detail['is_running'] = service is not None
    if service is not None:
        detail['closed_trades'] = list(getattr(service, 'closed_trades', []) or [])
        detail['equity_curve'] = list(getattr(service, 'equity_history', []) or [])
        detail['logs'] = list(getattr(service, 'logs', []) or [])
        detail['last_price'] = getattr(service, 'last_price', detail.get('last_price'))
        detail['open_positions'] = [
            {
                'symbol': sym, 'direction': int(t.direction),
                'entry': float(t.entry_price),
                'current': float(getattr(service, 'last_price', None) or t.entry_price),
                'lots': float(getattr(t, 'lots', 0) or 0),
                'margin_inr': float(getattr(t, 'margin_inr', 0) or 0),
                'sl': (float(t.sl) if getattr(t, 'sl', None) else None),
                'tp': (float(t.tp) if getattr(t, 'tp', None) else None),
                'trail_stop': (float(t.trail_stop) if getattr(t, 'trail_stop', None) else None),
                'bars_held': int(getattr(t, 'bars_held', 0) or 0),
                'entry_time': _to_ist(t.entry_time),
                'unrealised': True,
            }
            for sym, t in (getattr(service, 'oms', None).active_trades.items()
                           if getattr(service, 'oms', None) else [])
        ]
    return detail


@app.delete("/sessions/{session_id}")
def delete_any_session(session_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Delete one saved session (paper or live) from History."""
    found, instance_key = paper_history.delete_session(session_id, user.id, db)
    if not found:
        raise HTTPException(status_code=404, detail="Session not found")
    paper_trade_instances.pop(instance_key, None)
    live_trade_instances.pop(instance_key, None)
    return {"status": "Session deleted", "id": session_id}


@app.get("/paper-trade/status")
def get_paper_status(user=Depends(get_current_user)):
    status_list = []
    for key, service in paper_trade_instances.items():
        if f"_{user.username}_" in key:
            active_trades = []
            for symbol, trade in service.oms.active_trades.items():
                # Prefer the freshest tick price so the "current" value is live.
                current = getattr(service, 'last_price', None) or getattr(trade, 'current_price', None) or trade.peak_price
                pnl_inr = (current - trade.entry_price) * trade.direction * trade.lots * service.conversion_rate
                leverage = getattr(service.config, 'leverage', 1)
                # Percent change of the live price vs entry (direction-aware).
                chg_pct = ((current - trade.entry_price) / trade.entry_price * 100) * trade.direction if trade.entry_price else 0.0
                entry_time_ist = _to_ist(trade.entry_time)
                # Which stop is in force right now: trailing stop once the
                # peak crossed the activation level, otherwise the hard SL
                # (which may already have been moved to breakeven).
                # Note: values can be numpy scalars (ATR comes from pandas),
                # so coerce to plain Python types before serializing.
                trail_active = bool((trade.peak_price >= trade.trail_activation) if trade.direction == 1
                                    else (trade.peak_price <= trade.trail_activation))
                stop_level = getattr(trade, 'trail_stop', None) if trail_active else getattr(trade, 'sl', None)
                breakeven_active = bool((getattr(trade, 'sl', 0) >= trade.entry_price) if trade.direction == 1
                                        else (getattr(trade, 'sl', float('inf')) <= trade.entry_price))
                _f = lambda v: None if v is None else float(v)
                active_trades.append({
                    "symbol": symbol, "direction": trade.direction, "entry": float(trade.entry_price),
                    "current": float(current), "pnl": float(pnl_inr), "chg_pct": float(chg_pct),
                    "entry_time": entry_time_ist, "bars_held": int(trade.bars_held),
                    "margin": float(trade.margin_inr), "notional_usd": float(getattr(trade, 'notional_usd', 0)),
                    "lots": float(getattr(trade, 'lots', 0)), "leverage": leverage,
                    "sl": _f(getattr(trade, 'sl', None)), "sl_entry": _f(getattr(trade, 'sl_entry', None)),
                    "tp": _f(getattr(trade, 'tp', None)),
                    "trail_stop": _f(getattr(trade, 'trail_stop', None)),
                    "trail_activation": _f(getattr(trade, 'trail_activation', None)),
                    "trail_active": trail_active, "stop_level": _f(stop_level),
                    "breakeven_active": breakeven_active,
                    "atr_at_entry": _f(getattr(trade, 'atr_at_entry', None)),
                    "peak_price": _f(getattr(trade, 'peak_price', None)),
                    # BTC perpetual: the mark price the position is priced on
                    # and the traded price it was filled at.
                    "mark": _f(getattr(trade, 'current_mark_price', None)),
                    "entry_mark": _f(getattr(trade, 'entry_mark_price', None)),
                    "entry_trade": _f(getattr(trade, 'entry_trade_price', None)),
                    "mark_price_basis": bool(getattr(trade, 'mark_price_basis', False)),
                })
            windows = getattr(service, 'window_guard', None)
            status_list.append({
                "instance_key": key, "strategy_id": service.strategy_id,
                "strategy_name": getattr(service, 'strategy_name', service.strategy_id),
                "created_at": getattr(service, 'created_at', None),
                # Saved paper_sessions row for this worker (History deep link).
                "session_id": getattr(service, 'session_id', None),
                "equity_curve": list(getattr(service, 'equity_history', []) or [])[-200:],
                "data_source": service.market_source, "broker_name": service.broker_name,
                "taker_fee_bps": service.config.taker_fee_bps, "maker_fee_bps": service.config.maker_fee_bps,
                "equity_inr": service.equity_inr, "initial_capital_inr": service.initial_capital_inr,
                "leverage": getattr(service.config, 'leverage', 1),
                "margin_pct": getattr(service, 'margin_pct', 0),
                "conversion_rate": service.conversion_rate,
                "is_running": service.is_running, "active_trades": active_trades,
                # Which venue account this run is modelled on, plus why it is
                # not running if it stopped and what the last error was. These
                # are what turn a bare "Interrupted" into something actionable.
                "account_label": getattr(service, 'account_label', 'Primary'),
                "connection_id": getattr(service, 'connection_id', None),
                "stop_reason": getattr(service, 'stop_reason', None),
                "last_error": getattr(service, 'last_error', None),
                "restarts": int(getattr(service, 'restarts', 0) or 0),
                "resumed": getattr(service, 'resumed_from_session', None) is not None,
                "auto_resume": bool(getattr(service, 'auto_resume', True)),
                "testnet": bool(getattr(service, 'testnet', False)),
                "open_trade_count": len(service.oms.active_trades),
                "closed_trades": service.closed_trades[-50:],
                "last_price": service.last_price, "last_checked": service.last_checked,
                # BTC perpetual: the price the maths runs on (mark) vs the
                # traded price shown beside it.
                "last_trade_price": getattr(service, 'last_trade_price', None),
                "last_mark_price": getattr(service, 'last_mark_price', None),
                "mark_price_basis": bool(getattr(service, 'mark_price_basis', False)),
                "use_mark_price": bool(getattr(service, 'use_mark_price', True)),
                "contract": contract_label(service.market_source, "BTCUSDT"),
                "perpetual_symbol": perpetual_symbol(service.market_source, "BTCUSDT"),
                # "Skip new trades" schedule and whether it is blocking now.
                "trading_windows": windows.summary() if windows else None,
                "entry_paused": bool(windows.is_blocked(datetime.utcnow())) if windows else False,
                "blocked_entries": int(getattr(service, 'blocked_entries', 0) or 0),
                # Entry gating: signals the worker refused because a position is
                # already open, the candle was already traded, or a cooldown is
                # running. Surfaced so "why is it not trading?" is answerable.
                "skipped_entries": int(getattr(service, 'skipped_entries', 0) or 0),
                "last_skip_reason": getattr(service, 'last_skip_reason', None),
                # Data honesty: true while the candle set is too old to open
                # trades on (venue feed down / stored fallback being served).
                "candles_stale": bool(getattr(service, 'candles_stale', False)),
                "price_feed": {
                    "mode": getattr(service, "price_feed_mode", "off"),
                    "tick_interval": getattr(service, "tick_interval", 60.0),
                    "fast_ticks": int(getattr(service, "fast_ticks", 0) or 0),
                    **(getattr(service, "tick_feed", None).stats()
                       if getattr(service, "tick_feed", None) else {}),
                },
            })
    return status_list


@app.get("/paper-trade/logs")
def get_paper_logs(instance_key: str, user=Depends(get_current_user)):
    """Return the live log buffer for a paper-trade instance owned by this user."""
    if f"_{user.username}_" not in instance_key:
        raise HTTPException(status_code=403, detail="Not your instance")
    service = paper_trade_instances.get(instance_key)
    if not service:
        raise HTTPException(status_code=404, detail="Instance not found")
    return {"instance_key": instance_key, "logs": service.logs[-150:]}

def _benign_risk_rejection(error) -> bool:
    """Venue answers that mean "already set that way", not a refusal.

    Binance answers ``-4046: No need to change margin type`` and Delta
    answers ``HTTP 400 {"code": "same_margin_mode"}`` when the account
    already is in the requested mode — confirmation, not a failure; refusing
    the start over it would block every idempotent restart.
    """
    text = str(error or "").lower()
    return "no need to change" in text or "same_margin_mode" in text


# --- LIVE TRADING ---
@app.post("/live-trade/start")
def start_live_trade(
    payload: TradeStartRequest,
    user=Depends(get_current_user),
    db=Depends(get_db),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    if not (user.can_live if user.can_live is not None else 0):
        raise HTTPException(status_code=403, detail="Live trading is not enabled for this account. Contact admin.")
    source, definition, api_key, api_secret, passphrase, testnet, connection = resolve_broker_context(payload, user, db, True)
    conflict = running_conflict('live', user, payload.strategy_id, source, connection)
    if conflict:
        raise HTTPException(status_code=409,
                            detail=_conflict_detail('live', conflict[0], conflict[1], connection))
    fees = resolve_fees(db, source, 'live')
    config = _fee_config(_load_champion_config() if payload.strategy_id == 'PhantomV2' else PhantomV2Config(), fees)
    strategy_id = payload.strategy_id
    capital, margin_pct = _resolve_sizing(payload, user)
    # BTC perpetual pricing + "skip new trades" schedule for this instance.
    window_config = resolve_window_config(payload, user)
    use_mark = resolve_use_mark_price(payload, user)
    feed_mode, feed_interval = _resolve_price_feed(payload)
    # Deadman switch: required on Delta from day one (client document).
    # Explicit False disables it; omitted defaults to ON for Delta.
    heartbeat_on = payload.heartbeat
    if heartbeat_on is None:
        heartbeat_on = str(source).lower() == "delta"
    # Name the account this instance will trade on. With several strategies
    # pinned to different sub-accounts this is what tells the operator which
    # instance is which; without it every card just says "Binance".
    account_label = (getattr(connection, "label", None) or
                     getattr(connection, "broker_code", None) or "Primary") if connection else "Primary"
    # ---- Risk controls, applied to the VENUE before the first order -----
    # Sizing uses config.leverage locally; if the exchange still holds a
    # different leverage (or a different margin mode) the position that comes
    # back is not the one the strategy sized. Push both first and report what
    # the venue said, so a rejection is visible at start rather than at the
    # first fill.
    risk_setup = {"leverage": None, "margin_mode": None}
    if payload.leverage is not None:
        try:
            config.leverage = int(validate_leverage(payload.leverage))
        except PhantomValidationError as ve:
            raise HTTPException(status_code=ve.status_code, detail=ve.message)
    if payload.leverage is not None or payload.margin_mode is not None:
        try:
            client, _def, _cid = _live_client(db, user, source, payload.connection_id)
            if payload.leverage is not None:
                res = client.set_leverage("BTCUSDT", int(config.leverage))
                risk_setup["leverage"] = {
                    "requested": int(config.leverage),
                    "status": "rejected" if (isinstance(res, dict) and res.get("error")) else "ok",
                    "error": (res or {}).get("error") if isinstance(res, dict) else None}
            if payload.margin_mode is not None:
                mode = validate_margin_mode(payload.margin_mode)
                res = client.set_margin_mode("BTCUSDT", mode)
                risk_setup["margin_mode"] = {
                    "requested": mode,
                    "status": "rejected" if (isinstance(res, dict) and res.get("error")) else "ok",
                    "error": (res or {}).get("error") if isinstance(res, dict) else None}
        except HTTPException:
            raise
        except PhantomValidationError as ve:
            raise HTTPException(status_code=ve.status_code, detail=ve.message)
        except Exception as exc:
            risk_setup["error"] = f"{exc.__class__.__name__}: {exc}"
    # An explicitly requested leverage / margin mode the venue refused must
    # not ride along silently: the position that comes back would not be the
    # one the strategy sized (wrong leverage) or bracketed on the margin
    # family the client asked for. Historically the start went ahead anyway
    # and the instance card read "running" next to a small red note — call
    # the start REFUSED instead, name the venue's answer, and never register
    # the instance.
    refusals = []
    for setting in ("leverage", "margin_mode"):
        pushed = risk_setup.get(setting)
        if isinstance(pushed, dict) and pushed.get("status") == "rejected" \
                and not _benign_risk_rejection(pushed.get("error")):
            refusals.append(f"{setting.replace('_', ' ')}: {pushed.get('error')}")
    if risk_setup.get("error"):
        refusals.append(str(risk_setup["error"]))
    if refusals:
        raise HTTPException(
            status_code=502,
            detail="The venue refused the requested risk setup, so the live "
                   "trade was NOT started — " + " · ".join(refusals))

    instance_id = str(uuid.uuid4())[:8]
    instance_key = f"live_{user.username}_{source}_{strategy_id}_{instance_id}"

    if strategy_id == "FastTest":
        from .core.strategy import FastTestStrategyService
        service = LiveTradeService(strategy_id, config, api_key, api_secret, initial_capital=capital,
                                   margin_pct=margin_pct, broker_name=source, passphrase=passphrase,
                                   testnet=testnet, fee_schedule=fees, definition=definition,
                                   trading_windows=window_config, use_mark_price=use_mark,
                                   user_id=user.id, instance_key=instance_key,
                                   price_feed=feed_mode, tick_interval=feed_interval,
                                   account_label=account_label, heartbeat=heartbeat_on,
                                   connection_id=(connection.id if connection else None))
        service.strategy = FastTestStrategyService(service.config)
    elif strategy_id != "PhantomV2":
        resolved = _resolve_strategy_payload(db, strategy_id, user.id, fees)
        if not resolved:
            raise HTTPException(status_code=404, detail="Custom strategy not found")
        kind, payload, strat = resolved
        if kind == 'phantom':
            service = LiveTradeService(strategy_id, payload, api_key, api_secret, initial_capital=capital,
                                       margin_pct=margin_pct, is_custom=False, broker_name=source,
                                       passphrase=passphrase, testnet=testnet, fee_schedule=fees, definition=definition,
                                       trading_windows=window_config, use_mark_price=use_mark,
                                   user_id=user.id, instance_key=instance_key,
                                   price_feed=feed_mode, tick_interval=feed_interval,
                                   account_label=account_label, heartbeat=heartbeat_on,
                                   connection_id=(connection.id if connection else None))
        else:
            service = LiveTradeService(strategy_id, payload, api_key, api_secret, initial_capital=capital,
                                       margin_pct=margin_pct, is_custom=True, broker_name=source,
                                       passphrase=passphrase, testnet=testnet, fee_schedule=fees, definition=definition,
                                       trading_windows=window_config, use_mark_price=use_mark,
                                   user_id=user.id, instance_key=instance_key,
                                   price_feed=feed_mode, tick_interval=feed_interval,
                                   account_label=account_label, heartbeat=heartbeat_on,
                                   connection_id=(connection.id if connection else None))
    else:
        service = LiveTradeService(strategy_id, config, api_key, api_secret, initial_capital=capital,
                                   margin_pct=margin_pct, broker_name=source, passphrase=passphrase,
                                   testnet=testnet, fee_schedule=fees, definition=definition,
                                   trading_windows=window_config, use_mark_price=use_mark,
                                   user_id=user.id, instance_key=instance_key,
                                   price_feed=feed_mode, tick_interval=feed_interval,
                                   account_label=account_label, heartbeat=heartbeat_on,
                                   connection_id=(connection.id if connection else None))
    # Mirror the live session into the sessions table from the start, exactly
    # like a paper run, so stopping it later leaves a full reviewable record.
    service.user_id = user.id
    service.account_label_for_history = account_label
    session_id = paper_history.start_session(user.id, instance_key, service)
    # Blocks a double-clicked Start until the background task flips is_running.
    service.pending_start = True
    live_trade_instances[instance_key] = service
    background_tasks.add_task(service.start)
    return {"status": "Live trade started", "session_id": session_id, "instance_key": instance_key, "broker_name": source,
            "contract": contract_label(source, "BTCUSDT"),
            "perpetual_symbol": perpetual_symbol(source, "BTCUSDT"),
            # Which saved connection the instance will sign with, so a key
            # replaced later can be handed back to *this* instance, and so the
            # operator can see at start whether the venue accepts it at all.
            "connection_id": (connection.id if connection else None),
            "account_label": account_label,
            "use_mark_price": bool(getattr(service, 'use_mark_price', True)),
            "trading_windows": service.window_guard.summary(),
            "heartbeat": bool(getattr(service, "heartbeat_enabled", False)),
            "testnet": bool(testnet),
            "leverage": getattr(service.config, 'leverage', None),
            "margin_pct": margin_pct,
            # What the exchange said when leverage / margin mode were pushed.
            "risk_setup": risk_setup,
            "taker_fee_bps": fees.taker_fee_bps, "maker_fee_bps": fees.maker_fee_bps}

@app.post("/live-trade/stop")
async def stop_live_trade(instance_key: str, user=Depends(get_current_user)):
    """Stop a live instance and keep its complete result in History.

    Stopping used to drop the worker and everything it knew. The session is now
    finalised first, so the client can open the stopped run and review every
    trade, the equity curve, the log and the positions that were still open.
    """
    if f"_{user.username}_" not in instance_key:
        raise HTTPException(status_code=403, detail="Not your instance")
    if instance_key in live_trade_instances:
        service = live_trade_instances[instance_key]
        await service.stop()
        session_id = paper_history.finalize_session(instance_key, service)
        del live_trade_instances[instance_key]
        return {"status": "Live trade stopped", "saved_to_history": session_id is not None,
                "session_id": session_id}
    raise HTTPException(status_code=404, detail="Instance not found")

def _shared_account_status(service):
    """How many live runs share this instance's broker account, and whose turn it is.

    A futures account holds ONE netted position per contract, so several
    strategies on one API key cannot each carry their own trade — they queue.
    Reporting that here keeps "my strategy is running but not trading" from
    looking like a bug.
    """
    try:
        siblings = COORDINATOR.siblings(service)
    except Exception:
        return None
    if not siblings:
        return None
    holder = COORDINATOR.holder(service)
    # `holder()` only looks at siblings, so the instance carrying the position
    # would otherwise report "nobody holds it" — name it explicitly instead.
    if holder is None and getattr(service, "oms", None) and service.oms.active_trades:
        held_by = service.strategy_id
    else:
        held_by = holder.strategy_id if holder is not None else None
    return {
        "strategies_on_account": len(siblings) + 1,
        "queue_position": COORDINATOR.queue_position(service),
        "position_held_by": held_by,
        "holds_account_position": held_by == service.strategy_id,
        "other_strategies": sorted(getattr(s, "strategy_id", "?") for s in siblings),
        "note": ("one netted position per account — only one strategy can hold a "
                 "trade at a time; the rest wait their turn"),
    }


@app.get("/trade-executions")
def trade_executions(user=Depends(get_current_user), db=Depends(get_db)):
    """Executed trades for the market-chart overlay.

    Which candle each entry/exit actually landed on, with the full stop plan
    (SL at entry, SL in force at exit, TP, trailing stop) and PnL, from:

    * running live instances  — the open position (live SL/TP levels) plus the
      trades closed since the instance started
    * running paper instances — same, from the worker's own book
    * saved paper sessions    — trades that survived the instance (History),
      including the positions still open when it stopped

    Backtest runs keep their own deep link (`/chart?run=<id>`) because their
    trade rows carry the full entry-condition breakdown already.
    Times are IST-offset ISO strings; the chart converts to candle UNIX time.
    """
    from .services.paper_history import MAX_SAVED_LOG_LINES  # noqa: F401  (import guard)

    def _open_trade_dict(symbol, trade, status="open"):
        current = getattr(trade, "current_price", None) or trade.peak_price or trade.entry_price
        return {
            "symbol": symbol, "direction": int(trade.direction),
            "status": status,
            "entry": float(trade.entry_price),
            "exit": None,
            "current": float(current) if current is not None else None,
            "sl": float(trade.sl) if getattr(trade, "sl", None) else None,
            "sl_plan": float(trade.sl_entry) if getattr(trade, "sl_entry", None) else None,
            "tp": float(trade.tp) if getattr(trade, "tp", None) else None,
            "trail_stop": float(trade.trail_stop) if getattr(trade, "trail_stop", None) else None,
            "trail_activation": float(trade.trail_activation) if getattr(trade, "trail_activation", None) else None,
            "atr_at_entry": float(trade.atr_at_entry) if getattr(trade, "atr_at_entry", None) else None,
            "peak_price": float(trade.peak_price) if getattr(trade, "peak_price", None) else None,
            "lots": float(getattr(trade, "lots", 0.0) or 0.0),
            "margin_inr": float(trade.margin_inr) if getattr(trade, "margin_inr", None) else None,
            "notional_usd": float(trade.notional_usd) if getattr(trade, "notional_usd", None) else None,
            "entry_time": _to_ist(trade.entry_time),
            "exit_time": None,
            "exit_price": None, "pnl": None, "fees": None, "reason": None,
            "bars_held": int(trade.bars_held or 0),
            "entry_trade_price": getattr(trade, "entry_trade_price", None),
            "entry_mark_price": getattr(trade, "entry_mark_price", None),
            "mark_price_basis": bool(getattr(trade, "mark_price_basis", False)),
        }

    def _closed_trade_dict(t, symbol=None):
        return {
            "symbol": t.get("symbol") or symbol or "BTCUSDT",
            "direction": int(t.get("direction") or 1),
            "status": "closed",
            "entry": t.get("entry"),
            "exit": t.get("exit"),
            "current": None,
            "sl": t.get("sl_final") if t.get("sl_final") is not None else t.get("sl"),
            "sl_plan": t.get("sl"),
            "tp": t.get("tp"),
            "trail_stop": t.get("trail_stop"),
            "trail_activation": t.get("trail_activation"),
            "atr_at_entry": t.get("atr_at_entry"),
            "peak_price": t.get("peak_price"),
            "lots": t.get("lots"),
            "margin_inr": t.get("margin_inr"),
            "notional_usd": t.get("notional_usd"),
            "entry_time": t.get("entry_time"),
            "exit_time": t.get("exit_time"),
            "exit_price": t.get("exit"),
            "pnl": t.get("pnl"),
            "fees": t.get("fees"),
            "reason": t.get("reason"),
            "exit_detail": t.get("exit_detail"),
            "bars_held": t.get("bars_held"),
            "entry_trade_price": t.get("entry_trade_price"),
            "entry_mark_price": t.get("entry_mark_price"),
            "mark_price_basis": bool(t.get("mark_price_basis")),
        }

    out = []

    # ---- Running live instances --------------------------------------
    for key, svc in live_trade_instances.items():
        if f"_{user.username}_" not in key:
            continue
        trades = []
        for symbol, trade in (getattr(svc, "oms", None) and svc.oms.active_trades or {}).items():
            trades.append(_open_trade_dict(symbol, trade))
        for t in (getattr(svc, "closed_trades", None) or []):
            trades.append(_closed_trade_dict(t))
        out.append({
            "source": "live", "kind": "live", "key": f"live:{key}",
            "instance_key": key, "session_id": None,
            "label": f"{getattr(svc, 'account_label', 'Primary')} · {svc.strategy_id}",
            "broker": svc.broker_name, "strategy_id": svc.strategy_id,
            "status": "running", "symbol": getattr(svc, "symbol", None) or "BTCUSDT",
            "trades": trades,
        })

    # ---- Running paper instances -------------------------------------
    for key, svc in paper_trade_instances.items():
        if f"_{user.username}_" not in key:
            continue
        trades = []
        for symbol, trade in (getattr(svc, "oms", None) and svc.oms.active_trades or {}).items():
            trades.append(_open_trade_dict(symbol, trade))
        for t in (getattr(svc, "closed_trades", None) or []):
            trades.append(_closed_trade_dict(t))
        out.append({
            "source": "paper", "kind": "paper", "key": f"paper:{key}",
            "instance_key": key, "session_id": getattr(svc, "session_id", None),
            "label": f"{svc.strategy_name or svc.strategy_id} · paper",
            "broker": getattr(svc, "broker_name", None), "strategy_id": svc.strategy_id,
            "status": "running", "symbol": getattr(svc, "symbol", None) or "BTCUSDT",
            "trades": trades,
        })

    # ---- Saved paper sessions (History; survives stop + restart) ------
    try:
        rows = db.query(PaperSession).filter(PaperSession.user_id == user.id) \
            .order_by(PaperSession.created_at.desc()).limit(30).all()
    except Exception:
        rows = []
    running_keys = set(paper_trade_instances.keys())
    for row in rows:
        # A session still running in this process is reported from the live
        # worker above; listing its saved snapshot too would double every trade.
        if row.instance_key in running_keys:
            continue
        closed = []
        for t in (row.closed_trades or []):
            if isinstance(t, dict):
                closed.append(_closed_trade_dict(t, symbol=row.symbol))
        open_positions = []
        for t in (row.open_positions or []):
            if isinstance(t, dict):
                open_positions.append({
                    "symbol": t.get("symbol") or row.symbol,
                    "direction": int(t.get("direction") or 1),
                    "status": "open",
                    "entry": t.get("entry"), "exit": None,
                    "current": t.get("current"),
                    "sl": t.get("sl"), "sl_plan": t.get("sl"),
                    "tp": t.get("tp"), "trail_stop": t.get("trail_stop"),
                    "trail_activation": None, "atr_at_entry": None,
                    "peak_price": None,
                    "lots": t.get("lots"), "margin_inr": t.get("margin_inr"),
                    "notional_usd": None,
                    "entry_time": t.get("entry_time"), "exit_time": None,
                    "exit_price": None, "pnl": t.get("pnl"), "fees": None,
                    "reason": None, "bars_held": t.get("bars_held"),
                    "entry_trade_price": None, "entry_mark_price": None,
                    "mark_price_basis": False,
                    "unrealised": True,
                })
        out.append({
            "source": "paper", "kind": "history", "key": f"history:{row.id}",
            "instance_key": row.instance_key, "session_id": row.id,
            "label": f"{row.strategy_name or row.strategy_id} · {row.status}",
            "broker": row.broker_name, "strategy_id": row.strategy_id,
            "status": row.status, "symbol": row.symbol or "BTCUSDT",
            "trades": open_positions + closed,
        })

    return {"sessions": out}


@app.get("/live-trade/status")
def get_live_status(user=Depends(get_current_user)):
    status_list = []
    for key, service in live_trade_instances.items():
        if f"_{user.username}_" in key:
            active_trades = []
            for symbol, trade in service.oms.active_trades.items():
                current = getattr(service, 'last_price', None) or getattr(trade, 'current_price', None) or trade.peak_price
                pnl_inr = (current - trade.entry_price) * trade.direction * trade.lots * getattr(service, 'conversion_rate', 85.0)
                leverage = getattr(service.config, 'leverage', 1)
                chg_pct = ((current - trade.entry_price) / trade.entry_price * 100) * trade.direction if trade.entry_price else 0.0
                active_trades.append({
                    "symbol": symbol, "direction": trade.direction, "entry": trade.entry_price,
                    "current": current, "pnl": pnl_inr, "chg_pct": chg_pct,
                    "margin": trade.margin_inr, "notional_usd": getattr(trade, 'notional_usd', 0),
                    "lots": getattr(trade, 'lots', 0), "leverage": leverage,
                    "entry_time": _to_ist(trade.entry_time),
                    # BTC perpetual: mark price (pricing basis) vs traded fill.
                    "mark": getattr(trade, 'current_mark_price', None) or None,
                    "entry_mark": getattr(trade, 'entry_mark_price', None) or None,
                    "entry_trade": getattr(trade, 'entry_trade_price', None) or None,
                    "mark_price_basis": bool(getattr(trade, 'mark_price_basis', False)),
                })
            status_list.append({
                "instance_key": key, "strategy_id": service.strategy_id,
                "broker_name": service.broker_name, "data_source": service.market_source,
                # Which saved connection (sub-account / API key) this instance
                # trades on, so 3-4 runs on 3-4 accounts are tellable apart.
                "account_label": getattr(service, "account_label", "Primary"),
                "taker_fee_bps": service.config.taker_fee_bps, "maker_fee_bps": service.config.maker_fee_bps,
                "leverage": getattr(service.config, 'leverage', 1),
                "margin_pct": getattr(service, 'margin_pct', 0),
                "last_price": service.last_price, "last_checked": service.last_checked,
                "last_trade_price": getattr(service, 'last_trade_price', None),
                "last_mark_price": getattr(service, 'last_mark_price', None),
                "mark_price_basis": bool(getattr(service, 'mark_price_basis', False)),
                "use_mark_price": bool(getattr(service, 'use_mark_price', True)),
                "contract": contract_label(service.market_source, "BTCUSDT"),
                "perpetual_symbol": getattr(service, 'contract_symbol', None) or perpetual_symbol(service.market_source, "BTCUSDT"),
                "trading_windows": getattr(service, 'window_guard', None).summary() if getattr(service, 'window_guard', None) else None,
                "entry_paused": bool(getattr(service, 'window_guard', None).is_blocked(datetime.utcnow())) if getattr(service, 'window_guard', None) else False,
                "blocked_entries": int(getattr(service, 'blocked_entries', 0) or 0),
                # Entry gating: signals refused because a position is already
                # open (here or on the venue), the candle was already traded, or
                # a post-exit cooldown is running.
                "skipped_entries": int(getattr(service, 'skipped_entries', 0) or 0),
                "last_skip_reason": getattr(service, 'last_skip_reason', None),
                # Data honesty: true while the candle set is too old to open
                # trades on (venue feed down / stored fallback being served).
                "candles_stale": bool(getattr(service, 'candles_stale', False)),
                # Shared account: how many live runs point at this same API key.
                # They share ONE netted position per contract, so they take
                # turns — surfaced here so "why is my strategy idle?" is
                # answerable without reading the log.
                "shared_account": _shared_account_status(service),
                # Live price feed: which source is driving exit checks, whether
                # it is connected, and how old its last price is. Surfaced so a
                # silently-dead socket is visible instead of quietly falling
                # back to the 60-second cadence.
                "price_feed": {
                    "mode": getattr(service, "price_feed_mode", "off"),
                    "tick_interval": getattr(service, "tick_interval", 60.0),
                    "fast_ticks": int(getattr(service, "fast_ticks", 0) or 0),
                    **(getattr(service, "tick_feed", None).stats()
                       if getattr(service, "tick_feed", None) else {}),
                },
                # What the venue itself reports for this contract, so a position
                # this instance did not open is visible instead of silent.
                "exchange_position": getattr(service, 'exchange_position', None),
                "last_order_error": getattr(service, 'last_order_error', None),
                "heartbeat": (getattr(service, "heartbeat", None).stats()
                              if getattr(service, "heartbeat", None)
                              else {"enabled": bool(getattr(service, "heartbeat_enabled", False)),
                                    "created": False, "stale": True}),
                "testnet": bool(getattr(getattr(service, "broker", None), "testnet", False)),
                # Credential state of THIS instance: "ok", or "rejected" with the
                # venue's own error, the backoff clock, how many entries were held
                # and the reload counter. Without it a dead key shows up as a
                # running-but-empty terminal, which reads like a strategy problem.
                "credentials": service.credentials_status(),
                "connection_id": getattr(service, "connection_id", None),
                "is_running": service.is_running, "active_trades": active_trades
            })
    return status_list


class LiveReloadCredentialsRequest(BaseModel):
    instance_key: str
    # Optionally point the instance at a different saved connection first — the
    # recovery path when the connection it started on was deleted and re-added.
    connection_id: Optional[int] = None


@app.post("/live-trade/reload-credentials")
async def reload_live_credentials(payload: LiveReloadCredentialsRequest,
                                  user=Depends(get_current_user), db=Depends(get_db)):
    """Re-read a running instance's saved broker connection and swap it in.

    The alternative was a stop/start, which loses the instance's local book (an
    open trade it has been marking to market, its resting-leg ids, its cooldown
    clock) to fix nothing but credentials. Swapping the client keeps all of that
    and moves the account it queues on, its rate-limit budget and its deadman
    switch to the new key in the same step.

    Force-reloading is always safe: a key that is *not* re-saved simply fails
    the probe again and the instance goes back to holding entries.
    """
    key = payload.instance_key
    if f"_{user.username}_" not in key:
        raise HTTPException(status_code=403, detail="Not your instance")
    service = live_trade_instances.get(key)
    if service is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    if payload.connection_id is not None:
        row = db.query(BrokerConnection).filter(
            BrokerConnection.id == payload.connection_id,
            BrokerConnection.user_id == user.id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Broker connection not found")
        service.connection_id = row.id
    result = await asyncio.to_thread(service.reload_credentials, True)
    if (result.get("credentials") or {}).get("state") == "ok":
        await service.credentials_recovered()
    return {"instance_key": key, **result}

# --- DATA ENDPOINTS ---
@app.get("/klines")
def get_klines(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 500,
               source: str = "Delta", start_date: Optional[str] = None,
               end_date: Optional[str] = None, db=Depends(get_db)):
    try:
        # A date window is required for strategy/backtest overlays: the last-N
        # candles from "now" never contain a 2020–2024 trade marker.
        q = db.query(Klines).filter(
            Klines.symbol == symbol,
            Klines.interval == interval,
            Klines.source == normalize_source(source)
        )
        windowed = bool(start_date or end_date)
        if start_date:
            try:
                q = q.filter(Klines.event_time >= datetime.strptime(start_date, "%Y-%m-%d"))
            except ValueError:
                pass
        if end_date:
            try:
                q = q.filter(Klines.event_time < datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1))
            except ValueError:
                pass
        cap = max(1, min(int(limit or 500), 60000 if windowed else 5000))
        if windowed:
            data = q.order_by(Klines.event_time.asc()).limit(cap).all()
        else:
            data = q.order_by(Klines.event_time.desc()).limit(cap).all()
            data.reverse()

        if data:
            formatted = []
            for k in data:
                formatted.append({
                    "time": int(_utc_ts(k.event_time)),
                    "open": k.open,
                    "high": k.high,
                    "low": k.low,
                    "close": k.close,
                    "volume": k.volume,
                })
            return formatted

        # Fallback to the selected source's public API if its seed is empty.
        from .services.data_sync import DataSyncService
        rows = DataSyncService.fetch_klines(normalize_source(source), symbol, interval, limit=limit)
        return [{"time": int(pd.Timestamp(row['event_time']).timestamp()),
                 "open": row['open'], "high": row['high'], "low": row['low'],
                 "close": row['close'], "volume": row.get('volume', 0)} for row in rows]
    except Exception as e:
        # No local data and the remote API is unreachable — return an empty
        # series so the UI shows an empty chart instead of a hard error.
        print(f"Klines fetch error for {symbol}/{interval}: {e}")
        return []

@app.get("/ticks")
def get_ticks(symbol: str = "BTCUSD", source: str = "Delta",
              start_date: Optional[str] = None, end_date: Optional[str] = None,
              limit: int = 5000, user=Depends(get_current_user), db=Depends(get_db)):
    """Raw live ticks stored from the venue stream / REST poll.

    Windowed like /klines so a backtest range actually contains the ticks
    that fired inside it (a last-N fetch from "now" would drop them).
    """
    from .services.tick_store import flush_ticks, query_ticks
    flush_ticks()
    start = end = None
    if start_date:
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError:
            start = None
    if end_date:
        try:
            end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        except ValueError:
            end = None
    rows = query_ticks(normalize_source(source), symbol, start=start, end=end,
                       limit=limit, db=db)
    out = []
    for row in rows:
        out.append({
            "time": int(_utc_ts(row.event_time)),
            "mark_price": row.mark_price,
            "last_price": row.last_price,
            "index_price": row.index_price,
            "bid": row.bid,
            "ask": row.ask,
            "feed": row.feed_kind,
        })
    return out


@app.get("/ticks/latest")
def get_latest_tick(symbol: str = "BTCUSD", source: str = "Delta",
                    user=Depends(get_current_user), db=Depends(get_db)):
    from .services.tick_store import flush_ticks, latest_tick
    flush_ticks()
    row = latest_tick(normalize_source(source), symbol, db=db)
    if row is None:
        return None
    return {
        "time": int(_utc_ts(row.event_time)),
        "source": row.source, "symbol": row.symbol,
        "mark_price": row.mark_price, "last_price": row.last_price,
        "index_price": row.index_price, "bid": row.bid, "ask": row.ask,
        "feed": row.feed_kind,
    }


@app.get("/ticks/ohlc")
def get_tick_ohlc(symbol: str = "BTCUSD", source: str = "Delta",
                  interval: str = "1m", start_date: Optional[str] = None,
                  end_date: Optional[str] = None, limit: int = 20000,
                  user=Depends(get_current_user), db=Depends(get_db)):
    """Resample stored ticks into OHLC candles (volume = ticks in the bar)."""
    from .services.tick_store import OHLC_SECONDS, flush_ticks, query_ticks, ticks_to_ohlc
    if str(interval).lower() not in OHLC_SECONDS:
        raise HTTPException(status_code=400,
                            detail=f"interval must be one of {', '.join(OHLC_SECONDS)}")
    flush_ticks()
    start = end = None
    if start_date:
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError:
            start = None
    if end_date:
        try:
            end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        except ValueError:
            end = None
    rows = query_ticks(normalize_source(source), symbol, start=start, end=end,
                       limit=limit, db=db)
    bars = ticks_to_ohlc(rows, interval)
    return [{"time": int(_utc_ts(b["time"])), "open": b["open"], "high": b["high"],
             "low": b["low"], "close": b["close"], "volume": b["volume"]} for b in bars]


@app.get("/ticks/stats")
def get_tick_stats(user=Depends(get_current_user), db=Depends(get_db)):
    from .services.tick_store import collector_stats, flush_ticks, series_stats
    flush_ticks()
    return {"series": series_stats(db), "collector": collector_stats()}


@app.get("/symbols")
def list_symbols(source: str = "Delta", user=Depends(get_current_user), db=Depends(get_db)):
    """Distinct symbols available in the selected source's local store."""
    try:
        rows = db.query(Klines.symbol).filter(Klines.source == normalize_source(source)).distinct().all()
        return [r[0] for r in rows]
    except Exception:
        return ["BTCUSDT"]


class BrokerSettingsUpdate(BaseModel):
    api_key: Optional[str] = ''
    api_secret: Optional[str] = ''
    initial_capital: float = 20000.0
    margin_pct: float = 25.0
    broker_name: str = DEFAULT_BROKER
    connection_id: Optional[int] = None
    passphrase: Optional[str] = None
    is_testnet: bool = False
    # BTC perpetual: risk the account on the exchange MARK price (default on).
    use_mark_price: Optional[bool] = None


@app.post("/broker-settings")
def update_broker_settings(settings: BrokerSettingsUpdate, user=Depends(get_current_user), db=Depends(get_db)):
    code = normalize_source(settings.broker_name)
    if settings.connection_id:
        row = db.query(BrokerConnection).filter(BrokerConnection.id == settings.connection_id,
                                                BrokerConnection.user_id == user.id).first()
        if not row: raise HTTPException(status_code=404, detail="Broker connection not found")
        if settings.api_key: row.api_key = settings.api_key
        if settings.api_secret: row.api_secret = encrypt_secret(settings.api_secret)
        row.broker_code = code; row.passphrase = settings.passphrase; row.is_testnet = int(settings.is_testnet)
    elif settings.api_key and settings.api_secret:
        row = db.query(BrokerConnection).filter(BrokerConnection.user_id == user.id,
                                                BrokerConnection.broker_code == code).first()
        if row:
            row.api_key = settings.api_key; row.api_secret = encrypt_secret(settings.api_secret)
            row.is_testnet = int(settings.is_testnet)
        else:
            db.add(BrokerConnection(user_id=user.id, broker_code=code, label='Primary',
                                    api_key=settings.api_key, api_secret=encrypt_secret(settings.api_secret),
                                    passphrase=settings.passphrase, is_testnet=int(settings.is_testnet), is_active=1))
        # Keep legacy columns synchronized for old workers and clients.
        user.api_key = settings.api_key; user.api_secret = encrypt_secret(settings.api_secret); user.broker_name = code
    user.initial_capital = settings.initial_capital
    user.margin_deployment_pct = settings.margin_pct
    user.broker_name = code
    if settings.use_mark_price is not None:
        user.use_mark_price = int(bool(settings.use_mark_price))
    db.commit()
    return {"status": "Settings updated", "broker_name": code,
            "use_mark_price": bool(user.use_mark_price if user.use_mark_price is not None else 1)}


@app.get("/broker-settings")
def get_broker_settings(user=Depends(get_current_user), db=Depends(get_db)):
    rows = db.query(BrokerConnection).filter(BrokerConnection.user_id == user.id).all()
    return {
        "api_key": _mask(user.api_key),
        # The secret is never returned, not even masked: anything that reaches
        # the browser is reachable in dev tools, and Delta's guidance is that
        # the React side must never see the secret in any form.
        "has_secret": bool(user.api_secret),
        "broker_name": user.broker_name or DEFAULT_BROKER,
        "initial_capital": user.initial_capital,
        "margin_deployment_pct": user.margin_deployment_pct,
        # BTC perpetual pricing + the account's default "skip new trades"
        # schedule (see GET/PUT /trading-windows).
        "use_mark_price": bool(user.use_mark_price if user.use_mark_price is not None else 1),
        "trading_windows": _user_window_config(user).model_dump(),
        "connections": [_connection_dict(row) for row in rows],
    }

# --- DASHBOARD ---
@app.get("/dashboard/stats")
def get_dashboard_stats(user=Depends(get_current_user), db=Depends(get_db)):
    """Aggregate backtest stats for the signed-in user.

    Incomplete placeholder runs (roi / win_rate still NULL) used to crash this
    endpoint with a TypeError, which made the dashboard cards look broken.
    """
    try:
        runs = db.query(BacktestRun).filter(BacktestRun.user_id == user.id)\
            .order_by(BacktestRun.timestamp.desc()).all()
        completed = [r for r in runs if r.total_trades is not None]
        rois = [float(r.roi) for r in completed if r.roi is not None]
        win_rates = [float(r.win_rate) for r in completed if r.win_rate is not None]
        last = completed[0] if completed else None
        return {
            "best_roi": max(rois) if rois else 0.0,
            "avg_roi": (sum(rois) / len(rois)) if rois else 0.0,
            "total_runs": len(runs),
            "completed_runs": len(completed),
            "avg_win_rate": (sum(win_rates) / len(win_rates)) if win_rates else 0.0,
            "best_win_rate": max(win_rates) if win_rates else 0.0,
            "last_run": None if not last else {
                "id": last.id, "name": last.name, "roi": last.roi,
                "win_rate": last.win_rate, "total_trades": last.total_trades,
                "timestamp": last.timestamp,
            },
        }
    except Exception as e:
        print(f"Dashboard stats error: {e}")
        return {
            "best_roi": 0.0, "avg_roi": 0.0, "total_runs": 0, "completed_runs": 0,
            "avg_win_rate": 0.0, "best_win_rate": 0.0, "last_run": None,
        }


# ===========================================================================
# LIVE ACCOUNT — orders, positions, fills, margin and risk
#
# Everything below talks to the real broker through BrokerClient, which
# throttles each call against that venue's rate limits (Delta: 10 000 weight
# per fixed 5-minute window; Binance: 2 400 weight/min and 1 200 orders/min —
# see app/core/rate_limit.py). Broker payloads are normalized by
# app/services/broker_account.py so both venues render the same way.
# ===========================================================================
def _canonical_broker_code(db, value: Optional[str]) -> Optional[str]:
    """Resolve any spelling of an integration to its registry code.

    A saved connection can carry the canonical code (``Binance``), the code in
    another case, or the *display name* the dropdown shows (``Binance Futures``)
    — hand-edited rows and rows seeded by scripts use all three. Comparing that
    column literally is why a connection that is clearly in the database still
    reads as "API keys not configured", so every lookup resolves first.
    """
    if not value:
        return None
    text = str(value).strip()
    normalized = normalize_source(text)
    row = db.query(BrokerDefinition).filter(
        func.lower(BrokerDefinition.code) == normalized.lower()).first()
    if row:
        return row.code
    row = db.query(BrokerDefinition).filter(
        func.lower(BrokerDefinition.name) == text.lower()).first()
    return row.code if row else normalized


def _connection_is_active(row) -> bool:
    """``is_active`` is treated as ON unless it is explicitly 0.

    The column default only applies to rows written through SQLAlchemy, so a
    connection inserted straight into the database carries NULL — which must
    not silently disable the credentials someone just added.
    """
    value = getattr(row, 'is_active', None)
    if value is None:
        return True
    try:
        return int(value) != 0
    except (TypeError, ValueError):
        return True


def _user_connections(db, user, code: str):
    """This account's saved connections that belong to ``code``."""
    rows = db.query(BrokerConnection).filter(BrokerConnection.user_id == user.id).all()
    matched = [r for r in rows if _canonical_broker_code(db, r.broker_code) == code]
    matched.sort(key=lambda r: (r.created_at or datetime.min, r.id or 0))
    return matched


def _credentials_problem(db, user, code: str, connection_id: Optional[int] = None) -> str:
    """Say *which* of the possible causes applies, instead of one generic line.

    The same 400 used to cover five different situations (no row, row saved
    under the display name, row with ``is_active`` NULL, row switched off, keys
    saved on a different login), none of which the user could tell apart from
    the message alone.
    """
    rows = _user_connections(db, user, code)
    if connection_id is not None:
        rows = [r for r in rows if r.id == connection_id]
        if not rows:
            return (f"No broker connection #{connection_id} for {code} on the account "
                    f"'{user.username}'. Connections are saved per login, so keys added "
                    f"while signed in as another account are not shared.")
    usable = [r for r in rows if _connection_is_active(r)]
    if not usable:
        if rows:
            labels = ", ".join(f"'{r.label or r.broker_code}'" for r in rows)
            return (f"The {code} connection {labels} on the account '{user.username}' is "
                    f"switched off. Turn it back on in Broker Settings → Broker connections.")
        return (f"No API keys for {code} on the account '{user.username}'. The Exchange "
                f"Registry only registers the integration (adapter kind and URLs) and holds "
                f"no credentials — add the API key and secret under 'Add broker connection' "
                f"in Broker Settings.")
    blank = [r for r in usable if not (r.api_key and r.api_secret)]
    if blank:
        labels = ", ".join(f"'{r.label or r.broker_code}'" for r in blank)
        return (f"The {code} connection {labels} on the account '{user.username}' has no API "
                f"secret saved. Edit that connection and re-enter the secret — secrets are "
                f"never returned by the API, so an edit keeps the stored one unless a new "
                f"one is typed in.")
    return f"API keys not configured for {code} on the account '{user.username}'."


def _pick_connection(db, user, code: str, connection_id: Optional[int] = None):
    """The connection to trade with, or ``(None, problem)`` when there is none."""
    rows = _user_connections(db, user, code)
    if connection_id is not None:
        rows = [r for r in rows if r.id == connection_id]
    usable = [r for r in rows if _connection_is_active(r) and r.api_key and r.api_secret]
    if usable:
        return usable[0], None
    return None, _credentials_problem(db, user, code, connection_id)


def _live_client(db, user, broker_code: str, connection_id: Optional[int] = None,
                 require_credentials: bool = True):
    """Build a BrokerClient from a broker code + the user's credentials."""
    from .services.broker_client import BrokerClient
    code = normalize_source(broker_code)
    definition = db.query(BrokerDefinition).filter(
        BrokerDefinition.code == code, BrokerDefinition.enabled == 1).first()
    if not definition:
        raise HTTPException(status_code=400, detail=f"Broker '{code}' is not configured or enabled")
    connection, problem = _pick_connection(db, user, code, connection_id)
    api_key = (connection.api_key if connection else None) or (user.api_key or '')
    api_secret = (_decrypt_secret(connection.api_secret) if connection
                  else _decrypt_secret(user.api_secret))
    passphrase = (connection.passphrase if connection else None) or ''
    testnet = bool(connection.is_testnet) if connection else False
    if require_credentials and (not api_key or not api_secret):
        raise HTTPException(status_code=400, detail=problem or
                            f"API keys not configured for {code}. Add them in Broker Settings.")
    client = BrokerClient(api_key, api_secret, code, passphrase, testnet, definition)
    return client, definition, (connection.id if connection else None)


class LiveOrderRequest(BaseModel):
    broker: str
    connection_id: Optional[int] = None
    symbol: str = 'BTCUSDT'
    side: str
    order_type: str = 'market'
    size: float = 0.0
    size_in_btc: bool = True
    price: Optional[float] = None
    stop_price: Optional[float] = None
    trail_amount: Optional[float] = None
    reduce_only: bool = False
    post_only: bool = False
    time_in_force: str = 'gtc'
    working_type: str = 'MARK'
    client_order_id: Optional[str] = None
    # Bracket: attach protection legs to the entry.
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    stop_trigger: str = 'mark_price'
    # 'manual' from the terminal ticket, 'strategy' from a live-trade instance.
    source: str = 'manual'
    instance_key: Optional[str] = None


class LiveCancelRequest(BaseModel):
    broker: str
    connection_id: Optional[int] = None
    order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    symbol: str = 'BTCUSDT'


class LiveClosePositionRequest(BaseModel):
    broker: str
    connection_id: Optional[int] = None
    symbol: str = 'BTCUSDT'
    size: Optional[float] = None
    size_in_btc: bool = True
    instance_key: Optional[str] = None


class LiveLeverageRequest(BaseModel):
    broker: str
    connection_id: Optional[int] = None
    symbol: str = 'BTCUSDT'
    leverage: int = 1


class LiveMarginModeRequest(BaseModel):
    broker: str
    connection_id: Optional[int] = None
    symbol: str = 'BTCUSDT'
    mode: str = 'isolated'
    # Delta India keeps margin mode per (sub)account: pass the target
    # account's user id to set it there (default: this key's own account).
    subaccount_user_id: Optional[str] = None


class LiveMarginModeSyncRequest(BaseModel):
    """Mirror a reference account's margin mode onto a target (sub)account."""
    broker: str
    connection_id: Optional[int] = None
    symbol: str = 'BTCUSDT'
    reference_user_id: str
    target_user_id: str
    dry_run: bool = False


class LiveMarginModeAllRequest(BaseModel):
    """Apply one margin mode to EVERY account under the key (main + subs)."""
    broker: str
    connection_id: Optional[int] = None
    symbol: str = 'BTCUSDT'
    mode: str = 'isolated'
    dry_run: bool = False


class LivePositionMarginRequest(BaseModel):
    broker: str
    connection_id: Optional[int] = None
    symbol: str = 'BTCUSDT'
    amount: float = 0.0


class LiveMMPConfigRequest(BaseModel):
    broker: str = 'Delta'
    connection_id: Optional[int] = None
    asset: str
    window_interval: Optional[int] = None
    freeze_interval: Optional[int] = None
    trade_limit: Optional[str] = None
    delta_limit: Optional[str] = None
    vega_limit: Optional[str] = None
    mmp: str = 'mmp1'


class LiveMMPResetRequest(BaseModel):
    broker: str = 'Delta'
    connection_id: Optional[int] = None
    asset: str
    mmp: str = 'mmp1'


class LiveSnapshotRequest(BaseModel):
    broker: str
    connection_id: Optional[int] = None
    symbol: str = 'BTCUSDT'
    include_history: bool = True
    history_limit: int = 50


def _record_placed_order(client, user, broker_code, connection_id, response, request, contract_value):
    """Persist the local mirror of a placed order (plain or bracketed)."""
    from .services.broker_account import normalize_order, record_order, split_order_response
    recorded = []
    parent_id = None
    for row, leg_name in split_order_response(response, broker_code):
        order = normalize_order(row, broker_code, contract_value)
        if order.get('error'):
            continue
        order['symbol'] = client.perpetual_symbol(request.symbol)
        record_order(user.id, broker_code, order, source=request.source,
                     instance_key=request.instance_key, connection_id=connection_id,
                     leg=leg_name, parent_order_id=(None if leg_name == 'entry' else parent_id),
                     raw=row)
        if leg_name == 'entry':
            parent_id = order.get('order_id')
        recorded.append(order)
    return recorded


@app.post('/live-account/snapshot')
def live_account_snapshot(payload: LiveSnapshotRequest, user=Depends(get_current_user), db=Depends(get_db)):
    """Positions / open orders / stop orders / fills / history / margin for one broker."""
    from .services.broker_account import account_snapshot
    client, definition, _ = _live_client(db, user, payload.broker, payload.connection_id)
    return account_snapshot(client, payload.symbol, include_history=payload.include_history,
                            history_limit=payload.history_limit)


@app.post('/live-account/orders')
def live_place_order(payload: LiveOrderRequest, user=Depends(get_current_user), db=Depends(get_db)):
    """Place a market / limit / stop / take-profit order, optionally bracketed."""
    from .services.broker_account import (normalize_order, record_fills, record_order)
    try:
        # --- Input validation (DB & exchange changelog aware) ---
        broker_code = validate_broker_code(payload.broker)
        symbol = validate_symbol(payload.symbol or 'BTCUSDT')
        side = validate_side(payload.side)
        order_type = validate_order_type(payload.order_type)
        size = validate_size(payload.size, min_size=0.0001, max_size=10000.0)
        if payload.price is not None:
            validate_price(payload.price)
        if payload.stop_price is not None:
            validate_price(payload.stop_price)
        if payload.stop_loss is not None:
            validate_price(payload.stop_loss)
        if payload.take_profit is not None:
            validate_price(payload.take_profit)
    except PhantomValidationError as ve:
        raise HTTPException(status_code=ve.status_code, detail=ve.message)

    try:
        client, definition, connection_id = _live_client(db, user, payload.broker, payload.connection_id)
        instrument = client.get_instrument(symbol) or {}
        contract_value = float(instrument.get('contract_value') or 1.0) or 1.0
        client_order_id = payload.client_order_id or f"ph-{uuid.uuid4().hex[:24]}"
    except HTTPException:
        raise
    except Exception as exc:
        phantom_logger.error(f"live_place_order setup failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to prepare order: {exc}")

    if payload.stop_loss is not None or payload.take_profit is not None:
        response = client.place_bracket_order(
            symbol, side, payload.size, price=payload.price,
            stop_loss_price=payload.stop_loss, take_profit_price=payload.take_profit,
            client_order_id=client_order_id, trigger_method=payload.stop_trigger,
            size_in_btc=payload.size_in_btc, trail_amount=payload.trail_amount)
    else:
        response = client.place_order(
            symbol, side, payload.order_type, payload.size, price=payload.price,
            stop_price=payload.stop_price, reduce_only=payload.reduce_only,
            client_order_id=client_order_id, time_in_force=payload.time_in_force,
            post_only=payload.post_only, working_type=payload.working_type,
            trail_amount=payload.trail_amount, size_in_btc=payload.size_in_btc)

    orders = _record_placed_order(client, user, definition.code, connection_id, response, payload, contract_value)
    fills_recorded = 0
    if orders:
        # A market order can fill before the exchange answers; capture it.
        try:
            fills = client.get_fills(symbol, limit=10)
            if isinstance(fills, list) and not (isinstance(fills, dict) and fills.get('error')):
                from .services.broker_account import normalize_fill
                fills_recorded = record_fills(user.id, definition.code,
                                              [normalize_fill(f, definition.code, contract_value) for f in fills],
                                              source=payload.source, instance_key=payload.instance_key)
        except Exception:
            fills_recorded = 0
    return {
        "status": "rejected" if (isinstance(response, dict) and response.get('error')) else "placed",
        "broker": definition.code,
        "symbol": client.perpetual_symbol(symbol),
        "contract_value": contract_value,
        "client_order_id": client_order_id,
        "response": response,
        "orders": orders,
        "fills_recorded": fills_recorded,
        "rate_limits": client.rate_limit_usage(),
    }


@app.post('/live-account/orders/cancel')
def live_cancel_order(payload: LiveCancelRequest, user=Depends(get_current_user), db=Depends(get_db)):
    """Cancel one order by exchange id or by our client order id."""
    from .services.broker_account import mark_order_cancelled
    client, definition, connection_id = _live_client(db, user, payload.broker, payload.connection_id)
    if not payload.order_id and not payload.client_order_id:
        raise HTTPException(status_code=400, detail="order_id or client_order_id is required")
    response = client.cancel_order(payload.order_id, payload.symbol, payload.client_order_id)
    local_row = None
    if isinstance(response, dict) and not response.get('error'):
        local_row = mark_order_cancelled(user.id, definition.code,
                                         order_id=str(payload.order_id or ''),
                                         client_order_id=payload.client_order_id)
    return {"status": "rejected" if (isinstance(response, dict) and response.get('error')) else "cancelled",
            "response": response, "local": local_row,
            "rate_limits": client.rate_limit_usage()}


@app.post('/live-account/orders/cancel-all')
def live_cancel_all_orders(payload: LiveCancelRequest, user=Depends(get_current_user), db=Depends(get_db)):
    """Cancel every open order on the contract (or the whole account)."""
    client, definition, _ = _live_client(db, user, payload.broker, payload.connection_id)
    response = client.cancel_all_orders(payload.symbol)
    return {"status": "rejected" if (isinstance(response, dict) and response.get('error')) else "cancelled",
            "response": response, "rate_limits": client.rate_limit_usage()}


@app.post('/live-account/positions/close')
def live_close_position(payload: LiveClosePositionRequest, user=Depends(get_current_user), db=Depends(get_db)):
    """Flatten the open position on the contract."""
    client, definition, _ = _live_client(db, user, payload.broker, payload.connection_id)
    response = client.close_position(payload.symbol, size=payload.size,
                                     size_in_btc=payload.size_in_btc)
    return {"status": "rejected" if (isinstance(response, dict) and response.get('error')) else "closed",
            "response": response, "rate_limits": client.rate_limit_usage()}


@app.post('/live-account/leverage')
def live_set_leverage(payload: LiveLeverageRequest, user=Depends(get_current_user), db=Depends(get_db)):
    try:
        leverage = validate_leverage(payload.leverage)
        symbol = validate_symbol(payload.symbol)
        broker_code = validate_broker_code(payload.broker)
    except PhantomValidationError as ve:
        raise HTTPException(status_code=ve.status_code, detail=ve.message)
    client, definition, _ = _live_client(db, user, payload.broker, payload.connection_id)
    response = client.set_leverage(symbol, int(leverage))
    if isinstance(response, dict) and response.get('error'):
        cls = classify_broker_error(response.get('error'), broker=definition.code)
        phantom_logger.warning(f"set_leverage failed [{cls['category']}]: {response.get('error')}")
    return {"status": "rejected" if (isinstance(response, dict) and response.get('error')) else "ok",
            "response": response, "rate_limits": client.rate_limit_usage()}


@app.post('/live-account/margin-mode')
def live_set_margin_mode(payload: LiveMarginModeRequest, user=Depends(get_current_user), db=Depends(get_db)):
    try:
        mode = validate_margin_mode(payload.mode)
        symbol = validate_symbol(payload.symbol)
        broker_code = validate_broker_code(payload.broker)
    except PhantomValidationError as ve:
        raise HTTPException(status_code=ve.status_code, detail=ve.message)
    client, definition, _ = _live_client(db, user, payload.broker, payload.connection_id)
    response = client.set_margin_mode(symbol, mode,
                                      subaccount_user_id=payload.subaccount_user_id)
    if isinstance(response, dict) and response.get('error'):
        cls = classify_broker_error(response.get('error'), broker=definition.code)
        phantom_logger.warning(f"set_margin_mode failed [{cls['category']}]: {response.get('error')}")
    return {"status": "rejected" if (isinstance(response, dict) and response.get('error')) else "ok",
            "response": response, "rate_limits": client.rate_limit_usage()}


@app.get('/live-account/balance')
def live_account_balance(broker: Optional[str] = None, connection_id: Optional[int] = None,
                         user=Depends(get_current_user), db=Depends(get_db)):
    """Wallet equity / used margin for one broker account.

    The Live Trading page used to hardcode "Connecting..." because nothing ever
    fetched the balance. This is that call: it always answers 200 with a
    `state` the UI can render — `ok`, `no_credentials`, or `error` with the
    venue's own message — so the panel shows a number or a reason, never an
    eternal spinner.
    """
    from .services.broker_account import normalize_balance
    code = normalize_source(broker)
    try:
        client, definition, cid = _live_client(db, user, code, connection_id,
                                               require_credentials=True)
    except HTTPException as exc:
        return {"state": "no_credentials", "broker": code, "error": exc.detail,
                "wallet_balance": None, "available_balance": None}
    try:
        payload = client.get_account_balance()
    except Exception as exc:
        return {"state": "error", "broker": code, "connection_id": cid,
                "error": f"{exc.__class__.__name__}: {exc}",
                "wallet_balance": None, "available_balance": None}
    balance = normalize_balance(payload, code)
    if isinstance(balance, dict) and balance.get("error"):
        return {"state": "error", "broker": code, "connection_id": cid,
                "error": balance["error"], "wallet_balance": None,
                "available_balance": None}
    return {"state": "ok", "broker": code, "connection_id": cid,
            "testnet": bool(getattr(client, "testnet", False)), **balance}


@app.get('/live-trade/results')
def live_trade_results(strategy_id: Optional[str] = None, connection_id: Optional[int] = None,
                       user=Depends(get_current_user), db=Depends(get_db)):
    """Per-strategy live performance, so a client can judge ONE strategy.

    The live status feed answers "what is running right now"; this answers "how
    has this strategy actually done on this account". Results are grouped by
    (strategy, account) because the same strategy on two sub-accounts is two
    different results — which is exactly the comparison the multi-account setup
    exists to make. Open positions are reported separately and never folded
    into the realised stats.
    """
    from .services.paper_history import summarize
    groups = {}
    for key, service in live_trade_instances.items():
        if f"_{user.username}_" not in key:
            continue
        sid = str(getattr(service, "strategy_id", ""))
        if strategy_id is not None and sid != str(strategy_id):
            continue
        cid = getattr(service, "connection_id", None)
        if connection_id is not None and cid != connection_id:
            continue
        gkey = (sid, cid)
        group = groups.setdefault(gkey, {
            "strategy_id": sid,
            "strategy_name": getattr(service, "strategy_name", None) or sid,
            "connection_id": cid,
            "account_label": getattr(service, "account_label", "Primary"),
            "broker_name": getattr(service, "broker_name", None),
            "instances": [], "closed_trades": [], "open_positions": [],
            "initial_capital": 0.0, "leverage": getattr(service.config, "leverage", None),
            "margin_pct": getattr(service, "margin_pct", None),
            "last_order_error": None,
        })
        group["instances"].append(key)
        group["initial_capital"] += float(getattr(service, "initial_capital", 0.0) or 0.0)
        group["closed_trades"].extend(list(getattr(service, "closed_trades", []) or []))
        if getattr(service, "last_order_error", None):
            group["last_order_error"] = service.last_order_error
        rate = float(getattr(service, "conversion_rate", 85.0) or 85.0)
        price = getattr(service, "last_price", None)
        for symbol, trade in (getattr(service, "oms", None).active_trades.items()
                              if getattr(service, "oms", None) else []):
            current = price or trade.entry_price
            group["open_positions"].append({
                "instance_key": key, "symbol": symbol,
                "direction": int(trade.direction), "entry": float(trade.entry_price),
                "current": float(current), "lots": float(getattr(trade, "lots", 0) or 0),
                "unrealised_pnl": (float(current) - float(trade.entry_price))
                                  * int(trade.direction) * float(getattr(trade, "lots", 0) or 0) * rate,
                "entry_time": _to_ist(trade.entry_time),
            })

    out = []
    for group in groups.values():
        closed = group.pop("closed_trades")
        initial = group["initial_capital"] or None
        net = sum(float(t.get("pnl") or 0.0) for t in closed)
        stats = summarize(closed, initial, (initial + net) if initial else None)
        out.append({**group, "closed_trade_count": len(closed),
                    "recent_trades": closed[-50:], **stats,
                    "unrealised_pnl": sum(p["unrealised_pnl"] for p in group["open_positions"]),
                    "open_position_count": len(group["open_positions"])})
    out.sort(key=lambda g: (g["strategy_name"], g["account_label"] or ""))
    return out


@app.post('/live-account/margin-mode-all')
def live_set_margin_mode_all(payload: LiveMarginModeAllRequest, user=Depends(get_current_user), db=Depends(get_db)):
    """Apply one margin mode to EVERY account under the key (main + all sub-accounts).

    Delta India keeps margin mode per (sub)account, so a "portfolio mode"
    switch is one PUT per account. The listing needs the main/parent key —
    a sub-account key can only manage itself, and the result says so. Per-account
    outcomes come back in ``response.results``; ``status`` rolls them up as
    ok / partial / rejected.
    """
    try:
        mode = validate_margin_mode(payload.mode)
        symbol = validate_symbol(payload.symbol)
        broker_code = validate_broker_code(payload.broker)
    except PhantomValidationError as ve:
        raise HTTPException(status_code=ve.status_code, detail=ve.message)
    client, definition, _ = _live_client(db, user, payload.broker, payload.connection_id)
    result = client.set_margin_mode_all(mode, symbol, payload.dry_run)
    if isinstance(result, dict) and (result.get('error') or result.get('status') == 'rejected'):
        cls = classify_broker_error(result.get('error', 'all accounts rejected'), broker=definition.code)
        phantom_logger.warning(f"margin-mode-all failed [{cls['category']}]: {result.get('error') or result}")
    return {"status": result.get("status", "rejected"),
            "response": result, "rate_limits": client.rate_limit_usage()}


@app.post('/live-account/margin-mode-sync')
def live_sync_margin_mode(payload: LiveMarginModeSyncRequest, user=Depends(get_current_user), db=Depends(get_db)):
    """Mirror one (sub)account's margin mode onto another (Delta India).

    The flow Delta's integration guidance spells out: list the accounts under
    the main key, read the reference account's ``margin_mode``, apply it to
    the target via ``PUT /v2/users/margin_mode``. Read-only with
    ``dry_run`` — and the listing must come from the main/parent key, which
    the error reports when it cannot.
    """
    try:
        symbol = validate_symbol(payload.symbol)
        broker_code = validate_broker_code(payload.broker)
    except PhantomValidationError as ve:
        raise HTTPException(status_code=ve.status_code, detail=ve.message)
    client, definition, _ = _live_client(db, user, payload.broker, payload.connection_id)
    result = client.sync_margin_mode(payload.reference_user_id, payload.target_user_id,
                                     symbol, payload.dry_run)
    if isinstance(result, dict) and result.get('error'):
        cls = classify_broker_error(result.get('error'), broker=definition.code)
        phantom_logger.warning(f"margin-mode sync failed [{cls['category']}]: {result.get('error')}")
    return {"status": result.get("status", "rejected"),
            "response": result, "rate_limits": client.rate_limit_usage()}


@app.post('/live-account/position-margin')
def live_change_position_margin(payload: LivePositionMarginRequest, user=Depends(get_current_user), db=Depends(get_db)):
    client, definition, _ = _live_client(db, user, payload.broker, payload.connection_id)
    response = client.change_position_margin(payload.symbol, float(payload.amount))
    return {"status": "rejected" if (isinstance(response, dict) and response.get('error')) else "ok",
            "response": response, "rate_limits": client.rate_limit_usage()}


@app.post('/live-account/mmp-config')
def live_update_mmp_config(payload: LiveMMPConfigRequest, user=Depends(get_current_user), db=Depends(get_db)):
    """PUT /v2/users/update_mmp — Market Maker Protection config."""
    client, definition, _ = _live_client(db, user, payload.broker, payload.connection_id)
    response = client.update_mmp_config(
        asset=payload.asset,
        window_interval=payload.window_interval,
        freeze_interval=payload.freeze_interval,
        trade_limit=payload.trade_limit,
        delta_limit=payload.delta_limit,
        vega_limit=payload.vega_limit,
        mmp=payload.mmp,
    )
    return {"status": "rejected" if (isinstance(response, dict) and response.get('error')) else "ok",
            "response": response, "rate_limits": client.rate_limit_usage()}


@app.post('/live-account/mmp-reset')
def live_reset_mmp(payload: LiveMMPResetRequest, user=Depends(get_current_user), db=Depends(get_db)):
    """PUT /v2/users/reset_mmp — Reset MMP trigger."""
    client, definition, _ = _live_client(db, user, payload.broker, payload.connection_id)
    response = client.reset_mmp(asset=payload.asset, mmp=payload.mmp)
    return {"status": "rejected" if (isinstance(response, dict) and response.get('error')) else "ok",
            "response": response, "rate_limits": client.rate_limit_usage()}


@app.get('/live-account/rate-limits')
def live_rate_limits(broker: str = 'Delta', connection_id: Optional[int] = None,
                     user=Depends(get_current_user), db=Depends(get_db)):
    """Local throttling state, plus the exchange's own quota (Delta)."""
    from .core.rate_limit import all_snapshots
    client, definition, _ = _live_client(db, user, broker, connection_id)
    usage = client.rate_limit_usage()
    quota = None
    try:
        quota = client.fetch_rate_limit_quota()
    except Exception:
        quota = None
    return {"broker": definition.code, "local": usage, "exchange_quota": quota,
            "all": all_snapshots()}


@app.get('/live-account/orders')
def live_local_orders(broker: str = None, limit: int = 200, user=Depends(get_current_user)):
    """Orders sent through PHANTOM, from the local audit table."""
    from .services.broker_account import local_order_history
    return local_order_history(user.id, broker, limit=limit)


@app.get('/live-account/fills')
def live_local_fills(broker: str = None, limit: int = 200, user=Depends(get_current_user)):
    """Executions recorded locally (kept after the exchange history window)."""
    from .services.broker_account import local_fills
    return local_fills(user.id, broker, limit=limit)


@app.get('/live-account/fills/export')
def live_fills_export(broker: str = 'Delta', connection_id: Optional[int] = None,
                      symbol: str = 'BTCUSDT', limit: int = 200,
                      format: str = 'kudos', start_time: Optional[int] = None,
                      end_time: Optional[int] = None,
                      user=Depends(get_current_user), db=Depends(get_db)):
    """Download live fills as a Kudos/backtest-style CSV.

    ``format=fills`` is one row per execution; ``format=kudos`` FIFO-pairs
    them into round-trip trades matching the paper/backtest trade log.
    Falls back to the local audit table if the venue history window is empty.
    """
    from fastapi.responses import Response
    from .services.broker_account import (
        fills_to_csv, fills_to_kudos_trades_csv, local_fills, normalize_fill,
    )
    client, definition, _ = _live_client(db, user, broker, connection_id)
    instrument = client.get_instrument(symbol) or {}
    contract_value = float(instrument.get('contract_value') or 1.0) or 1.0
    raw = []
    try:
        raw = client.get_fills(symbol, start_time=start_time, end_time=end_time,
                               limit=max(1, min(int(limit), 1000)))
    except Exception:
        raw = []
    fills = []
    if isinstance(raw, list):
        fills = [normalize_fill(row, definition.code, contract_value) for row in raw
                 if isinstance(row, dict) and not row.get('error')]
    if not fills:
        fills = local_fills(user.id, definition.code, limit=limit)
    kind = str(format or 'kudos').lower()
    if kind in ('kudos', 'trades', 'trade'):
        csv_text = fills_to_kudos_trades_csv(fills, broker=definition.code)
        filename = f"kudos_{definition.code.lower()}_trades.csv"
    else:
        csv_text = fills_to_csv(fills, broker=definition.code)
        filename = f"kudos_{definition.code.lower()}_fills.csv"
    return Response(
        content=csv_text.encode('utf-8'),
        media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )
