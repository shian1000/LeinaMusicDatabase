from src.navigation.menu_utils import execute_menu_item
from src.debug import slog
from src.menu.song_actions.copy_songs_from_storage import copy_songs_from_storage

def song_actions(songs):
    action_map = {
        "Copy songs from local storage": lambda: copy_songs_from_storage(songs),
        "Make YT playlist": lambda: print("In progress"),
        "Make TXT file": lambda: print("In progress")
    }

    slog(action_map)

    execute_menu_item("What do you want to do with these songs?", action_map, exit_label="Nothing")