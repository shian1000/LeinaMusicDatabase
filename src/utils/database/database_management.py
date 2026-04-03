from utils.database.music_db_manager import get_music_session
from utils.database.datatables import Song, Artist
from sqlalchemy import func
from utils.database.datatables import song_categories

def edit_song_entry(song_query: str, category: str, new_value: str):

    old_value = None

    if " - " not in song_query:
        print("Invalid format. Use: 'Artist - Song Title'")
        return

    artist_name, song_title = [part.strip() for part in song_query.split(" - ", 1)]
    category = category.strip().lower()
    new_value = new_value.strip()

    if category not in song_categories:
        print(f"Invalid category '{category}'. Editable options: {', '.join(sorted(song_categories))}")
        return

    session = get_music_session()

    song = (
        session.query(Song)
        .join(Artist)
        .filter(
            func.lower(Song.title) == song_title.lower(),
            func.lower(Artist.name) == artist_name.lower(),
        )
        .first()
    )

    if not song:
        session.close()
        print(f"Song '{song_title}' by '{artist_name}' not found.")
        return

    if category == "title":
        old_value = song.title
        song.title = new_value

    if category == "artist":
        old_value = artist_name
        artist_name = new_value

    if category == "album":
        old_value = song.album or "—"
        song.album = new_value

    elif category == "year":
        if not new_value.isdigit():
            session.close()
            print(f"Year must be a number, got '{new_value}'.")
            return
        old_value = str(song.year) if song.year else "—"
        song.year = int(new_value)

    elif category == "language":
        old_value = song.language or "—"
        song.language = new_value

    elif category == "origin":
        old_value = song.artist.origin or "—"
        song.artist.origin = new_value

    session.commit()
    session.close()

    if not old_value:
        old_value = "---"

    print(f"Updated {category} for '{song_title}' by '{artist_name}': '{old_value}' → '{new_value}'.")