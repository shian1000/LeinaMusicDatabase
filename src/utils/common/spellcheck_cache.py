"""
Disk-backed cache for :func:`utils.common.text_utils.check_spelling` results.

``check_spelling`` calls the MusicBrainz web service, which is the dominant cost
of importing MP3 tags. Persisting its results between runs means a re-import only
pays the network cost for genuinely new ``(artist, title)`` pairs; everything
seen before (including "no match found" answers, which are the slowest because
they trigger the fallback query and any backoff) is served from disk.

Delete the cache file (``data/spellcheck_cache.json`` by default) to force fresh
lookups of everything.
"""

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional

from utils.common.debug import slog
from config.constants import SPELLCHECK_CACHE_FILE

# src/utils/common/spellcheck_cache.py -> parents[3] is the project root.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CACHE_PATH = _PROJECT_ROOT / SPELLCHECK_CACHE_FILE

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
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(_cache, f, ensure_ascii=False)
            os.replace(tmp, _CACHE_PATH)
            _dirty = False
            _pending = 0
            slog(f"[SPELLCACHE] Saved {len(_cache)} entries to {_CACHE_PATH}", priority=1)
        except OSError as e:
            slog(f"[SPELLCACHE] Failed to save cache: {e}", priority=1)
