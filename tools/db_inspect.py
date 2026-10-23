#!/usr/bin/env python3
"""
Read-only diagnostic report for the SQLite databases in DB_FILES (music,
tag, files): which files, how
many rows each table has, which migrations have been applied, and whether
the app (or the automatic inbox pass) is using them right now.

Usage:
    python tools/db_inspect.py

Never writes to either database (every connection is opened read-only via
SQLite's `mode=ro` URI, so this can't accidentally mutate anything even on a
bug). Safe to run at any time, including while main.py is running -- in fact
that's the main reason it exists: AGENTS.md -> Database safety says to check
that main.py isn't running and confirm the exact DB state before any
out-of-band write, and this replaces the ad-hoc `sqlite3 ... "SELECT
COUNT(*) ..."` one-liners that were being retyped for that each time.

Whether the app runs is read from its lock (utils/database/app_lock.py: every
open app holds it shared, the automatic pass exclusive), not from `ps`: a
process list can't tell this project's main.py from another one, and when
the check itself fails the answer is "unknown", never a reassuring "no".

Limitations: table/row counts are the whole story for "is there data here",
not a health check -- it doesn't validate foreign keys, run PRAGMA
integrity_check, or inspect archive/ contents beyond counting files.
"""

import fcntl
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.database import app_lock
from utils.database.backup import ARCHIVE_DIR, DB_FILES

TABLES_BY_DB = {
    "music": ["artists", "songs"],
    "tag": ["tags", "song_tags"],
    "files": ["files"],
}


def app_state() -> str:
    """"running", "not running" or "unknown". Takes the app lock exclusively
    for an instant (an app starting at that moment waits that long) and
    never creates the lock file."""
    if not app_lock.LOCK_FILE.exists():
        return "not running"  # no app has run against this database folder
    try:
        with open(app_lock.LOCK_FILE, "a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return "running"
            fcntl.flock(handle, fcntl.LOCK_UN)
            return "not running"
    except OSError:
        return "unknown"


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
        # A database added to DB_FILES later still gets its migrations listed
        for table in TABLES_BY_DB.get(name, []):
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
    state = app_state()
    print("main.py running: " + {
        "running": "YES (or the automatic inbox pass) -- avoid direct out-of-band writes, "
                   "see AGENTS.md -> Database safety",
        "not running": "no",
        "unknown": f"UNKNOWN -- couldn't check {app_lock.LOCK_FILE}; treat it as running",
    }[state])

    for name, path in DB_FILES.items():
        inspect_one(name, path)

    archive_count = len(list(ARCHIVE_DIR.glob("*.db"))) if ARCHIVE_DIR.exists() else 0
    print(f"\narchive/: {archive_count} backup file(s) in {ARCHIVE_DIR}")


if __name__ == "__main__":
    main()
