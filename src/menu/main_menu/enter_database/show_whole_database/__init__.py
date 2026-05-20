from utils.ui.menu_utils import execute_menu_item
from utils.common.debug import slog
from utils.ui.display_utils import display_songs, display_songs_with_tags, display_artists
from utils.database.database_getter import get_songs_from_db_session, get_artists_from_db_session

def show_artists():
    artists = get_artists_from_db_session()
    display_artists(artists)

def show_songs():
    songs = get_songs_from_db_session()
    display_songs(songs)

def show_songs_with_tags():
    songs = get_songs_from_db_session()
    display_songs_with_tags(songs)



def show_whole_database():
    action_map = {
        "Show artists": show_artists,
        "Show songs": show_songs,
        "Show songs with tags": show_songs_with_tags
    }

    slog(action_map)

    execute_menu_item("Show database", action_map)