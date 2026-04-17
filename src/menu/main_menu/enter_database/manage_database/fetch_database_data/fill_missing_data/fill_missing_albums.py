from utils.database.database_getter import get_songs_from_db_session, get_songs_with_empty_category, extract_db_object_info
from utils.database.database_management import edit_db_entry
from utils.discoveries.music_brainz_fetcher import fetch_album_from_musicbrainz
import questionary
from utils.database.database_sessions import open_database_sessions, close_database_sessions, submit_and_close_database_sessions
from utils.debug import slog, mlog
import time
from utils.display_utils import display_songs
from utils.text_utils import copy_to_clipboard
from utils.discoveries.wikipedia_fetcher import get_album_from_wikipedia
from menu.main_menu.enter_database.manage_database.manual_management import edit_entry_menu

def fill_missing_albums():
    category = "album"

    sessions = open_database_sessions()
    slog(sessions)
    songs_objects = get_songs_with_empty_category(category, sessions)
    slog(songs_objects)
    songs_list = extract_db_object_info(songs_objects, "artist, title, album")
    slog(songs_list)


    updated_songs = []
    
    for song in songs_list:
        art, son, alb = song
        slog(f"{art} - {son} ({alb})")
        if alb == None:
            slog("Trying to find in musicbrainz")
            alb = fetch_album_from_musicbrainz(art, son)
            if alb == None:
                slog("musicbrainz failed. Trying to find in wikipedia")
                alb = get_album_from_wikipedia(art, son)
            slog(alb)
            if not alb == None:
                print(f"Found album: {alb}  for {art} - {son}")
            else:
                print(f"Couldn't find album for {art} - {son}")
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
                    album = input("New album name: ")
                    if not album == "":
                        edit_db_entry(songs_objects[i], "album", album)
                    else:
                        print(f"Skipped {art} - {son}")

    submition_list = []
    confirmation = questionary.confirm(f"Do you want to edit some of the changes manually?").ask()
    if confirmation:
        for song in songs_list:
            art, son, alb = song
            submition_list.append(f"{art} - {son} ({alb})")
        submit_option = "Submit and save"
        submition_list.append(submit_option)
        loop_running = True
        while loop_running:
            song_selection = questionary.select("Select the entity you want to edit", choices=submition_list).ask()
            if song_selection == submit_option:
                loop_running = False
                submit_and_close_database_sessions(sessions)
            else:
                index = submition_list.index(song_selection)
                edit_entry_menu(db_object=songs_objects[index], sessions=sessions)
    else:
        submit_and_close_database_sessions(sessions)



