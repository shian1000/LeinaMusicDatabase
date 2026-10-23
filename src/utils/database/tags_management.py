from utils.database.database_sessions import get_global_database_sessions
from utils.database.tag_db_manager import Tag, SongTag
from sqlalchemy.exc import SQLAlchemyError

from utils.common.debug import flog, slog


def _validate_song_and_tag(song, tag: str):
    """
    Validate a (song, tag) pair shared by all tag CRUD helpers below.

    Returns (tag_session, cleaned_tag) on success, or None if song/tag are
    invalid or the global database sessions aren't initialized yet.
    """
    if not hasattr(song, 'id') or song.id is None:
        slog("Invalid song object: missing or None id")
        return None

    if not tag or not isinstance(tag, str):
        slog("Invalid tag: must be a non-empty string")
        return None

    tag = tag.strip()
    if not tag:
        slog("Tag cannot be empty after stripping whitespace")
        return None

    sessions = get_global_database_sessions()
    if sessions is None:
        slog("Global database sessions not initialized")
        return None

    _, tag_session = sessions
    return tag_session, tag


def _failed(tag_session, what: str, exc: SQLAlchemyError) -> bool:
    """A failed query or flush leaves the global tag.db session needing a
    rollback - without it the next commit fails somewhere else entirely.
    Rolled back (its other unsaved tag changes too), shown and logged."""
    tag_session.rollback()
    print(f"{what} failed, unsaved tag changes were undone: {exc}")
    flog(f"tags_management: {what} failed: {exc}")
    return False


def add_tag_to_song(song, tag: str) -> bool:
    """
    Add a tag to a song, respecting all existing tags.
    
    Args:
        song: Song object from music.db with an 'id' attribute
        tag: Tag string to add (e.g., "favorite", "classic")
    
    Returns:
        bool: True if tag was added/already exists, False if operation failed
        
    Raises:
        ValueError: If song_id is invalid or tag is empty
    """
    slog("add_tag_to_song functiion activated")
    validated = _validate_song_and_tag(song, tag)
    if validated is None:
        return False
    tag_session, tag = validated

    try:
        # Get or create the tag
        tag_obj = tag_session.query(Tag).filter_by(name=tag).first()
        if not tag_obj:
            tag_obj = Tag(name=tag)
            tag_session.add(tag_obj)
            tag_session.flush()  # Flush to get the id without committing
            slog(f"Created new tag: '{tag}'")
        else:
            slog(f"Tag already exists: '{tag}'")
        
        # Check if song-tag relationship already exists
        existing_link = (
            tag_session.query(SongTag)
            .filter_by(song_id=song.id, tag_id=tag_obj.id)
            .first()
        )
        
        if existing_link:
            slog(f"Song {song.id} already has tag '{tag}'")
            return True
        
        # Add the new tag relationship
        song_tag = SongTag(song_id=song.id, tag_id=tag_obj.id)
        tag_session.add(song_tag)
        slog(f"Added tag '{tag}' to song {song.id}")
        
        return True
        
    except SQLAlchemyError as e:
        return _failed(tag_session, f"Adding tag '{tag}' to song {song.id}", e)


def song_ids_with_tag(tag: str) -> set[int]:
    """Ids of every song carrying `tag`, in one query. Use it instead of
    has_tag_on_song() in a loop over the whole catalog (two queries per song,
    ~13 s for 5.7k songs)."""
    sessions = get_global_database_sessions()
    if sessions is None or not tag or not tag.strip():
        return set()
    _, tag_session = sessions
    rows = (
        tag_session.query(SongTag.song_id)
        .join(Tag, SongTag.tag_id == Tag.id)
        .filter(Tag.name == tag.strip())
        .all()
    )
    return {song_id for (song_id,) in rows}


def tag_names_by_song(song_ids) -> dict[int, list[str]]:
    """The tag names of each of `song_ids` that has any, sorted, in one query
    on the global tag.db session (left open)."""
    sessions = get_global_database_sessions()
    ids = set(song_ids)
    if sessions is None or not ids:
        return {}
    _, tag_session = sessions
    names = {}
    for song_id, name in (tag_session.query(SongTag.song_id, Tag.name)
                          .join(Tag, SongTag.tag_id == Tag.id).all()):
        if song_id in ids:
            names.setdefault(song_id, []).append(name)
    return {song_id: sorted(tags) for song_id, tags in names.items()}


def has_tag_on_song(song, tag: str) -> bool:
    slog("has_tag_on_song activatd")
    """
    Check if a song has a specific tag.
    
    Args:
        song: Song object from music.db with an 'id' attribute
        tag: Tag string to check (e.g., "favorite", "classic")
    
    Returns:
        bool: True if song has the tag, False otherwise
    """
    validated = _validate_song_and_tag(song, tag)
    if validated is None:
        return False
    tag_session, tag = validated

    try:
        # Query for the tag
        tag_obj = tag_session.query(Tag).filter_by(name=tag).first()
        if not tag_obj:
            # Tag doesn't exist, so song can't have it
            slog("tag doesn't exist")
            return False
        
        # Check if song-tag relationship exists
        song_tag = (
            tag_session.query(SongTag)
            .filter_by(song_id=song.id, tag_id=tag_obj.id)
            .first()
        )
        
        slog(song_tag)
        slog(song_tag is not None)
        return song_tag is not None
        
    except SQLAlchemyError as e:
        return _failed(tag_session, f"Checking tag '{tag}' on song {song.id}", e)


def remove_tag_from_song(song, tag: str) -> bool:
    """
    Remove a specific tag from a song, keeping all other tags intact.
    
    Args:
        song: Song object from music.db with an 'id' attribute
        tag: Tag string to remove (e.g., "favorite", "classic")
    
    Returns:
        bool: True if tag was removed or didn't exist, False if operation failed
        
    Raises:
        ValueError: If song_id is invalid or tag is empty
    """
    validated = _validate_song_and_tag(song, tag)
    if validated is None:
        return False
    tag_session, tag = validated

    try:
        # Query for the tag
        tag_obj = tag_session.query(Tag).filter_by(name=tag).first()
        if not tag_obj:
            # Tag doesn't exist, so nothing to remove
            slog(f"Tag '{tag}' does not exist")
            return True
        
        # Find and delete the song-tag relationship
        song_tag = (
            tag_session.query(SongTag)
            .filter_by(song_id=song.id, tag_id=tag_obj.id)
            .first()
        )
        
        if not song_tag:
            # Song doesn't have this tag, nothing to remove
            slog(f"Song {song.id} does not have tag '{tag}'")
            return True
        
        # Delete the relationship (keeps other tags intact)
        tag_session.delete(song_tag)
        slog(f"Removed tag '{tag}' from song {song.id}")
        
        return True
        
    except SQLAlchemyError as e:
        return _failed(tag_session, f"Removing tag '{tag}' from song {song.id}", e)