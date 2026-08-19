import pandas as pd
import numpy as np
from .strategy import StrategyService, PhantomV2Config
from .strategy import ValidatorService
from ..services.order_manager import OrderManager
from ..database.models import SessionLocal, Klines
from datetime import datetime

class BacktestEngine:
    def __init__(self, config: PhantomV2Config = PhantomV2Config()):
        self.config = config
        self.strategy_service = StrategyService(config)
        self.validator_service = ValidatorService()
        self.oms = OrderManager(config)

    def _get_data_from_db(self, symbol, interval, start_date=None, end_date=None):
        db = SessionLocal()
        query = db.query(Klines).filter(Klines.symbol == symbol, Klines.interval == interval)
        if start_date: query = query.filter(Klines.event_time >= start_date)
        if end_date: query = query.filter(Klines.event_time <= end_date)
        data = query.order_by(Klines.event_time.asc()).all()
        db.close()

        if not data: return pd.DataFrame()
        df = pd.DataFrame([
            {'event_time': k.event_time, 'open': k.open, 'high': k.high, 'low': k.low, 'close': k.close, 'volume': k.volume}
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

        entry_fee_inr = (result.notional_usd * (self.config.taker_fee_bps / 10000)) * conversion_rate
        exit_rate = self.config.maker_fee_bps if result.exit_reason == "TP" else self.config.taker_fee_bps
        exit_fee_inr = (result.notional_usd * (exit_rate / 10000)) * conversion_rate

        net_pnl_inr = pnl_inr - entry_fee_inr - exit_fee_inr
        equity_inr += net_pnl_inr

        trade_dict = {
            "entry_time": result.entry_time, "exit_time": result.exit_time,
            "direction": result.direction, "entry_price": result.entry_price,
            "exit_price": result.exit_price, "lots": result.lots,
            "margin": result.margin_inr, "notional": result.notional_usd,
            "gross_pnl": pnl_inr, "net_pnl": net_pnl_inr,
            "fees": entry_fee_inr + exit_fee_inr,
            "exit_reason": result.exit_reason, "equity_after": equity_inr,
            "drawdown": 0, "hold_bars": result.bars_held,
            "sl": result.sl, "tp": result.tp,
        }
        return net_pnl_inr, equity_inr, trade_dict

    @staticmethod
    def _condition_snapshot(meta, i, signal_dir):
        """Full market/condition snapshot on the signal candle (bar i)."""
        rsi_ok = bool(meta['cond_long_rsi'][i]) if signal_dir == 1 else bool(meta['cond_short_rsi'][i])
        macd_ok = bool(meta['cond_long_macd'][i]) if signal_dir == 1 else bool(meta['cond_short_macd'][i])
        return {
            "signal_candle_time": None,  # filled by caller (needs index)
            "candle_type": "GREEN" if bool(meta['is_green'][i]) else ("RED" if bool(meta['is_red'][i]) else "DOJI"),
            "trend_4h": "UP" if meta['trend'][i] == 1 else "DOWN",
            "setup": str(meta['setup'][i]),
            "rsi14": float(meta['rsi14'][i]),
            "macd_hist": float(meta['macd_hist'][i]),
            "adx": float(meta['adx'][i]),
            "atr14": float(meta['atr14'][i]),
            "ema50_1h": float(meta['ema50_1h'][i]),
            "ema50_4h": float(meta['ema50_4h'][i]),
            "cond_adx_ok": bool(meta['cond_adx_ok'][i]),
            "cond_macd_hist_ok": bool(meta['cond_macd_hist_ok'][i]),
            "cond_atr_regime_ok": bool(meta['cond_atr_regime_ok'][i]),
            "cond_rsi_ok": rsi_ok,
            "cond_macd_confirm_ok": macd_ok,
        }

    # ------------------------------------------------------------------
    # Main backtest loop
    # ------------------------------------------------------------------
    def run(self, symbol="BTCUSDT", initial_capital_inr=20000, conversion_rate=85.0,
            start_date=None, end_date=None, df_1h=None, df_4h=None,
            trade_log_path=None):
        if df_1h is None:
            df_1h = self._get_data_from_db(symbol, "1h", start_date, end_date)
        if df_4h is None:
            df_4h = self._get_data_from_db(symbol, "4h", start_date, end_date)

        if df_1h.empty or df_4h.empty:
            raise ValueError("Insufficient data in DB for the selected date range.")

        df_1h = df_1h.sort_index()
        df_4h = df_4h.sort_index()

        from .indicators import compute_indicators
        ind_1h = compute_indicators(df_1h)
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
        halted = False
        last_exit_i = -10**9
        cfg = self.config

        def close_active(sym, price, ts, reason):
            """Force-close helper used for reversals."""
            trade = self.oms.close_trade(sym, price, ts, reason)
            if trade.bars_held == 0:
                trade.bars_held = 1
            net, eq, td = self._book_closed_trade(trade, equity_box[0], conversion_rate)
            td.update(open_ctx_box.pop(sym, {}))
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
            current_price_usd = closes[i]
            current_atr_usd = atrs[i]

            # ---- Manage open positions ----------------------------------
            for sym in list(self.oms.active_trades.keys()):
                result = self.oms.update_trade(sym, current_price_usd, current_atr_usd, current_time)
                if result:
                    net, equity_box[0], td = self._book_closed_trade(result, equity_box[0], conversion_rate)
                    td.update(open_ctx_box.pop(sym, {}))
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
                if halted or in_cooldown:
                    pass
                else:
                    open_trade = self.oms.active_trades.get(symbol)
                    next_open_usd = opens[i + 1]
                    if open_trade is not None and cfg.allow_reverse and open_trade.direction != sig:
                        # Close at next open and reverse direction
                        close_active(symbol, next_open_usd, idx[i + 1], "REV")
                        last_exit_i = i
                        open_trade = None
                    if open_trade is None or cfg.allow_overlap:
                        ind_slice = df_1h.iloc[max(0, i - 50):i + 1]
                        val = self.validator_service.validate_signal(sig, closes[i], next_open_usd, ind_slice)
                        if val.passed:
                            margin_inr = equity_box[0] * margin_pct_now
                            if margin_pct_now != cfg.margin_pct:
                                throttled_entries += 1
                            if meta is not None:
                                ctx = self._condition_snapshot(meta, i, int(sig))
                                ctx["signal_candle_time"] = idx[i]
                            else:
                                ctx = {
                                    "signal_candle_time": idx[i],
                                    "setup": getattr(self.strategy_service, 'label', None)
                                             or type(self.strategy_service).__name__,
                                }
                            ctx["entry_dd_pct"] = dd_pct
                            ctx["margin_pct_used"] = margin_pct_now
                            ctx["equity_at_entry"] = equity_box[0]
                            new_trade = self.oms.create_order(symbol, int(sig), next_open_usd,
                                                              current_atr_usd, idx[i + 1],
                                                              margin_inr, conversion_rate)
                            if new_trade is not None:
                                open_ctx_box[symbol] = ctx
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
            "diagnostics": {
                "skipped_overlap": skipped_overlap,
                "halt_bars": halt_bars,
                "throttled_entries": throttled_entries,
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
            'signal_candle_time', 'entry_time', 'exit_time', 'direction', 'setup',
            'candle_type', 'trend_4h',
            'rsi14', 'macd_hist', 'adx', 'atr14', 'ema50_1h', 'ema50_4h',
            'cond_adx_ok', 'cond_macd_hist_ok', 'cond_atr_regime_ok',
            'cond_rsi_ok', 'cond_macd_confirm_ok',
            'entry_price', 'sl', 'tp', 'exit_price', 'exit_reason',
            'lots', 'margin', 'notional', 'margin_pct_used', 'entry_dd_pct',
            'gross_pnl', 'fees', 'net_pnl', 'equity_at_entry', 'equity_after',
            'drawdown', 'hold_bars',
        ]
        cols = [c for c in cols if c in log_df.columns]
        log_df = log_df[cols + [c for c in log_df.columns if c not in cols]]
        log_df.to_csv(path, index=False)
        return path
