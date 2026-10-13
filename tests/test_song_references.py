"""Song references across files (utils/database/song_references.py): the
song_id_changes log (deletions logged by the flush listener, merges by
record_song_merge(), bulk deletes by record_song_deletions()), song ids never
being reused, and replaying the log into tag.db (global tags) and a user's
user.db (playlist tracks, private tags) - idempotent, cursor-based, with the
first tag.db replay dropping tags of songs that no longer exist. Temp-file
SQLite, no mocking of SQL."""

import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import song_references as sr
from utils.database import users
from utils.database.datatables import Artist, Base, Song, SongIdChange
from utils.database.tag_db_manager import Base as TagBase


@pytest.fixture
def env(tmp_path, monkeypatch):
    music_engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(music_engine)
    music = sessionmaker(bind=music_engine)
    sr.register_song_reference_sync(music)
    monkeypatch.setattr(sr, "_music_engine", lambda: music_engine)

    tag_engine = create_engine(f"sqlite:///{tmp_path / 'tag.db'}")
    TagBase.metadata.create_all(tag_engine)
    with tag_engine.begin() as conn:
        conn.exec_driver_sql((repo_root / "migrations/tag/0002_applied_song_changes.sql").read_text())
    tag = sessionmaker(bind=tag_engine)

    monkeypatch.setattr(users, "USERS_DIR", tmp_path / "Users")
    monkeypatch.setattr(users, "users_available", lambda: True)
    monkeypatch.setattr(sr, "_changes_pending", False)
    # Never the real src/database/files.db
    monkeypatch.setattr(sr, "FILES_DB", tmp_path / "files.db")
    return music, tag, tmp_path


def _songs(session, *titles):
    artist = Artist(name="A")
    songs = [Song(title=t, artist=artist) for t in titles]
    session.add_all(songs)
    session.commit()
    return songs


def _tag_rows(tag_session):
    return sorted(tuple(r) for r in tag_session.execute(text(
        "SELECT st.song_id, t.name FROM song_tags st JOIN tags t ON t.id = st.tag_id")))


def _add_tags(tag_session, pairs):
    names = {name for _, name in pairs}
    for i, name in enumerate(sorted(names), 1):
        tag_session.execute(text("INSERT INTO tags (id, name) VALUES (:i, :n)"), {"i": i, "n": name})
    ids = {name: i for i, name in enumerate(sorted(names), 1)}
    for song_id, name in pairs:
        tag_session.execute(text("INSERT INTO song_tags (song_id, tag_id) VALUES (:s, :t)"),
                            {"s": song_id, "t": ids[name]})
    tag_session.commit()


def test_deleting_a_song_logs_it_and_its_id_is_never_reused(env):
    music, _, _ = env
    s = music()
    a, b = _songs(s, "a", "b")
    b_id = b.id
    s.delete(b)
    s.commit()
    assert [(c.old_song_id, c.new_song_id) for c in s.query(SongIdChange)] == [(b_id, None)]
    assert sr._changes_pending

    (c,) = _songs(s, "c")
    assert c.id == b_id + 1  # SQLite alone would have handed out b's id again


def test_merge_is_logged_once_not_as_a_deletion(env):
    music, _, _ = env
    s = music()
    keep, dup = _songs(s, "x", "x")
    keep_id, dup_id = keep.id, dup.id
    sr.record_song_merge(s, dup_id, keep_id)
    s.delete(dup)
    s.commit()
    assert [(c.old_song_id, c.new_song_id) for c in s.query(SongIdChange)] == [(dup_id, keep_id)]


def test_replay_moves_and_drops_global_tags_and_cleans_old_leftovers(env):
    music, tag, _ = env
    s = music()
    keep, dup, gone = _songs(s, "x", "x", "y")
    keep_id, dup_id, gone_id = keep.id, dup.id, gone.id
    t = tag()
    _add_tags(t, [(keep_id, "fav"), (dup_id, "fav"), (dup_id, "party"), (gone_id, "fav"), (999, "spellchecked")])

    sr.record_song_merge(s, dup_id, keep_id)
    s.delete(dup)
    s.delete(gone)
    s.commit()

    # dup's "party" moved (its "fav" was already on keep), gone's tag and the leftover 999 dropped
    assert sr.apply_song_id_changes(tag_session=t) == {"tag.db": 3}
    assert _tag_rows(t) == [(keep_id, "fav"), (keep_id, "party")]
    # Nothing new: no work; forced: replaying again changes nothing
    assert sr.apply_song_id_changes(tag_session=t) == {}
    assert sr.apply_song_id_changes(tag_session=t, force=True) == {"tag.db": 0}
    assert _tag_rows(t) == [(keep_id, "fav"), (keep_id, "party")]


def test_replay_reaches_users_playlists_and_private_tags(env):
    music, tag, tmp_path = env
    s = music()
    keep, dup, gone = _songs(s, "x", "x", "y")
    keep_id = keep.id
    folder = users.create_user("ania", "secret1")  # cursor starts at the current log end
    db = sqlite3.connect(folder / "user.db")
    db.execute("INSERT INTO playlists (id, name, created_at, updated_at) VALUES (1, 'Mix', 0, 0)")
    for position, song in enumerate([dup, gone, keep]):
        db.execute("INSERT INTO playlist_tracks (playlist_id, position, song_id, title) VALUES (1, ?, ?, ?)",
                   (position, song.id, song.title))
    db.execute("INSERT INTO tags (id, name) VALUES (1, 'mine')")
    db.execute("INSERT INTO song_tags (song_id, tag_id) VALUES (?, 1), (?, 1)", (dup.id, gone.id))
    db.commit()

    sr.record_song_merge(s, dup.id, keep.id)
    s.delete(dup)
    s.delete(gone)
    s.commit()
    results = sr.apply_song_id_changes(tag_session=tag())
    assert results["user ania"] == 4

    assert db.execute("SELECT position, song_id, title FROM playlist_tracks ORDER BY position").fetchall() == [
        (0, keep_id, "x"), (1, None, "y"), (2, keep_id, "x")]
    assert db.execute("SELECT song_id FROM song_tags").fetchall() == [(keep_id,)]


def _synced_playlist(folder, uid, song_ids, checked_to=None):
    """A playlist stored through playlist_sync (as the menu or the server
    would); `checked_to` = song_ids_checked_to of a server write."""
    from contextlib import closing
    from utils.database import playlist_sync as ps
    with closing(ps.connect(folder / "user.db")) as conn:
        body = ps.parse_playlist({"name": uid, "updated_at": 1,
                                  "tracks": [{"song_id": s, "title": f"t{s}"} for s in song_ids]})
        return ps.store_playlist(conn, folder.name, uid, body, 0, song_ids_checked_to=checked_to)[1]


def _revisions(db):
    return dict(db.execute("SELECT uid, revision FROM playlists").fetchall())


def test_replay_gives_changed_playlists_a_new_revision(env):
    music, tag, _ = env
    s = music()
    keep, dup, other = _songs(s, "x", "x", "y")
    keep_id, dup_id = keep.id, dup.id
    folder = users.create_user("ania", "secret1")
    _synced_playlist(folder, "with-dup", [dup_id, other.id])   # revision 1
    _synced_playlist(folder, "untouched", [other.id])          # revision 2

    sr.record_song_merge(s, dup_id, keep_id)
    s.delete(dup)
    s.commit()
    sr.apply_song_id_changes(tag_session=tag())

    db = sqlite3.connect(folder / "user.db")
    assert _revisions(db) == {"with-dup": 3, "untouched": 2}
    assert db.execute("SELECT last_revision FROM playlist_revision").fetchone() == (3,)
    # Nothing new: no new revision either
    sr.apply_song_id_changes(tag_session=tag(), force=True)
    assert _revisions(db) == {"with-dup": 3, "untouched": 2}


def test_replay_catches_up_playlists_resolved_against_an_older_shared_copy(env):
    music, tag, _ = env
    s = music()
    a, b, kept = _songs(s, "a", "b", "kept")
    a_id, b_id, kept_id = a.id, b.id, kept.id
    folder = users.create_user("ania", "secret1")
    # Merged and replayed into user.db before the server stored anything...
    sr.record_song_merge(s, a_id, kept_id)
    s.delete(a)
    s.commit()
    sr.apply_song_id_changes(tag_session=tag())
    # ...but the shared copy the server read was older (log entry 0), so the
    # phone's stale id went in as it was; and one more merge comes later
    _synced_playlist(folder, "from-phone", [a_id, b_id], checked_to=0)
    _synced_playlist(folder, "from-menu", [a_id])  # (can't happen; shows only server writes are caught up)
    sr.record_song_merge(s, b_id, kept_id)
    s.delete(b)
    s.commit()
    sr.apply_song_id_changes(tag_session=tag())

    db = sqlite3.connect(folder / "user.db")
    tracks = db.execute("SELECT p.uid, t.song_id FROM playlist_tracks t JOIN playlists p ON p.id = t.playlist_id "
                        "ORDER BY p.uid, t.position").fetchall()
    assert tracks == [("from-menu", a_id), ("from-phone", kept_id), ("from-phone", kept_id)]
    assert db.execute("SELECT song_ids_checked_to FROM playlists WHERE uid = 'from-phone'").fetchone() == (None,)
    assert _revisions(db)["from-phone"] == 3


def test_new_user_skips_changes_made_before_it_existed(env):
    music, tag, _ = env
    s = music()
    (a,) = _songs(s, "a")
    s.delete(a)
    s.commit()
    folder = users.create_user("ania", "secret1")
    db = sqlite3.connect(folder / "user.db")
    assert db.execute("SELECT last_change_id FROM applied_song_changes").fetchone() == (1,)


def test_bulk_deletes_are_logged_explicitly(env):
    music, _, _ = env
    s = music()
    ids = [song.id for song in _songs(s, "a", "b")]
    sr.record_song_deletions(s, ids)
    s.query(Song).delete(synchronize_session=False)
    s.commit()
    assert sorted(c.old_song_id for c in s.query(SongIdChange)) == ids
