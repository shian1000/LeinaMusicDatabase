import time
from menu.main_menu.enter_database.manage_database.fetch_database_data import import_data
from utils.search_for_songs import search_for_songs
from menu.main_menu.enter_database.manage_database.merge_artists import merge_artists
from utils.display_utils import display_songs
from utils.database.database_getter import get_songs_from_db, get_artists_from_db
import questionary
from utils.database.database_management import edit_song_entry
from utils.debug import slog
from menu.main_menu.enter_database.manage_database.manual_management import edit_entry

def test():

    edit_entry()

    time.sleep(10000)
