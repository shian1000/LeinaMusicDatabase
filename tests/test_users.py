"""Users and their private user.db (utils/database/users.py): creating a
user (folder, schema, password hash), refusing bad or duplicate names and an
unmounted drive, password checks, and user DBs being backed up and migrated
alongside music.db/tag.db. Everything in temp dirs."""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import backup, migrations, song_references, users


@pytest.fixture
def users_env(tmp_path, monkeypatch):
    users_dir = tmp_path / "T7" / "Users"
    monkeypatch.setattr(users, "USERS_DIR", users_dir)
    monkeypatch.setattr(users, "users_available", lambda: True)
    monkeypatch.setattr(backup, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(backup, "DB_FILES", {})
    # Never reach the real music.db's song id log
    monkeypatch.setattr(song_references, "latest_change_id", lambda: 0)
    return users_dir


def _tables(path):
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def test_create_user_makes_folder_schema_and_account(users_env):
    folder = users.create_user("  Ania ", "secret1")

    assert folder == users_env / "ania"
    assert {"playlists", "playlist_tracks", "tags", "song_tags", "schema_version"} <= _tables(folder / "user.db")
    account = json.loads((folder / "account.json").read_text())
    assert account["username"] == "ania"
    assert "secret1" not in account["password"]
    assert users.list_usernames() == ["ania"]
    assert users.user_db_files() == {"ania": folder / "user.db"}
    assert not list(users_env.glob(".*"))  # no temporary folder left


def test_playlist_tracks_keep_order_and_allow_repeats(users_env):
    folder = users.create_user("ania", "secret1")
    conn = sqlite3.connect(folder / "user.db")
    conn.execute("INSERT INTO playlists (id, name, created_at, updated_at) VALUES (1, 'Mix', 0, 0)")
    for position, song_id in enumerate([42, None, 42]):
        conn.execute(
            "INSERT INTO playlist_tracks (playlist_id, position, song_id, title) VALUES (1, ?, ?, 't')",
            (position, song_id),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO playlist_tracks (playlist_id, position, title) VALUES (1, 0, 'dup')")
    rows = conn.execute("SELECT song_id FROM playlist_tracks ORDER BY position").fetchall()
    assert rows == [(42,), (None,), (42,)]


@pytest.mark.parametrize("name", ["", "a", "ania kowalska", "../x", "-ania", "a" * 33, "zoë"])
def test_bad_usernames_are_refused(users_env, name):
    with pytest.raises(users.InvalidUserError):
        users.create_user(name, "secret1")
    assert not users_env.exists() or not any(users_env.iterdir())


def test_duplicate_user_and_short_password_are_refused(users_env):
    users.create_user("ania", "secret1")
    with pytest.raises(users.InvalidUserError):
        users.create_user("ANIA", "secret2")
    with pytest.raises(users.InvalidUserError):
        users.create_user("bartek", "123")
    assert users.list_usernames() == ["ania"]


def test_unmounted_drive_creates_nothing(users_env, monkeypatch):
    monkeypatch.setattr(users, "users_available", lambda: False)
    with pytest.raises(users.UsersUnavailableError):
        users.create_user("ania", "secret1")
    assert not users_env.exists()
    assert users.user_db_files() == {}


def test_passwords_are_salted_hashes(users_env):
    users.create_user("ania", "secret1")
    assert users.verify_user("ania", "secret1")
    assert not users.verify_user("ania", "secret2")
    assert not users.verify_user("nobody", "secret1")
    assert not users.verify_user("../ania", "secret1")
    assert users.hash_password("x") != users.hash_password("x")


def test_backup_includes_user_dbs(users_env):
    users.create_user("ania", "secret1")
    created = backup.backup_databases(reason="test")
    assert [p.name.split(".")[0] for p in created] == ["user-ania"]


def test_new_user_migration_is_applied_after_a_backup(users_env, tmp_path, monkeypatch):
    users.create_user("ania", "secret1")
    assert migrations.run_pending_user_migrations() == {}

    track = tmp_path / "migrations" / "user"
    track.mkdir(parents=True)
    for f in (repo_root / "migrations" / "user").glob("*.sql"):
        (track / f.name).write_text(f.read_text())
    (track / "9999_extra.sql").write_text("CREATE TABLE extra (id INTEGER PRIMARY KEY);")
    monkeypatch.setattr(migrations, "MIGRATIONS_ROOT", tmp_path / "migrations")

    assert migrations.run_pending_user_migrations() == {"ania": ["9999_extra.sql"]}
    assert "extra" in _tables(users_env / "ania" / "user.db")
    assert len(list((tmp_path / "archive").glob("user-ania.*.db"))) == 1
    assert migrations.run_pending_user_migrations() == {}
