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

    async def start(self):
        self.is_running = True
        print(f"🚀 LIVE Trading Started for {self.broker_name}/{self.strategy_id}")
        print(f"   Contract: {self.contract_symbol} perpetual · pricing basis: "
              f"{'MARK price' if self.use_mark_price else 'traded price'}")
        if self.window_guard.enabled:
            print("   Skip-new-trade windows: " + "; ".join(self.window_guard.describe()))
        else:
            print("   Skip-new-trade windows: off")
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

        for symbol in list(self.oms.active_trades.keys()):
            result = self.oms.update_trade(symbol, decision_price, current_atr, current_time,
                                           trade_price_usd=trade_price, mark_price_usd=mark_price)
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
                    exit_note = (f"mark {result.exit_mark_price:,.2f} (filled {result.exit_trade_price:,.2f})"
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

        if last_sig != 0:
            ind_slice = df_1h_with_ind.iloc[-50:]
            if self.validator.validate_signal(last_sig, current_price, current_price, ind_slice).passed:
                if blocked_window:
                    print(f"⏸ [{self.strategy_id}] LIVE entry skipped by trading window: {blocked_window.describe()}")
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
                    filled = extract_fill_price(res if not res.get("_bracket") else (res.get("entry") or res))
                    if filled:
                        planned.entry_trade_price = float(filled)
                    price_note = (f"mark {mark_price:,.2f} (filled {filled:,.2f})" if filled
                                  else (f"mark {mark_price:,.2f}" if use_mark else f"{current_price:,.2f}"))
                    protection = ""
                    if self.bracket_orders:
                        protection = (f" · SL {planned.sl:,.2f} / TP {planned.tp:,.2f}"
                                      f" ({'native bracket' if res.get('_bracket') and 'entry' not in res else 'bracket legs'})")
                    print(f"🚀 [{self.strategy_id}] LIVE {self.broker_name} opened: {side} at {price_note} ({lots} BTC){protection}")
                else:
                    # No order left the building: roll the OMS trade back so the
                    # local book does not drift away from the exchange.
                    self.oms.active_trades.pop("BTCUSDT", None)
                    self.last_order_error = res.get("error")
                    print(f"❌ [{self.strategy_id}] LIVE order failed: {res['error']}")

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
