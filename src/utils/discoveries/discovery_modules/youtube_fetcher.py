from datetime import datetime, timezone

import requests

from config.constants import (
    HTTP_LOOKUP_TIMEOUT,
    NO_VIDEO_SENTINEL,
    SPELLING_CHECK_THRESHOLD,
    YOUTUBE_MUSIC_MIN_INTERVAL,
    YOUTUBE_MUSIC_ORIGIN,
    YOUTUBE_MUSIC_SEARCH_URL,
    YOUTUBE_MUSIC_SONGS_FILTER,
)
from utils.common.debug import flog, slog
from utils.common.request_pacing import RequestPacer
from utils.common.text_utils import is_blacklisted_album, similarity
from utils.discoveries.discovery_result import DiscoveryResult, YearDiscoveryResult
from utils.youtube.manage_youtube_playlists import get_video_release_year, search_video_ytdlp

# Asks music.youtube.com's own JSON search, the one its web player calls,
# instead of the official YouTube Data API, so no app registration / API key
# is needed and no browser either. Trade-off: this endpoint and the shape of
# its answer are undocumented, so YouTube can change them without notice.
MODULE_NAME = "YouTube fetcher (Curl)"

_pacer = RequestPacer(YOUTUBE_MUSIC_MIN_INTERVAL)


def _result_rows(data):
    """Every result row in the search answer, wherever it is nested."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "musicResponsiveListItemRenderer":
                yield value
            else:
                yield from _result_rows(value)
    elif isinstance(data, list):
        for item in data:
            yield from _result_rows(item)


def _column_runs(row: dict, index: int) -> list[dict]:
    columns = row.get("flexColumns", [])
    if index >= len(columns):
        return []
    return columns[index].get("musicResponsiveListItemFlexColumnRenderer", {}).get("text", {}).get("runs", [])


def _page_type(run: dict) -> str | None:
    return (run.get("navigationEndpoint", {}).get("browseEndpoint", {})
            .get("browseEndpointContextSupportedConfigs", {})
            .get("browseEndpointContextMusicConfig", {}).get("pageType"))


def _find_matching_song(data: dict, artist: str, title: str):
    """Scan the filtered 'Songs' search results and return (matched_title,
    matched_artist, album) for the best-matching row, or None if nothing
    clears the similarity threshold for both title and artist."""
    best = None
    best_score = 0.0

    for row in _result_rows(data):
        row_title = "".join(run.get("text", "") for run in _column_runs(row, 0))
        if not row_title:
            continue

        details = _column_runs(row, 1)
        row_artists = [run["text"] for run in details if _page_type(run) == "MUSIC_PAGE_TYPE_ARTIST"]
        if not row_artists:
            continue

        albums = [run["text"] for run in details if _page_type(run) == "MUSIC_PAGE_TYPE_ALBUM"]
        if not albums:
            continue
        album = albums[0]

        # A collab is usually stored in the DB as one joined artist
        # ("A x B x C"), while YouTube Music lists each artist separately, so
        # also compare against the row's artists joined back together.
        artist_candidates = row_artists + [
            sep.join(row_artists) for sep in (" x ", ", ", " & ")
        ] if len(row_artists) > 1 else row_artists

        title_sim = similarity(title, row_title)
        artist_sim = max(similarity(artist, a) for a in artist_candidates)
        if title_sim < SPELLING_CHECK_THRESHOLD or artist_sim < SPELLING_CHECK_THRESHOLD:
            continue

        score = min(title_sim, artist_sim)
        if score > best_score:
            best_score = score
            best = (row_title, row_artists[0], album)

    return best


def get_album_name(artist: str, title: str) -> DiscoveryResult | None:
    query = f"{artist} {title}"
    slog(f"[YouTube] Searching for {query}", priority=1)
    _pacer.wait()
    try:
        response = requests.post(
            YOUTUBE_MUSIC_SEARCH_URL,
            params={"prettyPrint": "false"},
            headers={"Origin": YOUTUBE_MUSIC_ORIGIN},
            json={
                "context": {"client": {
                    "clientName": "WEB_REMIX",
                    # A version frozen here would age out; the web player's own is its build date
                    "clientVersion": f"1.{datetime.now(timezone.utc):%Y%m%d}.01.00",
                }},
                "query": query,
                "params": YOUTUBE_MUSIC_SONGS_FILTER,
            },
            timeout=HTTP_LOOKUP_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        flog(f"[YouTube] Search failed for {query}: {exc}")
        return None

    match = _find_matching_song(data, artist, title)
    if not match:
        flog("[YouTube] No matching song in search results")
        return None

    matched_title, matched_artist, album = match
    if is_blacklisted_album(album, artist):
        return None

    return DiscoveryResult(
        album=album,
        matched_title=matched_title,
        matched_artist=matched_artist,
    )


def get_release_year(artist: str, query: str, is_single: bool, songs=None) -> YearDiscoveryResult | None:
    """A release year from a YouTube video, found with the same DB ->
    YouTube matching as playlists (yt-dlp, no API quota).

    Unlike get_album_name() this doesn't search music.youtube.com.
    get_video_release_year() prefers YouTube Music's "Released on" date and
    otherwise takes the upload date, which is often not the release date (a
    live session, a reupload, one track standing in for a whole album), so
    the year is always flagged needs_review and fill_missing_years.py asks
    the user about it - which is why albums are looked up here too.

    `songs` (passed by discoveries_manager: the single, or the album's songs)
    lets a stored Song.youtube_video_id win over a fresh search - it may be a
    manually set video no search finds. A single marked NO_VIDEO_SENTINEL
    ("no video exists") is skipped; a stored link that no longer resolves
    falls back to searching.
    """
    stored_ids = [song.youtube_video_id for song in (songs or []) if song.youtube_video_id]
    if is_single and NO_VIDEO_SENTINEL in stored_ids:
        return None

    year = None
    for video_id in stored_ids:
        if video_id == NO_VIDEO_SENTINEL:
            continue
        year = get_video_release_year(video_id)
        if year:
            print(f"  Using the YouTube video stored for this song [{video_id}]")
            break
    if not year:
        video_id = search_video_ytdlp(artist, query)
        year = get_video_release_year(video_id)
    if not year:
        return None
    return YearDiscoveryResult(year=year, needs_review=True, source_url=f"https://youtu.be/{video_id}")
