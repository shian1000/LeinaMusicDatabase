from utils.menu_utils import execute_menu_item
from utils.debug import slog
from menu.main_menu.enter_database.fetch_songs import fetch_songs
from menu.main_menu.enter_database.manage_database import manage_database
from menu.main_menu.enter_database.show_whole_database import show_whole_database
from utils.database.database_sessions import open_and_set_global_database_sessions, close_global_database_sessions
import time

def enter_database():
    open_and_set_global_database_sessions()
    action_map = {
        "Fetch songs": fetch_songs,
        "Add tags to songs": lambda: print("In progress"),
        "Manage database": manage_database,
        "Show whole database": show_whole_database
    }

    slog(action_map)

    execute_menu_item("Database", action_map, exit_label="Exit database")
    close_global_database_sessions(commit=True)