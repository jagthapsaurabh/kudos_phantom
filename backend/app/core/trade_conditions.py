"""Shared entry- and exit-condition detail for the trade log.

The Backtest page has always spelled out every entry condition (the measured
value, the threshold applied to that side and PASS/FAIL) and the exact rule
that closed a trade. The client asked for the same detail on Paper and Live
trades, so these builders live here and are shared by all three:

* ``BacktestEngine`` — passes the run's per-bar metadata frame,
* ``PaperTradeService`` / ``LiveTradeService`` — pass the strategy metadata
  for the 1h bar that produced the entry signal.

The wording is the engine's original wording, moved out of ``engine.py``
unchanged so a paper/live log reads exactly like the backtest's.
"""


def row_candle_color(is_green, is_red):
    """GREEN / RED / DOJI from a candle's two colour flags (None if unknown)."""
    if is_green is None or is_red is None:
        return None
    if bool(is_green):
        return 'GREEN'
    if bool(is_red):
        return 'RED'
    return 'DOJI'


def candle_color(meta, i):
    """GREEN / RED / DOJI for bar i, or None when metadata is unavailable."""
    if meta is None:
        return None
    try:
        if bool(meta['is_green'][i]):
            return 'GREEN'
        if bool(meta['is_red'][i]):
            return 'RED'
        return 'DOJI'
    except (IndexError, KeyError, TypeError):
        return None


def condition_snapshot(meta, i, signal_dir):
    """Full market/condition snapshot on the signal candle (bar i).

    The RSI and MACD-confirmation flags are resolved per **setup**: a
    MOMENTUM (Setup B) trade is filtered by the zero-cross, DI and RSI
    agreement rules, not by the reversal rules, so its log shows the
    conditions that actually fired.
    """
    is_long = signal_dir == 1
    setup = str(meta['setup'][i])
    # Direction-specific filters: pick the mask for the side the trade
    # actually fired on (they are identical when the toggle is OFF).
    adx_ok = bool(meta['cond_adx_ok_long'][i]) if is_long else bool(meta['cond_adx_ok_short'][i])
    regime_ok = bool(meta['cond_atr_regime_ok_long'][i]) if is_long else bool(meta['cond_atr_regime_ok_short'][i])
    trend_ok = int(meta['trend'][i]) == signal_dir
    # Each setup has its own gates. A filter that the setup does not use is
    # reported as None (N/A) rather than True/False, so the log never shows
    # a trade "failing" a condition it was never tested against.
    if setup == 'MOMENTUM':
        # Setup B gates: trend, ADX, ATR regime, DI, MACD zero-cross, RSI.
        hist_ok = None
        rsi_ok = bool(meta['cond_mom_rsi_long' if is_long else 'cond_mom_rsi_short'][i])
        macd_ok = bool(meta['cond_mom_cross_long' if is_long else 'cond_mom_cross_short'][i])
        di_ok = bool(meta['cond_di_long' if is_long else 'cond_di_short'][i])
    else:
        # Setup A gates: trend, ADX, MACD magnitude, ATR regime, RSI
        # reversal + candle colour, MACD direction confirmation.
        hist_ok = bool(meta['cond_macd_hist_ok_long'][i]) if is_long else bool(meta['cond_macd_hist_ok_short'][i])
        rsi_ok = bool(meta['cond_long_rsi'][i]) if is_long else bool(meta['cond_short_rsi'][i])
        macd_ok = bool(meta['cond_long_macd'][i]) if is_long else bool(meta['cond_short_macd'][i])
        di_ok = None
    # v3.5 — MACD line / signal line rules are an optional gate on both
    # setups. N/A (None) while the block is off, so an old-style run never
    # reports a PASS for a filter it was not tested against.
    macd_line_ok = None
    if meta.get('macd_line_rules_enabled'):
        macd_line_ok = bool(meta['cond_macd_line_ok_long' if is_long else 'cond_macd_line_ok_short'][i])
    macd_line_v = float(meta['macd_line'][i]) if 'macd_line' in meta else None
    macd_signal_v = float(meta['macd_signal'][i]) if 'macd_signal' in meta else None
    return {
        "signal_candle_time": None,  # filled by caller (needs index)
        "signal_candle_type": candle_color(meta, i),
        "candle_type": candle_color(meta, i),  # legacy alias (signal candle)
        "trend_4h": "UP" if meta['trend'][i] == 1 else "DOWN",
        "setup": setup,
        "rsi14": float(meta['rsi14'][i]),
        "macd_hist": float(meta['macd_hist'][i]),
        "adx": float(meta['adx'][i]),
        "atr14": float(meta['atr14'][i]),
        "ema50_1h": float(meta['ema50_1h'][i]),
        "ema50_4h": float(meta['ema50_4h'][i]),
        "cond_trend_ok": trend_ok,
        "cond_adx_ok": adx_ok,
        "cond_macd_hist_ok": hist_ok,
        "cond_atr_regime_ok": regime_ok,
        "cond_rsi_ok": rsi_ok,
        "cond_macd_confirm_ok": macd_ok,
        "cond_di_ok": di_ok,
        # v3.5 — MACD line / signal line at the signal candle + rule result.
        "macd_line": macd_line_v,
        "macd_signal": macd_signal_v,
        "cond_macd_line_ok": macd_line_ok,
    }


def macd_line_conditions_text(config, meta, i, signal_dir, number):
    """One log line per active v3.5 MACD line / signal rule (or None when off).

    Mirrors ``StrategyService._macd_line_mask`` so the log always states
    the exact comparison that was applied to this side.
    """
    cfg = config
    if not getattr(cfg, 'uses_macd_line_rules', lambda: False)():
        return []
    is_long = signal_dir == 1
    side_key = 'long' if is_long else 'short'
    if f'macd_line_{side_key}' not in meta:
        # Strategy services other than StrategyService do not publish the
        # per-side MACD lines — nothing to explain.
        return []
    line_now = float(meta[f'macd_line_{side_key}'][i])
    line_prev = float(meta[f'macd_line_{side_key}_prev'][i])
    sig_now = float(meta[f'macd_signal_{side_key}'][i])
    sig_prev = float(meta[f'macd_signal_{side_key}_prev'][i])

    def num(v, digits=2):
        try:
            return f"{float(v):,.{digits}f}"
        except (TypeError, ValueError):
            return '—'

    checks = []
    specs = (
        ('line_vs_signal', 'MACD line', 'signal', line_now, sig_now, line_prev, sig_prev),
        ('line_vs_zero', 'MACD line', '0', line_now, 0.0, line_prev, 0.0),
        ('signal_vs_zero', 'signal line', '0', sig_now, 0.0, sig_prev, 0.0),
    )
    for key, left, right, now_l, now_r, prev_l, prev_r in specs:
        rule = cfg.macd_line_rule_for(signal_dir, key)
        if rule == 'off':
            continue
        now_ok = now_l > now_r if is_long else now_l < now_r
        if rule == 'cross':
            prev_other = prev_l <= prev_r if is_long else prev_l >= prev_r
            ok = now_ok and prev_other
            checks.append(f"{left} {num(prev_l)} -> {num(now_l)} vs {right}"
                          f"{'' if right == '0' else ' ' + num(now_r)} needs cross "
                          f"{'above' if is_long else 'below'} -> {'PASS' if ok else 'FAIL'}")
        else:
            checks.append(f"{left} {num(now_l)} {'>' if is_long else '<'} {right}"
                          f"{'' if right == '0' else ' ' + num(now_r)} -> {'PASS' if now_ok else 'FAIL'}")
    thr = cfg.macd_line_min_for(signal_dir)
    if thr is not None:
        ok = line_now >= thr if is_long else line_now <= thr
        checks.append(f"MACD line {num(line_now)} {'>=' if is_long else '<='} {num(thr)} -> "
                      f"{'PASS' if ok else 'FAIL'}")
    thr = cfg.macd_signal_min_for(signal_dir)
    if thr is not None:
        ok = sig_now >= thr if is_long else sig_now <= thr
        checks.append(f"signal line {num(sig_now)} {'>=' if is_long else '<='} {num(thr)} -> "
                      f"{'PASS' if ok else 'FAIL'}")
    if not checks:
        return [f"{number}. MACD line/signal rules: enabled but no rule active for this side -> N/A"]
    return [f"{number}. MACD line/signal: {c}" for c in checks]


def entry_conditions_text(config, meta, i, signal_dir):
    """Spell out every entry condition for the trade log and Excel export.

    One line per filter: the measured value, the threshold applied to that
    side and PASS/FAIL — so a reviewer can see exactly why the entry was
    taken without re-running the backtest.
    """
    if meta is None:
        return None
    cfg = config
    is_long = signal_dir == 1
    side = 'LONG' if is_long else 'SHORT'
    setup = str(meta['setup'][i])

    def num(v, digits=2):
        try:
            return f"{float(v):,.{digits}f}"
        except (TypeError, ValueError):
            return '—'

    lines = [f"Side: {side} | Setup: {setup}"]

    # 1. 4h trend alignment
    trend_up = int(meta['trend'][i]) == 1
    close_v = float(meta['close'][i])
    ema4h = float(meta['ema50_4h'][i])
    lines.append(
        f"1. 4h trend: close {num(close_v)} vs EMA50(4h) {num(ema4h)} -> "
        f"{'UP' if trend_up else 'DOWN'}; {side} needs "
        f"{'UP' if is_long else 'DOWN'} -> {'PASS' if trend_up == is_long else 'FAIL'}")

    # 2. ADX
    adx_min = cfg.adx_min_for(signal_dir)
    adx_v = float(meta['adx'][i])
    lines.append(f"2. ADX: {num(adx_v, 1)} >= min {num(adx_min, 1)} -> "
                 f"{'PASS' if adx_v >= adx_min else 'FAIL'}")

    # 3. MACD histogram — a Setup A gate only. Setup B enters on the
    # zero-cross instead, so saying "FAIL" there would be wrong.
    if setup == 'MOMENTUM':
        lines.append("3. MACD hist magnitude: not applied — Setup B (momentum) "
                     "enters on the MACD zero-cross instead -> N/A")
    elif cfg.uses_direction_macd_hist():
        thr = cfg.macd_hist_min_for(signal_dir)
        h = float(meta['macd_hist_long' if is_long else 'macd_hist_short'][i])
        ok = h >= thr if is_long else h <= thr
        lines.append(f"3. MACD hist: {num(h)} "
                     f"{'>=' if is_long else '<='} threshold {num(thr)} -> "
                     f"{'PASS' if ok else 'FAIL'}")
    else:
        h = float(meta['macd_hist'][i])
        ok = abs(h) >= cfg.macd_hist_min
        lines.append(f"3. MACD hist: |{num(h)}| >= {num(cfg.macd_hist_min)} -> "
                     f"{'PASS' if ok else 'FAIL'}")

    # 4. ATR volatility regime (per-side operator)
    op = cfg.atr_regime_op_for(signal_dir)
    ratio = cfg.atr_regime_ratio_for(signal_dir)
    atr_v = float(meta['atr14'][i])
    sma_v = float(meta['atr_sma50'][i])
    threshold = ratio * sma_v
    cmp_ok = {'>=': atr_v >= threshold, '<=': atr_v <= threshold,
              '>': atr_v > threshold, '<': atr_v < threshold}[op]
    cap = cfg.atr_regime_max_for(signal_dir)
    cap_txt = ''
    if cap is not None:
        cap_ok = atr_v <= cap * sma_v
        cap_txt = f" and ATR <= {num(cap)} x SMA50 = {num(cap * sma_v)} ({'PASS' if cap_ok else 'FAIL'})"
        cmp_ok = cmp_ok and cap_ok
    lines.append(f"4. ATR regime: ATR {num(atr_v)} {op} {num(ratio)} x SMA50(ATR) "
                 f"{num(sma_v)} = {num(threshold)}{cap_txt} -> "
                 f"{'PASS' if cmp_ok else 'FAIL'}")

    # 5 & 6 depend on which setup fired
    rsi_v = float(meta['rsi14'][i])
    rsi_prev_v = float(meta['rsi_prev'][i])
    if setup == 'MOMENTUM':
        lines.append(f"5. DI confirmation: +DI {num(meta['pdi'][i], 1)} vs -DI "
                     f"{num(meta['mdi'][i], 1)} -> needs "
                     f"{'+DI > -DI' if is_long else '-DI > +DI'} -> "
                     f"{'PASS' if (meta['pdi'][i] > meta['mdi'][i]) == is_long else 'FAIL'}")
        h_prev = float(meta['macd_hist_long_prev' if is_long else 'macd_hist_short_prev'][i])
        h_now = float(meta['macd_hist_long' if is_long else 'macd_hist_short'][i])
        crossed = (h_prev <= 0 < h_now) if is_long else (h_prev >= 0 > h_now)
        lines.append(f"6. MACD zero-cross: hist {num(h_prev)} -> {num(h_now)} -> needs "
                     f"{'cross above 0' if is_long else 'cross below 0'} -> "
                     f"{'PASS' if crossed else 'FAIL'}")
        mom_min = cfg.momentum_rsi_min
        ok_rsi = rsi_v >= mom_min if is_long else rsi_v <= 100.0 - mom_min
        lines.append(f"7. RSI agreement: RSI {num(rsi_v, 1)} "
                     f"{'>=' if is_long else '<='} {num(mom_min if is_long else 100.0 - mom_min, 1)} -> "
                     f"{'PASS' if ok_rsi else 'FAIL'}")
    else:
        bound = cfg.rsi_oversold_for(1) if is_long else cfg.rsi_overbought_for(-1)
        ok_rsi = rsi_prev_v < bound if is_long else rsi_prev_v > bound
        color = candle_color(meta, i)
        ok_candle = (color == 'GREEN') if is_long else (color == 'RED')
        lines.append(f"5. RSI trigger: prev RSI {num(rsi_prev_v, 1)} "
                     f"{'<' if is_long else '>'} {num(bound, 1)} -> "
                     f"{'PASS' if ok_rsi else 'FAIL'}")
        lines.append(f"6. Candle colour: {color} -> needs "
                     f"{'GREEN' if is_long else 'RED'} -> "
                     f"{'PASS' if ok_candle else 'FAIL'}")
        h_prev = float(meta['macd_hist_long_prev' if is_long else 'macd_hist_short_prev'][i])
        h_now = float(meta['macd_hist_long' if is_long else 'macd_hist_short'][i])
        confirm = h_now > h_prev if is_long else h_now < h_prev
        lines.append(f"7. MACD confirmation: hist {num(h_prev)} -> {num(h_now)} -> needs "
                     f"{'rising' if is_long else 'falling'} -> "
                     f"{'PASS' if confirm else 'FAIL'}")

    # 8. v3.5 MACD line / signal line rules — only written when the block
    # is switched on, so the log of an existing configuration is unchanged.
    lines.extend(macd_line_conditions_text(config, meta, i, signal_dir, 8))

    return '\n'.join(lines)


def entry_context(config, meta, i, signal_dir, signal_candle_time=None,
                  entry_candle_time=None, entry_candle_type=None):
    """Everything the trade log needs about the candle that produced a trade.

    Returns the market/condition snapshot plus the readable per-condition
    breakdown, keyed exactly like a backtest trade-log row so the same UI and
    CSV export can render it. ``entry_candle_type`` defaults to the colour of
    the candle after the signal bar (where a backtest fill lands); the paper
    and live workers fill during the signal bar itself and pass it in.
    """
    if meta is None:
        return {}
    ctx = condition_snapshot(meta, i, signal_dir)
    ctx["signal_candle_time"] = signal_candle_time
    ctx["entry_candle_time"] = entry_candle_time
    if entry_candle_type is None:
        entry_candle_type = candle_color(meta, i + 1)
    ctx["entry_candle_type"] = entry_candle_type
    ctx["entry_conditions_detail"] = entry_conditions_text(config, meta, i, signal_dir)
    return ctx


def frame_candle_color(df, i=-1):
    """Colour of one row of an indicator frame, or None when that frame
    carries no candle-colour flags (used to stamp the candle a paper/live
    exit landed in)."""
    if df is None or len(df) == 0:
        return None
    try:
        if "is_green" not in df.columns or "is_red" not in df.columns:
            return None
        row = df.iloc[i]
        return row_candle_color(row["is_green"], row["is_red"])
    except (IndexError, KeyError, TypeError):
        return None
