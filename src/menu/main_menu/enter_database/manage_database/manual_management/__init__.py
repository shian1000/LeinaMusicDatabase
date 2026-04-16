from utils.menu_utils import execute_menu_item
from utils.debug import slog
from menu.database_actions import edit_artist
from utils.database.datatables import song_categories, artist_categories
import questionary
from utils.database.database_management import validate_song, edit_db_entry, delete_db_entry
from utils.database.database_getter import get_songs_from_db_session, get_artists_from_db_session, extract_db_object_info
import time
from utils.debug import slog
from utils.database.database_sessions import open_database_sessions, close_database_sessions, submit_and_close_database_sessions
from utils.menu_utils import pick_from_db_objects
from utils.database.datatables import Artist, Song

def edit_entry_menu(mode: str = None, db_object = None, sessions = None):
    should_submit_session = False
    if sessions == None:
        sessions = open_database_sessions()
        should_submit_session = True

    if db_object == None:
        if mode not in ("Artist", "Song"):
            choice = questionary.select("Dou you wish to make changes in artist or song?", choices=["Artist", "Song", "Back"]).ask()
            if choice == "Back":
                return
            edit_entry_menu(choice, db_object, sessions)
            return
    
        action_map, query_fn = (
            (artist_categories, get_artists_from_db_session)
            if mode == "Artist"
            else (song_categories, get_songs_from_db_session)
        )
        
        query = input (f"What {mode.lower()} do you wish to search for: ")
        if not query:
            print("Aborted")
            close_database_sessions(sessions)
            return

        entries_objects = query_fn("name", query, sessions)

        slog(entries_objects)
        slog(extract_db_object_info(entries_objects, "artist, title"))
        slog(len(entries_objects))

        db_object = pick_from_db_objects(entries_objects) if len(entries_objects) > 1 else entries_objects[0]
        label = db_object.name if mode == "Artist" else f"{db_object.artist.name} - {db_object.title}"
        print(f"Found '{label}'")
    else:
        action_map = artist_categories if isinstance(db_object, Artist) else song_categories
                
    slog(db_object)
    category = questionary.select("What category entity do you wish to edit for?", choices=action_map).ask()

    label = db_object.name if mode == "Artist" else f"{db_object.artist.name} - {db_object.title}"
    new_data = input(f"Type {category} for {label}: ")
    if not new_data:
        print("Aborted")
        close_database_sessions(sessions)
        return

    edit_db_entry(db_object, category, new_data, sessions)        
    if should_submit_session:
        submit_and_close_database_sessions(sessions)




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



    submit_and_close_database_sessions(sessions)

def manual_management():

    action_map = {
        "Add song": lambda: print("In progress"),
        "Edit artist": lambda: edit_entry_menu("Artist"),
        "Edit song": lambda: edit_entry_menu("Song"),
        "Remove song": lambda: remove_song_menu("Song")
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map, exit_label="Back")