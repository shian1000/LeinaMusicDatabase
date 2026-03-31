from library.library import get_song_from_artist_and_name, edit_song_entry
import time
from library.music_brainz_fetcher import fetch_albums_from_musicbrainz
from music_db_manager import get_music_session
import questionary

def fill_missing_albums():
    category = "album"
    querry = ""
    songs = get_song_from_artist_and_name(category, querry)

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



    time.sleep(10000)