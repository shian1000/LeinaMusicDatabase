"""
Users and their private data: main menu -> Users.

Every user gets a folder under Settings.users_dir (on the data drive, next to
the shared music.db/tag.db):

    Users/<username>/user.db       playlists + private tags, schema from migrations/user/
    Users/<username>/account.json  username, password hash, created_at

One SQLite file per user keeps a user's data invisible to the others at the
file level: whatever later serves it only ever hands out the file of the
user who logged in. Nothing is encrypted - the admin can read every file, and
users know that. Passwords are only stored as salted scrypt hashes, outside
user.db (that file may be sent to the phone one day). These files are the
originals, not copies, so backup_databases() includes them.

The phone logs in as one of these users through the sharing server
(account_auth.py: password check, tokens in Users/<username>/tokens.json).
user.db itself is never sent; its playlists sync with the user's phones
through the same server (playlist_sync.py, docs/decisions/0004-per-user-data.md).
"""

import hashlib
import json
import os
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path

from config.constants import (
    PASSWORD_SALT_BYTES,
    PASSWORD_SCRYPT_N,
    PASSWORD_SCRYPT_P,
    PASSWORD_SCRYPT_R,
    USER_ACCOUNT_FILENAME,
    USER_DB_FILENAME,
    USER_PASSWORD_MIN_LENGTH,
    USERNAME_PATTERN,
)
from settings import Settings, drive_unavailable_message, sharing_dir_available
from utils.database.account_auth import PERMISSIONS, password_matches, revoke_all_tokens
from utils.database.account_auth import user_permissions as _user_permissions

USERS_DIR = Path(str(Settings.users_dir)) if Settings.users_dir else None
USERS_DRIVE_MOUNT = Path(str(Settings.sharing_drive_mount)) if Settings.sharing_drive_mount else None
USERS_SHARING_DIR = Path(str(Settings.sharing_dir)) if Settings.sharing_dir else None


class UsersUnavailableError(Exception):
    """The users folder can't be reached right now (the data drive isn't plugged in)."""


class InvalidUserError(ValueError):
    """A username or password that can't be used, or a user that already exists."""


def users_available() -> bool:
    return sharing_dir_available(USERS_DRIVE_MOUNT, USERS_SHARING_DIR)


def users_unavailable_message() -> str:
    return drive_unavailable_message(USERS_DRIVE_MOUNT, USERS_SHARING_DIR)


def _require_users_dir() -> None:
    # Never mkdir under an unmounted mount point: the folder would land on the
    # system disk instead of the external drive (same rule as sharing.py).
    if not users_available():
        raise UsersUnavailableError(users_unavailable_message())
    USERS_DIR.mkdir(parents=True, exist_ok=True)


def normalize_username(username: str) -> str:
    """The folder name a username maps to; raises InvalidUserError."""
    name = (username or "").strip().lower()
    if not re.fullmatch(USERNAME_PATTERN, name):
        raise InvalidUserError(
            "A username is 2-32 characters: lowercase letters, digits, '-' or '_', "
            "starting with a letter or digit."
        )
    return name


def user_dir(username: str) -> Path:
    if USERS_DIR is None:
        raise UsersUnavailableError(users_unavailable_message())
    return USERS_DIR / normalize_username(username)


def list_usernames() -> list[str]:
    """Users with an account, sorted. Empty when the drive isn't mounted."""
    if not users_available() or not USERS_DIR.exists():
        return []
    return sorted(
        p.name for p in USERS_DIR.iterdir()
        if p.is_dir() and (p / USER_ACCOUNT_FILENAME).exists()
    )


def user_db_files() -> dict[str, Path]:
    """username -> that user's user.db, for backups and migrations. Empty
    when the drive isn't mounted."""
    files = {}
    for name in list_usernames():
        path = USERS_DIR / name / USER_DB_FILENAME
        if path.exists():
            files[name] = path
    return files


# --------------------
# Passwords
# --------------------

def hash_password(password: str) -> str:
    """"scrypt$<n>$<r>$<p>$<salt hex>$<hash hex>" - the cost parameters are
    kept with the hash, so they can be raised later for new passwords."""
    salt = secrets.token_bytes(PASSWORD_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=PASSWORD_SCRYPT_N, r=PASSWORD_SCRYPT_R, p=PASSWORD_SCRYPT_P
    )
    return f"scrypt${PASSWORD_SCRYPT_N}${PASSWORD_SCRYPT_R}${PASSWORD_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_user(username: str, password: str) -> bool:
    """True when the user exists and the password is theirs."""
    try:
        account_file = user_dir(username) / USER_ACCOUNT_FILENAME
    except InvalidUserError:
        return False
    if not account_file.exists():
        return False
    account = json.loads(account_file.read_text())
    return password_matches(password, account.get("password", ""))


# --------------------
# Creating a user
# --------------------

def create_user(username: str, password: str) -> Path:
    """Create Users/<username>/ with an empty user.db (every migration in
    migrations/user/ applied) and account.json. Returns the folder. The
    folder is built under a temporary name and renamed at the end, so a
    failure never leaves a half-made user behind."""
    from utils.database.migrations import initialize_user_db
    from utils.database.song_references import latest_change_id, set_cursor
    from utils.database.user_datatables import user_db_session

    name = normalize_username(username)
    if len(password or "") < USER_PASSWORD_MIN_LENGTH:
        raise InvalidUserError(f"The password needs at least {USER_PASSWORD_MIN_LENGTH} characters.")
    _require_users_dir()
    final_dir = USERS_DIR / name
    if final_dir.exists():
        raise InvalidUserError(f"User '{name}' already exists.")

    tmp_dir = USERS_DIR / f".{name}.creating"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir()
    try:
        initialize_user_db(tmp_dir / USER_DB_FILENAME)
        # A new user has nothing to catch up on in the song id log
        with user_db_session(tmp_dir / USER_DB_FILENAME) as session:
            set_cursor(session, latest_change_id())
            session.commit()
        account = {
            "username": name,
            "password": hash_password(password),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        (tmp_dir / USER_ACCOUNT_FILENAME).write_text(json.dumps(account, indent=2) + "\n")
        os.rename(tmp_dir, final_dir)
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return final_dir


# --------------------
# Changing and removing users
# --------------------

# Where delete_user() moves a user's folder: next to the database backups,
# on the system disk, so a deletion can be undone by moving it back
DELETED_USERS_DIR = Path(str(Settings.database_dir)) / "archive" / "deleted-users"


def _existing_user_dir(username: str) -> Path:
    if not users_available():
        raise UsersUnavailableError(users_unavailable_message())
    folder = user_dir(username)
    if not (folder / USER_ACCOUNT_FILENAME).exists():
        raise InvalidUserError(f"There's no user '{normalize_username(username)}'.")
    return folder


def _write_account(folder: Path, account: dict) -> None:
    tmp = folder / f"{USER_ACCOUNT_FILENAME}.tmp"
    tmp.write_text(json.dumps(account, indent=2) + "\n")
    os.replace(tmp, folder / USER_ACCOUNT_FILENAME)


def _read_account(folder: Path) -> dict:
    return json.loads((folder / USER_ACCOUNT_FILENAME).read_text())


def rename_user(old_username: str, new_username: str) -> Path:
    """Rename the folder and the name in account.json. Old backups keep the
    old name (archive/user-<old>.*.db)."""
    folder = _existing_user_dir(old_username)
    new_name = normalize_username(new_username)
    if new_name == folder.name:
        raise InvalidUserError(f"The user is already called '{new_name}'.")
    target = USERS_DIR / new_name
    if target.exists():
        raise InvalidUserError(f"User '{new_name}' already exists.")
    os.rename(folder, target)
    # The phone's tokens belong to the old name (its request file names carry it)
    revoke_all_tokens(target)
    account = _read_account(target)
    account["username"] = new_name
    _write_account(target, account)
    return target


def change_password(username: str, new_password: str) -> None:
    """Set a new password (an admin action - the old one isn't asked for)."""
    folder = _existing_user_dir(username)
    if len(new_password or "") < USER_PASSWORD_MIN_LENGTH:
        raise InvalidUserError(f"The password needs at least {USER_PASSWORD_MIN_LENGTH} characters.")
    account = _read_account(folder)
    account["password"] = hash_password(new_password)
    account["password_changed_at"] = datetime.now().isoformat(timespec="seconds")
    _write_account(folder, account)
    revoke_all_tokens(folder)


def delete_user(username: str) -> Path:
    """Remove a user: their folder (user.db, account.json) is moved to
    archive/deleted-users/<name>.<YYMMDD-HHMM>/, not erased. Returns where
    it went."""
    folder = _existing_user_dir(username)
    DELETED_USERS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%y%m%d-%H%M")
    target = DELETED_USERS_DIR / f"{folder.name}.{stamp}"
    suffix = 1
    while target.exists():
        suffix += 1
        target = DELETED_USERS_DIR / f"{folder.name}.{stamp}_{suffix}"
    revoke_all_tokens(folder)
    shutil.move(str(folder), str(target))
    return target


# --------------------
# Permissions
# --------------------

def user_permissions(username: str) -> list[str]:
    """What the owner allowed this user (account.json "permissions")."""
    folder = _existing_user_dir(username)
    return _user_permissions(folder.parent, folder.name)


def set_permissions(username: str, permissions) -> list[str]:
    """Replace a user's permissions; the sharing server sees the change on
    the phone's next request. Returns them sorted."""
    folder = _existing_user_dir(username)
    unknown = set(permissions) - set(PERMISSIONS)
    if unknown:
        raise InvalidUserError(f"Unknown permission(s): {', '.join(sorted(unknown))}")
    account = _read_account(folder)
    account["permissions"] = sorted(set(permissions))
    _write_account(folder, account)
    return account["permissions"]


def read_account(username: str) -> dict:
    """account.json of an existing user (password hash included)."""
    return _read_account(_existing_user_dir(username))
