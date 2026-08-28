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
    entry_price: float  # USD trade (fill) price
    sl: float           # USD (derived from mark price)
    tp: float           # USD (derived from mark price)
    trail_activation: float # USD
    trail_stop: float    # USD
    atr_at_entry: float  # USD
    entry_time: any
    margin_inr: float
    notional_usd: float
    lots: float
    status: OrderStatus = OrderStatus.OPEN
    peak_price: float = 0.0
    current_price: float = 0.0     # latest trade price while the trade is open
    current_mark_price: float = 0.0  # latest mark price (used for calculations)
    entry_mark_price: float = 0.0    # mark price at entry
    exit_price: float = 0.0
    exit_mark_price: float = 0.0     # mark price at exit
    exit_time: any = None
    exit_reason: str = ""
    exit_detail: str = ""   # human-readable description of the exit condition
    bars_held: int = 0
    # Stop levels as originally set at entry. `sl` moves (breakeven/trailing),
    # so the UI needs the initial value to show "what the plan was".
    sl_entry: float = 0.0
    tp_entry: float = 0.0

class OrderManager:
    def __init__(self, config):
        self.config = config
        self.active_trades = {}

    def create_order(self, symbol, direction, price_usd, atr_usd, timestamp, margin_inr,
                     conversion_rate=85.0, mark_price=None):
        # `price_usd` is the executed trade (fill) price; `mark_price` (when
        # supplied) is the mark price the trade is calculated against. When a
        # caller only knows the mark price (backtest / paper) both are equal.
        trade_price = float(price_usd)
        mark = float(mark_price) if mark_price is not None else trade_price
        # 1. Notional Calculation
        notional_usd = (margin_inr * self.config.leverage) / conversion_rate
        
        # 2. Lot Quantization (0.001 BTC minimum) - Exactly as client code.
        # Position sizing and all stop calculations are on mark price.
        lots_raw = notional_usd / mark
        ql = int(lots_raw / self.config.lot_size_btc)
        
        if ql < 1:
            return None
            
        lots = ql * self.config.lot_size_btc
        notional_usd = lots * mark
        margin_inr = (notional_usd / self.config.leverage) * conversion_rate
        
        # SL / TP / Trail Distances. `stop_loss_atr_for` honours the
        # direction-specific override when the master toggle is ON, otherwise
        # it returns the shared `stop_loss_atr`.
        sl_atr = getattr(self.config, 'stop_loss_atr_for', None)
        sl_atr_val = sl_atr(direction) if callable(sl_atr) else self.config.stop_loss_atr
        sl_dist = sl_atr_val * atr_usd
        # SL Floor: max(2.0xATR, 1.6% * entry)
        min_sl = self.config.sl_floor_pct * mark
        if sl_dist < min_sl:
            sl_dist = min_sl
            
        sl = mark - sl_dist if direction == 1 else mark + sl_dist
        tp = mark + (self.config.take_profit_atr * atr_usd) if direction == 1 else mark - (self.config.take_profit_atr * atr_usd)
        trail_act = mark + (self.config.trail_activation_atr * atr_usd) if direction == 1 else mark - (self.config.trail_activation_atr * atr_usd)
        
        # Trailing stop level starts at hard SL
        trail_stop = sl
        
        trade = Trade(
            symbol=symbol, direction=direction, entry_price=trade_price, sl=sl, tp=tp,
            trail_activation=trail_act, trail_stop=trail_stop, atr_at_entry=atr_usd,
            entry_time=timestamp, margin_inr=margin_inr, notional_usd=notional_usd, lots=lots,
            peak_price=mark, current_price=trade_price, current_mark_price=mark,
            entry_mark_price=mark, sl_entry=sl, tp_entry=tp
        )
        self.active_trades[symbol] = trade
        return trade

    def update_trade(self, symbol, current_price_usd, current_atr_usd, timestamp, trade_price=None):
        if symbol not in self.active_trades: return None
        trade = self.active_trades[symbol]
        trade.bars_held += 1
        # `current_price_usd` is the MARK price used for every stop/trail/PnL
        # decision. `trade_price`, when supplied by the broker, is the actual
        # fill price recorded separately for the database trade-price columns.
        market = float(current_price_usd)
        fill = float(trade_price) if trade_price is not None else market
        trade.current_mark_price = market
        trade.current_price = fill
        if trade.direction == 1:
            # 1. Update peak and activate trail
            trade.peak_price = max(trade.peak_price, market)
            if trade.peak_price >= trade.trail_activation:
                # Trail advances based on peak
                new_tsl = trade.peak_price - (self.config.trail_distance_atr * current_atr_usd)
                trade.trail_stop = max(trade.trail_stop, new_tsl)

            # 1b. Breakeven stop (v3): once +breakeven_atr x ATR in favour,
            # the hard stop can never lose money.
            be = getattr(self.config, 'breakeven_atr', 0.0)
            if be > 0 and trade.peak_price >= trade.entry_mark_price + be * trade.atr_at_entry:
                trade.sl = max(trade.sl, trade.entry_mark_price)
                if trade.peak_price >= trade.trail_activation:
                    trade.trail_stop = max(trade.trail_stop, trade.entry_mark_price)
            
            # 2. Check TSL / SL FIRST
            trail_hit = trade.peak_price >= trade.trail_activation
            stop_level = trade.trail_stop if trail_hit else trade.sl
            if market <= stop_level:
                if trail_hit:
                    detail = (f"Trailing stop hit — mark price fell to {market:,.2f} ≤ trail {stop_level:,.2f} "
                              f"(peak {trade.peak_price:,.2f}, trail activated at {trade.trail_activation:,.2f})")
                else:
                    be_note = " (at breakeven)" if trade.sl >= trade.entry_mark_price else ""
                    detail = f"Stop loss hit — mark price fell to {market:,.2f} ≤ SL {stop_level:,.2f}{be_note} (initial SL {trade.sl_entry:,.2f})"
                return self.close_trade(symbol, (fill if trade_price is not None else stop_level), timestamp,
                                        "TSL" if trail_hit else "SL", detail, mark_price=stop_level)

            # 3. Check TP SECOND
            if market >= trade.tp:
                detail = f"Take profit hit — mark price rose to {market:,.2f} ≥ TP {trade.tp:,.2f}"
                return self.close_trade(symbol, (fill if trade_price is not None else trade.tp), timestamp,
                                        "TP", detail, mark_price=trade.tp)
                
        else: # SHORT
            trade.peak_price = min(trade.peak_price, market)
            if trade.peak_price <= trade.trail_activation:
                new_tsl = trade.peak_price + (self.config.trail_distance_atr * current_atr_usd)
                trade.trail_stop = min(trade.trail_stop, new_tsl)

            # Breakeven stop (v3) for shorts
            be = getattr(self.config, 'breakeven_atr', 0.0)
            if be > 0 and trade.peak_price <= trade.entry_mark_price - be * trade.atr_at_entry:
                trade.sl = min(trade.sl, trade.entry_mark_price)
                if trade.peak_price <= trade.trail_activation:
                    trade.trail_stop = min(trade.trail_stop, trade.entry_mark_price)
                
            trail_hit = trade.peak_price <= trade.trail_activation
            stop_level = trade.trail_stop if trail_hit else trade.sl
            if market >= stop_level:
                if trail_hit:
                    detail = (f"Trailing stop hit — mark price rose to {market:,.2f} ≥ trail {stop_level:,.2f} "
                              f"(low {trade.peak_price:,.2f}, trail activated at {trade.trail_activation:,.2f})")
                else:
                    be_note = " (at breakeven)" if trade.sl <= trade.entry_mark_price else ""
                    detail = f"Stop loss hit — mark price rose to {market:,.2f} ≥ SL {stop_level:,.2f}{be_note} (initial SL {trade.sl_entry:,.2f})"
                return self.close_trade(symbol, (fill if trade_price is not None else stop_level), timestamp,
                                        "TSL" if trail_hit else "SL", detail, mark_price=stop_level)

            if market <= trade.tp:
                detail = f"Take profit hit — mark price fell to {market:,.2f} ≤ TP {trade.tp:,.2f}"
                return self.close_trade(symbol, (fill if trade_price is not None else trade.tp), timestamp,
                                        "TP", detail, mark_price=trade.tp)

        if trade.bars_held >= self.config.timeout_bars:
            detail = (f"Max holding time reached — closed at mark {market:,.2f} "
                      f"after {trade.bars_held} bars (limit {self.config.timeout_bars})")
            return self.close_trade(symbol, (fill if trade_price is not None else market), timestamp,
                                    "MH", detail, mark_price=market)
        return None

    def close_trade(self, symbol, price, timestamp, reason, detail="", mark_price=None):
        trade = self.active_trades.pop(symbol)
        trade.status = OrderStatus.CLOSED
        trade.exit_price = price
        trade.exit_mark_price = float(mark_price) if mark_price is not None else float(price)
        trade.exit_time = timestamp
        trade.exit_reason = reason
        trade.exit_detail = detail
        return trade
