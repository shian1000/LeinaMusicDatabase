from utils.ui.menu_utils import execute_menu_item
from menu.main_menu.enter_database import enter_database
from menu.main_menu.manage_local_files import manage_local_files
from menu.main_menu.settings import settings_menu
from utils.common.debug import slog

def main_menu():
    action_map = {
        "Enter database": enter_database,
        "Manage local files": manage_local_files,
        "Settings": settings_menu
    }
    
    slog(action_map)

    execute_menu_item("Main Menu", action_map, exit_label="Exit")