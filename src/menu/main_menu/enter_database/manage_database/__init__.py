from utils.ui.menu_utils import execute_menu_item
from utils.common.debug import slog, mlog
from menu.main_menu.enter_database.manage_database.fetch_database_data import fetch_database_data
from menu.main_menu.enter_database.manage_database.manual_management import manual_management
from menu.main_menu.enter_database.manage_database.resolve_duplicates import resolve_duplicated_artists, resolve_duplicated_albums
from menu.main_menu.enter_database.manage_database.merge_divide_menu import merge_artists_menu, divide_artists_menu
import time
from menu.main_menu.enter_database.manage_database.get_rid_of_rubish_data import get_rid_of_rubish_data
from utils.common.text_utils import check_spelling
from utils.database.database_getter import get_songs_from_db_session
from utils.ui.display_utils import display_songs
import questionary
from utils.database.database_sessions import submit_global_database_session
from utils.database.tags_management import add_tag_to_song, has_tag_on_song

RED = "\033[91m"
GREEN = "\033[92m"
RESET = "\033[0m"


def resolve_duplicates():
    action_map = {
        "Resolve duplicated artists": resolve_duplicated_artists,
        "Resolve duplicated albums": resolve_duplicated_albums
    }

    execute_menu_item("Resolve duplicates", action_map)

def check_spelling_menu():

    songs = get_songs_from_db_session()
    
    for song in songs:
        if has_tag_on_song(song, "spellchecked"):
            continue
        print(f"Checking {song.artist.name} - {song.title}")
        spell_check_results = check_spelling(song.artist.name, song.title)
        new_artist = spell_check_results["corrected_artist"]
        new_title = spell_check_results["corrected_title"]

        # Handle Artist Correction
        if new_artist != song.artist.name:
            print(f"{RED}{song.artist.name}{RESET} -> {GREEN}{new_artist}{RESET}")
            if questionary.confirm("Do you wish to correct this artist name?").ask():
                song.artist.name = new_artist

        # Handle Title Correction
        if new_title != song.title:
            print(f"{RED}{song.title}{RESET} -> {GREEN}{new_title}{RESET}")
            if questionary.confirm("Do you wish to correct this song title?").ask():
                song.title = new_title

        add_tag_to_song(song, "spellchecked")
        submit_global_database_session()
    submit_global_database_session()
        

def manage_database():
    action_map = {
        "Fetch database data": fetch_database_data,
        "Manual management": manual_management,
        "Resolve duplicates": resolve_duplicates,
        "Merge artists": merge_artists_menu,
        "Divide artists": divide_artists_menu,
        "Spell check existing data": check_spelling_menu,
        "Get rid of rubish data": get_rid_of_rubish_data
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map)