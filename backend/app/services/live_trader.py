import asyncio
import hashlib
import time
from typing import Dict
import pandas as pd
import numpy as np
from datetime import datetime
from app.core.strategy import StrategyService, PhantomV2Config, ValidatorService
from app.core.dynamic_strategy import DynamicStrategyService
from app.core.mark_price import MarkPriceService, perpetual_symbol
from app.core.trading_windows import TradingWindowConfig, TradingWindowGuard
from app.services.order_manager import OrderManager
from app.services.broker_client import BrokerClient
from app.services.heartbeat import DeadmanSwitch
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


def account_key(broker_name, api_key):
    """Identity of the venue account a set of credentials points at.

    Two live instances pointed at the same API key share ONE account, and a
    futures account carries a single netted position per contract — so they
    have to take turns. Two instances on different keys (different sub-accounts)
    are genuinely independent and must not block each other. This mirrors the
    rate limiter's own notion of an account.
    """
    digest = hashlib.sha1(str(api_key or "").encode()).hexdigest()[:12]
    return f"{broker_name}:{digest}"


class AccountCoordinator:
    """Which live instance currently holds the position on a shared account.

    Every worker keeps its own in-memory order book, but the venue does not:
    one account, one netted position per contract. Left to themselves, two
    instances on the same key either stack into a position neither of them
    sized correctly, or hedge to net zero while both books still report a live
    trade. This registry lets them queue instead — one position at a time, per
    account.
    """

    def __init__(self):
        self._members: Dict[str, Dict[str, "LiveTradeService"]] = {}

    def register(self, service):
        key = getattr(service, "account_id", None)
        if not key:
            return
        self._members.setdefault(key, {})[str(service.instance_key or id(service))] = service

    def unregister(self, service):
        key = getattr(service, "account_id", None)
        if not key:
            return
        bucket = self._members.get(key)
        if bucket:
            bucket.pop(str(service.instance_key or id(service)), None)
            if not bucket:
                self._members.pop(key, None)

    def siblings(self, service):
        """Other running instances on the same account."""
        key = getattr(service, "account_id", None)
        if not key:
            return []
        mine = str(service.instance_key or id(service))
        return [s for k, s in self._members.get(key, {}).items() if k != mine]

    def holder(self, service):
        """The sibling currently holding a position on this account, if any."""
        for sibling in self.siblings(service):
            if getattr(sibling, "oms", None) and sibling.oms.active_trades:
                return sibling
        return None

    def queue_position(self, service):
        """1-based place in line for this account (1 = it is this instance's turn)."""
        waiting = [s for s in self.siblings(service)
                   if getattr(s, "oms", None) and s.oms.active_trades]
        return len(waiting) + 1


# One registry per process. Live workers run as background tasks inside a
# single event loop, so plain dict access is enough here.
COORDINATOR = AccountCoordinator()


def extract_leg_order_ids(response):
    """Order ids of the stop-loss / take-profit legs inside a bracket response.

    Cancelling protection has to be scoped to *this* instance's legs. The
    account-wide ``cancel_all_orders`` wipes every resting order on the
    contract, including the stop-loss and take-profit another live instance is
    relying on — leaving that position running unprotected.
    """
    ids = []
    try:
        from app.services.broker_account import split_order_response
        for row, leg in split_order_response(response, ""):
            if leg not in ("stop_loss", "take_profit") or not isinstance(row, dict):
                continue
            for key in ("orderId", "id", "order_id", "clientOrderId", "client_order_id"):
                value = row.get(key)
                if value not in (None, ""):
                    ids.append(str(value))
                    break
    except Exception:
        return []
    return ids


_NOTHING_TO_REDUCE = (
    "reduceonly order is rejected", "reduce only order is rejected",
    "reduce-only order is rejected", "order would immediately trigger",
    "no position to reduce", "not enough position", "position not found",
    "reduce only reject", "reduceonly reject", "-2022",
)


def _is_nothing_to_reduce(response):
    """True when the venue refused a reduce-only order because there is no
    position left to flatten.

    Venues word this differently (Binance answers with code -2022, Delta with
    its own text), so the match is deliberately loose. Only ever applied to a
    response that already carries an ``error``.
    """
    if not isinstance(response, dict):
        return False
    message = str(response.get("error") or "").lower()
    if not message:
        return False
    return any(needle in message for needle in _NOTHING_TO_REDUCE)


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
                 user_id=None, instance_key=None, bracket_orders=True,
                 price_feed="off", tick_interval=5.0, account_label=None,
                 heartbeat=None):
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
        # Which venue account this worker sends orders to. Several strategies
        # can share one API key, and that account holds ONE netted position per
        # contract, so instances on the same key queue for it.
        self.account_id = account_key(broker_name or "", api_key)
        # Human-readable name of that account (the saved connection's label, or
        # "Primary"). With 3-4 runs on 3-4 sub-accounts this is the only way to
        # tell from the UI which instance is trading which account.
        self.account_label = account_label or "Primary"
        # Order ids of the stop-loss / take-profit legs this instance placed.
        # Exiting cancels exactly these and nothing else on the account.
        self.protection_leg_ids = []
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
        self._last_atr = None             # ATR of that candle, reused by fast ticks
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
        # ---- Live price feed ------------------------------------------
        # "off" keeps the original 60-second cadence exactly. "websocket" or
        # "rest" additionally re-checks open positions on every live price, so
        # a stop is acted on within seconds instead of up to a minute late.
        # Entries still wait for a closed 1h candle — only exits speed up.
        self.price_feed_mode = str(price_feed or "off").lower()
        self.tick_interval = float(tick_interval or 5.0)
        self.tick_feed = None
        self.fast_ticks = 0
        # ---- Deadman switch (Delta Exchange India) --------------------
        # Runs in parallel with the import/export flow. Default ON for Delta
        # (the client document flags it as a must-have) and OFF elsewhere.
        if heartbeat is None:
            heartbeat = str(self.broker_name or "").lower() == "delta" or (
                str(getattr(self.broker, "kind", "") or "").lower() == "delta")
        self.heartbeat_enabled = bool(heartbeat)
        self.heartbeat = None
        self.instrument = None
        self.wallet_balance = None
        self._last_closed_candle = None

    async def start(self):
        self.is_running = True
        COORDINATOR.register(self)
        print(f"🚀 LIVE Trading Started for {self.broker_name}/{self.strategy_id}")
        others = len(COORDINATOR.siblings(self))
        if others:
            print(f"   Shared account: {others} other live strateg{'y' if others == 1 else 'ies'} "
                  f"on this API key — one position at a time, they take turns")
        print(f"   Contract: {self.contract_symbol} perpetual · pricing basis: "
              f"{'MARK price' if self.use_mark_price else 'traded price'}")
        if self.window_guard.enabled:
            print("   Skip-new-trade windows: " + "; ".join(self.window_guard.describe()))
        else:
            print("   Skip-new-trade windows: off")
        print(f"   Entry policy: one order per signal candle · one position at a time · "
              f"{int(getattr(self.config, 'cooldown_bars', 0) or 0)}-candle cooldown after an exit")
        if getattr(self.broker, "testnet", False):
            print("   Environment: TESTNET (demo.delta.exchange / testnet.binancefuture.com)")
        # ① Products API warmup (tick size, margin, lot size) + wallet.
        self._warmup()
        # ⚠ Heartbeat / Deadman Switch — parallel to the whole flow.
        if self.heartbeat_enabled and self._is_delta():
            hid = f"phantom_{str(self.instance_key or self.strategy_id)[-24:]}"
            self.heartbeat = DeadmanSwitch(
                self.broker, hid, product_symbols=[self.contract_symbol])
            await self.heartbeat.start()
            print(f"   Deadman switch: ON · heartbeat {hid} · cancel_orders on 1 missed beat")
        else:
            print("   Deadman switch: off")
        try:
            if self.price_feed_mode == "off":
                while self.is_running:
                    try:
                        await self.tick()
                    except Exception as e:
                        print(f"Live Trade Error [{self.strategy_id}]: {e}")
                    await asyncio.sleep(60)
                return
            await self._run_with_feed()
        finally:
            if self.heartbeat is not None:
                await self.heartbeat.stop()

    async def _run_with_feed(self):
        """Candle tick every 60s, plus an exit check on every live price.

        The 60-second cadence still owns everything measured in candles —
        signals, entries, the holding-time clock, the post-exit cooldown. The
        fast loop only re-marks open positions, which is the one thing that
        genuinely needed to happen sooner than once a minute.
        """
        from app.services.tick_feed import build_tick_feed
        self.tick_feed = build_tick_feed(
            self.price_feed_mode, self.market_source, self.symbol,
            definition=self.definition, perpetual=self.contract_symbol,
            client=self.broker, interval=self.tick_interval)
        await self.tick_feed.start()
        print(f"   Live price feed: {self.tick_feed.kind} · exit checks every "
              f"{self.tick_interval:g}s (entries still wait for a closed 1h candle)")
        last_candle_tick = 0.0
        try:
            while self.is_running:
                now = time.monotonic()
                # A closed 1h candle on the WebSocket wakes the slow tick
                # immediately — that is the import flow the client asked for
                # (WS candlesticks, not REST polling).
                if self._consume_closed_candle() or now - last_candle_tick >= 60.0:
                    last_candle_tick = now
                    try:
                        await self.tick()
                    except Exception as e:
                        print(f"Live Trade Error [{self.strategy_id}]: {e}")
                else:
                    try:
                        await self.fast_tick()
                    except Exception as e:
                        print(f"Live fast-tick error [{self.strategy_id}]: {e}")
                await asyncio.sleep(self.tick_interval)
        finally:
            await self.tick_feed.stop()

    async def fast_tick(self):
        """Re-check open positions against the newest live price.

        Deliberately narrow: no candles, no signals, no entries. It costs no
        rate-limit weight on the websocket feed, and on the REST feed it reuses
        the same throttled client the slow tick uses.
        """
        if not self.oms.active_trades:
            return
        if self.tick_feed is None:
            return
        quote = self.tick_feed.quote()
        if quote is None:
            # No price fresh enough to trust. Better to act a little late on
            # the 60-second tick than to trade on a number nobody refreshed.
            return
        price = quote.basis_price
        if price is None or self._last_atr is None or self._last_bar_time is None:
            return
        self.fast_ticks += 1
        self.last_price = float(price)
        if quote.last_price:
            self.last_trade_price = float(quote.last_price)
        if quote.mark_price:
            self.last_mark_price = float(quote.mark_price)
        # advance_bar=False: the holding-time clock counts candles, and the
        # candle has not rolled over just because the price moved.
        self._manage_open_positions(float(price), self._last_atr, self._last_bar_time,
                                    quote.last_price or price, quote.mark_price, False)

    async def stop(self):
        self.is_running = False
        COORDINATOR.unregister(self)
        if self.heartbeat is not None:
            try:
                await self.heartbeat.stop()
            except Exception as exc:
                print(f"[{self.strategy_id}] heartbeat disable failed: {exc}")
        # Do not silently leave a real position unmanaged. In production an
        # operator can close it from the broker; this worker stops opening new
        # positions and the next status call remains visible until then.
        print(f"🔴 LIVE Trading Stopped for {self.broker_name}/{self.strategy_id}")

    def _is_delta(self):
        return (str(getattr(self.broker, "kind", "") or "").lower() == "delta"
                or str(self.broker_name or "").lower() == "delta")

    def _warmup(self):
        """Import flow ① Products API + wallet, once at start.

        Historical candles (②) are fetched on the first ``tick()``. The
        WebSocket candlesticks channel (③) is subscribed when the live
        price feed is websocket.
        """
        try:
            self.instrument = self.broker.get_instrument(self.symbol, refresh=True)
            tick = (self.instrument or {}).get("tick_size")
            cv = (self.instrument or {}).get("contract_value")
            print(f"   Products: {self.contract_symbol} · tick {tick} · "
                  f"contract_value {cv} {(self.instrument or {}).get('size_unit')}")
        except Exception as exc:
            print(f"[{self.strategy_id}] product warmup failed: {exc}")
        try:
            self.wallet_balance = self.broker.get_account_balance()
        except Exception as exc:
            print(f"[{self.strategy_id}] wallet warmup failed: {exc}")
            self.wallet_balance = None

    def _consume_closed_candle(self):
        """True once when the candlesticks channel publishes a new closed 1h bar."""
        feed = self.tick_feed
        if feed is None:
            return False
        candle = getattr(feed, "last_candle", None)
        if not isinstance(candle, dict) or not candle.get("closed"):
            return False
        key = candle.get("event_time") or candle.get("close")
        if key is None or key == self._last_closed_candle:
            return False
        self._last_closed_candle = key
        return True

    def _trail_amount(self, atr):
        """ATR trail distance sent on the Delta bracket stop-loss leg."""
        try:
            dist = float(getattr(self.config, "trail_distance_atr", 0) or 0)
        except (TypeError, ValueError):
            return None
        if dist <= 0 or not atr:
            return None
        return float(dist) * float(atr)

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
        # Remembered so the fast tick can re-mark a position without paying for
        # another candle fetch. The ATR only changes when the candle rolls over.
        self._last_atr = current_atr
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
        self._manage_open_positions(decision_price, current_atr, current_time,
                                    trade_price, mark_price, new_bar)

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
                size_in_btc=True, trail_amount=self._trail_amount(current_atr))
        else:
            res = self.broker.place_order(self.contract_symbol, side, "MARKET", lots,
                                          size_in_btc=True)
        self._record_order(res, leg="entry")
        if "error" not in res:
            # Remember exactly which protection legs belong to THIS instance so
            # the eventual exit cancels its own stops and not a sibling
            # instance's (they share one account and one order book).
            self.protection_leg_ids = extract_leg_order_ids(res)
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
    def _manage_open_positions(self, decision_price, current_atr, current_time,
                               trade_price, mark_price, advance_bar):
        """Mark every open position to market and send any exit it triggers.

        Split out of ``tick()`` so the same exit logic runs on the 60-second
        candle tick *and* on every live price tick. Exits are the part of the
        worker that is genuinely price-sensitive — a stop checked once a minute
        can be a minute late — while entries wait for a closed 1h candle either
        way, so re-reading the candles faster would only burn rate-limit weight.

        ``advance_bar`` must stay False on the fast path: it moves the
        holding-time clock, which is counted in candles.
        """
        for symbol in list(self.oms.active_trades.keys()):
            result = self.oms.update_trade(symbol, decision_price, current_atr, current_time,
                                           trade_price_usd=trade_price, mark_price_usd=mark_price,
                                           advance_bar=advance_bar)
            if not result:
                continue
            side = "SELL" if result.direction == 1 else "BUY"
            # reduce-only: this order must flatten, never open the other
            # side. Without it, a position that is already flat (a sibling
            # instance closed it, or the venue-side stop got there first)
            # turns this "exit" into a brand new position nobody manages.
            response = self.broker.place_order(self.contract_symbol, side, "MARKET",
                                               result.lots, size_in_btc=True,
                                               reduce_only=True)
            if isinstance(response, dict) and _is_nothing_to_reduce(response):
                # The venue has nothing left to reduce: the position is
                # already flat. `update_trade` has already settled the
                # local book, so all that remains is to clear this
                # instance's resting legs and restart the cooldown rather
                # than retrying a close the venue cannot accept.
                self._cancel_protection_legs()
                self._bars_since_exit = 0
                print(f"🔴 [{self.strategy_id}] LIVE {self.broker_name} close: "
                      f"{result.exit_reason} — venue was already flat "
                      f"(stop or another instance got there first); local book settled")
                continue
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
        holder = COORDINATOR.holder(self)
        pos = self.exchange_position
        if holder is not None:
            # Another strategy on the SAME API key is in a trade. One futures
            # account carries one netted position per contract, so entering now
            # would either stack onto that position at a size neither instance
            # sized, or hedge it towards zero while both books still report a
            # live trade. Wait for the sibling to close instead.
            side = "LONG" if pos and pos["direction"] == 1 else ("SHORT" if pos else "")
            queued = COORDINATOR.queue_position(self)
            detail = f" ({side} {pos['size_btc']:.4f} BTC)" if pos and side else ""
            return (f"'{holder.strategy_id}' holds this account's position{detail} — "
                    f"queued behind {queued - 1} other strateg{'y' if queued - 1 == 1 else 'ies'}")
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
                                           trade.lots, size_in_btc=True,
                                           reduce_only=True)
        if isinstance(response, dict) and _is_nothing_to_reduce(response):
            # The venue is already flat — its stop got there first, or another
            # instance closed it. There is nothing to flatten, so settle the
            # local book and let the opposite entry through. Refusing here
            # instead would wedge the worker permanently: the phantom trade
            # stays in the book, every later tick retries a close the venue
            # cannot accept, and no order is ever sent again.
            self._cancel_protection_legs()
            self.oms.close_trade("BTCUSDT", float(price), current_time, "REV",
                                 "Close & reverse — venue was already flat",
                                 trade_price_usd=trade_price, mark_price_usd=mark_price)
            self._bars_since_exit = 0
            self.last_order_error = None
            print(f"🔁 [{self.strategy_id}] LIVE close-&-reverse: venue was already "
                  f"flat (stop or another instance got there first); local book settled")
            return True
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
        """Cancel the reduce-only stop / target legs of a closed bracket.

        Scoped to the legs *this* instance placed. The account-wide
        ``cancel_all_orders`` would also pull the stop-loss and take-profit
        belonging to any other strategy running on the same API key, and that
        position would then be left with no exchange-side protection at all.
        """
        legs = list(self.protection_leg_ids or [])
        self.protection_leg_ids = []
        if not legs:
            # Nothing this instance placed is resting, so nothing is safe to
            # cancel: the remaining orders on the contract belong to someone
            # else (another instance, or the client's own terminal orders).
            return None
        cancelled, failed = [], []
        for order_id in legs:
            try:
                result = self.broker.cancel_order(order_id, symbol=self.symbol)
                if isinstance(result, dict) and result.get("error"):
                    # An already-filled or already-cancelled stop is fine;
                    # anything else means a leg may still be resting.
                    message = str(result.get("error"))
                    if any(word in message.lower() for word in
                           ("unknown order", "not exist", "order does not exist",
                            "already filled", "order is completed")):
                        continue
                    failed.append(f"{order_id}: {message}")
                else:
                    cancelled.append(order_id)
            except Exception as exc:
                failed.append(f"{order_id}: {exc}")
        if failed:
            self.last_order_error = f"protection legs still resting: {'; '.join(failed)}"
            print(f"⚠️ [{self.strategy_id}] LIVE protection legs NOT cancelled — "
                  f"{'; '.join(failed)}. This position may still be open on the venue.")
        return {"cancelled": cancelled, "failed": failed}

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
