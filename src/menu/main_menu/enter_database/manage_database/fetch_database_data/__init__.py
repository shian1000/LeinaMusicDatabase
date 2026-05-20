from utils.ui.menu_utils import execute_menu_item
from utils.common.debug import slog
from menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data import fill_missing_data
from utils.discoveries.import_data_from_mp3_tags import import_data_from_mp3_tags
from utils.ui.menu_utils import open_file_browser_terminal
from settings import settings

def import_data():
    import_folder = settings.export_dir
    songs_path = open_file_browser_terminal(import_folder)
    import_data_from_mp3_tags(songs_path)

def fetch_database_data():
    action_map = {
        "Fill missing data": fill_missing_data,
        "Import data from mp3 tags": import_data
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map, exit_label="Back")