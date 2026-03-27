from src.settings import settings
from upath import UPath
from urllib.parse import urlparse
import questionary
from src.menu.song_actions.copy_using_index import copy_songs_from_index
import time

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

    print(index_file_str)
    print(index_file)

    if not (index_file.exists()):
        questionary.select("Index file not found. Would you like to create an index before proceeding further?", choices=["Yes", "No"]).ask()

    paste_destination = UPath(input("Where do you want the files to be copied into: "))

    if (index_file.exists()):
        copy_songs_from_index(songs, index_file, paste_destination)
    else:
        print("TODO - operating without using index")

    