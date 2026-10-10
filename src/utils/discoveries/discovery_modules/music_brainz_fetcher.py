import socket
import time
from contextlib import contextmanager

import musicbrainzngs
from utils.common.debug import flog, slog
from utils.common.text_utils import is_blacklisted_album, similarity, scaled_similarity_threshold
from utils.discoveries.discovery_result import DiscoveryResult, YearDiscoveryResult
import re
import requests
from difflib import SequenceMatcher
from utils.common.text_utils import check_spelling
from config.constants import MUSICBRAINZ_API_USER_AGENT, SPELLING_CHECK_THRESHOLD

# Set up the user agent (required by MusicBrainz). Contact points at the project
# repo; see MUSICBRAINZ_API_USER_AGENT in config/constants.py.
musicbrainzngs.set_useragent("MusicDatabase", "1.0", "https://github.com/shian1000/MusicDatabase")

HEADERS = {"User-Agent": MUSICBRAINZ_API_USER_AGENT}
MODULE_NAME = "Musicbrainz fetcher"

# musicbrainzngs issues its HTTP requests via urllib with no per-call timeout,
# so a stalled connection (server accepts then never answers) blocks forever
# instead of raising. It retries internally on socket.timeout, so bounding the
# socket here turns a permanent hang into a handled NetworkError.
MUSICBRAINZ_SEARCH_TIMEOUT = 20


@contextmanager
def _bounded_socket_timeout(seconds: float):
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(seconds)
    try:
        yield
    finally:
        socket.setdefaulttimeout(previous)

def _recording_title_artist(recording) -> tuple[str, str]:
    """Best-effort extraction of what MusicBrainz recording a release came from."""
    if not recording:
        return "", ""
    title = recording.get("title", "")
    try:
        artist = recording["artist-credit"][0]["artist"]["name"]
    except (KeyError, IndexError, TypeError):
        artist = ""
    return title, artist

def get_album_name(artist: str, song: str, delay: float = 1.0, spell_check = False) -> str | None:
    """Simple helper: fetch album for a single (artist, song) pair.

    Calls `fetch_albums_from_musicbrainz_batch` with a single-item list and returns
    the album title or None if not found or on error.
    """

    slog(f"{artist} - {song}", priority=1)
    if spell_check:
        spell_check_result = check_spelling(artist, song)
        slog(spell_check_result)
        if(spell_check_result["found"]):
            if artist != spell_check_result["corrected_artist"] or song != spell_check_result["corrected_title"]:
                artist = spell_check_result["corrected_artist"]
                song = spell_check_result["corrected_title"]
    slog(f"{artist} - {song}", priority=1)
    
    query = f'recording:"{song}" AND artist:"{artist}"'
    slog(query, priority=1)
    try:
        with _bounded_socket_timeout(MUSICBRAINZ_SEARCH_TIMEOUT):
            response = musicbrainzngs.search_recordings(query=query, limit=5)
        recordings = response.get("recording-list", [])

        album = None
        album_recording = None
        fallback = None
        fallback_recording = None

        for recording in recordings:
            releases = recording.get("release-list", [])
            for release in releases:
                release_title = release.get("title", "")
                release_group = release.get("release-group", {})
                primary_type = release_group.get("primary-type", "")
                secondary_types = release_group.get("secondary-type-list", [])

                # Skip if the release group is explicitly a compilation/live/etc.
                if any(t in ("Compilation", "Live", "Remix", "DJ-mix") for t in secondary_types):
                    continue

                slog(release_title)
                if is_blacklisted_album(release_title):
                    continue

                if primary_type == "Album":
                    album = release_title
                    album_recording = recording
                    break
                elif fallback is None:
                    fallback = release_title
                    fallback_recording = recording

            if album:
                break

        result = album or fallback
        result_recording = album_recording or fallback_recording
        slog(result)

        if result and not is_blacklisted_album(result):
            if not spell_check:
                return get_album_name(artist, song, spell_check=True)
            time.sleep(delay)
            matched_title, matched_artist = _recording_title_artist(result_recording)
            return DiscoveryResult(album=result, matched_title=matched_title, matched_artist=matched_artist)


        with _bounded_socket_timeout(MUSICBRAINZ_SEARCH_TIMEOUT):
            response = musicbrainzngs.search_recordings(query=query, limit=25)
        recordings = response.get("recording-list", [])

        album = None
        album_recording = None
        fallback = None
        fallback_recording = None

        for recording in recordings:
            releases = recording.get("release-list", [])
            for release in releases:
                release_title = release.get("title", "")
                release_group = release.get("release-group", {})
                primary_type = release_group.get("primary-type", "")
                secondary_types = release_group.get("secondary-type-list", [])

                if any(t in ("Compilation", "Live", "Remix", "DJ-mix") for t in secondary_types):
                    continue

                # slog(release_title)
                if is_blacklisted_album(release_title):
                    continue

                if primary_type == "Album":
                    album = release_title
                    album_recording = recording
                    break
                elif fallback is None:
                    fallback = release_title
                    fallback_recording = recording

            if album:
                break

        final = album or fallback
        final_recording = album_recording or fallback_recording

        time.sleep(delay)
        if not final:
            return None
        matched_title, matched_artist = _recording_title_artist(final_recording)
        return DiscoveryResult(album=final, matched_title=matched_title, matched_artist=matched_artist)

    except musicbrainzngs.WebServiceError as e:
        print(f"MusicBrainz error for '{song}' by '{artist}': {e}")
        return None


def _extract_year(date_str: str | None) -> int | None:
    """MusicBrainz dates come as 'YYYY', 'YYYY-MM', or 'YYYY-MM-DD' -- pull
    out just the leading year."""
    if not date_str:
        return None
    match = re.match(r"(\d{4})", date_str)
    return int(match.group(1)) if match else None


def get_release_year(artist: str, query: str, is_single: bool, delay: float = 1.0) -> YearDiscoveryResult | None:
    """Look up a release year.

    is_single=False: query is an album title - searched as a release group,
    since release groups (not recordings) are what carries an album's
    first-release-date.
    is_single=True: query is a song title - searched as a recording (same
    shape as get_album_name()), reading the date off the chosen release.
    """
    try:
        if is_single:
            query_str = f'recording:"{query}" AND artist:"{artist}"'
            with _bounded_socket_timeout(MUSICBRAINZ_SEARCH_TIMEOUT):
                response = musicbrainzngs.search_recordings(query=query_str, limit=10)
            recordings = response.get("recording-list", [])

            for recording in recordings:
                for release in recording.get("release-list", []):
                    release_group = release.get("release-group", {})
                    secondary_types = release_group.get("secondary-type-list", [])
                    if any(t in ("Compilation", "Live", "Remix", "DJ-mix") for t in secondary_types):
                        continue
                    if is_blacklisted_album(release.get("title", "")):
                        continue
                    year = _extract_year(release.get("date") or release_group.get("first-release-date"))
                    if year:
                        time.sleep(delay)
                        matched_title, matched_artist = _recording_title_artist(recording)
                        return YearDiscoveryResult(year=year, matched_title=matched_title, matched_artist=matched_artist)
            return None

        query_str = f'releasegroup:"{query}" AND artist:"{artist}"'
        with _bounded_socket_timeout(MUSICBRAINZ_SEARCH_TIMEOUT):
            response = musicbrainzngs.search_release_groups(query=query_str, limit=10)
        release_groups = response.get("release-group-list", [])

        for release_group in release_groups:
            title = release_group.get("title", "")
            # Skip anything that isn't the album we already have stored - a
            # compilation ranked first would otherwise be returned, rejected by
            # the manager's title check, and hide the real album further down.
            # No album blacklist here: the name is already in the DB, so a word
            # like "pop" in it ("SODA POP FANCLUB 4") says nothing.
            if similarity(query, title) < scaled_similarity_threshold(query, title, SPELLING_CHECK_THRESHOLD):
                continue
            year = _extract_year(release_group.get("first-release-date"))
            if not year:
                continue
            time.sleep(delay)
            try:
                matched_artist = release_group["artist-credit"][0]["artist"]["name"]
            except (KeyError, IndexError, TypeError):
                matched_artist = ""
            return YearDiscoveryResult(year=year, matched_title=title, matched_artist=matched_artist)
        return None

    except musicbrainzngs.WebServiceError as e:
        print(f"MusicBrainz error for release year of '{query}' by '{artist}': {e}")
        flog(f"[MusicBrainz] error for release year of '{artist} - {query}': {e}")
        return None