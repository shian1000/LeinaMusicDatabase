"""Manage database -> Resolve duplicates -> "Resolve duplicated additional
names of artists": first the artists' transliterations are worked out again
(transliterations.refresh_artist_transliterations()), then every synonym
repeating the artist's name, Latin form, a transliteration or an earlier
synonym is found (duplicate_synonyms()). Exact repeats go without asking and
are listed; the rest - equal only after normalize() - in one checkbox list,
none ticked at first: another case or accent may be the only record of the
artist's own spelling ("ROSALÍA", "AiNA THE END")."""

from utils.common.output import escape, review_header, show
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.database.transliterations import duplicate_synonyms, refresh_artist_transliterations, remove_synonyms
from utils.ui.prompts import tick_items


def _describe(duplicate) -> str:
    return f'{duplicate.artist.name}:  "{duplicate.synonym}"  repeats {duplicate.of} "{duplicate.other}"'


def review_duplicate_synonyms() -> int:
    """Returns how many synonyms were removed."""
    music_session, _ = open_and_set_global_database_sessions()
    added, changed, removed = refresh_artist_transliterations(music_session)
    submit_global_database_session()
    if added or changed or removed:
        show(f"Transliterations of artists' names: {added} added, {changed} changed, {removed} removed.")

    found = duplicate_synonyms(music_session)
    exact = [d for d in found if d.exact]
    alike = [d for d in found if not d.exact]
    if not found:
        show("No artist has an additional name repeating another of its names.")
        return 0

    for duplicate in exact:
        show(f"Removed [cyan]{escape(duplicate.artist.name)}[/cyan]'s \"{escape(duplicate.synonym)}\""
             f" (the same as {escape(duplicate.of)})")
    gone = remove_synonyms(exact)
    picked = []
    if alike:
        review_header(f"REVIEW: {len(alike)} additional name(s) alike another name of the artist",
                      "Equal to it once case, accents and punctuation are left out - search finds the "
                      "artist either way. None ticked: a spelling may be the artist's own (\"ROSALÍA\").")
        picked = tick_items("Tick the additional names to remove (space toggles, enter confirms):", alike,
                            _describe)
        if picked is None:
            show("Kept the alike ones.")
            picked = []
        gone += remove_synonyms(picked)
    submit_global_database_session()
    show(f"Removed {gone} additional name(s) ({len(exact)} exact repeat(s), {len(picked)} ticked).")
    return gone
