from passlib.context import CryptContext
from app.database.models import init_db, SessionLocal, User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def seed_admin():
    init_db()
    db = SessionLocal()
    
    admin_username = "admin"
    admin_password = "admin123" 
    
    existing_user = db.query(User).filter(User.username == admin_username).first()
    if existing_user:
        print(f"Admin user '{admin_username}' already exists. Skipping.")
        return

    hashed_password = pwd_context.hash(admin_password)
    admin = User(username=admin_username, password_hash=hashed_password)
    
    db.add(admin)
    db.commit()
    db.close()
    print(f"✅ Admin user created successfully!")
    print(f"Username: {admin_username}")
    print(f"Password: {admin_password}")

if __name__ == "__main__":
    seed_admin()

if __name__ == "__main__":
    seed_admin()
