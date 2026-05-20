from utils.ui.menu_utils import execute_menu_item
from utils.common.debug import slog
from menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data import fill_missing_data
from utils.discoveries.import_data_from_mp3_tags import import_data_from_mp3_tags
from utils.ui.menu_utils import open_file_browser_terminal
from menu.song_actions.edit_songs import edit_songs_menu
from settings import settings
from utils.database.database_getter import get_songs_from_db_session
from utils.ui.display_utils import display_songs

def import_data():
    import_folder = settings.export_dir
    songs_path = open_file_browser_terminal(import_folder)
    imported_songs = import_data_from_mp3_tags(songs_path)
    print(imported_songs)
    if imported_songs:
        slog(imported_songs)
        imported_songs_objects = []
        for song in imported_songs:
            imported_songs_objects.extend(get_songs_from_db_session("name", f"{song['artist_name']} {song['title']}"))
        print(imported_songs_objects)
        display_songs(imported_songs_objects)
        edit_songs_menu(imported_songs_objects)
    


def fetch_database_data():
    action_map = {
        "Fill missing data": fill_missing_data,
        "Import data from mp3 tags": import_data
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map, exit_label="Back")