# 🚀 PHANTOM v2.5 Deployment Guide (VPS)

This document provides step-by-step instructions to deploy the PHANTOM v2.5 trading tool on a VPS.

## 📌 Server Specifications
- **Deployment Path:** `/var/www/kudos_phantom`
- **Backend Port:** Configurable via `.env` (Suggested: `8001` as `8000` is occupied)
- **Frontend Port:** Suggested `5173` or served via Nginx
- **Access:** Via Server IP and Port

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
   Upload the `backend` and `frontend` folders to `/var/www/kudos_phantom`.

---

## ⚙️ Step 2: Backend Deployment (FastAPI)

### 1. Environment Setup
```bash
cd /var/www/kudos_phantom/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install python-dotenv # For .env support
```

### 2. Configure `.env` file
Create a `.env` file in `/var/www/kudos_phantom/backend/.env`:
```env
BACKEND_PORT=8001
DATABASE_URL=sqlite:///trading_system.db
CORS_ORIGINS=http://your_server_ip:5173,http://your_server_ip
```

### 3. Update `main.py` for Dynamic Port & CORS
Ensure your `backend/app/main.py` uses the `.env` for CORS. Update the `CORSMiddleware` section:
```python
import os
from dotenv import load_dotenv
load_dotenv()

origins = os.getenv("CORS_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### 4. Run with PM2 (Process Manager)
To keep the backend running 24/7:
```bash
sudo npm install -g pm2
pm2 start "source venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8001" --name phantom-backend
pm2 save
pm2 startup
```

---

## 🎨 Step 3: Frontend Deployment (React + Vite)

### 1. Installation & Build
```bash
cd /var/www/kudos_phantom/frontend
npm install
```

### 2. Configure API URL
Create or update `.env.production` in the frontend folder:
```env
VITE_API_URL=http://your_server_ip:8001
```

### 3. Build for Production
```bash
npm run build
```

### 4. Serve the Frontend
You have two options:

**Option A: Simple Node Server (Fastest)**
```bash
sudo npm install -g serve
pm2 start "serve -s dist -l 5173" --name phantom-frontend
```

**Option B: Nginx (Professional - Recommended)**
Add a server block to `/etc/nginx/sites-available/phantom`:
```nginx
server {
    listen 80;
    server_name your_server_ip;

    location / {
        root /var/www/kudos_phantom/frontend/dist;
        try_files $uri /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8001/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```
Then enable and restart:
```bash
sudo ln -s /etc/nginx/sites-available/phantom /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

---

## 🔒 Step 4: Firewall & Networking

Open the required ports on your VPS (assuming UFW is used):
```bash
sudo ufw allow 8001/tcp    # Backend
sudo ufw allow 5173/tcp    # Frontend (if not using Nginx)
sudo ufw allow 80/tcp      # HTTP (if using Nginx)
sudo ufw reload
```

## 🚀 Final Verification
1. **Backend:** Visit `http://your_server_ip:8001/docs` $\to$ Should see FastAPI Swagger UI.
2. **Frontend:** Visit `http://your_server_ip:5173` (or IP if using Nginx) $\to$ Should see the Login Page.
3. **Trade Test:** Start a Paper Trade $\to$ Verify the request goes to port 8001 and returns 200 OK.
