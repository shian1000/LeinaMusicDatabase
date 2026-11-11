"""Small file helpers both programs use: an SQLite file opened read-only,
a file written whole through a temporary one, the time stamp stored in
JSON files. Standard library only."""

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from .request_format import SQLITE_HEADER


def connect_read_only(path, timeout: float = 5.0, immutable: bool = False) -> sqlite3.Connection:
    """`path` opened read-only. as_uri() escapes "?", "#" and "%" in the
    folder names. `immutable`: no locks, journal or -shm next to a file only
    read, for one nobody writes in place."""
    flags = "mode=ro&immutable=1" if immutable else "mode=ro"
    return sqlite3.connect(f"{Path(path).absolute().as_uri()}?{flags}", uri=True, timeout=timeout)


def sqlite_problem(path) -> str | None:
    """What makes the file at `path` no sound SQLite database, to follow its
    name in a message (" isn't an SQLite database", ": <error>", " failed
    SQLite's integrity check: ..."); None when it is one."""
    with open(path, "rb") as file:
        if file.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
            return " isn't an SQLite database"
    conn = connect_read_only(path)
    try:
        conn.execute("PRAGMA trusted_schema=OFF")
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.Error as exc:
        return f": {exc}"
    finally:
        conn.close()
    return None if result == "ok" else f" failed SQLite's integrity check: {result}"


def write_atomically(target, data: bytes | str, tmp_name: str | None = None, fsync: bool = False) -> None:
    """`data` written to `target` through a temporary file next to it, so a
    reader sees the old file or the new one, never half of one. `tmp_name`:
    the temporary file's name, for folders whose listing skips such names
    (default "<name>.tmp"); `fsync`: on disk before the swap. A failed write
    leaves no temporary file."""
    target = Path(target)
    tmp = target.with_name(tmp_name or f"{target.name}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
            if fsync:
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def now_iso() -> str:
    """Now, with this machine's offset, to the second ("2026-11-10T14:03:22+01:00")."""
    return datetime.now().astimezone().isoformat(timespec="seconds")
