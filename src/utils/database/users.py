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

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from config.constants import USER_ACCOUNT_FILENAME, USER_DB_FILENAME
from musicdatabase_core.accounts import (
    InvalidAccountError,
    account_name,
    check_password,
    create_account,
    hash_password,
)
from settings import Settings, drive_unavailable_message, sharing_dir_available
from utils.database.account_auth import PERMISSIONS, check_login, revoke_all_tokens
from utils.database.account_auth import user_permissions as _user_permissions

USERS_DIR = Path(str(Settings.users_dir)) if Settings.users_dir else None
USERS_DRIVE_MOUNT = Path(str(Settings.sharing_drive_mount)) if Settings.sharing_drive_mount else None
USERS_SHARING_DIR = Path(str(Settings.sharing_dir)) if Settings.sharing_dir else None


class UsersUnavailableError(Exception):
    """The users folder can't be reached right now (the data drive isn't plugged in)."""


InvalidUserError = InvalidAccountError


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


normalize_username = account_name


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

def verify_user(username: str, password: str) -> bool:
    """True when the user exists and the password is theirs. An unreadable
    account.json is a wrong password, the same as for the server's login."""
    try:
        folder = user_dir(username)
    except InvalidUserError:
        return False
    return check_login(folder.parent, folder.name, password) is not None


# --------------------
# Creating a user
# --------------------

def create_user(username: str, password: str) -> Path:
    """Create Users/<username>/ with an empty user.db (every migration in
    migrations/user/ applied) and account.json. Returns the folder."""
    from utils.database.migrations import initialize_user_db
    from utils.database.song_references import latest_change_id

    name = normalize_username(username)
    check_password(password)
    _require_users_dir()
    return create_account(USERS_DIR, name, password, latest_change_id, initialize_db=initialize_user_db)


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
    check_password(new_password)
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
