"""What MDHS and MDW share (src/musicdatabase_core, ADR-0006): standard library
only, the old module names still the same modules, the sharing server still
starting as a script, and an account made and songs replayed without MDHS."""

import ast
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from config import constants
from musicdatabase_core import account_auth, accounts, playlist_sync, song_id_replay, user_schema
from utils.database import playlist_sync as old_playlist_sync

SRC = Path(__file__).resolve().parents[1] / "src"
CORE = SRC / "musicdatabase_core"
OLD_NAMES = ("account_auth", "playlist_sync", "mood_sync", "folder_sync", "saved_sync",
             "listen_sync", "global_mood_log")


@pytest.mark.parametrize("path", sorted(CORE.glob("*.py")), ids=lambda p: p.name)
def test_core_imports_only_the_standard_library(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module]
        else:
            continue
        for name in names:
            assert name.split(".")[0] in sys.stdlib_module_names, f"{path.name} imports {name}"


def test_constants_match_mdhs():
    assert accounts.USER_PASSWORD_MIN_LENGTH == constants.USER_PASSWORD_MIN_LENGTH
    assert accounts.USER_DB_FILENAME == constants.USER_DB_FILENAME
    assert accounts.PASSWORD_SCRYPT_N == constants.PASSWORD_SCRYPT_N
    assert accounts.PASSWORD_SCRYPT_R == constants.PASSWORD_SCRYPT_R
    assert accounts.PASSWORD_SCRYPT_P == constants.PASSWORD_SCRYPT_P
    assert accounts.PASSWORD_SALT_BYTES == constants.PASSWORD_SALT_BYTES


def test_old_name_is_the_same_module():
    assert old_playlist_sync is playlist_sync


def test_sharing_server_starts_as_a_script_from_another_folder(tmp_path):
    result = subprocess.run([sys.executable, str(SRC / "utils/database/sharing_server.py"), "--help"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", OLD_NAMES)
def test_old_name_works_with_only_its_folder_on_the_path(tmp_path, name):
    """A server started before the move imports folder_sync late, by this name."""
    code = (f"import sys; sys.path[:] = [{str(SRC / 'utils/database')!r}] + "
            "[p for p in sys.path if 'lib' in p]; "
            f"import {name}; assert {name}.__name__ == 'musicdatabase_core.{name}'")
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_create_account_needs_only_the_core(tmp_path):
    folder = accounts.create_account(tmp_path, " Ala ", "secret1", lambda: 7)

    assert folder == tmp_path / "ala"
    assert account_auth.check_login(tmp_path, "ala", "secret1") is not None
    assert account_auth.check_login(tmp_path, "ala", "wrong-one") is None
    conn = sqlite3.connect(folder / accounts.USER_DB_FILENAME)
    assert song_id_replay.cursor(conn) == 7
    assert user_schema.applied_filenames(conn) == {f.name for f in user_schema.migration_files()}
    conn.close()


def test_create_account_refuses_and_leaves_nothing_behind(tmp_path):
    with pytest.raises(accounts.InvalidAccountError):
        accounts.create_account(tmp_path, "a", "secret1", lambda: 0)
    with pytest.raises(accounts.InvalidAccountError):
        accounts.create_account(tmp_path, "ala", "short", lambda: 0)

    def no_log():
        raise OSError("no music.db")

    with pytest.raises(OSError):
        accounts.create_account(tmp_path, "ala", "secret1", no_log)
    assert list(tmp_path.iterdir()) == []

    accounts.create_account(tmp_path, "ala", "secret1", lambda: 0)
    with pytest.raises(accounts.InvalidAccountError, match="already exists"):
        accounts.create_account(tmp_path, "ala", "secret1", lambda: 0)


def test_migrations_applied_once(tmp_path):
    path = tmp_path / "user.db"
    assert user_schema.apply_user_migrations(path) == [f.name for f in user_schema.migration_files()]
    assert user_schema.apply_user_migrations(path) == []


def test_replay_follows_a_merge_and_a_deletion_on_a_plain_connection(tmp_path):
    path = tmp_path / "user.db"
    user_schema.apply_user_migrations(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO playlists (id, name, created_at, updated_at) VALUES (1, 'Mix', 1, 1)")
    for position, song_id in enumerate((10, 20)):
        conn.execute("INSERT INTO playlist_tracks (playlist_id, position, song_id, title) VALUES (1, ?, ?, 't')",
                     (position, song_id))
    conn.commit()

    assert song_id_replay.replay_user_db(conn, [(1, 10, 11), (2, 20, None)]) == 2
    conn.commit()

    assert [r[0] for r in conn.execute("SELECT song_id FROM playlist_tracks ORDER BY position")] == [11, None]
    assert song_id_replay.cursor(conn) == 2
    assert song_id_replay.replay_user_db(conn, [(1, 10, 11), (2, 20, None)]) == 0
    conn.close()
