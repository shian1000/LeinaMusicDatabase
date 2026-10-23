import json
import sys
from pathlib import Path
from types import SimpleNamespace

import types

import pytest
from googleapiclient.errors import HttpError

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.youtube import manage_youtube_playlists as m
from utils.youtube import yt_cache


def _fake_song(artist_name, title, youtube_video_id=None):
    artist = SimpleNamespace(name=artist_name, synonyms=None)
    return SimpleNamespace(artist=artist, title=title, language=None, youtube_video_id=youtube_video_id)


def _http_error(status, reason=None):
    content = json.dumps({"error": {"errors": [{"reason": reason}]}}).encode("utf-8") if reason else b"{}"
    return HttpError(SimpleNamespace(status=status, reason="error"), content)


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


@pytest.mark.parametrize("stderr, expected", [
    ("ERROR: [youtube] 49JVQEOkNmY: This video is not available", False),
    ("ERROR: [youtube] x: Private video. Sign in if you've been granted access to this video", False),
    # The bot wall after rate limiting says nothing about the video (real output, 2026-10-13)
    ("WARNING: [youtube] bqM-TDv73Bg: Unable to download webpage: HTTP Error 429: Too Many Requests\n"
     "ERROR: [youtube] bqM-TDv73Bg: Sign in to confirm you\u2019re not a bot. Use --cookies-from-browser", None),
    ("ERROR: [youtube] x: Video unavailable. This content isn't available, try again later.", None),
    # A 429 warning before a definite answer doesn't hide the answer
    ("WARNING: HTTP Error 429: Too Many Requests\nERROR: [youtube] x: This video is not available", False),
    ("ERROR: [youtube] x: Sign in to confirm your age.", None),
    ("ERROR: [youtube] x: The uploader has not made this video available in your country", None),
])
def test_video_playability_tells_gone_from_blocked(monkeypatch, stderr, expected):
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=1, stderr=stderr))
    assert m.video_playability("bqM-TDv73Bg") is expected
    # Only a definite "gone" may make a stored link look stale
    assert m.is_video_id_valid("bqM-TDv73Bg") is (expected is not False)


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
# add_video_to_playlist()
# ---------------------------------------------------------

def test_add_video_to_playlist_reraises_quota_exceeded(monkeypatch):
    def raise_quota_exceeded(part, body):
        raise _http_error(403, reason="quotaExceeded")

    youtube = SimpleNamespace(
        playlistItems=lambda: SimpleNamespace(
            insert=lambda part, body: SimpleNamespace(execute=lambda: raise_quota_exceeded(part, body))
        )
    )

    with pytest.raises(HttpError):
        m.add_video_to_playlist(youtube, "PLAYLIST1", "someid")


def test_add_video_to_playlist_returns_false_on_other_permanent_error():
    def raise_forbidden():
        raise _http_error(403, reason="forbidden")

    youtube = SimpleNamespace(
        playlistItems=lambda: SimpleNamespace(insert=lambda part, body: SimpleNamespace(execute=raise_forbidden))
    )

    assert m.add_video_to_playlist(youtube, "PLAYLIST1", "someid") is False


# ---------------------------------------------------------
# create_yt_playlist(): DB-link reuse/validation/persistence
# ---------------------------------------------------------

def _patch_playlist_plumbing(monkeypatch):
    """Stub out everything create_yt_playlist() needs besides the
    search/validate/persist logic under test: auth, playlist creation, and
    all cache disk I/O."""
    monkeypatch.setattr(m, "get_youtube_service", lambda: object())
    monkeypatch.setattr(m, "_load_bad_videos", lambda: None, raising=False)
    monkeypatch.setattr(m, "load_cache", lambda playlist_name: None)
    monkeypatch.setattr(m, "create_playlist", lambda youtube, title, description="": "PLAYLIST1")
    monkeypatch.setattr(yt_cache, "save_cache", lambda cache: None)
    monkeypatch.setattr(m, "save_cache", lambda cache: None)
    monkeypatch.setattr(m, "clear_cache", lambda playlist_name: None)

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


def test_create_yt_playlist_does_not_mark_added_when_add_to_playlist_fails(monkeypatch):
    added, committed = _patch_playlist_plumbing(monkeypatch)
    monkeypatch.setattr(m, "add_video_to_playlist", lambda youtube, playlist_id, video_id: False)
    monkeypatch.setattr(m, "search_video", lambda *a, **k: "foundid")

    saved_caches = []
    monkeypatch.setattr(m, "save_cache", lambda cache: saved_caches.append(json.loads(json.dumps(cache))))

    song = _fake_song("Some Artist", "Some Song")
    m.create_yt_playlist([song], "Test Playlist")

    key = yt_cache.make_song_key("Some Artist", "Some Song")
    # The video was found and persisted to the DB, but never actually
    # added to the playlist — the cache entry must stay unmarked so the
    # song is retried on the next run instead of being silently dropped
    # (see manage_youtube_playlists.py's create_yt_playlist()).
    assert saved_caches[-1]["songs"][key]["added"] is False
    assert song.youtube_video_id == "foundid"


def test_resuming_a_playlist_adds_the_songs_picked_now(monkeypatch):
    """YT-08: the resumed playlist used to get only the songs of the old
    cache, whatever was picked this time."""
    added, committed = _patch_playlist_plumbing(monkeypatch)
    monkeypatch.setattr(m, "is_video_id_valid", lambda video_id, timeout=20: True)
    monkeypatch.setattr(yt_cache, "save_cache", lambda cache: None)
    old = _fake_song("Queen", "Bicycle", youtube_video_id="OLD00000001")
    cache = yt_cache.init_cache("PLAYLIST1", "Test Playlist", [old])
    monkeypatch.setattr(m, "load_cache", lambda playlist_name: cache if playlist_name == "Test Playlist" else None)
    monkeypatch.setattr(m.questionary, "select",
                        lambda *a, **k: SimpleNamespace(ask=lambda: "Resume unfinished playlist"))
    monkeypatch.setattr(m, "create_playlist", lambda *a, **k: pytest.fail("resuming creates no playlist"))

    m.create_yt_playlist([_fake_song("Muse", "Uprising", youtube_video_id="NEW00000001")], "Test Playlist")

    assert added == ["NEW00000001"]


_REAL_CACHE = {name: getattr(yt_cache, name) for name in ("load_cache", "save_cache", "clear_cache")}


def test_a_new_playlist_leaves_another_unfinished_one_alone(monkeypatch, tmp_path):
    added, committed = _patch_playlist_plumbing(monkeypatch)
    monkeypatch.setattr(m, "is_video_id_valid", lambda video_id, timeout=20: True)
    monkeypatch.setattr(yt_cache, "CACHE_FILE", tmp_path / "yt_playlist_cache.json")
    # The real disk cache, which _patch_playlist_plumbing() stubbed out
    for name, function in _REAL_CACHE.items():
        monkeypatch.setattr(m, name, function)
        monkeypatch.setattr(yt_cache, name, function)
    yt_cache.init_cache("OTHER", "Unfinished", [_fake_song("Queen", "Bicycle")])

    m.create_yt_playlist([_fake_song("Muse", "Uprising", youtube_video_id="NEW00000001")], "Test Playlist")

    assert added == ["NEW00000001"]
    assert yt_cache.unfinished_playlists() == ["Unfinished"]
    assert yt_cache.load_cache("Unfinished")["playlist_id"] == "OTHER"


# ---------------------------------------------------------
# create_yt_playlist(): NO_VIDEO_SENTINEL ("N/A") - a confirmed "no video":
# left out without searching, never overwritten.
# ---------------------------------------------------------

def test_create_yt_playlist_leaves_out_a_song_marked_no_video_sentinel(monkeypatch):
    added, committed = _patch_playlist_plumbing(monkeypatch)

    def fail(*a, **k):
        raise AssertionError("a song marked N/A is neither validated nor searched")

    monkeypatch.setattr(m, "is_video_id_valid", fail)
    monkeypatch.setattr(m, "search_video", fail)

    song = _fake_song("Сербский Нож", "Рруа", youtube_video_id=m.NO_VIDEO_SENTINEL)
    m.create_yt_playlist([song], "Test Playlist")

    assert added == []
    assert committed == []
    assert song.youtube_video_id == m.NO_VIDEO_SENTINEL


# ---------------------------------------------------------
# create_yt_playlist(): videos in music.db's bad_youtube_videos
# ---------------------------------------------------------

def _bad_videos(*rows):
    from utils.database.inbox_requests import BadVideos
    return BadVideos([SimpleNamespace(video_id=v, song_id=s, reason="gone") for v, s in rows])


@pytest.mark.parametrize("bad_for_song", [None, 7])
def test_create_yt_playlist_never_adds_or_saves_a_rejected_video(monkeypatch, bad_for_song):
    added, committed = _patch_playlist_plumbing(monkeypatch)
    monkeypatch.setattr(m, "_load_bad_videos", lambda: _bad_videos(("rejectedid", bad_for_song)), raising=False)
    monkeypatch.setattr(m, "search_video", lambda *a, **k: "rejectedid")

    song = _fake_song("Some Artist", "Some Song")
    song.id = 7
    m.create_yt_playlist([song], "Test Playlist")

    assert added == []
    assert committed == []
    assert song.youtube_video_id is None


def test_create_yt_playlist_adds_a_video_rejected_only_for_another_song(monkeypatch):
    added, committed = _patch_playlist_plumbing(monkeypatch)
    monkeypatch.setattr(m, "_load_bad_videos", lambda: _bad_videos(("foundid", 99)), raising=False)
    monkeypatch.setattr(m, "search_video", lambda *a, **k: "foundid")

    song = _fake_song("Some Artist", "Some Song")
    song.id = 7
    m.create_yt_playlist([song], "Test Playlist")

    assert added == ["foundid"]
    assert song.youtube_video_id == "foundid"


# ---------------------------------------------------------
# create_yt_playlist(): the API quota used up while searching
# ---------------------------------------------------------

def test_a_used_up_search_quota_saves_the_progress_and_returns(monkeypatch):
    added, committed = _patch_playlist_plumbing(monkeypatch)
    saved_caches = []
    monkeypatch.setattr(m, "save_cache", lambda cache: saved_caches.append(json.loads(json.dumps(cache))))

    class _Search:
        def list(self, **kwargs):
            return SimpleNamespace(execute=lambda: (_ for _ in ()).throw(
                HttpError(SimpleNamespace(status=403, reason="quota"), b'{"reason": "quotaExceeded"}')))

    youtube = SimpleNamespace(search=lambda: _Search())
    monkeypatch.setattr(m, "get_youtube_service", lambda: youtube)
    monkeypatch.setattr(m, "search_video_ytdlp", lambda *a, **k: None)

    first, second = _fake_song("A", "One"), _fake_song("B", "Two")
    m.create_yt_playlist([first, second], "Test Playlist")  # returns instead of SystemExit

    assert added == []
    assert saved_caches and not any(e["added"] for e in saved_caches[-1]["songs"].values())


# ---------------------------------------------------------
# create_yt_playlist(): clearing a dead stored link nothing replaces
# ---------------------------------------------------------

def test_create_yt_playlist_clears_stale_link_when_replacement_search_finds_nothing(monkeypatch):
    added, committed = _patch_playlist_plumbing(monkeypatch)
    monkeypatch.setattr(m, "is_video_id_valid", lambda video_id, timeout=20: False)
    monkeypatch.setattr(m, "search_video", lambda *a, **k: None)

    saved_caches = []
    monkeypatch.setattr(m, "save_cache", lambda cache: saved_caches.append(json.loads(json.dumps(cache))))

    song = _fake_song("Some Artist", "Some Song", youtube_video_id="deadvideoid")
    m.create_yt_playlist([song], "Test Playlist")

    key = yt_cache.make_song_key("Some Artist", "Some Song")
    assert added == []
    assert committed == [True]
    assert song.youtube_video_id is None
    assert saved_caches[-1]["songs"][key]["video_id"] is None


# ---------------------------------------------------------
# get_video_album()
# ---------------------------------------------------------

def test_get_video_album_reads_an_official_uploads_metadata(monkeypatch):
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: FakeCompletedProcess(
        stdout="Ґрунт\tГлюки\tZlypni, Данило Грицак\n"))
    assert m.get_video_album("TSEjLo_hlgk") == ("Ґрунт", "Глюки", "Zlypni, Данило Грицак")


def test_get_video_album_none_for_a_video_without_album(monkeypatch):
    # yt-dlp prints "NA" for every field of an ordinary (non "Provided to YouTube") video
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: FakeCompletedProcess(stdout="NA\tNA\tNA\n"))
    assert m.get_video_album("E3c2yvV1qUU") is None


def test_get_video_album_skips_no_video_sentinel_and_failures(monkeypatch):
    calls = []
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: calls.append(a) or FakeCompletedProcess(
        returncode=1, stderr="ERROR: Video unavailable"))
    assert m.get_video_album(m.NO_VIDEO_SENTINEL) is None
    assert calls == []
    assert m.get_video_album("deadbeef123") is None


# ---------------------------------------------------------
# videos_exist_via_api()
# ---------------------------------------------------------

class _FakeVideos:
    def __init__(self, items, calls):
        self.items, self.calls = items, calls

    def list(self, part, id, maxResults):
        self.calls.append(id.split(","))
        items = [{"id": v, "status": self.items[v].get("status", self.items[v]),
                  "contentDetails": self.items[v].get("contentDetails", {})}
                 for v in id.split(",") if v in self.items]
        return types.SimpleNamespace(execute=lambda: {"items": items})


def _fake_service(monkeypatch, items):
    calls = []
    service = types.SimpleNamespace(videos=lambda: _FakeVideos(items, calls))
    monkeypatch.setattr(m, "_read_only_youtube_service", lambda: service)
    return calls


def test_videos_exist_via_api_reads_status(monkeypatch):
    calls = _fake_service(monkeypatch, {
        "public00000": {"privacyStatus": "public", "uploadStatus": "processed"},
        "unlisted000": {"privacyStatus": "unlisted", "uploadStatus": "processed"},
        "private0000": {"privacyStatus": "private", "uploadStatus": "processed"},
        "rejected000": {"privacyStatus": "public", "uploadStatus": "rejected"},
    })

    result = m.videos_exist_via_api(
        ["public00000", "unlisted000", "private0000", "rejected000", "missing0000", "public00000", "N/A", None])

    assert result == {"public00000": True, "unlisted000": True, "private0000": False,
                      "rejected000": False, "missing0000": False}
    assert len(calls) == 1  # deduplicated, one call


def test_videos_exist_via_api_region_blocks(monkeypatch):
    public = {"privacyStatus": "public", "uploadStatus": "processed"}
    _fake_service(monkeypatch, {
        # Real case (2026-10-13): a dead Topic upload blocked nearly everywhere
        "49JVQEOkNmY": {"status": {**public, "privacyStatus": "unlisted"},
                        "contentDetails": {"regionRestriction": {"blocked": ["DE", "PL", "US"]}}},
        "bqM-TDv73Bg": {"status": public, "contentDetails": {"regionRestriction": {"allowed": ["DE", "PL"]}}},
        "notforpl000": {"status": public, "contentDetails": {"regionRestriction": {"allowed": ["US"]}}},
        "blockedelse": {"status": public, "contentDetails": {"regionRestriction": {"blocked": ["US"]}}},
    })
    monkeypatch.setattr(m, "YOUTUBE_REGION", "PL")

    assert m.videos_exist_via_api(["49JVQEOkNmY", "bqM-TDv73Bg", "notforpl000", "blockedelse"]) == {
        "49JVQEOkNmY": False, "bqM-TDv73Bg": True, "notforpl000": False, "blockedelse": True,
    }


def test_videos_exist_via_api_batches_by_fifty(monkeypatch):
    calls = _fake_service(monkeypatch, {})
    m.videos_exist_via_api([f"v{i:010d}" for i in range(120)])
    assert [len(c) for c in calls] == [50, 50, 20]


def test_videos_exist_via_api_none_without_service_or_on_error(monkeypatch):
    monkeypatch.setattr(m, "_read_only_youtube_service", lambda: None)
    assert m.videos_exist_via_api(["public00000"]) is None

    def broken():
        raise RuntimeError("quota")
    monkeypatch.setattr(m, "_read_only_youtube_service",
                        lambda: types.SimpleNamespace(videos=lambda: types.SimpleNamespace(list=lambda **k: broken())))
    assert m.videos_exist_via_api(["public00000"]) is None


# ==================== YouTube login (YT-07) ====================

class _Creds:
    def __init__(self, valid=False, expired=True, refresh_token="r", client_id="client", refresh_error=None):
        self.valid, self.expired, self.refresh_token = valid, expired, refresh_token
        self.client_id, self.refresh_error = client_id, refresh_error

    def refresh(self, request):
        if self.refresh_error:
            raise self.refresh_error
        self.valid = True

    def to_json(self):
        return '{"token": "t"}'


@pytest.fixture
def login(tmp_path, monkeypatch):
    """A token file and a fake OAuth flow; `flows` counts browser logins."""
    token = tmp_path / "token.json"
    token.write_text("{}")
    state = {"creds": _Creds(), "flows": 0}

    def load(path, scopes):
        if isinstance(state["creds"], Exception):
            raise state["creds"]
        return state["creds"]

    class Flow:
        @staticmethod
        def from_client_secrets_file(path, scopes):
            return Flow()

        def run_local_server(self, port):
            state["flows"] += 1
            return _Creds(valid=True)

    monkeypatch.setattr(m, "TOKEN_PATH", str(token))
    monkeypatch.setattr(m, "_oauth_client_id", lambda: "client")
    monkeypatch.setattr(m.Credentials, "from_authorized_user_file", load)
    monkeypatch.setattr(m, "InstalledAppFlow", Flow)
    monkeypatch.setattr(m, "build", lambda *args, **kwargs: "service")
    for name in ("SSH_CONNECTION", "SSH_TTY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    state["token"] = token
    return state


def test_a_network_error_while_refreshing_doesnt_open_a_login(login):
    """Before: any refresh error, a dropped connection too, started the
    browser login - which hangs over ssh."""
    from google.auth.exceptions import TransportError
    login["creds"] = _Creds(refresh_error=TransportError("Name or service not known"))

    with pytest.raises(m.YouTubeLoginError, match="Can't reach Google"):
        m.get_youtube_service()
    assert login["flows"] == 0


def test_a_revoked_token_logs_in_again(login):
    from google.auth.exceptions import RefreshError
    login["creds"] = _Creds(refresh_error=RefreshError("invalid_grant"))

    assert m.get_youtube_service() == "service"
    assert login["flows"] == 1
    assert login["token"].stat().st_mode & 0o777 == 0o600


def test_a_refreshed_token_needs_no_login(login):
    assert m.get_youtube_service() == "service"
    assert login["flows"] == 0


def test_an_unreadable_token_file_logs_in_again(login):
    """Before: the ValueError from a broken token.json crashed the menu."""
    login["creds"] = ValueError("Expecting value: line 1 column 1")

    assert m.get_youtube_service() == "service"
    assert login["flows"] == 1


def test_no_browser_login_over_ssh_without_a_display(login, monkeypatch):
    from google.auth.exceptions import RefreshError
    login["creds"] = _Creds(refresh_error=RefreshError("invalid_grant"))
    monkeypatch.delenv("DISPLAY")
    monkeypatch.setenv("SSH_CONNECTION", "192.0.2.10 50000 192.0.2.20 22")

    with pytest.raises(m.YouTubeLoginError, match="over ssh"):
        m.get_youtube_service()
    assert login["flows"] == 0
