from utils.database.database_getter import get_songs_from_db_session
from utils.common.text_utils import is_blacklisted_album
import time
import questionary
from utils.ui.prompts import ask_yes_no
from utils.database.artist_reassignment import credit_unknown_artist_song
from utils.database.database_management import decomposed_names, edit_db_entry
from utils.database.database_sessions import get_global_database_sessions, submit_global_database_session
from utils.database.tags_management import add_tag_to_song, song_ids_with_tag
from utils.common.text_utils import copy_to_clipboard, strip_trailing_junk_suffix
from config.constants import TITLE_JUNK_SUFFIXES
from utils.database.datatables import Album, Artist, Song, artist_categories
import re
from utils.common.normalizer import strip_brackets, strip_format_characters
from utils.common.script_utils import fix_mixed_script_words
from utils.ui.artist_change import has_artist_twin, review_held_artist_renames


_CLEANUP_FIELDS = [
    ("title",  lambda song: song.title,        lambda song, v: setattr(song, "title", v)),
    ("artist", lambda song: song.artist.name,  lambda song, v: setattr(song.artist, "name", v)),
    ("album",  lambda song: song.album,        lambda song, v: setattr(song, "album", v)),
]

# Bracket-pair characters seen wrapping a junk annotation at the end of a
# title, beyond the plain "()"/"[]" pair uploaders sometimes reach for
# instead - e.g. "Lot 《Official Music Video》" (CJK angle/corner quotation
# brackets). Add new pairs here as they come up.
_TITLE_BRACKET_PAIRS = {
    "(": ")",
    "[": "]",
    "{": "}",
    "【": "】",
    "〈": "〉",
    "《": "》",
    "「": "」",
    "『": "』",
    "（": "）",
    "［": "］",
}
# DOTALL so the bracket's content can be matched across a stray newline
# (e.g. YouTube titles pasted with the closing bracket knocked onto its own
# line: "《Official Music Video\n》").
_TITLE_TRAILING_BRACKET_RE = re.compile(
    r"\s*(?:" + "|".join(
        re.escape(open_ch) + r"(.*?)" + re.escape(close_ch)
        for open_ch, close_ch in _TITLE_BRACKET_PAIRS.items()
    ) + r")\s*$",
    re.DOTALL,
)


def strip_trailing_junk_bracket(title: str) -> str:
    """Repeatedly strip a trailing bracket (any pair in _TITLE_BRACKET_PAIRS)
    off the end of `title` when its content - once whitespace, including a
    stray newline before the closing bracket, is collapsed - exactly matches
    one of TITLE_JUNK_SUFFIXES, e.g. "Lot 《Official Music Video\\n》" -> "Lot".
    Left alone (for manual review) when the bracket holds anything else, so a
    genuine subtitle in brackets is never touched.
    """
    title = (title or "").strip()
    while True:
        match = _TITLE_TRAILING_BRACKET_RE.search(title)
        if not match:
            return title
        content = next((g for g in match.groups() if g is not None), "")
        normalized_content = re.sub(r"\s+", " ", content).strip().lower()
        if normalized_content not in TITLE_JUNK_SUFFIXES:
            return title
        title = title[:match.start()].strip()


def strip_trailing_junk(title: str) -> str:
    """Strip both plain trailing junk phrases and bracket-wrapped ones off
    the end of `title`, repeating until nothing more changes (handles a
    bracketed annotation stacked after a plain one, or vice versa).
    """
    title = (title or "").strip()
    while True:
        new_title = strip_trailing_junk_suffix(title)
        new_title = strip_trailing_junk_bracket(new_title)
        if new_title == title:
            return title
        title = new_title

def _hold_artist_rename(held, artist, new_name) -> bool:
    """An artist row is shared, so it isn't renamed onto a name another
    artist already has: the rename is held in `held` (artist id -> (artist,
    new name)) for review_held_artist_renames(). True = held."""
    if held is None or (artist.id not in held and not has_artist_twin(artist, new_name)):
        return False
    held[artist.id] = (artist, new_name)
    return True


def _apply_field_cleanup(songs, needs_transform, transform, action_description, held=None):
    """
    Apply `transform` to each song's title/artist name/album whenever
    `needs_transform` matches the current value, logging which fields changed.
    Album is skipped when empty/None; title and artist name are always present.
    An artist rename onto another artist's name goes to `held` instead
    (_hold_artist_rename()); a later cleanup works on the held name.
    """
    for song in songs:
        changed_fields = []
        for field_name, get_value, set_value in _CLEANUP_FIELDS:
            value = get_value(song)
            is_artist = field_name == "artist"
            if is_artist and held and song.artist.id in held:
                value = held[song.artist.id][1]
            if value and needs_transform(value):
                new_value = transform(value)
                if is_artist and _hold_artist_rename(held, song.artist, new_value):
                    changed_fields.append(f"artist: '{new_value}' (held: another artist has that name)")
                    continue
                set_value(song, new_value)
                changed_fields.append(f"{field_name}: '{new_value}'")

        if changed_fields:
            print(f"{action_description} \033[93m{', '.join(changed_fields)}\033[0m for \033[93m{song.artist.name} - {song.title}\033[0m")

def convert_characters_encoding(songs, held=None):
    _apply_field_cleanup(
        songs,
        needs_transform=lambda v: "&amp;" in v,
        transform=lambda v: v.replace("&amp;", "&"),
        action_description="Converted",
        held=held,
    )

def strip_leading_spaces(songs, held=None):
    _apply_field_cleanup(
        songs,
        needs_transform=lambda v: v.startswith(" "),
        transform=lambda v: v.lstrip(),
        action_description="Stripped leading spaces in",
        held=held,
    )

def replace_double_spaces(songs, held=None):
    _apply_field_cleanup(
        songs,
        needs_transform=lambda v: "  " in v,
        transform=lambda v: re.sub(r" {2,}", " ", v),
        action_description="Replaced double spaces in",
        held=held,
    )

def remove_invisible_characters(songs, held=None):
    # Zero-width spaces etc. copied in from Bandcamp-style pages
    # ("Cz\u200bô\u200brny Ritm ..."); the emoji joiner is kept
    _apply_field_cleanup(
        songs,
        needs_transform=lambda v: strip_format_characters(v, keep_joiner=True) != v,
        transform=lambda v: strip_format_characters(v, keep_joiner=True),
        action_description="Removed invisible characters in",
        held=held,
    )

def compose_decomposed_letters(held=None):
    """Names stored with decomposed letters ("a" + a combining accent instead
    of "á", from YouTube titles and macOS file names) composed, in every
    column the models compose on write - albums' and artists' too, not just
    the songs' title/artist/album. Looks the same, so nothing is asked. An
    artist name onto another artist's goes to `held` (_hold_artist_rename())."""
    session, _ = get_global_database_sessions()
    changes = []
    for row, field, _old, new in decomposed_names(session):
        if isinstance(row, Artist) and field == "name" and _hold_artist_rename(held, row, new):
            changes.append(f"artist '{new}' (held: another artist has that name)")
            continue
        setattr(row, field, new)
        if isinstance(row, Song):
            changes.append(f"{field} of {row.artist.name} - {row.title}")
        elif isinstance(row, Album):
            changes.append(f"album {field}: {row.title}")
        else:
            changes.append(f"artist {field}: {row.name}" if field == "name" else f"{field} of artist {row.name}")
    if changes:
        print(f"Composed accented letters in {len(changes)} name(s):")
        for change in changes:
            print(f"  \033[93m{change}\033[0m")


def fix_lookalike_letters(songs, held=None):
    """Words mixing Latin and Cyrillic look-alike letters ("Вjörk",
    "Рiдна") in titles, artist names and albums: every fix in one checkbox
    list (all ticked), applied after the scan. One entry per artist and per
    album text, however many songs share it. An artist fix onto another
    artist's name goes to `held` (_hold_artist_rename())."""
    proposals = {}  # (field, key) -> [old, new, songs]
    for song in songs:
        for field_name, get_value, _ in _CLEANUP_FIELDS:
            value = get_value(song)
            fixed = fix_mixed_script_words(value) if value else value
            if fixed == value:
                continue
            key = {"title": song.id, "artist": song.artist.id, "album": value}[field_name]
            proposals.setdefault((field_name, key), [value, fixed, []])[2].append(song)
    if not proposals:
        return

    entries = list(proposals.items())
    print("\n" + "=" * 70)
    print(f"REVIEW: {len(entries)} name(s) mix Latin and Cyrillic look-alike letters")
    print("=" * 70)
    choices = []
    for i, ((field_name, _), (old, new, owners)) in enumerate(entries):
        where = f"{owners[0].artist.name} - {owners[0].title}" if field_name == "title" else f"{len(owners)} song(s)"
        choices.append(questionary.Choice(title=f"[{field_name}] {old}  ->  {new}  ({where})", value=i, checked=True))
    selected = questionary.checkbox(
        "Select the names to fix (space toggles, enter confirms):", choices=choices
    ).ask()
    if selected is None:
        print("Aborted, nothing fixed.")
        return
    setters = {name: set_value for name, _, set_value in _CLEANUP_FIELDS}
    fixed = 0
    for i in selected:
        (field_name, _), (old, new, owners) = entries[i]
        if field_name == "artist" and _hold_artist_rename(held, owners[0].artist, new):
            continue
        for song in owners:
            setters[field_name](song, new)
        fixed += 1
    print(f"Fixed {fixed} name(s).")

def seek_nonsense_names(songs):
    # Blacklisted album/title values used to ask "do you wish to edit it?" right
    # away, per song. Now the scan just tags and queues them, and the questions
    # (plus the free-text replacement each "yes" needs) are asked in one batch
    # once every song has been scanned - same pattern as the import pipeline and
    # the spell-check menu.
    pending_corrections = []
    auto_fixed_titles = []
    for song in songs:
        if is_blacklisted_album(song.album):
            pending_corrections.append({
                "song": song,
                "field": "album",
                "old_value": song.album,
            })

        if song.title:
            stripped_title = strip_trailing_junk(song.title)
            if stripped_title and stripped_title != song.title:
                old_title = song.title
                edit_db_entry(song, "title", stripped_title)
                auto_fixed_titles.append(f"'{old_title}' -> \033[93m'{stripped_title}'\033[0m")

        if is_blacklisted_album(song.title):
            pending_corrections.append({
                "song": song,
                "field": "title",
                "old_value": song.title,
            })
        add_tag_to_song(song, "album_checked")

    if auto_fixed_titles:
        print("\n" + "="*70)
        print(f"Auto-stripped junk suffixes from {len(auto_fixed_titles)} title(s):")
        for change in auto_fixed_titles:
            print(f"  {change}")
        print("="*70)

    if not pending_corrections:
        return

    print("\n" + "="*70)
    print(f"REVIEW: {len(pending_corrections)} field(s) look like junk and need your review")
    print("="*70)
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
            context = f"{song.artist.name} - {song.title} (album: \033[93m{song.album}\033[0m)"
        else:
            context = f"{song.artist.name} - \033[93m{song.title}\033[0m"
        print(f"\n[{field.upper()}] {context}")

        confirmation = ask_yes_no(f"'{old_value}' looks like junk. Do you wish to edit it?")
        if not confirmation:
            continue

        if field == "album":
            new_name = input("Enter new album name: ")
            edit_db_entry(song, "album", new_name)
        else:
            new_name = input("Enter new title: ")
            if new_name == "":
                new_name = strip_brackets(old_value)
                print(new_name)
            print(new_name)
            if new_name:
                edit_db_entry(song, "title", new_name)
            else:
                print("Can't delete title name entirely")

def resolve_unknown_artist(songs):
    """Songs by an unknown artist with an "Artist - Title" title are credited
    to that artist (artist_reassignment.credit_unknown_artist_song()); what
    changed is printed as one summary at the end."""
    session, _ = get_global_database_sessions()
    changes = []
    for song in songs:
        old_label = f"{song.artist.name} - {song.title}"
        artist = credit_unknown_artist_song(session, song)
        if artist is not None:
            changes.append(f"song_id {song.id}: '{old_label}' -> \033[93m'{artist.name}' - '{song.title}'\033[0m")

    if changes:
        print("\n" + "="*70)
        print(f"Credited {len(changes)} song(s) of an unknown artist:")
        for change in changes:
            print(f"  {change}")
        print("="*70)


def _load_songs():
    """All songs, with their artists loaded in one query. The artist list must
    be kept: the session holds its rows only weakly, so a discarded one is
    gone again and every song.artist becomes a query of its own."""
    songs = get_songs_from_db_session()
    music_session, _ = get_global_database_sessions()
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
    convert_characters_encoding(songs, held)
    compose_decomposed_letters(held)
    remove_invisible_characters(songs, held)
    strip_leading_spaces(songs, held)
    replace_double_spaces(songs, held)
    resolve_unknown_artist(unchecked)
    submit_global_database_session()
    # The commit expired every song: reload them all in two queries rather
    # than one per song (~20 s)
    songs, _artists = _load_songs()
    fix_lookalike_letters(songs, held)
    submit_global_database_session()
    seek_nonsense_names(unchecked)
    submit_global_database_session()
    review_held_artist_renames(held)
