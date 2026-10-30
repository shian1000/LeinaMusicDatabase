"""
files.db: the music library index - every audio file under
Settings.local_library_dir and the music.db song it is. Settings -> Server ->
"Index music library", and once a day at startup after the daily backup,
before the databases are copied for the phone (which downloads files.db with
music.db and tag.db, so it knows which songs have a file on the server).

One row per file (schema: migrations/files/, created by initialize_db() -
there's no bootstrap):
- path is relative to the library folder with '/' separators, so the index
  survives the library moving again and never shows the phone a server path;
- size + mtime decide whether a file is read again: a daily scan of an
  unchanged library only lists the folder;
- song_id is music.db's songs.id, matched the way the cover import and
  playlists from folders match (read_file_metadata() +
  import_engine.LocalSongMatcher - local only, nothing created). Unmatched
  files keep artist/title from their tags and are tried again whenever what
  matching reads from music.db (artist names, synonyms, song titles) has
  changed since the last scan, so a song added to the database later picks
  up its file. Not on every scan: even with the artists loaded once per
  scan (LocalSongMatcher(load_artists_once=True)), ~230 unmatched files
  take ~8 s - too long for a startup that found nothing new. A new file, or one whose contents
  changed, is always matched. Rows with match_source 'manual' are
  never rematched - a file pinned to its song by hand, link_file_to_song().
- ids are AUTOINCREMENT, never reused: a file that's moved or renamed is a
  new row.

song_id is a plain integer pointing into another file, so merges and
deletions reach it through song_references.apply_song_id_changes() like
tag.db; a scan replays the log first so it never matches against stale ids.
"""

import contextlib
import hashlib
import io
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Column, Integer, String, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from config.constants import FILE_MATCH_AUTO, FILE_MATCH_MANUAL, LIBRARY_AUDIO_EXTENSIONS
from settings import Settings
from utils.common.debug import flog, mlog, slog
from utils.database.backup import DB_FILES
from utils.database.migrations import FILES_TRACK, initialize_db

FILES_DB_PATH = DB_FILES[FILES_TRACK]
# Commit this often, so an interrupted first scan keeps what it read
_COMMIT_EVERY = 500
_FINGERPRINT_KEY = "catalog_fingerprint"

FilesBase = declarative_base()


class LibraryFile(FilesBase):
    """Mirrors migrations/files/ - keep in step with each new file there."""
    __tablename__ = "files"

    id = Column(Integer, primary_key=True)
    path = Column(String, nullable=False, unique=True)
    size = Column(Integer, nullable=False)
    mtime = Column(Integer, nullable=False)
    song_id = Column(Integer, index=True)
    match_source = Column(String, nullable=False, default=FILE_MATCH_AUTO)
    artist = Column(String)
    title = Column(String)
    duration_s = Column(Integer)
    indexed_at = Column(String, nullable=False)


class LibraryUnavailableError(Exception):
    """The library folder isn't set, isn't there (e.g. its drive isn't plugged in) or isn't local."""


@dataclass
class IndexReport:
    files: int = 0       # audio files in the library now
    added: int = 0
    changed: int = 0     # re-read because size or mtime changed
    removed: int = 0
    matched_now: int = 0  # files that got a song in this scan
    unmatched: list = field(default_factory=list)  # (path, artist, title) of files still without a song


@contextmanager
def files_db_session(db_path: Path = None):
    """A session on files.db; its engine is disposed afterwards. The caller commits."""
    engine = create_engine(f"sqlite:///{db_path or FILES_DB_PATH}")
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def ensure_files_db(db_path: Path = None) -> Path:
    db_path = Path(db_path or FILES_DB_PATH)
    if not db_path.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)
        initialize_db(db_path, FILES_TRACK, db_path.name)
        mlog(f"Created {db_path}")
    return db_path


def local_library_path() -> Path | None:
    """The configured library folder as a local path, whether or not it's
    there right now; None for a remote (smb://) library or none set."""
    root_str = Settings.local_library_dir_str
    if not root_str or "://" in root_str and not root_str.startswith("file://"):
        return None
    return Path(root_str.removeprefix("file://"))


def library_root() -> Path:
    """The library folder, or LibraryUnavailableError. Only a local folder
    can be indexed (an smb:// library would have to be read over the network)."""
    if not Settings.local_library_dir_str:
        raise LibraryUnavailableError("MUSIC_LIBRARY_DIR isn't set - Settings -> Server -> Folders and network (or .env)")
    root = local_library_path()
    if root is None:
        raise LibraryUnavailableError(f"{Settings.local_library_dir_str} is not a local folder")
    if not root.is_dir():
        raise LibraryUnavailableError(f"{root} not found - is its drive plugged in?")
    return root


def library_audio_files(root: Path) -> dict:
    """{relative posix path: (size, mtime)} of every audio file under root."""
    found = {}
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if os.path.splitext(name)[1].lower() not in LIBRARY_AUDIO_EXTENSIONS:
                continue
            full = Path(dirpath) / name
            try:
                st = full.stat()
            except OSError as exc:
                slog(f"[FILES INDEX] can't stat {full}: {exc}")
                continue
            found[full.relative_to(root).as_posix()] = (st.st_size, int(st.st_mtime))
    return found


def song_file_paths(song_ids, db_path: Path = None) -> dict:
    """{song id: [full path, ...]} of the library files indexed for these
    songs (several when a song has more than one file). Empty when files.db
    doesn't exist yet - nothing has been indexed."""
    db_path = Path(db_path or FILES_DB_PATH)
    song_ids = [i for i in song_ids if i is not None]
    if not song_ids or not db_path.exists() or not Settings.local_library_dir_str:
        return {}
    root = Settings.local_library_dir_str.removeprefix("file://").rstrip("/")
    found = {}
    with files_db_session(db_path) as session:
        rows = (session.query(LibraryFile.song_id, LibraryFile.path)
                .filter(LibraryFile.song_id.in_(song_ids))
                .order_by(LibraryFile.path))
        for song_id, rel in rows:
            found.setdefault(song_id, []).append(f"{root}/{rel}")
    return found


def long_song_files(min_duration_s: int, db_path: Path = None) -> dict:
    """{song id: longest duration_s} of the songs with an indexed file at
    least `min_duration_s` long - full album candidates. Empty when files.db
    doesn't exist yet."""
    db_path = Path(db_path or FILES_DB_PATH)
    if not db_path.exists():
        return {}
    found = {}
    with files_db_session(db_path) as session:
        rows = (session.query(LibraryFile.song_id, LibraryFile.duration_s)
                .filter(LibraryFile.song_id.isnot(None), LibraryFile.duration_s >= min_duration_s))
        for song_id, duration in rows:
            found[song_id] = max(duration, found.get(song_id, 0))
    return found


def link_file_to_song(rel_path: str, song_id: int, db_path: Path = None) -> None:
    """Pin a library file to a song by hand: match_source 'manual', which no
    scan rematches - for a file whose tags won't find its song again (a
    title changed to "Mogę zniknąć (SzUsty Blend)" while the tags still say
    "Mogę zniknąć"). `rel_path` is relative to the library folder, as stored.
    Raises LookupError if the file isn't indexed."""
    with files_db_session(ensure_files_db(db_path)) as session:
        row = session.query(LibraryFile).filter(LibraryFile.path == rel_path).one_or_none()
        if row is None:
            raise LookupError(f"{rel_path} is not in the library index - scan the library first")
        row.song_id, row.match_source = song_id, FILE_MATCH_MANUAL
        session.commit()


def _read_tags(file: Path) -> tuple:
    """(artist, title, duration_s) - tags, else the filename, like the tag import."""
    from utils.discoveries.import_data_from_mp3_tags import read_file_metadata
    from utils.discoveries.mp3_utils import audio_duration
    # read_file_metadata() prints a warning per file without tags - fine for
    # one import, thousands of lines for a whole library
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            metadata = read_file_metadata(file)
        except Exception as exc:
            slog(f"[FILES INDEX] can't read tags of {file}: {exc}")
            metadata = {}
    return metadata.get("artist_name") or None, metadata.get("title") or file.stem, metadata.get("duration_s") or audio_duration(file)


def catalog_fingerprint(music_session) -> str:
    """Hash of everything LocalSongMatcher reads from music.db: artists'
    names, Latin forms and synonyms, songs' titles (and their Latin forms)
    and artists. Filling in albums or years doesn't change it."""
    digest = hashlib.sha256()
    for query in ("SELECT id, name, name_latin, synonyms FROM artists ORDER BY id",
                  "SELECT id, artist_id, title, title_latin FROM songs ORDER BY id"):
        for row in music_session.execute(text(query)):
            digest.update(repr(tuple(row)).encode())
    return digest.hexdigest()


def _state(session, key: str) -> str | None:
    return session.execute(text("SELECT value FROM index_state WHERE key = :k"), {"k": key}).scalar()


def _set_state(session, key: str, value: str) -> None:
    session.execute(text("INSERT INTO index_state (key, value) VALUES (:k, :v) "
                         "ON CONFLICT (key) DO UPDATE SET value = :v"), {"k": key, "v": value})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def scan_library(music_session, root: Path = None, db_path: Path = None, progress=None) -> IndexReport:
    """Bring files.db up to date with the library folder and match files
    without a song. `progress(done, total)` is called every few hundred
    files read. Raises LibraryUnavailableError when there's no library."""
    from utils.database.song_references import _replay, load_changes
    from utils.discoveries.import_engine import LocalSongMatcher

    root = Path(root) if root else library_root()
    db_path = ensure_files_db(db_path)
    report = IndexReport()
    on_disk = library_audio_files(root)
    report.files = len(on_disk)

    with files_db_session(db_path) as session:
        _replay(session, load_changes(), has_tags=False, has_files=True)
        session.commit()

        fingerprint = catalog_fingerprint(music_session)
        catalog_changed = _state(session, _FINGERPRINT_KEY) != fingerprint
        indexed = {row.path: row for row in session.query(LibraryFile)}
        to_read = [p for p, stat in on_disk.items()
                   if p not in indexed or (indexed[p].size, indexed[p].mtime) != stat]
        for done, rel in enumerate(sorted(to_read), 1):
            size, mtime = on_disk[rel]
            artist, title, duration = _read_tags(root / rel)
            row = indexed.get(rel)
            if row is None:
                row = LibraryFile(path=rel, match_source=FILE_MATCH_AUTO)
                session.add(row)
                report.added += 1
            else:
                report.changed += 1
                if row.match_source == FILE_MATCH_AUTO:
                    row.song_id = None
            row.size, row.mtime = size, mtime
            row.artist, row.title, row.duration_s = artist, title, duration
            row.indexed_at = _now()
            if done % _COMMIT_EVERY == 0:
                session.commit()
                if progress:
                    progress(done, len(to_read))

        for rel, row in indexed.items():
            if rel not in on_disk:
                session.delete(row)
                report.removed += 1
        session.commit()

        matcher = LocalSongMatcher(music_session, load_artists_once=True)
        read_now = set(to_read)
        for row in (session.query(LibraryFile)
                    .filter(LibraryFile.song_id.is_(None), LibraryFile.match_source == FILE_MATCH_AUTO)
                    .order_by(LibraryFile.path)):
            song = None
            if catalog_changed or row.path in read_now:
                song = matcher.match({"artist_name": row.artist, "title": row.title, "duration_s": row.duration_s})
            if song is not None:
                row.song_id = song.id
                report.matched_now += 1
            else:
                report.unmatched.append((row.path, row.artist, row.title))
        _set_state(session, _FINGERPRINT_KEY, fingerprint)
        session.commit()

    for path, artist, title in report.unmatched:
        flog(f"Library index: no song in the database for {path} ({artist} - {title})")
    mlog(f"Library index: {report.files} files, +{report.added} ~{report.changed} -{report.removed}, "
         f"{report.matched_now} matched now, {len(report.unmatched)} without a song")
    return report


def scan_library_quietly() -> IndexReport | None:
    """scan_library() for the startup path, on a music session of its own: a
    missing drive or any failure is logged, never raised, so it can't block
    opening the app."""
    from utils.database.music_db_manager import get_music_session
    music_session = get_music_session()
    try:
        return scan_library(music_session)
    except LibraryUnavailableError as exc:
        slog(f"Library index skipped: {exc}", priority=3)
    except Exception as exc:
        slog(f"Library index failed (non-fatal): {exc}", priority=3)
    finally:
        music_session.rollback()  # matching only reads
        music_session.close()
    return None
