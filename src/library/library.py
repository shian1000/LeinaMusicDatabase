from utils.database.music_db_manager import get_music_session
from utils.database.datatables import Song, Artist
from sqlalchemy import func, or_
from utils.debug import slog

def search_songs_from_name(query: str) -> list[tuple[str, str]]:

    words = query.strip().split()
    if not words:
        print("Empty query.")
        return []
    
    slog(words)

    session = get_music_session()

    slog(session)

    def build_filter(word):
        w = word.lower()
        return or_(
            func.lower(Song.title).contains(w),
            func.lower(Artist.name).contains(w),
            func.lower(Song.album).contains(w),
        )

    candidates = (
        session.query(Song)
        .join(Artist)
        .filter(build_filter(words[0]))
        .all()
    )

    slog(candidates)

    for word in words[1:]:
        if len(candidates) <= 1:
            break

        w = word.lower()
        narrowed = [
            song for song in candidates
            if w in (song.title or "").lower()
            or w in (song.artist.name or "").lower()
            or w in (song.album or "").lower()
        ]

        if narrowed:
            candidates = narrowed

    if not candidates:
        print(f"No songs found for query '{query}'.")
        return []

    results = [(song.artist.name, song.title) for song in candidates]

    session.close()

    if len(results) == 1:
        print(f"Found: {results[0][0]} - {results[0][1]}")
    else:
        print(f"Multiple matches found for '{query}':")
        for i, (artist, title) in enumerate(results, 1):
            print(f"  {i}. {artist} - {title}")

    return results