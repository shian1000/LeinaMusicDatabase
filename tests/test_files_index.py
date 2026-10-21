"""The music library index (utils/database/files_index.py): scanning a
library folder into files.db - relative paths, audio files only, re-reading
only new or changed files, removed files dropped and their ids never reused,
unmatched files rematched only when the catalog changed, hand-set matches
left alone - and files.db following merged/deleted songs through
song_references. Temp-file SQLite for music.db, tag.db and files.db
(files.db from the real migrations/files/); tag reading and the song matcher
are faked."""

import os
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import files_index as fi
from utils.database import song_references as sr
from utils.database import users
from utils.database.datatables import Artist, Base, Song
from utils.database.tag_db_manager import Base as TagBase
from utils.discoveries.import_engine import LocalSongMatcher


@pytest.fixture
def env(tmp_path, monkeypatch):
    music_engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(music_engine)
    music = sessionmaker(bind=music_engine)
    sr.register_song_reference_sync(music)
    monkeypatch.setattr(sr, "_music_engine", lambda: music_engine)
    monkeypatch.setattr(sr, "_changes_pending", False)
    monkeypatch.setattr(sr, "FILES_DB", tmp_path / "files.db")
    monkeypatch.setattr(users, "users_available", lambda: False)

    library = tmp_path / "Library"
    library.mkdir()

    # Tags come from the filename "Artist - Title.mp3"; counts files read
    reads = []

    def read_tags(file):
        reads.append(file.name)
        artist, _, title = file.stem.partition(" - ")
        return artist or None, title or file.stem, 200

    monkeypatch.setattr(fi, "_read_tags", read_tags)

    # Exact artist name + title, against the session passed to the scan
    matches = []

    def match(self, metadata):
        matches.append(metadata["title"])
        return (self.session.query(Song).join(Artist)
                .filter(Artist.name == metadata["artist_name"], Song.title == metadata["title"])
                .first())

    monkeypatch.setattr(LocalSongMatcher, "match", match)

    session = music()
    yield session, library, tmp_path / "files.db", reads, matches
    session.close()


def _song(session, artist_name, title):
    artist = session.query(Artist).filter_by(name=artist_name).first() or Artist(name=artist_name)
    song = Song(title=title, artist=artist)
    session.add(song)
    session.commit()
    return song


def _file(library, rel, content=b"x"):
    path = library / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _rows(db_path):
    with sqlite3.connect(db_path) as conn:
        return {path: (file_id, song_id, source, artist, title)
                for file_id, path, song_id, source, artist, title in conn.execute(
                    "SELECT id, path, song_id, match_source, artist, title FROM files")}


def test_first_scan_indexes_audio_files_by_relative_path(env):
    session, library, db, reads, _ = env
    song = _song(session, "Hey", "Karma")
    _file(library, "Hey - Karma.mp3")
    _file(library, "Folder/Sub/Nobody - Unknown.MP3")
    _file(library, "Folder/Thumbs.db")
    _file(library, "Folder/cover.jpg")

    report = fi.scan_library(session, root=library, db_path=db)

    rows = _rows(db)
    assert set(rows) == {"Hey - Karma.mp3", "Folder/Sub/Nobody - Unknown.MP3"}
    assert rows["Hey - Karma.mp3"][1:] == (song.id, "auto", "Hey", "Karma")
    assert rows["Folder/Sub/Nobody - Unknown.MP3"][1:] == (None, "auto", "Nobody", "Unknown")
    assert (report.files, report.added, report.changed, report.removed, report.matched_now) == (2, 2, 0, 0, 1)
    assert report.unmatched == [("Folder/Sub/Nobody - Unknown.MP3", "Nobody", "Unknown")]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT filename FROM schema_version").fetchall() == [("0001_files.sql",)]


def test_unchanged_library_reads_and_matches_nothing(env):
    session, library, db, reads, matches = env
    _song(session, "Hey", "Karma")
    _file(library, "Hey - Karma.mp3")
    _file(library, "Nobody - Unknown.mp3")
    fi.scan_library(session, root=library, db_path=db)
    before = _rows(db)
    reads.clear()
    matches.clear()

    report = fi.scan_library(session, root=library, db_path=db)

    assert reads == [] and matches == []
    assert _rows(db) == before
    assert (report.added, report.changed, report.removed, report.matched_now) == (0, 0, 0, 0)
    assert len(report.unmatched) == 1


def test_changed_new_and_removed_files(env):
    session, library, db, reads, _ = env
    _song(session, "Hey", "Karma")
    _file(library, "Hey - Karma.mp3")
    gone = _file(library, "Old - Gone.mp3")
    fi.scan_library(session, root=library, db_path=db)
    old_ids = {path: row[0] for path, row in _rows(db).items()}
    reads.clear()

    _file(library, "Hey - Karma.mp3", b"longer content")
    gone.unlink()
    _file(library, "New - One.mp3")
    report = fi.scan_library(session, root=library, db_path=db)

    rows = _rows(db)
    assert sorted(reads) == ["Hey - Karma.mp3", "New - One.mp3"]
    assert (report.added, report.changed, report.removed) == (1, 1, 1)
    assert set(rows) == {"Hey - Karma.mp3", "New - One.mp3"}
    assert rows["Hey - Karma.mp3"][0] == old_ids["Hey - Karma.mp3"]
    assert rows["Hey - Karma.mp3"][1] is not None  # rematched after re-reading
    # AUTOINCREMENT: the removed file's id isn't handed to the new one
    assert rows["New - One.mp3"][0] > max(old_ids.values())


def test_touched_file_is_reread(env):
    session, library, db, reads, _ = env
    path = _file(library, "Hey - Karma.mp3")
    fi.scan_library(session, root=library, db_path=db)
    reads.clear()

    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 10))
    report = fi.scan_library(session, root=library, db_path=db)

    assert reads == ["Hey - Karma.mp3"] and report.changed == 1


def test_unmatched_file_matched_once_its_song_is_added(env):
    session, library, db, _, matches = env
    _file(library, "Hey - Karma.mp3")
    fi.scan_library(session, root=library, db_path=db)
    assert _rows(db)["Hey - Karma.mp3"][1] is None

    song = _song(session, "Hey", "Karma")
    report = fi.scan_library(session, root=library, db_path=db)

    assert _rows(db)["Hey - Karma.mp3"][1] == song.id
    assert report.matched_now == 1 and report.unmatched == []


def test_manual_rows_are_never_rematched(env):
    session, library, db, _, matches = env
    _file(library, "Hey - Karma.mp3")
    fi.scan_library(session, root=library, db_path=db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE files SET match_source = 'manual'")

    _song(session, "Hey", "Karma")  # catalog changed
    matches.clear()
    fi.scan_library(session, root=library, db_path=db)

    assert matches == []
    assert _rows(db)["Hey - Karma.mp3"][1:3] == (None, "manual")


def test_link_file_to_song_pins_a_file_its_tags_no_longer_find(env):
    # The tags still say "Mogę zniknąć"; the song was renamed to the version the file really is
    session, library, db, _, matches = env
    song = _song(session, "Taco Hemingway", "Mogę zniknąć (SzUsty Blend)")
    path = _file(library, "Taco Hemingway - Mogę zniknąć.mp3")
    fi.scan_library(session, root=library, db_path=db)
    assert _rows(db)["Taco Hemingway - Mogę zniknąć.mp3"][1:3] == (None, "auto")

    fi.link_file_to_song("Taco Hemingway - Mogę zniknąć.mp3", song.id, db_path=db)
    assert _rows(db)["Taco Hemingway - Mogę zniknąć.mp3"][1:3] == (song.id, "manual")

    # Re-read (its contents changed) and the catalog changed: still pinned, never rematched
    path.write_bytes(b"retagged")
    os.utime(path, (1, 1))
    _song(session, "Taco Hemingway", "Mogę zniknąć")
    matches.clear()
    fi.scan_library(session, root=library, db_path=db)
    assert matches == []
    assert _rows(db)["Taco Hemingway - Mogę zniknąć.mp3"][1:3] == (song.id, "manual")


def test_link_file_to_song_refuses_a_file_not_in_the_index(env):
    session, library, db, _, _ = env
    fi.scan_library(session, root=library, db_path=db)
    with pytest.raises(LookupError):
        fi.link_file_to_song("Nowhere - Missing.mp3", 1, db_path=db)


def test_files_follow_merged_and_deleted_songs(env):
    session, library, db, _, _ = env
    kept = _song(session, "Hey", "Karma")
    merged = _song(session, "Hey", "Karma (live)")
    deleted = _song(session, "Hey", "Gone")
    _file(library, "Hey - Karma (live).mp3")
    _file(library, "Hey - Gone.mp3")
    fi.scan_library(session, root=library, db_path=db)
    assert _rows(db)["Hey - Karma (live).mp3"][1] == merged.id

    sr.record_song_merge(session, merged.id, kept.id)
    session.delete(merged)
    session.delete(deleted)
    session.commit()
    tag_engine = create_engine(f"sqlite:///{db.parent / 'tag.db'}")
    TagBase.metadata.create_all(tag_engine)
    with tag_engine.begin() as conn:
        conn.exec_driver_sql((repo_root / "migrations/tag/0002_applied_song_changes.sql").read_text())
    with sessionmaker(bind=tag_engine)() as tag_session:
        results = sr.apply_song_id_changes(tag_session=tag_session, force=True)

    rows = _rows(db)
    assert results["files.db"] == 2
    assert rows["Hey - Karma (live).mp3"][1] == kept.id
    assert rows["Hey - Gone.mp3"][1:3] == (None, "auto")


def test_scan_replays_song_changes_before_matching(env):
    session, library, db, _, _ = env
    kept = _song(session, "Hey", "Karma")
    merged = _song(session, "Hey", "Karma 2")
    _file(library, "Hey - Karma 2.mp3")
    fi.scan_library(session, root=library, db_path=db)

    sr.record_song_merge(session, merged.id, kept.id)
    session.delete(merged)
    session.commit()
    fi.scan_library(session, root=library, db_path=db)  # no apply_song_id_changes() in between

    assert _rows(db)["Hey - Karma 2.mp3"][1] == kept.id


def test_missing_or_remote_library_is_unavailable(env, monkeypatch, tmp_path):
    session, _, db, _, _ = env
    monkeypatch.setattr(fi.Settings, "local_library_dir_str", str(tmp_path / "not-mounted" / "Library"))
    with pytest.raises(fi.LibraryUnavailableError):
        fi.library_root()

    monkeypatch.setattr(fi.Settings, "local_library_dir_str", "smb://server/Shared/Music/")
    with pytest.raises(fi.LibraryUnavailableError):
        fi.library_root()

    with pytest.raises(fi.LibraryUnavailableError):
        fi.scan_library(session, db_path=db)
    assert not db.exists()
