"""Before songs are deleted (Song actions -> delete, the song editor's
"delete", Remove songs): a song used in playlists, moods or the library index
(song_references.song_uses()) is offered merging into the song it may
duplicate instead (song_duplicates.merge_candidates(), or one searched for).
Deleting leaves its playlist tracks with no song and drops its moods and
library match; merging moves them to the kept song. On 2026-10-17/18 five
wrongly read duplicates from folder playlists were deleted, and their
playlists showed them as not in the database."""

import questionary
from questionary import Choice

from utils.common.output import say, warn
from utils.database.database_sessions import require_global_database_sessions
from utils.database.datatables import Song
from utils.database.song_artists import song_artist_label
from utils.database.song_duplicates import merge_candidates, merge_songs
from utils.database.song_references import song_uses
from utils.ui.menu_utils import search_and_pick_db_object
from utils.common.cover_names import title_with_cover
from utils.common.project_names import song_project_name
from utils.ui.song_colors import colored_song, song_tokens

_SEARCH = "Merge it into another song (search)..."
_DELETE = "Delete it anyway"
_KEEP = "Keep it (don't delete)"


def _uses_text(uses) -> str:
    parts = [(uses.playlists, "playlist"), (uses.moods, "mood"), (uses.files, "library file")]
    return ", ".join(f"{n} {word}{'s' if n != 1 else ''}" for n, word in parts if n)


def merge_instead_of_deleting(songs: list) -> list | None:
    """Ask, for each of `songs` that is used somewhere, whether to merge it
    instead of deleting it. Merges (uncommitted - the caller commits) and
    returns the songs still to delete; None when the user cancelled (the
    merges rolled back - the menu commits after every action, so nothing
    else is pending)."""
    session, _ = require_global_database_sessions()
    to_delete = []
    for song in songs:
        uses = song_uses(song.id)
        if not uses:
            to_delete.append(song)
            continue
        label = colored_song(song_artist_label(song), title_with_cover(song), project=song_project_name(song))
        warn(f"Used in {_uses_text(uses)} - deleting leaves them without this song, merging moves them:")
        print(f"  {label}")
        choices = [Choice(title=song_tokens(song_artist_label(c), title_with_cover(c), c.album or None, song_project_name(c)), value=c)
                   for c in merge_candidates(session, song)]
        choices += [Choice(_SEARCH, value=_SEARCH), Choice(_DELETE, value=_DELETE), Choice(_KEEP, value=_KEEP)]
        picked = questionary.select("Merge it into one of these songs instead?", choices=choices).ask()
        if picked is None:
            # Cancelled: the merges made above aren't saved either
            session.rollback()
            return None
        if picked == _SEARCH:
            picked = search_and_pick_db_object("Song")
            if not isinstance(picked, Song) or picked.id == song.id:
                say("Nothing picked - kept, not deleted.")
                continue
        if picked == _DELETE:
            to_delete.append(song)
        elif picked == _KEEP:
            say("Kept.")
        else:
            merge_songs(session, picked, song)
            print(f"  Merged into {colored_song(song_artist_label(picked), picked.title)}")
    return to_delete
