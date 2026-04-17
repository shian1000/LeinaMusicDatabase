import questionary
from utils.database.datatables import Song, Artist, artist_categories, song_categories
from utils.debug import slog
from utils.database.database_sessions import open_database_sessions, get_global_database_sessions, submit_global_database_session
from utils.database.database_getter import get_artists_from_db_session, get_songs_from_db_session, extract_db_object_info
from utils.menu_utils import pick_from_db_objects, get_list_of_properties_from_db_object
from utils.database.database_management import edit_db_entry, delete_db_entry
import time

def edit_entry_menu(mode: str = None, db_object = None):
    if db_object == None:
        if mode not in ("Artist", "Song"):
            choice = questionary.select("Dou you wish to make changes in artist or song?", choices=["Artist", "Song", "Back"]).ask()
            if choice == "Back":
                return
            edit_entry_menu(choice, db_object, work_on_global_session=True)
            return
    
        action_map, query_fn = (
            (artist_categories, get_artists_from_db_session)
            if mode == "Artist"
            else (song_categories, get_songs_from_db_session)
        )
        
        query = input (f"What {mode.lower()} do you wish to search for: ")
        if not query:
            print("Aborted")
            return

        entries_objects = query_fn("name", query) if mode == "Song" else query_fn("artist name", query)

        slog(entries_objects)
        slog(extract_db_object_info(entries_objects, "artist, title"))
        slog(len(entries_objects))

        if not entries_objects:
            return
        db_object = pick_from_db_objects(entries_objects) if len(entries_objects) > 1 else entries_objects[0]
        label = db_object.name if mode == "Artist" else f"{db_object.artist.name} - {db_object.title}"
        print(f"Found '{label}'")
    else:
        action_map = artist_categories if isinstance(db_object, Artist) else song_categories
    

    properties_list = get_list_of_properties_from_db_object(db_object)
    displayed_list = [f"{menu_item} ({property})" for menu_item, property in zip(action_map, properties_list)]
    back_option = "back"
    displayed_list.append(back_option)

    choice = questionary.select("What category do you wish to edit?", choices=displayed_list).ask()
    if choice == back_option:
        return
    choosen_index = (displayed_list.index(choice))
    category = action_map[choosen_index]
    label = db_object.name if mode == "Artist" else f"{db_object.artist.name} - {db_object.title}"
    new_data = input(f"Type {category} for {label}: ")
    if not new_data:
        print("Aborted")
        return

    edit_db_entry(db_object, category, new_data)
    submit_global_database_session()
            




def remove_song_menu(mode: str = None):
    sessions = open_database_sessions()

    if (mode == "Artist"):
        query = input("What artist do you wish to search for: ")    
        entries_objects = get_artists_from_db_session("name", query, sessions)
    else:
        query = input("What song do you wish to search for: ")
        slog(query)
        entries_objects = get_songs_from_db_session("name", query, sessions)

    slog(entries_objects)
    slog(extract_db_object_info(entries_objects, "artist, title"))
    slog(len(entries_objects))

    if(len(entries_objects) > 1):
        db_object = pick_from_db_objects(entries_objects)
    else:
        db_object = entries_objects[0]
        if mode == "Artist":
            print(f"Found '{db_object.name}'")
        else:
            print(f"Found '{db_object.artist.name} - {db_object.title}")
                

    slog(db_object)

    confirmation = questionary.confirm(f"Do you really want to delete {db_object.artist.name} - {db_object.title}?").ask()
    if(confirmation):
        delete_db_entry(db_object, sessions)
        print(f"Song deleted")
    else:
        print("Aborted")


def edit_songs_menu(songs_objects):
    songs_list = []
    loop_running = True
    while loop_running:
        songs_list.clear()
        for i, song in enumerate(songs_objects):
            songs_list.append(f"{song.artist.name} - {song.title} ({song.album})")
        submit_option = "Submit and save"
        songs_list.append(submit_option)
        song_selection = questionary.select("Select the entity you want to edit", choices=songs_list).ask()
        if song_selection == submit_option:
            loop_running = False
            return
        else:
            index = songs_list.index(song_selection)
            edit_entry_menu(db_object=songs_objects[index])