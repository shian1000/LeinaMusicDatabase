from upath import UPath
import questionary
from utils.file_management import copy_songs_using_index, get_proper_uri
from utils.menu_utils import open_file_browser_terminal, save_recent_dirs, load_recent_dirs


def copy_songs_from_storage(songs):
    source_path_str = get_proper_uri()

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
        copy_songs_using_index(songs, index_file, paste_destination)
    else:
        print("TODO - operating without using index")

    return "Back"

    