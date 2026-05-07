"""This whole script is meant to be a mess as this is a place meant for testing only.
Nothing is dependend on this script"""


import time
from utils.discoveries.discoveries_manager import discover_album_name, load_discovery_modules
from utils.database.database_getter import get_songs_from_db_session
from utils.database.database_sessions import open_and_set_global_database_sessions
from utils.debug import slog
from menu.song_actions.copy_songs_from_storage import copy_songs_from_storage
from utils.file_management import copy_file_to_destination
from upath import UPath

def test():
    
    # open_and_set_global_database_sessions()
    # song = get_songs_from_db_session("title", "поо")[0]
    # slog(song)
    # slog(song.title)
    # modules = load_discovery_modules()
    # discover_album_name(song, modules)

    # open_global_driver()
    # song = get_album_from_genius("Hamilton Leithauser, Rostam Batmanglij", "In a Black Out")
    # print(song)
    # close_global_driver()
    

    # artist = get_artists_from_db_session("artist name", "Unknown Artist")

    # slog(artist)

    # divide_artist(artist[0])

    # uris = []
    # for path in files:
    #     dest = UPath("/home/owner/Downloads/")
    #     copy_file_to_destination(path, dest)

    time.sleep(10000)
