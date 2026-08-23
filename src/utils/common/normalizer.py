import re
import unicodedata
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

APOSTROPHES = "'’‘‚‛`´ʻʼʹʽ′‵"

ACCEPTED_WORDS = {"of", "the", "in", "for", "to", "on", "as", "a", "and", "remix"}


def normalize(
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
    - Unicode normalization (NFKD) and removal of combining marks
    - Replace a handful of special letters which don't decompose as desired
    - Optionally strip or replace apostrophes
    - Optionally remove punctuation (keep word chars and whitespace)
    - Collapse multiple whitespace to single space and trim
    - Optionally lowercase

    This function aims to centralize normalization rules used across the app.
    """
    if s is None:
        return ""

    # Normalize unicode to decompose accents
    s = unicodedata.normalize("NFKD", s)

    # Remove combining diacritics
    s = "".join(c for c in s if not unicodedata.combining(c))

    # Apply special replacements
    for src, dst in _SPECIAL_REPLACEMENTS.items():
        if src in s:
            s = s.replace(src, dst)

    # Apostrophes and similar characters
    if strip_apostrophe:
        s = re.sub(r"[''`\u00b4\u02bc\u02bb\uff07\u2018\u2019\u201a\u201b\u2032\u2035]", "", s)
    else:
        s = re.sub(r"[''`\u00b4\u02bc\u02bb\uff07\u2018\u2019\u201a\u201b\u2032\u2035]", " ", s)

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


def compare(a: Optional[str], b: Optional[str], *, threshold: int = 100) -> bool:
    """Simple equality-based comparator after normalization.

    threshold is kept for API compatibility; currently only exact match is used.
    """
    na = normalize(a)
    nb = normalize(b)
    if not na or not nb:
        return False
    return na == nb

def extract_unknown_data(filepath: Path):

    name = filepath.stem
    print(name)
    parts = re.split(r" [–—\-_] ", name, maxsplit=1)
    if len(parts) < 2:
        # Secondary fallback: hyphen/underscore without surrounding spaces
        parts = re.split(r"[-_]", name, maxsplit=1)
        if len(parts) < 2:
            # Tertiary fallback: two or more consecutive spaces
            parts = re.split(r"\s{2,}", name, maxsplit=1)
            if len(parts) < 2:
                return None, None

            artist, title = (p.strip() for p in parts)
            if not artist or not title:
                return None, None

            return artist, title

        artist, title = (p.strip(" -_") for p in parts)
        if not artist or not title:
            return None, None

        return artist, title

    artist, title = parts

    return artist, title

def strip_brackets(title: str):
    print("Stripping title")
    
    # Find the earliest occurrence of either bracket type
    round_pos = title.find(" (")
    square_pos = title.find(" [")
    
    if round_pos == -1 and square_pos == -1:
        print("Can't strip brackets")
        return None
    
    # Pick the earliest bracket that exists
    if round_pos == -1:
        split_str = " ["
    elif square_pos == -1:
        split_str = " ("
    else:
        split_str = " (" if round_pos <= square_pos else " ["
    
    new_title, _ = title.split(split_str, maxsplit=1)
    print(title)
    print(new_title)
    
    if new_title:
        print("returning new title")
        return new_title
    else:
        print("returning none")
        return None

    
def _normalize_apostrophes(text: str) -> str:
    """Replace all apostrophe-like characters with a single canonical one."""
    return re.sub(f"[{re.escape(APOSTROPHES)}]", "'", text)

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
    old_words = _normalize_apostrophes(old_title).split()
    new_words = _normalize_apostrophes(new_title).split()

    if len(old_words) != len(new_words):
        return False

    return all(_words_match(ow, nw) for ow, nw in zip(old_words, new_words))

def rule_all_caps_correction(old_title: str, new_title: str) -> bool:
    """Auto-confirm if the old title is written in ALL CAPS and the
    correction is purely a casing fix (e.g. "LA TORTURA" -> "La tortura"),
    not a change to the underlying letters or words."""
    old_norm = _normalize_apostrophes(old_title)
    new_norm = _normalize_apostrophes(new_title)

    if not old_norm.isupper():
        return False

    return old_norm.upper() == new_norm.upper()