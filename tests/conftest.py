# tests/test_scripts.py is an explicit scratch/manual-testing file (its own
# docstring: "meant to be a mess... nothing depends on this script"), not a
# regression test -- its body is entirely commented out except a
# `time.sleep(10000)`. Exclude it from collection: importing it pulls in
# `menu.song_actions`, which hits a real circular import
# (src/menu/song_actions/__init__.py <-> src/menu/main_menu/...), so letting
# pytest try to collect it just fails the whole run for a file that was never
# meant to assert anything.
collect_ignore = ["test_scripts.py"]


import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)


@pytest.fixture(autouse=True)
def no_real_db_album_lookup(monkeypatch):
    # is_blacklisted_album(title, artist) asks the real music.db whether the
    # artist already has that album (albums.artist_has_album()). Tests that
    # want the exception patch it themselves; none may read the user's DB.
    from utils.database import albums
    monkeypatch.setattr(albums, "artist_has_album", lambda artist_name, title: False)
