"""Whether MusicBrainz knows an artist of a name - for telling a real
artist's name from a channel's or from two names joined by "and"
(the song request review's channel names, a "ft. A and B" credit).

One artist search per name (mb_get(), rate-limited), its answer cached in
MUSICBRAINZ_ARTIST_CACHE_FILE (normalized name -> true / false); a search
MusicBrainz didn't answer gives None and is cached nowhere, so the next
call asks again (as check_spelling()). A name counts as known when a hit
scored at least MUSICBRAINZ_ARTIST_MIN_SCORE is called exactly that - its
name, sort name or an alias, normalize()d - never a similar one."""

import threading

from config.constants import MUSICBRAINZ_ARTIST_CACHE_FILE, MUSICBRAINZ_ARTIST_MIN_SCORE, MUSICBRAINZ_WS_ROOT
from utils.common.debug import mlog
from utils.common.json_file import read_json, write_json
from utils.common.musicbrainz_client import lucene_quote, mb_get
from utils.common.normalizer import normalize

CACHE_PATH = MUSICBRAINZ_ARTIST_CACHE_FILE

_lock = threading.Lock()
_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        _cache = read_json(CACHE_PATH, {}, "the MusicBrainz artist cache")
    return _cache


def forget_cache() -> None:
    """Read the file again at the next call (tests, a changed CACHE_PATH)."""
    global _cache
    with _lock:
        _cache = None


def cached_answer(name: str) -> bool | None:
    """The cached answer for `name`, None when it was never asked."""
    with _lock:
        value = _load().get(normalize(name or ""))
    return value if isinstance(value, bool) else None


def artist_on_musicbrainz(name: str | None) -> bool | None:
    """True when MusicBrainz has an artist called exactly `name`, False when
    it answered without one, None when it couldn't be asked (or `name` is
    empty)."""
    key = normalize(name or "")
    if not key:
        return None
    known = cached_answer(name)
    if known is not None:
        return known
    response = mb_get(f"{MUSICBRAINZ_WS_ROOT}artist/", {
        "query": f"artist:{lucene_quote(name)} OR alias:{lucene_quote(name)}", "limit": 10, "fmt": "json"})
    if response is None:
        return None
    try:
        hits = response.json().get("artists", [])
    except ValueError:
        return None
    found = any(int(hit.get("score") or 0) >= MUSICBRAINZ_ARTIST_MIN_SCORE
                and key in {normalize(n or "") for n in (hit.get("name"), hit.get("sort-name"),
                                                         *(a.get("name") for a in hit.get("aliases") or []))}
                for hit in hits if isinstance(hit, dict))
    with _lock:
        _load()[key] = found
        write_json(CACHE_PATH, _cache, atomic=True)
    mlog(f"[MusicBrainz] artist '{name}': {'known' if found else 'not known'}", 1)
    return found
