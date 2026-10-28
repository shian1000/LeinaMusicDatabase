from utils.common.debug import slog
from utils.database.database_getter import get_artists_from_db_session, get_songs_from_db_session
import questionary
from utils.ui.prompts import ask_yes_no
from utils.database.datatables import artist_categories, search_only_categories, song_categories
from menu.song_actions import song_actions
from menu.artist_actions import artist_actions
from utils.ui.display_utils import display_songs, display_artists
from utils.database.database_sessions import submit_global_database_session

def fetch_artists():
    exit_label = ["Back"]
    action_map = artist_categories + exit_label

    category = questionary.select("What category do you wish to search for?", choices=action_map).ask()
    if category in (None, exit_label[0]):  # None: Esc / Ctrl+C
        return

    query = input("What are you looking for: ")
    if query == "":
        return

    artist_objects = get_artists_from_db_session(category, query)

    slog(artist_objects)

    display_artists(artist_objects)

    if(artist_objects):
        decision = ask_yes_no("Do you want to do something with these artists?")
        if decision:
            artist_actions(artist_objects)
            submit_global_database_session()

def fetch_songs():
    exit_label = ["Back"]
    action_map = search_only_categories + song_categories + exit_label

    category = questionary.select("What category do you wish to search for?", choices=action_map).ask()
    if category in (None, exit_label[0]):  # None: Esc / Ctrl+C
        return

    if category == search_only_categories[1]:
        query = input("What are you looking for (nothing = all full albums): ")
    else:
        query = input("What are you looking for: ")
        if query == "":
            return

    songs_objects = get_songs_from_db_session(category, query)
    if not songs_objects:
        print("Nothing found.")

    slog(songs_objects)

    display_songs(songs_objects)


    if(songs_objects):
        decision = ask_yes_no("Do you want to do something with these songs?")
        if decision:
            song_actions(songs_objects)
            submit_global_database_session()
