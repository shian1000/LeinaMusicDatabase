"""End of Fill missing data -> Albums: the songs whose album was found only with
their artist and title swapped, in one checkbox list. Nothing is ticked: a
service knowing the song that way round is a strong hint, not proof ("Rain -
Madonna" may be a song of a band called Rain)."""

from utils.common.output import ok
from utils.database.artist_identity import swap_song_names
from utils.database.database_sessions import require_global_database_sessions
from utils.ui.album_guess_review import tick_album_pairs


def review_swapped_albums(found: list) -> list:
    """Ask about these (song, AlbumMatch) pairs and swap the names of the
    ticked songs. Returns the pairs swapped; the caller saves their albums and
    commits."""
    swapped = tick_album_pairs(found, [f"{len(found)} song(s) with an album found only with the artist and title swapped",
                                       "Ticked: the artist and title are swapped and the album is saved.",
                                       "Unticked: the song stays as it is, without an album."],
                               "Swap and save which?", "Nothing swapped.")
    if not swapped:
        return []
    session, _ = require_global_database_sessions()
    for song, _match in swapped:
        swap_song_names(session, song)
    ok(f"Swapped the artist and title of {len(swapped)} song(s).")
    return swapped
