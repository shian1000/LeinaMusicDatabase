"""Display utilities for rendering rich terminal tables and information.

This module provides functions to display database objects (Songs, Artists)
in formatted tables using the rich library.
"""

from typing import Optional, List, Tuple, Any, Dict
from rich.console import Console
from rich.table import Table
from utils.database.datatables import Song, Artist
from sqlalchemy.orm import Session
from utils.common.debug import slog
from utils.database.database_sessions import get_global_database_sessions
from utils.database.song_artists import count_artist_songs, song_artist_label
from utils.database.tags_management import tag_names_by_song
from utils.database.song_kinds import is_full_album


def _create_table(title: str, columns: List[Tuple[str, Optional[str], Optional[str]]]) -> Table:
    """
    Create a formatted Rich table with specified columns.
    
    Args:
        title: Title for the table
        columns: List of (name, style, justify) tuples for each column.
                 style and justify can be None for defaults.
    
    Returns:
        Table: Configured Rich Table object ready for data rows
    """
    table: Table = Table(title=title)
    for name, style, justify in columns:
        kwargs: Dict[str, str] = {}
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
    
    console: Console = Console()

    table: Table = _create_table("Music Library", [
        ("Artist", "cyan", None),
        ("Title", "magenta", None),
        ("Album", "green", None),
        ("Year", None, "right"),
        ("Language", None, None),
        ("Genre", None, None),
        ("Origin", None, None),
    ])

    for song in songs:
        table.add_row(
            song_artist_label(song),
            f"{song.title} [full album]" if is_full_album(song) else song.title,
            song.album or "—",
            str(song.year) if song.year else "—",
            song.language or "—",
            song.genre or "—",
            song.artist.origin,
        )

    console.print(table)

def display_song_links(songs: Optional[List[Song]]) -> None:
    """
    Display the songs' links: the library file(s) indexed for each song in
    files.db, its YouTube video and its Spotify page.

    Args:
        songs: List of Song objects to display
    """
    if not songs:
        return

    from utils.database.files_index import song_file_paths
    from utils.youtube.manage_youtube_playlists import NO_VIDEO_SENTINEL

    console: Console = Console()

    table: Table = _create_table("Music Library", [
        ("Artist", "cyan", None),
        ("Title", "magenta", None),
        ("Album", "green", None),
        ("Year", None, "right"),
        ("DB path", None, None),
        ("YouTube", None, None),
        ("Music video", None, None),
        ("Spotify", None, None),
    ])
    # Paths and URLs have no spaces to wrap at - fold them instead of cutting them off with "…"
    for column in table.columns[4:]:
        column.overflow = "fold"

    try:
        paths = song_file_paths([song.id for song in songs])
    except Exception as exc:
        slog(f"Couldn't read files.db: {exc}")
        paths = {}

    for song in songs:
        video_id = song.youtube_video_id
        if video_id == NO_VIDEO_SENTINEL:
            youtube = "N/A (no video)"
        elif video_id:
            youtube = f"https://www.youtube.com/watch?v={video_id}"
        else:
            youtube = "—"
        table.add_row(
            song_artist_label(song),
            song.title,
            song.album or "—",
            str(song.year) if song.year else "—",
            "\n".join(paths.get(song.id, [])) or "—",
            youtube,
            f"https://www.youtube.com/watch?v={song.music_video_youtube_id}" if song.music_video_youtube_id else "—",
            song.spotify_url or "—",
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
    
    console: Console = Console()

    table: Table = _create_table("Music Library", [
        ("Name", "cyan", None),
        ("Songs", None, None),
        ("Origin", None, None),
        ("Synonyms", None, None),
    ])

    music_session: Session
    music_session, _ = get_global_database_sessions()

    for artist in artists:
        songs_count: int = count_artist_songs(music_session, artist.id)
        
        table.add_row(
            artist.name,
            str(songs_count),
            artist.origin,
            artist.synonyms or "—",
        )

    console.print(table)

def display_songs_with_tags(songs: List[Song]) -> None:
    """A table of `songs` with their tags (tag.db). The menu's global
    sessions stay open - other screens still hold their objects."""
    tags_by_song = tag_names_by_song(song.id for song in songs)
    table = _create_table("Songs & Tags", [
        ("Title", "cyan", None),
        ("Artist", "magenta", None),
        ("Album", "green", None),
        ("Year", None, "right"),
        ("Tags", "yellow", None),
    ])
    for song in songs:
        table.add_row(
            song.title,
            song_artist_label(song),
            song.album or "—",
            str(song.year) if song.year else "—",
            ", ".join(tags_by_song.get(song.id, [])) or "—",
        )
    Console().print(table)
