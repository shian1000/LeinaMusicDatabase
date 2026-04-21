import time
from utils.discoveries.wikipedia_fetcher import get_album_from_wikipedia
import questionary
from settings import settings
import subprocess
from upath import UPath
from utils.file_management import copy_file_to_destination
from utils.database.database_getter import get_artists_from_db_session
from utils.database.database_management import divide_artist
from utils.database.database_sessions import open_and_set_global_database_sessions
from utils.debug import slog
from menu.main_menu.enter_database.manage_database.merge_divide_menu import merge_artists_menu

def test():
    
    open_and_set_global_database_sessions()

    merge_artists_menu()

    

    # artist = get_artists_from_db_session("artist name", "Unknown Artist")

    # slog(artist)

    # divide_artist(artist[0])

    # uris = []
    # for path in files:
    #     dest = UPath("/home/owner/Downloads/")
    #     copy_file_to_destination(path, dest)

    time.sleep(10000)
