from backend.app.core.engine import BacktestEngine
from backend.app.core.strategy import PhantomV2Config
from datetime import datetime

def run_verification():
    config = PhantomV2Config()
    engine = BacktestEngine(config)
    
    start_date = datetime(2023, 4, 1)
    end_date = datetime(2026, 4, 1)
    
    print(f"Running backtest from {start_date} to {end_date}...")
    try:
        results = engine.run(
            symbol="BTCUSDT", 
            initial_capital_inr=20000, 
            conversion_rate=85.0, 
            start_date=start_date, 
            end_date=end_date
        )
        
        print("\n--- Backtest Results ---")
        print(f"Total Trades: {results['total_trades']}")
        print(f"Win Rate: {results['win_rate']:.2f}%")
        print(f"Profit Factor: {results['profit_factor']:.2f}")
        print(f"Max Drawdown: {results['max_drawdown']:.2f}%")
        print(f"ROI: {results['roi']:.2f}%")
        print(f"Sharpe Ratio: {results['sharpe_ratio']:.2f}")
        print(f"Final Equity (INR): {results['final_equity_inr']:.2f}")
        print(f"Exit Distribution: {results['exit_dist']}")
        print(f"Rejected by Validator: {results['rejected_count']}")
        
    except Exception as e:
        print(f"Error running backtest: {e}")

if __name__ == "__main__":
    run_verification()
