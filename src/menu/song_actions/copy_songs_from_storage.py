from upath import UPath
import questionary
from utils.common.file_management import copy_songs_using_index, get_proper_uri
from utils.ui.menu_utils import open_file_browser_terminal, save_recent_dirs, load_recent_dirs
from utils.common.debug import slog


def copy_songs_from_storage(songs):
    source_path_str = get_proper_uri()

    index_file_str = (f"{source_path_str}.mp3_index.sqlite3")
    index_file = UPath(index_file_str)

    # Check index existence but guard against remote filesystem/network errors (SMB, fsspec, etc.)
    try:
        index_exists = index_file.exists()
    except Exception as e:
        # Log detailed debug info and show a friendly warning to the user. Don't crash the app.
        slog(e, "copy_songs_from_storage - index existence check failed")
        print("Warning: Could not reach the storage to check for an index file. The operation will be aborted.")
        return "Back"

    if not index_exists:
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

    slog(songs)

    try:
        if index_exists and index_file.exists():
            copy_songs_using_index(songs, index_file, paste_destination)
        else:
            print("TODO - operating without using index")
    except Exception as e:
        slog(e, "copy_songs_from_storage - copy operation failed")
        print("Warning: Copy operation failed due to storage/network error. Returning to menu.")
        return "Back"

    return "Back"

    