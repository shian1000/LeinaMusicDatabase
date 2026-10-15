"""
The questions around crediting songs to another artist and fixing a song
whose title and artist name were swapped (the work itself is
utils/database/artist_reassignment.py). Used by the song editor and Song
actions; renaming an artist row is Artist actions → Edit only.
"""

import questionary

from config.constants import ADDITIONAL_ARTIST_ROLE_FEAT, ADDITIONAL_ARTIST_ROLE_MAIN
from utils.database.artist_reassignment import (
    artists_named,
    is_unused,
    move_swapped_song,
    remove_unused_artist,
    set_primary_artist,
    swap_names_of_artist,
)
from utils.database.database_getter import get_artists_from_db_session, get_global_database_sessions
from utils.database.database_management import add_db_entry, merge_artists_in_db
from utils.database.database_sessions import submit_global_database_session
from utils.database.datatables import Artist, Song, artist_categories
from utils.database.song_artists import additional_artist_role, count_artist_songs, song_artist_label
from utils.ui.display_utils import display_songs
from utils.ui.menu_utils import pick_from_db_objects

# How many of an artist's other songs to list before asking about them
_OTHER_SONGS_SHOWN = 10


def pick_or_create_artist(name: str) -> Artist | None:
    """Artists found by `name` to pick from, else (or on "Add new") a new
    one after a confirm. None = cancelled."""
    existing = get_artists_from_db_session(artist_categories[0], name)
    if existing:
        picked = pick_from_db_objects(existing, question="Pick the artist", back_label="Add new")
        if picked:
            return picked
    if not questionary.confirm(f"Add new artist '{name}' to the database?").ask():
        return None
    return add_db_entry(Artist(name=name))


def _ask_old_artist_role(song: Song, new: Artist) -> str | None | bool:
    """`new` already is an additional artist of the song: what does the old
    primary artist become? A role, None (dropped from the song), or False
    when the user cancels."""
    old = song.artist
    print(f'"{new.name}" is already credited on "{song_artist_label(song)} - {song.title}" '
          f'({additional_artist_role(song, new)}).')
    choices = {
        f'"{old.name}" stays as a main co-artist ("{new.name} x {old.name}")': ADDITIONAL_ARTIST_ROLE_MAIN,
        f'"{old.name}" stays as a guest ("{new.name} feat. {old.name}")': ADDITIONAL_ARTIST_ROLE_FEAT,
        f'Remove "{old.name}" from this song': None,
    }
    skip = "Skip this song"
    choice = questionary.select(f'What should "{old.name}" be on this song?', choices=[*choices, skip]).ask()
    if choice in (None, skip):
        return False
    return choices[choice]


def _offer_removing_emptied(session, emptied: list[Artist], heir: Artist) -> None:
    """Ask what to do with the artists the songs left, now without a song."""
    names = ", ".join(f'"{a.name}"' for a in emptied)
    print(f"{names} {'has' if len(emptied) == 1 else 'have'} no songs left.")
    merge = f'Merge into "{heir.name}" (old names kept as its synonyms)'
    delete = "Delete"
    keep = "Keep"
    choice = questionary.select("What do you want to do with it?" if len(emptied) == 1
                                else "What do you want to do with them?",
                                choices=[merge, delete, keep]).ask()
    if choice not in (merge, delete):
        return
    for artist in emptied:
        remove_unused_artist(session, artist, heir, keep_names=choice == merge)
    print(f"{'Merged' if choice == merge else 'Deleted'} {names}.")


def change_songs_artist(songs: list[Song]) -> None:
    """Credit the songs to another artist (found by name or added), without
    renaming any artist row."""
    if not songs:
        return
    name = input("Type the name of the artist to credit the song(s) to: ").strip()
    if not name:
        print("Aborted")
        return
    target = pick_or_create_artist(name)
    if target is None:
        print("Aborted")
        return

    to_move = [s for s in songs if s.artist_id != target.id]
    for song in songs:
        if song not in to_move:
            print(f'Skipped "{song.title}": it already is by "{target.name}".')
    if not to_move:
        return
    if len(to_move) > 1:
        print(f'About to credit these songs to "{target.name}":')
        display_songs(to_move)
        if not questionary.confirm(f'Credit {len(to_move)} song(s) to "{target.name}"?').ask():
            print("Aborted")
            return

    session, _ = get_global_database_sessions()
    old_artists = []
    moved = 0
    for song in to_move:
        keep_old_as = None
        if additional_artist_role(song, target):
            keep_old_as = _ask_old_artist_role(song, target)
            if keep_old_as is False:
                continue
        old = song.artist
        set_primary_artist(session, song, target, keep_old_as)
        if old not in old_artists:
            old_artists.append(old)
        moved += 1
        print(f'Now: {song_artist_label(song)} - {song.title}')

    emptied = [a for a in old_artists if is_unused(session, a)]
    if emptied:
        _offer_removing_emptied(session, emptied, target)
    submit_global_database_session()
    print(f'Credited {moved} song(s) to "{target.name}".')


def _pick_twin(twins: list[Artist]) -> Artist:
    if len(twins) == 1:
        return twins[0]
    return pick_from_db_objects(twins, question="Which of these artists?") or twins[0]


def _swap_one(session, song: Song) -> bool | str:
    """Swap one song's title and artist name, asking when other songs share
    the artist or an artist named like the title exists. Returns "renamed"
    when the artist row was renamed (for all its songs), True when the song
    was fixed otherwise, False when skipped."""
    old = song.artist
    label = f'"{old.name} - {song.title}"'
    others = count_artist_songs(session, old.id) - 1
    twins = artists_named(session, song.title, exclude=old)
    skip = "Skip"

    if others == 0 and not twins:
        swap_names_of_artist(song)
        return "renamed"

    if others == 0:
        twin = _pick_twin(twins)
        move = f'Credit it to the existing "{twin.name}" ("{old.name}" is deleted)'
        rename = f'Swap the names anyway (two artists called "{twin.name}")'
        print(f'An artist called "{twin.name}" already exists.')
        choice = questionary.select(f"Swap title and artist of {label}:", choices=[move, rename, skip]).ask()
        if choice == move:
            move_swapped_song(session, song, twin)
            return True
        if choice == rename:
            swap_names_of_artist(song)
            return "renamed"
        return False

    other_titles = [s.title for s in old.songs if s is not song]
    shown = ", ".join(f'"{t}"' for t in other_titles[:_OTHER_SONGS_SHOWN])
    more = f" and {others - _OTHER_SONGS_SHOWN} more" if others > _OTHER_SONGS_SHOWN else ""
    print(f'"{old.name}" has {others} other song(s): {shown or "(as an additional artist)"}{more}.')
    twin = _pick_twin(twins) if twins else None
    rename = f'Rename "{old.name}" to "{song.title}" for all its songs'
    if twin:
        rename += f' (another artist is already called "{twin.name}")'
    split = (f'Move only this song to the existing "{twin.name}"' if twin
             else f'Move only this song to a new artist "{song.title}"')
    choice = questionary.select(f"Swap title and artist of {label}:", choices=[split, rename, skip]).ask()
    if choice == split:
        move_swapped_song(session, song, twin)
        return True
    if choice == rename:
        swap_names_of_artist(song)
        return "renamed"
    return False


def swap_titles_and_artist_names(songs: list[Song]) -> None:
    """Fix songs stored as "Title - Artist". An artist with only this song
    is simply renamed; when it has other songs the user picks whether they
    get the new name too or the song moves to its own artist."""
    if not songs:
        return
    if len(songs) == 1:
        song = songs[0]
        confirmed = questionary.confirm(f'Swap "{song.title}" and "{song.artist.name}"?').ask()
    else:
        print("About to swap the title and artist name of these songs:")
        display_songs(songs)
        confirmed = questionary.confirm(f"Swap title and artist name of {len(songs)} song(s)?").ask()
    if not confirmed:
        print("Aborted")
        return

    session, _ = get_global_database_sessions()
    renamed_artist_ids = set()
    swapped = 0
    for song in songs:
        # A renamed artist row already got the swapped name; doing it again
        # for its next selected song would swap it back
        if song.artist_id in renamed_artist_ids:
            print(f'Skipped "{song.artist.name} - {song.title}": artist "{song.artist.name}" '
                  "was already renamed for another selected song.")
            continue
        artist_id = song.artist_id
        result = _swap_one(session, song)
        if not result:
            continue
        if result == "renamed":
            renamed_artist_ids.add(artist_id)
        swapped += 1
        session.flush()
        print(f'Now: "{song_artist_label(song)} - {song.title}"')

    submit_global_database_session()
    print(f"Swapped title and artist name of {swapped} song(s).")


def rename_artist(artist: Artist, new_name: str) -> bool:
    """Called before renaming `artist` to `new_name`: when another artist
    already has that name, offer merging into it instead. True = handled
    here (merged or cancelled), False = go on with the plain rename."""
    session, _ = get_global_database_sessions()
    twins = artists_named(session, new_name, exclude=artist)
    if not twins:
        return False
    twin = _pick_twin(twins)
    print(f'An artist called "{twin.name}" already exists ({count_artist_songs(session, twin.id)} song(s)).')
    merge = f'Merge "{artist.name}" into "{twin.name}"'
    rename = f'Rename anyway (two artists called "{twin.name}")'
    cancel = "Cancel"
    choice = questionary.select("What do you want to do?", choices=[merge, rename, cancel]).ask()
    if choice == rename:
        return False
    if choice == merge:
        merge_artists_in_db(artist, twin)
        submit_global_database_session()
    else:
        print("Aborted")
    return True
