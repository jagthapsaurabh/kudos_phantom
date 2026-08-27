import pandas as pd
import requests
import time
import os
from datetime import datetime, timedelta
from app.database.models import init_db, SessionLocal, Klines
from app.services.data_sync import DataSyncService

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
    
    print(f"Fetching {interval} data for {symbol} from API...")
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
                source="Binance",
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

def seed_from_csv(csv_path, interval, symbol="BTCUSDT"):
    """Seeds data from a local CSV file."""
    if not os.path.exists(csv_path):
        print(f"CSV file {csv_path} not found at {os.path.abspath(csv_path)}.")
        return False

    print(f"Loading data from {csv_path} for {interval}...")
    df = pd.read_csv(csv_path)
    df['event_time'] = pd.to_datetime(df['event_time'])
    
    init_db()
    db = SessionLocal()
    
    # Clear existing data for this interval to avoid duplicates
    db.query(Klines).filter_by(source="Binance", symbol=symbol, interval=interval).delete()
    
    batch_size = 1000
    for i in range(0, len(df), batch_size):
        batch = df.iloc[i:i+batch_size]
        records = [
            Klines(
                source="Binance",
                symbol=symbol,
                interval=interval,
                event_time=row.event_time,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume
            ) for _, row in batch.iterrows()
        ]
        db.bulk_save_objects(records)
        db.commit()
    
    db.close()
    print(f"Successfully seeded {len(df)} candles for {interval} from CSV.")
    return True

def update_daily_data(symbol="BTCUSDT", intervals=None):
    """Run the same configured multi-source daily refresh as the API."""
    return DataSyncService.sync_all_configured_sources_daily(symbol, intervals)

if __name__ == "__main__":
    # Default behavior: Try CSV first, then API
    # Assuming files are in backend/data/ relative to where the script is run (or modified for the app structure)
    csv_1h = "data/btc_1h.csv"
    csv_4h = "data/btc_4h.csv"
    
    if seed_from_csv(csv_1h, "1h") and seed_from_csv(csv_4h, "4h"):
        print("✅ Initial seed completed using CSV files.")
    else:
        print("⚠️ CSV files not found or failed. Falling back to API seeding...")
        seed_to_db(interval="1h")
        seed_to_db(interval="4h")
        
    update_daily_data()
