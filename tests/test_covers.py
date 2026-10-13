"""Covers of playlists and moods (utils/database/covers.py, the cover rules in
playlist_sync.py / mood_sync.py): blobs stored under their sha256 (hash and
signature checks, re-upload touching the mtime), migration 0006 and
NotMigratedError before it, `cover` kept when the key is missing and removed
by null, the merge rules (same import on two phones, same mood name, a rename
onto another mood), promotion turning the cover into a private global-mood
cover, the global-mood-covers collection (conflicts, removal by null, since),
archiving a deleted playlist's/mood's blob, and the clean-up's 24-hour grace
period. Temp-file SQLite, no mocking of SQL; the HTTP side is in
test_database_sharing.py."""

import hashlib
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import covers
from utils.database import mood_sync as ms
from utils.database import playlist_sync as ps
from utils.database.migrations import initialize_user_db

PNG = b"\x89PNG\r\n\x1a\n" + b"picture-a"
PNG_B = b"\x89PNG\r\n\x1a\n" + b"picture-b"
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def _playlist(conn, uid="uid-0001", base=0, updated_at=1000, **extra):
    body = {"name": "Mix", "updated_at": updated_at, "tracks": [], **extra}
    return ps.store_playlist(conn, "ania", uid, ps.parse_playlist(body), base)


def _mood(conn, folder, uid="uid-0001", base=0, updated_at=1000, name="Siłownia", **extra):
    body = {"name": name, "updated_at": updated_at, "song_ids": [1], **extra}
    return ms.store_mood(conn, folder, uid, ms.parse_mood(body), base)


# ==================== blobs ====================

def test_blob_is_stored_once_under_its_hash(user_folder):
    assert covers.store_blob(user_folder, sha(PNG), PNG, "image/png")
    path = covers.blob_path(user_folder, sha(PNG))
    assert path == user_folder / "covers" / f"{sha(PNG)}.png" and path.read_bytes() == PNG
    os.utime(path, (0, 0))
    assert not covers.store_blob(user_folder, sha(PNG), PNG, "image/png")  # already there
    assert path.stat().st_mtime > 0  # touched: the grace period starts again
    assert covers.store_blob(user_folder, sha(JPEG), JPEG, "image/jpeg; charset=binary")
    assert covers.content_type_of(covers.blob_path(user_folder, sha(JPEG))) == "image/jpeg"
    assert [p.name for p in (user_folder / "covers").iterdir() if p.name.startswith(".")] == []


@pytest.mark.parametrize("data, content_type, error", [
    (PNG, "image/png", covers.HashMismatchError),         # sent under another picture's hash
    (PNG, "image/gif", covers.UnsupportedImageError),
    (PNG, None, covers.UnsupportedImageError),
    (b"GIF89a....", "image/png", covers.UnsupportedImageError),
    (PNG, "image/jpeg", covers.UnsupportedImageError),    # type and bytes disagree
])
def test_bad_blobs_are_refused(user_folder, data, content_type, error):
    name = sha(PNG_B) if error is covers.HashMismatchError else sha(data)
    with pytest.raises(error):
        covers.store_blob(user_folder, name, data, content_type)
    assert not (user_folder / "covers").exists() or not list((user_folder / "covers").iterdir())


def test_require_blob(user_folder):
    covers.require_blob(user_folder, None)
    with pytest.raises(covers.CoverMissingError) as exc:
        covers.require_blob(user_folder, sha(PNG))
    assert exc.value.cover == sha(PNG)
    covers.store_blob(user_folder, sha(PNG), PNG, "image/png")
    covers.require_blob(user_folder, sha(PNG))


def test_migration_is_required(tmp_path):
    db_path = tmp_path / "user.db"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE schema_version (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT UNIQUE NOT NULL,"
               " applied_at TEXT NOT NULL DEFAULT (datetime('now')))")
    for f in sorted((repo_root / "migrations" / "user").glob("*.sql")):
        if f.name == covers.REQUIRED_MIGRATION:
            break
        db.executescript(f.read_text())
        db.execute("INSERT INTO schema_version (filename) VALUES (?)", (f.name,))
    db.execute("INSERT INTO playlists (name, created_at, updated_at, uid) VALUES ('old', 0, 0, 'uid-old-1')")
    db.commit()
    db.close()
    with pytest.raises(ps.NotMigratedError):
        ps.connect(db_path)
    with pytest.raises(ms.NotMigratedError):
        ms.connect(db_path)

    initialize_user_db(db_path)

    with closing(ms.connect(db_path)) as c:
        row = c.execute("SELECT * FROM playlists").fetchone()
        assert ps.playlist_json(c, row)["cover"] is None
        assert ms.global_covers_since(c, 0) == {"revision": 0, "covers": []}


# ==================== cover in playlists ====================

def test_playlist_cover_kept_without_the_key_and_removed_by_null(conn):
    _, stored = _playlist(conn, cover=sha(PNG))
    assert stored["cover"] == sha(PNG)
    _, stored = _playlist(conn, base=1, updated_at=2000, name="Renamed by an old app")
    assert stored["cover"] == sha(PNG)
    _, stored = _playlist(conn, base=2, updated_at=3000, cover=sha(PNG_B))
    assert stored["cover"] == sha(PNG_B)
    _, stored = _playlist(conn, base=3, updated_at=4000, cover=None)
    assert stored["cover"] is None
    assert ps.parse_playlist({"name": "x", "updated_at": 1, "tracks": []})["cover"] is ps.KEEP_COVER


@pytest.mark.parametrize("value", ["ABC", sha(PNG).upper(), 5, ""])
def test_bad_cover_values_are_refused(value):
    with pytest.raises(ps.BadPlaylistError):
        ps.parse_playlist({"name": "x", "updated_at": 1, "tracks": [], "cover": value})
    with pytest.raises(ms.BadMoodError):
        ms.parse_mood({"name": "x", "updated_at": 1, "song_ids": [], "cover": value})


def _import(conn, uid, updated_at, **extra):
    return _playlist(conn, uid=uid, updated_at=updated_at, source="youtube", source_id="PL1", **extra)


def test_same_import_newer_side_keeps_its_cover(conn):
    _import(conn, "phone-a-uid", 1000, cover=sha(PNG))
    applied, stored = _import(conn, "phone-b-uid", 2000, cover=sha(PNG_B))
    assert applied and stored["uid"] == "phone-a-uid" and stored["cover"] == sha(PNG_B)


def test_same_import_newer_side_without_a_cover_keeps_the_others(conn):
    _import(conn, "phone-a-uid", 1000, cover=sha(PNG))
    assert _import(conn, "phone-b-uid", 2000, cover=None)[1]["cover"] == sha(PNG)
    assert _import(conn, "phone-c-uid", 3000)[1]["cover"] == sha(PNG)


def test_same_import_older_side_gives_its_cover_only_when_the_stored_has_none(conn):
    _import(conn, "phone-a-uid", 2000)
    applied, stored = _import(conn, "phone-b-uid", 1000, cover=sha(PNG))
    assert not applied and stored["uid"] == "phone-a-uid" and stored["cover"] == sha(PNG)
    assert stored["revision"] == 2  # a change the other phones hear about
    applied, stored = _import(conn, "phone-c-uid", 1500, cover=sha(PNG_B))
    assert not applied and stored["cover"] == sha(PNG) and stored["revision"] == 2


def test_deleted_playlist_archives_its_cover_and_drops_the_reference(conn, user_folder):
    covers.store_blob(user_folder, sha(PNG), PNG, "image/png")
    _playlist(conn, cover=sha(PNG))
    applied, stored, archived = ps.delete_playlist(conn, user_folder, "uid-0001", 1, 5000)
    assert applied and "cover" not in stored
    assert (user_folder / "archive" / "covers" / f"{sha(PNG)}.png").read_bytes() == PNG
    assert '"cover": "' + sha(PNG) in archived.read_text()
    assert covers.referenced(conn) == set()


# ==================== cover in moods ====================

def test_mood_cover_kept_without_the_key_and_removed_by_null(conn, user_folder):
    _mood(conn, user_folder, cover=sha(PNG))
    _, stored = _mood(conn, user_folder, base=1, updated_at=2000)
    assert stored["cover"] == sha(PNG)
    _, stored = _mood(conn, user_folder, base=2, updated_at=3000, cover=None)
    assert stored["cover"] is None
    assert ms.live_moods(conn)[0][4] is None


@pytest.mark.parametrize("first, second, newer_second, expected", [
    (PNG, PNG_B, True, PNG_B),
    (PNG, PNG_B, False, PNG),
    (PNG, None, True, PNG),     # the newer side has none: the other's
    (None, PNG_B, False, PNG_B),
])
def test_same_mood_name_merge_keeps_the_newer_sides_cover(conn, user_folder, first, second, newer_second, expected):
    _mood(conn, user_folder, uid="phone-a-uid", updated_at=2000, cover=first and sha(first))
    applied, stored = _mood(conn, user_folder, uid="phone-b-uid", updated_at=3000 if newer_second else 1000,
                            cover=second and sha(second))
    assert applied and stored["uid"] == "phone-a-uid" and stored["cover"] == sha(expected)


def test_rename_onto_another_mood_brings_its_own_cover(conn, user_folder):
    covers.store_blob(user_folder, sha(PNG), PNG, "image/png")
    _mood(conn, user_folder, uid="uid-gym-01", name="Siłownia", updated_at=1000)
    _mood(conn, user_folder, uid="uid-run-01", name="Bieganie", updated_at=1000, cover=sha(PNG))
    # Renamed by an app without covers (no key): the renamed mood's cover goes along
    _, stored = _mood(conn, user_folder, uid="uid-run-01", base=2, name="Siłownia", updated_at=2000)
    assert stored["uid"] == "uid-gym-01" and stored["cover"] == sha(PNG)
    assert (user_folder / "archive" / "covers" / f"{sha(PNG)}.png").exists()
    assert covers.referenced(conn) == {sha(PNG)}


def test_promotion_turns_the_cover_into_a_private_global_mood_cover(conn, user_folder):
    _mood(conn, user_folder, cover=sha(PNG))
    tag_id = conn.execute("SELECT id FROM tags WHERE uid = 'uid-0001'").fetchone()[0]
    ms.mark_promoted(conn, user_folder, tag_id, 12, "Siłownia", at=5000)
    assert ms.global_covers_since(conn, 0) == {
        "revision": 1, "covers": [{"mood_id": 12, "revision": 1, "cover": sha(PNG), "updated_at": 5000}]}
    assert covers.referenced(conn) == {sha(PNG)}
    # Without a cover, nothing is written
    _mood(conn, user_folder, uid="uid-0002", name="Chill")
    tag_id = conn.execute("SELECT id FROM tags WHERE uid = 'uid-0002'").fetchone()[0]
    ms.mark_promoted(conn, user_folder, tag_id, 13, "Chill")
    assert ms.global_covers(conn) == {12: sha(PNG)}


# ==================== private covers of global moods ====================

def test_global_mood_covers_sync(conn):
    applied, stored = ms.store_global_cover(conn, 12, sha(PNG), 0, 1000)
    assert applied and stored == {"mood_id": 12, "revision": 1, "cover": sha(PNG), "updated_at": 1000}
    ms.store_global_cover(conn, 99999, sha(PNG_B), 0, 1000)   # not in any tag.db: taken anyway
    # Stale base and older: the stored row wins
    applied, stored = ms.store_global_cover(conn, 12, sha(PNG_B), 0, 500)
    assert not applied and stored["cover"] == sha(PNG) and stored["revision"] == 1
    # Removal keeps the row with a new revision
    applied, stored = ms.store_global_cover(conn, 12, None, 1, 2000)
    assert applied and stored == {"mood_id": 12, "revision": 3, "cover": None, "updated_at": 2000}
    assert ms.global_covers_since(conn, 1) == {
        "revision": 3, "covers": [{"mood_id": 99999, "revision": 2, "cover": sha(PNG_B), "updated_at": 1000},
                                  stored]}
    assert ms.global_covers(conn) == {99999: sha(PNG_B)}
    # Its own counter, separate from moods' and playlists'
    assert ms.current_revision(conn) == 0 and ps.current_revision(conn) == 0


@pytest.mark.parametrize("body", [
    None, {"updated_at": 1, "cover": None}, {"base_revision": 0, "cover": None},
    {"base_revision": 0, "updated_at": 1}, {"base_revision": 0, "updated_at": 1, "cover": "x"},
])
def test_bad_global_cover_bodies(body):
    with pytest.raises(ms.BadMoodError):
        ms.parse_global_cover(body)


# ==================== clean-up ====================

def test_clean_up_removes_only_old_unreferenced_blobs(conn, user_folder):
    for data in (PNG, PNG_B, JPEG):
        covers.store_blob(user_folder, sha(data), data, "image/jpeg" if data is JPEG else "image/png")
    _playlist(conn, cover=sha(PNG))
    ms.store_global_cover(conn, 1, sha(JPEG), 0, 1)
    leftover = user_folder / "covers" / f".{sha(PNG_B)}.png.part"
    leftover.write_bytes(b"half")
    now = (user_folder / "covers" / f"{sha(PNG_B)}.png").stat().st_mtime

    # Unreferenced but fresh: kept (the PUT naming it may be on its way)
    assert covers.clean_up(conn, user_folder, now=now + covers.CLEAN_UP_GRACE_SECONDS - 60) == []
    removed = covers.clean_up(conn, user_folder, now=now + covers.CLEAN_UP_GRACE_SECONDS + 60)
    assert sorted(p.name for p in removed) == sorted([f"{sha(PNG_B)}.png", leftover.name])
    assert covers.has_blob(user_folder, sha(PNG)) and covers.has_blob(user_folder, sha(JPEG))
    assert covers.clean_up(conn, tmp_folder := user_folder / "nobody") == [] and not tmp_folder.exists()
