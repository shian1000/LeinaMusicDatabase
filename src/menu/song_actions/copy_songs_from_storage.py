from settings import settings
from upath import UPath
from urllib.parse import urlparse
import questionary
from menu.song_actions.copy_using_index import copy_songs_from_index
import time
from navigation.recent_dirs import save_recent_dirs, load_recent_dirs
from navigation.menu_utils import execute_menu_item, open_file_browser_terminal

def is_local_dir(uri_str: str):
    parsed = urlparse(uri_str)
    return parsed.scheme == "" or parsed.scheme == "file"


def get_proper_uri():

    uri_upath = settings.local_library_dir
    uri_str = settings.local_library_dir_str

    if (is_local_dir(uri_str)):
        uri = str(uri_upath)
    else:
        parsed = urlparse(uri_str)
        scheme = f"{parsed.scheme}://"
        remainder = uri_str[len(scheme):]
        uri = f"{scheme}{settings.smb_username}:{settings.smb_password}@{remainder}"

    return uri


def copy_songs_from_storage(songs):
    source_path_str = get_proper_uri()
    source_path = UPath(source_path_str)

    index_file_str = (f"{source_path_str}.mp3_index.sqlite3")
    index_file = UPath(index_file_str)

    if not (index_file.exists()):
        choice = questionary.select("Index file not found. Would you like to create an index before proceeding further?", choices=["Yes", "No"]).ask()

        if choice == "No":
            print("Sorry, not implemented")

    recent_dirs = load_recent_dirs()

    action_map = [
        "Open file manager",
        "Type path manually",
        "Back"
    ]

    action_map = recent_dirs + action_map

    choice = questionary.select("Where do you want the files to be copied into: ", choices=action_map).ask()

    if choice == "Open file manager":
        paste_destination = open_file_browser_terminal(load_recent_dirs()[0])
    elif choice == "Type path manually":
        paste_destination = UPath(input("Type path: "))        
    elif choice == "Back":
        return
    else:
        paste_destination = UPath(choice)

    save_recent_dirs(str(paste_destination))

    if (index_file.exists()):
        copy_songs_from_index(songs, index_file, paste_destination)
    else:
        print("TODO - operating without using index")

    return "Back"

    