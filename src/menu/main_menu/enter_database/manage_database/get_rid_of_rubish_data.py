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


def convert_characters_encoding(songs):
    wrong_characters_list = []
    for song in songs:
        converted_fields = []
        wrong_characters_list.clear()

        if "&amp;" in song.title:
            wrong_characters_list.append(song.title)
            song.title = song.title.replace("&amp;", "&")
            converted_fields.append(f"title: '{song.title}'")

        if "&amp;" in song.artist.name:
            wrong_characters_list.append(song.artist.name)
            song.artist.name = song.artist.name.replace("&amp;", "&")
            converted_fields.append(f"artist: '{song.artist.name}'")

        if song.album:
            if "&amp;" in song.album:
                wrong_characters_list.append(song.album)
                song.album = song.album.replace("&amp;", "&")
                converted_fields.append(f"album: '{song.album}'")

        if converted_fields:
            print(f"Converted \033[93m{wrong_characters_list}\033[0m for \033[93m{song.artist.name} - {song.title}\033[0m")

def strip_leading_spaces(songs):
    additive_spaces_list = []
    for song in songs:
        stripped_fields = []
        additive_spaces_list.clear()

        if song.title.startswith(" "):
            additive_spaces_list.append(song.title)
            song.title = song.title.lstrip()
            stripped_fields.append(f"title: '{song.title}'")

        if song.artist.name.startswith(" "):
            additive_spaces_list.append(song.artist.name)
            song.artist.name = song.artist.name.lstrip()
            stripped_fields.append(f"artist: '{song.artist.name}'")

        if song.album:
            if song.album.startswith(" "):
                additive_spaces_list.append(song.album)
                song.album = song.album.lstrip()
                stripped_fields.append(f"album: '{song.album}'")

        if stripped_fields:
            print(f"Stripped leading spaces \033[93m{additive_spaces_list}\033[0m for \033[93m{song.artist.name} - {song.title}\033[0m")

def seek_nonsense_album_names(songs):
    for song in songs:
        if not has_tag_on_song(song, "album_checked"):
            if(is_blacklisted_album(song.album)):
                copy_to_clipboard(f"{song.artist.name} - {song.title}")
                confirmation = questionary.confirm(f"'{song.album}' seems like rubish. Do you wish to edit it? (The song is '{song.artist.name} - {song.title}')").ask()
                if confirmation:
                    new_name = input("Enter new album name: ")
                    edit_db_entry(song, "album", new_name)
            add_tag_to_song(song, "album_checked")

def resolve_unknown_artist(songs):
        new_title = ""
        new_artist = ""
        for song in songs:
            if not has_tag_on_song(song, "album_checked"):
                lowered_artist = song.artist.name.strip().lower()
                lowered_title = song.title.strip().lower()
                slog(lowered_artist)
                slog(lowered_title)
                if "unknown" in lowered_artist or not lowered_artist:
                    print(f"Went through because lowered_artist is {lowered_artist} (song is {song.artist.name} - {song.title} [song_id is {song.id}])")
                    if " - " in lowered_artist:
                        new_artist, new_title = lowered_artist.split(" - ", 1)
                    elif " - " in lowered_title:
                        new_artist, new_title = lowered_title.split(" - ", 1)
                    if new_artist:
                        if new_artist is not "":
                            input(f"editting entry of a song (song is {song.artist.name} - {song.title} [song_id is {song.id}]). New artist name = {new_artist}. Press anything to continue")
                            edit_db_entry(song, song_categories[1], new_artist)
                    if new_title:
                        if new_title is not "":
                            edit_db_entry(song, song_categories[0], new_title)
                            input(f"editting entry of a song (song is {song.artist.name} - {song.title} [song_id is {song.id}]). New title = {new_title}. Press anything to continue")

                 



def get_rid_of_rubish_data():
    print("Looking for sus album titles . . . . . ")
    songs = get_songs_from_db_session()
    convert_characters_encoding(songs)
    strip_leading_spaces(songs)
    resolve_unknown_artist(songs)
    submit_global_database_session()
    seek_nonsense_album_names(songs)
    submit_global_database_session()


