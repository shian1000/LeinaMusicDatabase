"""Run a manual script against temporary copies of the databases, never the real ones.

Call enter_sandbox() before the script imports anything from the app: settings.py,
music_db_manager.py and the rest snapshot the database folder at import. The copies,
an empty .env and the logs go to a fresh temp folder, which is left in place so the
result can be looked at afterwards; the real music.db / tag.db / files.db, .env,
.debug, debug.log and youtube_link_cache.log are only read (the databases, for the copy)."""
import logging
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_FILES = ("music.db", "tag.db", "files.db")


def _ensure_src_on_path() -> None:
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _copy_database(source: Path, target: Path) -> None:
    # SQLite's own backup: a consistent copy even while the app has the file open
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


def enter_sandbox(verbosity: int | None = None, source_dir: Path | None = None) -> Path:
    """Copy the databases into a temp folder and point the app at it; returns the folder.

    verbosity turns on the debug output in this process only (instead of writing .debug)."""
    _ensure_src_on_path()
    from utils.common import debug, env_file
    from utils.database import database_location

    if source_dir is None:
        source_dir = Path(database_location.load_database_dir_override() or ROOT / "src" / "database")
    sandbox = Path(tempfile.mkdtemp(prefix="musicdatabase-manual-"))
    database_dir = sandbox / "database"
    database_dir.mkdir()
    for name in DB_FILES:
        if (source_dir / name).exists():
            _copy_database(source_dir / name, database_dir / name)

    database_location.load_database_dir_override = lambda: str(database_dir)
    env_file.ENV_FILE = sandbox / ".env"
    debug.DEBUG_LOG_FILE = sandbox / "debug.log"
    if verbosity is not None:
        debug.DEBUG_ENABLED = True
        debug.VERBOSITY_LEVEL = verbosity
    link_log = logging.getLogger("youtube_link_cache")
    if not link_log.handlers:
        link_log.addHandler(logging.FileHandler(sandbox / "youtube_link_cache.log", encoding="utf-8"))
        link_log.setLevel(logging.INFO)
        link_log.propagate = False
    print(f"Working on copies of the databases in {sandbox} (the real ones are untouched)")
    return sandbox
