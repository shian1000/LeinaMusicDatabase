"""Utilities for extracting and normalizing metadata from MP3 files."""

from pathlib import Path
from typing import Optional

import unicodedata
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3

from utils.common.normalizer import normalize
from utils.common.debug import slog

from utils.common.normalizer import normalize, extract_unknown_data


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
    
    Attempts to extract metadata from ID3 tags first. If that fails,
    falls back to parsing the filename using extract_unknown_data.
    
    Args:
        file: Path to the MP3 file
        
    Returns:
        dict: Metadata with keys 'title', 'artist_name', 'album', 'year', 'language', 'origin'
    """
    try:
        metadata = extract_mp3_metadata(file)
        slog(f"[METADATA] Successfully extracted from ID3 tags: {file.name}")
        return metadata
    except Exception as e:
        slog(f"[METADATA FALLBACK] ID3 extraction failed for {file.name}: {e}")
        print(f"Could not read ID3 tags from {file.name}, attempting to extract from filename...")
        
        try:
            artist_name, title = extract_unknown_data(file)
            print(artist_name)
            print(title)
            slog(f"[METADATA FALLBACK] Extracted from filename: artist='{artist_name}', title='{title}'")
            
            # Return a complete metadata dict with extracted data
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
