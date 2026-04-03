import time
from menu.main_menu.enter_database.manage_database.fetch_database_data import import_data
from utils.search_for_songs import search_for_songs
from menu.main_menu.enter_database.manage_database.merge_artists import merge_artists
from utils.display_utils import display_songs
from utils.database.database_getter import get_songs

def test():


    songs = get_songs("artist", "Aurora")
    time.sleep(1)
    display_songs(songs)
    
    
    time.sleep(10000)
