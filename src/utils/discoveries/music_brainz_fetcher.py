import time
import musicbrainzngs

# Set up the user agent (required by MusicBrainz)
musicbrainzngs.set_useragent("MusicLibraryFetcher", "1.0", "your@email.com")


def fetch_albums_from_musicbrainz(
    songs: list[tuple[str, str]],
    delay: float = 1.0,
) -> dict[tuple[str, str], str | None]:
    """
    Given a list of (artist_name, song_title) tuples,
    queries MusicBrainz for each song's album name.

    Args:
        songs:  List of (artist, title) tuples — as returned by get_song_from_artist_and_name().
        delay:  Seconds to wait between requests (MusicBrainz rate limit is 1 req/sec).

    Returns:
        A dict mapping (artist, title) -> album name string, or None if not found.
    """
    results = {}

    for artist, title in songs:
        query = f'recording:"{title}" AND artist:"{artist}"'
        print(f"Searching: {artist} — {title}")

        try:
            response = musicbrainzngs.search_recordings(
                query=query,
                limit=5
            )
            recordings = response.get("recording-list", [])

            album = None
            for recording in recordings:
                releases = recording.get("release-list", [])
                for release in releases:
                    # Prefer official studio albums over singles/compilations
                    release_group = release.get("release-group", {})
                    primary_type = release_group.get("primary-type", "")
                    if primary_type == "Album":
                        album = release.get("title")
                        break
                    elif album is None:
                        # Fall back to whatever is available
                        album = release.get("title")
                if album:
                    break

            if album:
                print(f"  ✓ Found album: {album}")
            else:
                print(f"  ✗ No album found")

            results[(artist, title)] = album

        except musicbrainzngs.WebServiceError as e:
            print(f"  ! MusicBrainz error for '{title}' by '{artist}': {e}")
            results[(artist, title)] = None

        time.sleep(delay)  # Respect the rate limit

    return results