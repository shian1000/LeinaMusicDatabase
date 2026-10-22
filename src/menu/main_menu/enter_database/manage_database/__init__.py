from utils.ui.menu_utils import execute_menu_item
from utils.common.debug import slog, mlog
from menu.main_menu.enter_database.manage_database.fetch_database_data import fetch_database_data
from menu.main_menu.enter_database.manage_database.manual_management import manual_management
from menu.main_menu.enter_database.manage_database.resolve_duplicates import resolve_duplicated_artists
from menu.main_menu.enter_database.manage_database.merge_divide_menu import merge_artists_menu, divide_artists_menu
import time
from menu.main_menu.enter_database.manage_database.clean_names import clean_names
from utils.common.text_utils import check_spelling, similarity
from utils.database.database_getter import get_songs_from_db_session
from utils.ui.display_utils import display_songs
from utils.ui.artist_split_review import review_artist_splits
from utils.ui.album_resolution_review import review_multi_artist_albums
from utils.ui.inbox_request_review import resolve_new_requests
from utils.ui.request_package_import import import_request_package
from utils.ui.latin_forms_review import fill_latin_forms
from utils.ui.file_fix_review import fix_file_names_and_tags
from utils.ui.song_duplicate_review import resolve_duplicated_songs
from utils.database.song_kinds import is_full_album
from utils.ui.full_album_review import review_full_albums
from utils.database.inbox_requests import count_new_requests
import questionary
from utils.ui.prompts import ask_yes_no
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
        "Resolve duplicated songs": resolve_duplicated_songs,
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

    # Corrections that need user review aren't asked about inline anymore -
    # they're queued here and asked about in one batch after every song has
    # been checked, so the menu doesn't keep stopping for input.
    # Keyed by artist.id (not a list) because song.artist is a shared
    # SQLAlchemy object: several songs by the same artist would otherwise
    # queue the identical rename question once per song.
    pending_artist_corrections = {}
    pending_title_corrections = []

    for song in songs:
        if has_tag_on_song(song, "spellchecked"):
            continue
        # MusicBrainz is asked about recordings; a whole album's title isn't one
        if is_full_album(song):
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
            elif song.artist.id not in pending_artist_corrections:
                pending_artist_corrections[song.artist.id] = {
                    "artist": song.artist,
                    "old_name": song.artist.name,
                    "new_name": new_artist,
                }

        # Handle Title Correction
        if new_title != song.title:
            print(f"{RED}{song.title}{RESET} -> {GREEN}{new_title}{RESET}")
            print(f"song_id = {song.id}")
            if should_auto_confirm(song.title, new_title):
                print("Auto-confirmed")
                song.title = new_title
            else:
                pending_title_corrections.append({
                    "song": song,
                    "old_title": song.title,
                    "new_title": new_title,
                })

        add_tag_to_song(song, "spellchecked")
        submit_global_database_session()
    submit_global_database_session()

    # Now that every song has been checked, go through the artist/title
    # corrections that were queued along the way and ask about each one in a
    # single batch, instead of interrupting the menu every time one came up.
    if pending_artist_corrections or pending_title_corrections:
        print("\n" + "="*70)
        print(f"REVIEW: {len(pending_artist_corrections)} artist name(s) and {len(pending_title_corrections)} song title(s) need review")
        print("="*70)

        for correction in pending_artist_corrections.values():
            print(f"{RED}{correction['old_name']}{RESET} -> {GREEN}{correction['new_name']}{RESET}")
            if ask_yes_no("Do you wish to correct this artist name?"):
                print("Confirmed")
                correction["artist"].name = correction["new_name"]

        for correction in pending_title_corrections:
            song = correction["song"]
            print(f"{RED}{correction['old_title']}{RESET} -> {GREEN}{correction['new_title']}{RESET}")
            print(f"song_id = {song.id}")
            if ask_yes_no("Do you wish to correct this song title?"):
                print("Confirmed")
                song.title = correction["new_title"]

        submit_global_database_session()
        

def manage_database():
    waiting = count_new_requests()
    action_map = {
        f"Resolve new requests{f' ({waiting})' if waiting else ''}": resolve_new_requests,
        "Import a request package": import_request_package,
        "Fetch database data": fetch_database_data,
        "Manual management": manual_management,
        "Resolve duplicates": resolve_duplicates,
        "Merge artists": merge_artists_menu,
        "Divide artists": divide_artists_menu,
        "Split joined artist names": review_artist_splits,
        "Find full albums": review_full_albums,
        "Resolve albums with several artists": review_multi_artist_albums,
        "Fill in Latin forms of names in other alphabets": fill_latin_forms,
        "Fix names and tags for mp3 files that are in the database": fix_file_names_and_tags,
        "Spell check existing data": check_spelling_menu,
        "Clean names": clean_names
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map)