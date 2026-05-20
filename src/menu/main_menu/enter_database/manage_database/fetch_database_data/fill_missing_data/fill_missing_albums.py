from utils.database.database_getter import get_songs_with_empty_category, extract_db_object_info
from utils.database.database_management import edit_db_entry
import questionary
from utils.common.debug import slog, mlog
from menu.song_actions import edit_songs_menu
from utils.database.database_sessions import submit_global_database_session
from utils.common.selenium_sessions import open_global_driver, close_global_driver
from utils.discoveries.discoveries_manager import discover_album_name, load_discovery_modules
from utils.common.text_utils import copy_to_clipboard

def fill_missing_albums():
    category = "album"

    songs_objects = get_songs_with_empty_category(category)
    slog(songs_objects)

    songs_list = []
    
    if not songs_objects:
        print("No songs with missing albums found")
        return
    
    #Make it adjustable in settings
    print("Preparing fetch modules . . . ")
    open_global_driver()
    discovery_modules = load_discovery_modules()
    for song in songs_objects:
        print(f"Checking for \033[93m{song.artist.name} - {song.title}\033[0m")
        if song.album is None:
            slog(song)
            slog(discovery_modules)
            new_album = discover_album_name(song, discovery_modules)
            if new_album == None:
                print(f"Couldn't find album for \033[93m{song.artist.name} - {song.title}\033[0m")
            else:
                if (new_album) == "Singles":
                    print(f"\033[93m{song.artist.name} - {song.title}\033[0m is a single. Added to \033[93m{new_album}\033[0m")
                else:
                    print(f"Found album: \033[93m{new_album}\033[0m for \033[93m{song.artist.name} - {song.title}\033[0m")
                    print()
        songs_list.append((song.artist.name, song.title, new_album))
    slog(songs_list)
    
    #Make it adjustable in settings
    close_global_driver()

    if not len(songs_objects) == len(songs_list):
        print(f"albums_search_results and matched_objects items quantity mismatch! song_objects = {songs_objects}, songs_list = {songs_list}! Aborting!")
        return
    
    slog("Seems songs_objcets and songs_list do match. Continuing")
    for i, song in enumerate(songs_list):
        art, son, new_album = song
        if new_album is not None:
            slog(songs_objects[i])
            slog(new_album)
            edit_db_entry(songs_objects[i], "album", new_album)
            slog(f"Just edited an entry. {songs_objects[i].artist.name} - {songs_objects[i].title} ({songs_objects[i].album})")


    no_albums_count = sum(1 for art, son, alb in songs_list if alb is None)
    slog(no_albums_count)

    if (no_albums_count > 0):
        confirmation = questionary.confirm(f"Unable to find {no_albums_count} album names. Would you like to fill them up manually?").ask()
        if confirmation:
            for i, song in enumerate(songs_list):
                art, son, new_album = song
                if new_album is None:
                    print(f"{art} - {son}")
                    copy_to_clipboard(f"{art} - {son}")
                    album = input("New album name (Put nothing to cancel): ")
                    if not album == "":
                        edit_db_entry(songs_objects[i], "album", album)
                    else:
                        print(f"Skipped {art} - {son}")

    confirmation = questionary.confirm(f"Do you want to edit some of the changes manually?").ask()
    if confirmation:
        edit_songs_menu(songs_objects)

    submit_global_database_session()



