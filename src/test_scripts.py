import time
from menu.main_menu.enter_database.manage_database.merge_artists import merge_artists
from utils.display_utils import display_songs
from utils.database.database_getter import get_songs_from_db_session
import questionary
from utils.database.database_management import edit_song_entry
from utils.debug import slog
from menu.main_menu.enter_database.manage_database.manual_management import edit_entry
from utils.database.database_getter import extract_song_info, get_artists_from_db_session, extract_artist_info
from utils.database.music_db_manager import get_music_session
from utils.database.tag_db_manager import get_tag_session
from utils.database.database_sessions import open_database_sessions, close_database_sessions


def test():

    sessions = open_database_sessions()
    artists_objects = get_artists_from_db_session("name", "au", sessions)
    print(artists_objects)
    artists = extract_artist_info(artists_objects, "name")
    print(artists)
    



    time.sleep(10000)
