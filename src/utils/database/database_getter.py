from utils.database.tag_db_manager import Tag, SongTag
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload
from utils.database.datatables import artist_categories, song_categories, search_only_categories, Song, Artist, AdditionalSongArtist, Album
from utils.common.debug import slog
from utils.common.text_utils import is_soundtrack_artist, normalize_text
from utils.common.normalizer import normalize
from collections import Counter
from utils.database.database_sessions import require_global_database_sessions
from utils.database.song_artists import (
    additional_artist_name_contains,
    artist_name_or_synonym_contains,
    song_all_artists,
    song_artist_label,
    song_has_artist_id,
)
from utils.database.song_kinds import full_album_filter


class SongSearchFilters:
    """Encapsulates database filters for song searches."""

    @staticmethod
    def title(query: str):
        """Filter songs by title or its Latin form containing the query."""
        return or_(
            func.lower(Song.title).contains(query.lower()),
            func.lower(func.coalesce(Song.title_latin, "")).contains(query.lower()),
        )

    @staticmethod
    def artist_name(query: str):
        """Filter songs by artist name or synonym (primary or additional) containing the query."""
        return or_(
            artist_name_or_synonym_contains(Artist, query),
            additional_artist_name_contains(query),
        )

    @staticmethod
    def album(query: str):
        """Filter songs by album name, or its album row's Latin form, containing the query."""
        return or_(
            func.lower(Song.album).contains(query.lower()),
            Song.album_id.in_(
                select(Album.id).where(func.lower(func.coalesce(Album.title_latin, "")).contains(query.lower()))
            ),
        )

    @staticmethod
    def year(query: str):
        """Filter songs by exact year."""
        return Song.year == int(query)

    @staticmethod
    def language(query: str):
        """Filter songs by language containing the query."""
        return func.lower(Song.language).contains(query.lower())

    @staticmethod
    def origin(query: str):
        """Filter songs by artist origin containing the query."""
        return func.lower(Artist.origin).contains(query.lower())

    @staticmethod
    def artist_id(query: str):
        """Filter songs by exact artist ID (primary or additional)."""
        return song_has_artist_id(int(query))

    @staticmethod
    def general_search(query: str):
        """Search in title (or its Latin form) and artist name or synonym (general search)."""
        return or_(
            SongSearchFilters.title(query),
            artist_name_or_synonym_contains(Artist, query),
            additional_artist_name_contains(query),
        )


# Map categories to their corresponding filter functions
CATEGORY_DB_FILTERS = {
    song_categories[0]: SongSearchFilters.title,  # "title"
    song_categories[1]: SongSearchFilters.artist_name,  # "artist name"
    song_categories[2]: SongSearchFilters.album,  # "album"
    song_categories[3]: SongSearchFilters.year,  # "year"
    song_categories[4]: SongSearchFilters.language,  # "language"
    song_categories[5]: SongSearchFilters.origin,  # "artist origin"
    song_categories[7]: SongSearchFilters.artist_id,  # "artist id"
    search_only_categories[0]: SongSearchFilters.general_search,  # "name"
    search_only_categories[1]: SongSearchFilters.general_search,  # "full album" (+ full_album_filter())
}


# Searched by an equal whole number only (the text in "isn't ..."), never by
# the substring fallback
_WHOLE_NUMBER_CATEGORIES = {
    song_categories[3]: "a year",  # "year"
    song_categories[7]: "an artist id",  # "artist id"
}


# The Python fallback and every display path read each song's additional
# artists; without this that's one lazy query per song over the whole DB.
_EAGER_ADDITIONAL_ARTISTS = selectinload(Song.additional_artist_links).selectinload(AdditionalSongArtist.artist)


def _artist_name_and_synonyms(artist: Artist) -> str:
    return normalize_text(f"{artist.name or ''} {artist.name_latin or ''} {artist.synonyms or ''}")


def _title_and_latin(song: Song) -> str:
    return normalize_text(f"{song.title or ''} {song.title_latin or ''}")


def _all_artist_names(song: Song) -> str:
    """Every artist's name and synonyms, for the Python-side fallback filter."""
    return " ".join(_artist_name_and_synonyms(a) for a in song_all_artists(song))


class SongNormalizedFields:
    """Encapsulates field extraction functions for normalized text search."""

    @staticmethod
    def title(song: Song) -> str:
        """Extract and normalize song title and its Latin form."""
        return _title_and_latin(song)

    @staticmethod
    def artist_name(song: Song) -> str:
        """Extract and normalize all artist names (primary and additional)."""
        return _all_artist_names(song)

    @staticmethod
    def album(song: Song) -> str:
        """Extract and normalize album name and its album row's Latin form."""
        latin = song.album_entry.title_latin if song.album_entry is not None else None
        return normalize_text(f"{song.album or ''} {latin or ''}")

    @staticmethod
    def year(song: Song) -> str:
        """Extract year as string."""
        return str(song.year) if song.year is not None else ""

    @staticmethod
    def language(song: Song) -> str:
        """Extract and normalize language."""
        return normalize_text(song.language or "")

    @staticmethod
    def origin(song: Song) -> str:
        """Extract and normalize artist origin."""
        return normalize_text(song.artist.origin or "")

    @staticmethod
    def artist_id(song: Song) -> str:
        """Extract artist ID as string."""
        return str(song.artist_id) if song.artist_id is not None else ""

    @staticmethod
    def general_search(song: Song) -> str:
        """Combine title and artist name for general search."""
        return f"{_title_and_latin(song)} {_all_artist_names(song)}"


# Map categories to their corresponding field extraction functions
CATEGORY_NORMALIZED_FIELDS = {
    song_categories[0]: SongNormalizedFields.title,  # "title"
    song_categories[1]: SongNormalizedFields.artist_name,  # "artist name"
    song_categories[2]: SongNormalizedFields.album,  # "album"
    song_categories[3]: SongNormalizedFields.year,  # "year"
    song_categories[4]: SongNormalizedFields.language,  # "language"
    song_categories[5]: SongNormalizedFields.origin,  # "artist origin"
    song_categories[7]: SongNormalizedFields.artist_id,  # "artist id"
    search_only_categories[0]: SongNormalizedFields.general_search,  # "name"
    search_only_categories[1]: SongNormalizedFields.general_search,  # "full album"
}




def _songs_by_tag(words: list[str], db_query, tag_session) -> list[Song]:
    """
    Find songs whose tags match all given words (AND logic).
    
    Searches for tags matching all provided words and returns only songs
    that have ALL of those tags.
    
    Args:
        words: List of tag name words to search for
        db_query: Base SQLAlchemy query object for Song
        tag_session: Database session for tag queries
    
    Returns:
        list[Song]: Songs matching all provided tags, empty list if none found
    """
    song_ids = None
    
    for word in words:
        # Find all tags containing this word
        matching_tags = (
            tag_session.query(Tag)
            .filter(func.lower(Tag.name).contains(word.lower()))
            .all()
        )
        
        # Find all song IDs with these tags
        word_ids = {
            st.song_id for st in
            tag_session.query(SongTag)
            .filter(SongTag.tag_id.in_([t.id for t in matching_tags]))
            .all()
        }
        
        # AND logic: intersect with previous results
        song_ids = word_ids if song_ids is None else song_ids & word_ids
        
        # Early exit if no matches
        if not song_ids:
            slog(f"No songs found with tags matching '{' '.join(words)}'.")
            return []

    # Return songs matching all criteria
    if song_ids:
        return db_query.filter(Song.id.in_(song_ids)).all()
    
    return []

def _validate_category(category: str, valid_categories: set) -> str | None:
    """`category` lowercased, or None after printing the valid ones - the
    caller then finds nothing."""
    normalized = category.strip().lower()
    valid_lower = {c.lower() for c in valid_categories}
    if normalized not in valid_lower:
        print(f"Invalid category '{normalized}'. Valid options: {', '.join(sorted(valid_lower))}")
        return None
    return normalized

def _normalized_python_filter(candidates: list, words: list[str], get_field) -> list:
    """
    Python-side fallback filter: keep candidates whose normalized field contains
    every word, case-insensitively. Used when DB-level LIKE filtering can't be
    trusted (non-ASCII queries) or came back empty.
    """
    normalized_words = [normalize_text(w).casefold() for w in words]
    return [c for c in candidates if all(w in get_field(c).casefold() for w in normalized_words)]

def extract_db_object_info(songs, categories: str = None) -> list[tuple]:
    if not songs:
        slog("No such songs found")
        return


    category_list = [c.strip() for c in categories.split(",")] if categories else []

    slog(songs)

    slog(songs[0])

    if type(songs[0]) == Song:
        field_map = {
            song_categories[0]: lambda s: s.title,
            song_categories[1]: lambda s: song_artist_label(s),
            song_categories[2]: lambda s: s.album,
            song_categories[3]: lambda s: str(s.year),
            song_categories[4]: lambda s: s.language,
            song_categories[5]: lambda s: s.artist.origin,
            song_categories[7]: lambda s: s.artist_id
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
        slog(invalid_categories)
        print (f"Invalid categories: {invalid_categories}. Valid options for {type(songs[0])} are: {list(field_map.keys())}")
        return

    result = []

    for song in songs:
        info = tuple(field_map[cat](song) for cat in category_list)
        result.append(info)

    slog(result)

    return result

def get_songs_with_empty_category(category: str) -> list[Song]:

    music_session, _ = require_global_database_sessions()

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
    if category is None:
        return []

    col = column_map[category]
    return db_query.filter(or_(col == None, col == "")).all()


def get_songs_with_album_missing_year() -> list[Song]:
    """Songs that already have an album recorded (a real album name, or the
    SINGLES_ALBUM sentinel from constants.py) but no
    year yet. This is the source set for "Fill missing data -> Years": we
    only look up a year once we know what to search for (an album name, or
    the song title itself for a single)."""

    music_session, _ = require_global_database_sessions()

    return (
        music_session.query(Song)
        .join(Artist)
        .filter(Song.year == None)
        .filter(Song.album != None, Song.album != "")
        .order_by(Artist.name, Song.album, Song.title)
        .all()
    )


def get_known_album_year(artist_id: int, album: str) -> int | None:
    """Year already recorded on another song of the same album: same primary
    artist, album name equal after normalize(). Several different years
    (a reissue, a stray import) -> the most common one, the earliest on a
    tie. None if no song of that album has a year yet.

    A soundtrack album ("Persona 5 Strikers OST" - a SOUNDTRACK_ARTIST_MARKERS
    word in the album name) matches across all artists: its tracks are
    usually stored under a different "artist" each (composer, game title,
    uploader), while the album name itself is specific. Any other album
    keeps the same-artist rule - "Greatest Hits" or "Live" by two artists
    aren't one album.

    The caller keeps SINGLES_ALBUM away from here - it isn't a real shared
    album, so one single's year says nothing about another's."""
    target = normalize(album)
    if not target:
        return None

    music_session, _ = require_global_database_sessions()
    query = music_session.query(Song.album, Song.year).filter(Song.year != None, Song.album != None)
    if not is_soundtrack_artist(album):
        query = query.filter(Song.artist_id == artist_id)
    rows = query.all()
    counts = Counter(year for row_album, year in rows if normalize(row_album) == target)
    if not counts:
        return None
    return max(counts, key=lambda year: (counts[year], -year))



def get_songs_from_db_session(category: str = None, query: str = None) -> list[Song]:

    slog("get_songs_from_db_session")

    if type(query) == int:
        query = str(query)
        
    slog(query)
    music_session, tag_session = require_global_database_sessions()
    base_query = (
        music_session.query(Song)
        .join(Artist)
        .options(_EAGER_ADDITIONAL_ARTISTS)
        .order_by(Artist.name, Song.title)
    )

    if category is None:
        return base_query.all()
    
    # Determine if the query contains any ASCII alphanumeric characters
    # (isalnum(), so a number counts too): only then is DB-level LIKE
    # filtering tried first.
    query_has_any_standard_latin_characters = any(c.isascii() and c.isalnum() for c in (query or ""))

    category = _validate_category(category, song_categories + search_only_categories)
    if category is None:
        return []
    words = [w for w in (query or "").strip().split() if w]
    slog(category, words)

    if category == search_only_categories[1]:
        # Full albums only, searched by title / artist like "name"; no words = all of them
        base_query = base_query.filter(full_album_filter())
        if not words:
            return base_query.all()

    if not words:
        return []

    if category == "tag":
        return _songs_by_tag(words, base_query, tag_session)

    if category in _WHOLE_NUMBER_CATEGORIES:
        # Equal, never a substring: no Python fallback, which would find
        # artist id 5 in 15, 50, ... once SQL found nothing
        if not all(w.isascii() and w.isdigit() for w in words):
            print(f"'{query.strip()}' isn't {_WHOLE_NUMBER_CATEGORIES[category]} - type a whole number.")
            return []
        filtered_query = base_query
        for word in words:
            filtered_query = filtered_query.filter(CATEGORY_DB_FILTERS[category](word))
        return filtered_query.all()

    # Primary DB-level filtering
    if (query_has_any_standard_latin_characters):
        filtered_query = base_query
        for word in words:
            filtered_query = filtered_query.filter(CATEGORY_DB_FILTERS[category](word))
        results = filtered_query.all()
        if results:
            return results

    # Python fallback for difficult characters - requires loading the db into the memory
    slog("Falling back to Python-side normalized filtering")
    get_field = CATEGORY_NORMALIZED_FIELDS[category]
    if category == song_categories[2]:
        base_query = base_query.options(selectinload(Song.album_entry))
    candidates = base_query.all()
    filtered = _normalized_python_filter(candidates, words, get_field)

    if not filtered:
        slog("No such songs found")

    return filtered



def get_artists_from_db_session(
    category: str = None,
    query: str = None,
    aggressive_search: bool = False,
    session=None,
) -> list[Artist]:
    """The artists matching `query` in `category`, by name; every artist
    without a category, none without a query. `session` defaults to the
    global music session."""

    slog(category)
    slog(query)
    music_session = session or require_global_database_sessions()[0]
    db_query = music_session.query(Artist).order_by(Artist.name)

    if category is None:
        return db_query.all()

    valid_categories = artist_categories
    category = _validate_category(category, valid_categories)
    if category is None:
        return []

    query = (query or "").strip()
    words = [w for w in query.split() if w]
    if not words:
        return []

    if category == artist_categories[2]:  # "artist id": a whole number, never a substring
        if not query.isascii() or not query.isdigit():
            print(f"'{query}' isn't an artist id - type a whole number.")
            return []
        return db_query.filter(Artist.id == int(query)).all()

    # Determine if the query contains any ASCII alphanumeric characters,
    # same logic as get_songs_from_db_session.
    query_has_any_standard_latin_characters = any(c.isascii() and c.isalnum() for c in query)

    CATEGORY_DB_FILTERS = {
        artist_categories[0]: lambda w: artist_name_or_synonym_contains(Artist, w),
        artist_categories[1]: lambda w: func.lower(Artist.origin).contains(w.lower()),
    }

    CATEGORY_NORMALIZED_FIELDS = {
        artist_categories[0]: _artist_name_and_synonyms,
        artist_categories[1]: lambda artist: normalize_text(artist.origin),
    }

    if aggressive_search or not query_has_any_standard_latin_characters:
        # Python-side normalized filtering — handles Polish and other non-ASCII characters
        slog("Falling back to Python-side normalized filtering")
        get_field = CATEGORY_NORMALIZED_FIELDS[category]
        candidates = db_query.all()
        filtered = _normalized_python_filter(candidates, words, get_field)
        if not filtered:
            slog("No such artists found")
        return filtered

    # Primary DB-level filtering (ASCII queries)
    filtered_query = db_query
    for word in words:
        filtered_query = filtered_query.filter(CATEGORY_DB_FILTERS[category](word))
    results = filtered_query.all()
    if results:
        return results

    # Python fallback even for ASCII queries, in case DB-level filtering missed anything
    slog("Falling back to Python-side normalized filtering")
    get_field = CATEGORY_NORMALIZED_FIELDS[category]
    candidates = db_query.all()
    filtered = _normalized_python_filter(candidates, words, get_field)
    if not filtered:
        slog("No such artists found")
    return filtered

