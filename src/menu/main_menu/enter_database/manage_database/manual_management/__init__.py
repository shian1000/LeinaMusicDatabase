from utils.menu_utils import execute_menu_item
from utils.debug import slog
from menu.database_actions import edit_artist
from utils.database.datatables import song_categories, artist_categories
import questionary
from utils.database.database_management import edit_song_entry_from_name, validate_song, edit_artist_entry, edit_song_entry
from utils.database.database_getter import get_songs_from_db_session, extract_song_info, get_artists_from_db_session, extract_artist_info
import time
from utils.debug import slog
from utils.database.database_sessions import open_database_sessions, close_database_sessions, submit_and_close_database_sessions
from utils.menu_utils import pick_from_db_objects

def edit_entry(mode: str = None):
    sessions = open_database_sessions()

    if (mode == "Artist"):
        action_map = artist_categories
        query = input("What artist do you wish to search for: ")    
        entries_objects = get_artists_from_db_session("name", query, sessions)
    else:
        action_map = song_categories
        query = input("What song do you wish to search for: ")
        entries_objects = get_songs_from_db_session("name", query, sessions)

    slog(entries_objects)
    slog(len(entries_objects))

    if(len(entries_objects)):
        db_object = pick_from_db_objects(entries_objects)

    slog(db_object)

    category = questionary.select("What category entity do you wish to edit?", choices=action_map).ask()

    new_data = input("Type new data: ")

    if query == "":
        print("Aborted")
        close_database_sessions(sessions)
        return


    if(mode == "Artist"):
        edit_artist_entry(song, category, new_data)
    else:
        edit_song_entry(db_object, category, new_data)
        
    submit_and_close_database_sessions(sessions)

def manual_management():

    action_map = {
        "Add song": lambda: print("In progress"),
        "Edit artist": lambda: edit_entry("Artist"),
        "Edit song": lambda: edit_entry("Song"),
        "Remove song": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map, exit_label="Back")