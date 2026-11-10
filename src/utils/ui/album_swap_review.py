"""End of Fill missing data -> Albums: the songs whose album was found only with
their artist and title swapped, in one checkbox list. Nothing is ticked: a
service knowing the song that way round is a strong hint, not proof ("Rain -
Madonna" may be a song of a band called Rain)."""

from utils.common.output import ok, review_header, say
from utils.database.artist_identity import swap_song_names
from utils.database.database_sessions import require_global_database_sessions
from utils.ui.prompts import tick_items
from utils.ui.song_colors import song_tokens


def review_swapped_albums(found: list) -> list:
    """Ask about these (song, AlbumMatch) pairs and swap the names of the
    ticked songs. Returns the pairs swapped; the caller saves their albums and
    commits."""
    if not found:
        return []
    review_header(f"{len(found)} song(s) with an album found only with the artist and title swapped",
                  "Ticked: the artist and title are swapped and the album is saved.",
                  "Unticked: the song stays as it is, without an album.")
    swapped = tick_items("Swap and save which?", found,
                         lambda pair: song_tokens(pair[0].title, pair[0].artist.name, f"album: {pair[1].album}"))
    if not swapped:
        say("Nothing swapped.")
        return []
    session, _ = require_global_database_sessions()
    for song, _match in swapped:
        swap_song_names(session, song)
    ok(f"Swapped the artist and title of {len(swapped)} song(s).")
    return swapped
