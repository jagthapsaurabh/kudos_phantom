"""Per-side ATR regime operator (client request: >, <, >=, <= for LONG / SHORT).

Offline check — no network, no database. Uses the bundled BTC candles so the
signal masks are compared against numpy references computed the same way the
strategy does it, and a full BacktestEngine run proves the engine consumes the
per-side operator end to end.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')))

import numpy as np
import pandas as pd
import pydantic

from app.core.indicators import compute_indicators, sma
from app.core.strategy import (
    ATR_REGIME_OPS, DEFAULT_ATR_REGIME_OP, PhantomV2Config, StrategyService,
    normalize_atr_regime_op,
)
from app.core.engine import BacktestEngine

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f"  [{extra}]" if extra and not cond else ""), flush=True)


DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
df_1h = pd.read_csv(os.path.join(DATA, 'btc_1h.csv'), index_col=0, parse_dates=True).iloc[:3000]
df_4h = pd.read_csv(os.path.join(DATA, 'btc_4h.csv'), index_col=0, parse_dates=True).iloc[:800]

ind = compute_indicators(df_1h)
atr = ind['atr14']
atr_sma = sma(atr, 50)

BASE = dict(adx_min=10, macd_hist_min=5, rsi_oversold=40, rsi_overbought=60,
            atr_regime_ratio=0.5, enable_momentum_entry=True)


def masks(cfg):
    """(long mask, short mask) of the ATR regime filter as the engine sees it."""
    _, meta = StrategyService(cfg).generate_signals_with_metadata(df_1h.copy(), df_4h.copy())
    return meta['cond_atr_regime_ok_long'], meta['cond_atr_regime_ok_short'], meta


print("\n== default behaviour is unchanged (toggle OFF) ==", flush=True)
default_cfg = PhantomV2Config(**BASE)
ok_l, ok_s, meta_default = masks(default_cfg)
legacy = atr >= (0.5 * atr_sma)
check("toggle OFF -> ATR >= ratio x SMA50 (legacy)", np.array_equal(ok_l, legacy))
check("toggle OFF -> long and short masks identical", np.array_equal(ok_l, ok_s))
check("toggle OFF -> default operator is >=", default_cfg.atr_regime_op_for(1) == DEFAULT_ATR_REGIME_OP
      and default_cfg.atr_regime_op_for(-1) == DEFAULT_ATR_REGIME_OP)
check("toggle OFF -> rule text mentions the floor",
      '≥' in default_cfg.atr_regime_rule_for(1), default_cfg.atr_regime_rule_for(1))

print("\n== toggle ON but no operator chosen -> still '>=' ==", flush=True)
same_cfg = PhantomV2Config(**BASE, entry_conditions={
    'use_direction_atr_floor': True,
    'long': {'atr_regime_ratio': 0.5},
    'short': {'atr_regime_ratio': 0.5},
})
l2, s2, _ = masks(same_cfg)
check("no operator -> identical to default masks", np.array_equal(l2, legacy) and np.array_equal(s2, legacy))

print("\n== each operator compares ATR the way the client asked ==", flush=True)
expected = {
    '>=': atr >= (0.7 * atr_sma),
    '<=': atr <= (0.7 * atr_sma),
    '>': atr > (0.7 * atr_sma),
    '<': atr < (0.7 * atr_sma),
}
for op in ATR_REGIME_OPS:
    cfg = PhantomV2Config(**BASE, entry_conditions={
        'use_direction_atr_floor': True,
        'long': {'atr_regime_ratio': 0.7, 'atr_regime_op': op},
        'short': {'atr_regime_ratio': 0.7, 'atr_regime_op': op},
    })
    lo, sh, _ = masks(cfg)
    check(f"operator '{op}' applied to LONG", np.array_equal(lo, expected[op]),
          f"{int(lo.sum())} vs {int(expected[op].sum())}")
    check(f"operator '{op}' applied to SHORT", np.array_equal(sh, expected[op]))
    check(f"operator '{op}' reported by atr_regime_op_for",
          cfg.atr_regime_op_for(1) == op and cfg.atr_regime_op_for(-1) == op)

print("\n== LONG and SHORT can differ in one config ==", flush=True)
split_cfg = PhantomV2Config(**BASE, entry_conditions={
    'use_direction_atr_floor': True,
    'long': {'atr_regime_ratio': 0.5, 'atr_regime_op': '>='},
    'short': {'atr_regime_ratio': 1.2, 'atr_regime_op': '<'},
})
lo, sh, split_meta = masks(split_cfg)
check("LONG keeps the >= 0.5 floor", np.array_equal(lo, atr >= (0.5 * atr_sma)))
check("SHORT uses < 1.2 x SMA50", np.array_equal(sh, atr < (1.2 * atr_sma)))
check("the two sides actually differ", not np.array_equal(lo, sh),
      f"long={int(lo.sum())} short={int(sh.sum())}")
check("split rule text is per side",
      '≥' in split_cfg.atr_regime_rule_for(1) and '<' in split_cfg.atr_regime_rule_for(-1)
      and '≥' not in split_cfg.atr_regime_rule_for(-1),
      f"{split_cfg.atr_regime_rule_for(1)} | {split_cfg.atr_regime_rule_for(-1)}")
check("meta exposes both rules",
      split_meta.get('atr_regime_rule_long') == split_cfg.atr_regime_rule_for(1)
      and split_meta.get('atr_regime_rule_short') == split_cfg.atr_regime_rule_for(-1),
      str(split_meta.get('atr_regime_rule_long')) + ' | ' + str(split_meta.get('atr_regime_rule_short')))

print("\n== operator aliases and validation ==", flush=True)
check("'≥' normalises to '>='", normalize_atr_regime_op('≥') == '>=')
check("'≤' normalises to '<='", normalize_atr_regime_op('≤') == '<=')
check("'=> ' normalises to '>='", normalize_atr_regime_op(' => ') == '>=')
check("None / blank normalise to the default", normalize_atr_regime_op(None) == DEFAULT_ATR_REGIME_OP
      and normalize_atr_regime_op('') == DEFAULT_ATR_REGIME_OP)
try:
    normalize_atr_regime_op('!=')
    check("unknown operator rejected", False)
except ValueError:
    check("unknown operator rejected", True)
try:
    PhantomV2Config(**BASE, entry_conditions={
        'use_direction_atr_floor': True,
        'long': {'atr_regime_ratio': 0.5, 'atr_regime_op': 'between'}})
    check("bad operator in a config raises", False)
except pydantic.ValidationError:
    check("bad operator in a config raises", True)

print("\n== max-ATR cap still combines with the chosen operator ==", flush=True)
cap_cfg = PhantomV2Config(**BASE, entry_conditions={
    'use_direction_atr_floor': True,
    'short': {'atr_regime_ratio': 0.0, 'atr_regime_op': '>', 'atr_regime_max': 1.5},
})
_, sh_cap, _ = masks(cap_cfg)
check("cap ANDs with the operator", np.array_equal(sh_cap, (atr > 0) & (atr <= 1.5 * atr_sma)))

print("\n== BacktestEngine honours the per-side operator ==", flush=True)
engine_default = BacktestEngine(config=PhantomV2Config(**BASE))
res_default = engine_default.run(df_1h=df_1h.copy(), df_4h=df_4h.copy(), initial_capital_inr=20000)
engine_split = BacktestEngine(config=split_cfg)
res_split = engine_split.run(df_1h=df_1h.copy(), df_4h=df_4h.copy(), initial_capital_inr=20000)
check("default run produced trades", res_default['total_trades'] > 0, str(res_default['total_trades']))
check("split-operator run produced trades", res_split['total_trades'] > 0, str(res_split['total_trades']))
check("short-side '<' operator changed the trade set",
      res_default['total_trades'] != res_split['total_trades']
      or [t['entry_time'] for t in res_default['trades']] != [t['entry_time'] for t in res_split['trades']],
      f"{res_default['total_trades']} vs {res_split['total_trades']}")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed", flush=True)
if FAIL:
    print("FAILED:", FAIL, flush=True)
    sys.exit(1)
