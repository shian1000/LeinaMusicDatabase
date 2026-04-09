from utils.database.database_getter import get_songs_from_db_session, extract_song_info
from utils.database.database_management import edit_song_entry
from utils.discoveries.music_brainz_fetcher import fetch_albums_from_musicbrainz
import questionary
from test_scripts import open_database_sessions, close_database_sessions

def fill_missing_albums():
    category = "album"
    querry = ""

    sessions = open_database_sessions
    songs_objects = get_songs_from_db_session(category, querry, sessions)
    songs = extract_song_info(songs_objects, "artist, title")
    close_database_sessions(sessions)

    albums_search_results = fetch_albums_from_musicbrainz(songs)

    albums_found = {k: v for k, v in albums_search_results.items() if v is not None}
    albums_missing = {k: v for k, v in albums_search_results.items() if v is None}

    for (artist, title), album in albums_found.items():
        edit_song_entry(f"{artist} - {title}", "album", album)

    if (len(albums_missing) > 0):
        choice = questionary.select(f"Unable to find {(len(albums_missing))} album names. Would you like to fill them up manually?", choices=["Yes", "No"]).ask()

        if choice == "Yes":
            for (artist, title), album in albums_missing.items():
                print(f"{artist} - {title}")
                album = input("Album name: ")
                if not album == "":
                    edit_song_entry(f"{artist} - {title}", "album", album)
                else:
                    print(f"Skipped {artist} - {title}")