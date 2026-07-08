import requests
import pandas as pd
from datetime import datetime
from app.database.models import SessionLocal, Klines

class DataSyncService:
    TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]
    SYMBOL = "BTCUSDT"

    @staticmethod
    def sync_market_data():
        db = SessionLocal()
        try:
            print(f"🔄 Starting Market Data Sync for {datetime.now()}")
            for interval in DataSyncService.TIMEFRAMES:
                print(f"Fetching {interval} candles...")
                # Fetch last 1000 candles to ensure overlap and cover gaps
                url = f"https://fapi.binance.com/fapi/v1/klines?symbol={DataSyncService.SYMBOL}&interval={interval}&limit=1000"
                res = requests.get(url).json()
                
                for k in res:
                    event_time = datetime.utcfromtimestamp(k[0] / 1000)
                    # Only add if it doesn't exist (simple check)
                    exists = db.query(Klines).filter(
                        Klines.symbol == DataSyncService.SYMBOL,
                        Klines.interval == interval,
                        Klines.event_time == event_time
                    ).first()
                    
                    if not exists:
                        new_klines = Klines(
                            symbol=DataSyncService.SYMBOL,
                            interval=interval,
                            event_time=event_time,
                            open=float(k[1]),
                            high=float(k[2]),
                            low=float(k[3]),
                            close=float(k[4]),
                            volume=float(k[5])
                        )
                        db.add(new_klines)
                
                db.commit()
            print("✅ Market Data Sync Completed.")
        except Exception as e:
            print(f"❌ Sync Error: {e}")
            db.rollback()
        finally:
            db.close()
