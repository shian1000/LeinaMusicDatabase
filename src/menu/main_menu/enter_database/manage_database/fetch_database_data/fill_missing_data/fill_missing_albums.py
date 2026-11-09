from utils.common.project_names import song_project_name
from utils.database.database_getter import get_songs_with_empty_category
from utils.database.database_management import edit_db_entry
from utils.ui.prompts import ask_yes_no
from menu.song_actions import edit_songs_menu
from utils.database.artist_identity import may_be_swapped
from utils.database.database_sessions import require_global_database_sessions, submit_global_database_session
from utils.common.selenium_sessions import close_global_driver
from utils.discoveries.discoveries_manager import (
    AlbumTrackCache,
    discover_album,
    discover_album_after_swap,
    load_discovery_modules,
)
from utils.common.text_utils import copy_to_clipboard
from utils.ui.album_guess_review import review_album_guesses
from utils.ui.album_swap_review import review_swapped_albums
from utils.ui.soundtrack_review import review_soundtrack_guesses
from utils.database.soundtrack_albums import guess_soundtrack_album, known_soundtrack_works
from utils.database.fetch_data_settings import load_max_songs_per_fetch
from config.constants import SINGLES_ALBUM
from utils.common.output import RESET, YELLOW, warn

def _set_track_position(song, match):
    """The song's place on the album just found, unless it has one already
    (songs.track_number / disc_number, migration 0007)."""
    if match is None or match.track_number is None or getattr(song, "track_number", None) is not None:
        return
    song.track_number = match.track_number
    if match.disc_number is not None and getattr(song, "disc_number", None) is None:
        song.disc_number = match.disc_number


def fill_missing_albums(songs: list | None = None, entries: dict | None = None):
    """Albums for the songs without one - every such song, or only those of
    `songs` (an import's, filled in right after it). `entries` (song id ->
    SongEntry) is taken for the import's sake and not used yet."""
    category = "album"

    if songs is None:
        songs_objects = get_songs_with_empty_category(category)
    else:
        songs_objects = [song for song in songs if not song.album]
    # A project's recording isn't on the studio song's album the fetchers would find
    songs_objects = [song for song in songs_objects if not song_project_name(song)]

    if not songs_objects:
        print("No songs with missing albums found")
        return

    max_songs = load_max_songs_per_fetch()
    if len(songs_objects) > max_songs:
        print(f"Found {len(songs_objects)} songs with missing albums, processing the first {max_songs} (see Settings to change this).")
        songs_objects = songs_objects[:max_songs]

    songs_list = []
    # The place on the album found with each song's album, same order as songs_list
    matches = []
    # (song, AlbumMatch) found only with the song's artist and title swapped
    swapped_finds = []
    # (song, AlbumMatch) only an unsure source knew
    album_guesses = []
    # (song, album) where the song's artist may be the game or film itself
    soundtrack_guesses = []
    known_works = None  # read at the first song no module found an album for

    print("Preparing fetch modules . . . ")
    try:
        discovery_modules = load_discovery_modules()
        album_cache = AlbumTrackCache(songs_objects)

        for song in songs_objects:
            new_album = None
            match = None
            print()
            print(f"Checking for {YELLOW}{song.artist.name} - {song.title}{RESET}")
            if song.album is None or song.album == "":
                match = discover_album(song, discovery_modules, album_cache)
                if getattr(match, "needs_review", False):
                    print(f"Unsure album: {YELLOW}{match.album}{RESET}, asking about it at the end")
                    album_guesses.append((song, match))
                    songs_list.append((song.artist.name, song.title, None))
                    matches.append(None)
                    continue
                new_album = match.album if match else None
                if new_album is None:
                    print(f"Couldn't find album for {YELLOW}{song.artist.name} - {song.title}{RESET}")
                    session, _ = require_global_database_sessions()
                    swapped_match = (discover_album_after_swap(song, discovery_modules)
                                     if may_be_swapped(session, song) else None)
                    if known_works is None:
                        known_works = known_soundtrack_works(session)
                    guess = guess_soundtrack_album(song.artist.name, known_works)
                    if swapped_match:
                        print(f"Found album {YELLOW}{swapped_match.album}{RESET} with the artist and title swapped, "
                              "asking about it at the end")
                        swapped_finds.append((song, swapped_match))
                    elif guess:
                        print(f"The artist may be a soundtrack: {YELLOW}{guess}{RESET}, asking about it at the end")
                        soundtrack_guesses.append((song, guess))
                else:
                    if new_album == SINGLES_ALBUM:
                        warn(f"{song.artist.name} - {song.title}{RESET} is a single. Added to {YELLOW}{new_album}")
                    else:
                        place = f" (track {match.track_number})" if match.track_number else ""
                        print(f"Found album: {YELLOW}{new_album}{RESET}{place} for {YELLOW}{song.artist.name} - {song.title}{RESET}")
            songs_list.append((song.artist.name, song.title, new_album))
            matches.append(match)
    except KeyboardInterrupt:
        # The lookups are slow (a browser per fetcher): keep what was found
        print("\nStopped - the albums found so far are saved.")
        songs_objects = songs_objects[:len(songs_list)]
    finally:
        close_global_driver()

    for i, song in enumerate(songs_list):
        art, son, new_album = song
        if new_album is not None:
            edit_db_entry(songs_objects[i], "album", new_album)
            _set_track_position(songs_objects[i], matches[i])

    for song, match in review_album_guesses(album_guesses):
        edit_db_entry(song, "album", match.album)
        _set_track_position(song, match)
        songs_list[songs_objects.index(song)] = (song.artist.name, song.title, match.album)

    for song, match in review_swapped_albums(swapped_finds):
        edit_db_entry(song, "album", match.album)
        _set_track_position(song, match)
        i = songs_objects.index(song)
        songs_list[i] = (song.artist.name, song.title, match.album)

    for song, album in review_soundtrack_guesses(soundtrack_guesses):
        edit_db_entry(song, "album", album)
        songs_list[songs_objects.index(song)] = (song.artist.name, song.title, album)

    no_albums_count = sum(1 for art, son, alb in songs_list if alb is None)

    if (no_albums_count > 0):
        confirmation = ask_yes_no(f"Unable to find {no_albums_count} album names. Would you like to fill them up manually?")
        if confirmation:
            for i, song in enumerate(songs_list):
                art, son, new_album = song
                if new_album is None:
                    print(f"{art} - {son}")
                    copy_to_clipboard(f"{art} - {son}")
                    album = input("New album name (Put nothing to cancel): ")
                    if not album == "":
                        edit_db_entry(songs_objects[i], "album", album)
                    else:
                        print(f"Skipped {art} - {son}")

    confirmation = ask_yes_no(f"Do you want to edit some of the changes manually?")
    if confirmation:
        edit_songs_menu(songs_objects)

    submit_global_database_session()



