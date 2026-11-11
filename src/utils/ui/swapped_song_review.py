"""Manage database -> "Find songs with artist and title swapped": one checkbox
list of the catalog's songs stored "Title - Artist"
(artist_identity.find_swapped_in_catalog()), the ones entered both ways
ticked, then one question about remembering the unticked ones."""

from utils.common.output import escape, review_header, show
from config.constants import SWAPPED_SONG_IGNORE_FILE
from utils.database.artist_identity import find_swapped_in_catalog, fix_swapped_song, remember_ids
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.ui.prompts import ask_yes_no, tick_items


def _describe(flagged) -> str:
    song, artist, both_ways = flagged
    note = "  (that song is there already)" if both_ways else ""
    return f"{song.artist.name} - {song.title}  ->  {artist.name} - {song.artist.name}{note}"


def review_swapped_songs() -> int:
    """Returns how many songs were fixed."""
    music_session, _ = open_and_set_global_database_sessions()
    flagged = find_swapped_in_catalog(music_session)
    if not flagged:
        show("No songs look like they have artist and title swapped.")
        return 0

    review_header(f"REVIEW: {len(flagged)} song(s) may have artist and title swapped",
                  "Their title is another artist's name and their artist has no other song.",
                  "Pre-selected: the other artist already has a song titled like this one's artist.")
    picked = tick_items("Tick the songs whose artist and title are swapped (space toggles, enter confirms):",
                        flagged, _describe, checked=lambda f: f[2])
    if picked is None:
        show("Aborted, nothing changed.")
        return 0

    for song, artist, _ in picked:
        old = fix_swapped_song(music_session, song, artist)
        note = f" (artist \"{old.name}\" had no other song, removed)" if old else ""
        show(f"Now: {escape(artist.name)} - {escape(song.title)}{escape(note)}")
    submit_global_database_session()
    if any(both_ways for _, _, both_ways in picked):
        show("Songs that were there already are now in twice - Resolve duplicates -> "
             "\"Resolve duplicated songs\" merges them.")

    fixed = {song.id for song, _, _ in picked}
    skipped = [song.id for song, _, _ in flagged if song.id not in fixed]
    if skipped and ask_yes_no(f"Remember the {len(skipped)} unticked song(s) as not swapped, so they aren't asked about again?"):
        remember_ids(SWAPPED_SONG_IGNORE_FILE, skipped)
        show(f"Remembered {len(skipped)} song(s).")

    show(f"Fixed {len(picked)} song(s).")
    return len(picked)
