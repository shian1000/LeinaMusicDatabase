from src.navigation.menu_utils import execute_menu_item
from src.debug import slog
from src.menu.main_menu.enter_database import enter_database
from src.menu.main_menu.manage_local_files import manage_local_files

def main_menu():
    action_map = {
        "Enter database": enter_database,
        "Manage local files": manage_local_files,
        "Settings": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("Main Menu", action_map, exit_label="Exit")