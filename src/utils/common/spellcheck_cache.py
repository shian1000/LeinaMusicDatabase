"""
Disk-backed cache for :func:`utils.common.text_utils.check_spelling` results.

``check_spelling`` calls the MusicBrainz web service, which is the dominant cost
of importing MP3 tags. Persisting its results between runs means a re-import only
pays the network cost for genuinely new ``(artist, title)`` pairs; everything
seen before (including "no match found" answers, which are the slowest because
they trigger the fallback query and any backoff) is served from disk.

Delete the cache file (``data/spellcheck_cache.json`` by default) to force fresh
lookups of everything. A lookup MusicBrainz didn't answer (503, timeout) is
never stored; until 2026-11-25 it was, as "no match found" for good. To ask
again about every "no match" answer once (they're only a cache), with the app
and the automation not running::

    cd src && ../venv/bin/python -m utils.common.spellcheck_cache --forget-not-found
"""

import atexit
import json
import os
import tempfile
import threading
from typing import Optional

from utils.common.debug import flog, slog
from config.constants import SPELLCHECK_CACHE_FILE

_CACHE_PATH = SPELLCHECK_CACHE_FILE

_AUTOSAVE_EVERY = 20

_lock = threading.Lock()
_cache: Optional[dict] = None
_dirty = False
_pending = 0


def _key(artist: str, title: str) -> str:
    return f"{(artist or '').strip().lower()}\x1f{(title or '').strip().lower()}"


def _load_locked() -> dict:
    """Load the cache from disk once. Caller must hold ``_lock``."""
    global _cache
    if _cache is not None:
        return _cache
    try:
        with _CACHE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        _cache = data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        _cache = {}
    slog(f"[SPELLCACHE] Loaded {len(_cache)} entries from {_CACHE_PATH}", priority=1)
    return _cache


def get(artist: str, title: str) -> Optional[dict]:
    with _lock:
        cached = _load_locked().get(_key(artist, title))
        return dict(cached) if isinstance(cached, dict) else None


def put(artist: str, title: str, result: dict) -> None:
    global _dirty, _pending
    with _lock:
        _load_locked()[_key(artist, title)] = result
        _dirty = True
        _pending += 1
        should_save = _pending >= _AUTOSAVE_EVERY
    if should_save:
        save()


def save() -> None:
    """Atomically write the cache to disk if it changed since the last save."""
    global _dirty, _pending
    with _lock:
        if not _dirty or _cache is None:
            return
        try:
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(_CACHE_PATH.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(_cache, f, ensure_ascii=False)
                os.replace(tmp, _CACHE_PATH)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            _dirty = False
            _pending = 0
            slog(f"[SPELLCACHE] Saved {len(_cache)} entries to {_CACHE_PATH}", priority=1)
        except OSError as e:
            flog(f"[SPELLCACHE] Saving {_CACHE_PATH} failed: {e}")


# Only the import pipeline saves on its own; Check spelling and the fetchers
# would otherwise lose up to _AUTOSAVE_EVERY - 1 answers when the app exits
atexit.register(save)


def forget_not_found() -> int:
    """Drop every "no match found" answer (found False), so the next
    check_spelling() of those pairs asks MusicBrainz again. The number
    dropped; saved at once."""
    global _dirty
    with _lock:
        cache = _load_locked()
        stale = [key for key, value in cache.items() if isinstance(value, dict) and value.get("found") is False]
        for key in stale:
            del cache[key]
        _dirty = _dirty or bool(stale)
    save()
    return len(stale)


if __name__ == "__main__":
    import sys

    from utils.database.app_lock import try_exclusive_lock

    if sys.argv[1:] != ["--forget-not-found"]:
        sys.exit("Usage: python -m utils.common.spellcheck_cache --forget-not-found")
    # An open app keeps its own copy of the cache and writes it back on exit
    lock = try_exclusive_lock()
    if lock is None:
        sys.exit("The app or the automatic pass is running - close it first.")
    try:
        print(f"Dropped {forget_not_found()} \"not on MusicBrainz\" answers from {_CACHE_PATH}")
    finally:
        lock.close()
