from src.navigation.menu_utils import open_file_browser
import questionary
from pathlib import Path
import sqlite3
from typing import Optional
from src.debug import slog
import requests
from langdetect import detect, DetectorFactory
import pycountry
import time


def find_language_from_lyrics(lyrics: str) -> Optional[str]:
    """Detect language name from lyrics text.

    Uses langdetect (if installed) to detect the ISO639-1 code, then maps
    to a friendly language name using pycountry when available. Returns
    None if detection isn't available or fails.
    """

    if not detect:
        slog("langdetect_not_available", "find_language_from_lyrics")
        return None

    try:
        code = detect(lyrics)
        slog(code, "langdetect_code")
    except Exception as e:
        slog(e, "langdetect_error")
        return None

    name: Optional[str] = None
    if pycountry:
        try:
            lang = pycountry.languages.get(alpha_2=code)
            if lang and getattr(lang, "name", None):
                name = lang.name
        except Exception:
            name = None

    if not name:
        common = {
            'en': 'English', 'pl': 'Polish', 'fr': 'French', 'de': 'German',
            'es': 'Spanish', 'it': 'Italian', 'pt': 'Portuguese', 'ru': 'Russian',
            'nl': 'Dutch'
        }
        name = common.get(code, code)

    return name


def find_lyrics_for_song(artist: Optional[str], title: Optional[str], filename: Optional[str] = None) -> Optional[str]:
    """Try to find lyrics for a song using simple heuristics.

    Strategy:
    1. If artist and title are available, call the free lyrics.ovh API.
    2. If only filename is available, try to parse it as "Artist - Title".

    Returns the lyrics string when found, otherwise None.

    This is intentionally simple and robust: it logs attempts with `slog`
    and gracefully handles missing network or API failures.
    """
    slog((artist, title, filename), "find_lyrics_input")

    # Helper to call lyrics.ovh
    def _lyrics_ovh(a: str, t: str) -> Optional[str]:
        if not requests:
            slog("requests_not_available", "find_lyrics")
            return None
        url = f"https://api.lyrics.ovh/v1/{a}/{t}"
        try:
            resp = requests.get(url, timeout=8)
            slog((url, resp.status_code), "lyrics_ovh_response")
            if resp.status_code == 200:
                data = resp.json()
                lyrics = data.get("lyrics")
                if lyrics:
                    return lyrics
        except Exception as e:
            slog(e, "lyrics_ovh_error")
        return None

    # Attempt 1: direct artist+title
    if artist and title:
        lyrics = _lyrics_ovh(artist, title)
        if lyrics:
            slog("found_via_ovh", "find_lyrics")
            return lyrics

    # Attempt 2: try to parse filename into artist/title
    if filename and (not artist or not title):
        name = Path(filename).stem
        # common pattern: Artist - Title
        if " - " in name:
            parts = name.split(" - ", 1)
            a, t = parts[0].strip(), parts[1].strip()
            lyrics = _lyrics_ovh(a, t)
            if lyrics:
                slog((a, t), "found_via_filename_parse")
                return lyrics

    # No lyrics found
    slog("no_lyrics_found", "find_lyrics")
    return None


def get_entries_with_empty_tag(index_path: Path, tag: str):
    if not index_path:
        print("No index selected.")
        return

    if isinstance(index_path, Path):
        index_file = str(index_path)
    else:
        index_file = str(index_path)

    slog(index_file, "selected_index_path")
    slog(tag, "selected_tag")

    try:
        conn = sqlite3.connect(index_file)
        cur = conn.cursor()
    except Exception as e:
        print(f"Could not open index file: {e}")
        slog(e, "sqlite_connect_error")
        return

    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cur.fetchall()]

        target_table: Optional[str] = None
        for table in tables:
            cur.execute(f"PRAGMA table_info('{table}')")
            cols = [r[1] for r in cur.fetchall()]
            if tag in cols:
                target_table = table
                break

        if not target_table:
            print(f"No table in the index contains column '{tag}'")
            slog(tables, "available_tables")
            return

        slog(target_table, "found_table")

        # Fetch rows where the chosen tag is NULL or empty
        query = f"SELECT * FROM '{target_table}' WHERE ({tag} IS NULL OR {tag} = '')"
        cur.execute(query)
        rows = cur.fetchall()

        slog(len(rows), "rows_with_empty_tag_count")

        if not rows:
            print(f"No items found with empty '{tag}'")
            return

        # Try to display sensible identifying column: filename, title or id
        display_col = None
        cur.execute(f"PRAGMA table_info('{target_table}')")
        cols = [r[1] for r in cur.fetchall()]
        for pref in ("filename", "title", "id", "path", "file"):
            if pref in cols:
                display_col = pref
                break

        # Print found rows
        print(f"Found {len(rows)} items with empty '{tag}':")
        if display_col:
            # find index
            idx = cols.index(display_col)
            for r in rows:
                print(f" - {r[idx]}")
        else:
            for r in rows:
                print(f" - {r}")

        # If the selected tag is 'language', try to fetch lyrics for each item
        # so a language detector or manual inspection can be used later.
        if tag.lower() == "language":
            # Determine indices for artist/title/filename if present
            artist_idx = cols.index("artist") if "artist" in cols else None
            title_idx = cols.index("title") if "title" in cols else None
            filename_idx = None
            for candidate in ("filename", "path", "file"):
                if candidate in cols:
                    filename_idx = cols.index(candidate)
                    break

            for r in rows:
                artist_val = r[artist_idx] if artist_idx is not None else None
                title_val = r[title_idx] if title_idx is not None else None
                filename_val = r[filename_idx] if filename_idx is not None else None

                slog((artist_val, title_val, filename_val), "language_check_item")

                lyrics = find_lyrics_for_song(artist_val, title_val, filename_val)
                if lyrics:
                    # show a short preview
                    language = find_language_from_lyrics(lyrics)
                    print(f"{artist_val} - {title_val} ({filename_val}) - language: {language}")
                else:
                    print(f" -> No lyrics found for: {artist_val or filename_val or title_val}")

    finally:
        try:
            conn.close()
        except Exception:
            pass



def tags_fetcher():
    choices = ["Yes", "No", "Back"]
    choice = questionary.select("Would you like to open file browser?", choices=choices).ask()
    
    index_path: Path
    
    if(choice == "Yes"):
        index_path = open_file_browser()

    elif(choice == "No"):
        index_path = input("Put the index path: ")

    else:
        return
    
    tags = ["filename", "artist", "title", "album", "year", "language", "comment"]
    choosen_tag = questionary.select("Select what tags should be filled", choices=tags).ask()
    get_entries_with_empty_tag(index_path, choosen_tag)