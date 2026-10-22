"""No test and no manual script reaches the owner's real .env, databases or logs."""
import logging
import sqlite3
import sys
from pathlib import Path

import pytest

from utils.common import debug, env_file, sharing_setup, startup_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
MANUAL_DIR = REPO_ROOT / "tests" / "manual"


@pytest.fixture
def swapped_env(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(env_file, "ENV_FILE", path)
    return path


def test_env_helpers_without_a_file_use_the_swapped_env(swapped_env):
    env_file.save_env_value("MUSIC_LIBRARY_DIR", "/music")

    assert swapped_env.read_text().strip() == "MUSIC_LIBRARY_DIR=/music"
    assert env_file.read_env_value("MUSIC_LIBRARY_DIR") == "/music"


def test_callers_with_a_default_env_file_use_the_swapped_env(swapped_env):
    swapped_env.write_text("SHARING_LAN_INTERFACE=eth9\n")

    assert sharing_setup.restart_needed(environ={}) == ["SHARING_LAN_INTERFACE"]
    switch = startup_settings.SWITCHES[0]
    startup_settings.set_enabled(switch, False)
    assert f"{switch.key}=off" in swapped_env.read_text()
    assert not startup_settings.enabled(switch)


def test_logs_are_kept_out_of_the_repo():
    from utils.youtube import manage_youtube_playlists  # noqa: F401 - attaches its log at import

    assert REPO_ROOT not in debug.DEBUG_LOG_FILE.parents
    files = [Path(h.baseFilename) for h in logging.getLogger("youtube_link_cache").handlers
             if isinstance(h, logging.FileHandler)]
    assert files and all(REPO_ROOT not in f.parents for f in files)


def test_manual_scripts_work_on_copies_and_never_write_debug():
    scripts = [p for p in MANUAL_DIR.glob("*.py") if p.name not in ("conftest.py", "_sandbox.py")]
    assert scripts
    for script in scripts:
        source = script.read_text()
        assert "enter_sandbox(" in source, script.name
        assert '".debug"' not in source, script.name
        # before anything from the app is imported
        assert source.index("enter_sandbox(") < source.index("from utils."), script.name


def test_sandbox_copies_the_databases_and_points_the_app_at_them(tmp_path, monkeypatch):
    from utils.database import database_location

    for module, name in ((database_location, "load_database_dir_override"), (env_file, "ENV_FILE"),
                         (debug, "DEBUG_LOG_FILE"), (debug, "DEBUG_ENABLED"), (debug, "VERBOSITY_LEVEL")):
        monkeypatch.setattr(module, name, getattr(module, name))
    real = tmp_path / "real"
    real.mkdir()
    with sqlite3.connect(real / "music.db") as conn:
        conn.execute("CREATE TABLE songs (title TEXT)")
        conn.execute("INSERT INTO songs VALUES ('Original')")
    monkeypatch.syspath_prepend(str(MANUAL_DIR))
    sys.modules.pop("_sandbox", None)
    from _sandbox import enter_sandbox

    sandbox = enter_sandbox(verbosity=1, source_dir=real)

    copy = Path(database_location.load_database_dir_override()) / "music.db"
    assert copy.parent.parent == sandbox and copy.exists()
    with sqlite3.connect(copy) as conn:
        conn.execute("UPDATE songs SET title = 'Changed'")
    with sqlite3.connect(real / "music.db") as conn:
        assert conn.execute("SELECT title FROM songs").fetchone() == ("Original",)
    assert env_file.ENV_FILE.parent == sandbox
    assert debug.DEBUG_LOG_FILE.parent == sandbox
    assert debug.DEBUG_ENABLED and debug.VERBOSITY_LEVEL == 1
