from src.settings import settings
from src.debug import slog
import time
from src.menu.main_menu.enter_database.fetch_songs import fetch_songs
from src.menu.song_actions.copy_songs_from_storage import get_proper_uri
from upath import UPath
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from src.menu.song_actions.copy_songs_from_storage import copy_songs_from_index


def test():


    songs = [('Angelo Badalamenti', 'Twin Peaks Theme'), ('Aurora', 'The River'), ('Aurora', 'The Seed'), ('Aurora', 'Mothership'), ('Aurora', 'Dance On The Moon'), ('Gran Turismo 4 OST', 'Moon Over The Castle [Extended Orchestral Version]')]
    index_path = UPath(get_proper_uri())
    destination = UPath("/home/owner/Documents/Code/MusicDatabase/")



    copy_songs_from_index(songs, index_path, destination)

    time.sleep(10000)
