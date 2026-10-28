from settings import settings
from upath import UPath
from pathlib import Path
from utils.common.debug import slog
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from urllib.parse import urlparse
import re
import tempfile
from rapidfuzz import fuzz
from config.constants import FILE_SIMILARITY_THRESHOLD

from utils.common.normalizer import normalize


def _is_local_dir(uri_str: str):
    parsed = urlparse(uri_str)
    return parsed.scheme == "" or parsed.scheme == "file"


def get_proper_uri():
    """The music library folder as a URI. Never with the SMB login in it -
    that goes to UPath as options (library_index_path()) - so the URI can be
    shown or logged."""
    if _is_local_dir(settings.local_library_dir_str):
        return str(settings.local_library_dir)
    return settings.local_library_dir_str


LIBRARY_INDEX_NAME = ".mp3_index.sqlite3"


class MissingSmbLoginError(Exception):
    """The library is on an SMB share, but SMB_USERNAME / SMB_PASSWORD aren't set."""


def library_index_uri() -> str:
    """The library's .mp3_index.sqlite3, inside the library folder: get_proper_uri()
    may or may not end in "/" (str(UPath) drops it for a local folder)."""
    return f"{get_proper_uri().rstrip('/')}/{LIBRARY_INDEX_NAME}"


def library_index_path() -> UPath:
    """library_index_uri() as a UPath, with the SMB login for a share.
    MissingSmbLoginError when a share has none, instead of trying to log in
    as "None"."""
    uri = library_index_uri()
    if _is_local_dir(settings.local_library_dir_str):
        return UPath(uri)
    if not (settings.smb_username and settings.smb_password):
        raise MissingSmbLoginError("The music library is on a network share, but SMB_USERNAME / SMB_PASSWORD "
                                   "aren't set in .env.")
    return UPath(uri, username=settings.smb_username, password=settings.smb_password)


def fuzzy_match(a, b):
    """Check if two strings match under either apostrophe strategy, or are close enough."""
    for strip in [True, False]:
        na, nb = normalize(a, strip_apostrophe=strip), normalize(b, strip_apostrophe=strip)
        if na == nb:
            return True
        if fuzz.ratio(na, nb) >= FILE_SIMILARITY_THRESHOLD:
            return True
    return False


def _artist_initial(artist) -> str:
    name = normalize(artist or "")
    return name[0] if name else ""


def load_index_rows(engine) -> dict[str, list]:
    """Every file row (path, artist, title) of the library index, by the
    first character of its normalize()d artist - one query for a whole copy,
    and both sides normalized ("Ølafur Arnalds" under "o", which LOWER() in
    SQL never gave)."""
    with Session(engine) as session:
        rows = session.execute(text("SELECT path, artist, title FROM files")).fetchall()
    by_initial = {}
    for row in rows:
        initial = _artist_initial(row.artist)
        if initial:
            by_initial.setdefault(initial, []).append(row)
    return by_initial


def find_in_index(rows_by_initial: dict[str, list], artist: str, title: str) -> str | None:
    """The path of the indexed file matching artist and title
    (fuzzy_match()), or None - also for an empty artist."""
    for row in rows_by_initial.get(_artist_initial(artist), []):
        if fuzzy_match(artist, row.artist) and fuzzy_match(title, row.title or ""):
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


def _read_index(path: Path) -> dict[str, list]:
    engine = create_engine(f"sqlite:///{path}")
    try:
        return load_index_rows(engine)
    finally:
        engine.dispose()


def copy_songs_using_index(
    songs: list[tuple[str, str]],
    index_path,
    paste_destination: UPath | Path,
) -> None:

    slog(songs)
    slog(index_path)
    slog(paste_destination)

    if isinstance(index_path, Path):
        rows_by_initial = _read_index(index_path)
    else:
        # On a network share: SQLite needs a local copy - a private one, gone afterwards
        with tempfile.TemporaryDirectory(prefix="musicdatabase-index-") as folder:
            local_index = Path(folder) / "mp3_index.sqlite3"
            local_index.write_bytes(index_path.read_bytes())
            rows_by_initial = _read_index(local_index)
    for artist, title in songs:
        path_from_db = find_in_index(rows_by_initial, artist, title)
        if not path_from_db:
            print(f"Couldn't find {artist} - {title}. Skipping")
        else:
            copy_file_to_destination(path_from_db, paste_destination)
