import questionary
from old.scan_and_import import import_music_from_folder
from old.read_music_db import read_music_db
from old.get_songs import get_songs_by_language
from old.get_songs import get_songs_by_tag, get_all_artists, get_songs_by_artist
from old.get_songs import get_songs
from old.create_yt_playlist import create_yt_playlist
from utils.database.music_db_manager import create_music_db, rename_artist, delete_artist_and_songs, delete_song
from utils.database.tag_db_manager import create_tag_db, delete_tag
from old.import_from_txt import load_songs_from_txt
from utils.menu_utils import execute_menu_item
from src.library.library import display_songs, display_artists, display_songs_with_tags
from src.settings import settings
from utils.search_for_songs import search_for_songs
from src.navigation.edit_entry import edit_entry


def manage_database():

    action_map = {
        "Show songs library": display_songs,
        "Show artists": display_artists,
        "Show songs tags": display_songs_with_tags,
        "Search for songs": search_for_songs,
        "Edit entry": edit_entry
    }

    execute_menu_item("Manage database:,", action_map)

def manage_database_old():

    def create_playlist():
        print("Choose the name of the playlsit:")
        playlist_name = input()
        songs = load_songs_from_txt("../songs.txt")
        print(songs)
        create_yt_playlist(songs, playlist_name)

    action_map = {
        "Get artists": get_artists,
        "Get songs": get_songs_navigation,
        "Create playlist" : create_playlist,
        "Import songs": import_songs,
        "Database manager": database_manager,
    }

    choices = list(action_map.keys()) + ["Exit"]

    while True:
        choice = questionary.select("What do you want to do?", choices=choices).ask()

        if choice == "Exit":
            break

        action = action_map.get(choice)
        if action:
            action()


def get_artists():
    print("Warning - As for now this code lists only all artists")
    artists = get_all_artists()
    for artist in artists:
        print(f"- {artist.name}")

def get_songs_navigation():
    choice = questionary.select(
        "Get songs by:",
        choices=[
            "EXPERIMENTAL",
            "Artist",
            "Language",
            "Tags",
            "Back"
        ]
    ).ask()

    if choice == "EXPERIMENTAL":
        items = get_songs("Artist", "Aurora")
        for item in items:
            print(f"- {item.title}")

    elif choice == "Artist":
        artist = questionary.text("Artist:").ask()
        songs = get_songs_by_artist(artist)
        for song in songs:
            print(f"- {song.artist.name} - {song.title}")

    elif choice == "Language":
        lang = questionary.text("Language:").ask()
        songs = get_songs_by_language(lang)
        for song in songs:
            print(f"- {song.artist.name} - {song.title}")

    elif choice == "Tags":
        tags = questionary.text("Tags:").ask()
        songs = get_songs_by_tag(tags)
        print(songs)

def import_songs():
    import_music_from_folder("/home/owner/Music", mode = "update")

def database_manager():
    choice = questionary.select(
        "Database Manager:",
        choices=[
            "Music.db",
            "Tag.db",
            "Back"
        ]
    ).ask()

    if choice == "Music.db":
        database_manager_music_db()

    elif choice == "Tag.db":
        database_manager_tag_db()

def database_manager_music_db():
    choice = questionary.select(
        "Database Manager: Music.db:",
        choices=[
            "Create a new database",
            "Rename an artist",
            "Remove an artist",
            "Remove a song",
            "Back"
        ]
    ).ask()

    if choice == "Create a new database":
        create_music_db()

    elif choice == "Rename an artist":
        oldname = questionary.text("Rename which artist:").ask()
        newname = questionary.text("To what name:").ask()
        rename_artist(oldname, newname)

    elif choice == "Remove an artist":
        artist = questionary.text("Artist:").ask()
        delete_artist_and_songs(artist)

    elif choice == "Remove a song":
        artist = questionary.text("Song's artist:").ask()
        song = questionary.text("Song name:").ask()
        delete_song(song, artist)

def database_manager_tag_db():
    choice = questionary.select(
        "Database Manager: Tag.db:",
        choices=[
            "Create a new database",
            "Remove tag",
            "Back"
        ]
    ).ask()

    if choice == "Create a new database":
        create_tag_db()

    elif choice == "Remove tag":
        tag = questionary.text("Tag:").ask()
        print(type(tag)) #debug
        delete_tag(tag)