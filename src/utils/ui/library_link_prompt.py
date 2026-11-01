"""The end of "Import data from YouTube playlist": offer to find the
imported songs' files in the music library (files.db) and link them.

A library scan (files_index.scan_library()) is what links them: the import
changed the catalog, so the scan retries every file without a song against
it, and reads any file added to the library since the last scan. Only the
safe matches LocalSongMatcher.match() takes - a file it can't place stays
for Settings -> Server -> "Index music library" or a pin by hand."""

from utils.common.output import ok, say, warn
from utils.database.files_index import LibraryUnavailableError, library_root, scan_library, song_file_paths
from utils.database.song_artists import song_artist_label
from utils.ui.prompts import ask_yes_no


def offer_linking_library_files(music_session, songs) -> None:
    """Ask whether to look for these songs' files in the music library, and
    if so scan it and list which of them got a file. Asks nothing when there's
    no library (unset, remote, drive unplugged) or every song has a file already."""
    songs = [s for s in songs if s.id is not None]
    if not songs:
        return
    try:
        library_root()
    except LibraryUnavailableError:
        return
    with_file = song_file_paths([s.id for s in songs])
    without_file = [s for s in songs if s.id not in with_file]
    if not without_file:
        return
    if not ask_yes_no(f"Look for the files of these {len(without_file)} song(s) without one "
                      f"in the music library and link them?"):
        return

    say("Scanning the music library ...")
    try:
        report = scan_library(music_session, progress=lambda done, total: say(f"  {done}/{total} files read"))
    except LibraryUnavailableError as exc:
        warn(f"Not scanned: {exc}")
        return
    wanted = {s.id: s for s in without_file}
    linked = [(path, wanted[song_id]) for path, song_id in report.matched if song_id in wanted]
    linked_ids = {song.id for _, song in linked}
    if linked:
        ok(f"Linked {len(linked)} file(s) to {len(linked_ids)} of the songs:")
        for path, song in linked:
            say(f"  {song_artist_label(song)} - {song.title}   <- {path}")
    else:
        say("No file in the music library matches these songs.")
    still_without = len(without_file) - len(linked_ids)
    if still_without:
        say(f"{still_without} song(s) still have no file.")
    others = report.matched_now - len(linked)
    if others:
        say(f"The scan also linked {others} other file(s) to songs already in the database.")
