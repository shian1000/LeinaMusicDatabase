"""Albums (utils/database/albums.py): songs.album_id following songs.album on
every flush, the post-migration backfill, the multi-artist resolver's
helpers, album/artist merges, and the migration files themselves.
In-memory SQLite, no mocking of SQL."""

import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, text
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from config.constants import ALBUM_TYPE_COMPILATION, ALBUM_TYPE_SINGLE, SINGLES_ALBUM
from utils.database import albums as al
from utils.database.datatables import Album, AlbumCover, Artist, Base, Song


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    al.register_album_sync(factory)
    return factory


@pytest.fixture
def session(session_factory):
    s = session_factory()
    yield s
    s.close()


@pytest.fixture
def covers(tmp_path, monkeypatch):
    monkeypatch.setattr(al, "covers_dir", lambda: tmp_path)
    return tmp_path


def _artists(session, *names):
    artists = [Artist(name=n) for n in names]
    session.add_all(artists)
    session.flush()
    return artists


def _song(session, title, artist, album=None, year=None):
    song = Song(title=title, artist=artist, album=album, year=year)
    session.add(song)
    session.flush()
    return song


def _album_count(session):
    return session.query(Album).count()


# ---- keeping album_id in step with album ----------------------------------

def test_songs_of_one_artist_share_one_album(session):
    (a,) = _artists(session, "Madonna")
    s1 = _song(session, "Frozen", a, "Ray of Light", 1998)
    s2 = _song(session, "Nothing Really Matters", a, "Ray Of Light")

    assert s1.album_entry is s2.album_entry
    album = s1.album_entry
    assert (album.title, album.artist_id, album.year, album.type) == ("Ray of Light", a.id, 1998, None)


def test_new_song_set_by_artist_id_finds_album(session):
    (a,) = _artists(session, "Hey")
    s1 = _song(session, "Karma", a, "Karma")
    s2 = Song(title="Moja i Twoja Nadzieja", artist_id=a.id, album="Karma")
    session.add(s2)
    session.commit()
    assert s2.album_id == s1.album_id


def test_singles_get_one_album_each_titled_like_the_song(session):
    (a,) = _artists(session, "Fletcher")
    s1 = _song(session, "Serial Heartbreaker", a, SINGLES_ALBUM, 2019)
    s2 = _song(session, "Undrunk", a, SINGLES_ALBUM)

    assert s1.album_id != s2.album_id
    assert (s1.album_entry.title, s1.album_entry.type, s1.album_entry.year) == (
        "Serial Heartbreaker", ALBUM_TYPE_SINGLE, 2019)
    assert s1.album == SINGLES_ALBUM

    s1.title = "Serial Heartbreaker (Live)"
    session.flush()
    assert s1.album_entry.title == "Serial Heartbreaker (Live)"
    assert _album_count(session) == 2


def test_no_album_text_means_no_album(session):
    (a,) = _artists(session, "A")
    song = _song(session, "x", a)
    assert song.album_id is None
    song.album = "Debut"
    session.flush()
    assert song.album_entry.title == "Debut"
    song.album = ""
    session.flush()
    assert song.album_id is None


def test_editing_album_text_moves_song_and_keeps_old_album(session):
    (a,) = _artists(session, "A")
    song = _song(session, "x", a, "First")
    first_id = song.album_id
    song.album = "Second"
    session.flush()
    assert song.album_entry.title == "Second"
    # Kept (id and cover stay) for a later edit back
    assert session.get(Album, first_id) is not None
    song.album = "first"
    session.flush()
    assert song.album_id == first_id


def test_title_shared_by_several_artists_is_left_for_the_user(session):
    a, b = _artists(session, "A", "B")
    s1 = _song(session, "x", a, "Greatest Hits")
    s2 = _song(session, "y", b, "Greatest Hits")
    assert s1.album_id is not None  # first one in, nobody else had the title
    assert s2.album_id is None


def test_song_joins_compilation_with_that_title(session):
    a, b = _artists(session, "A", "B")
    comp = Album(title="Cyberpunk: Edgerunners OST", type=ALBUM_TYPE_COMPILATION)
    session.add(comp)
    session.flush()
    song = _song(session, "x", b, "Cyberpunk Edgerunners OST")
    assert song.album_entry is comp


def test_song_joins_its_own_artists_album_even_when_others_share_the_title(session):
    a, b = _artists(session, "A", "B")
    album_a = Album(title="Hits", artist=a)
    album_b = Album(title="Hits", artist=b)
    session.add_all([album_a, album_b])
    session.flush()
    song = _song(session, "x", b, "Hits")
    assert song.album_entry is album_b


def test_artist_change_moves_album_row_when_all_its_songs_move(session):
    joined, a = _artists(session, "A feat. B", "A")
    s1 = _song(session, "x", joined, "Debut")
    s2 = _song(session, "y", joined, "Debut")
    album_id = s1.album_id

    s1.artist = a
    s2.artist = a
    session.flush()
    assert s1.album_id == s2.album_id == album_id
    assert session.get(Album, album_id).artist_id == a.id


def test_artist_change_of_one_song_leaves_shared_album(session):
    a, b = _artists(session, "A", "B")
    s1 = _song(session, "x", a, "Debut")
    s2 = _song(session, "y", a, "Debut")
    album_id = s1.album_id
    s2.artist_id = b.id
    session.flush()
    assert s1.album_id == album_id
    assert session.get(Album, album_id).artist_id == a.id
    assert s2.album_id is None  # "Debut" now has two artists


def test_singles_follow_artist_change(session):
    a, b = _artists(session, "A", "B")
    song = _song(session, "x", a, SINGLES_ALBUM)
    album_id = song.album_id
    song.artist_id = b.id
    session.flush()
    assert song.album_id == album_id
    assert session.get(Album, album_id).artist_id == b.id


def test_new_artist_and_song_in_one_flush(session):
    artist = Artist(name="New")
    s1 = Song(title="x", artist=artist, album="Debut")
    s2 = Song(title="y", artist=artist, album="Debut")
    session.add_all([s1, s2])
    session.commit()
    assert s1.album_id == s2.album_id is not None
    assert s1.album_entry.artist_id == artist.id


# ---- backfill ---------------------------------------------------------------

def _raw_song(session, title, artist, album, year=None):
    """A song as it was before migration 0006: album text, no album_id."""
    song_id = session.execute(
        insert(Song.__table__).values(title=title, artist_id=artist.id, album=album, year=year)
    ).inserted_primary_key[0]
    return session.get(Song, song_id)


def test_backfill_groups_titles_before_deciding(session):
    a, b = _artists(session, "A", "B")
    songs = [
        _raw_song(session, "1", a, "Solo", 2001),
        _raw_song(session, "2", a, "solo", 2001),
        _raw_song(session, "3", a, "Solo", 2002),
        _raw_song(session, "4", a, "Shared"),
        _raw_song(session, "5", b, "Shared"),
        _raw_song(session, "6", b, SINGLES_ALBUM),
    ]
    assert all(s.album_id is None for s in songs)

    left = al.link_unassigned_songs(session)
    session.commit()

    assert sorted(s.title for s in left) == ["4", "5"]
    assert songs[0].album_id == songs[1].album_id == songs[2].album_id
    assert songs[0].album_entry.year == 2001
    assert songs[3].album_id is None and songs[4].album_id is None
    assert songs[5].album_entry.type == ALBUM_TYPE_SINGLE
    # Re-running changes nothing
    assert len(al.link_unassigned_songs(session)) == 2
    assert _album_count(session) == 2


def test_find_unresolved_album_groups_lists_pending_and_linked_songs(session):
    a, b = _artists(session, "A", "B")
    linked = _song(session, "x", a, "Hits")
    pending = _song(session, "y", b, "Hits")
    _song(session, "z", a, "Other")

    groups = al.find_unresolved_album_groups(session)
    assert [g.title for g in groups] == ["Hits"]
    assert groups[0].pending == [pending]
    assert groups[0].songs == [linked, pending]
    assert [g.key for g in al.find_unresolved_album_groups(session, [linked])] == ["hits"]
    assert al.find_unresolved_album_groups(session, []) == []


# ---- resolver ---------------------------------------------------------------

def test_compilation_reuses_the_existing_album_row(session):
    a, b = _artists(session, "A", "B")
    s1 = _song(session, "x", a, "OST")
    s2 = _song(session, "y", b, "OST")
    album_id = s1.album_id

    album = al.assign_songs_to_album(session, [s1, s2], "OST", None, ALBUM_TYPE_COMPILATION)
    session.commit()
    assert album.id == album_id
    assert (album.artist_id, album.type) == (None, ALBUM_TYPE_COMPILATION)
    assert s2.album_id == album_id
    assert al.find_unresolved_album_groups(session) == []
    # Later songs of that title join the compilation
    (c,) = _artists(session, "C")
    assert _song(session, "z", c, "OST").album_id == album_id


def test_separate_albums_per_artist(session):
    a, b = _artists(session, "A", "B")
    s1 = _song(session, "x", a, "Hits")
    s2 = _song(session, "y", b, "Hits")
    first = al.assign_songs_to_album(session, [s1], "Hits", a)
    second = al.assign_songs_to_album(session, [s2], "Hits", b)
    session.commit()
    assert first.id == s1.album_id and second.id == s2.album_id and first.id != second.id
    assert second.artist_id == b.id


def test_merging_albums_keeps_one_id_and_moves_cover(session, covers):
    a, b = _artists(session, "A", "B")
    s1 = _song(session, "x", a, "OST")
    keep = al.assign_songs_to_album(session, [s1], "OST", a)
    s2 = _song(session, "y", b, "OST")
    drop = al.assign_songs_to_album(session, [s2], "OST", b)
    drop.covers.append(AlbumCover(source="file", path=f"{drop.id}.jpg", status="found"))
    session.commit()
    (covers / f"{drop.id}.jpg").write_bytes(b"jpeg")
    drop_id, keep_id = drop.id, keep.id

    al.merge_albums(session, keep, drop)
    session.commit()
    assert session.get(Album, drop_id) is None
    assert s2.album_id == keep_id
    assert [(c.source, c.path) for c in keep.covers] == [("file", f"{keep_id}.jpg")]
    assert (covers / f"{keep_id}.jpg").read_bytes() == b"jpeg"
    assert not (covers / f"{drop_id}.jpg").exists()


def test_artist_merge_reassigns_and_merges_albums(session):
    a, dup = _artists(session, "Akute", "akuteofficial")
    s1 = _song(session, "x", a, "Debut")
    s2 = _song(session, "y", dup, "debut")
    s3 = _song(session, "z", dup, "Second")
    s2_old_album = s2.album_id
    assert s2_old_album is None  # two artists shared "Debut"
    session.commit()

    session.execute(text("UPDATE songs SET artist_id = :a WHERE artist_id = :d"), {"a": a.id, "d": dup.id})
    al.reassign_artist_albums(session, dup.id, a.id)
    session.commit()

    assert s3.album_entry.artist_id == a.id
    assert session.query(Album).filter(Album.artist_id == dup.id).count() == 0
    # Its "Debut" was never linked; the backfill can link it now
    session.expire_all()
    assert al.link_unassigned_songs(session) == []
    session.commit()
    assert session.get(Song, s2.id).album_id == session.get(Song, s1.id).album_id


# ---- migrations ---------------------------------------------------------------

def test_new_migrations_apply_to_a_pre_album_schema():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE artists (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, origin VARCHAR, synonyms VARCHAR);"
        "CREATE TABLE songs (id INTEGER PRIMARY KEY, title VARCHAR NOT NULL, album VARCHAR, year INTEGER,"
        " language VARCHAR, artist_id INTEGER NOT NULL, nostalgic INTEGER, melancholic INTEGER, party INTEGER,"
        " youtube_video_id VARCHAR, spotify_url VARCHAR);"
    )
    for name in ("0005_albums.sql", "0006_song_album_id.sql", "0007_song_track_position.sql", "0008_album_covers.sql"):
        conn.executescript((repo_root / "migrations" / "music" / name).read_text())
    song_columns = {row[1] for row in conn.execute("PRAGMA table_info(songs)")}
    assert {"album", "album_id", "track_number", "disc_number"} <= song_columns
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"albums", "album_covers"} <= tables


# ---- the resolver menu (utils/ui/album_resolution_review.py) ----------------

class _Answer:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


def test_review_settles_titles_and_is_resumable(session, monkeypatch):
    from utils.ui import album_resolution_review as review

    a, b = _artists(session, "A", "B")
    ost = [_song(session, "x", a, "OST"), _song(session, "y", b, "OST")]
    hits = [_song(session, "p", a, "Hits"), _song(session, "q", b, "Hits")]
    later = [_song(session, "m", a, "Zzz"), _song(session, "n", b, "Zzz")]
    session.commit()

    answers = iter([review._PER_ARTIST, review._SOUNDTRACK, review._STOP])
    monkeypatch.setattr(review, "open_and_set_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(review, "submit_global_database_session", session.commit)
    monkeypatch.setattr(review.questionary, "select", lambda *args, **kwargs: _Answer(next(answers)))

    assert review.review_multi_artist_albums() == 2
    assert hits[0].album_id != hits[1].album_id and hits[1].album_entry.artist_id == b.id
    assert ost[0].album_id == ost[1].album_id and ost[0].album_entry.type == "soundtrack"
    # Stopped before "Zzz": still waiting for next time
    assert [g.title for g in al.find_unresolved_album_groups(session)] == ["Zzz"]
    assert later[1].album_id is None
