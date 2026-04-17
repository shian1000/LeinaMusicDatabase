import questionary
import time
from utils.database.database_getter import get_artists_from_db_session, extract_db_object_info
from utils.debug import slog, mlog
from utils.database.database_sessions import submit_global_database_session
from utils.database.database_management import merge_artists_in_db


def merge_artists_menu():
    query = input("What querry do you wish to search for: ")

    if query == "":
        return

    artists_objects = get_artists_from_db_session("name", query, aggresive_search=True, work_on_global_session=True)
    slog(artists_objects)
    mlog(extract_db_object_info(artists_objects, "name, origin"))
    slog(len(artists_objects))

    if len(artists_objects)>1:
        artists_names = extract_db_object_info(artists_objects, "name")
        artists_names = [item for t in artists_names for item in t]+["Back"]
        merge_from_name = questionary.select("Select the artist you want to merge from (gets deleted from db)", choices=artists_names).ask()
        if merge_from_name == "Back":
            return
        merge_to_name = questionary.select("Select the artist you want to merge to (stays in db)", choices=artists_names).ask()
        if merge_to_name == "Back":
            return
        if merge_from_name == merge_to_name:
            print("Selected the same artists")
        else:
            confirm = questionary.confirm(f"You are about to merge all the songs from {merge_from_name} into {merge_to_name}. Do you wish to proceed?").ask()
            if(confirm):
                print("Merging songs")
                merge_from_obj = artists_objects[artists_names.index(merge_from_name)]
                merge_to_obj   = artists_objects[artists_names.index(merge_to_name)]
                merge_artists_in_db(merge_from_obj, merge_to_obj)
                submit_global_database_session()
    elif len(artists_objects) == 1:
        print("Only one artist found")
