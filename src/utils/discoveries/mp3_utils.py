"""Utilities for extracting and normalizing metadata from MP3 files."""

import re
from pathlib import Path

from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3

from config.constants import PLACEHOLDER_TAG_ALBUMS, PLACEHOLDER_TAG_ARTISTS, PLACEHOLDER_TAG_TITLE_WORDS
from utils.common.debug import flog, slog

from utils.common.normalizer import normalize, extract_unknown_data

_MISSING_TITLES = {None, "", "Unknown Title"}
_MISSING_ARTISTS = {None, "", "Unknown Artist"}
_PLACEHOLDER_TITLE_RE = re.compile(rf"(?:{'|'.join(PLACEHOLDER_TAG_TITLE_WORDS)})\s*\d+", re.IGNORECASE)
_PLACEHOLDER_ALBUM_RE = re.compile(rf"(?:{'|'.join(PLACEHOLDER_TAG_ALBUMS)})(?:\s*\(.*\))?", re.IGNORECASE)


def audio_duration(file: Path) -> int | None:
    """Length in whole seconds, or None when mutagen can't read it."""
    try:
        return round(MP3(file).info.length)
    except Exception as e:
        flog(f"Can't read the length of {file}: {type(e).__name__}: {e}")
        return None


def extract_mp3_metadata(file: Path) -> dict:
    """Extract metadata from an MP3 file.

    Returns a dict with keys: 'title', 'artist_name', 'album', 'year', 'language', 'origin'.
    Handles missing ID3 tags gracefully with sensible defaults; a CD ripper's
    placeholder ("Nieznany wykonawca", "Ścieżka 15") counts as missing.
    """
    audio = MP3(file)
    easy = EasyID3(file)

    title = easy.get("title", ["Unknown Title"])[0]
    artist_name = easy.get("artist", ["Unknown Artist"])[0]
    album = easy.get("album", [None])[0]
    if _PLACEHOLDER_TITLE_RE.fullmatch(title.strip()):
        title = "Unknown Title"
    if artist_name.strip().casefold() in PLACEHOLDER_TAG_ARTISTS:
        artist_name = "Unknown Artist"
    if album and _PLACEHOLDER_ALBUM_RE.fullmatch(album.strip()):
        album = None
    year = easy.get("date", [None])[0]
    language = easy.get("language", ["Unknown"])[0]

    origin = None
    if audio.tags:
        comments = audio.tags.getall("COMM")
        if comments and comments[0].text:
            origin = comments[0].text[0]

    if language == "Unknown" and audio.tags:
        for tag in audio.tags.getall("TXXX"):
            if tag.desc.lower() == "language" and tag.text:
                language = tag.text[0]
                break

    return {
        "title": title,
        "artist_name": artist_name,
        "album": album,
        "year": year,
        "language": language,
        "origin": origin,
    }


def extract_metadata_with_fallback(file: Path) -> dict:
    """Extract MP3 metadata with fallback to filename parsing.

    Attempts to extract metadata from ID3 tags first. If the tags are present
    but do not contain usable title/artist values, it falls back to parsing the
    filename using extract_unknown_data. The other tags stay (the year of a
    rip whose names are placeholders), and so does a usable artist or title
    when the filename has no "Artist - Title" to read.

    Args:
        file: Path to the MP3 file

    Returns:
        dict: Metadata with keys 'title', 'artist_name', 'album', 'year', 'language', 'origin'
    """
    try:
        metadata = extract_mp3_metadata(file)
    except Exception as e:
        slog(f"[METADATA FALLBACK] ID3 extraction failed for {file.name}: {e}")
        print(f"Could not read ID3 tags from {file.name}, attempting to extract from filename...")
        metadata = None

    if metadata and metadata.get("title") not in _MISSING_TITLES and metadata.get("artist_name") not in _MISSING_ARTISTS:
        slog(f"[METADATA] Successfully extracted from ID3 tags: {file.name}")
        return metadata

    if metadata is not None:
        slog(f"[METADATA FALLBACK] Missing title/artist tags for {file.name}; trying filename")
    else:
        slog(f"[METADATA FALLBACK] ID3 extraction failed for {file.name}; trying filename")

    try:
        artist_name, title = extract_unknown_data(file)
        slog(f"[METADATA FALLBACK] Extracted from filename: artist='{artist_name}', title='{title}'")

        tags = metadata or {"album": None, "year": None, "language": "Unknown", "origin": None}
        if not artist_name:
            if tags.get("artist_name") not in _MISSING_ARTISTS:
                artist_name = tags["artist_name"]
            if not title and tags.get("title") not in _MISSING_TITLES:
                title = tags["title"]
        return {**tags, "title": title, "artist_name": artist_name}
    except Exception as fallback_error:
        slog(f"[METADATA ERROR] Both ID3 and fallback extraction failed for {file.name}: {fallback_error}")
        raise ValueError(f"Could not extract metadata from {file.name} via ID3 or filename") from fallback_error


def write_song_tags(file: Path, song, save: bool = True) -> bool:
    """Copy a database song's data into an mp3's ID3 tags: title, artist (the
    whole credit, "A x B feat. C"), album and year. A "Singles" song's album
    tag is left alone - that's a sentinel, not an album name. Only differing
    fields are written; returns whether the file changed (with `save` False:
    would change, nothing written). A file with no ID3 tags at all gets them."""
    from mutagen.id3 import ID3NoHeaderError

    from config.constants import SINGLES_ALBUM
    from utils.common.project_names import title_with_project
    from utils.database.song_artists import song_artist_label

    try:
        tags = EasyID3(file)
    except ID3NoHeaderError:
        tags = EasyID3()
    wanted = {"title": title_with_project(song), "artist": song_artist_label(song)}
    if song.album and song.album.strip() != SINGLES_ALBUM:
        wanted["album"] = song.album
    if song.year:
        wanted["date"] = str(song.year)

    changed = False
    for key, value in wanted.items():
        if tags.get(key, [None])[0] != value:
            tags[key] = value
            changed = True
    if changed and save:
        tags.save(file)
    return changed


# Not allowed in a file name here or on the Windows side of the Samba share
_FILE_NAME_FORBIDDEN = str.maketrans({c: "_" for c in '/\\:*?"<>|'})


def song_file_name(song, suffix: str = ".mp3") -> str:
    """"Artist - Title.mp3" from the database's data (the whole artist
    credit, a project's recording "Title (Project)"), characters a file name
    can't hold replaced by "_"."""
    from utils.common.project_names import title_with_project
    from utils.database.song_artists import song_artist_label

    stem = f"{song_artist_label(song)} - {title_with_project(song)}".translate(_FILE_NAME_FORBIDDEN).strip(" .")
    return stem + suffix


def rename_to_song(file: Path, song) -> Path:
    """Rename `file` to song_file_name() in its own folder; returns the new
    path (the old one when it already has that name). Raises FileExistsError
    rather than overwrite another file."""
    target = file.with_name(song_file_name(song, file.suffix))
    if target == file:
        return file
    if target.exists():
        if not target.samefile(file):
            raise FileExistsError(f"{target} already exists")
        # Same name in other case only, on a case-insensitive filesystem: one
        # file under two spellings, renamed through a free name in between
        step = file.with_name(f".{file.name}.renaming")
        file.rename(step)
        step.rename(target)
        return target
    file.rename(target)
    return target
