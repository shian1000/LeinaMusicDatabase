from utils.database.database_getter import get_songs_with_empty_category, extract_db_object_info
from utils.database.database_management import edit_db_entry
from utils.discoveries.music_brainz_fetcher import fetch_album_from_musicbrainz
import questionary
from utils.debug import slog, mlog
import time
from utils.text_utils import copy_to_clipboard
from utils.discoveries.wikipedia_fetcher import get_album_from_wikipedia
from menu.song_actions import edit_songs_menu
from utils.database.database_sessions import submit_global_database_session
from utils.database.datatables import song_categories
from utils.text_utils import truncate_at_word

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
    
    for song in songs_list:
        art, son, alb = song        
        art = truncate_at_word(art)
        son = truncate_at_word(son)
        slog(f"{art} - {son} ({alb})")
        if alb == None:
            slog("Trying to find in musicbrainz")
            art_clen = art.split("(")[0].strip()
            son_cln = son.split("(")[0].strip()
            alb = fetch_album_from_musicbrainz(art_clen, son_cln)
            if alb == None:
                slog("musicbrainz failed. Trying to find in wikipedia")
                alb = get_album_from_wikipedia(art_clen, son_cln)
            slog(alb)
            if not alb == None:
                print(f"Found album: \033[93m{alb}\033[0m for \033[93m{art} - {son}\033[0m")
            else:
                print(f"Couldn't find album for \033[93m{art} - {son}\033[0m")
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



