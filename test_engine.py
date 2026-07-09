from backend.app.core.engine import BacktestEngine
from backend.app.core.strategy import FastTestStrategyService, PhantomV2Config
from backend.app.database.models import SessionLocal, Klines
from datetime import datetime
import pandas as pd

def run_fast_test():
    config = PhantomV2Config()
    engine = BacktestEngine(config)
    # Override strategy service with FastTest
    engine.strategy_service = FastTestStrategyService(config)
    
    start_date = datetime(2023, 4, 1)
    end_date = datetime(2023, 6, 1) # Small window
    
    print(f"Running fast test from {start_date} to {end_date}...")
    try:
        results = engine.run(
            symbol="BTCUSDT", 
            initial_capital_inr=20000, 
            conversion_rate=85.0, 
            start_date=start_date, 
            end_date=end_date
        )
        print(f"Total Trades: {results['total_trades']}")
        print(f"ROI: {results['roi']:.2f}%")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_fast_test()
