import re
import unicodedata
from functools import lru_cache
from typing import Optional
from pathlib import Path


# Small mapping for special latin letters that don't decompose nicely
_SPECIAL_REPLACEMENTS = {
    "\u00df": "ss",
    "\u00c6": "AE",
    "\u00e6": "ae",
    "\u0152": "OE",
    "\u0153": "oe",
    "\u00d8": "O",
    "\u00f8": "o",
    "\u0141": "L",
    "\u0142": "l",
    "\u0110": "D",
    "\u0111": "d",
}

# Letters that don't NFKD-decompose ("ł" is a base letter of its own in
# Unicode) but have one unambiguous plain-Latin equivalent worth substituting
# directly: an ASCII-typed "Milosc" means "Miłość". "ø" too: "Ørganek"
# is the stylized name of Organek, whose uploads all spell it with a plain "O".
NON_DECOMPOSING_LETTER_FOLDS = str.maketrans({"\u0142": "l", "\u0141": "L", "\u00f8": "o", "\u00d8": "O"})


def fold_diacritics(text: str) -> str:
    """`text` without accents, one character for one - nothing else changed,
    so a match in the folded text is at the same positions in `text`
    (sanitize's strip_artist_from_title() relies on that). NFKD, then the
    combining marks dropped; a character that doesn't decompose to exactly one
    base character stays, except "ł" / "ø" (NON_DECOMPOSING_LETTER_FOLDS).
    "Niesmiertelnosc" meets "Nieśmiertelność", "SIAMES" "SIAMÉS", and the typo
    "набіраи" "набірай" ("й" is "и" + a breve)."""
    def fold(ch: str) -> str:
        base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        return base if len(base) == 1 else ch
    return "".join(map(fold, text)).translate(NON_DECOMPOSING_LETTER_FOLDS)


# Every apostrophe-like character, for normalize() (dropped, or a space) and
# normalize_apostrophes() (one canonical "'") alike
APOSTROPHES = "'’‘‚‛`´ʻʼʹʽ′‵＇"
_APOSTROPHE_RE = re.compile(f"[{re.escape(APOSTROPHES)}]")

ACCEPTED_WORDS = {"of", "the", "in", "for", "to", "on", "as", "a", "and", "remix", "by"}


# Zero-width joiner: glues emoji sequences into one glyph, so stored text keeps
# it (keep_joiner=True); comparisons drop it with everything else.
_ZERO_WIDTH_JOINER = "\u200d"


# ™ ® ℠ © ℗
_MARK_SYMBOLS = {ord(c): None for c in "\u2122\u00ae\u2120\u00a9\u2117"}


def composed(s: Optional[str]) -> Optional[str]:
    """`s` in Unicode NFC: "a" + a combining accent (U+0301) becomes the one
    letter "á". Looks the same, but MusicBrainz's search found nothing for a
    decomposed title ("Abrásame" by Бумбокс, from a YouTube video title).
    music.db stores names only in this form (datatables' validators)."""
    return unicodedata.normalize("NFC", s) if s else s


def strip_format_characters(s: str, keep_joiner: bool = False) -> str:
    """Drop invisible Unicode format characters (category Cf): zero-width
    space, soft hyphen, joiners, direction marks."""
    return "".join(
        c for c in s
        if unicodedata.category(c) != "Cf" or (keep_joiner and c == _ZERO_WIDTH_JOINER)
    )


def normalize(
    s: Optional[str],
    *,
    lower: bool = True,
    strip_apostrophe: bool = True,
    collapse_spaces: bool = True,
    remove_punctuation: bool = True,
) -> str:
    """Normalize a string for database lookup and comparisons - see
    _normalize(). Cached: the database search normalizes every artist name
    again for each query (database_getter._normalized_python_filter), which
    made matching a whole music library spend ~85% of its time here."""
    if type(s) is not str:
        return _normalize.__wrapped__(s, lower=lower, strip_apostrophe=strip_apostrophe,
                                      collapse_spaces=collapse_spaces, remove_punctuation=remove_punctuation)
    return _normalize(s, lower=lower, strip_apostrophe=strip_apostrophe,
                      collapse_spaces=collapse_spaces, remove_punctuation=remove_punctuation)


@lru_cache(maxsize=1 << 16)
def _normalize(
    s: Optional[str],
    *,
    lower: bool = True,
    strip_apostrophe: bool = True,
    collapse_spaces: bool = True,
    remove_punctuation: bool = True,
) -> str:
    """Normalize a string for database lookup and comparisons.

    Steps:
    - None -> empty string
    - Unicode normalization (NFKD) and removal of combining marks and
      invisible format characters (zero-width space etc.)
    - Replace a handful of special letters which don't decompose as desired
    - Optionally strip or replace apostrophes
    - Optionally remove punctuation (keep word chars and whitespace)
    - Collapse multiple whitespace to single space and trim
    - Optionally lowercase

    This function aims to centralize normalization rules used across the app.
    """
    if s is None:
        return ""

    # Trademark/copyright marks are never part of a name ("Cope™"), and NFKD
    # would otherwise expand "™" into the letters "TM" ("copetm")
    s = s.translate(_MARK_SYMBOLS)

    # Normalize unicode to decompose accents
    s = unicodedata.normalize("NFKD", s)

    # Remove combining diacritics, and invisible format characters (Unicode
    # category Cf: zero-width space, soft hyphen, joiners, direction marks).
    # Dropped rather than turned into a space below: Bandcamp-style text puts
    # U+200B *inside* words ("Cz\u200bô\u200brny"), which would otherwise
    # normalize to "cz o rny" and stop matching "Czôrny".
    s = strip_format_characters("".join(c for c in s if not unicodedata.combining(c)))

    # Apply special replacements
    for src, dst in _SPECIAL_REPLACEMENTS.items():
        if src in s:
            s = s.replace(src, dst)

    # Apostrophes and similar characters
    if strip_apostrophe:
        s = _APOSTROPHE_RE.sub("", s)
    else:
        s = _APOSTROPHE_RE.sub(" ", s)

    # Replace some punctuation with space and optionally remove other punctuation
    if remove_punctuation:
        # Keep word characters and whitespace; replace everything else with space
        s = re.sub(r"[^\w\s]", " ", s)
    else:
        # Only normalize common separators to spaces
        s = re.sub(r"[-_.,()]", " ", s)

    if collapse_spaces:
        s = re.sub(r"\s+", " ", s).strip()

    if lower:
        s = s.lower()

    return s


def name_key(s: Optional[str]) -> str:
    """normalize() of an artist name with a bracketed part, a leading "the"
    and all spaces dropped: "The S.O.S. Band" and "SOS Band" both give
    "sosband", "Cody・Lee(李)" gives "codylee". Only for an exact-equality
    check - two different names never collide in practice, while plain
    similarity rates those pairs 0.70-0.74."""
    key = normalize((s or "").split("(")[0])
    return re.sub(r"^the ", "", key).replace(" ", "")


def compare(a: Optional[str], b: Optional[str], *, threshold: int = 100) -> bool:
    """Simple equality-based comparator after normalization.

    threshold is kept for API compatibility; currently only exact match is used.
    """
    na = normalize(a)
    nb = normalize(b)
    if not na or not nb:
        return False
    return na == nb

def split_artist_title(name: str) -> tuple[str, str] | tuple[None, None]:
    """Split a free-form "Artist - Title" style string into (artist, title).

    Used both for mp3 filenames (via extract_unknown_data, given the file's
    stem) and for YouTube video titles (which follow the same loose
    convention but aren't backed by a file at all).

    Three stages, each tried only when the one before finds no separator:
    a spaced " - " / " – " / " — " / " _ ", then a bare "-" / "~" / "_"
    (_bare_separator()), then two or more spaces. Both parts come back
    stripped; (None, None) when no stage finds one, or either part is empty.
    """
    parts = re.split(r" [–—\-_] ", name, maxsplit=1)
    if len(parts) < 2:
        at = _bare_separator(name)
        if at is not None:
            parts = [name[:at], name[at + 1:]]
        else:
            parts = re.split(r"\s{2,}", name, maxsplit=1)
            if len(parts) < 2:
                return None, None

    artist, title = (p.strip(" -_~") for p in parts)
    if not artist or not title:
        return None, None
    return artist, title


def _bare_separator(name: str) -> int | None:
    """Where split_artist_title()'s second stage splits: the index of a bare
    "-" / "~" / "_", or None.

    One touching a space wins ("Die Ärzte _Unrockbar_"), then "-", "~" and
    "_" in that order, each leftmost first ("AC_DC-Thunderstruck" splits at
    the "-"). A separator tying a single letter to a word, with no space
    beside it, belongs to the name and never splits: "Jay-Z Song" is not
    ("Jay", "Z Song"), "X-Ray Spex-Oh Bondage" splits at the second "-".
    """
    def run(index, step):
        # Letters/digits from index on in direction step
        length = 0
        while 0 <= index < len(name) and name[index].isalnum():
            length += 1
            index += step
        return length

    candidates = []
    for at, char in enumerate(name):
        if char not in "-~_":
            continue
        spaced = (at > 0 and name[at - 1].isspace()) or (at + 1 < len(name) and name[at + 1].isspace())
        if not spaced and 1 in (run(at - 1, -1), run(at + 1, 1)):
            continue
        candidates.append((not spaced, "-~_".index(char), at))
    return min(candidates)[2] if candidates else None


def extract_unknown_data(filepath: Path):
    """(artist, title) read off a file's name, cleaned by sanitize's
    "file_name" steps first."""
    from utils.common.sanitize import SOURCE_FILE_NAME, sanitize_raw  # sanitize imports this module
    return split_artist_title(sanitize_raw(filepath.stem, SOURCE_FILE_NAME))

def strip_brackets(title: str):
    """The title before its first " (" or " [", or None when it has none
    (or nothing comes before it)."""
    # Find the earliest occurrence of either bracket type
    round_pos = title.find(" (")
    square_pos = title.find(" [")
    
    if round_pos == -1 and square_pos == -1:
        return None
    
    # Pick the earliest bracket that exists
    if round_pos == -1:
        split_str = " ["
    elif square_pos == -1:
        split_str = " ("
    else:
        split_str = " (" if round_pos <= square_pos else " ["
    
    new_title, _ = title.split(split_str, maxsplit=1)
    return new_title or None

    
def normalize_apostrophes(text: str) -> str:
    """Replace all apostrophe-like characters with a single canonical one."""
    return _APOSTROPHE_RE.sub("'", text)

def _words_match(old_word: str, new_word: str) -> bool:
    """A word pair is acceptable if identical, or if the new word is an
    accepted (lowercase) word and the old word is just a different-case
    variant of it. Correcting TOWARDS the accepted form is fine; the
    reverse (moving away from it) is not.
    """
    if old_word == new_word:
        return True
    return new_word in ACCEPTED_WORDS and old_word.lower() == new_word

def rule_apostrophe_and_common_word_diff(old_title: str, new_title: str) -> bool:
    """Auto-confirm if, after canonicalizing apostrophes, the only
    remaining differences are casing corrections on ACCEPTED_WORDS."""
    old_words = normalize_apostrophes(old_title).split()
    new_words = normalize_apostrophes(new_title).split()

    if len(old_words) != len(new_words):
        return False

    return all(_words_match(ow, nw) for ow, nw in zip(old_words, new_words))

def rule_all_caps_correction(old_title: str, new_title: str) -> bool:
    """Auto-confirm if the old title is written in ALL CAPS and the
    correction is purely a casing fix (e.g. "LA TORTURA" -> "La tortura"),
    not a change to the underlying letters or words."""
    old_norm = normalize_apostrophes(old_title)
    new_norm = normalize_apostrophes(new_title)

    if not old_norm.isupper():
        return False

    return old_norm.upper() == new_norm.upper()