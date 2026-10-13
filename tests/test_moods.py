"""Moods (utils/database/moods.py) and permissions (users.py, account_auth.py):
global moods in tag.db never including markers, names refused when a marker
has them, promoting a user's mood (a new global mood, a merge into an existing
one, refusal on a marker's name, the user's marker with promoted_to), applying
the phones' changes to global moods (global_mood_log.py: added / removed /
unchanged / mood gone / song gone, a song merged after the server checked it,
the history written and pending files removed), and granting/revoking
permissions. Temp-file SQLite, no mocking of SQL."""

import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from config import constants
from utils.database import account_auth, global_mood_log, mood_sync, moods
from utils.database import song_references as sr
from utils.database import users
from utils.database.datatables import Artist, Base, Song
from utils.database.migrations import initialize_user_db
from utils.database.tag_db_manager import Base as TagBase


@pytest.fixture
def env(tmp_path, monkeypatch):
    music_engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(music_engine)
    music = sessionmaker(bind=music_engine)
    sr.register_song_reference_sync(music)
    monkeypatch.setattr(sr, "_music_engine", lambda: music_engine)

    # tag.db as the app has it: the model, then the migrations on top
    tag_path = tmp_path / "tag.db"
    tag_engine = create_engine(f"sqlite:///{tag_path}")
    TagBase.metadata.create_all(tag_engine)
    tag = sessionmaker(bind=tag_engine)()
    tag.execute(text("INSERT INTO tags (name, kind) VALUES ('spellchecked', 'marker')"))
    tag.commit()

    monkeypatch.setattr(users, "USERS_DIR", tmp_path / "Users")
    monkeypatch.setattr(users, "users_available", lambda: True)
    folder = tmp_path / "Users" / "ania"
    folder.mkdir(parents=True)
    (folder / "account.json").write_text(json.dumps({"username": "ania", "password": "x"}))
    initialize_user_db(folder / "user.db")

    s = music()
    artist = Artist(name="A")
    songs = [Song(title=f"s{i}", artist=artist) for i in range(4)]
    s.add_all(songs)
    s.commit()
    yield s, tag, folder, [x.id for x in songs], tmp_path / "MoodChanges"
    tag.close()
    s.close()


def _user_mood(folder, name, song_ids, uid="uid-user-1"):
    with closing(mood_sync.connect(folder / "user.db")) as c:
        mood_sync.store_mood(c, folder, uid, mood_sync.parse_mood(
            {"name": name, "updated_at": 1000, "song_ids": song_ids}), 0)
        return c.execute("SELECT id FROM tags WHERE uid = ?", (uid,)).fetchone()[0]


def test_tag_kind_migration_marks_existing_tags_as_markers(tmp_path):
    db = sqlite3.connect(tmp_path / "tag.db")
    db.executescript((repo_root / "migrations/tag/0001_baseline.sql").read_text())
    db.execute("INSERT INTO tags (name) VALUES ('spellchecked'), ('album_checked')")
    db.executescript((repo_root / "migrations/tag/0003_tag_kind.sql").read_text())
    assert db.execute("SELECT DISTINCT kind FROM tags").fetchall() == [("marker",)]


def test_global_moods_never_include_markers(env):
    _, tag, *_ = env
    gym = moods.create_global_mood(tag, " Siłownia ")
    assert moods.list_global_moods(tag) == [(gym, "Siłownia", 0)]


@pytest.mark.parametrize("name, message", [("spellchecked", "technical tag"), ("Siłownia", "already")])
def test_names_taken_are_refused(env, name, message):
    _, tag, *_ = env
    moods.create_global_mood(tag, "Siłownia")
    with pytest.raises(moods.MoodError, match=message):
        moods.create_global_mood(tag, name)
    chill = moods.create_global_mood(tag, "Chill")
    with pytest.raises(moods.MoodError, match=message):
        moods.rename_global_mood(tag, chill, name)


def test_global_mood_songs_rename_and_delete(env):
    _, tag, _, ids, _ = env
    gym = moods.create_global_mood(tag, "Gym")
    assert moods.add_song_to_global_mood(tag, gym, ids[0])
    assert not moods.add_song_to_global_mood(tag, gym, ids[0])
    moods.add_song_to_global_mood(tag, gym, ids[1])
    assert moods.remove_song_from_global_mood(tag, gym, ids[1])
    assert not moods.remove_song_from_global_mood(tag, gym, ids[1])
    moods.rename_global_mood(tag, gym, "Siłownia")
    assert moods.list_global_moods(tag) == [(gym, "Siłownia", 1)]
    assert moods.delete_global_mood(tag, gym) == 1
    assert moods.list_global_moods(tag) == []
    with pytest.raises(moods.MoodError):
        moods.delete_global_mood(tag, 1)  # 'spellchecked' is a marker, not a mood


def test_promotion_moves_the_mood_and_leaves_a_marker(env):
    _, tag, folder, ids, _ = env
    mood_id = _user_mood(folder, "Siłownia", ids[:2])
    global_id, name, merged, added = moods.promote_user_mood(tag, "ania", mood_id)
    assert (name, merged, added) == ("Siłownia", False, 2)
    assert moods.global_mood_song_ids(tag, global_id) == ids[:2]
    assert moods.list_user_moods("ania") == []
    with closing(mood_sync.connect(folder / "user.db")) as c:
        (marker,) = mood_sync.changes_since(c, 1)["moods"]
    assert marker["deleted"] and marker["promoted_to"] == global_id and marker["promoted_to_name"] == "Siłownia"
    assert (folder / "archive" / "moods").exists()


def test_promotion_merges_into_an_existing_global_mood(env):
    _, tag, folder, ids, _ = env
    global_id = moods.create_global_mood(tag, "Siłownia")
    moods.add_song_to_global_mood(tag, global_id, ids[1])
    mood_id = _user_mood(folder, "Siłownia", ids[:3])
    assert moods.promote_user_mood(tag, "ania", mood_id) == (global_id, "Siłownia", True, 2)
    assert moods.global_mood_song_ids(tag, global_id) == ids[:3]


def test_promotion_is_refused_on_a_markers_name(env):
    _, tag, folder, ids, _ = env
    mood_id = _user_mood(folder, "spellchecked", ids[:1])
    with pytest.raises(moods.MoodError, match="technical tag"):
        moods.promote_user_mood(tag, "ania", mood_id)
    assert [m[2] for m in moods.list_user_moods("ania")] == ["spellchecked"]  # nothing moved
    assert moods.list_global_moods(tag) == []


# --------------------
# Changes from the phones
# --------------------

def _record(changes_dir, action, mood_id, song_id, checked_to=0, user="ola"):
    return global_mood_log.record_change(changes_dir, user, action, mood_id, "Gym", song_id, song_id, checked_to)


def test_phone_changes_are_applied_in_order_and_logged(env):
    _, tag, _, ids, changes_dir = env
    gym = moods.create_global_mood(tag, "Gym")
    _record(changes_dir, "add", gym, ids[0])
    _record(changes_dir, "add", gym, ids[0])
    _record(changes_dir, "add", gym, ids[1])
    _record(changes_dir, "remove", gym, ids[1])
    _record(changes_dir, "remove", gym, ids[2])
    _record(changes_dir, "add", 999, ids[0])

    history = moods.apply_global_mood_changes(tag, changes_dir)

    assert [r["result"] for r in history] == ["added", "unchanged", "added", "removed", "unchanged", "mood_gone"]
    assert moods.global_mood_song_ids(tag, gym) == [ids[0]]
    assert global_mood_log.pending_changes(changes_dir) == []
    logged = moods.global_mood_history(changes_dir)
    assert len(logged) == 6 and logged[0]["result"] == "mood_gone"  # newest first
    assert all(r["user"] == "ola" and isinstance(r["applied_at"], int) for r in logged)
    assert moods.apply_global_mood_changes(tag, changes_dir) == []


def test_phone_changes_follow_songs_merged_or_deleted_since_the_server_checked(env):
    s, tag, _, ids, changes_dir = env
    gym = moods.create_global_mood(tag, "Gym")
    _record(changes_dir, "add", gym, ids[1])
    _record(changes_dir, "add", gym, ids[2])
    sr.record_song_merge(s, ids[1], ids[0])
    s.delete(s.get(Song, ids[1]))
    s.delete(s.get(Song, ids[2]))
    s.commit()

    history = moods.apply_global_mood_changes(tag, changes_dir)

    assert [(r["result"], r["song_id"]) for r in history] == [("added", ids[0]), ("song_gone", None)]
    assert moods.global_mood_song_ids(tag, gym) == [ids[0]]


def test_damaged_pending_file_is_left_alone(env):
    _, tag, _, _, changes_dir = env
    (changes_dir / "pending").mkdir(parents=True)
    (changes_dir / "pending" / "0001_x.json").write_text("{not json")
    assert moods.apply_global_mood_changes(tag, changes_dir) == []
    assert (changes_dir / "pending" / "0001_x.json").exists()


# --------------------
# Permissions
# --------------------

def test_permission_names_match_the_app():
    assert set(account_auth.PERMISSIONS) == set(constants.PERMISSION_LABELS)
    assert account_auth.PERMISSION_EDIT_GLOBAL_MOODS == constants.PERMISSION_EDIT_GLOBAL_MOODS


def test_grant_and_revoke_permissions(env):
    *_, folder, _, _ = env
    users_dir = folder.parent
    assert users.user_permissions("ania") == []
    assert users.set_permissions("ania", ["edit_global_moods"]) == ["edit_global_moods"]
    assert account_auth.user_permissions(users_dir, "ania") == ["edit_global_moods"]
    account = json.loads((folder / "account.json").read_text())
    assert account["password"] == "x"  # the rest of account.json is kept
    assert users.set_permissions("ania", []) == []
    assert account_auth.user_permissions(users_dir, "ania") == []
    with pytest.raises(users.InvalidUserError):
        users.set_permissions("ania", ["fly"])


def test_unknown_or_malformed_permissions_are_ignored(tmp_path):
    (tmp_path / "ola").mkdir()
    (tmp_path / "ola" / "account.json").write_text(json.dumps({"permissions": ["edit_global_moods", "fly"]}))
    assert account_auth.user_permissions(tmp_path, "ola") == ["edit_global_moods"]
    (tmp_path / "ola" / "account.json").write_text(json.dumps({"permissions": "edit_global_moods"}))
    assert account_auth.user_permissions(tmp_path, "ola") == []
    assert account_auth.user_permissions(tmp_path, "nobody") == []
