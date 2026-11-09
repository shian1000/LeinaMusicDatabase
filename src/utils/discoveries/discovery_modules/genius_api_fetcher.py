import requests

from config.constants import (
    GENIUS_API_MIN_INTERVAL,
    GENIUS_API_SEARCH_URL,
    GENIUS_API_SONG_URL,
    HTTP_LOOKUP_TIMEOUT,
    SPELLING_CHECK_THRESHOLD,
)
from settings import settings
from utils.common.debug import flog, slog
from utils.common.output import warn
from utils.common.request_pacing import RequestPacer
from utils.common.text_utils import is_blacklisted_album, similarity
from utils.discoveries.discovery_result import DiscoveryResult

# Genius's official API, with a client access token (GENIUS_ACCESS_TOKEN in
# .env). Off by default (constants.DISCOVERY_MODULES_NEEDING_API_KEY);
# genius_fetcher.py is the keyless version that needs a browser. No
# get_release_year(), like that one: see docs/agent-notes/discovery-modules.md.
MODULE_NAME = "Genius fetcher (Curl + API)"

_pacer = RequestPacer(GENIUS_API_MIN_INTERVAL)
_warned_about_token = False


def _get(url: str, params: dict | None = None) -> dict:
    """The `response` part of an API answer ({} when the call fails)."""
    _pacer.wait()
    try:
        response = requests.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {settings.genius_access_token}"},
            timeout=HTTP_LOOKUP_TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get("response", {})
    except requests.RequestException as exc:
        flog(f"[Genius API] Request failed: {exc}")
        return {}


def _find_best_hit(hits: list[dict], artist: str, title: str) -> dict | None:
    """The song resembling both `title` and `artist` most, or None if nothing
    clears the threshold for both."""
    best = None
    best_score = 0.0

    for hit in hits:
        song = hit.get("result") or {}
        song_artist = (song.get("primary_artist") or {}).get("name")
        if hit.get("type") != "song" or not song.get("title") or not song_artist:
            continue

        title_sim = similarity(title, song["title"])
        artist_sim = similarity(artist, song_artist)
        if title_sim < SPELLING_CHECK_THRESHOLD or artist_sim < SPELLING_CHECK_THRESHOLD:
            continue

        score = min(title_sim, artist_sim)
        if score > best_score:
            best_score = score
            best = song

    return best


def get_album_name(artist: str, title: str) -> DiscoveryResult | None:
    global _warned_about_token
    if not settings.genius_access_token:
        if not _warned_about_token:
            _warned_about_token = True
            warn("Genius API lookups skipped: GENIUS_ACCESS_TOKEN isn't set in .env.")
        return None

    hits = _get(GENIUS_API_SEARCH_URL, {"q": f"{artist} {title}"}).get("hits", [])
    song = _find_best_hit(hits, artist, title)
    if not song:
        flog("[Genius API] No matching song in search results")
        return None

    # The search hit doesn't name the album; only the song's own record does
    album = (_get(GENIUS_API_SONG_URL.format(song_id=song["id"])).get("song", {}).get("album") or {}).get("name")
    if not album or is_blacklisted_album(album, artist):
        flog("[Genius API] No album on the song's record")
        return None

    slog(f"[Genius API] Matched: {song['title']} ({album})", priority=1)
    return DiscoveryResult(album=album, matched_title=song["title"], matched_artist=song["primary_artist"]["name"])
