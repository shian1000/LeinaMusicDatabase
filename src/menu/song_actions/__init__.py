from utils.menu_utils import execute_menu_item
from utils.debug import slog
from menu.song_actions.copy_songs_from_storage import copy_songs_from_storage
from menu.song_actions.edit_songs import edit_songs_menu
from utils.database.database_getter import extract_db_object_info


def song_actions(songs_objects):
    songs_list = extract_db_object_info(songs_objects, "artist, title")

    action_map = {
        "Edit": lambda: edit_songs_menu(songs_objects),
        "Copy songs from local storage": lambda: copy_songs_from_storage(songs_list),
        "Make YT playlist": lambda: print("In progress"),
        "Make TXT file": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("What do you want to do with these songs?", action_map, exit_label="Nothing", one_time=True)