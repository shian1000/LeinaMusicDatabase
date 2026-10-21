"""
Request packages: Leina Radio on a phone that can't reach the sharing server
exports its waiting outbox files as one .zip and its user sends it to the
owner by hand (app ADR 0014). Importing one puts its files into the inbox's
new/, exactly as if the phone had sent them with PUT /inbox/<name>, and the
owner reviews them as any other request (Manage database -> "Resolve new
requests"; the automatic pass may settle them too).

A package is taken only when every entry is what the sharing server would
accept over HTTP - a top-level file with a request file name
(sharing_server.REQUEST_NAME_RE), at most PHONE_REQUEST_MAX_BYTES, starting
with the SQLite header - and the whole is within REQUEST_PACKAGE_MAX_FILES /
REQUEST_PACKAGE_MAX_BYTES (read sizes, not the zip's own claims). Anything
else (a folder, "..", an absolute path, a symlink, an encrypted entry, a
duplicate name) refuses the whole package, saying why.

The user part of an imported name is a name typed on the phone, not a
login: inbox_requests.packages.json records it, so the file never acts with
an account's permissions.
"""

import os
import stat
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from config.constants import (
    PHONE_INBOX_LIMIT_BYTES,
    PHONE_REQUEST_MAX_BYTES,
    REQUEST_PACKAGE_MAX_BYTES,
    REQUEST_PACKAGE_MAX_FILES,
)
from settings import drive_unavailable_message
from utils.common.debug import mlog
from utils.database import inbox_requests
from utils.database.sharing_server import REQUEST_NAME_RE, SQLITE_HEADER


class PackageError(Exception):
    """The package is refused as a whole; the message says why."""


@dataclass
class PackageFile:
    name: str
    data: bytes
    sender: str | None  # the name's user part; None = sent without a name


@dataclass
class ImportResult:
    added: list[str] = field(default_factory=list)
    already_there: list[str] = field(default_factory=list)  # same name in the inbox: skipped


def read_package(path: Path) -> list[PackageFile]:
    """Check the package and return its request files; raises PackageError."""
    if not zipfile.is_zipfile(path):
        raise PackageError(f"{path.name} isn't a .zip file")
    try:
        with zipfile.ZipFile(path) as package:
            infos = package.infolist()
            if not infos:
                raise PackageError("the package is empty")
            if len(infos) > REQUEST_PACKAGE_MAX_FILES:
                raise PackageError(f"{len(infos)} files - a package may hold at most {REQUEST_PACKAGE_MAX_FILES}")
            seen = set()
            for info in infos:
                _check_entry(info, seen)
            files, total = [], 0
            for info in infos:
                with package.open(info) as f:
                    # The zip's own sizes can lie: read one byte past the limit
                    data = f.read(PHONE_REQUEST_MAX_BYTES + 1)
                if len(data) > PHONE_REQUEST_MAX_BYTES:
                    raise PackageError(f"{info.filename} is bigger than {PHONE_REQUEST_MAX_BYTES} bytes")
                total += len(data)
                if total > REQUEST_PACKAGE_MAX_BYTES:
                    raise PackageError(f"the files together are bigger than {REQUEST_PACKAGE_MAX_BYTES} bytes")
                if not data.startswith(SQLITE_HEADER):
                    raise PackageError(f"{info.filename} isn't an SQLite file")
                files.append(PackageFile(info.filename, data, REQUEST_NAME_RE.match(info.filename).group("user")))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, EOFError) as exc:
        # RuntimeError: an encrypted entry; NotImplementedError: an unknown compression
        raise PackageError(f"the package can't be read: {exc}") from None
    return files


def _check_entry(info: zipfile.ZipInfo, seen: set) -> None:
    name = info.filename
    if info.is_dir() or "/" in name or "\\" in name:
        raise PackageError(f"{name!r} is in a folder - a package holds only request files, no folders")
    if name in (".", "..") or ".." in name or ":" in name or name.startswith("/"):
        raise PackageError(f"{name!r} isn't a plain file name")
    if stat.S_ISLNK(info.external_attr >> 16):
        raise PackageError(f"{name!r} is a symbolic link")
    if info.flag_bits & 0x1:
        raise PackageError(f"{name!r} is encrypted")
    if not REQUEST_NAME_RE.match(name):
        raise PackageError(f"{name!r} isn't a Leina Radio request file name")
    if name in seen:
        raise PackageError(f"{name!r} is in the package twice")
    if info.file_size > PHONE_REQUEST_MAX_BYTES:
        raise PackageError(f"{name} is bigger than {PHONE_REQUEST_MAX_BYTES} bytes")
    seen.add(name)


def _in_inbox(name: str) -> bool:
    folders = (inbox_requests.NEW, inbox_requests.REVIEW, inbox_requests.PROCESSED, inbox_requests.REJECTED)
    return any((inbox_requests.INBOX_DIR / folder / name).exists() for folder in folders)


def _waiting_bytes() -> int:
    total = 0
    for folder in (inbox_requests.NEW, inbox_requests.REVIEW):
        directory = inbox_requests.INBOX_DIR / folder
        if directory.is_dir():
            total += sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
    return total


def import_package(path: Path) -> ImportResult:
    """Check the package and put its new files into the inbox's new/.
    Raises PackageError when the package is refused or the inbox can't
    take it (data drive missing, inbox full)."""
    if not inbox_requests.inbox_available():
        raise PackageError(f"the inbox isn't available: {drive_unavailable_message(inbox_requests.INBOX_DRIVE_MOUNT)}")
    files = read_package(path)
    result = ImportResult()
    fresh = []
    for f in files:
        if _in_inbox(f.name):
            result.already_there.append(f.name)
        else:
            fresh.append(f)
    adding = sum(len(f.data) for f in fresh)
    if fresh and _waiting_bytes() + adding > PHONE_INBOX_LIMIT_BYTES:
        raise PackageError("the inbox is full - resolve the waiting requests first")
    new_dir = inbox_requests.INBOX_DIR / inbox_requests.NEW
    new_dir.mkdir(parents=True, exist_ok=True)
    imported_at = datetime.now().astimezone().isoformat(timespec="seconds")
    # Recorded before the files appear: a file must never be read as sent by an account
    inbox_requests.record_package_files({f.name: {"sender": f.sender, "package": path.name,
                                                  "imported_at": imported_at} for f in fresh})
    for f in fresh:
        # Dot files are skipped by the inbox reader until renamed
        part = new_dir / f".{f.name}.part"
        try:
            with open(part, "wb") as out:
                out.write(f.data)
                out.flush()
                os.fsync(out.fileno())
            os.replace(part, new_dir / f.name)
        finally:
            part.unlink(missing_ok=True)
        result.added.append(f.name)
    mlog(f"Request package {path.name}: {len(result.added)} file(s) added, {len(result.already_there)} already there")
    return result
