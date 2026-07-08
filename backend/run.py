import uvicorn
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", 8000))


if __name__ == "__main__":
    print(f"🚀 Starting PHANTOM v2.5 Backend on {HOST}:{PORT}...")
    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=True,
    )