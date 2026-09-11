#!/usr/bin/env python3
"""
Read-only diagnostic report for the two SQLite databases: which files, how
many rows each table has, which migrations have been applied, and whether
main.py is currently running.

Usage:
    python tools/db_inspect.py

Never writes to either database (every connection is opened read-only via
SQLite's `mode=ro` URI, so this can't accidentally mutate anything even on a
bug). Safe to run at any time, including while main.py is running -- in fact
that's the main reason it exists: AGENTS.md -> Database safety says to check
`ps aux | grep main.py` and confirm the exact DB state before any
out-of-band write, and this replaces the ad-hoc `sqlite3 ... "SELECT
COUNT(*) ..."` one-liners that were being retyped for that each time.

Limitations: table/row counts are the whole story for "is there data here",
not a health check -- it doesn't validate foreign keys, run PRAGMA
integrity_check, or inspect archive/ contents beyond counting files.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.database.backup import ARCHIVE_DIR, DB_FILES

TABLES_BY_DB = {
    "music": ["artists", "songs"],
    "tag": ["tags", "song_tags"],
}


def _is_main_py_running() -> bool:
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,args"], capture_output=True, text=True, timeout=5
        )
    except Exception:
        return False
    for line in result.stdout.splitlines():
        if "main.py" in line and "db_inspect.py" not in line:
            return True
    return False


def _table_count(conn: sqlite3.Connection, table: str):
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError:
        return None  # table doesn't exist yet


def _schema_version_rows(conn: sqlite3.Connection):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    )
    if not cur.fetchone():
        return None
    return conn.execute(
        "SELECT filename, applied_at FROM schema_version ORDER BY id"
    ).fetchall()


def inspect_one(name: str, path: Path) -> None:
    print(f"\n{name}.db -> {path}")
    if not path.exists():
        print("  does not exist")
        return

    print(f"  size: {path.stat().st_size / 1024:.1f} KB")

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for table in TABLES_BY_DB[name]:
            count = _table_count(conn, table)
            print(f"  {table}: {'?' if count is None else count} row(s)")

        versions = _schema_version_rows(conn)
        if versions is None:
            print("  schema_version: not present yet (bootstraps on next `python main.py` run)")
        else:
            print(f"  schema_version: {len(versions)} migration(s) applied")
            for filename, applied_at in versions:
                print(f"    - {filename} ({applied_at})")
    finally:
        conn.close()


def main() -> None:
    running = _is_main_py_running()
    print(
        "main.py running: "
        + ("YES -- avoid direct out-of-band writes, see AGENTS.md -> Database safety" if running else "no")
    )

    for name, path in DB_FILES.items():
        inspect_one(name, path)

    archive_count = len(list(ARCHIVE_DIR.glob("*.db"))) if ARCHIVE_DIR.exists() else 0
    print(f"\narchive/: {archive_count} backup file(s) in {ARCHIVE_DIR}")


if __name__ == "__main__":
    main()
