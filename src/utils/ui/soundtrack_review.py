"""Soundtrack albums, asked about in one checkbox list each: guesses at the end
of Fill missing data -> Albums (nothing ticked: a name ending in "Music" is only
a hint) and Manage database -> "Resolve soundtrack album names" (one soundtrack
spelt several ways ticked; a lone title, maybe its official name, not)."""

import questionary

from utils.common.output import ok, review_header, say
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.database.soundtrack_albums import apply_soundtrack_variants, find_soundtrack_variants
from utils.ui.song_colors import song_tokens


def review_soundtrack_guesses(found: list) -> list:
    """Ask about these (song, album) pairs. Returns the pairs ticked; the
    caller saves their albums and commits."""
    if not found:
        return []
    review_header(f"{len(found)} song(s) whose artist may be the name of a game or film",
                  "Ticked: the album is saved.",
                  "Unticked: the song stays without an album.")
    choices = [questionary.Choice(title=song_tokens(song.title, song.artist.name, f"album: {album}"), value=i)
               for i, (song, album) in enumerate(found)]
    picked = questionary.checkbox("Save which albums?", choices=choices).ask()
    if not picked:
        say("No soundtrack album saved.")
        return []
    return [found[i] for i in picked]


def review_soundtrack_names() -> int:
    """Bring the ticked soundtracks to the one form of their name. Returns how
    many were changed."""
    session, _ = open_and_set_global_database_sessions()
    found = find_soundtrack_variants(session)
    if not found:
        say("Every soundtrack album already has its one name.")
        return 0
    review_header(f"{len(found)} soundtrack album name(s) to bring to the form \"Title OST\"",
                  "Ticked by default: one soundtrack spelt several ways.",
                  "Unticked: a single title, which may be the album's official name.")
    choices = [questionary.Choice(title=f"{' / '.join(v.titles)}  ->  {v.title}  ({len(v.songs)} song(s))",
                                  value=i, checked=len(v.titles) > 1)
               for i, v in enumerate(found)]
    picked = questionary.checkbox("Change which?", choices=choices).ask()
    if not picked:
        say("Nothing changed.")
        return 0
    for i in picked:
        apply_soundtrack_variants(session, found[i])
    submit_global_database_session()
    ok(f"Changed {len(picked)} soundtrack album name(s).")
    return len(picked)
