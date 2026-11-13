"""What each name-cleanup step does to a text - the functions only; which
step runs when, for which source and in which order is pipeline.py. Pure
text: no settings, no database."""
import html
import re
from functools import lru_cache
from pathlib import Path

from config.constants import (
    BAND_WORD_NAME_CONTINUATIONS,
    FILE_NAME_JUNK_FILE,
    GENERIC_TOPIC_CHANNELS,
    SITE_NAME_TLDS,
    TITLE_JUNK_SUFFIXES,
)
from utils.common.normalizer import fold_diacritics
from utils.common.title_patterns import PIPE, YEAR, YOUTUBE_BRACKET_PAIRS, YOUTUBE_BRACKETED


# ==================== Download-site names, separators, encodings ====================

_SITE_NAME_RE = re.compile(
    r"[(\[]?\s*\b[\w-]+(?:\.[\w-]+)*\.(?:" + "|".join(SITE_NAME_TLDS) + r")\b\s*[)\]]?", re.IGNORECASE)
_EDGE_SEPARATORS_RE = re.compile(r"^[\s\-–—_|:]+|[\s\-–—_|:]+$")
# What Y2Mate appends to a file name: the video id, the bitrate and a
# timestamp in ms ("...Blues)-AzEBH6DZJVk-160k-1645418932557")
_CONVERTER_SUFFIX_RE = re.compile(r"-[A-Za-z0-9_-]{11}-\d{2,3}k-\d{10,}$")
# Two separators left touching once a word between them is gone ("A -  - B")
_DOUBLED_SEPARATOR_RE = re.compile(r"\s+[-–—|]\s+(?:[-–—|]\s+)+")


@lru_cache(maxsize=1)
def _junk_words_re():
    """FILE_NAME_JUNK_FILE's words as one pattern (None when there are none),
    with a bracket around them if any."""
    path = Path(__file__).resolve().parents[3] / "config" / FILE_NAME_JUNK_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    words = sorted({w.strip() for w in lines if w.strip() and not w.lstrip().startswith("#")}, key=len, reverse=True)
    if not words:
        return None
    alternatives = "|".join(re.escape(w) for w in words)
    return re.compile(r"[(\[]?\s*(?<![\w.])(?:" + alternatives + r")(?![\w.])\s*[)\]]?", re.IGNORECASE)


def _drop_junk(text: str) -> str:
    text = _CONVERTER_SUFFIX_RE.sub("", text)
    text = _SITE_NAME_RE.sub(" ", text)
    if (words := _junk_words_re()) is not None:
        text = words.sub(" ", text)
    return _trim(text)


def _trim(text: str) -> str:
    text = _DOUBLED_SEPARATOR_RE.sub(" - ", text)
    return _EDGE_SEPARATORS_RE.sub("", re.sub(r"\s{2,}", " ", text))


# A separator left dangling at the end: an underscore (a "?" a file name
# couldn't hold, "Da li želiš_") or a dash / bar / colon after a space
_DANGLING_SEPARATOR_RE = re.compile(r"(?:\s+[-–—|:]+|_+)\s*$")


def trim_dangling_separator(text: str) -> str:
    """`text` without a separator dangling at its end ("Da li želiš_",
    "... - Piano Solo -"); unchanged when it starts with one too (a styled
    "- I m p u l s i o n -"), when the separator closes a part
    ("暁月夜-アカツキヅクヨ-", no space before it) or when nothing but
    punctuation is left (":/", "-!-")."""
    if not text:
        return text
    found = _DANGLING_SEPARATOR_RE.search(text)
    if not found or text[:1] in "-–—_|:" or not re.search(r"[^\W_]", text[:found.start()]):
        return text
    return text[:found.start()].rstrip()


def has_site_name(text: str) -> bool:
    """Does `text` hold a download-site name, a FILE_NAME_JUNK_FILE word or
    Y2Mate's suffix - what strip_site_names() takes out?"""
    if not text:
        return False
    words = _junk_words_re()
    return bool(_CONVERTER_SUFFIX_RE.search(text) or _SITE_NAME_RE.search(text) or (words and words.search(text)))


# Polish (Windows-1250) tags read as Latin-1 or Windows-1252: "Ma³pa" for
# "Małpa". These never stand in real text - "³" / "¹" (ł / ą), "¯" (Ż),
# control characters 0x80-0x9F (ś, ź, Ś, Ź, ...), "¿" inside a word (ż) -
# while "ñ", "ê", "ó" do, so only one of them triggers re-reading the whole
# text as Windows-1250. A text with none ("Mêskie" for "Męskie") stays,
# unless another field of the same record shows one (`force`).
_CP1250_MOJIBAKE_RE = re.compile(r"[³¹¯\x80-\x9f]|(?<=\w)¿")


def has_cp1250_mojibake(text: str) -> bool:
    """Does `text` hold a character only Polish mis-read as Latin-1 has?"""
    return bool(text) and bool(_CP1250_MOJIBAKE_RE.search(text))


def fix_cp1250_mojibake(text: str, force: bool = False) -> str:
    """`text` re-read as Windows-1250 when it's Polish mis-read as Latin-1
    or Windows-1252 ("Ma³pa i ja" -> "Małpa i ja", "Wspania³oœæ" ->
    "Wspaniałość"); unchanged otherwise. `force`: re-read it even without a
    sure sign ("Mêskie" -> "Męskie"), for a field of a record another field
    of which has one - correct Polish text is left alone either way, since
    its "ę", "ł" can't be encoded as Latin-1."""
    if not text or not (force or has_cp1250_mojibake(text)):
        return text
    for misread_as in ("latin-1", "cp1252"):  # cp1252: "œ" for ś, which Latin-1 lacks
        try:
            return text.encode(misread_as).decode("cp1250")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return text


def fix_cp1250_mojibake_by_words(text: str, known_words: set[str]) -> str:
    """`text` re-read as Windows-1250 without a sure sign, when every word
    the re-reading changes then is a word the database already has
    (`known_words`, casefolded): "Mêskie Granie" -> "Męskie Granie", while
    "Efêmera" ("Efęmera" nowhere) stays."""
    fixed = fix_cp1250_mojibake(text, force=True)
    if fixed == text:
        return text
    old, new = text.split(), fixed.split()
    changed = [n.casefold() for o, n in zip(old, new) if o != n]
    if len(old) != len(new) or not all(_WORD_EDGES_RE.sub("", w) in known_words for w in changed):
        return text
    return fixed


_WORD_EDGES_RE = re.compile(r"^\W+|\W+$")


def strip_site_names(text: str) -> str:
    """`text` without download-site names ("y2meta.com - David Dean
    Burkhart Playlist" -> "David Dean Burkhart Playlist", "Song (greenmp3.com)"
    -> "Song"), the words in FILE_NAME_JUNK_FILE ("Y2Mate.is") and Y2Mate's
    "-<video id>-160k-<timestamp>" suffix; separators left over trimmed; unchanged when
    nothing else would be left."""
    if not text:
        return text
    cleaned = _drop_junk(text)
    return cleaned or text


def is_site_name(text: str) -> bool:
    """Is `text` nothing but download-site names ("y2meta.com")?"""
    return bool(text) and not _drop_junk(text).strip()


_TOPIC_CHANNEL_RE = re.compile(r"\s+-\s+topic\s*$", re.IGNORECASE)


def strip_topic_channel(artist: str) -> str:
    """An artist named after their auto-generated YouTube channel, "Igo -
    Topic" -> "Igo". A generic channel ("Release - Topic") stays: what's left
    wouldn't be an artist. Spaces around the dash required: "Hot-Topic"."""
    base = _TOPIC_CHANNEL_RE.sub("", artist)
    return base if base.strip() and base.strip().lower() not in GENERIC_TOPIC_CHANNELS else artist


_BAND_WORD_RE = re.compile(r"\s*zesp[óo][łl]\s+(?=\S)", re.IGNORECASE)


def strip_band_word(artist: str) -> str:
    """An artist named "band <Name>" by their channel, "Zespół Hurt" -> "Hurt".
    Stays when the word is part of the name ("Zespół Pieśni i Tańca Śląsk") or
    the whole of it ("Zespół")."""
    start = _BAND_WORD_RE.match(artist)
    if not start:
        return artist
    rest = artist[start.end():]
    return artist if rest.split()[0].casefold() in BAND_WORD_NAME_CONTINUATIONS else rest


# ==================== Junk phrases at the end ====================

_TITLE_JUNK_SUFFIX_RE = re.compile(
    r"\s*\b(?:" + "|".join(re.escape(p) for p in TITLE_JUNK_SUFFIXES) + r")\s*$",
    re.IGNORECASE,
)

# A separator a junk phrase leaves dangling ("Secret Youth - Music Video")
_JUNK_LEFTOVER_SEPARATOR_RE = re.compile(r"\s*[-–—:|]+$")


def strip_trailing_junk_suffix(title: str) -> str:
    """Repeatedly strip a trailing phrase from TITLE_JUNK_SUFFIXES off the
    end of `title`, handling more than one stacked suffix (e.g. "Song Music
    Video Official Audio"). Returns `title` unchanged if nothing matches.
    A separator the phrase leaves dangling goes with it ("Secret Youth -
    Music Video" -> "Secret Youth").
    """
    title = (title or "").strip()
    while True:
        new_title = _TITLE_JUNK_SUFFIX_RE.sub("", title).strip()
        if new_title == title:
            return title
        title = _JUNK_LEFTOVER_SEPARATOR_RE.sub("", new_title)


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
    "Video",
    # Says who uploaded it again, not which version (real case:
    # "DISCO BANIA - BRUNETKI I BLONDYNKI (reupload HD)")
    "Reupload",
    "Re-upload",
]
# A bracket naming a version of the song is never junk, whatever else it
# says: "(live at Le Bataclan, Paris, France, 6/25/1998)", "(Official
# Acoustic Live Video)". "From" was a marker word once: "(from “Chess”)",
# "(WHERE ARE YOU FROM?)" are titles or the work a song is from
VERSION_MARKER_RE = re.compile(
    r"\b(?:Live|Cover|Remix|Acoustic|Extended|Club Edit|Unplugged|Demo|Instrumental)\b", re.IGNORECASE)
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
# double quotes - e.g. '『Cinderella by Cidergirl』') - uploaders wrap the song
# name in them fairly often (e.g. 'Night Club - "Need You Tonight" (INXS
# cover)'), and that's never meaningful punctuation worth keeping in the
# stored title.
_DOUBLE_QUOTE_RE = re.compile(r'["“”„‟『』]')

# Single quotes wrap a title too ("M.O.O.N. - 'Hydrogen' [Hotline Miami
# Soundtrack]"), but they are also apostrophes ("Rock 'n' Roll", "'Bout Time",
# "’74–’75"), so only a sure pair counts: the mark the title starts with and
# one closing the title or standing before a bracket, with nothing but
# apostrophes inside a word between them ("'Don't Stop'")
_SINGLE_QUOTE = "'‘’‚‛"
_SINGLE_QUOTED_RE = re.compile(
    rf"[{_SINGLE_QUOTE}]((?:[^{_SINGLE_QUOTE}]|(?<=\w)[{_SINGLE_QUOTE}](?=\w))*[^{_SINGLE_QUOTE}\s])[{_SINGLE_QUOTE}]"
    rf"(\s*(?:$|[{re.escape(''.join(YOUTUBE_BRACKET_PAIRS))}].*))"
)


def strip_quote_marks(title: str) -> str:
    """Unwrap a quoted title: the double-quote-like mark `title` starts with
    and the next one ('"Need You Tonight" (INXS cover)' -> 'Need You Tonight
    (INXS cover)'), or a sure pair of single quotes ("'Hydrogen'" ->
    'Hydrogen'); quotes inside a title stay ('Take The "A" Train'). The
    whitespace collapsed either way.
    """
    title = (title or "").strip()
    if title and _DOUBLE_QUOTE_RE.match(title):
        closing = _DOUBLE_QUOTE_RE.search(title, 1)
        title = title[1:closing.start()] + title[closing.end():] if closing else title[1:]
    elif single_quoted := _SINGLE_QUOTED_RE.fullmatch(title):
        title = single_quoted.group(1) + single_quoted.group(2)
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
    "Visualizer",
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
        if YOUTUBE_TITLE_JUNK_MARKER_ANYWHERE_RE.search(match.group(1)) and not VERSION_MARKER_RE.search(match.group(1)):
            return ""
        return match.group(0)

    title = ANY_BRACKET_RE.sub(_replace, title or "")
    return re.sub(r"\s+", " ", title).strip()


# A producer's credit in a bracket: "Sam (prod. Gibbs)", "Одна (prod.by LFox)",
# "PATOLOVE (PROD. AETHERBOY1)", "(produced by X)" - "prod" a whole word, so
# "(Prodigy Remix)" and "(Producer's Cut)" stay. Nothing keeps the name (owner,
# 2026-11-12): LRCLIB and Genius don't have it in their titles, Leina Radio
# shouldn't show it as one
_PRODUCER_CREDIT_RE = re.compile(r"^\s*(?:prod\b\.?|produced\s+by\b)", re.IGNORECASE)


def strip_producer_credit(title: str) -> str:
    """`title` without its producer credits, wherever they stand; a title that
    is nothing but one stays."""
    if not any(_PRODUCER_CREDIT_RE.match(m.group(1)) for m in ANY_BRACKET_RE.finditer(title or "")):
        return title
    stripped = ANY_BRACKET_RE.sub(lambda m: "" if _PRODUCER_CREDIT_RE.match(m.group(1)) else m.group(0), title)
    return re.sub(r"\s+", " ", stripped).strip() or title


# A "|" in a YouTube title is routinely used to tack on channel/label
# branding after the actual song info (e.g. "OMNIMAR - The Matrix (Official
# Video) | darkTunes Music Group") - never part of the song title itself, so
# everything from the first one onward is dropped. Doing this before
# strip_junk_suffix() also lets a junk bracket that only *looks* trailing
# once the branding is gone (like "(Official Video)" above) get caught by
# the existing trailing-bracket check.
_PIPE_RE = re.compile(PIPE)
_PIPE_SUFFIX_RE = re.compile(r"\s*" + PIPE + r".*$")
# The separator split_artist_title() looks for first
_SPACED_SEPARATOR_RE = re.compile(r" [–—\-_] ")


def strip_pipe_suffix(title: str) -> str:
    """Drop a trailing "| ..." branding suffix from `title`, if present."""
    return _PIPE_SUFFIX_RE.sub("", title or "").strip()


def lists_artists_with_pipes(text: str) -> bool:
    """A whole video title whose only " - " comes after its first "|": the
    "|" lists the artists rather than starting a suffix, the song is behind
    the dash ("MIUOSH | SMOLIK | NOSPR - Wizje", which used to leave "MIUOSH",
    read as a title of the channel)."""
    pipe = _PIPE_RE.search(text)
    return bool(pipe and not _SPACED_SEPARATOR_RE.search(text[:pipe.start()])
                and _SPACED_SEPARATOR_RE.search(text, pipe.end()))


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
    ("pejzaz_bez_ciebie.mpg" -> "Pejzaz Bez Ciebie"), when every word is
    letters only."""
    text = MEDIA_EXTENSION_RE.sub("", (text or "").strip()).strip()
    words = [w for w in text.split("_") if w]
    # Only words of letters: "2_00", "#ВІЛЬНА_КАСА" and a release name
    # "felix_da_housecat-silver_screen" are no file-name artefact to undo
    if "_" in text and " " not in text and len(words) > 1 and all(w.isalpha() for w in words):
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


def strip_title_year(title: str) -> str:
    """`title` without the year it ends with - a last bracket holding one
    ("Legenda (1991)", "[театр PipinO, 2013]", "(1988, HQ)"; a version, "(live
    ... 1998)", stays) or " - 1982" - repeatedly; unchanged when nothing
    else would be left ("(1991)")."""
    original = title = (title or "").strip()
    while True:
        match = _TRAILING_BRACKET_RE.search(title)
        if match and _YOUTUBE_TITLE_YEAR_RE.search(match.group(1)) and not VERSION_MARKER_RE.search(match.group(1)):
            title = title[:match.start()].strip()
            continue
        if (shorter := strip_trailing_dash_year(title)) != title:
            title = shorter
            continue
        return title or original


def strip_junk_suffix(title: str) -> str:
    """Repeatedly strip a trailing bracket group from `title` as long as it's
    junk - either its content matches YOUTUBE_TITLE_JUNK_PHRASES exactly or it
    contains one of YOUTUBE_TITLE_JUNK_MARKER_WORDS anywhere (a year is
    strip_title_year()'s) - handles a title decorated with more than one such
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
        if VERSION_MARKER_RE.search(content) or not (is_exact_junk or has_marker_word):
            break
        title = title[:match.start()].strip()
    return title


# Opening->closing bracket characters that strip_artist_from_title() will
# drop along with an artist-name match they wrap entirely (e.g. the "【】" in
# "Ado - 【Ado】 unravel 歌いました" exist only to enclose the artist name, so
# leaving them behind after the name is removed would strand an empty
# "【】" in the title).
_ARTIST_WRAPPING_BRACKETS = YOUTUBE_BRACKET_PAIRS


_LEADING_QUOTES = " \"'“”„"
# What follows an artist name the title starts with ("" = nothing more)
_AFTER_ARTIST = ("-", "–", "—", ":", "/", "|", '"', "“", "„", "'", "")


def strip_artist_from_title(title: str, artist: str) -> str:
    """If the song title itself repeats the artist's name, drop that
    occurrence - real case found testing this: channel "SIAMES", video
    titled 'SIAMÉS "Summer Nights" [Official Animated Video]' (the bare
    video title already includes the artist name, redundant once it's
    already stored separately as the song's artist).

    Diacritic-insensitive on both sides (fold_diacritics()) since the
    channel name and the same name embedded in a title aren't always spelled
    identically, as in the SIAMES/SIAMÉS case above. Only removes a whole
    word/phrase match (not a substring of some other word), and leaves the
    title untouched entirely if that would empty it out.
    """
    if not title or not artist:
        return title

    folded_title = fold_diacritics(title)
    folded_artist = fold_diacritics(artist).strip()
    if not folded_artist:
        return title

    # Only where it stands for the artist: at the start, before a separator
    # or a quoted title ("Lynyrd Skynyrd - Free Bird", 'SIAMÉS "Summer
    # Nights"'), or alone in a bracket ("Hard Times (Paramore)") - never
    # inside the title ("White Queen" by Queen, "Feng Suave EP")
    for match in re.finditer(r"\b" + re.escape(folded_artist) + r"\b", folded_title, re.IGNORECASE):
        start, end = match.start(), match.end()
        wrapped = (start > 0 and title[start - 1] in _ARTIST_WRAPPING_BRACKETS and end < len(title)
                   and title[end] == _ARTIST_WRAPPING_BRACKETS[title[start - 1]])
        at_start = not title[:start].strip(_LEADING_QUOTES) and title[end:].lstrip()[:1] in _AFTER_ARTIST
        if wrapped or at_start:
            break
    else:
        return title
    if wrapped:
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
    remainder = re.sub(r'^[\s\-:–—/|]+', "", remainder)
    remainder = re.sub(r'[\s\-:–—/|]+$', "", remainder)
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


# ==================== Characters ====================

# An HTML entity, always ending in ";" - html.unescape() alone also reads
# "&#3" in "Kushi&#3" (a title) as a control character
_HTML_ENTITY_RE = re.compile(r"&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")


def unescape_entities(text: str) -> str:
    """`text` with its HTML entities as characters ("Major Lazer &amp; DJ
    Snake" -> "Major Lazer & DJ Snake"); "&" not starting one stays."""
    return _HTML_ENTITY_RE.sub(lambda m: html.unescape(m.group(0)), text or "")
