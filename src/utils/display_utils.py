from rich.console import Console
from rich.table import Table
from utils.database.music_db_manager import get_music_session
from utils.database.datatables import Song, Artist
from utils.database.tag_db_manager import get_tag_session, Tag, SongTag
from sqlalchemy import func
import time
from utils.debug import slog


def _create_table(title, columns):
    table = Table(title=title)
    for name, style, justify in columns:
        kwargs = {}
        if style:
            kwargs["style"] = style
        if justify:
            kwargs["justify"] = justify
        table.add_column(name, **kwargs)
    return table



def display_songs(songs = None):
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
            "V" if song.nostalgic else "X",
            "V" if song.melancholic else "X",
            "V" if song.party else "X",
        )

    console.print(table)

def display_artists(artists = None):
    console = Console()

    table = _create_table("Music Library", [
        ("Name", "cyan", None),
        ("Origin", None, None)
    ])

    for artist in artists:
        table.add_row(
            artist.name,
            artist.origin,
        )

    console.print(table)

def display_songs_with_tags(songs, sessions):
    music_session, tag_session = sessions
    console = Console()

    song_tags = tag_session.query(SongTag).all()
    tag_ids = {st.tag_id for st in song_tags}

    tags_by_id = {}
    if tag_ids:
        tags = tag_session.query(Tag).filter(Tag.id.in_(tag_ids)).all()
        tags_by_id = {tag.id: tag.name for tag in tags}

    song_tag_map = {}
    for st in song_tags:
        song_tag_map.setdefault(st.song_id, []).append(tags_by_id.get(st.tag_id, "?"))

    tag_session.close()

    table = _create_table("Songs & Tags", [
        ("Title", "cyan", None),
        ("Artist", "magenta", None),
        ("Album", "green", None),
        ("Year", None, "right"),
        ("Tags", "yellow", None),
    ])

    songs = music_session.query(Song).join(Artist).order_by(Artist.name, Song.year).all()

    for song in songs:
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