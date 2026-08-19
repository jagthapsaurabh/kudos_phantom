from dataclasses import dataclass
from enum import Enum
import numpy as np

class OrderStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"

@dataclass
class Trade:
    symbol: str
    direction: int
    entry_price: float  # USD
    sl: float           # USD
    tp: float           # USD
    trail_activation: float # USD
    trail_stop: float    # USD
    atr_at_entry: float  # USD
    entry_time: any
    margin_inr: float
    notional_usd: float
    lots: float
    status: OrderStatus = OrderStatus.OPEN
    peak_price: float = 0.0
    exit_price: float = 0.0
    exit_time: any = None
    exit_reason: str = ""
    bars_held: int = 0

class OrderManager:
    def __init__(self, config):
        self.config = config
        self.active_trades = {}

    def create_order(self, symbol, direction, price_usd, atr_usd, timestamp, margin_inr, conversion_rate=85.0):
        # 1. Notional Calculation
        notional_usd = (margin_inr * self.config.leverage) / conversion_rate
        
        # 2. Lot Quantization (0.001 BTC minimum) - Exactly as client code
        lots_raw = notional_usd / price_usd
        ql = int(lots_raw / self.config.lot_size_btc)
        
        if ql < 1:
            return None
            
        lots = ql * self.config.lot_size_btc
        notional_usd = lots * price_usd
        margin_inr = (notional_usd / self.config.leverage) * conversion_rate
        
        # SL / TP / Trail Distances
        sl_dist = self.config.stop_loss_atr * atr_usd
        # SL Floor: max(2.0xATR, 1.6% * entry)
        min_sl = self.config.sl_floor_pct * price_usd
        if sl_dist < min_sl:
            sl_dist = min_sl
            
        sl = price_usd - sl_dist if direction == 1 else price_usd + sl_dist
        tp = price_usd + (self.config.take_profit_atr * atr_usd) if direction == 1 else price_usd - (self.config.take_profit_atr * atr_usd)
        trail_act = price_usd + (self.config.trail_activation_atr * atr_usd) if direction == 1 else price_usd - (self.config.trail_activation_atr * atr_usd)
        
        # Trailing stop level starts at hard SL
        trail_stop = sl
        
        trade = Trade(
            symbol=symbol, direction=direction, entry_price=price_usd, sl=sl, tp=tp, 
            trail_activation=trail_act, trail_stop=trail_stop, atr_at_entry=atr_usd, 
            entry_time=timestamp, margin_inr=margin_inr, notional_usd=notional_usd, lots=lots,
            peak_price=price_usd
        )
        self.active_trades[symbol] = trade
        return trade

    def update_trade(self, symbol, current_price_usd, current_atr_usd, timestamp):
        if symbol not in self.active_trades: return None
        trade = self.active_trades[symbol]
        trade.bars_held += 1
        
        if trade.direction == 1:
            # 1. Update peak and activate trail
            trade.peak_price = max(trade.peak_price, current_price_usd)
            if trade.peak_price >= trade.trail_activation:
                # Trail advances based on peak
                new_tsl = trade.peak_price - (self.config.trail_distance_atr * current_atr_usd)
                trade.trail_stop = max(trade.trail_stop, new_tsl)

            # 1b. Breakeven stop (v3): once +breakeven_atr x ATR in favour,
            # the hard stop can never lose money.
            be = getattr(self.config, 'breakeven_atr', 0.0)
            if be > 0 and trade.peak_price >= trade.entry_price + be * trade.atr_at_entry:
                trade.sl = max(trade.sl, trade.entry_price)
                if trade.peak_price >= trade.trail_activation:
                    trade.trail_stop = max(trade.trail_stop, trade.entry_price)
            
            # 2. Check TSL / SL FIRST
            stop_level = trade.trail_stop if trade.peak_price >= trade.trail_activation else trade.sl
            if current_price_usd <= stop_level:
                return self.close_trade(symbol, stop_level, timestamp, "TSL" if trade.peak_price >= trade.trail_activation else "SL")
            
            # 3. Check TP SECOND
            if current_price_usd >= trade.tp:
                return self.close_trade(symbol, trade.tp, timestamp, "TP")
                
        else: # SHORT
            trade.peak_price = min(trade.peak_price, current_price_usd)
            if trade.peak_price <= trade.trail_activation:
                new_tsl = trade.peak_price + (self.config.trail_distance_atr * current_atr_usd)
                trade.trail_stop = min(trade.trail_stop, new_tsl)

            # Breakeven stop (v3) for shorts
            be = getattr(self.config, 'breakeven_atr', 0.0)
            if be > 0 and trade.peak_price <= trade.entry_price - be * trade.atr_at_entry:
                trade.sl = min(trade.sl, trade.entry_price)
                if trade.peak_price <= trade.trail_activation:
                    trade.trail_stop = min(trade.trail_stop, trade.entry_price)
                
            stop_level = trade.trail_stop if trade.peak_price <= trade.trail_activation else trade.sl
            if current_price_usd >= stop_level:
                return self.close_trade(symbol, stop_level, timestamp, "TSL" if trade.peak_price <= trade.trail_activation else "SL")
                
            if current_price_usd <= trade.tp:
                return self.close_trade(symbol, trade.tp, timestamp, "TP")
        
        if trade.bars_held >= self.config.timeout_bars:
            return self.close_trade(symbol, current_price_usd, timestamp, "MH")
        return None

    def close_trade(self, symbol, price, timestamp, reason):
        trade = self.active_trades.pop(symbol)
        trade.status = OrderStatus.CLOSED
        trade.exit_price = price
        trade.exit_time = timestamp
        trade.exit_reason = reason
        return trade
