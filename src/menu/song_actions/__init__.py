from utils.ui.menu_utils import execute_menu_item
from utils.common.debug import slog
from menu.song_actions.copy_songs_from_storage import copy_songs_from_storage
from menu.song_actions.edit_songs import edit_songs_menu
from utils.database.database_getter import extract_db_object_info
from menu.main_menu.enter_database.manage_database.merge_divide_menu import merge_artists_menu
from utils.database.datatables import artist_categories, song_categories
from utils.database.tags_management import remove_tag_from_song
from utils.youtube.manage_youtube_playlists import create_yt_playlist
from utils.ui.display_utils import display_songs

def remove_check_protection(songs_objects):
    for song in songs_objects:
        remove_tag_from_song(song, "spellchecked")
        remove_tag_from_song(song, "album_checked")

def make_yt_playlist_menu(songs_objects):
    print("About to create a playlist with these songs:")
    display_songs(songs_objects)
    playlist_name = input("Enter playlsit name:")
    create_yt_playlist(songs_objects, playlist_name)


def song_actions(songs_objects):
    slog(songs_objects)
    songs_list = extract_db_object_info(songs_objects, f"{song_categories[1]}, {song_categories[0]}")
    slog(songs_list)

    action_map = {
        "Edit": lambda: edit_songs_menu(songs_objects),
        "Copy songs from local storage": lambda: copy_songs_from_storage(songs_list),
        "Make YT playlist": lambda: make_yt_playlist_menu(songs_objects),
        "Make TXT file": lambda: print("In progress"),
        "Remove check protection": lambda: remove_check_protection(songs_objects)
    }

    slog(action_map)

    execute_menu_item("What do you want to do with these songs?", action_map, exit_label="Nothing", one_time=True)