# migrate_add_gestation_notes.py
from pathlib import Path
from datetime import datetime
import sqlite3

def main():
    base_dir = Path(__file__).resolve().parent
    db_path = base_dir / "erp.db"

    print(f"[i] Using DB path: {db_path}")

    if not db_path.exists():
        print("[!] erp.db not found here. Run this from the Cattle folder where erp.db lives.")
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # show current columns
    print("\n[i] Current gestation table schema:")
    cur.execute("PRAGMA table_info(gestation)")
    rows = cur.fetchall()
    if not rows:
        print("   [!] gestation table not found.")
        conn.close()
        return

    col_names = [r[1] for r in rows]
    for r in rows:
        cid, name, coltype, notnull, dflt, pk = r
        print(f"   cid={cid} name={name} type={coltype} notnull={notnull} dflt={dflt} pk={pk}")

    if "notes" in col_names:
        print("\n[i] Column 'notes' already exists on gestation. Nothing to do.")
        conn.close()
        return

    # backup first
    bak_name = f"erp.db.bak.{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    bak_path = base_dir / bak_name
    db_bytes = db_path.read_bytes()
    bak_path.write_bytes(db_bytes)
    print(f"\n[+] Backup created: {bak_path}")

    # add column
    print(" - Adding column 'notes' to table 'gestation' ...")
    cur.execute("ALTER TABLE gestation ADD COLUMN notes VARCHAR")
    conn.commit()

    print("\n[i] Final gestation schema:")
    cur.execute("PRAGMA table_info(gestation)")
    for cid, name, coltype, notnull, dflt, pk in cur.fetchall():
        print(f"   cid={cid} name={name} type={coltype} notnull={notnull} dflt={dflt} pk={pk}")

    conn.close()
    print("\n[i] Done. Restart uvicorn and open /dashboard again.")

if __name__ == "__main__":
    main()
