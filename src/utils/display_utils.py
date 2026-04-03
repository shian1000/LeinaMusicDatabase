from rich.console import Console
from rich.table import Table
from utils.database.music_db_manager import get_music_session
from utils.database.datatables import Song, Artist
from utils.database.tag_db_manager import get_tag_session, Tag, SongTag
from sqlalchemy import func

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

def _fetch_songs_by_names(session, songs_names):
    songs = []
    for artist_name, song_title in songs_names:
        if not artist_name or not song_title:
            continue
        song = (
            session.query(Song)
            .join(Artist)
            .filter(
                func.lower(Song.title) == (song_title or "").lower(),
                func.lower(Artist.name) == (artist_name or "").lower(),
            )
            .first()
        )
        if song:
            songs.append(song)
        else:
            print(f"Song not found: '{artist_name}' - '{song_title}'")
    return songs

def _fetch_artists_by_names(session, artist_names):
    artists = []
    for name in artist_names:
        if not name:
            continue
        artist = (
            session.query(Artist)
            .filter(func.lower(Artist.name) == name.lower())
            .first()
        )
        if artist:
            artists.append(artist)
        else:
            print(f"Artist not found: '{name}'")
    return artists

def display_songs(songs_names = None):
    session = get_music_session()
    console = Console()

    table = _create_table("Music Library", [
        ("Title", "cyan", None),
        ("Artist", "magenta", None),
        ("Album", "green", None),
        ("Year", None, "right"),
        ("Language", None, None),
        ("Nostalgic", None, "center"),
        ("Melancholic", None, "center"),
        ("Party", None, "center"),
    ])

    if songs_names:
        songs = _fetch_songs_by_names(session, songs_names)
    else:
        songs = session.query(Song).join(Artist).order_by(Artist.name, Song.year).all()

    for song in songs:
        table.add_row(
            song.title,
            song.artist.name,
            song.album or "—",
            str(song.year) if song.year else "—",
            song.language or "—",
            "V" if song.nostalgic else "X",
            "V" if song.melancholic else "X",
            "V" if song.party else "X",
        )

    session.close()
    console.print(table)

def display_artists(artist_names = None):
    session = get_music_session()
    console = Console()

    table = _create_table("Artists", [
        ("Artist", "magenta", None),
        ("Origin", "yellow", None),
        ("Songs", "cyan", "right"),
    ])

    if artist_names:
        artists = _fetch_artists_by_names(session, artist_names)
    else:
        artists = session.query(Artist).order_by(Artist.name).all()

    for artist in artists:
        table.add_row(
            artist.name,
            artist.origin or "—",
            str(len(artist.songs))
        )

    session.close()
    console.print(table)

def display_songs_with_tags():
    music_session = get_music_session()
    tag_session = get_tag_session()
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