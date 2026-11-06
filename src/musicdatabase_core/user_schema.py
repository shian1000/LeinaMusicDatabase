"""Applying migrations/user/ to a user.db. No backups or logging here: whoever
runs the migrations decides about those."""

import sqlite3
from pathlib import Path

USER_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "user"


def migration_files(directory: Path = USER_MIGRATIONS_DIR) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(directory.glob("*.sql"))


def ensure_schema_version_table(conn: sqlite3.Connection) -> bool:
    """Creates schema_version if missing. Returns True if it just created it
    (i.e. this DB predates the migration mechanism)."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    )
    if cur.fetchone():
        return False
    conn.execute(
        "CREATE TABLE schema_version ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "filename TEXT UNIQUE NOT NULL, "
        "applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.commit()
    return True


def applied_filenames(conn: sqlite3.Connection) -> set:
    cur = conn.execute("SELECT filename FROM schema_version")
    return {row[0] for row in cur.fetchall()}


def apply_migration(conn: sqlite3.Connection, f: Path) -> None:
    """Runs one migration file and records it, all or nothing.
    executescript() can't take parameters, hence the quoted literal."""
    name = f.name.replace("'", "''")
    script = (
        "BEGIN;\n"
        f"{f.read_text()}\n;\n"
        f"INSERT INTO schema_version (filename) VALUES ('{name}');\n"
        "COMMIT;"
    )
    try:
        conn.executescript(script)
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise


def apply_user_migrations(db_path: Path, directory: Path = USER_MIGRATIONS_DIR) -> list[str]:
    """Create or bring up to date a user.db. The names of the files applied."""
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema_version_table(conn)
        applied = applied_filenames(conn)
        executed = []
        for f in migration_files(directory):
            if f.name not in applied:
                apply_migration(conn, f)
                executed.append(f.name)
        return executed
    finally:
        conn.close()
