from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass

from utils.database.datatables import Song, Artist
from utils.database.database_sessions import open_and_set_global_database_sessions
from utils.database.tags_management import add_tag_to_song

import random
import questionary

from utils.common.debug import slog

from utils.common.normalizer import normalize, extract_unknown_data
from utils.common.text_utils import check_spelling, are_song_entries_similar
from utils.discoveries.mp3_utils import extract_mp3_metadata, normalize_title
from rich import print


@dataclass
class SongUpsertResult:
    """Result of an upsert operation."""
    song: Optional[Song]
    status: str  # 'skipped', 'added', 'updated'
    reason: Optional[str] = None  # e.g., "exact_match", "user_skipped_similar"


def _find_exact_match(music_session, artist_id: int, normalized_title: str) -> Optional[Song]:
    """Find a song by artist and normalized title.
    
    Returns the song if found, None otherwise.
    """
    songs = music_session.query(Song).filter(Song.artist_id == artist_id).all()
    return next(
        (s for s in songs if normalize_title(s.title) == normalized_title),
        None
    )


def _extract_metadata_with_fallback(file: Path) -> dict:
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


def _check_for_similar_song(music_session, new_song_title: str, new_artist_name: str, interactive: bool = True) -> Optional[Song]:
    """Check all database songs for similarity.
    
    If interactive=True and a similar song is found, ask the user if they want to add anyway.
    Returns the similar song if found and user confirms skip, None otherwise.
    """
    all_songs = music_session.query(Song).all()
    print(f"Checking {len(all_songs)} total songs in database for similarities")
    
    for s in all_songs:
        if are_song_entries_similar(s, new_song_title, new_artist_name):
            slog(f"[SIMILAR SONG FOUND] '{s.artist.name} - {s.title}' matches '{new_artist_name} - {new_song_title}'")
            
            if interactive:
                print(f"Similar song found!")
                print(f"Song in database: [blue]{s.artist.name} - {s.title}[/blue]")
                print(f"Song you're adding: [red]{new_artist_name} - {new_song_title}[/red]")
                user_wants_to_add = questionary.confirm("Do you want to add the song anyway?").ask()
                if not user_wants_to_add:
                    return s  # Signal that user chose to skip
            return s
    
    return None


def upsert_song(music_session, artist: Artist, metadata: dict, mode: str, file: Path, interactive: bool = True) -> Tuple[Optional[Song], str]:
    """Insert or update a song record.
    
    Returns tuple (song, status) where status is one of 'skipped', 'added', 'updated'.
    
    Args:
        music_session: Database session for music database
        artist: Artist instance (may be updated with extracted name if unknown)
        metadata: Dict with 'title', 'album', 'language', 'year', etc.
        mode: 'skip' (don't update existing), 'update' (update existing)
        file: Path to the MP3 file (used for metadata extraction)
        interactive: If True, ask user about similar songs; if False, auto-skip
    """
    # Extract/normalize metadata
    new_artist_name = artist.name
    new_song_title = metadata["title"]

    if new_artist_name == "Unknown Artist" or new_song_title == "Unknown Title":
        new_artist_name, new_song_title = extract_unknown_data(file)
        artist.name = new_artist_name

    album = metadata.get("album")
    language = metadata.get("language")
    year = int(metadata["year"]) if metadata.get("year") and str(metadata.get("year")).isdigit() else None

    print(f"Checking: {new_artist_name} - {new_song_title} (lang: {language})")
    slog(f"Artist: '{new_artist_name}' (id={artist.id})")
    
    # Normalize title
    normalized_title = normalize_title(new_song_title)
    slog(f"[TITLE LOOKUP] Input: '{new_song_title}' | Normalized: '{normalized_title}'")

    # Try exact match
    song = _find_exact_match(music_session, artist.id, normalized_title)
    slog(f"Exact match found: {song is not None} (song_id={song.id if song else 'N/A'})")

    if song:
        # Exact match found
        if mode == "skip":
            print(" -> Song exists (exact match), skipping.")
            return song, "skipped"
        elif mode == "update":
            song.year = year or song.year
            song.language = language
            song.album = album or song.album
            music_session.commit()
            slog(f"[SONG UPDATED] '{new_artist_name} - {new_song_title}'")
            print(" -> Song updated.")
            return song, "updated"
    
    # No exact match: check for similar songs
    slog(f"[NO EXACT MATCH] Checking for similar entries for: '{new_song_title}'")
    similar_song = _check_for_similar_song(music_session, new_song_title, new_artist_name, interactive=interactive)
    
    if similar_song and interactive:
        # User was asked and chose not to add
        print(" -> Similar song found, skipping.")
        return None, "skipped"
    
    # Add new song
    song = Song(
        title=new_song_title,
        artist_id=artist.id,
        album=album,
        year=year,
        language=language,
        nostalgic=0,
        melancholic=0,
        party=0,
    )
    music_session.add(song)
    music_session.commit()
    slog(f"[NEW SONG ADMITTED] Artist: '{new_artist_name}' | Title: '{new_song_title}'")
    print(" -> Added new song.")
    return song, "added"


def apply_tag(song: Song, file: Path, folder: Path) -> None:
    """Apply a tag to a song based on its folder structure.
    
    The tag name is derived from the file's parent directory relative to the root folder.
    Uses the centralized add_tag_to_song function from tags_management.
    
    Args:
        song: Song object to tag
        file: Path to the MP3 file
        folder: Root folder path (tag name = file.parent relative to folder)
    """
    tag_name = str(file.parent.relative_to(folder))
    
    if add_tag_to_song(song, tag_name):
        print(f" -> Tagged with [{tag_name}]")
    else:
        slog(f"Failed to add tag '{tag_name}' to song {song.id}")




def import_data_from_mp3_tags(folder_path: str, mode: str = "skip"):
    music_session, tag_session = open_and_set_global_database_sessions()

    added_songs = []

    folder = Path(folder_path).resolve()
    if not folder.exists():
        print(f"Folder {folder_path} does not exist.")
        return

    mp3_files = list(folder.rglob("*.mp3"))
    print(f"Found {len(mp3_files)} mp3 files.")

    added_count = 0
    updated_count = 0

    for file in mp3_files:
        try:
            # Try to extract metadata with fallback to filename parsing
            metadata = _extract_metadata_with_fallback(file)
            
            artist = resolve_artist(music_session, metadata["artist_name"], metadata["origin"], metadata["title"])
            song, status = upsert_song(music_session, artist, metadata, mode, file)
            print(song)
            print(song.artist.name)
            print(song.title)

            if status == "skipped":
                continue

            if status == "added":
                added_count += 1
                added_songs.append({"artist_name": song.artist.name, "title": song.title})
            elif status == "updated":
                updated_count += 1

            apply_tag(song, file, folder)

        except ValueError as ve:
            # Metadata extraction failed completely (both ID3 and fallback)
            slog(f"[SKIP] {file}: {ve}")
            print(f"❌ Skipping {file.name}: Could not extract metadata")
        except Exception as e:
            # Other unexpected errors
            slog(f"[ERROR] {file}: {e}")
            print(f"❌ Error processing {file}: {e}")

    music_session.close()
    print(f"\nDone! Added {added_count}, updated {updated_count}.")
    return added_songs


def resolve_artist(music_session, artist_name: str, origin: str | None = None, song_name: str = None) -> Artist:
    # Normalize incoming artist name for comparison
    normalized_incoming = normalize(artist_name)
    slog(f"[ARTIST LOOKUP] Input: '{artist_name}' | Normalized: '{normalized_incoming}'")
    
    all_artists = music_session.query(Artist).all()
    
    # Find matching artists using normalized comparison
    matching_artists = []
    for a in all_artists:
        normalized_db = normalize(a.name)
        is_match = normalized_db == normalized_incoming
        slog(f"  - DB artist '{a.name}' | Normalized: '{normalized_db}' | Match: {is_match}")
        if is_match:
            matching_artists.append(a)

    # --- no artist found ---
    if not matching_artists:
        artist = Artist(
            name=artist_name,
            origin=origin
        )
        music_session.add(artist)
        music_session.commit()
        return artist


    # --- exactly one artist ---
    if len(matching_artists) == 1:
        return matching_artists[0]

    # --- multiple artists found: user must choose ---
    choices = []

    for artist in matching_artists:
        # fetch up to 3 random songs
        songs = (
            music_session.query(Song)
            .filter(Song.artist_id == artist.id)
            .all()
        )

        sample_songs = random.sample(songs, min(3, len(songs))) if songs else []

        if sample_songs:
            song_preview = ", ".join(song.title for song in sample_songs)
        else:
            song_preview = "no songs yet"

        label = f"{artist.name} (id={artist.id}) → {song_preview}"
        choices.append(questionary.Choice(title=label, value=artist))

    selected_artist = questionary.select(
        f"Multiple artists found for '{artist_name} - {song_name}'. Choose the correct one:",
        choices=choices
    ).ask()

    return selected_artist

