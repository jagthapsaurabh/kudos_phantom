from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, status, Body
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
from .database.models import init_db, SessionLocal, User, CustomStrategy, BacktestRun, Trade, Klines
import bcrypt
from passlib.context import CryptContext
import asyncio
from datetime import datetime, timezone

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
        df_1h = engine._get_data_from_db(payload.symbol, payload.interval, payload.start_date, payload.end_date)
        df_4h = engine._get_data_from_db(payload.symbol, "4h", payload.start_date, payload.end_date)
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
                    user=Depends(get_current_user), db=Depends(get_db)):
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
        from .core.dynamic_strategy import DynamicStrategyService
        cfg = PhantomV2Config()
        engine = BacktestEngine(cfg)
        strategy_service = DynamicStrategyService(strat.rules)
        wants_metadata = False
        label = strat.name

    df_1h = engine._get_data_from_db(symbol, "1h", start_date, end_date)
    df_4h = engine._get_data_from_db(symbol, "4h", start_date, end_date)
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

        if req.strategy_id == "PhantomV2":
            engine = BacktestEngine(config=req.params)
        else:
            strat = db.query(CustomStrategy).filter(CustomStrategy.id == int(req.strategy_id), CustomStrategy.user_id == user_id).first()
            if not strat: return
            from .core.dynamic_strategy import DynamicStrategyService
            class DynamicBacktestEngine:
                def __init__(self, rules):
                    self.config = PhantomV2Config()
                    self.strategy_service = DynamicStrategyService(rules)
                    from .core.strategy import ValidatorService
                    self.validator_service = ValidatorService()
                    from .services.order_manager import OrderManager
                    self.oms = OrderManager(self.config)
                def run(self, symbol="BTCUSDT", initial_capital_inr=20000, start_date=None, end_date=None):
                    original_engine = BacktestEngine(self.config)
                    original_engine.strategy_service = self.strategy_service
                    # NOTE: pass by keyword - the engine signature is
                    # run(symbol, initial_capital_inr, conversion_rate, start_date, end_date, ...)
                    return original_engine.run(symbol=symbol, initial_capital_inr=initial_capital_inr, start_date=start_date, end_date=end_date)
            engine = DynamicBacktestEngine(strat.rules)

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
            config_json=json.dumps(req.params.dict()),
            initial_capital=capital,
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
             "roi": r.roi, "initial_capital": r.initial_capital or 20000, "timestamp": r.timestamp} for r in runs]

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

# --- PAPER TRADING ---
class TradeStartRequest(BaseModel):
    strategy_id: str
    # Optional starting capital. If omitted, the user's (admin-set default)
    # initial capital is used.
    initial_capital: Optional[float] = None
    margin_pct: Optional[float] = None

@app.post("/paper-trade/start")
def start_paper_trade(
    payload: TradeStartRequest,
    user=Depends(get_current_user), 
    db=Depends(get_db), 
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    if not (user.can_paper if user.can_paper is not None else 1):
        raise HTTPException(status_code=403, detail="Paper trading is disabled for this account. Contact admin.")
    strategy_id = payload.strategy_id
    # Overridable capital: explicit request value, else the user's default.
    capital = float(payload.initial_capital) if payload.initial_capital else float(user.initial_capital or 20000.0)
    margin_pct = float(payload.margin_pct) if payload.margin_pct else float(user.margin_deployment_pct or 25.0)

    instance_id = str(uuid.uuid4())[:8]
    instance_key = f"paper_{user.username}_{strategy_id}_{instance_id}"

    if strategy_id == "FastTest":
        # Fixed: Import within the function to avoid UnboundLocalError if not available at module level
        from .core.strategy import FastTestStrategyService, PhantomV2Config
        service = PaperTradeService(strategy_id, PhantomV2Config(), initial_capital=capital, margin_pct=margin_pct)
        service.strategy = FastTestStrategyService(service.config)
    elif strategy_id != "PhantomV2":
        try:
            strat_id_int = int(strategy_id)
            strat = db.query(CustomStrategy).filter(CustomStrategy.id == strat_id_int, CustomStrategy.user_id == user.id).first()
            if not strat: raise HTTPException(status_code=404, detail="Custom strategy not found")
            service = PaperTradeService(strategy_id, strat.rules, initial_capital=capital, margin_pct=margin_pct, is_custom=True)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid strategy ID format")
    else:
        # Use the tuned champion config (same settings that drive the chart overlay)
        # so paper trades match the strategy being analysed.
        service = PaperTradeService(strategy_id, _load_champion_config(), initial_capital=capital, margin_pct=margin_pct, is_custom=False)

    paper_trade_instances[instance_key] = service
    background_tasks.add_task(service.start)
    return {"status": "Paper trade started", "instance_key": instance_key}

@app.post("/paper-trade/stop")
def stop_paper_trade(instance_key: str):
    if instance_key in paper_trade_instances:
        paper_trade_instances[instance_key].stop()
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
    if not user.api_key or not user.api_secret: raise HTTPException(status_code=400, detail="Broker API keys not configured")
    strategy_id = payload.strategy_id
    capital = float(payload.initial_capital) if payload.initial_capital else float(user.initial_capital or 20000.0)
    margin_pct = float(payload.margin_pct) if payload.margin_pct else float(user.margin_deployment_pct or 25.0)

    instance_id = str(uuid.uuid4())[:8]
    instance_key = f"live_{user.username}_{strategy_id}_{instance_id}"

    if strategy_id == "FastTest":
        from .core.strategy import FastTestStrategyService, PhantomV2Config
        service = LiveTradeService(strategy_id, PhantomV2Config(), user.api_key, user.api_secret, initial_capital=capital, margin_pct=margin_pct)
        service.strategy = FastTestStrategyService(service.config)
    elif strategy_id != "PhantomV2":
        try:
            strat_id_int = int(strategy_id)
            strat = db.query(CustomStrategy).filter(CustomStrategy.id == strat_id_int, CustomStrategy.user_id == user.id).first()
            if not strat: raise HTTPException(status_code=404, detail="Custom strategy not found")
            service = LiveTradeService(strategy_id, strat.rules, user.api_key, user.api_secret, initial_capital=capital, margin_pct=margin_pct, is_custom=True)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid strategy ID format")
    else:
        service = LiveTradeService(strategy_id, PhantomV2Config(), user.api_key, user.api_secret, initial_capital=capital, margin_pct=margin_pct, is_custom=False)

    live_trade_instances[instance_key] = service
    background_tasks.add_task(service.start)
    return {"status": "Live trade started", "instance_key": instance_key}

@app.post("/live-trade/stop")
def stop_live_trade(instance_key: str):
    if instance_key in live_trade_instances:
        live_trade_instances[instance_key].stop()
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
                "is_running": service.is_running, "active_trades": active_trades
            })
    return status_list

# --- DATA ENDPOINTS ---
@app.get("/klines")
def get_klines(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 500, db=Depends(get_db)):
    try:
        # First, try to fetch from the database for speed and history
        data = db.query(Klines).filter(
            Klines.symbol == symbol, 
            Klines.interval == interval
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

        # Fallback to API if DB is empty
        import requests
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
        res = requests.get(url, timeout=10).json()
        formatted = []
        for k in res:
            formatted.append({
                "time": int(k[0] / 1000),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        return formatted
    except Exception as e:
        # No local data and the remote API is unreachable — return an empty
        # series so the UI shows an empty chart instead of a hard error.
        print(f"Klines fetch error for {symbol}/{interval}: {e}")
        return []

@app.get("/symbols")
def list_symbols(user=Depends(get_current_user), db=Depends(get_db)):
    """Distinct symbols available in the local market-data store."""
    try:
        rows = db.query(Klines.symbol).distinct().all()
        return [r[0] for r in rows]
    except Exception:
        return ["BTCUSDT"]


class BrokerSettingsUpdate(BaseModel):
    api_key: str
    api_secret: str
    initial_capital: float = 20000.0
    margin_pct: float = 25.0
    broker_name: str = "Binance"

@app.post("/broker-settings")
def update_broker_settings(settings: BrokerSettingsUpdate, user=Depends(get_current_user), db=Depends(get_db)):
    user.api_key = settings.api_key
    user.api_secret = settings.api_secret
    user.initial_capital = settings.initial_capital
    user.margin_deployment_pct = settings.margin_pct
    user.broker_name = settings.broker_name
    db.commit()
    return {"status": "Settings updated"}

@app.get("/broker-settings")
def get_broker_settings(user=Depends(get_current_user)):
    return { 
        "api_key": user.api_key, 
        "api_secret": user.api_secret, 
        "broker_name": user.broker_name,
        "initial_capital": user.initial_capital,
        "margin_deployment_pct": user.margin_deployment_pct
    }

# --- DASHBOARD ---
@app.get("/dashboard/stats")
def get_dashboard_stats(user=Depends(get_current_user), db=Depends(get_db)):
    runs = db.query(BacktestRun).filter(BacktestRun.user_id == user.id).all()
    if not runs: return {"best_roi": 0, "total_runs": 0, "avg_win_rate": 0}
    best_roi = max(r.roi for r in runs)
    avg_win_rate = sum(r.win_rate for r in runs) / len(runs)
    return { "best_roi": best_roi, "total_runs": len(runs), "avg_win_rate": avg_win_rate }
