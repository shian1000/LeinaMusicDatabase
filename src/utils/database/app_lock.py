"""
Keeps the automatic inbox pass (auto_resolve_requests.py) and the app apart:
both write music.db, and an open app holds ORM objects that a background
write would make stale (a live edit has been clobbered by an out-of-band
write before - AGENTS.md, Database safety).

An flock on <database_dir>/.musicdatabase.lock: every running app holds it
shared (two apps open at once is fine), the pass needs it exclusive and
skips its run when it can't have it. An app started during a run waits for
the run to end.
"""

import fcntl
from pathlib import Path

from settings import Settings

LOCK_FILE = Path(str(Settings.database_dir)) / ".musicdatabase.lock"

_held = []  # keeps the app's lock open for the life of the process


def _open():
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    return open(LOCK_FILE, "a")


def hold_app_lock(on_wait=None) -> None:
    """For main.py: hold the shared lock until the process ends.
    `on_wait()` is called once if an automatic pass is running."""
    handle = _open()
    try:
        fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        if on_wait:
            on_wait()
        fcntl.flock(handle, fcntl.LOCK_SH)
    _held.append(handle)


def try_exclusive_lock():
    """For the automatic pass: the locked file (close it to release), or
    None while an app is open."""
    handle = _open()
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle
