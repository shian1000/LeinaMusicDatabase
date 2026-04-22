from utils.database.database_getter import get_songs_with_empty_category, extract_db_object_info
from utils.database.database_management import edit_db_entry
import questionary
from utils.debug import slog, mlog
import time
from utils.text_utils import copy_to_clipboard
from menu.song_actions import edit_songs_menu
from utils.database.database_sessions import submit_global_database_session
from utils.database.datatables import song_categories
from utils.text_utils import truncate_at_word
from utils.discoveries.discovery_modules.genius_fetcher import get_album_from_genius
from utils.selenium_sessions import open_global_driver, close_global_driver
from utils.discoveries.discoveries_manager import discover_album_name, load_discovery_modules

def fill_missing_albums():
    category = "album"

    songs_objects = get_songs_with_empty_category(category)
    slog(songs_objects)
    songs_list = extract_db_object_info(songs_objects, f"{song_categories[1]}, {song_categories[0]}, {song_categories[2]}")
    slog(songs_list)


    updated_songs = []
    
    if not songs_list:
        print("No songs with missing albums found")
        return
    
    discovery_modules = load_discovery_modules()
    for song in songs_list:
        art, son, alb = song
        slog(f"{art} - {son} ({alb})")
        if alb is None:
            alb = discover_album_name(art, son, discovery_modules)
            if alb == None:
                print(f"Couldn't find album for \033[93m{art} - {son}\033[0m")
            else:
                print(f"Found album: \033[93m{alb}\033[0m for \033[93m{art} - {son}\033[0m")
        updated_songs.append((art, son, alb))
    songs_list = updated_songs
    slog(songs_list)

    if not len(songs_objects) == len(songs_list):
        print(f"albums_search_results and matched_objects items quantity mismatch! song_objects = {songs_objects}, songs_list = {songs_list}! Aborting!")
        return
    
    slog("Seems songs_objcets and songs_list do match. Continuing")
    for i, song in enumerate(songs_list):
        art, son, alb = song
        if alb is not None:
            slog(songs_objects[i])
            slog(alb)
            edit_db_entry(songs_objects[i], "album", alb)
            slog(f"Just edited an entry. {songs_objects[i].artist.name} - {songs_objects[i].title} ({songs_objects[i].album})")


    no_albums_count = sum(1 for art, son, alb in songs_list if alb is None)
    slog(no_albums_count)

    if (no_albums_count > 0):
        confirmation = questionary.confirm(f"Unable to find {no_albums_count} album names. Would you like to fill them up manually?").ask()
        if confirmation:
            for i, song in enumerate(songs_list):
                art, son, alb = song
                if alb is None:
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



