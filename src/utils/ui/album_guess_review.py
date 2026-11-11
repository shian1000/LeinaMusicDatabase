"""End of Fill missing data -> Albums: the albums only an unsure source knew
(Google's "first album" of a recording, maybe a compilation: "Folk Hit Maraton
4"), in one checkbox list. Nothing is ticked: the caller saves only the ones
the owner picks. tick_album_pairs() is that list, shared by the other reviews
at the end of Albums (swapped names, soundtrack guesses)."""

from utils.common.output import review_header, say
from utils.ui.prompts import tick_items
from utils.ui.song_colors import song_tokens


def tick_album_pairs(found: list, notes: list[str], question: str, nothing: str,
                     album=lambda pair: pair[1].album) -> list:
    """Ask about these (song, album) pairs, nothing ticked, under the banner
    `notes`. Returns the pairs ticked (`nothing` said when none)."""
    if not found:
        return []
    review_header(*notes)
    picked = tick_items(question, found,
                        lambda pair: song_tokens(pair[0].title, pair[0].artist.name, f"album: {album(pair)}"))
    if not picked:
        say(nothing)
    return picked or []


def review_album_guesses(found: list) -> list:
    """Ask about these (song, AlbumMatch) pairs. Returns the pairs ticked; the
    caller saves their albums and commits."""
    return tick_album_pairs(found, [f"{len(found)} song(s) with an album only Google knew",
                                    "Google names the first release it knows a recording from, which may be a compilation.",
                                    "Ticked: the album is saved.",
                                    "Unticked: the song stays without an album."],
                            "Save which albums?", "None of these albums saved.")
