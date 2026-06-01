import re
import unicodedata
from typing import Optional
import tkinter as tk
from utils.common.debug import slog
import requests
from difflib import SequenceMatcher
from settings import settings
import pyperclip
import time
from utils.common.normalizer import normalize

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


def copy_to_clipboard(message: str):
    try:
        pyperclip.copy(message)
        return True
    except pyperclip.PyperclipException as e:
        print(f"Clipboard unavailable: {e}")
        return False


def normalize_text(s: str) -> str:
    """Compatibility wrapper that uses the centralized normalizer."""
    return normalize(s)


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
    Includes fallback logic, normalization, and extensive logging.
    """
    from utils.common.normalizer import normalize as central_normalize

    def similarity(a, b):
        # Use centralized normalization for consistent comparison
        a_norm = central_normalize(a)
        b_norm = central_normalize(b)
        sim_score = SequenceMatcher(None, a_norm, b_norm).ratio()
        slog(f"[SIMILARITY] '{a}' ({a_norm}) vs '{b}' ({b_norm}) = {sim_score:.2f}")
        return sim_score

    slog(f"[SPELLCHECK START] Artist: '{artist}' | Title: '{title}'")
    slog(f"  Input (normalized): Artist: '{central_normalize(artist)}' | Title: '{central_normalize(title)}'")
    
    fuzzy_title = make_fuzzy(title)
    fuzzy_artist = make_fuzzy(artist)
    slog(f"[FUZZY] Artist: '{fuzzy_artist}' | Title: '{fuzzy_title}'")


    primary_query = f'recording:{fuzzy_title} AND artist:{fuzzy_artist}'
    fallback_query = f'{artist} {title}'
    
    url = "https://musicbrainz.org/ws/2/recording/"
    
    headers = {"User-Agent": "MyMusicApp/1.0.0 ( contact@example.com )"}

    def perform_search(q, retries=3, backoff=5):
        params = {"query": q, "limit": 20, "fmt": "json"}
        slog(f"Attempting MB Query: {q}")

        for attempt in range(1, retries + 1):
            try:
                resp = requests.get(url, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data.get("recordings", [])

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                if status in (429, 503) and attempt < retries:
                    wait = backoff * attempt
                    slog(f"HTTP {status} on attempt {attempt}/{retries}, retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    slog(f"HTTP error after {attempt} attempt(s): {e}")
                    return []

            except requests.exceptions.RequestException as e:
                slog(f"Request failed on attempt {attempt}/{retries}: {e}")
                if attempt < retries:
                    time.sleep(backoff * attempt)
                else:
                    return []

        return []

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
    
    slog(f"[MB RESULT] Score: {mb_score}")
    slog(f"  Title: input='{title}' ({central_normalize(title)}) vs MB='{mb_title}' ({central_normalize(mb_title)})")
    slog(f"  Artist: input='{artist}' ({central_normalize(artist)}) vs MB='{mb_artist}' ({central_normalize(mb_artist)})")

    final_artist = mb_artist if artist_sim >= threshold else artist
    final_title = mb_title if title_sim >= threshold else title
    
    is_corrected = (final_artist != artist) or (final_title != title)

    result = {
        "input_artist": artist,
        "input_title": title,
        "input_artist_norm": central_normalize(artist),
        "input_title_norm": central_normalize(title),
        "corrected_artist": final_artist,
        "corrected_title": final_title,
        "corrected_artist_norm": central_normalize(final_artist),
        "corrected_title_norm": central_normalize(final_title),
        "corrected": is_corrected,
        "mb_score": mb_score,
        "title_similarity": round(title_sim, 2),
        "artist_similarity": round(artist_sim, 2),
        "found": True,
    }
    
    slog(f"[SPELLCHECK END] Corrected: {is_corrected} | Artist sim: {artist_sim:.2f} | Title sim: {title_sim:.2f}")
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


def similarity(a: str, b: str) -> float:
    """Returns similarity ratio between 0 and 1."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def are_song_entries_similar(
    db_object,
    title_query: str,
    artist_query: str,
    threshold: float = 0.7
) -> bool:
    title_score = similarity(title_query, db_object.title)
    artist_score = similarity(artist_query, db_object.artist.name)
    return title_score >= threshold and artist_score >= threshold

def are_artists_entries_similar(
    db_object,
    artist_query: str,
    threshold: float = 0.7
) -> bool:
    artist_score = similarity(artist_query, db_object.artist.name)
    return artist_score >= threshold