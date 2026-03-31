from debug import slog
from navigation.menu_utils import execute_menu_item

def fill_missing_data():
    action_map = {
        "This is in progress": fill_missing_data,
        "Please, just select BACK": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map, exit_label="Back")