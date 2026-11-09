"""The shared Chrome driver (utils/common/selenium_sessions.py): the driver
build is mocked, no browser is started."""
from types import SimpleNamespace

import pytest

from utils.common import selenium_sessions


@pytest.mark.parametrize("headless", [True, False])
def test_open_global_driver_passes_headless_on(monkeypatch, headless):
    # COM-04: open_global_driver(headless=False) built a headless driver anyway
    built = []
    monkeypatch.setattr(selenium_sessions, "_GLOBAL_DRIVER", None)
    monkeypatch.setattr(selenium_sessions, "_build_driver", lambda headless=True: built.append(headless) or "driver")

    assert selenium_sessions.open_global_driver(headless=headless) == "driver"
    assert built == [headless]


@pytest.fixture
def no_driver_yet(monkeypatch):
    monkeypatch.setattr(selenium_sessions, "_GLOBAL_DRIVER", None)
    monkeypatch.setattr(selenium_sessions, "_GLOBAL_DRIVER_FAILED", False)


def test_chrome_starts_at_the_first_lookup_that_needs_it_and_only_once(monkeypatch, no_driver_yet):
    built = []
    driver = SimpleNamespace(quit=lambda: None)
    monkeypatch.setattr(selenium_sessions, "_build_driver", lambda headless=True: built.append(headless) or driver)

    assert built == []  # nothing until someone asks
    assert selenium_sessions.get_global_driver() is driver
    assert selenium_sessions.get_global_driver() is driver
    assert len(built) == 1


def test_a_chrome_that_wont_start_is_reported_once_until_the_run_ends(monkeypatch, no_driver_yet):
    built, shown = [], []

    def no_chrome(headless=True):
        built.append(headless)
        raise selenium_sessions.ChromeDriverLaunchError("no Chrome")
    monkeypatch.setattr(selenium_sessions, "_build_driver", no_chrome)
    monkeypatch.setattr(selenium_sessions, "error", shown.append)

    assert selenium_sessions.get_global_driver() is None
    assert selenium_sessions.get_global_driver() is None
    assert len(built) == 1
    assert shown == ["no Chrome"]

    selenium_sessions.close_global_driver()  # the next run tries again
    assert selenium_sessions.get_global_driver() is None
    assert len(built) == 2


def test_closing_a_driver_that_never_started_does_nothing(monkeypatch, no_driver_yet):
    monkeypatch.setattr(selenium_sessions, "_build_driver", lambda headless=True: pytest.fail("started Chrome"))

    selenium_sessions.close_global_driver()


def test_a_chrome_that_wont_start_is_explained_in_english(monkeypatch):
    # COM-33: the only Polish message in an English program
    from selenium.common.exceptions import WebDriverException

    def no_chrome(options=None):
        raise WebDriverException("DevToolsActivePort file doesn't exist")
    monkeypatch.setattr(selenium_sessions.shutil, "which", lambda name: "/usr/bin/chromium")
    monkeypatch.setattr(selenium_sessions.webdriver, "Chrome", no_chrome)

    with pytest.raises(selenium_sessions.ChromeDriverLaunchError, match="Couldn't start Chrome/Chromium"):
        selenium_sessions._build_driver()
