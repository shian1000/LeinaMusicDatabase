"""Making a user's account: the folder Users/<name>/ with account.json and an
empty user.db. Logging in with one is account_auth.py."""

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path

from . import account_auth, song_id_replay, user_schema
from .file_tools import now_iso

# Must match config.constants (tests/test_musicdatabase_core.py checks they do)
USER_PASSWORD_MIN_LENGTH = 6
USER_DB_FILENAME = "user.db"
PASSWORD_SCRYPT_N = 2 ** 14
PASSWORD_SCRYPT_R = 8
PASSWORD_SCRYPT_P = 1
PASSWORD_SALT_BYTES = 16


class InvalidAccountError(ValueError):
    """A username or password that can't be used, or a user that already exists."""


def account_name(username: str) -> str:
    """The folder name a username maps to; raises InvalidAccountError."""
    name = account_auth.normalize_username(username or "")
    if name is None:
        raise InvalidAccountError(
            "A username is 2-32 characters: lowercase letters, digits, '-' or '_', "
            "starting with a letter or digit."
        )
    return name


def check_password(password: str) -> None:
    if len(password or "") < USER_PASSWORD_MIN_LENGTH:
        raise InvalidAccountError(f"The password needs at least {USER_PASSWORD_MIN_LENGTH} characters.")


def hash_password(password: str) -> str:
    """"scrypt$<n>$<r>$<p>$<salt hex>$<hash hex>" - the cost parameters are
    kept with the hash, so they can be raised later for new passwords."""
    salt = secrets.token_bytes(PASSWORD_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=PASSWORD_SCRYPT_N, r=PASSWORD_SCRYPT_R, p=PASSWORD_SCRYPT_P
    )
    return f"scrypt${PASSWORD_SCRYPT_N}${PASSWORD_SCRYPT_R}${PASSWORD_SCRYPT_P}${salt.hex()}${digest.hex()}"


def create_account(users_dir: Path, username: str, password: str, latest_change_id: Callable[[], int],
                   initialize_db: Callable[[Path], object] = user_schema.apply_user_migrations) -> Path:
    """Create users_dir/<username>/ with an empty user.db (every migration in
    migrations/user/ applied) and account.json. Returns the folder. The
    folder is built under a temporary name and renamed at the end, so a
    failure never leaves a half-made user behind. latest_change_id gives the
    newest entry of music.db's song_id_changes."""
    name = account_name(username)
    check_password(password)
    final_dir = Path(users_dir) / name
    if final_dir.exists():
        raise InvalidAccountError(f"User '{name}' already exists.")

    tmp_dir = Path(users_dir) / f".{name}.creating"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir()
    try:
        db_path = tmp_dir / USER_DB_FILENAME
        initialize_db(db_path)
        # A new user has nothing to catch up on in the song id log
        conn = sqlite3.connect(str(db_path))
        try:
            song_id_replay.set_cursor(conn, latest_change_id())
            conn.commit()
        finally:
            conn.close()
        account = {
            "username": name,
            "password": hash_password(password),
            "created_at": now_iso(),
        }
        (tmp_dir / account_auth.ACCOUNT_FILENAME).write_text(json.dumps(account, indent=2) + "\n")
        os.rename(tmp_dir, final_dir)
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return final_dir
