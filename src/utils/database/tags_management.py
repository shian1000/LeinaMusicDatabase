from utils.database.database_sessions import get_global_database_sessions
from utils.database.tag_db_manager import Tag, SongTag
from sqlalchemy.exc import SQLAlchemyError

from utils.common.debug import flog, slog


def _tag_session():
    """The global tag.db session, None while the menus haven't opened it."""
    sessions = get_global_database_sessions()
    if sessions is None:
        slog("Global database sessions not initialized")
        return None
    return sessions[1]


def _clean_tag(tag) -> str | None:
    return (tag.strip() or None) if isinstance(tag, str) else None


def _valid_song(song) -> bool:
    if getattr(song, "id", None) is None:
        slog("Invalid song object: missing or None id")
        return False
    return True


def _failed(tag_session, what: str, exc: SQLAlchemyError) -> bool:
    """A failed query or flush leaves the global tag.db session needing a
    rollback - without it the next commit fails somewhere else entirely.
    Rolled back (its other unsaved tag changes too), shown and logged."""
    tag_session.rollback()
    print(f"{what} failed, unsaved tag changes were undone: {exc}")
    flog(f"tags_management: {what} failed: {exc}")
    return False


def add_tag_to_song(song, tag: str) -> bool:
    """`tag` on one song (Song from music.db). True when it's there now,
    False for an invalid song or tag, or a failed write. The caller commits."""
    return _valid_song(song) and add_tag_to_songs([song.id], tag)


def add_tag_to_songs(song_ids, tag: str) -> bool:
    """`tag` on every one of `song_ids`, the tag created when new, in two
    queries however many songs. The caller commits."""
    tag_session, tag = _tag_session(), _clean_tag(tag)
    if tag_session is None or tag is None:
        return False
    try:
        tag_obj = tag_session.query(Tag).filter_by(name=tag).first()
        if not tag_obj:
            tag_obj = Tag(name=tag)
            tag_session.add(tag_obj)
            tag_session.flush()
        tagged = {song_id for (song_id,) in tag_session.query(SongTag.song_id).filter_by(tag_id=tag_obj.id)}
        tag_session.add_all(SongTag(song_id=song_id, tag_id=tag_obj.id) for song_id in set(song_ids) - tagged)
        return True
    except SQLAlchemyError as e:
        return _failed(tag_session, f"Adding tag '{tag}'", e)


def remove_tag_from_song(song, tag: str) -> bool:
    """`tag` off one song, its other tags kept. True also when it wasn't
    there. The caller commits."""
    tag_session, tag = _tag_session(), _clean_tag(tag)
    if tag_session is None or tag is None or not _valid_song(song):
        return False
    try:
        tag_ids = tag_session.query(Tag.id).filter_by(name=tag)
        tag_session.query(SongTag).filter(SongTag.song_id == song.id, SongTag.tag_id.in_(tag_ids)) \
            .delete(synchronize_session=False)
        return True
    except SQLAlchemyError as e:
        return _failed(tag_session, f"Removing tag '{tag}' from song {song.id}", e)


def song_ids_with_tag(tag: str) -> set[int]:
    """Ids of every song carrying `tag`, in one query - also for one song's
    check, instead of a query per song in a loop over the catalog."""
    tag_session, tag = _tag_session(), _clean_tag(tag)
    if tag_session is None or tag is None:
        return set()
    rows = tag_session.query(SongTag.song_id).join(Tag, SongTag.tag_id == Tag.id).filter(Tag.name == tag).all()
    return {song_id for (song_id,) in rows}


def clear_tag(tag: str) -> bool:
    """`tag` off every song, the tag itself staying. The caller commits."""
    tag_session = _tag_session()
    if tag_session is None:
        return False
    try:
        tag_obj = tag_session.query(Tag).filter_by(name=tag).first()
        if tag_obj:
            tag_session.query(SongTag).filter_by(tag_id=tag_obj.id).delete()
        return True
    except SQLAlchemyError as e:
        return _failed(tag_session, f"Clearing tag '{tag}'", e)


def tag_names_by_song(song_ids) -> dict[int, list[str]]:
    """The tag names of each of `song_ids` that has any, sorted, in one query
    on the global tag.db session (left open)."""
    tag_session = _tag_session()
    ids = set(song_ids)
    if tag_session is None or not ids:
        return {}
    names = {}
    for song_id, name in (tag_session.query(SongTag.song_id, Tag.name)
                          .join(Tag, SongTag.tag_id == Tag.id).all()):
        if song_id in ids:
            names.setdefault(song_id, []).append(name)
    return {song_id: sorted(tags) for song_id, tags in names.items()}
