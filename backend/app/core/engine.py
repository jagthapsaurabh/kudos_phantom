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

    def run(self, symbol="BTCUSDT", initial_capital_inr=20000, conversion_rate=85.0, start_date=None, end_date=None):
        df_1h = self._get_data_from_db(symbol, "1h", start_date, end_date)
        df_4h = self._get_data_from_db(symbol, "4h", start_date, end_date)
        
        if df_1h.empty or df_4h.empty:
            raise ValueError("Insufficient data in DB for the selected date range.")

        from .indicators import compute_indicators
        ind_1h = compute_indicators(df_1h)
        for col, values in ind_1h.items(): df_1h[col] = values
        
        signals = self.strategy_service.generate_signals(df_1h, df_4h)
        equity_inr = initial_capital_inr
        equity_curve = [initial_capital_inr]
        trades = []
        rejected_reasons = {}
        
        for i in range(1, len(df_1h)):
            current_time = df_1h.index[i]
            current_price_usd = df_1h['close'].iloc[i]
            current_atr_usd = df_1h['atr14'].iloc[i]
            
            for sym in list(self.oms.active_trades.keys()):
                result = self.oms.update_trade(sym, current_price_usd, current_atr_usd, current_time)
                if result:
                    price_diff = (result.exit_price - result.entry_price) * result.direction
                    pnl_usd = result.lots * price_diff
                    pnl_inr = pnl_usd * conversion_rate
                    
                    entry_fee_inr = (result.notional_usd * (self.config.taker_fee_bps / 10000)) * conversion_rate
                    exit_rate = self.config.maker_fee_bps if result.exit_reason == "TP" else self.config.taker_fee_bps
                    exit_fee_inr = (result.notional_usd * (exit_rate / 10000)) * conversion_rate
                    
                    net_pnl_inr = pnl_inr - entry_fee_inr - exit_fee_inr
                    equity_inr += net_pnl_inr
                    
                    trades.append({
                        "entry_time": result.entry_time, "exit_time": result.exit_time,
                        "direction": result.direction, "entry_price": result.entry_price,
                        "exit_price": result.exit_price, "lots": result.lots,
                        "margin": result.margin_inr, "notional": result.notional_usd,
                        "net_pnl": net_pnl_inr, "fees": entry_fee_inr + exit_fee_inr,
                        "exit_reason": result.exit_reason, "equity_after": equity_inr,
                        "drawdown": 0, "hold_bars": result.bars_held
                    })

            sig = signals[i]
            if sig != 0 and i + 1 < len(df_1h):
                next_open_usd = df_1h['open'].iloc[i+1]
                ind_slice = df_1h.iloc[max(0, i-50):i+1]
                val = self.validator_service.validate_signal(sig, df_1h['close'].iloc[i], next_open_usd, ind_slice)
                if val.passed:
                    margin_inr = equity_inr * 0.25
                    self.oms.create_order("BTCUSDT", sig, next_open_usd, current_atr_usd, df_1h.index[i+1], margin_inr, conversion_rate)
                else:
                    reason = val.reason
                    rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1
            
            equity_curve.append(equity_inr)

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
        
        # Exit Distribution
        reasons = [t['exit_reason'] for t in trades]
        dist = {r: reasons.count(r) for r in set(reasons)}
        
        # Sharpe Ratio (Simplified monthly)
        equity_series = pd.Series(equity_curve, index=df_1h.index)
        monthly_returns = equity_series.resample('ME').last().pct_change().dropna()
        sharpe = (monthly_returns.mean() / monthly_returns.std() * np.sqrt(12)) if len(monthly_returns) > 1 else 0
        
        # Calculate DD for each trade
        for j, t in enumerate(trades):
            t['drawdown'] = drawdown.iloc[int(t['exit_time'].timestamp() - df_1h.index[0].timestamp()) // 3600] * 100 if len(equity_curve) > 0 else 0

        return {
            "final_equity_inr": equity_inr,
            "total_trades": len(trades),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_dd,
            "roi": roi,
            "equity_curve": equity_curve,
            "trades": trades,
            "exit_dist": dist,
            "rejected_reasons": rejected_reasons
        }
