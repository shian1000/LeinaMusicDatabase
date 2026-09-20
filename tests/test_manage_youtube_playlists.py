import sys
from pathlib import Path
from types import SimpleNamespace

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.youtube import manage_youtube_playlists as m
from utils.youtube import yt_cache


def _fake_song(artist_name, title, youtube_video_id=None):
    artist = SimpleNamespace(name=artist_name, synonyms=None)
    return SimpleNamespace(artist=artist, title=title, language=None, youtube_video_id=youtube_video_id)


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ---------------------------------------------------------
# is_video_id_valid()
# ---------------------------------------------------------

def test_is_video_id_valid_true_when_ytdlp_succeeds(monkeypatch):
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=0))
    assert m.is_video_id_valid("bLOSjREikDc") is True


def test_is_video_id_valid_false_when_ytdlp_reports_error(monkeypatch):
    monkeypatch.setattr(
        m.subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=1, stderr="ERROR: Video unavailable")
    )
    assert m.is_video_id_valid("deadbeef123") is False


def test_is_video_id_valid_false_for_empty_id():
    assert m.is_video_id_valid(None) is False
    assert m.is_video_id_valid("") is False


def test_is_video_id_valid_assumes_valid_when_ytdlp_unavailable(monkeypatch):
    def raise_missing(*a, **k):
        raise FileNotFoundError("yt-dlp not found")

    monkeypatch.setattr(m.subprocess, "run", raise_missing)
    # Can't tell whether the link is actually stale if yt-dlp itself can't
    # run — trust it rather than force a redundant search on every
    # environment where yt-dlp happens to be missing.
    assert m.is_video_id_valid("bLOSjREikDc") is True


# ---------------------------------------------------------
# save_video_id_to_song()
# ---------------------------------------------------------

def test_save_video_id_to_song_persists_and_commits(monkeypatch):
    committed = []
    monkeypatch.setattr(m, "submit_global_database_session", lambda: committed.append(True))

    song = _fake_song("Artist", "Title")
    m.save_video_id_to_song(song, "newid123")

    assert song.youtube_video_id == "newid123"
    assert committed == [True]


def test_save_video_id_to_song_is_noop_when_unchanged(monkeypatch):
    committed = []
    monkeypatch.setattr(m, "submit_global_database_session", lambda: committed.append(True))

    song = _fake_song("Artist", "Title", youtube_video_id="sameid")
    m.save_video_id_to_song(song, "sameid")

    assert committed == []


# ---------------------------------------------------------
# create_yt_playlist(): DB-link reuse/validation/persistence
# ---------------------------------------------------------

def _patch_playlist_plumbing(monkeypatch):
    """Stub out everything create_yt_playlist() needs besides the
    search/validate/persist logic under test: auth, playlist creation, and
    all cache disk I/O."""
    monkeypatch.setattr(m, "get_youtube_service", lambda: object())
    monkeypatch.setattr(m, "load_cache", lambda: None)
    monkeypatch.setattr(m, "create_playlist", lambda youtube, title, description="": "PLAYLIST1")
    monkeypatch.setattr(yt_cache, "save_cache", lambda cache: None)
    monkeypatch.setattr(m, "save_cache", lambda cache: None)
    monkeypatch.setattr(m, "clear_cache", lambda: None)

    added = []
    monkeypatch.setattr(
        m, "add_video_to_playlist", lambda youtube, playlist_id, video_id: added.append(video_id) or True
    )

    committed = []
    monkeypatch.setattr(m, "submit_global_database_session", lambda: committed.append(True))

    return added, committed


def test_create_yt_playlist_uses_valid_stored_link_without_searching(monkeypatch):
    added, committed = _patch_playlist_plumbing(monkeypatch)
    monkeypatch.setattr(m, "is_video_id_valid", lambda video_id, timeout=20: True)

    def fail_if_searched(*a, **k):
        raise AssertionError("search_video should not run when the stored DB link is valid")

    monkeypatch.setattr(m, "search_video", fail_if_searched)

    song = _fake_song("Taco Hemingway", "Fuck Your List", youtube_video_id="bLOSjREikDc")
    m.create_yt_playlist([song], "Test Playlist")

    assert added == ["bLOSjREikDc"]
    assert committed == []  # link was already correct, nothing new to persist
    assert song.youtube_video_id == "bLOSjREikDc"


def test_create_yt_playlist_falls_back_to_search_when_stored_link_invalid(monkeypatch):
    added, committed = _patch_playlist_plumbing(monkeypatch)
    monkeypatch.setattr(m, "is_video_id_valid", lambda video_id, timeout=20: False)
    monkeypatch.setattr(m, "search_video", lambda *a, **k: "freshvideoid")

    song = _fake_song("Some Artist", "Some Song", youtube_video_id="stalevideoid")
    m.create_yt_playlist([song], "Test Playlist")

    assert added == ["freshvideoid"]
    assert song.youtube_video_id == "freshvideoid"
    assert committed == [True]


def test_create_yt_playlist_searches_and_persists_when_no_stored_link(monkeypatch):
    added, committed = _patch_playlist_plumbing(monkeypatch)

    def fail_if_validated(*a, **k):
        raise AssertionError("is_video_id_valid should not run when there's no stored link")

    monkeypatch.setattr(m, "is_video_id_valid", fail_if_validated)
    monkeypatch.setattr(m, "search_video", lambda *a, **k: "foundid")

    song = _fake_song("Another Artist", "Another Song")
    m.create_yt_playlist([song], "Test Playlist")

    assert added == ["foundid"]
    assert song.youtube_video_id == "foundid"
    assert committed == [True]


def test_create_yt_playlist_does_not_persist_when_search_finds_nothing(monkeypatch):
    added, committed = _patch_playlist_plumbing(monkeypatch)
    monkeypatch.setattr(m, "search_video", lambda *a, **k: None)

    song = _fake_song("Nobody", "Unfindable Song")
    m.create_yt_playlist([song], "Test Playlist")

    assert added == []
    assert committed == []
    assert song.youtube_video_id is None
