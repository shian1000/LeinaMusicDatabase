from utils.database.music_db_manager import get_music_session
from utils.database.datatables import Song, Artist
from utils.database.tag_db_manager import get_tag_session, Tag, SongTag
from sqlalchemy import func, inspect, or_, select
import os
from utils.database.datatables import artist_categories, song_categories, search_only_categories, Song, Artist
import time
from utils.debug import mlog, slog
from utils.text_utils import normalize_text
from utils.database.database_sessions import get_global_database_sessions

def _validate_category(category: str, valid_categories: set) -> str | None:
    slog(category)
    normalized = category.strip().lower()
    slog(normalized)
    valid_lower = {c.lower() for c in valid_categories}
    slog(valid_categories)
    if normalized not in valid_lower:
        print(f"Invalid category '{normalized}'. Valid options: {', '.join(sorted(valid_lower))}")
        return None
    slog(normalized)
    return normalized

def extract_db_object_info(songs, categories: str = None) -> list[tuple]:
    if not songs:
        print("No such songs found")
        return


    category_list = [c.strip() for c in categories.split(",")] if categories else []

    slog(songs)

    slog(songs[0])

    if type(songs[0]) == Song:
        field_map = {
            song_categories[0]: lambda s: s.title,
            song_categories[1]: lambda s: s.artist.name,
            song_categories[2]: lambda s: s.album,
            song_categories[3]: lambda s: str(s.year),
            song_categories[4]: lambda s: s.language,
            song_categories[5]: lambda s: s.artist.origin,
        }
        if not category_list:
            category_list = [song_categories[1], song_categories[0]]
    else:
        field_map = {
            artist_categories[0]: lambda a: a.name,
            artist_categories[1]: lambda a: a.origin,
        }
        if not category_list:
            category_list = [artist_categories[0]]

    slog(category_list)
    slog(field_map)

    invalid_categories = [c for c in category_list if c not in field_map]
    if invalid_categories:
        print (f"Invalid categories: {invalid_categories}. Valid options for {type(songs[0])} are: {list(field_map.keys())}")
        return

    result = []

    for song in songs:
        info = tuple(field_map[cat](song) for cat in category_list)
        result.append(info)

    return result

def get_songs_with_empty_category(category: str) -> list[Song]:

    music_session, _ = get_global_database_sessions()

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

def get_songs_from_db_session(category: str = None, query: str = None) -> list[Song]:

    slog(query)
    music_session, tag_session = get_global_database_sessions()
    db_query = music_session.query(Song).join(Artist).order_by(Artist.name, Song.title)

    if category is None:
        return db_query.all()

    valid_categories = song_categories + search_only_categories
    category = _validate_category(category, valid_categories)
    slog(category)

    query = query.strip()
    slog(query)

    words = [w for w in query.split() if w]
    if not words:
        return []
    
    slog(words)
    
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
        slog(word)
        """Returns the appropriate SQLAlchemy filter expression for a single word."""
        filter_map = {
            song_categories[0]: lambda w: func.lower(Song.title).contains(w.lower()),
            song_categories[1]: lambda w: func.lower(Artist.name).contains(w.lower()),
            song_categories[2]: lambda w: func.lower(Song.album).contains(w.lower()),
            song_categories[3]: lambda w: Song.year == int(w),
            song_categories[4]: lambda w: func.lower(Song.language).contains(w.lower()),
            song_categories[5]: lambda w: func.lower(Artist.origin).contains(w.lower()),
            search_only_categories[0]: lambda w: or_(func.lower(Song.title).contains(w.lower()),
                                      func.lower(Artist.name).contains(w.lower()))
        }
        return filter_map[category](word)

    if category == "tag":
        return get_filter_for_tag()

    else:
        from sqlalchemy import or_
        filtered_query = db_query
        for word in words:
            filtered_query = filtered_query.filter(get_filter_for_word(word))
            slog(filtered_query)

        results = filtered_query.all()
        if results:
            return results

        # Fallback: SQL returned no rows — perform Unicode-aware normalized filtering in Python
        # Normalize search words
        normalized_words = [normalize_text(w).casefold() for w in words]

        # Fetch a candidate set (broad) and filter in Python
        candidates = music_session.query(Song).join(Artist).order_by(Artist.name, Song.title).all()

        # field getter per category — normalized and combined where appropriate
        def song_field_normalized(s: Song) -> str:
            field_map = {
                song_categories[0]: lambda s: normalize_text(s.title or ""),
                song_categories[1]: lambda s: normalize_text(s.artist.name or ""),
                song_categories[2]: lambda s: normalize_text(s.album or ""),
                song_categories[3]: lambda s: str(s.year) if s.year is not None else "",
                song_categories[4]: lambda s: normalize_text(s.language or ""),
                song_categories[5]: lambda s: normalize_text(s.artist.origin or ""),
                search_only_categories[0]: lambda s: f"{normalize_text(s.title or '')} {normalize_text(s.artist.name or '')}"
            }
            return field_map[category](s).casefold()

        slog("Falling back to Python-side normalized filtering")
        filtered = [
            s for s in candidates
            if all(w in song_field_normalized(s) for w in normalized_words)
        ]

        if not filtered:
            print("No such songs found")

        return filtered

def get_artists_from_db_session(
    category: str = None,
    query: str = None,
    artist_id: int = None,
    aggresive_search: bool = False
) -> list[Artist]:

    slog(category)
    slog(query)
    music_session, tag_session = get_global_database_sessions()
    db_query = music_session.query(Artist).order_by(Artist.name)

    if category is None:
        return db_query.all()

    valid_categories = artist_categories
    category = _validate_category(category, valid_categories)

    if query:
        query = query.strip()

        words = [w for w in query.split() if w]
        if not words:
            slog(words)
            return []

        if aggresive_search:
            # Normalize the search words once up front
            normalized_words = [normalize_text(w) for w in words]

            # Pull a broader candidate set from the DB (no per-word filtering),
            # then do precise normalized matching in Python
            candidates = db_query.all()

            field_getter = {
                artist_categories[0]:   lambda artist: normalize_text(artist.name),
                artist_categories[1]: lambda artist: normalize_text(artist.origin),
            }[category]

            slog(field_getter)
            slog([
                artist for artist in candidates
                if all(w in field_getter(artist) for w in normalized_words)
            ]
    )
            return [
                artist for artist in candidates
                if all(w in field_getter(artist) for w in normalized_words)
            ]
        
        def get_filter_for_word(word: str):
            """Returns the appropriate SQLAlchemy filter expression for a single word."""
            filter_map = {
                artist_categories[0]: lambda w: func.lower(Artist.name).contains(w.lower()),
                artist_categories[1]: lambda w: func.lower(Artist.origin).contains(w.lower()),
            }
            slog (filter_map)
            return filter_map[category](word)

        for word in words:
            db_query = db_query.filter(get_filter_for_word(word))
        
        slog (word)
        return db_query.all()


    if artist_id:
        slog(artist_id)
        artists_objs_list = []
        artists_objs_list.append(music_session.query(Artist).filter(Artist.id == artist_id).first())
        return artists_objs_list
    
    print("get_artist_from_db_session needs either str or id to get artists list")
    return


