"""Which name-cleanup step runs when: the registry of steps (STEPS), the
Settings switches turning them on or off (SWITCHES), the order they run in
(RAW_ORDER / FIELD_ORDER), where each one works (runs_for()), and running
them (sanitize_raw() / sanitize_field() / sanitize_metadata() /
sanitize_changes()). What a step does to a text is text.py."""
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable, Optional

from utils.common.normalizer import composed, strip_format_characters
from utils.common.sanitize import settings as sanitize_settings
from utils.common.sanitize.text import (
    ANY_BRACKET_RE,
    MEDIA_EXTENSION_RE,
    YOUTUBE_TITLE_JUNK_MARKER_ANYWHERE_RE,
    VERSION_MARKER_RE,
    _PIPE_SUFFIX_RE,
    _TRAILING_HASHTAGS_RE,
    clean_file_name_title,
    fix_cp1250_mojibake,
    has_site_name,
    is_site_name,
    strip_artist_from_title,
    strip_hashtags,
    strip_junk_brackets_anywhere,
    strip_junk_suffix,
    strip_media_extension,
    strip_pipe_suffix,
    strip_quote_marks,
    strip_site_names,
    strip_title_year,
    strip_trailing_junk,
    strip_trailing_junk_suffix,
    trim_dangling_separator,
    unescape_entities,
)
from utils.common.song_entry import Removed

# Where a text comes from
SOURCE_MP3 = "mp3"              # an mp3's tags
SOURCE_FILE_NAME = "file_name"  # a file name read as "Artist - Title"
SOURCE_YOUTUBE = "youtube"      # a YouTube video's title and channel
SOURCE_REQUEST = "request"      # a song request from Leina Radio
SOURCE_MANUAL = "manual"        # typed by hand (the editor, a field correction)
SOURCE_STORED = "stored"        # already in music.db (Clean names)
SOURCES = (SOURCE_MP3, SOURCE_FILE_NAME, SOURCE_YOUTUBE, SOURCE_REQUEST, SOURCE_MANUAL, SOURCE_STORED)

STAGE_RAW = "raw"
STAGE_FIELD = "field"

TITLE = "title"
ARTIST = "artist"
ALBUM = "album"
_ALL_FIELDS = frozenset({TITLE, ARTIST, ALBUM})

MODE_AUTO = "auto"
MODE_REVIEW = "review"

# metadata dict key -> field
_METADATA_FIELDS = {"artist_name": ARTIST, "title": TITLE, "album": ALBUM}


@dataclass
class Context:
    field: str
    source: str
    stage: str
    artist: Optional[str] = None  # the entry's artist, for a title step
    feat_artists: Optional[list] = None  # where the featured artists go; None: keep them in the title


@dataclass(frozen=True)
class Step:
    name: str                  # its key in the settings - never renamed
    stages: frozenset
    fields: frozenset          # for a field step; a raw step sees the whole text
    func: Callable[[str, Context], str]
    mode: str = MODE_AUTO      # MODE_REVIEW: in the database only offered (Clean names asks)
    switch: str = ""           # the Settings switch that turns it on or off (SWITCHES)


def _site_names(value: str, ctx: Context) -> str:
    # Only a name holding one changes - strip_site_names() alone also trims the
    # separators of any name ("edge_separators"). A field that is nothing but
    # a site name is emptied (the mp3 import then reads the file name); a
    # whole file name is left as it is
    if not has_site_name(value):
        return value
    if ctx.stage == STAGE_FIELD and is_site_name(value):
        return ""
    return strip_site_names(value)


def _whitespace(value: str, ctx: Context) -> str:
    # Whitespace at either end, two or more spaces in a row
    return re.sub(r" {2,}", " ", value.strip())


# These change a name only when there is something to take out: the
# functions also collapse every whitespace - the ideographic space of
# "ｚｂｉｇｎｉｅｗ\u3000ｓｔｏｎｏｇａ", or the two spaces a file name
# "Artist  Title" is split at. A whole video title gets its spaces collapsed
# first, by build_metadata_from_item() itself, as it always did
def _junk_brackets_anywhere(value: str, ctx: Context) -> str:
    if not any(YOUTUBE_TITLE_JUNK_MARKER_ANYWHERE_RE.search(m.group(1)) and not VERSION_MARKER_RE.search(m.group(1))
               for m in ANY_BRACKET_RE.finditer(value)):
        return value
    return strip_junk_brackets_anywhere(value)


def _pipe_suffix(value: str, ctx: Context) -> str:
    return strip_pipe_suffix(value) if _PIPE_SUFFIX_RE.search(value) else value


def _hashtags(value: str, ctx: Context) -> str:
    return strip_hashtags(value) if _TRAILING_HASHTAGS_RE.search(value) else value


def _file_name_artifacts(value: str, ctx: Context) -> str:
    # Only a name with an extension or a "_" (it would also trim spaces)
    return clean_file_name_title(value) if "_" in value or MEDIA_EXTENSION_RE.search(value) else value


def _media_extension(value: str, ctx: Context) -> str:
    return strip_media_extension(value) if MEDIA_EXTENSION_RE.search(value) else value


def _fix_lookalike_letters(value: str, ctx: Context) -> str:
    from utils.common.script_utils import fix_mixed_script_words
    return fix_mixed_script_words(value)


def _featured_artists(value: str, ctx: Context) -> str:
    # Only when the names have somewhere to go (a metadata dict's
    # feat_artists): a title never loses a credit nobody keeps
    if ctx.feat_artists is None:
        return value
    from utils.common.artist_credits import split_feat_credits
    bare, names = split_feat_credits(value)
    if is_enabled("junk_phrases_any_bracket", ctx.source):
        # A credit runs to the end: "Savana feat Darko Rundek Official Video"
        names = [strip_trailing_junk(name) or name for name in names]
    ctx.feat_artists.extend(n for n in names if n not in ctx.feat_artists)
    return bare


def _step(name, switch, stages, fields, func, mode=MODE_AUTO) -> Step:
    return Step(name, frozenset(stages), frozenset(fields), func, mode, switch)


_RAW = (STAGE_RAW,)
_FIELD = (STAGE_FIELD,)
_BOTH = (STAGE_RAW, STAGE_FIELD)

# The Settings switches, in the order they run and are shown (owner,
# 2026-11-01): characters first (a file name's "_" as spaces too, before
# anything reads the words), then what a title says - kept on the entry
# before anything is cut, or the cut takes it along ("Song (Official Video
# ft. X)") - then removing, then tidying. Each turns its steps on or off for
# every source at once, so each step must be safe for every name it sees
SWITCHES = (
    "html_entities", "encoding", "file_names_in_titles", "lookalike_letters",
    "featured_artists", "year_in_title", "artist_in_title",
    "site_names", "junk_in_titles", "quote_marks",
    "spaces",
)

FEAT_TO_CREDITS = "feat_to_credits"

STEPS = {step.name: step for step in (
    _step("html_entities", "html_entities", _FIELD, _ALL_FIELDS, lambda v, ctx: unescape_entities(v)),
    _step("cp1250_mojibake", "encoding", _FIELD, _ALL_FIELDS, lambda v, ctx: fix_cp1250_mojibake(v)),
    _step("nfc", "encoding", _FIELD, _ALL_FIELDS, lambda v, ctx: composed(v)),
    _step("invisible_chars", "spaces", _FIELD, _ALL_FIELDS, lambda v, ctx: strip_format_characters(v, keep_joiner=True)),
    _step("lookalike_letters", "lookalike_letters", _FIELD, _ALL_FIELDS, _fix_lookalike_letters, MODE_REVIEW),
    # "Song ft. X" -> "Song", X a featured artist; in the database Clean names'
    # checkbox list (name_cleanup.feat_move_proposals()). find_existing_song()
    # reads a stored title without its credits and tries the songs crediting
    # the entry's guests first, so "No Scrubs" doesn't take "No Scrubs ft.
    # Joanna Jones"'s place
    _step(FEAT_TO_CREDITS, "featured_artists", _FIELD, {TITLE}, _featured_artists, MODE_REVIEW),
    _step("title_year", "year_in_title", _FIELD, {TITLE}, lambda v, ctx: strip_title_year(v)),
    _step("artist_in_title", "artist_in_title", _FIELD, {TITLE}, lambda v, ctx: strip_artist_from_title(v, ctx.artist)),
    _step("site_names", "site_names", _BOTH, _ALL_FIELDS, _site_names),
    _step("pipe_suffix", "junk_in_titles", _BOTH, {TITLE}, _pipe_suffix),
    _step("junk_brackets_anywhere", "junk_in_titles", _BOTH, {TITLE}, _junk_brackets_anywhere),
    _step("hashtags", "junk_in_titles", _BOTH, {TITLE}, _hashtags),
    _step("junk_marker_brackets", "junk_in_titles", _FIELD, {TITLE}, lambda v, ctx: strip_junk_suffix(v)),
    # A title that is nothing but junk is kept (it *is* the title: "HD")
    _step("junk_phrases_any_bracket", "junk_in_titles", _FIELD, {TITLE}, lambda v, ctx: strip_trailing_junk(v) or v),
    _step("junk_phrases", "junk_in_titles", _FIELD, {TITLE}, lambda v, ctx: strip_trailing_junk_suffix(v) or v),
    _step("media_extension", "file_names_in_titles", _BOTH, {TITLE}, _media_extension),
    _step("file_name_artifacts", "file_names_in_titles", _FIELD, {TITLE, ARTIST}, _file_name_artifacts),
    _step("quote_marks", "quote_marks", _FIELD, {TITLE}, lambda v, ctx: strip_quote_marks(v)),
    _step("whitespace", "spaces", _FIELD, _ALL_FIELDS, _whitespace),
    _step("edge_separators", "spaces", _BOTH, _ALL_FIELDS, lambda v, ctx: trim_dangling_separator(v)),
)}

# The order steps run in - that of SWITCHES. A step may be listed twice: the
# year and the trailing-bracket junk again once the other junk is gone ("Song
# (2020) (Official Video)", "Song (Official Video) Official Audio"), the
# spaces at the start and again after the cuts
RAW_ORDER = ("site_names", "pipe_suffix", "junk_brackets_anywhere", "hashtags", "media_extension", "edge_separators")
FIELD_ORDER = (
    "html_entities", "cp1250_mojibake", "nfc", "media_extension", "file_name_artifacts",
    "invisible_chars", "whitespace", "lookalike_letters",
    FEAT_TO_CREDITS, "title_year", "artist_in_title",
    "site_names", "pipe_suffix", "junk_brackets_anywhere", "hashtags",
    "junk_marker_brackets", "junk_phrases_any_bracket", "junk_phrases", "title_year", "junk_marker_brackets",
    "quote_marks",
    "whitespace", "edge_separators",
)

# Where a step runs once it's on - what it can see, not a choice: a raw step
# where a whole text is split (a video title, a file name), a field step
# where a field is cleaned. Never a name typed by hand ("manual"). In the
# database (Clean names) a review step only offers its change in a list
RAW_SOURCES = frozenset({SOURCE_YOUTUBE, SOURCE_FILE_NAME})
FIELD_SOURCES = frozenset({SOURCE_MP3, SOURCE_YOUTUBE, SOURCE_REQUEST, SOURCE_STORED})

# Steps (or switches) a settings file of an older version named, and the
# switch they belong to now
_FORMER_STEPS = {"amp_entity": "html_entities", "trailing_dash_year": "year_in_title"}


def runs_for(name: str) -> frozenset:
    """The sources step `name` works on when it's on."""
    step = STEPS[name]
    return (RAW_SOURCES if STAGE_RAW in step.stages else frozenset()) | \
        (FIELD_SOURCES if STAGE_FIELD in step.stages else frozenset())


def switch_steps(switch: str) -> list[str]:
    """The steps switch `switch` turns on or off."""
    return [name for name, step in STEPS.items() if step.switch == switch]


def switch_on(switch: str) -> bool:
    """Is `switch` on (Settings -> "Name cleanup"), for every source? All
    are on by default. A file of an older version, which switched each
    step: off when one of its steps was switched off."""
    overrides = sanitize_settings.load_overrides()
    if switch in overrides:
        return overrides[switch]
    former = [overrides[name] for name in (*switch_steps(switch), *(n for n, s in _FORMER_STEPS.items() if s == switch))
              if name in overrides]
    return all(former)


def set_switch_on(switch: str, on: bool) -> None:
    """Turn `switch` on or off everywhere; only what differs from the
    default (on) is kept, a former step's setting dropped."""
    members = {switch, *switch_steps(switch), *(n for n, s in _FORMER_STEPS.items() if s == switch)}
    overrides = {name: value for name, value in sanitize_settings.load_overrides().items() if name not in members}
    if not on:
        overrides[switch] = False
    sanitize_settings.save_overrides(overrides)


def reset_switches() -> None:
    """Every switch back to on."""
    sanitize_settings.save_overrides({})


def is_enabled(name: str, source: str) -> bool:
    """Does `source` run step `name`?"""
    return source in runs_for(name) and switch_on(STEPS[name].switch)


def enabled_steps(source: str, stage: str, field: str | None = None, review: bool = False) -> list[Step]:
    """The steps `source` runs at `stage` (for `field`), in order; the review
    ones only with `review`."""
    order = RAW_ORDER if stage == STAGE_RAW else FIELD_ORDER
    steps = []
    for name in order:
        step = STEPS[name]
        if stage not in step.stages or not is_enabled(name, source):
            continue
        if stage == STAGE_FIELD and field not in step.fields:
            continue
        if step.mode == MODE_REVIEW and source == SOURCE_STORED and not review:
            continue
        steps.append(step)
    return steps


def _run(value, source, stage, field, artist, review, feat_artists=None) -> list[tuple[str, str, str]]:
    changes = []
    if not value:
        return changes
    ctx = Context(field=field, source=source, stage=stage, artist=artist, feat_artists=feat_artists)
    for step in enabled_steps(source, stage, field, review):
        new_value = step.func(value, ctx)
        if new_value != value:
            changes.append((step.name, value, new_value))
            value = new_value
        if not value:
            break
    return changes


# The steps that cut a part of a name out, rather than rewrite characters:
# what they cut is kept (SongEntry.removed) - a year, a "(from “Chess”)", an
# "(Official Video)" say something about the song
CUTTING_STEPS = frozenset({
    "site_names", "pipe_suffix", "junk_brackets_anywhere", "hashtags", "media_extension", "artist_in_title",
    "junk_marker_brackets", "junk_phrases_any_bracket", "junk_phrases", "title_year", FEAT_TO_CREDITS,
})


def cut_parts(changes, field: str | None) -> list[Removed]:
    """What the cutting steps among `changes` ((step, before, after), as
    sanitize_changes() gives them) took out of `field` (None: a whole text
    before the split), one Removed per piece, whitespace left out."""
    parts = []
    for name, before, after in changes:
        if name not in CUTTING_STEPS:
            continue
        for tag, i1, i2, j1, j2 in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
            if tag == "delete" or (tag == "replace" and not after[j1:j2].strip()):
                if text := before[i1:i2].strip():
                    parts.append(Removed(name, field, text))
    return parts


def sanitize_raw(text: str | None, source: str, removed: list | None = None) -> str | None:
    """`text` - a whole text still to be split into artist and title -
    after `source`'s raw steps; what they cut appended to `removed`."""
    changes = _run(text, source, STAGE_RAW, None, None, review=False)
    if removed is not None:
        removed.extend(cut_parts(changes, None))
    return changes[-1][2] if changes else text


def sanitize_field(value: str | None, field: str, source: str, *, artist: str | None = None,
                   removed: list | None = None, feat_artists: list | None = None) -> str | None:
    """`value` of `field` ("title", "artist", "album") after `source`'s
    field steps; `artist` is the entry's artist, for a title. What they cut
    is appended to `removed`, the featured artists a title credits to
    `feat_artists` - without it they stay in the title."""
    changes = _run(value, source, STAGE_FIELD, field, artist, review=False, feat_artists=feat_artists)
    if removed is not None:
        removed.extend(cut_parts(changes, field))
    return changes[-1][2] if changes else value


def sanitize_changes(value: str | None, field: str, source: str, *, artist: str | None = None,
                     review: bool = True) -> list[tuple[str, str, str]]:
    """What `source`'s field steps would change in `value`, one (step name,
    before, after) per step that changed something - with the review steps
    too (their change is a proposal), unless not `review`."""
    return _run(value, source, STAGE_FIELD, field, artist, review)


def sanitize_metadata(metadata: dict, source: str) -> dict:
    """`metadata` (an import_engine metadata dict) with its artist, title and
    album cleaned by `source`'s field steps, in place. The title first, with
    the artist as it came; a field cleaned down to nothing becomes None. The
    featured artists the title credits go to feat_artists (cleaned twice -
    an mp3's tags, then its file name - the first names stay). What the
    steps cut goes to a SongEntry's `removed`."""
    removed = getattr(metadata, "removed", None)
    artist = metadata.get("artist_name")
    feat_artists = list(metadata.get("feat_artists") or [])
    for key in ("title", "artist_name", "album"):
        value = metadata.get(key)
        if isinstance(value, str):
            cleaned = sanitize_field(value, _METADATA_FIELDS[key], source, artist=artist, removed=removed,
                                     feat_artists=feat_artists if key == "title" else None)
            metadata[key] = cleaned if cleaned or not value else None
    if feat_artists or "feat_artists" in metadata:
        metadata["feat_artists"] = feat_artists
    return metadata
