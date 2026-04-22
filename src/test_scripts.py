import time
from utils.discoveries.discovery_modules.itunes_fetcher import get_album_itunes

def test():

    song = get_album_itunes("Aurora", "Under the Water")
    print(song)

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
