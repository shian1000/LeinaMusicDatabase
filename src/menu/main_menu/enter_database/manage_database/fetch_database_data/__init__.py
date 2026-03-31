from src.navigation.menu_utils import execute_menu_item
from src.debug import slog
from menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data import fill_missing_data

def fetch_database_data():
    action_map = {
        "Fill missing data": fill_missing_data,
        "Import data from mp3 tags": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map, exit_label="Back")