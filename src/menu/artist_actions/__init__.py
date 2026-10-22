import questionary
from utils.ui.prompts import ask_yes_no
from utils.ui.menu_utils import execute_menu_item, pick_from_db_objects
from utils.database.database_sessions import submit_global_database_session
from utils.database.database_management import ArtistHasSongsError, delete_db_entry
from utils.ui.display_utils import display_artists
from menu.song_actions.edit_songs import HAS_SONGS_HINT, edit_entry_menu


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


def remove_synonym_menu(artist_objects):
    with_synonyms = [a for a in artist_objects if a.synonyms and a.synonyms.strip()]
    if not with_synonyms:
        print("None of these artists has synonyms")
        return
    if len(with_synonyms) > 1:
        artist = pick_from_db_objects(with_synonyms, question="Which artist do you want to remove a synonym from?")
        if artist is None:
            return
    else:
        artist = with_synonyms[0]

    existing_synonyms = [s.strip() for s in artist.synonyms.split(",") if s.strip()]
    to_remove = questionary.checkbox(f"Which synonyms of {artist.name} do you want to remove?",
                                     choices=existing_synonyms).ask()
    if not to_remove:
        print("Aborted")
        return

    remaining = [s for s in existing_synonyms if s not in to_remove]
    artist.synonyms = ", ".join(remaining) or None
    submit_global_database_session()
    print(f"Removed {', '.join(repr(s) for s in to_remove)} from the synonyms of {artist.name}")


def remove_artists(artist_objects):
    print("About to delete these artists from the database:")
    display_artists(artist_objects)
    confirmation = ask_yes_no(f"Are you sure you want to delete {len(artist_objects)} artist(s)???")
    if not confirmation:
        print("Aborted")
        return

    deleted = 0
    for artist in artist_objects:
        try:
            delete_db_entry(artist)
        except ArtistHasSongsError as exc:
            print(f"Not deleted: {exc}. {HAS_SONGS_HINT}")
            continue
        deleted += 1

    submit_global_database_session()
    print(f"Deleted {deleted} artist(s).")


def edit_artist_menu(artist_objects):
    """The artist row itself - its name, origin, Latin form - for all its
    songs. Crediting one song to another artist is in Song actions."""
    if len(artist_objects) > 1:
        artist = pick_from_db_objects(artist_objects, question="Which artist do you want to edit?")
        if artist is None:
            return
    else:
        artist = artist_objects[0]
    edit_entry_menu(db_object=artist)


def artist_actions(artist_objects):
    action_map = {
        "Edit": lambda: edit_artist_menu(artist_objects),
        "Add synonym": lambda: add_synonym_menu(artist_objects),
        "Remove synonym": lambda: remove_synonym_menu(artist_objects),
        "Remove from the database": lambda: remove_artists(artist_objects),
    }

    execute_menu_item("What do you want to do with these artists?", action_map, exit_label="Nothing", one_time=True)
