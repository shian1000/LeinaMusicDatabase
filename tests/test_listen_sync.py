"""Syncing a user's plays for Leina Radio's listening stats
(utils/database/listen_sync.py): migration 0009 required by `connect()`,
adding plays and reading them back from revision 0 and from a later one (own
counter, other counters untouched), a longer listened_ms updating a play (and
its revision, filling a missing duration) while a shorter or equal one
changes nothing, paging with `limit` / `more`, unknown keys and `source`
values kept, and bad batches storing nothing. Temp-file SQLite."""

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import folder_sync as fs
from utils.database import listen_sync as ls
from utils.database import playlist_sync as ps
from utils.database.migrations import initialize_user_db


@pytest.fixture
def conn(tmp_path):
    initialize_user_db(tmp_path / "user.db")
    with closing(ls.connect(tmp_path / "user.db")) as c:
        yield c


def _play(uid="3f2b-1", listened_ms=183400, **extra):
    play = {"uid": uid, "started_at": 1791839796631, "listened_ms": listened_ms, "duration_ms": 214000,
            "title": "Gruppa krovi", "artist": "Kino", "album": "Gruppa krovi", "song_id": 1234,
            "youtube_video_id": "abc123", "spotify_track_id": None, "source": "youtube",
            "playlist_uid": "a1c4", "playlist_name": "Do auta", "mood_uid": None, "global_mood_id": 5,
            "mood_name": "Do biegania"}
    play.update(extra)
    return play


def _post(conn, *plays):
    return ls.store_listens(conn, ls.parse_listens({"listens": list(plays)}))


def test_migration_exists_and_is_required(tmp_path):
    assert (repo_root / "migrations" / "user" / ls.REQUIRED_MIGRATION).exists()
    db_path = tmp_path / "user.db"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE schema_version (filename TEXT)")
    db.execute("INSERT INTO schema_version VALUES (?)", (fs.REQUIRED_MIGRATION,))
    db.commit()
    db.close()
    with pytest.raises(ls.NotMigratedError):
        ls.connect(db_path)


def test_post_then_get_from_zero_and_later(conn):
    assert ls.changes_since(conn, 0) == {"revision": 0, "listens": [], "more": False}
    assert _post(conn, _play("a"), _play("b")) == {"revision": 2, "added": 2, "updated": 0}
    got = ls.changes_since(conn, 0)
    assert got["revision"] == 2 and got["more"] is False
    assert got["listens"] == [{**_play("a"), "revision": 1}, {**_play("b"), "revision": 2}]
    assert ls.changes_since(conn, 1)["listens"] == [{**_play("b"), "revision": 2}]
    assert ls.changes_since(conn, 2) == {"revision": 2, "listens": [], "more": False}
    # Own counter: playlists' and folders' stay where they were
    assert ps.current_revision(conn) == 0 and fs.current_revision(conn) == 0
    assert ls.listen_count(conn) == 2


def test_longer_time_updates_shorter_does_not(conn):
    _post(conn, _play("a", listened_ms=1000, duration_ms=None), _play("b"))
    # Shorter or equal: nothing changes, not even the other fields
    assert _post(conn, _play("a", listened_ms=500, title="Other"), _play("a", listened_ms=1000)) == \
        {"revision": 2, "added": 0, "updated": 0}
    # Longer: listened_ms, the missing duration and the revision; the title stays
    assert _post(conn, _play("a", listened_ms=90000, duration_ms=214000, title="Other")) == \
        {"revision": 3, "added": 0, "updated": 1}
    [play] = ls.changes_since(conn, 2)["listens"]
    assert play == {**_play("a", listened_ms=90000, duration_ms=214000), "revision": 3}
    # A known duration isn't overwritten
    _post(conn, _play("a", listened_ms=95000, duration_ms=1))
    assert ls.changes_since(conn, 3)["listens"][0]["duration_ms"] == 214000


def test_paging_with_limit_and_more(conn):
    _post(conn, *[_play(f"p{i}") for i in range(5)])
    page = ls.changes_since(conn, 0, limit=2)
    assert [p["uid"] for p in page["listens"]] == ["p0", "p1"]
    assert page["revision"] == 2 and page["more"] is True
    page = ls.changes_since(conn, page["revision"], limit=2)
    assert [p["uid"] for p in page["listens"]] == ["p2", "p3"] and page["more"] is True
    page = ls.changes_since(conn, page["revision"], limit=2)
    assert [p["uid"] for p in page["listens"]] == ["p4"] and page == {**page, "revision": 5, "more": False}
    # Exactly `limit` left: no more, the counter
    assert ls.changes_since(conn, 3, limit=2)["more"] is False
    # Capped
    assert ls.changes_since(conn, 0, limit=10 ** 6)["more"] is False


def test_unknown_keys_and_sources_kept(conn):
    play = _play("a", source="radio-fm", rating={"stars": 4, "note": "ąę"}, skipped=True, revision=99)
    _post(conn, play)
    [got] = ls.changes_since(conn, 0)["listens"]
    expected = {**play, "revision": 1}  # the echoed revision isn't stored
    assert got == expected
    # Only the required keys: the rest come back as null
    _post(conn, {"uid": "b", "started_at": 1, "listened_ms": 0, "title": ""})
    [minimal] = ls.changes_since(conn, 1)["listens"]
    assert minimal["title"] == "" and minimal["song_id"] is None and minimal["duration_ms"] is None


@pytest.mark.parametrize("bad", [
    {"started_at": 1, "listened_ms": 0, "title": "x"},             # no uid
    _play("bad_uid"), _play("x" * 65), _play(5),
    {"uid": "a", "started_at": 1, "listened_ms": 0},               # no title
    _play("a", title=None), _play("a", listened_ms=-1), _play("a", listened_ms=1.5),
    _play("a", listened_ms="10"), _play("a", listened_ms=True), _play("a", started_at=None),
    _play("a", duration_ms=-1), _play("a", song_id="1234"), _play("a", artist=5), "not a play",
])
def test_bad_play_rejects_the_whole_batch(conn, bad):
    with pytest.raises(ls.BadListenError):
        _post(conn, _play("good"), bad)
    assert ls.listen_count(conn) == 0 and ls.current_revision(conn) == 0


@pytest.mark.parametrize("body", [None, [], {}, {"listens": {}}, {"plays": []}])
def test_bad_body(body):
    with pytest.raises(ls.BadListenError):
        ls.parse_listens(body)


def test_empty_batch(conn):
    assert _post(conn) == {"revision": 0, "added": 0, "updated": 0}
