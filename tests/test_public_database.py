"""The public copy of music.db / tag.db (utils/database/public_export.py) and
its publishing as a GitHub Release (public_database.py, github_release.py):
the allow-list export, the self-check that stops a publish (extra or user
tables, withheld values, markers, local paths, integrity), "only when
changed", the upload order (version.json last) and the failures that must
publish nothing. GitHub is a fake HTTP server on localhost."""

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from utils.database import public_database as pd
from utils.database import public_export as pe
from utils.database.github_release import GithubReleaseClient

repo_root = Path(__file__).resolve().parents[1]
TOKEN = "github_pat_secret_value_123"


def _run_migrations(path: Path, track: str) -> None:
    conn = sqlite3.connect(str(path))
    for sql in sorted((repo_root / "migrations" / track).glob("*.sql")):
        conn.executescript(sql.read_text())
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "filename TEXT UNIQUE NOT NULL, applied_at TEXT NOT NULL DEFAULT (datetime('now')))")
    conn.execute("INSERT INTO schema_version (filename) VALUES ('0001_baseline.sql')")
    conn.commit()
    conn.close()


@pytest.fixture
def sources(tmp_path):
    music, tag = tmp_path / "src" / "music.db", tmp_path / "src" / "tag.db"
    music.parent.mkdir()
    _run_migrations(music, "music")
    _run_migrations(tag, "tag")
    conn = sqlite3.connect(str(music))
    with conn:
        conn.execute("INSERT INTO artists (id, name, origin) VALUES (1, 'AC/DC', 'Australia')")
        conn.execute("INSERT INTO songs (id, title, artist_id, album, spotify_url, youtube_video_id) VALUES "
                     "(1, 'Re:Zero', 1, 'Back in Black', 'https://open.spotify.com/track/abc', 'dQw4w9WgXcQ')")
        conn.execute("INSERT INTO albums (id, title, artist_id) VALUES (1, 'Back in Black', 1)")
        conn.execute("INSERT INTO album_covers (album_id, source, path, status) "
                     "VALUES (1, 'file', '/home/someone/covers/1.jpg', 'found')")
    conn.close()
    conn = sqlite3.connect(str(tag))
    with conn:
        conn.execute("INSERT INTO tags (id, name, kind) VALUES (1, 'spellchecked', 'marker'), (2, 'Chill', 'mood')")
        conn.execute("INSERT INTO song_tags (song_id, tag_id) VALUES (1, 1), (1, 2)")
    conn.close()
    return {"music": music, "tag": tag}


def _rows(path, sql):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _tables(path):
    return {r[0] for r in _rows(path, "SELECT name FROM sqlite_master WHERE type = 'table'")}


# ==================== export ====================

def test_export_keeps_the_allow_list_and_empties_the_rest(sources, tmp_path):
    conn = sqlite3.connect(str(sources["music"]))
    with conn:
        # Something a later migration might add: a new table and a new column
        conn.execute("CREATE TABLE lyrics (song_id INTEGER, text TEXT)")
        conn.execute("INSERT INTO lyrics VALUES (1, 'la la')")
        conn.execute("ALTER TABLE songs ADD COLUMN lyrics TEXT")
        conn.execute("UPDATE songs SET lyrics = 'la la'")
        conn.execute("CREATE TABLE playlists (id INTEGER)")
    conn.close()
    before = sources["music"].read_bytes()
    out = tmp_path / "out"
    out.mkdir()

    export = pe.export_public_databases(sources, out)

    music, tag = out / "music.db", out / "tag.db"
    assert [f.name for f in export.files] == ["music.db", "tag.db"]
    assert "lyrics" not in _tables(music) and "playlists" not in _tables(music)
    # The column stays (same schema as the server's copy) but holds nothing
    assert _rows(music, "SELECT title, lyrics, spotify_url FROM songs") == [
        ("Re:Zero", None, "https://open.spotify.com/track/abc")]
    assert _rows(music, "SELECT path FROM album_covers") == [(None,)]
    assert {"songs", "artists", "albums", "additional_song_artists", "song_id_changes", "bad_youtube_videos",
            "rejected_song_requests", "album_covers", "schema_version"} <= _tables(music)
    assert _rows(tag, "SELECT name, kind FROM tags") == [("Chill", "mood")]
    assert _rows(tag, "SELECT song_id, tag_id FROM song_tags") == [(1, 2)]
    assert sources["music"].read_bytes() == before  # the original is only read
    assert pe.check_public_database(music, pe.MUSIC_SPEC) == []


def test_dropped_mood_flags_are_gone_after_the_migrations(sources):
    columns = {r[1] for r in _rows(sources["music"], "PRAGMA table_info(songs)")}
    assert not {"nostalgic", "melancholic", "party"} & columns


def test_a_not_null_column_nobody_listed_fails_the_export(sources, tmp_path):
    conn = sqlite3.connect(str(sources["music"]))
    with conn:
        conn.execute("ALTER TABLE artists ADD COLUMN secret TEXT NOT NULL DEFAULT 'x'")
    conn.close()
    with pytest.raises(pe.ExportError, match="artists.secret"):
        pe.export_public_databases(sources, tmp_path)


def test_same_data_gives_the_same_digest_and_a_change_a_new_one(sources, tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "c").mkdir()
    first = pe.export_public_databases(sources, tmp_path / "a")
    second = pe.export_public_databases(sources, tmp_path / "b")
    assert [f.content_sha256 for f in first.files] == [f.content_sha256 for f in second.files]
    conn = sqlite3.connect(str(sources["music"]))
    with conn:
        conn.execute("UPDATE songs SET year = 1980")
    conn.close()
    third = pe.export_public_databases(sources, tmp_path / "c")
    assert third.files[0].content_sha256 != first.files[0].content_sha256
    assert third.files[1].content_sha256 == first.files[1].content_sha256


# ==================== the self-check ====================

@pytest.fixture
def built(sources, tmp_path):
    out = tmp_path / "built"
    out.mkdir()
    pe.export_public_databases(sources, out)
    return out


def _change(path, *sql):
    conn = sqlite3.connect(str(path))
    with conn:
        for statement in sql:
            conn.execute(statement)
    conn.close()


def test_check_refuses_a_table_not_on_the_allow_list(built):
    _change(built / "music.db", "CREATE TABLE extra (x)")
    assert any("table extra isn't on the allow-list" in p for p in pe.check_public_database(built / "music.db", pe.MUSIC_SPEC))


def test_check_names_a_users_table(built):
    _change(built / "tag.db", "CREATE TABLE listens (x)")
    problems = pe.check_public_database(built / "tag.db", pe.TAG_SPEC)
    assert any("listens" in p and "a user's" in p for p in problems)


def test_check_refuses_files_db_and_views(built):
    _change(built / "music.db", "CREATE TABLE files (path TEXT)", "CREATE VIEW v AS SELECT 1")
    problems = pe.check_public_database(built / "music.db", pe.MUSIC_SPEC)
    assert any("table files" in p for p in problems) and any("view v" in p for p in problems)


def test_check_refuses_a_withheld_value(built):
    _change(built / "music.db", "UPDATE album_covers SET path = 'covers/1.jpg'")
    assert any("album_covers.path is withheld" in p for p in pe.check_public_database(built / "music.db", pe.MUSIC_SPEC))


def test_check_refuses_markers(built):
    _change(built / "tag.db", "INSERT INTO tags (id, name, kind) VALUES (9, 'album_checked', 'marker')")
    assert any("tags has 1 row(s)" in p for p in pe.check_public_database(built / "tag.db", pe.TAG_SPEC))


@pytest.mark.parametrize("value", [
    "/home/someone/Music/a.mp3", "x /media/drive/Shared", "smb://nas/music", "file:///tmp/a", "C:\\Users\\a",
    "d:/music", "\\\\server\\share\\a", "~/Music",
])
def test_check_refuses_a_value_looking_like_a_local_path(built, value):
    _change(built / "music.db", f"UPDATE artists SET synonyms = '{value}'")
    problems = pe.check_public_database(built / "music.db", pe.MUSIC_SPEC, local_paths=[])
    assert problems == ["music.db: artists.synonyms has a value that looks like a local path"]
    assert value not in problems[0]


def test_check_refuses_this_machines_own_folder(built):
    _change(built / "music.db", "UPDATE songs SET album = 'stuff in /srvdata/library/x'")
    problems = pe.check_public_database(built / "music.db", pe.MUSIC_SPEC, local_paths=["/srvdata/library"])
    assert problems == ["music.db: songs.album has a value that looks like a local path"]


@pytest.mark.parametrize("value", ["AC/DC", "Re:Zero", "https://open.spotify.com/album/x", "Love/Hate (Remix)",
                                   "Part 1: Intro", "24/7", "N/A"])
def test_ordinary_text_isnt_a_path(value):
    assert not pe.looks_like_local_path(value)


def test_check_reports_a_broken_file(built):
    path = built / "music.db"
    data = bytearray(path.read_bytes())
    page = int.from_bytes(data[16:18], "big")
    for start in range(page, len(data), page):
        data[start:start + 8] = b"\x0d\xff\xff\xff\xff\xff\xff\xff"  # garble every page header but the first
    path.write_bytes(bytes(data))
    assert pe.check_public_database(path, pe.MUSIC_SPEC) != []


def test_a_failed_check_raises_and_says_why(sources, tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "_local_path_strings", lambda: ["Back in Black"])
    with pytest.raises(pe.ExportError, match="songs.album has a value that looks like a local path"):
        pe.export_public_databases(sources, tmp_path)


# ==================== a fake GitHub ====================

class FakeGithub:
    def __init__(self):
        self.releases = {}
        self.assets = {}
        self.next_id = 100
        self.log = []  # (method, what)
        self.auth = []
        self.fail_status = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def release_json(self, rid):
        release = self.releases[rid]
        return {"id": rid, "tag_name": release["tag"], "html_url": f"{self.url}/release/{rid}",
                "upload_url": f"{self.url}/uploads/{rid}/assets{{?name,label}}",
                "assets": [{"id": aid, "name": a["name"]} for aid, a in self.assets.items() if a["release"] == rid]}

    def asset(self, name):
        return next((a for a in self.assets.values() if a["name"] == name), None)

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload=None, raw=None):
                body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else b"")
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def _handle(self, method):
                fake.auth.append(self.headers.get("Authorization"))
                path = urlsplit(self.path)
                body = self._body()
                if fake.fail_status:
                    return self._send(fake.fail_status, {"message": "nope"})
                parts = path.path.strip("/").split("/")
                if method == "GET" and parts[:5] == ["repos", "o", "r", "releases", "tags"]:
                    rid = next((i for i, r in fake.releases.items() if r["tag"] == parts[5]), None)
                    return self._send(404, {"message": "Not Found"}) if rid is None else self._send(200, fake.release_json(rid))
                if method == "POST" and parts == ["repos", "o", "r", "releases"]:
                    data = json.loads(body)
                    rid = len(fake.releases) + 1
                    fake.releases[rid] = {"tag": data["tag_name"], "make_latest": data.get("make_latest")}
                    fake.log.append(("create", data["tag_name"]))
                    return self._send(201, fake.release_json(rid))
                if parts[:4] == ["repos", "o", "r", "releases"] and parts[4] == "assets":
                    aid = int(parts[5])
                    if method == "GET":
                        return self._send(200, raw=fake.assets[aid]["data"])
                    if method == "DELETE":
                        fake.log.append(("delete", fake.assets.pop(aid)["name"]))
                        return self._send(204)
                    if method == "PATCH":
                        new = json.loads(body)["name"]
                        fake.log.append(("rename", f"{fake.assets[aid]['name']}->{new}"))
                        fake.assets[aid]["name"] = new
                        return self._send(200, {"id": aid, "name": new})
                if method == "POST" and parts[0] == "uploads":
                    name = parse_qs(path.query)["name"][0]
                    if fake.asset(name):
                        return self._send(422, {"message": "already_exists"})
                    fake.next_id += 1
                    fake.assets[fake.next_id] = {"name": name, "release": int(parts[1]), "data": body}
                    fake.log.append(("upload", name))
                    return self._send(201, {"id": fake.next_id, "name": name})
                return self._send(404, {"message": "Not Found"})

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_PATCH(self):
                self._handle("PATCH")

            def do_DELETE(self):
                self._handle("DELETE")

        return Handler


@pytest.fixture
def github(tmp_path, monkeypatch):
    fake = FakeGithub()
    monkeypatch.setattr(pd, "PUBLIC_DB_STATE_FILE", str(tmp_path / "state" / "public_database.json"))
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


def _client(fake):
    return GithubReleaseClient("o/r", TOKEN, api_url=fake.url)


def test_first_publish_creates_the_release_and_uploads_version_json_last(github, sources):
    result = pd.publish(client=_client(github), sources=sources)

    assert result.status == pd.PUBLISHED
    assert github.log[0] == ("create", "database")
    assert next(iter(github.releases.values()))["make_latest"] == "false"
    uploads = [what for kind, what in github.log if kind == "upload"]
    assert uploads == ["music.db.uploading", "tag.db.uploading", "version.json.uploading"]
    assert github.log[-1] == ("rename", "version.json.uploading->version.json")
    assert sorted(a["name"] for a in github.assets.values()) == ["music.db", "tag.db", "version.json"]
    version = json.loads(github.asset("version.json")["data"])
    assert version["format"] == 1 and version["published_at"].endswith("Z")
    music = github.asset("music.db")["data"]
    import hashlib
    assert version["files"]["music.db"]["size"] == len(music)
    assert version["files"]["music.db"]["sha256"] == hashlib.sha256(music).hexdigest()
    assert music.startswith(b"SQLite format 3\x00")
    assert set(github.auth) == {f"Bearer {TOKEN}"}
    assert pd.last_result()["status"] == pd.PUBLISHED


def test_unchanged_catalog_uploads_nothing(github, sources):
    pd.publish(client=_client(github), sources=sources)
    github.log.clear()

    result = pd.publish(client=_client(github), sources=sources)

    assert result.status == pd.UNCHANGED
    assert github.log == []


def test_a_change_is_published_and_force_publishes_anyway(github, sources):
    pd.publish(client=_client(github), sources=sources)
    _change(sources["tag"], "INSERT INTO tags (id, name, kind) VALUES (3, 'Rain', 'mood')")
    github.log.clear()
    assert pd.publish(client=_client(github), sources=sources).status == pd.PUBLISHED
    assert ("delete", "tag.db") in github.log
    github.log.clear()
    assert pd.publish(client=_client(github), sources=sources).status == pd.UNCHANGED
    assert pd.publish(force=True, client=_client(github), sources=sources).status == pd.PUBLISHED


def test_a_database_deleted_from_the_release_is_published_again(github, sources):
    pd.publish(client=_client(github), sources=sources)
    aid = next(i for i, a in github.assets.items() if a["name"] == "music.db")
    del github.assets[aid]
    assert pd.publish(client=_client(github), sources=sources).status == pd.PUBLISHED
    assert github.asset("music.db") is not None


def test_a_failed_check_publishes_nothing(github, sources, monkeypatch):
    monkeypatch.setattr(pe, "_local_path_strings", lambda: ["Back in Black"])
    result = pd.publish(client=_client(github), sources=sources)
    assert result.status == pd.FAILED and "nothing was published" in result.message
    assert github.log == [] and github.auth == []


def test_github_refusing_the_token_is_a_clear_failure_without_the_token(github, sources):
    github.fail_status = 401
    result = pd.publish(client=_client(github), sources=sources)
    assert result.status == pd.FAILED and "refused the token" in result.message
    assert TOKEN not in json.dumps(pd._load_state())


def test_no_network_is_a_failure_not_a_crash(sources, tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "PUBLIC_DB_STATE_FILE", str(tmp_path / "state.json"))
    client = GithubReleaseClient("o/r", TOKEN, api_url="http://127.0.0.1:9")
    result = pd.publish(client=client, sources=sources)
    assert result.status == pd.FAILED and "can't reach GitHub" in result.message


def test_not_set_up_is_skipped_naming_the_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "PUBLIC_DB_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(pd.Settings, "github_db_repo", "")
    monkeypatch.setattr(pd.Settings, "github_db_token", "")
    result = pd.publish()
    assert result.status == pd.SKIPPED and "GITHUB_DB_REPO and GITHUB_DB_TOKEN" in result.message


def test_daily_publish_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "PUBLIC_DB_STATE_FILE", str(tmp_path / "state.json"))
    assert pd.daily_publish_enabled()  # on by default
    pd.set_daily_publish(False)
    monkeypatch.setattr(pd, "publish", lambda **k: pytest.fail("published while switched off"))
    assert pd.publish_daily() is None


def test_daily_publish_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "PUBLIC_DB_STATE_FILE", str(tmp_path / "state.json"))

    def broken(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pd, "publish", broken)
    assert pd.publish_daily().status == pd.FAILED
    assert pd.last_result()["status"] == pd.FAILED
