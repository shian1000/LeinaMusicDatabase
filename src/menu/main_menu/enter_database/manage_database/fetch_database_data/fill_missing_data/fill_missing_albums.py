from utils.database.database_getter import get_songs_from_db_session, get_songs_with_empty_category, extract_db_object_info
from utils.database.database_management import edit_db_entry
from utils.discoveries.music_brainz_fetcher import fetch_albums_from_musicbrainz
import questionary
from utils.database.database_sessions import open_database_sessions, close_database_sessions, submit_and_close_database_sessions
from utils.debug import slog, mlog
import time
from utils.display_utils import display_songs
from utils.text_utils import copy_to_clipboard

def fill_missing_albums():
    category = "album"


    sessions = open_database_sessions()
    slog(sessions)
    songs_objects = get_songs_with_empty_category(category, sessions)
    slog(songs_objects)
    songs = extract_db_object_info(songs_objects, "artist, title")
    slog(songs)

    if (songs):
        albums_search_results = fetch_albums_from_musicbrainz(songs)
    else:
        print("No missing albums found")
        return

    slog(albums_search_results)

    db_objects_with_albums_found = []

    for (artist_name, song_title) in albums_search_results.keys():
        artist_name = artist_name.strip().lower()
        song_title = song_title.strip().lower()

        match = next(
            (song for song in songs_objects
             if song.artist.name.strip().lower() == artist_name
             and song.title.strip().lower() == song_title),
            None
        )

        if match:
            db_objects_with_albums_found.append(match)

    slog(db_objects_with_albums_found)

    display_songs(db_objects_with_albums_found)

    albums_found = {k: v for k, v in albums_search_results.items() if v is not None}
    db_objects_with_no_albums = [song for song in songs_objects if not song.album]
    slog(db_objects_with_albums_found)
    slog(db_objects_with_no_albums)

    if not len(albums_search_results) == len(db_objects_with_albums_found):
        print(f"albums_search_results and matched_objects items quantity mismatch! albums_search_results = {albums_search_results}, matched_objects = {db_objects_with_albums_found}! Aborting!")
        return

    filtered_pairs = [(song, album) for song, album in zip(db_objects_with_albums_found, albums_found.values()) if album is not None]

    for song, album in filtered_pairs:
        edit_db_entry(song, "album", album)

    mlog(f"length of db_objects_with_no_albums = {len(db_objects_with_no_albums)}")

    if (len(db_objects_with_no_albums) > 0):
        confirmation = questionary.confirm(f"Unable to find {(len(db_objects_with_no_albums))} album names. Would you like to fill them up manually?").ask()

        if confirmation:
            for song in db_objects_with_no_albums:
                print(f"{song.artist.name} - {song.title}")
                copy_to_clipboard(f"{song.artist.name} - {song.title}")
                album = input("Album name: ")
                if not album == "":
                    edit_db_entry(song, "album", album)
                else:
                    print(f"Skipped {song.artist.name} - {song.title}")

    submit_and_close_database_sessions(sessions)
