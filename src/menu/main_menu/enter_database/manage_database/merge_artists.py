import questionary
from utils.database.music_db_manager import get_music_session, Artist
from sqlalchemy import text
import questionary
import time
from utils.database.database_getter import get_artists_from_db_session, extract_artist_info
from utils.debug import slog, mlog
from utils.database.database_sessions import open_database_sessions, close_database_sessions, submit_and_close_database_sessions
from utils.database.database_management import merge_artists_in_db


def merge_artists_menu():
    query = input("What querry do you wish to search for: ")

    if query == "":
        return

    sessions = open_database_sessions()
    artists_objects = get_artists_from_db_session("name", query, sessions, aggresive_search=True)
    slog(artists_objects)
    mlog(extract_artist_info(artists_objects, "name, origin"))
    slog(len(artists_objects))

    if len(artists_objects)>1:
        artists_names = extract_artist_info(artists_objects, "name")
        artists_names = [item for t in artists_names for item in t]
        merge_from_name = questionary.select("Select the artist you want to merge from (gets deleted from db)", choices=artists_names).ask()
        merge_to_name = questionary.select("Select the artist you want to merge to (stays in db)", choices=artists_names).ask()
        if merge_from_name == merge_to_name:
            print("Selected the same artists")
            close_database_sessions(sessions)
        else:
            confirm = questionary.confirm(f"You are about to merge all the songs from {merge_from_name} into {merge_to_name}. Do you wish to proceed?").ask()
            if(confirm):
                print("Merging songs")
                merge_from_obj = artists_objects[artists_names.index(merge_from_name)]
                merge_to_obj   = artists_objects[artists_names.index(merge_to_name)]
                merge_artists_in_db(merge_from_obj, merge_to_obj, sessions)
                submit_and_close_database_sessions(sessions)
    else:
        print("Only one artist found")
        close_database_sessions(sessions)

