"""
Minimal forward-only schema migration runner for music.db, tag.db, files.db
and every user's user.db.

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
from utils.database.backup import DB_FILES as BACKED_UP_DB_FILES, backup_databases
from utils.database.users import user_db_files

# migrations/user/ - applied to every user's own user.db (utils/database/users.py)
USER_TRACK = "user"
# migrations/files/ - the music library index (utils/database/files_index.py)
FILES_TRACK = "files"

MIGRATIONS_ROOT = Path(__file__).resolve().parents[3] / "migrations"
DB_FILES = {
    "music": Path(str(Settings.database_dir)) / "music.db",
    "tag": Path(str(Settings.database_dir)) / "tag.db",
    # Created by initialize_db(), never adopted: it has schema_version from the start
    FILES_TRACK: BACKED_UP_DB_FILES[FILES_TRACK],
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
    Apply any not-yet-applied migrations for one database ("music", "tag"
    or "files"). Returns the filenames actually executed -- empty on a no-op run,
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

        return _apply_pending(conn, files, f"{db_name}.db", backup_first=True)
    finally:
        conn.close()


def _apply_pending(conn: sqlite3.Connection, files: list[Path], label: str, backup_first: bool) -> list:
    applied = _applied_filenames(conn)
    pending = [f for f in files if f.name not in applied]
    if not pending:
        return []

    if backup_first:
        backup_databases(reason=f"pre-migration ({label})")

    executed = []
    for f in pending:
        try:
            conn.executescript(f.read_text())
            conn.execute(
                "INSERT INTO schema_version (filename) VALUES (?)", (f.name,)
            )
            conn.commit()
            executed.append(f.name)
            mlog(f"{label}: applied migration {f.name}")
        except Exception as exc:
            slog(f"{label}: migration {f.name} FAILED, stopping here: {exc}")
            raise
    return executed


def initialize_db(db_path: Path, track: str, label: str) -> list:
    """Create a new database with every migrations/<track>/ file applied.
    Unlike music.db/tag.db there is no bootstrap: a user.db or files.db
    always starts empty and gets its whole schema from the files. No backup -
    there's nothing in it yet."""
    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_schema_version_table(conn)
        return _apply_pending(conn, _migration_files(track), label, backup_first=False)
    finally:
        conn.close()


def initialize_user_db(db_path: Path) -> list:
    """Create a new user's user.db with every migrations/user/ file applied."""
    return initialize_db(db_path, USER_TRACK, db_path.parent.name)


def run_pending_user_migrations() -> dict:
    """Apply new migrations/user/ files to every user's user.db, after one
    backup of everything. {username: [applied files]}, only users that had
    something pending. Nothing when the users drive isn't mounted - the
    next startup with it mounted catches up."""
    files = _migration_files(USER_TRACK)
    if not files:
        return {}
    pending_dbs = {}
    for name, path in user_db_files().items():
        conn = sqlite3.connect(str(path))
        try:
            _ensure_schema_version_table(conn)
            if {f.name for f in files} - _applied_filenames(conn):
                pending_dbs[name] = path
        finally:
            conn.close()
    if not pending_dbs:
        return {}

    backup_databases(reason="pre-migration (users)")
    executed = {}
    for name, path in pending_dbs.items():
        conn = sqlite3.connect(str(path))
        try:
            executed[name] = _apply_pending(conn, files, f"user {name}", backup_first=False)
        finally:
            conn.close()
    return executed


def has_pending_migrations(db_name: str) -> bool:
    """Whether the app's next start would migrate this database - for code
    that runs without main.py (auto_resolve_requests.py) and mustn't touch
    a schema older than the models. Read-only."""
    db_path = DB_FILES[db_name]
    files = _migration_files(db_name)
    if not db_path.exists() or not files:
        return False
    conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone():
            return True
        return any(f.name not in _applied_filenames(conn) for f in files)
    finally:
        conn.close()


def run_all_pending_migrations() -> dict:
    """Runs every DB's pending migrations - music.db, tag.db, files.db and each
    user's user.db (under "users": {username: [files]}). Called once at app
    startup."""
    applied = {name: run_pending_migrations(name) for name in DB_FILES}
    applied["users"] = run_pending_user_migrations()
    return applied
