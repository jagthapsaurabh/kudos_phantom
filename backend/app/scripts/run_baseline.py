"""Baseline backtest runner - runs the current Phantom v2.5 strategy and prints metrics."""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from backend.app.database.models import init_db
from backend.app.core.engine import BacktestEngine
from backend.app.core.strategy import PhantomV2Config


def main():
    init_db()
    config = PhantomV2Config()
    engine = BacktestEngine(config)
    print("=" * 60)
    print("BASELINE Phantom v2.5 backtest (full DB range)")
    print("=" * 60)
    r = engine.run(symbol="BTCUSDT", initial_capital_inr=20000, conversion_rate=85.0)
    print(f"Final Equity (INR) : {r['final_equity_inr']:,.2f}")
    print(f"ROI                : {r['roi']:.2f}%")
    print(f"Total Trades       : {r['total_trades']}")
    print(f"Win Rate           : {r['win_rate']:.2f}%")
    print(f"Profit Factor      : {r['profit_factor']:.2f}")
    print(f"Sharpe Ratio       : {r['sharpe_ratio']:.2f}")
    print(f"Max Drawdown       : {r['max_drawdown']:.2f}%")
    print(f"Exit Distribution  : {r['exit_dist']}")
    print(f"Rejected Signals   : {r['rejected_reasons']}")


if __name__ == "__main__":
    main()
