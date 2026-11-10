"""Persisted override for the folder where music.db and tag.db live
(Settings.database_dir).

Read once by settings.py at process startup, so a change made through the
Settings menu only takes effect after the app is restarted -- see
music_db_manager.py / tag_db_manager.py / backup.py / migrations.py, which
all snapshot Settings.database_dir into module-level constants at import
time.
"""
from typing import Optional
from upath import UPath
from config.constants import DATABASE_LOCATION_CONFIG_FILE
from utils.common.json_file import read_json, write_json


def _config_path() -> UPath:
    return UPath(DATABASE_LOCATION_CONFIG_FILE)


def load_database_dir_override() -> Optional[str]:
    """Return the persisted database folder override, or None if unset/unreadable."""
    return read_json(_config_path(), {}, "Database location config").get("database_dir") or None


def save_database_dir_override(path_str: Optional[str]) -> None:
    """Persist the database folder override, or clear it when path_str is None."""
    path = _config_path()
    if path_str is None:
        if path.exists():
            path.unlink()
        return
    write_json(path, {"database_dir": path_str}, atomic=True)  # read at every start
