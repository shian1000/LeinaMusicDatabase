from utils.menu_utils import execute_menu_item
from utils.debug import slog
from menu.main_menu.enter_database.manage_database.fetch_database_data import fetch_database_data
from menu.main_menu.enter_database.manage_database.manual_management import manual_management
from menu.main_menu.enter_database.manage_database.delete_duplicates import delete_duplicates
from menu.main_menu.enter_database.manage_database.merge_artists import merge_artists

def manage_database():
    action_map = {
        "Fetch database data": fetch_database_data,
        "Manual management": manual_management,
        "Remove duplicates": delete_duplicates,
        "Merge artists": merge_artists
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map, exit_label="Back")