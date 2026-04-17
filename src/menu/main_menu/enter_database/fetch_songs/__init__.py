from utils.debug import slog
from utils.database.database_getter import get_songs_from_db_session
import questionary
from utils.database.datatables import song_categories, search_only_categories
from menu.song_actions import song_actions
from utils.menu_utils import clear_screen
from utils.display_utils import display_songs
import time
from utils.database.database_sessions import submit_global_database_session

def fetch_songs():
    exit_label = ["Back"]
    action_map = search_only_categories + song_categories + exit_label

    category = questionary.select("What category do you wish to search for?", choices=action_map).ask()
    if category == exit_label[0]:
        return

    querry = input("What are you looking for: ")
    if querry == "":
        return

    songs_objects = get_songs_from_db_session(category, querry)

    slog(songs_objects)

    display_songs(songs_objects)


    if(songs_objects):
        decision = questionary.confirm("Do you want to do something with these songs?").ask()
        if decision:
            song_actions(songs_objects)
            submit_global_database_session()
        else:
            clear_screen