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
    parts = re.split(r" [–\-_] ", name, maxsplit=1)
    if len(parts) < 2:
        return None, None
    
    artist, title = parts
    print(f"normalizer - artist: {artist}")
    print(f"normalizer - title: {title}")

    return artist, title

def strip_brackets(title: str):
    print("Stripping title")
    if " (" not in title:
        print("Can't strip brackets")
        return None
    new_title, _ = title.split(" (", maxsplit=1)
    # new_title = re.split(r' \(| {2}\|', title, maxsplit=1)[0]
    print(title)
    print(new_title)
    if new_title:
        print("returning new title")
        return new_title
    else:
        print("returning none")
        return None