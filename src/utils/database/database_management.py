from utils.database.datatables import Album, COMPOSED_FIELDS, Song, Artist
from sqlalchemy import func
from utils.database.datatables import song_categories, search_only_categories, artist_categories
import time
from utils.common.debug import slog
from utils.common.normalizer import composed
from sqlalchemy import text
from sqlalchemy.orm import object_session
from utils.database.database_sessions import (
    get_global_database_sessions,
    open_and_set_global_database_sessions,
    require_global_database_sessions,
)
from utils.database.tags_management import remove_tag_from_song
from utils.database.song_artists import count_artist_songs, merge_artist_synonyms, reassign_additional_artist_links
from utils.database.albums import reassign_artist_albums
from rich import print


# What edit_db_entry() can change: category -> attribute. The rest of
# song_categories / artist_categories ("tag", "artist id", ...) are search
# categories only.
EDITABLE_ARTIST_FIELDS = {
    artist_categories[0]: "name",  # "artist name"
    artist_categories[1]: "origin",  # "artist origin"
}
EDITABLE_SONG_FIELDS = {
    song_categories[0]: "title",  # "title"
    song_categories[2]: "album",  # "album"
    song_categories[3]: "year",  # "year"
    song_categories[4]: "language",  # "language"
}


def decomposed_names(session) -> list[tuple[object, str, str, str]]:
    """(row, attribute, stored value, composed value) for every name music.db
    holds in decomposed form (COMPOSED_FIELDS): stored before the models
    composed every write. Assigning the value again composes it."""
    found = []
    for model in (Artist, Song, Album):
        fields = COMPOSED_FIELDS[model.__tablename__]
        for row in session.query(model).order_by(model.id):
            for field in fields:
                value = getattr(row, field)
                if value and composed(value) != value:
                    found.append((row, field, value, composed(value)))
    return found


def edit_db_entry(db_object, category: str, new_value: str) -> bool:
    """Set one field of a song or an artist (the caller commits). A category
    it can't edit, or a year that isn't a number, is reported and changes
    nothing. True = changed."""
    category = category.strip().lower()
    new_value = new_value.strip()
    slog(db_object)

    #checking if we should work with Artist or Song table
    if(type(db_object) == Artist):
        fields = EDITABLE_ARTIST_FIELDS
        label = f"'{db_object.name}'"
    elif(type(db_object) == Song):
        if category in artist_categories:
            # The Artist row is shared by all its songs: crediting the song to
            # another artist is artist_reassignment.set_primary_artist()
            raise ValueError(f"'{category}' belongs to the artist, not to the song")
        fields = EDITABLE_SONG_FIELDS
        label = f"'{db_object.title}' by '{db_object.artist.name}'"
    else:
        print(f"db_object is neither Artist nor Song. It's {type(db_object)}. Aborting")
        return False

    if category not in fields:
        print(f"Can't edit '{category}' here. Editable: {', '.join(fields)}")
        return False
    attribute = fields[category]
    value = new_value
    if attribute == "year":
        if not new_value.isascii() or not new_value.isdigit():
            print(f"Year must be a number, got '{new_value}'.")
            return False
        value = int(new_value)

    old_value = getattr(db_object, attribute)
    setattr(db_object, attribute, value)
    old_text = "---" if old_value in (None, "") else str(old_value)
    print(f"Updated {category} for {label}: '{old_text}' --> '{new_value}'.")
    return True

class ArtistHasSongsError(ValueError):
    """An artist still credited on songs can't be deleted (songs.artist_id is NOT NULL)."""


def delete_db_entry(db_object):
    """Delete a song or an artist; the caller commits. An artist still on a
    song raises ArtistHasSongsError before anything changes - deleted, it
    would only fail at the next commit and take every other unsaved change
    with it."""
    music_session, tag_session = require_global_database_sessions()
    slog(type(db_object))
    if isinstance(db_object, Artist) and db_object.id is not None:
        songs = count_artist_songs(music_session, db_object.id)
        if songs:
            raise ArtistHasSongsError(f"'{db_object.name}' still has {songs} song(s)")
    music_session.delete(db_object)

def merge_artists_in_db(merge_from, merge_to, session=None):

    """Merge two artists in the database.

    Parameters
    - merge_from: Artist ORM object (the artist to be removed)
    - merge_to: Artist ORM object (the artist to be kept)
    - session: the music session to do it in; default the one merge_to
      belongs to, else the global one. The caller commits.

    Behavior:
    1. Reassign all songs that reference merge_from.id to merge_to.id
    2. If merge_from has no songs left afterwards, delete the artist row

    The moves are raw SQL, so afterwards every object in the session is
    expired (pending changes are flushed first, never lost) and merge_from is
    expunged: a song loaded before would otherwise keep pointing at the
    deleted artist, and a later change to it would write that back.
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

    if session is None:
        session = object_session(merge_to) if isinstance(merge_to, Artist) else None
    if session is None:
        session, _ = require_global_database_sessions()

    session.flush()
    session.execute(
        text("UPDATE songs SET artist_id = :to_id WHERE artist_id = :from_id"),
        {"to_id": to_id, "from_id": from_id}
    )
    reassign_additional_artist_links(session, from_id, to_id)
    reassign_artist_albums(session, from_id, to_id)

    remaining = count_artist_songs(session, from_id)

    if not remaining:
        if isinstance(merge_to, Artist) and isinstance(merge_from, Artist):
            merge_artist_synonyms(merge_to, merge_from)
        session.flush()
        session.execute(text("DELETE FROM artists WHERE id = :id"), {"id": from_id})
        if merge_from in session:
            session.expunge(merge_from)
        print(f"Artist {merge_from_name} (id {from_id}) has been merged to {merge_to_name} (id {to_id}).")
    else:
        print(f"Artist {merge_from_name} (id {from_id}) still has {remaining} song(s); not deleting.")
    session.flush()
    session.expire_all()


def artist_primary_songs(artist: Artist) -> list[Song]:
    """The songs crediting `artist` as their primary artist, oldest first."""
    music_session, _ = require_global_database_sessions()
    return music_session.query(Song).filter(Song.artist_id == artist.id).order_by(Song.id).all()


def divide_artist(db_object):
    """Split an artist so each song (except the first) becomes assigned to a newly created
    artist record with the same name, in the global music session. Asks nothing (the menu
    shows the songs and asks first); the caller commits.
    """
    music_session, _ = require_global_database_sessions()
    songs = artist_primary_songs(db_object)

    original_name = db_object.name
    for s in songs[1:]:
        new_artist = Artist(name=original_name)
        music_session.add(new_artist)
        music_session.flush()
        s.artist_id = new_artist.id
        print(f"Reassigned song [{s.id}] '{s.title}' to new artist id {new_artist.id}.")

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

    # Outside the menu (an import run on its own) the global sessions open
    # here, so the entry lands in the session its caller commits
    music_session, _ = get_global_database_sessions() or open_and_set_global_database_sessions()

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

