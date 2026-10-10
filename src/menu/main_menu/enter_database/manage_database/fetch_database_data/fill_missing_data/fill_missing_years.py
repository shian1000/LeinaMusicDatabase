import random
from collections import defaultdict
import questionary
from utils.database.database_getter import get_known_album_year, get_songs_with_album_missing_year
from utils.database.database_management import edit_db_entry
from utils.common.debug import slog
from menu.song_actions import edit_songs_menu
from utils.database.database_sessions import submit_global_database_session
from utils.common.selenium_sessions import open_global_driver, close_global_driver, ChromeDriverLaunchError
from utils.discoveries.discoveries_manager import (
    discover_release_year_result,
    load_year_discovery_modules,
    release_year_from_stored_link,
)
from utils.common.text_utils import copy_to_clipboard
from utils.database.fetch_data_settings import load_max_songs_per_fetch
from config.constants import SINGLES_ALBUM, MANUAL_YEAR_SAMPLE_SONGS
from utils.common.normalizer import compare, normalize


def fill_missing_years():
    songs_objects = get_songs_with_album_missing_year()
    slog(songs_objects)

    if not songs_objects:
        print("No songs with a known album but missing year found")
        return

    # Free, offline first step: an album whose year is already on another of
    # its songs gets that year, before the per-fetch cap so it never uses
    # up a slot meant for a network lookup.
    touched_songs = _fill_years_known_in_database(songs_objects)
    songs_objects = [song for song in songs_objects if song.year is None]
    if not songs_objects:
        _finish(touched_songs)
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
        if song.album == SINGLES_ALBUM:
            singles.append(song)
        else:
            groups[(song.artist.name, song.album)].append(song)

    # A one-song "album" named after the song ("Venture" / "Venture") is a
    # single released under its own title - look it up as one, so it gets
    # the single-only lookups (YouTube, the "A x B" collab retry).
    for key, songs in list(groups.items()):
        if len(songs) == 1 and compare(songs[0].album, songs[0].title):
            singles.append(songs[0])
            del groups[key]

    print("Preparing fetch modules . . . ")
    try:
        open_global_driver()
    except ChromeDriverLaunchError as exc:
        print(f"\033[91m{exc}\033[0m")
        submit_global_database_session()  # keep the years taken from the database
        return

    # (label, year_or_None, [songs]) collected here, paired by index with
    # what was queried so the "fill up manually" step can show the user
    # what to search for.
    results = []
    # (label, year, [songs], source_url) for years a module flagged
    # needs_review (YouTube upload dates) - shown to the user before
    # anything is written.
    review_results = []

    try:
        modules = load_year_discovery_modules()
        if not modules:
            print("No discovery modules support looking up a release year yet (see Settings -> Discovery modules).")
            submit_global_database_session()  # keep the years taken from the database
            return

        for (artist_name, album_name), songs in groups.items():
            print()
            label = f"{artist_name} - {album_name}"
            print(f"Checking release year for \033[93m{label}\033[0m")
            result = (release_year_from_stored_link(songs)
                      or discover_release_year_result(artist_name, album_name, False, modules, songs[0].artist.synonyms))
            _collect(label, result, songs, results, review_results)

        for song in singles:
            print()
            label = f"{song.artist.name} - {song.title}"
            print(f"Checking release year for single \033[93m{label}\033[0m")
            result = (release_year_from_stored_link([song])
                      or discover_release_year_result(song.artist.name, song.title, True, modules, song.artist.synonyms, song=song))
            _collect(label, result, [song], results, review_results)
    finally:
        close_global_driver()

    no_year_entries = []
    for label, year, songs in results:
        if year is not None:
            for song in songs:
                edit_db_entry(song, "year", str(year))
                touched_songs.append(song)
        else:
            no_year_entries.append((label, songs))

    touched_songs += _review_unsure_years(review_results)

    if no_year_entries:
        total_missing_songs = sum(len(songs) for _, songs in no_year_entries)
        confirmation = questionary.confirm(
            f"Unable to find {total_missing_songs} release year(s) across {len(no_year_entries)} album/single group(s). "
            f"Would you like to fill them up manually?"
        ).ask()
        if confirmation:
            for label, songs in no_year_entries:
                print(label + _sample_titles(label, songs))
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

    _finish(touched_songs)


def _sample_titles(label: str, songs) -> str:
    """ " (title, title, title)" - up to MANUAL_YEAR_SAMPLE_SONGS random songs
    of an album, as a hint which album it is. Empty for a single, whose
    label already is "Artist - Title"."""
    titles = [song.title for song in songs if not label.endswith(f" - {song.title}")]
    if not titles:
        return ""
    return f" ({', '.join(random.sample(titles, min(MANUAL_YEAR_SAMPLE_SONGS, len(titles))))})"


def _finish(touched_songs) -> None:
    if touched_songs:
        confirmation = questionary.confirm("Do you want to edit some of the changes manually?").ask()
        if confirmation:
            edit_songs_menu(touched_songs)

    submit_global_database_session()


def _fill_years_known_in_database(songs) -> list:
    """Write a year already recorded on another song of the same album (see
    get_known_album_year()) to these songs. Returns the songs that got one."""
    groups = defaultdict(list)
    for song in songs:
        if song.album != SINGLES_ALBUM:
            groups[(song.artist_id, normalize(song.album))].append(song)

    touched = []
    for (artist_id, _), group in groups.items():
        year = get_known_album_year(artist_id, group[0].album)
        if year is None:
            continue
        print(f"Found year \033[93m{year}\033[0m for \033[93m{group[0].artist.name} - {group[0].album}\033[0m "
              f"in the database ({len(group)} song(s))")
        for song in group:
            edit_db_entry(song, "year", str(year))
            touched.append(song)
    return touched



def _collect(label, result, songs, results, review_results) -> None:
    if result is None:
        print(f"Couldn't find release year for \033[93m{label}\033[0m")
        results.append((label, None, songs))
    elif result.needs_review:
        print(f"Found year \033[93m{result.year}\033[0m for \033[93m{label}\033[0m (unsure, to review at the end)")
        review_results.append((label, result.year, songs, result.source_url))
    else:
        print(f"Found year \033[93m{result.year}\033[0m for \033[93m{label}\033[0m ({len(songs)} song(s))")
        results.append((label, result.year, songs))


def _review_unsure_years(review_results) -> list:
    """List every year a module flagged needs_review (a YouTube upload date
    is often not the release date) and let the user tick the ones to change.
    Unticked ones are written; ticked ones ask for a new year. Returns the
    songs that got a year."""
    if not review_results:
        return []

    print()
    print("=" * 70)
    print(f"REVIEW: {len(review_results)} unsure release year(s), taken from a YouTube video's date")
    print("=" * 70)
    choices = [
        questionary.Choice(title=f"{label}: {year}" + (f"  ({url})" if url else ""), value=i)
        for i, (label, year, _, url) in enumerate(review_results)
    ]
    selected = questionary.checkbox(
        "Select the years you want to change (space toggles, enter confirms):", choices=choices
    ).ask()
    if selected is None:
        print("Aborted, none of these years saved.")
        return []

    touched = []
    for i, (label, year, songs, _) in enumerate(review_results):
        if i in selected:
            print(f"{label} (found {year})")
            copy_to_clipboard(label)
            year_input = input("New year (put nothing to leave it empty): ")
            if year_input == "":
                print(f"Skipped {label}")
                continue
            if not year_input.isdigit():
                print(f"'{year_input}' is not a valid year, skipped {label}")
                continue
            year = year_input
        for song in songs:
            edit_db_entry(song, "year", str(year))
            touched.append(song)
    return touched
