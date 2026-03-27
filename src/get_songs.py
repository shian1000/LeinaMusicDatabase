from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.datatables import Song, Artist
from src.create_tag_db import Tag, SongTag
from pathlib import Path
from sqlalchemy import func

BASE_DIR = Path(__file__).resolve().parent.parent  # Goes from src → project root

TAG_DATABASE_URL = f"sqlite:///{BASE_DIR}/database/tag.db"

tag_engine = create_engine(TAG_DATABASE_URL)
TagSessionLocal = sessionmaker(bind=tag_engine)
tag_session = TagSessionLocal()

MUSIC_DATABASE_URL = f"sqlite:///{BASE_DIR}/database/music.db"

music_engine = create_engine(MUSIC_DATABASE_URL)
MusicSessionLocal = sessionmaker(bind=music_engine)
music_session = MusicSessionLocal()

def get_all_artists():
    """
    List all artists stored in music.db.
    """
    session = music_session

    artists = (
        session.query(Artist)
        .order_by(func.lower(Artist.name))
        .all()
    )

    session.close()

    return artists

def get_songs_by_artist(artist_name: str):
    songs = (
        music_session
        .query(Song)
        .join(Artist)
        .filter(func.lower(Artist.name) == artist_name.lower())
        .order_by(func.lower(Song.title))
        .all()
    )

    return songs


def get_songs_by_language(language: str):
    songs = (
        music_session
        .query(Song)
        .filter(Song.language == language)
        .order_by(Song.title.asc())
        .all()
    )

    return songs


def get_songs_by_tag(tag_name: str):
    # Step 1: get song IDs from tag.db
    song_id_rows = (
        tag_session.query(SongTag.song_id)
        .join(Tag, SongTag.tag_id == Tag.id)
        .filter(Tag.name == tag_name)
        .all()
    )

    if not song_id_rows:
        return []

    song_ids = [row.song_id for row in song_id_rows]

    # Step 2: fetch songs from music.db
    songs = music_session.query(Song).filter(Song.id.in_(song_ids)).all()

    if not songs:
        return []

    # Step 3: deduplicate
    unique = set()
    song_list = []

    for song in songs:
        key = (song.title.lower(), song.artist.name.lower())
        if key not in unique:
            unique.add(key)
            song_list.append({
                "title": song.title,
                "artist": song.artist.name
            })

    return song_list

# def get_songs_by_artist(artist: str):
#     return get_songs(artist=artist)

# def get_songs_by_language(language: str):
#     return get_songs(language=language)

# def get_songs_by_tag(tag: str):
#     return get_songs(tag=tag)

def get_songs(mode: str, user_query: str):
    
    query: any
    order: any
    join : any
    filter: any

    if(mode == "Artist"):
        if(user_query == "All"):
            query = Artist
            order = func.lower(Artist.name)
        else:
            query = Song
            join = Artist
            filter = func.lower(Artist.name) == user_query.lower()
            order = func.lower(Song.title)

    else:
        print("Nie paviezłoś")

    items = (
        music_session
        .query(query)
        .join(join)
        .filter(filter)
        .order_by(order)
        .all()
    )

    music_session.close()

    list_converter("songs", items)

    return items

def list_converter(mode: str, list: list):
    items = list
    for item in items:
        print(f"- {item.title}")

def get_songs_commented(
    *,
    artist: str | None = None,
    language: str | None = None,
    tag: str | None = None,
    order_alpha: bool = True
):
    """
    This is intended to replace all existing get function as it's getting crowder and crowder
    """
    with MusicSessionLocal() as music_session, TagSessionLocal() as tag_session:
        query = music_session.query(Song).join(Artist)

        if artist:
            query = query.filter(func.lower(Artist.name) == artist.lower())

        if language:
            query = query.filter(Song.language == language)

        if tag:
            song_ids = (
                tag_session.query(SongTag.song_id)
                .join(Tag)
                .filter(Tag.name == tag)
                .subquery()
            )
            query = query.filter(Song.id.in_(song_ids))

        if order_alpha:
            query = query.order_by(func.lower(Song.title))

        return query.all()




if __name__ == "__main__":
    print(get_songs_by_language("Polish"))
