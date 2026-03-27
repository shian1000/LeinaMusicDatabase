from src.navigation.menu_utils import execute_menu_item
from src.debug import slog
from src.menu.main_menu.enter_database.fetch_songs import fetch_songs
from src.menu.main_menu.enter_database.manage_database import manage_database
from src.menu.main_menu.enter_database.show_whole_database import show_whole_database

def enter_database():
    action_map = {
        "Fetch songs": fetch_songs,
        "Add tags to songs": lambda: print("In progress"),
        "Manage database": manage_database,
        "Show whole database": show_whole_database
    }

    slog(action_map)

    execute_menu_item("Database", action_map, exit_label="Exit")