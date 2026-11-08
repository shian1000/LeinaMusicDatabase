"""The inbox as folders (ADR-0006): a request arrives in new/, the only folder the outside
world can put anything into, and the server moves it on after checking it.

    new/       just arrived
    valid/     passed the check, rewritten; the first task ends here, and MDHS picks up from here
    held/      a kind of request this version doesn't know yet
    rejected/  failed the check, kept for the owner to look at

The two programs never talk to each other: this folder layout is all they share. Everything
is private to the account running the server, so only it and the machine's admin can read it.
"""

import os
import stat
import threading
from dataclasses import dataclass
from pathlib import Path

from musicdatabase_core import inbox_files
from musicdatabase_core.inbox_files import DEFAULT_INBOX_LIMIT_BYTES, INBOX_NEW, INBOX_PROCESSED, INBOX_REVIEW

from . import vetting

VALID, HELD, REJECTED = inbox_files.INBOX_VALID, "held", "rejected"
PRIVATE_FOLDER = 0o700
PRIVATE_FILE = 0o600
# Failed files are only kept for a look: a user sending bad ones can't fill the disk
REJECTED_LIMIT_BYTES = 20 * 1024 * 1024
# Where MDHS moves a valid request once it has dealt with it: sent again, it's already here
_DONE_FOLDERS = (VALID, INBOX_REVIEW, INBOX_PROCESSED)
_COUNTED_FOLDERS = (INBOX_NEW, VALID, HELD)
_WORK_FOLDER = ".checking"

STORED, ALREADY_HERE, NAME_TAKEN, FULL = (inbox_files.STORED, inbox_files.ALREADY_HERE, inbox_files.NAME_TAKEN,
                                          inbox_files.FULL)
REFUSED = "refused"
NOTHING_TO_DO = "nothing to do"


class NotPrivateError(Exception):
    pass


@dataclass(frozen=True)
class Received:
    outcome: str
    reason: str | None = None


def _write_privately(target: Path, data: bytes) -> None:
    part = target.with_name(f".{target.name}.part")
    try:
        descriptor = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE)
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)


def _same(path: Path, data: bytes) -> bool:
    try:
        return path.read_bytes() == data
    except OSError:
        return False


def _size(folder: Path) -> int:
    return sum(path.stat().st_size for path in folder.iterdir() if path.is_file()) if folder.is_dir() else 0


class RequestFolders:
    def __init__(self, inbox_dir: Path, catalog_db: Path, limit_bytes: int = DEFAULT_INBOX_LIMIT_BYTES):
        self.root = Path(inbox_dir)
        self.catalog_db = Path(catalog_db)
        self.limit_bytes = limit_bytes
        # "is there room / is the name free" + the write happen together
        self._lock = threading.Lock()

    def folder(self, name: str) -> Path:
        return self.root / name

    def make_private(self) -> None:
        """NotPrivateError where permissions don't work (exFAT, FAT, some network drives)."""
        self.root.mkdir(parents=True, exist_ok=True)
        folders = [self.root, *(self.folder(name) for name in (INBOX_NEW, VALID, HELD, REJECTED, _WORK_FOLDER))]
        for folder in folders:
            folder.mkdir(exist_ok=True)
            try:
                os.chmod(folder, PRIVATE_FOLDER)
            except PermissionError:
                pass  # the check below says what that means
            if stat.S_IMODE(folder.stat().st_mode) != PRIVATE_FOLDER:
                raise NotPrivateError(f"{folder} can't be made private: this drive doesn't keep permissions. "
                                      "Put the data on one that does (ext4, for example).")

    def receive(self, name: str, data: bytes, sender: str | None) -> Received:
        new_file = self.folder(INBOX_NEW) / name
        with self._lock:
            if new_file.exists():
                # Being checked by another connection, or left by a stop in the middle of one
                return Received(ALREADY_HERE if _same(new_file, data) else NAME_TAKEN)
            if sum(_size(self.folder(folder)) for folder in _COUNTED_FOLDERS) + len(data) > self.limit_bytes:
                return Received(FULL)
            _write_privately(new_file, data)
        return self._sort(new_file, data, sender)

    def _sort(self, new_file: Path, data: bytes, sender: str | None) -> Received:
        vetted = vetting.vet_request(data, sender, self.catalog_db, self.folder(_WORK_FOLDER))
        with self._lock:
            try:
                return self._file(new_file.name, data, vetted, sender)
            finally:
                new_file.unlink(missing_ok=True)

    def _file(self, name: str, data: bytes, vetted: vetting.Vetted, sender: str | None) -> Received:
        if vetted.state == vetting.EMPTY:
            return Received(NOTHING_TO_DO)
        if vetted.state == vetting.REFUSED:
            vetting.log_refusal(self.root, name, sender, vetted.reason)
            if _size(self.folder(REJECTED)) + len(data) <= REJECTED_LIMIT_BYTES:
                _write_privately(self.folder(REJECTED) / name, data)
            return Received(REFUSED, vetted.reason)
        folders, content, target = ((HELD,), data, HELD) if vetted.state == vetting.HELD \
            else (_DONE_FOLDERS, vetted.clean, VALID)
        for folder in folders:
            existing = self.folder(folder) / name
            if existing.exists():
                return Received(ALREADY_HERE if _same(existing, content) else NAME_TAKEN)
        _write_privately(self.folder(target) / name, content)
        return Received(STORED, vetted.reason)

    def sort_leftovers(self) -> int:
        """What a stop left in new/ gets its check at the next start."""
        count = 0
        for path in sorted(self.folder(INBOX_NEW).glob("*.db")):
            self._sort(path, path.read_bytes(), vetting.sender_of_name(path.name))
            count += 1
        for leftover in self.folder(_WORK_FOLDER).iterdir():
            vetting.remove_work_folder(leftover)
        return count

    def counts(self) -> dict[str, int]:
        return {name: len(list(self.folder(name).glob("*.db"))) for name in (VALID, HELD, REJECTED)}

    def recheck_held(self) -> dict[str, int]:
        """After updating MDW: what it now understands moves on. How many ended up where."""
        counts = {vetting.ACCEPTED: 0, vetting.REFUSED: 0, vetting.HELD: 0, vetting.EMPTY: 0}
        for path in sorted(self.folder(HELD).glob("*.db")):
            data = path.read_bytes()
            sender = vetting.sender_of_name(path.name)
            vetted = vetting.vet_request(data, sender, self.catalog_db, self.folder(_WORK_FOLDER))
            counts[vetted.state] += 1
            if vetted.state == vetting.HELD:
                continue
            with self._lock:
                path.unlink()
                self._file(path.name, data, vetted, sender)
        return counts
