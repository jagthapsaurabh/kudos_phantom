import pandas as pd
import numpy as np
from backend.core.engine import BacktestEngine
from backend.core.strategy import PhantomV2Config
from backend.core.indicators import compute_indicators

def run_final_backtest():
    print("🚀 Starting Production PHANTOM v2.5 Backtest...")
    
    # 1. Load Data
    try:
        df_1h = pd.read_csv("backend/data/btc_1h.csv", index_col=0, parse_dates=True)
        df_4h = pd.read_csv("backend/data/btc_4h.csv", index_col=0, parse_dates=True)
    except FileNotFoundError:
        print("❌ Data not found. Please run seeder.py first.")
        return

    # 2. Pre-compute Indicators for the engine
    # The engine expects indicators like 'atr14' to be present in df_1h
    print("Computing indicators...")
    ind_1h = compute_indicators(df_1h)
    for col, values in ind_1h.items():
        df_1h[col] = values

    # 3. Initialize Engine with Production Config
    # Defaults in PhantomV2Config already match v2.5 specification
    config = PhantomV2Config()
    engine = BacktestEngine(config)
    
    # 4. Run Simulation
    print("Simulating trades...")
    initial_capital = 10000.0
    results = engine.run(df_1h, df_4h, initial_capital=initial_capital)
    
    # 5. Analyze Results
    trades = results['trades']
    total_trades = results['total_trades']
    win_rate = results['win_rate']
    final_cap = results['final_capital']
    
    # Breakdown by exit reason
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
        
    print("\n" + "="*40)
    print("   PHANTOM v2.5 BACKTEST SUMMARY")
    print("="*40)
    print(f"Initial Capital:  ${initial_capital:,.2f}")
    print(f"Final Capital:    ${final_cap:,.2f}")
    print(f"Total PnL:        ${(final_cap - initial_capital):,.2f}")
    print(f"Total Trades:     {total_trades}")
    print(f"Win Rate:         {win_rate:.2f}%")
    print("-" * 40)
    print("Exit Breakdown:")
    for reason, count in reasons.items():
        pct = (count / total_trades * 100) if total_trades > 0 else 0
        print(f" - {reason}: {count} ({pct:.1f}%)")
    print("="*40)

if __name__ == "__main__":
    run_final_backtest()
