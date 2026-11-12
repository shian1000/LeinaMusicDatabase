"""Settings -> "Debug log" and the import's trace: the .debug file read and
written (verbosity, console), slog() / mlog() reaching debug.log with a
timestamp only from their verbosity, and utils.common.trace counting and
logging phases, SQL statements, YouTube API requests, yt-dlp runs and
MusicBrainz requests - also when the call fails."""

import re
import subprocess
from types import SimpleNamespace
from unittest import mock

import pytest
from sqlalchemy import create_engine, text

from utils.common import debug, musicbrainz_client, trace
from utils.youtube import import_from_playlist as ifp
from utils.youtube import manage_youtube_playlists as m

_STAMP = re.compile(r"^\*\*\*Debug\*\*\* \[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}\] ")


@pytest.fixture
def log(tmp_path, monkeypatch):
    """debug.log in tmp_path, at the verbosity the test sets with log.at(n)."""
    path = tmp_path / "debug.log"
    monkeypatch.setattr(debug, "DEBUG_LOG_FILE", path)
    monkeypatch.setattr(debug, "VERBOSITY_LEVEL", 0)
    monkeypatch.setattr(debug, "DEBUG_ENABLED", False)

    class Log:
        def at(self, verbosity):
            debug.VERBOSITY_LEVEL = verbosity

        def lines(self):
            return path.read_text().splitlines() if path.exists() else []

    return Log()


# ==================== the .debug file ====================

@pytest.mark.parametrize("content, expected", [
    (None, (0, False)),
    ("verbosity = 2\n", (2, True)),  # a bare .debug always printed - it still does
    ("verbosity = 1\nconsole = off\n", (1, False)),
    ("console = on\n", (0, True)),
    ("verbosity = 9\n", (3, True)),
])
def test_the_debug_file_is_read(tmp_path, content, expected):
    path = tmp_path / ".debug"
    if content is not None:
        path.write_text(content)
    assert debug.read_debug_settings(path) == expected


def test_saved_settings_are_used_at_once_and_read_back(tmp_path, monkeypatch):
    monkeypatch.setattr(debug, "VERBOSITY_LEVEL", 0)
    monkeypatch.setattr(debug, "DEBUG_ENABLED", False)
    path = tmp_path / ".debug"

    debug.save_debug_settings(2, False, path)

    assert debug.read_debug_settings(path) == (2, False)
    assert (debug.VERBOSITY_LEVEL, debug.DEBUG_ENABLED) == (2, False)
    debug.save_debug_settings(0, False, path)
    assert not path.exists()
    assert (debug.VERBOSITY_LEVEL, debug.DEBUG_ENABLED) == (0, False)


def test_a_verbosity_out_of_range_is_refused(tmp_path):
    with pytest.raises(ValueError):
        debug.save_debug_settings(4, False, tmp_path / ".debug")
    assert not (tmp_path / ".debug").exists()


def test_the_tests_never_use_the_real_debug_file():
    assert debug.DEBUG_FILE.parent != debug.PROJECT_ROOT


# ==================== slog() / mlog() ====================

def test_mlog_reaches_debug_log_only_from_its_verbosity(log, capsys):
    debug.mlog("detail", priority=1)
    assert log.lines() == []

    log.at(2)
    debug.mlog("\n[1/2] detail", priority=1)
    debug.mlog("everything", priority=0)

    [line] = log.lines()
    assert _STAMP.match(line) and line.endswith("] [1/2] detail")
    assert capsys.readouterr().out == ""  # the screen only with console = on


def test_mlog_prints_too_with_the_console_on(log, capsys, monkeypatch):
    log.at(1)
    monkeypatch.setattr(debug, "DEBUG_ENABLED", True)
    debug.mlog("phase", priority=2)
    assert "phase" in capsys.readouterr().out
    assert len(log.lines()) == 1


def test_slog_lines_carry_a_timestamp(log):
    log.at(3)
    value = 42
    debug.slog(value)
    [line] = log.lines()
    assert _STAMP.match(line) and "value (int): 42" in line


# ==================== utils.common.trace ====================

def test_a_phase_logs_its_time_and_the_sql_it_ran(log):
    log.at(1)
    engine = create_engine("sqlite://")
    with trace.phase("Demo"):
        with engine.connect() as conn:
            conn.execute(text("SELECT 1")).all()
            conn.execute(text("SELECT 2")).all()

    start, end = log.lines()
    assert start.endswith("[Phase] Demo - start")
    assert re.search(r"\[Phase\] Demo - \d+\.\d\ds: SQL 2 \(", end)


def test_nothing_is_logged_at_verbosity_0(log):
    with trace.phase("Demo"):
        trace.api_call(mock.Mock(execute=lambda: {"items": []}), "videos.list")
    assert log.lines() == []


def test_slow_sql_is_logged_from_verbosity_2(log, monkeypatch):
    monkeypatch.setattr(trace, "DEBUG_SLOW_SQL_SECONDS", 0)
    engine = create_engine("sqlite://")
    log.at(1)
    with engine.connect() as conn:
        conn.execute(text("SELECT 'one'")).all()
    assert log.lines() == []

    log.at(2)
    with engine.connect() as conn:
        conn.execute(text("SELECT 'two'")).all()
    assert any("[SQL] slow" in line and "SELECT 'two'" in line for line in log.lines())


def test_youtube_api_requests_are_counted_and_logged_also_when_they_fail(log):
    log.at(1)
    before = trace.snapshot()
    request = mock.Mock()
    request.execute.return_value = {"items": [1, 2, 3]}

    assert trace.api_call(request, "videos.list lengths of 3 video(s)") == {"items": [1, 2, 3]}
    request.execute.side_effect = RuntimeError("quota")
    with pytest.raises(RuntimeError):
        trace.api_call(request, "playlistItems.list PL1 page 1")

    ok, failed = log.lines()
    assert "[YouTube API]" in ok and "3 item(s): videos.list lengths of 3 video(s)" in ok
    assert "failed: playlistItems.list PL1 page 1" in failed
    assert "YouTube API 2 (" in trace.describe(before)


def test_every_ytdlp_run_is_counted_and_logged(log, monkeypatch):
    log.at(1)
    before = trace.snapshot()
    result = SimpleNamespace(returncode=1, stdout='{"id": "a"}\n{"id": "b"}\n',
                             stderr="WARNING: x\nERROR: [youtube] c: Video unavailable")
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: result)

    assert m.run_ytdlp(["--dump-json", "ytsearch5:Foals 2001"], capture_output=True, text=True) is result

    [line] = log.lines()
    assert "[yt-dlp]" in line and "exit 1, 2 line(s), ERROR: [youtube] c: Video unavailable" in line
    assert line.endswith(": --dump-json ytsearch5:Foals 2001")
    assert "yt-dlp 1 (" in trace.describe(before)


def test_a_ytdlp_run_that_times_out_is_logged_and_still_raises(log, monkeypatch):
    log.at(1)

    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(m.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        m.run_ytdlp(["--dump-json", "url"], timeout=30)
    [line] = log.lines()
    assert "timed out after 30s: --dump-json url" in line


def test_a_ytdlp_result_without_output_fields_is_still_logged(log, monkeypatch):
    # Tests fake subprocess.run with objects that have only stdout
    log.at(1)
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=None))
    m.run_ytdlp(["--version"])
    assert "exit 0, ? line(s): --version" in log.lines()[0]


def test_musicbrainz_requests_are_logged_with_their_query(log, monkeypatch):
    log.at(1)
    monkeypatch.setattr(musicbrainz_client, "MUSICBRAINZ_API_MIN_INTERVAL", 0)
    response = mock.Mock(status_code=200)
    monkeypatch.setattr(musicbrainz_client._session, "get", lambda *a, **k: response)

    assert musicbrainz_client.mb_get("https://musicbrainz.org/ws/2/recording",
                                     {"query": "recording:Sonne AND artist:Rammstein"}) is response

    [line] = log.lines()
    assert "[MusicBrainz]" in line and "attempt 1 -> HTTP 200: recording recording:Sonne AND artist:Rammstein" in line


def test_each_parsed_video_is_logged_with_how_it_was_read(log):
    log.at(1)
    ifp.build_metadata_from_item({"video_id": "vid1", "title": "Sonne", "channel": "Rammstein - Topic",
                                  "duration": 272})
    ifp.build_metadata_from_item({"video_id": "vid2", "title": "Mr. Vain (Original Radio Edit)",
                                  "channel": "Culture Beat"})

    topic, channel = log.lines()
    assert ('[Parse] vid1 "Sonne" (channel "Rammstein - Topic", 272s) -> "Rammstein" - "Sonne" [Topic channel]'
            in topic)
    assert '-> "Culture Beat" - "Mr. Vain" [no separator, channel as artist]' in channel


# ==================== Settings -> Debug log ====================

def _settings_menu(monkeypatch, level, only_file):
    import menu.main_menu  # noqa: F401 - menu.song_actions' circular import (AGENTS.md)
    from menu.main_menu import settings

    monkeypatch.setattr(settings.questionary, "select",
                        lambda *a, **k: SimpleNamespace(ask=lambda: level))
    asked = []
    monkeypatch.setattr(settings, "ask_yes_no", lambda question: asked.append(question) or only_file)
    saved = []
    monkeypatch.setattr(settings.debug, "save_debug_settings", lambda *args: saved.append(args))
    monkeypatch.setattr(settings.debug, "read_debug_settings", lambda: (0, False))
    settings.debug_log_menu()
    return saved, asked


def test_the_menu_saves_the_level_only_into_the_file_by_default(monkeypatch):
    saved, asked = _settings_menu(monkeypatch, 2, True)
    assert saved == [(2, False)]
    assert len(asked) == 1


def test_the_menu_can_print_on_the_screen_too(monkeypatch):
    assert _settings_menu(monkeypatch, 1, False)[0] == [(1, True)]


def test_level_0_asks_nothing_more(monkeypatch):
    saved, asked = _settings_menu(monkeypatch, 0, None)
    assert saved == [(0, False)] and asked == []


@pytest.mark.parametrize("level, only_file", [(None, True), (2, None)])
def test_a_cancelled_menu_changes_nothing(monkeypatch, level, only_file):
    assert _settings_menu(monkeypatch, level, only_file)[0] == []


# Real case (2026-11-12): a googleapiclient HttpError's text holds the request
# URL with the API key, and flog(f"... {exc}") / a crash wrote it to debug.log
def test_secrets_in_urls_never_reach_debug_log(log):
    debug.flog("videos.list failed: <HttpError 404 when requesting "
               "https://youtube.googleapis.com/youtube/v3/videos?id=x&key=AIzaSECRET-123_x&alt=json>")
    debug.flog("refresh: https://oauth2.googleapis.com/token?access_token=ya29.SECRET&x=1")
    text = "\n".join(log.lines())
    assert "SECRET" not in text
    assert "key=REDACTED&alt=json" in text and "access_token=REDACTED&x=1" in text
    assert debug.redact_secrets("a monkey=1 and keyboard") == "a monkey=1 and keyboard"
