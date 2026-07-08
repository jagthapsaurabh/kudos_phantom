import requests
import json
import time
from datetime import datetime

BASE_URL = "http://127.0.0.1:8000"
USERNAME = "test_user_full"
PASSWORD = "test_password_123"

def test_api():
    print("🧪 Starting Full API Integration Test...")
    
    # 1. Health Check
    try:
        res = requests.get(f"{BASE_URL}/")
        print(f"✅ Health Check: {res.status_code} - {res.json()['status']}")
    except Exception as e:
        print(f"❌ Health Check Failed: {e}")

    # 2. Registration
    try:
        res = requests.post(f"{BASE_URL}/auth/register", params={"username": USERNAME, "password": PASSWORD})
        print(f"✅ Registration: {res.status_code}")
    except Exception as e:
        print(f"❌ Registration Failed: {e}")

    # 3. Login
    try:
        res = requests.post(f"{BASE_URL}/token", data={"username": USERNAME, "password": PASSWORD})
        token = res.json().get("access_token")
        print(f"✅ Login: {res.status_code} (Token acquired)")
        headers = {"Authorization": f"Bearer {token}"}
    except Exception as e:
        print(f"❌ Login Failed: {e}")
        return

    # 4. Broker Settings
    try:
        settings = {
            "api_key": "test_key",
            "api_secret": "test_secret",
            "initial_capital": 20000.0,
            "margin_pct": 25.0,
            "broker_name": "Binance"
        }
        res = requests.post(f"{BASE_URL}/broker-settings", json=settings, headers=headers)
        print(f"✅ Broker Settings Update: {res.status_code}")
    except Exception as e:
        print(f"❌ Broker Settings Failed: {e}")

    # 5. Start Paper Trade (FastTest)
    try:
        res = requests.post(f"{BASE_URL}/api/paper-trade/start", 
                            json={"strategy_id": "FastTest"}, 
                            headers=headers)
        # Note: Adjusted to /api if proxy is used, but script hits backend directly
        # For direct backend test, we use the endpoint defined in main.py
        res = requests.post(f"{BASE_URL}/paper-trade/start", 
                            json={"strategy_id": "FastTest"}, 
                            headers=headers)
        instance_key = res.json().get("instance_key")
        print(f"✅ Paper Trade Start: {res.status_code} (Key: {instance_key})")
    except Exception as e:
        print(f"❌ Paper Trade Start Failed: {e}")

    # 6. Check Paper Status
    try:
        res = requests.get(f"{BASE_URL}/paper-trade/status", headers=headers)
        print(f"✅ Paper Status: {res.status_code} - Instances: {len(res.json())}")
    except Exception as e:
        print(f"❌ Paper Status Failed: {e}")

    # 7. Backtest Execution
    try:
        backtest_req = {
            "params": {
                "trend_ema_period": 50, "rsi_oversold": 30, "rsi_overbought": 70, "adx_min": 22,
                "macd_hist_min": 25, "atr_regime_ratio": 0.5, "stop_loss_atr": 2.0, "take_profit_atr": 1.2,
                "trail_activation_atr": 1.5, "trail_distance_atr": 0.5,
                "timeout_bars": 72, "cooldown_bars": 2, "margin_pct": 0.25, "leverage": 7,
                "lot_size_btc": 0.001, "max_notional_mult": 10, "taker_fee_bps": 5.9, "maker_fee_bps": 2.36, "liquidation_buffer": 0.005
            },
            "strategy_id": "PhantomV2",
            "start_date": "2023-01-01",
            "end_date": "2023-06-01",
            "strategy_name": "Integration Test Run"
        }
        res = requests.post(f"{BASE_URL}/backtest", json=backtest_req, headers=headers)
        print(f"✅ Backtest Triggered: {res.status_code}")
    except Exception as e:
        print(f"❌ Backtest Failed: {e}")

    print("\n🏁 Integration Test Completed.")

if __name__ == "__main__":
    test_api()
