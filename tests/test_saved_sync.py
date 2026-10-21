"""Syncing Leina Radio's saved songs (liked / "Na później") and saved links
with a user's phones (utils/database/saved_sync.py): migration 0010, a
counter per collection and `since`, the conflict rules shared with folders
(base_revision 0 on an existing uid included), delete markers - also for a
uid never seen - and bringing an item back, items and unknown fields kept
verbatim, bad bodies, and saved songs following song merges. Temp-file
SQLite, no mocking of SQL."""

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import folder_sync as fs
from utils.database import playlist_sync as ps
from utils.database import saved_sync as ss
from utils.database.migrations import initialize_user_db

SONGS, LINKS = ss.SAVED_SONGS, ss.LATER_LINKS
UID = "3f2b0c" + "0" * 34
SONG = {"song_id": 1234, "title": "Группа крови", "artist": "Кино", "video_id": "abcdefghijk",
        "playlist_video_id": "pv", "spotify_track_id": "sp"}


@pytest.fixture
def conn(tmp_path):
    initialize_user_db(tmp_path / "user.db")
    with closing(ss.connect(tmp_path / "user.db")) as c:
        yield c


def _song_body(updated_at=1000, added_at=None, song=None, **extra):
    return {"list": "liked", "added_at": added_at if added_at is not None else updated_at,
            "updated_at": updated_at, "deleted": False, "song": dict(song or SONG), **extra}


def _put(conn, uid=UID, base=0, collection=SONGS, body=None, **kw):
    return ss.store_item(conn, collection, uid, ss.parse_item(collection, body or _song_body(**kw)), base)


def _link_body(updated_at=1000, **extra):
    return {"url": "https://youtu.be/abcdefghijk", "service": "youtube", "kind": "Film z YouTube",
            "person": "Ola", "saved_at": 900, "opened_at": None, "title": None, "artist": None,
            "updated_at": updated_at, "deleted": False, **extra}


def test_migration_exists_and_is_required(tmp_path):
    assert (repo_root / "migrations" / "user" / ss.REQUIRED_MIGRATION).exists()
    db_path = tmp_path / "user.db"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE schema_version (filename TEXT)")
    db.execute("INSERT INTO schema_version VALUES (?)", (fs.REQUIRED_MIGRATION,))
    db.commit()
    db.close()
    with pytest.raises(ss.NotMigratedError):
        ss.connect(db_path)


def test_put_then_get_from_another_revision(conn):
    applied, stored = _put(conn)
    assert applied
    assert stored == {"list": "liked", "added_at": 1000, "song": SONG, "uid": UID, "revision": 1,
                      "updated_at": 1000, "deleted": False}
    _put(conn, uid="second", body=_song_body(list="later", song={"title": "Only a title"}))
    assert [s["uid"] for s in ss.changes_since(conn, SONGS, 0)["songs"]] == [UID, "second"]
    assert [s["song"] for s in ss.changes_since(conn, SONGS, 1)["songs"]] == [{"title": "Only a title"}]
    assert ss.changes_since(conn, SONGS, 2) == {"revision": 2, "songs": []}
    # Each collection keeps its own counter, apart from playlists' and folders'
    assert ss.changes_since(conn, LINKS, 0) == {"revision": 0, "links": []}
    assert ps.current_revision(conn) == 0 and fs.current_revision(conn) == 0


def test_song_and_unknown_fields_kept_verbatim(conn):
    song = {"title": "t", "song_id": "not-an-int", "nested": {"a": [1, None, "ą"]}, "video_id": None}
    _, stored = _put(conn, body=_song_body(song=song, list="someday", future_field={"x": 1}))
    assert stored["song"] == song and stored["list"] == "someday" and stored["future_field"] == {"x": 1}
    assert ss.changes_since(conn, SONGS, 0)["songs"] == [stored]
    # Server fields from the body never end up in the data
    _, stored = _put(conn, base=1, body=_song_body(updated_at=2000, revision=99, base_revision=1, uid=UID))
    assert stored["revision"] == 2 and "base_revision" not in stored


def test_links_round_trip_verbatim(conn):
    body = _link_body(new_key=[1, 2])
    _, stored = _put(conn, uid="a1c4-uuid", collection=LINKS, body=body)
    expected = {k: v for k, v in body.items() if k not in ("updated_at", "deleted")}
    assert stored == {**expected, "uid": "a1c4-uuid", "revision": 1, "updated_at": 1000, "deleted": False}
    assert ss.changes_since(conn, LINKS, 0) == {"revision": 1, "links": [stored]}
    assert ss.changes_since(conn, SONGS, 0)["songs"] == []


def test_base_revision_zero_on_an_existing_uid_newer_wins_older_conflicts(conn):
    _, stored = _put(conn, updated_at=1000)
    # The same song liked on another phone, earlier: the stored one wins
    applied, answer = _put(conn, base=0, updated_at=900)
    assert (applied, answer) == (False, stored)
    # Equal updated_at too
    assert _put(conn, base=0, updated_at=1000) == (False, stored)
    # Saved again on top, later: applied
    applied, answer = _put(conn, base=0, updated_at=3000)
    assert applied and answer["added_at"] == 3000 and answer["revision"] == 2


def test_older_change_based_on_an_old_revision_conflicts(conn):
    _put(conn, updated_at=1000)
    _, stored = _put(conn, base=1, updated_at=2000)
    assert _put(conn, base=1, updated_at=1500) == (False, stored)
    # Based on the stored revision: applied even with an older clock
    applied, answer = _put(conn, base=2, updated_at=1200)
    assert applied and answer["updated_at"] == 1200


def test_delete_marker_reaches_since_and_a_newer_put_brings_it_back(conn):
    _put(conn, updated_at=1000)
    stored = ss.changes_since(conn, SONGS, 0)["songs"][0]
    assert ss.delete_item(conn, SONGS, UID, 0, 900) == (False, stored)
    applied, deleted = ss.delete_item(conn, SONGS, UID, 1, 5000)
    assert (applied, deleted) == (True, {"uid": UID, "revision": 2, "updated_at": 5000, "deleted": True})
    assert ss.changes_since(conn, SONGS, 1) == {"revision": 2, "songs": [deleted]}
    # Deleting again is done already
    assert ss.delete_item(conn, SONGS, UID, 0, 6000) == (True, deleted)
    # An older PUT doesn't bring it back, a newer one does
    assert _put(conn, base=0, updated_at=4000) == (False, deleted)
    applied, revived = _put(conn, base=0, updated_at=7000)
    assert applied and revived["deleted"] is False and revived["song"] == SONG and revived["revision"] == 3


def test_delete_of_a_uid_never_seen_stores_the_marker(conn):
    applied, deleted = ss.delete_item(conn, LINKS, "never-seen", 0, 5000)
    assert (applied, deleted) == (True, {"uid": "never-seen", "revision": 1, "updated_at": 5000, "deleted": True})
    assert ss.changes_since(conn, LINKS, 0)["links"] == [deleted]
    # A phone that still has it, saved before the deletion, gets the marker
    assert _put(conn, uid="never-seen", collection=LINKS, body=_link_body(updated_at=4000)) == (False, deleted)


@pytest.mark.parametrize("collection, body, error", [
    (SONGS, [], "expected a JSON object"),
    (SONGS, {"added_at": 1, "updated_at": 1, "song": {"title": "t"}}, "list"),
    (SONGS, {"list": "", "added_at": 1, "updated_at": 1, "song": {"title": "t"}}, "list"),
    (SONGS, {"list": "liked", "updated_at": 1, "song": {"title": "t"}}, "added_at"),
    (SONGS, {"list": "liked", "added_at": 1, "updated_at": 1}, "song"),
    (SONGS, {"list": "liked", "added_at": 1, "updated_at": 1, "song": {"artist": "a"}}, "song.title"),
    (SONGS, {"list": "liked", "added_at": 1, "song": {"title": "t"}}, "updated_at"),
    (SONGS, {"list": "liked", "added_at": True, "updated_at": 1, "song": {"title": "t"}}, "added_at"),
    (LINKS, {"saved_at": 1, "updated_at": 1}, "url"),
    (LINKS, {"url": " ", "saved_at": 1, "updated_at": 1}, "url"),
    (LINKS, {"url": "https://x", "updated_at": 1}, "saved_at"),
])
def test_bad_bodies(collection, body, error):
    with pytest.raises(ss.BadItemError, match=error):
        ss.parse_item(collection, body)


def test_uids():
    assert ss.UID_RE.match("k") and ss.UID_RE.match("a" * 64) and ss.UID_RE.match(UID)
    assert not ss.UID_RE.match("a" * 65) and not ss.UID_RE.match("bad_uid") and not ss.UID_RE.match("")


def test_follow_song_merges(conn):
    _put(conn, uid="merged", song={"song_id": 2, "title": "a"})           # 2 -> 3 -> 4
    _put(conn, uid="deleted", song={"song_id": 5, "title": "b"})          # 5 deleted
    _put(conn, uid="untouched", song={"song_id": 4, "title": "c"})
    _put(conn, uid="no-id", song={"title": "d"})
    _put(conn, uid="gone", song={"song_id": 2, "title": "e"})
    ss.delete_item(conn, SONGS, "gone", 5, 2000)                            # revision 6
    changes = [(1, 2, 3), (2, 5, None), (3, 3, 4)]

    with ps._transaction(conn):
        assert ss.follow_song_merges(conn, changes) == 1
    by_uid = {s["uid"]: s for s in ss.changes_since(conn, SONGS, 6)["songs"]}
    assert list(by_uid) == ["merged"]
    assert by_uid["merged"]["song"] == {"song_id": 4, "title": "a"} and by_uid["merged"]["updated_at"] == 1000
    assert conn.execute("SELECT song_id FROM saved_songs WHERE uid = 'merged'").fetchone()[0] == 4
    assert conn.execute("SELECT song_id FROM saved_songs WHERE uid = 'deleted'").fetchone()[0] == 5
    # Following the same log again changes nothing
    with ps._transaction(conn):
        assert ss.follow_song_merges(conn, changes) == 0
    assert ss.current_revision(conn, SONGS) == 7


def test_follow_song_merges_before_the_migration_does_nothing(tmp_path):
    db = sqlite3.connect(tmp_path / "user.db")
    db.execute("CREATE TABLE schema_version (filename TEXT)")
    assert ss.follow_song_merges(db, [(1, 2, 3)]) == 0
