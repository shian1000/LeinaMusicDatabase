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
from navigation.menu_utils import open_file_browser_terminal


def test():


    open_file_browser_terminal("/home/owner/Documents/Code/")

    time.sleep(10000)
