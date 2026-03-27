from src.navigation.menu_utils import execute_menu_item
from src.debug import slog

def fetch_database_data():
    action_map = {
        "Fill missing data": lambda: print("In progress"),
        "Import data from mp3 tags": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map, exit_label="Exit")