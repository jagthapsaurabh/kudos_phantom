"""Run the tuned PHANTOM v3 config on the full dataset.

- Loads backend/logs/champion_config.json (produced by optimize_phantom.py);
  falls back to the shipped v3 default if missing.
- Writes the full trade log (every trade + entry conditions + candles) to
  backend/logs/phantom_v3_trades.csv
- Prints a baseline-vs-v3 comparison.
"""
import sys, os, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
import pandas as pd

from backend.app.database.models import init_db
from backend.app.core.engine import BacktestEngine
from backend.app.core.strategy import PhantomV2Config

# v2.5 baseline measured on this machine (full range, 20k INR start):
BASELINE = {
    'Final Equity (INR)': 53127.57, 'ROI %': 165.64, 'Total Trades': 263,
    'Win Rate %': 51.71, 'Profit Factor': 1.27, 'Sharpe': 0.74, 'Max Drawdown %': 30.34,
}

FALLBACK_V3 = dict(
    adx_min=14.0, macd_hist_min=10.0, rsi_oversold=33, rsi_overbought=67,
    atr_regime_ratio=0.35, enable_momentum_entry=True,
    stop_loss_atr=1.6, trail_activation_atr=1.2, trail_distance_atr=0.5,
    take_profit_atr=10.0, timeout_bars=72, cooldown_bars=2,
    breakeven_atr=1.0, dd_soft_pct=18.0, dd_halt_pct=30.0, dd_resume_pct=20.0,
    allow_reverse=False, allow_overlap=False,
)


def comparison_table(name, r):
    return {
        'Final Equity (INR)': round(r['final_equity_inr'], 2),
        'ROI %': round(r['roi'], 2),
        'Total Trades': r['total_trades'],
        'Win Rate %': round(r['win_rate'], 2),
        'Profit Factor': round(min(r['profit_factor'], 99), 2),
        'Sharpe': round(r['sharpe_ratio'], 2),
        'Max Drawdown %': round(r['max_drawdown'], 2),
        'Max Consec Losses': r['max_consec_losses'],
        'Exit Dist': r['exit_dist'],
        'Setup Dist': r.get('setup_dist', {}),
    }


def main():
    init_db()
    cfg_path = 'backend/logs/champion_config.json'
    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            kw = json.load(f)
        src = f"champion config ({cfg_path})"
    else:
        kw = FALLBACK_V3
        src = "fallback v3 default"
    config = PhantomV2Config(**{k: v for k, v in kw.items() if k in PhantomV2Config.model_fields})
    print(f"Config source: {src}")
    print(json.dumps({k: getattr(config, k) for k in FALLBACK_V3 if hasattr(config, k)}, indent=2, default=str))

    engine = BacktestEngine(config)
    r = engine.run(symbol='BTCUSDT', initial_capital_inr=20000, conversion_rate=85.0,
                   trade_log_path='backend/logs/phantom_v3_trades.csv')

    print("\n================ BASELINE (v2.5) vs PHANTOM v3 ================")
    v3 = comparison_table('v3', r)
    keys = [k for k in BASELINE]
    print(f"{'Metric':<22}{'v2.5 baseline':>16}{'v3':>16}")
    for k in keys:
        print(f"{k:<22}{BASELINE[k]:>16}{v3[k]:>16}")
    print(f"{'Max Consec Losses':<22}{'—':>16}{v3['Max Consec Losses']:>16}")
    print(f"Exit dist: {v3['Exit Dist']}")
    print(f"Setup dist: {v3['Setup Dist']}")
    print(f"Diagnostics: {r['diagnostics']}")
    print(f"Rejected signals: {r['rejected_reasons']}")
    print("\nTrade log written to backend/logs/phantom_v3_trades.csv "
          f"({r['total_trades']} trades)")


if __name__ == '__main__':
    main()
