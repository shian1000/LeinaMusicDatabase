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
