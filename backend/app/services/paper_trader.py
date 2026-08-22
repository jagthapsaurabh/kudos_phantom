import asyncio
import pandas as pd
from datetime import datetime
from app.core.strategy import StrategyService, PhantomV2Config, ValidatorService
from app.core.dynamic_strategy import DynamicStrategyService
from app.services.order_manager import OrderManager
import requests
from app.core.indicators import compute_indicators

class PaperTradeService:
    MAX_LOG_LINES = 200  # keep last N log entries per instance

    def __init__(self, strategy_id: str, config_or_rules, initial_capital=20000.0, margin_pct=25.0, is_custom=False):
        self.strategy_id = strategy_id
        self.is_custom = is_custom
        
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
        # Live log buffer: list of {"ts": ISO, "level": "info|warn|error|trade", "msg": str}
        self.logs: list = []
        self._log("info", f"Instance initialised — strategy={strategy_id}, capital=₹{initial_capital:,.0f}, margin={margin_pct}%")

    def _log(self, level: str, msg: str):
        entry = {"ts": datetime.utcnow().isoformat(timespec="seconds"), "level": level, "msg": msg}
        self.logs.append(entry)
        if len(self.logs) > self.MAX_LOG_LINES:
            self.logs = self.logs[-self.MAX_LOG_LINES:]

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
            await asyncio.sleep(60) # Check every minute

    async def stop(self):
        self.is_running = False
        self._log("info", "🔴 Paper trading stopped by user")
        print(f"🔴 Paper Trading Stopped for Strategy: {self.strategy_id}")

    async def tick(self):
        df_1h = self._fetch_candles("1h", 100)
        df_4h = self._fetch_candles("4h", 100)
        
        if df_1h is None or df_4h is None:
            self._log("warn", "Candle data fetch failed — retrying next tick")
            print(f"[{self.strategy_id}] Data fetch failed")
            return

        ind_1h = compute_indicators(df_1h)
        df_1h_with_ind = df_1h.copy()
        for k, v in ind_1h.items(): df_1h_with_ind[k] = v
        
        signals = self.strategy.generate_signals(df_1h_with_ind, df_4h)
        last_sig = signals[-1]
        
        current_price = df_1h['close'].iloc[-1]
        current_atr = ind_1h['atr14'][-1]
        current_time = df_1h.index[-1]
        
        for symbol in list(self.oms.active_trades.keys()):
            result = self.oms.update_trade(symbol, current_price, current_atr, current_time)
            if result:
                price_diff = (result.exit_price - result.entry_price) * result.direction
                pnl_inr = (result.lots * price_diff) * self.conversion_rate
                self.equity_inr += pnl_inr
                self._log("trade", f"✖ Closed {symbol} ({result.exit_reason}) — PnL ₹{pnl_inr:+,.2f} | Equity ₹{self.equity_inr:,.2f}")
                print(f"[{self.strategy_id}] Trade Closed: {result.exit_reason}, PnL: ₹{pnl_inr:.2f}, Equity: ₹{self.equity_inr:.2f}")

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
        try:
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval={interval}&limit={limit}"
            res = requests.get(url).json()
            df = pd.DataFrame(res, columns=['event_time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'tbb', 'tbq', 'ignore'])
            df['event_time'] = pd.to_datetime(df['event_time'], unit='ms')
            df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
            df.set_index('event_time', inplace=True)
            return df
        except:
            return None
