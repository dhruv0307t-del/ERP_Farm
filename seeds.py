# seeds.py
"""
Seed script for Cattle ERP WITHOUT passlib/bcrypt.
Creates Admin user: username="admin", password="adminpass"
"""

from datetime import datetime
from sqlmodel import SQLModel, Field, Session, create_engine, select
import hashlib, os, binascii

DATABASE_URL = "sqlite:///erp.db"
engine = create_engine(DATABASE_URL, echo=False)

PBKDF2_ITER = 180000
HASH_NAME = "sha256"
SALT_BYTES = 16

# ---------- USER MODEL ----------
class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    hashed_password: str
    full_name: str | None = None
    is_admin: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------- HASH UTILS ----------
def pbkdf2_hash(password: str):
    password = password.encode()
    salt = os.urandom(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(HASH_NAME, password, salt, PBKDF2_ITER)
    return (
        f"pbkdf2_sha256$"
        f"{PBKDF2_ITER}$"
        f"{binascii.hexlify(salt).decode()}$"
        f"{binascii.hexlify(dk).decode()}"
    )


def pbkdf2_verify(password: str, stored: str):
    try:
        scheme, it_s, salt_hex, hash_hex = stored.split("$")
        assert scheme == "pbkdf2_sha256"
        it = int(it_s)
        salt = binascii.unhexlify(salt_hex)
        expected = binascii.unhexlify(hash_hex)
        dk = hashlib.pbkdf2_hmac(HASH_NAME, password.encode(), salt, it)
        return hashlib.compare_digest(dk, expected)
    except Exception:
        return False


# ---------- SEED SCRIPT ----------
def run():
    print("Running seeds.py — creating admin user...")

    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        existing = session.exec(select(User).where(User.username == "admin")).first()

        admin_username = "admin"
        admin_password = "adminpass"

        hashed = pbkdf2_hash(admin_password)

        # If user already exists
        if existing:
            if pbkdf2_verify(admin_password, existing.hashed_password):
                print("Admin user already exists. Password OK.")
                return
            else:
                print("Admin exists but password mismatched — updating password...")
                existing.hashed_password = hashed
                session.commit()
                print("Password updated.")
                return

        # Create new admin user
        admin = User(
            username=admin_username,
            hashed_password=hashed,
            full_name="Super Admin",
            is_admin=True,
        )

        session.add(admin)
        session.commit()

        print("Admin created → username: admin | password: adminpass")


if __name__ == "__main__":
    run()
