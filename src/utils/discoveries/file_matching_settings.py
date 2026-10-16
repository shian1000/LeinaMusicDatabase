"""Persisted user settings for "Create playlist from folder" matching, in one
JSON file: how similar a file's tag artist must be to a song's artist (same
title) to match it without asking (DEFAULT_AUTO_ACCEPT_ARTIST_SIMILARITY),
and the lowest score of the closest songs offered when nothing else matched
(DEFAULT_BEST_GUESS_MIN_SCORE)."""
import json

from upath import UPath

from config.constants import (
    BEST_GUESS_MIN_SCORE_RANGE,
    DEFAULT_AUTO_ACCEPT_ARTIST_SIMILARITY,
    DEFAULT_BEST_GUESS_MIN_SCORE,
    FILE_MATCHING_CONFIG_FILE,
    MIN_AUTO_ACCEPT_ARTIST_SIMILARITY,
)
from utils.common.debug import slog

_AUTO_ACCEPT = "auto_accept_artist_similarity"
_BEST_GUESS = "best_guess_min_score"
# key -> (default, lowest, highest)
_SETTINGS = {
    _AUTO_ACCEPT: (DEFAULT_AUTO_ACCEPT_ARTIST_SIMILARITY, MIN_AUTO_ACCEPT_ARTIST_SIMILARITY, 1.0),
    _BEST_GUESS: (DEFAULT_BEST_GUESS_MIN_SCORE, *BEST_GUESS_MIN_SCORE_RANGE),
}


def _config_path() -> UPath:
    return UPath(FILE_MATCHING_CONFIG_FILE)


def _read() -> dict:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        slog(f"Failed to read file matching config, using defaults: {e}")
        return {}
    return data if isinstance(data, dict) else {}


def _load(key: str) -> float:
    """The persisted value, else the default (missing, unreadable or out of
    range)."""
    default, lowest, highest = _SETTINGS[key]
    try:
        value = float(_read()[key])
    except (KeyError, TypeError, ValueError):
        return default
    return value if lowest <= value <= highest else default


def _save(key: str, value: float) -> None:
    _, lowest, highest = _SETTINGS[key]
    if not lowest <= value <= highest:
        raise ValueError(f"must be between {lowest} and {highest}")
    data = _read()
    data[key] = value
    _config_path().write_text(json.dumps(data, indent=2))


def load_auto_accept_artist_similarity() -> float:
    return _load(_AUTO_ACCEPT)


def save_auto_accept_artist_similarity(value: float) -> None:
    _save(_AUTO_ACCEPT, value)


def load_best_guess_min_score() -> float:
    return _load(_BEST_GUESS)


def save_best_guess_min_score(value: float) -> None:
    _save(_BEST_GUESS, value)
