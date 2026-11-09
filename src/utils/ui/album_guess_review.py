"""End of Fill missing data -> Albums: the albums only an unsure source knew
(Google's "first album" of a recording, maybe a compilation: "Folk Hit Maraton
4"), in one checkbox list. Nothing is ticked: the caller saves only the ones
the owner picks."""

import questionary

from utils.common.output import review_header, say
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
    choices = [questionary.Choice(title=song_tokens(song.title, song.artist.name, f"album: {match.album}"), value=i)
               for i, (song, match) in enumerate(found)]
    picked = questionary.checkbox("Save which albums?", choices=choices).ask()
    if not picked:
        say("None of these albums saved.")
        return []
    return [found[i] for i in picked]
