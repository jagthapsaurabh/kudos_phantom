#!/bin/bash

# PHANTOM v2.5 Maintenance & Setup Script
# This script fixes dependencies, seeds the admin, and fetches market data.

echo "🚀 Starting PHANTOM v2.5 Maintenance..."

# 1. Navigate to backend folder
cd /var/www/kudos_phantom/backend

# 2. Activate Virtual Environment
source venv/bin/activate

# 3. Fix bcrypt compatibility issue
echo "🛠️ Fixing bcrypt compatibility..."
pip install bcrypt==3.2.0 passlib==1.7.4

# 4. Install other dependencies
echo "📦 Installing requirements..."
pip install -r requirements.txt

# 5. Seed Admin User
echo "👤 Seeding Admin User..."
export PYTHONPATH=$PYTHONPATH:$(pwd)
python3 -m app.scripts.seed_admin

# 6. Seed Market Data (Old and New)
echo "📊 Seeding Market Data..."
python3 -m app.scripts.seeder

# 7. Restart Backend via PM2
echo "♻️ Restarting Backend..."
pm2 restart phantom-backend

echo "✅ Maintenance Complete! System is now fully synced and ready."
echo "Try logging in with: admin / admin_password_123"
