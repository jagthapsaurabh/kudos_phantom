import pandas as pd
import requests
import time
import os
from datetime import datetime, timedelta
from app.database.models import init_db, SessionLocal, Klines

def fetch_binance_klines(symbol, interval, start_time, end_time=None):
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": int(start_time.timestamp() * 1000),
    }
    if end_time:
        params["endTime"] = int(end_time.timestamp() * 1000)
    
    all_klines = []
    while True:
        try:
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            if not data or not isinstance(data, list): break
            all_klines.extend(data)
            params["startTime"] = data[-1][0] + 1
            if end_time and data[-1][0] >= params.get("endTime", float('inf')): break
            if len(data) < 500: break
            time.sleep(0.2)
        except Exception as e:
            print(f"Error: {e}")
            break
    return all_klines

def seed_to_db(symbol="BTCUSDT", interval="1h", years=6):
    init_db()
    db = SessionLocal()
    
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=years*365)
    
    print(f"Fetching {interval} data for {symbol}...")
    klines = fetch_binance_klines(symbol, interval, start_date, end_date)
    
    if not klines:
        print("No data fetched.")
        return

    # Batch insert for performance
    batch_size = 1000
    for i in range(0, len(klines), batch_size):
        batch = klines[i:i+batch_size]
        records = [
            Klines(
                symbol=symbol,
                interval=interval,
                event_time=pd.to_datetime(k[0], unit='ms'),
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5])
            ) for k in batch
        ]
        db.bulk_save_objects(records)
        db.commit()
    
    db.close()
    print(f"Successfully seeded {len(klines)} candles for {interval} into DB.")

def update_daily_data(symbol="BTCUSDT", intervals=["1h", "4h"]):
    """Updates the DB with the most recent candles."""
    init_db()
    db = SessionLocal()
    
    for interval in intervals:
        last_candle = db.query(Klines).filter_by(symbol=symbol, interval=interval).order_by(Klines.event_time.desc()).first()
        
        start_time = last_candle.event_time if last_candle else (datetime.utcnow() - timedelta(days=365*6))
        end_time = datetime.utcnow()
        
        print(f"Updating {interval} data from {start_time}...")
        klines = fetch_binance_klines(symbol, interval, start_time, end_time)
        
        if klines:
            records = [
                Klines(
                    symbol=symbol, interval=interval,
                    event_time=pd.to_datetime(k[0], unit='ms'),
                    open=float(k[1]), high=float(k[2]), low=float(k[3]),
                    close=float(k[4]), volume=float(k[5])
                ) for k in klines[1:]
            ]
            db.bulk_save_objects(records)
            db.commit()
    
    db.close()

if __name__ == "__main__":
    seed_to_db()
    update_daily_data()
