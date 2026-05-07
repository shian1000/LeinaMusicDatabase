import re
import unicodedata
from typing import Optional
import tkinter as tk
from utils.debug import slog
import requests
from difflib import SequenceMatcher
from settings import settings

def _is_word_substring(needle: str, haystack: str) -> bool:
    """Return True if needle appears as whole words within haystack."""
    pattern = r'\b' + re.escape(needle) + r'\b'
    return bool(re.search(pattern, haystack))



def load_blacklist(config_dir):
    blacklist_path = config_dir / "blacklist.txt"
    exact = set()
    substrings = set()
    current_section = None

    with open(blacklist_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line == "[exact]":
                current_section = "exact"
            elif line == "[substring]":
                current_section = "substring"
            elif current_section == "exact":
                exact.add(line.lower())
            elif current_section == "substring":
                substrings.add(line.lower())

    return exact, substrings

ALBUM_TITLE_BLACKLIST, ALBUM_TITLE_BLACKLIST_SUBSTRINGS = load_blacklist(settings.config_dir)

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


def normalize_text(s: str) -> str:
    if s is None:
        return ""

    s = unicodedata.normalize("NFKD", s)

    s = "".join(c for c in s if not unicodedata.combining(c))

    for src, dst in _SPECIAL_REPLACEMENTS.items():
        if src in s:
            s = s.replace(src, dst)

    s = re.sub(r"[^\w\s]", " ", s)

    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def compare_strings(a: Optional[str], b: Optional[str]) -> bool:
    na = normalize_text(a or "")
    nb = normalize_text(b or "")

    if not na or not nb:
        return False

    if na == nb:
        return True

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
    if not text:
        return text

    text = re.sub(r'[・･•·\-+]', ' ', text)
    slog(text)

    text = re.sub(r'[\.\。…]+$', '', text)

    text = unicodedata.normalize('NFKC', text)

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
    fallback_query = f'{artist} {title}'
    
    url = "https://musicbrainz.org/ws/2/recording/"
    
    headers = {"User-Agent": "MyMusicApp/1.0.0 ( contact@example.com )"}

    def perform_search(q):
        params = {"query": q, "limit": 20, "fmt": "json"}
        slog(f"Attempting MB Query: {q}")
        resp = requests.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data.get("recordings", [])

    recordings = perform_search(primary_query)
    slog(f"Primary results count: {len(recordings)}")

    if not recordings:
        slog("No results for primary query. Trying fallback keyword search...")
        recordings = perform_search(fallback_query)
        slog(f"Fallback results count: {len(recordings)}")

    if not recordings:
        slog("No recordings found in both attempts.")
        return {"artist": artist, "title": title, "corrected": False, "found": False}

    best = recordings[0]

    mb_title = best.get("title", "")
    try:
        mb_artist = best["artist-credit"][0]["artist"]["name"]
    except (KeyError, IndexError):
        mb_artist = ""

    mb_score = int(best.get("score", 0))

    title_sim = similarity(title, mb_title)
    artist_sim = similarity(artist, mb_artist)
    
    slog(title_sim)
    slog(artist_sim)

    final_artist = mb_artist if artist_sim >= threshold else artist
    final_title = mb_title if title_sim >= threshold else title
    
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