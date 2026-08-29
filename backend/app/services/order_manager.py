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
    # `entry_price` is the pricing basis used for the maths. For the BTC
    # perpetual that is the exchange MARK price when `mark_price_basis` is on;
    # the price the order actually filled at is kept in `entry_trade_price`.
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
    current_price: float = 0.0     # latest market price while the trade is open
    exit_price: float = 0.0
    exit_time: any = None
    exit_reason: str = ""
    exit_detail: str = ""   # human-readable description of the exit condition
    bars_held: int = 0
    # Stop levels as originally set at entry. `sl` moves (breakeven/trailing),
    # so the UI needs the initial value to show "what the plan was".
    sl_entry: float = 0.0
    tp_entry: float = 0.0
    # ---- BTC perpetual: traded price vs mark price ------------------
    entry_trade_price: float = 0.0      # actual fill / traded price
    entry_mark_price: float = 0.0       # exchange mark price at entry
    exit_trade_price: float = 0.0       # traded price when the exit fired
    exit_mark_price: float = 0.0        # mark price the exit was triggered on
    mark_price_basis: bool = False      # True when entry/exit_price are marks
    current_mark_price: float = 0.0     # latest mark price while open

class OrderManager:
    def __init__(self, config):
        self.config = config
        self.active_trades = {}

    def create_order(self, symbol, direction, price_usd, atr_usd, timestamp, margin_inr,
                     conversion_rate=85.0, trade_price_usd=None, mark_price_usd=None,
                     mark_price_basis=None):
        """Open a position.

        ``price_usd`` is the pricing basis (mark price of the BTC perpetual when
        mark pricing is on). ``trade_price_usd`` is the price the order would
        actually fill at and ``mark_price_usd`` the exchange mark price at that
        instant; both are stored on the trade so the fill and the pricing basis
        can always be reconciled.
        """
        use_mark = bool(mark_price_basis) if mark_price_basis is not None else bool(getattr(self.config, 'use_mark_price', True))
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
        
        # SL / TP / Trail Distances. `stop_loss_atr_for` honours the
        # direction-specific override when the master toggle is ON, otherwise
        # it returns the shared `stop_loss_atr`.
        sl_atr = getattr(self.config, 'stop_loss_atr_for', None)
        sl_atr_val = sl_atr(direction) if callable(sl_atr) else self.config.stop_loss_atr
        sl_dist = sl_atr_val * atr_usd
        # SL Floor: max(2.0xATR, 1.6% * entry)
        min_sl = self.config.sl_floor_pct * price_usd
        if sl_dist < min_sl:
            sl_dist = min_sl
            
        sl = price_usd - sl_dist if direction == 1 else price_usd + sl_dist
        tp = price_usd + (self.config.take_profit_atr * atr_usd) if direction == 1 else price_usd - (self.config.take_profit_atr * atr_usd)
        trail_act = price_usd + (self.config.trail_activation_atr * atr_usd) if direction == 1 else price_usd - (self.config.trail_activation_atr * atr_usd)
        
        # Trailing stop level starts at hard SL
        trail_stop = sl
        
        # Fill price vs pricing basis. When the engine prices on mark, `price`
        # is already the mark price; otherwise the mark is whatever the caller
        # read from the mark series (may be None for un-seeded bars).
        if use_mark:
            entry_mark = float(price_usd)
            entry_trade = float(trade_price_usd) if trade_price_usd else float(price_usd)
            basis_price = float(price_usd)
        else:
            entry_mark = float(mark_price_usd) if mark_price_usd else 0.0
            entry_trade = float(trade_price_usd) if trade_price_usd else float(price_usd)
            basis_price = float(price_usd)

        trade = Trade(
            symbol=symbol, direction=direction, entry_price=basis_price, sl=sl, tp=tp,
            trail_activation=trail_act, trail_stop=trail_stop, atr_at_entry=atr_usd,
            entry_time=timestamp, margin_inr=margin_inr, notional_usd=notional_usd, lots=lots,
            peak_price=basis_price, current_price=basis_price,
            sl_entry=sl, tp_entry=tp,
            entry_trade_price=entry_trade, entry_mark_price=entry_mark,
            mark_price_basis=use_mark, current_mark_price=entry_mark,
        )
        self.active_trades[symbol] = trade
        return trade

    def update_trade(self, symbol, current_price_usd, current_atr_usd, timestamp,
                     trade_price_usd=None, mark_price_usd=None, advance_bar=True):
        """Mark-to-market an open position and apply its stop/target rules.

        ``advance_bar`` controls the holding-time clock only. The backtest
        engine calls this once per candle, so it stays True there. The live and
        paper workers poll every 60 seconds — often dozens of times inside one
        1h candle — so they pass ``advance_bar=False`` until the candle actually
        rolls over, otherwise ``timeout_bars`` (72 candles = 3 days) would
        force-close a position after 72 *minutes*.
        """
        if symbol not in self.active_trades: return None
        trade = self.active_trades[symbol]
        if advance_bar:
            trade.bars_held += 1
        trade.current_price = current_price_usd
        # Keep both prices current: `current_price` is the pricing basis (mark),
        # `exit_trade_price` records what the market was trading at when the
        # stop/target level was reached.
        trade.exit_trade_price = float(trade_price_usd) if trade_price_usd else float(current_price_usd)
        if mark_price_usd:
            trade.current_mark_price = float(mark_price_usd)
            trade.exit_mark_price = float(mark_price_usd)
        elif trade.mark_price_basis:
            trade.current_mark_price = float(current_price_usd)
            trade.exit_mark_price = float(current_price_usd)
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
            trail_hit = trade.peak_price >= trade.trail_activation
            stop_level = trade.trail_stop if trail_hit else trade.sl
            if current_price_usd <= stop_level:
                if trail_hit:
                    detail = (f"Trailing stop hit — price fell to {current_price_usd:,.2f} ≤ trail {stop_level:,.2f} "
                              f"(peak {trade.peak_price:,.2f}, trail activated at {trade.trail_activation:,.2f})")
                else:
                    be_note = " (at breakeven)" if trade.sl >= trade.entry_price else ""
                    detail = f"Stop loss hit — price fell to {current_price_usd:,.2f} ≤ SL {stop_level:,.2f}{be_note} (initial SL {trade.sl_entry:,.2f})"
                return self.close_trade(symbol, stop_level, timestamp, "TSL" if trail_hit else "SL", detail)

            # 3. Check TP SECOND
            if current_price_usd >= trade.tp:
                detail = f"Take profit hit — price rose to {current_price_usd:,.2f} ≥ TP {trade.tp:,.2f}"
                return self.close_trade(symbol, trade.tp, timestamp, "TP", detail)
                
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
                
            trail_hit = trade.peak_price <= trade.trail_activation
            stop_level = trade.trail_stop if trail_hit else trade.sl
            if current_price_usd >= stop_level:
                if trail_hit:
                    detail = (f"Trailing stop hit — price rose to {current_price_usd:,.2f} ≥ trail {stop_level:,.2f} "
                              f"(low {trade.peak_price:,.2f}, trail activated at {trade.trail_activation:,.2f})")
                else:
                    be_note = " (at breakeven)" if trade.sl <= trade.entry_price else ""
                    detail = f"Stop loss hit — price rose to {current_price_usd:,.2f} ≥ SL {stop_level:,.2f}{be_note} (initial SL {trade.sl_entry:,.2f})"
                return self.close_trade(symbol, stop_level, timestamp, "TSL" if trail_hit else "SL", detail)

            if current_price_usd <= trade.tp:
                detail = f"Take profit hit — price fell to {current_price_usd:,.2f} ≤ TP {trade.tp:,.2f}"
                return self.close_trade(symbol, trade.tp, timestamp, "TP", detail)

        if trade.bars_held >= self.config.timeout_bars:
            detail = (f"Max holding time reached — closed at market {current_price_usd:,.2f} "
                      f"after {trade.bars_held} bars (limit {self.config.timeout_bars})")
            return self.close_trade(symbol, current_price_usd, timestamp, "MH", detail)
        return None

    def close_trade(self, symbol, price, timestamp, reason, detail="", trade_price_usd=None,
                    mark_price_usd=None):
        trade = self.active_trades.pop(symbol)
        trade.status = OrderStatus.CLOSED
        trade.exit_price = price
        trade.exit_time = timestamp
        trade.exit_reason = reason
        trade.exit_detail = detail
        # `price` is the level that triggered the exit, expressed on the pricing
        # basis (mark price when mark pricing is on). Store the traded price
        # and the mark price of the same moment next to it.
        if trade.mark_price_basis:
            trade.exit_mark_price = float(mark_price_usd) if mark_price_usd else float(price)
            trade.exit_trade_price = float(trade_price_usd) if trade_price_usd else float(trade.exit_trade_price or price)
        else:
            trade.exit_trade_price = float(trade_price_usd) if trade_price_usd else float(price)
            trade.exit_mark_price = float(mark_price_usd) if mark_price_usd else float(trade.exit_mark_price or 0.0)
        return trade
