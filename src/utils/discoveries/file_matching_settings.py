"""Persisted user setting for how similar a file's tag artist must be to a
song's artist (same title) for "Create playlist from folder" to match it
without asking - see DEFAULT_AUTO_ACCEPT_ARTIST_SIMILARITY."""
import json

from upath import UPath

from config.constants import (
    DEFAULT_AUTO_ACCEPT_ARTIST_SIMILARITY,
    FILE_MATCHING_CONFIG_FILE,
    MIN_AUTO_ACCEPT_ARTIST_SIMILARITY,
)
from utils.common.debug import slog


def _config_path() -> UPath:
    return UPath(FILE_MATCHING_CONFIG_FILE)


def load_auto_accept_artist_similarity() -> float:
    """The persisted threshold, else the default (missing, unreadable or out
    of range file)."""
    path = _config_path()
    if not path.exists():
        return DEFAULT_AUTO_ACCEPT_ARTIST_SIMILARITY
    try:
        value = float(json.loads(path.read_text())["auto_accept_artist_similarity"])
    except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as e:
        slog(f"Failed to read file matching config, using default: {e}")
        return DEFAULT_AUTO_ACCEPT_ARTIST_SIMILARITY
    if not MIN_AUTO_ACCEPT_ARTIST_SIMILARITY <= value <= 1:
        return DEFAULT_AUTO_ACCEPT_ARTIST_SIMILARITY
    return value


def save_auto_accept_artist_similarity(value: float) -> None:
    if not MIN_AUTO_ACCEPT_ARTIST_SIMILARITY <= value <= 1:
        raise ValueError(f"must be between {MIN_AUTO_ACCEPT_ARTIST_SIMILARITY} and 1")
    _config_path().write_text(json.dumps({"auto_accept_artist_similarity": value}, indent=2))
