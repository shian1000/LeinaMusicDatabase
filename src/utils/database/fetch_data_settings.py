"""Persisted user setting for how many songs a single "Fetch database data"
run processes at once (e.g. Fill missing albums), so a run can be limited to
a quick batch instead of churning through the whole database.
"""
import json
from upath import UPath
from utils.common.debug import slog
from config.constants import FETCH_DATA_CONFIG_FILE, DEFAULT_MAX_SONGS_PER_FETCH


def _config_path() -> UPath:
    return UPath(FETCH_DATA_CONFIG_FILE)


def load_max_songs_per_fetch() -> int:
    """Return the persisted max-songs-per-fetch setting, falling back to the
    default if the file is missing or unreadable."""
    path = _config_path()
    if not path.exists():
        return DEFAULT_MAX_SONGS_PER_FETCH

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        slog(f"Failed to read fetch data config, using default: {e}")
        return DEFAULT_MAX_SONGS_PER_FETCH

    value = data.get("max_songs_per_fetch") if isinstance(data, dict) else None
    # bool is an int too; "100", 0 or -5 would reach the fetch loop as they are
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        if value is not None or not isinstance(data, dict):
            slog(f"Fetch data config holds {data!r}, using the default")
        return DEFAULT_MAX_SONGS_PER_FETCH
    return value


def save_max_songs_per_fetch(value: int) -> None:
    path = _config_path()
    path.write_text(json.dumps({"max_songs_per_fetch": value}, indent=2))
