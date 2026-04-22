import re
import unicodedata
from typing import Optional
import tkinter as tk

_SPECIAL_REPLACEMENTS = {
    "ß": "ss",
    "Æ": "AE",
    "æ": "ae",
    "Œ": "OE",
    "œ": "oe",
    "Ø": "O",
    "ø": "o",
    "Ł": "L",
    "ł": "l",
    "Đ": "D",
    "đ": "d",
    "Þ": "Th",
    "þ": "th",
}

def copy_to_clipboard(message: str):
    root = tk.Tk()
    root.withdraw()
    root.clipboard_clear()
    root.clipboard_append(message)
    root.update
    root.destroy

def _is_word_substring(needle: str, haystack: str) -> bool:
    """Return True if needle appears as whole words within haystack."""
    pattern = r'\b' + re.escape(needle) + r'\b'
    return bool(re.search(pattern, haystack))


def normalize_text(s: str) -> str:
    """Normalize text for comparison.

    Steps:
    - NFKD unicode normalization and removal of combining marks (diacritics)
    - Apply a small mapping for special Latin letters that don't decompose
    - Convert to lower-case
    - Collapse whitespace
    """
    if s is None:
        return ""

    # Normalize and decompose characters
    s = unicodedata.normalize("NFKD", s)

    # Remove combining marks
    s = "".join(c for c in s if not unicodedata.combining(c))

    # Apply special replacements (for characters that remain non-ASCII)
    for src, dst in _SPECIAL_REPLACEMENTS.items():
        if src in s:
            s = s.replace(src, dst)

    # Remove any remaining non-letter/digit characters except spaces
    s = re.sub(r"[^\w\s]", " ", s)

    # Collapse whitespace and lower-case
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def compare_strings(a: Optional[str], b: Optional[str]) -> bool:
    """Compare two strings with normalization.

    Returns True when the normalized strings are equal or when one is a
    whole-word substring of the other (case-insensitive, diacritics-insensitive).

    Examples:
    - compare_strings('Żółć', 'zolc') -> True
    - compare_strings('The Prisonners', 'prison') -> False  (partial word, no longer matches)
    - compare_strings('one', 'the one') -> True  (whole word match)
    - compare_strings('one', 'bones') -> False  (inside a word, rejected)
    """
    na = normalize_text(a or "")
    nb = normalize_text(b or "")

    if not na or not nb:
        return False

    if na == nb:
        return True

    # Check whole-word substring in both directions
    if _is_word_substring(na, nb) or _is_word_substring(nb, na):
        return True

    return False


def truncate_at_word(text: str) -> str:
    stop_words=[
        "feat",
        "&",
        "ft.",
        " и "
    ]

    earliest_index = len(text)
 
    for word in stop_words:
        idx = text.lower().find(word.lower())
        if idx != -1 and idx < earliest_index:
            earliest_index = idx
 
    return text[:earliest_index].rstrip()