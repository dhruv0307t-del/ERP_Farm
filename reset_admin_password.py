# reset_admin_password.py
from sqlmodel import Session, create_engine, select
from app.main import pwdctx  # ensure this import is valid (app package)
import sqlite3

DB = "sqlite:///erp.db"

engine = create_engine(DB, echo=False)

def set_admin_password(new_password: str = "adminpass"):
    with Session(engine) as session:
        # adjust model import path if needed
        from app.models import User  # or from app.main import User (where your User model is)
        q = session.exec(select(User).where(User.username == "admin"))
        admin = q.first()
        if not admin:
            print("Admin user not found.")
            return
        hashed = pwdctx.hash(new_password)
        admin.hashed_password = hashed
        session.add(admin)
        session.commit()
        print(f"Admin password reset to: {new_password!r}")

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--password", "-p", default="adminpass", help="New admin password")
    args = p.parse_args()
    set_admin_password(args.password)
