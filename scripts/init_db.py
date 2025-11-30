# scripts/init_db.py
# This script will be run once during Render build to initialize the master DB.
# It uses the same init_master_db() you already have in app.main and does NOT
# change any functionality – it just runs earlier, in a single process.

from app.main import init_master_db

def main():
    # Your existing init logic: create master tables, default data, etc.
    init_master_db()

if __name__ == "__main__":
    main()
