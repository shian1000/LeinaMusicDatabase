from src.navigation.menu_utils import execute_menu_item
from src.debug import slog
from menu.database_actions import edit_artist
from src.datatables import mp3_categories, artist_categories
import questionary
from src.library.library import edit_song_entry, search_songs_from_name

def edit_entry(mode: str):
    if (mode == "Artist"):
        action_map = artist_categories
        query = input("What artist do you wish to search for: ")
    else:
        action_map = mp3_categories
        query = input("What song do you wish to search for: ")

    category = questionary.select("What category entity do you wish to edit?", choices=action_map).ask()

    new_data = input("Type new data: ")

    if query == "":
        return
    
    songs = search_songs_from_name(query)

    songs_simple = [f"{artist} - {title}" for artist, title in songs]

    if len(songs) > 1:
        print("More than 1")
        song = questionary.select("Found more than one result:", choices=songs_simple).ask()
    else:
        song = songs_simple[0]
        print("Probably fine")

    print(f"This be ur song: {song}")

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