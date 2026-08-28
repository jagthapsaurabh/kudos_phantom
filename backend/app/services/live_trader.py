import asyncio
import pandas as pd
import numpy as np
from datetime import datetime
from app.core.strategy import StrategyService, PhantomV2Config, ValidatorService
from app.core.dynamic_strategy import DynamicStrategyService
from app.services.order_manager import OrderManager
from app.services.broker_client import BrokerClient
from app.database.models import SessionLocal, Klines
from app.core.indicators import compute_indicators


class LiveTradeService:
    def __init__(self, strategy_id: str, config_or_rules, api_key: str, api_secret: str,
                 initial_capital=20000.0, margin_pct=25.0, is_custom=False,
                 broker_name="Binance", passphrase="", testnet=False, fee_schedule=None, definition=None):
        self.strategy_id = strategy_id
        self.is_custom = is_custom
        self.market_source = broker_name or "Binance"
        self.broker_name = broker_name or "Binance"
        self.broker = BrokerClient(api_key, api_secret, self.broker_name, passphrase, testnet, definition)
        self.fee_schedule = fee_schedule
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
        self.last_price = None
        self.last_checked = None

    async def start(self):
        self.is_running = True
        print(f"🚀 LIVE Trading Started for {self.broker_name}/{self.strategy_id}")
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
        current_trade_usd = float(df_1h['close'].iloc[-1])
        current_price = float(df_1h['mark_close'].fillna(df_1h['close']).iloc[-1]) \
            if 'mark_close' in df_1h.columns else current_trade_usd
        try:
            current_price = float(self.broker.fetch_mark_price("BTCUSDT") or current_price)
        except Exception:
            pass
        current_atr = float(ind_1h['atr14'][-1])
        current_time = df_1h.index[-1]
        self.last_price = current_price
        self.last_mark_price = current_price
        self.last_trade_price = current_trade_usd
        self.last_checked = datetime.utcnow().isoformat(timespec="seconds")

        # All stop/trail/PnL decisions use the exchange mark price; the actual
        # trade (fill) price is the candle close and stays on the trade record.
        for symbol in list(self.oms.active_trades.keys()):
            result = self.oms.update_trade(symbol, current_price, current_atr, current_time,
                                           trade_price=current_trade_usd)
            if result:
                side = "SELL" if result.direction == 1 else "BUY"
                response = self.broker.place_order(symbol, side, "MARKET", result.lots)
                if "error" not in response:
                    print(f"🔴 [{self.strategy_id}] LIVE {self.broker_name} close: {result.exit_reason} at mark {result.exit_mark_price:,.2f} (trade {result.exit_price:,.2f})")
                    if result.exit_detail:
                        print(f"   Exit condition: {result.exit_detail}")
                else:
                    print(f"❌ [{self.strategy_id}] LIVE close failed: {response['error']}")

        if last_sig != 0:
            if self.config.is_new_trade_blocked(current_time):
                print(f"⚠️ [{self.strategy_id}] LIVE new entry skipped — configured weekly skip window")
            else:
                ind_slice = df_1h_with_ind.iloc[-50:]
                if self.validator.validate_signal(last_sig, current_price, current_price, ind_slice).passed:
                    margin_inr = self.initial_capital * (self.margin_pct / 100.0)
                    notional_usd = margin_inr / self.conversion_rate * self.config.leverage
                    lots = float(np.floor((notional_usd / current_price) / self.config.lot_size_btc) * self.config.lot_size_btc)
                    if lots <= 0:
                        return
                    side = "BUY" if last_sig == 1 else "SELL"
                    res = self.broker.place_order("BTCUSDT", side, "MARKET", lots)
                    if "error" not in res:
                        self.oms.create_order("BTCUSDT", last_sig, current_trade_usd, current_atr,
                                              current_time, margin_inr, self.conversion_rate,
                                              mark_price=current_price)
                        print(f"🚀 [{self.strategy_id}] LIVE {self.broker_name} opened: {side} at mark {current_price} (trade {current_trade_usd}) ({lots} BTC)")
                    else:
                        print(f"❌ [{self.strategy_id}] LIVE order failed: {res['error']}")

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
                                'low': k.low, 'close': k.close, 'volume': k.volume,
                                'mark_open': getattr(k, 'mark_open', None),
                                'mark_high': getattr(k, 'mark_high', None),
                                'mark_low': getattr(k, 'mark_low', None),
                                'mark_close': getattr(k, 'mark_close', None)} for k in rows])
            if df.empty:
                return df
            df.set_index('event_time', inplace=True)
            return df.sort_index()
        except Exception:
            return None
