"""Syncing the folders of a user's Biblioteka with their phones
(utils/database/folder_sync.py): migration 0008, revisions and `since`
(own counter), the conflict rules shared with playlists, deletion markers
and bringing a folder back, items kept verbatim (unknown refs and types),
short uids, bad bodies, and the folder refs following a playlist or mood the
server merged into another. Temp-file SQLite, no mocking of SQL."""

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import folder_sync as fs
from utils.database import mood_sync as ms
from utils.database import playlist_sync as ps
from utils.database.migrations import initialize_user_db

ITEMS = [{"type": "playlist", "uid": "a1c4"}, {"type": "mood", "uid": "77de"}, {"type": "global_mood", "id": 5}]


@pytest.fixture
def user_folder(tmp_path):
    folder = tmp_path / "Users" / "ania"
    folder.mkdir(parents=True)
    initialize_user_db(folder / "user.db")
    return folder


@pytest.fixture
def conn(user_folder):
    with closing(fs.connect(user_folder / "user.db")) as c:
        yield c


def _folder(name="Do auta", updated_at=1000, items=ITEMS, **extra):
    return fs.parse_folder({"name": name, "updated_at": updated_at, "items": list(items), **extra})


def _put(conn, uid="k3x", base=0, **kw):
    return fs.store_folder(conn, uid, _folder(**kw), base)


def test_migration_exists_and_is_required(tmp_path):
    assert (repo_root / "migrations" / "user" / fs.REQUIRED_MIGRATION).exists()
    db_path = tmp_path / "user.db"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE schema_version (filename TEXT)")
    db.execute("INSERT INTO schema_version VALUES (?)", (ps.EXCLUDED_TRACKS_MIGRATION,))
    db.commit()
    db.close()
    with pytest.raises(fs.NotMigratedError):
        fs.connect(db_path)


def test_put_then_get_from_another_revision(conn):
    applied, stored = _put(conn, created_at=900)
    assert applied
    assert stored == {"uid": "k3x", "revision": 1, "name": "Do auta", "created_at": 900, "updated_at": 1000,
                      "deleted": False, "items": ITEMS}
    _put(conn, uid="3f2b0000-0000-4000-8000-000000000001", name="Rano", items=[])
    assert [f["uid"] for f in fs.changes_since(conn, 0)["folders"]] == ["k3x", "3f2b0000-0000-4000-8000-000000000001"]
    assert [f["name"] for f in fs.changes_since(conn, 1)["folders"]] == ["Rano"]
    assert fs.changes_since(conn, 2) == {"revision": 2, "folders": []}
    # Folders keep their own counter
    assert ps.current_revision(conn) == 0 and ms.current_revision(conn) == 0
    assert [f["name"] for f in fs.live_folders(conn)] == ["Do auta", "Rano"]


def test_items_kept_verbatim_including_unknown_refs_and_types(conn):
    items = [{"type": "playlist", "uid": "never-synced"}, {"type": "smart_list", "query": {"year": 1984}, "x": [1]},
             {"type": "global_mood", "id": 999}, {"type": "mood", "uid": "ąę", "extra": None}]
    _, stored = _put(conn, items=items)
    assert stored["items"] == items
    assert fs.changes_since(conn, 0)["folders"][0]["items"] == items


def test_conflict_rules_match_playlists(conn):
    _put(conn, updated_at=1000)
    applied, stored = _put(conn, base=1, name="A", updated_at=10)
    assert applied and stored["name"] == "A" and stored["revision"] == 2  # based on the stored revision
    applied, stored = _put(conn, base=1, name="B", updated_at=5)
    assert not applied and stored["name"] == "A"  # stale and older -> 409 with the stored folder
    applied, stored = _put(conn, base=1, name="B", updated_at=10)
    assert not applied and stored["name"] == "A"  # stale and equal -> the stored one too
    applied, stored = _put(conn, base=1, name="C", updated_at=20)
    assert applied and stored["name"] == "C"  # stale but newer


def test_delete_marker_reaches_since_and_an_older_delete_loses(conn):
    _put(conn, updated_at=1000)
    assert fs.delete_folder(conn, "k3x", base_revision=0, deleted_at=500) == (False, fs.changes_since(conn, 0)["folders"][0])
    applied, marker = fs.delete_folder(conn, "k3x", base_revision=1, deleted_at=2000)
    assert applied and marker == {"uid": "k3x", "revision": 2, "updated_at": 2000, "deleted": True}
    assert fs.changes_since(conn, 1)["folders"] == [marker]
    assert fs.live_folders(conn) == []
    assert fs.delete_folder(conn, "k3x", 2, 3000) == (True, marker)  # already deleted
    assert fs.delete_folder(conn, "unknown", 0, 7) == (True, {"uid": "unknown", "revision": 0, "updated_at": 7,
                                                               "deleted": True})


def test_put_newer_than_the_deletion_brings_the_folder_back(conn):
    _put(conn)
    fs.delete_folder(conn, "k3x", 1, 2000)
    assert not _put(conn, base=1, updated_at=1500)[0]
    applied, stored = _put(conn, base=1, updated_at=2500, name="Back")
    assert applied and stored["name"] == "Back" and not stored["deleted"] and stored["created_at"] == 1000


@pytest.mark.parametrize("body", [
    None, [], {"updated_at": 1, "items": []}, {"name": "  ", "updated_at": 1, "items": []},
    {"name": "x", "items": []}, {"name": "x", "updated_at": -1, "items": []}, {"name": "x", "updated_at": 1},
    {"name": "x", "updated_at": 1, "items": ["a1c4"]}, {"name": "x", "updated_at": 1, "items": [{"uid": "a"}]},
])
def test_bad_bodies(body):
    with pytest.raises(fs.BadFolderError):
        fs.parse_folder(body)


@pytest.mark.parametrize("uid, ok", [("k", True), ("3f2b-AZ09", True), ("x" * 64, True), ("x" * 65, False),
                                     ("", False), ("a_b", False), ("a b", False)])
def test_uid_format(uid, ok):
    assert bool(fs.UID_RE.match(uid)) is ok


def _playlist(source_id="PL1", updated_at=1000):
    return ps.parse_playlist({"name": "Mix", "source": "youtube", "source_id": source_id,
                              "updated_at": updated_at, "tracks": []})


def test_same_source_playlist_merge_rewrites_folder_refs(conn):
    ps.store_playlist(conn, "ania", "kept-uid-1", _playlist(), 0)
    _put(conn, items=[{"type": "playlist", "uid": "phone-uid-2"}, {"type": "mood", "uid": "phone-uid-2"}])
    _put(conn, uid="other", items=[{"type": "playlist", "uid": "kept-uid-1"}, {"type": "playlist", "uid": "phone-uid-2"}])
    fs.delete_folder(conn, "other", 2, 5000)  # deleted folders are left alone
    # The other phone imported the same playlist under its own uid
    applied, stored = ps.store_playlist(conn, "ania", "phone-uid-2", _playlist(updated_at=900), 0)
    assert not applied and stored["uid"] == "kept-uid-1"
    (changed,) = fs.changes_since(conn, 3)["folders"]
    assert changed["uid"] == "k3x" and changed["revision"] == 4
    assert changed["items"] == [{"type": "playlist", "uid": "kept-uid-1"}, {"type": "mood", "uid": "phone-uid-2"}]


def test_mood_merges_rewrite_folder_refs_without_duplicates(conn, user_folder):
    ms.store_mood(conn, user_folder, "mood-kept", ms.parse_mood({"name": "Siłownia", "updated_at": 1, "song_ids": []}), 0)
    ms.store_mood(conn, user_folder, "mood-old1", ms.parse_mood({"name": "Chill", "updated_at": 1, "song_ids": []}), 0)
    _put(conn, items=[{"type": "mood", "uid": "mood-new1"}, {"type": "mood", "uid": "mood-kept"},
                      {"type": "mood", "uid": "mood-old1"}, {"type": "global_mood", "id": 1}])
    # A new uid with a taken name, then a rename onto it
    ms.store_mood(conn, user_folder, "mood-new1", ms.parse_mood({"name": "Siłownia", "updated_at": 2, "song_ids": []}), 0)
    ms.store_mood(conn, user_folder, "mood-old1", ms.parse_mood({"name": "Siłownia", "updated_at": 3, "song_ids": []}), 2)
    (folder,) = fs.live_folders(conn)
    assert folder["items"] == [{"type": "mood", "uid": "mood-kept"}, {"type": "global_mood", "id": 1}]
    assert folder["revision"] == 3


def test_refs_untouched_before_the_migration(tmp_path):
    db_path = tmp_path / "user.db"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE schema_version (filename TEXT)")
    db.row_factory = sqlite3.Row
    fs.replace_refs(db, "playlist", "a", "b")  # no folders table: nothing to do, no error
