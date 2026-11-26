"""Offline regression tests for optional compulsory entry conditions.

The champion config stays byte-for-byte unchanged: no condition is compulsory
unless the client asks for it. ``Kudos Optimize Compulsory`` is a named preset
that asks for condition 3 (MACD hist) on both setups, including the momentum
zero-cross entries that previously skipped that filter.

Run from the repo root:  python backend/test_compulsory_conditions.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.strategy import (  # noqa: E402
    COMPULSORY_CONDITIONS, KUDOS_OPTIMIZE_COMPIL_ID, KUDOS_OPTIMIZE_COMPIL_NAME,
    PHANTOM_PRESETS, PhantomV2Config, StrategyService, apply_phantom_variant,
    normalize_compulsory_conditions, parse_phantom_variant, phantom_preset_name,
)
from app.core.trade_conditions import condition_snapshot, entry_conditions_text  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
PASS, FAIL = [], []


def check(name, ok, extra=''):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not ok else ''))


def load_frames():
    def read(fname):
        df = pd.read_csv(os.path.join(DATA_DIR, fname))
        ts_col = 'timestamp' if 'timestamp' in df.columns else df.columns[0]
        df[ts_col] = pd.to_datetime(df[ts_col])
        df = df.set_index(ts_col).sort_index()
        return df[['open', 'high', 'low', 'close', 'volume']].astype(float)
    df_1h = read('btc_1h.csv').iloc[-9000:]
    df_4h = read('btc_4h.csv')
    df_4h = df_4h[df_4h.index >= df_1h.index[0] - pd.Timedelta(days=30)]
    return df_1h, df_4h


def signals(cfg, df_1h, df_4h):
    sig, meta = StrategyService(cfg).generate_signals_with_metadata(df_1h.copy(), df_4h.copy())
    return np.asarray(sig), meta


print('\n== config defaults and validation ==')
plain = PhantomV2Config(enable_momentum_entry=True)
check('champion has no compulsory conditions', plain.compulsory_conditions == [])
check('all seven documented conditions are addressable', len(COMPULSORY_CONDITIONS) == 7)
check('condition 3 normalises to macd_hist',
      normalize_compulsory_conditions(['3']) == ['macd_hist']
      and normalize_compulsory_conditions('MACD histogram, candle colour') == ['macd_hist', 'candle_color'])
try:
    PhantomV2Config(compulsory_conditions=['not_a_condition'])
    check('unknown compulsory condition is rejected', False)
except Exception:
    check('unknown compulsory condition is rejected', True)
# An older saved payload has no key at all; loading it must not change anything.
old_payload = plain.model_dump()
old_payload.pop('compulsory_conditions')
old_cfg = PhantomV2Config(**old_payload)
check('pre-feature payload loads with an empty list', old_cfg.compulsory_conditions == [])

print('\n== default signals are unchanged ==')
df_1h, df_4h = load_frames()
base_sig, base_meta = signals(plain, df_1h, df_4h)
old_sig, _ = signals(old_cfg, df_1h, df_4h)
check('empty compulsory list produces identical signals', np.array_equal(base_sig, old_sig))
check('default metadata has no compulsory histogram gate', not base_meta['compulsory_macd_hist'])

print('\n== Kudos Optimize Compulsory preset ==')
variant = parse_phantom_variant(KUDOS_OPTIMIZE_COMPIL_ID)
check('preset id parses', variant is not None and variant['setup_mode'] == 'both'
      and variant['trade_direction'] == 'both' and variant['compulsory_conditions'] == ['macd_hist'])
check('preset name is exactly the requested name',
      phantom_preset_name(KUDOS_OPTIMIZE_COMPIL_ID) == KUDOS_OPTIMIZE_COMPIL_NAME == 'Kudos Optimize Compulsory')
check('preset is listed in the Backtest dropdown payload',
      any(p['id'] == KUDOS_OPTIMIZE_COMPIL_ID and p['name'] == KUDOS_OPTIMIZE_COMPIL_NAME
          and p['compulsory_conditions'] == ['macd_hist'] for p in PHANTOM_PRESETS))
preset_cfg = apply_phantom_variant(plain, KUDOS_OPTIMIZE_COMPIL_ID)
check('preset forces MACD hist compulsory', preset_cfg.compulsory_conditions == ['macd_hist']
      and preset_cfg.is_compulsory('macd_hist') and not preset_cfg.is_compulsory('adx'))
check('plain PhantomV2 stays untouched by the preset helper',
      apply_phantom_variant(plain, 'PhantomV2').compulsory_conditions == [])

# Isolate Setup B: the momentum-only run has exactly the same entries as the
# default run's momentum setup, minus the new gate.
momentum = PhantomV2Config(enable_momentum_entry=True, setup_mode='momentum', macd_hist_min=1000.0)
strict = momentum.model_copy(update={'compulsory_conditions': ['macd_hist']})
m_sig, m_meta = signals(momentum, df_1h, df_4h)
s_sig, s_meta = signals(strict, df_1h, df_4h)
m_count = int((m_sig != 0).sum())
s_count = int((s_sig != 0).sum())
check('compulsory MACD hist removes momentum entries with weak histogram',
      0 < s_count < m_count, f'plain={m_count} strict={s_count}')
check('every surviving momentum entry passes the histogram gate',
      all(bool(s_meta['cond_macd_hist_ok_long' if s_sig[i] == 1 else 'cond_macd_hist_ok_short'][i])
          for i in np.where(s_sig != 0)[0]))

first = int(np.where(s_sig != 0)[0][0])
side = 1 if s_sig[first] == 1 else -1
snap = condition_snapshot(s_meta, first, side)
check('trade log snapshot reports the histogram condition as PASS on Setup B',
      snap['cond_macd_hist_ok'] is True and snap['setup'] == 'MOMENTUM', str(snap['cond_macd_hist_ok']))
text = entry_conditions_text(strict, s_meta, first, side) or ''
check('trade log text shows condition 3 applied on Setup B',
      '3. MACD hist:' in text and 'N/A' not in text.split('\n')[3], text.split('\n')[3])
m_first = int(np.where(m_sig != 0)[0][0])
m_text = entry_conditions_text(momentum, m_meta, m_first, 1 if m_sig[m_first] == 1 else -1) or ''
check('default trade log text still marks condition 3 N/A on Setup B',
      'not applied' in m_text and 'N/A' in m_text)

print(f'\ncompulsory_conditions: {len(PASS)} passed, {len(FAIL)} failed')
if FAIL:
    sys.exit(1)
