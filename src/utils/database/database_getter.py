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


def _progressive_name_search(db_query, query: str):
    """Progressively narrow search by splitting query into words.

    - db_query: SQLAlchemy query already joining Song and Artist
    - query: raw user input string

    Returns a list of (artist_name, song_title) tuples (may be empty).
    """
    words = [w for w in query.split() if w]
    if not words:
        return []

    first = words[0].lower()
    mlog(f"[debug] 'name' search initial word='{first}'")
    try:
        candidates = (
            db_query
            .filter(
                or_(
                    func.lower(Song.title).contains(first),
                    func.lower(Artist.name).contains(first),
                )
            )
            .order_by(Artist.name, Song.year)
            .all()
        )
    except Exception as e:
        mlog(f"[debug] Error executing initial 'name' query: {e}")
        raise

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


def get_artists_from_db(category: str, query: str) -> list[str]:
    
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


def get_songs_from_db(category: str, query: str) -> list[tuple[str, str]]:
    valid_categories = song_categories + hidden_song_categories
    category = _validate_category(category, valid_categories)
    if category is None:
        return []

    query = query.strip()
    python_filter = None

    with _sessions() as (music_session, tag_session):
        db_query = music_session.query(Song).join(Artist)

        if category == "title":
            python_filter = lambda s: query.lower() in s.title.lower()
        elif category == "artist":
            python_filter = lambda s: query.lower() in s.artist.name.lower()
        elif category == "name":
            #I might user it for other categories as well. Or better yet - replace elifs with an action map
            return _progressive_name_search(db_query, query)
        elif category == "album":
            if query == "":
                db_query = db_query.filter((Song.album == None) | (Song.album == ""))
            else:
                python_filter = lambda s: s.album and query.lower() in s.album.lower()
        elif category == "year":
            db_query = db_query.filter(Song.year == int(query))
        elif category == "language":
            db_query = db_query.filter(func.lower(Song.language).contains(query.lower()))
        elif category == "origin":
            db_query = db_query.filter(func.lower(Artist.origin).contains(query.lower()))
        elif category == "tag":
            matching_tags = (
                tag_session.query(Tag)
                .filter(func.lower(Tag.name).contains(query.lower()))
                .all()
            )
            if not matching_tags:
                print(f"No tags found matching '{query}'.")
                music_session.close()
                tag_session.close()
                return []

            tag_ids = [tag.id for tag in matching_tags]
            song_ids = [
                st.song_id for st in
                tag_session.query(SongTag).filter(SongTag.tag_id.in_(tag_ids)).all()
            ]
            if not song_ids:
                print(f"No songs found with tag matching '{query}'.")
                music_session.close()
                tag_session.close()
                return []

            db_query = db_query.filter(Song.id.in_(song_ids))

        ordered_q = db_query.order_by(Artist.name, Song.year)
        mlog(f"[debug] About to execute songs query for category='{category}', query='{query}'")
        songs = _apply_filter(ordered_q.all(), python_filter)


        if not songs:
            print(f"No results for {category}='{query}'.")
            return []

        return [(s.artist.name, s.title) for s in songs]
