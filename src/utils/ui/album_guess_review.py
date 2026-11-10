"""End of Fill missing data -> Albums: the albums only an unsure source knew
(Google's "first album" of a recording, maybe a compilation: "Folk Hit Maraton
4"), in one checkbox list. Nothing is ticked: the caller saves only the ones
the owner picks."""

from utils.common.output import review_header, say
from utils.ui.prompts import tick_items
from utils.ui.song_colors import song_tokens


def review_album_guesses(found: list) -> list:
    """Ask about these (song, AlbumMatch) pairs. Returns the pairs ticked; the
    caller saves their albums and commits."""
    if not found:
        return []
    review_header(f"{len(found)} song(s) with an album only Google knew",
                  "Google names the first release it knows a recording from, which may be a compilation.",
                  "Ticked: the album is saved.",
                  "Unticked: the song stays without an album.")
    picked = tick_items("Save which albums?", found,
                        lambda pair: song_tokens(pair[0].title, pair[0].artist.name, f"album: {pair[1].album}"))
    if not picked:
        say("None of these albums saved.")
    return picked or []
