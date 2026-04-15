import time
import musicbrainzngs
from utils.debug import slog

# Set up the user agent (required by MusicBrainz)
musicbrainzngs.set_useragent("MusicLibraryFetcher", "1.0", "your@email.com")


ALBUM_TITLE_BLACKLIST = {
    # Exact matches (lowercased)
    "now that's what i call music",
    "greatest hits",
    "best of",
    "the best of",
    "very best of",
    "the very best of",
    "essential",
    "the essential",
    "collection",
    "the collection",
    "gold",
    "platinum",
    "anthology",
    "retrospective",
    "definitive collection",
    "complete collection"
}

ALBUM_TITLE_BLACKLIST_SUBSTRINGS = {
    # Partial matches — if any of these appear in the title, skip it
    "greatest hits",
    "best of",
    "collection",
    "anthology",
    "retrospective",
    "compilation",
    "now that's what i call",
    "the essential",
    "pop party",
    "itunes",
    "remix",
    "germany",
    "festival",
    "United Palace Theatre",
    "1996-2011",
    "radio",
    "spotify",
    "paris",
    "session",
    "ultimate",
    "hits",
    "edition",
    "awards",
    "przebojów",
    "valentine's day",
    "exercises",
    "new orleans",
    "morrison",
    "2014",
    "women in music",
    "london, uk",
    "england",
    "collecion",
    "youtube",
    "essential",
    "live",
    "хит",
    "concert"
}


def is_blacklisted_album(title: str) -> bool:
    slog(title)
    lowered = title.lower().strip()
    if lowered in ALBUM_TITLE_BLACKLIST:
        slog("True")
        return True
    return any(sub in lowered for sub in ALBUM_TITLE_BLACKLIST_SUBSTRINGS)



def fetch_album_from_musicbrainz(artist: str, song: str, delay: float = 1.0) -> str | None:
    """Simple helper: fetch album for a single (artist, song) pair.

    Calls `fetch_albums_from_musicbrainz_batch` with a single-item list and returns
    the album title or None if not found or on error.
    """
    query = f'recording:"{song}" AND artist:"{artist}"'
    try:
        response = musicbrainzngs.search_recordings(query=query, limit=5)
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
            print(f"  ✓ Found album: {result}")
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
        if final:
            print(f"Deep found album: {final}")
        else:
            print(f"Deep search found no album")

        time.sleep(delay)
        return final

    except musicbrainzngs.WebServiceError as e:
        print(f"  ! MusicBrainz error for '{song}' by '{artist}': {e}")
        return None