from utils.common.output import GREEN, RED, RESET, error, review_header, say, warn
from utils.ui.cover_review import review_covers
from utils.ui.project_review import manage_projects
from utils.ui.menu_utils import execute_menu_item
from utils.common.debug import slog
from menu.main_menu.enter_database.manage_database.fetch_database_data import fetch_database_data
from menu.main_menu.enter_database.manage_database.manual_management import manual_management
from menu.main_menu.enter_database.manage_database.resolve_duplicates import (
    remove_duplicate_artists, remove_duplicate_songs, resolve_duplicated_artists)
from menu.main_menu.enter_database.manage_database.merge_divide_menu import merge_artists_menu, divide_artists_menu
from menu.main_menu.enter_database.manage_database.clean_names import clean_names
from utils.common.text_utils import check_spelling, similarity
from utils.database.database_getter import get_songs_from_db_session
from utils.ui.artist_split_review import review_artist_splits
from utils.ui.album_resolution_review import review_multi_artist_albums
from utils.ui.soundtrack_review import review_soundtrack_names
from utils.ui.inbox_request_review import resolve_new_requests
from utils.ui.request_package_import import import_request_package
from menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data import fill_missing_for_import
from menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data.fill_missing_albums import fill_missing_albums
from menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data.fill_missing_years import fill_missing_years
from utils.ui.genre_review import fill_missing_genres
from utils.ui.project_review import review_project_proposals
from utils.ui.latin_forms_review import fill_latin_forms
from utils.ui.file_fix_review import fix_file_names_and_tags
from utils.ui.song_duplicate_review import resolve_duplicated_songs
from utils.database.song_kinds import is_full_album
from utils.ui.full_album_review import review_full_albums
from utils.database.inbox_requests import count_new_requests
from utils.ui.prompts import ask_yes_no, tick_items
from utils.ui.artist_change import has_artist_twin, rename_artist
from utils.database.database_sessions import (
    DatabaseCommitError, get_global_database_sessions, submit_global_database_session)
from utils.common.normalizer import rule_apostrophe_and_common_word_diff, rule_all_caps_correction, normalize
from utils.database.tags_management import add_tag_to_song, song_ids_with_tag
from config.constants import RUN_ALL_STEP_LABELS, SPELLING_CHECK_THRESHOLD
from utils.common.run_all_settings import steps_turned_off


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

    # One query for every checked song: a tag check per song took
    # two queries each, seconds of waiting when everything was checked
    checked = song_ids_with_tag("spellchecked")
    # MusicBrainz is asked about recordings; a whole album's title isn't one
    songs = [song for song in get_songs_from_db_session()
             if song.id not in checked and not is_full_album(song)]
    if not songs:
        say("Every song has been spell checked already.")
        return
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
            # Never automatic onto a name another artist has: that's a merge
            # question, asked in the review below
            if should_auto_confirm(song.artist.name, new_artist) and not has_artist_twin(song.artist, new_artist):
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
        review_header(f"REVIEW: {len(pending_artist_corrections)} artist name(s) and {len(pending_title_corrections)} song title(s) need review")

        for correction in pending_artist_corrections.values():
            print(f"{RED}{correction['old_name']}{RESET} -> {GREEN}{correction['new_name']}{RESET}")
            if ask_yes_no("Do you wish to correct this artist name?"):
                # An artist with that name already: merge into it instead
                if not rename_artist(correction["artist"], correction["new_name"]):
                    print("Confirmed")
                    correction["artist"].name = correction["new_name"]

        for correction in pending_title_corrections:
            song = correction["song"]
            print(f"{RED}{correction['old_title']}{RESET} -> {GREEN}{correction['new_title']}{RESET}")
            if ask_yes_no("Do you wish to correct this song title?"):
                print("Confirmed")
                song.title = correction["new_title"]

        submit_global_database_session()
        

def _duplicates_after_spell_check():
    """A spelling corrected can make two artists or songs alike: the silent
    merges again (quietly - they already ran once), then the reviewed ones."""
    while remove_duplicate_songs(report_none=False) + remove_duplicate_artists(report_none=False):
        pass
    resolve_duplicated_songs()


# Every Manage database step that works on the whole catalog without being
# given a folder, a file or a pick, by its key in constants.RUN_ALL_STEP_LABELS,
# whose order "Run all" goes through:
# - the requests first: the songs they add get every later step too;
# - names cleaned and joined artists split before anything compares names,
#   then the silent duplicate merges, so spell check doesn't ask MusicBrainz
#   about one song twice; its corrections can make new duplicates, so those
#   are looked at again (and the reviewed ones asked) only after it;
# - what a song is (a full album, a project's recording, a cover) before the
#   album lookup: it skips projects' recordings, and a full album is filed
#   under its own title;
# - albums found, then their names settled (soundtracks, titles shared by
#   several artists - which also gives those songs their album rows);
# - years after albums (Years looks only at songs with an album and shares a
#   year across one), genres, and Latin forms last, for every name by then.
# Left out: imports and "Import a request package" (they need a source),
# "Fix names and tags for mp3 files ..." (a folder; it rewrites files),
# Manual management and Merge / Divide artists (the user picks the rows).
RUN_ALL_STEPS = {
    "requests": lambda: resolve_new_requests(after_import=fill_missing_for_import),
    "clean_names": clean_names,
    "split_artists": review_artist_splits,
    "duplicated_artists": resolve_duplicated_artists,
    "spell_check": check_spelling_menu,
    "duplicated_songs": _duplicates_after_spell_check,
    "full_albums": review_full_albums,
    "projects": review_project_proposals,
    "covers": review_covers,
    "albums": fill_missing_albums,
    "soundtracks": review_soundtrack_names,
    "shared_albums": review_multi_artist_albums,
    "years": fill_missing_years,
    "genres": fill_missing_genres,
    "latin_forms": fill_latin_forms,
}


def _undo_unsaved_changes():
    """What a step interrupted with Ctrl+C left unsaved is undone, so the next
    step's commit doesn't save half of it."""
    sessions = get_global_database_sessions()
    if sessions is not None:
        for session in sessions:
            session.rollback()


def run_all_steps():
    """RUN_ALL_STEPS in RUN_ALL_STEP_LABELS' order, after one checkbox list to
    leave some out this time (ticked: the steps on in Settings -> "Run all:
    steps"). Ctrl+C during a step stops it (its unsaved part undone) and asks
    whether to go on; a step whose save failed is reported, the rest still run."""
    turned_off = steps_turned_off()
    picked = tick_items("Which steps should run, in this order? (space toggles, enter confirms)",
                        list(RUN_ALL_STEP_LABELS), RUN_ALL_STEP_LABELS.get,
                        checked=lambda key: key not in turned_off)
    if not picked:
        say("Nothing picked.")
        return
    for position, key in enumerate(picked, 1):
        label = RUN_ALL_STEP_LABELS[key]
        review_header(f"STEP {position}/{len(picked)}: {label}")
        try:
            RUN_ALL_STEPS[key]()
        except DatabaseCommitError as exc:
            error(f"{exc}")
        except KeyboardInterrupt:
            _undo_unsaved_changes()
            warn(f"{label} stopped, its unsaved changes undone.")
            if position < len(picked) and not ask_yes_no("Go on with the next step?"):
                return
    say("All picked steps done.")


def manage_database():
    waiting = count_new_requests()
    action_map = {
        "Run all clean-ups and lookups in order": run_all_steps,
        # Songs added from the requests: offered their missing data right after
        f"Resolve new requests{f' ({waiting})' if waiting else ''}":
            lambda: resolve_new_requests(after_import=fill_missing_for_import),
        "Import a request package": lambda: import_request_package(after_import=fill_missing_for_import),
        "Fetch database data": fetch_database_data,
        "Manual management": manual_management,
        "Resolve duplicates": resolve_duplicates,
        "Merge artists": merge_artists_menu,
        "Divide artists": divide_artists_menu,
        "Split joined artist names": review_artist_splits,
        "Find full albums": review_full_albums,
        "Projects": manage_projects,
        "Find covers": review_covers,
        "Resolve albums with several artists": review_multi_artist_albums,
        "Resolve soundtrack album names": review_soundtrack_names,
        "Fill in Latin forms of names in other alphabets": fill_latin_forms,
        "Fix names and tags for mp3 files that are in the database": fix_file_names_and_tags,
        "Spell check existing data": check_spelling_menu,
        "Clean names": clean_names
    }


    execute_menu_item("Manage database", action_map)