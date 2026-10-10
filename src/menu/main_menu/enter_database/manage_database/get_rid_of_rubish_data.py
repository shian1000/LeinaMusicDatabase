from utils.database.database_getter import get_songs_from_db_session
from utils.common.text_utils import is_blacklisted_album
import time
import questionary
from utils.database.database_management import edit_db_entry
from utils.database.database_sessions import submit_global_database_session
from utils.database.tags_management import add_tag_to_song, has_tag_on_song
from utils.common.text_utils import copy_to_clipboard
from utils.database.datatables import song_categories, artist_categories
from utils.common.debug import slog
import re
from utils.common.normalizer import strip_brackets


_CLEANUP_FIELDS = [
    ("title",  lambda song: song.title,        lambda song, v: setattr(song, "title", v)),
    ("artist", lambda song: song.artist.name,  lambda song, v: setattr(song.artist, "name", v)),
    ("album",  lambda song: song.album,        lambda song, v: setattr(song, "album", v)),
]

# Trailing junk phrases that only describe how the video was uploaded, never
# part of the song's actual name (e.g. "Smallville - Save Me Music Video" ->
# "Smallville - Save Me"). Stripped from the end of the title automatically,
# without asking for confirmation, before falling back to the "seems like
# rubbish" prompt for anything that's still blacklisted afterwards. Matched
# case-insensitively against the very end of the title; add new phrases here
# as they come up. Longer phrases are listed before the shorter phrases they
# contain (e.g. "official music video" before "music video") so the whole
# annotation is stripped in one go.
TITLE_JUNK_SUFFIXES = [
    "official music video",
    "official lyric video",
    "official video",
    "official audio",
    "music video",
    "lyric video",
    "video clip",
    "videoclip",
    # Written as one word, e.g. Cody Lee's "...Green Onion(MusicVideo)"
    "musicvideo",
]
_TITLE_JUNK_SUFFIX_RE = re.compile(
    r"\s*(?:" + "|".join(re.escape(p) for p in TITLE_JUNK_SUFFIXES) + r")\s*$",
    re.IGNORECASE,
)


def strip_trailing_junk_suffix(title: str) -> str:
    """Repeatedly strip a trailing phrase from TITLE_JUNK_SUFFIXES off the
    end of `title`, handling more than one stacked suffix (e.g. "Song Music
    Video Official Audio"). Returns `title` unchanged if nothing matches.
    """
    title = (title or "").strip()
    while True:
        new_title = _TITLE_JUNK_SUFFIX_RE.sub("", title).strip()
        if new_title == title:
            return title
        title = new_title


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

def _apply_field_cleanup(songs, needs_transform, transform, action_description):
    """
    Apply `transform` to each song's title/artist name/album whenever
    `needs_transform` matches the current value, logging which fields changed.
    Album is skipped when empty/None; title and artist name are always present.
    """
    for song in songs:
        changed_fields = []
        for field_name, get_value, set_value in _CLEANUP_FIELDS:
            value = get_value(song)
            if value and needs_transform(value):
                new_value = transform(value)
                set_value(song, new_value)
                changed_fields.append(f"{field_name}: '{new_value}'")

        if changed_fields:
            print(f"{action_description} \033[93m{', '.join(changed_fields)}\033[0m for \033[93m{song.artist.name} - {song.title}\033[0m")

def convert_characters_encoding(songs):
    _apply_field_cleanup(
        songs,
        needs_transform=lambda v: "&amp;" in v,
        transform=lambda v: v.replace("&amp;", "&"),
        action_description="Converted",
    )

def strip_leading_spaces(songs):
    _apply_field_cleanup(
        songs,
        needs_transform=lambda v: v.startswith(" "),
        transform=lambda v: v.lstrip(),
        action_description="Stripped leading spaces in",
    )

def replace_double_spaces(songs):
    _apply_field_cleanup(
        songs,
        needs_transform=lambda v: "  " in v,
        transform=lambda v: re.sub(r" {2,}", " ", v),
        action_description="Replaced double spaces in",
    )

def seek_nonsense_names(songs):
    # Blacklisted album/title values used to ask "do you wish to edit it?" right
    # away, per song. Now the scan just tags and queues them, and the questions
    # (plus the free-text replacement each "yes" needs) are asked in one batch
    # once every song has been scanned - same pattern as the import pipeline and
    # the spell-check menu.
    pending_rubbish_corrections = []
    auto_fixed_titles = []
    for song in songs:
        if not has_tag_on_song(song, "album_checked"):
            if is_blacklisted_album(song.album):
                pending_rubbish_corrections.append({
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
                pending_rubbish_corrections.append({
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

    if not pending_rubbish_corrections:
        return

    print("\n" + "="*70)
    print(f"REVIEW: {len(pending_rubbish_corrections)} field(s) look like rubbish and need your review")
    print("="*70)
    for correction in pending_rubbish_corrections:
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

        confirmation = questionary.confirm(f"'{old_value}' seems like rubish. Do you wish to edit it?").ask()
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
        # Editing an unknown-artist/title split is unconditional (there's no
        # yes/no decision to defer) - the per-song "Press anything to continue"
        # was only there to pause and show what changed, one song at a time.
        # Apply edits during the scan as before, but collect what changed and
        # print it as a single summary at the end instead of blocking per song.
        changes = []
        for song in songs:
            if not has_tag_on_song(song, "album_checked"):
                lowered_artist = song.artist.name.strip().lower()
                lowered_title = song.title.strip().lower()
                slog(lowered_artist)
                slog(lowered_title)
                if "unknown" in lowered_artist or not lowered_artist:
                    print(f"Went through because lowered_artist is {lowered_artist} (song is {song.artist.name} - {song.title} [song_id is {song.id}])")
                    new_artist = ""
                    new_title = ""
                    if " - " in lowered_artist:
                        new_artist, new_title = lowered_artist.split(" - ", 1)
                    elif " - " in lowered_title:
                        new_artist, new_title = lowered_title.split(" - ", 1)
                    if new_artist:
                        old_artist = song.artist.name
                        edit_db_entry(song, song_categories[1], new_artist)
                        changes.append(f"song_id {song.id}: artist '{old_artist}' -> \033[93m'{new_artist}'\033[0m")
                    if new_title:
                        old_title = song.title
                        edit_db_entry(song, song_categories[0], new_title)
                        changes.append(f"song_id {song.id}: title '{old_title}' -> \033[93m'{new_title}'\033[0m")

        if changes:
            print("\n" + "="*70)
            print(f"Resolved {len(changes)} unknown-artist field(s):")
            for change in changes:
                print(f"  {change}")
            print("="*70)



def get_rid_of_rubish_data():
    print("Looking for sus album titles . . . . . ")
    songs = get_songs_from_db_session()
    convert_characters_encoding(songs)
    strip_leading_spaces(songs)
    replace_double_spaces(songs)
    resolve_unknown_artist(songs)
    submit_global_database_session()
    seek_nonsense_names(songs)
    submit_global_database_session()


