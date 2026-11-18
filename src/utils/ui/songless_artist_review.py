"""Manage database -> "Remove artists without songs": one checkbox list of the
artist rows nothing uses (artist_identity.find_songless_artists()), then one
question about remembering the unticked ones as kept. Ticked at first: the
ones simply deleted; one that looks like an artist with songs is offered
twice, unticked: merged into it (its name kept as a synonym) - check that it is
the same artist - or deleted, for a misspelt leftover ("egenda" of "Legenda")
whose name isn't worth keeping. Both ticked: deleted."""

from config.constants import SONGLESS_ARTIST_IGNORE_FILE
from utils.common.output import escape, review_header, show
from utils.common.text_utils import similarity
from utils.database.artist_identity import find_songless_artists, remember_ids, remove_songless_artist
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.ui.prompts import ask_yes_no, tick_items


def _choices(found: list) -> list[tuple]:
    """(artist, heir, merge) per row: a look-alike's merge, then its delete."""
    rows = []
    for artist, heir in found:
        if heir is not None:
            rows.append((artist, heir, True))
        rows.append((artist, heir, False))
    return rows


def _describe(row) -> str:
    artist, heir, merge = row
    albums = f", {len(artist.albums)} empty album(s)" if artist.albums else ""
    if merge:
        action = f'merge into "{heir.name}" (name kept as its synonym)'
    elif heir is not None:
        action = f'delete (a misspelling of "{heir.name}": the name not kept)'
    else:
        action = "delete"
    return f"{artist.name}  ->  {action}{albums}"


def _facts(row) -> dict:
    artist, heir, merge = row
    return {"merge": merge, "look_alike": heir is not None,
            "name_similarity": round(similarity(artist.name, heir.name), 3) if heir else None,
            "empty_albums": len(artist.albums)}


def _picked_actions(picked: list) -> list[tuple]:
    """(artist, heir to merge into or None) per ticked artist, in order - a
    look-alike ticked both ways is deleted."""
    deleted = {artist.id for artist, _, merge in picked if not merge}
    actions, seen = [], set()
    for artist, heir, merge in picked:
        if artist.id not in seen:
            seen.add(artist.id)
            actions.append((artist, None if artist.id in deleted else heir))
    return actions


def review_songless_artists() -> int:
    """Returns how many artists were removed."""
    music_session, _ = open_and_set_global_database_sessions()
    found = find_songless_artists(music_session)
    if not found:
        show("Every artist has a song or is covered by one.")
        return 0

    review_header(f"REVIEW: {len(found)} artist(s) without songs",
                  "No song credits them, covers them or is on their albums - left over from "
                  "deleted songs and songs credited to another artist.",
                  "Unticked at first: an artist like one with songs, merged into it (check they are one "
                  "artist) or deleted as a misspelling (its name not kept).")
    picked = tick_items("Tick the artists to remove (space toggles, enter confirms):", _choices(found), _describe,
                        checked=lambda row: row[1] is None, rule="songless_artists", facts=_facts)
    if picked is None:
        show("Aborted, nothing removed.")
        return 0

    picked = _picked_actions(picked)
    for artist, heir in picked:
        show(f"{'Merged' if heir else 'Deleted'} [cyan]{escape(artist.name)}[/cyan]"
             + (f" into [cyan]{escape(heir.name)}[/cyan]" if heir else ""))
        remove_songless_artist(music_session, artist, heir)
    submit_global_database_session()

    removed = {artist.id for artist, _ in picked}
    kept = [artist.id for artist, _ in found if artist.id not in removed]
    if kept and ask_yes_no(f"Remember the {len(kept)} unticked artist(s) as kept, so they aren't asked about again?"):
        remember_ids(SONGLESS_ARTIST_IGNORE_FILE, kept)
        show(f"Remembered {len(kept)} artist(s).")

    show(f"Removed {len(picked)} artist(s).")
    return len(picked)
