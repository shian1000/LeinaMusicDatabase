from pathlib import Path

from utils.database.datatables import Song, Artist
from utils.database.database_sessions import get_global_database_sessions

from utils.common.debug import slog
from rich import print
from utils.discoveries.mp3_utils import extract_metadata_with_fallback
from utils.database.database_getter import get_artists_from_db_session, get_songs_from_db_session
from utils.database.datatables import artist_categories
from utils.database.database_management import add_db_entry
from utils.common.text_utils import normalize

def resolve_artist(metadata: dict) -> Artist:

    new_artist_name = metadata["artist_name"]
    new_artist_origin = metadata["origin"]

    existing_artist = None
    existing_artists = get_artists_from_db_session(artist_categories[0], new_artist_name)
    if existing_artists:
        existing_artist = existing_artists[0]

    new_artist_obj = None
    if not existing_artist:
        new_artist_obj = Artist()
        new_artist_obj.name = new_artist_name
        if new_artist_origin:
            new_artist_obj.origin = new_artist_origin
        print("Creating a new artist")
        add_db_entry(new_artist_obj)
    else:
        new_artist_obj = (get_artists_from_db_session(artist_categories[0], new_artist_name))[0]
        print("Found existing artist")
        print(new_artist_obj)
        print(new_artist_obj.name)
        print(new_artist_obj.id)

    return new_artist_obj


def resolve_song(metadata: dict, artist_obj: Artist) -> Song:
    
    print(metadata)

    print(f"new_song_title = {metadata["title"]}")

    existing_artists_songs = get_songs_from_db_session(artist_categories[2], artist_obj.id)
    slog(existing_artists_songs)
    for ex_son in existing_artists_songs:
        if normalize(metadata["title"]) in normalize(ex_son.title):
            print("Yes, there is a song like this already. Skipping")
            return
    print("Creating a new song")
    new_song_obj = Song()
    new_song_obj.artist_id = artist_obj.id
    new_song_obj.title = metadata["title"]
    new_song_obj.album = metadata["album"]
    new_song_obj.year = int(metadata["year"])
    new_song_obj.language = metadata["language"]
    add_db_entry(new_song_obj)

    return new_song_obj


def import_data_from_mp3_tags(folder_path: str, mode: str = "skip") -> list:
    added_songs = []

    folder = Path(folder_path).resolve()
    if not folder.exists():
        print(f"Folder {folder_path} does not exist.")
        return

    mp3_files = list(folder.rglob("*.mp3"))
    print(f"Found {len(mp3_files)} mp3 files.")

    added_count = 0
    updated_count = 0

    for file in mp3_files:
        metadata = extract_metadata_with_fallback(file)

        new_artist_obj = resolve_artist(metadata)

        new_song_obj = resolve_song(metadata, new_artist_obj)

        added_songs.append(new_song_obj)

        print(added_songs)

        # submit_global_database_session()

        input()


    print(f"\nDone! Added {added_count}, updated {updated_count}.")
    return added_songs
