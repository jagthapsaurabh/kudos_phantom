"""Offline checks for the v3.5 additions:

* MACD line / signal line entry rules (off by default, per-side optional),
* setup separation (Reversal only / Momentum only),
* direction separation (Long only / Short only),
* the built-in ``PhantomV2:<setup>[:<direction>]`` presets.

Runs on the bundled CSVs, no server or database needed:

    cd backend && python test_macd_line_and_modes.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.engine import BacktestEngine  # noqa: E402
from app.core.indicators import compute_indicators  # noqa: E402
from app.core.strategy import (  # noqa: E402
    BUILTIN_PHANTOM_ID, PHANTOM_PRESETS, MacdLineConditions, PhantomV2Config, StrategyService,
    apply_phantom_variant, parse_phantom_variant, phantom_preset_id, phantom_preset_name,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
PASSED = 0
FAILED = 0


def check(name, cond, detail=''):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS: {name}")
    else:
        FAILED += 1
        print(f"  FAIL: {name} {detail}")


def load_frames():
    def read(fname):
        df = pd.read_csv(os.path.join(DATA_DIR, fname))
        ts_col = 'timestamp' if 'timestamp' in df.columns else df.columns[0]
        df[ts_col] = pd.to_datetime(df[ts_col])
        df = df.set_index(ts_col).sort_index()
        return df[['open', 'high', 'low', 'close', 'volume']].astype(float)
    return read('btc_1h.csv'), read('btc_4h.csv')


def signals_for(cfg, df_1h, df_4h):
    svc = StrategyService(cfg)
    sig, meta = svc.generate_signals_with_metadata(df_1h, df_4h)
    return np.asarray(sig), meta


def main():
    df_1h, df_4h = load_frames()
    # Keep the runs quick — the tail of the bundled history is plenty.
    df_1h = df_1h.iloc[-9000:]
    df_4h = df_4h[df_4h.index >= df_1h.index[0] - pd.Timedelta(days=30)]

    # ------------------------------------------------------------------
    print("\n[1] Defaults are inert")
    # ``base`` mirrors the champion: both setups on, so every separation
    # below has something to remove.
    BASE = dict(enable_momentum_entry=True)
    base = PhantomV2Config(**BASE)
    plain = PhantomV2Config()
    check("setup_mode defaults to both", plain.setup_mode == 'both')
    check("trade_direction defaults to both", plain.trade_direction == 'both')
    check("macd_line_rules default OFF", not plain.uses_macd_line_rules())
    check("macd line rule text is 'off' while disabled", plain.macd_line_rule_text_for(1) == 'off')
    check("momentum_enabled follows legacy switch (on)", base.momentum_enabled() is True)
    legacy_off = PhantomV2Config(enable_momentum_entry=False)
    check("momentum_enabled follows legacy switch (off)", legacy_off.momentum_enabled() is False)
    check("plain defaults keep momentum off (legacy default)", plain.momentum_enabled() is False)
    # A saved config from before v3.5 (no new keys at all) must still load.
    old_payload = {k: v for k, v in base.model_dump().items()
                   if k not in ('setup_mode', 'trade_direction', 'macd_line_rules')}
    old_payload['entry_conditions'] = {k: v for k, v in old_payload['entry_conditions'].items()
                                       if k != 'use_direction_macd_line'}
    for side in ('long', 'short'):
        old_payload['entry_conditions'][side] = {
            k: v for k, v in old_payload['entry_conditions'][side].items()
            if not k.startswith('macd_line') and not k.startswith('macd_signal')}
    old_cfg = PhantomV2Config(**old_payload)
    check("pre-v3.5 payload loads with inert defaults",
          old_cfg.setup_mode == 'both' and old_cfg.trade_direction == 'both'
          and not old_cfg.uses_macd_line_rules())
    # Enabled block with every rule off is still a no-op.
    noop = PhantomV2Config(macd_line_rules={'enabled': True}, **BASE)
    check("enabled block with no rule is a no-op", not noop.uses_macd_line_rules())

    sig_base, meta_base = signals_for(base, df_1h, df_4h)
    sig_old, _ = signals_for(old_cfg, df_1h, df_4h)
    sig_noop, _ = signals_for(noop, df_1h, df_4h)
    check("pre-v3.5 config gives identical signals", np.array_equal(sig_base, sig_old))
    check("no-op MACD block gives identical signals", np.array_equal(sig_base, sig_noop))
    check("meta exposes macd_line / macd_signal", 'macd_line' in meta_base and 'macd_signal' in meta_base)
    check("meta reports line rules disabled", meta_base['macd_line_rules_enabled'] is False)
    check("cond_macd_line_ok is all-True while off", bool(meta_base['cond_macd_line_ok_long'].all()))
    n_long_base = int((sig_base == 1).sum())
    n_short_base = int((sig_base == -1).sum())
    check("baseline produces both long and short signals", n_long_base > 0 and n_short_base > 0,
          f"long={n_long_base} short={n_short_base}")

    # ------------------------------------------------------------------
    print("\n[2] Validation of the new fields")
    for bad in ({'setup_mode': 'sideways'}, {'trade_direction': 'up'}):
        try:
            PhantomV2Config(**bad)
            check(f"rejects {bad}", False)
        except Exception:
            check(f"rejects {bad}", True)
    try:
        MacdLineConditions(line_vs_signal='sometimes')
        check("rejects unknown MACD line rule", False)
    except Exception:
        check("rejects unknown MACD line rule", True)
    lenient = PhantomV2Config(setup_mode=' Reversal-Only ', trade_direction='LONG')
    check("normalises setup_mode spelling", lenient.setup_mode == 'reversal')
    check("normalises trade_direction spelling", lenient.trade_direction == 'long')
    check("labels are human readable",
          lenient.setup_label() == 'Reversal only' and lenient.direction_label() == 'Long only')

    # ------------------------------------------------------------------
    print("\n[3] Setup separation")
    rev = PhantomV2Config(setup_mode='reversal', **BASE)
    mom = PhantomV2Config(setup_mode='momentum', **BASE)
    sig_rev, meta_rev = signals_for(rev, df_1h, df_4h)
    sig_mom, meta_mom = signals_for(mom, df_1h, df_4h)
    setups_rev = set(np.unique(meta_rev['setup'][sig_rev != 0]))
    setups_mom = set(np.unique(meta_mom['setup'][sig_mom != 0]))
    check("reversal-only never fires MOMENTUM", 'MOMENTUM' not in setups_rev, str(setups_rev))
    check("momentum-only never fires REVERSAL", 'REVERSAL' not in setups_mom, str(setups_mom))
    check("reversal-only has signals", int((sig_rev != 0).sum()) > 0)
    check("momentum-only has signals", int((sig_mom != 0).sum()) > 0)
    # Reversal-only equals the legacy enable_momentum_entry=False path exactly.
    sig_legacy_off, _ = signals_for(legacy_off, df_1h, df_4h)
    check("reversal-only == legacy enable_momentum_entry=False", np.array_equal(sig_rev, sig_legacy_off))
    # Momentum-only signals are a subset of the baseline momentum bars.
    base_mom_bars = (sig_base != 0) & (meta_base['setup'] == 'MOMENTUM')
    check("momentum-only signals ⊆ baseline momentum bars",
          bool(np.all((sig_mom != 0) <= base_mom_bars)))
    check("meta carries setup_mode", meta_rev['setup_mode'] == 'reversal')

    # ------------------------------------------------------------------
    print("\n[4] Direction separation")
    lo = PhantomV2Config(trade_direction='long', **BASE)
    sh = PhantomV2Config(trade_direction='short', **BASE)
    sig_lo, meta_lo = signals_for(lo, df_1h, df_4h)
    sig_sh, _ = signals_for(sh, df_1h, df_4h)
    check("long-only has no short signals", int((sig_lo == -1).sum()) == 0)
    check("short-only has no long signals", int((sig_sh == 1).sum()) == 0)
    check("long-only keeps every baseline long", np.array_equal(sig_lo == 1, sig_base == 1))
    check("short-only keeps every baseline short", np.array_equal(sig_sh == -1, sig_base == -1))
    check("meta carries trade_direction", meta_lo['trade_direction'] == 'long')
    both = PhantomV2Config(setup_mode='momentum', trade_direction='short', **BASE)
    sig_both, meta_both = signals_for(both, df_1h, df_4h)
    check("momentum+short: only short momentum bars",
          int((sig_both == 1).sum()) == 0 and 'REVERSAL' not in set(np.unique(meta_both['setup'][sig_both != 0])))

    # ------------------------------------------------------------------
    print("\n[5] MACD line / signal rules")
    # Shared (non per-side) MACD lines exactly as the strategy computed them.
    line = np.asarray(meta_base['macd_line'], dtype=float)
    sigl = np.asarray(meta_base['macd_signal'], dtype=float)
    ind = compute_indicators(df_1h.sort_index())
    check("meta MACD lines equal compute_indicators()",
          np.allclose(line, np.asarray(ind['macd_line'], dtype=float), equal_nan=True)
          and np.allclose(sigl, np.asarray(ind['macd_signal'], dtype=float), equal_nan=True))

    rule_sig = PhantomV2Config(macd_line_rules={'enabled': True, 'line_vs_signal': 'above_below'}, **BASE)
    sig_r, meta_r = signals_for(rule_sig, df_1h, df_4h)
    check("meta reports line rules enabled", meta_r['macd_line_rules_enabled'] is True)
    check("rule text describes line vs signal",
          'line > signal' in meta_r['macd_line_rule_long'] and 'line < signal' in meta_r['macd_line_rule_short'],
          f"{meta_r['macd_line_rule_long']!r} / {meta_r['macd_line_rule_short']!r}")
    longs = np.where(sig_r == 1)[0]
    shorts = np.where(sig_r == -1)[0]
    check("every long has MACD line above signal", bool(np.all(line[longs] > sigl[longs])) if len(longs) else True)
    check("every short has MACD line below signal", bool(np.all(line[shorts] < sigl[shorts])) if len(shorts) else True)
    check("line-vs-signal rule only removes signals",
          bool(np.all((sig_r != 0) <= (sig_base != 0))) and int((sig_r != 0).sum()) < int((sig_base != 0).sum()))
    check("cond mask matches rule on long side",
          bool(np.array_equal(meta_r['cond_macd_line_ok_long'], line > sigl)))

    rule_zero = PhantomV2Config(macd_line_rules={'enabled': True, 'line_vs_zero': 'above_below'}, **BASE)
    sig_z, _ = signals_for(rule_zero, df_1h, df_4h)
    lz = np.where(sig_z == 1)[0]
    sz = np.where(sig_z == -1)[0]
    check("line-vs-zero: longs above 0, shorts below 0",
          (bool(np.all(line[lz] > 0)) if len(lz) else True) and (bool(np.all(line[sz] < 0)) if len(sz) else True))

    rule_cross = PhantomV2Config(macd_line_rules={'enabled': True, 'line_vs_signal': 'cross'}, **BASE)
    sig_c, meta_c = signals_for(rule_cross, df_1h, df_4h)
    lc = np.where(sig_c == 1)[0]
    sc = np.where(sig_c == -1)[0]
    ok_cross_long = all(line[i] > sigl[i] and line[i - 1] <= sigl[i - 1] for i in lc)
    ok_cross_short = all(line[i] < sigl[i] and line[i - 1] >= sigl[i - 1] for i in sc)
    check("cross rule needs a crossover on the signal candle", ok_cross_long and ok_cross_short)
    check("cross is stricter than above/below",
          int((sig_c != 0).sum()) <= int((sig_r != 0).sum()))
    check("cross rule text", 'cross' in meta_c['macd_line_rule_long'])

    thr = PhantomV2Config(macd_line_rules={'enabled': True, 'line_min': 50.0}, **BASE)
    sig_t, _ = signals_for(thr, df_1h, df_4h)
    lt = np.where(sig_t == 1)[0]
    st = np.where(sig_t == -1)[0]
    check("line_min: longs >= +50, shorts <= -50",
          (bool(np.all(line[lt] >= 50)) if len(lt) else True) and (bool(np.all(line[st] <= -50)) if len(st) else True))
    check("threshold resolves signed per side",
          thr.macd_line_min_for(1) == 50.0 and thr.macd_line_min_for(-1) == -50.0)
    sthr = PhantomV2Config(macd_line_rules={'enabled': True, 'signal_min': 20.0}, **BASE)
    sig_s, _ = signals_for(sthr, df_1h, df_4h)
    ls = np.where(sig_s == 1)[0]
    ss = np.where(sig_s == -1)[0]
    check("signal_min: longs >= +20, shorts <= -20",
          (bool(np.all(sigl[ls] >= 20)) if len(ls) else True) and (bool(np.all(sigl[ss] <= -20)) if len(ss) else True))

    # Per-side rules via the existing branch pattern.
    per_side = PhantomV2Config(
        macd_line_rules={'enabled': True, 'line_vs_signal': 'above_below'},
        entry_conditions={
            'use_direction_macd_line': True,
            'long': {'macd_line_vs_signal': 'off'},
            'short': {'macd_line_vs_zero': 'above_below', 'macd_line_min': -10.0},
        },
        **BASE,
    )
    check("per-side: long falls back to 'off' explicitly",
          per_side.macd_line_rule_for(1, 'line_vs_signal') == 'off')
    check("per-side: short inherits shared line_vs_signal",
          per_side.macd_line_rule_for(-1, 'line_vs_signal') == 'above_below')
    check("per-side: short own line_vs_zero", per_side.macd_line_rule_for(-1, 'line_vs_zero') == 'above_below')
    check("per-side: short threshold used signed as-is (like macd_hist_min)",
          per_side.macd_line_min_for(-1) == -10.0)
    check("per-side: rule text for short lists all three",
          per_side.macd_line_rule_text_for(-1) == 'MACD line < signal; MACD line < 0; MACD line <= -10',
          per_side.macd_line_rule_text_for(-1))
    check("per-side: long has no threshold", per_side.macd_line_min_for(1) is None)
    sig_ps, meta_ps = signals_for(per_side, df_1h, df_4h)
    check("per-side: longs untouched (no long rule active)", np.array_equal(sig_ps == 1, sig_base == 1))
    sps = np.where(sig_ps == -1)[0]
    check("per-side: shorts satisfy line<signal, line<0, line<=-10",
          bool(np.all((line[sps] < sigl[sps]) & (line[sps] < 0) & (line[sps] <= -10))) if len(sps) else True)
    # The per-side switch without the block enabled remains inert.
    inert = PhantomV2Config(entry_conditions={'use_direction_macd_line': True,
                                              'short': {'macd_line_vs_zero': 'above_below'}}, **BASE)
    sig_in, _ = signals_for(inert, df_1h, df_4h)
    check("per-side settings inert while block disabled", np.array_equal(sig_in, sig_base))

    # ------------------------------------------------------------------
    print("\n[6] Presets")
    check("default id parses to both/both",
          parse_phantom_variant(BUILTIN_PHANTOM_ID) == {'setup_mode': 'both', 'trade_direction': 'both'})
    check("separators : - . / all accepted",
          all(parse_phantom_variant(f'PhantomV2{s}reversal{s}long') == {'setup_mode': 'reversal', 'trade_direction': 'long'}
              for s in (':', '-', '.', '/')))
    check("unknown ids do not parse",
          parse_phantom_variant('MyStrategy') is None and parse_phantom_variant('PhantomV2:foo') is None
          and parse_phantom_variant(42) is None and parse_phantom_variant('FastTest') is None)
    check("direction-only id", parse_phantom_variant('PhantomV2:short') == {'setup_mode': 'both', 'trade_direction': 'short'})
    check("preset id builder round-trips",
          phantom_preset_id('momentum', 'long') == 'PhantomV2:momentum:long' and phantom_preset_id('both', 'both') == 'PhantomV2')
    check("8 curated presets", len(PHANTOM_PRESETS) == 8 and all(p['id'] != BUILTIN_PHANTOM_ID for p in PHANTOM_PRESETS))
    check("preset names", phantom_preset_name('PhantomV2:reversal') == 'Kudos — Reversal only'
          and phantom_preset_name('PhantomV2:momentum:short') == 'Kudos — Momentum · Short only'
          and phantom_preset_name('PhantomV2') == 'Kudos V2.5 (Default)'
          and phantom_preset_name('custom-1') is None)
    applied = apply_phantom_variant(PhantomV2Config(adx_min=17.5, **BASE), 'PhantomV2:reversal:long')
    check("apply_phantom_variant keeps other params", applied.adx_min == 17.5)
    check("apply_phantom_variant sets setup/direction",
          applied.setup_mode == 'reversal' and applied.trade_direction == 'long')
    check("apply_phantom_variant on unknown id is identity",
          apply_phantom_variant(PhantomV2Config(adx_min=17.5), 'custom-1').adx_min == 17.5)
    sig_pre, meta_pre = signals_for(applied, df_1h, df_4h)
    check("preset signals: long-only reversal",
          int((sig_pre == -1).sum()) == 0 and 'MOMENTUM' not in set(np.unique(meta_pre['setup'][sig_pre != 0])))

    # ------------------------------------------------------------------
    print("\n[7] Engine plumbing")
    eng = BacktestEngine(rule_sig)
    res = eng.run(df_1h=df_1h.copy(), df_4h=df_4h.copy(), initial_capital_inr=5_000_000, conversion_rate=83.0)
    trades = res['trades']
    check("engine run returns trades", len(trades) > 0, str(len(trades)))
    if trades:
        t = trades[0]
        check("trade carries macd_line / macd_signal", 'macd_line' in t and 'macd_signal' in t)
        check("trade carries cond_macd_line_ok = True", t.get('cond_macd_line_ok') is True)
        check("entry log has the MACD line rule line",
              '8. MACD line/signal' in (t.get('entry_conditions_detail') or ''))
    check("results carry setup_mode / trade_direction / macd_line_rules",
          res.get('setup_mode') == 'both' and res.get('trade_direction') == 'both'
          and res.get('macd_line_rules', {}).get('enabled') is True)
    eng_base = BacktestEngine(PhantomV2Config(**BASE))
    res_b = eng_base.run(df_1h=df_1h.copy(), df_4h=df_4h.copy(), initial_capital_inr=5_000_000, conversion_rate=83.0)
    if res_b['trades']:
        tb = res_b['trades'][0]
        check("default run: cond_macd_line_ok is None (N/A)", tb.get('cond_macd_line_ok') is None)
        check("default run: log has no MACD line rule line",
              '8. MACD line/signal' not in (tb.get('entry_conditions_detail') or ''))
    csv_path = os.path.join(DATA_DIR, '_tmp_trade_log_test.csv')
    try:
        eng.export_trade_log(trades, csv_path)
        cols = pd.read_csv(csv_path, nrows=1).columns.tolist()
        check("CSV export has macd_line/macd_signal/cond_macd_line_ok",
              all(c in cols for c in ('macd_line', 'macd_signal', 'cond_macd_line_ok')))
    finally:
        if os.path.exists(csv_path):
            os.remove(csv_path)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
