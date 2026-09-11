"""
Automatic backup of the SQLite databases (music.db, tag.db) into
src/database/archive/, using SQLite's online backup API so a copy is safe to
take even while the app holds an open connection.

Two call sites trigger a backup:
- main.py, once per calendar day at startup (backup_if_needed_today()).
- migrations.run_pending_migrations(), unconditionally, right before applying
  any pending schema change.

See docs/runbooks/database.md for the full picture (why this exists, how to
restore, retention).
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from settings import Settings
from utils.common.debug import mlog, slog

ARCHIVE_DIR = Path(str(Settings.database_dir)) / "archive"
DB_FILES = {
    "music": Path(str(Settings.database_dir)) / "music.db",
    "tag": Path(str(Settings.database_dir)) / "tag.db",
}

_TIMESTAMP_FORMAT = "%y%m%d-%H%M"
_DATE_FORMAT = "%y%m%d"


def _unique_dest_path(name: str, timestamp: str) -> Path:
    """
    `music.YYMMDD-HHMM.db`, or `music.YYMMDD-HHMM_2.db` / `_3` / ... if that
    name is already taken. Needed because two backups can legitimately
    happen within the same minute -- e.g. main.py runs the daily backup and
    then immediately checks for pending migrations, which takes its own
    backup right before applying anything. Without this, the second backup
    would silently overwrite the first instead of being a distinct snapshot.
    """
    dest_path = ARCHIVE_DIR / f"{name}.{timestamp}.db"
    suffix = 1
    while dest_path.exists():
        suffix += 1
        dest_path = ARCHIVE_DIR / f"{name}.{timestamp}_{suffix}.db"
    return dest_path


def _backup_one(src_path: Path, name: str, timestamp: str) -> Path:
    dest_path = _unique_dest_path(name, timestamp)
    src_conn = sqlite3.connect(str(src_path))
    dest_conn = sqlite3.connect(str(dest_path))
    try:
        with dest_conn:
            src_conn.backup(dest_conn)
    finally:
        src_conn.close()
        dest_conn.close()
    return dest_path


def backup_databases(reason: str = "manual") -> list[Path]:
    """
    Snapshot every existing DB file into archive/, timestamped to the minute
    (same `<name>.YYMMDD-HHMM.db` convention as the pre-existing manual
    backups already in that folder).

    Uses SQLite's online backup API (`sqlite3.Connection.backup()`), not a
    plain file copy, so it's safe regardless of a concurrently open
    connection. Measured at ~2ms per database at this project's current DB
    sizes (a few hundred KB) -- cheap enough to call unconditionally rather
    than trying to make it async.
    """
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime(_TIMESTAMP_FORMAT)
    created = []
    for name, src_path in DB_FILES.items():
        if not src_path.exists():
            continue
        created.append(_backup_one(src_path, name, timestamp))
    if created:
        mlog(f"Database backup ({reason}): {[p.name for p in created]}")
    return created


def has_backup_today() -> bool:
    if not ARCHIVE_DIR.exists():
        return False
    today = datetime.now().strftime(_DATE_FORMAT)
    return any(ARCHIVE_DIR.glob(f"music.{today}-*.db"))


def backup_if_needed_today(reason: str = "daily-startup") -> list[Path]:
    """
    Called once at app startup. No-op if a backup already exists for today
    (checked by scanning archive/ filenames, not a separate marker file, to
    stay consistent with the pre-existing naming convention).

    Failures are logged and swallowed, never raised -- a backup problem
    (e.g. a full disk) must not block the user from opening the app.
    """
    if has_backup_today():
        return []
    try:
        return backup_databases(reason=reason)
    except Exception as exc:
        slog(f"Daily database backup failed (non-fatal): {exc}")
        return []
