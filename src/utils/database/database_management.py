from utils.database.music_db_manager import get_music_session
from utils.database.datatables import Song, Artist
from sqlalchemy import func
from utils.database.datatables import song_categories, search_only_categories, artist_categories
import time
from utils.debug import slog
from sqlalchemy import text
from utils.database.database_getter import get_artists_from_db_session, get_global_database_sessions


def edit_db_entry(db_object, category: str, new_value: str):

    #setup
    category = category.strip().lower()
    new_value = new_value.strip()
    slog(db_object)

    #checking if we should work with Artist or Song table
    if(type(db_object) == Artist):
        valid_categories = artist_categories
        artist_name = db_object.name
    elif(type(db_object) == Song):
        if category in artist_categories:
            db_artist = db_artist = get_artists_from_db_session(artist_categories[0], db_object.artist.name)
            edit_db_entry(db_artist[0], category, new_value)
            return
        valid_categories = song_categories + search_only_categories
        artist_name = db_object.artist.name
        song_title = db_object.title
    else:
        print(f"db_object is neither Artist nor Song. It's {type(db_object)}. Aborting")
        return
    slog(valid_categories)
    old_value = None

    #checking if the category is valid for the given db_object
    if category not in valid_categories:
        print(f"Invalid category '{category}'. Editable options: {', '.join(sorted(valid_categories))}")
        return

    #interpretting what category to work with
    if category == artist_categories[0]:
        old_value = db_object.name or "---"
        db_object.name = new_value

    if category == artist_categories[1]:
        old_value = db_object.origin or "---"
        db_object.origin = new_value

    if category == song_categories[0]:
        old_value = db_object.title or "---"
        db_object.title = new_value

    if category == song_categories[2]:
        old_value = db_object.album or "---"
        db_object.album = new_value

    if category == song_categories[3]:
        if not new_value.isdigit():
            print(f"Year must be a number, got '{new_value}'.")
            return
        old_value = str(db_object.year) if db_object.year else "---"
        db_object.year = int(new_value)

    if category == song_categories[4]:
        old_value = db_object.language or "---"
        db_object.language = new_value

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

def merge_artists_in_db(merge_from, merge_to):

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

    session, tag_session = get_global_database_sessions()

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


def divide_artist(db_object):
    """Split an artist so each song (except the first) becomes assigned to a newly created
    artist record with the same name.

    Behavior:
    - Validates that db_object is an Artist
    - Fetches all songs for that artist
    - Shows the list to the user and asks for confirmation
    - For every song except the first one, creates a new Artist(name=old_name) and
      reassigns the song to that new artist
    - Commits the changes to the global music session
    """
    
    if type(db_object) != Artist:
        print(f"divide_artist expects an Artist object, got {type(db_object)}. Aborting.")
        return

    sessions = get_global_database_sessions()
    if not sessions:
        print("Global database sessions are not open. Aborting.")
        return

    music_session, _ = sessions

    try:
        artist_id = int(db_object.id)
    except Exception:
        print("Provided Artist object does not have a valid 'id'. Aborting.")
        return

    # fetch songs belonging to this artist
    songs = (
        music_session
        .query(Song)
        .filter(Song.artist_id == artist_id)
        .order_by(Song.id)
        .all()
    )

    if len(songs) == 1:
        print(f"Artist has only one song. Nothing to do.")
        return

    if not songs:
        print(f"Artist '{db_object.name}' (id {artist_id}) has no songs. Nothing to do.")
        return

    print(f"Found {len(songs)} song(s) for artist '{db_object.name}':")
    for idx, s in enumerate(songs, start=1):
        album = (s.album or "").strip()
        year = s.year or ""
        extra = []
        if album:
            extra.append(f"album: {album}")
        if year:
            extra.append(f"year: {year}")
        extra_str = f" ({'; '.join(extra)})" if extra else ""
        print(f"  {idx}. [{s.id}] {s.title}{extra_str}")

    answer = input("Proceed with dividing this artist so each song gets its own new artist record? (yes/no): ")
    if not answer or answer.strip().lower() not in ("y", "yes"):
        print("Aborted by user.")
        return

    # Keep the first song assigned to the original artist; for every other song create
    # a new artist record with the same name and assign the song to it.
    original_name = db_object.name
    # iterate songs in order; skip the first
    for s in songs[1:]:
        new_artist = Artist(name=original_name)
        music_session.add(new_artist)
        # flush so new_artist.id is available
        music_session.flush()
        # reassign the song to the newly created artist
        s.artist_id = new_artist.id
        print(f"Reassigned song [{s.id}] '{s.title}' to new artist id {new_artist.id}.")

    # commit the changes to the global session
    music_session.commit()
    print(f"Division complete: created {len(songs)-1} new artist(s) and reassigned {len(songs)-1} song(s).")

    