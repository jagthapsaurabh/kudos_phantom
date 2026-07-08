import pandas as pd
import json
from backend.database.models import init_db, SessionLocal, BacktestRun, Trade
from backend.core.engine import BacktestEngine, StrategyConfig
from backend.core.indicators import compute_indicators

def run_optimized_backtest():
    # 1. Setup DB
    init_db()
    db = SessionLocal()
    
    print("Loading data...")
    df_1h = pd.read_csv("backend/data/btc_1h.csv", index_col=0, parse_dates=True)
    df_4h = pd.read_csv("backend/data/btc_4h.csv", index_col=0, parse_dates=True)
    
    df_1h = compute_indicators(df_1h)
    
    # 2. Optimized Config (Based on the suggestions to increase trade frequency)
    config = StrategyConfig(
        adx_min=15.0,            # Lowered from 22
        macd_hist_min=10.0,      # Lowered from 25
        rsi_oversold=35,         # Relaxed from 30
        rsi_overbought=65,       # Relaxed from 70
        atr_regime_ratio=0.3     # Lowered from 0.5 to be less strict on vol
    )
    
    config_dict = {
        "trend_ema_period": config.trend_ema_period,
        "rsi_oversold": config.rsi_oversold,
        "rsi_overbought": config.rsi_overbought,
        "adx_min": config.adx_min,
        "macd_hist_min": config.macd_hist_min,
        "atr_regime_ratio": config.atr_regime_ratio,
    }
    
    print(f"Running Optimized Backtest (ADX:{config.adx_min}, MACD:{config.macd_hist_min})...")
    engine = BacktestEngine(config)
    results = engine.run(df_1h, df_4h)
    
    # 3. Store Run in DB
    run_record = BacktestRun(
        config_json=json.dumps(config_dict),
        final_capital=results['final_capital'],
        total_trades=results['total_trades'],
        win_rate=results['win_rate']
    )
    db.add(run_record)
    db.commit()
    
    # 4. Store Trades in DB
    print(f"Storing {len(results['trades'])} trades in database...")
    for t in results['trades']:
        trade_pnl = (t.exit_price - t.entry_price) * t.direction
        trade_record = Trade(
            run_id=run_record.id,
            entry_time=t.entry_time,
            exit_time=t.exit_time,
            entry_price=t.entry_price,
            exit_price=t.exit_price,
            direction=t.direction,
            pnl=trade_pnl,
            exit_reason=t.exit_reason
        )
        db.add(trade_record)
    
    db.commit()
    db.close()
    print(f"Success! Optimized Backtest saved.")
    print(f"Final Capital: ${results['final_capital']:.2f}, Total Trades: {results['total_trades']}, Win Rate: {results['win_rate']:.2f}%")

if __name__ == "__main__":
    run_optimized_backtest()
