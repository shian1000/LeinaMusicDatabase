"""The one place that cleans artist names, titles and albums before they are
stored or compared: every cleanup is a named step, and a source (an mp3's
tags, a YouTube video, a song request from Leina Radio, the stored catalog)
runs the steps it has turned on, in one fixed order.

Two stages: "raw" steps work on a whole text before it is split into artist
and title (a video title: "A - B | Label #tag"), "field" steps on one field
after. The splitting itself is not a step; it stays with its source.

A "review" step is never applied silently: sanitize_field() skips it, and
sanitize_changes() reports it as a proposal for the owner to tick.

Which steps a source runs: each step's `sources` by default - chosen to do
exactly what each source did before this module existed
(tests/test_name_cleanup_characterization.py pins that) - with what the
owner changed in Settings -> "Name cleanup" on top (sanitize_settings.py),
for the sources a step can run for (configurable_sources()). The comparison keys
(normalizer.normalize(), name_key(), import_engine._title_core()) are not
steps and never depend on these settings.
"""
import html
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Optional

from config.constants import TITLE_JUNK_SUFFIXES
from utils.common.normalizer import (
    NON_DECOMPOSING_LETTER_FOLDS,
    composed,
    fix_cp1250_mojibake,
    is_site_name,
    strip_format_characters,
    strip_site_names,
)
from utils.common.text_utils import strip_trailing_junk_suffix
from utils.common import sanitize_settings
from utils.common.title_patterns import YEAR, YOUTUBE_BRACKET_PAIRS, YOUTUBE_BRACKETED

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


@dataclass(frozen=True)
class Step:
    name: str                  # its key in the settings - never renamed
    stages: frozenset
    fields: frozenset          # for a field step; a raw step sees the whole text
    sources: frozenset         # where it runs by default
    func: Callable[[str, Context], str]
    mode: str = MODE_AUTO
    needs_artist: bool = False  # a title step that reads the entry's artist


# ==================== YouTube titles ====================

# Trailing "(...)"/"[...]" annotations that don't identify a genuinely
# different version of the song - just upload metadata - so they're stripped
# from the imported title entirely (e.g. "Mr. Vain (Original Radio Edit)" ->
# "Mr. Vain"). Deliberately NOT included: anything that marks a real
# alternate version worth keeping distinguishable in the library - "Remix",
# "Live", "Acoustic", "Cover", "Extended", "Club Edit", a named remixer, or a
# "feat./ft." credit. Add new junk phrases here as they come up; matching is
# case-insensitive and only against the title's trailing bracket (see
# strip_junk_suffix()), so this never touches a bracket earlier in the title.
YOUTUBE_TITLE_JUNK_PHRASES = [
    "Official Video",
    "Official Audio",
    "Official Music Video",
    "Official Lyric Video",
    "Lyric Video",
    "Lyrics",
    "HD",
    "HQ",
    "Videoclip",
    "MusicVideo",
    "Video Clip",
    "Original Radio Edit",
    "Radio Edit",
    "Radio Version",
    "Album Version",
    "Original Mix",
    "Explicit",
    "Clean Version",
    "Edit",
]
_YOUTUBE_TITLE_JUNK_PHRASES_LOWER = {p.lower() for p in YOUTUBE_TITLE_JUNK_PHRASES}

# Unlike YOUTUBE_TITLE_JUNK_PHRASES above (which requires the bracket's
# *entire* content to match one known phrase exactly), these are single
# words that condemn the whole trailing bracket just by appearing in it
# anywhere, since the rest of the bracket is typically source-specific and
# not worth trying to enumerate - e.g. "(Pes 2009 Soundtrack)" doesn't match
# any whole phrase above, but the bracket as a whole still just says what
# the recording was used for, not a version of the song. Add new marker
# words here as they come up; matched as a whole word, case-insensitive.
YOUTUBE_TITLE_JUNK_MARKER_WORDS = [
    "Soundtrack",
    "Audio",
    "From",
    "Video",
    # Says who uploaded it again, not which version (real case:
    # "DISCO BANIA - BRUNETKI I BLONDYNKI (reupload HD)")
    "Reupload",
    "Re-upload",
]
_YOUTUBE_TITLE_JUNK_MARKER_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in YOUTUBE_TITLE_JUNK_MARKER_WORDS) + r")\b",
    re.IGNORECASE,
)

# Same trailing-only scope as YOUTUBE_TITLE_JUNK_MARKER_WORDS above, but for
# a release/recording year instead of a fixed word list - any bare 4-digit
# number condemns the whole trailing bracket (e.g. "Sierpień (2022)" ->
# "Sierpień"), since a year annotation is upload metadata, not part of the
# song's identity.
_YOUTUBE_TITLE_YEAR_RE = re.compile(r"\b\d{4}\b")

# Matches one "(...)"/"[...]"/"【...】" group (no nested brackets) at the
# very end of the string, plus any trailing whitespace. "【】" is the CJK
# "lenticular bracket" pair, used the same way "[...]" is in Western titles
# (e.g. a trailing "【Kan/Rom/Eng Lyrics】" annotation). The fullwidth
# "（）"/"［］" pairs come from CJK input methods (real case: "Fujii Kaze -
# Workin’ Hard（Official Video）") and are treated exactly like "()"/"[]".
_TRAILING_BRACKET_RE = re.compile(r"\s*" + YOUTUBE_BRACKETED + r"\s*$")

# Straight and curly double-quote characters, plus the CJK "corner bracket"
# pair "『』" (used to set off a work's title, the way Western titles use
# double quotes - e.g. '『Cinderella by Cidergirl』'). Only double-quote-like
# characters are stripped (never single quotes/apostrophes, which routinely
# appear inside a title itself - e.g. "Rock 'n' Roll", "Don't Stop") -
# uploaders wrap the song name in them fairly often (e.g. 'Night Club -
# "Need You Tonight" (INXS cover)'), and that's never meaningful punctuation
# worth keeping in the stored title.
_DOUBLE_QUOTE_RE = re.compile(r'["“”„‟『』]')


def strip_quote_marks(title: str) -> str:
    """Unwrap a quoted title: the double-quote-like mark `title` starts with
    and the next one ('"Need You Tonight" (INXS cover)' -> 'Need You Tonight
    (INXS cover)'); quotes inside a title stay ('Take The "A" Train'). The
    whitespace collapsed either way.
    """
    title = (title or "").strip()
    if title and _DOUBLE_QUOTE_RE.match(title):
        closing = _DOUBLE_QUOTE_RE.search(title, 1)
        title = title[1:closing.start()] + title[closing.end():] if closing else title[1:]
    return re.sub(r"\s+", " ", title).strip()


# Unlike YOUTUBE_TITLE_JUNK_MARKER_WORDS above (only checked against the
# *trailing* bracket, to avoid nuking a genuine subtitle earlier in the
# title), these condemn a "(...)"/"[...]" group no matter where it sits -
# e.g. "Herr Mannelig (animation) - Żniwa - Polish version" has real title
# text (" - Żniwa - Polish version") after the junk bracket, so it would
# never be reached by the trailing-only check. Only add a word here when
# it's safe to assume it never appears inside a real subtitle.
YOUTUBE_TITLE_JUNK_MARKER_WORDS_ANYWHERE = [
    "Animation",
    "Official",
    "Lyrics",
    "Lyric",
    "Visualiser",
    "Video",
    "Clip",
    "Officiel",
    "Oryginał",
    "Subtitles",
    "4K",
    "Remaster",
    "Remastered",
    "Oficial",
    # Bollywood label uploads (real case: "T-Series - Criminal (Full Song)
    # Ra.One") - the whole track, as opposed to a teaser/promo cut.
    "Full Song",
]
YOUTUBE_TITLE_JUNK_MARKER_ANYWHERE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in YOUTUBE_TITLE_JUNK_MARKER_WORDS_ANYWHERE) + r")\b",
    re.IGNORECASE,
)

# Matches one "(...)"/"[...]"/"【...】" group (no nested brackets) anywhere
# in the string, plus any whitespace right before it.
ANY_BRACKET_RE = re.compile(r"\s*" + YOUTUBE_BRACKETED)


def strip_junk_brackets_anywhere(title: str) -> str:
    """Drop any "(...)"/"[...]" group whose content contains one of
    YOUTUBE_TITLE_JUNK_MARKER_WORDS_ANYWHERE, wherever in `title` it sits.
    """
    def _replace(match: re.Match) -> str:
        if YOUTUBE_TITLE_JUNK_MARKER_ANYWHERE_RE.search(match.group(1)):
            return ""
        return match.group(0)

    title = ANY_BRACKET_RE.sub(_replace, title or "")
    return re.sub(r"\s+", " ", title).strip()


# A "|" in a YouTube title is routinely used to tack on channel/label
# branding after the actual song info (e.g. "OMNIMAR - The Matrix (Official
# Video) | darkTunes Music Group") - never part of the song title itself, so
# everything from the first one onward is dropped. Doing this before
# strip_junk_suffix() also lets a junk bracket that only *looks* trailing
# once the branding is gone (like "(Official Video)" above) get caught by
# the existing trailing-bracket check.
# Look-alikes of "|" count too (real case: "Dizzko Bizkit aka Mejsi - Bál ⎮
# 2017", U+23AE): fullwidth "｜", box-drawing "│", broken bar "¦".
_PIPE_SUFFIX_RE = re.compile(r"\s*[|⎮｜│¦].*$")


def strip_pipe_suffix(title: str) -> str:
    """Drop a trailing "| ..." branding suffix from `title`, if present."""
    return _PIPE_SUFFIX_RE.sub("", title or "").strip()


# Uploaders routinely tack promo hashtags onto the end of a title (e.g.
# "Nocą (Official Audio) #Zostańwdomu #KulturalnaStrefa") - never part of the
# song title itself. Only at the end and only "#" + a letter: "Funk #49" and
# "TV [RóbTuLoop! #1]" are titles, and so is a hashtag right after the
# artist's " - " ("Artist - #ВІЛЬНА_КАСА #shorts").
_HASHTAG = r"#[^\W\d_][^\s#]*"
_TRAILING_HASHTAGS_RE = re.compile(r"(?:\s+" + _HASHTAG + r")+\s*$")
_LEADING_HASHTAG_RE = re.compile(r"\s*" + _HASHTAG)


def strip_hashtags(title: str) -> str:
    """Drop the "#word" hashtags `title` ends with, unless one is its title."""
    title = title or ""
    match = _TRAILING_HASHTAGS_RE.search(title)
    if match:
        before = title[:match.start()].rstrip()
        if not before or before[-1] in "-–—:|":
            # The first hashtag is the title itself
            match = _TRAILING_HASHTAGS_RE.search(title, _LEADING_HASHTAG_RE.match(title, match.start()).end())
        if match:
            title = title[:match.start()]
    return re.sub(r"\s+", " ", title).strip()


# A title that is really an uploaded file name (real case: "Justyna
# Steczkowska - pejzaz_bez_ciebie.mpg"): a trailing media extension is
# dropped, and an underscore-joined name is turned back into words.
MEDIA_EXTENSION_RE = re.compile(
    r"\.(?:mpg|mpeg|mp4|m4v|avi|wmv|flv|mkv|webm|mov|3gp|mp3|wav|flac|ogg|m4a|wma)\s*$",
    re.IGNORECASE,
)


def strip_media_extension(text: str) -> str:
    """`text` without a trailing media extension (".mpg", ".mp3", ...)."""
    return MEDIA_EXTENSION_RE.sub("", text or "").strip()


def clean_file_name_title(text: str) -> str:
    """Undo file-name artefacts in `text`: drop a trailing media extension,
    and if the rest is words joined by "_" with no spaces, split them back
    apart - capitalising each word when it was written all lowercase
    ("pejzaz_bez_ciebie.mpg" -> "Pejzaz Bez Ciebie")."""
    text = MEDIA_EXTENSION_RE.sub("", (text or "").strip()).strip()
    if "_" in text and " " not in text:
        words = [w for w in text.split("_") if w]
        if text.islower():
            words = [w[:1].upper() + w[1:] for w in words]
        text = " ".join(words)
    return text


# A bare year tacked on after a dash, outside any bracket (real case, channel
# "VHS-Goldie": "Hank Williams Jr. - Mr. Weatherman - 1982"). Bracketed years
# are already handled by _YOUTUBE_TITLE_YEAR_RE in strip_junk_suffix().
_TRAILING_DASH_YEAR_RE = re.compile(r"\s+[-–—]\s+" + YEAR + r"\s*$")


def strip_trailing_dash_year(title: str) -> str:
    """Drop a trailing " - 1982"-style year, unless it's the whole title."""
    return _TRAILING_DASH_YEAR_RE.sub("", title or "").strip() or (title or "")


def strip_junk_suffix(title: str) -> str:
    """Repeatedly strip a trailing bracket group from `title` as long as it's
    junk - either its content matches YOUTUBE_TITLE_JUNK_PHRASES exactly, it
    contains one of YOUTUBE_TITLE_JUNK_MARKER_WORDS anywhere, or it contains a
    bare 4-digit year - handles a title decorated with more than one such
    annotation (e.g. "Song (Official Video) (HD)"). Only ever touches the
    trailing bracket, never one earlier in the title (e.g. a genuine subtitle
    like "Sad Story (Out of Luck)" is left alone).
    """
    title = (title or "").strip()
    while True:
        match = _TRAILING_BRACKET_RE.search(title)
        if not match:
            break
        content = match.group(1).strip()
        is_exact_junk = content.lower() in _YOUTUBE_TITLE_JUNK_PHRASES_LOWER
        has_marker_word = bool(_YOUTUBE_TITLE_JUNK_MARKER_RE.search(content))
        has_year = bool(_YOUTUBE_TITLE_YEAR_RE.search(content))
        if not (is_exact_junk or has_marker_word or has_year):
            break
        title = title[:match.start()].strip()
    return title


# Opening->closing bracket characters that strip_artist_from_title() will
# drop along with an artist-name match they wrap entirely (e.g. the "【】" in
# "Ado - 【Ado】 unravel 歌いました" exist only to enclose the artist name, so
# leaving them behind after the name is removed would strand an empty
# "【】" in the title).
_ARTIST_WRAPPING_BRACKETS = YOUTUBE_BRACKET_PAIRS


def fold_char(ch: str) -> str:
    """Strip accents off a single character via NFKD decomposition, falling
    back to the original character when decomposition doesn't yield exactly
    one base character - keeps a 1:1 length correspondence with the input,
    which strip_artist_from_title() relies on to map a match back to
    original-string positions. "ł" / "ø", which have no decomposition, are
    folded with the same table as the search's _fold_diacritics(), so "Łona"
    is "Lona" here too.
    """
    decomposed = unicodedata.normalize("NFKD", ch)
    base = "".join(c for c in decomposed if not unicodedata.combining(c))
    return (base if len(base) == 1 else ch).translate(NON_DECOMPOSING_LETTER_FOLDS)


def strip_artist_from_title(title: str, artist: str) -> str:
    """If the song title itself repeats the artist's name, drop that
    occurrence - real case found testing this: channel "SIAMES", video
    titled 'SIAMÉS "Summer Nights" [Official Animated Video]' (the bare
    video title already includes the artist name, redundant once it's
    already stored separately as the song's artist).

    Diacritic-insensitive on both sides (via fold_char - same technique as
    manage_youtube_playlists.py's _fold_diacritics, done per-character here
    to keep a position mapping back to the original title) since the
    channel name and the same name embedded in a title aren't always spelled
    identically, as in the SIAMES/SIAMÉS case above. Only removes a whole
    word/phrase match (not a substring of some other word), and leaves the
    title untouched entirely if that would empty it out.
    """
    if not title or not artist:
        return title

    folded_title = "".join(fold_char(c) for c in title)
    folded_artist = "".join(fold_char(c) for c in artist).strip()
    if not folded_artist:
        return title

    match = re.search(r"\b" + re.escape(folded_artist) + r"\b", folded_title, re.IGNORECASE)
    if not match:
        return title

    start, end = match.start(), match.end()
    if (
        start > 0
        and title[start - 1] in _ARTIST_WRAPPING_BRACKETS
        and end < len(title)
        and title[end] == _ARTIST_WRAPPING_BRACKETS[title[start - 1]]
    ):
        start -= 1
        end += 1

    remainder = (title[:start] + title[end:]).strip()

    # If the artist name was itself quoted (real case: 'SIAMÉS "Summer
    # Nights" [...]' - the quotes wrap the title, not the artist), removing
    # just the name leaves one quote character orphaned at the front with
    # its matching partner stranded mid-string rather than at an edge a
    # plain edge-trim would catch. Unwrap that pair specifically instead.
    if remainder[:1] in ('"', "'"):
        quote_char = remainder[0]
        closing = remainder.find(quote_char, 1)
        if closing != -1:
            remainder = remainder[1:closing] + remainder[closing + 1:]

    # Clean up whatever else was only there to connect the artist name to
    # the rest of the title - a leading "-"/":"/"–" and surrounding spaces.
    remainder = re.sub(r'^[\s\-:–—]+', "", remainder)
    remainder = re.sub(r'[\s\-:–—]+$', "", remainder)
    return remainder.strip() or title


# ==================== Stored titles (Clean names) ====================

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


# ==================== Steps ====================

# An HTML entity, always ending in ";" - html.unescape() alone also reads
# "&#3" in "Kushi&#3" (a title) as a control character
_HTML_ENTITY_RE = re.compile(r"&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")


def unescape_entities(text: str) -> str:
    """`text` with its HTML entities as characters ("Major Lazer &amp; DJ
    Snake" -> "Major Lazer & DJ Snake"); "&" not starting one stays."""
    return _HTML_ENTITY_RE.sub(lambda m: html.unescape(m.group(0)), text or "")


def _site_names(value: str, ctx: Context) -> str:
    # A field that is nothing but a site name is emptied (the mp3 import then
    # reads the file name); a whole file name is left as it is
    if ctx.stage == STAGE_FIELD and is_site_name(value):
        return ""
    return strip_site_names(value)


def _whitespace(value: str, ctx: Context) -> str:
    # Whitespace at either end, two or more spaces in a row
    return re.sub(r" {2,}", " ", value.strip())


def _junk_brackets_anywhere(value: str, ctx: Context) -> str:
    # A field (an mp3 tag, a stored title) only changes when a junk bracket is
    # there: strip_junk_brackets_anywhere() also collapses every whitespace,
    # the ideographic space of "ｚｂｉｇｎｉｅｗ\u3000ｓｔｏｎｏｇａ" too
    if ctx.stage == STAGE_FIELD and not any(YOUTUBE_TITLE_JUNK_MARKER_ANYWHERE_RE.search(m.group(1))
                                            for m in ANY_BRACKET_RE.finditer(value)):
        return value
    return strip_junk_brackets_anywhere(value)


def _fix_lookalike_letters(value: str, ctx: Context) -> str:
    from utils.common.script_utils import fix_mixed_script_words
    return fix_mixed_script_words(value)


def _step(name, stages, fields, sources, func, mode=MODE_AUTO, needs_artist=False) -> Step:
    return Step(name, frozenset(stages), frozenset(fields), frozenset(sources), func, mode, needs_artist)


_RAW = (STAGE_RAW,)
_FIELD = (STAGE_FIELD,)
# Where a new name comes in through a field (a file name only as a whole)
_IMPORTS = frozenset({SOURCE_MP3, SOURCE_YOUTUBE, SOURCE_REQUEST})

STEPS = {step.name: step for step in (
    _step("amp_entity", _FIELD, _ALL_FIELDS, {SOURCE_STORED}, lambda v, ctx: v.replace("&amp;", "&")),
    _step("html_entities", _FIELD, _ALL_FIELDS, {SOURCE_MP3}, lambda v, ctx: unescape_entities(v)),
    _step("cp1250_mojibake", _FIELD, _ALL_FIELDS, {SOURCE_MP3}, lambda v, ctx: fix_cp1250_mojibake(v)),
    _step("nfc", _FIELD, _ALL_FIELDS, _IMPORTS | {SOURCE_STORED}, lambda v, ctx: composed(v)),
    _step("invisible_chars", _FIELD, _ALL_FIELDS, _IMPORTS | {SOURCE_STORED},
          lambda v, ctx: strip_format_characters(v, keep_joiner=True)),
    _step("lookalike_letters", _FIELD, _ALL_FIELDS, {SOURCE_STORED}, _fix_lookalike_letters, MODE_REVIEW),
    _step("site_names", (STAGE_RAW, STAGE_FIELD), _ALL_FIELDS, {SOURCE_MP3, SOURCE_FILE_NAME}, _site_names),
    _step("pipe_suffix", _RAW, (), {SOURCE_YOUTUBE}, lambda v, ctx: strip_pipe_suffix(v)),
    _step("junk_brackets_anywhere", (STAGE_RAW, STAGE_FIELD), {TITLE}, {SOURCE_YOUTUBE, SOURCE_MP3, SOURCE_STORED},
          _junk_brackets_anywhere),
    _step("hashtags", _RAW, (), {SOURCE_YOUTUBE}, lambda v, ctx: strip_hashtags(v)),
    _step("media_extension", _RAW, (), {SOURCE_YOUTUBE}, lambda v, ctx: strip_media_extension(v)),
    _step("artist_in_title", _FIELD, {TITLE}, {SOURCE_YOUTUBE},
          lambda v, ctx: strip_artist_from_title(v, ctx.artist), needs_artist=True),
    _step("junk_marker_brackets", _FIELD, {TITLE}, {SOURCE_YOUTUBE}, lambda v, ctx: strip_junk_suffix(v)),
    # A title that is nothing but junk is kept (it *is* the title: "HD")
    _step("junk_phrases_any_bracket", _FIELD, {TITLE}, {SOURCE_STORED}, lambda v, ctx: strip_trailing_junk(v) or v),
    _step("junk_phrases", _FIELD, {TITLE}, {SOURCE_YOUTUBE}, lambda v, ctx: strip_trailing_junk_suffix(v) or v),
    _step("trailing_dash_year", _FIELD, {TITLE}, {SOURCE_YOUTUBE, SOURCE_MP3, SOURCE_STORED},
          lambda v, ctx: strip_trailing_dash_year(v)),
    _step("quote_marks", _FIELD, {TITLE}, {SOURCE_YOUTUBE}, lambda v, ctx: strip_quote_marks(v)),
    _step("file_name_artifacts", _FIELD, {TITLE, ARTIST}, {SOURCE_YOUTUBE}, lambda v, ctx: clean_file_name_title(v)),
    _step("whitespace", _FIELD, _ALL_FIELDS, _IMPORTS | {SOURCE_STORED}, _whitespace),
)}

# The order steps run in. A step may be listed twice: the trailing-bracket
# junk runs again once the plain junk phrases and the year are gone ("Song
# (Official Video) Official Audio"), as the YouTube import always did
RAW_ORDER = ("site_names", "pipe_suffix", "junk_brackets_anywhere", "hashtags", "media_extension")
FIELD_ORDER = (
    "amp_entity", "html_entities", "cp1250_mojibake", "nfc", "invisible_chars", "whitespace", "lookalike_letters",
    "site_names", "junk_brackets_anywhere", "artist_in_title",
    "junk_marker_brackets", "junk_phrases_any_bracket", "junk_phrases", "trailing_dash_year", "junk_marker_brackets",
    "quote_marks", "file_name_artifacts",
)

# A metadata step changes the entry's structure, not one text: a "ft. X" in
# the title becomes metadata["feat_artists"], which create_song_entry()
# credits. Only for song requests so far - the mp3 / YouTube imports keep the
# credit in the title, since find_existing_song() wouldn't then see "Song
# ft. X" as the stored "Song" and would add it again
FEAT_TO_CREDITS = "feat_to_credits"
METADATA_STEP_SOURCES = {FEAT_TO_CREDITS: frozenset({SOURCE_REQUEST})}


# The sources a step can be switched on for: a raw step runs only where a
# whole text is split (a video title, a file name), a field step where a
# field is cleaned - not for a name typed by hand ("manual"), and not for
# the database when it needs the entry's artist; a review step only in
# Clean names, which asks
_RAW_SOURCES = (SOURCE_YOUTUBE, SOURCE_FILE_NAME)
_FIELD_SOURCES = (SOURCE_MP3, SOURCE_YOUTUBE, SOURCE_REQUEST, SOURCE_STORED)


def step_names() -> list[str]:
    """Every step and metadata step, in the order they run (raw first)."""
    return list(dict.fromkeys((*RAW_ORDER, *FIELD_ORDER, *METADATA_STEP_SOURCES)))


def default_sources(name: str) -> frozenset:
    """The sources step `name` runs for unless the owner changed it."""
    if name in METADATA_STEP_SOURCES:
        return METADATA_STEP_SOURCES[name]
    return STEPS[name].sources


def configurable_sources(name: str) -> tuple:
    """The sources step `name` can be switched on or off for, in SOURCES order."""
    if name in METADATA_STEP_SOURCES:
        # The imports would need find_existing_song() to see "Song ft. X" as "Song"
        return (SOURCE_REQUEST,)
    step = STEPS[name]
    if step.mode == MODE_REVIEW:
        return (SOURCE_STORED,)
    allowed = set()
    if STAGE_RAW in step.stages:
        allowed.update(_RAW_SOURCES)
    if STAGE_FIELD in step.stages:
        allowed.update(src for src in _FIELD_SOURCES if not (src == SOURCE_STORED and step.needs_artist))
    return tuple(src for src in SOURCES if src in allowed)


def step_sources(name: str) -> frozenset:
    """The sources step `name` runs for now: its defaults with the owner's
    settings on top."""
    sources = set(default_sources(name))
    allowed = configurable_sources(name)
    for source, on in sanitize_settings.load_overrides().get(name, {}).items():
        if source in allowed:
            (sources.add if on else sources.discard)(source)
    return frozenset(sources)


def set_step_sources(name: str, sources) -> None:
    """Save that step `name` runs for exactly `sources` (of its
    configurable_sources()); only what differs from the defaults is kept."""
    wanted, default = set(sources), default_sources(name)
    changed = {src: src in wanted for src in configurable_sources(name) if (src in wanted) != (src in default)}
    overrides = {step: dict(values) for step, values in sanitize_settings.load_overrides().items() if step != name}
    if changed:
        overrides[name] = changed
    sanitize_settings.save_overrides(overrides)


def reset_step_sources() -> None:
    """Every step back to its defaults."""
    sanitize_settings.save_overrides({})


def is_enabled(name: str, source: str) -> bool:
    """Does `source` run the step (or metadata step) `name`?"""
    return source in step_sources(name)


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
        if step.mode == MODE_REVIEW and not review:
            continue
        steps.append(step)
    return steps


def _run(value, source, stage, field, artist, review) -> list[tuple[str, str, str]]:
    changes = []
    if not value:
        return changes
    ctx = Context(field=field, source=source, stage=stage, artist=artist)
    for step in enabled_steps(source, stage, field, review):
        new_value = step.func(value, ctx)
        if new_value != value:
            changes.append((step.name, value, new_value))
            value = new_value
        if not value:
            break
    return changes


def sanitize_raw(text: str | None, source: str) -> str | None:
    """`text` - a whole text still to be split into artist and title -
    after `source`'s raw steps."""
    changes = _run(text, source, STAGE_RAW, None, None, review=False)
    return changes[-1][2] if changes else text


def sanitize_field(value: str | None, field: str, source: str, *, artist: str | None = None) -> str | None:
    """`value` of `field` ("title", "artist", "album") after `source`'s
    field steps; `artist` is the entry's artist, for a title."""
    changes = _run(value, source, STAGE_FIELD, field, artist, review=False)
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
    the artist as it came; a field cleaned down to nothing becomes None.
    Then the metadata steps (feat_to_credits)."""
    artist = metadata.get("artist_name")
    for key in ("title", "artist_name", "album"):
        value = metadata.get(key)
        if isinstance(value, str):
            cleaned = sanitize_field(value, _METADATA_FIELDS[key], source, artist=artist)
            metadata[key] = cleaned if cleaned or not value else None
    if is_enabled(FEAT_TO_CREDITS, source):
        from utils.database.song_duplicates import split_feat_credits
        title, feat_artists = split_feat_credits(metadata.get("title") or "")
        metadata["title"] = title or None
        metadata["feat_artists"] = feat_artists
    return metadata
