import questionary
from utils.ui.prompts import ask_yes_no, choose
from utils.database.datatables import Song, Artist, artist_categories, song_categories
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
from utils.database.other_titles import other_titles, set_other_titles, split_other_titles
from config.constants import OTHER_TITLES_SEPARATOR
from utils.common.cover_names import covered_artist_name
from utils.common.project_names import song_project_name
from utils.ui.genre_review import set_genre_of_songs
from utils.ui.cover_review import set_covered_artist_of_songs, set_covered_title_of_songs
from utils.ui.version_review import set_version_of_songs
from utils.common.version_names import version_marker
from utils.ui.project_review import set_project_of_songs
from utils.ui.song_kind_prompt import set_kind_of_songs
from utils.ui.song_delete_review import merge_instead_of_deleting
from utils.database.song_kinds import kind_label
from utils.ui.artist_change import change_songs_artist, pick_or_create_artist, rename_artist, swap_titles_and_artist_names

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


def edit_other_titles(song: Song) -> None:
    """The song's other titles (utils/database/other_titles.py): other
    languages, a wrong title it's known by, a line of its lyrics."""
    current = f"{OTHER_TITLES_SEPARATOR} ".join(other_titles(song))
    typed = questionary.text(f'Other titles of "{song.title}", separated by "{OTHER_TITLES_SEPARATOR}" (empty = none):',
                             default=current).ask()
    if typed is None or typed.strip() == current:
        print("Aborted")
        return
    set_other_titles(song, split_other_titles(typed))
    submit_global_database_session()
    print(f"Other titles of {song.title}: {song.other_titles or '—'}")


def edit_entry_menu(mode: str = None, db_object = None):
    if db_object is None:
        if mode not in ("Artist", "Song"):
            choice = choose("Do you wish to make changes in artist or song?", ["Artist", "Song"])
            if choice is None:
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
    # menu label -> category edited by typing a new value
    field_options = {f"{category} ({getattr(db_object, attribute)})": category
                     for category, attribute in editable.items()}
    # menu label -> what choosing it does; the artist goes right after the title
    artist_action, actions = {}, {}
    # Latin forms of names in another alphabet (utils/database/latin_names.py):
    # menu label -> the Song / Artist / Album it sets
    latin_rows = {}
    if mode == "Song":
        artist_action[f"artist ({db_object.artist.name})"] = lambda: change_songs_artist([db_object])
        extras = additional_artists(db_object)
        extras_label = ", ".join(f"{a.name} ({additional_artist_role(db_object, a)})" for a in extras) or "—"
        actions[f"additional artists ({extras_label})"] = lambda: edit_additional_artists_menu(db_object)
        latin_rows[f"title in Latin letters ({latin_of(db_object) or '—'})"] = db_object
        actions[f"other titles ({db_object.other_titles or '—'})"] = lambda: edit_other_titles(db_object)
        album = db_object.album_entry
        # A "Singles" album copies the song's own form
        if album is not None and album.type != ALBUM_TYPE_SINGLE:
            latin_rows[f"album title in Latin letters ({latin_of(album) or '—'})"] = album
    else:
        latin_rows[f"name in Latin letters ({latin_of(db_object) or '—'})"] = db_object
    for label, row in latin_rows.items():
        actions[label] = lambda row=row: edit_latin_form(row)
    if mode == "Song":
        songs = [db_object]
        actions.update({
            f"genre ({db_object.genre or '—'})": lambda: set_genre_of_songs(songs),
            f"song or full album ({kind_label(db_object)})": lambda: set_kind_of_songs(songs),
            f"project ({song_project_name(db_object) or '—'})": lambda: set_project_of_songs(songs),
            f"cover of ({covered_artist_name(db_object) or '—'})": lambda: set_covered_artist_of_songs(songs),
            f"original title of the cover ({db_object.covered_title or '—'})":
                lambda: set_covered_title_of_songs(songs),
            f"version ({version_marker(db_object) or '—'})": lambda: set_version_of_songs(songs),
            "Swap title and artist names": lambda: swap_titles_and_artist_names(songs),
        })
    actions["Delete from database"] = lambda: _delete_entry(db_object, mode)
    actions.update(artist_action)

    fields = list(field_options)
    back_option = "back"
    choices = [*fields[:1], *artist_action, *fields[1:], *(label for label in actions if label not in artist_action),
               back_option]
    choice = questionary.select("What category do you wish to edit?", choices=choices).ask()
    if choice in actions:
        actions[choice]()
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

    edit_db_entry(db_object, category, new_data)
    submit_global_database_session()
    renamed = {song_categories[0]: db_object, artist_categories[0]: getattr(db_object, "artist", db_object)}.get(category)
    if renamed is not None and latin_of(renamed):
        print(f'Its Latin form "{latin_of(renamed)}" stays as it is - change it here if it no longer fits.')
            

def remove_song_menu(mode: str = None):
    db_object = search_and_pick_db_object(mode)
    if db_object is None:
        return
    _delete_entry(db_object, mode)


def _delete_entry(db_object, mode: str = None) -> None:
    """Delete a song or an artist after a yes; a song used in playlists or
    moods is offered merging into the song it duplicates first
    (merge_instead_of_deleting())."""
    label = db_object.name if mode == "Artist" else f"{song_artist_label(db_object)} - {db_object.title}"
    if isinstance(db_object, Song):
        remaining = merge_instead_of_deleting([db_object])
        if remaining is None:
            print("Aborted")
            return
        if not remaining:
            submit_global_database_session()
            return
    if not ask_yes_no(f"Do you really want to delete {label}?"):
        print("Aborted")
        return
    try:
        delete_db_entry(db_object)
    except ArtistHasSongsError as exc:
        print(f"Not deleted: {exc}. {HAS_SONGS_HINT}")
        return
    submit_global_database_session()
    print(f"Deleted {label}")


def edit_songs_menu(songs_objects):
    loop_running = True
    while loop_running:
        q = "Select the entity you want to edit"
        selected_song = pick_from_db_objects(songs_objects, question=q, back_label="Submit")
        if not selected_song:
            loop_running = False
            return
        else:
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
