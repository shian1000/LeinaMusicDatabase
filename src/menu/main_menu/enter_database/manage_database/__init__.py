from utils.menu_utils import execute_menu_item
from utils.debug import slog, mlog
from menu.main_menu.enter_database.manage_database.fetch_database_data import fetch_database_data
from menu.main_menu.enter_database.manage_database.manual_management import manual_management
from menu.main_menu.enter_database.manage_database.resolve_duplicates import resolve_duplicated_artists, resolve_duplicated_albums
from menu.main_menu.enter_database.manage_database.merge_artists import merge_artists_menu
from utils.database.database_getter import get_artists_from_db_session, extract_db_object_info
from utils.database.database_sessions import open_database_sessions, submit_and_close_database_sessions
import time
from menu.main_menu.enter_database.manage_database.get_rid_of_rubish_data import get_rid_of_rubish_data

def resolve_duplicates():
    action_map = {
        "Resolve duplicated artists": resolve_duplicated_artists,
        "Resolve duplicated albums": resolve_duplicated_albums
    }

    execute_menu_item("Resolve duplicates", action_map)

def manage_database():
    action_map = {
        "Fetch database data": fetch_database_data,
        "Manual management": manual_management,
        "Resolve duplicates": resolve_duplicates,
        "Merge artists": merge_artists_menu,
        "Get rid of rubish data": get_rid_of_rubish_data
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map)