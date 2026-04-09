from utils.database.music_db_manager import get_music_session
from utils.database.datatables import Song, Artist
from utils.database.tag_db_manager import get_tag_session, Tag, SongTag
from sqlalchemy import func, inspect, or_
import os
from utils.database.datatables import artist_categories, song_categories, hidden_song_categories
from contextlib import contextmanager
import time
from utils.debug import mlog, slog

@contextmanager
def _sessions():
    music_session = get_music_session()
    tag_session = get_tag_session()
    try:
        yield music_session, tag_session
    finally:
        music_session.close()
        tag_session.close()

def _validate_category(category: str, valid_categories: set) -> str | None:
    normalized = category.strip().lower()
    valid_lower = {c.lower() for c in valid_categories}
    if normalized not in valid_lower:
        print(f"Invalid category '{normalized}'. Valid options: {', '.join(sorted(valid_lower))}")
        return None
    return normalized

def _apply_filter(items, python_filter):
    if python_filter:
        return [item for item in items if python_filter(item)]
    return items



def _progressive_name_search(db_query, query: str, candidates_filter):
    words = [w for w in query.split() if w]
    if not words:
        return []
        
    candidates = (
        db_query
        .filter(or_(*candidates_filter))  # * unpacks the tuple into separate args
        .order_by(Artist.name, Song.year)
        .all()
    )

    for w in words[1:]:
        if len(candidates) <= 1:
            break
        lw = w.lower()
        narrowed = [
            s for s in candidates
            if lw in (s.title or "").lower() or lw in (s.artist.name or "").lower()
        ]
        if narrowed:
            candidates = narrowed

    return [(s.artist.name, s.title) for s in candidates]


def get_artists_names(category: str, query: str) -> list[str]:
    
    category = _validate_category(category, artist_categories)
    if category is None:
        return []

    query = query.strip()
    python_filter = None

    with _sessions() as (music_session, _):
        db_query = music_session.query(Artist)

        if category == "artist":
            python_filter = lambda a: query.lower() in a.name.lower()
        elif category == "origin":
            python_filter = lambda a: a.origin and query.lower() in a.origin.lower()

        ordered_q = db_query.order_by(Artist.name)
        mlog(f"[debug] About to execute artists query for category='{category}', query='{query}'")
        artists = _apply_filter(ordered_q.all(), python_filter)

        if not artists:
            print(f"No results for {category}='{query}'.")
            return []

        return [a.name for a in artists]


def get_songs_names(category: str, query: str) -> list[tuple[str, str]]:
    valid_categories = song_categories + hidden_song_categories
    category = _validate_category(category, valid_categories)
    if category is None:
        return []

    query = query.strip()
    python_filter = None

    with _sessions() as (music_session, tag_session):
        db_query = music_session.query(Song).join(Artist)

        words = [w for w in query.split() if w]
        if not words:
            return []

        first = words[0].lower()
        mlog(f"[debug] 'name' search initial word='{first}'")

        def filter_tag():
            nonlocal db_query
            matching_tags = (
                tag_session.query(Tag)
                .filter(func.lower(Tag.name).contains(query.lower()))
                .all()
            )
            if not matching_tags:
                print(f"No tags found matching '{query}'.")
                return None, None

            tag_ids = [tag.id for tag in matching_tags]
            song_ids = [
                st.song_id for st in
                tag_session.query(SongTag).filter(SongTag.tag_id.in_(tag_ids)).all()
            ]
            if not song_ids:
                print(f"No songs found with tag matching '{query}'.")
                return None, None

            return None, db_query.filter(Song.id.in_(song_ids))

        action_map = {
            "title": lambda: ((func.lower(Song.title).contains(first),), db_query),
            "artist": lambda: ((func.lower(Artist.name).contains(first),), db_query),
            "name": lambda: ((func.lower(Song.title).contains(first),
                    func.lower(Artist.name).contains(first)), db_query),
            "album":    lambda: ((Song.album.ilike(f"%{query}%"),), db_query),
            "year":     lambda: ((Song.year == int(query),), db_query),
            "language": lambda: ((func.lower(Song.language).contains(query.lower()),), db_query),
            "origin":   lambda: ((func.lower(Artist.origin).contains(query.lower()),), db_query),
            "tag":      filter_tag,
        }

        candidates_filter, db_query = action_map[category]()
        if db_query is None:
            return []

        return _progressive_name_search(db_query, query, candidates_filter)

        ordered_q = db_query.order_by(Artist.name, Song.year)
        mlog(f"[debug] About to execute songs query for category='{category}', query='{query}'")
        songs = _apply_filter(ordered_q.all(), python_filter)


        if not songs:
            print(f"No results for {category}='{query}'.")
            return []

        return [(s.artist.name, s.title) for s in songs]

def get_songs_objects(session, songs_names):
    """This function takes music db session and song_names with artists and songs, opens songs tables and returns db objects as a list"""
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

def get_artists_objects(session, artist_names):
    """This function takes music db session and song_names with artists, opens artist tables and returns db objects as a list"""
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