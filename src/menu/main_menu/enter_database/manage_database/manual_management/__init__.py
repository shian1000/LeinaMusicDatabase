from utils.menu_utils import execute_menu_item
from utils.debug import slog
from menu.database_actions import edit_artist
from utils.database.datatables import song_categories, artist_categories
import questionary
from utils.database.database_management import edit_song_entry_from_name, validate_song, edit_artist_entry
from utils.database.database_getter import get_songs_from_db_session, extract_song_info, get_artists_from_db_session, extract_artist_info
import time
from utils.debug import slog
from utils.database.database_sessions import open_database_sessions, close_database_sessions

def edit_entry(mode: str = None):
    if (mode == "Artist"):
        action_map = artist_categories
        query = input("What artist do you wish to search for: ")
        sessions = open_database_sessions()
        artist_objects = get_artists_from_db_session("name", query, sessions)
        artists = extract_artist_info(artist_objects, "name")
        slog(artists)
    else:
        action_map = song_categories
        query = input("What song do you wish to search for: ")
        sessions = open_database_sessions()
        song_objects = get_songs_from_db_session("name", query, sessions)
        songs = extract_song_info(song_objects, "artist, title")
        close_database_sessions(sessions)


    slog(songs)
    song = validate_song(songs)
    slog(song)

    category = questionary.select("What category entity do you wish to edit?", choices=action_map).ask()

    new_data = input("Type new data: ")

    if query == "":
        return

    slog(song)

    if(mode == "Artist"):
        edit_artist_entry(song, category, new_data)
    else:
        edit_song_entry_from_name(song, category, new_data)

def manual_management():

    action_map = {
        "Add song": lambda: print("In progress"),
        "Edit artist": lambda: edit_entry("Artist"),
        "Edit song": lambda: edit_entry("Song"),
        "Remove song": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map, exit_label="Back")