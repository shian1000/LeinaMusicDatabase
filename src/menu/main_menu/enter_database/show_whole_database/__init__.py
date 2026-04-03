from utils.menu_utils import execute_menu_item
from utils.debug import slog
from utils.display_utils import display_songs, display_songs_with_tags, display_artists

def show_whole_database():
    action_map = {
        "Show artists": display_artists,
        "Show songs": display_songs,
        "Show songs with tags": display_songs_with_tags
    }

    slog(action_map)

    execute_menu_item("Show database", action_map, exit_label="Exit")