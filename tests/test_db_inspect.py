"""tools/db_inspect.py's answer to "is the app using the databases?", read
from the app lock (utils/database/app_lock.py) - never a reassuring "no"
when the check fails."""

import importlib.util
from pathlib import Path

import pytest

from utils.database import app_lock

_spec = importlib.util.spec_from_file_location(
    "db_inspect", Path(__file__).resolve().parents[1] / "tools" / "db_inspect.py")
db_inspect = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db_inspect)


@pytest.fixture
def lock(tmp_path, monkeypatch):
    path = tmp_path / ".musicdatabase.lock"
    monkeypatch.setattr(app_lock, "LOCK_FILE", path)
    monkeypatch.setattr(app_lock, "_held", [])
    yield path
    for handle in app_lock._held:
        handle.close()


def test_no_lock_file_means_no_app_and_none_is_created(lock):
    assert db_inspect.app_state() == "not running"
    assert not lock.exists()


def test_an_open_app_is_seen(lock):
    app_lock.hold_app_lock()
    assert db_inspect.app_state() == "running"


def test_a_running_automatic_pass_is_seen(lock):
    handle = app_lock.try_exclusive_lock()
    try:
        assert db_inspect.app_state() == "running"
    finally:
        handle.close()


def test_closed_app_and_the_lock_is_left_free(lock):
    app_lock.hold_app_lock()
    app_lock._held.pop().close()

    assert db_inspect.app_state() == "not running"
    handle = app_lock.try_exclusive_lock()  # the check let it go
    assert handle is not None
    handle.close()


def test_a_failed_check_is_unknown_not_no(lock, monkeypatch):
    """Before: any error from `ps` answered "main.py running: no"."""
    lock.touch()

    def refuse(*args, **kwargs):
        raise PermissionError("no access")
    monkeypatch.setattr("builtins.open", refuse)

    assert db_inspect.app_state() == "unknown"
