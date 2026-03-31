from src.navigation.menu_utils import execute_menu_item
from src.debug import slog
from library.library import display_songs, display_artists, display_songs_with_tags

def show_whole_database():
    action_map = {
        "Show artists": display_artists,
        "Show songs": display_songs,
        "Show songs with tags": display_songs_with_tags
    }

    slog(action_map)

    execute_menu_item("Show database", action_map, exit_label="Exit")