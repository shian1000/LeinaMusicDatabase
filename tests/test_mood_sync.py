"""Syncing a user's own moods with their phones (utils/database/mood_sync.py):
migration 0005 rebuilding user.db's tags (existing private tags kept as
markers, names unique among live rows only), revisions, the conflict rules
shared with playlists, the duplicate-name merge (a new uid, and a rename onto
another mood's name), deletion with an archive, promoted/merged markers that
are never revived, and song ids resolved against the shared music.db. The
replay side (merges reaching mood songs, catching up server-stored moods) is
at the bottom. Temp-file SQLite, no mocking of SQL."""

import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from config import constants
from utils.database import mood_sync as ms
from utils.database import song_references as sr
from utils.database import users
from utils.database.datatables import Artist, Base, Song
from utils.database.migrations import initialize_user_db


@pytest.fixture
def user_folder(tmp_path):
    folder = tmp_path / "Users" / "ania"
    folder.mkdir(parents=True)
    initialize_user_db(folder / "user.db")
    return folder


@pytest.fixture
def conn(user_folder):
    with closing(ms.connect(user_folder / "user.db")) as c:
        yield c


def _mood(name="Siłownia", updated_at=1000, song_ids=(1, 2), **extra):
    return ms.parse_mood({"name": name, "updated_at": updated_at, "song_ids": list(song_ids), **extra})


def _put(conn, folder, uid="uid-0001", base=0, **kw):
    return ms.store_mood(conn, folder, uid, _mood(**kw), base)


def test_constants_match_the_app():
    assert ms.MOOD_KIND == constants.TAG_KIND_MOOD
    assert (repo_root / "migrations" / "user" / ms.REQUIRED_MIGRATION).exists()


def test_migration_keeps_private_tags_as_markers_and_frees_deleted_names(tmp_path):
    db_path = tmp_path / "user.db"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE schema_version (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT UNIQUE NOT NULL,"
               " applied_at TEXT NOT NULL DEFAULT (datetime('now')))")
    for f in sorted((repo_root / "migrations" / "user").glob("*.sql")):
        if f.name == ms.REQUIRED_MIGRATION:
            break
        db.executescript(f.read_text())
        db.execute("INSERT INTO schema_version (filename) VALUES (?)", (f.name,))
    db.execute("INSERT INTO tags (id, name) VALUES (7, 'old')")
    db.execute("INSERT INTO song_tags (song_id, tag_id) VALUES (3, 7)")
    db.commit()
    db.close()

    initialize_user_db(db_path)

    with closing(ms.connect(db_path)) as c:
        assert tuple(c.execute("SELECT id, name, kind, deleted_at FROM tags").fetchone()) == (7, "old", "marker", None)
        assert ms.song_ids(c, 7) == [3]
        assert ms.live_moods(c) == []  # a marker is never a mood
        assert ms.current_revision(c) == 0
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO tags (name) VALUES ('old')")
        c.execute("UPDATE tags SET deleted_at = 1 WHERE id = 7")
        c.execute("INSERT INTO tags (name) VALUES ('old')")  # a deleted row's name is free again


def test_new_mood_and_changes_since(conn, user_folder):
    applied, stored = _put(conn, user_folder, name="  Siłownia ", song_ids=[5, 2, 5], created_at=900)
    assert applied
    assert stored == {"uid": "uid-0001", "revision": 1, "name": "Siłownia", "created_at": 900,
                      "updated_at": 1000, "deleted": False, "song_ids": [2, 5]}
    _put(conn, user_folder, uid="uid-0002", name="Chill")
    assert [m["uid"] for m in ms.changes_since(conn, 0)["moods"]] == ["uid-0001", "uid-0002"]
    assert ms.changes_since(conn, 2) == {"revision": 2, "moods": []}
    # Moods and playlists keep separate counters
    assert conn.execute("SELECT last_revision FROM playlist_revision").fetchone()[0] == 0


def test_markers_are_never_listed_or_sent_as_moods(conn, user_folder):
    conn.execute("INSERT INTO tags (name, kind, revision) VALUES ('private marker', 'marker', 99)")
    _put(conn, user_folder)
    assert [m["name"] for m in ms.changes_since(conn, 0)["moods"]] == ["Siłownia"]
    assert [m[2] for m in ms.live_moods(conn)] == ["Siłownia"]
    with pytest.raises(ms.BadMoodError):
        _put(conn, user_folder, uid="uid-0002", name="private marker")


def test_conflict_rules_match_playlists(conn, user_folder):
    _put(conn, user_folder, updated_at=1000)
    applied, stored = _put(conn, user_folder, base=1, song_ids=[3], updated_at=10)
    assert applied and stored["song_ids"] == [3] and stored["revision"] == 2  # based on the stored revision
    applied, stored = _put(conn, user_folder, base=1, song_ids=[4], updated_at=5)
    assert not applied and stored["song_ids"] == [3]  # stale and older -> 409 with the stored mood
    applied, stored = _put(conn, user_folder, base=1, song_ids=[4], updated_at=20)
    assert applied and stored["song_ids"] == [4]  # stale but newer
    again = _put(conn, user_folder, base=1, song_ids=[4], updated_at=20)
    assert again == (False, stored)  # a resend gets what it sent


def test_same_name_from_another_phone_is_merged_and_keeps_the_existing_uid(conn, user_folder):
    _put(conn, user_folder, uid="phone-a-uid", song_ids=[1, 2], updated_at=2000)
    applied, stored = _put(conn, user_folder, uid="phone-b-uid", song_ids=[2, 3], updated_at=1000)
    assert applied and stored["uid"] == "phone-a-uid" and stored["song_ids"] == [1, 2, 3]
    assert stored["updated_at"] == 2000 and stored["revision"] == 2
    assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 1


def test_rename_onto_another_mood_merges_and_leaves_a_marker(conn, user_folder):
    _put(conn, user_folder, uid="uid-gym1", name="Gym", song_ids=[1])
    _put(conn, user_folder, uid="uid-sport", name="Sport", song_ids=[2])
    applied, stored = _put(conn, user_folder, uid="uid-sport", base=2, name="Gym", song_ids=[2], updated_at=3000)
    assert applied and stored["uid"] == "uid-gym1" and stored["song_ids"] == [1, 2]
    (marker,) = [m for m in ms.changes_since(conn, 0)["moods"] if m["uid"] == "uid-sport"]
    assert marker == {"uid": "uid-sport", "revision": 4, "updated_at": 3000, "deleted": True,
                      "merged_into": "uid-gym1"}
    assert len(list((user_folder / "archive" / "moods").iterdir())) == 1
    # Never revived, even by a newer change: the phone switches to "Gym"
    assert _put(conn, user_folder, uid="uid-sport", base=4, name="Sport", updated_at=9000) == (False, marker)


def test_delete_archives_keeps_a_marker_and_frees_the_name(conn, user_folder):
    _put(conn, user_folder, song_ids=[1, 2])
    applied, stored, archived = ms.delete_mood(conn, user_folder, "uid-0001", 1, 5000)
    assert applied and stored == {"uid": "uid-0001", "revision": 2, "updated_at": 5000, "deleted": True}
    assert json.loads(archived.read_text())["song_ids"] == [1, 2]
    assert conn.execute("SELECT COUNT(*) FROM song_tags").fetchone()[0] == 0
    # Already deleted / never known: done
    assert ms.delete_mood(conn, user_folder, "uid-0001", 0, 1) == (True, stored, None)
    assert ms.delete_mood(conn, user_folder, "uid-9999", 0, 7)[1] == {
        "uid": "uid-9999", "revision": 0, "updated_at": 7, "deleted": True}
    # A new mood may take the name; a later edit of the old one brings it back
    assert _put(conn, user_folder, uid="uid-0002", updated_at=6000)[1]["uid"] == "uid-0002"
    applied, revived = _put(conn, user_folder, uid="uid-0001", base=2, name="Again", updated_at=7000)
    assert applied and not revived["deleted"]


def test_older_delete_loses(conn, user_folder):
    _put(conn, user_folder, updated_at=1000)
    _put(conn, user_folder, base=1, updated_at=3000)
    applied, stored, archived = ms.delete_mood(conn, user_folder, "uid-0001", 1, 2000)
    assert not applied and not stored["deleted"] and archived is None


def test_promoted_marker_says_where_it_went_and_stays_deleted(conn, user_folder):
    _put(conn, user_folder, song_ids=[1, 2])
    tag_id = conn.execute("SELECT id FROM tags").fetchone()[0]
    archived = ms.mark_promoted(conn, user_folder, tag_id, 12, "Siłownia", at=4000)
    assert archived.exists()
    (marker,) = ms.changes_since(conn, 1)["moods"]
    assert marker == {"uid": "uid-0001", "revision": 2, "updated_at": 4000, "deleted": True,
                      "promoted_to": 12, "promoted_to_name": "Siłownia"}
    assert _put(conn, user_folder, base=2, updated_at=9000) == (False, marker)
    assert ms.live_moods(conn) == []


@pytest.mark.parametrize("body", [
    {"updated_at": 1, "song_ids": []},
    {"name": " ", "updated_at": 1, "song_ids": []},
    {"name": "x", "song_ids": []},
    {"name": "x", "updated_at": 1, "song_ids": "1,2"},
    {"name": "x", "updated_at": 1, "song_ids": [0]},
    {"name": "x", "updated_at": 1, "song_ids": [True]},
    [],
])
def test_bad_bodies(body):
    with pytest.raises(ms.BadMoodError):
        ms.parse_mood(body)


def test_song_ids_resolved_against_the_shared_music_db(tmp_path):
    music = sqlite3.connect(tmp_path / "music.db")
    music.execute("CREATE TABLE songs (id INTEGER PRIMARY KEY)")
    music.execute("CREATE TABLE song_id_changes (id INTEGER PRIMARY KEY, old_song_id INTEGER, new_song_id INTEGER)")
    music.executemany("INSERT INTO songs VALUES (?)", [(4,), (6,)])
    music.executemany("INSERT INTO song_id_changes VALUES (?, ?, ?)", [(1, 2, 3), (2, 3, 4), (3, 5, None)])
    music.commit()
    music.close()
    # 2 -> 3 -> 4 (a chain), 5 deleted, 6 kept, 77 never existed
    assert ms.resolve_song_ids([2, 5, 6, 77, 4], tmp_path / "music.db") == ([4, 6], 3)


# --------------------
# Replaying music.db's song id log into moods
# --------------------

@pytest.fixture
def replay_env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(engine)
    music = sessionmaker(bind=engine)
    sr.register_song_reference_sync(music)
    monkeypatch.setattr(sr, "_music_engine", lambda: engine)
    monkeypatch.setattr(users, "USERS_DIR", tmp_path / "Users")
    monkeypatch.setattr(users, "users_available", lambda: True)
    monkeypatch.setattr(sr, "FILES_DB", tmp_path / "files.db")
    tag_engine = create_engine(f"sqlite:///{tmp_path / 'tag.db'}")
    from utils.database.tag_db_manager import Base as TagBase
    TagBase.metadata.create_all(tag_engine)
    with tag_engine.begin() as c:
        c.exec_driver_sql((repo_root / "migrations/tag/0002_applied_song_changes.sql").read_text())
    folder = tmp_path / "Users" / "ania"
    folder.mkdir(parents=True)
    (folder / "account.json").write_text("{}")
    initialize_user_db(folder / "user.db")
    return music, sessionmaker(bind=tag_engine), folder


def test_merges_and_deletions_reach_mood_songs_with_a_new_revision(replay_env):
    music, tag, folder = replay_env
    s = music()
    artist = Artist(name="A")
    keep, dup, gone, other = (Song(title=t, artist=artist) for t in "kdgo")
    s.add_all([keep, dup, gone, other])
    s.commit()
    with closing(ms.connect(folder / "user.db")) as c:
        ms.store_mood(c, folder, "uid-mood1", _mood(name="M", song_ids=[keep.id, dup.id, gone.id]), 0)
        ms.store_mood(c, folder, "uid-mood2", _mood(name="Other", song_ids=[other.id]), 0)
    keep_id = keep.id
    sr.record_song_merge(s, dup.id, keep_id)
    s.delete(dup)
    s.delete(gone)
    s.commit()

    sr.apply_song_id_changes(tag_session=tag(), force=True)

    with closing(ms.connect(folder / "user.db")) as c:
        (changed,) = ms.changes_since(c, 2)["moods"]
        assert changed["uid"] == "uid-mood1" and changed["song_ids"] == [keep_id] and changed["revision"] == 3


def test_server_stored_mood_catches_up_on_what_its_copy_lacked(replay_env):
    music, tag, folder = replay_env
    s = music()
    artist = Artist(name="A")
    keep, dup = Song(title="k", artist=artist), Song(title="d", artist=artist)
    s.add_all([keep, dup])
    s.commit()
    keep_id, dup_id = keep.id, dup.id
    sr.record_song_merge(s, dup_id, keep_id)
    s.delete(dup)
    s.commit()
    sr.apply_song_id_changes(tag_session=tag(), force=True)  # the user.db's cursor is past the merge now

    # The server resolved this phone's ids against a copy from before the merge
    with closing(ms.connect(folder / "user.db")) as c:
        ms.store_mood(c, folder, "uid-mood1", _mood(name="M", song_ids=[dup_id]), 0, song_ids_checked_to=0)
    sr.apply_song_id_changes(tag_session=tag(), force=True)

    with closing(ms.connect(folder / "user.db")) as c:
        (mood,) = ms.changes_since(c, 1)["moods"]
        assert mood["song_ids"] == [keep_id] and mood["revision"] == 2
        assert c.execute("SELECT song_ids_checked_to FROM tags").fetchone()[0] is None
