"""
lyrics.db: songs' lyrics, next to music.db. Filled by Fetch database data ->
Fill missing data -> "Lyrics" (utils/ui/lyrics_review.py) from the discovery
modules that define get_lyrics() (discoveries_manager.discover_lyrics()).

A file of its own, not a music.db table: lyrics are ~25 MB for the catalog
against music.db's ~2 MB, and Leina Radio downloads music.db whole. The
sharing server answers one song's lyrics from its copy instead
(GET /lyrics/<song id>); Leina Radio falls back to its own LRCLIB lookup when
the server has none. Never part of the public copy on GitHub - the lyrics'
licence doesn't allow republishing them (public_export refuses its tables).

Schema: migrations/lyrics/, created by initialize_db() (no bootstrap). One
row per song in `lyrics`; `lyrics_module_misses` remembers, per song and
module, what a module didn't find ('not_found', asked again after
LYRICS_RETRY_DAYS) and the module texts the owner turned down ('rejected',
never asked again) - so a song is only ever asked of the modules that haven't
answered for it, and a new module asks every song once. A miss holds the name
the song was asked by (lyrics_key()): a song renamed since is asked again. A module that failed
(no network, LRCLIB's 429) records nothing. Songs without words
(songs.language LANGUAGE_INSTRUMENTAL, an instrumental version) are never
looked up. song_id is a plain integer
pointing into music.db, so merges and deletions reach it through
song_references.apply_song_id_changes(), like files.db.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base

from config.constants import (
    LANGUAGE_INSTRUMENTAL,
    LYRICS_MISS_NOT_FOUND,
    LYRICS_MISS_REJECTED,
    LYRICS_RETRY_DAYS,
    LYRICS_SOURCE_ORIGINAL,
    SONG_KIND_FULL_ALBUM,
    VERSION_INSTRUMENTAL,
)
from utils.common.debug import mlog
from utils.common.normalizer import normalize
from utils.database.backup import DB_FILES
from utils.database.datatables import Artist, Song
from utils.database.files_index import long_song_files
from utils.database.video_durations import stored_video_durations
from utils.database.migrations import LYRICS_TRACK, initialize_db
from utils.database.user_datatables import sqlite_session

LYRICS_DB_PATH = DB_FILES[LYRICS_TRACK]
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

LyricsBase = declarative_base()


class SongLyrics(LyricsBase):
    """Mirrors migrations/lyrics/ - keep in step with each new file there."""
    __tablename__ = "lyrics"

    song_id = Column(Integer, primary_key=True, autoincrement=False)
    plain = Column(String)
    synced = Column(String)
    instrumental = Column(Integer, nullable=False, default=0)
    source = Column(String, nullable=False)
    source_ref = Column(String)
    fetched_at = Column(String, nullable=False)


class ModuleMiss(LyricsBase):
    __tablename__ = "lyrics_module_misses"

    song_id = Column(Integer, primary_key=True, autoincrement=False)
    module_id = Column(String, primary_key=True)
    reason = Column(String, nullable=False)
    checked_at = Column(String, nullable=False)
    asked_as = Column(String)  # lyrics_key() of the song when asked; NULL = any


@dataclass(frozen=True)
class Lyrics:
    """A song's lyrics as stored or found: `synced` is LRC text."""
    plain: str | None = None
    synced: str | None = None
    instrumental: bool = False
    source: str = ""
    source_ref: str | None = None

    @property
    def first_line(self) -> str:
        return next((line.strip() for line in (self.plain or "").splitlines() if line.strip()), "")


def _now() -> str:
    return datetime.now(timezone.utc).strftime(_TIME_FORMAT)


def ensure_lyrics_db(db_path: Path = None) -> Path:
    db_path = Path(db_path or LYRICS_DB_PATH)
    if not db_path.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)
        initialize_db(db_path, LYRICS_TRACK, db_path.name)
        mlog(f"Created {db_path}")
    return db_path


def lyrics_db_session(db_path: Path = None):
    """A session on lyrics.db (created when missing); its engine is disposed
    afterwards. The caller commits."""
    return sqlite_session(ensure_lyrics_db(db_path))


def stored_lyrics(session, song_id: int) -> Lyrics | None:
    row = session.get(SongLyrics, song_id)
    if row is None:
        return None
    return Lyrics(row.plain, row.synced, bool(row.instrumental), row.source, row.source_ref)


def _unix_lines(text: str | None) -> str | None:
    # mp3 tags and some LRCLIB entries come with Windows line ends
    return text.replace("\r\n", "\n").replace("\r", "\n") if text else text


def save_lyrics(session, song_id: int, lyrics: Lyrics) -> None:
    """Store (or replace) a song's lyrics, lines ended by "\\n". Caller commits."""
    session.merge(SongLyrics(song_id=song_id, plain=_unix_lines(lyrics.plain), synced=_unix_lines(lyrics.synced),
                             instrumental=int(lyrics.instrumental), source=lyrics.source,
                             source_ref=lyrics.source_ref, fetched_at=_now()))


def lyrics_key(song) -> str:
    """The name a song is looked up by: its artist and title, normalized."""
    return f"{normalize(song.artist.name)}|{normalize(song.title)}"


def record_miss(session, song, module_id: str, reason: str = LYRICS_MISS_NOT_FOUND) -> None:
    """Remember that `module_id` found nothing for the song as it's named now,
    or (LYRICS_MISS_REJECTED) that the owner turned its text down. Caller commits."""
    session.merge(ModuleMiss(song_id=song.id, module_id=module_id, reason=reason, checked_at=_now(),
                             asked_as=lyrics_key(song)))


def reject_lyrics(session, song) -> None:
    """The owner says the stored text is wrong: it goes, and its module is
    never asked for this song again (the other modules still are). Caller commits."""
    stored = stored_lyrics(session, song.id)
    if stored is None:
        return
    session.query(SongLyrics).filter(SongLyrics.song_id == song.id).delete()
    record_miss(session, song, stored.source, LYRICS_MISS_REJECTED)


def forget_lookups(session, song_id: int) -> None:
    """Song actions -> "Lyrics" -> look up again: the stored text and every
    module's answer go, so each module is asked once more. Caller commits."""
    session.query(SongLyrics).filter(SongLyrics.song_id == song_id).delete()
    session.query(ModuleMiss).filter(ModuleMiss.song_id == song_id).delete()


def answered_modules(session, now: datetime | None = None, keys: dict | None = None) -> dict:
    """{song id: module ids not to ask for it} - those that rejected text came
    from, and those that found nothing less than LYRICS_RETRY_DAYS ago; with
    `keys` ({song id: lyrics_key()}), only the answers about the name the
    song has now."""
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=LYRICS_RETRY_DAYS)).strftime(_TIME_FORMAT)
    answered = {}
    for miss in session.query(ModuleMiss):
        if miss.asked_as is not None and keys is not None and keys.get(miss.song_id) != miss.asked_as:
            continue
        if miss.reason != LYRICS_MISS_NOT_FOUND or miss.checked_at > cutoff:
            answered.setdefault(miss.song_id, set()).add(miss.module_id)
    return answered


def is_instrumental(song) -> bool:
    return song.language == LANGUAGE_INSTRUMENTAL or song.version_kind == VERSION_INSTRUMENTAL


def mark_instrumental(songs) -> None:
    """Songs without words: songs.language says so, and no lookup asks for them
    again. Caller commits (music.db)."""
    for song in songs:
        song.language = LANGUAGE_INSTRUMENTAL


def songs_missing_lyrics(music_session, lyrics_session, module_ids, now: datetime | None = None) -> list:
    """[(song, the module ids still to ask for it)] by artist then title: songs
    without lyrics that some of `module_ids` hasn't answered for (under the
    name the song has now); full albums
    in one recording (songs.kind) and songs without words never."""
    with_lyrics = {song_id for (song_id,) in lyrics_session.query(SongLyrics.song_id)}
    songs = (music_session.query(Song).join(Song.artist)
             .filter((Song.kind.is_(None)) | (Song.kind != SONG_KIND_FULL_ALBUM))
             .order_by(Artist.name, Song.title).all())
    answered = answered_modules(lyrics_session, now, {song.id: lyrics_key(song) for song in songs})
    missing = []
    for song in songs:
        if song.id in with_lyrics or is_instrumental(song):
            continue
        left = [m for m in module_ids if m not in answered.get(song.id, ())]
        if left:
            missing.append((song, left))
    return missing


def song_durations(music_session, songs) -> dict:
    """{song id: seconds} - the length of the song's YouTube video, else of
    its longest library file; songs with neither are left out."""
    by_video = stored_video_durations(music_session, [song.youtube_video_id for song in songs])
    in_files = long_song_files(1)  # every indexed file with a length
    durations = {}
    for song in songs:
        seconds = by_video.get(song.youtube_video_id) or in_files.get(song.id)
        if seconds:
            durations[song.id] = seconds
    return durations


def original_lyrics(session, song) -> Lyrics | None:
    """A version's lyrics when nothing was found for it: its original's plain
    text (a live take or a remix sings the same words, at other times - so
    never the synced lines)."""
    original_id = getattr(song, "version_of", None)
    stored = stored_lyrics(session, original_id) if original_id else None
    if stored is None or stored.instrumental or not stored.plain:
        return None
    return Lyrics(plain=stored.plain, source=LYRICS_SOURCE_ORIGINAL, source_ref=str(original_id))
