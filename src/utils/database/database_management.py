from utils.database.music_db_manager import get_music_session
from utils.database.datatables import Song, Artist
from sqlalchemy import func
from utils.database.datatables import song_categories, hidden_song_categories, artist_categories
import time
from utils.debug import slog
from menu.song_actions.pick_song import pick_song
from sqlalchemy import text
from utils.database.database_getter import get_artists_from_db_session


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

def edit_db_entry(db_object, category: str, new_value: str, sessions = None):

    category = category.strip().lower()
    new_value = new_value.strip()

    slog(db_object)

    if(type(db_object) == Artist):
        valid_categories = artist_categories
        artist_name = db_object.name
    elif(type(db_object) == Song):
        if category == "artist":
            if not sessions:
                print("When changing song to artist, sessions must be provided")
                return
            db_artist = get_artists_from_db_session("name", db_object.artist.name, sessions=sessions)
            edit_db_entry(db_artist[0], "name", new_value)
            return
        valid_categories = song_categories + hidden_song_categories
        artist_name = db_object.artist.name
        song_title = db_object.title
    else:
        print("db_object is neither Artist nor Song. Aborting")
        return
    slog(valid_categories)
    old_value = None

    if category not in valid_categories:
        print(f"Invalid category '{category}'. Editable options: {', '.join(sorted(valid_categories))}")
        return

    if category == "name":
        old_value = db_object.name
        db_object.name = new_value

    if category == "origin":
        old_value = db_object.origin
        db_object.origin = new_value

    if category == "title":
        old_value = db_object.title
        db_object.title = new_value

    if category == "artist":
        old_value = artist_name
        artist_name = new_value

    if category == "album":
        old_value = db_object.album or "—"
        db_object.album = new_value

    if category == "year":
        if not new_value.isdigit():
            print(f"Year must be a number, got '{new_value}'.")
            return
        old_value = str(db_object.year) if db_object.year else "—"
        db_object.year = int(new_value)

    if category == "language":
        old_value = db_object.language or "—"
        db_object.language = new_value

    if category == "origin":
        old_value = db_object.artist.origin or "—"
        db_object.artist.origin = new_value

    if not old_value:
        old_value = "---"

    if type(db_object) == Artist:
        print(f"Updated {category} for '{artist_name}: {old_value} --> {new_value}")
    if type(db_object) == Song:
        print(f"Updated {category} for '{song_title}' by '{artist_name}': '{old_value}' --> '{new_value}'.")

def delete_db_entry(db_object, session):
    music_session, tag_session = session
    slog(type(db_object))
    music_session.delete(db_object)

def merge_artists_in_db(merge_from, merge_to, sessions):

    """Merge two artists in the database.

    Parameters
    - merge_from: Artist ORM object (the artist to be removed)
    - merge_to: Artist ORM object (the artist to be kept)

    Behavior:
    1. Reassign all songs that reference merge_from.id to merge_to.id
    2. If merge_from has no songs left afterwards, delete the artist row
    """

    slog(merge_from)
    slog(merge_to)

    merge_from_name = getattr(merge_from, "name", str(getattr(merge_from, "id", "?")))
    merge_to_name = getattr(merge_to, "name", str(getattr(merge_to, "id", "?")))

    try:
        from_id = int(merge_from.id)
    except Exception:
        raise ValueError("merge_from must be an Artist-like object with an 'id' attribute")

    try:
        to_id = int(merge_to.id)
    except Exception:
        raise ValueError("merge_to must be an Artist-like object with an 'id' attribute")

    if from_id == to_id:
        print("Source and target artist are the same; nothing to merge.")
        return

    session, tag_session = sessions

    session.execute(
        text("UPDATE songs SET artist_id = :to_id WHERE artist_id = :from_id"),
        {"to_id": to_id, "from_id": from_id}
    )

    remaining = session.execute(
        text("SELECT COUNT(1) FROM songs WHERE artist_id = :from_id"),
        {"from_id": from_id}
    ).scalar()

    if not remaining:
        session.execute(text("DELETE FROM artists WHERE id = :id"), {"id": from_id})
        print(f"Artist {merge_from_name} (id {from_id}) has been merged to {merge_to_name} (id {to_id}).")
    else:
        print(f"Artist {merge_from_name} (id {from_id}) still has {remaining} song(s); not deleting.")
