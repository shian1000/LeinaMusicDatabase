from utils.common.debug import slog
from utils.database.database_getter import get_artists_from_db_session, get_songs_from_db_session
import questionary
from utils.ui.prompts import ask_yes_no
from utils.database.datatables import artist_categories, search_only_categories, song_categories
from menu.song_actions import song_actions
from menu.artist_actions import artist_actions
from utils.ui.display_utils import display_songs, display_artists
from utils.database.database_sessions import submit_global_database_session


def _fetch(categories: list, search, display, actions, what: str) -> None:
    """Pick a category, type a query, show what `search` finds and offer
    `actions` on it. Only "full album" may be searched with an empty query."""
    category = questionary.select("What category do you wish to search for?", choices=categories + ["Back"]).ask()
    if category in (None, "Back"):  # None: Esc / Ctrl+C
        return
    may_be_empty = category == search_only_categories[1]
    query = input(f"What are you looking for{' (nothing = all full albums)' if may_be_empty else ''}: ")
    if query == "" and not may_be_empty:
        return

    found = search(category, query)
    if not found:
        print("Nothing found.")
    slog(found)
    display(found)
    if found and ask_yes_no(f"Do you want to do something with these {what}?"):
        actions(found)
        submit_global_database_session()


def fetch_artists():
    _fetch(artist_categories, get_artists_from_db_session, display_artists, artist_actions, "artists")


def fetch_songs():
    _fetch(search_only_categories + song_categories, get_songs_from_db_session, display_songs, song_actions, "songs")
