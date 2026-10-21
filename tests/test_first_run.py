"""A fresh install without databases: the public catalog downloaded from a
local fake release (never GitHub), checked against version.json, or an empty
start through the models; then the startup migrations on top."""

import functools
import hashlib
import json
import sqlite3
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from utils.database import first_run, migrations, music_db_manager, public_export, tag_db_manager
from utils.database.datatables import Artist, Song
from utils.ui import first_run_prompt


# ==================== fixtures ====================

@pytest.fixture
def db_dir(tmp_path, monkeypatch):
    """The app's database folder, empty, with the managers' engines and the
    migration runner pointed at it."""
    directory = tmp_path / "database"
    monkeypatch.setattr(first_run, "database_dir", lambda: directory)
    for module, name in ((music_db_manager, "music.db"), (tag_db_manager, "tag.db")):
        engine = create_engine(f"sqlite:///{directory / name}")
        monkeypatch.setattr(module, "DB_PATH", directory / name)
        monkeypatch.setattr(module, "ENGINE", engine)
        monkeypatch.setattr(module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(migrations, "DB_FILES", {"music": directory / "music.db", "tag": directory / "tag.db"})
    monkeypatch.setattr(migrations, "backup_databases", lambda reason: [])
    return directory


def _owner_databases(folder: Path) -> dict:
    """An owner's music.db / tag.db made the normal way, with a song, a mood
    and a marker, migrations adopted."""
    folder.mkdir()
    music = create_engine(f"sqlite:///{folder / 'music.db'}")
    tag = create_engine(f"sqlite:///{folder / 'tag.db'}")
    music_db_manager.Base.metadata.create_all(music)
    tag_db_manager.Base.metadata.create_all(tag)
    with sessionmaker(bind=music)() as session:
        artist = Artist(name="Kino")
        session.add(artist)
        session.flush()
        session.add(Song(title="Gruppa krovi", artist_id=artist.id))
        session.commit()
    with tag.begin() as conn:
        conn.exec_driver_sql("INSERT INTO tags (id, name, kind) VALUES (1, 'Calm', 'mood'), (2, 'spellchecked', 'marker')")
        conn.exec_driver_sql("INSERT INTO song_tags (song_id, tag_id) VALUES (1, 1), (1, 2)")
    music.dispose()
    tag.dispose()
    for name in ("music", "tag"):
        conn = sqlite3.connect(str(folder / f"{name}.db"))
        migrations._ensure_schema_version_table(conn)
        for f in migrations._migration_files(name):
            conn.execute("INSERT INTO schema_version (filename) VALUES (?)", (f.name,))
        conn.commit()
        conn.close()
    return {"music.db": folder / "music.db", "tag.db": folder / "tag.db"}


def _version_json(release: Path, **overrides) -> dict:
    files = {}
    for name in first_run.CATALOG_FILES:
        data = (release / name).read_bytes()
        files[name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "content_sha256": "x"}
    document = {"format": 1, "published_at": "2026-10-21T11:35:11Z", "files": files, **overrides}
    (release / "version.json").write_text(json.dumps(document))
    return document


@pytest.fixture
def release(tmp_path):
    """A fake GitHub release folder holding the public export of an owner's
    databases, served over HTTP on 127.0.0.1."""
    sources = _owner_databases(tmp_path / "owner")
    folder = tmp_path / "release"
    folder.mkdir()
    public_export.build_public_database(sources["music.db"], folder / "music.db", public_export.MUSIC_SPEC)
    public_export.build_public_database(sources["tag.db"], folder / "tag.db", public_export.TAG_SPEC)
    _version_json(folder)

    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(folder))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.RequestHandlerClass.log_message = lambda *a: None
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    yield {"folder": folder, "url": f"http://127.0.0.1:{server.server_address[1]}"}
    server.shutdown()
    server.server_close()


# ==================== the download ====================

def test_download_verifies_and_installs_both_databases(db_dir, release):
    written = first_run.download_public_catalog(base_url=release["url"])

    assert written == [db_dir / "music.db", db_dir / "tag.db"]
    assert sorted(p.name for p in db_dir.iterdir()) == ["music.db", "tag.db"]  # no partial files
    for name in first_run.CATALOG_FILES:
        assert (db_dir / name).read_bytes() == (release["folder"] / name).read_bytes()


def test_downloaded_catalog_runs_like_the_owners_database(db_dir, release):
    first_run.download_public_catalog(base_url=release["url"])

    # Every migration is already recorded, so the startup run applies nothing
    assert migrations.run_pending_migrations("music") == []
    assert migrations.run_pending_migrations("tag") == []
    with music_db_manager.get_music_session() as session:
        assert [(s.title, s.artist.name) for s in session.query(Song)] == [("Gruppa krovi", "Kino")]
    with tag_db_manager.get_tag_session() as session:
        # Moods only - the owner's markers aren't in the public copy
        assert [(t.name, t.kind) for t in session.query(tag_db_manager.Tag)] == [("Calm", "mood")]
        assert session.query(tag_db_manager.AppliedSongChange).count() == 0


def test_catalog_info_comes_from_version_json(release):
    info = first_run.fetch_catalog_info(release["url"])

    assert info.published_at == "2026-10-21T11:35:11Z"
    assert set(info.files) == {"music.db", "tag.db"}
    assert info.total_bytes == sum((release["folder"] / n).stat().st_size for n in first_run.CATALOG_FILES)


@pytest.mark.parametrize("change, message", [
    ("sha256", "SHA-256 doesn't match"),
    ("size", "says"),
])
def test_a_file_not_matching_version_json_leaves_nothing(db_dir, release, change, message):
    document = _version_json(release["folder"])
    entry = document["files"]["tag.db"]
    if change == "sha256":
        entry["sha256"] = "0" * 64
    else:
        entry["size"] += 1
    (release["folder"] / "version.json").write_text(json.dumps(document))

    with pytest.raises(first_run.CatalogDownloadError, match=message):
        first_run.download_public_catalog(base_url=release["url"])

    # music.db was fine, but it's never installed without its tag.db
    assert not db_dir.exists() or list(db_dir.iterdir()) == []


def test_a_bigger_file_than_announced_stops_the_download(db_dir, release):
    document = _version_json(release["folder"])
    document["files"]["music.db"]["size"] = 100
    (release["folder"] / "version.json").write_text(json.dumps(document))

    with pytest.raises(first_run.CatalogDownloadError, match="bigger than"):
        first_run.download_public_catalog(base_url=release["url"])
    assert list(db_dir.iterdir()) == []


def test_a_file_that_isnt_sqlite_is_refused(db_dir, release):
    (release["folder"] / "tag.db").write_bytes(b"<html>not a database</html>")
    _version_json(release["folder"])

    with pytest.raises(first_run.CatalogDownloadError, match="isn't an SQLite database"):
        first_run.download_public_catalog(base_url=release["url"])
    assert list(db_dir.iterdir()) == []


@pytest.mark.parametrize("document, message", [
    ({"format": 2, "files": {}}, "format 2"),
    ({"format": 1}, "no file list"),
    ({"format": 1, "files": {"music.db": {"size": 1, "sha256": "a"}}}, "doesn't describe tag.db"),
    ({"format": 1, "files": {n: {"size": 0, "sha256": "a"} for n in ("music.db", "tag.db")}}, "implausible size"),
])
def test_a_bad_version_json_is_refused(db_dir, release, document, message):
    (release["folder"] / "version.json").write_text(json.dumps(document))

    with pytest.raises(first_run.CatalogDownloadError, match=message):
        first_run.download_public_catalog(base_url=release["url"])


def test_a_missing_release_says_so(db_dir, release):
    with pytest.raises(first_run.CatalogDownloadError, match="HTTP 404"):
        first_run.download_public_catalog(base_url=release["url"] + "/no-such-release")


def test_an_unreachable_server_is_a_download_error(db_dir):
    with pytest.raises(first_run.CatalogDownloadError):
        first_run.download_public_catalog(base_url="http://127.0.0.1:9")


def test_an_existing_database_is_never_overwritten(db_dir, release):
    db_dir.mkdir()
    (db_dir / "music.db").write_bytes(b"mine")

    assert first_run.missing_databases() == ["tag.db"]
    first_run.download_public_catalog(base_url=release["url"])

    assert (db_dir / "music.db").read_bytes() == b"mine"


def test_the_catalog_comes_from_github_db_repo_or_the_project(monkeypatch):
    monkeypatch.setattr(first_run.Settings, "github_db_repo", "")
    assert first_run.catalog_base_url() == \
        "https://github.com/shian1000/LeinaMusicDatabase/releases/download/database"
    monkeypatch.setattr(first_run.Settings, "github_db_repo", "friend/catalog")
    assert first_run.catalog_base_url() == "https://github.com/friend/catalog/releases/download/database"


# ==================== an empty start ====================

def test_empty_start_has_the_real_schema_and_adopts_every_migration(db_dir):
    assert first_run.create_empty_databases() == ["music.db", "tag.db"]

    assert migrations.run_pending_migrations("music") == []
    assert migrations.run_pending_migrations("tag") == []
    for name in ("music", "tag"):
        conn = sqlite3.connect(str(db_dir / f"{name}.db"))
        recorded = {row[0] for row in conn.execute("SELECT filename FROM schema_version")}
        conn.close()
        assert recorded == {f.name for f in migrations._migration_files(name)}
    with music_db_manager.get_music_session() as session:
        assert session.query(Song).count() == 0
    # tag.db's cursor table (tag migration 0002) is part of the models too
    with tag_db_manager.get_tag_session() as session:
        assert session.query(tag_db_manager.AppliedSongChange).count() == 0


# ==================== the question ====================

def _answers(monkeypatch, *choices):
    queue = list(choices)

    class Prompt:
        def __init__(self, *a, **kw):
            pass

        def ask(self):
            return queue.pop(0)

    monkeypatch.setattr(first_run_prompt.questionary, "select", Prompt)
    return queue


def test_nothing_is_asked_when_the_databases_exist(db_dir, monkeypatch):
    db_dir.mkdir()
    (db_dir / "music.db").touch()
    (db_dir / "tag.db").touch()
    _answers(monkeypatch)  # any question would pop from an empty list

    assert first_run_prompt.ensure_databases()


def test_a_failed_download_asks_again_and_an_empty_start_follows(db_dir, monkeypatch, release, capsys):
    monkeypatch.setattr(first_run, "catalog_base_url", lambda repo=None: release["url"] + "/gone")
    left = _answers(monkeypatch, first_run_prompt.DOWNLOAD, first_run_prompt.EMPTY)

    assert first_run_prompt.ensure_databases()

    assert left == []
    assert "The download failed" in capsys.readouterr().out
    assert first_run.missing_databases() == []


def test_choosing_the_catalog_downloads_it(db_dir, monkeypatch, release):
    monkeypatch.setattr(first_run, "catalog_base_url", lambda repo=None: release["url"])
    _answers(monkeypatch, first_run_prompt.DOWNLOAD)

    assert first_run_prompt.ensure_databases()
    assert (db_dir / "music.db").read_bytes() == (release["folder"] / "music.db").read_bytes()


def test_quitting_creates_nothing(db_dir, monkeypatch):
    _answers(monkeypatch, first_run_prompt.QUIT)

    assert not first_run_prompt.ensure_databases()
    assert not db_dir.exists()


def test_only_a_missing_tag_db_is_created_empty_without_asking(db_dir, monkeypatch):
    first_run.create_empty_databases()
    tag_db_manager.ENGINE.dispose()  # no pooled connection to the removed file
    (db_dir / "tag.db").unlink()
    _answers(monkeypatch)

    assert first_run_prompt.ensure_databases()
    assert (db_dir / "tag.db").exists()
