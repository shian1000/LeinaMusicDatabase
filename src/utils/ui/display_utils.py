"""Display utilities for rendering rich terminal tables and information.

This module provides functions to display database objects (Songs, Artists)
in formatted tables using the rich library.
"""

from typing import Optional, List
from rich.console import Console
from rich.table import Table
from utils.database.music_db_manager import get_music_session
from utils.database.datatables import Song, Artist
from utils.database.tag_db_manager import get_tag_session, Tag, SongTag
from sqlalchemy import func, text
from utils.common.debug import slog
from utils.database.database_sessions import get_global_database_sessions


def _create_table(title: str, columns: List[tuple]) -> Table:
    """
    Create a formatted Rich table with specified columns.
    
    Args:
        title: Title for the table
        columns: List of (name, style, justify) tuples for each column.
                 style and justify can be None for defaults.
    
    Returns:
        Table: Configured Rich Table object ready for data rows
    """
    table = Table(title=title)
    for name, style, justify in columns:
        kwargs = {}
        if style:
            kwargs["style"] = style
        if justify:
            kwargs["justify"] = justify
        table.add_column(name, **kwargs)
    return table



def display_songs(songs: Optional[List[Song]]) -> None:
    """
    Display a formatted table of songs with their properties.
    
    Args:
        songs: List of Song objects to display
    """
    if not songs:
        return
    
    console = Console()

    table = _create_table("Music Library", [
        ("Artist", "cyan", None),
        ("Title", "magenta", None),
        ("Album", "green", None),
        ("Year", None, "right"),
        ("Language", None, None),
        ("Origin", None, None),
        ("Nostalgic", None, "center"),
        ("Melancholic", None, "center"),
        ("Party", None, "center"),
    ])

    for song in songs:
        table.add_row(
            song.artist.name,
            song.title,
            song.album or "—",
            str(song.year) if song.year else "—",
            song.language or "—",
            song.artist.origin,
            "✓" if song.nostalgic else "✗",
            "✓" if song.melancholic else "✗",
            "✓" if song.party else "✗",
        )

    console.print(table)

def display_artists(artists: Optional[List[Artist]]) -> None:
    """
    Display a formatted table of artists with their song counts.
    
    Queries the database to count songs per artist and displays
    artist information in a formatted table.
    
    Args:
        artists: List of Artist objects to display
    """
    if not artists:
        return
    
    console = Console()

    table = _create_table("Music Library", [
        ("Name", "cyan", None),
        ("Songs", None, None),
        ("Origin", None, None),
    ])

    music_session, _ = get_global_database_sessions()

    for artist in artists:
        songs_count = music_session.execute(
            text("SELECT COUNT(*) FROM songs WHERE artist_id = :artist_id"),
            {"artist_id": artist.id}
        ).scalar()
        
        table.add_row(
            artist.name,
            str(songs_count),
            artist.origin,
        )

    console.print(table)

def display_songs_with_tags(songs: List[Song]) -> None:
    """
    Display a formatted table of songs with their associated tags.
    
    Joins song and tag data from the database and displays in a
    formatted table showing tags for each song.
    
    Args:
        songs: List of Song objects to display (currently unused,
               all songs are queried from database)
    """
    music_session, tag_session = get_global_database_sessions()
    console = Console()

    # Fetch all song-tag relationships
    song_tags = tag_session.query(SongTag).all()
    tag_ids = {st.tag_id for st in song_tags}

    # Build tag lookup dictionary
    tags_by_id = {}
    if tag_ids:
        tags = tag_session.query(Tag).filter(Tag.id.in_(tag_ids)).all()
        tags_by_id = {tag.id: tag.name for tag in tags}

    # Build song-to-tags mapping
    song_tag_map = {}
    for st in song_tags:
        song_tag_map.setdefault(st.song_id, []).append(tags_by_id.get(st.tag_id, "?"))

    tag_session.close()

    # Create and populate table
    table = _create_table("Songs & Tags", [
        ("Title", "cyan", None),
        ("Artist", "magenta", None),
        ("Album", "green", None),
        ("Year", None, "right"),
        ("Tags", "yellow", None),
    ])

    # Query all songs ordered by artist name and year
    all_songs = music_session.query(Song).join(Artist).order_by(Artist.name, Song.year).all()

    for song in all_songs:
        tag_list = song_tag_map.get(song.id, [])
        tag_str = ", ".join(sorted(tag_list)) if tag_list else "—"
        table.add_row(
            song.title,
            song.artist.name,
            song.album or "—",
            str(song.year) if song.year else "—",
            tag_str,
        )

    music_session.close()
    console.print(table)
