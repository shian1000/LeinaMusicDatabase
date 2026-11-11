"""Manage database -> "Remove artists without songs": one checkbox list of the
artist rows nothing uses (artist_identity.find_songless_artists()), then one
question about remembering the unticked ones as kept. Ticked at first: the
ones simply deleted; one that looks like an artist with songs is merged into
it instead (its name kept as a synonym), unticked - check that it is the same
artist."""

from config.constants import SONGLESS_ARTIST_IGNORE_FILE
from utils.common.output import escape, review_header, show
from utils.database.artist_identity import find_songless_artists, remember_ids, remove_songless_artist
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.ui.prompts import ask_yes_no, tick_items


def _describe(found) -> str:
    artist, heir = found
    albums = f", {len(artist.albums)} empty album(s)" if artist.albums else ""
    action = f'merge into "{heir.name}" (name kept as its synonym)' if heir else "delete"
    return f"{artist.name}  ->  {action}{albums}"


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
                  "Unticked at first: the merges - check the artists are one artist.")
    picked = tick_items("Tick the artists to remove (space toggles, enter confirms):", found, _describe,
                        checked=lambda f: f[1] is None)
    if picked is None:
        show("Aborted, nothing removed.")
        return 0

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
