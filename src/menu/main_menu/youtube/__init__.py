from utils.ui.menu_utils import execute_menu_item
from utils.youtube.manage_youtube_playlists import show_playlists_in_creation_order


def youtube_menu():
    action_map = {
        "Show all playlists in creation order": show_playlists_in_creation_order,
    }
    execute_menu_item("YouTube", action_map, exit_label="Back")
