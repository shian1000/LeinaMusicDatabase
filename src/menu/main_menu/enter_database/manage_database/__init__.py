from utils.ui.menu_utils import execute_menu_item
from utils.common.debug import slog, mlog
from menu.main_menu.enter_database.manage_database.fetch_database_data import fetch_database_data
from menu.main_menu.enter_database.manage_database.manual_management import manual_management
from menu.main_menu.enter_database.manage_database.resolve_duplicates import resolve_duplicated_artists, resolve_duplicated_albums
from menu.main_menu.enter_database.manage_database.merge_divide_menu import merge_artists_menu, divide_artists_menu
import time
from menu.main_menu.enter_database.manage_database.get_rid_of_rubish_data import get_rid_of_rubish_data
from utils.common.text_utils import check_spelling, similarity
from utils.database.database_getter import get_songs_from_db_session
from utils.ui.display_utils import display_songs
import questionary
from utils.database.database_sessions import submit_global_database_session
from utils.common.normalizer import rule_apostrophe_and_common_word_diff, rule_all_caps_correction, normalize
from utils.database.tags_management import add_tag_to_song, has_tag_on_song
from config.constants import SPELLING_CHECK_THRESHOLD

RED = "\033[91m"
GREEN = "\033[92m"
RESET = "\033[0m"

AUTO_CONFIRM_RULES = [
    rule_apostrophe_and_common_word_diff,
    rule_all_caps_correction,
]

def should_auto_confirm(old_title: str, new_title: str) -> bool:
    return any(rule(old_title, new_title) for rule in AUTO_CONFIRM_RULES)


def resolve_duplicates():
    action_map = {
        "Resolve duplicated artists": resolve_duplicated_artists,
        "Resolve duplicated albums": resolve_duplicated_albums
    }

    execute_menu_item("Resolve duplicates", action_map)

_MENU_SPELLCHECK_CACHE = {}


def _find_local_spellcheck_match(song):
    """Return an existing same-artist song if a local title/artist match is already present.

    This keeps the menu logic fast: if the database already contains a close match,
    we avoid the expensive MusicBrainz request entirely. The remote site remains a
    fallback only when local evidence is missing.
    """
    artist_id = getattr(song.artist, "id", None)
    if artist_id is None:
        return None

    local_candidates = get_songs_from_db_session("artist id", artist_id)
    current_artist = normalize(song.artist.name or "")
    current_title = normalize(song.title or "")

    for candidate in local_candidates:
        if candidate.id == song.id:
            continue

        candidate_artist = normalize(getattr(candidate.artist, "name", "") or "")
        candidate_title = normalize(getattr(candidate, "title", "") or "")

        artist_sim = similarity(current_artist, candidate_artist)
        title_sim = similarity(current_title, candidate_title)

        if artist_sim >= SPELLING_CHECK_THRESHOLD and title_sim >= SPELLING_CHECK_THRESHOLD:
            return candidate

    return None


def check_spelling_menu():

    songs = get_songs_from_db_session()
    spell_check_cache = dict(_MENU_SPELLCHECK_CACHE)

    for song in songs:
        if has_tag_on_song(song, "spellchecked"):
            continue

        if local_match := _find_local_spellcheck_match(song):
            slog(f"[LOCAL MATCH] Skipping remote spell check for {song.artist.name} - {song.title} because {local_match.artist.name} - {local_match.title} already exists locally", priority=1)
            add_tag_to_song(song, "spellchecked")
            submit_global_database_session()
            continue

        cache_key = (song.artist.name, song.title)
        if cache_key in spell_check_cache:
            spell_check_results = spell_check_cache[cache_key]
            slog(f"[CACHE HIT] Using cached spell-check result for {song.artist.name} - {song.title}", priority=1)
        else:
            print(f"Checking {song.artist.name} - {song.title}")
            spell_check_results = check_spelling(song.artist.name, song.title)
            spell_check_cache[cache_key] = spell_check_results
            _MENU_SPELLCHECK_CACHE[cache_key] = spell_check_results

        new_artist = spell_check_results["corrected_artist"]
        new_title = spell_check_results["corrected_title"]

        if new_artist != song.artist.name:
            print(f"{RED}{song.artist.name}{RESET} -> {GREEN}{new_artist}{RESET}")
            if should_auto_confirm(song.artist.name, new_artist):
                print("Auto-confirmed")
                song.artist.name = new_artist
            else:
                if questionary.confirm("Do you wish to correct this artist name?").ask():
                    print("Confirmed")
                    song.artist.name = new_artist

        # Handle Title Correction
        if new_title != song.title:
            print(f"{RED}{song.title}{RESET} -> {GREEN}{new_title}{RESET}")
            print(f"song_id = {song.id}")
            if should_auto_confirm(song.title, new_title):
                print("Auto-confirmed")
                song.title = new_title
            else:
                if questionary.confirm("Do you wish to correct this song title?").ask():
                    print("Confirmed")
                    song.title = new_title

        add_tag_to_song(song, "spellchecked")
        submit_global_database_session()
    submit_global_database_session()
        

def manage_database():
    action_map = {
        "Fetch database data": fetch_database_data,
        "Manual management": manual_management,
        "Resolve duplicates": resolve_duplicates,
        "Merge artists": merge_artists_menu,
        "Divide artists": divide_artists_menu,
        "Spell check existing data": check_spelling_menu,
        "Get rid of rubish data": get_rid_of_rubish_data
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map)