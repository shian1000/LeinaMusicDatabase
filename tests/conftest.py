import shutil
import sys
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# Before anything imports settings.py: no test may reach the owner's real
# databases or .env. music_db_manager / tag_db_manager / backup / song_references
# snapshot Settings.database_dir at import, so a test that forgot to patch one
# of them read (or wrote) the real music.db / tag.db - and failed only when the
# app or the inbox timer happened to be changing or locking them. Pointed at a
# folder that doesn't exist, such a test now fails every time ("unable to open
# database file"). Without .env the drive-path Settings are None, so users,
# the inbox and the library are skipped unless a test sets them.
_ISOLATED_DIR = Path(tempfile.mkdtemp(prefix="musicdatabase-tests-"))

from utils.common import env_file  # noqa: E402
from utils.database import database_location  # noqa: E402

env_file.ENV_FILE = _ISOLATED_DIR / ".env"
database_location.load_database_dir_override = lambda: str(_ISOLATED_DIR / "no-database")

# The logs too: debug.log is appended to whatever the verbosity, and
# manage_youtube_playlists.py attaches youtube_link_cache.log at import unless
# its logger already has a handler.
import logging  # noqa: E402

from utils.common import debug  # noqa: E402

debug.DEBUG_LOG_FILE = _ISOLATED_DIR / "debug.log"
# Whatever the owner set in Settings -> Debug log, tests log only failures
# and never print diagnostics; Settings writes this .debug, not the real one
debug.DEBUG_FILE = _ISOLATED_DIR / ".debug"
debug.VERBOSITY_LEVEL, debug.DEBUG_ENABLED = 0, False

# check_spelling() answers: saved every 20 and when the process exits
from utils.common import spellcheck_cache  # noqa: E402

spellcheck_cache._CACHE_PATH = _ISOLATED_DIR / "spellcheck_cache.json"

# Whether MusicBrainz knows an artist: the same, never the owner's cache
from utils.common import musicbrainz_artists  # noqa: E402

musicbrainz_artists.CACHE_PATH = _ISOLATED_DIR / "musicbrainz_artists.json"

# Settings -> "Name cleanup": the steps' defaults, never the owner's choice
from utils.common.sanitize import settings as sanitize_settings  # noqa: E402

sanitize_settings.CONFIG_PATH = _ISOLATED_DIR / "sanitize_config.json"
sanitize_settings.forget_cache()

# Settings -> "Run all: steps": every step on, never the owner's choice
from utils.common import run_all_settings  # noqa: E402

run_all_settings.CONFIG_PATH = _ISOLATED_DIR / "run_all_config.json"

# Settings -> "Automation" (the rules' defaults, the default intervals) and the
# decision log every prompt appends to: never the owner's files
from utils.common import automation_settings, decision_log  # noqa: E402

automation_settings.CONFIG_PATH = _ISOLATED_DIR / "automation_config.json"
automation_settings.STATE_PATH = _ISOLATED_DIR / "automation_state.json"
automation_settings.MISSES_PATH = _ISOLATED_DIR / "automation_misses.json"
decision_log.LOG_PATH = _ISOLATED_DIR / "decision_log.jsonl"

# Settings -> Discovery modules and the Statistics counters, every kind: a
# test reconciling a config once rewrote the owner's discovery_modules_config.json
import dataclasses  # noqa: E402

from utils.discoveries import discovery_settings  # noqa: E402

for _kind, _spec in discovery_settings.KINDS.items():
    discovery_settings.KINDS[_kind] = dataclasses.replace(
        _spec, config_file=_ISOLATED_DIR / f"discovery_modules_config_{_kind}.json",
        stats_file=_ISOLATED_DIR / f"discovery_fetcher_stats_{_kind}.json")
_link_log = logging.getLogger("youtube_link_cache")
_link_log.addHandler(logging.FileHandler(_ISOLATED_DIR / "youtube_link_cache.log", encoding="utf-8"))
_link_log.setLevel(logging.INFO)
_link_log.propagate = False


def pytest_unconfigure(config):
    shutil.rmtree(_ISOLATED_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def no_real_db_album_lookup(monkeypatch):
    # is_blacklisted_album(title, artist) asks the real music.db whether the
    # artist already has that album (albums.artist_has_album()). Tests that
    # want the exception patch it themselves; none may read the user's DB.
    from utils.database import albums
    monkeypatch.setattr(albums, "artist_has_album", lambda artist_name, title: False)


@pytest.fixture(autouse=True)
def no_musicbrainz_artist_search(monkeypatch):
    # artist_on_musicbrainz() asks MusicBrainz for a name it hasn't cached:
    # unreachable in tests (None, nothing cached) unless a test answers it
    from utils.common import musicbrainz_artists
    monkeypatch.setattr(musicbrainz_artists, "mb_get", lambda url, params, **kwargs: None)
    musicbrainz_artists.forget_cache()


@pytest.fixture(autouse=True)
def no_musicbrainz_release_group_lookup(monkeypatch):
    # An album's year may ask MusicBrainz for its release group's own date
    # (_release_group_first_year()): never the real one in tests
    from utils.discoveries.discovery_modules import music_brainz_fetcher
    monkeypatch.setattr(music_brainz_fetcher, "mb_get", lambda url, params, **kwargs: None)
