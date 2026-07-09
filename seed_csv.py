import pandas as pd
from backend.app.database.models import init_db, SessionLocal, Klines
import os

def seed_from_csv(csv_path, interval, symbol="BTCUSDT"):
    if not os.path.exists(csv_path):
        print(f"CSV file {csv_path} not found.")
        return

    print(f"Loading data from {csv_path} for {interval}...")
    df = pd.read_csv(csv_path)
    df['event_time'] = pd.to_datetime(df['event_time'])
    
    init_db()
    db = SessionLocal()
    
    # Clear existing data for this interval to avoid duplicates
    db.query(Klines).filter_by(symbol=symbol, interval=interval).delete()
    
    batch_size = 1000
    for i in range(0, len(df), batch_size):
        batch = df.iloc[i:i+batch_size]
        records = [
            Klines(
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

if __name__ == "__main__":
    seed_from_csv("backend/data/btc_1h.csv", "1h")
    seed_from_csv("backend/data/btc_4h.csv", "4h")
