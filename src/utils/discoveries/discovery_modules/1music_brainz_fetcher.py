import time
import musicbrainzngs
from utils.debug import slog
from utils.database.datatables import is_blacklisted_album
import re

# Set up the user agent (required by MusicBrainz)
musicbrainzngs.set_useragent("MusicLibraryFetcher", "1.0", "your@email.com")

def get_album_name(artist: str, song: str, delay: float = 1.0) -> str | None:
    """Simple helper: fetch album for a single (artist, song) pair.

    Calls `fetch_albums_from_musicbrainz_batch` with a single-item list and returns
    the album title or None if not found or on error.
    """
    query = f'recording:"{song}" AND artist:"{artist}"'
    slog(query)
    try:
        response = musicbrainzngs.search_recordings(query=query, limit=5)
        slog(response)
        recordings = response.get("recording-list", [])
        slog(recordings)

        album = None
        fallback = None

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

                if is_blacklisted_album(release_title):
                    continue

                if primary_type == "Album":
                    album = release_title
                    break
                elif fallback is None:
                    fallback = release_title

            if album:
                break

        result = album or fallback

        if result and not is_blacklisted_album(result):
            time.sleep(delay)
            return result


        response = musicbrainzngs.search_recordings(query=query, limit=25)
        recordings = response.get("recording-list", [])

        album = None
        fallback = None

        for recording in recordings:
            releases = recording.get("release-list", [])
            for release in releases:
                release_title = release.get("title", "")
                release_group = release.get("release-group", {})
                primary_type = release_group.get("primary-type", "")
                secondary_types = release_group.get("secondary-type-list", [])

                if any(t in ("Compilation", "Live", "Remix", "DJ-mix") for t in secondary_types):
                    continue
                if is_blacklisted_album(release_title):
                    continue

                if primary_type == "Album":
                    album = release_title
                    break
                elif fallback is None:
                    fallback = release_title

            if album:
                break

        final = album or fallback

        time.sleep(delay)
        return final

    except musicbrainzngs.WebServiceError as e:
        print(f"MusicBrainz error for '{song}' by '{artist}': {e}")
        return None