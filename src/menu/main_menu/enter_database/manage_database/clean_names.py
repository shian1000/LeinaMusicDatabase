from utils.common.output import RESET, YELLOW, review_header
from utils.database.database_getter import get_songs_from_db_session
from utils.common.text_utils import is_blacklisted_album
import questionary
from utils.ui.prompts import ask_yes_no
from utils.database.albums import rename_album
from utils.database.song_artists import song_artist_label
from utils.database.artist_reassignment import credit_unknown_artist_song, split_title_artists
from utils.database.database_management import decomposed_names, edit_db_entry
from utils.database.database_sessions import require_global_database_sessions, submit_global_database_session
from utils.database.tags_management import add_tag_to_song, song_ids_with_tag
from utils.common.text_utils import copy_to_clipboard, strip_trailing_junk_suffix
from config.constants import TITLE_JUNK_SUFFIXES
from utils.database.datatables import Album, Artist, Song
import re
from utils.common.normalizer import fix_cp1250_mojibake, fix_cp1250_mojibake_by_words, has_cp1250_mojibake, strip_brackets, strip_format_characters
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
            print(f"{action_description} {YELLOW}{', '.join(changed_fields)}{RESET} for {YELLOW}{song.artist.name} - {song.title}{RESET}")

def convert_characters_encoding(songs, held=None):
    _apply_field_cleanup(
        songs,
        needs_transform=lambda v: "&amp;" in v,
        transform=lambda v: v.replace("&amp;", "&"),
        action_description="Converted",
        held=held,
    )

def _known_words(songs) -> set[str]:
    """Every word of the titles, albums and artist names, casefolded, edge
    punctuation dropped - what a re-read word must be to count as repaired."""
    words = set()
    for song in songs:
        for _name, get, _set in _CLEANUP_FIELDS:
            for word in (get(song) or "").split():
                words.add(re.sub(r"^\W+|\W+$", "", word.casefold()))
    return words


def repair_polish_mojibake(songs, held=None):
    """Polish names imported before the mp3 import repaired them ("S³oneczna
    strona miasta" -> "Słoneczna ..."), nothing asked: fix_cp1250_mojibake()
    only acts on characters real text never has - or on every field of a
    song one field of which has one (title "¯ycie jest jak tramwaj", album
    "Mêskie Granie": the whole tag was mis-read). Album rows first, through
    rename_album(), so a repaired title meeting its correct twin merges with
    it instead of leaving an empty album row behind."""
    session, _ = require_global_database_sessions()
    misread = {song.id for song in songs
               if any(has_cp1250_mojibake(get(song)) for _name, get, _set in _CLEANUP_FIELDS)}
    known = _known_words(songs)
    albums = session.query(Album).all()
    # From the loaded songs, not album.songs: that's a query per album (~3 s).
    # Read after the query above, whose autoflush relinks the album ids
    misread_albums = {song.album_id for song in songs if song.id in misread}
    for album in albums:
        if album.id in misread_albums:
            fixed = fix_cp1250_mojibake(album.title, force=True)
        else:
            fixed = fix_cp1250_mojibake_by_words(fix_cp1250_mojibake(album.title), known)
        if fixed != album.title:
            print(f"Repaired Polish letters in {YELLOW}album: '{fixed}'{RESET}")
            rename_album(session, album, fixed)
    # The rest of a mis-read song's own fields; an artist row is shared by
    # other songs, so only its own sure sign (below) repairs its name
    for song in songs:
        if song.id not in misread:
            continue
        changed = []
        for field in ("title", "album"):
            value = getattr(song, field)
            fixed = fix_cp1250_mojibake(value, force=True)
            if fixed != value:
                setattr(song, field, fixed)
                changed.append(f"{field}: '{fixed}'")
        if changed:
            print(f"Repaired Polish letters in {YELLOW}{', '.join(changed)}{RESET} for "
                  f"{YELLOW}{song.artist.name} - {song.title}{RESET}")
    repair = lambda v: fix_cp1250_mojibake_by_words(fix_cp1250_mojibake(v), known)
    _apply_field_cleanup(
        songs,
        needs_transform=lambda v: repair(v) != v,
        transform=repair,
        action_description="Repaired Polish letters in",
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
    session, _ = require_global_database_sessions()
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
            print(f"  {YELLOW}{change}{RESET}")


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
    review_header(f"REVIEW: {len(entries)} name(s) mix Latin and Cyrillic look-alike letters")
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
    session, _ = require_global_database_sessions()
    changes = []
    for song in songs:
        old_label = f"{song.artist.name} - {song.title}"
        artist = credit_unknown_artist_song(session, song)
        if artist is not None:
            changes.append(f"song_id {song.id}: '{old_label}' -> {YELLOW}'{artist.name}' - '{song.title}'{RESET}")

    if changes:
        review_header(f"Credited {len(changes)} song(s) of an unknown artist:", *(f"  {change}" for change in changes))


def split_artists_from_titles(songs):
    """Titles written "Artists / Title" by the song's own artists, split
    (artist_reassignment.split_title_artists()); one summary at the end."""
    session, _ = require_global_database_sessions()
    changes = []
    for song in songs:
        old_label = f"{song.artist.name} - {song.title}"
        credited = split_title_artists(session, song)
        if credited is not None:
            changes.append(f"song_id {song.id}: '{old_label}' -> {YELLOW}'{song_artist_label(song)}' - '{song.title}'{RESET}")
    if changes:
        review_header(f"Took the artists out of {len(changes)} title(s):", *(f"  {change}" for change in changes))


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
    convert_characters_encoding(songs, held)
    repair_polish_mojibake(songs, held)
    compose_decomposed_letters(held)
    remove_invisible_characters(songs, held)
    strip_leading_spaces(songs, held)
    replace_double_spaces(songs, held)
    resolve_unknown_artist(unchecked)
    split_artists_from_titles(songs)
    submit_global_database_session()
    # The commit expired every song: reload them all in two queries rather
    # than one per song (~20 s)
    songs, _artists = _load_songs()
    fix_lookalike_letters(songs, held)
    submit_global_database_session()
    seek_nonsense_names(unchecked)
    submit_global_database_session()
    review_held_artist_renames(held)
