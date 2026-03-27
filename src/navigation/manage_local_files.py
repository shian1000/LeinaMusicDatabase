from src.navigation.menu_utils import execute_menu_item
from src.local_files_manager.tags_fetcher import tags_fetcher


def manage_indexed_entires():
    action_map = {
        "Fill the gaps in the index": tags_fetcher
    }

    execute_menu_item("Manage indexed entries:", action_map)


def manage_local_files():
    # Make sure values are callables (don't call them at dict creation time)
    action_map = {
        "Copy MP3s": lambda: print(""),
        "Manage indexed entries": manage_indexed_entires
    }

    execute_menu_item("Select what to do", action_map)