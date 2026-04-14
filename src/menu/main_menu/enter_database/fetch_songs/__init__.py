from utils.debug import slog
from utils.database.database_getter import get_songs_from_db_session, extract_db_object_info
import questionary
from utils.database.datatables import song_categories
from menu.song_actions import song_actions
from utils.menu_utils import clear_screen
from utils.display_utils import display_songs
import time
from utils.database.music_db_manager import get_music_session
from utils.database.datatables import Song, Artist
from utils.database.database_sessions import open_database_sessions, close_database_sessions

def fetch_songs():
    exit_label = ["Back"]
    action_map = song_categories + exit_label

    category = questionary.select("What category do you wish to search for?", choices=action_map).ask()
    if category == exit_label[0]:
        return

    querry = input("What querry do you wish to search for: ")
    if querry == "":
        return


    session = get_music_session()

    sessions = open_database_sessions()

    songs_objects = get_songs_from_db_session(category, querry, sessions)

    slog(songs_objects)

    display_songs(songs_objects)

    session.close()
    close_database_sessions(sessions)

    if(songs_objects):
        decision = questionary.confirm("Do you want to do something with these songs?").ask()
        if decision: song_actions(extract_db_object_info(songs_objects, "artist, title"))
        else: clear_screen