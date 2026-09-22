import questionary
from utils.ui.menu_utils import execute_menu_item, pick_from_db_objects
from utils.database.database_sessions import submit_global_database_session


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


def artist_actions(artist_objects):
    action_map = {
        "Add synonym": lambda: add_synonym_menu(artist_objects),
    }

    execute_menu_item("What do you want to do with these artists?", action_map, exit_label="Nothing", one_time=True)
