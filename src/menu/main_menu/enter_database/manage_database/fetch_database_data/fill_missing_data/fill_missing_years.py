from collections import defaultdict
import questionary
from utils.database.database_getter import get_songs_with_album_missing_year
from utils.database.database_management import edit_db_entry
from utils.common.debug import slog
from menu.song_actions import edit_songs_menu
from utils.database.database_sessions import submit_global_database_session
from utils.common.selenium_sessions import open_global_driver, close_global_driver, ChromeDriverLaunchError
from utils.discoveries.discoveries_manager import discover_release_year, load_year_discovery_modules
from utils.common.text_utils import copy_to_clipboard
from utils.database.fetch_data_settings import load_max_songs_per_fetch

# Sentinel album value meaning "no real parent album" - see
# itunes_fetcher.py/wikipedia_fetcher.py. These songs are looked up and
# written individually by their own title rather than grouped together.
SINGLES_ALBUM_SENTINEL = "Singles"


def fill_missing_years():
    songs_objects = get_songs_with_album_missing_year()
    slog(songs_objects)

    if not songs_objects:
        print("No songs with a known album but missing year found")
        return

    max_songs = load_max_songs_per_fetch()
    if len(songs_objects) > max_songs:
        print(f"Found {len(songs_objects)} songs with missing years, processing the first {max_songs} (see Settings to change this).")
        songs_objects = songs_objects[:max_songs]

    # Group songs sharing a real album so the year is looked up once per
    # album and then written to every song in that group.
    groups = defaultdict(list)
    singles = []
    for song in songs_objects:
        if song.album == SINGLES_ALBUM_SENTINEL:
            singles.append(song)
        else:
            groups[(song.artist.name, song.album)].append(song)

    print("Preparing fetch modules . . . ")
    try:
        open_global_driver()
    except ChromeDriverLaunchError as exc:
        print(f"\033[91m{exc}\033[0m")
        return

    # (label, year_or_None, [songs]) collected here, paired by index with
    # what was queried so the "fill up manually" step can show the user
    # what to search for.
    results = []

    try:
        modules = load_year_discovery_modules()
        if not modules:
            print("No discovery modules support looking up a release year yet (see Settings -> Discovery modules).")
            return

        for (artist_name, album_name), songs in groups.items():
            print()
            print(f"Checking release year for \033[93m{artist_name} - {album_name}\033[0m")
            year = discover_release_year(artist_name, album_name, False, modules, songs[0].artist.synonyms)
            if year is None:
                print(f"Couldn't find release year for \033[93m{artist_name} - {album_name}\033[0m")
            else:
                print(f"Found year \033[93m{year}\033[0m for \033[93m{artist_name} - {album_name}\033[0m ({len(songs)} song(s))")
            results.append((f"{artist_name} - {album_name}", year, songs))

        for song in singles:
            print()
            print(f"Checking release year for single \033[93m{song.artist.name} - {song.title}\033[0m")
            year = discover_release_year(song.artist.name, song.title, True, modules, song.artist.synonyms)
            if year is None:
                print(f"Couldn't find release year for \033[93m{song.artist.name} - {song.title}\033[0m")
            else:
                print(f"Found year \033[93m{year}\033[0m for \033[93m{song.artist.name} - {song.title}\033[0m")
            results.append((f"{song.artist.name} - {song.title}", year, [song]))
    finally:
        close_global_driver()

    touched_songs = []
    no_year_entries = []
    for label, year, songs in results:
        if year is not None:
            for song in songs:
                edit_db_entry(song, "year", str(year))
                touched_songs.append(song)
        else:
            no_year_entries.append((label, songs))

    if no_year_entries:
        total_missing_songs = sum(len(songs) for _, songs in no_year_entries)
        confirmation = questionary.confirm(
            f"Unable to find {total_missing_songs} release year(s) across {len(no_year_entries)} album/single group(s). "
            f"Would you like to fill them up manually?"
        ).ask()
        if confirmation:
            for label, songs in no_year_entries:
                print(label)
                copy_to_clipboard(label)
                year_input = input("New year (put nothing to cancel): ")
                if year_input == "":
                    print(f"Skipped {label}")
                elif not year_input.isdigit():
                    print(f"'{year_input}' is not a valid year, skipped {label}")
                else:
                    for song in songs:
                        edit_db_entry(song, "year", year_input)
                        touched_songs.append(song)

    if touched_songs:
        confirmation = questionary.confirm("Do you want to edit some of the changes manually?").ask()
        if confirmation:
            edit_songs_menu(touched_songs)

    submit_global_database_session()
