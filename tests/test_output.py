"""Output for the user vs diagnostics (AGENTS.md -> "Output"): utils.common.output
prints names literally (a "[Live]" in a title isn't eaten as rich markup), no
module shadows print with rich's or writes its own ANSI codes; debug.log is
rotated and a failed write is reported once; failures that used to vanish
(a file that couldn't be fixed, a yt-dlp description that didn't load, a
backup that failed) reach debug.log through flog()."""

import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils.common import debug, output
from utils.ui import file_batch
from utils.youtube import import_from_playlist as ifp

SRC = Path(__file__).resolve().parent.parent / "src"


def test_styled_names_keep_their_brackets(capsys):
    output.show(f"Found {output.styled('Song [Live]', 'blue')} by {output.styled('[ewa]', 'cyan')}")
    assert capsys.readouterr().out == "Found Song [Live] by [ewa]\n"


def test_say_and_the_coloured_lines_print_text_literally(capsys):
    output.say("[red]not markup[/red]")
    output.error("Couldn't fix a [b].mp3")
    output.warn("x [/] y")
    output.ok("done [Live]")
    assert capsys.readouterr().out.splitlines() == [
        "[red]not markup[/red]", "Couldn't fix a [b].mp3", "x [/] y", "done [Live]"]


def test_a_long_line_isnt_broken_by_rich(capsys):
    output.say("x" * 300)
    assert capsys.readouterr().out == "x" * 300 + "\n"


def test_review_header(capsys):
    output.review_header("REVIEW: 2 song(s)", "a note")
    rule = "=" * output.REVIEW_WIDTH
    assert capsys.readouterr().out == f"\n{rule}\nREVIEW: 2 song(s)\na note\n{rule}\n"


def _python_files():
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def test_nothing_shadows_print_with_rich():
    offenders = [str(p.relative_to(SRC)) for p in _python_files()
                 if re.search(r"^from rich import .*\bprint\b", p.read_text(), re.M)]
    assert offenders == []


def test_ansi_colours_come_from_one_place():
    # song_colors keeps its own map: prompt_toolkit styles -> ANSI
    allowed = {"utils/common/output.py", "utils/ui/song_colors.py"}
    offenders = [str(p.relative_to(SRC)) for p in _python_files()
                 if str(p.relative_to(SRC)) not in allowed and "\\033[" in p.read_text()]
    assert offenders == []


def test_debug_log_rotates(tmp_path, monkeypatch):
    log = tmp_path / "debug.log"
    monkeypatch.setattr(debug, "DEBUG_LOG_FILE", log)
    monkeypatch.setattr(debug, "LOG_MAX_BYTES", 100)
    monkeypatch.setattr(debug, "LOG_BACKUP_COUNT", 2)
    for i in range(20):
        debug.flog(f"line {i:02d} " + "x" * 40)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["debug.log", "debug.log.1", "debug.log.2"]
    assert "line 19" in log.read_text()
    assert all(p.stat().st_size <= 100 for p in tmp_path.iterdir())


def test_a_failed_log_write_is_reported_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(debug, "DEBUG_LOG_FILE", tmp_path)  # a folder: can't be opened for append
    monkeypatch.setattr(debug, "_write_failure_reported", False)
    debug.flog("one")
    debug.flog("two")
    err = capsys.readouterr().err
    assert err.count("Can't write") == 1


def test_apply_to_files_goes_on_after_a_failure_and_logs_it(monkeypatch):
    logged = []
    monkeypatch.setattr(file_batch, "flog", logged.append)

    def fix(name):
        if name == "bad.mp3":
            raise OSError("read-only")
        return name.upper()

    done, failed = file_batch.apply_to_files(["a.mp3", "bad.mp3", "c.mp3"], fix, Path)
    assert done == ["A.MP3", "C.MP3"]
    assert [(str(f), str(e)) for f, e in failed] == [("bad.mp3", "read-only")]
    assert logged == ["Couldn't change bad.mp3: OSError: read-only"]


def _ytdlp(monkeypatch, stdout="", stderr="", returncode=0, raises=None):
    def run(args, **kwargs):
        if raises:
            raise raises
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(ifp, "run_ytdlp", run)


@pytest.mark.parametrize("kwargs, reason", [
    ({"raises": subprocess.TimeoutExpired("yt-dlp", 30)}, "timed out"),
    ({"returncode": 1, "stderr": "ERROR: [youtube] v1: Video unavailable"}, "Video unavailable"),
    ({"stdout": "not json"}, "unreadable"),
])
def test_a_description_that_didnt_load_says_why(monkeypatch, kwargs, reason):
    logged = []
    monkeypatch.setattr(ifp, "flog", logged.append)
    _ytdlp(monkeypatch, **kwargs)
    assert ifp._fetch_video_description("v1") is None
    assert len(logged) == 1 and "v1" in logged[0] and reason in logged[0]


def test_a_playlist_ytdlp_couldnt_read_says_why(monkeypatch):
    logged = []
    monkeypatch.setattr(ifp, "flog", logged.append)
    _ytdlp(monkeypatch, stdout="", stderr="ERROR: [youtube:tab] PL1: This playlist is private", returncode=1)
    assert ifp._fetch_playlist_items_ytdlp("PL1") is None
    assert "This playlist is private" in logged[0]


def test_a_failed_daily_backup_reaches_debug_log(monkeypatch):
    from utils.database import backup
    logged = []
    monkeypatch.setattr(backup, "flog", logged.append)
    monkeypatch.setattr(backup, "has_backup_today", lambda: False)

    def full_disk(reason):
        raise OSError("No space left on device")
    monkeypatch.setattr(backup, "backup_databases", full_disk)
    with pytest.raises(backup.BackupFailedError):
        backup.backup_if_needed_today()
    assert logged == ["Daily database backup failed: No space left on device"]
