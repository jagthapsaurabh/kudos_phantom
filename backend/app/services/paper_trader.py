import asyncio
import pandas as pd
from datetime import datetime, timezone, timedelta
from app.core.strategy import StrategyService, PhantomV2Config, ValidatorService
from app.core.dynamic_strategy import DynamicStrategyService
from app.services.order_manager import OrderManager
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
                 is_custom=False, market_source="Binance", broker_name=None, fee_schedule=None, broker_definition=None):
        self.strategy_id = strategy_id
        self.is_custom = is_custom
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
        self._log("info", f"Instance initialised — strategy={strategy_id}, capital=₹{initial_capital:,.0f}, margin={margin_pct}%")

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
        self.closed_trades.append({
            "symbol": trade.symbol,
            "direction": trade.direction,
            "entry": trade.entry_price,
            "exit": trade.exit_price,
            "pnl": pnl_inr,
            "gross_pnl": gross_pnl if gross_pnl is not None else pnl_inr,
            "fees": fees_inr,
            "reason": trade.exit_reason,
            "entry_time": _to_ist(trade.entry_time),
            "exit_time": _to_ist(trade.exit_time),
            "bars_held": trade.bars_held,
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
                self._log("trade", f"✖ Closed {symbol} ({result.exit_reason}) — Net PnL ₹{pnl_inr:+,.2f} | Fees ₹{entry_fee + exit_fee:,.2f} | Equity ₹{self.equity_inr:,.2f}")
                print(f"[{self.strategy_id}] Trade Closed: {result.exit_reason}, Net PnL: ₹{pnl_inr:.2f}, Equity: ₹{self.equity_inr:.2f}")

        # ---- New entries --------------------------------------------
        if last_sig != 0:
            ind_slice = df_1h_with_ind.iloc[-50:]
            validation = self.validator.validate_signal(last_sig, current_price, current_price, ind_slice)
            if validation.passed:
                margin_inr = self.equity_inr * (self.margin_pct / 100.0)
                self.oms.create_order("BTCUSDT", last_sig, current_price, current_atr, current_time, margin_inr, self.conversion_rate)
                side = "LONG" if last_sig == 1 else "SHORT"
                self._log("trade", f"🚀 Opened {side} BTCUSDT @ {current_price:,.2f} | Margin ₹{margin_inr:,.0f}")
                print(f"🚀 [{self.strategy_id}] Paper Trade Opened: {'LONG' if last_sig==1 else 'SHORT'} at {current_price}")
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
        except Exception:
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
