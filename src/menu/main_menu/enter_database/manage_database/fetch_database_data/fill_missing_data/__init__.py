from utils.debug import slog
from utils.menu_utils import execute_menu_item
from menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data.fill_missing_albums import fill_missing_albums

def fill_missing_data():
    action_map = {
        "Artists and title": lambda: print("Not yet implemented :)"),
        "Albums": fill_missing_albums,
        "Years": lambda: print("Not yet implemented :)")
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map, exit_label="Back")