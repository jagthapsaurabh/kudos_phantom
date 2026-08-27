import asyncio
import pandas as pd
from datetime import datetime, timezone, timedelta
from app.core.strategy import StrategyService, PhantomV2Config, ValidatorService
from app.core.dynamic_strategy import DynamicStrategyService
from app.services.order_manager import OrderManager
from app.services.broker_client import BrokerClient
from app.database.models import SessionLocal, Klines
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

    def __init__(self, strategy_id: str, config_or_rules, initial_capital=20000.0, margin_pct=25.0,
                 is_custom=False, market_source="Binance", broker_name=None, fee_schedule=None,
                 broker_definition=None, strategy_name=None):
        self.strategy_id = str(strategy_id)
        # Custom strategies are identified by a numeric id internally. Keep a
        # human-readable name on the worker so every status/list view can show
        # what the user actually selected.
        self.strategy_name = strategy_name or self.strategy_id
        self.is_custom = is_custom
        self.created_at = _ist_now()
        self.market_source = market_source or "Binance"
        self.broker_name = broker_name or self.market_source
        self.broker_definition = broker_definition
        self.fee_schedule = fee_schedule
        self.initial_capital_inr = initial_capital

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

        self.validator = ValidatorService()
        self.oms = OrderManager(self.config)
        self.is_running = False
        self.equity_inr = initial_capital
        self.margin_pct = margin_pct
        self.conversion_rate = 85.0
        if fee_schedule:
            self.config.taker_fee_bps = float(getattr(fee_schedule, "taker_fee_bps", self.config.taker_fee_bps))
            self.config.maker_fee_bps = float(getattr(fee_schedule, "maker_fee_bps", self.config.maker_fee_bps))
        self.last_price = None
        self.last_checked = None
        # Live log buffer: list of {"ts": ISO, "level": "info|warn|error|trade", "msg": str}
        self.logs: list = []
        # Closed-trade history: list of trade dicts
        self.closed_trades: list = []
        self._log("info", f"Instance initialised — strategy={self.strategy_name}, capital=₹{initial_capital:,.0f}, margin={margin_pct}%")

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
        if len(self.closed_trades) > self.MAX_CLOSED_TRADES:
            self.closed_trades = self.closed_trades[-self.MAX_CLOSED_TRADES:]

    async def start(self):
        self.is_running = True
        self._log("info", f"🟢 Paper trading started — strategy={self.strategy_id}")
        print(f"🟢 Paper Trading Started for Strategy: {self.strategy_id}")
        while self.is_running:
            try:
                await self.tick()
            except Exception as e:
                self._log("error", f"Tick error: {e}")
                print(f"Paper Trade Error [{self.strategy_id}]: {e}")
            await asyncio.sleep(60)  # Check every minute

    async def stop(self):
        self.is_running = False
        self._log("info", "🔴 Paper trading stopped by user")
        print(f"🔴 Paper Trading Stopped for Strategy: {self.strategy_id}")

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

        current_price = float(df_1h['close'].iloc[-1])
        self.last_price = current_price
        self.last_checked = _ist_now()
        current_atr = ind_1h['atr14'][-1]
        current_time = df_1h.index[-1]

        # ---- Manage open positions ----------------------------------
        for symbol in list(self.oms.active_trades.keys()):
            result = self.oms.update_trade(symbol, current_price, current_atr, current_time)
            if result:
                price_diff = (result.exit_price - result.entry_price) * result.direction
                gross_pnl = (result.lots * price_diff) * self.conversion_rate
                taker = float(getattr(self.config, "taker_fee_bps", 0.0))
                maker = float(getattr(self.config, "maker_fee_bps", 0.0))
                entry_fee = result.notional_usd * taker / 10000 * self.conversion_rate
                exit_fee = result.notional_usd * (maker if result.exit_reason == "TP" else taker) / 10000 * self.conversion_rate
                pnl_inr = gross_pnl - entry_fee - exit_fee
                self.equity_inr += pnl_inr
                self._record_closed(result, pnl_inr, entry_fee + exit_fee, gross_pnl)
                self._log("trade", f"✖ Closed {symbol} ({result.exit_reason}) — exit {result.exit_price:,.2f} | "
                                    f"SL {result.sl_entry:,.2f} → {result.sl:,.2f} | TP {result.tp:,.2f} | "
                                    f"Trail {result.trail_stop:,.2f} | ATR {result.atr_at_entry:,.2f} | "
                                    f"Net PnL ₹{pnl_inr:+,.2f} | Fees ₹{entry_fee + exit_fee:,.2f} | "
                                    f"Equity ₹{self.equity_inr:,.2f}")
                if result.exit_detail:
                    self._log("info", f"Exit condition: {result.exit_detail}")
                print(f"[{self.strategy_id}] Trade Closed: {result.exit_reason} @ {result.exit_price:.2f}, Net PnL: ₹{pnl_inr:.2f}, Equity: ₹{self.equity_inr:.2f}")

        # ---- New entries --------------------------------------------
        if last_sig != 0:
            ind_slice = df_1h_with_ind.iloc[-50:]
            validation = self.validator.validate_signal(last_sig, current_price, current_price, ind_slice)
            if validation.passed:
                margin_inr = self.equity_inr * (self.margin_pct / 100.0)
                new_trade = self.oms.create_order("BTCUSDT", last_sig, current_price, current_atr, current_time, margin_inr, self.conversion_rate)
                if new_trade is None:
                    self._log("warn", "Signal rejected: notional below the minimum 0.001 BTC lot")
                else:
                    side = "LONG" if last_sig == 1 else "SHORT"
                    self._log("trade", f"🚀 Opened {side} BTCUSDT @ {current_price:,.2f} | SL {new_trade.sl:,.2f} | "
                                        f"TP {new_trade.tp:,.2f} | Trail act {new_trade.trail_activation:,.2f} | "
                                        f"ATR {current_atr:,.2f} | {new_trade.lots:.4f} BTC | Margin ₹{new_trade.margin_inr:,.0f}")
                    print(f"🚀 [{self.strategy_id}] Paper Trade Opened: {side} at {current_price} (SL {new_trade.sl:.2f}, TP {new_trade.tp:.2f})")
            else:
                self._log("warn", f"Signal {last_sig} rejected: {validation.reason}")
                print(f"⚠️ [{self.strategy_id}] Signal {last_sig} failed validation: {validation.reason}")
        else:
            self._log("info", f"Scanning… BTCUSDT {current_price:,.2f} — no signal")
            print(f"🔍 [{self.strategy_id}] Scanning... Current Price: {current_price:.2f}, No signal.")

    def _fetch_candles(self, interval, limit):
        """Use candles seeded for this source, then the source public API."""
        df = self._get_data_from_db("BTCUSDT", interval, limit)
        if df is not None and not df.empty:
            return df
        try:
            rows = BrokerClient(broker_name=self.market_source, definition=self.broker_definition).fetch_klines("BTCUSDT", interval, limit)
            df = pd.DataFrame(rows)
            if df.empty:
                return df
            df.set_index('event_time', inplace=True)
            return df.sort_index()
        except Exception as exc:
            # Do not swallow the reason silently — a quiet None here becomes
            # "Data fetch failed" with no way to diagnose it.
            print(f"[{self.strategy_id}] Candle fetch failed for {self.market_source} {interval}: {exc}")
            return None

    def _get_data_from_db(self, symbol, interval, limit=500):
        """Return the most recent candles for the selected source."""
        try:
            db = SessionLocal()
            data = db.query(Klines).filter(
                Klines.symbol == symbol, Klines.interval == interval,
                Klines.source == self.market_source
            ).order_by(Klines.event_time.desc()).limit(limit).all()
            db.close()
            if not data:
                return pd.DataFrame()
            df = pd.DataFrame([
                {'event_time': k.event_time, 'open': k.open, 'high': k.high,
                 'low': k.low, 'close': k.close, 'volume': k.volume}
                for k in data
            ])
            df.set_index('event_time', inplace=True)
            return df.sort_index()
        except Exception:
            return pd.DataFrame()
