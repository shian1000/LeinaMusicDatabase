from utils.menu_utils import execute_menu_item
from utils.debug import slog
from utils.debug import slog
from menu.song_actions.edit_songs import edit_entry_menu, remove_song_menu, add_songs_menu

def manual_management():

    action_map = {
        "Add song": add_songs_menu,
        "Edit artist": lambda: edit_entry_menu("Artist"),
        "Edit song": lambda: edit_entry_menu("Song"),
        "Remove song": lambda: remove_song_menu("Song")
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map, exit_label="Back")