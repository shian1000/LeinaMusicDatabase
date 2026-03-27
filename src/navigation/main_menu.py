from src.navigation.manage_database import manage_database
from src.navigation.manage_local_files import manage_local_files
from src.navigation.manage_youtube_playlists import manage_youtube_playlists
from src.navigation.menu_utils import execute_menu_item
from src.debug import slog
from src.settings import settings



def what_to_look_for():
    action_map = {
        "Copy songs from local storage": lambda: print("In progress"),
        "Make YT playlist": lambda: print("In progress"),
        "Make TXT file": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("What to look for?", action_map)

def fetch_songs():
    action_map = {
        "Search by what category": lambda: print("In progress"),
        "What to look for": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("Fetch songs", action_map)

def show_whole_database():
    action_map = {
        "Show artists": lambda: print("In progress"),
        "Show songs": lambda: print("In progress"),
        "Show songs with tags": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("Show database", action_map)

def enter_database():
    action_map = {
        "Fetch Songs": fetch_songs,
        "Add tags to songs": lambda: print("In progress"),
        "Manage database": lambda: print("In progress"),
        "Show whole database": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("Database action", action_map)

def def_manage_local_files():
    action_map = {
        "Index local mp3": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("Manage local files", action_map)

def settings_menu():
    action_map = {
        "No settings right now": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("Settings", action_map)    


def main_menu():
    action_map = {
        "Enter database": enter_database,
        "Manage local files": lambda: print("In progress"),
        "Settings": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("Main Menu", action_map, exit_label="Exit")