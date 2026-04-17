from utils.database.database_getter import get_songs_from_db_session
from utils.discoveries.music_brainz_fetcher import is_blacklisted_album
import time
import questionary
from utils.database.database_management import edit_db_entry

def get_rid_of_rubish_data():
    print("Looking for sus album titles . . . . . ")
    songs = get_songs_from_db_session()

    for song in songs:
        if(is_blacklisted_album(song.album)):
            confirmation = questionary.confirm(f"'{song.album}' seems like rubish. Do you wish to edit it? (The song is '{song.artist.name} - {song.title}')").ask()
            if confirmation:
                new_name = input("Enter new album name: ")
                edit_db_entry(song, "album", new_name)