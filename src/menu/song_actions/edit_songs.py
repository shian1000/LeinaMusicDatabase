import questionary
from utils.database.datatables import Song, Artist, artist_categories, song_categories
from utils.common.debug import slog
from utils.database.database_sessions import submit_global_database_session
from utils.database.database_getter import get_artists_from_db_session, get_songs_from_db_session
from utils.ui.menu_utils import pick_from_db_objects, get_list_of_properties_from_db_object, search_and_pick_db_object
from utils.database.database_management import edit_db_entry, delete_db_entry, add_db_entry
from utils.database.song_artists import (
    additional_artists,
    additional_artist_role,
    add_additional_artist,
    remove_additional_artist,
    set_additional_artist_role,
    song_artist_label,
)
from config.constants import ADDITIONAL_ARTIST_ROLE_MAIN, ADDITIONAL_ARTIST_ROLE_FEAT
import time

def swap_title_and_artist(song: Song):
    print(f"About to swap title and artist name for \"{song.artist.name} - {song.title}\":")
    confirmation = questionary.confirm(f"Swap \"{song.title}\" and \"{song.artist.name}\"?").ask()
    if not confirmation:
        print("Aborted")
        return

    artist_name = song.artist.name
    title = song.title
    song.artist.name = title
    song.title = artist_name
    submit_global_database_session()
    print(f"Swapped title and artist name. Now: \"{song.artist.name} - {song.title}\".")


_ROLE_CHOICES = {
    f"{ADDITIONAL_ARTIST_ROLE_MAIN} (co-artist, \"A x B\")": ADDITIONAL_ARTIST_ROLE_MAIN,
    f"{ADDITIONAL_ARTIST_ROLE_FEAT} (guest, \"A feat. B\")": ADDITIONAL_ARTIST_ROLE_FEAT,
}


def _pick_or_create_artist(name: str) -> Artist | None:
    existing = get_artists_from_db_session(artist_categories[0], name)
    if existing:
        picked = pick_from_db_objects(existing, question="Pick the artist", back_label="Add new")
        if picked:
            return picked
    if not questionary.confirm(f"Add new artist '{name}' to the database?").ask():
        return None
    return add_db_entry(Artist(name=name))


def _pick_additional_artist(song: Song, question: str) -> Artist | None:
    extras = additional_artists(song)
    back = "Back"
    # id in the label: two extra artists may share a name, and choices must be unique
    labels = {f"{a.name} [{a.id}] ({additional_artist_role(song, a)})": a for a in extras}
    choice = questionary.select(question, choices=[*labels, back]).ask()
    return labels.get(choice)


def edit_additional_artists_menu(song: Song):
    """Add/remove/re-role the extra artists of a multi-artist song. The
    primary artist (songs.artist_id) is edited via the "artist name" entry."""
    add_option, remove_option, role_option, back_option = "Add artist", "Remove artist", "Change role", "Back"
    while True:
        print(f"Artists: {song_artist_label(song)} - {song.title}")
        choices = [add_option]
        if additional_artists(song):
            choices += [remove_option, role_option]
        choices.append(back_option)
        choice = questionary.select("What do you wish to do?", choices=choices).ask()
        if choice in (None, back_option):
            return

        try:
            if choice == add_option:
                name = input("Type the name of the artist to add: ").strip()
                if not name:
                    continue
                artist = _pick_or_create_artist(name)
                if artist is None:
                    continue
                role_label = questionary.select("Role of this artist:", choices=list(_ROLE_CHOICES)).ask()
                if role_label is None:
                    continue
                add_additional_artist(song, artist, _ROLE_CHOICES[role_label])
            elif choice == remove_option:
                artist = _pick_additional_artist(song, "Which artist do you wish to remove?")
                if artist is None:
                    continue
                remove_additional_artist(song, artist)
            elif choice == role_option:
                artist = _pick_additional_artist(song, "Which artist's role do you wish to change?")
                if artist is None:
                    continue
                current = additional_artist_role(song, artist)
                new_role = ADDITIONAL_ARTIST_ROLE_FEAT if current == ADDITIONAL_ARTIST_ROLE_MAIN else ADDITIONAL_ARTIST_ROLE_MAIN
                set_additional_artist_role(song, artist, new_role)
        except ValueError as e:
            print(e)
            continue

        submit_global_database_session()
        print(f"Now: {song_artist_label(song)} - {song.title}")


def edit_entry_menu(mode: str = None, db_object = None):
    if db_object is None:
        if mode not in ("Artist", "Song"):
            choice = questionary.select("Do you wish to make changes in artist or song?", choices=["Artist", "Song", "Back"]).ask()
            if choice == "Back":
                return
            edit_entry_menu(choice)
            return

        db_object = search_and_pick_db_object(mode)
        if db_object is None:
            return
    else:
        mode = "Artist" if isinstance(db_object, Artist) else "Song"

    action_map = artist_categories if mode == "Artist" else song_categories


    properties_list = get_list_of_properties_from_db_object(db_object)
    displayed_list = [f"{menu_item} ({property})" for menu_item, property in zip(action_map, properties_list)]
    swap_option = "Swap title and artist"
    extras_option = None
    if mode == "Song":
        extras = additional_artists(db_object)
        extras_label = ", ".join(f"{a.name} ({additional_artist_role(db_object, a)})" for a in extras) or "—"
        extras_option = f"additional artists ({extras_label})"
        displayed_list.append(extras_option)
        displayed_list.append(swap_option)
    delete_option = "Delete from database"
    displayed_list.append(delete_option)
    back_option = "back"
    displayed_list.append(back_option)

    choice = questionary.select("What category do you wish to edit?", choices=displayed_list).ask()
    if choice == back_option:
        return
    if choice == swap_option:
        swap_title_and_artist(db_object)
        return
    if choice == extras_option:
        edit_additional_artists_menu(db_object)
        return
    if choice == delete_option:
        label = db_object.name if mode == "Artist" else f"{song_artist_label(db_object)} - {db_object.title}"
        confirmation = questionary.confirm(f"Do you really want to delete {label}?").ask()
        if confirmation:
            delete_db_entry(db_object)
            print(f"Deleted {label}")
        else:
            print("Aborted")
        return
    choosen_index = (displayed_list.index(choice))
    category = action_map[choosen_index]
    label = db_object.name if mode == "Artist" else f"{db_object.artist.name} - {db_object.title}"
    new_data = input(f"Type {category} for {label}: ")
    if not new_data:
        print("Aborted")
        return

    slog(db_object)
    if isinstance(db_object, Song):
        slog(db_object.artist.name)
        slog(db_object.title)
        slog(db_object.artist.id)
    edit_db_entry(db_object, category, new_data)
    submit_global_database_session()
            

def remove_song_menu(mode: str = None):
    db_object = search_and_pick_db_object(mode)
    if db_object is None:
        return

    label = db_object.name if mode == "Artist" else f"{db_object.artist.name} - {db_object.title}"
    confirmation = questionary.confirm(f"Do you really want to delete {label}?").ask()
    if confirmation:
        delete_db_entry(db_object)
        print(f"Deleted {label}")
    else:
        print("Aborted")


def edit_songs_menu(songs_objects):
    loop_running = True
    while loop_running:
        q = "Select the entity you want to edit"
        selected_song = pick_from_db_objects(songs_objects, question=q, back_label="Submit")
        if not selected_song:
            loop_running = False
            return
        else:
            slog(selected_song)
            slog(selected_song.artist.name)
            slog(selected_song.title)
            slog(selected_song.artist.id)
            edit_entry_menu(db_object=selected_song)

def add_songs_menu():
    exit_label = "/exit"
    user_input = ""
    song_labels = []
    artist_labels = []

    picked_artist = None

    for category in song_categories:
        user_input = None
        user_input = input(f"Type the {category} of the song (type '{exit_label}' to abort): ")
        if user_input == exit_label:
            break

        if(category == song_categories[0]):
            while user_input == None:
                user_input = input(f"{category} can't be blank")
            existing_songs = get_songs_from_db_session(category, user_input)
            if(existing_songs):
                print("NOTE: There are songs in the database that fully or partially match this name already: ")
                for song in existing_songs:
                    print(f"{song.artist.name} - {song.title}")

        if(category == song_categories[1]):
            while user_input == None:
                user_input = input(f"{category} can't be blank")
            existing_artists = get_artists_from_db_session(category, user_input)
            if(existing_artists):
                print("Do you want to use an existing artist?: ")
                picked_artist = pick_from_db_objects(existing_artists, back_label="Add new")
                if picked_artist:
                    user_input = None

        if category == song_categories[3]:
            while user_input and not user_input.isdigit():
                user_input = input("Type numericals only: ")
        
        song_labels.append(user_input)

    if user_input == exit_label:
        return

    if (song_labels[1]):
        artist_labels.append(song_labels[1])
    else:
        artist_labels.append("")

    if (song_labels[5]):
        artist_labels.append(song_labels[5])
    else:
        artist_labels.append("")


    print(song_labels)
    print(artist_labels)

    artist_object = Artist()
    song_object = Song()

    if picked_artist is None:
        if(artist_labels[0]):
            artist_object.name = artist_labels[0]
        if(artist_labels[1]):
            artist_object.origin = artist_labels[1]
        artist_object = add_db_entry(artist_object)
        print(artist_object)
        print(artist_object.id)
    else:
        artist_object = picked_artist
        print(artist_object)
        print(artist_object.id)

    if song_labels[0]:
        song_object.title = song_labels[0]
    song_object.artist_id = artist_object.id
    if song_labels[2]:
        song_object.album = song_labels[2]
    if song_labels[3]:
        song_object.year = int(song_labels[3])
    if song_labels[4]:
        song_object.language = song_labels[4]

    add_db_entry(song_object)

    if user_input == exit_label:
        return None