from utils.database.database_sessions import get_global_database_sessions
from utils.database.tag_db_manager import Tag, SongTag
from utils.common.debug import slog


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
    # Validate inputs
    if not hasattr(song, 'id') or song.id is None:
        slog("Invalid song object: missing or None id")
        return False
    
    if not tag or not isinstance(tag, str):
        slog("Invalid tag: must be a non-empty string")
        return False
    
    tag = tag.strip()
    if not tag:
        slog("Tag cannot be empty after stripping whitespace")
        return False
    
    # Get global sessions
    sessions = get_global_database_sessions()
    if sessions is None:
        slog("Global database sessions not initialized")
        return False
    
    music_session, tag_session = sessions
    
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
        
    except Exception as e:
        slog(f"Error adding tag to song: {e}")
        return False


def has_tag_on_song(song, tag: str) -> bool:
    """
    Check if a song has a specific tag.
    
    Args:
        song: Song object from music.db with an 'id' attribute
        tag: Tag string to check (e.g., "favorite", "classic")
    
    Returns:
        bool: True if song has the tag, False otherwise
    """
    # Validate inputs
    if not hasattr(song, 'id') or song.id is None:
        slog("Invalid song object: missing or None id")
        return False
    
    if not tag or not isinstance(tag, str):
        slog("Invalid tag: must be a non-empty string")
        return False
    
    tag = tag.strip()
    if not tag:
        slog("Tag cannot be empty after stripping whitespace")
        return False
    
    # Get global sessions
    sessions = get_global_database_sessions()
    if sessions is None:
        slog("Global database sessions not initialized")
        return False
    
    music_session, tag_session = sessions
    
    try:
        # Query for the tag
        tag_obj = tag_session.query(Tag).filter_by(name=tag).first()
        if not tag_obj:
            # Tag doesn't exist, so song can't have it
            return False
        
        # Check if song-tag relationship exists
        song_tag = (
            tag_session.query(SongTag)
            .filter_by(song_id=song.id, tag_id=tag_obj.id)
            .first()
        )
        
        return song_tag is not None
        
    except Exception as e:
        slog(f"Error checking tag on song: {e}")
        return False


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
    # Validate inputs
    if not hasattr(song, 'id') or song.id is None:
        slog("Invalid song object: missing or None id")
        return False
    
    if not tag or not isinstance(tag, str):
        slog("Invalid tag: must be a non-empty string")
        return False
    
    tag = tag.strip()
    if not tag:
        slog("Tag cannot be empty after stripping whitespace")
        return False
    
    # Get global sessions
    sessions = get_global_database_sessions()
    if sessions is None:
        slog("Global database sessions not initialized")
        return False
    
    music_session, tag_session = sessions
    
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
        
    except Exception as e:
        slog(f"Error removing tag from song: {e}")
        return False