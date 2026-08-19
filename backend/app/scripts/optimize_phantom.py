"""PHANTOM v3 optimizer.

Stage 1: sweep ENTRY parameters (train split only).
Stage 2: greedy coordinate-descent over RISK/EXIT parameters for the top entries.
Stage 3: evaluate the tuned leaders on the unseen TEST split + full range.

Outputs: backend/logs/optimize_results.csv (leaderboard) and prints the champion config.
"""
import sys, os, json, itertools, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
import numpy as np
import pandas as pd

from backend.app.database.models import init_db
from backend.app.core.engine import BacktestEngine
from backend.app.core.strategy import PhantomV2Config


def score(r):
    """Composite objective (Calmar-style): log-ROI per unit of drawdown, plus
    Sharpe / profit-factor / trade-count terms. Heavily favours low DD so the
    search optimises 'smooth' performance instead of raw leveraged compounding."""
    roi_frac = max(r['roi'], -99.0) / 100.0   # fraction, always > -1
    dd = max(r['max_drawdown'], 1.0)
    calmar_like = np.log1p(roi_frac) / dd
    return (100.0 * calmar_like
            + 10.0 * r['sharpe_ratio']
            + 20.0 * (min(r['profit_factor'], 4.0) - 1.0)
            + 0.05 * min(r['total_trades'], 900))


def run_cfg(df_1h, df_4h, **kw):
    cfg = PhantomV2Config(**kw)
    eng = BacktestEngine(cfg)
    return eng.run(symbol='BTCUSDT', df_1h=df_1h.copy(), df_4h=df_4h.copy())


def brief(r):
    return (f"trades={r['total_trades']:>4} WR={r['win_rate']:5.1f}% PF={min(r['profit_factor'],99):4.2f} "
            f"Sharpe={r['sharpe_ratio']:5.2f} ROI={r['roi']:8.1f}% MaxDD={r['max_drawdown']:5.1f}%")


def main():
    t0 = time.time()
    init_db()
    boot = BacktestEngine(PhantomV2Config())
    df_1h_full = boot._get_data_from_db('BTCUSDT', '1h')
    df_4h_full = boot._get_data_from_db('BTCUSDT', '4h')
    n = len(df_1h_full)
    split = int(n * 0.65)
    cut = df_1h_full.index[split]
    df_1h_train, df_1h_test = df_1h_full.iloc[:split], df_1h_full.iloc[split:]
    df_4h_train, df_4h_test = (df_4h_full[df_4h_full.index <= cut],
                               df_4h_full[df_4h_full.index > cut])
    print(f"data: {n} 1h bars | train={len(df_1h_train)} ({df_1h_train.index[0]} -> {df_1h_train.index[-1]})"
          f" | test={len(df_1h_test)} ({df_1h_test.index[0]} -> {df_1h_test.index[-1]})", flush=True)

    base_risk = dict(cooldown_bars=2, allow_overlap=False, allow_reverse=False)

    # ---------------- Stage 1: entry sweep on TRAIN ----------------
    print("\n=== STAGE 1: entry parameter sweep (train) ===", flush=True)
    stage1_rows = []
    for adx_min, hist_min, (os_, ob_), regime, mom in itertools.product(
            [10.0, 14.0, 18.0, 22.0], [0.0, 5.0, 10.0, 20.0],
            [(30, 70), (33, 67), (36, 64), (40, 60)], [0.2, 0.35, 0.5], [False, True]):
        kw = dict(adx_min=adx_min, macd_hist_min=hist_min, rsi_oversold=os_, rsi_overbought=ob_,
                  atr_regime_ratio=regime, enable_momentum_entry=mom, **base_risk)
        try:
            r = run_cfg(df_1h_train, df_4h_train, **kw)
            stage1_rows.append({**kw, 'score': score(r), 'trades': r['total_trades'],
                                'wr': r['win_rate'], 'pf': r['profit_factor'],
                                'sharpe': r['sharpe_ratio'], 'roi': r['roi'], 'dd': r['max_drawdown']})
        except Exception as e:
            print('ERR', kw, e, flush=True)
    s1 = pd.DataFrame(stage1_rows).sort_values('score', ascending=False)
    print(s1.head(12).to_string(index=False), flush=True)
    top_entries = s1.head(10).to_dict('records')
    print(f"[stage1 done in {time.time()-t0:.0f}s]", flush=True)

    # ---------------- Stage 2: greedy risk tuning (train) ----------------
    print("\n=== STAGE 2: greedy risk/exit tuning (train) ===", flush=True)
    risk_grid = [
        ('stop_loss_atr', [1.2, 1.6, 2.0, 2.5]),
        ('trail_activation_atr', [0.8, 1.2, 1.5]),
        ('trail_distance_atr', [0.3, 0.5, 0.7]),
        ('timeout_bars', [48, 72, 96]),
        ('cooldown_bars', [0, 2, 4]),
        ('dd_soft_pct', [8.0, 12.0, 18.0, 25.0, 100.0]),
        ('dd_pair', [(100.0, 100.0), (30.0, 20.0), (20.0, 12.0), (15.0, 8.0)]),
        ('breakeven_atr', [0.0, 0.75, 1.0]),
        ('take_profit_atr', [6.0, 10.0, 14.0]),
        ('allow_reverse', [False, True]),
    ]
    tuned = []
    for rec in top_entries:
        cur = {k: rec[k] for k in ['adx_min', 'macd_hist_min', 'rsi_oversold', 'rsi_overbought',
                                   'atr_regime_ratio', 'enable_momentum_entry']}
        cur.update(base_risk)
        best_r = run_cfg(df_1h_train, df_4h_train, **cur)
        best_s = score(best_r)
        for _pass in range(2):
            for param, values in risk_grid:
                for v in values:
                    trial = dict(cur)
                    if param == 'dd_pair':
                        trial['dd_halt_pct'], trial['dd_resume_pct'] = v
                    else:
                        trial[param] = v
                    try:
                        r = run_cfg(df_1h_train, df_4h_train, **trial)
                    except Exception:
                        continue
                    s = score(r)
                    if s > best_s:
                        best_s = s
                        cur = trial
        tuned.append({'kw': cur, 'train_score': best_s})
        print(f"tuned entry#{len(tuned)}: score={best_s:.1f} {brief(run_cfg(df_1h_train, df_4h_train, **cur))}", flush=True)
        print('   ', {k: v for k, v in cur.items()}, flush=True)
    print(f"[stage2 done in {time.time()-t0:.0f}s]", flush=True)

    # ---------------- Stage 3: out-of-sample test + full ----------------
    print("\n=== STAGE 3: out-of-sample validation ===", flush=True)
    rows = []
    for t in tuned:
        kw = t['kw']
        r_test = run_cfg(df_1h_test, df_4h_test, **kw)
        r_full = run_cfg(df_1h_full, df_4h_full, **kw)
        rows.append({'kw': kw, 'test_score': score(r_test), 'test': r_test, 'full': r_full})
        print(f"TEST: score={score(r_test):7.1f} {brief(r_test)}")
        print(f"FULL: score={score(r_full):7.1f} {brief(r_full)}")
        print(' cfg:', json.dumps(kw), flush=True)

    os.makedirs('backend/logs', exist_ok=True)
    lb = pd.DataFrame([{
        **{f'cfg_{k}': v for k, v in row['kw'].items()},
        'test_score': row['test_score'],
        'test_trades': row['test']['total_trades'], 'test_wr': row['test']['win_rate'],
        'test_pf': row['test']['profit_factor'], 'test_sharpe': row['test']['sharpe_ratio'],
        'test_roi': row['test']['roi'], 'test_dd': row['test']['max_drawdown'],
        'full_score': score(row['full']),
        'full_trades': row['full']['total_trades'], 'full_wr': row['full']['win_rate'],
        'full_pf': row['full']['profit_factor'], 'full_sharpe': row['full']['sharpe_ratio'],
        'full_roi': row['full']['roi'], 'full_dd': row['full']['max_drawdown'],
        'full_consec_losses': row['full']['max_consec_losses'],
    } for row in rows]).sort_values('full_score', ascending=False)
    lb.to_csv('backend/logs/optimize_results.csv', index=False)

    # Champion: must beat baseline trades & drawdown on FULL range, then best score
    elig = lb[(lb['full_trades'] >= 263) & (lb['full_dd'] < 30.34)]
    champion = elig.iloc[0] if len(elig) else lb.iloc[0]
    champ_kw = {c[4:]: (v.item() if hasattr(v, 'item') else v) for c in lb.columns
                if c.startswith('cfg_') for v in [champion[c]]}
    with open('backend/logs/champion_config.json', 'w') as f:
        json.dump(champ_kw, f, indent=2)
    print("\n=== CHAMPION ===")
    print(json.dumps(champ_kw, indent=2))
    print(f"[total {time.time()-t0:.0f}s]")


if __name__ == '__main__':
    main()
