from utils.database.database_getter import get_songs_from_db_session
from utils.common.text_utils import is_blacklisted_album
import time
import questionary
from utils.database.database_management import edit_db_entry
from utils.database.database_sessions import submit_global_database_session
from utils.database.tags_management import add_tag_to_song, has_tag_on_song
from utils.common.text_utils import copy_to_clipboard
from utils.database.datatables import song_categories, artist_categories
from utils.common.debug import slog
import re
from utils.common.normalizer import strip_brackets


_CLEANUP_FIELDS = [
    ("title",  lambda song: song.title,        lambda song, v: setattr(song, "title", v)),
    ("artist", lambda song: song.artist.name,  lambda song, v: setattr(song.artist, "name", v)),
    ("album",  lambda song: song.album,        lambda song, v: setattr(song, "album", v)),
]

def _apply_field_cleanup(songs, needs_transform, transform, action_description):
    """
    Apply `transform` to each song's title/artist name/album whenever
    `needs_transform` matches the current value, logging which fields changed.
    Album is skipped when empty/None; title and artist name are always present.
    """
    for song in songs:
        changed_fields = []
        for field_name, get_value, set_value in _CLEANUP_FIELDS:
            value = get_value(song)
            if value and needs_transform(value):
                new_value = transform(value)
                set_value(song, new_value)
                changed_fields.append(f"{field_name}: '{new_value}'")

        if changed_fields:
            print(f"{action_description} \033[93m{', '.join(changed_fields)}\033[0m for \033[93m{song.artist.name} - {song.title}\033[0m")

def convert_characters_encoding(songs):
    _apply_field_cleanup(
        songs,
        needs_transform=lambda v: "&amp;" in v,
        transform=lambda v: v.replace("&amp;", "&"),
        action_description="Converted",
    )

def strip_leading_spaces(songs):
    _apply_field_cleanup(
        songs,
        needs_transform=lambda v: v.startswith(" "),
        transform=lambda v: v.lstrip(),
        action_description="Stripped leading spaces in",
    )

def replace_double_spaces(songs):
    _apply_field_cleanup(
        songs,
        needs_transform=lambda v: "  " in v,
        transform=lambda v: re.sub(r" {2,}", " ", v),
        action_description="Replaced double spaces in",
    )

def seek_nonsense_names(songs):
    # Blacklisted album/title values used to ask "do you wish to edit it?" right
    # away, per song. Now the scan just tags and queues them, and the questions
    # (plus the free-text replacement each "yes" needs) are asked in one batch
    # once every song has been scanned - same pattern as the import pipeline and
    # the spell-check menu.
    pending_rubbish_corrections = []
    for song in songs:
        if not has_tag_on_song(song, "album_checked"):
            if is_blacklisted_album(song.album):
                pending_rubbish_corrections.append({
                    "song": song,
                    "field": "album",
                    "old_value": song.album,
                })
            if is_blacklisted_album(song.title):
                pending_rubbish_corrections.append({
                    "song": song,
                    "field": "title",
                    "old_value": song.title,
                })
            add_tag_to_song(song, "album_checked")

    if not pending_rubbish_corrections:
        return

    print("\n" + "="*70)
    print(f"REVIEW: {len(pending_rubbish_corrections)} field(s) look like rubbish and need your review")
    print("="*70)
    for correction in pending_rubbish_corrections:
        song = correction["song"]
        field = correction["field"]
        old_value = correction["old_value"]

        copy_to_clipboard(f"{song.artist.name} - {song.title}")
        confirmation = questionary.confirm(f"'{old_value}' seems like rubish. Do you wish to edit it? (The song is '{song.artist.name} - {song.title}')").ask()
        if not confirmation:
            continue

        if field == "album":
            new_name = input("Enter new album name: ")
            edit_db_entry(song, "album", new_name)
        else:
            new_name = input("Enter new title: ")
            if new_name == "":
                new_name = strip_brackets(old_value)
                print(new_name)
            print(new_name)
            if new_name:
                edit_db_entry(song, "title", new_name)
            else:
                print("Can't delete title name entirely")

def resolve_unknown_artist(songs):
        # Editing an unknown-artist/title split is unconditional (there's no
        # yes/no decision to defer) - the per-song "Press anything to continue"
        # was only there to pause and show what changed, one song at a time.
        # Apply edits during the scan as before, but collect what changed and
        # print it as a single summary at the end instead of blocking per song.
        changes = []
        for song in songs:
            if not has_tag_on_song(song, "album_checked"):
                lowered_artist = song.artist.name.strip().lower()
                lowered_title = song.title.strip().lower()
                slog(lowered_artist)
                slog(lowered_title)
                if "unknown" in lowered_artist or not lowered_artist:
                    print(f"Went through because lowered_artist is {lowered_artist} (song is {song.artist.name} - {song.title} [song_id is {song.id}])")
                    new_artist = ""
                    new_title = ""
                    if " - " in lowered_artist:
                        new_artist, new_title = lowered_artist.split(" - ", 1)
                    elif " - " in lowered_title:
                        new_artist, new_title = lowered_title.split(" - ", 1)
                    if new_artist:
                        old_artist = song.artist.name
                        edit_db_entry(song, song_categories[1], new_artist)
                        changes.append(f"song_id {song.id}: artist '{old_artist}' -> \033[93m'{new_artist}'\033[0m")
                    if new_title:
                        old_title = song.title
                        edit_db_entry(song, song_categories[0], new_title)
                        changes.append(f"song_id {song.id}: title '{old_title}' -> \033[93m'{new_title}'\033[0m")

        if changes:
            print("\n" + "="*70)
            print(f"Resolved {len(changes)} unknown-artist field(s):")
            for change in changes:
                print(f"  {change}")
            print("="*70)



def get_rid_of_rubish_data():
    print("Looking for sus album titles . . . . . ")
    songs = get_songs_from_db_session()
    convert_characters_encoding(songs)
    strip_leading_spaces(songs)
    replace_double_spaces(songs)
    resolve_unknown_artist(songs)
    submit_global_database_session()
    seek_nonsense_names(songs)
    submit_global_database_session()


