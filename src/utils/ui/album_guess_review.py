"""End of Fill missing data -> Albums: the albums only an unsure source knew
(Google's "first album" of a recording, maybe a compilation: "Folk Hit Maraton
4"), in one checkbox list. Nothing is ticked: the caller saves only the ones
the owner picks. tick_album_pairs() is that list, shared by the other reviews
at the end of Albums (swapped names, soundtrack guesses, live versions'
albums - review_version_albums()). None of them has a rule: unattended,
nothing of them is saved."""

from utils.common.output import review_header, say
from utils.ui.prompts import tick_items
from utils.ui.song_colors import song_tokens


def tick_album_pairs(found: list, notes: list[str], question: str, nothing: str,
                     album=lambda pair: pair[1].album, swapped: bool = False) -> list:
    """Ask about these (song, album) pairs, nothing ticked, under the banner
    `notes`. Each reads "Artist - Title", or with `swapped` as it would after
    swapping the two (the swap review shows the names it proposes). Returns
    the pairs ticked (`nothing` said when none)."""
    if not found:
        return []
    review_header(*notes)

    def label(pair):
        names = (pair[0].title, pair[0].artist.name) if swapped else (pair[0].artist.name, pair[0].title)
        return song_tokens(*names, f"album: {album(pair)}")

    picked = tick_items(question, found, label)
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


def review_version_albums(found: list) -> list:
    """Ask about these (song, AlbumMatch) pairs: live versions whose album
    found isn't a live release (version_names.lookup_fits_version()) - the
    lookup searches by artist and title and finds the studio original's
    ("The Rolling Stones - The Last Time", live, got the 1965 single). Nothing
    ticked. Returns the pairs ticked; the caller saves their albums (with the
    release's year) and commits."""
    def album(pair):
        match = pair[1]
        return f"{match.album} ({match.year})" if match.year else match.album

    return tick_album_pairs(found, [f"{len(found)} version(s) with an album that isn't a release of that version",
                                    "Albums and years are looked up by artist and title, which finds the original's "
                                    "release - a live take gets the studio album.",
                                    "Ticked: the album (and its year) is saved.",
                                    "Unticked: the song stays without an album."],
                            "Save which albums?", "None of these albums saved.", album=album)
