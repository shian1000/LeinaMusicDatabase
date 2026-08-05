import time
import musicbrainzngs
from utils.common.debug import slog
from utils.common.text_utils import is_blacklisted_album
from utils.discoveries.discovery_result import DiscoveryResult
import re
import requests
from difflib import SequenceMatcher
from utils.common.text_utils import check_spelling

# Set up the user agent (required by MusicBrainz)
musicbrainzngs.set_useragent("MusicLibraryFetcher", "1.0", "your@email.com")

HEADERS = {"User-Agent": "YourScriptName/1.0 (your@email.com)"}
MODULE_NAME = "Musicbrainz fetcher"

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