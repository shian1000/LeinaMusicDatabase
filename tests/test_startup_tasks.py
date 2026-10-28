"""Start-up tasks (utils/ui/startup_tasks.py) and their switches (utils/common/
startup_settings.py): each switchable step runs when on and is skipped when
off, the steps that keep the data consistent always run, the daily backup's
warning while it's off, the once-a-day marker, and the .env round trip - on
temp files only."""
from datetime import date, datetime, timedelta

import pytest

from utils.common import env_file
from utils.common import startup_settings as switches
from utils.common.env_file import read_env_value
from utils.ui import startup_tasks as startup

STEPS = ["_keep_data_consistent", "_apply_mood_changes", "_index_library", "_share_databases",
         "_publish_to_github", "_offer_ytdlp_update", "_show_messages"]


@pytest.fixture
def run(monkeypatch):
    """Runs run_startup_tasks() with every step faked; returns the steps run.
    `off` are the switches turned off, `daily` whether it's the day's first start."""
    def go(off=(), daily=True):
        ran = []
        for name in STEPS:
            monkeypatch.setattr(startup, name, lambda name=name: ran.append(name))
        monkeypatch.setattr(startup, "_daily", lambda: daily)
        monkeypatch.setattr(switches, "enabled", lambda switch, env_file=None: switch not in off)
        startup.run_startup_tasks()
        return ran
    return go


def test_everything_on_runs_every_step_in_order(run):
    assert run() == STEPS


def test_not_the_first_start_of_the_day_skips_the_daily_steps(run):
    assert run(daily=False) == ["_keep_data_consistent", "_apply_mood_changes", "_show_messages"]


@pytest.mark.parametrize("switch, step", [
    (switches.MOOD_CHANGES, "_apply_mood_changes"),
    (switches.LIBRARY_INDEX, "_index_library"),
    (switches.SHARE_COPY, "_share_databases"),
    (switches.YTDLP_UPDATE, "_offer_ytdlp_update"),
    (switches.MESSAGES, "_show_messages"),
])
def test_a_switch_turned_off_skips_only_its_step(run, switch, step):
    assert run(off={switch}) == [s for s in STEPS if s != step]


def test_the_data_consistency_steps_run_with_everything_off(run):
    assert run(off=set(switches.SWITCHES)) == ["_keep_data_consistent", "_publish_to_github"]


def test_the_github_publish_follows_its_own_switch(monkeypatch):
    from utils.database import public_database

    published = []
    monkeypatch.setattr(public_database, "missing_setup", lambda: [])
    monkeypatch.setattr(public_database, "publish_daily", lambda: published.append(True))
    monkeypatch.setattr(public_database, "daily_publish_enabled", lambda: False)
    startup._publish_to_github()
    assert published == []
    monkeypatch.setattr(public_database, "daily_publish_enabled", lambda: True)
    startup._publish_to_github()
    assert published == [True]


# ==================== the daily backup and the first start of a day ====================

@pytest.fixture
def backup(monkeypatch, tmp_path):
    from utils.database import backup as backup_module

    state = {"backups": 0, "newest": None, "marked": [], "first": True}
    monkeypatch.setattr(backup_module, "backup_if_needed_today",
                        lambda: state.__setitem__("backups", state["backups"] + 1) or ["music.db"])
    monkeypatch.setattr(backup_module, "newest_backup_time", lambda: state["newest"])
    monkeypatch.setattr(switches, "mark_daily_tasks_run", lambda: state["marked"].append(True))
    monkeypatch.setattr(switches, "first_start_today", lambda: state["first"])
    return state


def test_backup_on_takes_the_daily_backup_as_before(backup, monkeypatch, capsys):
    monkeypatch.setattr(switches, "enabled", lambda switch, env_file=None: True)
    assert startup._daily() is True
    assert backup["backups"] == 1 and backup["marked"] == [True]
    assert capsys.readouterr().out == ""


def test_backup_off_takes_no_backup_and_warns_when_the_newest_is_old(backup, monkeypatch, capsys):
    monkeypatch.setattr(switches, "enabled", lambda switch, env_file=None: switch is not switches.BACKUP)
    backup["newest"] = datetime.now() - timedelta(days=8)
    assert startup._daily() is True
    assert backup["backups"] == 0
    assert "the newest backup is 8 days old" in capsys.readouterr().out


def test_backup_off_is_quiet_while_the_newest_is_recent(backup, monkeypatch, capsys):
    monkeypatch.setattr(switches, "enabled", lambda switch, env_file=None: switch is not switches.BACKUP)
    backup["newest"] = datetime.now() - timedelta(days=2)
    backup["first"] = False
    assert startup._daily() is False
    assert capsys.readouterr().out == ""


def test_a_failed_daily_backup_is_shown_and_the_day_still_starts(backup, monkeypatch, capsys):
    from utils.database import backup as backup_module

    def fail():
        raise backup_module.BackupFailedError("database or disk is full")

    monkeypatch.setattr(switches, "enabled", lambda switch, env_file=None: True)
    monkeypatch.setattr(backup_module, "backup_if_needed_today", fail)
    assert startup._daily() is True  # first_start_today()
    out = capsys.readouterr().out
    assert "daily backup of the databases failed: database or disk is full" in out


@pytest.fixture
def archive(monkeypatch, tmp_path):
    import sqlite3
    from utils.database import backup as backup_module

    music = tmp_path / "music.db"
    with sqlite3.connect(music) as conn:
        conn.execute("CREATE TABLE songs (title TEXT)")
    monkeypatch.setattr(backup_module, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(backup_module, "DB_FILES", {"music": music})
    monkeypatch.setattr(backup_module, "user_db_files", lambda: {})
    return backup_module


def test_the_daily_backup_is_taken_once_a_day(archive):
    created = archive.backup_if_needed_today()
    assert [p.parent for p in created] == [archive.ARCHIVE_DIR]
    assert archive.backup_if_needed_today() == []
    assert archive.has_backup_today()


def test_an_interrupted_backup_leaves_no_file_counting_as_one(archive, monkeypatch):
    import sqlite3
    from types import SimpleNamespace

    class _Source:
        def __init__(self, path):
            self.conn = sqlite3.connect(path)

        def backup(self, dest):
            self.conn.backup(dest)
            raise OSError("database or disk is full")

        def close(self):
            self.conn.close()

    real_connect = sqlite3.connect
    monkeypatch.setattr(archive, "sqlite3", SimpleNamespace(
        connect=lambda path: _Source(path) if path.endswith("music.db") else real_connect(path)))

    with pytest.raises(archive.BackupFailedError):
        archive.backup_if_needed_today()
    assert list(archive.ARCHIVE_DIR.iterdir()) == []
    assert not archive.has_backup_today() and archive.newest_backup_time() is None


def test_backup_warning():
    now = datetime(2026, 10, 22, 12, 0)
    assert "no backup of the databases at all" in switches.backup_warning(None, now)
    assert switches.backup_warning(now - timedelta(days=7), now) is None
    assert "8 days old (2026-10-14)" in switches.backup_warning(now - timedelta(days=8), now)


def test_newest_backup_time(tmp_path):
    from utils.database.backup import newest_backup_time

    assert newest_backup_time(tmp_path / "missing") is None
    for name in ("music.261001-0900.db", "music.261015-1830_2.db", "tag.261020-1000.db", "music.notes.db"):
        (tmp_path / name).write_text("")
    assert newest_backup_time(tmp_path) == datetime(2026, 10, 15, 18, 30)


def test_first_start_today_marks_the_day(tmp_path):
    state = tmp_path / "data" / "startup_state.json"
    today = date(2026, 10, 22)
    assert switches.first_start_today(today, state) is True
    assert switches.first_start_today(today, state) is False
    assert switches.first_start_today(today + timedelta(days=1), state) is True


def test_a_day_marked_by_the_backup_isnt_run_again_with_it_off(tmp_path):
    state = tmp_path / "startup_state.json"
    switches.mark_daily_tasks_run(date.today().isoformat(), state)
    assert switches.first_start_today(None, state) is False


def test_a_startup_state_it_cant_save_is_logged(tmp_path, monkeypatch):
    # COM-25: swallowed silently, the daily tasks ran at every start with nothing saying why
    logged = []
    monkeypatch.setattr(switches, "flog", logged.append)
    blocker = tmp_path / "data"
    blocker.write_text("a file where the folder should be")
    switches.mark_daily_tasks_run("2026-10-28", blocker / "startup_state.json")
    assert len(logged) == 1 and "startup_state.json" in logged[0]


# ==================== the .env round trip ====================

def test_switches_are_on_by_default_and_off_is_written_to_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SHARING_DIR=/srv/shared\n")
    assert all(switches.enabled(switch, env) for switch in switches.SWITCHES)

    on = {switch.key for switch in switches.SWITCHES} - {switches.BACKUP.key, switches.MESSAGES.key}
    changed = switches.save_choices(on, env)

    assert changed == [switches.BACKUP, switches.MESSAGES]
    assert read_env_value("STARTUP_BACKUP", env) == "off" and read_env_value("STARTUP_MESSAGES", env) == "off"
    assert not switches.enabled(switches.BACKUP, env) and switches.enabled(switches.SHARE_COPY, env)
    assert read_env_value("SHARING_DIR", env) == "/srv/shared"

    # Back on: the key goes, the default is on
    assert switches.save_choices({switch.key for switch in switches.SWITCHES}, env) == [switches.BACKUP,
                                                                                         switches.MESSAGES]
    assert "STARTUP_" not in env.read_text()


def test_the_settings_screen_saves_the_ticks_and_the_github_switch(monkeypatch, tmp_path, capsys):
    import questionary
    from menu.main_menu import settings as settings_menu
    from utils.database import public_database

    env = tmp_path / ".env"
    monkeypatch.setattr(env_file, "ENV_FILE", env)
    github = {"on": True}
    monkeypatch.setattr(public_database, "daily_publish_enabled", lambda: github["on"])
    monkeypatch.setattr(public_database, "set_daily_publish", lambda on: github.__setitem__("on", on))

    class _Ask:
        def __init__(self, value):
            self.value = value

        def ask(self):
            return self.value

    offered = []

    def checkbox(message, choices):
        offered.extend((c.value, c.checked) for c in choices)
        return _Ask([c.value for c in choices if c.value not in (switches.BACKUP.key, "__github__")])

    monkeypatch.setattr(questionary, "checkbox", checkbox)
    settings_menu.startup_tasks_menu()

    assert all(checked for _, checked in offered)
    assert read_env_value("STARTUP_BACKUP", env) == "off" and github["on"] is False
    out = capsys.readouterr().out
    assert "Changed: Daily backup, Daily publish to GitHub." in out
    assert "The daily backup is off" in out



def test_env_example_copied_as_it_is_sets_no_folder():
    # COM-26: "/path/to/Music/Library/" copied unchanged gave messages about a folder that isn't
    # there instead of a clean "not set"
    from dotenv import dotenv_values
    from config.constants import PROJECT_ROOT
    example = dotenv_values(PROJECT_ROOT / ".env.example")
    for key in ("MUSIC_LIBRARY_DIR", "SHARING_DIR", "SHARING_DRIVE_MOUNT"):
        assert example[key] == "", key


def test_backup_switch_text_follows_the_database_folder_and_the_warning_age():
    # COM-42: it named src/database/archive/ (the folder can be moved) and wrote out "7 days"
    text = switches.BACKUP.explanation
    assert "src/database" not in text
    assert f"more than {switches.BACKUP_WARNING_AGE.days} days old" in text
