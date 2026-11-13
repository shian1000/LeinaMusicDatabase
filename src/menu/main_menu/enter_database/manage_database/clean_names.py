from utils.common.output import RESET, YELLOW, review_header
from utils.database.database_getter import get_songs_from_db_session
from utils.common.text_utils import is_blacklisted_album
from utils.ui.prompts import ask_yes_no, tick_items
from utils.database.song_artists import song_artist_label
from utils.database.artist_reassignment import credit_unknown_artist_song, split_title_artists
from utils.database.database_management import edit_db_entry
from utils.database.database_sessions import require_global_database_sessions, submit_global_database_session
from utils.database.tags_management import add_tag_to_song, song_ids_with_tag
from utils.common.text_utils import copy_to_clipboard
from utils.common.sanitize import FEAT_TO_CREDITS, SOURCE_STORED, STEPS, is_enabled
from config.constants import SANITIZE_SWITCH_LABELS
from utils.database import name_cleanup
from utils.database.datatables import Artist
from utils.common.normalizer import strip_brackets
from utils.ui.artist_change import review_held_artist_renames
from utils.ui.artist_split_review import review_artist_splits


def _print_changes(changes, action_description):
    """One line per song (or album row) a pass changed."""
    for change in changes:
        fields = f"{YELLOW}{', '.join(change.fields)}{RESET}"
        if change.song is None:
            print(f"{action_description} {fields}")
        else:
            print(f"{action_description} {fields} for {YELLOW}{change.song.artist.name} - {change.song.title}{RESET}")


def _apply_step(songs, step_name, held=None):
    """sanitize's "stored" step `step_name` on every song."""
    label = SANITIZE_SWITCH_LABELS[STEPS[step_name].switch][1]
    _print_changes(name_cleanup.apply_step(songs, step_name, held), f"{label} -")


def repair_polish_mojibake(songs, held=None):
    """Polish names imported before the mp3 import repaired them
    (name_cleanup.repair_polish_mojibake()), nothing asked."""
    session, _ = require_global_database_sessions()
    _print_changes(name_cleanup.repair_polish_mojibake(session, songs, held), "Repaired Polish letters in")


def compose_decomposed_letters(held=None):
    """Names stored with decomposed letters composed, in every column
    (name_cleanup.compose_decomposed_letters()). Looks the same, so nothing
    is asked."""
    if not is_enabled("nfc", SOURCE_STORED):
        return
    session, _ = require_global_database_sessions()
    changes = name_cleanup.compose_decomposed_letters(session, held)
    if changes:
        print(f"Composed accented letters in {len(changes)} name(s):")
        for change in changes:
            print(f"  {YELLOW}{change}{RESET}")


# The review steps (sanitize MODE_REVIEW) Clean names asks about in one list each,
# with the list's header: what the names have
REVIEW_STEPS = {
    # "Вjörk", "Рiдна" in titles, artist names and albums
    "lookalike_letters": "mix Latin and Cyrillic look-alike letters",
    # "Sam (prod. Gibbs)" -> "Sam", in titles and albums
    "producer_credit": "carry a producer credit",
}


def review_step(songs, step_name: str, held=None):
    """A review step's fixes in one checkbox list (all ticked), applied after
    the scan. One entry per artist and per album text, however many songs
    share it. An artist fix onto another artist's name is held."""
    if not is_enabled(step_name, SOURCE_STORED):
        return
    proposals = name_cleanup.review_step_proposals(songs, step_name)
    if not proposals:
        return

    review_header(f"REVIEW: {len(proposals)} name(s) {REVIEW_STEPS[step_name]}")
    def label(proposal):
        owners = proposal.songs
        where = f"{owners[0].artist.name} - {owners[0].title}" if proposal.field == "title" else f"{len(owners)} song(s)"
        return f"[{proposal.field}] {proposal.old}  ->  {proposal.new}  ({where})"
    selected = tick_items("Select the names to fix (space toggles, enter confirms):", proposals, label, checked=True)
    if selected is None:
        print("Aborted, nothing fixed.")
        return
    fixed = name_cleanup.apply_proposals(selected, held)
    print(f"Fixed {fixed} name(s).")

def move_feat_credits(songs):
    """Titles crediting featured artists ("Firestone ft. Conrad Sewell"): the
    names move to the song's featured artists, the title loses them - in one
    checkbox list, all ticked but a name that looks like more than one. The
    artists it creates are offered for splitting afterwards."""
    if not is_enabled(FEAT_TO_CREDITS, SOURCE_STORED):
        return
    session, _ = require_global_database_sessions()
    moves = name_cleanup.feat_move_proposals(session, songs)
    if not moves:
        return
    review_header(f"REVIEW: {len(moves)} title(s) credit featured artists - move them to the songs' artists",
                  "(existing) = an artist the database has; unticked = a name that looks like more than one")
    def label(move):
        names = ", ".join(f"{name} ({'existing' if move.known[name] else 'new'})" for name in move.names)
        return f"{move.song.artist.name} - {move.song.title}  ->  {move.title}  + feat. {names}"
    selected = tick_items("Select the titles to change (space toggles, enter confirms):", moves, label,
                          checked=lambda move: not move.suspicious)
    if selected is None:
        print("Aborted, nothing changed.")
        return
    created = name_cleanup.apply_feat_moves(session, selected)
    print(f"Moved the featured artists of {len(selected)} title(s), {len(created)} new artist(s).")
    if created:
        submit_global_database_session()
        review_artist_splits(created)


def seek_nonsense_names(songs):
    # Blacklisted album/title values used to ask "do you wish to edit it?" right
    # away, per song. Now the scan just tags and queues them, and the questions
    # (plus the free-text replacement each "yes" needs) are asked in one batch
    # once every song has been scanned - same pattern as the import pipeline and
    # the spell-check menu.
    pending_corrections = []
    auto_fixed_titles = []
    strip_junk = is_enabled("junk_phrases_any_bracket", SOURCE_STORED)
    for song in songs:
        if is_blacklisted_album(song.album):
            pending_corrections.append({
                "song": song,
                "field": "album",
                "old_value": song.album,
            })

        stripped_title = name_cleanup.junk_title_fix(song.title) if strip_junk else None
        if stripped_title:
            old_title = song.title
            edit_db_entry(song, "title", stripped_title)
            auto_fixed_titles.append(f"'{old_title}' -> {YELLOW}'{stripped_title}'{RESET}")

        if is_blacklisted_album(song.title):
            pending_corrections.append({
                "song": song,
                "field": "title",
                "old_value": song.title,
            })
        add_tag_to_song(song, "album_checked")

    if auto_fixed_titles:
        review_header(f"Auto-stripped junk suffixes from {len(auto_fixed_titles)} title(s):",
                      *(f"  {change}" for change in auto_fixed_titles))

    if not pending_corrections:
        return

    review_header(f"REVIEW: {len(pending_corrections)} field(s) look like junk and need your review")
    for correction in pending_corrections:
        song = correction["song"]
        field = correction["field"]
        old_value = correction["old_value"]

        copy_to_clipboard(f"{song.artist.name} - {song.title}")

        # Questionary renders its own prompt text through prompt_toolkit's
        # formatted-text machinery, which doesn't interpret raw ANSI escape
        # codes embedded in a plain string (they'd show up as literal
        # garbage instead of color) - so the field label and the
        # color-highlighted context are printed as a plain line above the
        # prompt instead of being folded into questionary's own message.
        if field == "album":
            context = f"{song.artist.name} - {song.title} (album: {YELLOW}{song.album}{RESET})"
        else:
            context = f"{song.artist.name} - {YELLOW}{song.title}{RESET}"
        print(f"\n[{field.upper()}] {context}")

        confirmation = ask_yes_no(f"'{old_value}' looks like junk. Do you wish to edit it?")
        if not confirmation:
            continue

        if field == "album":
            # Empty skips, as everywhere else; clearing the album takes a "-"
            new_name = input("Enter new album name (nothing = keep it, '-' = no album): ").strip()
            if new_name == "":
                print(f"Kept '{old_value}'")
            else:
                edit_db_entry(song, "album", "" if new_name == "-" else new_name)
        else:
            new_name = input("Enter new title: ")
            if new_name == "":
                new_name = strip_brackets(old_value)
            if new_name:
                edit_db_entry(song, "title", new_name)
            else:
                print("Can't delete title name entirely")

def resolve_unknown_artist(songs):
    """Songs by an unknown artist with an "Artist - Title" title are credited
    to that artist (artist_reassignment.credit_unknown_artist_song()); what
    changed is printed as one summary at the end."""
    _apply_with_summary(songs, credit_unknown_artist_song, "Credited {} song(s) of an unknown artist:")


def split_artists_from_titles(songs):
    """Titles written "Artists / Title" by the song's own artists, split
    (artist_reassignment.split_title_artists()); one summary at the end."""
    _apply_with_summary(songs, split_title_artists, "Took the artists out of {} title(s):")


def _apply_with_summary(songs, fix, header: str) -> None:
    """`fix(session, song)` on every song (None = left as it was), then one
    summary of the changed ones."""
    session, _ = require_global_database_sessions()
    changes = []
    for song in songs:
        old_label = f"{song.artist.name} - {song.title}"
        if fix(session, song) is not None:
            changes.append(f"song_id {song.id}: '{old_label}' -> {YELLOW}'{song_artist_label(song)}' - '{song.title}'{RESET}")
    if changes:
        review_header(header.format(len(changes)), *(f"  {change}" for change in changes))


def _load_songs():
    """All songs, with their artists loaded in one query. The artist list must
    be kept: the session holds its rows only weakly, so a discarded one is
    gone again and every song.artist becomes a query of its own."""
    songs = get_songs_from_db_session()
    music_session, _ = require_global_database_sessions()
    return songs, music_session.query(Artist).all()


def clean_names():
    print("Cleaning names . . . . . ")
    songs, _artists = _load_songs()
    # Only songs without the "album_checked" marker get the unknown-artist and
    # junk-name checks. Picked here, before the first commit: a commit
    # expires every loaded row, and reading song.id afterwards reloads each
    # song with a query of its own (~14 s for the whole catalog).
    checked_ids = song_ids_with_tag("album_checked")
    unchecked = [song for song in songs if song.id not in checked_ids]
    held = {}
    # Every value step turned on for the database (Settings -> "Name cleanup"),
    # with the record-level repairs where "nfc" sits in the steps' order
    before, after = name_cleanup.stored_step_passes()
    for step_name in before:
        _apply_step(songs, step_name, held)
    repair_polish_mojibake(songs, held)
    compose_decomposed_letters(held)
    for step_name in after:
        _apply_step(songs, step_name, held)
    resolve_unknown_artist(unchecked)
    split_artists_from_titles(songs)
    submit_global_database_session()
    # The commit expired every song: reload them all in two queries rather
    # than one per song (~20 s)
    songs, _artists = _load_songs()
    for step_name in REVIEW_STEPS:
        review_step(songs, step_name, held)
    move_feat_credits(songs)
    submit_global_database_session()
    seek_nonsense_names(unchecked)
    submit_global_database_session()
    review_held_artist_renames(held)
