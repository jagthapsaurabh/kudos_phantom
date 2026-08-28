import pandas as pd
import numpy as np
from .strategy import StrategyService, PhantomV2Config
from .strategy import ValidatorService
from ..services.order_manager import OrderManager
from ..database.models import SessionLocal, Klines
from datetime import datetime

class BacktestEngine:
    def __init__(self, config: PhantomV2Config = PhantomV2Config(), fee_schedule=None, data_source="Binance"):
        self.config = config
        self.fee_schedule = fee_schedule
        self.data_source = data_source
        self.strategy_service = StrategyService(config)
        self.validator_service = ValidatorService()
        self.oms = OrderManager(config)

    def _get_data_from_db(self, symbol, interval, start_date=None, end_date=None, source=None):
        db = SessionLocal()
        query = db.query(Klines).filter(Klines.symbol == symbol, Klines.interval == interval)
        if source:
            query = query.filter(Klines.source == source)
        if start_date: query = query.filter(Klines.event_time >= start_date)
        if end_date: query = query.filter(Klines.event_time <= end_date)
        data = query.order_by(Klines.event_time.asc()).all()
        db.close()

        if not data: return pd.DataFrame()
        df = pd.DataFrame([
            {'event_time': k.event_time, 'open': k.open, 'high': k.high, 'low': k.low, 'close': k.close, 'volume': k.volume,
             # Mark price of the BTC perpetual for the same bar (NULL until the
             # mark series is seeded for this source).
             'mark_open': getattr(k, 'mark_open', None), 'mark_high': getattr(k, 'mark_high', None),
             'mark_low': getattr(k, 'mark_low', None), 'mark_close': getattr(k, 'mark_close', None)}
            for k in data
        ])
        df.set_index('event_time', inplace=True)
        return df

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _book_closed_trade(self, result, equity_inr, conversion_rate):
        """Fee + PnL accounting for a closed trade. Returns (net_pnl_inr, trade_dict)."""
        price_diff = (result.exit_price - result.entry_price) * result.direction
        pnl_usd = result.lots * price_diff
        pnl_inr = pnl_usd * conversion_rate

        fee = self.fee_schedule or self.config
        if isinstance(fee, dict):
            taker = float(fee.get("taker_fee_bps", 0.0))
            maker = float(fee.get("maker_fee_bps", 0.0))
        else:
            taker = float(getattr(fee, "taker_fee_bps", 0.0))
            maker = float(getattr(fee, "maker_fee_bps", 0.0))
        entry_fee_inr = (result.notional_usd * (taker / 10000)) * conversion_rate
        exit_rate = maker if result.exit_reason == "TP" else taker
        exit_fee_inr = (result.notional_usd * (exit_rate / 10000)) * conversion_rate

        net_pnl_inr = pnl_inr - entry_fee_inr - exit_fee_inr
        equity_inr += net_pnl_inr

        trade_dict = {
            "entry_time": result.entry_time, "exit_time": result.exit_time,
            "direction": result.direction, "entry_price": result.entry_price,
            "exit_price": result.exit_price, "lots": result.lots,
            # BTC perpetual: the traded price AND the mark price are both
            # stored. `entry_price`/`exit_price` are the pricing basis the PnL
            # was computed on (mark price when `mark_price_basis` is 1).
            "entry_trade_price": getattr(result, "entry_trade_price", None),
            "exit_trade_price": getattr(result, "exit_trade_price", None),
            "entry_mark_price": getattr(result, "entry_mark_price", None),
            "exit_mark_price": getattr(result, "exit_mark_price", None),
            "mark_price_basis": bool(getattr(result, "mark_price_basis", False)),
            "margin": result.margin_inr, "notional": result.notional_usd,
            "gross_pnl": pnl_inr, "net_pnl": net_pnl_inr,
            "fees": entry_fee_inr + exit_fee_inr,
            "exit_reason": result.exit_reason, "equity_after": equity_inr,
            "drawdown": 0, "hold_bars": result.bars_held,
            "sl": result.sl, "tp": result.tp,
            # The exact rule that closed the trade (e.g. "Trailing stop hit —
            # price fell to 67,099.00 <= trail 67,150.00"), plus the SL that was
            # in force at entry vs at exit so breakeven moves are visible.
            "exit_detail": getattr(result, "exit_detail", "") or "",
            "sl_entry": getattr(result, "sl_entry", None),
            "trail_stop": getattr(result, "trail_stop", None),
            "atr_at_entry": getattr(result, "atr_at_entry", None),
            "peak_price": getattr(result, "peak_price", None),
        }
        return net_pnl_inr, equity_inr, trade_dict

    @staticmethod
    def _candle_color(meta, i):
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

    @classmethod
    def _condition_snapshot(cls, meta, i, signal_dir):
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
        return {
            "signal_candle_time": None,  # filled by caller (needs index)
            "signal_candle_type": cls._candle_color(meta, i),
            "candle_type": cls._candle_color(meta, i),  # legacy alias (signal candle)
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
        }

    def _entry_conditions_text(self, meta, i, signal_dir):
        """Spell out every entry condition for the trade log and Excel export.

        One line per filter: the measured value, the threshold applied to that
        side and PASS/FAIL — so a reviewer can see exactly why the entry was
        taken without re-running the backtest.
        """
        if meta is None:
            return None
        cfg = self.config
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
            color = self._candle_color(meta, i)
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

        return '\n'.join(lines)


    # ------------------------------------------------------------------
    # Main backtest loop
    # ------------------------------------------------------------------
    def _resolve_price_frame(self, df_1h):
        """Pick the price series the engine trades on.

        For the BTC perpetual the client prices risk on the exchange MARK price,
        so when a mark series has been seeded the mark OHLC becomes the pricing
        basis and the traded OHLC stays the recorded fill price. Bars without a
        mark price (older history, a source that does not publish one) fall back
        to the traded price bar-by-bar instead of dropping the whole run.
        """
        from .mark_price import decision_series

        use_mark = bool(getattr(self.config, "use_mark_price", True))
        dec_close, coverage_close, basis_close = decision_series(
            df_1h, use_mark, fallback_column="close", mark_column="mark_close")
        dec_open, coverage_open, basis_open = decision_series(
            df_1h, use_mark, fallback_column="open", mark_column="mark_open")
        # A run is priced on mark only when BOTH the open and the close series
        # carry marks — mixing a traded entry with a mark exit (or the other way
        # round) would make the PnL impossible to reconcile.
        basis = "mark" if (basis_close == "mark" and basis_open == "mark") else "trade"
        if basis == "trade":
            dec_close = pd.to_numeric(df_1h["close"], errors="coerce")
            dec_open = pd.to_numeric(df_1h["open"], errors="coerce")
        raw_close = (pd.to_numeric(df_1h["mark_close"], errors="coerce")
                     if "mark_close" in df_1h.columns else None)
        raw_open = (pd.to_numeric(df_1h["mark_open"], errors="coerce")
                    if "mark_open" in df_1h.columns else None)
        return {
            "decision_close": dec_close.astype(float).values,
            "decision_open": dec_open.astype(float).values,
            "mark_close": raw_close.astype(float).values if raw_close is not None else None,
            "mark_open": raw_open.astype(float).values if raw_open is not None else None,
            "basis": basis,
            "coverage": min(coverage_close, coverage_open) if basis == "mark" else 0.0,
        }

    @staticmethod
    def _mark_at(array, index):
        """Mark price at ``index`` or None (NaN / no series)."""
        if array is None:
            return None
        try:
            value = float(array[index])
        except (IndexError, TypeError, ValueError):
            return None
        if value != value:  # NaN
            return None
        return value

    def run(self, symbol="BTCUSDT", initial_capital_inr=20000, conversion_rate=85.0,
            start_date=None, end_date=None, df_1h=None, df_4h=None,
            trade_log_path=None):
        if df_1h is None:
            df_1h = self._get_data_from_db(symbol, "1h", start_date, end_date, self.data_source)
        if df_4h is None:
            df_4h = self._get_data_from_db(symbol, "4h", start_date, end_date, self.data_source)

        if df_1h.empty or df_4h.empty:
            raise ValueError("Insufficient data in DB for the selected date range.")

        df_1h = df_1h.sort_index()
        df_4h = df_4h.sort_index()

        from .indicators import compute_indicators
        ind_1h = compute_indicators(df_1h, macd_fast=self.config.macd_fast, macd_slow=self.config.macd_slow, macd_signal=self.config.macd_signal)
        for col, values in ind_1h.items(): df_1h[col] = values

        # StrategyServices built for Phantom expose metadata; third-party
        # services (custom dynamic rules, FastTest) only expose generate_signals.
        if hasattr(self.strategy_service, 'generate_signals_with_metadata'):
            signals, meta = self.strategy_service.generate_signals_with_metadata(df_1h, df_4h)
        else:
            signals = self.strategy_service.generate_signals(df_1h, df_4h)
            meta = None
        equity_inr = initial_capital_inr
        peak_equity = initial_capital_inr
        equity_curve = [initial_capital_inr]
        trades = []
        rejected_reasons = {}
        skipped_overlap = 0
        halt_bars = 0
        throttled_entries = 0
        blocked_entries = 0
        halted = False
        last_exit_i = -10**9
        cfg = self.config

        # ---- BTC perpetual pricing + "skip new trades" schedule ---------
        price_frame = self._resolve_price_frame(df_1h)
        dec_closes = price_frame["decision_close"]
        dec_opens = price_frame["decision_open"]
        mark_closes = price_frame["mark_close"]
        mark_opens = price_frame["mark_open"]
        self.mark_price_basis = price_frame["basis"] == "mark"
        self.mark_price_coverage = price_frame["coverage"]
        from .trading_windows import TradingWindowGuard, BLOCK_REASON
        window_guard = TradingWindowGuard.from_any(getattr(cfg, "trading_windows", None))
        self.window_guard = window_guard

        def close_active(sym, price, ts, reason):
            """Force-close helper used for reversals."""
            trade = self.oms.close_trade(sym, price, ts, reason,
                                         mark_price_usd=self._mark_at(mark_closes, i))
            if trade.bars_held == 0:
                trade.bars_held = 1
            net, eq, td = self._book_closed_trade(trade, equity_box[0], conversion_rate)
            td.update(open_ctx_box.pop(sym, {}))
            td["exit_candle_type"] = self._candle_color(meta, i)
            trades.append(td)
            equity_box[0] = eq
            return eq

        equity_box = [equity_inr]
        open_ctx_box = {}

        n = len(df_1h)
        idx = df_1h.index
        opens = df_1h['open'].values
        closes = df_1h['close'].values
        atrs = df_1h['atr14'].values

        for i in range(1, n):
            current_time = idx[i]
            # `current_price_usd` is the pricing basis (mark price of the
            # perpetual when mark pricing is on); `trade_price_usd` is what the
            # market was actually trading at, and is recorded on the trade.
            current_price_usd = float(dec_closes[i])
            trade_price_usd = float(closes[i])
            current_mark_usd = self._mark_at(mark_closes, i)
            current_atr_usd = atrs[i]

            # ---- Manage open positions ----------------------------------
            for sym in list(self.oms.active_trades.keys()):
                result = self.oms.update_trade(sym, current_price_usd, current_atr_usd, current_time,
                                               trade_price_usd=trade_price_usd,
                                               mark_price_usd=current_mark_usd)
                if result:
                    net, equity_box[0], td = self._book_closed_trade(result, equity_box[0], conversion_rate)
                    td.update(open_ctx_box.pop(sym, {}))
                    # Colour of the candle the exit was evaluated on.
                    td["exit_candle_type"] = self._candle_color(meta, i)
                    trades.append(td)
                    last_exit_i = i

            equity_inr = equity_box[0]
            peak_equity = max(peak_equity, equity_inr)
            dd_pct = ((peak_equity - equity_inr) / peak_equity * 100.0) if peak_equity > 0 else 0.0

            # ---- Drawdown throttle state machine ------------------------
            if halted:
                halt_bars += 1
                if dd_pct <= cfg.dd_resume_pct:
                    halted = False
            elif dd_pct >= cfg.dd_halt_pct:
                halted = True
            margin_pct_now = cfg.reduced_margin_pct if dd_pct >= cfg.dd_soft_pct else cfg.margin_pct

            # ---- Entries -------------------------------------------------
            sig = signals[i]
            if sig != 0 and i + 1 < n:
                in_cooldown = (i - last_exit_i) <= cfg.cooldown_bars
                # "Skip new trades" schedule. The new position would open at
                # the next candle, so that candle's timestamp decides whether
                # the entry is allowed. Open positions are untouched — only a
                # NEW trade is refused.
                blocked_window = window_guard.blocking_window(idx[i + 1])
                if blocked_window:
                    blocked_entries += 1
                    rejected_reasons[BLOCK_REASON] = rejected_reasons.get(BLOCK_REASON, 0) + 1
                if halted or in_cooldown or blocked_window:
                    pass
                else:
                    open_trade = self.oms.active_trades.get(symbol)
                    next_open_usd = float(dec_opens[i + 1])
                    next_trade_open_usd = float(opens[i + 1])
                    next_mark_usd = self._mark_at(mark_opens, i + 1)
                    if open_trade is not None and cfg.allow_reverse and open_trade.direction != sig:
                        # Close at next open and reverse direction
                        close_active(symbol, next_open_usd, idx[i + 1], "REV")
                        last_exit_i = i
                        open_trade = None
                    if open_trade is None or cfg.allow_overlap:
                        ind_slice = df_1h.iloc[max(0, i - 50):i + 1]
                        # Drift is measured against the price the order fills
                        # at (traded price), not against the mark-price basis.
                        val = self.validator_service.validate_signal(sig, closes[i], next_trade_open_usd, ind_slice)
                        if val.passed:
                            margin_inr = equity_box[0] * margin_pct_now
                            if margin_pct_now != cfg.margin_pct:
                                throttled_entries += 1
                            if meta is not None:
                                ctx = self._condition_snapshot(meta, i, int(sig))
                                ctx["signal_candle_time"] = idx[i]
                                # The entry fills on the NEXT candle's open, so
                                # record that candle and its colour separately
                                # from the signal candle the client asked about.
                                ctx["entry_candle_time"] = idx[i + 1]
                                ctx["entry_candle_type"] = self._candle_color(meta, i + 1)
                                ctx["entry_conditions_detail"] = self._entry_conditions_text(meta, i, int(sig))
                            else:
                                ctx = {
                                    "signal_candle_time": idx[i],
                                    "entry_candle_time": idx[i + 1],
                                    "setup": getattr(self.strategy_service, 'label', None)
                                             or type(self.strategy_service).__name__,
                                }
                            ctx["entry_dd_pct"] = dd_pct
                            ctx["margin_pct_used"] = margin_pct_now
                            ctx["equity_at_entry"] = equity_box[0]
                            new_trade = self.oms.create_order(symbol, int(sig), next_open_usd,
                                                              current_atr_usd, idx[i + 1],
                                                              margin_inr, conversion_rate,
                                                              trade_price_usd=next_trade_open_usd,
                                                              mark_price_usd=next_mark_usd,
                                                              mark_price_basis=self.mark_price_basis)
                            if new_trade is not None:
                                open_ctx_box[symbol] = ctx
                            else:
                                # Notional below the minimum 0.001 BTC lot
                                rejected_reasons['LOT_TOO_SMALL'] = rejected_reasons.get('LOT_TOO_SMALL', 0) + 1
                        else:
                            reason = val.reason
                            rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1
                    else:
                        skipped_overlap += 1

            equity_curve.append(equity_box[0])

        equity_inr = equity_box[0]

        # Final Metrics Calculation
        equity_series = pd.Series(equity_curve)
        peak = equity_series.cummax()
        drawdown = (peak - equity_series) / peak
        max_dd = drawdown.max() * 100

        pnl_list = [t['net_pnl'] for t in trades]
        wins = [p for p in pnl_list if p > 0]
        losses = [abs(p) for p in pnl_list if p <= 0]

        profit_factor = sum(wins) / sum(losses) if sum(losses) > 0 else 99.0
        win_rate = (len(wins) / len(trades) * 100 if trades else 0)
        roi = ((equity_inr - initial_capital_inr) / initial_capital_inr) * 100

        # Consecutive-loss streak
        max_consec_losses = 0
        streak = 0
        for p in pnl_list:
            if p <= 0:
                streak += 1
                max_consec_losses = max(max_consec_losses, streak)
            else:
                streak = 0

        # Exit Distribution
        reasons = [t['exit_reason'] for t in trades]
        dist = {r: reasons.count(r) for r in set(reasons)}

        # Setup distribution
        setups = [t.get('setup', '') for t in trades]
        setup_dist = {s: setups.count(s) for s in set(setups) if s}

        # Sharpe Ratio (Simplified monthly)
        equity_series = pd.Series(equity_curve, index=df_1h.index)
        monthly_returns = equity_series.resample('ME').last().pct_change().dropna()
        sharpe = (monthly_returns.mean() / monthly_returns.std() * np.sqrt(12)) if len(monthly_returns) > 1 else 0

        # Drawdown at each trade's exit candle (robust index lookup)
        dd_values = drawdown.values
        for t in trades:
            j = idx.get_indexer([t['exit_time']], method='ffill')[0]
            j = min(max(j, 0), len(dd_values) - 1)
            t['drawdown'] = float(dd_values[j] * 100)

        results = {
            "final_equity_inr": equity_inr,
            "total_trades": len(trades),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_dd,
            "roi": roi,
            "max_consec_losses": max_consec_losses,
            "avg_win": float(np.mean(wins)) if wins else 0.0,
            "avg_loss": float(np.mean(losses)) if losses else 0.0,
            "equity_curve": equity_curve,
            "trades": trades,
            "exit_dist": dist,
            "setup_dist": setup_dist,
            "rejected_reasons": rejected_reasons,
            # BTC perpetual: which price the run was priced on, and how much of
            # the range actually carried a mark price.
            "mark_price_basis": self.mark_price_basis,
            "mark_price_coverage": round(float(self.mark_price_coverage or 0.0) * 100.0, 2),
            # "Skip new trades" schedule actually applied to this run.
            "trading_windows": window_guard.summary(),
            "diagnostics": {
                "skipped_overlap": skipped_overlap,
                "halt_bars": halt_bars,
                "throttled_entries": throttled_entries,
                "blocked_entries": blocked_entries,
            }
        }

        if trade_log_path:
            self.export_trade_log(trades, trade_log_path)

        return results

    # ------------------------------------------------------------------
    @staticmethod
    def export_trade_log(trades, path):
        """Write every trade with the full entry-condition snapshot to CSV."""
        import os
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if not trades:
            pd.DataFrame().to_csv(path, index=False)
            return path
        log_df = pd.DataFrame(trades)
        cols = [
            'signal_candle_time', 'signal_candle_type', 'entry_candle_time',
            'entry_candle_type', 'exit_candle_type',
            'entry_time', 'exit_time', 'direction', 'setup',
            'candle_type', 'trend_4h',
            'rsi14', 'macd_hist', 'adx', 'atr14', 'ema50_1h', 'ema50_4h',
            'cond_trend_ok', 'cond_adx_ok', 'cond_macd_hist_ok', 'cond_atr_regime_ok',
            'cond_rsi_ok', 'cond_macd_confirm_ok', 'cond_di_ok',
            'entry_conditions_detail',
            'entry_price', 'entry_trade_price', 'entry_mark_price',
            'sl', 'sl_entry', 'tp', 'trail_stop',
            'exit_price', 'exit_trade_price', 'exit_mark_price',
            'mark_price_basis',
            'exit_reason', 'exit_detail',
            'atr_at_entry', 'peak_price',
            'lots', 'margin', 'notional', 'margin_pct_used', 'entry_dd_pct',
            'gross_pnl', 'fees', 'net_pnl', 'equity_at_entry', 'equity_after',
            'drawdown', 'hold_bars',
        ]
        cols = [c for c in cols if c in log_df.columns]
        log_df = log_df[cols + [c for c in log_df.columns if c not in cols]]
        log_df.to_csv(path, index=False)
        return path
