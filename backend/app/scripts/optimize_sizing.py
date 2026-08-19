"""PHANTOM v3 sizing optimizer - minimize drawdown via leverage/margin/throttle.

Entry + exit logic is fixed at the tuned champion; this sweep explores the
remaining drawdown levers: leverage, margin per trade, and the DD throttle
thresholds. Selection rule: lowest MaxDD subject to >= 800 trades and PF >= 1.5
(so the strategy edge and frequency are preserved).
"""
import sys, os, json, time, itertools
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
import pandas as pd

from backend.app.database.models import init_db
from backend.app.core.engine import BacktestEngine
from backend.app.core.strategy import PhantomV2Config

CHAMPION = json.load(open('backend/logs/champion_config.json'))
# Fixed entry/exit logic from the tuned champion
FIXED = {k: CHAMPION[k] for k in ['adx_min', 'macd_hist_min', 'rsi_oversold', 'rsi_overbought',
                                  'atr_regime_ratio', 'enable_momentum_entry', 'cooldown_bars',
                                  'allow_overlap', 'allow_reverse', 'stop_loss_atr',
                                  'trail_activation_atr', 'trail_distance_atr',
                                  'take_profit_atr', 'breakeven_atr']}


def main():
    t0 = time.time()
    init_db()
    boot = BacktestEngine(PhantomV2Config())
    df1 = boot._get_data_from_db('BTCUSDT', '1h')
    df4 = boot._get_data_from_db('BTCUSDT', '4h')
    print(f"data: {len(df1)} bars | fixed entries: {FIXED}", flush=True)

    rows = []
    halts = [(100.0, 100.0), (20.0, 12.0), (15.0, 8.0)]
    for lev, margin, soft, (halt, resume) in itertools.product(
            [1, 2, 3, 5, 7], [0.05, 0.10, 0.15, 0.25], [8.0, 12.0], halts):
        kw = dict(FIXED, leverage=lev, margin_pct=margin, reduced_margin_pct=round(margin / 2, 4),
                  dd_soft_pct=soft, dd_halt_pct=halt, dd_resume_pct=resume)
        try:
            r = BacktestEngine(PhantomV2Config(**kw)).run(df_1h=df1.copy(), df_4h=df4.copy())
            rows.append({**{f'p_{k}': v for k, v in kw.items()},
                         'trades': r['total_trades'], 'wr': r['win_rate'], 'pf': r['profit_factor'],
                         'sharpe': r['sharpe_ratio'], 'roi': r['roi'], 'dd': r['max_drawdown'],
                         'consecL': r['max_consec_losses'],
                         'halt_bars': r['diagnostics']['halt_bars']})
        except Exception as e:
            print('ERR', kw, e, flush=True)
    df = pd.DataFrame(rows)

    pd.set_option('display.width', 250)
    ok = df[(df['trades'] >= 800) & (df['pf'] >= 1.5)].sort_values('dd')
    print("\n=== eligible (trades>=800, PF>=1.5), sorted by MaxDD ===", flush=True)
    print(ok[['p_leverage', 'p_margin_pct', 'p_dd_soft_pct', 'p_dd_halt_pct', 'trades', 'wr',
              'pf', 'sharpe', 'roi', 'dd', 'consecL']].head(15).to_string(index=False), flush=True)

    df.sort_values('dd').to_csv('backend/logs/optimize_sizing.csv', index=False)
    if len(ok):
        best = ok.iloc[0]
        lowdd = {k[2:]: (v.item() if hasattr(v, 'item') else v)
                 for k, v in best.items() if k.startswith('p_')}
        champion_lowdd = {**FIXED, **lowdd}
        with open('backend/logs/champion_lowdd_config.json', 'w') as f:
            json.dump(champion_lowdd, f, indent=2)
        print("\n=== LOW-DD CHAMPION ===")
        print(json.dumps(champion_lowdd, indent=2))
        print(f"trades={best['trades']} WR={best['wr']:.1f}% PF={best['pf']:.2f} "
              f"Sharpe={best['sharpe']:.2f} MaxDD={best['dd']:.2f}% consecL={best['consecL']}")
    else:
        print('no eligible config found')
    print(f"[done in {time.time()-t0:.0f}s]")


if __name__ == '__main__':
    main()
