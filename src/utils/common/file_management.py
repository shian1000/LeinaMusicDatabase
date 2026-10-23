from settings import settings
from upath import UPath
from pathlib import Path
from utils.common.debug import slog
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from urllib.parse import urlparse
import re
from rapidfuzz import fuzz

from utils.common.normalizer import normalize


def save_string_to_file(text: str) -> str:
    """
    Saves a string to a .txt file in the local directory.
    The file is named using the first three words of the string.

    Args:
        text: The string to save.

    Returns:
        The filename used to save the file.
    """
    if not text:
        return
    words = text.split()

    if not words:
        raise ValueError("Input string is empty or contains no words.")

    # Take up to the first three words and join them with underscores
    name_parts = words[:3]
    filename = "_".join(name_parts) + ".txt"

    # Sanitize filename by removing characters not allowed in filenames
    invalid_chars = r'\/:*?"<>|'
    for char in invalid_chars:
        filename = filename.replace(char, "")

    with open(filename, "w", encoding="utf-8") as f:
        f.write(text)

    print(f"Saved to: {filename}")
    return filename


def is_local_dir(uri_str: str):
    parsed = urlparse(uri_str)
    return parsed.scheme == "" or parsed.scheme == "file"


def get_proper_uri():
    uri_upath = settings.local_library_dir
    uri_str = settings.local_library_dir_str

    if (is_local_dir(uri_str)):
        uri = str(uri_upath)
    else:
        parsed = urlparse(uri_str)
        scheme = f"{parsed.scheme}://"
        remainder = uri_str[len(scheme):]
        uri = f"{scheme}{settings.smb_username}:{settings.smb_password}@{remainder}"

    return uri


LIBRARY_INDEX_NAME = ".mp3_index.sqlite3"


def library_index_uri() -> str:
    """The library's .mp3_index.sqlite3, inside the library folder: get_proper_uri()
    may or may not end in "/" (str(UPath) drops it for a local folder)."""
    return f"{get_proper_uri().rstrip('/')}/{LIBRARY_INDEX_NAME}"


def create_temp_index(index_file):
    local_index = Path("/tmp/.mp3_index.sqlite3")
    local_index.write_bytes(index_file.read_bytes())
    return local_index


def fuzzy_match(a, b):
    """Check if two strings match under either apostrophe strategy, or are close enough."""
    for strip in [True, False]:
        na, nb = normalize(a, strip_apostrophe=strip), normalize(b, strip_apostrophe=strip)
        if na == nb:
            return True
        if fuzz.ratio(na, nb) >= 90:  # 90% similarity threshold
            return True
    return False


def query_database(engine, query):
    slog(query)

    sql, params = query
    artist = params["artist"]
    title = params["title"]

    sql = text("SELECT path, artist, title FROM files WHERE LOWER(artist) LIKE :artist_first_char")

    with Session(engine) as session:
        candidates = session.execute(sql, {"artist_first_char": normalize(artist)[0] + '%'}).fetchall()

    for row in candidates:
        if fuzzy_match(artist, row.artist) and fuzzy_match(title, row.title):
            slog(row.path)
            return row.path

    slog(f"No result for {artist} - {title}")
    return None


def copy_file_to_destination(source, destination):
    slog(source)
    slog(destination)
    if source.startswith("\\\\"):
        unc_path = source.replace("\\\\", "\\").replace("\\", "/").lstrip("/")
        smb_url = "smb://" + unc_path
        source_file = UPath(
            smb_url,
            username=settings.smb_username,
            password=settings.smb_password,
        )
    else:
        source_file = Path(source)

    dest_file = destination / source_file.name
    dest_file.write_bytes(source_file.read_bytes())
    print("Copied to:", dest_file)


def copy_songs_using_index(
    songs: list[tuple[str, str]],
    index_path,
    paste_destination: UPath | Path,
) -> None:

    slog(songs)
    slog(index_path)
    slog(paste_destination)

    if (isinstance(index_path, Path)):
        usable_index_path = index_path
    else:
        usable_index_path = create_temp_index(index_path)

    engine = create_engine(f"sqlite:///{usable_index_path}")

    with engine.connect():
        for artist, title in songs:
            query = (text("SELECT path FROM files WHERE LOWER(artist) = :artist AND LOWER(title) = :title"),
                    {"artist": normalize(artist), "title": normalize(title)})

            path_from_db = query_database(engine, query)
            if not path_from_db:
                print(f"Couldn't find {query[1]}. Skipping")
            else:
                copy_file_to_destination(path_from_db, paste_destination)

    engine.dispose()




def load_paths(input_file: str) -> list[str]:
    """This script loads paths from a file and returns a list"""
    with open(input_file, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]