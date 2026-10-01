from dataclasses import dataclass, field
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
    # ---- trade-log detail (paper / live workers) ---------------------
    # The full entry-condition record for the trade: signal candle + colour,
    # the per-condition snapshot and the readable "value vs threshold ->
    # PASS/FAIL" breakdown. Filled by the paper / live workers from the
    # strategy's metadata (same builders as the backtest log); empty for
    # OMS-level trades nobody logs (e.g. manual terminal orders).
    entry_context: dict = field(default_factory=dict)
    # Colour of the candle the exit landed in (GREEN / RED / DOJI), stamped
    # by the worker when the position is closed.
    exit_candle_type: str = ""

def _flag(config, name, default=True):
    """A per-strategy switch, read defensively.

    Only the debug strategy configs declare ``use_stop_loss`` & friends; every
    other config (and every test stub) gets the default — True — so their
    behaviour is exactly the original one.
    """
    value = getattr(config, name, default)
    return default if value is None else bool(value)


class OrderManager:
    def __init__(self, config):
        self.config = config
        self.active_trades = {}

    # ------------------------------------------------------------------
    # Strategy hooks (no-ops for every strategy except those that override
    # them — see ``app/core/fast_test_v1.py``). Kept on the base class so the
    # engine and the paper / live workers can call them unconditionally.
    # ------------------------------------------------------------------
    def strategy_touch_exit(self, trade, seen_high, seen_low):
        """Touch-based exit rule; ``None`` = nothing fired.

        Evaluated after the candle's worst-case stop check and before the
        trailing / target steps, so a resting stop keeps priority over a
        strategy's own booking rule while the booking rule keeps priority over
        the trailing stop, the take profit and the holding-time timeout.
        """
        return None

    def strategy_bar_close_exit(self, trade, bar_close_usd, bar_time):
        """Close-based exit rule judged on a completed candle's close.

        ``None`` = nothing fired. ``(price, reason, detail)`` closes the trade
        at that close.
        """
        return None

    def strategy_audit_fields(self, trade, net_pnl_inr=None):
        """Extra audit fields recorded when the trade closes (``{}`` = none)."""
        return {}

    def strategy_summary(self, trades=None):
        """Strategy-specific run summary for the results payload (``{}`` = none)."""
        return {}

    def bracket_take_profit(self, direction, price_usd, planned_tp):
        """Take-profit level for the venue-side bracket order.

        Default: the plan's own TP, so live brackets are unchanged for every
        existing strategy. FastTest V1 overrides it with its +0.90% booking
        level so the exchange rests the target the strategy actually uses.
        A strategy whose take profit is switched OFF returns ``None``, which
        the venue adapters read as "no target leg".
        """
        if not _flag(self.config, 'use_take_profit'):
            return None
        return planned_tp

    def bracket_stop_loss(self, planned_sl):
        """Venue-side stop level, or ``None`` when the strategy turned it off."""
        if not _flag(self.config, 'use_stop_loss'):
            return None
        return planned_sl

    def bracket_trail_amount(self, trail_amount):
        """Venue-side trail distance, or ``None`` when it is switched off."""
        if not (_flag(self.config, 'use_trailing_stop') and _flag(self.config, 'use_stop_loss')):
            return None
        return trail_amount

    # ------------------------------------------------------------------
    # Risk & Exit model resolution (v3.6).
    #
    # Every level is resolved through the config so the engine, the paper and
    # live workers and any strategy subclass share ONE answer to "how far is
    # the stop / target / trail?". The config's resolvers know whether the
    # level is measured in ATR units (default) or as a % of price; the
    # fallbacks below keep a plain config object (a test stub, an old saved
    # dict) working with exactly the original ATR formulas.
    # ------------------------------------------------------------------
    def _stop_distance(self, direction, price_usd, atr_usd):
        fn = getattr(self.config, 'stop_loss_distance_for', None)
        if callable(fn):
            return float(fn(direction, price_usd, atr_usd))
        sl_atr = getattr(self.config, 'stop_loss_atr_for', None)
        val = sl_atr(direction) if callable(sl_atr) else getattr(self.config, 'stop_loss_atr', 2.0)
        dist = float(val) * float(atr_usd)
        floor = float(getattr(self.config, 'sl_floor_pct', 0.0) or 0.0) * float(price_usd)
        return max(dist, floor)   # SL floor: max(stop_loss_atr × ATR, sl_floor_pct × price)

    def _target_distance(self, direction, price_usd, atr_usd):
        fn = getattr(self.config, 'take_profit_distance_for', None)
        if callable(fn):
            return float(fn(direction, price_usd, atr_usd))
        return float(getattr(self.config, 'take_profit_atr', 0.0) or 0.0) * float(atr_usd)

    def _activation_distance(self, direction, price_usd, atr_usd):
        fn = getattr(self.config, 'trail_activation_distance_for', None)
        if callable(fn):
            return float(fn(direction, price_usd, atr_usd))
        return float(getattr(self.config, 'trail_activation_atr', 0.0) or 0.0) * float(atr_usd)

    def _trail_distance(self, reference_price_usd, current_atr_usd):
        fn = getattr(self.config, 'trail_distance_for', None)
        if callable(fn):
            return float(fn(reference_price_usd, current_atr_usd))
        return float(getattr(self.config, 'trail_distance_atr', 0.0) or 0.0) * float(current_atr_usd)

    def _breakeven_trigger(self, direction, entry_price, atr_at_entry):
        """Price at which the stop ratchets to entry; ``None`` = feature off."""
        fn = getattr(self.config, 'breakeven_trigger_for', None)
        if callable(fn):
            return fn(direction, entry_price, atr_at_entry)
        be = float(getattr(self.config, 'breakeven_atr', 0.0) or 0.0)
        if be <= 0:
            return None
        sign = 1.0 if int(direction) == 1 else -1.0
        return float(entry_price) + sign * be * float(atr_at_entry)

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
        
        # SL / TP / Trail Distances. Every level is asked of the config's Risk
        # & Exit model: ATR units (original behaviour, including the SL floor
        # max(stop_loss_atr × ATR, sl_floor_pct × price) and the direction
        # specific stop override) or a % of the entry price when the client
        # switched that level over.
        sl_dist = self._stop_distance(direction, price_usd, atr_usd)
        tp_dist = self._target_distance(direction, price_usd, atr_usd)
        act_dist = self._activation_distance(direction, price_usd, atr_usd)

        sl = price_usd - sl_dist if direction == 1 else price_usd + sl_dist
        tp = price_usd + tp_dist if direction == 1 else price_usd - tp_dist
        trail_act = price_usd + act_dist if direction == 1 else price_usd - act_dist
        
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
                     trade_price_usd=None, mark_price_usd=None, advance_bar=True,
                     bar_high_usd=None, bar_low_usd=None,
                     bar_close_usd=None, bar_time=None, strategy_bar_state=None):
        """Mark-to-market an open position and apply its stop/target rules.

        ``advance_bar`` controls the holding-time clock only. The backtest
        engine calls this once per candle, so it stays True there. The live and
        paper workers poll every 60 seconds — often dozens of times inside one
        1h candle — so they pass ``advance_bar=False`` until the candle actually
        rolls over, otherwise ``timeout_bars`` (72 candles = 3 days) would
        force-close a position after 72 *minutes*.

        ``bar_high_usd`` / ``bar_low_usd`` are the candle's extremes on the same
        pricing basis as ``current_price_usd``. The backtest engine passes them
        so a stop pierced INSIDE the candle triggers even when the close
        recovered — on the venue the resting stop order would have filled, and
        a backtest that quietly survives those candles reports profits live
        trading can never see. When both the stop and the target sit inside one
        candle the STOP fills (the sequence inside the bar is unknowable, so
        the worst case is booked, never the best). Live and paper tick with
        real-time prices and omit them; behaviour there is unchanged.

        ``bar_close_usd`` / ``bar_time`` describe the candle whose close is
        being judged (the completed candle in paper / live, the current candle
        in a backtest). They are only used by close-based strategy rules such
        as FastTest V1's 2h validation and the configurable debug exit
        conditions; every other strategy ignores them.

        ``strategy_bar_state`` carries that candle's indicator values
        (``{'rsi', 'macd_line', 'macd_signal'}``) for the debug strategies'
        signal-condition exits. It is exposed to the strategy hooks as
        ``self._bar_state``; other strategies pass nothing.
        """
        if symbol not in self.active_trades: return None
        trade = self.active_trades[symbol]
        # The candle's indicator values (RSI / MACD line / signal) for the
        # strategy's own exit conditions. Only the debug strategies configure
        # any, and only they ever send a state — every other strategy passes
        # None and skips the check entirely.
        self._bar_state = strategy_bar_state
        # Which protective rules this strategy runs. All True by default, so
        # an unedited strategy keeps the original plan bit for bit.
        use_stop = _flag(self.config, 'use_stop_loss')
        use_tp = _flag(self.config, 'use_take_profit')
        use_trail = _flag(self.config, 'use_trailing_stop')
        use_be = _flag(self.config, 'use_breakeven') and use_stop
        use_timeout = _flag(self.config, 'use_timeout')
        if advance_bar:
            trade.bars_held += 1
        trade.current_price = current_price_usd
        # The candle's reach on the pricing basis: how high and how low the
        # price actually went while this bar formed. Without candle extremes
        # they collapse to the tick price and nothing changes.
        seen_high = max(current_price_usd, bar_high_usd) if bar_high_usd is not None else current_price_usd
        seen_low = min(current_price_usd, bar_low_usd) if bar_low_usd is not None else current_price_usd
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
        # With candle extremes the price path INSIDE the bar is unknowable, so
        # every ambiguity is booked against the trade, never for it.
        intra_bar = bar_high_usd is not None or bar_low_usd is not None
        if trade.direction == 1:
            # 0. Worst case first (intra-candle only): did the low pierce the
            #    stop as it stood when the bar OPENED? The resting stop fills
            #    before any trail advance this bar's high might have earned —
            #    assuming the high came before the low is exactly the optimism
            #    this exists to kill.
            if intra_bar and use_stop:
                pre_trail = use_trail and trade.peak_price >= trade.trail_activation
                pre_stop = trade.trail_stop if pre_trail else trade.sl
                if seen_low <= pre_stop:
                    if pre_trail:
                        detail = (f"Trailing stop hit — price fell to {seen_low:,.2f} \u2264 trail {pre_stop:,.2f} "
                                  f"(peak {trade.peak_price:,.2f}, trail activated at {trade.trail_activation:,.2f})")
                    else:
                        be_note = " (at breakeven)" if trade.sl >= trade.entry_price else ""
                        detail = f"Stop loss hit — price fell to {seen_low:,.2f} \u2264 SL {pre_stop:,.2f}{be_note} (initial SL {trade.sl_entry:,.2f})"
                    return self.close_trade(symbol, pre_stop, timestamp, "TSL" if pre_trail else "SL", detail)

            # 0b. Strategy hook (FastTest V1 +0.90% booking). After the stop, so
            #     the resting stop keeps priority, and before the trail / target
            #     steps so a profit touch beats the trailing stop and the timeout.
            rule = self.strategy_touch_exit(trade, seen_high, seen_low)
            if rule is not None:
                price, reason, detail = rule
                return self.close_trade(symbol, price, timestamp, reason, detail,
                                        trade_price_usd=trade_price_usd,
                                        mark_price_usd=mark_price_usd)

            # 1. Update peak and activate trail
            trade.peak_price = max(trade.peak_price, seen_high)
            if use_trail and trade.peak_price >= trade.trail_activation:
                # Trail advances based on peak (ATR units, or the % of the peak
                # the client chose in the Risk & Exit model).
                new_tsl = trade.peak_price - self._trail_distance(trade.peak_price, current_atr_usd)
                trade.trail_stop = max(trade.trail_stop, new_tsl)

            # 1b. Breakeven stop (v3): once the favourable move reaches the
            # configured trigger (breakeven_atr × ATR, or breakeven_pct % of
            # entry), the hard stop can never lose money.
            if use_be:
                be = self._breakeven_trigger(trade.direction, trade.entry_price, trade.atr_at_entry)
                if be is not None and trade.peak_price >= be:
                    trade.sl = max(trade.sl, trade.entry_price)
                    if use_trail and trade.peak_price >= trade.trail_activation:
                        trade.trail_stop = max(trade.trail_stop, trade.entry_price)
            
            # 2. Check TSL / SL against the freshly-updated levels. Without
            #    extremes this is the tick price (legacy behaviour). With
            #    extremes only the CLOSE may be compared to the new trail: the
            #    close is the one price known to come after the high that
            #    advanced it. The bar's low was already handled in step 0.
            stop_ref = current_price_usd if intra_bar else seen_low
            trail_hit = use_trail and trade.peak_price >= trade.trail_activation
            stop_level = trade.trail_stop if trail_hit else trade.sl
            if use_stop and stop_ref <= stop_level:
                if trail_hit:
                    detail = (f"Trailing stop hit — price fell to {stop_ref:,.2f} ≤ trail {stop_level:,.2f} "
                              f"(peak {trade.peak_price:,.2f}, trail activated at {trade.trail_activation:,.2f})")
                else:
                    be_note = " (at breakeven)" if trade.sl >= trade.entry_price else ""
                    detail = f"Stop loss hit — price fell to {stop_ref:,.2f} ≤ SL {stop_level:,.2f}{be_note} (initial SL {trade.sl_entry:,.2f})"
                return self.close_trade(symbol, stop_level, timestamp, "TSL" if trail_hit else "SL", detail)

            # 3. Check TP — on the candle's HIGH: a resting venue TP order at
            #    that level would have filled the moment the bar touched it.
            if use_tp and seen_high >= trade.tp:
                detail = f"Take profit hit — price rose to {seen_high:,.2f} ≥ TP {trade.tp:,.2f}"
                return self.close_trade(symbol, trade.tp, timestamp, "TP", detail)
                
        else: # SHORT
            # 0. Worst case first (intra-candle only): the bar's HIGH against
            #    the stop as it stood at the open.
            if intra_bar and use_stop:
                pre_trail = use_trail and trade.peak_price <= trade.trail_activation
                pre_stop = trade.trail_stop if pre_trail else trade.sl
                if seen_high >= pre_stop:
                    if pre_trail:
                        detail = (f"Trailing stop hit — price rose to {seen_high:,.2f} ≥ trail {pre_stop:,.2f} "
                                  f"(low {trade.peak_price:,.2f}, trail activated at {trade.trail_activation:,.2f})")
                    else:
                        be_note = " (at breakeven)" if trade.sl <= trade.entry_price else ""
                        detail = f"Stop loss hit — price rose to {seen_high:,.2f} ≥ SL {pre_stop:,.2f}{be_note} (initial SL {trade.sl_entry:,.2f})"
                    return self.close_trade(symbol, pre_stop, timestamp, "TSL" if pre_trail else "SL", detail)

            # 0b. Strategy hook (FastTest V1 +0.90% booking) — see the long side.
            rule = self.strategy_touch_exit(trade, seen_high, seen_low)
            if rule is not None:
                price, reason, detail = rule
                return self.close_trade(symbol, price, timestamp, reason, detail,
                                        trade_price_usd=trade_price_usd,
                                        mark_price_usd=mark_price_usd)

            trade.peak_price = min(trade.peak_price, seen_low)
            if use_trail and trade.peak_price <= trade.trail_activation:
                new_tsl = trade.peak_price + self._trail_distance(trade.peak_price, current_atr_usd)
                trade.trail_stop = min(trade.trail_stop, new_tsl)

            # Breakeven stop (v3) for shorts
            if use_be:
                be = self._breakeven_trigger(trade.direction, trade.entry_price, trade.atr_at_entry)
                if be is not None and trade.peak_price <= be:
                    trade.sl = min(trade.sl, trade.entry_price)
                    if use_trail and trade.peak_price <= trade.trail_activation:
                        trade.trail_stop = min(trade.trail_stop, trade.entry_price)
                
            stop_ref = current_price_usd if intra_bar else seen_high
            trail_hit = use_trail and trade.peak_price <= trade.trail_activation
            stop_level = trade.trail_stop if trail_hit else trade.sl
            if use_stop and stop_ref >= stop_level:
                if trail_hit:
                    detail = (f"Trailing stop hit — price rose to {stop_ref:,.2f} ≥ trail {stop_level:,.2f} "
                              f"(low {trade.peak_price:,.2f}, trail activated at {trade.trail_activation:,.2f})")
                else:
                    be_note = " (at breakeven)" if trade.sl <= trade.entry_price else ""
                    detail = f"Stop loss hit — price rose to {stop_ref:,.2f} ≥ SL {stop_level:,.2f}{be_note} (initial SL {trade.sl_entry:,.2f})"
                return self.close_trade(symbol, stop_level, timestamp, "TSL" if trail_hit else "SL", detail)

            if use_tp and seen_low <= trade.tp:
                detail = f"Take profit hit — price fell to {seen_low:,.2f} ≤ TP {trade.tp:,.2f}"
                return self.close_trade(symbol, trade.tp, timestamp, "TP", detail)

        # Strategy hook (FastTest V1 2h validation): judged on a completed
        # candle's close, after every stop / target of that candle and before
        # the holding-time timeout.
        rule = self.strategy_bar_close_exit(trade, bar_close_usd, bar_time)
        if rule is not None:
            price, reason, detail = rule
            return self.close_trade(symbol, price, timestamp, reason, detail,
                                    trade_price_usd=trade_price_usd,
                                    mark_price_usd=mark_price_usd)

        if use_timeout and trade.bars_held >= self.config.timeout_bars:
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
