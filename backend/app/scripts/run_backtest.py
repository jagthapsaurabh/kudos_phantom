import os
import pandas as pd
from .database.models import init_db, SessionLocal, Klines
from .core.engine import BacktestEngine
from .core.strategy import PhantomV2Config

def run_backtest_from_db():
    init_db()
    config = PhantomV2Config()
    engine = BacktestEngine(config)
    
    print("Running backtest from DB...")
    results = engine.run()
    print(f"Final Equity: ₹{results['final_equity_inr']:.2f}")
    print(f"Win Rate: {results['win_rate']:.2f}%")

if __name__ == "__main__":
    run_backtest_from_db()
