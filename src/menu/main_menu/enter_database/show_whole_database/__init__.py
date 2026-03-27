from src.navigation.menu_utils import execute_menu_item
from src.debug import slog

def show_whole_database():
    action_map = {
        "Show artists": lambda: print("In progress"),
        "Show songs": lambda: print("In progress"),
        "Show songs with tags": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("Show database", action_map, exit_label="Exit")