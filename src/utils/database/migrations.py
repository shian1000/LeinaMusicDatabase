"""
Minimal forward-only schema migration runner for music.db and tag.db.

Each database gets its own numbered migration track under
`migrations/<db_name>/` (repo root), tracked by a `schema_version` table
inside that database. A migration is a plain `.sql` file, applied in
filename order.

Bootstrapping an existing installation: the first time this runs against a
DB that predates this mechanism (no `schema_version` table yet), every
`.sql` file already present on disk at that moment is recorded as applied
WITHOUT being executed -- `Base.metadata.create_all()` already brought a
fresh or up-to-date install to the current model, so those files describe
schema that's already there. Only `.sql` files added *after* that point ever
actually run.

Never edit an already-applied migration file's contents -- add a new one
instead. See docs/runbooks/database.md for the full procedure and the
reasoning (this repo's schema changed by hand at least 3 times in the past
with no record of which DB copies got the change -- that's the problem this
replaces).

Note on safety: a migration file is applied via `executescript()`, which can
partially apply a multi-statement script before failing. The real safety net
here is the mandatory backup taken immediately before running any pending
migration (see `backup_databases`), not transactional rollback -- keep each
migration file to one logical change so there's nothing to partially apply.
"""

import sqlite3
from pathlib import Path

from settings import Settings
from utils.common.debug import mlog, slog
from utils.database.backup import backup_databases

MIGRATIONS_ROOT = Path(__file__).resolve().parents[3] / "migrations"
DB_FILES = {
    "music": Path(str(Settings.database_dir)) / "music.db",
    "tag": Path(str(Settings.database_dir)) / "tag.db",
}


def _migration_files(db_name: str) -> list[Path]:
    directory = MIGRATIONS_ROOT / db_name
    if not directory.exists():
        return []
    return sorted(directory.glob("*.sql"))


def _ensure_schema_version_table(conn: sqlite3.Connection) -> bool:
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


def _applied_filenames(conn: sqlite3.Connection) -> set:
    cur = conn.execute("SELECT filename FROM schema_version")
    return {row[0] for row in cur.fetchall()}


def run_pending_migrations(db_name: str) -> list:
    """
    Apply any not-yet-applied migrations for one database ("music" or
    "tag"). Returns the filenames actually executed -- empty on a no-op run,
    which is the common case (this is cheap to call on every startup).
    """
    db_path = DB_FILES[db_name]
    if not db_path.exists():
        # No DB file yet -- nothing to migrate against. Whatever creates it
        # (create_music_db()/create_tag_db(), or a restored backup) is
        # responsible for the initial schema; the next startup will see the
        # file and bootstrap schema_version against it normally.
        return []

    files = _migration_files(db_name)
    if not files:
        return []

    conn = sqlite3.connect(str(db_path))
    try:
        just_bootstrapped = _ensure_schema_version_table(conn)

        if just_bootstrapped:
            for f in files:
                conn.execute(
                    "INSERT INTO schema_version (filename) VALUES (?)", (f.name,)
                )
            conn.commit()
            mlog(
                f"{db_name}.db: adopted migrations, recorded "
                f"{len(files)} existing file(s) as already applied"
            )
            return []

        applied = _applied_filenames(conn)
        pending = [f for f in files if f.name not in applied]
        if not pending:
            return []

        backup_databases(reason=f"pre-migration ({db_name})")

        executed = []
        for f in pending:
            try:
                conn.executescript(f.read_text())
                conn.execute(
                    "INSERT INTO schema_version (filename) VALUES (?)", (f.name,)
                )
                conn.commit()
                executed.append(f.name)
                mlog(f"{db_name}.db: applied migration {f.name}")
            except Exception as exc:
                slog(f"{db_name}.db: migration {f.name} FAILED, stopping here: {exc}")
                raise
        return executed
    finally:
        conn.close()


def run_all_pending_migrations() -> dict:
    """Runs both DBs' pending migrations. Called once at app startup."""
    return {name: run_pending_migrations(name) for name in DB_FILES}
