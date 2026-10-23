import questionary
from utils.ui.prompts import ask_yes_no
from utils.database.datatables import Song, Artist, artist_categories, song_categories
from utils.common.debug import slog
from utils.database.database_sessions import submit_global_database_session
from utils.ui.menu_utils import pick_from_db_objects, search_and_pick_db_object
from utils.database.database_management import (
    EDITABLE_ARTIST_FIELDS,
    EDITABLE_SONG_FIELDS,
    ArtistHasSongsError,
    delete_db_entry,
    edit_db_entry,
)
from utils.discoveries.import_engine import run_import_batch
from utils.database.song_artists import (
    additional_artists,
    additional_artist_role,
    add_additional_artist,
    remove_additional_artist,
    set_additional_artist_role,
    song_artist_label,
)
from config.constants import ADDITIONAL_ARTIST_ROLE_MAIN, ADDITIONAL_ARTIST_ROLE_FEAT, ALBUM_TYPE_SINGLE
from utils.database.latin_names import latin_of
from utils.ui.latin_forms_review import edit_latin_form
from utils.ui.genre_review import set_genre_of_songs
from utils.ui.song_kind_prompt import set_kind_of_songs
from utils.database.song_kinds import kind_label
from utils.ui.artist_change import change_songs_artist, pick_or_create_artist, rename_artist, swap_titles_and_artist_names
import time

HAS_SONGS_HINT = ("Credit its songs to another artist first (Song actions -> Change artist), "
                  "or merge it into another artist (Manage database -> Merge artists).")

_ROLE_CHOICES = {
    f"{ADDITIONAL_ARTIST_ROLE_MAIN} (co-artist, \"A x B\")": ADDITIONAL_ARTIST_ROLE_MAIN,
    f"{ADDITIONAL_ARTIST_ROLE_FEAT} (guest, \"A feat. B\")": ADDITIONAL_ARTIST_ROLE_FEAT,
}


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
                artist = pick_or_create_artist(name)
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

    # Only what edit_db_entry() can change; for a song the artist row's own
    # fields (name, origin, ...) are edited from Artist actions, and "artist"
    # credits the song to another artist
    editable = EDITABLE_ARTIST_FIELDS if mode == "Artist" else EDITABLE_SONG_FIELDS
    fields = [(category, getattr(db_object, attribute)) for category, attribute in editable.items()]
    artist_option = None
    if mode == "Song":
        artist_option = f"artist ({db_object.artist.name})"
    # menu label -> category edited by typing a new value
    field_options = {f"{category} ({value})": category for category, value in fields}
    displayed_list = list(field_options)
    if artist_option:
        displayed_list.insert(1, artist_option)
    swap_option = "Swap title and artist names"
    extras_option = None
    # Latin forms of names in another alphabet (utils/database/latin_names.py):
    # menu label -> the Song / Artist / Album it sets
    latin_options = {}
    if mode == "Song":
        extras = additional_artists(db_object)
        extras_label = ", ".join(f"{a.name} ({additional_artist_role(db_object, a)})" for a in extras) or "—"
        extras_option = f"additional artists ({extras_label})"
        displayed_list.append(extras_option)
        latin_options[f"title in Latin letters ({latin_of(db_object) or '—'})"] = db_object
        album = db_object.album_entry
        # A "Singles" album copies the song's own form
        if album is not None and album.type != ALBUM_TYPE_SINGLE:
            latin_options[f"album title in Latin letters ({latin_of(album) or '—'})"] = album
    else:
        latin_options[f"name in Latin letters ({latin_of(db_object) or '—'})"] = db_object
    displayed_list.extend(latin_options)
    genre_option = None
    kind_option = None
    if mode == "Song":
        genre_option = f"genre ({db_object.genre or '—'})"
        displayed_list.append(genre_option)
        kind_option = f"song or full album ({kind_label(db_object)})"
        displayed_list.append(kind_option)
        displayed_list.append(swap_option)
    delete_option = "Delete from database"
    displayed_list.append(delete_option)
    back_option = "back"
    displayed_list.append(back_option)

    choice = questionary.select("What category do you wish to edit?", choices=displayed_list).ask()
    if choice == back_option:
        return
    if choice == swap_option:
        swap_titles_and_artist_names([db_object])
        return
    if choice == artist_option:
        change_songs_artist([db_object])
        return
    if choice == extras_option:
        edit_additional_artists_menu(db_object)
        return
    if choice in latin_options:
        edit_latin_form(latin_options[choice])
        return
    if choice == genre_option:
        set_genre_of_songs([db_object])
        return
    if choice == kind_option:
        set_kind_of_songs([db_object])
        return
    if choice == delete_option:
        label = db_object.name if mode == "Artist" else f"{song_artist_label(db_object)} - {db_object.title}"
        confirmation = ask_yes_no(f"Do you really want to delete {label}?")
        if not confirmation:
            print("Aborted")
            return
        try:
            delete_db_entry(db_object)
        except ArtistHasSongsError as exc:
            print(f"Not deleted: {exc}. {HAS_SONGS_HINT}")
            return
        submit_global_database_session()
        print(f"Deleted {label}")
        return
    category = field_options.get(choice)
    if category is None:
        return
    label = db_object.name if mode == "Artist" else f"{db_object.artist.name} - {db_object.title}"
    new_data = input(f"Type {category} for {label}: ")
    if not new_data:
        print("Aborted")
        return
    if category == artist_categories[0] and rename_artist(db_object, new_data.strip()):
        return

    slog(db_object)
    if isinstance(db_object, Song):
        slog(db_object.artist.name)
        slog(db_object.title)
        slog(db_object.artist.id)
    edit_db_entry(db_object, category, new_data)
    submit_global_database_session()
    renamed = {song_categories[0]: db_object, artist_categories[0]: getattr(db_object, "artist", db_object)}.get(category)
    if renamed is not None and latin_of(renamed):
        print(f'Its Latin form "{latin_of(renamed)}" stays as it is - change it here if it no longer fits.')
            

def remove_song_menu(mode: str = None):
    db_object = search_and_pick_db_object(mode)
    if db_object is None:
        return

    label = db_object.name if mode == "Artist" else f"{db_object.artist.name} - {db_object.title}"
    confirmation = ask_yes_no(f"Do you really want to delete {label}?")
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

def _ask_text(question: str, required: bool = False, number: bool = False) -> str | None:
    """Typed text, stripped; None = cancelled (Ctrl+C / Esc)."""
    def validate(value: str):
        value = value.strip()
        if required and not value:
            return "Can't be blank"
        if number and value and not (value.isascii() and value.isdigit()):
            return "Type a whole number"
        return True
    answer = questionary.text(question, validate=validate).ask()
    return None if answer is None else answer.strip()


def add_songs_menu():
    """Add one song by hand. Title and artist are required, the rest
    optional; it goes through the import like every other source
    (run_import_batch(): spelling, an existing artist or a similar song
    found, the album row)."""
    print("Add a song by hand (Ctrl+C cancels).")
    metadata = {}
    for key, question, required, number in (
        ("title", "Title:", True, False),
        ("artist_name", "Artist:", True, False),
        ("album", "Album (optional):", False, False),
        ("year", "Year (optional):", False, True),
        ("language", "Language (optional):", False, False),
        ("origin", "Artist's origin (optional):", False, False),
    ):
        answer = _ask_text(question, required, number)
        if answer is None:
            print("Aborted")
            return
        # every key present: the import reads metadata["year"]
        metadata[key] = (int(answer) if number else answer) if answer else None
    run_import_batch([metadata])
