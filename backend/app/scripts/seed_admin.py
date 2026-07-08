import bcrypt
from app.database.models import init_db, SessionLocal, User

def seed_admin():
    init_db()
    db = SessionLocal()
    
    admin_username = "admin"
    admin_password = "admin_password_123" 
    
    existing_user = db.query(User).filter(User.username == admin_username).first()
    if existing_user:
        print(f"Admin user '{admin_username}' already exists. Skipping.")
        db.close()
        return

    # Use bcrypt directly to avoid passlib version incompatibility issues
    # bcrypt.hashpw expects bytes
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(admin_password.encode('utf-8'), salt).decode('utf-8')
    
    admin = User(username=admin_username, password_hash=hashed_password)
    
    db.add(admin)
    db.commit()
    db.close()
    print(f"✅ Admin user created successfully using direct bcrypt!")
    print(f"Username: {admin_username}")
    print(f"Password: {admin_password}")

if __name__ == "__main__":
    seed_admin()
