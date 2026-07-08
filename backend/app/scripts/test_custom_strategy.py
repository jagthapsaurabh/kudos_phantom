import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Add current directory to sys.path for module resolution
sys.path.append(os.path.join(os.getcwd(), 'backend'))
sys.path.append(os.path.join(os.getcwd(), 'backend/app'))

from app.database.models import SessionLocal, User, CustomStrategy, BacktestRun, Trade, Klines, Base
from app.core.engine import BacktestEngine
from app.core.strategy import PhantomV2Config
from app.core.dynamic_strategy import DynamicStrategyService
from app.services.order_manager import OrderManager
from sqlalchemy import create_engine

def setup_mock_data():
    print("🧪 Setting up MOCK test data...")
    from app.database.models import Base
    db = SessionLocal()
    
    # Create tables if they don't exist
    Base.metadata.create_all(db.bind)
    
    # 1. Create a test user
    user = db.query(User).filter(User.username == "tester").first()
    if not user:
        user = User(username="tester", password_hash="hash", initial_capital=20000.0)
        db.add(user)
        db.commit()
    
    # 2. Inject MOCK Klines (Ensure we have 1h and 4h data)
    symbol = "BTCUSDT"
    intervals = ["1h", "4h"]
    
    for interval in intervals:
        db.query(Klines).filter(Klines.symbol == symbol, Klines.interval == interval).delete()
        start_time = datetime(2023, 1, 1)
        for i in range(200):
            timestamp = start_time + timedelta(hours=i if interval == "1h" else i*4)
            price = 30000 + (i * 10) + np.random.randint(-100, 100)
            k = Klines(
                symbol=symbol,
                interval=interval,
                event_time=timestamp,
                open=price,
                high=price + 50,
                low=price - 50,
                close=price + 10,
                volume=100.0
            )
            db.add(k)
        db.commit()
    
    # 3. Create a Custom Strategy
    strat_name = "AlwaysLongTest"
    strat = db.query(CustomStrategy).filter(CustomStrategy.name == strat_name).first()
    if not strat:
        rules = [
            {
                'type': 'condition',
                'left': {'name': 'close', 'offset': 0},
                'op': 'gt',
                'right': {'type': 'number', 'value': 0},
                'timeframe': '1h',
                'enabled': True
            }
        ]
        strat = CustomStrategy(user_id=user.id, name=strat_name, rules=rules)
        db.add(strat)
        db.commit()
    
    # Return a dict instead of objects to avoid DetachedInstanceError
    user_data = {"id": user.id, "username": user.username}
    strat_data = {"id": strat.id, "name": strat.name, "rules": strat.rules}
    
    db.close()
    return user_data, strat_data

def test_custom_backtest():
    user_data, strat_data = setup_mock_data()
    print(f"🚀 Testing Custom Strategy: {strat_data['name']} for User: {user_data['username']}")
    
    from app.core.strategy import ValidatorService
    
    class DynamicBacktestEngine:
        def __init__(self, rules):
            self.config = PhantomV2Config()
            self.strategy_service = DynamicStrategyService(rules)
            self.validator_service = ValidatorService()
            self.oms = OrderManager(self.config)
        
        def run(self, symbol="BTCUSDT", start_date=None, end_date=None):
            from app.core.engine import BacktestEngine
            engine = BacktestEngine(self.config)
            engine.strategy_service = self.strategy_service
            return engine.run(
                symbol=symbol, 
                start_date=start_date, 
                end_date=end_date,
                initial_capital_inr=20000,
                conversion_rate=85.0
            )

    try:
        engine = DynamicBacktestEngine(strat_data['rules'])
        # Use a window that matches our mock data
        start_date = datetime(2023, 1, 1)
        end_date = datetime(2023, 12, 31)
        
        print("Running backtest simulation on mock data...")
        results = engine.run(
            symbol="BTCUSDT", 
            start_date=start_date, 
            end_date=end_date
        )
        
        print("\n--- Backtest Results ---")
        print(f"Total Trades: {results['total_trades']}")
        print(f"ROI: {results['roi']:.2f}%")
        print(f"Final Equity: ₹{results['final_equity_inr']:.2f}")
        
        if results['total_trades'] > 0:
            print("✅ SUCCESS: Custom strategy generated trades on mock data.")
        else:
            print("❌ FAILURE: Custom strategy generated 0 trades. Check logic.")
            
    except Exception as e:
        print(f"❌ CRITICAL ERROR during backtest: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_custom_backtest()
