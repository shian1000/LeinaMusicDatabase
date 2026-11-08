"""The inbox's folder: storing a request file from Leina Player. What the file may
be is checked before this (request_format.py); who settles it is another matter."""

import os
import threading

from .request_format import REQUEST_NAME_RE
from .server_rules import ANONYMOUS_INBOX_SHARE

INBOX_NEW = "new"
INBOX_VALID = "valid"
INBOX_REVIEW = "review"
INBOX_PROCESSED = "processed"
# Where a request sent again counts as received already - by HTTP and in a
# package (request_packages.py) alike. Not rejected/: a file rejected as
# unreadable may come back fixed, and then it's a new request.
INBOX_KEPT_FOLDERS = (INBOX_NEW, INBOX_VALID, INBOX_REVIEW, INBOX_PROCESSED)
DEFAULT_MAX_REQUEST_BYTES = 1024 * 1024
DEFAULT_INBOX_LIMIT_BYTES = 100 * 1024 * 1024
# Partial uploads start with a dot; the inbox reader skips them
_PART_PREFIX = "."
_PART_SUFFIX = ".part"

STORED = "stored"
ALREADY_HERE = "already here"
NAME_TAKEN = "name taken"
FULL = "full"
FULL_FOR_UNSIGNED = "full for unsigned"


def _unsigned_size(path: str) -> int:
    """Bytes of the request files in `path` whose name carries no user."""
    total = 0
    if not os.path.isdir(path):
        return 0
    with os.scandir(path) as entries:
        for entry in entries:
            match = REQUEST_NAME_RE.match(entry.name)
            if match and match.group("user") is None and entry.is_file(follow_symlinks=False):
                total += entry.stat(follow_symlinks=False).st_size
    return total


def _dir_size(path: str) -> int:
    total = 0
    if not os.path.isdir(path):
        return 0
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.is_file(follow_symlinks=False):
                total += entry.stat(follow_symlinks=False).st_size
    return total


def _same_content(path: str, data: bytes) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(data) + 1) == data
    except OSError:
        return False


class Inbox:
    def __init__(self, folder: str, limit_bytes: int = DEFAULT_INBOX_LIMIT_BYTES):
        self.folder = str(folder)
        self.limit_bytes = limit_bytes
        # One lock for "is there room / is the name free" + the write, so two
        # uploads at once can't both squeeze past the limit or take one name.
        self._lock = threading.Lock()

    def store(self, name: str, data: bytes, unsigned: bool = False) -> str:
        """One of the outcomes above; OSError when the disk fails. Never overwrites."""
        new_dir = os.path.join(self.folder, INBOX_NEW)
        target = os.path.join(new_dir, name)
        with self._lock:
            os.makedirs(new_dir, exist_ok=True)
            # Sent again (e.g. the answer to the first try got lost)
            for folder in INBOX_KEPT_FOLDERS:
                existing = os.path.join(self.folder, folder, name)
                if os.path.exists(existing):
                    return ALREADY_HERE if _same_content(existing, data) else NAME_TAKEN
            waiting = [new_dir, os.path.join(self.folder, INBOX_VALID), os.path.join(self.folder, INBOX_REVIEW)]
            if sum(_dir_size(folder) for folder in waiting) + len(data) > self.limit_bytes:
                return FULL
            if unsigned and (sum(_unsigned_size(folder) for folder in waiting) + len(data)
                             > self.limit_bytes * ANONYMOUS_INBOX_SHARE):
                return FULL_FOR_UNSIGNED
            part = os.path.join(new_dir, f"{_PART_PREFIX}{name}{_PART_SUFFIX}")
            try:
                with open(part, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(part, target)
            finally:
                if os.path.exists(part):
                    os.unlink(part)
        return STORED
