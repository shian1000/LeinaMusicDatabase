from utils.menu_utils import execute_menu_item
from utils.debug import slog
from utils.display_utils import display_songs, display_songs_with_tags, display_artists
from utils.database.database_sessions import open_database_sessions, close_database_sessions
from utils.database.database_getter import get_songs_from_db_session, get_artists_from_db_session

def show_artists():
    sessions = open_database_sessions()
    artists = get_artists_from_db_session(sessions=sessions)
    display_artists(artists)
    close_database_sessions(sessions)

def show_songs():
    sessions = open_database_sessions()
    songs = get_songs_from_db_session(sessions=sessions)
    display_songs(songs)
    close_database_sessions(sessions)

def show_songs_with_tags():
    sessions = open_database_sessions()
    songs = get_songs_from_db_session(sessions=sessions)
    display_songs_with_tags(songs, sessions)
    close_database_sessions(sessions)



def show_whole_database():
    action_map = {
        "Show artists": show_artists,
        "Show songs": show_songs,
        "Show songs with tags": show_songs_with_tags
    }

    slog(action_map)

    execute_menu_item("Show database", action_map, exit_label="Exit")