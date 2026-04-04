from utils.menu_utils import execute_menu_item
from utils.debug import slog
from menu.database_actions import edit_artist
from utils.database.datatables import song_categories, artist_categories
import questionary
from utils.database.database_management import edit_song_entry, validate_song
from utils.database.database_getter import get_songs_from_db
import time
from utils.debug import slog

def edit_entry(mode: str = None):
    if (mode == "Artist"):
        action_map = artist_categories
        query = input("What artist do you wish to search for: ")
    else:
        action_map = song_categories
        query = input("What song do you wish to search for: ")

    category = questionary.select("What category entity do you wish to edit?", choices=action_map).ask()

    new_data = input("Type new data: ")

    if query == "":
        return

    song = get_songs_from_db("name", query)


    edit_song_entry(song, category, new_data)

def manual_management():

    action_map = {
        "Add song": lambda: print("In progress"),
        "Edit artist": lambda: edit_entry("Artist"),
        "Edit song": lambda: edit_entry("Song"),
        "Remove song": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map, exit_label="Back")