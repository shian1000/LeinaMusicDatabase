from utils.ui.menu_utils import execute_menu_item
from utils.common.debug import slog

def manage_local_files():
    action_map = {
        "Index local files": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("Manage local files", action_map, exit_label="Exit")