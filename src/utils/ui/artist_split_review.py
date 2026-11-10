"""The one batch review in front of every artist split (see
utils/database/artist_splitting.py): list every proposal, let the user tick
which ones to apply, then optionally remember the rest as single artists."""

from utils.ui.prompts import ask_yes_no, tick_items

from utils.database.artist_splitting import (
    SplitCandidate,
    add_to_split_ignore_list,
    apply_artist_split,
    find_split_candidates,
)
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.database.datatables import Artist
from utils.common.output import escape, review_header, show


def _describe(candidate: SplitCandidate) -> str:
    def part(name: str) -> str:
        return f"{name} [{'existing' if name in candidate.existing else 'new'}]"

    proposal = candidate.proposal
    text = " + ".join(part(n) for n in proposal.main)
    if proposal.feat:
        text += " feat. " + ", ".join(part(n) for n in proposal.feat)
    songs = f"{candidate.song_count} song{'s' if candidate.song_count != 1 else ''}"
    return f"{proposal.original}  ->  {text}  ({songs})"


def review_artist_splits(artists: list[Artist] | None = None) -> int:
    """Propose splitting joined artist names - all artists in the DB, or only
    `artists` (an import passes the ones it just created). Returns how many
    were split."""
    music_session, _ = open_and_set_global_database_sessions()
    candidates = find_split_candidates(music_session, artists)
    if not candidates:
        if artists is None:
            show("No joined artist names to split.")
        return 0

    review_header(f"REVIEW: {len(candidates)} artist name(s) look like several artists", "Pre-selected: a feat./x credit, or every part already exists as an artist.")
    selected = tick_items("Select the names to split (space toggles, enter confirms):", candidates, _describe,
                          checked=lambda c: c.preselected)
    if selected is None:
        show("Aborted, nothing split.")
        return 0

    created = {}
    for candidate in selected:
        parts = apply_artist_split(music_session, candidate, created)
        show(f"Split [green]{escape(candidate.proposal.original)}[/green] into {escape(', '.join(a.name for a in parts))}")
    submit_global_database_session()

    skipped = [c.proposal.original for c in candidates if c not in selected]
    if skipped and ask_yes_no(f"Remember the {len(skipped)} unselected name(s) as single artists, so they aren't proposed again?"):
        add_to_split_ignore_list(skipped)
        show(f"Remembered {len(skipped)} name(s).")

    show(f"Split {len(selected)} artist name(s).")
    return len(selected)
