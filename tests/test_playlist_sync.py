"""Syncing users' playlists with the phones (utils/database/playlist_sync.py,
shared by the sharing server and user_playlists.py): migration 0004 on an
existing user.db, revisions, the conflict rules (equal base, stale base
newer/older, delete vs edit, a resend), the source + source_id merge, the
archive written before a deletion, song ids from a phone's older music.db,
and the menu's writes (save/delete/list). Temp-file SQLite, no mocking of
SQL; the replay side (apply_song_id_changes() bumping revisions, catching up
server-stored playlists) is in test_song_references.py."""

import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from config import constants
from utils.database import playlist_sync as ps
from utils.database import song_references, users
from utils.database.migrations import initialize_user_db


@pytest.fixture
def user_folder(tmp_path):
    folder = tmp_path / "Users" / "ania"
    folder.mkdir(parents=True)
    initialize_user_db(folder / "user.db")
    return folder


@pytest.fixture
def conn(user_folder):
    with closing(ps.connect(user_folder / "user.db")) as c:
        yield c


def _playlist(name="Mix", updated_at=1000, tracks=None, **extra):
    body = {"name": name, "updated_at": updated_at,
            "tracks": tracks if tracks is not None else [{"title": "Karma", "artist": "Hey", "song_id": 1}],
            **extra}
    return ps.parse_playlist(body)


def _put(conn, uid="uid-0001", base=0, **kw):
    return ps.store_playlist(conn, "ania", uid, _playlist(**kw), base)


def test_constants_match_the_app():
    assert ps.USER_DB_FILENAME == constants.USER_DB_FILENAME
    assert set(ps.SOURCES) == {
        constants.USER_PLAYLIST_SOURCE_FOLDER, constants.USER_PLAYLIST_SOURCE_YOUTUBE,
        constants.USER_PLAYLIST_SOURCE_SPOTIFY, constants.USER_PLAYLIST_SOURCE_SPOTIFY_ALBUM}
    assert (repo_root / "migrations" / "user" / ps.REQUIRED_MIGRATION).exists()


def test_migration_gives_existing_playlists_uids_and_revisions(tmp_path):
    db_path = tmp_path / "user.db"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE schema_version (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT UNIQUE NOT NULL,"
               " applied_at TEXT NOT NULL DEFAULT (datetime('now')))")
    for f in sorted((repo_root / "migrations" / "user").glob("*.sql")):
        if f.name == ps.REQUIRED_MIGRATION:
            break
        db.executescript(f.read_text())
        db.execute("INSERT INTO schema_version (filename) VALUES (?)", (f.name,))
    db.execute("INSERT INTO playlists (id, name, created_at, updated_at) VALUES (1, 'a', 0, 0), (2, 'b', 0, 0)")
    db.execute("INSERT INTO playlist_tracks (playlist_id, position, title) VALUES (1, 0, 't')")
    db.commit()
    db.close()

    initialize_user_db(db_path)  # applies just what's pending

    with closing(ps.connect(db_path)) as c:
        rows = c.execute("SELECT uid, revision, deleted_at FROM playlists ORDER BY id").fetchall()
        assert [(r["revision"], r["deleted_at"]) for r in rows] == [(1, None), (2, None)]
        uids = [r["uid"] for r in rows]
        assert len(set(uids)) == 2 and all(ps.UID_RE.match(u) and u[14] == "4" for u in uids)
        assert ps.current_revision(c) == 2
        assert tuple(c.execute("SELECT youtube_removed, spotify_track_id FROM playlist_tracks").fetchone()) == (0, None)


def test_new_playlist_and_changes_since(conn):
    applied, stored = _put(conn, source="youtube", source_id="PL1", source_owner="Someone",
                           tracks=[{"title": "Karma", "artist": "Hey", "song_id": 1, "youtube_video_id": "v1",
                                    "youtube_removed": True, "duration_s": 215},
                                   {"title": "Only on YouTube"}])
    assert applied
    assert stored == {
        "uid": "uid-0001", "revision": 1, "name": "Mix", "source": "youtube", "source_id": "PL1",
        "source_owner": "Someone", "created_at": 1000, "updated_at": 1000, "deleted": False, "cover": None,
        "tracks": [
            {"song_id": 1, "title": "Karma", "artist": "Hey", "youtube_video_id": "v1",
             "spotify_track_id": None, "youtube_removed": True, "duration_s": 215},
            {"song_id": None, "title": "Only on YouTube", "artist": None, "youtube_video_id": None,
             "spotify_track_id": None, "youtube_removed": False, "duration_s": None}]}
    assert conn.execute("SELECT owner FROM playlists").fetchone()[0] == "ania"
    _put(conn, uid="uid-0002")
    assert [p["uid"] for p in ps.changes_since(conn, 0)["playlists"]] == ["uid-0001", "uid-0002"]
    assert ps.changes_since(conn, 1) == {"revision": 2, "playlists": [ps.changes_since(conn, 0)["playlists"][1]]}
    assert ps.changes_since(conn, 2) == {"revision": 2, "playlists": []}


def test_change_based_on_the_stored_revision_wins_even_if_older(conn):
    _put(conn, updated_at=5000)
    applied, stored = _put(conn, base=1, name="Renamed", updated_at=10, tracks=[])
    assert applied and stored["name"] == "Renamed" and stored["revision"] == 2 and stored["tracks"] == []
    assert stored["updated_at"] == 10  # the device's time is kept, not the server's


def test_stale_base_newer_wins_older_gets_the_stored_playlist(conn):
    _put(conn, updated_at=1000)
    _put(conn, base=1, name="Phone A", updated_at=2000)            # revision 2
    applied, stored = _put(conn, base=1, name="Phone B", updated_at=1500)
    assert not applied and stored["name"] == "Phone A" and stored["revision"] == 2
    applied, stored = _put(conn, base=1, name="Phone B", updated_at=2500)
    assert applied and stored["name"] == "Phone B" and stored["revision"] == 3


def test_resend_after_a_lost_answer_is_harmless(conn):
    _put(conn, updated_at=1000)
    first = _put(conn, base=1, name="Edit", updated_at=2000)
    again = _put(conn, base=1, name="Edit", updated_at=2000)
    assert first[0] and not again[0]
    assert again[1] == first[1]  # the 409 carries exactly what was sent
    assert ps.current_revision(conn) == 2


def test_same_source_from_another_phone_updates_the_existing_playlist(conn):
    _put(conn, uid="phone-a-uid", source="youtube", source_id="PL1", updated_at=1000)
    applied, stored = _put(conn, uid="phone-b-uid", source="youtube", source_id="PL1", name="Newer",
                           updated_at=2000)
    assert applied and stored["uid"] == "phone-a-uid" and stored["name"] == "Newer"
    assert conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0] == 1
    # Older than what's stored: the phone gets the existing one (and its uid) back
    applied, stored = _put(conn, uid="phone-c-uid", source="youtube", source_id="PL1", updated_at=1500)
    assert not applied and stored["uid"] == "phone-a-uid"


def test_source_merge_ignores_deleted_and_can_be_switched_off(conn, user_folder):
    _put(conn, uid="first-uid", source="folder", source_id="/m")
    ps.delete_playlist(conn, user_folder, "first-uid", 1, 2000)
    assert _put(conn, uid="second-uid", source="folder", source_id="/m")[1]["uid"] == "second-uid"
    applied, stored = ps.store_playlist(conn, "ania", "third-uid", _playlist(source="folder", source_id="/m"), 0,
                                        merge_same_source=False)
    assert stored["uid"] == "third-uid"


def test_delete_archives_first_then_keeps_a_marker(conn, user_folder):
    _put(conn, updated_at=1000)
    applied, stored, archived = ps.delete_playlist(conn, user_folder, "uid-0001", 1, 1791839796631)
    assert applied
    assert stored == {"uid": "uid-0001", "revision": 2, "updated_at": 1791839796631, "deleted": True}
    assert archived.parent == user_folder / "archive" / "playlists"
    assert archived.name.startswith("uid-0001.") and archived.suffix == ".json"
    saved = json.loads(archived.read_text())
    assert saved["name"] == "Mix" and saved["tracks"][0]["title"] == "Karma" and saved["owner"] == "ania"
    assert saved["deleted_at"] == 1791839796631
    assert conn.execute("SELECT COUNT(*) FROM playlist_tracks").fetchone()[0] == 0
    assert ps.changes_since(conn, 1)["playlists"] == [stored]


def test_archive_files_are_never_overwritten(conn, user_folder):
    _put(conn, updated_at=1000)
    first = ps.delete_playlist(conn, user_folder, "uid-0001", 1, 5000)[2]
    _put(conn, base=2, updated_at=5001)                  # brought back...
    second = ps.delete_playlist(conn, user_folder, "uid-0001", 3, 5002)[2]  # ...and deleted again
    assert first != second and first.exists() and second.exists()
    assert second.name.endswith("_2.json")


def test_delete_versus_edit(conn, user_folder):
    _put(conn, updated_at=1000)
    _put(conn, base=1, updated_at=3000, name="Edited")             # revision 2
    applied, stored, archived = ps.delete_playlist(conn, user_folder, "uid-0001", 1, 2000)
    assert not applied and archived is None and stored["name"] == "Edited"
    applied, _, _ = ps.delete_playlist(conn, user_folder, "uid-0001", 1, 4000)
    assert applied
    # An edit made after the deletion brings it back...
    applied, stored = _put(conn, base=2, updated_at=4500, name="Back")
    assert applied and stored["deleted"] is False and stored["name"] == "Back"
    ps.delete_playlist(conn, user_folder, "uid-0001", stored["revision"], 6000)
    # ...an older one doesn't
    applied, stored = _put(conn, base=2, updated_at=5000)
    assert not applied and stored["deleted"] is True


def test_deleting_unknown_or_deleted_is_done(conn, user_folder):
    assert ps.delete_playlist(conn, user_folder, "never-seen", 0, 7000) == (
        True, {"uid": "never-seen", "revision": 0, "updated_at": 7000, "deleted": True}, None)
    _put(conn)
    ps.delete_playlist(conn, user_folder, "uid-0001", 1, 2000)
    applied, stored, archived = ps.delete_playlist(conn, user_folder, "uid-0001", 0, 9000)
    assert applied and archived is None and stored["revision"] == 2 and stored["updated_at"] == 2000
    assert len(list((user_folder / "archive" / "playlists").iterdir())) == 1


@pytest.mark.parametrize("body", [
    None, [], {"updated_at": 1, "tracks": []}, {"name": " ", "updated_at": 1, "tracks": []},
    {"name": "x", "tracks": []}, {"name": "x", "updated_at": "1", "tracks": []},
    {"name": "x", "updated_at": True, "tracks": []}, {"name": "x", "updated_at": 1},
    {"name": "x", "updated_at": 1, "tracks": [{"artist": "no title"}]},
    {"name": "x", "updated_at": 1, "tracks": [{"title": "t", "song_id": "5"}]},
    {"name": "x", "updated_at": 1, "tracks": [{"title": "t", "youtube_removed": 1}]},
    {"name": "x", "updated_at": 1, "tracks": [], "source": 3},
])
def test_bad_bodies_are_refused(body):
    with pytest.raises(ps.BadPlaylistError):
        ps.parse_playlist(body)


def test_unmigrated_or_missing_user_db_is_not_ready(tmp_path):
    with pytest.raises(ps.NotMigratedError):
        ps.connect(tmp_path / "missing.db")
    sqlite3.connect(tmp_path / "old.db").execute("CREATE TABLE playlists (id INTEGER)").connection.close()
    with pytest.raises(ps.NotMigratedError):
        ps.connect(tmp_path / "old.db")


# ==================== song ids from an older music.db ====================

def _shared_music_db(path: Path, song_ids, changes):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE songs (id INTEGER PRIMARY KEY)")
    db.execute("CREATE TABLE song_id_changes (id INTEGER PRIMARY KEY, old_song_id INTEGER, new_song_id INTEGER)")
    db.executemany("INSERT INTO songs VALUES (?)", [(i,) for i in song_ids])
    db.executemany("INSERT INTO song_id_changes VALUES (?, ?, ?)", changes)
    db.commit()
    db.close()


def test_song_ids_follow_merges_and_deletions(tmp_path):
    music = tmp_path / "music.db"
    # 2 merged into 3, 3 merged into 4 (a chain), 5 deleted
    _shared_music_db(music, [1, 4], [(1, 2, 3), (2, 3, 4), (3, 5, None)])
    tracks = [{"song_id": s, "title": f"t{s}"} for s in (1, 2, 3, 5, 99, None)]
    assert ps.resolve_song_ids(tracks, music) == 3
    assert [t["song_id"] for t in tracks] == [1, 4, 4, None, None, None]
    assert [t["title"] for t in tracks] == ["t1", "t2", "t3", "t5", "t99", "tNone"]


def test_unreadable_music_db_leaves_ids_for_the_app_to_check(tmp_path):
    tracks = [{"song_id": 7, "title": "t"}]
    assert ps.resolve_song_ids(tracks, tmp_path / "missing.db") == 0
    assert tracks[0]["song_id"] == 7
    no_log = tmp_path / "old.db"
    sqlite3.connect(no_log).execute("CREATE TABLE songs (id INTEGER)").connection.close()
    assert ps.resolve_song_ids(tracks, no_log) == 0 and tracks[0]["song_id"] == 7


# ==================== the menu's writes ====================

@pytest.fixture
def users_env(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "USERS_DIR", tmp_path / "Users")
    monkeypatch.setattr(users, "users_available", lambda: True)
    monkeypatch.setattr(song_references, "latest_change_id", lambda: 0)
    return tmp_path / "Users"


def test_menu_playlists_sync_like_the_phones(users_env):
    from utils.database.user_playlists import delete_playlist, list_playlists, save_playlist

    folder = users.create_user("ania", "secret1")
    track = SimpleNamespace(song_id=1, title="Karma", artist="Hey", youtube_video_id=None, duration_s=None)
    first = save_playlist("ania", "Mix", [track], source="folder", source_id="/m")
    second = save_playlist("ania", "Mix again", [track], source="folder", source_id="/m")
    assert first != second  # the same folder twice stays two playlists in the menu

    archived = delete_playlist("ania", first)
    assert archived.exists() and json.loads(archived.read_text())["name"] == "Mix"
    assert [p[0] for p in list_playlists("ania")] == [second]
    with closing(ps.connect(folder / "user.db")) as c:
        changed = ps.changes_since(c, 0)
    assert changed["revision"] == 3
    assert [(p["revision"], p["deleted"]) for p in changed["playlists"]] == [(2, False), (3, True)]
    assert delete_playlist("ania", 12345) is None
