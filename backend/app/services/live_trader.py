import asyncio
import pandas as pd
import numpy as np
from datetime import datetime
from app.core.strategy import StrategyService, PhantomV2Config, ValidatorService
from app.core.dynamic_strategy import DynamicStrategyService
from app.core.mark_price import MarkPriceService, perpetual_symbol
from app.core.trading_windows import TradingWindowConfig, TradingWindowGuard
from app.services.order_manager import OrderManager
from app.services.broker_client import BrokerClient
from app.database.models import SessionLocal, Klines
from app.core.indicators import compute_indicators


# Fill price keys seen across Binance / Delta order responses.
_FILL_PRICE_KEYS = ("avgPrice", "average_fill_price", "avg_fill_price", "price", "limit_price")


def price_note(mark_price=None, fill_price=None, reference_price=None, use_mark=False):
    """Human-readable price for a live log line.

    The mark price is simply *not available* sometimes (public endpoint down,
    venue without a premium-index call), so every branch here has to survive a
    ``None`` instead of blowing up mid-tick: an exception thrown while building
    a log string would abort the rest of the tick, after the order had already
    been sent to the exchange.
    """
    if use_mark and mark_price:
        base = f"mark {float(mark_price):,.2f}"
        return f"{base} (filled {float(fill_price):,.2f})" if fill_price else base
    if fill_price:
        return f"filled {float(fill_price):,.2f}"
    if mark_price:
        return f"mark {float(mark_price):,.2f}"
    if reference_price:
        return f"{float(reference_price):,.2f}"
    return "price unavailable"


# Signed size / entry price keys seen in Binance and Delta position payloads.
_SIZE_KEYS = ("positionAmt", "position_amt", "size", "quantity", "qty")
_ENTRY_KEYS = ("entryPrice", "entry_price", "avg_entry_price", "average_entry_price")


def parse_open_position(rows, contract_value: float = 1.0):
    """Read a venue's open-position payload into one plain description.

    Returns ``{"direction": ±1, "size_btc": float, "entry_price": float|None}``
    for the first non-flat position, or ``None`` when the account is flat (or
    the venue returned an error object). Used to stop the worker stacking a
    second order on top of a position the exchange already holds.
    """
    if not isinstance(rows, list):
        return None
    cv = float(contract_value or 1.0) or 1.0
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = next((row.get(k) for k in _SIZE_KEYS if row.get(k) not in (None, "")), None)
        try:
            size = float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            continue
        if not size:
            continue
        entry = next((row.get(k) for k in _ENTRY_KEYS if row.get(k) not in (None, "", "0")), None)
        try:
            entry_price = float(entry) if entry is not None else None
        except (TypeError, ValueError):
            entry_price = None
        return {
            "direction": 1 if size > 0 else -1,
            # Binance USDS-M reports BTC directly; Delta reports contracts.
            "size_btc": abs(size) * cv,
            "entry_price": entry_price,
        }
    return None


def extract_fill_price(response):
    """Best-effort average fill price from a broker order response.

    Nothing is guaranteed here: different venues (and market orders) report the
    fill differently, and a rejected order has none at all. ``None`` simply
    means "use the price we saw when the order was sent".
    """
    if not isinstance(response, dict):
        return None
    for key in _FILL_PRICE_KEYS:
        value = response.get(key)
        if value not in (None, "", "0", "0.0"):
            try:
                number = float(value)
                if number > 0:
                    return number
            except (TypeError, ValueError):
                continue
    result = response.get("result")
    if isinstance(result, dict):
        for key in _FILL_PRICE_KEYS:
            value = result.get(key)
            if value not in (None, "", "0", "0.0"):
                try:
                    number = float(value)
                    if number > 0:
                        return number
                except (TypeError, ValueError):
                    continue
    return None


class LiveTradeService:
    def __init__(self, strategy_id: str, config_or_rules, api_key: str, api_secret: str,
                 initial_capital=20000.0, margin_pct=25.0, is_custom=False,
                 broker_name="Binance", passphrase="", testnet=False, fee_schedule=None,
                 definition=None, trading_windows=None, use_mark_price=None,
                 user_id=None, instance_key=None, bracket_orders=True):
        self.strategy_id = strategy_id
        self.is_custom = is_custom
        self.market_source = broker_name or "Binance"
        self.broker_name = broker_name or "Binance"
        self.broker = BrokerClient(api_key, api_secret, self.broker_name, passphrase, testnet, definition)
        self.definition = definition
        self.fee_schedule = fee_schedule
        self.symbol = "BTCUSDT"
        # The BTC *perpetual* is the only contract this tool trades; orders go
        # out on the venue's perpetual symbol (BTCUSDT / BTCUSD).
        self.contract_symbol = self.broker.perpetual_symbol(self.symbol)
        if is_custom:
            self.rules = config_or_rules
            self.strategy = DynamicStrategyService(self.rules)
            self.config = PhantomV2Config()
        else:
            self.config = config_or_rules
            self.strategy = StrategyService(self.config)
        if fee_schedule:
            self.config.taker_fee_bps = float(getattr(fee_schedule, "taker_fee_bps", self.config.taker_fee_bps))
            self.config.maker_fee_bps = float(getattr(fee_schedule, "maker_fee_bps", self.config.maker_fee_bps))
        self.validator = ValidatorService()
        self.oms = OrderManager(self.config)
        self.is_running = False
        self.initial_capital = initial_capital
        self.margin_pct = margin_pct
        self.conversion_rate = 85.0
        # ---- BTC perpetual pricing (mark price) -----------------------
        if use_mark_price is not None:
            try:
                self.config.use_mark_price = bool(use_mark_price)
            except Exception:
                pass
        self.use_mark_price = bool(getattr(self.config, "use_mark_price", True))
        self.last_price = None          # pricing basis (mark when available)
        self.last_trade_price = None    # traded / last price
        self.last_mark_price = None
        self.mark_price_basis = False
        # ---- "Skip new trades" schedule -------------------------------
        if trading_windows is not None:
            windows = trading_windows if isinstance(trading_windows, TradingWindowConfig) \
                else TradingWindowConfig(**trading_windows)
            self.trading_windows = windows
            try:
                self.config.trading_windows = windows
            except Exception:
                pass
        else:
            self.trading_windows = getattr(self.config, "trading_windows", None) or TradingWindowConfig()
        self.window_guard = TradingWindowGuard(self.trading_windows)
        self.blocked_entries = 0
        self._last_block_notice = None
        self.last_checked = None
        # ---- Live order lifecycle ---------------------------------------
        # `user_id` + `instance_key` let every order and fill this instance
        # sends be mirrored into broker_orders / broker_fills for the terminal.
        self.user_id = user_id
        self.instance_key = instance_key
        # Entries go out as bracket orders (entry + stop-loss + take-profit) so
        # risk is protected exchange-side even if this worker dies mid-trade.
        self.bracket_orders = bool(bracket_orders)
        # Protection legs left over after a close must be cancelled, otherwise
        # a stale reduce-only stop can reopen the other side of the book.
        self.cancel_legs_on_exit = True
        self.last_order_error = None
        # ---- Entry gating: ONE order per signal candle -----------------
        # This worker polls every 60 seconds, but an entry condition (a custom
        # rule set especially) can stay TRUE for many 1h candles. Without these
        # guards every tick re-read the same signal and sent another real order,
        # so a single live run stacked a new position on the exchange once a
        # minute for as long as the signal held.
        self._last_bar_time = None        # candle this worker last processed
        self._acted_signal_bar = None     # candle whose signal already traded
        self._bars_since_exit = None      # candles since the last close
        self.skipped_entries = 0          # entries held back by the guards
        self.last_skip_reason = None
        self._last_skip_notice = None     # de-duplicates the "held back" log
        # A position already on the exchange (previous run, restart, or a manual
        # order placed in the terminal) must never be doubled up.
        self.sync_exchange_positions = True
        self.exchange_position = None
        self.exchange_position_known = True

    async def start(self):
        self.is_running = True
        print(f"🚀 LIVE Trading Started for {self.broker_name}/{self.strategy_id}")
        print(f"   Contract: {self.contract_symbol} perpetual · pricing basis: "
              f"{'MARK price' if self.use_mark_price else 'traded price'}")
        if self.window_guard.enabled:
            print("   Skip-new-trade windows: " + "; ".join(self.window_guard.describe()))
        else:
            print("   Skip-new-trade windows: off")
        print(f"   Entry policy: one order per signal candle · one position at a time · "
              f"{int(getattr(self.config, 'cooldown_bars', 0) or 0)}-candle cooldown after an exit")
        while self.is_running:
            try:
                await self.tick()
            except Exception as e:
                print(f"Live Trade Error [{self.strategy_id}]: {e}")
            await asyncio.sleep(60)

    async def stop(self):
        self.is_running = False
        # Do not silently leave a real position unmanaged. In production an
        # operator can close it from the broker; this worker stops opening new
        # positions and the next status call remains visible until then.
        print(f"🔴 LIVE Trading Stopped for {self.broker_name}/{self.strategy_id}")

    async def tick(self):
        df_1h = self._fetch_candles("1h", 100)
        df_4h = self._fetch_candles("4h", 100)
        if df_1h is None or df_4h is None or df_1h.empty or df_4h.empty:
            return
        ind_1h = compute_indicators(df_1h)
        df_1h_with_ind = df_1h.copy()
        for k, v in ind_1h.items():
            df_1h_with_ind[k] = v
        signals = self.strategy.generate_signals(df_1h_with_ind, df_4h)
        last_sig = signals[-1]
        # Traded price from the candle feed, mark price straight from the
        # exchange: risk runs on the mark price of the perpetual.
        current_price = float(df_1h['close'].iloc[-1])
        mark_quote = self._fetch_mark_price()
        mark_price = getattr(mark_quote, 'mark_price', None)
        trade_price = getattr(mark_quote, 'last_price', None) or current_price
        use_mark = bool(self.use_mark_price) and mark_price is not None
        decision_price = float(mark_price) if use_mark else float(current_price)
        current_atr = float(ind_1h['atr14'][-1])
        current_time = df_1h.index[-1]
        self.last_price = decision_price
        self.last_trade_price = float(trade_price)
        self.last_mark_price = float(mark_price) if mark_price is not None else None
        self.mark_price_basis = bool(use_mark)
        self.last_checked = datetime.utcnow().isoformat(timespec="seconds")

        # ---- Candle clock ---------------------------------------------
        # This worker polls every 60s, so one 1h candle is normally seen dozens
        # of times. Everything measured in *candles* — the holding-time clock,
        # one entry per signal, the post-exit cooldown — keys off this flag.
        new_bar = self._last_bar_time is None or current_time != self._last_bar_time
        if new_bar:
            self._last_bar_time = current_time
            if self._bars_since_exit is not None:
                self._bars_since_exit += 1

        # ---- Manage open positions ------------------------------------
        for symbol in list(self.oms.active_trades.keys()):
            result = self.oms.update_trade(symbol, decision_price, current_atr, current_time,
                                           trade_price_usd=trade_price, mark_price_usd=mark_price,
                                           advance_bar=new_bar)
            if result:
                side = "SELL" if result.direction == 1 else "BUY"
                response = self.broker.place_order(self.contract_symbol, side, "MARKET",
                                                   result.lots, size_in_btc=True)
                # Drop the stop-loss / take-profit legs the entry created; the
                # position they protected is already flat.
                if "error" not in response and self.cancel_legs_on_exit:
                    self._cancel_protection_legs()
                self._record_order(response, leg="exit")
                if "error" not in response:
                    filled = extract_fill_price(response)
                    if filled:
                        result.exit_trade_price = float(filled)
                    # Restart the post-exit cooldown in candles, not in ticks.
                    self._bars_since_exit = 0
                    exit_note = (price_note(result.exit_mark_price, result.exit_trade_price,
                                            result.exit_price, True)
                                 if result.mark_price_basis else f"{result.exit_price:,.2f}")
                    print(f"🔴 [{self.strategy_id}] LIVE {self.broker_name} close: {result.exit_reason} at {exit_note}")
                    if result.exit_detail:
                        print(f"   Exit condition: {result.exit_detail}")
                else:
                    self.last_order_error = response.get("error")
                    print(f"❌ [{self.strategy_id}] LIVE close failed: {response['error']}")

        # "Skip new trades" schedule. A position already open keeps being
        # managed above — only a NEW entry is refused.
        blocked_window = self.window_guard.blocking_window(current_time)
        if blocked_window:
            self.blocked_entries += 1
            if self._last_block_notice != blocked_window.describe():
                when = self.window_guard.next_open_from(current_time)
                print(f"⏸ [{self.strategy_id}] New entries paused ({blocked_window.describe()})"
                      + (f" — resume {when:%a %d %b %H:%M}" if when else ""))
                self._last_block_notice = blocked_window.describe()
        elif self._last_block_notice:
            self._last_block_notice = None

        # ---- Exchange-side reality check -------------------------------
        # Whatever the local book says, a position already on the venue (an
        # earlier run of this strategy, a worker restart, or a manual order sent
        # from the terminal) means no new entry may go out on top of it.
        if self.oms.active_trades:
            # We opened and manage it ourselves — nothing to reconcile.
            self.exchange_position, self.exchange_position_known = None, True
        elif self.sync_exchange_positions:
            self.exchange_position, self.exchange_position_known = self._read_exchange_position()

        # ---- New entries -----------------------------------------------
        if last_sig == 0:
            return
        if blocked_window:
            print(f"⏸ [{self.strategy_id}] LIVE entry skipped by trading window: {blocked_window.describe()}")
            return
        ind_slice = df_1h_with_ind.iloc[-50:]
        if not self.validator.validate_signal(last_sig, current_price, current_price, ind_slice).passed:
            return

        # One order per signal candle, one position at a time, and a cooldown
        # after an exit — the same three rules the backtest engine applies.
        hold = self._entry_hold_reason(last_sig, current_time)
        if hold:
            self.skipped_entries += 1
            self.last_skip_reason = hold
            notice = f"{current_time}::{hold}"
            if self._last_skip_notice != notice:
                print(f"⏸ [{self.strategy_id}] LIVE entry held back: {hold}")
                self._last_skip_notice = notice
            return
        self._last_skip_notice = None

        # Close-&-reverse (config ``allow_reverse``): flatten the open position
        # first, then take the opposite signal below. Skipping this step would
        # either ignore the configured behaviour or stack a second position.
        open_trade = self.oms.active_trades.get("BTCUSDT")
        if open_trade is not None and getattr(self.config, "allow_reverse", False) \
                and open_trade.direction != last_sig:
            if not self._close_for_reverse(open_trade, decision_price, trade_price,
                                           mark_price, current_time):
                return

        margin_inr = self.initial_capital * (self.margin_pct / 100.0)
        notional_usd = margin_inr / self.conversion_rate * self.config.leverage
        lots = float(np.floor((notional_usd / decision_price) / self.config.lot_size_btc) * self.config.lot_size_btc)
        if lots <= 0:
            return
        side = "BUY" if last_sig == 1 else "SELL"
        # Open the OMS trade first so the entry can be bracketed with
        # the same stop-loss / take-profit levels the strategy uses.
        planned = self.oms.create_order("BTCUSDT", last_sig, decision_price, current_atr, current_time,
                                        margin_inr, self.conversion_rate,
                                        trade_price_usd=trade_price,
                                        mark_price_usd=mark_price, mark_price_basis=use_mark)
        if planned is None:
            return
        lots = float(planned.lots)
        if self.bracket_orders:
            res = self.broker.place_bracket_order(
                self.contract_symbol, side, lots, price=None,
                stop_loss_price=float(planned.sl), take_profit_price=float(planned.tp),
                trigger_method="mark_price" if use_mark else "last_traded_price",
                size_in_btc=True)
        else:
            res = self.broker.place_order(self.contract_symbol, side, "MARKET", lots,
                                          size_in_btc=True)
        self._record_order(res, leg="entry")
        if "error" not in res:
            # This candle's signal is spent: the remaining ticks of the same
            # 1h candle must not send another order.
            self._acted_signal_bar = current_time
            filled = extract_fill_price(res if not res.get("_bracket") else (res.get("entry") or res))
            if filled:
                planned.entry_trade_price = float(filled)
            entry_note = price_note(mark_price, filled, current_price, use_mark)
            protection = ""
            if self.bracket_orders:
                protection = (f" · SL {planned.sl:,.2f} / TP {planned.tp:,.2f}"
                              f" ({'native bracket' if res.get('_bracket') and 'entry' not in res else 'bracket legs'})")
            print(f"🚀 [{self.strategy_id}] LIVE {self.broker_name} opened: {side} at {entry_note} ({lots} BTC){protection}")
        else:
            # No order left the building: roll the OMS trade back so the
            # local book does not drift away from the exchange.
            self.oms.active_trades.pop("BTCUSDT", None)
            self.last_order_error = res.get("error")
            print(f"❌ [{self.strategy_id}] LIVE order failed: {res['error']}")

    # ------------------------------------------------------------------
    # Entry gating
    # ------------------------------------------------------------------
    def _entry_hold_reason(self, signal, current_time):
        """Why no new order may go out this tick; ``None`` means go ahead.

        A signal can stay TRUE for many 1h candles and this worker ticks every
        60 seconds, so without these checks the same signal would fire a fresh
        order once a minute for as long as the condition held.
        """
        if self._acted_signal_bar is not None and current_time == self._acted_signal_bar:
            return f"the signal on this candle ({current_time}) was already traded"
        open_trade = self.oms.active_trades.get("BTCUSDT")
        if open_trade is not None:
            if getattr(self.config, "allow_reverse", False) and open_trade.direction != signal:
                # Configured close-&-reverse: the caller flattens first and then
                # opens the other side in the same tick.
                return None
            if getattr(self.config, "allow_overlap", False):
                # The worker can only manage one position per contract, so the
                # documented backtest "overlap" mode is refused here rather than
                # leaving an unmanaged position on the exchange.
                return ("overlapping entries are not supported live — this worker "
                        "manages one position per contract")
            side = "LONG" if open_trade.direction == 1 else "SHORT"
            return (f"position already open ({side} {open_trade.lots:.4f} BTC) — "
                    f"waiting for it to close")
        if not self.exchange_position_known:
            return (f"could not read the open position on {self.broker_name} — "
                    f"entry held until the venue can be read")
        pos = self.exchange_position
        if pos:
            side = "LONG" if pos["direction"] == 1 else "SHORT"
            return (f"{self.broker_name} already holds a position "
                    f"({side} {pos['size_btc']:.4f} BTC) that this instance did not open")
        cooldown = int(getattr(self.config, "cooldown_bars", 0) or 0)
        if self._bars_since_exit is not None and self._bars_since_exit <= cooldown:
            return (f"post-exit cooldown — {self._bars_since_exit}/{cooldown} "
                    f"candles since the last close")
        return None

    def _read_exchange_position(self):
        """Position the venue holds for this contract.

        Returns ``(position, known)``. ``known`` is False when the venue could
        not be read at all, and the worker then holds new entries instead of
        risking a second order on a position it cannot see.
        """
        try:
            rows = self.broker.get_positions(self.symbol)
            if isinstance(rows, dict) and rows.get("error"):
                print(f"[{self.strategy_id}] position check failed: {rows['error']}")
                return None, False
            instrument = self.broker.get_instrument(self.symbol) or {}
            contract_value = float(instrument.get("contract_value") or 1.0) or 1.0
            return parse_open_position(rows, contract_value), True
        except Exception as exc:
            print(f"[{self.strategy_id}] position check failed: {exc}")
            return None, False

    def _close_for_reverse(self, trade, price, trade_price, mark_price, current_time):
        """Flatten the open position so the opposite signal can be taken."""
        side = "SELL" if trade.direction == 1 else "BUY"
        response = self.broker.place_order(self.contract_symbol, side, "MARKET",
                                           trade.lots, size_in_btc=True)
        if "error" not in response and self.cancel_legs_on_exit:
            self._cancel_protection_legs()
        self._record_order(response, leg="exit")
        if "error" in response:
            # The old position is still on: never open the other side on top.
            self.last_order_error = response.get("error")
            print(f"❌ [{self.strategy_id}] LIVE close-&-reverse failed: {response['error']} — entry not sent")
            return False
        self.oms.close_trade("BTCUSDT", float(price), current_time, "REV",
                             "Close & reverse — opposite signal",
                             trade_price_usd=trade_price, mark_price_usd=mark_price)
        self._bars_since_exit = 0
        print(f"🔁 [{self.strategy_id}] LIVE close-&-reverse at {float(price):,.2f}")
        return True

    # ------------------------------------------------------------------
    # Local audit trail + bracket cleanup
    # ------------------------------------------------------------------
    def _record_order(self, response, leg="entry"):
        """Mirror a broker order (and any fills) into the local tables."""
        if not self.user_id or not isinstance(response, dict) or response.get("error"):
            return None
        try:
            from app.services.broker_account import (normalize_fill, normalize_order,
                                                     record_fills, record_order,
                                                     split_order_response)
            instrument = self.broker.get_instrument(self.symbol) or {}
            contract_value = float(instrument.get("contract_value") or 1.0) or 1.0
            code = str(self.broker_name)
            written = None
            parent_id = None
            for row, row_leg in split_order_response(response, code):
                if not isinstance(row, dict) or row.get("error"):
                    continue
                order = normalize_order(row, code, contract_value)
                if order.get("error"):
                    continue
                order["symbol"] = self.contract_symbol
                written = record_order(self.user_id, code, order, source="strategy",
                                       instance_key=self.instance_key, leg=row_leg,
                                       parent_order_id=parent_id, raw=row)
                if row_leg == "entry":
                    parent_id = order.get("order_id")
            try:
                fills = self.broker.get_fills(self.symbol, limit=10)
                if isinstance(fills, list):
                    record_fills(self.user_id, code,
                                 [normalize_fill(f, code, contract_value) for f in fills],
                                 source="strategy", instance_key=self.instance_key)
            except Exception:
                pass
            return written
        except Exception as exc:
            print(f"[{self.strategy_id}] could not record order: {exc}")
            return None

    def _cancel_protection_legs(self):
        """Cancel the reduce-only stop / target legs of a closed bracket."""
        try:
            return self.broker.cancel_all_orders(self.symbol)
        except Exception as exc:
            print(f"[{self.strategy_id}] could not cancel protection legs: {exc}")
            return None

    def _fetch_mark_price(self):
        """Current mark price of the BTC perpetual; ``None`` when unavailable."""
        try:
            return MarkPriceService.current(self.market_source, self.symbol,
                                            definition=self.definition, client=self.broker)
        except Exception as exc:
            print(f"[{self.strategy_id}] mark price fetch failed: {exc}")
            return None

    def _fetch_candles(self, interval, limit):
        # Live mode prefers the selected broker's current feed. Seeded data is
        # an offline fallback, useful for tests and when a public endpoint is down.
        try:
            rows = self.broker.fetch_klines("BTCUSDT", interval, limit)
            df = pd.DataFrame(rows)
            if not df.empty:
                df.set_index('event_time', inplace=True)
                return df.sort_index()
        except Exception:
            pass
        try:
            db = SessionLocal()
            rows = db.query(Klines).filter(Klines.symbol == "BTCUSDT", Klines.interval == interval,
                                           Klines.source == self.market_source).order_by(Klines.event_time.desc()).limit(limit).all()
            db.close()
            df = pd.DataFrame([{'event_time': k.event_time, 'open': k.open, 'high': k.high,
                                'low': k.low, 'close': k.close, 'volume': k.volume} for k in rows])
            if df.empty:
                return df
            df.set_index('event_time', inplace=True)
            return df.sort_index()
        except Exception:
            return None
