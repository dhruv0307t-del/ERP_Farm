# migrate_add_breeding_event_date.py
"""
Quick migration: add event_date and notes columns to breedingevent table if missing.
Creates a backup of erp.db first.
"""
import sqlite3
from datetime import datetime
import os
import shutil
DB = os.path.join(os.getcwd(), "erp.db")

print(f"[i] Using DB path: {DB}")
if not os.path.exists(DB):
    raise SystemExit("[!] erp.db not found in current directory")

bak = f"{DB}.bak.{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
shutil.copy2(DB, bak)
print(f"[+] Backup created: {bak}")

con = sqlite3.connect(DB)
cur = con.cursor()

# helper
def has_column(table, col):
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    return col in cols

# Add event_date if missing
if not has_column("breedingevent", "event_date"):
    print(" - Adding column 'event_date DATE' to table 'breedingevent' ...")
    cur.execute("ALTER TABLE breedingevent ADD COLUMN event_date DATE")
else:
    print(" - event_date exists on breedingevent")

# Add notes if missing
if not has_column("breedingevent", "notes"):
    print(" - Adding column 'notes TEXT' to table 'breedingevent' ...")
    cur.execute("ALTER TABLE breedingevent ADD COLUMN notes TEXT")
else:
    print(" - notes exists on breedingevent")

con.commit()
con.close()
print("\n[i] Migration finished. If app still errors, restart uvicorn from this folder:")
print("    pkill -f uvicorn || true")
print("    python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000")
