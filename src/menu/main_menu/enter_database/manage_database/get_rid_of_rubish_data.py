from utils.database.database_getter import get_songs_from_db_session
from utils.database.datatables import is_blacklisted_album
import time
import questionary
from utils.database.database_management import edit_db_entry
from utils.database.database_sessions import submit_global_database_session


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
        if(is_blacklisted_album(song.album)):
            confirmation = questionary.confirm(f"'{song.album}' seems like rubish. Do you wish to edit it? (The song is '{song.artist.name} - {song.title}')").ask()
            if confirmation:
                new_name = input("Enter new album name: ")
                edit_db_entry(song, "album", new_name)

def get_rid_of_rubish_data():
    print("Looking for sus album titles . . . . . ")
    songs = get_songs_from_db_session()
    convert_characters_encoding(songs)
    strip_leading_spaces(songs)
    seek_nonsense_album_names(songs)
    submit_global_database_session()


