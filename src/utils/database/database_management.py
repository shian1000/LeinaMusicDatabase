from utils.database.music_db_manager import get_music_session
from utils.database.datatables import Song, Artist
from sqlalchemy import func
from utils.database.datatables import song_categories, search_only_categories, artist_categories
import time
from utils.common.debug import slog
from sqlalchemy import text
from utils.database.database_getter import get_artists_from_db_session, get_global_database_sessions
from utils.database.tags_management import remove_tag_from_song
from rich import print


def edit_db_entry(db_object, category: str, new_value: str):

    # remove_tag_from_song(db_object, "spellchecked")
    # remove_tag_from_song(db_object, "album_checked")

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
            db_artist = get_artists_from_db_session(artist_categories[0], artist_id=db_object.artist.id)
            slog(db_artist[0])
            slog(db_artist[0].id)
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

    if isinstance(db_object, Artist):
        slog(db_object)
        slog(db_object.name)
        slog(db_object.id)

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
    
    music_session, _ = get_global_database_sessions()
    artist_id = int(db_object.id)
    songs = (
        music_session
        .query(Song)
        .filter(Song.artist_id == artist_id)
        .order_by(Song.id)
        .all()
    )

    original_name = db_object.name
    for s in songs[1:]:
        new_artist = Artist(name=original_name)
        music_session.add(new_artist)
        music_session.flush()
        s.artist_id = new_artist.id
        print(f"Reassigned song [{s.id}] '{s.title}' to new artist id {new_artist.id}.")

    music_session.commit()
    print(f"Division complete: created {len(songs)-1} new artist(s) and reassigned {len(songs)-1} song(s).")

    

def add_db_entry(db_object):
    """Add a new Artist or Song entry to the music database.

    Accepts either an ORM-like object (instance of `Artist` or `Song`) or a
    dict-like object with the required fields.

    Behavior:
    - If `db_object` is an `Artist` instance: add it to the music session and
      commit. Returns the persisted Artist (with id).
    - If `db_object` is a `Song` instance: ensure the referenced artist exists
      (by Artist instance or artist_id), add the Song, commit and return it.

    Returns the created ORM object on success.
    """

    music_session, _ = get_global_database_sessions() or (get_music_session(), None)

    # If it's already an ORM instance of Artist
    if isinstance(db_object, Artist):
        music_session.add(db_object)
        music_session.commit()
        music_session.refresh(db_object)
        print(f"Added Artist '{db_object.name}' with id {db_object.id}")
        return db_object

    # If it's already an ORM instance of Song
    if isinstance(db_object, Song):
        # Ensure artist exists (either attached or by id)
        if getattr(db_object, "artist", None) is None and getattr(db_object, "artist_id", None) is None:
            raise ValueError("Song must have either 'artist' relationship set or 'artist_id' before adding.")

        # If artist relationship provided but not persisted, persist or find matching artist by name
        if getattr(db_object, "artist", None) is not None and getattr(db_object.artist, "id", None) is None:
            # try to find existing artist by name
            existing = music_session.query(Artist).filter(func.lower(Artist.name) == db_object.artist.name.lower()).first()
            if existing:
                db_object.artist_id = existing.id
            else:
                music_session.add(db_object.artist)
                music_session.flush()
                db_object.artist_id = db_object.artist.id

        music_session.add(db_object)
        music_session.commit()
        music_session.refresh(db_object)
        print(f"Added Song '{db_object.title}' with id {db_object.id}")
        return db_object

    # Allow dict-like creation for convenience
    if isinstance(db_object, dict):
        obj_type = db_object.get("_type")
        if obj_type == "artist":
            name = db_object.get("name")
            if not name:
                raise ValueError("Artist dict must contain 'name'")
            new_artist = Artist(name=name, origin=db_object.get("origin"), synonyms=db_object.get("synonyms"))
            music_session.add(new_artist)
            music_session.commit()
            music_session.refresh(new_artist)
            print(f"Added artist [green]'{new_artist.name}'[/green] with id [green]{new_artist.id}[/green]")
            return new_artist

        if obj_type == "song":
            title = db_object.get("title")
            artist_id = db_object.get("artist_id")
            artist_name = db_object.get("artist_name")
            if not title:
                raise ValueError("Song dict must contain 'title'")
            if not artist_id and not artist_name:
                raise ValueError("Song dict must contain either 'artist_id' or 'artist_name'")

            if not artist_id and artist_name:
                existing = music_session.query(Artist).filter(func.lower(Artist.name) == artist_name.lower()).first()
                if existing:
                    artist_id = existing.id
                else:
                    new_artist = Artist(name=artist_name)
                    music_session.add(new_artist)
                    music_session.flush()
                    artist_id = new_artist.id

            new_song = Song(
                title=title,
                album=db_object.get("album"),
                year=db_object.get("year"),
                language=db_object.get("language"),
                artist_id=artist_id,
            )
            music_session.add(new_song)
            music_session.refresh(new_song)
            print(f"Added song [green]'{new_song.title}'[/green] with id [green]{new_song.id}[/green]")
            return new_song

    raise ValueError(f"Unsupported db_object type: {type(db_object)}")

