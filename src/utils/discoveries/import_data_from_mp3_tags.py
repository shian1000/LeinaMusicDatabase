from pathlib import Path

from utils.database.datatables import Song, Artist
from utils.database.database_sessions import get_global_database_sessions

from utils.common.debug import slog
from utils.discoveries.mp3_utils import extract_metadata_with_fallback, extract_unknown_data
from utils.database.database_getter import get_artists_from_db_session, get_songs_from_db_session
from utils.database.datatables import artist_categories
from utils.database.database_management import add_db_entry
from utils.common.text_utils import normalize, check_spelling, similarity
from config.constants import SPELLING_CHECK_THRESHOLD
from rich import print
import questionary

def check_artist_spelling(metadata) -> Artist:

    new_artist_name = metadata["artist_name"]

    slog(f"Couldn't find artist {new_artist_name}, trying spellcheck")
    spell_check_result = check_spelling(new_artist_name, metadata["title"])
    corrected_spelling = spell_check_result["corrected_artist"]
    similarity_percent = similarity(new_artist_name, corrected_spelling)
    if(similarity_percent > SPELLING_CHECK_THRESHOLD):
        existing_artists = get_artists_from_db_session(artist_categories[0], corrected_spelling)
        if existing_artists:
            print(f"Found similar artist [blue]{existing_artists[0].name}[/blue] but you were trying to add [green]{new_artist_name}[/green]")
            confirmation = questionary.confirm(f"Do you wish to use the artist already in the database?").ask()
            if confirmation:
                new_artist_obj = (get_artists_from_db_session(artist_categories[0], corrected_spelling))[0]
                print("Found existing artist")
                slog(new_artist_obj)
                slog(new_artist_obj.name)
                slog(new_artist_obj.id)
                return new_artist_obj
            else:
                return None
            
def does_similar_song_exists(metadata: dict, artist_obj: Artist) -> bool:

    new_artist_name = metadata["artist_name"]
    new_title = metadata["title"]
    slog(f"Couldn't find song {new_artist_name} - {new_title}, trying spellcheck")

    spell_check_result = check_spelling(new_artist_name, new_title)
    slog(spell_check_result)

    corrected_spelling = spell_check_result["corrected_title"]
    slog(corrected_spelling)

    similarity_percent = similarity(new_title, corrected_spelling)
    slog(similarity_percent)

    if(similarity_percent > SPELLING_CHECK_THRESHOLD):
        existing_artists_songs = get_songs_from_db_session(artist_categories[2], artist_obj.id)
        for ex_son in existing_artists_songs:
            if normalize(corrected_spelling) in normalize(ex_son.title):
                print(f"Found similar song [blue]{existing_artists_songs[0].title} by {existing_artists_songs[0].artist.name}[/blue] but you were trying to add [green]{new_title}[/green]")
                confirmation = questionary.confirm(f"Do you wish to use the song already in the database?").ask()
                if confirmation:
                    return True
                else:
                    return False
    else:
        return False

def resolve_artist(metadata: dict) -> Artist:

    new_artist_name = metadata["artist_name"]
    new_artist_origin = metadata["origin"]

    existing_artist = None
    existing_artists = get_artists_from_db_session(artist_categories[0], new_artist_name)
    if existing_artists:
        print("Checking the similarity between the two artists")
        similarity_percent = similarity(new_artist_name, existing_artists[0].name)
        print(f"The first is {new_artist_name}")
        print(f"The second is {existing_artists[0].name}")
        if(similarity_percent > SPELLING_CHECK_THRESHOLD):
            existing_artist = existing_artists[0]
            print(f"Artists seem the same (Similarity is {similarity_percent})")
        else:
            print(f"Artists are not the same (Similarity is {similarity_percent})")


    else:
        existing_artist = check_artist_spelling(metadata)

    new_artist_obj = None
    if not existing_artist:
        new_artist_obj = Artist()
        new_artist_obj.name = new_artist_name
        if new_artist_origin:
            new_artist_obj.origin = new_artist_origin
        add_db_entry(new_artist_obj)
    else:
        new_artist_obj = (get_artists_from_db_session(artist_categories[0], existing_artist.name))[0]
        print(f"Found existing artist ({new_artist_obj.name})")

    return new_artist_obj


def resolve_song(metadata: dict, artist_obj: Artist) -> Song:

    existing_artists_songs = get_songs_from_db_session(artist_categories[2], artist_obj.id)
    for ex_son in existing_artists_songs:
        if normalize(metadata["title"]) in normalize(ex_son.title):
            print("Yes, there is a song like this already. Skipping")
            return
    slog("Couldn't find the song, trying spellchecking")
    simlar_song_exists = does_similar_song_exists(metadata, artist_obj)
    
    if simlar_song_exists:
        print("Skipping song")
        return
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

        if metadata["artist_name"] == "Unknown Artist" or metadata["title"] == "Unknown Title":
            print("Warning - no mp3 tags data. Trying to extract data from filename")
            artist, title = extract_unknown_data(file)
            if not artist:
                print("The filename didn't have a proper separator")
            else:
                metadata["artist_name"] = artist
                metadata["title"] = title

        if metadata["artist_name"] == "Unknown Artist":
            print("Couldn't establish the artist and title")
            print("##########")
            print()
            continue

        new_artist_obj = resolve_artist(metadata)

        new_song_obj = resolve_song(metadata, new_artist_obj)

        if new_song_obj:
            added_songs.append(new_song_obj)

        added_count = added_count +1
        print("##########")
        print()

        # submit_global_database_session()

    print(f"\nDone! Added {added_count}, updated {updated_count}.")
    return added_songs
