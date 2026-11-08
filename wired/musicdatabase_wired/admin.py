"""What the owner does to accounts, without any UI. The server re-reads these files on
every request, so changes reach it at once, without a restart."""

import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from musicdatabase_core import account_auth, accounts

from .config import Config
from .request_folders import RequestFolders


@dataclass(frozen=True)
class Device:
    name: str
    logged_in_at: str
    token_hash: str


def request_folders_of(config: Config) -> RequestFolders:
    return RequestFolders(config.inbox_dir, config.catalog_dir / "music.db")


def list_users(config: Config) -> list[str]:
    if not config.users_dir.is_dir():
        return []
    return sorted(path.name for path in config.users_dir.iterdir()
                  if (path / account_auth.ACCOUNT_FILENAME).exists())


def create_user(config: Config, username: str, password: str) -> str:
    config.users_dir.mkdir(parents=True, exist_ok=True)
    return accounts.create_account(config.users_dir, username, password, lambda: _latest_change_id(config)).name


def _latest_change_id(config: Config) -> int:
    """The newest entry of music.db's song id log, 0 without a catalog: a new user has nothing to catch up on."""
    path = config.catalog_dir / "music.db"
    if not path.exists():
        return 0
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT MAX(id) FROM song_id_changes").fetchone()[0] or 0
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def _existing(config: Config, username: str) -> Path:
    folder = config.users_dir / accounts.account_name(username)
    if not (folder / account_auth.ACCOUNT_FILENAME).exists():
        raise accounts.InvalidAccountError(f"There's no user '{username}'.")
    return folder


def change_password(config: Config, username: str, password: str) -> None:
    """Every phone of the user has to log in again."""
    folder = _existing(config, username)
    accounts.check_password(password)
    path = folder / account_auth.ACCOUNT_FILENAME
    account = json.loads(path.read_text())
    account["password"] = accounts.hash_password(password)
    account["password_changed_at"] = datetime.now().isoformat(timespec="seconds")
    temporary = folder / f".{account_auth.ACCOUNT_FILENAME}.tmp"
    temporary.write_text(json.dumps(account, indent=2) + "\n")
    os.replace(temporary, path)
    account_auth.revoke_all_tokens(folder)


def delete_user(config: Config, username: str) -> Path:
    """The folder moves to the archive, it isn't erased. Returns where it went."""
    folder = _existing(config, username)
    config.archive_dir.mkdir(parents=True, exist_ok=True)
    target = config.archive_dir / f"{folder.name}.{datetime.now().strftime('%y%m%d-%H%M%S')}"
    account_auth.revoke_all_tokens(folder)
    shutil.move(str(folder), str(target))
    return target


def devices(config: Config, username: str) -> list[Device]:
    try:
        entries = json.loads((_existing(config, username) / account_auth.TOKENS_FILENAME).read_text()).get("tokens", [])
    except (OSError, ValueError, AttributeError):
        return []
    return [Device(entry.get("device") or "unknown device", entry.get("created_at", ""), entry["hash"])
            for entry in entries if isinstance(entry, dict) and isinstance(entry.get("hash"), str)]


def revoke_device(config: Config, username: str, token_hash: str) -> None:
    folder = _existing(config, username)
    with account_auth._tokens_locked(folder):
        tokens = account_auth._read_tokens(folder)
        account_auth._write_tokens(folder, [t for t in tokens if t["hash"] != token_hash])
