from pathlib import Path

import questionary

from utils.ui.menu_utils import execute_menu_item
from utils.common.debug import slog
from menu.main_menu.enter_database.fetch_songs import fetch_songs, fetch_artists
from menu.main_menu.enter_database.manage_database import manage_database
from menu.main_menu.enter_database.show_whole_database import show_whole_database
from utils.database.database_sessions import open_and_set_global_database_sessions, close_global_database_sessions
from utils.youtube.manage_youtube_playlists import download_youtube_video

def fetch_songs_from_txt_file_menu():
    print("WIP")

def download_yt_song_menu():
    link = questionary.text("Enter YouTube link:").ask()
    if not link:
        print("No link provided")
        return None

    output_dir = str(Path(__file__).resolve().parents[4] / "import")
    return download_youtube_video(link, output_dir=output_dir)

def enter_database():
    open_and_set_global_database_sessions()
    action_map = {
        "Fetch songs": fetch_songs,
        "Fetch artists": fetch_artists,
        "Fetch songs from txt file": fetch_songs_from_txt_file_menu,
        "Download YT song": download_yt_song_menu,
        "Manage database": manage_database,
        "Show whole database": show_whole_database
    }

    slog(action_map)

    execute_menu_item("Database", action_map, exit_label="Exit database")
    close_global_database_sessions(commit=True)