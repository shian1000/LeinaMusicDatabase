from utils.database.music_db_manager import get_music_session
from utils.database.datatables import Song, Artist
from utils.database.tag_db_manager import get_tag_session, Tag, SongTag
from sqlalchemy import func, inspect, or_
import os
from utils.database.datatables import artist_categories, song_categories, hidden_song_categories
import time
from utils.debug import mlog, slog

def _validate_category(category: str, valid_categories: set) -> str | None:
    slog(category)
    normalized = category.strip().lower()
    slog(normalized)
    valid_lower = {c.lower() for c in valid_categories}
    if normalized not in valid_lower:
        print(f"Invalid category '{normalized}'. Valid options: {', '.join(sorted(valid_lower))}")
        return None
    slog(normalized)
    return normalized

def extract_song_info(songs: list[Song], categories: str) -> list[tuple]:
    category_list = [c.strip() for c in categories.split(",")]

    field_map = {
        "title":    lambda s: s.title,
        "artist":   lambda s: s.artist.name,
        "album":    lambda s: s.album,
        "year":     lambda s: str(s.year),
        "language": lambda s: s.language,
        "origin":   lambda s: s.artist.origin,
    }

    result = []
    for song in songs:
        info = tuple(field_map[cat](song) for cat in category_list)
        result.append(info)

    return result

def extract_artist_info(artists: list[Artist], categories: str) -> list[tuple]:
    category_list = [c.strip() for c in categories.split(",")]

    field_map = {
        "name":    lambda a: a.name,
        "origin":   lambda a: a.origin,
    }

    result = []
    for artist in artists:
        info = tuple(field_map[cat](artist) for cat in category_list)
        result.append(info)

    return result

def get_songs_with_empty_category(category: str, sessions: tuple) -> list[Song]:
    music_session, _ = sessions

    db_query = (
        music_session.query(Song)
        .join(Artist)
        .order_by(Artist.name, Song.title)
    )

    column_map = {
        "title":    Song.title,
        "artist":   Artist.name,
        "album":    Song.album,
        "year":     Song.year,
        "language": Song.language,
        "origin":   Artist.origin,
    }

    valid_categories = list(column_map.keys())
    category = _validate_category(category, valid_categories)

    col = column_map[category]
    return db_query.filter(or_(col == None, col == "")).all()

def get_songs_from_db_session(category: str = None, query: str = None, sessions: tuple = None) -> list[Song]:

    music_session, tag_session = sessions
    db_query = music_session.query(Song).join(Artist).order_by(Artist.name, Song.title)

    if category is None:
        return db_query.all()

    valid_categories = song_categories + hidden_song_categories
    category = _validate_category(category, valid_categories)
    slog(category)

    query = query.strip()

    words = [w for w in query.split() if w]
    if not words:
        return []
    
    def get_filter_for_tag():
        matching_tags = (
            tag_session.query(Tag)
            .filter(func.lower(Tag.name).contains(words[0].lower()))
            .all()
        )
        if not matching_tags:
            print(f"No tags found matching '{words[0]}'.")
            return []

        song_ids = set(
            st.song_id for st in
            tag_session.query(SongTag).filter(
                SongTag.tag_id.in_([t.id for t in matching_tags])
            ).all()
        )

        for word in words[1:]:
            matching_tags = (
                tag_session.query(Tag)
                .filter(func.lower(Tag.name).contains(word.lower()))
                .all()
            )
            word_song_ids = set(
                st.song_id for st in
                tag_session.query(SongTag).filter(
                    SongTag.tag_id.in_([t.id for t in matching_tags])
                ).all()
            )
            song_ids &= word_song_ids  # keep only IDs present in both sets

        if not song_ids:
            print(f"No songs found with tags matching '{query}'.")
            return []

        return db_query.filter(Song.id.in_(song_ids)).all()

    def get_filter_for_word(word: str):
        """Returns the appropriate SQLAlchemy filter expression for a single word."""
        filter_map = {
            "title":    lambda w: func.lower(Song.title).contains(w.lower()),
            "artist":   lambda w: func.lower(Artist.name).contains(w.lower()),
            "name":     lambda w: or_(func.lower(Song.title).contains(w.lower()),
                                      func.lower(Artist.name).contains(w.lower())),
            "album":    lambda w: Song.album.ilike(f"%{w}%"),
            "year":     lambda w: Song.year == int(w),
            "language": lambda w: func.lower(Song.language).contains(w.lower()),
            "origin":   lambda w: func.lower(Artist.origin).contains(w.lower()),
        }
        return filter_map[category](word)

    if category == "tag":
        return get_filter_for_tag()

    else:
        from sqlalchemy import or_
        for word in words:
            db_query = db_query.filter(get_filter_for_word(word))
        return db_query.all()


def get_artists_from_db_session(category: str = None, query: str = None, sessions: tuple = None) -> list[Artist]:

    music_session, tag_session = sessions
    db_query = music_session.query(Artist).order_by(Artist.name)

    if category is None:
        return db_query.all()

    valid_categories = artist_categories
    category = _validate_category(category, valid_categories)

    query = query.strip()

    words = [w for w in query.split() if w]
    if not words:
        return []

    def get_filter_for_word(word: str):
        """Returns the appropriate SQLAlchemy filter expression for a single word."""
        filter_map = {
            "name":   lambda w: func.lower(Artist.name).contains(w.lower()),
            "origin":   lambda w: func.lower(Artist.origin).contains(w.lower()),
        }
        return filter_map[category](word)

    from sqlalchemy import or_
    for word in words:
        db_query = db_query.filter(get_filter_for_word(word))

    return db_query.all()



