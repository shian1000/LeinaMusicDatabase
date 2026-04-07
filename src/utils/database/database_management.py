from utils.database.music_db_manager import get_music_session
from utils.database.datatables import Song, Artist
from sqlalchemy import func
from utils.database.datatables import song_categories, hidden_song_categories, artist_categories
import time
from utils.debug import slog
from menu.song_actions.pick_song import pick_song


def validate_song(song_query):

    if type(song_query) == str:
        return song_query
    elif type(song_query) == list:
        if len(song_query) > 1:
            return pick_song(song_query)
        else:
            slog(song_query)
            if isinstance(song_query[0], str):
                songs_simple = [f"{artist}" for artist in song_query]
            else:
                songs_simple = [f"{artist} - {title}" for artist, title in song_query]
            return(f"{songs_simple[0]}")
    else:
        print("validate_song error - song query is not a str or a list")
    

def edit_artist_entry(artist_query: str, category: str, new_value: str):

    slog(artist_query)

    artist_query = validate_song(artist_query)

    slog(artist_query)

    old_value = None

    artist_name = artist_query
    category = category.strip().lower()
    new_value = new_value.strip()

    valid_categories = artist_categories
    if category not in valid_categories:
        print(f"Invalid category '{category}'. Editable options: {', '.join(sorted(valid_categories))}")
        return
    
    session = get_music_session()

    artist = (
        session.query(Artist)
        .filter(func.lower(Artist.name) == artist_name.lower())
        .first()
    )

    if not artist:
        session.close()
        print(f"Artist '{artist_name}' not found.")
        return

    if category == "artist":
        old_value = artist.name
        artist.name = new_value
    elif category == "origin":
        old_value = artist.origin or "—"
        artist.origin = new_value

    session.commit()
    session.close()

    if not old_value:
        old_value = "---"

    print(f"Updated {category} for '{artist_name}': '{old_value}' → '{new_value}'.")



def edit_song_entry(song_query: str, category: str, new_value: str):

    slog(song_query)

    song_query = validate_song(song_query)

    slog(song_query)

    old_value = None

    if " - " not in song_query:
        print("Invalid format. Use: 'Artist - Song Title'")
        return

    artist_name, song_title = [part.strip() for part in song_query.split(" - ", 1)]
    category = category.strip().lower()
    new_value = new_value.strip()

    valid_categories = song_categories + hidden_song_categories

    slog(valid_categories)

    if category not in valid_categories:
        print(f"Invalid category '{category}'. Editable options: {', '.join(sorted(valid_categories))}")
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