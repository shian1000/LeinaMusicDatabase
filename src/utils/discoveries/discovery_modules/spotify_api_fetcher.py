import base64
import time

import requests

from config.constants import (
    HTTP_LOOKUP_TIMEOUT,
    SPELLING_CHECK_THRESHOLD,
    SPOTIFY_API_MIN_INTERVAL,
    SPOTIFY_API_SEARCH_LIMIT,
    SPOTIFY_API_SEARCH_URL,
    SPOTIFY_API_TOKEN_MARGIN,
    SPOTIFY_API_TOKEN_URL,
)
from settings import settings
from utils.common.debug import flog, slog
from utils.common.output import warn
from utils.common.request_pacing import RequestPacer
from utils.common.text_utils import is_blacklisted_album, similarity
from utils.discoveries.discovery_result import DiscoveryResult, YearDiscoveryResult

# Spotify's official Web API, with an app's SPOTIFY_CLIENT_ID /
# SPOTIFY_CLIENT_SECRET from .env. Off by default
# (constants.DISCOVERY_MODULES_NEEDING_API_KEY); spotify_fetcher.py is the
# keyless version that needs a browser.
MODULE_NAME = "Spotify fetcher (Curl + API)"

_pacer = RequestPacer(SPOTIFY_API_MIN_INTERVAL)
_token = None
_token_expires = 0.0
_warned_about_keys = False


def _access_token() -> str | None:
    """The app's token (client credentials), kept until shortly before it expires."""
    global _token, _token_expires, _warned_about_keys
    if _token and time.monotonic() < _token_expires:
        return _token

    if not (settings.spotify_client_id and settings.spotify_client_secret):
        if not _warned_about_keys:
            _warned_about_keys = True
            warn("Spotify API lookups skipped: SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET aren't set in .env.")
        return None

    credentials = f"{settings.spotify_client_id}:{settings.spotify_client_secret}".encode()
    try:
        response = requests.post(
            SPOTIFY_API_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {base64.b64encode(credentials).decode()}"},
            timeout=HTTP_LOOKUP_TIMEOUT,
        )
        response.raise_for_status()
        answer = response.json()
    except requests.RequestException as exc:
        flog(f"[Spotify API] Couldn't get a token: {exc}")
        return None

    _token = answer.get("access_token")
    _token_expires = time.monotonic() + answer.get("expires_in", 0) - SPOTIFY_API_TOKEN_MARGIN
    return _token


def _search(query: str, kind: str) -> list[dict]:
    """Items of a search for "track" or "album" ([] when it fails)."""
    token = _access_token()
    if not token:
        return []

    _pacer.wait()
    try:
        response = requests.get(
            SPOTIFY_API_SEARCH_URL,
            params={"q": query, "type": kind, "limit": SPOTIFY_API_SEARCH_LIMIT},
            headers={"Authorization": f"Bearer {token}"},
            timeout=HTTP_LOOKUP_TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get(f"{kind}s", {}).get("items", [])
    except requests.RequestException as exc:
        flog(f"[Spotify API] Search failed for {query}: {exc}")
        return []


def _find_best(items: list[dict], artist: str, name: str) -> dict | None:
    """The item resembling both `name` and `artist` most, or None if nothing
    clears the threshold for both. At an equal score a real album beats a
    single."""
    best = None
    best_rank = None

    for item in items:
        # Spotify can leave null holes in a result list
        if not item or not item.get("name"):
            continue
        artists = [a.get("name", "") for a in item.get("artists", [])]
        if not artists:
            continue

        name_sim = similarity(name, item["name"])
        artist_sim = max(similarity(artist, a) for a in artists)
        if name_sim < SPELLING_CHECK_THRESHOLD or artist_sim < SPELLING_CHECK_THRESHOLD:
            continue

        album_type = (item.get("album") or item).get("album_type")
        rank = (min(name_sim, artist_sim), album_type == "album")
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best = item

    return best


def get_album_name(artist: str, title: str) -> DiscoveryResult | None:
    track = _find_best(_search(f"{artist} {title}", "track"), artist, title)
    if not track:
        flog("[Spotify API] No matching track in search results")
        return None

    album = (track.get("album") or {}).get("name")
    if not album or is_blacklisted_album(album, artist):
        return None

    slog(f"[Spotify API] Matched: {track['name']} ({album})", priority=1)
    return DiscoveryResult(album=album, matched_title=track["name"], matched_artist=track["artists"][0]["name"])


def get_release_year(artist: str, query: str, is_single: bool) -> YearDiscoveryResult | None:
    """is_single=True: query is a song title, the year is its album's.
    is_single=False: query is an album title."""
    kind = "track" if is_single else "album"
    item = _find_best(_search(f"{artist} {query}", kind), artist, query)
    if not item:
        flog(f"[Spotify API] No matching {kind} in search results")
        return None

    release_date = ((item.get("album") or {}) if is_single else item).get("release_date") or ""
    if not release_date[:4].isdigit():
        return None
    return YearDiscoveryResult(
        year=int(release_date[:4]),
        matched_title=item["name"],
        matched_artist=item["artists"][0]["name"],
    )
