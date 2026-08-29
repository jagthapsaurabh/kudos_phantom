import os
import bcrypt
from app.database.models import init_db, SessionLocal, User, Klines
from app.scripts.seeder import seed_from_csv

def factory_reset():
    db_path = 'trading_system.db'
    
    # 1. Delete existing database file
    if os.path.exists(db_path):
        print(f"🗑️ Deleting database {db_path}...")
        os.remove(db_path)
    
    # 2. Re-initialize database schema
    print("🏗️ Re-creating database schema...")
    init_db()
    
    # 3. Seed Market Data from CSVs (rejected when off the candle grid)
    print("📊 Seeding market data from CSVs...")
    # Paths relative to backend folder
    ok_1h = seed_from_csv("data/btc_1h.csv", "1h")
    ok_4h = seed_from_csv("data/btc_4h.csv", "4h")
    if not (ok_1h and ok_4h):
        print("⚠️ CSV history unavailable/corrupt. Fetch clean candles from the exchange instead:")
        print("     python -m app.scripts.seeder        # Binance 2020 → today (15m, 1h, 4h, 1d)")
    
    # 4. Seed Admin User
    print("👤 Seeding admin user...")
    db = SessionLocal()
    username = "admin"
    password = "admin_password_123"
    
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
    
    admin_user = User(
        username=username,
        password_hash=hashed,
        role='admin',
        is_active=1,
        can_paper=1,
        can_live=1,
        initial_capital=20000.0,
        margin_deployment_pct=25.0
    )
    db.add(admin_user)
    db.commit()
    db.close()
    
    print("\n✨ Factory Reset Complete!")
    print(f"Login: {username} / {password}")

if __name__ == "__main__":
    factory_reset()
