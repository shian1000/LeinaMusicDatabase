"""State files (config.constants.PROJECT_ROOT): every settings / state / debug
file is resolved against the repository folder, never the current directory,
so the app, the systemd timer and tools/ started from elsewhere read the same
files - the database folder override above all (COM-06)."""
import importlib
from pathlib import Path

import pytest

from config import constants

STATE_FILES = [
    "RECENT_DIRS_FILE", "DISCOVERY_MODULES_CONFIG_FILE", "DISCOVERY_MODULES_YEAR_CONFIG_FILE",
    "DATABASE_LOCATION_CONFIG_FILE", "DISCOVERY_STATS_FILE", "DISCOVERY_STATS_YEAR_FILE",
    "FETCH_DATA_CONFIG_FILE", "FILE_MATCHING_CONFIG_FILE", "PUBLIC_DB_STATE_FILE", "STARTUP_STATE_FILE",
    "FULL_ALBUM_IGNORE_FILE", "GENRE_LOOKUP_MISSES_FILE", "SPELLCHECK_CACHE_FILE", "ARTIST_SPLIT_IGNORE_FILE",
]


def test_project_root_is_the_repository():
    assert (constants.PROJECT_ROOT / "main.py").is_file()
    assert constants.PROJECT_ROOT == Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", STATE_FILES)
def test_state_file_constants_are_inside_the_project(name):
    path = Path(getattr(constants, name))
    assert path.is_absolute() and path.parent in (constants.PROJECT_ROOT, constants.PROJECT_ROOT / "data")


@pytest.mark.parametrize("module, getter, name", [
    ("utils.database.database_location", "_config_path", "database_location_config.json"),
    ("utils.database.fetch_data_settings", "_config_path", "fetch_data_config.json"),
    ("utils.discoveries.file_matching_settings", "_config_path", "file_matching_config.json"),
    ("utils.discoveries.discovery_stats", "_stats_path", "discovery_fetcher_stats.json"),
    ("utils.discoveries.discovery_stats", "_year_stats_path", "discovery_fetcher_stats_years.json"),
    ("utils.database.public_database", "_state_path", "data/public_database.json"),
])
def test_paths_ignore_the_current_directory(module, getter, name, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = Path(str(getattr(importlib.import_module(module), getter)()))

    assert path == constants.PROJECT_ROOT / name


def test_other_project_files_ignore_the_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from utils.common import debug
    from utils.youtube import manage_youtube_playlists as playlists, yt_cache
    from utils.discoveries.discovery_modules import spotify_fetcher

    root = constants.PROJECT_ROOT
    assert debug.PROJECT_ROOT == root
    assert yt_cache.CACHE_FILE == root / "yt_playlist_cache.json"
    assert Path(playlists.TOKEN_PATH) == root / ".secrets" / "token.json"
    assert Path(playlists.CLIENT_SECRET_PATH) == root / ".secrets" / "client_secret.json"
    assert Path(spotify_fetcher.DEBUG_SNAPSHOT_FILE) == root / "debug_spotify.html"
