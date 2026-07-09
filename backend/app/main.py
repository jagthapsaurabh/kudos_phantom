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
from .database.models import init_db, SessionLocal, User, CustomStrategy, BacktestRun, Trade
import bcrypt
from passlib.context import CryptContext
import asyncio
from datetime import datetime

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

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def get_current_user(token: str = Depends(oauth2_scheme), db=Depends(get_db)):
    user = db.query(User).filter(User.username == token).first()
    if not user: raise HTTPException(status_code=401, detail="Invalid credentials")
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
    
    try:
        # Use bcrypt directly to avoid passlib version incompatibility issues
        import bcrypt
        password_bytes = form_data.password.encode('utf-8')
        hashed_bytes = user.password_hash.encode('utf-8')
        if bcrypt.checkpw(password_bytes, hashed_bytes):
            return {"access_token": user.username, "token_type": "bearer"}
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

# --- BACKTESTING ---
def execute_backtest_task(run_id: int, req: BacktestRequest, user_id: int):
    db = SessionLocal()
    try:
        start_date_str = req.start_date or "2020-07-04"
        end_date_str = req.end_date or "2026-07-04"
        
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
                def run(self, symbol="BTCUSDT", start_date=None, end_date=None):
                    original_engine = BacktestEngine(self.config)
                    original_engine.strategy_service = self.strategy_service
                    return original_engine.run(symbol, start_date, end_date)
            engine = DynamicBacktestEngine(strat.rules)

        results = engine.run(symbol="BTCUSDT", start_date=start_date_str, end_date=end_date_str)
        
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
            
            for t in results['trades']:
                db.add(Trade(run_id=run.id, **t))
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

        # Create a placeholder run record
        run = BacktestRun(
            user_id=user.id,
            name=req.strategy_name,
            start_date=start_date_dt,
            end_date=end_date_dt,
            config_json=json.dumps(req.params.dict()),
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
    return [{"id": r.id, "name": r.name, "start_date": r.start_date, "end_date": r.end_date, "roi": r.roi, "timestamp": r.timestamp} for r in runs]

@app.get("/backtest/results/{run_id}")
def get_backtest_results(run_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    run = db.query(BacktestRun).filter(BacktestRun.id == run_id, BacktestRun.user_id == user.id).first()
    if not run: raise HTTPException(status_code=404, detail="Run not found")
    trades = db.query(Trade).filter(Trade.run_id == run_id).all()
    trade_list = [{ "entry_time": t.entry_time, "exit_time": t.exit_time, "direction": t.direction, "entry_price": t.entry_price, "exit_price": t.exit_price, "lots": t.lots, "margin": t.margin, "notional": t.notional, "net_pnl": t.net_pnl, "fees": t.fees, "exit_reason": t.exit_reason, "equity_after": t.equity_after, "drawdown": t.drawdown, "hold_bars": t.hold_bars } for t in trades]
    return {
        "run_details": {
            "name": run.name, "start_date": run.start_date, "end_date": run.end_date,
            "final_equity": run.final_equity, "total_trades": run.total_trades,
            "win_rate": run.win_rate, "profit_factor": run.profit_factor,
            "sharpe_ratio": run.sharpe_ratio, "max_drawdown": run.max_drawdown, 
            "roi": run.roi, "equity_curve": run.equity_curve
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

# --- PAPER TRADING ---
@app.post("/paper-trade/start")
def start_paper_trade(
    strategy_id: str = Body(..., embed=True), 
    user=Depends(get_current_user), 
    db=Depends(get_db), 
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    instance_id = str(uuid.uuid4())[:8]
    instance_key = f"paper_{user.username}_{strategy_id}_{instance_id}"
    
    if strategy_id == "FastTest":
        # Fixed: Import within the function to avoid UnboundLocalError if not available at module level
        from .core.strategy import FastTestStrategyService, PhantomV2Config
        service = PaperTradeService(strategy_id, PhantomV2Config(), initial_capital=user.initial_capital, margin_pct=user.margin_deployment_pct)
        service.strategy = FastTestStrategyService(service.config)
    elif strategy_id != "PhantomV2":
        try:
            strat_id_int = int(strategy_id)
            strat = db.query(CustomStrategy).filter(CustomStrategy.id == strat_id_int, CustomStrategy.user_id == user.id).first()
            if not strat: raise HTTPException(status_code=404, detail="Custom strategy not found")
            service = PaperTradeService(strategy_id, strat.rules, initial_capital=user.initial_capital, margin_pct=user.margin_deployment_pct, is_custom=True)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid strategy ID format")
    else:
        # Fixed: Explicitly use the imported PhantomV2Config from the top of the file
        # The issue was a scope conflict where the name PhantomV2Config was being shadowed or accessed incorrectly
        from .core.strategy import PhantomV2Config as PConfig
        service = PaperTradeService(strategy_id, PConfig(), initial_capital=user.initial_capital, margin_pct=user.margin_deployment_pct, is_custom=False)

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
                active_trades.append({
                    "symbol": symbol, "direction": trade.direction, "entry": trade.entry_price,
                    "current": trade.peak_price, "pnl": (trade.peak_price - trade.entry_price) * trade.direction * trade.lots * 85.0,
                    "margin": trade.margin_inr
                })
            status_list.append({
                "instance_key": key, "strategy_id": service.strategy_id,
                "equity_inr": service.equity_inr, "is_running": service.is_running, "active_trades": active_trades
            })
    return status_list

# --- LIVE TRADING ---
@app.post("/live-trade/start")
def start_live_trade(
    strategy_id: str = Body(..., embed=True), 
    user=Depends(get_current_user), 
    db=Depends(get_db), 
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    if not user.api_key or not user.api_secret: raise HTTPException(status_code=400, detail="Broker API keys not configured")
    instance_id = str(uuid.uuid4())[:8]
    instance_key = f"live_{user.username}_{strategy_id}_{instance_id}"

    if strategy_id == "FastTest":
        from .core.strategy import FastTestStrategyService, PhantomV2Config
        service = LiveTradeService(strategy_id, PhantomV2Config(), user.api_key, user.api_secret, initial_capital=user.initial_capital, margin_pct=user.margin_deployment_pct)
        service.strategy = FastTestStrategyService(service.config)
    elif strategy_id != "PhantomV2":
        try:
            strat_id_int = int(strategy_id)
            strat = db.query(CustomStrategy).filter(CustomStrategy.id == strat_id_int, CustomStrategy.user_id == user.id).first()
            if not strat: raise HTTPException(status_code=404, detail="Custom strategy not found")
            service = LiveTradeService(strategy_id, strat.rules, user.api_key, user.api_secret, initial_capital=user.initial_capital, margin_pct=user.margin_deployment_pct, is_custom=True)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid strategy ID format")
    else:
        service = LiveTradeService(strategy_id, PhantomV2Config(), user.api_key, user.api_secret, initial_capital=user.initial_capital, margin_pct=user.margin_deployment_pct, is_custom=False)

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
                    "time": k.event_time.timestamp(),
                    "open": k.open,
                    "high": k.high,
                    "low": k.low,
                    "close": k.close,
                })
            return formatted

        # Fallback to API if DB is empty
        import requests
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
        res = requests.get(url).json()
        formatted = []
        for k in res:
            formatted.append({
                "time": k[0] / 1000,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
            })
        return formatted
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Klines fetch error: {str(e)}")

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
