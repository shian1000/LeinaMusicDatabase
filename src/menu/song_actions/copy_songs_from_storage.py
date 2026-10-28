from upath import UPath
import questionary
from utils.common.file_management import (LIBRARY_INDEX_NAME, MissingSmbLoginError, copy_songs_using_index,
                                          library_index_path)
from utils.ui.menu_utils import open_file_browser_terminal, save_recent_dirs, load_recent_dirs
from utils.common.debug import flog, slog
from settings import settings


def copy_songs_from_storage(songs):
    if not settings.local_library_dir_str:
        print("No music library: MUSIC_LIBRARY_DIR isn't set - Settings -> Server -> Folders and network (or .env).")
        return "Back"
    try:
        index_file = library_index_path()
    except MissingSmbLoginError as exc:
        print(exc)
        return "Back"

    # Check index existence but guard against remote filesystem/network errors (SMB, fsspec, etc.)
    try:
        index_exists = index_file.exists()
    except Exception as e:
        # Log detailed debug info and show a friendly warning to the user. Don't crash the app.
        flog(f"Copy songs from storage: checking {index_file} failed: {type(e).__name__}: {e}")
        print("Warning: Could not reach the storage to check for an index file. The operation will be aborted.")
        return "Back"

    # Songs are found only through the index; this program doesn't build it
    if not index_exists:
        print(f"No library index ({LIBRARY_INDEX_NAME}) in the music library folder - copying needs it.")
        return "Back"

    recent_dirs = load_recent_dirs()

    action_map = [
        "Open file manager",
        "Type path manually",
        "Back"
    ]

    action_map = recent_dirs + action_map

    choice = questionary.select("Where do you want the files to be copied into: ", choices=action_map).ask()

    if choice in (None, "Back"):  # None: Esc / Ctrl+C
        return "Back"
    if choice == "Open file manager":
        # No recent folder yet (first use): start in the export folder
        paste_destination = open_file_browser_terminal(recent_dirs[0] if recent_dirs else str(settings.export_dir))
    elif choice == "Type path manually":
        typed = input("Type path: ").strip()
        paste_destination = UPath(typed) if typed else None
    else:
        paste_destination = UPath(choice)
    if paste_destination is None:  # left the file browser, or typed nothing
        return "Back"

    save_recent_dirs(str(paste_destination))

    slog(songs)

    try:
        copy_songs_using_index(songs, index_file, paste_destination)
    except Exception as e:
        flog(f"Copy songs from storage: copying to {paste_destination} failed: {type(e).__name__}: {e}")
        print("Warning: Copy operation failed due to storage/network error. Returning to menu.")
        return "Back"

    return "Back"

    