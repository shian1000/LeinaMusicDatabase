"""Utilities for extracting and normalizing metadata from MP3 files."""

from pathlib import Path
from typing import Optional

import unicodedata
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3

from utils.common.normalizer import normalize
from utils.common.debug import slog

from utils.common.normalizer import normalize, extract_unknown_data


def audio_duration(file: Path) -> int | None:
    """Length in whole seconds, or None when mutagen can't read it."""
    try:
        return round(MP3(file).info.length)
    except Exception:
        return None


def extract_mp3_metadata(file: Path) -> dict:
    """Extract metadata from an MP3 file.

    Returns a dict with keys: 'title', 'artist_name', 'album', 'year', 'language', 'origin'.
    Handles missing ID3 tags gracefully with sensible defaults.
    """
    audio = MP3(file)
    easy = EasyID3(file)

    title = easy.get("title", ["Unknown Title"])[0]
    artist_name = easy.get("artist", ["Unknown Artist"])[0]
    album = easy.get("album", [None])[0]
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


def normalize_title(title: Optional[str]) -> str:
    """Normalize a title from MP3 tags.

    Uses NFC normalization then the project's `normalize` rules. Returns an empty
    string for None inputs.
    """
    if not title:
        return ""
    title = unicodedata.normalize("NFC", title)
    return normalize(title)

def extract_metadata_with_fallback(file: Path) -> dict:
    """Extract MP3 metadata with fallback to filename parsing.

    Attempts to extract metadata from ID3 tags first. If the tags are present
    but do not contain usable title/artist values, it falls back to parsing the
    filename using extract_unknown_data.

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

    if metadata and metadata.get("title") not in {None, "", "Unknown Title"} and metadata.get("artist_name") not in {None, "", "Unknown Artist"}:
        slog(f"[METADATA] Successfully extracted from ID3 tags: {file.name}")
        return metadata

    if metadata is not None:
        slog(f"[METADATA FALLBACK] Missing title/artist tags for {file.name}; trying filename")
    else:
        slog(f"[METADATA FALLBACK] ID3 extraction failed for {file.name}; trying filename")

    try:
        artist_name, title = extract_unknown_data(file)
        slog(f"[METADATA FALLBACK] Extracted from filename: artist='{artist_name}', title='{title}'")

        return {
            "title": title,
            "artist_name": artist_name,
            "album": None,
            "year": None,
            "language": "Unknown",
            "origin": None,
        }
    except Exception as fallback_error:
        slog(f"[METADATA ERROR] Both ID3 and fallback extraction failed for {file.name}: {fallback_error}")
        raise ValueError(f"Could not extract metadata from {file.name} via ID3 or filename") from fallback_error


def write_song_tags(file: Path, song) -> bool:
    """Copy a database song's data into an mp3's ID3 tags: title, artist (the
    whole credit, "A x B feat. C"), album and year. A "Singles" song's album
    tag is left alone - that's a sentinel, not an album name. Only differing
    fields are written; returns whether the file changed. A file with no ID3
    tags at all gets them."""
    from mutagen.id3 import ID3NoHeaderError

    from config.constants import SINGLES_ALBUM
    from utils.database.song_artists import song_artist_label

    try:
        tags = EasyID3(file)
    except ID3NoHeaderError:
        tags = EasyID3()
    wanted = {"title": song.title, "artist": song_artist_label(song)}
    if song.album and song.album.strip() != SINGLES_ALBUM:
        wanted["album"] = song.album
    if song.year:
        wanted["date"] = str(song.year)

    changed = False
    for key, value in wanted.items():
        if tags.get(key, [None])[0] != value:
            tags[key] = value
            changed = True
    if changed:
        tags.save(file)
    return changed


# Not allowed in a file name here or on the Windows side of the Samba share
_FILE_NAME_FORBIDDEN = str.maketrans({c: "_" for c in '/\\:*?"<>|'})


def song_file_name(song, suffix: str = ".mp3") -> str:
    """"Artist - Title.mp3" from the database's data (the whole artist
    credit), characters a file name can't hold replaced by "_"."""
    from utils.database.song_artists import song_artist_label

    stem = f"{song_artist_label(song)} - {song.title}".translate(_FILE_NAME_FORBIDDEN).strip(" .")
    return stem + suffix


def rename_to_song(file: Path, song) -> Path:
    """Rename `file` to song_file_name() in its own folder; returns the new
    path (the old one when it already has that name). Raises FileExistsError
    rather than overwrite another file."""
    target = file.with_name(song_file_name(song, file.suffix))
    if target == file:
        return file
    # Same name in other case only: a case-insensitive filesystem sees one file
    if target.exists() and target.name.casefold() != file.name.casefold():
        raise FileExistsError(f"{target} already exists")
    file.rename(target)
    return target
