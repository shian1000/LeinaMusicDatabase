import questionary
from utils.ui.menu_utils import execute_menu_item, pick_from_db_objects
from utils.database.database_sessions import submit_global_database_session
from utils.database.database_management import delete_db_entry
from utils.ui.display_utils import display_artists


def add_synonym_menu(artist_objects):
    if len(artist_objects) > 1:
        artist = pick_from_db_objects(artist_objects, question="Which artist do you want to add a synonym to?")
        if artist is None:
            return
    else:
        artist = artist_objects[0]

    synonym = input(f"Enter synonym to add for {artist.name}: ")
    if not synonym:
        print("Aborted")
        return

    existing_synonyms = [s.strip() for s in artist.synonyms.split(",")] if artist.synonyms else []
    if synonym.lower() in (s.lower() for s in existing_synonyms):
        print(f"'{synonym}' is already a synonym of {artist.name}")
        return

    existing_synonyms.append(synonym)
    artist.synonyms = ", ".join(existing_synonyms)
    submit_global_database_session()
    print(f"Added '{synonym}' as a synonym of {artist.name}")


def remove_artists(artist_objects):
    print("About to delete these artists from the database:")
    display_artists(artist_objects)
    confirmation = questionary.confirm(f"Are you sure you want to delete {len(artist_objects)} artist(s)???").ask()
    if not confirmation:
        print("Aborted")
        return

    for artist in artist_objects:
        delete_db_entry(artist)

    submit_global_database_session()
    print(f"Deleted {len(artist_objects)} artist(s).")


def artist_actions(artist_objects):
    action_map = {
        "Add synonym": lambda: add_synonym_menu(artist_objects),
        "Remove from the database": lambda: remove_artists(artist_objects),
    }

    execute_menu_item("What do you want to do with these artists?", action_map, exit_label="Nothing", one_time=True)
