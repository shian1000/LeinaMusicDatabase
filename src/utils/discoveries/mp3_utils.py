"""Utilities for extracting and normalizing metadata from MP3 files."""

from pathlib import Path
from typing import Optional

import unicodedata
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3

from utils.common.normalizer import normalize


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
