"""The one batch review in front of every artist split (see
utils/database/artist_splitting.py): list every proposal, let the user tick
which ones to apply, then optionally remember the rest as single artists."""

import questionary
from rich import print
from rich.markup import escape

from utils.database.artist_splitting import (
    SplitCandidate,
    add_to_split_ignore_list,
    apply_artist_split,
    find_split_candidates,
)
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.database.datatables import Artist


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
            print("No joined artist names to split.")
        return 0

    print("\n" + "=" * 70)
    print(f"REVIEW: {len(candidates)} artist name(s) look like several artists")
    print("Pre-selected: a feat./x credit, or every part already exists as an artist.")
    print("=" * 70)
    choices = [
        questionary.Choice(title=_describe(c), value=i, checked=c.preselected)
        for i, c in enumerate(candidates)
    ]
    selected = questionary.checkbox(
        "Select the names to split (space toggles, enter confirms):", choices=choices
    ).ask()
    if selected is None:
        print("Aborted, nothing split.")
        return 0

    created = {}
    for i in selected:
        candidate = candidates[i]
        parts = apply_artist_split(music_session, candidate, created)
        print(f"Split [green]{escape(candidate.proposal.original)}[/green] into {escape(', '.join(a.name for a in parts))}")
    submit_global_database_session()

    skipped = [candidates[i].proposal.original for i in range(len(candidates)) if i not in set(selected)]
    if skipped and questionary.confirm(
        f"Remember the {len(skipped)} unselected name(s) as single artists, so they aren't proposed again?",
        default=False,
    ).ask():
        add_to_split_ignore_list(skipped)
        print(f"Remembered {len(skipped)} name(s).")

    print(f"Split {len(selected)} artist name(s).")
    return len(selected)
