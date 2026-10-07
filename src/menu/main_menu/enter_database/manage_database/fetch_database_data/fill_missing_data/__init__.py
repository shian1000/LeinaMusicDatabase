from utils.common.debug import slog
from utils.ui.menu_utils import execute_menu_item
from menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data.fill_missing_albums import fill_missing_albums
from menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data.fill_missing_years import fill_missing_years

def fill_missing_data():
    action_map = {
        "Artists and title": lambda: print("Not yet implemented :)"),
        "Albums": fill_missing_albums,
        "Years": fill_missing_years
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map, exit_label="Back")