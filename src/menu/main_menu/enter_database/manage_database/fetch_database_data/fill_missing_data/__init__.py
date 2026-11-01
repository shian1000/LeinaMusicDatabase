from utils.ui.menu_utils import execute_menu_item
from menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data.fill_missing_albums import fill_missing_albums
from menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data.fill_missing_years import fill_missing_years
from utils.ui.genre_review import fill_missing_genres
import questionary
from utils.database.database_sessions import require_global_database_sessions
from utils.database.datatables import Song

def fill_missing_data():
    action_map = {
        "Albums": fill_missing_albums,
        "Years": fill_missing_years,
        "Genres": fill_missing_genres,
    }


    execute_menu_item("Fill missing data", action_map, exit_label="Back")


_ALBUMS, _YEARS = "albums", "years"


def fill_missing_for_import(result) -> None:
    """At the end of an import (mp3 tags, a YouTube playlist, Leina Radio's
    requests): which missing data to fill in for its songs - added or found
    already there (`result.entries`, an ImportResult) - in one checkbox list,
    then those steps for just these songs, with the entries they were read
    from (a title's year, "Legenda (1991)", for the years). Genres aren't
    offered yet. Albums go first: Years looks only at songs with an album."""
    entries = getattr(result, "entries", None) or {}
    if not entries:
        return
    session, _ = require_global_database_sessions()
    songs = sorted((song for song in (session.get(Song, song_id) for song_id in entries) if song is not None),
                   key=lambda song: (song.artist.name, song.title))
    without_album = [song for song in songs if not song.album]
    without_year = [song for song in songs if song.year is None]
    choices = []
    if without_album:
        choices.append(questionary.Choice(title=f"Albums ({len(without_album)} song(s) without one)", value=_ALBUMS,
                                          checked=True))
    if without_year:
        choices.append(questionary.Choice(title=f"Years ({len(without_year)} song(s) without one, "
                                                f"looked up for those with an album)", value=_YEARS, checked=True))
    if not choices:
        return
    picked = questionary.checkbox(
        f"Fill in missing data for the {len(songs)} imported song(s)? (space toggles, enter confirms, "
        f"nothing ticked = no)", choices=choices).ask()
    if not picked:
        return
    if _ALBUMS in picked:
        fill_missing_albums(songs, entries)
    if _YEARS in picked:
        fill_missing_years(songs, entries)
