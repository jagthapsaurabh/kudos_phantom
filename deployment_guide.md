# 🚀 PHANTOM v2.5 Deployment Guide (VPS)

This document provides the absolute, latest instructions to deploy the PHANTOM v2.5 trading tool on a VPS.

## 📌 Server Specifications
- **Deployment Path:** `/var/www/kudos_phantom`
- **Backend Port:** `8001` (Configured in PM2)
- **Frontend Port:** `5173` (or served via Nginx)

---

## 🛠️ Step 1: Server Preparation

1. **Access your VPS via SSH:**
   ```bash
   ssh root@your_server_ip
   ```

2. **Create the directory and set permissions:**
   ```bash
   sudo mkdir -p /var/www/kudos_phantom
   sudo chown -R $USER:$USER /var/www/kudos_phantom
   cd /var/www/kudos_phantom
   ```

3. **Upload your code:**
   Upload the `backend` and `frontend` folders to `/var/www/kudos_phantom`. Ensure `backend/data/` contains `btc_1h.csv` and `btc_4h.csv`.

---

## ⚙️ Step 2: Backend Deployment (FastAPI)

### 1. Environment Setup
```bash
cd /var/www/kudos_phantom/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install python-dotenv
```

### 2. Configure `.env` file
Create `/var/www/kudos_phantom/backend/.env` with the following (replace `your_server_ip`):
```env
DATABASE_URL=sqlite:///./trading_system.db
SECRET_KEY=phantom_secret_key_2026_xyz
CORS_ORIGINS=http://your_server_ip:5173,http://your_server_ip
CONVERSION_RATE=85.0
TAKER_FEE_BPS=5.9
MAKER_FEE_BPS=2.36
```

### 3. Database Setup (CRITICAL)
The system now includes a Factory Reset tool to ensure the database is perfectly seeded from CSVs.
```bash
export PYTHONPATH=$PYTHONPATH:.
python3 app/scripts/reset_db.py
```
*This will delete the old DB, create a new one, seed all BTC data from CSVs, and create the admin user.*

> ⚠️ **The database is NOT in Git.** SQLite database files (`*.db`, `*.sqlite`)
> are git-ignored because they hold live trading data. If the server has a
> `backend/trading_system.db` with real data, **`git switch`/`git checkout` will
> refuse to change branches** ("local changes would be overwritten") because that
> file looks like a tracked-but-modified file.
>
> To deploy WITHOUT losing the live data, on the server run:
> ```bash
> cd /var/www/kudos_phantom
> cp backend/trading_system.db backend/trading_system.db.bak   # backup (always)
> git rm --cached backend/trading_system.db                     # untrack, keep file
> [ -f trading_system.db ] && git rm --cached trading_system.db # legacy root DB
> git add .gitignore
> git commit -m "Untrack database files for clean deploys"
> git switch arena/<your-branch>
> ls -la backend/trading_system.db || cp backend/trading_system.db.bak backend/trading_system.db
> pm2 restart phantom-backend
> ```
> The app always opens the DB at `backend/trading_system.db` regardless of the
> working directory (see `app/database/models.py`), so the code deploy never
> replaces the live database. Schema changes are applied on startup as
> **additive** `ALTER TABLE` migrations that preserve existing rows.

### 4. Run with PM2
```bash
sudo npm install -g pm2
pm2 start "source venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8001" --name phantom-backend
pm2 save
```

---

## 🎨 Step 3: Frontend Deployment (React + Vite)

### 1. Installation & Build
```bash
cd /var/www/kudos_phantom/frontend
npm install
```

### 2. Configure API URL
Create `.env.production`:
```env
VITE_API_URL=http://your_server_ip:8001
```

### 3. Build & Serve
```bash
npm run build
sudo npm install -g serve
pm2 start "serve -s dist -l 5173" --name phantom-frontend
```

---

## 🔒 Step 4: Firewall & Networking

```bash
sudo ufw allow 8001/tcp    # Backend
sudo ufw allow 5173/tcp    # Frontend
sudo ufw reload
```

## 🚀 Maintenance Commands
- **To reset everything (Data & Users):**
  `cd /var/www/kudos_phantom/backend && source venv/bin/activate && export PYTHONPATH=$PYTHONPATH:. && python3 app/scripts/reset_db.py`
- **To update market data:**
  `python3 -m app.scripts.seeder`
- **To restart backend:**
  `pm2 restart phantom-backend`
