"""
Checking a user's password and the phone's login tokens, against the files
in a user's folder (Users/<username>/, see users.py):

    account.json  username + salted scrypt password hash, and the
                  permissions the owner granted (written by users.py)
    tokens.json   {"tokens": [{"hash": <sha256 hex>, "created_at": ...,
                               "device": "Pixel 7a"}]}
                  "device" is the model name the phone sent with POST /login
                  (clean_device_name(); absent for older phones and older
                  entries), so a token can be told apart from another user's
                  phone's and revoked on its own

Standard-library only and free of the app's config, because the sharing
server (sharing_server.py, run on the system python3) imports it too, for
POST /login, POST /logout and signed inbox uploads (docs/runbooks/database.md
-> "Logging in from the phone"). users.py uses it for the same password
check and to revoke tokens.

Permissions say what the owner trusts a user with, so they live in
account.json rather than in user.db (the user's own data). The server reads
them on every request that needs one, so a revoked permission takes effect at
once, without logging in again.

A token is 32 random bytes the phone keeps instead of the password. Only its
SHA-256 is stored, so tokens.json leaks nothing usable. Tokens never expire;
one is revoked by /logout, and all of a user's are dropped when the password
is changed, the user renamed or deleted (users.py).

For a tool outside this repo that revokes tokens by editing tokens.json
(docs/runbooks/database.md -> "Logging in from the phone"): the file is
Users/<username>/tokens.json under the users folder the server runs with
(--users). Nothing caches it - every request that carries a token reads the
file again, so an entry removed is refused from the next request on, with no
restart. Write it like _write_tokens() does, while holding an exclusive flock
on Users/<username>/.tokens.lock (create it if missing) for the whole
read-modify-write: write the new content to .tokens.json.tmp in the same
folder, then os.replace() it over tokens.json (or delete tokens.json when no
token is left). Keep every other entry and its unknown keys as they are.
"""

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# Must match config.constants (tests/test_account_auth.py checks they do)
USERNAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,31}$"
ACCOUNT_FILENAME = "account.json"
TOKENS_FILENAME = "tokens.json"
TOKENS_LOCK_FILENAME = ".tokens.lock"
TOKEN_BYTES = 32
DEVICE_NAME_MAX_LENGTH = 64
# Every permission there is (also config.constants.PERMISSION_LABELS); the
# names are part of the phone contract
PERMISSION_EDIT_GLOBAL_MOODS = "edit_global_moods"
# Read by the app's automatic inbox pass (utils/database/inbox_auto.py), not the server
PERMISSION_APPLY_CORRECTIONS = "apply_corrections"
PERMISSIONS = (PERMISSION_EDIT_GLOBAL_MOODS, PERMISSION_APPLY_CORRECTIONS)

# Compared against when the user doesn't exist, so a wrong username takes as
# long as a wrong password (scrypt) and the answer doesn't tell them apart
_DUMMY_HASH = "scrypt$16384$8$1$" + "00" * 16 + "$" + "00" * 64

# The server answers requests in threads; one lock around every tokens.json
# read-modify-write in this process
_tokens_lock = threading.Lock()


@contextmanager
def _tokens_locked(folder: Path):
    """Around every read-modify-write of a user's tokens.json. Two processes
    write it - the server (login, logout) and the app (revoke_all_tokens()) -
    so besides the thread lock an flock on a file next to it: without it, a
    login read just before a password change wrote the revoked tokens back."""
    with _tokens_lock:
        with open(folder / TOKENS_LOCK_FILENAME, "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield


def normalize_username(username) -> str | None:
    """The folder name a typed username maps to, or None if it can't be one."""
    if not isinstance(username, str):
        return None
    name = username.strip().lower()
    return name if re.fullmatch(USERNAME_PATTERN, name) else None


def password_matches(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(candidate, bytes.fromhex(digest))
    except ValueError:
        return False


def check_login(users_dir, username, password) -> str | None:
    """The normalized username when the password is that user's, else None."""
    name = normalize_username(username)
    stored = None
    if name is not None and isinstance(password, str):
        try:
            account = json.loads((Path(users_dir) / name / ACCOUNT_FILENAME).read_text())
            stored = account.get("password")
        except (OSError, ValueError, AttributeError):
            stored = None
    if not isinstance(password, str):
        password = ""
    if not isinstance(stored, str):
        password_matches(password, _DUMMY_HASH)
        return None
    return name if password_matches(password, stored) else None


def user_permissions(users_dir, username: str) -> list[str]:
    """The known permissions in a user's account.json, sorted; [] when the
    user or the file is missing or unreadable."""
    try:
        granted = json.loads((Path(users_dir) / username / ACCOUNT_FILENAME).read_text()).get("permissions", [])
    except (OSError, ValueError, AttributeError):
        return []
    if not isinstance(granted, list):
        return []
    return sorted(p for p in set(granted) if p in PERMISSIONS)


def clean_device_name(value) -> str | None:
    """The device name a login may store: control and format characters
    dropped, trimmed, at most DEVICE_NAME_MAX_LENGTH characters. None for
    anything that isn't a string or leaves nothing."""
    if not isinstance(value, str):
        return None
    text = "".join(c for c in value if unicodedata.category(c) not in ("Cc", "Cf"))
    text = text.strip()[:DEVICE_NAME_MAX_LENGTH].rstrip()
    return text or None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _read_tokens(folder: Path) -> list:
    try:
        tokens = json.loads((folder / TOKENS_FILENAME).read_text()).get("tokens", [])
    except (OSError, ValueError, AttributeError):
        return []
    return [t for t in tokens if isinstance(t, dict) and isinstance(t.get("hash"), str)]


def _write_tokens(folder: Path, tokens: list) -> None:
    path = folder / TOKENS_FILENAME
    if not tokens:
        path.unlink(missing_ok=True)
        return
    tmp = folder / f".{TOKENS_FILENAME}.tmp"
    tmp.write_text(json.dumps({"tokens": tokens}, indent=2) + "\n")
    os.replace(tmp, path)


def _user_folders(users_dir) -> list[Path]:
    try:
        entries = list(Path(users_dir).iterdir())
    except OSError:
        return []
    return [p for p in entries
            if p.is_dir() and not p.name.startswith(".") and (p / ACCOUNT_FILENAME).exists()]


def issue_token(users_dir, username: str, device=None) -> str:
    """A new token for an existing user (call check_login() first), stored
    with the device name when one is given (cleaned by clean_device_name())."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    folder = Path(users_dir) / username
    entry = {"hash": _token_hash(token),
             "created_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    device = clean_device_name(device)
    if device is not None:
        entry["device"] = device
    with _tokens_locked(folder):
        tokens = _read_tokens(folder)
        tokens.append(entry)
        _write_tokens(folder, tokens)
    return token


def token_user(users_dir, token) -> str | None:
    """The username a token belongs to, or None (unknown or revoked)."""
    if not isinstance(token, str) or not token:
        return None
    wanted = _token_hash(token)
    for folder in _user_folders(users_dir):
        if any(hmac.compare_digest(t["hash"], wanted) for t in _read_tokens(folder)):
            return folder.name
    return None


def revoke_token(users_dir, token) -> bool:
    """Forget one token (logout). True if it existed."""
    if not isinstance(token, str) or not token:
        return False
    wanted = _token_hash(token)
    for folder in _user_folders(users_dir):
        with _tokens_locked(folder):
            tokens = _read_tokens(folder)
            kept = [t for t in tokens if not hmac.compare_digest(t["hash"], wanted)]
            if len(kept) != len(tokens):
                _write_tokens(folder, kept)
                return True
    return False


def revoke_all_tokens(user_folder) -> None:
    """Log a user out everywhere (password changed, renamed, deleted)."""
    folder = Path(user_folder)
    if not folder.is_dir():
        return
    with _tokens_locked(folder):
        (folder / TOKENS_FILENAME).unlink(missing_ok=True)
