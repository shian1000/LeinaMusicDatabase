import requests

from config.constants import (
    GENRE_LEVEL_ARTIST,
    HTTP_LOOKUP_TIMEOUT,
    LASTFM_API_MIN_INTERVAL,
    LASTFM_API_URL,
    LASTFM_GENRE_MIN_COUNT,
)
from settings import settings
from utils.common.debug import flog
from utils.common.output import warn
from utils.common.request_pacing import RequestPacer
from utils.discoveries.discovery_result import GenreDiscoveryResult

# Last.fm's API with a free key (LASTFM_API_KEY in .env), genres only: the
# tags its listeners gave the track, else the artist. Off by default
# (constants.DISCOVERY_MODULES_NEEDING_API_KEY). Tags are anyone's words, so
# discover_genre() keeps only names on MusicBrainz's genre list, and every
# answer is reviewed (GENRE_NEVER_SURE).
MODULE_NAME = "Last.fm fetcher (Curl + API)"
GENRE_NEVER_SURE = True

# Last.fm's answer for a track or artist it doesn't know
_NOT_FOUND = 6

_pacer = RequestPacer(LASTFM_API_MIN_INTERVAL)
_warned_about_key = False


def _top_tags(method: str, **params) -> dict | None:
    """The `toptags` part of the answer, None when Last.fm doesn't know the
    track / artist. Raises when it can't be asked (no key, no network, a
    rate limit), so that's never remembered as a miss."""
    global _warned_about_key
    if not settings.lastfm_api_key:
        if not _warned_about_key:
            _warned_about_key = True
            warn("Last.fm lookups skipped: LASTFM_API_KEY isn't set in .env.")
        raise ConnectionError("LASTFM_API_KEY isn't set")
    _pacer.wait()
    response = requests.get(
        LASTFM_API_URL,
        params={"method": method, "api_key": settings.lastfm_api_key, "autocorrect": 1, "format": "json", **params},
        timeout=HTTP_LOOKUP_TIMEOUT,
    )
    try:
        answer = response.json()
    except ValueError:
        response.raise_for_status()
        raise
    if answer.get("error") == _NOT_FOUND:
        return None
    if "error" in answer:
        raise ConnectionError(f"Last.fm error {answer['error']}: {answer.get('message')}")
    response.raise_for_status()
    return answer.get("toptags") or {}


def _genres(toptags: dict) -> list[str]:
    tags = toptags.get("tag") or []
    if isinstance(tags, dict):  # a single tag comes unlisted
        tags = [tags]
    return [tag["name"] for tag in tags if tag.get("name") and int(tag.get("count") or 0) >= LASTFM_GENRE_MIN_COUNT]


def get_genre(artist: str, title: str, songs=None) -> GenreDiscoveryResult | None:
    """The track's most used tags, else the artist's (level artist). The
    names Last.fm corrected the query to are the matched ones."""
    track = _top_tags("track.getTopTags", artist=artist, track=title)
    if track is not None and (genres := _genres(track)):
        names = track.get("@attr") or {}
        return GenreDiscoveryResult(genres, matched_title=names.get("track") or title,
                                    matched_artist=names.get("artist"), never_sure=True)
    artist_tags = _top_tags("artist.getTopTags", artist=artist)
    if artist_tags is not None and (genres := _genres(artist_tags)):
        return GenreDiscoveryResult(genres, level=GENRE_LEVEL_ARTIST,
                                    matched_artist=(artist_tags.get("@attr") or {}).get("artist"), never_sure=True)
    flog(f"[Last.fm] no tags for '{artist} - {title}' nor its artist")
    return None
