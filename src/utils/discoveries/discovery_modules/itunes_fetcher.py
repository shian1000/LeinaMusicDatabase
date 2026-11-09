import requests

from utils.common.debug import flog, slog
from utils.common.request_pacing import RequestPacer
from utils.common.text_utils import is_blacklisted_album, is_various_artists_compilation, similarity
from utils.discoveries.discovery_result import DiscoveryResult
from config.constants import (
    HTTP_LOOKUP_TIMEOUT,
    ITUNES_SEARCH_COUNTRY,
    ITUNES_SEARCH_LIMIT,
    ITUNES_SEARCH_MIN_INTERVAL,
    ITUNES_SEARCH_URL,
    SINGLES_ALBUM,
    SPELLING_CHECK_THRESHOLD,
)

# Apple's public iTunes Search API: JSON, no key, no browser.
MODULE_NAME = "iTunes fetcher (Curl)"

# Apple appends this to the name of a release that only ever came out as a
# single (no parent album) -- e.g. "Say So (feat. Nicki Minaj) - Single".
SINGLE_TITLE_SUFFIX = " - single"

_pacer = RequestPacer(ITUNES_SEARCH_MIN_INTERVAL)


def _find_track(results: list[dict], artist: str, song: str):
    """Returns (album, matched_title, matched_artist) of the result that fits
    best, or None. An equally good title on a real album beats a single, and
    the plain title beats its "(Live ...)" versions."""
    song_query = song.lower()
    best = None
    best_rank = None

    for result in results:
        album = result.get("collectionName")
        track_title = result.get("trackName")
        track_artist = result.get("artistName")
        if result.get("kind") != "song" or not (album and track_title and track_artist):
            continue
        if artist.lower() not in track_artist.lower():
            slog(f"Artist mismatch: expected '{artist}', found '{track_artist}'")
            continue

        # Apple bakes "(feat. X)" credits into the title, so compare the part
        # before the parenthetical -- the same convention
        # discover_album_name() uses to clean the query itself.
        track_title_cln = track_title.split("(")[0].strip()
        if similarity(song_query, track_title_cln.lower()) <= SPELLING_CHECK_THRESHOLD:
            continue

        # A single-only release reports no real album -- the same
        # SINGLES_ALBUM sentinel wikipedia_fetcher uses for this case.
        if album.lower().endswith(SINGLE_TITLE_SUFFIX):
            album = SINGLES_ALBUM
        if is_blacklisted_album(album, artist):
            continue
        if is_various_artists_compilation(album, result.get("collectionArtistName")):
            flog(f"[iTunes] '{album}' is a various-artists compilation, skipping it")
            continue

        rank = (similarity(song_query, track_title.lower()), album != SINGLES_ALBUM)
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best = (album, track_title_cln, track_artist)

    return best


def get_album_name(artist: str, song: str) -> DiscoveryResult | None:
    _pacer.wait()
    try:
        response = requests.get(
            ITUNES_SEARCH_URL,
            params={
                "term": f"{artist} {song}",
                "entity": "song",
                "country": ITUNES_SEARCH_COUNTRY,
                "limit": ITUNES_SEARCH_LIMIT,
            },
            timeout=HTTP_LOOKUP_TIMEOUT,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
    except requests.RequestException as exc:
        flog(f"[iTunes] Search failed for {artist} - {song}: {exc}")
        return None

    match = _find_track(results, artist, song)
    if not match:
        flog("[iTunes] No matching song in search results")
        return None

    album_name, matched_title, matched_artist = match
    slog(f"Matched: {artist} - {matched_title} ({album_name})")
    return DiscoveryResult(album=album_name, matched_title=matched_title, matched_artist=matched_artist)
