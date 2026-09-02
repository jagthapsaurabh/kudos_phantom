import asyncio
import os
import time
import traceback
import pandas as pd
from datetime import datetime, timezone, timedelta
from app.core.strategy import StrategyService, PhantomV2Config, ValidatorService
from app.core.dynamic_strategy import DynamicStrategyService
from app.core.mark_price import MarkPriceService, perpetual_symbol
from app.core.trading_windows import TradingWindowConfig, TradingWindowGuard
from app.services.order_manager import OrderManager
from app.services.broker_client import BrokerClient
import requests
from app.core.indicators import compute_indicators

# India Standard Time is UTC+5:30. All timestamps shown in the paper-trade UI
# (last checked, trade entry/exit, log lines) are emitted in IST so the user
# sees local India time without any client-side conversion.
IST = timezone(timedelta(hours=5, minutes=30))


def _ist_now() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _to_ist(value) -> str:
    """Convert a (possibly naive-UTC) datetime to an IST-offset ISO string."""
    if isinstance(value, datetime):
        naive = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return naive.astimezone(IST).isoformat(timespec="seconds")
    return str(value)


class PaperTradeService:
    MAX_LOG_LINES = 200  # keep last N log entries per instance
    MAX_CLOSED_TRADES = 100  # keep last N closed trades per instance
    MAX_EQUITY_POINTS = 5000  # equity-curve resolution kept in memory/DB
    PERSIST_EVERY_TICKS = 5  # quiet ticks between database snapshots (minutes)
    MAX_RESTARTS = 5  # consecutive crashes before the session is marked failed

    def __init__(self, strategy_id: str, config_or_rules, initial_capital=20000.0, margin_pct=25.0,
                 is_custom=False, market_source="Delta", broker_name=None, fee_schedule=None,
                 broker_definition=None, strategy_name=None, user_id=None,
                 trading_windows=None, use_mark_price=None,
                 price_feed="off", tick_interval=5.0, testnet=False,
                 connection_id=None, account_label=None, leverage=None):
        self.strategy_id = str(strategy_id)
        # Custom strategies are identified by a numeric id internally. Keep a
        # human-readable name on the worker so every status/list view can show
        # what the user actually selected.
        self.strategy_name = strategy_name or self.strategy_id
        self.is_custom = is_custom
        self.created_at = _ist_now()
        self.symbol = "BTCUSDT"
        self.user_id = user_id
        self.market_source = market_source or "Delta"
        self.broker_name = broker_name or self.market_source
        self.broker_definition = broker_definition
        self.fee_schedule = fee_schedule
        self.initial_capital_inr = initial_capital
        # Which saved broker connection (sub-account) this paper run mirrors.
        # Paper trading places no orders, but it must be sized and reported
        # against a *specific* account so its result is comparable with the
        # live run on the same account — and so the one-strategy-per-account
        # rule can be applied identically in both modes.
        self.connection_id = connection_id
        self.account_label = account_label or "Primary"

        if is_custom:
            # config_or_rules is a list of rules
            self.rules = config_or_rules
            self.strategy = DynamicStrategyService(self.rules)
            # Use default PhantomV2Config for OMS and Validator
            self.config = PhantomV2Config()
        else:
            # config_or_rules is a PhantomV2Config object
            self.config = config_or_rules
            self.strategy = StrategyService(self.config)

        # Per-instance leverage override (the client sets it on the start form).
        if leverage is not None:
            try:
                self.config.leverage = int(leverage)
            except (TypeError, ValueError):
                pass
        self.validator = ValidatorService()
        self.oms = OrderManager(self.config)
        self.is_running = False
        # Set by the start endpoint while the background task has not yet run,
        # so a double-clicked Start cannot register a second worker.
        self.pending_start = False
        self.equity_inr = initial_capital
        self.margin_pct = margin_pct
        # USD->INR: admin-saved setting -> USD_INR_RATE env -> default.
        from app.services.app_settings import get_usd_inr_rate
        self.conversion_rate = get_usd_inr_rate()
        if fee_schedule:
            self.config.taker_fee_bps = float(getattr(fee_schedule, "taker_fee_bps", self.config.taker_fee_bps))
            self.config.maker_fee_bps = float(getattr(fee_schedule, "maker_fee_bps", self.config.maker_fee_bps))
        # ---- BTC perpetual pricing (mark price) ----------------------
        # `last_price` is the pricing basis (mark price when mark pricing is
        # on); `last_trade_price` is the traded price shown next to it.
        if use_mark_price is not None:
            try:
                self.config.use_mark_price = bool(use_mark_price)
            except Exception:
                pass
        self.use_mark_price = bool(getattr(self.config, "use_mark_price", True))
        self.last_price = None          # pricing basis (mark when available)
        self.last_trade_price = None    # traded / last price
        self.last_mark_price = None     # exchange mark price
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
        self._last_block_notice = None   # de-duplicates "entries paused" logs
        # ---- Entry gating: ONE entry per signal candle ----------------
        # The worker ticks every 60s while an entry condition can stay TRUE for
        # many 1h candles. Without these guards every tick re-read the same
        # signal and replaced the open trade, so a session churned a "new"
        # position every minute and its equity curve meant nothing.
        self._last_bar_time = None        # candle this worker last processed
        self._acted_signal_bar = None     # candle whose signal already traded
        self._bars_since_exit = None      # candles since the last close
        self.skipped_entries = 0          # entries held back by the guards
        self.last_skip_reason = None
        # Data freshness: same rule as the live worker — a candle set older
        # than this opens no NEW simulated trades (None disables, for tests).
        self.max_candle_age_hours = 3.0
        self.candles_stale = False
        self._stale_notice = None
        self._last_skip_notice = None     # de-duplicates the "held back" log
        self.last_checked = None
        self._last_atr = None             # ATR of the last 1h candle, reused by fast ticks
        # ---- Live price feed ------------------------------------------
        # Same as live trading: "off" keeps the 60-second cadence; websocket
        # or rest re-checks open positions on every live price so a paper
        # stop is acted on in seconds, not up to a minute late. Entries still
        # wait for a closed 1h candle.
        self.price_feed_mode = str(price_feed or "off").lower()
        self.tick_interval = float(tick_interval or 5.0)
        self.tick_feed = None
        self.fast_ticks = 0
        self._last_closed_candle = None
        self.testnet = bool(testnet)
        # Live log buffer: list of {"ts": ISO, "level": "info|warn|error|trade", "msg": str}
        self.logs: list = []
        # Closed-trade history: list of trade dicts
        self.closed_trades: list = []
        # Equity curve: list of {"ts": ISO-IST, "equity": float}. Sampled once
        # per tick and on every fill so the saved session can be reviewed (and
        # charted) after the instance is stopped.
        self.equity_history: list = [{"ts": self.created_at, "equity": float(initial_capital)}]
        # Persistence bookkeeping (see app.services.paper_history). The API
        # fills both in when the instance is registered.
        self.instance_key = None
        self.session_id = None
        self.session_mode = "paper"
        self.dropped_trade_count = 0
        self.dropped_trade_pnl = 0.0
        self.history_status = "running"
        self._tick_count = 0
        # Why a session is no longer running, and the last error the loop saw.
        # Surfaced in the status API and History so "Interrupted" is never the
        # only thing the user is told.
        self.stop_reason = None
        self.last_error = None
        self.restarts = 0
        # Set when a session is rebuilt from a saved row after a restart.
        self.resumed_from_session = None
        # Keep this session alive across a server restart (see the startup
        # resume pass in app.main). Set by the API from the start payload.
        self.auto_resume = True
        # Cached broker client reused across ticks. Creating a new BrokerClient
        # on every _fetch_candles call allocates a rate limiter, normalises URLs
        # and does other bookkeeping — for a fast tick loop that is pure waste.
        self._broker_client = BrokerClient(broker_name=self.market_source, definition=self.broker_definition)
        self._log("info", f"Instance initialised — strategy={self.strategy_name}, capital=₹{initial_capital:,.0f}, margin={margin_pct}%")
        # The BTC perpetual is the only contract this tool trades; say which
        # one the venue uses and which price the maths runs on.
        self._log("info", f"Contract: {perpetual_symbol(self.market_source, self.symbol)} perpetual on {self.market_source}"
                          f" — pricing basis: {'MARK price' if self.use_mark_price else 'traded price'}")
        if self.window_guard.enabled:
            self._log("info", "Skip-new-trade windows ON — " + "; ".join(self.window_guard.describe()))
        else:
            self._log("info", "Skip-new-trade windows OFF — new entries allowed at any time")

    async def _sleep_responsive(self, seconds: float, step: float = 1.0):
        """Sleep, but wake up as soon as the session is stopped.

        A flat ``asyncio.sleep(60)`` meant a Stop could sit unacknowledged for
        up to a minute (and a shutdown could be cut off mid-sleep, which is one
        of the ways a session ended up flagged interrupted). Polling the flag
        in short steps makes stopping immediate.
        """
        waited = 0.0
        while waited < seconds and self.is_running:
            await asyncio.sleep(min(step, seconds - waited))
            waited += step

    def _log(self, level: str, msg: str):
        entry = {"ts": _ist_now(), "level": level, "msg": msg}
        self.logs.append(entry)
        if len(self.logs) > self.MAX_LOG_LINES:
            self.logs = self.logs[-self.MAX_LOG_LINES:]

    def _record_closed(self, trade, pnl_inr, fees_inr=0.0, gross_pnl=None):
        """Append a closed trade to the instance history.

        Entry/exit times are the candle timestamps (naive UTC) which are
        converted to IST so the UI always shows India time.
        """
        # Coerce to plain Python types: ATR-derived values are numpy scalars
        # (np.float64 serializes as float, but comparisons yield np.bool_ which
        # FastAPI cannot encode — keep every numeric field a real float).
        f = lambda v: None if v is None else float(v)
        self.closed_trades.append({
            "symbol": trade.symbol,
            "direction": int(trade.direction),
            "entry": f(trade.entry_price),
            "exit": f(trade.exit_price),
            # BTC perpetual: the traded price and the exchange mark price are
            # both stored; `entry`/`exit` are the pricing basis used for PnL.
            "entry_trade_price": f(getattr(trade, "entry_trade_price", None)),
            "exit_trade_price": f(getattr(trade, "exit_trade_price", None)),
            "entry_mark_price": f(getattr(trade, "entry_mark_price", None)),
            "exit_mark_price": f(getattr(trade, "exit_mark_price", None)),
            "mark_price_basis": bool(getattr(trade, "mark_price_basis", False)),
            "pnl": f(pnl_inr),
            "gross_pnl": f(gross_pnl) if gross_pnl is not None else f(pnl_inr),
            "fees": f(fees_inr),
            "reason": trade.exit_reason,
            "exit_detail": getattr(trade, "exit_detail", "") or "",
            # Stop plan at entry vs the levels actually in force at exit.
            "sl": f(getattr(trade, "sl_entry", trade.sl)),
            "sl_final": f(trade.sl),
            "tp": f(trade.tp),
            "trail_stop": f(trade.trail_stop),
            "trail_activation": f(trade.trail_activation),
            "atr_at_entry": f(trade.atr_at_entry),
            "peak_price": f(trade.peak_price),
            "margin_inr": f(trade.margin_inr),
            "notional_usd": f(trade.notional_usd),
            "lots": f(trade.lots),
            "entry_time": _to_ist(trade.entry_time),
            "exit_time": _to_ist(trade.exit_time),
            "bars_held": int(trade.bars_held),
        })
        # Same accounting rule as the live worker: trades age out of memory,
        # but their PnL must not vanish from the session totals.
        if len(self.closed_trades) > self.MAX_CLOSED_TRADES:
            dropped = self.closed_trades[:-self.MAX_CLOSED_TRADES]
            self.dropped_trade_count += len(dropped)
            self.dropped_trade_pnl += sum(float(t.get("pnl") or 0.0) for t in dropped)
            self.closed_trades = self.closed_trades[-self.MAX_CLOSED_TRADES:]

    def _record_equity_point(self):
        """Append one equity-curve sample (IST timestamp, equity in ₹)."""
        self.equity_history.append({"ts": _ist_now(), "equity": float(self.equity_inr)})
        if len(self.equity_history) > self.MAX_EQUITY_POINTS:
            self.equity_history = self.equity_history[-self.MAX_EQUITY_POINTS:]

    def _persist_history(self, force=False):
        """Mirror the session into the paper_sessions table.

        Writes on every trade event and every ``PERSIST_EVERY_TICKS`` quiet
        ticks, so the History view is current without hammering the database
        once a minute. Never raises — bookkeeping must not kill the loop.
        """
        if not self.session_id:
            return
        if not (force or self._tick_count % self.PERSIST_EVERY_TICKS == 0):
            return
        try:
            from app.services.paper_history import persist_snapshot
            persist_snapshot(self.instance_key, self)
        except Exception as exc:
            print(f"[{self.strategy_id}] History snapshot failed: {exc}")

    async def start(self):
        """Supervised entry point for the worker.

        Everything below this method used to be able to kill a session for
        good: one unhandled exception outside the per-tick ``try`` (a bad
        import, a dead websocket, a NameError) ended the background task while
        ``is_running`` stayed True and the saved row stayed ``running`` — which
        is exactly what surfaced later as "Interrupted". The loop body is now
        supervised: it is restarted with a backoff, the reason is logged and
        persisted, and only a genuine stop (or too many failures in a row)
        ends the session.
        """
        self.is_running = True
        self.pending_start = False
        self.stop_reason = None
        self._log("info", f"🟢 Paper trading started — strategy={self.strategy_id}")
        print(f"🟢 Paper Trading Started for Strategy: {self.strategy_id}")
        failures = 0
        while self.is_running:
            try:
                await self._run_loop()
                # A clean return means the user stopped it.
                return
            except asyncio.CancelledError:
                self.stop_reason = "cancelled"
                raise
            except Exception as exc:
                failures += 1
                self.restarts += 1
                detail = f"{exc.__class__.__name__}: {exc}"
                self.last_error = detail
                self._log("error", f"Worker crashed ({detail}) — restart {failures}/{self.MAX_RESTARTS}")
                print(f"[{self.strategy_id}] paper worker crashed: {detail}\n"
                      f"{traceback.format_exc()}")
                self._persist_history(force=True)
                if failures >= self.MAX_RESTARTS:
                    self.is_running = False
                    self.stop_reason = f"stopped after {failures} consecutive failures: {detail}"
                    self._log("error", f"🔴 Giving up — {self.stop_reason}")
                    try:
                        from app.services.paper_history import finalize_session, STATUS_FAILED
                        finalize_session(self.instance_key, self, STATUS_FAILED)
                    except Exception:
                        pass
                    return
                await asyncio.sleep(min(60.0, 5.0 * failures))
                # A restart that survives one full tick resets the counter.
                self._log("info", "Restarting the worker loop…")

    async def _run_loop(self):
        """The actual trading loop (candle cadence, or candle + live ticks)."""
        if self.price_feed_mode != "off":
            self._log("info", f"Live ticks ON ({self.price_feed_mode}) — exits every "
                              f"{self.tick_interval:g}s; entries still wait for a closed 1h candle")
            await self._run_with_feed()
            return
        while self.is_running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = f"{e.__class__.__name__}: {e}"
                self._log("error", f"Tick error: {e}")
                print(f"Paper Trade Error [{self.strategy_id}]: {e}")
            await self._sleep_responsive(60)  # Check every minute

    async def _run_with_feed(self):
        """Candle tick every 60s, plus an exit check on every live price."""
        from app.services.tick_feed import build_tick_feed
        try:
            feed_client = BrokerClient(broker_name=self.market_source,
                                       definition=self.broker_definition,
                                       testnet=self.testnet)
            self.tick_feed = build_tick_feed(
                self.price_feed_mode, self.market_source, self.symbol,
                definition=self.broker_definition,
                perpetual=perpetual_symbol(self.market_source, self.symbol),
                client=feed_client, interval=self.tick_interval)
            await self.tick_feed.start()
        except Exception as exc:
            # A feed that will not start must degrade to the 60-second cadence,
            # not end the session. The badge in the UI shows the mode is off.
            self.tick_feed = None
            self.price_feed_mode = "off"
            self.last_error = f"price feed unavailable: {exc}"
            self._log("warn", f"Live tick feed could not start ({exc}) — "
                              "falling back to the 60-second candle cadence")
            while self.is_running:
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.last_error = f"{e.__class__.__name__}: {e}"
                    self._log("error", f"Tick error: {e}")
                await self._sleep_responsive(60)
            return
        last_candle_tick = 0.0
        try:
            while self.is_running:
                now = time.monotonic()
                if self._consume_closed_candle() or now - last_candle_tick >= 60.0:
                    last_candle_tick = now
                    try:
                        await self.tick()
                    except Exception as e:
                        self._log("error", f"Tick error: {e}")
                        print(f"Paper Trade Error [{self.strategy_id}]: {e}")
                else:
                    try:
                        await self.fast_tick()
                    except Exception as e:
                        self._log("error", f"Fast-tick error: {e}")
                        print(f"Paper fast-tick error [{self.strategy_id}]: {e}")
                await self._sleep_responsive(self.tick_interval, step=min(1.0, self.tick_interval))
        finally:
            if self.tick_feed is not None:
                await self.tick_feed.stop()

    def _consume_closed_candle(self):
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

    async def fast_tick(self):
        """Re-check open paper positions against the newest live price.

        No candles, no signals, no entries — the same narrow exit path the
        live worker uses. A stale quote is ignored.
        """
        if not self.oms.active_trades or self.tick_feed is None:
            return
        quote = self.tick_feed.quote()
        if quote is None:
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
        closed = self._manage_open_positions(
            float(price), self._last_atr, self._last_bar_time,
            quote.last_price or price, quote.mark_price, False)
        if closed:
            self._record_equity_point()
            self._persist_history(force=True)

    async def stop(self, reason="stopped by user"):
        self.is_running = False
        self.pending_start = False
        self.stop_reason = reason
        if self.tick_feed is not None:
            try:
                await self.tick_feed.stop()
            except Exception:
                pass
        self._log("info", "🔴 Paper trading stopped by user — results saved to History")
        print(f"🔴 Paper Trading Stopped for Strategy: {self.strategy_id}")
        # Final snapshot so the saved session holds the closing equity, the
        # closed trades and the positions that were still open.
        self._persist_history(force=True)

    async def tick(self):
        df_1h = self._fetch_candles("1h", 100)
        df_4h = self._fetch_candles("4h", 100)

        if df_1h is None or df_4h is None or df_1h.empty or df_4h.empty:
            self._log("warn", "Candle data fetch failed — retrying next tick")
            print(f"[{self.strategy_id}] Data fetch failed")
            return

        ind_1h = compute_indicators(df_1h)
        df_1h_with_ind = df_1h.copy()
        for k, v in ind_1h.items():
            df_1h_with_ind[k] = v

        signals = self.strategy.generate_signals(df_1h_with_ind, df_4h)
        last_sig = signals[-1]

        # ---- BTC perpetual: traded price vs mark price ---------------
        # Risk maths runs on the MARK price of the perpetual; the traded price
        # is what an order would actually fill at and is recorded alongside.
        current_price = float(df_1h['close'].iloc[-1])
        mark_quote = self._fetch_mark_price()
        mark_price = getattr(mark_quote, 'mark_price', None)
        trade_price = getattr(mark_quote, 'last_price', None) or current_price
        use_mark = bool(self.use_mark_price) and mark_price is not None
        decision_price = float(mark_price) if use_mark else float(current_price)
        self.last_price = decision_price          # pricing basis
        self.last_trade_price = float(trade_price)
        self.last_mark_price = float(mark_price) if mark_price is not None else None
        self.mark_price_basis = bool(use_mark)
        self.last_checked = _ist_now()
        current_atr = ind_1h['atr14'][-1]
        current_time = df_1h.index[-1]
        # Remembered so the fast tick can re-mark a position without paying
        # for another candle fetch.
        self._last_atr = current_atr
        trade_event = False  # a fill this tick forces an immediate DB snapshot

        # ---- Data freshness gate -------------------------------------
        # Paper trading is a rehearsal with REAL market data. When the candle
        # set is hours old (venue feed down, stored fallback being served),
        # opening simulated trades on it would manufacture results nobody
        # could reproduce live — exactly the kind of "paper profit" that
        # cannot be trusted. Entries are held and say so; open positions keep
        # being managed only on a fresh mark price.
        stale_candles = self._candles_stale(current_time)
        self.candles_stale = bool(stale_candles)
        if stale_candles:
            if self._stale_notice != stale_candles:
                self._log("warn", f"{stale_candles} — entries held")
                self._stale_notice = stale_candles
            if not use_mark:
                self.last_skip_reason = stale_candles
                return
        elif self._stale_notice:
            self._log("info", "Candle feed recovered — trading resumes")
            self._stale_notice = None

        # ---- Candle clock -------------------------------------------
        # This worker ticks every 60s, so one 1h candle is seen many times.
        # Everything measured in candles (holding time, one entry per signal,
        # post-exit cooldown) keys off this flag instead of the tick count.
        new_bar = self._last_bar_time is None or current_time != self._last_bar_time
        if new_bar:
            self._last_bar_time = current_time
            if self._bars_since_exit is not None:
                self._bars_since_exit += 1

        # ---- Manage open positions ----------------------------------
        if self._manage_open_positions(decision_price, current_atr, current_time,
                                       trade_price, mark_price, new_bar):
            trade_event = True

        # A stale candle set must never OPEN anything — the signal and the
        # ATR-derived stop plan describe a market that has moved on.
        if stale_candles:
            self.skipped_entries += 1
            self.last_skip_reason = stale_candles
            return

        # ---- New entries --------------------------------------------
        # "Skip new trades" schedule: an existing position keeps running (its
        # stop/trail is managed above), only a NEW entry is refused.
        blocked_window = self.window_guard.blocking_window(current_time)
        if blocked_window:
            self.blocked_entries += 1
            # Log once per block, not on every tick: keep the reason and when
            # entries open again.
            if not self._last_block_notice or self._last_block_notice != str(blocked_window.describe()):
                when = self.window_guard.next_open_from(current_time)
                self._log("warn", f"⏸ New trade skipped — {blocked_window.describe()}"
                                  + (f" · entries resume {when:%a %d %b %H:%M}" if when else ""))
                self._last_block_notice = str(blocked_window.describe())
        elif self._last_block_notice:
            self._last_block_notice = None

        if last_sig != 0:
            ind_slice = df_1h_with_ind.iloc[-50:]
            # Drift is measured on the traded price (the fill), not the mark.
            validation = self.validator.validate_signal(last_sig, current_price, current_price, ind_slice)
            if blocked_window:
                self._log("warn", f"Signal {last_sig} not taken — inside a skip-new-trade window "
                                  f"({blocked_window.describe()})")
                print(f"⏸ [{self.strategy_id}] Entry skipped by trading window: {blocked_window.describe()}")
            elif validation.passed:
                # One entry per signal candle, one position at a time, and a
                # cooldown after an exit — the same three rules the backtest
                # engine applies. This worker ticks every 60s, so without them a
                # condition that stays TRUE would "open" a new position every
                # minute and silently replace the one already running.
                hold = self._entry_hold_reason(last_sig, current_time)
                if hold:
                    self.skipped_entries += 1
                    self.last_skip_reason = hold
                    notice = f"{current_time}::{hold}"
                    if self._last_skip_notice != notice:
                        self._log("info", f"Signal {last_sig} held back — {hold}")
                        self._last_skip_notice = notice
                else:
                    self._last_skip_notice = None
                    open_trade = self.oms.active_trades.get("BTCUSDT")
                    if open_trade is not None and getattr(self.config, "allow_reverse", False) \
                            and open_trade.direction != last_sig:
                        closed = self.oms.close_trade(
                            "BTCUSDT", decision_price, current_time, "REV",
                            "Close & reverse — opposite signal",
                            trade_price_usd=trade_price, mark_price_usd=mark_price)
                        self._bars_since_exit = 0
                        self._book_close(closed)
                        trade_event = True
                    margin_inr = self.equity_inr * (self.margin_pct / 100.0)
                    new_trade = self.oms.create_order(
                        "BTCUSDT", last_sig, decision_price, current_atr, current_time, margin_inr,
                        self.conversion_rate, trade_price_usd=trade_price, mark_price_usd=mark_price,
                        mark_price_basis=use_mark)
                    if new_trade is None:
                        self._log("warn", "Signal rejected: notional below the minimum 0.001 BTC lot")
                    else:
                        trade_event = True
                        # This candle's signal is spent — the remaining ticks of
                        # the same 1h candle must not open another position.
                        self._acted_signal_bar = current_time
                        side = "LONG" if last_sig == 1 else "SHORT"
                        price_note = (f"mark {mark_price:,.2f} (traded {trade_price:,.2f})" if use_mark
                                      else f"{current_price:,.2f}")
                        self._log("trade", f"🚀 Opened {side} BTCUSDT @ {price_note} | SL {new_trade.sl:,.2f} | "
                                            f"TP {new_trade.tp:,.2f} | Trail act {new_trade.trail_activation:,.2f} | "
                                            f"ATR {current_atr:,.2f} | {new_trade.lots:.4f} BTC | Margin ₹{new_trade.margin_inr:,.0f}")
                        print(f"🚀 [{self.strategy_id}] Paper Trade Opened: {side} at {decision_price} (SL {new_trade.sl:.2f}, TP {new_trade.tp:.2f})")
            else:
                self._log("warn", f"Signal {last_sig} rejected: {validation.reason}")
                print(f"⚠️ [{self.strategy_id}] Signal {last_sig} failed validation: {validation.reason}")
        else:
            price_note = (f"mark {mark_price:,.2f} / last {trade_price:,.2f}" if use_mark
                          else f"{current_price:,.2f}")
            self._log("info", f"Scanning… BTCUSDT {price_note} — no signal")
            print(f"🔍 [{self.strategy_id}] Scanning... Current Price: {current_price:.2f}, No signal.")

        # ---- History --------------------------------------------------
        # One equity sample per tick; the row is rewritten on every fill and
        # every few quiet ticks so History always has a usable result.
        self._record_equity_point()
        self._tick_count += 1
        self._persist_history(force=trade_event)

    # ------------------------------------------------------------------
    # Trade management helpers
    # ------------------------------------------------------------------
    def _manage_open_positions(self, decision_price, current_atr, current_time,
                               trade_price, mark_price, advance_bar):
        """Mark every open paper position and book any exit it triggers.

        Shared by the 60-second candle tick and the live-tick path so a stop
        is acted on as soon as the price arrives, not up to a minute late.
        ``advance_bar`` stays False on the fast path (holding time is candles).
        Returns True when at least one trade closed.
        """
        closed = False
        for symbol in list(self.oms.active_trades.keys()):
            result = self.oms.update_trade(symbol, decision_price, current_atr, current_time,
                                           trade_price_usd=trade_price,
                                           mark_price_usd=mark_price,
                                           advance_bar=advance_bar)
            if result:
                closed = True
                self._bars_since_exit = 0
                self._book_close(result)
        return closed

    def _book_close(self, result):
        """Apply a closed trade to equity, the closed-trade list and the log.

        Shared by the stop/target loop and by close-&-reverse, so both book PnL
        and fees exactly the same way. Returns the net PnL in INR.
        """
        price_diff = (result.exit_price - result.entry_price) * result.direction
        gross_pnl = (result.lots * price_diff) * self.conversion_rate
        taker = float(getattr(self.config, "taker_fee_bps", 0.0))
        maker = float(getattr(self.config, "maker_fee_bps", 0.0))
        entry_fee = result.notional_usd * taker / 10000 * self.conversion_rate
        exit_fee = result.notional_usd * (maker if result.exit_reason == "TP" else taker) / 10000 * self.conversion_rate
        pnl_inr = gross_pnl - entry_fee - exit_fee
        self.equity_inr += pnl_inr
        self._record_closed(result, pnl_inr, entry_fee + exit_fee, gross_pnl)
        exit_price_note = (f"mark {result.exit_mark_price:,.2f} (traded {result.exit_trade_price:,.2f})"
                           if getattr(result, 'mark_price_basis', False) else f"{result.exit_price:,.2f}")
        self._log("trade", f"✖ Closed {result.symbol} ({result.exit_reason}) — exit {exit_price_note} | "
                            f"SL {result.sl_entry:,.2f} → {result.sl:,.2f} | TP {result.tp:,.2f} | "
                            f"Trail {result.trail_stop:,.2f} | ATR {result.atr_at_entry:,.2f} | "
                            f"Net PnL ₹{pnl_inr:+,.2f} | Fees ₹{entry_fee + exit_fee:,.2f} | "
                            f"Equity ₹{self.equity_inr:,.2f}")
        if result.exit_detail:
            self._log("info", f"Exit condition: {result.exit_detail}")
        print(f"[{self.strategy_id}] Trade Closed: {result.exit_reason} @ {result.exit_price:.2f}, "
              f"Net PnL: ₹{pnl_inr:.2f}, Equity: ₹{self.equity_inr:.2f}")
        return pnl_inr

    def _entry_hold_reason(self, signal, current_time):
        """Why no new position may be opened this tick; ``None`` means go ahead.

        A signal can stay TRUE for many 1h candles and this worker ticks every
        60 seconds, so without these checks the same signal would replace the
        open trade once a minute for as long as the condition held.
        """
        if self._acted_signal_bar is not None and current_time == self._acted_signal_bar:
            return f"the signal on this candle ({current_time}) was already traded"
        open_trade = self.oms.active_trades.get("BTCUSDT")
        if open_trade is not None:
            if getattr(self.config, "allow_reverse", False) and open_trade.direction != signal:
                # Configured close-&-reverse: the caller flattens first, then
                # opens the other side in the same tick.
                return None
            if getattr(self.config, "allow_overlap", False):
                return ("overlapping entries are not supported — this worker "
                        "manages one position per contract")
            side = "LONG" if open_trade.direction == 1 else "SHORT"
            return (f"position already open ({side} {open_trade.lots:.4f} BTC) — "
                    f"waiting for it to close")
        cooldown = int(getattr(self.config, "cooldown_bars", 0) or 0)
        if self._bars_since_exit is not None and self._bars_since_exit <= cooldown:
            return (f"post-exit cooldown — {self._bars_since_exit}/{cooldown} "
                    f"candles since the last close")
        return None

    def _candles_stale(self, newest_candle_time):
        """A message naming how stale the candle set is, or ``None`` when fresh.

        Same rule as ``LiveTradeService``: on a healthy 1h feed the newest
        candle is at most ~1 hour behind the wall clock; several hours means
        the venue fetch failed and stored data is being served. Simulated
        trades opened on that would report results nobody can reproduce.
        """
        limit_hours = getattr(self, "max_candle_age_hours", 3.0)
        if not limit_hours or limit_hours <= 0:
            return None
        try:
            newest = pd.Timestamp(newest_candle_time).to_pydatetime()
            if newest.tzinfo is not None:
                newest = newest.replace(tzinfo=None)
            age = (datetime.utcnow() - newest).total_seconds()
        except Exception:
            return None
        if age <= float(limit_hours) * 3600.0:
            return None
        hours = age / 3600.0
        return (f"candle data is {hours:.1f}h old (limit {limit_hours:g}h) — the venue "
                f"feed is down or a stored fallback is being served")

    def _fetch_mark_price(self):
        """Current mark + last price of the BTC perpetual on this source.

        Returns ``None`` when the venue does not answer; the caller then falls
        back to the traded candle price (and says so in the status payload).
        """
        try:
            return MarkPriceService.current(self.market_source, self.symbol,
                                            definition=self.broker_definition)
        except Exception as exc:
            print(f"[{self.strategy_id}] mark price fetch failed: {exc}")
            return None

    def _fetch_candles(self, interval, limit):
        """The venue's LIVE feed — and nothing else.

        Paper trading is a rehearsal with real market data and simulated
        money. It used to fall back to the seeded database when the venue
        fetch failed, so a session could quietly replay stored candles while
        presenting itself as live — results nobody could reproduce with real
        orders. Now a dead feed simply means no candles: the tick logs the
        failure and retries, exactly like the live worker would.

        Reuses the cached broker client from __init__ to avoid per-tick
        allocation overhead.
        """
        try:
            rows = self._broker_client.fetch_klines("BTCUSDT", interval, limit)
            df = pd.DataFrame(rows)
            if not df.empty:
                df.set_index('event_time', inplace=True)
                return df.sort_index()
        except Exception as exc:
            # Do not swallow the reason silently — a quiet None here becomes
            # "Data fetch failed" with no way to diagnose it.
            print(f"[{self.strategy_id}] Candle fetch failed for {self.market_source} {interval}: {exc}")
        return None
