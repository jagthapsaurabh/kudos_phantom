import pandas as pd
import numpy as np
from backend.core.engine import BacktestEngine
from backend.core.strategy import PhantomV2Config
from backend.core.indicators import compute_indicators

def run_client_aligned_backtest():
    print("🚀 Running Client-Aligned PHANTOM v2.5 Backtest...")
    
    df_1h = pd.read_csv("backend/data/btc_1h.csv", index_col=0, parse_dates=True)
    df_4h = pd.read_csv("backend/data/btc_4h.csv", index_col=0, parse_dates=True)
    
    ind_1h = compute_indicators(df_1h)
    for col, values in ind_1h.items():
        df_1h[col] = values

    config = PhantomV2Config()
    engine = BacktestEngine(config)
    
    # Client Specs: 20,000 INR, 1 USD = 85 INR
    initial_capital_inr = 20000.0
    conversion_rate = 85.0
    
    results = engine.run(df_1h, df_4h, initial_capital_inr=initial_capital_inr, conversion_rate=conversion_rate)
    
    trades = results['trades']
    total_trades = results['total_trades']
    win_rate = results['win_rate']
    final_equity = results['final_equity_inr']
    
    reasons = {}
    for t in trades:
        reasons[t['exit_reason']] = reasons.get(t['exit_reason'], 0) + 1
        
    print("\n" + "="*45)
    print("   CLIENT ALIGNED BACKTEST SUMMARY (INR)")
    print("="*45)
    print(f"Initial Capital:  ₹{initial_capital_inr:,.2f}")
    print(f"Final Equity:     ₹{final_equity:,.2f}")
    print(f"Net Profit:       ₹{(final_equity - initial_capital_inr):,.2f}")
    print(f"ROI:              {((final_equity - initial_capital_inr)/initial_capital_inr * 100):.2f}%")
    print(f"Total Trades:     {total_trades}")
    print(f"Win Rate:         {win_rate:.2f}%")
    print("-" * 45)
    print("Exit Distribution:")
    for reason, count in reasons.items():
        pct = (count / total_trades * 100) if total_trades > 0 else 0
        print(f" - {reason}: {count} ({pct:.1f}%)")
    print("="*45)

if __name__ == "__main__":
    run_client_aligned_backtest()
