import uvicorn
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

if __name__ == "__main__":
    # Read port from .env or default to 8000
    port = int(os.getenv("BACKEND_PORT", 8000))
    
    print(f"🚀 Starting PHANTOM v2.5 Backend on port {port}...")
    # Run from 'backend' folder: we target the 'app.main:app' module
    # reload=False for production, but kept True for dev. 
    # In deployment guide, we use PM2 which handles restarts.
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
