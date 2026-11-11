"""Soundtrack albums, asked about in one checkbox list each: guesses at the end
of Fill missing data -> Albums (nothing ticked: a name ending in "Music" is only
a hint) and Manage database -> "Resolve soundtrack album names" (one soundtrack
spelt several ways ticked; a lone title, maybe its official name, not)."""

from utils.common.output import ok, review_header, say
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.database.soundtrack_albums import apply_soundtrack_variants, find_soundtrack_variants
from utils.ui.album_guess_review import tick_album_pairs
from utils.ui.prompts import tick_items


def review_soundtrack_guesses(found: list) -> list:
    """Ask about these (song, album) pairs. Returns the pairs ticked; the
    caller saves their albums and commits."""
    return tick_album_pairs(found, [f"{len(found)} song(s) whose artist may be the name of a game or film",
                                    "Ticked: the album is saved.",
                                    "Unticked: the song stays without an album."],
                            "Save which albums?", "No soundtrack album saved.", album=lambda pair: pair[1])


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
    picked = tick_items("Change which?", found,
                        lambda v: f"{' / '.join(v.titles)}  ->  {v.title}  ({len(v.songs)} song(s))",
                        checked=lambda v: len(v.titles) > 1)
    if not picked:
        say("Nothing changed.")
        return 0
    for variants in picked:
        apply_soundtrack_variants(session, variants)
    submit_global_database_session()
    ok(f"Changed {len(picked)} soundtrack album name(s).")
    return len(picked)
