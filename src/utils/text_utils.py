import re
import unicodedata
from typing import Optional
import tkinter as tk
from utils.debug import slog
import requests
from difflib import SequenceMatcher

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
        " и ",
        " i ",
        ", "
    ]

    earliest_index = len(text)
 
    for word in stop_words:
        idx = text.lower().find(word.lower())
        if idx != -1 and idx < earliest_index:
            earliest_index = idx
 
    return text[:earliest_index].rstrip()

def normalize_japanese_title(text: str) -> str:
    """
    Normalize Japanese text for fuzzy comparison.
    Handles middle dots, trailing punctuation, full-width chars, etc.
    """
    if not text:
        return text

    # Replace Japanese middle dot (・ U+30FB) and similar separators with a regular space
    text = re.sub(r'[・･•·\-+]', ' ', text)
    slog(text)

    # Remove trailing punctuation (periods, ellipsis, 。etc.)
    text = re.sub(r'[\.\。…]+$', '', text)

    # Normalize full-width alphanumerics to half-width
    # e.g. ＡＢＣＤ → ABCD, １２３ → 123
    text = unicodedata.normalize('NFKC', text)

    # Collapse multiple spaces
    text = re.sub(r'\s+', ' ', text).strip()

    return text

def make_fuzzy(text: str) -> str:
    """Split on spaces AND Japanese middle dot, append ~ to each token."""
    # Replace middle dot variants with space before splitting
    text = re.sub(r'[・･•·]', ' ', text)
    tokens = text.split()
    return " ".join(t + "~" for t in tokens if t)


def check_spelling(artist: str, title: str, threshold: float = 0.8) -> dict:
    """
    Queries MusicBrainz to verify/correct spelling.
    Includes fallback logic and extensive logging.
    """
    
    def similarity(a, b):
        a_norm = normalize_japanese_title(a)
        b_norm = normalize_japanese_title(b)
        return SequenceMatcher(None, a_norm.lower(), b_norm.lower()).ratio()

    fuzzy_title = make_fuzzy(title)
    fuzzy_artist = make_fuzzy(artist)
    slog(fuzzy_title)
    slog(fuzzy_artist)


    primary_query = f'recording:{fuzzy_title} AND artist:{fuzzy_artist}'
    # Fallback: General keyword search (more forgiving)
    fallback_query = f'{artist} {title}'
    
    url = "https://musicbrainz.org/ws/2/recording/"
    
    # MusicBrainz requires a proper User-Agent
    headers = {"User-Agent": "MyMusicApp/1.0.0 ( contact@example.com )"}

    def perform_search(q):
        params = {"query": q, "limit": 20, "fmt": "json"}
        slog(f"Attempting MB Query: {q}")
        resp = requests.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data.get("recordings", [])

    # Try Primary Search
    recordings = perform_search(primary_query)
    slog(f"Primary results count: {len(recordings)}")

    # If no results, try Fallback Search
    if not recordings:
        slog("No results for primary query. Trying fallback keyword search...")
        recordings = perform_search(fallback_query)
        slog(f"Fallback results count: {len(recordings)}")

    if not recordings:
        slog("No recordings found in both attempts.")
        return {"artist": artist, "title": title, "corrected": False, "found": False}

    # Process results
    best = recordings[0]
    # slog(best) # Log the full raw data of the best match to see structure

    mb_title = best.get("title", "")
    # Handle the complex artist-credit structure
    try:
        mb_artist = best["artist-credit"][0]["artist"]["name"]
    except (KeyError, IndexError):
        mb_artist = ""

    mb_score = int(best.get("score", 0))

    title_sim = similarity(title, mb_title)
    artist_sim = similarity(artist, mb_artist)
    
    slog(title_sim)
    slog(artist_sim)

    # Use threshold to decide on correction
    final_artist = mb_artist if artist_sim >= threshold else artist
    final_title = mb_title if title_sim >= threshold else title
    
    # We consider it corrected if the strings actually changed
    is_corrected = (final_artist != artist) or (final_title != title)

    result = {
        "input_artist": artist,
        "input_title": title,
        "corrected_artist": final_artist,
        "corrected_title": final_title,
        "corrected": is_corrected,
        "mb_score": mb_score,
        "title_similarity": round(title_sim, 2),
        "artist_similarity": round(artist_sim, 2),
        "found": True,
    }
    
    slog(result)
    return result


ALBUM_TITLE_BLACKLIST = {
    # Exact matches (lowercased)
    "now that's what i call music",
    "greatest hits",
    "best of",
    "the best of",
    "very best of",
    "the very best of",
    "essential",
    "the essential",
    "collection",
    "the collection",
    "gold",
    "platinum",
    "anthology",
    "retrospective",
    "definitive collection",
    "complete collection"
}

ALBUM_TITLE_BLACKLIST_SUBSTRINGS = {
    # Partial matches — if any of these appear in the title, skip it
    "greatest hits",
    "best of",
    "collection",
    "anthology",
    "retrospective",
    "compilation",
    "now that's what i call",
    "the essential",
    "pop party",
    "itunes",
    "remix",
    "germany",
    "festival",
    "United Palace Theatre",
    "1996-2011",
    "radio",
    "spotify",
    "paris",
    "session",
    "ultimate",
    "hits",
    "edition",
    "awards",
    "przebojów",
    "valentine's day",
    "exercises",
    "new orleans",
    "morrison",
    "2014",
    "women in music",
    "london, uk",
    "england",
    "collecion",
    "youtube",
    "essential",
    "live",
    "хит",
    "concert",
    "hottest",
    "liverpool",
    "volume",
    "reedycja",
    "edycja",
    "лучшие",
    "album",
    "albums",
    "references",
    "export",
    "import",
    "week",
    "versie",
    "rock",
    "speciale",
    "fifa",
    "official",
    "guest",
    "tour",
    "released",
    "version",
    "demos",
    "club",
    "liva",
    "remasters",
    "filmography",
    "videography",
    "music",
    "the best",
    "kolekcja",
    "collaborative singles"
}

def is_blacklisted_album(title: str) -> bool:
    slog(title)
    if title:
        lowered = title.lower().strip()
        if lowered in ALBUM_TITLE_BLACKLIST:
            slog("True")
            return True
        return any(re.search(r'\b' + re.escape(sub) + r'\b', lowered) for sub in ALBUM_TITLE_BLACKLIST_SUBSTRINGS)
    return False

def remove_brackets(text):
    return re.sub(r'\s*\([^)]*\)', '', text).strip()