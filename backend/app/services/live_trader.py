import asyncio
import pandas as pd
from datetime import datetime
from app.core.strategy import StrategyService, PhantomV2Config, ValidatorService
from app.core.dynamic_strategy import DynamicStrategyService
from app.services.order_manager import OrderManager
from app.services.broker_client import BrokerClient
import requests
from app.core.indicators import compute_indicators

class LiveTradeService:
    def __init__(self, strategy_id: str, config_or_rules, api_key: str, api_secret: str, initial_capital=20000.0, margin_pct=25.0, is_custom=False):
        self.strategy_id = strategy_id
        self.is_custom = is_custom
        self.broker = BrokerClient(api_key, api_secret)
        
        if is_custom:
            self.rules = config_or_rules
            self.strategy = DynamicStrategyService(self.rules)
            self.config = PhantomV2Config()
        else:
            self.config = config_or_rules
            self.strategy = StrategyService(self.config)
            
        self.validator = ValidatorService()
        self.oms = OrderManager(self.config) # Local tracking of live trades
        self.is_running = False
        self.initial_capital = initial_capital
        self.margin_pct = margin_pct
        self.conversion_rate = 85.0

    async def start(self):
        self.is_running = True
        print(f"🚀 LIVE Trading Started for Strategy: {self.strategy_id}")
        while self.is_running:
            try:
                await self.tick()
            except Exception as e:
                print(f"Live Trade Error [{self.strategy_id}]: {e}")
            await asyncio.sleep(60)

    async def stop(self):
        self.is_running = False
        print(f"🔴 LIVE Trading Stopped for Strategy: {self.strategy_id}")

    async def tick(self):
        df_1h = self._fetch_candles("1h", 100)
        df_4h = self._fetch_candles("4h", 100)
        
        if df_1h is None or df_4h is None: return

        ind_1h = compute_indicators(df_1h)
        df_1h_with_ind = df_1h.copy()
        for k, v in ind_1h.items(): df_1h_with_ind[k] = v
        
        signals = self.strategy.generate_signals(df_1h_with_ind, df_4h)
        last_sig = signals[-1]
        
        current_price = df_1h['close'].iloc[-1]
        current_atr = ind_1h['atr14'][-1]
        current_time = df_1h.index[-1]
        
        # Update local tracking and handle exits
        for symbol in list(self.oms.active_trades.keys()):
            result = self.oms.update_trade(symbol, current_price, current_atr, current_time)
            if result:
                # EXECUTE LIVE CLOSE
                side = "SELL" if result.direction == 1 else "BUY"
                self.broker.place_order(symbol, side, "MARKET", result.lots)
                print(f"🔴 [{self.strategy_id}] LIVE Trade Closed: {result.exit_reason} at {current_price}")

        # Handle entry
        if last_sig != 0:
            ind_slice = df_1h_with_ind.iloc[-50:]
            if self.validator.validate_signal(last_sig, current_price, current_price, ind_slice).passed:
                # Calculate quantity based on dynamic margin settings
                margin_inr = self.initial_capital * (self.margin_pct / 100.0)
                notional_usd = margin_inr / self.conversion_rate * self.config.leverage
                lots = notional_usd / current_price
                
                # Quantize lots to 0.001 BTC
                lots = float(np.floor(lots / 0.001) * 0.001)
                
                if lots <= 0:
                    print(f"⚠️ [{self.strategy_id}] Calculated lots too small: {lots}")
                    return

                side = "BUY" if last_sig == 1 else "SELL"
                res = self.broker.place_order("BTCUSDT", side, "MARKET", lots)
                
                if "error" not in res:
                    # Update local tracking
                    self.oms.create_order("BTCUSDT", last_sig, current_price, current_atr, current_time, margin_inr)
                    print(f"🚀 [{self.strategy_id}] LIVE Trade Opened: {side} at {current_price} ({lots} BTC)")
                else:
                    print(f"❌ [{self.strategy_id}] LIVE Order Failed: {res['error']}")

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
