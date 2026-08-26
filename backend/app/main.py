from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, status, Body, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
import pandas as pd
import json
from typing import Optional, List, Dict, Union
import os
from dotenv import load_dotenv
from .core.engine import BacktestEngine
from .core.strategy import PhantomV2Config, StrategyService
from .services.paper_trader import PaperTradeService
from .services.live_trader import LiveTradeService
from .services.data_sync import DataSyncService
from .database.models import (
    init_db, SessionLocal, User, CustomStrategy, BacktestRun, Trade, Klines,
    BrokerDefinition, BrokerConnection, FeeSetting,
)
import bcrypt
from passlib.context import CryptContext
import asyncio
from datetime import datetime, timezone
from sqlalchemy import func

# Load environment variables
load_dotenv()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

app = FastAPI(title="PHANTOM v2.5 Trading Tool")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    data_source: str = 'Binance'
    fee_mode: str = 'backtest'

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


def normalize_source(source: Optional[str]) -> str:
    value = (source or 'Binance').strip()
    return {'binance': 'Binance', 'delta': 'Delta', 'delta exchange': 'Delta'}.get(value.lower(), value)


def _fee_dict(row, broker_code=None, mode=None):
    return {
        'id': getattr(row, 'id', None), 'broker_code': getattr(row, 'broker_code', broker_code),
        'mode': getattr(row, 'mode', mode),
        'taker_fee_bps': float(getattr(row, 'taker_fee_bps', 0.0)),
        'maker_fee_bps': float(getattr(row, 'maker_fee_bps', 0.0)),
        'enabled': bool(getattr(row, 'enabled', 1)), 'updated_at': getattr(row, 'updated_at', None),
    }


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
    'adx_min', 'trend_ema_period',
)


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
    while True:
        DataSyncService.sync_market_data()
        # Sleep for 24 hours (86400 seconds)
        await asyncio.sleep(86400)

@app.on_event("startup")
def startup():
    init_db()
    # Start the background sync task
    asyncio.create_task(daily_sync_task())

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
    source: str = "Binance"
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
                # Stop the client's running sessions
                for key in list(paper_trade_instances.keys()):
                    if f"_{user.username}_" in key:
                        paper_trade_instances[key].is_running = False
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
    paper = [{"instance_key": k, "strategy_id": s.strategy_id, "equity_inr": s.equity_inr,
              "is_running": s.is_running, "open_trades": len(s.oms.active_trades)}
             for k, s in paper_trade_instances.items() if f"_{user.username}_" in k]
    live = [{"instance_key": k, "strategy_id": s.strategy_id, "equity_inr": s.equity_inr,
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


def _broker_dict(row):
    return {'id': row.id, 'code': row.code, 'name': row.name, 'kind': row.kind,
            'market_data_url': row.market_data_url, 'trading_api_url': row.trading_api_url,
            'enabled': bool(row.enabled), 'is_builtin': bool(row.is_builtin), 'notes': row.notes}


@app.get('/broker-definitions')
def broker_definitions(user=Depends(get_current_user), db=Depends(get_db)):
    return [_broker_dict(b) for b in db.query(BrokerDefinition).filter(BrokerDefinition.enabled == 1).order_by(BrokerDefinition.name).all()]


@app.get('/admin/brokers')
def admin_brokers(admin=Depends(require_admin), db=Depends(get_db)):
    return [_broker_dict(b) for b in db.query(BrokerDefinition).order_by(BrokerDefinition.name).all()]


@app.post('/admin/brokers')
def create_broker(payload: BrokerDefinitionPayload, admin=Depends(require_admin), db=Depends(get_db)):
    code = normalize_source(payload.code)
    if db.query(BrokerDefinition).filter(BrokerDefinition.code == code).first():
        raise HTTPException(status_code=400, detail='Broker code already exists')
    row = BrokerDefinition(code=code, name=payload.name, kind=payload.kind.lower(),
                           market_data_url=payload.market_data_url, trading_api_url=payload.trading_api_url,
                           enabled=int(payload.enabled), is_builtin=0, notes=payload.notes)
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
    row.name = payload.name; row.kind = payload.kind.lower(); row.market_data_url = payload.market_data_url
    row.trading_api_url = payload.trading_api_url; row.enabled = int(payload.enabled); row.notes = payload.notes
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
def fee_settings(broker_code: str = 'Binance', mode: str = 'backtest', user=Depends(get_current_user), db=Depends(get_db)):
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


def _connection_dict(row):
    return {'id': row.id, 'broker_code': row.broker_code, 'label': row.label or row.broker_code,
            'api_key': _mask(row.api_key), 'has_secret': bool(row.api_secret),
            'is_testnet': bool(row.is_testnet), 'is_active': bool(row.is_active), 'created_at': row.created_at}


@app.get('/broker-connections')
def list_broker_connections(user=Depends(get_current_user), db=Depends(get_db)):
    rows = db.query(BrokerConnection).filter(BrokerConnection.user_id == user.id).order_by(BrokerConnection.created_at).all()
    # A legacy account still gets a selectable connection without a migration step.
    result = [_connection_dict(r) for r in rows]
    if not result and user.api_key:
        result.append({'id': None, 'broker_code': user.broker_name or 'Binance', 'label': 'Legacy account',
                       'api_key': _mask(user.api_key), 'has_secret': bool(user.api_secret), 'is_testnet': False,
                       'is_active': True, 'legacy': True})
    return result


@app.post('/broker-connections')
def create_broker_connection(payload: BrokerConnectionPayload, user=Depends(get_current_user), db=Depends(get_db)):
    code = normalize_source(payload.broker_code)
    if not db.query(BrokerDefinition).filter_by(code=code, enabled=1).first():
        raise HTTPException(status_code=400, detail='Unknown or disabled broker integration')
    if not payload.api_key or not payload.api_secret:
        raise HTTPException(status_code=400, detail='API key and secret are required')
    row = BrokerConnection(user_id=user.id, broker_code=code, label=payload.label, api_key=payload.api_key,
                           api_secret=payload.api_secret, passphrase=payload.passphrase,
                           is_testnet=int(payload.is_testnet), is_active=int(payload.is_active))
    db.add(row); db.commit(); db.refresh(row)
    return _connection_dict(row)


@app.put('/broker-connections/{connection_id}')
def update_broker_connection(connection_id: int, payload: BrokerConnectionPayload, user=Depends(get_current_user), db=Depends(get_db)):
    row = db.query(BrokerConnection).filter(BrokerConnection.id == connection_id, BrokerConnection.user_id == user.id).first()
    if not row: raise HTTPException(status_code=404, detail='Broker connection not found')
    row.broker_code = normalize_source(payload.broker_code); row.label = payload.label
    # Empty secrets mean "keep the existing secret" in the edit form.
    if payload.api_key: row.api_key = payload.api_key
    if payload.api_secret: row.api_secret = payload.api_secret
    if payload.passphrase is not None: row.passphrase = payload.passphrase
    row.is_testnet = int(payload.is_testnet); row.is_active = int(payload.is_active)
    db.commit(); db.refresh(row)
    return _connection_dict(row)


@app.delete('/broker-connections/{connection_id}')
def delete_broker_connection(connection_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    row = db.query(BrokerConnection).filter(BrokerConnection.id == connection_id, BrokerConnection.user_id == user.id).first()
    if not row: raise HTTPException(status_code=404, detail='Broker connection not found')
    db.delete(row); db.commit(); return {'status': 'Broker connection removed'}


class MarketSeedPayload(BaseModel):
    source: str = 'Binance'
    symbol: str = 'BTCUSDT'
    intervals: List[str] = ['1m', '5m', '15m', '1h', '4h', '1d']
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    limit: int = 1000
    fetch_all: bool = False


@app.get('/admin/market-data/status')
def market_data_status(admin=Depends(require_admin), db=Depends(get_db)):
    rows = db.query(
        Klines.source, Klines.symbol, Klines.interval,
        func.count(Klines.id).label('count'), func.count(Klines.volume).label('volume_rows'),
        func.min(Klines.event_time).label('first'), func.max(Klines.event_time).label('last'),
    ).group_by(Klines.source, Klines.symbol, Klines.interval).order_by(
        Klines.source, Klines.symbol, Klines.interval).all()
    return [{'source': source or 'Binance', 'symbol': symbol, 'interval': interval,
             'count': int(count), 'volume_rows': int(volume_rows), 'first': first, 'last': last}
            for source, symbol, interval, count, volume_rows, first, last in rows]


@app.post('/admin/market-data/seed')
def seed_market_data(payload: MarketSeedPayload, admin=Depends(require_admin)):
    try:
        from .services.data_sync import DataSyncService
        intervals = [i for i in payload.intervals if i in DataSyncService.TIMEFRAMES]
        if not intervals: raise HTTPException(status_code=400, detail='Select at least one valid interval')
        return {'status': 'Seed completed', 'summary': DataSyncService.seed_market_data(
            normalize_source(payload.source), payload.symbol.upper(), intervals,
            payload.start_date, payload.end_date, max(1, min(payload.limit, 2000)))}
    except HTTPException: raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post('/admin/market-data/seed-csv')
async def seed_market_data_csv(source: str = 'Binance', symbol: str = 'BTCUSDT', interval: str = '1h',
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


# --- PHANTOM v3: config + signal overlay for charts ---
_LOGS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'logs'))

def _champion_config_path() -> Optional[str]:
    for name in ('champion_lowdd_config.json', 'champion_config.json'):
        path = os.path.join(_LOGS_DIR, name)
        if os.path.exists(path):
            return path
    return None

def _load_champion_config() -> PhantomV2Config:
    """Best-known tuned config (sizing/low-dd profile preferred), else defaults."""
    path = _champion_config_path()
    if path:
        try:
            with open(path) as f:
                kw = json.load(f)
            return PhantomV2Config(**{k: v for k, v in kw.items() if k in PhantomV2Config.model_fields})
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
                    source: str = "Binance", user=Depends(get_current_user), db=Depends(get_db)):
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
        item = {
            "time": int(_utc_ts(df_1h.index[i])),
            "direction": int(s),
            "price": float(closes[i]),
            "setup": None,
            "rsi14": None,
            "adx": None,
        }
        if meta is not None:
            item["setup"] = str(meta['setup'][i])
            item["rsi14"] = round(float(meta['rsi14'][i]), 2)
            item["adx"] = round(float(meta['adx'][i]), 3)
        else:
            item["setup"] = label or "CUSTOM"
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
                    def run(self, symbol="BTCUSDT", initial_capital_inr=20000, start_date=None, end_date=None):
                        original_engine = BacktestEngine(self.config, fee_schedule=fees, data_source=source)
                        original_engine.strategy_service = self.strategy_service
                        # NOTE: pass by keyword - the engine signature is
                        # run(symbol, initial_capital_inr, conversion_rate, start_date, end_date, ...)
                        return original_engine.run(symbol=symbol, initial_capital_inr=initial_capital_inr, start_date=start_date, end_date=end_date)
                engine = DynamicBacktestEngine(payload)

        results = engine.run(symbol="BTCUSDT", initial_capital_inr=capital, start_date=start_date_str, end_date=end_date_str)
        
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
            start_date=start_date_dt,
            end_date=end_date_dt,
            config_json=json.dumps(req.params.model_dump()),
            initial_capital=capital,
            data_source=normalize_source(req.data_source),
            fee_mode=req.fee_mode,
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
    return [{"id": r.id, "name": r.name, "start_date": r.start_date, "end_date": r.end_date,
             "roi": r.roi, "initial_capital": r.initial_capital or 20000,
             "data_source": r.data_source or 'Binance', "taker_fee_bps": r.taker_fee_bps,
             "maker_fee_bps": r.maker_fee_bps, "timestamp": r.timestamp} for r in runs]

@app.get("/backtest/results/{run_id}")
def get_backtest_results(run_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    run = db.query(BacktestRun).filter(BacktestRun.id == run_id, BacktestRun.user_id == user.id).first()
    if not run: raise HTTPException(status_code=404, detail="Run not found")
    trades = db.query(Trade).filter(Trade.run_id == run_id).all()
    trade_list = [{ "entry_time": t.entry_time, "exit_time": t.exit_time, "direction": t.direction, "entry_price": t.entry_price, "exit_price": t.exit_price, "lots": t.lots, "margin": t.margin, "notional": t.notional, "net_pnl": t.net_pnl, "fees": t.fees, "exit_reason": t.exit_reason, "equity_after": t.equity_after, "drawdown": t.drawdown, "hold_bars": t.hold_bars,
                    # PHANTOM v3: entry-condition snapshot (candle, setup, indicators)
                    "signal_candle_time": t.signal_candle_time, "setup": t.setup,
                    "candle_type": t.candle_type, "trend_4h": t.trend_4h,
                    "rsi14": t.rsi14, "macd_hist": t.macd_hist, "adx": t.adx,
                    "atr14": t.atr14, "ema50_1h": t.ema50_1h, "ema50_4h": t.ema50_4h,
                    "conditions": {
                        "adx_ok": t.cond_adx_ok, "macd_hist_ok": t.cond_macd_hist_ok,
                        "atr_regime_ok": t.cond_atr_regime_ok, "rsi_ok": t.cond_rsi_ok,
                        "macd_confirm_ok": t.cond_macd_confirm_ok,
                    },
                    "gross_pnl": t.gross_pnl, "sl": t.sl, "tp": t.tp,
                    "entry_dd_pct": t.entry_dd_pct, "margin_pct_used": t.margin_pct_used,
                    "equity_at_entry": t.equity_at_entry } for t in trades]
    return {
        "run_details": {
            "name": run.name, "start_date": run.start_date, "end_date": run.end_date,
            "initial_capital": run.initial_capital or 20000,
            "final_equity": run.final_equity, "total_trades": run.total_trades,
            "win_rate": run.win_rate, "profit_factor": run.profit_factor,
            "sharpe_ratio": run.sharpe_ratio, "max_drawdown": run.max_drawdown,
            "roi": run.roi, "equity_curve": run.equity_curve,
            "rejected_reasons": json.loads(run.rejected_reasons) if run.rejected_reasons else {},
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
    symbol: str = "BTCUSDT"
    data_source: str = 'Binance'
    fee_mode: str = 'backtest'

@app.post("/backtest/filter-preview")
def filter_preview(req: FilterPreviewRequest, user=Depends(get_current_user), db=Depends(get_db)):
    """Quick per-bucket peek at the conditions currently in the form.

    Runs a lightweight backtest with the given params (shipping the
    direction-specific overrides when the toggle is ON) and buckets the
    resulting trades by LONG/SHORT x REVERSAL/MOMENTUM with win rate,
    profit factor and average net PnL for each bucket. This lets an admin
    tune the MACD / ATR-regime thresholds and see the bucket-level
    trade-off without a separate offline script.
    """
    try:
        source = normalize_source(req.data_source)
        fees = resolve_fees(db, source, req.fee_mode, req.params)
        config = _fee_config(req.params, fees)
        engine = BacktestEngine(config=config, fee_schedule=fees, data_source=source)
        start = req.start_date or "2020-07-04"
        end = req.end_date or "2026-07-04"
        results = engine.run(symbol=req.symbol, initial_capital_inr=20000,
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
    # Provide a per-direction roll-up too (LONG / SHORT).
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
        'use_direction_conditions': req.params.entry_conditions.use_direction_conditions,
        'buckets': out,
        'by_side': {k: v for k, v in sides.items()},
        'setup_dist': results.get('setup_dist', {}),
    }

# --- PAPER TRADING ---
class TradeStartRequest(BaseModel):
    strategy_id: str
    # Optional starting capital. If omitted, the user's (admin-set default)
    # initial capital is used.
    initial_capital: Optional[float] = None
    margin_pct: Optional[float] = None
    broker_name: str = 'Binance'
    data_source: Optional[str] = None
    connection_id: Optional[int] = None
    testnet: bool = False

def resolve_broker_context(payload, user, db, require_credentials=False):
    code = normalize_source(payload.data_source or payload.broker_name)
    definition = db.query(BrokerDefinition).filter(BrokerDefinition.code == code, BrokerDefinition.enabled == 1).first()
    if not definition:
        raise HTTPException(status_code=400, detail=f"Broker/data source '{code}' is not configured or enabled")
    connection = None
    if payload.connection_id is not None:
        connection = db.query(BrokerConnection).filter(
            BrokerConnection.id == payload.connection_id, BrokerConnection.user_id == user.id,
            BrokerConnection.is_active == 1).first()
        if not connection:
            raise HTTPException(status_code=404, detail="Selected broker connection not found or disabled")
        if connection.broker_code != code:
            raise HTTPException(status_code=400, detail="Connection does not belong to the selected broker")
    else:
        connection = db.query(BrokerConnection).filter(
            BrokerConnection.user_id == user.id, BrokerConnection.broker_code == code,
            BrokerConnection.is_active == 1).order_by(BrokerConnection.created_at).first()
    api_key = connection.api_key if connection else (user.api_key if (user.broker_name or 'Binance') == code else '')
    api_secret = connection.api_secret if connection else (user.api_secret if (user.broker_name or 'Binance') == code else '')
    passphrase = connection.passphrase if connection else ''
    testnet = bool(connection.is_testnet) if connection else bool(payload.testnet)
    if require_credentials and (not api_key or not api_secret):
        raise HTTPException(status_code=400, detail=f"API keys not configured for {code}. Add a connection in Broker Settings.")
    return code, definition, api_key or '', api_secret or '', passphrase or '', testnet, connection


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
    fees = resolve_fees(db, source, 'paper')
    config = _fee_config(_load_champion_config() if payload.strategy_id == 'PhantomV2' else PhantomV2Config(), fees)
    capital = float(payload.initial_capital) if payload.initial_capital else float(user.initial_capital or 20000.0)
    margin_pct = float(payload.margin_pct) if payload.margin_pct else float(user.margin_deployment_pct or 25.0)
    strategy_id = payload.strategy_id
    instance_id = str(uuid.uuid4())[:8]
    instance_key = f"paper_{user.username}_{source}_{strategy_id}_{instance_id}"

    if strategy_id == "FastTest":
        from .core.strategy import FastTestStrategyService
        service = PaperTradeService(strategy_id, config, initial_capital=capital, margin_pct=margin_pct,
                                    market_source=source, broker_name=source, fee_schedule=fees, broker_definition=definition)
        service.strategy = FastTestStrategyService(service.config)
    elif strategy_id != "PhantomV2":
        resolved = _resolve_strategy_payload(db, strategy_id, user.id, fees)
        if not resolved:
            raise HTTPException(status_code=404, detail="Custom strategy not found")
        kind, payload, strat = resolved
        if kind == 'phantom':
            service = PaperTradeService(strategy_id, payload, initial_capital=capital, margin_pct=margin_pct,
                                        market_source=source, broker_name=source, fee_schedule=fees, is_custom=False, broker_definition=definition)
        else:
            service = PaperTradeService(strategy_id, payload, initial_capital=capital, margin_pct=margin_pct,
                                        market_source=source, broker_name=source, fee_schedule=fees, is_custom=True, broker_definition=definition)
    else:
        service = PaperTradeService(strategy_id, config, initial_capital=capital, margin_pct=margin_pct,
                                    market_source=source, broker_name=source, fee_schedule=fees, broker_definition=definition)
    paper_trade_instances[instance_key] = service
    background_tasks.add_task(service.start)
    return {"status": "Paper trade started", "instance_key": instance_key, "data_source": source,
            "taker_fee_bps": fees.taker_fee_bps, "maker_fee_bps": fees.maker_fee_bps}

@app.post("/paper-trade/stop")
async def stop_paper_trade(instance_key: str, user=Depends(get_current_user)):
    if f"_{user.username}_" not in instance_key:
        raise HTTPException(status_code=403, detail="Not your instance")
    if instance_key in paper_trade_instances:
        await paper_trade_instances[instance_key].stop()
        del paper_trade_instances[instance_key]
        return {"status": "Paper trade stopped"}
    raise HTTPException(status_code=404, detail="Instance not found")

@app.get("/paper-trade/status")
def get_paper_status(user=Depends(get_current_user)):
    status_list = []
    for key, service in paper_trade_instances.items():
        if f"_{user.username}_" in key:
            active_trades = []
            for symbol, trade in service.oms.active_trades.items():
                current = getattr(trade, 'current_price', None) or trade.peak_price
                pnl_inr = (current - trade.entry_price) * trade.direction * trade.lots * service.conversion_rate
                active_trades.append({
                    "symbol": symbol, "direction": trade.direction, "entry": trade.entry_price,
                    "current": current, "pnl": pnl_inr,
                    "entry_time": str(trade.entry_time), "bars_held": trade.bars_held,
                    "margin": trade.margin_inr
                })
            status_list.append({
                "instance_key": key, "strategy_id": service.strategy_id,
                "data_source": service.market_source, "broker_name": service.broker_name,
                "taker_fee_bps": service.config.taker_fee_bps, "maker_fee_bps": service.config.maker_fee_bps,
                "equity_inr": service.equity_inr, "initial_capital_inr": service.initial_capital_inr,
                "is_running": service.is_running, "active_trades": active_trades,
                "open_trade_count": len(service.oms.active_trades),
                "closed_trades": service.closed_trades[-50:],
                "last_price": service.last_price, "last_checked": service.last_checked,
                "conversion_rate": service.conversion_rate,
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
    fees = resolve_fees(db, source, 'live')
    config = _fee_config(_load_champion_config() if payload.strategy_id == 'PhantomV2' else PhantomV2Config(), fees)
    strategy_id = payload.strategy_id
    capital = float(payload.initial_capital) if payload.initial_capital else float(user.initial_capital or 20000.0)
    margin_pct = float(payload.margin_pct) if payload.margin_pct else float(user.margin_deployment_pct or 25.0)
    instance_id = str(uuid.uuid4())[:8]
    instance_key = f"live_{user.username}_{source}_{strategy_id}_{instance_id}"

    if strategy_id == "FastTest":
        from .core.strategy import FastTestStrategyService
        service = LiveTradeService(strategy_id, config, api_key, api_secret, initial_capital=capital,
                                   margin_pct=margin_pct, broker_name=source, passphrase=passphrase,
                                   testnet=testnet, fee_schedule=fees, definition=definition)
        service.strategy = FastTestStrategyService(service.config)
    elif strategy_id != "PhantomV2":
        resolved = _resolve_strategy_payload(db, strategy_id, user.id, fees)
        if not resolved:
            raise HTTPException(status_code=404, detail="Custom strategy not found")
        kind, payload, strat = resolved
        if kind == 'phantom':
            service = LiveTradeService(strategy_id, payload, api_key, api_secret, initial_capital=capital,
                                       margin_pct=margin_pct, is_custom=False, broker_name=source,
                                       passphrase=passphrase, testnet=testnet, fee_schedule=fees, definition=definition)
        else:
            service = LiveTradeService(strategy_id, payload, api_key, api_secret, initial_capital=capital,
                                       margin_pct=margin_pct, is_custom=True, broker_name=source,
                                       passphrase=passphrase, testnet=testnet, fee_schedule=fees, definition=definition)
    else:
        service = LiveTradeService(strategy_id, config, api_key, api_secret, initial_capital=capital,
                                   margin_pct=margin_pct, broker_name=source, passphrase=passphrase,
                                   testnet=testnet, fee_schedule=fees, definition=definition)
    live_trade_instances[instance_key] = service
    background_tasks.add_task(service.start)
    return {"status": "Live trade started", "instance_key": instance_key, "broker_name": source,
            "taker_fee_bps": fees.taker_fee_bps, "maker_fee_bps": fees.maker_fee_bps}

@app.post("/live-trade/stop")
async def stop_live_trade(instance_key: str, user=Depends(get_current_user)):
    if f"_{user.username}_" not in instance_key:
        raise HTTPException(status_code=403, detail="Not your instance")
    if instance_key in live_trade_instances:
        await live_trade_instances[instance_key].stop()
        del live_trade_instances[instance_key]
        return {"status": "Live trade stopped"}
    raise HTTPException(status_code=404, detail="Instance not found")

@app.get("/live-trade/status")
def get_live_status(user=Depends(get_current_user)):
    status_list = []
    for key, service in live_trade_instances.items():
        if f"_{user.username}_" in key:
            active_trades = []
            for symbol, trade in service.oms.active_trades.items():
                active_trades.append({
                    "symbol": symbol, "direction": trade.direction, "entry": trade.entry_price,
                    "current": trade.peak_price, "pnl": (trade.peak_price - trade.entry_price) * trade.direction * trade.lots * 85.0,
                    "margin": trade.margin_inr
                })
            status_list.append({
                "instance_key": key, "strategy_id": service.strategy_id,
                "broker_name": service.broker_name, "data_source": service.market_source,
                "taker_fee_bps": service.config.taker_fee_bps, "maker_fee_bps": service.config.maker_fee_bps,
                "last_price": service.last_price, "last_checked": service.last_checked,
                "is_running": service.is_running, "active_trades": active_trades
            })
    return status_list

# --- DATA ENDPOINTS ---
@app.get("/klines")
def get_klines(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 500, source: str = "Binance", db=Depends(get_db)):
    try:
        # First, try to fetch from the database for speed and history
        data = db.query(Klines).filter(
            Klines.symbol == symbol,
            Klines.interval == interval,
            Klines.source == normalize_source(source)
        ).order_by(Klines.event_time.desc()).limit(limit).all()

        if data:
            # Reverse to get chronological order
            data.reverse()
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

@app.get("/symbols")
def list_symbols(source: str = "Binance", user=Depends(get_current_user), db=Depends(get_db)):
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
    broker_name: str = "Binance"
    connection_id: Optional[int] = None
    passphrase: Optional[str] = None
    is_testnet: bool = False


@app.post("/broker-settings")
def update_broker_settings(settings: BrokerSettingsUpdate, user=Depends(get_current_user), db=Depends(get_db)):
    code = normalize_source(settings.broker_name)
    if settings.connection_id:
        row = db.query(BrokerConnection).filter(BrokerConnection.id == settings.connection_id,
                                                BrokerConnection.user_id == user.id).first()
        if not row: raise HTTPException(status_code=404, detail="Broker connection not found")
        if settings.api_key: row.api_key = settings.api_key
        if settings.api_secret: row.api_secret = settings.api_secret
        row.broker_code = code; row.passphrase = settings.passphrase; row.is_testnet = int(settings.is_testnet)
    elif settings.api_key and settings.api_secret:
        row = db.query(BrokerConnection).filter(BrokerConnection.user_id == user.id,
                                                BrokerConnection.broker_code == code).first()
        if row:
            row.api_key = settings.api_key; row.api_secret = settings.api_secret
            row.is_testnet = int(settings.is_testnet)
        else:
            db.add(BrokerConnection(user_id=user.id, broker_code=code, label='Primary',
                                    api_key=settings.api_key, api_secret=settings.api_secret,
                                    passphrase=settings.passphrase, is_testnet=int(settings.is_testnet), is_active=1))
        # Keep legacy columns synchronized for old workers and clients.
        user.api_key = settings.api_key; user.api_secret = settings.api_secret; user.broker_name = code
    user.initial_capital = settings.initial_capital
    user.margin_deployment_pct = settings.margin_pct
    user.broker_name = code
    db.commit()
    return {"status": "Settings updated", "broker_name": code}


@app.get("/broker-settings")
def get_broker_settings(user=Depends(get_current_user), db=Depends(get_db)):
    rows = db.query(BrokerConnection).filter(BrokerConnection.user_id == user.id).all()
    return {
        "api_key": _mask(user.api_key),
        "api_secret": _mask(user.api_secret),
        "broker_name": user.broker_name or 'Binance',
        "initial_capital": user.initial_capital,
        "margin_deployment_pct": user.margin_deployment_pct,
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
