from src.navigation.menu_utils import execute_menu_item
from src.debug import slog

def manual_management():
    action_map = {
        "Add song": lambda: print("In progress"),
        "Edit song": lambda: print("In progress"),
        "Remove song": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map, exit_label="Exit")