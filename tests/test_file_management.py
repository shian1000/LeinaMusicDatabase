"""Finding a song's file in the library's .mp3_index.sqlite3 for "Copy songs
from storage" (src/utils/common/file_management.py, COM-02)."""
import sqlite3

from sqlalchemy import create_engine

from utils.common import file_management as fm


def _index(tmp_path, rows):
    path = tmp_path / ".mp3_index.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE files (path TEXT, artist TEXT, title TEXT)")
        db.executemany("INSERT INTO files VALUES (?, ?, ?)", rows)
    engine = create_engine(f"sqlite:///{path}")
    rows_by_initial = fm.load_index_rows(engine)
    engine.dispose()
    return rows_by_initial


def test_finds_artists_starting_with_a_letter_lower_doesnt_fold(tmp_path):
    # LOWER() keeps "Ø", normalize() makes it "o": the file was never found
    rows = _index(tmp_path, [("/music/saman.mp3", "Ølafur Arnalds", "Saman"),
                             ("/music/other.mp3", "Other", "Saman"),
                             ("/music/none.mp3", None, None)])

    assert fm.find_in_index(rows, "Ølafur Arnalds", "Saman") == "/music/saman.mp3"
    assert fm.find_in_index(rows, "olafur arnalds", "saman") == "/music/saman.mp3"
    assert fm.find_in_index(rows, "Ólafur Arnalds", "Ekki hugsa") is None


def test_an_empty_artist_finds_nothing_instead_of_an_index_error(tmp_path):
    rows = _index(tmp_path, [("/music/a.mp3", "A", "Song")])

    assert fm.find_in_index(rows, "", "Song") is None
    assert fm.find_in_index(rows, None, "Song") is None


def test_an_index_on_a_share_is_read_from_a_private_copy_removed_afterwards(tmp_path, monkeypatch):
    # COM-39: every run left the index at the shared /tmp/.mp3_index.sqlite3
    from types import SimpleNamespace
    import tempfile
    source = tmp_path / "share" / ".mp3_index.sqlite3"
    source.parent.mkdir()
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE files (path TEXT, artist TEXT, title TEXT)")
        db.execute("INSERT INTO files VALUES ('/music/a.mp3', 'Hey', 'Teksański')")
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    read_from, copied = [], []
    real_read = fm._read_index
    monkeypatch.setattr(fm, "_read_index", lambda path: read_from.append(path) or real_read(path))
    monkeypatch.setattr(fm, "copy_file_to_destination", lambda path, dest: copied.append(path))

    remote = SimpleNamespace(read_bytes=source.read_bytes)  # an UPath to smb://, as far as this goes
    fm.copy_songs_using_index([("Hey", "Teksański")], remote, tmp_path / "out")

    assert copied == ["/music/a.mp3"]
    assert temp_root in read_from[0].parents
    assert list(temp_root.iterdir()) == []
