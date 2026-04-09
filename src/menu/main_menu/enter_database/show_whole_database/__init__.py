from utils.menu_utils import execute_menu_item
from utils.debug import slog
from utils.display_utils import display_songs, display_songs_with_tags, display_artists
from utils.database.database_sessions import open_database_sessions, close_database_sessions
from utils.database.database_getter import get_songs_from_db_session, get_artists_from_db_session

def display_artists_menu():
    sessions = open_database_sessions()
    artists = get_artists_from_db_session(sessions=sessions)
    display_artists(artists)
    close_database_sessions(sessions)

def display_songs_menu():
    sessions = open_database_sessions()
    songs = get_songs_from_db_session(sessions=sessions)
    display_songs(songs)
    close_database_sessions(sessions)

def display_songs_with_tags_menu():
    sessions = open_database_sessions()
    songs = get_songs_from_db_session(sessions=sessions)
    display_songs_with_tags(songs, sessions)
    close_database_sessions(sessions)



def show_whole_database():
    action_map = {
        "Show artists": display_artists_menu,
        "Show songs": display_songs_menu,
        "Show songs with tags": display_songs_with_tags_menu
    }

    slog(action_map)

    execute_menu_item("Show database", action_map, exit_label="Exit")