"""Rich terminal tables of songs and artists."""

from typing import Optional, List
from utils.common.output import console
from rich.table import Table
from utils.database.datatables import Song, Artist
from utils.common.debug import flog
from utils.database.database_sessions import require_global_database_sessions
from utils.database.song_artists import count_artist_songs, song_artist_label
from utils.database.tags_management import tag_names_by_song
from utils.common.cover_names import title_with_cover
from utils.common.project_names import song_project_name
from utils.database.song_kinds import is_full_album


def _print_table(title: str, rows: list, columns: list) -> None:
    """`columns`: (name, cell, rich column options) - `cell(row)` is the text."""
    table = Table(title=title)
    for name, _, options in columns:
        table.add_column(name, **options)
    for row in rows:
        table.add_row(*(cell(row) for _, cell, _ in columns))
    console.print(table)


def _artist_cell(song: Song) -> str:
    """The Artist column, so a row reads "Project: Artist - Title"."""
    project = song_project_name(song)
    return f"{project}: {song_artist_label(song)}" if project else song_artist_label(song)


def _youtube_url(video_id: str | None) -> str:
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else "—"


# The first columns of every song table
_SONG_COLUMNS = [
    ("Artist", _artist_cell, {"style": "cyan"}),
    ("Title", lambda s: f"{title_with_cover(s)} [full album]" if is_full_album(s) else title_with_cover(s),
     {"style": "magenta"}),
    ("Album", lambda s: s.album or "—", {"style": "green"}),
    ("Year", lambda s: str(s.year) if s.year else "—", {"justify": "right"}),
]


def display_songs(songs: Optional[List[Song]]) -> None:
    if songs:
        _print_table("Songs", songs, _SONG_COLUMNS + [
            ("Language", lambda s: s.language or "—", {}),
            ("Genre", lambda s: s.genre or "—", {}),
            ("Origin", lambda s: s.artist.origin, {}),
        ])


def display_song_links(songs: Optional[List[Song]]) -> None:
    """The songs' library file(s) indexed in files.db, YouTube video, music
    video and Spotify page."""
    if not songs:
        return
    from utils.database.files_index import song_file_paths
    from config.constants import NO_VIDEO_SENTINEL

    try:
        paths = song_file_paths([song.id for song in songs])
    except Exception as exc:
        flog(f"Couldn't read files.db: {type(exc).__name__}: {exc}")
        paths = {}
    # Paths and URLs have no spaces to wrap at - fold them instead of cutting them off with "…"
    fold = {"overflow": "fold"}
    _print_table("Song links", songs, _SONG_COLUMNS + [
        ("Library file", lambda s: "\n".join(paths.get(s.id, [])) or "—", fold),
        ("YouTube", lambda s: "N/A (no video)" if s.youtube_video_id == NO_VIDEO_SENTINEL
         else _youtube_url(s.youtube_video_id), fold),
        ("Music video", lambda s: _youtube_url(s.music_video_youtube_id), fold),
        ("Spotify", lambda s: s.spotify_url or "—", fold),
    ])


def display_artists(artists: Optional[List[Artist]]) -> None:
    if not artists:
        return
    music_session, _ = require_global_database_sessions()
    _print_table("Artists", artists, [
        ("Name", lambda a: a.name, {"style": "cyan"}),
        ("Songs", lambda a: str(count_artist_songs(music_session, a.id)), {}),
        ("Origin", lambda a: a.origin, {}),
        ("Synonyms", lambda a: a.synonyms or "—", {}),
    ])


def display_songs_with_tags(songs: List[Song]) -> None:
    """A table of `songs` with their tags (tag.db). The menu's global
    sessions stay open - other screens still hold their objects."""
    tags_by_song = tag_names_by_song(song.id for song in songs)
    _print_table("Songs & Tags", songs, _SONG_COLUMNS + [
        ("Tags", lambda s: ", ".join(tags_by_song.get(s.id, [])) or "—", {"style": "yellow"}),
    ])
