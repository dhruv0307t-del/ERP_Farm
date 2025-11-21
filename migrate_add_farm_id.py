#!/usr/bin/env python3
"""
ensure_farm_id.py

Check the schema of erp.db, show table info for animal and user,
backup the DB, and add farm_id INTEGER to animal and user if missing.

Usage:
  (activate your .venv)
  cd /path/to/project/Cattle    # make sure this is the same folder you run uvicorn from
  python ensure_farm_id.py
"""

import os
import sqlite3
import shutil
from datetime import datetime

DB_FILENAME = "erp.db"

def find_db():
    # prefer cwd DB (explicit), but also show absolute path
    cwd = os.getcwd()
    db_path = os.path.join(cwd, DB_FILENAME)
    return db_path

def backup_db(db_path):
    if not os.path.exists(db_path):
        print(f"[!] No DB found at: {db_path}")
        return False
    bak = f"{db_path}.bak.{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    shutil.copyfile(db_path, bak)
    print(f"[+] Backup created: {bak}")
    return True

def table_exists(conn, tname):
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (tname,))
    return cur.fetchone() is not None

def get_columns(conn, tname):
    cur = conn.cursor()
    try:
        cur.execute(f"PRAGMA table_info('{tname}')")
        rows = cur.fetchall()
        # rows: (cid, name, type, notnull, dflt_value, pk)
        cols = [r[1] for r in rows]
        return cols, rows
    except sqlite3.OperationalError as e:
        return None, str(e)

def add_column_if_missing(conn, tname, col_decl):
    # col_decl like "farm_id INTEGER"
    col = col_decl.split()[0]
    cols, _ = get_columns(conn, tname)
    if cols is None:
        print(f" - Table '{tname}' does not exist.")
        return False
    if col in cols:
        print(f" - Table '{tname}' already has column '{col}'")
        return False
    print(f" - Adding column '{col_decl}' to table '{tname}' ...")
    cur = conn.cursor()
    cur.execute(f"ALTER TABLE {tname} ADD COLUMN {col_decl};")
    return True

def show_schema(conn, tname):
    cols, rows = get_columns(conn, tname)
    if cols is None:
        print(f" - {tname}: ERROR -> {rows}")
        return
    print(f" - {tname} columns: {cols}")
    for r in rows:
        print(f"    cid={r[0]} name={r[1]} type={r[2]} notnull={r[3]} dflt={r[4]} pk={r[5]}")

def main():
    db_path = find_db()
    print(f"[i] Using DB path: {db_path}")
    if not os.path.exists(db_path):
        print("[!] DB file not found. Are you in the project root? (where erp.db lives)")
        print("Try: cd /Users/dhruvtomar/project/Cattle")
        return

    # show any other erp.db on disk (quick check)
    print("[i] Running quick filesystem check for other erp.db files (in current subtree)...")
    other = []
    for root, dirs, files in os.walk("."):
        if DB_FILENAME in files:
            other.append(os.path.abspath(os.path.join(root, DB_FILENAME)))
    other = sorted(set(other))
    for p in other:
        print("   found:", p)

    # connect
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        print("\n[i] Current schema snapshot:")
        for t in ("animal", "user", "farm"):
            exist = table_exists(conn, t)
            print(f" - table '{t}': exists={exist}")
            if exist:
                show_schema(conn, t)

        # if animal missing farm_id, backup + alter
        mutated = False
        if table_exists(conn, "animal"):
            cols, _ = get_columns(conn, "animal")
            if "farm_id" not in cols:
                print("\n[i] 'farm_id' missing on 'animal' -> will add (after backup).")
                backed = backup_db(db_path)
                if not backed:
                    print("[!] Backup failed or DB missing; aborting ALTER.")
                else:
                    mutated |= add_column_if_missing(conn, "animal", "farm_id INTEGER")
            else:
                print("\n[i] 'animal' already has farm_id.")
        else:
            print("\n[!] 'animal' table not present; nothing to do for it.")

        if table_exists(conn, "user"):
            cols, _ = get_columns(conn, "user")
            if "farm_id" not in cols:
                if not mutated:
                    # ensure backup exists if we didn't already backup for animal earlier
                    backup_db(db_path)
                mutated |= add_column_if_missing(conn, "user", "farm_id INTEGER")
            else:
                print("[i] 'user' already has farm_id.")
        else:
            print("[i] 'user' table not present; skipping user.farm_id.")

        if mutated:
            conn.commit()
            print("\n[+] Migration changes committed.")
        else:
            print("\n[*] No schema changes required.")

        # final schema show
        print("\n[i] Final schema snapshot:")
        for t in ("animal", "user", "farm"):
            exist = table_exists(conn, t)
            print(f" - table '{t}': exists={exist}")
            if exist:
                show_schema(conn, t)

        # quick counts
        cur = conn.cursor()
        for t in ("animal", "user"):
            if table_exists(conn, t):
                try:
                    cur.execute(f"SELECT COUNT(*) as c FROM {t}")
                    print(f" - {t}.count = {cur.fetchone()['c']}")
                except Exception as e:
                    print(f" - count {t}: error: {e}")
    finally:
        conn.close()

    print("\n[i] Done. If the app still errors, restart uvicorn from this same folder:")
    print("    pkill -f uvicorn || true")
    print("    python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000")

if __name__ == "__main__":
    main()
